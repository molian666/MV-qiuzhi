/**
 * 面试官的"思考停顿"。
 *
 * **为什么要有一点停顿**：真人面试官听完回答不会立刻开口——他会停一下（在想要追问什么、
 * 或者只是点个头）。零延迟的回答在听感上就是"对讲机"：你一说完，对面立刻机械地报出
 * 下一句。这一点点延迟是对话感的来源。
 *
 * **但必须很短**：模型出题本身已经要 2-5 秒（非流式，见 `openai_compat` 的 `stream: False`），
 * 再叠一个明显的停顿只会让人以为卡住了。所以这里只补一个"接话前的那口气"级别的停顿，
 * 而且是**按文本长短浮动**的——长回答之后停顿略久一点，短回答之后几乎立刻接上。
 *
 * 抽成纯函数是为了能直接单测：随机数可注入，边界不用靠真实等待去验证。
 */

/** 不注入随机源时的默认区间（毫秒）。刻意很小，见文件头的说明。 */
export const MIN_THINKING_MS = 180
export const MAX_THINKING_MS = 450

/** 文本超过这个长度后，停顿逐步贴近上限 */
const LONG_ANSWER_CHARS = 220

export interface ThinkingPauseOptions {
  /** 注入随机源（测试用）。返回 [0, 1) */
  random?: () => number
}

/**
 * 按回答长度算一个停顿毫秒数。
 *
 * 空回答（没有上一轮，例如开场）给最短停顿：开场不需要"假装在想"。
 */
export function thinkingPauseMs(answerText = '', options: ThinkingPauseOptions = {}): number {
  const random = options.random ?? Math.random
  const length = answerText.trim().length

  if (length === 0) {
    return MIN_THINKING_MS
  }

  // 回答越长，"想一下"的时间略久：按长度在区间内线性插值
  const ratio = Math.min(1, length / LONG_ANSWER_CHARS)
  const span = MAX_THINKING_MS - MIN_THINKING_MS
  const jitter = span * 0.35 * clamp01(random())
  return Math.round(MIN_THINKING_MS + span * ratio * 0.65 + jitter)
}

function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0
  return Math.min(1, Math.max(0, value))
}
