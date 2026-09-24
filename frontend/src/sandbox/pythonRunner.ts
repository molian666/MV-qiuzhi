/** Python 判题（M13）：驱动 `python.worker.ts`。
 *
 * 与 JS 沙箱（`runner.ts`）返回同一个 `SandboxOutcome` 结构，所以页面不需要
 * 区分两条路径。
 *
 * 两个关键设计：
 *
 * 1. **Worker 是单例**。Pyodide 初始化要下 9MB wasm + 2.4MB 标准库 + numpy wheel，
 *    每次跑题重建一遍是不可接受的。只有超时被 `terminate()` 之后才重建。
 * 2. **初始化与运行用两套超时**。首次初始化慢是正常的（下载 + 解压），给 120 秒；
 *    单轮运行给 8 秒（Python 比 JS 慢，而且这里不是要限性能）。把两者混成一个
 *    超时，会让「网慢」看起来像「代码死循环」。
 */

import type { CompareMode, SandboxCaseResult, SandboxOutcome, SandboxTest } from './runner'
import PythonWorker from './python.worker?worker'

const INIT_TIMEOUT_MS = 120_000
const RUN_TIMEOUT_MS = 8_000

let worker: Worker | null = null
let ready: Promise<void> | null = null

/** Pyodide 的 indexURL：由 vite 的 BASE_URL 决定，托管在 public/pyodide/。 */
function indexUrl(): string {
  const base = import.meta.env.BASE_URL || '/'
  return `${base.endsWith('/') ? base : `${base}/`}pyodide/`
}

function spawn(onStatus?: (message: string) => void): Promise<void> {
  const instance = new PythonWorker()

  const promise = new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(() => {
      cleanup()
      instance.terminate()
      reject(new Error('Python 运行时加载超时（首次需要下载约 13MB，检查网络或稍后重试）'))
    }, INIT_TIMEOUT_MS)

    const onMessage = (event: MessageEvent) => {
      const message = event.data as { type?: string; message?: string; error?: string }
      if (message.type === 'status') {
        onStatus?.(message.message ?? '')
      } else if (message.type === 'ready') {
        cleanup()
        resolve()
      } else if (message.type === 'loadError') {
        cleanup()
        reject(new Error(message.error ?? 'Python 运行时加载失败'))
      }
    }

    function cleanup() {
      window.clearTimeout(timer)
      instance.removeEventListener('message', onMessage)
    }

    instance.addEventListener('message', onMessage)
    instance.postMessage({ type: 'init', indexUrl: indexUrl() })
  })

  worker = instance
  ready = promise
  // 初始化失败就清掉单例，下次调用可以重新试
  promise.catch(() => {
    worker = null
    ready = null
  })
  return promise
}

function ensureReady(onStatus?: (message: string) => void): Promise<void> {
  if (ready && worker) {
    return ready
  }
  return spawn(onStatus)
}

export async function runPythonTests(options: {
  code: string
  functionName: string
  tests: SandboxTest[]
  timeoutMs?: number
  onStatus?: (message: string) => void
}): Promise<SandboxOutcome> {
  const startedAt = Date.now()
  const timeoutMs = options.timeoutMs ?? RUN_TIMEOUT_MS
  const results: SandboxCaseResult[] = []
  let loadError = ''
  let interrupted = false

  try {
    await ensureReady(options.onStatus)
  } catch (error) {
    return {
      results,
      interrupted: false,
      loadError: error instanceof Error ? error.message : String(error),
      totalMs: Date.now() - startedAt,
    }
  }

  const instance = worker
  if (!instance) {
    return { results, interrupted: false, loadError: 'Python 运行时不可用', totalMs: Date.now() - startedAt }
  }

  try {
    const loaded = await new Promise<boolean>((resolve) => {
      const onMessage = (event: MessageEvent) => {
        const message = event.data as { type?: string; error?: string }
        if (message.type === 'loaded') {
          instance.removeEventListener('message', onMessage)
          resolve(true)
        } else if (message.type === 'loadError') {
          loadError = message.error ?? '代码无法运行'
          instance.removeEventListener('message', onMessage)
          resolve(false)
        }
      }
      instance.addEventListener('message', onMessage)
      instance.postMessage({ type: 'load', code: options.code, functionName: options.functionName })
    })

    if (!loaded) {
      return { results, interrupted: false, loadError, totalMs: Date.now() - startedAt }
    }

    // 逐例下发，每跑完一例立刻收到——超时终止时已完成的结果不丢
    const finished = new Promise<void>((resolve) => {
      const onMessage = (event: MessageEvent) => {
        const message = event.data as { type?: string; result?: SandboxCaseResult }
        if (message.type !== 'result' || !message.result) {
          return
        }
        results.push(message.result)
        if (results.length === options.tests.length) {
          instance.removeEventListener('message', onMessage)
          resolve()
        }
      }
      instance.addEventListener('message', onMessage)
      for (const test of options.tests) {
        instance.postMessage({
          type: 'run',
          test: {
            index: test.index,
            args: test.args,
            expected: test.expected,
            compare: test.compare ?? ('exact' satisfies CompareMode),
          },
        })
      }
    })

    const timedOut = await Promise.race([
      finished.then(() => false),
      new Promise<boolean>((resolve) => window.setTimeout(() => resolve(true), timeoutMs)),
    ])

    if (timedOut) {
      interrupted = true
      // Python 侧的同步死循环只能整轮杀掉；杀完必须重建 Worker
      instance.terminate()
      worker = null
      ready = null
      results.sort((a, b) => a.index - b.index)
    }
  } catch (error) {
    return {
      results,
      interrupted,
      loadError: error instanceof Error ? error.message : String(error),
      totalMs: Date.now() - startedAt,
    }
  }

  return { results, interrupted, loadError, totalMs: Date.now() - startedAt }
}

/** 页面卸载或明确释放时调用。平时不用管——单例留着反而省下一次 13MB 的加载。 */
export function disposePythonWorker(): void {
  worker?.terminate()
  worker = null
  ready = null
}
