/** 报告页：总分 + 七维雷达图 + 改进项（原话证据 / 怎么改 / 示范答案 / 立刻可做的练习）。 */

import { useCallback, useEffect, useState } from 'react'
import { PolarAngleAxis, PolarGrid, PolarRadiusAxis, Radar, RadarChart, ResponsiveContainer } from 'recharts'
import { api } from '../api/client'
import type { ReportSummary, ScoreReport } from '../api/types'

const SEVERITY_STYLE: Record<string, { label: string; badge: string }> = {
  high: { label: '高优先级', badge: 'border-ng bg-ng-soft text-ng' },
  medium: { label: '中优先级', badge: 'border-warn bg-warn-soft text-warn' },
  low: { label: '低优先级', badge: 'border-ink-1 bg-paper text-ink-6' },
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

  // 未评分的维度在雷达图上不能画成 0：那会看起来像"这项考砸了"，
  // 而实际是模型没给分。画成 null 让图表留空（echarts 的 radar 会跳过空值）。
  const radarData =
    report?.dimensions.map((dimension) => ({
      label: dimension.label,
      score: dimension.scored === false ? null : dimension.score,
    })) ?? []

  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      <section className="shrink-0 rounded-panel border border-ink-1 bg-surface p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-ink-9">面试报告</h2>
            <p className="text-xs text-ink-6">
              {report
                ? `${report.target_label || '通用面试'} · 会话 #${report.session_id} · ${formatTime(report.generated_at)}${report.reused ? ' · 复用已有报告' : ''}`
                : '选一场面试查看报告；没有报告时会自动生成'}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={sessionId ?? ''}
              onChange={(event) => onSelectSession(event.target.value ? Number(event.target.value) : null)}
              aria-label="选择要查看报告的面试"
              className="rounded-card border border-ink-2 px-2 py-1.5 text-xs outline-none focus:border-info"
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
              className="btn-primary rounded-control px-3 py-1.5 text-xs font-medium"
            >
              {regenerating ? '评分中…' : '重新生成'}
            </button>
          </div>
        </div>
      </section>

      {error && (
        <p className="shrink-0 rounded-control bg-ng-soft px-3 py-2 text-sm text-ng">{error}</p>
      )}
      {loading && (
        <p className="shrink-0 rounded-control bg-paper px-3 py-2 text-sm text-ink-6">
          正在载入报告…
        </p>
      )}

      {/* 空态：报告是"评分台"——没有输入时，主区不该是空白，而是一张待命的工作台。 */}
      {!report && !loading && !error && (
        <section className="flex min-h-0 flex-1 items-center justify-center">
          <div className="w-full max-w-md rounded-panel border border-ink-1 bg-surface p-6">
            <div className="flex items-baseline gap-2">
              <h3 className="text-sm font-semibold text-ink-9">报告生成台</h3>
              <span className="font-mono text-[10px] text-ink-4">IDLE</span>
            </div>
            <p className="mt-1 text-xs leading-relaxed text-ink-6">
              报告不会凭空出现——选一场面试，评分才会开始。步骤：
            </p>
            <ol className="mt-3 space-y-2">
              <li className="flex gap-2 text-xs leading-relaxed text-ink-6">
                <span className="mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full bg-ok" />
                <span>在「历史面试」里跑一场面试，或从右侧下拉直接挑一场已结束的场次</span>
              </li>
              <li className="flex gap-2 text-xs leading-relaxed text-ink-6">
                <span className="mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full bg-ok" />
                <span>选中会话后点「重新生成」，从这场对话里自动评分</span>
              </li>
              <li className="flex gap-2 text-xs leading-relaxed text-ink-6">
                <span className="mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full bg-ok" />
                <span>报告含总分、维度雷达图与逐题点评，改进项会写进「训练」的掌握度</span>
              </li>
            </ol>
            {reports.length === 0 && (
              <p className="mt-4 rounded-control bg-ng-soft px-3 py-2 text-xs text-ng">
                还没有可评分的会话——先去「面试间」跑一场，再回来看报告。
              </p>
            )}
          </div>
        </section>
      )}

      {report && (
        <>
          <section className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[340px_minmax(0,1fr)]">
            <div className="min-h-0 overflow-y-auto rounded-panel border border-ink-1 bg-surface p-4 pr-3">
              <div className="mb-1 flex items-baseline gap-2">
                <span className="text-4xl font-semibold text-ink-9">{Math.round(report.overall)}</span>
                <span className="text-sm text-ink-6">/ 100</span>
              </div>
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <RadarChart data={radarData} outerRadius="70%">
                    <PolarGrid />
                    <PolarAngleAxis dataKey="label" tick={{ fontSize: 11 }} />
                    <PolarRadiusAxis domain={[0, 5]} tickCount={6} tick={{ fontSize: 9 }} />
                    <Radar
                      dataKey="score"
                      stroke="var(--color-info)"
                      fill="var(--color-info)"
                      fillOpacity={0.3}
                    />
                  </RadarChart>
                </ResponsiveContainer>
              </div>
            </div>

            <div className="min-h-0 space-y-3 overflow-y-auto pr-1">
              <div className="rounded-panel border border-ink-1 bg-surface p-4">
                <h3 className="mb-2 text-sm font-semibold text-ink-9">整体评价</h3>
                <p className="whitespace-pre-wrap text-sm leading-relaxed text-ink-7">{report.summary}</p>
                <p className="mt-2 text-xs text-ink-4">评分模型：{report.model || '—'}</p>
              </div>

              <div className="rounded-panel border border-ink-1 bg-surface p-4">
                <h3 className="mb-2 text-sm font-semibold text-ink-9">七维明细</h3>
                <div className="space-y-2">
                  {report.dimensions.map((dimension) => {
                    // `scored === false` 表示模型没给这一项评分（score 是占位的 0）。
                    // 必须显示"未评分"——直接显示 0/5 会让用户以为这项考砸了。
                    const unscored = dimension.scored === false
                    return (
                      <div key={dimension.key} className="rounded-card bg-paper px-3 py-2">
                        <div className="flex items-center justify-between text-xs">
                          <span className="font-medium text-ink-7">{dimension.label}</span>
                          <span className={unscored ? 'text-ink-4' : 'text-ink-6'}>
                            {unscored ? '未评分（不计入总分）' : `${dimension.score}/5`}
                          </span>
                        </div>
                        <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-ink-1">
                          <div
                            className={unscored ? 'h-full bg-ink-2' : 'h-full bg-info'}
                            style={{ width: unscored ? '0%' : `${(dimension.score / 5) * 100}%` }}
                          />
                        </div>
                        {dimension.comment && (
                          <p className="mt-1 text-xs leading-relaxed text-ink-6">{dimension.comment}</p>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            </div>
          </section>

          <section className="space-y-3">
            <h3 className="text-sm font-semibold text-ink-9">改进项（{report.items.length}）</h3>

            {report.items.map((item) => {
              const style = SEVERITY_STYLE[item.severity] ?? SEVERITY_STYLE.low
              return (
                <article key={item.id} className="rounded-panel border border-ink-1 bg-surface p-4">
                  <header className="mb-2 flex flex-wrap items-center gap-2">
                    <span className={`rounded-full border px-2 py-0.5 text-xs ${style.badge}`}>{style.label}</span>
                    <span className="rounded-full bg-sunken px-2 py-0.5 text-xs text-ink-6">{item.dimension_label}</span>
                    {item.topics.map((topic) => (
                      <span key={topic} className="text-xs text-ink-4">
                        #{topic}
                      </span>
                    ))}
                  </header>

                  <p className="text-sm font-medium text-ink-9">{item.problem}</p>

                  {item.evidence.length > 0 && (
                    <div className="mt-2 space-y-1.5">
                      {item.evidence.map((evidence) => (
                        <div key={evidence.turn_id} className="rounded-card border-l-2 border-warn bg-warn-soft/70 px-3 py-1.5">
                          <p className="text-xs leading-relaxed text-ink-7">“{evidence.text}”</p>
                          <div className="mt-1 flex items-center gap-3">
                            <span className="text-[11px] text-ink-4">回合 #{evidence.turn_id}</span>
                            {evidence.audio_url !== null && (
                              <button
                                type="button"
                                onClick={() => {
                                  void new Audio(evidence.audio_url as string).play()
                                }}
                                className="text-[11px] text-info hover:underline"
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
                      <dt className="text-xs font-medium text-ink-6">怎么改</dt>
                      <dd className="leading-relaxed text-ink-7">{item.suggestion}</dd>
                    </div>
                    {item.corrected_answer && (
                      <div>
                        <dt className="text-xs font-medium text-ink-6">示范回答（用你自己的经历重写）</dt>
                        <dd className="whitespace-pre-wrap leading-relaxed text-ink-7">{item.corrected_answer}</dd>
                      </div>
                    )}
                    {item.drill && (
                      <div>
                        <dt className="text-xs font-medium text-ink-6">立刻可做</dt>
                        <dd className="leading-relaxed text-ink-7">{item.drill}</dd>
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
