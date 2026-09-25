/** 面试间：文字与语音两种模式共用一个页面。
 *
 * **为什么合并**：这两种模式共用同一套「本场配置」（岗位、简历、面试官风格、
 * 压力与多角色），只是在**怎么说话**上不同——打字还是开麦。分成两个页面意味着
 * 同一套配置要设置两遍，而且看不出它们其实是一件事。
 *
 * **一次只跑一场**：两种模式各自维护独立会话。切换模式会卸载当前模式，
 * 进行中的会话就没了（后端数据还在，但页面状态丢了）。所以**面试进行中时禁止切换**，
 * 而不是让人切过去才发现会话不见了。
 */

import { useState } from 'react'
import InterviewLive from './InterviewLive'
import InterviewVoice from './InterviewVoice'

type Mode = 'text' | 'voice'

const MODES: { key: Mode; label: string; note: string }[] = [
  { key: 'text', label: '文字', note: '打字作答，适合梳理思路' },
  { key: 'voice', label: '语音', note: '开麦对话，练真实临场' },
]

export default function InterviewRoom({ onOpenReport }: { onOpenReport: (sessionId: number) => void }) {
  const [mode, setMode] = useState<Mode>('text')
  // 当前模式是否有一场进行中的面试——有则不允许切走
  const [busy, setBusy] = useState(false)

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-1 rounded-card border border-ink-1 bg-surface p-0.5">
          {MODES.map((item) => {
            const active = item.key === mode
            const locked = !active && busy
            return (
              <button
                key={item.key}
                type="button"
                disabled={locked}
                onClick={() => setMode(item.key)}
                aria-current={active ? 'true' : undefined}
                title={locked ? '当前有一场进行中的面试，先结束它再切换' : item.note}
                className={
                  active
                    ? 'rounded-control bg-ink-9 px-3 py-1 text-xs font-medium text-surface'
                    : locked
                      ? 'rounded-control px-3 py-1 text-xs text-ink-3'
                      : 'rounded-control px-3 py-1 text-xs text-ink-6 transition-colors hover:bg-paper'
                }
              >
                {item.label}
              </button>
            )
          })}
        </div>
        <p className="text-[11px] text-ink-4">
          {busy
            ? '面试进行中——结束后才能切换到另一种模式'
            : MODES.find((item) => item.key === mode)?.note}
        </p>
      </div>

      <div className="min-h-0 flex-1">
        {mode === 'text' ? (
          <InterviewLive onOpenReport={onOpenReport} onSessionChange={setBusy} />
        ) : (
          <InterviewVoice onOpenReport={onOpenReport} onSessionChange={setBusy} />
        )}
      </div>
    </div>
  )
}
