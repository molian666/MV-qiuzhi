/**
 * 面试官的声音，两条路：
 *
 * - `client` 模式（M1 默认）：用浏览器 speechSynthesis 播报文本。
 *   零依赖、中文音色开箱可用，而且**立刻能听见**——这样在还没有服务端 TTS 的情况下，
 *   语音面试也是真的能跑的一场对话，而不是"看起来通了"。
 * - `server` 模式（接入服务端 TTS 后）：播放后端经 `audio_chunk` 下发的 pcm16 分片。
 *
 * 两条路都必须能被"打断"立刻静音：用户开口时前端先本地停（毫秒级），
 * 再让后端的 `barge_in` 事件兜底。
 */

export type AudioMode = 'client' | 'server'

export class AgentSpeaker {
  private mode: AudioMode = 'client'
  private context: AudioContext | null = null
  private sources = new Set<AudioBufferSourceNode>()
  private nextStartTime = 0

  setMode(mode: AudioMode): void {
    this.mode = mode
    if (mode === 'client') {
      this.stopServerAudio()
    }
  }

  get speaking(): boolean {
    if (this.mode === 'client') {
      return typeof window !== 'undefined' && window.speechSynthesis?.speaking === true
    }
    return this.sources.size > 0
  }

  /** client 模式：把面试官的话读出来。 */
  speak(text: string): void {
    if (this.mode !== 'client' || !text.trim()) {
      return
    }
    if (typeof window === 'undefined' || !('speechSynthesis' in window)) {
      return
    }
    const synth = window.speechSynthesis
    synth.cancel()
    const utterance = new SpeechSynthesisUtterance(text)
    utterance.lang = 'zh-CN'
    utterance.rate = 1.02
    utterance.pitch = 1
    synth.speak(utterance)
  }

  /** server 模式：播放一段 pcm16。 */
  play(pcm: Int16Array, sampleRate: number): void {
    if (this.mode !== 'server' || pcm.length === 0) {
      return
    }
    const context = this.ensureContext()
    const buffer = context.createBuffer(1, pcm.length, sampleRate)
    const channel = buffer.getChannelData(0)
    for (let index = 0; index < pcm.length; index += 1) {
      channel[index] = pcm[index] / 32768
    }

    const source = context.createBufferSource()
    source.buffer = buffer
    source.connect(context.destination)
    const startAt = Math.max(context.currentTime + 0.02, this.nextStartTime)
    source.start(startAt)
    this.nextStartTime = startAt + buffer.duration
    this.sources.add(source)
    source.onended = () => {
      this.sources.delete(source)
    }
  }

  /** 打断 / 收尾：立刻静音。 */
  stop(): void {
    if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
      window.speechSynthesis.cancel()
    }
    this.stopServerAudio()
  }

  private stopServerAudio(): void {
    for (const source of this.sources) {
      try {
        source.stop()
      } catch {
        // 已经播完的 source 再 stop 会抛错，忽略
      }
    }
    this.sources.clear()
    this.nextStartTime = this.context?.currentTime ?? 0
  }

  private ensureContext(): AudioContext {
    if (this.context === null) {
      this.context = new AudioContext()
    }
    void this.context.resume()
    return this.context
  }
}
