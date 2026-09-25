/**
 * 语音会话的 WebSocket 客户端。
 *
 * 事件类型与后端 `app/realtime/session_runtime.py` 的下行事件一一对应；
 * 所有事件都带 `seq`，前端据此忽略重复事件（断线重连时有用）。
 */

export type AudioMode = 'client' | 'server'

export interface StateEvent {
  type: 'state'
  session_id: number
  audio_mode: AudioMode
  speech_provider: string
  text_fallback: boolean
  input_sample_rate: number
  silence_ms: number
  seq: number
}

export interface InterviewerTextEvent {
  type: 'interviewer_text'
  turn_id: number
  idx: number
  text: string
  kind: string
  topic: string
  is_followup: boolean
  seq: number
}

export interface CandidateTurnEvent {
  type: 'candidate_turn'
  turn_id: number
  idx: number
  text: string
  start_ms: number
  end_ms: number
  source: string
  seq: number
}

export interface AsrEvent {
  type: 'asr_partial' | 'asr_final'
  text: string
  seq: number
  /** 术语纠错记录：ASR 把「亚像素」听成「亚相素」时，这里说明改了什么 */
  corrections?: { wrong: string; right: string }[]
}

export interface SpeechStateEvent {
  type: 'speech_state'
  state: 'speech_started' | 'speech_stopped'
  at_ms: number
  seq: number
}

export interface BargeInEvent {
  type: 'barge_in'
  at_ms?: number
  ack?: boolean
  seq: number
}

export interface AudioChunkEvent {
  type: 'audio_chunk'
  pcm: string
  sample_rate: number
  seq: number
}

export interface MetricsEvent {
  type: 'metrics'
  audio_ms: number
  speaking: boolean
  threshold: number
  seq: number
}

export interface TurnMetaEvent {
  type: 'turn_meta'
  question_count: number
  answer_count: number
  max_questions: number
  audio_ms: number
  seq: number
}

export interface AwaitingTextEvent {
  type: 'awaiting_text'
  message: string
  reason?: string
  seq: number
}

export interface FinishedEvent {
  type: 'finished'
  session_id: number
  manual: boolean
  audio_ms: number
  audio_available: boolean
  seq: number
}

export interface ErrorEvent {
  type: 'error'
  message: string
  seq: number
}

export interface PongEvent {
  type: 'pong'
  seq: number
}

export interface PressureEvent {
  type: 'pressure'
  /** 每个下行事件都带 seq，前端据此去重与续接 */
  seq: number
  /** interrupt=面试官插话；time_limit=这题时间到了 */
  mode: 'interrupt' | 'time_limit'
  elapsed_ms: number
  message: string
  limit_seconds?: number
  after_ms?: number
}

export type ServerEvent =
  | StateEvent
  | InterviewerTextEvent
  | CandidateTurnEvent
  | AsrEvent
  | SpeechStateEvent
  | BargeInEvent
  | AudioChunkEvent
  | MetricsEvent
  | TurnMetaEvent
  | AwaitingTextEvent
  | FinishedEvent
  | PressureEvent
  | ErrorEvent
  | PongEvent

export interface SocketHandlers {
  onEvent: (event: ServerEvent) => void
  onOpen?: () => void
  onClose?: () => void
}

function toBase64(pcm: Int16Array): string {
  const bytes = new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength)
  let binary = ''
  const step = 0x2000 // 分块拼接，避免超长字符串一次性构造
  for (let offset = 0; offset < bytes.length; offset += step) {
    const slice = bytes.subarray(offset, Math.min(offset + step, bytes.length))
    binary += String.fromCharCode(...slice)
  }
  return btoa(binary)
}

function fromBase64(encoded: string): Int16Array {
  const binary = atob(encoded)
  const bytes = new Uint8Array(binary.length)
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index)
  }
  return new Int16Array(bytes.buffer)
}

export class SessionSocket {
  private readonly sessionId: number
  private readonly handlers: SocketHandlers
  private socket: WebSocket | null = null
  private lastSeq = 0

  constructor(sessionId: number, handlers: SocketHandlers) {
    this.sessionId = sessionId
    this.handlers = handlers
  }

  get connected(): boolean {
    return this.socket?.readyState === WebSocket.OPEN
  }

  connect(): Promise<void> {
    const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const url = `${scheme}://${window.location.host}/ws/sessions/${this.sessionId}`

    return new Promise((resolve, reject) => {
      const socket = new WebSocket(url)
      this.socket = socket

      socket.onopen = () => {
        this.handlers.onOpen?.()
        resolve()
      }
      socket.onerror = () => {
        reject(new Error('WebSocket 连接失败：请确认后端已启动'))
      }
      socket.onclose = () => {
        this.handlers.onClose?.()
      }
      socket.onmessage = (event: MessageEvent<string>) => {
        let parsed: ServerEvent
        try {
          parsed = JSON.parse(event.data) as ServerEvent
        } catch {
          return
        }
        // 去重：同一 seq 只处理一次（断线续接时可能重复下发）
        if (typeof parsed.seq === 'number') {
          if (parsed.seq <= this.lastSeq) {
            return
          }
          this.lastSeq = parsed.seq
        }
        this.handlers.onEvent(parsed)
      }
    })
  }

  sendAudio(pcm: Int16Array): void {
    this.send({ type: 'audio_append', pcm: toBase64(pcm) })
  }

  sendText(text: string): void {
    this.send({ type: 'text', text })
  }

  /** 用户点"我答完了"：立刻收口这一轮。 */
  endTurn(): void {
    this.send({ type: 'control', action: 'end_turn' })
  }

  /** 用户主动打断面试官。 */
  interrupt(): void {
    this.send({ type: 'interrupt' })
  }

  finish(): void {
    this.send({ type: 'finish' })
  }

  close(): void {
    this.socket?.close()
    this.socket = null
  }

  static decodeAudio(event: AudioChunkEvent): { pcm: Int16Array; sampleRate: number } {
    return { pcm: fromBase64(event.pcm), sampleRate: event.sample_rate }
  }

  private send(payload: Record<string, unknown>): void {
    if (this.socket === null || this.socket.readyState !== WebSocket.OPEN) {
      return
    }
    this.socket.send(JSON.stringify(payload))
  }
}
