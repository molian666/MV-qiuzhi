/** 入职与长期（M15）：入职清单、试用期 OKR、长期规划与薪资基准、数据导出。
 *
 * 四个子面板对应四件不同时刻的事：入职前（清单）、试用期（OKR）、长期（规划与基准）、
 * 以及随时可能做的（导出）。
 */

import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import type {
  BenchmarkItem,
  BenchmarkSummary,
  CareerOptions,
  ExportPreview,
  ObjectiveView,
  OnboardingData,
  PlanItem,
  ReviewDraft,
  TimingData,
} from '../api/types'

type Panel = 'onboarding' | 'okr' | 'longterm' | 'export'

const PANELS: { key: Panel; label: string }[] = [
  { key: 'onboarding', label: '入职清单' },
  { key: 'okr', label: '试用期 OKR' },
  { key: 'longterm', label: '长期与薪资' },
  { key: 'export', label: '数据导出' },
]

const PLAN_KIND_LABEL: Record<string, string> = {
  promotion: '晋升准备',
  switch: '跳槽时机',
  skill: '技能积累',
  other: '其它',
}

const PLAN_STATUS_LABEL: Record<string, string> = { idea: '想法', doing: '推进中', done: '已完成' }

const TOKEN_KEY = 'mianshi.export_token'

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function wan(value: number | null | undefined): string {
  if (!value) {
    return '—'
  }
  return `${Math.round((value / 10000) * 10) / 10} 万`
}

export default function CareerDesk() {
  const [panel, setPanel] = useState<Panel>('onboarding')
  const [options, setOptions] = useState<CareerOptions | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const [onboarding, setOnboarding] = useState<OnboardingData | null>(null)
  const [objectives, setObjectives] = useState<ObjectiveView[]>([])
  const [review, setReview] = useState<ReviewDraft | null>(null)
  const [plans, setPlans] = useState<PlanItem[]>([])
  const [timing, setTiming] = useState<TimingData | null>(null)
  const [benchmarks, setBenchmarks] = useState<BenchmarkItem[]>([])
  const [benchmarkSummary, setBenchmarkSummary] = useState<BenchmarkSummary | null>(null)
  const [preview, setPreview] = useState<ExportPreview | null>(null)
  const [token, setToken] = useState('')

  const [objectiveDraft, setObjectiveDraft] = useState({ title: '', period: '' })
  const [krDraft, setKrDraft] = useState<Record<number, { title: string; unit: string; target: number }>>({})
  const [planDraft, setPlanDraft] = useState({ kind: 'promotion', title: '', detail: '', target_date: '' })
  const [benchmarkDraft, setBenchmarkDraft] = useState({
    company: '',
    title: '',
    city: '',
    range_low: 0,
    range_high: 0,
    period: '',
    source: '',
    source_url: '',
    note: '',
  })

  const refresh = useCallback(async () => {
    try {
      const [nextOptions, nextOnboarding, nextObjectives, nextPlans, nextTiming, nextBenchmarks, nextSummary] =
        await Promise.all([
          api.careerOptions(),
          api.onboarding(),
          api.okr(),
          api.careerPlan(),
          api.careerTiming(),
          api.benchmarks(),
          api.benchmarkSummary(),
        ])
      setOptions(nextOptions)
      setOnboarding(nextOnboarding)
      setObjectives(nextObjectives)
      setPlans(nextPlans)
      setTiming(nextTiming)
      setBenchmarks(nextBenchmarks)
      setBenchmarkSummary(nextSummary)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [])

  useEffect(() => {
    void refresh()
    setToken(window.localStorage.getItem(TOKEN_KEY) ?? '')
  }, [refresh])

  const run = async (task: () => Promise<void>) => {
    setBusy(true)
    setError('')
    try {
      await task()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const toggleTask = (id: number, done: boolean) =>
    run(async () => {
      await api.checkOnboarding(id, done)
      await refresh()
    })

  const reseed = () =>
    run(async () => {
      const result = await api.seedOnboarding()
      if (result.added === 0) {
        setError('默认目录里的项都已经有了，没有新增。')
      }
      await refresh()
    })

  const addObjective = () =>
    run(async () => {
      if (!objectiveDraft.title.trim()) {
        setError('先写目标是什么')
        return
      }
      await api.createObjective(objectiveDraft)
      setObjectiveDraft({ title: '', period: '' })
      await refresh()
    })

  const addKr = (objectiveId: number) =>
    run(async () => {
      const draft = krDraft[objectiveId] ?? { title: '', unit: '', target: 0 }
      if (!draft.title.trim()) {
        setError('先写关键结果是什么')
        return
      }
      await api.addKeyResult(objectiveId, {
        title: draft.title,
        unit: draft.unit,
        target_value: draft.target,
      })
      setKrDraft({ ...krDraft, [objectiveId]: { title: '', unit: '', target: 0 } })
      await refresh()
    })

  const recordProgress = (krId: number, current: number) =>
    run(async () => {
      await api.updateKrProgress(krId, current)
      await refresh()
    })

  const loadReview = () =>
    run(async () => {
      setReview(await api.okrReview())
    })

  const addPlan = () =>
    run(async () => {
      if (!planDraft.title.trim()) {
        setError('先写这条规划是什么')
        return
      }
      await api.createPlanItem({
        kind: planDraft.kind,
        title: planDraft.title,
        detail: planDraft.detail,
        target_date: planDraft.target_date || null,
      })
      setPlanDraft({ kind: 'promotion', title: '', detail: '', target_date: '' })
      await refresh()
    })

  const addBenchmark = () =>
    run(async () => {
      if (!benchmarkDraft.source.trim()) {
        setError('来源是必填的——没有来源的区间没有参考价值')
        return
      }
      await api.createBenchmark(benchmarkDraft)
      setBenchmarkDraft({
        company: '',
        title: '',
        city: '',
        range_low: 0,
        range_high: 0,
        period: '',
        source: '',
        source_url: '',
        note: '',
      })
      await refresh()
    })

  const loadPreview = () =>
    run(async () => {
      window.localStorage.setItem(TOKEN_KEY, token)
      setPreview(await api.exportPreview(token))
    })

  const progress = onboarding?.progress

  return (
    <div className="space-y-4">
      {error && (
        <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</div>
      )}

      <div className="flex flex-wrap gap-1">
        {PANELS.map((item) => (
          <button
            key={item.key}
            type="button"
            onClick={() => setPanel(item.key)}
            className={
              item.key === panel
                ? 'rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white'
                : 'rounded-md px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100'
            }
          >
            {item.label}
          </button>
        ))}
      </div>

      {/* --------------------------- 入职清单 --------------------------- */}
      {panel === 'onboarding' && onboarding && (
        <>
          <section className="rounded-lg border border-slate-200 bg-white p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="text-sm font-semibold">入职清单</h2>
                <p className="mt-0.5 text-xs text-slate-500">
                  每项都带「为什么需要 + 去哪里办」——只写事项名的清单最后都会变成摆设。
                </p>
              </div>
              <button
                type="button"
                disabled={busy}
                onClick={() => void reseed()}
                className="rounded-md border border-slate-300 px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-50"
              >
                补入默认目录
              </button>
            </div>

            <div className="mt-3">
              <div className="flex items-center justify-between text-xs text-slate-600">
                <span>
                  已完成 {progress?.done}/{progress?.total}
                  {progress?.complete ? ' · 全部搞定' : ` · 还剩 ${progress?.remaining} 项`}
                </span>
                <span>{progress?.pct}%</span>
              </div>
              <div className="mt-1 h-2 w-full overflow-hidden rounded-full bg-slate-100">
                <div
                  className="h-full rounded-full bg-emerald-500"
                  style={{ width: `${progress?.pct ?? 0}%` }}
                />
              </div>
              <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-slate-500">
                {(progress?.by_category ?? []).map((item) => (
                  <span key={item.category} className="rounded bg-slate-100 px-1.5 py-0.5">
                    {item.category} {item.done}/{item.total}
                  </span>
                ))}
              </div>
            </div>
          </section>

          <section className="space-y-2">
            {(options?.categories ?? []).map((category) => {
              const rows = onboarding.tasks.filter((task) => task.category === category)
              if (rows.length === 0) {
                return null
              }
              return (
                <div key={category} className="rounded-lg border border-slate-200 bg-white p-3">
                  <div className="text-xs font-medium text-slate-700">{category}</div>
                  <div className="mt-2 space-y-2">
                    {rows.map((task) => (
                      <div
                        key={task.id}
                        className={
                          task.done
                            ? 'rounded-md border border-emerald-200 bg-emerald-50 p-2 text-xs'
                            : 'rounded-md border border-slate-200 p-2 text-xs'
                        }
                      >
                        <label className="flex items-start gap-2">
                          <input
                            type="checkbox"
                            className="mt-0.5"
                            checked={task.done}
                            onChange={(event) => void toggleTask(task.id, event.target.checked)}
                          />
                          <span className={task.done ? 'text-slate-500 line-through' : 'font-medium text-slate-800'}>
                            {task.title}
                          </span>
                        </label>
                        {task.why_needed && <div className="mt-1 pl-5 text-slate-500">{task.why_needed}</div>}
                        {task.where_to_do && <div className="mt-0.5 pl-5 text-sky-800">去哪办：{task.where_to_do}</div>}
                      </div>
                    ))}
                  </div>
                </div>
              )
            })}
          </section>
        </>
      )}

      {/* --------------------------- 试用期 OKR --------------------------- */}
      {panel === 'okr' && (
        <>
          <section className="rounded-lg border border-slate-200 bg-white p-4">
            <h2 className="text-sm font-semibold">试用期 OKR</h2>
            <p className="mt-0.5 text-xs text-slate-500">
              进展由「当前值 / 目标值」算出，不单独存——避免出现"进度 80% 但当前值还是 0"这种自相矛盾的数据。
            </p>

            <div className="mt-3 flex flex-wrap items-end gap-2 text-xs">
              <input
                className="w-64 rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                placeholder="目标（如：三个月内独立负责一块业务）"
                value={objectiveDraft.title}
                onChange={(event) => setObjectiveDraft({ ...objectiveDraft, title: event.target.value })}
              />
              <input
                className="w-40 rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                placeholder="周期（如 2026Q4）"
                value={objectiveDraft.period}
                onChange={(event) => setObjectiveDraft({ ...objectiveDraft, period: event.target.value })}
              />
              <button
                type="button"
                disabled={busy}
                onClick={() => void addObjective()}
                className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white disabled:bg-slate-300"
              >
                加目标
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => void loadReview()}
                className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
              >
                生成转正述职稿
              </button>
            </div>
          </section>

          {objectives.map((objective) => (
            <section key={objective.id} className="rounded-lg border border-slate-200 bg-white p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="text-sm font-medium text-slate-800">
                  {objective.title}
                  {objective.period ? ` · ${objective.period}` : ''}
                  <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600">
                    {objective.status_label}
                  </span>
                </div>
                <div className="flex items-center gap-2 text-xs text-slate-600">
                  <span>
                    {objective.pct}% · {objective.kr_done}/{objective.krs.length} 个 KR 达成
                  </span>
                  <button
                    type="button"
                    onClick={() => void run(async () => {
                      await api.deleteObjective(objective.id)
                      await refresh()
                    })}
                    className="rounded border border-slate-300 px-2 py-0.5 text-[11px] hover:bg-slate-50"
                  >
                    删除
                  </button>
                </div>
              </div>

              <div className="mt-2 space-y-2">
                {objective.krs.map((kr) => (
                  <div key={kr.id} className="rounded-md border border-slate-100 p-2 text-xs">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="text-slate-800">{kr.title}</span>
                      <span className="text-slate-500">
                        {kr.current_value} / {kr.target_value} {kr.unit} · 信心 {kr.confidence}
                      </span>
                    </div>
                    <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
                      <div
                        className={kr.pct >= 100 ? 'h-full bg-emerald-500' : 'h-full bg-sky-500'}
                        style={{ width: `${Math.min(100, kr.pct)}%` }}
                      />
                    </div>
                    <div className="mt-1 flex items-center gap-2">
                      <span className="text-slate-500">记进展：</span>
                      <input
                        type="number"
                        className="w-20 rounded border border-slate-300 px-1.5 py-0.5"
                        defaultValue={kr.current_value}
                        onBlur={(event) => {
                          const value = Number(event.target.value)
                          if (value !== kr.current_value) {
                            void recordProgress(kr.id, value)
                          }
                        }}
                      />
                      <button
                        type="button"
                        onClick={() => void run(async () => {
                          await api.deleteKr(kr.id)
                          await refresh()
                        })}
                        className="rounded border border-slate-300 px-2 py-0.5 text-[11px] hover:bg-slate-50"
                      >
                        删除
                      </button>
                    </div>
                  </div>
                ))}
              </div>

              <div className="mt-3 flex flex-wrap items-end gap-2 text-xs">
                <input
                  className="w-64 rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                  placeholder="加一条关键结果"
                  value={(krDraft[objective.id] ?? { title: '' }).title}
                  onChange={(event) =>
                    setKrDraft({
                      ...krDraft,
                      [objective.id]: { ...(krDraft[objective.id] ?? { unit: '', target: 0 }), title: event.target.value },
                    })
                  }
                />
                <input
                  className="w-20 rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                  placeholder="单位"
                  value={(krDraft[objective.id] ?? { unit: '' }).unit}
                  onChange={(event) =>
                    setKrDraft({
                      ...krDraft,
                      [objective.id]: { ...(krDraft[objective.id] ?? { title: '', target: 0 }), unit: event.target.value },
                    })
                  }
                />
                <input
                  type="number"
                  className="w-24 rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                  placeholder="目标值"
                  value={(krDraft[objective.id] ?? { target: 0 }).target}
                  onChange={(event) =>
                    setKrDraft({
                      ...krDraft,
                      [objective.id]: {
                        ...(krDraft[objective.id] ?? { title: '', unit: '' }),
                        target: Number(event.target.value),
                      },
                    })
                  }
                />
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void addKr(objective.id)}
                  className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
                >
                  加 KR
                </button>
              </div>
            </section>
          ))}

          {objectives.length === 0 && (
            <div className="rounded-lg border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">
              还没有目标。试用期是唯一有明确终点的阶段——把目标写下来，转正时才有话可说。
            </div>
          )}

          {review && (
            <section className="rounded-lg border border-slate-200 bg-white p-4">
              <h2 className="text-sm font-semibold">{review.title}</h2>
              <div className="mt-2 flex flex-wrap gap-2 text-[11px]">
                {review.note && <span className="rounded bg-amber-50 px-2 py-0.5 text-amber-800">{review.note}</span>}
                <button
                  type="button"
                  className="rounded border border-slate-300 px-2 py-0.5 text-slate-600 hover:bg-slate-50"
                  onClick={() => void navigator.clipboard.writeText(review.markdown)}
                >
                  复制 Markdown
                </button>
              </div>
              <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap rounded-md bg-slate-50 p-3 text-xs text-slate-700">
                {review.markdown}
              </pre>
            </section>
          )}
        </>
      )}

      {/* --------------------------- 长期与薪资 --------------------------- */}
      {panel === 'longterm' && (
        <>
          <section className="rounded-lg border border-slate-200 bg-white p-4">
            <h2 className="text-sm font-semibold">长期规划</h2>
            <p className="mt-0.5 text-xs text-slate-500">
              晋升与跳槽的时机不替你判断——下面的自查清单只给你「该问自己什么 + 数据去哪看」。
            </p>

            <div className="mt-3 flex flex-wrap items-end gap-2 text-xs">
              <select
                className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                value={planDraft.kind}
                onChange={(event) => setPlanDraft({ ...planDraft, kind: event.target.value })}
              >
                {(options?.plan_kinds ?? []).map((item) => (
                  <option key={item.key} value={item.key}>
                    {PLAN_KIND_LABEL[item.key] ?? item.label}
                  </option>
                ))}
              </select>
              <input
                className="w-56 rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                placeholder="一句话（如：明年年中具备晋升条件）"
                value={planDraft.title}
                onChange={(event) => setPlanDraft({ ...planDraft, title: event.target.value })}
              />
              <input
                type="date"
                className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                value={planDraft.target_date}
                onChange={(event) => setPlanDraft({ ...planDraft, target_date: event.target.value })}
              />
              <button
                type="button"
                disabled={busy}
                onClick={() => void addPlan()}
                className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white disabled:bg-slate-300"
              >
                加一条
              </button>
            </div>

            <div className="mt-3 space-y-1">
              {plans.map((item) => (
                <div key={item.id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-slate-100 px-3 py-1.5 text-xs">
                  <span className="text-slate-800">
                    <span className="mr-2 rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600">
                      {PLAN_KIND_LABEL[item.kind] ?? item.kind}
                    </span>
                    {item.title}
                    {item.target_date ? ` · 目标 ${item.target_date}` : ''}
                    <span className="ml-2 text-slate-500">{PLAN_STATUS_LABEL[item.status] ?? item.status}</span>
                  </span>
                  <button
                    type="button"
                    onClick={() => void run(async () => {
                      await api.deletePlanItem(item.id)
                      await refresh()
                    })}
                    className="rounded border border-slate-300 px-2 py-0.5 text-[11px] hover:bg-slate-50"
                  >
                    删除
                  </button>
                </div>
              ))}
              {plans.length === 0 && <p className="text-xs text-slate-500">还没有规划条目。</p>}
            </div>
          </section>

          {timing && (
            <section className="rounded-lg border border-slate-200 bg-white p-4">
              <h2 className="text-sm font-semibold">时机自查</h2>
              <div className="mt-2 grid gap-3 lg:grid-cols-2">
                {[
                  { label: '晋升前该问自己', rows: timing.promotion },
                  { label: '跳槽前该问自己', rows: timing.switch },
                ].map((group) => (
                  <div key={group.label}>
                    <div className="text-xs font-medium text-slate-700">{group.label}</div>
                    <ul className="mt-1 space-y-1 text-xs">
                      {group.rows.map((row) => (
                        <li key={row.question} className="rounded-md border border-slate-100 px-2 py-1.5">
                          <div className="text-slate-800">{row.question}</div>
                          <div className="mt-0.5 text-slate-500">{row.why}</div>
                          <div className="mt-0.5 text-sky-800">去哪看：{row.where}</div>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
              {timing.note && <div className="mt-2 text-[11px] text-slate-400">{timing.note}</div>}
            </section>
          )}

          <section className="rounded-lg border border-slate-200 bg-white p-4">
            <h2 className="text-sm font-semibold">薪资基准（只存来源与区间）</h2>
            <p className="mt-0.5 text-xs text-slate-500">
              这里不会给出"你应该要多少"的结论——它只汇总**你自己记录的**来源与区间，并始终带样本量提示。
            </p>

            <div className="mt-3 grid gap-2 text-xs sm:grid-cols-3 lg:grid-cols-5">
              <input
                className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                placeholder="公司"
                value={benchmarkDraft.company}
                onChange={(event) => setBenchmarkDraft({ ...benchmarkDraft, company: event.target.value })}
              />
              <input
                className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                placeholder="岗位"
                value={benchmarkDraft.title}
                onChange={(event) => setBenchmarkDraft({ ...benchmarkDraft, title: event.target.value })}
              />
              <input
                className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                placeholder="城市"
                value={benchmarkDraft.city}
                onChange={(event) => setBenchmarkDraft({ ...benchmarkDraft, city: event.target.value })}
              />
              <input
                type="number"
                className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                placeholder="区间下限（年薪）"
                value={benchmarkDraft.range_low || ''}
                onChange={(event) => setBenchmarkDraft({ ...benchmarkDraft, range_low: Number(event.target.value) })}
              />
              <input
                type="number"
                className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                placeholder="区间上限（年薪）"
                value={benchmarkDraft.range_high || ''}
                onChange={(event) => setBenchmarkDraft({ ...benchmarkDraft, range_high: Number(event.target.value) })}
              />
              <input
                className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                placeholder="时期（如 2026Q3）"
                value={benchmarkDraft.period}
                onChange={(event) => setBenchmarkDraft({ ...benchmarkDraft, period: event.target.value })}
              />
              <input
                className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                placeholder="来源（必填：同学打听 / 招聘网站 / 匿名社区）"
                value={benchmarkDraft.source}
                onChange={(event) => setBenchmarkDraft({ ...benchmarkDraft, source: event.target.value })}
              />
              <input
                className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                placeholder="来源链接（可选）"
                value={benchmarkDraft.source_url}
                onChange={(event) => setBenchmarkDraft({ ...benchmarkDraft, source_url: event.target.value })}
              />
              <button
                type="button"
                disabled={busy}
                onClick={() => void addBenchmark()}
                className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white disabled:bg-slate-300"
              >
                记一条
              </button>
            </div>

            {benchmarkSummary && benchmarkSummary.count > 0 && (
              <div className="mt-3 rounded-md border border-slate-200 bg-slate-50 p-3 text-xs">
                <div className="text-slate-800">
                  你记录的 {benchmarkSummary.count} 条区间的中位数：{wan(benchmarkSummary.median_low)} –{' '}
                  {wan(benchmarkSummary.median_high)}
                  （最低 {wan(benchmarkSummary.min_low)}，最高 {wan(benchmarkSummary.max_high)}）
                </div>
                <div className="mt-1 text-slate-500">
                  来源：{benchmarkSummary.sources.join('、') || '—'}；其中已核实的{' '}
                  {benchmarkSummary.verified_count}/{benchmarkSummary.count} 条
                </div>
                <div className="mt-1 text-amber-800">{benchmarkSummary.note}</div>
              </div>
            )}

            <div className="mt-3 space-y-1">
              {benchmarks.map((item) => (
                <div key={item.id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-slate-100 px-3 py-1.5 text-xs">
                  <span className="text-slate-700">
                    {item.company || '（未填公司）'} · {wan(item.range_low)}–{wan(item.range_high)}
                    {item.period ? ` · ${item.period}` : ''} · 来源 {item.source}
                    {item.source_url && (
                      <a href={item.source_url} target="_blank" rel="noreferrer" className="ml-1 text-sky-700 underline">
                        链接
                      </a>
                    )}
                    <span className={item.verified ? 'ml-2 text-emerald-700' : 'ml-2 text-amber-700'}>
                      {item.verified ? '已核实' : '未核实'}
                    </span>
                  </span>
                  <button
                    type="button"
                    onClick={() => void run(async () => {
                      await api.deleteBenchmark(item.id)
                      await refresh()
                    })}
                    className="rounded border border-slate-300 px-2 py-0.5 text-[11px] hover:bg-slate-50"
                  >
                    删除
                  </button>
                </div>
              ))}
            </div>
          </section>
        </>
      )}

      {/* --------------------------- 数据导出 --------------------------- */}
      {panel === 'export' && (
        <>
          <section className="rounded-lg border border-slate-200 bg-white p-4">
            <h2 className="text-sm font-semibold">数据导出</h2>
            <p className="mt-0.5 text-xs text-slate-500">
              本地也不能裸奔：导出接口要令牌，同网段的任何进程都不该能读走你的全量求职数据。
              令牌在 <code className="rounded bg-slate-100 px-1">backend/.env</code> 的{' '}
              <code className="rounded bg-slate-100 px-1">EXPORT_TOKEN</code> 里配置。
            </p>

            <div className="mt-3 flex flex-wrap items-end gap-2 text-xs">
              <input
                type="password"
                className="w-72 rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                placeholder="导出令牌"
                value={token}
                onChange={(event) => setToken(event.target.value)}
              />
              <button
                type="button"
                disabled={busy || !token}
                onClick={() => void loadPreview()}
                className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white disabled:bg-slate-300"
              >
                查看导出预览
              </button>
            </div>

            {preview && (
              <>
                <div className="mt-3 text-xs text-slate-600">
                  共 {preview.sections.length} 个板块 ·{' '}
                  {preview.sections.reduce((sum, section) => sum + section.rows, 0)} 行数据 ·
                  生成于 {new Date(preview.generated_at).toLocaleString('zh-CN')}
                </div>

                <div className="mt-3 flex flex-wrap gap-2">
                  {preview.formats.map((format) => (
                    <a
                      key={format.key}
                      href={api.exportFileUrl(format.key as 'markdown' | 'csv' | 'json', token)}
                      className="rounded-md border border-slate-300 px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-50"
                    >
                      下载 {format.label}
                    </a>
                  ))}
                </div>

                <table className="mt-3 w-full text-left text-xs">
                  <thead className="text-slate-500">
                    <tr>
                      <th className="py-1 pr-2">板块</th>
                      <th className="py-1 pr-2">行数</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.sections.map((section) => (
                      <tr key={section.key} className="border-t border-slate-100">
                        <td className="py-1 pr-2">{section.title}</td>
                        <td className={`py-1 pr-2 ${section.rows === 0 ? 'text-slate-400' : 'text-slate-700'}`}>
                          {section.rows}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
          </section>

          <section className="rounded-lg border border-slate-200 bg-white p-4">
            <h2 className="text-sm font-semibold">外部集成</h2>
            <p className="mt-0.5 text-xs text-slate-500">
              真实凭证没拿到之前不写假实现——那只会让你以为数据同步了。
            </p>
            <div className="mt-3 space-y-2 text-xs">
              {(preview?.integrations ?? []).map((item) => (
                <div key={item.key} className="rounded-md border border-amber-200 bg-amber-50 p-3 text-amber-900">
                  <div className="font-medium">
                    {item.label}
                    <span className="ml-2 rounded bg-white/70 px-1.5 py-0.5 text-[11px]">未接入</span>
                  </div>
                  <div className="mt-1">{item.detail}</div>
                </div>
              ))}
              {(preview?.integrations ?? []).length === 0 && (
                <p className="text-slate-500">先查看导出预览，这里会列出集成状态。</p>
              )}
            </div>
          </section>
        </>
      )}
    </div>
  )
}
