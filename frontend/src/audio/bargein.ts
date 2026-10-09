/**
 * 打断（barge-in）判定：为什么不能再用一个固定音量阈值。
 *
 * 原来的实现是一个常量：`BARGE_IN_LEVEL = 0.02`，与后端 VAD 的 `min_energy` 同量级。
 * 那是在**笔记本**上调出来的一个够用的值。换到手机就不成立了：
 *
 * - 手机扬声器和麦克风相距不到 1cm，面试官的声音会被自己的麦克风收进去；
 * - 移动端音频路由（听筒 / 外放 / 蓝牙）来回切换，同一句话的采集电平能差好几倍；
 * - 于是固定阈值的结果是两种坏情况二选一——要么面试官一说话就把自己打断，
 *   要么用户喊两声才被认成开口。
 *
 * 这里改成"跟着环境走"：用最近一段时间的电平估一个**底噪**，触发线取
 * `底噪 × 倍数` 并夹在一个合理区间里；再加一个冷却窗，避免一次开口被反复计。
 *
 * **做成不碰 DOM 的纯类**是刻意的：判定逻辑可以完全用单测钉住
 * （见 `bargein.test.mjs`），而真实麦克风的接入留在 `InterviewVoice.tsx`。
 */

/**: 底噪估计窗口（电平样本数）。40ms 一个样本 → 约 2 秒。 */
export const NOISE_WINDOW = 50

/**: 触发倍数：高出底噪这么多倍才算"人在说话"。 */
export const DEFAULT_MULTIPLIER = 3.2

/**: 绝对下限：底噪极低（安静房间）时也不能被一点电流声触发。 */
export const MIN_THRESHOLD = 0.012

/**: 绝对上限：再吵的环境也不该把触发线抬到说话都过不去。 */
export const MAX_THRESHOLD = 0.09

/**: 打断后的冷却时长（毫秒）：这段时间内不再重复判定。 */
export const DEFAULT_COOLDOWN_MS = 900

export interface BargeInOptions {
  multiplier?: number
  minThreshold?: number
  maxThreshold?: number
  cooldownMs?: number
  windowSize?: number
}

/** 取 30% 分位数当底噪：比最小值抗"某帧恰好很静"，比均值抗"整段都有说话声"。 */
const NOISE_QUANTILE = 0.3

export function noiseFloorOf(samples: readonly number[], quantile = NOISE_QUANTILE): number {
  const clean = samples.filter((value) => Number.isFinite(value) && value >= 0)
  if (clean.length === 0) {
    return 0
  }
  const sorted = [...clean].sort((left, right) => left - right)
  const index = Math.min(sorted.length - 1, Math.max(0, Math.floor(sorted.length * quantile)))
  return sorted[index]
}

export function thresholdFor(
  noiseFloor: number,
  options: BargeInOptions = {},
): number {
  const multiplier = options.multiplier ?? DEFAULT_MULTIPLIER
  const low = options.minThreshold ?? MIN_THRESHOLD
  const high = options.maxThreshold ?? MAX_THRESHOLD
  const raw = (Number.isFinite(noiseFloor) ? Math.max(0, noiseFloor) : 0) * multiplier
  // 夹紧顺序很重要：先夹到 [low, high]。若 high < low（调用方乱传），以 low 为准。
  return Math.min(Math.max(raw, low), Math.max(low, high))
}

/**
 * 打断判定器。
 *
 * 用法（语音面试里）：
 * 1. 每来一个电平样本调 `observe(level)`——底噪估计要**一直**更新，
 *    否则面试官说话期间的高电平会被当成"环境本来就吵"；
 * 2. 只在"面试官正在说话"时问 `shouldTrigger(level, now)`；
 * 3. 真的触发了就调 `markTriggered(now)`，进入冷却。
 */
export class BargeInDetector {
  private readonly options: BargeInOptions
  private readonly windowSize: number
  private readonly cooldownMs: number
  private readonly samples: number[] = []
  private lastTriggerAt = Number.NEGATIVE_INFINITY
  private lastLevel = 0

  constructor(options: BargeInOptions = {}) {
    this.options = options
    this.windowSize = Math.max(1, options.windowSize ?? NOISE_WINDOW)
    this.cooldownMs = Math.max(0, options.cooldownMs ?? DEFAULT_COOLDOWN_MS)
  }

  /** 记录一个电平样本并更新底噪估计。 */
  observe(level: number): void {
    if (!Number.isFinite(level) || level < 0) {
      return
    }
    this.lastLevel = level
    this.samples.push(level)
    if (this.samples.length > this.windowSize) {
      this.samples.splice(0, this.samples.length - this.windowSize)
    }
  }

  get noiseFloor(): number {
    return noiseFloorOf(this.samples)
  }

  get threshold(): number {
    return thresholdFor(this.noiseFloor, this.options)
  }

  get lastObservedLevel(): number {
    return this.lastLevel
  }

  /** 现在是否处在冷却窗内。 */
  inCooldown(now: number): boolean {
    if (!Number.isFinite(now)) {
      return false
    }
    return now - this.lastTriggerAt < this.cooldownMs
  }

  /**
   * 这个电平算不算"用户开口打断了面试官"。
   *
   * `now` 用调用方传入（`performance.now()`），**不在内部读时钟**——否则单测只能靠真等。
   */
  shouldTrigger(level: number, now: number): boolean {
    if (!Number.isFinite(level)) {
      return false
    }
    if (this.inCooldown(now)) {
      return false
    }
    return level >= this.threshold
  }

  /** 标记"刚刚触发了"，进入冷却。 */
  markTriggered(now: number): void {
    if (Number.isFinite(now)) {
      this.lastTriggerAt = now
    }
  }

  /** 清空底噪估计（例如换了输入设备、或重新开始采集）。 */
  reset(): void {
    this.samples.length = 0
    this.lastTriggerAt = Number.NEGATIVE_INFINITY
    this.lastLevel = 0
  }
}
