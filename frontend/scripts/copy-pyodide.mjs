/**
 * 把 Pyodide 运行时从 node_modules 复制到 public/pyodide/（dev 与 build 前都会跑）。
 *
 * 为什么这么做而不是直接 import：Pyodide 的核心是 9MB 的 wasm + 2.4MB 标准库，
 * 让 bundler 处理它既慢又容易出岔子；而这些东西也**不该进 git 仓库**。
 * 复制到 public/ 之后由本机托管，于是「Python 判题」在离线/内网环境下也能用。
 *
 * 唯一不在 npm 包里的是 numpy 的 wheel（Pyodide 按 pyodide-lock.json 按需下载）。
 * 这里顺手把它也取下来并校验 sha256 —— 校验过才敢说「离线可用」，
 * 否则断网时会在运行时才暴露出来。
 */

import { createHash } from 'node:crypto'
import { access, mkdir, readFile, writeFile, copyFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(here, '..')
const source = path.join(root, 'node_modules', 'pyodide')
const target = path.join(root, 'public', 'pyodide')

/** 只带运行必需的：wasm、标准库、加载器、lock 文件。.map 与 UMD 版都不需要。 */
const RUNTIME_FILES = [
  'pyodide.mjs',
  'pyodide.asm.mjs',
  'pyodide.asm.wasm',
  'python_stdlib.zip',
  'pyodide-lock.json',
]

async function exists(file) {
  try {
    await access(file)
    return true
  } catch {
    return false
  }
}

async function copyRuntime(pkg) {
  await mkdir(target, { recursive: true })
  let copied = 0
  for (const name of RUNTIME_FILES) {
    const from = path.join(source, name)
    if (!(await exists(from))) {
      throw new Error(`pyodide 包里缺少 ${name}，版本可能变了`)
    }
    await copyFile(from, path.join(target, name))
    copied += 1
  }
  return { copied, version: pkg.version }
}

/**
 * 取 numpy 的 wheel。已经在本地且哈希对得上就跳过，省掉每次 build 的网络往返。
 * 失败不致命：Pyodide 运行时找不到本地文件会回落到官方 CDN，只是那样首次
 * 使用需要联网——所以这里只警告，不中断构建。
 *
 * 会重试并换镜像：实测过一次 2.9MB 下载中途被重置（本机有系统代理），
 * 单次失败不代表拿不到。
 */
async function ensureNumpyWheel(lock, version) {
  const entry = lock?.packages?.numpy
  if (!entry?.file_name) {
    console.warn('  [警告] pyodide-lock.json 里没有 numpy 条目，Python 判题首次运行需要联网')
    return
  }

  const destination = path.join(target, entry.file_name)
  if (await exists(destination)) {
    const digest = createHash('sha256')
      .update(await readFile(destination))
      .digest('hex')
    if (!entry.sha256 || digest === entry.sha256) {
      console.log(`  [跳过] numpy wheel 已在本地且校验通过`)
      return
    }
    console.warn('  [警告] 本地 numpy wheel 校验不通过，重新下载')
  }

  const mirrors = [
    `https://cdn.jsdelivr.net/pyodide/v${version}/full/${entry.file_name}`,
    `https://fastly.jsdelivr.net/pyodide/v${version}/full/${entry.file_name}`,
  ]

  const attempts = mirrors.flatMap((url) => [url, url]) // 每个镜像各试两次
  for (const [attempt, url] of attempts.entries()) {
    try {
      // 短超时：构建不该被一次网络抖动卡住两分钟（实测 node 直连会卡很久）。
      // 真取不到就用 scripts/fetch-numpy-wheel.ps1 手动补，那条路走系统代理。
      const response = await fetch(url, { signal: AbortSignal.timeout(20_000) })
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }
      const buffer = Buffer.from(await response.arrayBuffer())
      if (entry.sha256) {
        const digest = createHash('sha256').update(buffer).digest('hex')
        if (digest !== entry.sha256) {
          throw new Error(`sha256 不匹配（期望 ${entry.sha256.slice(0, 12)}…，实得 ${digest.slice(0, 12)}…）`)
        }
      }
      await writeFile(destination, buffer)
      console.log(
        `  [下载] numpy wheel ${(buffer.length / 1024 / 1024).toFixed(1)} MB，sha256 校验通过`,
      )
      return
    } catch (error) {
      const reason = error instanceof Error ? error.message : String(error)
      const last = attempt === attempts.length - 1
      console.warn(`  [${last ? '警告' : '重试'}] 取 numpy wheel 失败（${reason}）`)
    }
  }

  console.warn('         Python 判题仍可用：浏览器会从官方 CDN 取 numpy（首次约 3MB，之后走缓存）。')
  console.warn('         要完全离线：pwsh -File scripts/fetch-numpy-wheel.ps1')
}

async function main() {
  if (!(await exists(source))) {
    console.warn('[pyodide] 没装 pyodide（pnpm install），跳过。Python 判题将不可用。')
    return
  }

  const pkg = JSON.parse(await readFile(path.join(source, 'package.json'), 'utf8'))
  const { copied, version } = await copyRuntime(pkg)
  console.log(`[pyodide] 运行时 ${copied} 个文件已就绪（v${version}）→ public/pyodide/`)

  const lock = JSON.parse(await readFile(path.join(source, 'pyodide-lock.json'), 'utf8'))
  await ensureNumpyWheel(lock, version)
}

main().catch((error) => {
  console.error('[pyodide] 准备失败：', error instanceof Error ? error.message : error)
  process.exitCode = 1
})
