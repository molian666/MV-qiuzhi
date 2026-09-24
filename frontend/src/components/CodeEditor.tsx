/** Monaco 包装（M13）。
 *
 * 这个模块**整体被 `React.lazy` 懒加载**：只有打开算法题白板才下载编辑器，
 * 首屏不受它体积的影响。
 *
 * Monaco 默认从 CDN 拉资源，但本项目的定位是本地/内网也能用（判题都在浏览器端
 * 跑，就是为了不依赖服务端），所以改成打包进本地：`loader.config({ monaco })`
 * 加上本地 worker。
 *
 * 路径要注意：0.56 的 `exports` 是 `"./*": "./esm/vs/*.js"`，所以子路径必须写成
 * `monaco-editor/editor/editor.worker`，写成 `monaco-editor/esm/vs/...` 会被解析成
 * `esm/vs/esm/vs/...` 而找不到。
 */

import Editor, { loader } from '@monaco-editor/react'
import * as monaco from 'monaco-editor'
import editorWorker from 'monaco-editor/editor/editor.worker?worker'

// Monaco 通过 MonacoEnvironment.getWorker 取 worker；只注册基础 worker
;(globalThis as unknown as { MonacoEnvironment: unknown }).MonacoEnvironment = {
  getWorker: () => new editorWorker(),
}

loader.config({ monaco })

export default function CodeEditor({
  value,
  onChange,
  language = 'javascript',
  readOnly = false,
  height = '420px',
}: {
  value: string
  onChange: (next: string) => void
  language?: string
  readOnly?: boolean
  height?: string
}) {
  return (
    <div className="overflow-hidden rounded-lg border border-slate-300">
      <Editor
        height={height}
        language={language === 'python' ? 'python' : 'javascript'}
        theme="vs"
        value={value}
        onChange={(next) => onChange(next ?? '')}
        options={{
          readOnly,
          minimap: { enabled: false },
          fontSize: 13,
          lineNumbers: 'on',
          scrollBeyondLastLine: false,
          tabSize: 2,
          automaticLayout: true,
          renderLineHighlight: 'none',
          padding: { top: 10, bottom: 10 },
        }}
        loading={<div className="p-4 text-xs text-slate-400">正在加载编辑器…</div>}
      />
    </div>
  )
}
