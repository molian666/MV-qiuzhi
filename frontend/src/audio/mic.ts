/**
 * 麦克风采集：getUserMedia → AudioWorklet → pcm16 分片。
 *
 * 三个关键点：
 * 1. 请求 16000Hz 的 AudioContext：实时语音上游要的就是 pcm16@16k，
 *    从源头对齐格式比在链路里补重采样省事得多；
 * 2. 浏览器若不支持指定采样率，退回线性重采样——**绝不能把 48k 数据直接发上去**
 *    （上游会得到 3 倍速的"花栗鼠"音频，转写全废）；
 * 3. 按 chunkMs 聚合后再回调，避免每个 128 帧的音频块都发一次 WebSocket 消息。
 */

import processorUrl from './worklet/pcm-processor.js?url'

const TARGET_RATE = 16000

export interface MicCaptureOptions {
  onChunk: (pcm: Int16Array) => void
  onLevel?: (level: number) => void
  chunkMs?: number
}

export interface MicCapture {
  /** 实际送往服务的采样率（固定 16k） */
  readonly sampleRate: number
  stop: () => Promise<void>
}

function resampleLinear(input: Int16Array, fromRate: number, toRate: number): Int16Array {
  if (fromRate === toRate || input.length === 0) {
    return input
  }
  const ratio = fromRate / toRate
  const length = Math.floor(input.length / ratio)
  const output = new Int16Array(length)
  for (let index = 0; index < length; index += 1) {
    const position = index * ratio
    const left = Math.floor(position)
    const right = Math.min(left + 1, input.length - 1)
    const weight = position - left
    output[index] = Math.round(input[left] * (1 - weight) + input[right] * weight)
  }
  return output
}

/** 归一化 RMS，用于音量条与"用户是否开口"的本地判断。 */
export function levelOf(pcm: Int16Array): number {
  let total = 0
  for (let index = 0; index < pcm.length; index += 1) {
    total += pcm[index] * pcm[index]
  }
  return Math.sqrt(total / Math.max(1, pcm.length)) / 32768
}

export async function startMicCapture(options: MicCaptureOptions): Promise<MicCapture> {
  const { onChunk, onLevel, chunkMs = 40 } = options

  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  })

  const context = new AudioContext({ sampleRate: TARGET_RATE })
  await context.audioWorklet.addModule(processorUrl)

  const source = context.createMediaStreamSource(stream)
  const node = new AudioWorkletNode(context, 'pcm-processor')
  source.connect(node)
  // 故意不接 destination：否则自己的声音会被放出来形成回声

  const chunkSamples = Math.max(160, Math.round((TARGET_RATE * chunkMs) / 1000))
  let buffered = new Int16Array(0)

  node.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
    // 显式标注 ArrayBufferLike：TS 6 起 Int16Array 按 backing buffer 泛型化，
    // 不标注的话重采样返回值（ArrayBufferLike）无法赋给 Int16Array<ArrayBuffer>
    let pcm: Int16Array<ArrayBufferLike> = new Int16Array(event.data)
    if (context.sampleRate !== TARGET_RATE) {
      pcm = resampleLinear(pcm, context.sampleRate, TARGET_RATE)
    }

    const merged = new Int16Array(buffered.length + pcm.length)
    merged.set(buffered, 0)
    merged.set(pcm, buffered.length)
    buffered = merged

    while (buffered.length >= chunkSamples) {
      const chunk = buffered.slice(0, chunkSamples)
      buffered = buffered.slice(chunkSamples)
      onLevel?.(levelOf(chunk))
      onChunk(chunk)
    }
  }

  return {
    sampleRate: TARGET_RATE,
    stop: async () => {
      node.port.onmessage = null
      node.disconnect()
      source.disconnect()
      for (const track of stream.getTracks()) {
        track.stop()
      }
      await context.close()
    },
  }
}
