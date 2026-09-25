/** 表达体检（M9）：填充词、语速曲线、停顿、紧张信号。
 *
 * 这一页的核心是**可核对**：每条问题都带时间点，点一下就跳到录音的那一句。
 * 「你说快了」这种评价如果听不到证据，就只是评价；能跳过去听，才叫反馈。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '../api/client'
import type { ExpressionReport, SessionSummary } from '../api/types'

const ISSUE_LABEL: Record<string, string> = {
  pace_fast: '语速偏快',
  pace_slow: '语速偏慢',
  long_pause: '长停顿',
  volume_drop: '音量变小',
  volume_spike: '音量突增',
}

const PAUSE_LABEL: Record<string, string> = {
  normal: '普通停顿',
  long: '长停顿',
  very_long: '很长停顿',
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function formatClock(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000))
  const minutes = Math.floor(total / 60)
  const seconds = total % 60
  return `${minutes}:${String(seconds).padStart(2, '0')}`
}

function sessionLabel(session: SessionSummary): string {
  const when = session.started_at ? new Date(session.started_at).toLocaleString('zh-CN') : `#${session.id}`
  const mode = session.mode === 'realtime' ? '语音' : '文字'
  return `${when} · ${mode} · ${session.status}`
}

export default function Expression() {
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [sessionId, setSessionId] = useState<number | null>(null)
  const [report, setReport] = useState<ExpressionReport | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const audioRef = useRef<HTMLAudioElement | null>(null)

  const loadSessions = useCallback(async () => {
    try {
      const items = await api.listSessions()
      setSessions(items)
      setSessionId((current) => current ?? items[0]?.id ?? null)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [])

  useEffect(() => {
    void loadSessions()
  }, [loadSessions])

  useEffect(() => {
    if (sessionId === null) {
      setReport(null)
      return
    }
    let cancelled = false
    api
      .getExpression(sessionId)
      .then((data) => {
        if (!cancelled) {
          setReport(data)
        }
      })
      .catch(() => {
        // 还没生成过是正常状态，不是错误
        if (!cancelled) {
          setReport(null)
        }
      })
    return () => {
      cancelled = true
    }
  }, [sessionId])

  const generate = async (withAudio: boolean) => {
    if (sessionId === null) {
      return
    }
    setBusy(true)
    setError('')
    try {
      setReport(await api.createExpression(sessionId, withAudio))
    } catch (err) {
      setError(errorMessage(err))
      setReport(null)
    } finally {
      setBusy(false)
    }
  }

  /** 跳到录音的某一句——这是"每条问题都能核对"的落点 */
  const jumpTo = (ms: number) => {
    const audio = audioRef.current
    if (!audio) {
      setError('这场面试没有录音，无法跳转（文字面试只能做文字层面的分析）')
      return
    }
    audio.currentTime = Math.max(0, ms / 1000)
    void audio.play()
  }

  const chartData =
    report?.windows.map((window) => ({
      at: window.start_ms,
      label: formatClock(window.start_ms),
      cpm: Number(window.cpm.toFixed(0)),
      rms: Number((window.rms * 100).toFixed(1)),
    })) ?? []

  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      {error && (
        <div className="shrink-0 rounded-control border border-ng bg-ng-soft px-3 py-2 text-sm text-ng">
          {error}
        </div>
      )}

      <section className="shrink-0 rounded-card border border-ink-1 bg-surface p-4">
        <h2 className="text-sm font-semibold">选一场面试做体检</h2>
        <p className="mt-0.5 text-xs text-ink-6">
          语音面试才有停顿与音量分析（需要录音）；文字面试只能看填充词与整体语速。
        </p>

        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="text-xs text-ink-6">
            面试场次
            <select
              className="mt-1 w-80 rounded-control border border-ink-2 px-2 py-1.5 text-sm"
              value={sessionId ?? ''}
              onChange={(event) => setSessionId(Number(event.target.value))}
            >
              <option value="">（未选择）</option>
              {sessions.map((item) => (
                <option key={item.id} value={item.id}>
                  {sessionLabel(item)}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            disabled={busy || sessionId === null}
            onClick={() => void generate(true)}
            className="rounded-control bg-ink-9 px-3 py-1.5 text-xs text-surface disabled:cursor-not-allowed disabled:bg-ink-2"
          >
            生成表达体检（含录音）
          </button>
          <button
            type="button"
            disabled={busy || sessionId === null}
            onClick={() => void generate(false)}
            className="rounded-control border border-ink-2 px-3 py-1.5 text-xs text-ink-7 hover:bg-paper disabled:cursor-not-allowed"
          >
            只看文字
          </button>
        </div>
      </section>

      {!report && (
        <section className="rounded-card border border-ink-1 bg-surface p-6 text-center text-sm text-ink-6">
          还没有体检结果。选一场面试，点「生成表达体检」。
        </section>
      )}

      {report && (
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
          {/* 结论 */}
          <section className="rounded-card border border-ink-1 bg-surface p-4">
            <div className="flex flex-wrap items-center gap-3">
              <div className="flex items-baseline gap-2">
                <span className="text-3xl font-semibold">{report.overall}</span>
                <span className="text-xs text-ink-6">/ 10</span>
              </div>
              <span
                className={
                  report.has_audio
                    ? 'rounded-full bg-ok-soft px-2.5 py-1 text-xs text-ok'
                    : 'rounded-full bg-sunken px-2.5 py-1 text-xs text-ink-6'
                }
              >
                {report.has_audio ? '含录音分析' : '仅文字分析'}
              </span>
              {!report.reliable && (
                <span className="rounded-full bg-warn-soft px-2.5 py-1 text-xs text-warn">
                  样本偏少，密度类指标只作参考
                </span>
              )}
            </div>
            <p className="mt-2 text-sm text-ink-7">{report.summary}</p>

            <div className="mt-3 grid gap-3 text-xs sm:grid-cols-3 lg:grid-cols-6">
              {[
                { label: '发言时长', value: formatClock(report.speaking_ms) },
                { label: '字数', value: String(report.chars) },
                { label: '整体语速', value: `${Math.round(report.overall_cpm)} 字/分` },
                { label: '填充词', value: `${report.filler_total} 次` },
                { label: '语速起伏', value: report.pace_cv.toFixed(2) },
                { label: '紧张信号', value: `${report.tension}/100` },
              ].map((item) => (
                <div key={item.label} className="rounded-control border border-ink-1 px-3 py-2">
                  <div className="text-ink-6">{item.label}</div>
                  <div className="mt-0.5 text-sm font-medium text-ink-9">{item.value}</div>
                </div>
              ))}
            </div>

            {report.tension >= 55 && (
              <div className="mt-3 rounded-control border border-warn bg-warn-soft px-3 py-2 text-xs text-warn">
                语速与音量波动较大，可能是紧张信号。（这是从节奏推出来的启发式提示，不是心理判断。）
              </div>
            )}

            {report.audio_url && (
              <div className="mt-3">
                <div className="mb-1 text-xs text-ink-6">整场录音（点下面的「跳到这句」会从这里播放）</div>
                <audio ref={audioRef} src={report.audio_url} controls className="w-full" />
              </div>
            )}
          </section>

          {/* 语速-音量曲线 */}
          {chartData.length > 0 && (
            <section className="rounded-card border border-ink-1 bg-surface p-4">
              <h2 className="text-sm font-semibold">语速与音量曲线（每 10 秒）</h2>
              <p className="mt-0.5 text-xs text-ink-6">
                折线是语速（字/分钟），柱子是相对音量。两条线一起看，紧张的位置通常很明显。
              </p>
              <div className="mt-3 h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={chartData} margin={{ top: 8, right: 8, bottom: 8, left: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--color-ink-1)" />
                    <XAxis dataKey="label" tick={{ fontSize: 11 }} />
                    <YAxis yAxisId="left" tick={{ fontSize: 11 }} />
                    <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} />
                    <Tooltip />
                    <Bar yAxisId="right" dataKey="rms" name="音量" fill="var(--color-ink-2)" />
                    <Line
                      yAxisId="left"
                      type="monotone"
                      dataKey="cpm"
                      name="语速"
                      stroke="var(--color-ink-9)"
                      dot={false}
                    />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            </section>
          )}

          {/* 问题清单（可跳播） */}
          <section className="rounded-card border border-ink-1 bg-surface p-4">
            <h2 className="text-sm font-semibold">具体问题（{report.issues.length}）</h2>
            <p className="mt-0.5 text-xs text-ink-6">
              点「跳到这句」会把上面的录音移到那个位置——评价要能被核对，不然就只是评价。
            </p>

            {report.issues.length === 0 ? (
              <p className="mt-3 text-xs text-ok">节奏上没有发现明显问题。</p>
            ) : (
              <ul className="mt-3 space-y-2">
                {report.issues.map((issue) => (
                  <li
                    key={`${issue.kind}-${issue.at_ms}`}
                    className={
                      issue.severity === 'warning'
                        ? 'rounded-control border border-warn bg-warn-soft px-3 py-2 text-xs'
                        : 'rounded-control border border-ink-1 px-3 py-2 text-xs'
                    }
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-medium text-ink-9">
                        {formatClock(issue.at_ms)} · {ISSUE_LABEL[issue.kind] ?? issue.kind}
                      </span>
                      <button
                        type="button"
                        onClick={() => jumpTo(issue.at_ms)}
                        className="rounded-control border border-ink-2 px-2 py-0.5 text-[11px] text-ink-6 hover:bg-surface"
                      >
                        跳到这句
                      </button>
                    </div>
                    <div className="mt-1 text-ink-6">{issue.detail}</div>
                    {issue.excerpt && <div className="mt-1 text-ink-6">「{issue.excerpt}」</div>}
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* 填充词与停顿 */}
          <section className="grid gap-4 lg:grid-cols-2">
            <div className="rounded-card border border-ink-1 bg-surface p-4">
              <h2 className="text-sm font-semibold">填充词（{report.filler_total} 次）</h2>
              <p className="mt-0.5 text-xs text-ink-6">
                {report.reliable
                  ? `每千字 ${report.filler_per_kchar} 个（加权）`
                  : `说得太少，密度不参与评分（本次 ${report.filler_total} 次）`}
              </p>
              {report.fillers.length === 0 ? (
                <p className="mt-2 text-xs text-ok">没有明显填充词。</p>
              ) : (
                <table className="mt-2 w-full text-left text-xs">
                  <thead className="text-ink-6">
                    <tr>
                      <th className="py-1 pr-2">词</th>
                      <th className="py-1 pr-2">次数</th>
                      <th className="py-1 pr-2">类型</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.fillers.map((item) => (
                      <tr key={item.word} className="border-t border-ink-1">
                        <td className="py-1 pr-2">{item.word}</td>
                        <td className="py-1 pr-2">{item.count}</td>
                        <td className={`py-1 pr-2 ${item.strong ? 'text-ng' : 'text-ink-6'}`}>
                          {item.strong ? '明显卡壳' : '口头习惯'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>

            <div className="rounded-card border border-ink-1 bg-surface p-4">
              <h2 className="text-sm font-semibold">停顿（{report.pauses.length} 处）</h2>
              <p className="mt-0.5 text-xs text-ink-6">
                {report.has_audio ? '只统计你自己发言里的停顿' : '没有录音，无法定位停顿'}
              </p>
              {report.pauses.length === 0 ? (
                <p className="mt-2 text-xs text-ink-6">没有检测到超过 0.6 秒的停顿。</p>
              ) : (
                <ul className="mt-2 space-y-1 text-xs">
                  {report.pauses.map((pause) => (
                    <li key={pause.start_ms} className="flex items-center justify-between gap-2 border-t border-ink-1 py-1">
                      <span>
                        {formatClock(pause.start_ms)} · {pause.seconds} 秒 ·{' '}
                        <span className={pause.kind === 'very_long' ? 'text-ng' : 'text-ink-6'}>
                          {PAUSE_LABEL[pause.kind] ?? pause.kind}
                        </span>
                      </span>
                      <button
                        type="button"
                        onClick={() => jumpTo(pause.start_ms)}
                        className="rounded-control border border-ink-2 px-2 py-0.5 text-[11px] text-ink-6 hover:bg-paper"
                      >
                        跳到这句
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>

          {/* 练习建议 */}
          <section className="rounded-card border border-ink-1 bg-surface p-4">
            <h2 className="text-sm font-semibold">怎么练</h2>
            <ul className="mt-2 space-y-2 text-sm text-ink-7">
              {report.suggestions.map((tip) => (
                <li key={tip} className="rounded-control border border-ink-1 bg-paper px-3 py-2">
                  {tip}
                </li>
              ))}
            </ul>
          </section>
        </div>
      )}
    </div>
  )
}
