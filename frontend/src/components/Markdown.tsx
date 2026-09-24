/** 轻量 Markdown 渲染（M13）。
 *
 * 题库答案与算法题题干只用到标题、表格、列表、代码块、粗体、行内代码这几种标记。
 * 为此引 react-markdown + remark 一整套不值得——这里按行做状态机解析，
 * 够用、可预测，而且没有依赖。
 *
 * 解析不了的东西**原样显示**，不会吞内容：题库的答案是要读的，
 * 渲染器丢字比渲染得难看严重得多。
 */

import type { ReactNode } from 'react'

type Block =
  | { kind: 'heading'; level: number; text: string }
  | { kind: 'paragraph'; text: string }
  | { kind: 'list'; ordered: boolean; items: string[] }
  | { kind: 'code'; text: string }
  | { kind: 'table'; header: string[]; rows: string[][] }

function splitRow(line: string): string[] {
  return line
    .replace(/^\s*\|/, '')
    .replace(/\|\s*$/, '')
    .split('|')
    .map((cell) => cell.trim())
}

/** 表格分隔行，如 `|---|---|` */
function isSeparator(line: string): boolean {
  return /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(line) && line.includes('-')
}

function parse(text: string): Block[] {
  const lines = text.split('\n')
  const blocks: Block[] = []
  let index = 0

  while (index < lines.length) {
    const line = lines[index]

    if (!line.trim()) {
      index += 1
      continue
    }

    // 代码块
    if (line.trim().startsWith('```')) {
      const body: string[] = []
      index += 1
      while (index < lines.length && !lines[index].trim().startsWith('```')) {
        body.push(lines[index])
        index += 1
      }
      index += 1 // 跳过结束的 ```
      blocks.push({ kind: 'code', text: body.join('\n') })
      continue
    }

    // 标题
    const heading = line.match(/^(#{1,6})\s+(.*)$/)
    if (heading) {
      blocks.push({ kind: 'heading', level: heading[1].length, text: heading[2].trim() })
      index += 1
      continue
    }

    // 表格：表头 + 分隔行
    if (line.includes('|') && index + 1 < lines.length && isSeparator(lines[index + 1])) {
      const header = splitRow(line)
      index += 2
      const rows: string[][] = []
      while (index < lines.length && lines[index].includes('|') && lines[index].trim()) {
        rows.push(splitRow(lines[index]))
        index += 1
      }
      blocks.push({ kind: 'table', header, rows })
      continue
    }

    // 列表（有序 / 无序）
    if (/^\s*[-*]\s+/.test(line) || /^\s*\d+\.\s+/.test(line)) {
      const ordered = /^\s*\d+\.\s+/.test(line)
      const items: string[] = []
      while (index < lines.length && /^\s*(?:[-*]|\d+\.)\s+/.test(lines[index])) {
        items.push(lines[index].replace(/^\s*(?:[-*]|\d+\.)\s+/, '').trim())
        index += 1
      }
      blocks.push({ kind: 'list', ordered, items })
      continue
    }

    // 段落：连续非空行合成一段
    const paragraph: string[] = []
    while (
      index < lines.length &&
      lines[index].trim() &&
      !lines[index].trim().startsWith('```') &&
      !/^#{1,6}\s/.test(lines[index]) &&
      !/^\s*(?:[-*]|\d+\.)\s+/.test(lines[index]) &&
      !(lines[index].includes('|') && index + 1 < lines.length && isSeparator(lines[index + 1]))
    ) {
      paragraph.push(lines[index].trim())
      index += 1
    }
    blocks.push({ kind: 'paragraph', text: paragraph.join(' ') })
  }

  return blocks
}

/** 行内的 `**粗体**` 与 `` `代码` ``。 */
function inline(text: string, keyPrefix: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((piece, index) => {
    const key = `${keyPrefix}-${index}`
    if (piece.startsWith('**') && piece.endsWith('**')) {
      return (
        <strong key={key} className="font-semibold text-slate-900">
          {piece.slice(2, -2)}
        </strong>
      )
    }
    if (piece.startsWith('`') && piece.endsWith('`')) {
      return (
        <code key={key} className="rounded bg-slate-100 px-1 py-0.5 text-[12px]">
          {piece.slice(1, -1)}
        </code>
      )
    }
    return <span key={key}>{piece}</span>
  })
}

export default function Markdown({ text }: { text: string }) {
  const blocks = parse(text)

  return (
    <div className="space-y-2.5 text-sm leading-relaxed text-slate-700">
      {blocks.map((block, blockIndex) => {
        const key = `block-${blockIndex}`
        switch (block.kind) {
          case 'heading':
            return block.level <= 2 ? (
              <h3 key={key} className="pt-1 text-sm font-semibold text-slate-900">
                {inline(block.text, key)}
              </h3>
            ) : (
              <h4 key={key} className="pt-1 text-xs font-semibold text-slate-700">
                {inline(block.text, key)}
              </h4>
            )
          case 'code':
            return (
              <pre
                key={key}
                className="overflow-x-auto rounded-md bg-slate-900 px-3 py-2 font-mono text-[11px] leading-relaxed text-slate-100"
              >
                {block.text}
              </pre>
            )
          case 'list':
            return block.ordered ? (
              <ol key={key} className="list-decimal space-y-1 pl-5">
                {block.items.map((item, itemIndex) => (
                  <li key={`${key}-${itemIndex}`}>{inline(item, `${key}-${itemIndex}`)}</li>
                ))}
              </ol>
            ) : (
              <ul key={key} className="list-disc space-y-1 pl-5">
                {block.items.map((item, itemIndex) => (
                  <li key={`${key}-${itemIndex}`}>{inline(item, `${key}-${itemIndex}`)}</li>
                ))}
              </ul>
            )
          case 'table':
            return (
              <div key={key} className="overflow-x-auto">
                <table className="w-full border-collapse text-xs">
                  <thead>
                    <tr>
                      {block.header.map((cell, cellIndex) => (
                        <th
                          key={`${key}-h-${cellIndex}`}
                          className="border border-slate-200 bg-slate-50 px-2 py-1.5 text-left font-semibold text-slate-700"
                        >
                          {inline(cell, `${key}-h-${cellIndex}`)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {block.rows.map((row, rowIndex) => (
                      <tr key={`${key}-r-${rowIndex}`}>
                        {row.map((cell, cellIndex) => (
                          <td
                            key={`${key}-r-${rowIndex}-${cellIndex}`}
                            className="border border-slate-200 px-2 py-1.5 align-top text-slate-600"
                          >
                            {inline(cell, `${key}-r-${rowIndex}-${cellIndex}`)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          default:
            return <p key={key}>{inline(block.text, key)}</p>
        }
      })}
    </div>
  )
}
