/** Excalidraw 白板包装（M13）。
 *
 * 这个模块**整体被 `React.lazy` 懒加载**：Excalidraw 体积不小，
 * 只有打开系统设计白板才下载，首屏不受影响。
 *
 * 场景用 Excalidraw 自己的 JSON 格式存取，所以历史作答可以**载入回来继续画**——
 * 这是设计题很实际的需求：第一遍想漏了，第二遍在原来的图上补。
 *
 * 画布内容不改动时不做序列化：Excalidraw 的 `onChange` 触发很频繁
 * （连鼠标移动都会触发），每次都 `JSON.stringify` 整个场景会明显卡。
 */

import { Excalidraw } from '@excalidraw/excalidraw'
import '@excalidraw/excalidraw/index.css'
import { useMemo } from 'react'

/** 把存下来的场景 JSON 还原成 Excalidraw 的 elements。 */
function parseScene(scene: string): readonly unknown[] {
  if (!scene.trim()) {
    return []
  }
  try {
    const parsed: unknown = JSON.parse(scene)
    if (Array.isArray(parsed)) {
      return parsed
    }
    if (parsed && typeof parsed === 'object' && 'elements' in parsed) {
      const elements = (parsed as { elements: unknown }).elements
      return Array.isArray(elements) ? elements : []
    }
  } catch {
    // 存坏了就当空画布，不要因为一段坏 JSON 让整页打不开
  }
  return []
}

export function serializeScene(elements: readonly unknown[]): string {
  return JSON.stringify({ type: 'excalidraw', version: 2, elements })
}

export default function Whiteboard({
  initialScene,
  onChange,
  height = '520px',
}: {
  initialScene: string
  onChange: (scene: string) => void
  height?: string
}) {
  const initialElements = useMemo(() => parseScene(initialScene), [initialScene])

  return (
    <div className="overflow-hidden rounded-lg border border-slate-300" style={{ height }}>
      <Excalidraw
        initialData={{
          elements: initialElements as never,
          appState: { viewBackgroundColor: '#ffffff' },
        }}
        onChange={(elements) => onChange(serializeScene(elements))}
        langCode="zh-CN"
      />
    </div>
  )
}
