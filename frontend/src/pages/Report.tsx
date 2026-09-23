/** 报告页：总分 + 七维雷达图 + 改进项（原话证据 / 怎么改 / 示范答案 / 立刻可做的练习）。 */

import { useCallback, useEffect, useState } from 'react'
import { PolarAngleAxis, PolarGrid, PolarRadiusAxis, Radar, RadarChart, ResponsiveContainer } from 'recharts'
import { api } from '../api/client'
import type { ReportSummary, ScoreReport } from '../api/types'

const SEVERITY_STYLE: Record<string, { label: string; badge: string }> = {
  high: { label: '高优先级', badge: 'border-rose-200 bg-rose-50 text-rose-700' },
  medium: { label: '中优先级', badge: 'border-amber-200 bg-amber-50 text-amber-800' },
  low: { label: '低优先级', badge: 'border-slate-200 bg-slate-50 text-slate-600' },
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function formatTime(value: string): string {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

interface Props {
  sessionId: number | null
  onSelectSession: (id: number | null) => void
}

export default function Report({ sessionId, onSelectSession }: Props) {
  const [report, setReport] = useState<ScoreReport | null>(null)
  const [reports, setReports] = useState<ReportSummary[]>([])
  const [loading, setLoading] = useState(false)
  const [regenerating, setRegenerating] = useState(false)
  const [error, setError] = useState('')

  const refreshList = useCallback(async () => {
    try {
      setReports(await api.listReports())
    } catch {
      // 列表拉不到不影响已有报告的展示，静默即可
    }
  }, [])

  useEffect(() => {
    void refreshList()
  }, [refreshList])

  // 载入报告：GET 命中就直接用；404 说明还没生成 → 自动跑一次（force=false，避免重复烧 token）
  useEffect(() => {
    if (sessionId === null) {
      setReport(null)
      return
    }
    let cancelled = false

    const load = async () => {
      setLoading(true)
      setError('')
      setReport(null)
      try {
        const existing = await api.getReport(sessionId)
        if (!cancelled) {
          setReport(existing)
        }
      } catch {
        try {
          const created = await api.createReport(sessionId, false)
          if (!cancelled) {
            setReport(created)
            void refreshList()
          }
        } catch (err) {
          if (!cancelled) {
            setError(errorMessage(err))
          }
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    void load()
    return () => {
      cancelled = true
    }
  }, [sessionId, refreshList])

  const regenerate = async () => {
    if (sessionId === null) {
      return
    }
    setRegenerating(true)
    setError('')
    try {
      setReport(await api.createReport(sessionId, true))
      await refreshList()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setRegenerating(false)
    }
  }

  const radarData = report?.dimensions.map((dimension) => ({ label: dimension.label, score: dimension.score })) ?? []

  return (
    <div className="space-y-4">
      <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-slate-800">面试报告</h2>
            <p className="text-xs text-slate-500">
              {report
                ? `${report.target_label || '通用面试'} · 会话 #${report.session_id} · ${formatTime(report.generated_at)}${report.reused ? ' · 复用已有报告' : ''}`
                : '选一场面试查看报告；没有报告时会自动生成'}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={sessionId ?? ''}
              onChange={(event) => onSelectSession(event.target.value ? Number(event.target.value) : null)}
              className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs outline-none focus:border-sky-500"
            >
              <option value="">选择一场面试</option>
              {reports.map((item) => (
                <option key={item.id} value={item.session_id ?? ''}>
                  会话 #{item.session_id} · {Math.round(item.overall)} 分 · {formatTime(item.generated_at)}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={() => void regenerate()}
              disabled={sessionId === null || regenerating || loading}
              className="rounded-md bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-800 disabled:opacity-40"
            >
              {regenerating ? '评分中…' : '重新生成'}
            </button>
          </div>
        </div>
      </section>

      {error && <p className="rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</p>}
      {loading && <p className="rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-500">正在载入报告…</p>}

      {report && (
        <>
          <section className="grid gap-4 lg:grid-cols-[360px_minmax(0,1fr)]">
            <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="mb-1 flex items-baseline gap-2">
                <span className="text-4xl font-semibold text-slate-900">{Math.round(report.overall)}</span>
                <span className="text-sm text-slate-500">/ 100</span>
              </div>
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <RadarChart data={radarData} outerRadius="70%">
                    <PolarGrid />
                    <PolarAngleAxis dataKey="label" tick={{ fontSize: 11 }} />
                    <PolarRadiusAxis domain={[0, 5]} tickCount={6} tick={{ fontSize: 9 }} />
                    <Radar dataKey="score" stroke="#0284c7" fill="#0ea5e9" fillOpacity={0.35} />
                  </RadarChart>
                </ResponsiveContainer>
              </div>
            </div>

            <div className="space-y-3">
              <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                <h3 className="mb-2 text-sm font-semibold text-slate-800">整体评价</h3>
                <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-700">{report.summary}</p>
                <p className="mt-2 text-xs text-slate-400">评分模型：{report.model || '—'}</p>
              </div>

              <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                <h3 className="mb-2 text-sm font-semibold text-slate-800">七维明细</h3>
                <div className="space-y-2">
                  {report.dimensions.map((dimension) => (
                    <div key={dimension.key} className="rounded-lg bg-slate-50 px-3 py-2">
                      <div className="flex items-center justify-between text-xs">
                        <span className="font-medium text-slate-700">{dimension.label}</span>
                        <span className="text-slate-500">{dimension.score}/5</span>
                      </div>
                      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-200">
                        <div className="h-full bg-sky-500" style={{ width: `${(dimension.score / 5) * 100}%` }} />
                      </div>
                      {dimension.comment && <p className="mt-1 text-xs leading-relaxed text-slate-600">{dimension.comment}</p>}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </section>

          <section className="space-y-3">
            <h3 className="text-sm font-semibold text-slate-800">改进项（{report.items.length}）</h3>

            {report.items.map((item) => {
              const style = SEVERITY_STYLE[item.severity] ?? SEVERITY_STYLE.low
              return (
                <article key={item.id} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                  <header className="mb-2 flex flex-wrap items-center gap-2">
                    <span className={`rounded-full border px-2 py-0.5 text-xs ${style.badge}`}>{style.label}</span>
                    <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">{item.dimension_label}</span>
                    {item.topics.map((topic) => (
                      <span key={topic} className="text-xs text-slate-400">
                        #{topic}
                      </span>
                    ))}
                  </header>

                  <p className="text-sm font-medium text-slate-800">{item.problem}</p>

                  {item.evidence.length > 0 && (
                    <div className="mt-2 space-y-1.5">
                      {item.evidence.map((evidence) => (
                        <div key={evidence.turn_id} className="rounded-lg border-l-2 border-amber-300 bg-amber-50/70 px-3 py-1.5">
                          <p className="text-xs leading-relaxed text-slate-700">“{evidence.text}”</p>
                          <div className="mt-1 flex items-center gap-3">
                            <span className="text-[11px] text-slate-400">回合 #{evidence.turn_id}</span>
                            {evidence.audio_url !== null && (
                              <button
                                type="button"
                                onClick={() => {
                                  void new Audio(evidence.audio_url as string).play()
                                }}
                                className="text-[11px] text-sky-700 hover:underline"
                              >
                                回放这段
                              </button>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}

                  <dl className="mt-3 space-y-2 text-sm">
                    <div>
                      <dt className="text-xs font-medium text-slate-500">怎么改</dt>
                      <dd className="leading-relaxed text-slate-700">{item.suggestion}</dd>
                    </div>
                    {item.corrected_answer && (
                      <div>
                        <dt className="text-xs font-medium text-slate-500">示范回答（用你自己的经历重写）</dt>
                        <dd className="whitespace-pre-wrap leading-relaxed text-slate-700">{item.corrected_answer}</dd>
                      </div>
                    )}
                    {item.drill && (
                      <div>
                        <dt className="text-xs font-medium text-slate-500">立刻可做</dt>
                        <dd className="leading-relaxed text-slate-700">{item.drill}</dd>
                      </div>
                    )}
                  </dl>
                </article>
              )
            })}
          </section>
        </>
      )}
    </div>
  )
}
