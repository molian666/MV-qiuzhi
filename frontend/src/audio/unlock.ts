/**
 * 音频解锁（移动端必需，桌面端顺带受益）。
 *
 * 两个"必须在用户手势里做"的动作，缺一个都会让手机上的语音面试静默失效：
 *
 * 1. **`AudioContext` 的创建/恢复**：iOS 要求 `AudioContext` 在用户手势的调用栈里
 *    进入 running。原来的代码在 `audio_chunk` 到达时才懒创建（`player.ts` 的
 *    `ensureContext()`）——那已经不是手势上下文了，拿到的是 `suspended`，
 *    表现是"面试官说话了但没声音"，**不报错**。
 *
 * 2. **`speechSynthesis` 的首次 `speak()`**：iOS 会忽略不是在用户手势里发起的
 *    utterance。原来的 `player.ts` 在 `setTimeout` 回调里才 `speak()`
 *    （那是"思考停顿"，见 `audio/thinking.ts`），于是 iOS 上整个面试官都不会出声，
 *    同样不报错。惯用解法是在手势里先 `speak()` 一个空 utterance 把它"点亮"。
 *
 * 所以：**所有"开始面试""开始说话"按钮都先过 `unlockAudio()`**。
 *
 * 这里把 `AudioContext` 做成可注入的（`AudioContextCtor`），因为：
 * - Node 里跑单测没有 `AudioContext`，硬依赖就测不了；
 * - 解锁逻辑"是不是只做一次""suspended 会不会被 resume"才是真正要钉住的部分。
 */

export interface UnlockResult {
  /** 是否真的做了动作（重复调用时为 false，说明已解锁过） */
  unlocked: boolean
  /** 拿到了可用的 AudioContext（可能仍处于 suspended，见 `ctx.state`） */
  context: AudioContext | null
  /** 被识别为"当前不是安全上下文/不支持"而跳过的原因（有值说明降级了） */
  skipped: string
}

export interface UnlockOptions {
  /** 注入用；默认取 `globalThis.AudioContext`（含 webkit 前缀兜底） */
  audioContextCtor?: typeof AudioContext | null
  /** 注入用；默认取 `window.speechSynthesis` */
  speechSynthesis?: SpeechSynthesis | null
  /** 注入用；默认取 `globalThis.SpeechSynthesisUtterance` */
  utteranceCtor?: (new (text: string) => SpeechSynthesisUtterance) | null
}

let cachedContext: AudioContext | null = null
let speechPrimed = false

function resolveContextCtor(options: UnlockOptions): typeof AudioContext | null {
  if (options.audioContextCtor !== undefined) {
    return options.audioContextCtor
  }
  if (typeof globalThis === 'undefined') {
    return null
  }
  const scope = globalThis as unknown as {
    AudioContext?: typeof AudioContext
    webkitAudioContext?: typeof AudioContext
  }
  // iOS Safari 老版本只有 webkitAudioContext；新版本两者都在
  return scope.AudioContext ?? scope.webkitAudioContext ?? null
}

function resolveSynth(options: UnlockOptions): SpeechSynthesis | null {
  if (options.speechSynthesis !== undefined) {
    return options.speechSynthesis
  }
  if (typeof window === 'undefined') {
    return null
  }
  return window.speechSynthesis ?? null
}

function resolveUtterance(
  options: UnlockOptions,
): (new (text: string) => SpeechSynthesisUtterance) | null {
  if (options.utteranceCtor !== undefined) {
    return options.utteranceCtor
  }
  if (typeof globalThis === 'undefined') {
    return null
  }
  const ctor = (globalThis as unknown as { SpeechSynthesisUtterance?: new (t: string) => SpeechSynthesisUtterance })
    .SpeechSynthesisUtterance
  return ctor ?? null
}

/**
 * 在一个用户手势里调用它。重复调用是安全的（幂等）。
 *
 * 返回的 `context` 可以交给 `AgentSpeaker` 复用，避免出现第二个 AudioContext
 * （iOS 上多开 context 会互相抢音频焦点）。
 */
export function unlockAudio(options: UnlockOptions = {}): UnlockResult {
  const skipped: string[] = []
  let context: AudioContext | null = null
  let did = false

  // ---- AudioContext ----
  const Ctor = resolveContextCtor(options)
  if (Ctor === null) {
    skipped.push('没有 AudioContext')
  } else {
    if (cachedContext === null) {
      try {
        cachedContext = new Ctor()
        did = true
      } catch (error) {
        skipped.push(`创建 AudioContext 失败：${String(error)}`)
        cachedContext = null
      }
    }
    context = cachedContext
    if (context !== null && context.state === 'suspended') {
      // 在手势里 resume 才会成功；失败也不抛（有些环境返回 rejected promise）
      try {
        void context.resume().catch(() => undefined)
      } catch {
        // 忽略：调用方看 state 就会知道
      }
    }
  }

  // ---- speechSynthesis ----
  const synth = resolveSynth(options)
  const Utterance = resolveUtterance(options)
  if (synth === null || Utterance === null) {
    skipped.push('没有 speechSynthesis')
  } else if (!speechPrimed) {
    try {
      // 空 utterance：不作声，只把 iOS 的语音合成"点亮"
      synth.speak(new Utterance(''))
      speechPrimed = true
      did = true
    } catch (error) {
      skipped.push(`预热 speechSynthesis 失败：${String(error)}`)
    }
  }

  return { unlocked: did, context, skipped: skipped.join('；') }
}

/** 让 `AgentSpeaker` 复用手势里创建的那个 context（没有就返回 null）。 */
export function unlockedAudioContext(): AudioContext | null {
  return cachedContext
}

/** 仅供测试：忘掉"已解锁"的状态。 */
export function resetUnlockState(): void {
  cachedContext = null
  speechPrimed = false
}
