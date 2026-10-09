/**
 * 断线重连的退避策略（M19 手机端）。
 *
 * 桌面浏览器上"连接断了就断了"还能忍——用户看到报错，自己刷新页面重开一场。
 * 手机上不行：**切后台、锁屏、地铁进隧道、Wi-Fi 换 4G**，WebSocket 一定会断。
 * 而一场面试正在进行的中间，用户既看不到控制台，也不该被迫重开。
 *
 * 所以退避规则本身要钉死（见 `backoff.test.mjs`）：第一次重试要快（多半是瞬时抖动），
 * 之后逐步拉长，但要封顶——否则一次长面试里能堆出成百上千次重试。
 *
 * 做成纯函数是刻意的：`SessionSocket` 里只放"什么时候调它"，
 * "算出来该等多久"全部可单测，不用真的等时间。
 */

/**: 第几次重试之后放弃（放弃后由 UI 提示用户手动重连）。 */
export const MAX_RECONNECT_ATTEMPTS = 8

/**: 首次重试延迟：网络瞬时抖动占多数，早一点试回来体感好得多。 */
export const BASE_DELAY_MS = 600

/**: 单次延迟上限。 */
export const MAX_DELAY_MS = 15_000

/**
 * 第 `attempt` 次重试（从 1 开始）该等多久。
 *
 * - 指数退避：600ms、1.2s、2.4s、4.8s…；
 * - 封顶 `MAX_DELAY_MS`：避免等到天荒地老；
 * - `jitterRatio` 抖动：多标签页/多设备同时重连时避免"齐步走"打在同一毫秒。
 *
 * `attempt <= 0`、`NaN` 这类异常输入按"第一次"处理——**不返回 NaN**：
 * `setTimeout(fn, NaN)` 会变成 0ms 立即重试，那是个隐藏的忙等循环。
 */
export function reconnectDelayMs(
  attempt: number,
  options: {
    baseMs?: number
    maxMs?: number
    jitterRatio?: number
    random?: () => number
  } = {},
): number {
  const base = options.baseMs ?? BASE_DELAY_MS
  const max = Math.max(base, options.maxMs ?? MAX_DELAY_MS)
  const jitterRatio = Math.min(Math.max(options.jitterRatio ?? 0.2, 0), 1)
  const random = options.random ?? Math.random

  const safeAttempt = Number.isFinite(attempt) ? Math.max(1, Math.floor(attempt)) : 1
  // 位运算会在 attempt 很大时溢出，用显式乘法 + 提前封顶
  const exponential = Math.min(max, base * 2 ** (safeAttempt - 1))

  const sample = random()
  const usable = Number.isFinite(sample) ? Math.min(Math.max(sample, 0), 1) : 0.5
  const jitter = exponential * jitterRatio * usable

  return Math.round(Math.min(max, exponential + jitter))
}

/** 还能不能继续重试。 */
export function canRetry(attempt: number): boolean {
  return Number.isFinite(attempt) && attempt <= MAX_RECONNECT_ATTEMPTS
}

/**
 * 重连尝试的计数状态机。
 *
 * 把"现在是第几次、要不要继续、这次等多久"抽出来，是为了让 `SessionSocket`
 * 里只剩 IO、而这些判断能被单测直接钉住（连计时器都不用装）。
 */
export class ReconnectPlan {
  private attempts = 0
  private readonly options: Parameters<typeof reconnectDelayMs>[1]

  constructor(options: Parameters<typeof reconnectDelayMs>[1] = {}) {
    this.options = options
  }

  /** 已经重试过几次。 */
  get attempt(): number {
    return this.attempts
  }

  get exhausted(): boolean {
    return !canRetry(this.attempts + 1)
  }

  /**
   * 规划下一次重连：返回等待毫秒数，或 `null` 表示已经放弃。
   * 停止后仍然可以再调，仍返回 `null`（幂等）。
   */
  next(): number | null {
    if (!canRetry(this.attempts + 1)) {
      return null
    }
    this.attempts += 1
    return reconnectDelayMs(this.attempts, this.options)
  }

  /** 连上了：清零，下次断线从头开始退避。 */
  reset(): void {
    this.attempts = 0
  }
}
