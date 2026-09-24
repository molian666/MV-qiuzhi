/** 浏览器端判题沙箱（M13）。
 *
 * PLAN §9.2 明确要求判题在浏览器端做：本机没有 docker，起不了隔离容器，
 * 在本机跑用户或模型生成的代码等于把自己的机器交出去。
 *
 * **先说清它是什么、不是什么**：
 * - 是：防误伤（死循环不会卡死页面）+ 收敛外泄面（屏蔽网络类 API）。
 * - 不是：真正的安全隔离。跑的是你自己的代码，Worker 与页面同源同权限。
 *   所以这里只跑**你自己写的答案**，别把来路不明的代码粘进来。
 *   Worker 里没有 `localStorage`，所以会话 token 天然读不到——但 `fetch`
 *   会带上 cookie，因此这里显式把它禁掉。
 *
 * **超时是怎么做的**：Worker 是单线程的，同步跑飞的代码没法从内部打断，
 * 只能整轮 `terminate()`。为了让已经跑完的用例不白跑，Worker 每跑完一例就
 * 立刻回传，主线程收到即存；到点终止时已完成的结果都还在。
 */

export type CompareMode = 'exact' | 'unordered'

export interface SandboxTest {
  index: number
  args: unknown[]
  expected: unknown
  compare?: CompareMode
}

export interface SandboxCaseResult {
  index: number
  passed: boolean
  actual: unknown
  error: string
  durationMs: number | null
  /** 用户代码里的 print 输出（目前只有 Python 侧采集）。调试用。 */
  stdout?: string
}

export interface SandboxOutcome {
  /** 实际跑完的用例（超时被终止时可能少于传入数量） */
  results: SandboxCaseResult[]
  /** 是否因为超时被强制终止 */
  interrupted: boolean
  /** 装载代码本身失败（语法错误等），此时 results 为空 */
  loadError: string
  totalMs: number
}

/** Worker 源码。
 *
 * 用户代码通过 `postMessage` 送进来，**不拼进这段源码**——拼接会让代码里的
 * 反引号、`${}` 之类破坏模板，也更容易出意外。
 */
const WORKER_SOURCE = `
const net = ['fetch', 'XMLHttpRequest', 'WebSocket', 'EventSource', 'importScripts', 'indexedDB', 'caches', 'navigator'];
for (const key of net) {
  try {
    delete self[key];
    if (self[key] !== undefined) self[key] = undefined;
  } catch (e) { /* 只读属性，忽略——它只是纵深防御的一层 */ }
}

let fn = null;

function stable(value) {
  if (value === undefined) return 'undefined';
  if (value === null || typeof value !== 'object') return JSON.stringify(value) ?? 'undefined';
  if (Array.isArray(value)) return '[' + value.map(stable).join(',') + ']';
  if (value instanceof Set) return 'Set(' + [...value].map(stable).sort().join(',') + ')';
  if (value instanceof Map) return 'Map(' + [...value.entries()].map(e => stable(e)).sort().join(',') + ')';
  const keys = Object.keys(value).sort();
  return '{' + keys.map(k => JSON.stringify(k) + ':' + stable(value[k])).join(',') + '}';
}

/** 「顺序无关」的题（Top-K 之类）：比较多重集而不是序列 */
function unordered(value) {
  if (!Array.isArray(value)) return stable(value);
  return '[' + value.map(stable).sort().join(',') + ']';
}

function same(actual, expected, mode) {
  return (mode === 'unordered' ? unordered(actual) : stable(actual)) ===
         (mode === 'unordered' ? unordered(expected) : stable(expected));
}

self.onmessage = (event) => {
  const message = event.data || {};

  if (message.type === 'load') {
    try {
      // new Function 而不是 eval：作用域干净，且能同时接受函数声明与变量赋值两种写法
      const factory = new Function(message.code + '\\n;return typeof ' + message.functionName +
        ' === "function" ? ' + message.functionName + ' : undefined;');
      fn = factory();
      if (typeof fn !== 'function') {
        self.postMessage({ type: 'loadError', error: '代码里没有找到函数 ' + message.functionName + '。' });
        return;
      }
      self.postMessage({ type: 'loaded' });
    } catch (error) {
      self.postMessage({ type: 'loadError', error: String((error && error.message) || error) });
    }
    return;
  }

  if (message.type === 'run') {
    const test = message.test;
    const started = performance.now();
    try {
      const actual = fn.apply(null, test.args);
      self.postMessage({
        type: 'result',
        result: {
          index: test.index,
          passed: same(actual, test.expected, test.compare),
          actual: describe(actual),
          error: '',
          durationMs: Math.round(performance.now() - started),
        },
      });
    } catch (error) {
      self.postMessage({
        type: 'result',
        result: {
          index: test.index,
          passed: false,
          actual: null,
          error: String((error && error.message) || error),
          durationMs: Math.round(performance.now() - started),
        },
      });
    }
  }
};

/** 回传前把值收敛成能结构化克隆的东西：函数、循环引用都不能 postMessage */
function describe(value) {
  try {
    return JSON.parse(JSON.stringify(value === undefined ? null : value));
  } catch (e) {
    try { return String(value); } catch (e2) { return '（无法序列化的返回值）'; }
  }
}
`

const DEFAULT_TIMEOUT_MS = 4000

export async function runTests(options: {
  code: string
  functionName: string
  tests: SandboxTest[]
  timeoutMs?: number
}): Promise<SandboxOutcome> {
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS
  const startedAt = Date.now()

  const blob = new Blob([WORKER_SOURCE], { type: 'application/javascript' })
  const url = URL.createObjectURL(blob)
  const worker = new Worker(url)

  const results: SandboxCaseResult[] = []
  let loadError = ''
  let interrupted = false

  try {
    const loaded = await new Promise<boolean>((resolve) => {
      const onMessage = (event: MessageEvent) => {
        const message = event.data as { type?: string; error?: string }
        if (message.type === 'loaded') {
          worker.removeEventListener('message', onMessage)
          resolve(true)
        } else if (message.type === 'loadError') {
          loadError = message.error ?? '代码无法解析'
          worker.removeEventListener('message', onMessage)
          resolve(false)
        }
      }
      worker.addEventListener('message', onMessage)
      worker.postMessage({ type: 'load', code: options.code, functionName: options.functionName })

      window.setTimeout(() => {
        worker.removeEventListener('message', onMessage)
        loadError = '装载代码超时'
        resolve(false)
      }, timeoutMs)
    })

    if (!loaded) {
      return { results, interrupted: false, loadError, totalMs: Date.now() - startedAt }
    }

    // 逐例下发：每跑完一例立刻收到，所以超时终止时已完成的结果不丢
    const finished = new Promise<void>((resolve) => {
      const onMessage = (event: MessageEvent) => {
        const message = event.data as { type?: string; result?: SandboxCaseResult }
        if (message.type !== 'result' || !message.result) {
          return
        }
        results.push(message.result)
        if (results.length === options.tests.length) {
          worker.removeEventListener('message', onMessage)
          resolve()
        }
      }
      worker.addEventListener('message', onMessage)
      for (const test of options.tests) {
        worker.postMessage({ type: 'run', test })
      }
    })

    const timedOut = await Promise.race([
      finished.then(() => false),
      new Promise<boolean>((resolve) => window.setTimeout(() => resolve(true), timeoutMs)),
    ])

    if (timedOut) {
      // 同步代码跑飞时只能整轮终止——所以界面上要把「未跑完」说清楚，
      // 不能当成通过（M13 的评估正是拿这个事实说话）
      interrupted = true
      results.sort((a, b) => a.index - b.index)
    }
  } finally {
    worker.terminate()
    URL.revokeObjectURL(url)
  }

  return { results, interrupted, loadError, totalMs: Date.now() - startedAt }
}

/** 把沙箱结果换成后端要的格式（只回传事实，期望值由服务端从题库补）。 */
export function toReportedCases(results: SandboxCaseResult[]) {
  return results.map((result) => ({
    index: result.index,
    passed: result.passed,
    actual: result.actual,
    error: result.error,
  }))
}
