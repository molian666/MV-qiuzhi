/** 训练页（M5）：掌握度总览 + 今日训练 + 能力掌握度清单 + 掌握度曲线。 */

import { useCallback, useEffect, useState } from 'react'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '../api/client'
import type { MasteryEvent, MasteryOverview, TrainingGrade, TrainingQuestion } from '../api/types'

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function formatTime(value: string | null): string {
  if (!value) {
    return '—'
  }
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function StatCard({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-panel border border-ink-1 bg-surface px-4 py-3">
      <p className="text-xs text-ink-6">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-ink-9">{value}</p>
      {hint && <p className="text-[11px] text-ink-4">{hint}</p>}
    </div>
  )
}

export default function Training() {
  const [overview, setOverview] = useState<MasteryOverview | null>(null)
  const [questions, setQuestions] = useState<TrainingQuestion[]>([])
  const [activeId, setActiveId] = useState<number | null>(null)
  const [draft, setDraft] = useState('')
  const [grades, setGrades] = useState<Record<number, TrainingGrade>>({})
  const [history, setHistory] = useState<{ name: string; points: { label: string; mastery: number }[] } | null>(null)
  const [loading, setLoading] = useState(true)
  const [grading, setGrading] = useState(false)
  const [error, setError] = useState('')

  const refreshOverview = useCallback(async () => {
    try {
      setOverview(await api.masteryOverview())
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [])

  const loadQuestions = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setQuestions(await api.nextTraining(4, 3))
      setGrades({})
      setActiveId(null)
      setDraft('')
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refreshOverview()
    void loadQuestions()
  }, [refreshOverview, loadQuestions])

  const submit = async () => {
    if (activeId === null || !draft.trim()) {
      return
    }
    setGrading(true)
    setError('')
    try {
      const grade = await api.gradeTraining(activeId, draft.trim())
      setGrades((previous) => ({ ...previous, [grade.question_id]: grade }))
      setDraft('')
      await refreshOverview()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setGrading(false)
    }
  }

  const openHistory = async (topicId: number, name: string) => {
    try {
      const events = await api.masteryHistory(topicId)
      setHistory({
        name,
        points: events.map((event: MasteryEvent, index) => ({
          label: `#${index + 1}`,
          mastery: Math.round(event.mastery_after * 10) / 10,
        })),
      })
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  return (
    <div className="space-y-4">
      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="平均掌握度" value={`${overview?.average_mastery ?? 0}`} hint="0–100，越高越稳" />
        <StatCard label="高危薄弱点" value={`${overview?.high_risk_count ?? 0}`} hint="低于 40 或连续两次低分" />
        <StatCard label="待复习" value={`${overview?.due_count ?? 0}`} hint="SM-2 已到期" />
        <StatCard label="能力标签" value={`${overview?.items.length ?? 0}`} hint="来自面试报告的改进项" />
      </section>

      {error && <p className="rounded-control bg-ng-soft px-3 py-2 text-sm text-ng">{error}</p>}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
        <section className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-ink-9">今日训练</h3>
            <button
              type="button"
              onClick={() => void loadQuestions()}
              disabled={loading}
              className="rounded-control border border-ink-2 px-3 py-1.5 text-xs text-ink-7 hover:bg-paper disabled:opacity-40"
            >
              换一批
            </button>
          </div>

          {loading ? (
            <p className="rounded-panel border border-ink-1 bg-surface px-4 py-6 text-sm text-ink-6">正在组题…</p>
          ) : questions.length === 0 ? (
            <p className="rounded-panel border border-dashed border-ink-2 px-4 py-6 text-sm text-ink-6">
              暂时没有可用题目。先去「面试间」跑一场面试并生成报告，系统会根据薄弱点出题。
            </p>
          ) : (
            questions.map((question) => {
              const grade = grades[question.question_id]
              const isActive = question.question_id === activeId
              return (
                <article key={question.question_id} className="rounded-panel border border-ink-1 bg-surface p-4">
                  <header className="mb-2 flex flex-wrap items-center gap-2 text-xs">
                    <span
                      className={
                        question.kind === 'weak'
                          ? 'rounded-full border border-ng bg-ng-soft px-2 py-0.5 text-ng'
                          : 'rounded-full border border-ink-1 bg-paper px-2 py-0.5 text-ink-6'
                      }
                    >
                      {question.kind === 'weak' ? '薄弱点' : '换换脑子'}
                    </span>
                    <span className="text-ink-6">难度 {question.difficulty}/5</span>
                    <span className="text-ink-4">{question.origin === 'generated' ? '新生成' : '题库'}</span>
                    {question.topic_names.map((name) => (
                      <span key={name} className="rounded-full bg-info-soft px-2 py-0.5 text-info">
                        {name}
                      </span>
                    ))}
                  </header>

                  <p className="text-sm font-medium leading-relaxed text-ink-9">{question.text}</p>

                  <details className="mt-1 text-[11px] text-ink-6">
                    <summary className="cursor-pointer">为什么推荐这题</summary>
                    <ul className="mt-1 space-y-0.5 pl-4">
                      <li>薄弱度：{question.reason.weakness?.toFixed(2)}</li>
                      <li>岗位匹配：{question.reason.jd_match?.toFixed(2)}</li>
                      <li>到期紧迫：{question.reason.due_urgency?.toFixed(2)}</li>
                      <li>难度适配：{question.reason.difficulty_fit?.toFixed(2)}</li>
                      <li>题型罚项：{question.reason.type_penalty?.toFixed(2)}</li>
                      <li className="font-medium text-ink-6">合计：{question.reason.total?.toFixed(2)}</li>
                    </ul>
                  </details>

                  {grade ? (
                    <div className="mt-3 space-y-2 rounded-card bg-paper px-3 py-2">
                      <p className="text-sm">
                        <span className="font-semibold text-ink-9">{grade.score}</span>
                        <span className="text-ink-6"> / 5</span>
                        {grade.comment && <span className="ml-2 text-ink-7">{grade.comment}</span>}
                      </p>
                      {grade.better_answer && (
                        <p className="text-xs leading-relaxed text-ink-7">
                          <span className="font-medium text-ink-6">示范：</span>
                          {grade.better_answer}
                        </p>
                      )}
                      {grade.mastery.length > 0 && (
                        <p className="text-[11px] text-ink-6">
                          掌握度变化：
                          {grade.mastery
                            .map((change) => `${change.name} ${Math.round(change.before)} → ${Math.round(change.after)}`)
                            .join('；')}
                        </p>
                      )}
                    </div>
                  ) : isActive ? (
                    <div className="mt-3 space-y-2">
                      <textarea
                        value={draft}
                        onChange={(event) => setDraft(event.target.value)}
                        rows={4}
                        placeholder="像面试那样口头组织，再敲进来；Enter 换行，写完后点提交"
                        className="w-full resize-none rounded-card border border-ink-2 px-3 py-2 text-sm outline-none focus:border-info"
                      />
                      <div className="flex gap-2">
                        <button
                          type="button"
                          onClick={() => void submit()}
                          disabled={grading || !draft.trim()}
                          className="rounded-control bg-ink-9 px-3 py-1.5 text-xs font-medium text-surface hover:bg-ink-9 disabled:opacity-40"
                        >
                          {grading ? '批改中…' : '提交并批改'}
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setActiveId(null)
                            setDraft('')
                          }}
                          className="rounded-control border border-ink-2 px-3 py-1.5 text-xs text-ink-7 hover:bg-paper"
                        >
                          先跳过
                        </button>
                      </div>
                    </div>
                  ) : (
                    <button
                      type="button"
                      onClick={() => {
                        setActiveId(question.question_id)
                        setDraft('')
                      }}
                      className="mt-3 rounded-control bg-info px-3 py-1.5 text-xs font-medium text-surface hover:bg-info"
                    >
                      开始作答
                    </button>
                  )}
                </article>
              )
            })
          )}
        </section>

        <aside className="space-y-4">
          {history && (
            <div className="rounded-panel border border-ink-1 bg-surface p-4">
              <div className="mb-2 flex items-center justify-between">
                <h3 className="text-sm font-semibold text-ink-9">{history.name} · 掌握度曲线</h3>
                <button
                  type="button"
                  onClick={() => setHistory(null)}
                  className="text-xs text-ink-6 hover:underline"
                >
                  收起
                </button>
              </div>
              {history.points.length === 0 ? (
                <p className="text-xs text-ink-6">还没有记录。</p>
              ) : (
                <div className="h-40">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={history.points}>
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis dataKey="label" tick={{ fontSize: 10 }} />
                      <YAxis domain={[0, 100]} tick={{ fontSize: 10 }} width={28} />
                      <Tooltip />
                      <Line type="monotone" dataKey="mastery" stroke="#0284c7" dot />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              )}
            </div>
          )}

          <div className="rounded-panel border border-ink-1 bg-surface p-4">
            <h3 className="mb-2 text-sm font-semibold text-ink-9">
              能力掌握度（{overview?.items.length ?? 0}）
            </h3>
            {!overview || overview.items.length === 0 ? (
              <p className="text-xs text-ink-6">还没有数据。生成一份面试报告后，这里会按薄弱程度排序。</p>
            ) : (
              <div className="max-h-[60vh] space-y-2 overflow-y-auto pr-1">
                {overview.items.map((item) => (
                  <button
                    key={item.topic_id}
                    type="button"
                    onClick={() => void openHistory(item.topic_id, item.name)}
                    className="block w-full rounded-card px-2 py-1.5 text-left hover:bg-paper"
                  >
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-ink-7">
                        {item.high_risk ? '⚠ ' : ''}
                        {item.name}
                      </span>
                      <span className="text-ink-6">{Math.round(item.mastery)}</span>
                    </div>
                    <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-ink-1">
                      <div
                        className={item.high_risk ? 'h-full bg-ng' : 'h-full bg-ok'}
                        style={{ width: `${Math.max(2, Math.min(100, item.mastery))}%` }}
                      />
                    </div>
                    <p className="mt-0.5 text-[11px] text-ink-4">
                      练过 {item.attempts} 次 · {formatTime(item.updated_at)}
                    </p>
                  </button>
                ))}
              </div>
            )}
          </div>
        </aside>
      </div>
    </div>
  )
}
