/** Python 判题沙箱（M13）：Pyodide + numpy，跑在独立 Worker 里。
 *
 * **为什么必须是独立 Worker**：Pyodide 初始化要几秒（下 9MB 的 wasm + 解压标准库），
 * 放主线程会把界面卡死；更要紧的是，跑飞的 Python 代码（死循环）没法从内部打断，
 * 只能靠整轮 `terminate()` —— 这也需要它不在主线程上。
 *
 * **资源从本机取，不走 CDN**：`/pyodide/` 由 `scripts/copy-pyodide.mjs` 在 dev/build
 * 前从 node_modules 复制到 `public/`，所以既不占仓库体积，又能离线用。
 * 唯一例外是 numpy 的 wheel（不在 npm 包里），由 Pyodide 自己按 lock 文件下载，
 * 所以**第一次用 Python 判题需要联网**——界面里会说明这一点。
 *
 * 与 JS 沙箱一样，说清楚它不是什么：**这不是安全隔离**。Worker 与页面同源同权限，
 * 只防误伤（卡死）与顺手的外泄（这里也把网络类 API 掐掉了）。所以只跑你自己写的答案。
 */

/// <reference lib="webworker" />

export interface PythonRunRequest {
  index: number
  args: unknown[]
  expected: unknown
  compare?: string
}

/** 只做比较与调用，不含任何用户代码——用户代码在下面被 exec 进独立命名空间。 */
const SANDBOX_PY = `
import io
import json
import numpy as np

__print_buffer = io.StringIO()


def __sandbox_convert(value):
    """嵌套 list 转成 np.ndarray（视觉题里的图像、boxes 本来就是数组）；标量原样。"""
    if isinstance(value, list):
        return np.array(value)
    if isinstance(value, dict):
        return {k: __sandbox_convert(v) for k, v in value.items()}
    return value


def __sandbox_equal(actual, expected, compare):
    """数值（含数组、列表）用 allclose 比，其他用相等比。

    allclose 而不是 == 是因为浮点算子（IoU、卷积、softmax）手写实现必有
    1e-16 量级的差异，用 == 会让所有正确解都判失败。
    """
    if compare == "unordered":
        try:
            left = np.sort(np.asarray(actual).ravel())
            right = np.sort(np.asarray(expected).ravel())
            return bool(left.shape == right.shape and np.array_equal(left, right))
        except Exception:
            return False

    try:
        left = np.asarray(actual)
        right = np.asarray(expected)
    except Exception:
        return actual == expected

    if left.shape != right.shape:
        return False
    if left.dtype.kind in "biufc" and right.dtype.kind in "biufc":
        return bool(np.allclose(left, right, rtol=1e-6, atol=1e-8))
    return bool(np.array_equal(left, right))


def __sandbox_load(code, function_name):
    """执行用户代码并取出目标函数。作用域干净，不污染沙箱自己的命名空间。"""
    namespace = {"np": np, "numpy": np}
    exec(compile(code, "<你的代码>", "exec"), namespace)
    fn = namespace.get(function_name)
    if not callable(fn):
        raise NameError(
            "代码里没有找到函数 " + function_name + "（注意函数名要和题目一致）"
        )
    return fn


def __sandbox_call(fn, args_json, expected_json, compare):
    args = [__sandbox_convert(a) for a in json.loads(args_json)]
    expected = json.loads(expected_json)
    actual = fn(*args)
    passed = __sandbox_equal(actual, expected, compare)
    try:
        shown = actual.tolist() if isinstance(actual, np.ndarray) else actual
        json.dumps(shown)
    except Exception:
        shown = repr(actual)
    return [passed, shown]
`

type Post = (message: unknown) => void
const post: Post = (message) => (self as unknown as Worker).postMessage(message)

type Pyodide = {
  runPython: (code: string) => unknown
  globals: {
    get: (name: string) => unknown
    set: (name: string, value: unknown) => void
  }
  loadPackage: (name: string) => Promise<unknown>
  setStdout: (options: { batched: (text: string) => void }) => void
  setStderr: (options: { batched: (text: string) => void }) => void
}

let pyodide: Pyodide | null = null
/** 装载好的用户函数（PyProxy）。Worker 是单线程，模块级变量就够。 */
let compiledFn: unknown = null
let printed: string[] = []

/** 掐掉网络类 API。Worker 里没有 localStorage（会话 token 天然读不到），
 *  但 fetch 会带上 cookie，所以显式禁掉——是纵深防御的一层。 */
for (const key of ['fetch', 'XMLHttpRequest', 'WebSocket', 'EventSource', 'indexedDB', 'caches']) {
  try {
    delete (self as unknown as Record<string, unknown>)[key]
  } catch {
    // 只读属性：忽略
  }
}

async function init(indexUrl: string): Promise<void> {
  post({ type: 'status', message: '正在加载 Python 运行时（首次约十几 MB，之后走缓存）…' })
  // 动态 import 而不是打包进来：让 Pyodide 自己从 indexURL 解析它的 wasm 与标准库，
  // 绕过 bundler 对 wasm 的处理，这是它官方推荐的用法。
  const module = (await import(/* @vite-ignore */ `${indexUrl}pyodide.mjs`)) as {
    loadPyodide: (options: { indexURL: string }) => Promise<Pyodide>
  }
  pyodide = await module.loadPyodide({ indexURL: indexUrl })

  pyodide.setStdout({ batched: (text) => printed.push(text) })
  pyodide.setStderr({ batched: (text) => printed.push(text) })

  post({ type: 'status', message: '正在加载 numpy…' })
  await pyodide.loadPackage('numpy')

  pyodide.runPython(SANDBOX_PY)
  post({ type: 'ready' })
}

async function load(code: string, functionName: string): Promise<void> {
  if (!pyodide) {
    post({ type: 'loadError', error: 'Python 运行时还没就绪' })
    return
  }
  printed = []
  compiledFn = null
  try {
    compiledFn = pyodide.runPython(
      `__sandbox_load(${JSON.stringify(code)}, ${JSON.stringify(functionName)})`,
    )
    post({ type: 'loaded' })
  } catch (error) {
    const text = error instanceof Error ? error.message : String(error)
    post({ type: 'loadError', error: lastLine(text) })
  }
}

function run(test: PythonRunRequest): void {
  if (!pyodide) {
    post({ type: 'result', result: fail(test.index, 'Python 运行时还没就绪') })
    return
  }
  if (!compiledFn) {
    post({ type: 'result', result: fail(test.index, '代码还没装载成功') })
    return
  }
  const started = performance.now()
  printed = []
  try {
    pyodide.globals.set('__fn', compiledFn)
    pyodide.globals.set('__args', JSON.stringify(test.args))
    pyodide.globals.set('__expected', JSON.stringify(test.expected))
    pyodide.globals.set('__compare', test.compare ?? 'exact')

    const outcome = pyodide.runPython(
      '__sandbox_call(__fn, __args, __expected, __compare)',
    ) as { toJs: () => [boolean, unknown] }
    const [passed, actual] = outcome.toJs()

    post({
      type: 'result',
      result: {
        index: test.index,
        passed: Boolean(passed),
        actual,
        error: '',
        durationMs: Math.round(performance.now() - started),
        stdout: printed.join(''),
      },
    })
  } catch (error) {
    const text = error instanceof Error ? error.message : String(error)
    post({
      type: 'result',
      result: {
        index: test.index,
        passed: false,
        actual: null,
        error: lastLine(text),
        durationMs: Math.round(performance.now() - started),
        stdout: printed.join(''),
      },
    })
  }
}

function fail(index: number, error: string) {
  return { index, passed: false, actual: null, error, durationMs: null, stdout: '' }
}

/** Pyodide 的错误堆栈很长，最后一行才是真正的原因。 */
function lastLine(text: string): string {
  const lines = text.split('\n').filter((line) => line.trim())
  const tail = lines[lines.length - 1] ?? text
  return tail.replace(/^[\w.]*Error:\s*/, '') || tail
}

self.onmessage = async (event: MessageEvent) => {
  const message = event.data as {
    type?: string
    indexUrl?: string
    code?: string
    functionName?: string
    test?: PythonRunRequest
  }

  if (message.type === 'init' && message.indexUrl) {
    try {
      await init(message.indexUrl)
    } catch (error) {
      const text = error instanceof Error ? error.message : String(error)
      post({ type: 'loadError', error: `Python 运行时加载失败：${lastLine(text)}` })
    }
    return
  }

  if (message.type === 'load') {
    await load(message.code ?? '', message.functionName ?? '')
    return
  }

  if (message.type === 'run' && message.test) {
    run(message.test)
  }
}
