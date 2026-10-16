/** 历史面试：列表 + 回合详情（回听与报告分别在 M1/M3 加入）。 */

import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import type { SessionDetail, SessionSummary } from '../api/types'

const STATUS_LABEL: Record<string, string> = {
  live: '进行中',
  finished: '已结束',
  created: '未开始',
  failed: '失败',
}

const SPEAKER_LABEL: Record<string, string> = {
  interviewer: '面试官',
  candidate: '我',
  system: '系统',
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function formatDuration(ms: number | null): string {
  if (!ms) return '—'
  const totalSeconds = Math.round(ms / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  return minutes > 0 ? `${minutes} 分 ${totalSeconds % 60} 秒` : `${totalSeconds} 秒`
}

function formatTime(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

interface Props {
  onOpenReport?: (sessionId: number) => void
}

export default function Sessions({ onOpenReport }: Props) {
  const [items, setItems] = useState<SessionSummary[]>([])
  const [detail, setDetail] = useState<SessionDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setItems(await api.listSessions())
      setError('')
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const openDetail = async (id: number) => {
    try {
      setDetail(await api.getSession(id))
      setError('')
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  /** 删除一场面试。会连带回合、作答、报告与磁盘上的录音文件——所以要先确认。 */
  const remove = async (id: number) => {
    if (!window.confirm(`删除面试 #${id}？它的回合、报告与录音文件都会一起删掉，不能撤销。`)) {
      return
    }
    try {
      await api.deleteSession(id)
      if (detail?.id === id) {
        setDetail(null)
      }
      await load()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  /** 逐回合回放：单独 new Audio，避免和"整场播放器"互相抢播放。 */
  const playTurn = async (url: string) => {
    try {
      await new Audio(url).play()
    } catch (err) {
      setError(`无法播放该回合：${errorMessage(err)}`)
    }
  }

  return (
        <div className="grid min-h-0 gap-4 lg:h-full lg:grid-cols-[minmax(0,1fr)_380px]">
      <section className="flex min-h-0 flex-col rounded-panel border border-ink-1 bg-surface">
        <header className="flex items-center justify-between border-b border-ink-1 px-4 py-3">
          <h2 className="text-base font-semibold text-ink-9">历史面试</h2>
          <button
            type="button"
            onClick={() => void load()}
            className="rounded-control border border-ink-2 px-3 py-1.5 text-xs text-ink-7 hover:bg-paper"
          >
            刷新
          </button>
        </header>

        {error && <p className="border-b border-ng-soft bg-ng-soft px-4 py-2 text-xs text-ng">{error}</p>}

        {loading ? (
          <p className="px-4 py-6 text-sm text-ink-6">加载中…</p>
        ) : items.length === 0 ? (
          <p className="px-4 py-6 text-sm text-ink-6">还没有面试记录。去「面试间」跑一场文字面试。</p>
        ) : (
          <div className="lg:min-h-0 lg:flex-1 lg:overflow-auto">
            {/* 中文没有词边界：列一窄就逐字断行（"已结束"竖成三行）。
                所以固定列一律 nowrap，只让"岗位"这一列自己换行。 */}
            <table className="w-full whitespace-nowrap text-sm">
              <thead className="bg-paper text-left text-xs text-ink-6">
                <tr>
                  <th className="px-4 py-2 font-medium">#</th>
                  <th className="px-4 py-2 font-medium">岗位</th>
                  <th className="px-4 py-2 font-medium">状态</th>
                  <th className="px-4 py-2 font-medium">题量</th>
                  {/* M22：手机窄屏砍掉低价值列——时长与时间在右侧详情里都有 */}
                  <th className="hidden px-4 py-2 font-medium sm:table-cell">时长</th>
                  <th className="hidden px-4 py-2 font-medium sm:table-cell">开始时间</th>
                  <th className="px-4 py-2 font-medium"></th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr
                    key={item.id}
                    onClick={() => void openDetail(item.id)}
                    className={`cursor-pointer border-t border-ink-1 hover:bg-paper ${
                      detail?.id === item.id ? 'bg-info-soft' : ''
                    }`}
                  >
                    <td className="px-4 py-2 text-ink-6">{item.id}</td>
                    <td className="px-4 py-2 text-ink-9">{item.target_label || '通用面试'}</td>
                    <td className="px-4 py-2 text-ink-6">{STATUS_LABEL[item.status] ?? item.status}</td>
                    <td className="px-4 py-2 text-ink-6">
                      {item.answer_count}/{item.plan.max_questions ?? '—'}
                    </td>
                    <td className="hidden px-4 py-2 text-ink-6 sm:table-cell">{formatDuration(item.duration_ms)}</td>
                    <td className="hidden px-4 py-2 text-ink-6 sm:table-cell">{formatTime(item.created_at)}</td>
                    <td className="px-4 py-2 text-right">
                      <button
                        type="button"
                        onClick={(event) => {
                          // 这一行自己也有 onClick（打开详情），别让它一起触发
                          event.stopPropagation()
                          void remove(item.id)
                        }}
                        className="-my-1 whitespace-nowrap rounded-control px-1.5 py-1 text-xs text-ng hover:bg-ng-soft hover:underline"
                        title="删除这场面试，连同它的回合、报告与录音文件"
                      >
                        删除
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <aside className="rounded-panel border border-ink-1 bg-surface p-4 lg:min-h-0 lg:overflow-y-auto">
        {!detail ? (
          <p className="text-sm text-ink-6">点击左侧任意一行查看该场面试的全部回合。</p>
        ) : (
          <>
            <div className="flex items-start justify-between gap-2">
              <h3 className="text-sm font-semibold text-ink-9">
                会话 #{detail.id} · {detail.target_label || '通用面试'}
              </h3>
              {onOpenReport && (
                <button
                  type="button"
                  onClick={() => onOpenReport(detail.id)}
                  className="shrink-0 rounded-control bg-info px-2.5 py-1 text-xs text-surface hover:bg-info"
                >
                  查看报告
                </button>
              )}
            </div>
            <p className="mt-1 text-xs text-ink-6">
              {STATUS_LABEL[detail.status] ?? detail.status} · 共 {detail.turns.length} 个回合 · 时长{' '}
              {formatDuration(detail.duration_ms)} · 提供方 {detail.provider}
            </p>

            {detail.turns.some((turn) => turn.audio_url !== null) ? (
              <div className="mt-3 rounded-card bg-paper px-3 py-2">
                <p className="mb-1 text-xs text-ink-6">整场录音</p>
                <audio controls src={`/api/sessions/${detail.id}/audio`} className="h-8 w-full" />
              </div>
            ) : (
              <p className="mt-3 text-xs text-ink-4">本场没有录音（纯文字作答不产生音频）。</p>
            )}

            <div className="mt-3 space-y-3 pr-1 lg:max-h-[55vh] lg:overflow-y-auto">
              {detail.turns.map((turn) => (
                <div key={turn.idx} className="rounded-card border border-ink-1 bg-paper/60 px-3 py-2">
                  <div className="mb-1 flex items-center justify-between gap-2 text-[11px] text-ink-6">
                    <span>
                      {SPEAKER_LABEL[turn.speaker] ?? turn.speaker}
                      {turn.kind ? ` · ${turn.kind}` : ''}
                      {turn.topic ? ` · ${turn.topic}` : ''}
                      {turn.end_ms !== null ? ` · ${(turn.end_ms / 1000).toFixed(1)}s` : ''}
                    </span>
                    {turn.audio_url !== null && (
                      <button
                        type="button"
                        onClick={() => void playTurn(turn.audio_url as string)}
                        className="rounded-control border border-ink-2 px-2 py-0.5 text-[11px] text-ink-6 hover:bg-surface"
                      >
                        回放
                      </button>
                    )}
                  </div>
                  <p className="whitespace-pre-wrap text-sm leading-relaxed text-ink-9">{turn.text}</p>
                </div>
              ))}
            </div>
          </>
        )}
      </aside>
    </div>
  )
}
