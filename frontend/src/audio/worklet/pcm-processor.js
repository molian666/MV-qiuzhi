// AudioWorklet：把麦克风的 Float32 采样转成 pcm16，推给主线程。
//
// 采样率由 AudioContext 决定（我们在 mic.ts 里请求 16000Hz），这里不做重采样。
// AudioWorklet 跑在音频线程，所以这里只做最轻的转换，不碰 DOM、不发网络请求。

class PcmProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0]?.[0]
    if (!channel) {
      return true
    }

    const pcm = new Int16Array(channel.length)
    for (let index = 0; index < channel.length; index += 1) {
      const sample = Math.max(-1, Math.min(1, channel[index]))
      pcm[index] = sample < 0 ? sample * 0x8000 : sample * 0x7fff
    }

    // 转移 buffer 所有权，避免每 128 帧都复制一次
    this.port.postMessage(pcm.buffer, [pcm.buffer])
    return true
  }
}

registerProcessor('pcm-processor', PcmProcessor)
