/** 投递执行台（M10）：抓候选 → 逐条确认 → 限速投递。
 *
 * 这个页面的交互刻意做成"多一道确认"：
 * 「抓取候选」只会进队列，「确认投递」才会真的发出去。
 * 后端的 /execute 也只处理已确认的记录，所以这里即使点错也不会投出去一批。
 */

import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import type {
  ApplyExecutionOutcome,
  ApplyQueueSnapshot,
  ApplyQueueTask,
  ApplyRule,
  ApplySearchOutcome,
  ApplySettings,
  ApplyStatus,
} from '../api/types'

const PLATFORMS = [
  { value: 'mock', label: 'Mock 演练（不会真的投递）' },
  { value: 'boss', label: 'Boss 直聘（需先校准选择器）' },
]

const RULE_KIND_LABELS: Record<string, string> = {
  blacklist: '黑名单公司',
  dedup: '已投过去重',
  headhunter: '猎头岗位',
  hr_active: 'HR 活跃度',
  salary: '薪资区间',
  experience: '经验要求',
  degree: '学历要求',
  company_size: '公司规模',
  company_stage: '融资阶段',
  industry: '行业',
  keyword: '关键词',
}

const RISK_POINTS = [
  '只会操作你自己已登录的账号，不会代注册、不会换账号、不会绕过验证码。',
  '每投一条都要你确认；默认每分钟最多 3 条、每天最多 30 条，中间有 3–9 秒随机间隔。',
  '一旦出现验证码、滑块或登录失效，会立刻停止并停投当天剩余时间。',
  '平台规则可能不允许自动化操作，账号受限的风险由你自行评估与承担。',
]

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function ruleParams(rule: ApplyRule): string {
  const entries = Object.entries(rule.params ?? {})
  if (entries.length === 0) {
    return '—'
  }
  return entries
    .map(([key, value]) => `${key}=${Array.isArray(value) ? value.join('/') : String(value)}`)
    .join('，')
}

function statusLabel(status: string): string {
  return (
    { pending: '待确认', approved: '已确认', sent: '已投递', failed: '失败', skipped: '已跳过' }[status] ?? status
  )
}

export default function ApplyDesk() {
  const [status, setStatus] = useState<ApplyStatus | null>(null)
  const [settings, setSettings] = useState<ApplySettings | null>(null)
  const [rules, setRules] = useState<ApplyRule[]>([])
  const [queue, setQueue] = useState<ApplyQueueTask[]>([])
  const [snapshot, setSnapshot] = useState<ApplyQueueSnapshot | null>(null)

  const [keywords, setKeywords] = useState('后端 Python')
  const [candidateLimit, setCandidateLimit] = useState(20)
  const [search, setSearch] = useState<ApplySearchOutcome | null>(null)
  const [picked, setPicked] = useState<number[]>([])
  const [drafts, setDrafts] = useState<Record<number, string>>({})
  const [outcome, setOutcome] = useState<ApplyExecutionOutcome | null>(null)
  const [dryRun, setDryRun] = useState(true)

  const [riskAck, setRiskAck] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    try {
      const [nextStatus, nextSettings, nextRules, nextQueue, nextSnapshot] = await Promise.all([
        api.applyStatus(),
        api.applySettings(),
        api.applyRules(),
        api.applyQueue(),
        api.applyQueueSnapshot(),
      ])
      setStatus(nextStatus)
      setSettings(nextSettings)
      setRules(nextRules)
      setQueue(nextQueue)
      setSnapshot(nextSnapshot)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const saveSettings = async (patch: Partial<ApplySettings>) => {
    if (!settings) {
      return
    }
    setBusy(true)
    setError('')
    try {
      const next = await api.updateApplySettings({ ...settings, ...patch })
      setSettings(next)
      await refresh()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const runSearch = async () => {
    setBusy(true)
    setError('')
    setOutcome(null)
    try {
      const words = keywords
        .split(/[\s,，]+/)
        .map((word) => word.trim())
        .filter(Boolean)
      const result = await api.searchApplyCandidates(words, candidateLimit)
      setSearch(result)
      setPicked(result.candidates.filter((item) => item.passed && item.task_id).map((item) => item.task_id as number))
      setDrafts({})
      await refresh()
    } catch (err) {
      setError(errorMessage(err))
      setSearch(null)
    } finally {
      setBusy(false)
    }
  }

  const approvePicked = async () => {
    if (picked.length === 0) {
      setError('先勾选要投的岗位')
      return
    }
    setBusy(true)
    setError('')
    try {
      const overrides: Record<number, string> = {}
      for (const id of picked) {
        const draft = drafts[id]
        if (draft !== undefined && draft.trim()) {
          overrides[id] = draft
        }
      }
      await api.approveApplyTasks(picked, overrides)
      setPicked([])
      setDrafts({})
      await refresh()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const skipPicked = async () => {
    if (picked.length === 0) {
      setError('先勾选要跳过的岗位')
      return
    }
    setBusy(true)
    setError('')
    try {
      await api.skipApplyTasks(picked)
      setPicked([])
      await refresh()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const execute = async () => {
    setBusy(true)
    setError('')
    try {
      const result = await api.executeApply(10, dryRun)
      setOutcome(result)
      await refresh()
    } catch (err) {
      setError(errorMessage(err))
      setOutcome(null)
    } finally {
      setBusy(false)
    }
  }

  const toggleRule = async (rule: ApplyRule) => {
    try {
      await api.updateApplyRule(rule.id, { ...rule, enabled: !rule.enabled })
      await refresh()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const pendingQueue = queue.filter((item) => item.status === 'pending')
  const approvedQueue = queue.filter((item) => item.status === 'approved')
  const doneQueue = queue.filter((item) => ['sent', 'failed', 'skipped'].includes(item.status))
  const enabled = status?.enabled ?? false

  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      {error && (
        <div className="rounded-control border border-ng bg-ng-soft px-3 py-2 text-sm text-ng">{error}</div>
      )}

      {status?.broken && (
        <div className="rounded-control border border-warn bg-warn-soft px-3 py-2 text-sm text-warn">
          <div className="font-medium">已触发熔断，今天不会再投递任何岗位。</div>
          <div className="mt-1 text-xs">
            恢复时间：{status.circuit_broken_until ? new Date(status.circuit_broken_until).toLocaleString('zh-CN') : '—'}
            。请先处理平台提示（验证码 / 重新登录），明天再继续。
          </div>
        </div>
      )}

      {/* 状态与开关 */}
      <section className="shrink-0 rounded-card border border-ink-1 bg-surface p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold">投递执行台</h2>
            <p className="mt-0.5 text-xs text-ink-6">
              抓候选 → 你逐条确认 → 按限速投递。模块默认关闭，开启需要显式确认风险。
            </p>
          </div>
          <div className="flex items-center gap-3 text-xs">
            <span className="rounded-full bg-sunken px-2.5 py-1 text-ink-6">
              今日额度 {status ? `${status.daily_sent}/${status.daily_limit}` : '—'}
            </span>
            <span
              className={
                enabled
                  ? 'rounded-full bg-ok-soft px-2.5 py-1 text-ok'
                  : 'rounded-full bg-sunken px-2.5 py-1 text-ink-6'
              }
            >
              {enabled ? '已开启' : '已关闭'}
            </span>
          </div>
        </div>

        {!enabled && (
          <div className="mt-3 rounded-control border border-ink-1 bg-paper p-3">
            <div className="text-xs font-medium text-ink-7">开启前请先确认你了解这几点：</div>
            <ul className="mt-2 space-y-1 text-xs text-ink-6">
              {RISK_POINTS.map((point) => (
                <li key={point}>— {point}</li>
              ))}
            </ul>
            <label className="mt-3 flex items-start gap-2 text-xs text-ink-7">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={riskAck}
                onChange={(event) => setRiskAck(event.target.checked)}
              />
              <span>我了解上述风险，并只在本人账号上使用这个功能。</span>
            </label>
            <button
              type="button"
              disabled={!riskAck || busy || !settings}
              onClick={() => void saveSettings({ enabled: true, risk_acknowledged: true })}
              className="mt-3 rounded-control bg-ink-9 px-3 py-1.5 text-xs text-surface disabled:cursor-not-allowed disabled:bg-ink-2"
            >
              开启半自动投递
            </button>
          </div>
        )}

        {enabled && settings && (
          <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <label className="text-xs text-ink-6">
              平台
              <select
                className="mt-1 w-full rounded-control border border-ink-2 px-2 py-1.5 text-sm"
                value={settings.platform}
                onChange={(event) => void saveSettings({ platform: event.target.value })}
              >
                {PLATFORMS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-xs text-ink-6">
              每分钟上限（≤{settings.max_per_minute}）
              <input
                type="number"
                min={1}
                max={settings.max_per_minute}
                className="mt-1 w-full rounded-control border border-ink-2 px-2 py-1.5 text-sm"
                value={settings.per_minute}
                onChange={(event) => void saveSettings({ per_minute: Number(event.target.value) })}
              />
            </label>
            <label className="text-xs text-ink-6">
              每天上限（≤{settings.max_per_day}）
              <input
                type="number"
                min={1}
                max={settings.max_per_day}
                className="mt-1 w-full rounded-control border border-ink-2 px-2 py-1.5 text-sm"
                value={settings.per_day}
                onChange={(event) => void saveSettings({ per_day: Number(event.target.value) })}
              />
            </label>
            <label className="text-xs text-ink-6">
              期望月薪下限（元）
              <input
                type="number"
                min={0}
                step={1000}
                className="mt-1 w-full rounded-control border border-ink-2 px-2 py-1.5 text-sm"
                value={settings.want_salary_min}
                onChange={(event) => void saveSettings({ want_salary_min: Number(event.target.value) })}
              />
            </label>
            <div className="sm:col-span-2 lg:col-span-4 flex flex-wrap items-center justify-between gap-2 text-xs text-ink-6">
              <span>
                每条之间随机等待 {settings.jitter_min_seconds}–{settings.jitter_max_seconds} 秒（可在后端设置里调整）。
              </span>
              <button
                type="button"
                onClick={() => void saveSettings({ enabled: false, risk_acknowledged: false })}
                className="rounded-control border border-ink-2 px-2.5 py-1 text-ink-6 hover:bg-paper"
              >
                关闭模块
              </button>
            </div>
          </div>
        )}

        {status && !status.adapter_ready && (
          <div className="mt-3 rounded-control border border-warn bg-warn-soft px-3 py-2 text-xs text-warn">
            适配器还没就绪：{status.adapter_note}
          </div>
        )}
      </section>

      {/* 抓候选 */}

      <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[minmax(320px,380px)_minmax(0,1fr)]">
        <div className="min-h-0 space-y-4 overflow-y-auto pr-1">
      <section className="rounded-card border border-ink-1 bg-surface p-4">
        <h2 className="text-sm font-semibold">1. 抓取候选</h2>
        <p className="mt-0.5 text-xs text-ink-6">
          抓取只会把岗位放进待确认队列，**不会投递**。不通过的岗位会连原因一起列出来。
        </p>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="text-xs text-ink-6">
            关键词（空格分隔）
            <input
              className="mt-1 w-56 rounded-control border border-ink-2 px-2 py-1.5 text-sm"
              value={keywords}
              onChange={(event) => setKeywords(event.target.value)}
            />
          </label>
          <label className="text-xs text-ink-6">
            抓取数量
            <input
              type="number"
              min={1}
              max={60}
              className="mt-1 w-20 rounded-control border border-ink-2 px-2 py-1.5 text-sm"
              value={candidateLimit}
              onChange={(event) => setCandidateLimit(Number(event.target.value))}
            />
          </label>
          <button
            type="button"
            disabled={busy || !enabled}
            onClick={() => void runSearch()}
            className="rounded-control bg-ink-9 px-3 py-1.5 text-sm text-surface disabled:cursor-not-allowed disabled:bg-ink-2"
          >
            抓取候选
          </button>
          {!enabled && <span className="text-xs text-ink-6">先在上面开启模块</span>}
        </div>

        {search && (
          <div className="mt-4 space-y-3">
            <div className="text-xs text-ink-6">
              抓到 {search.fetched} 个 · 通过过滤 {search.passed} · 被拦 {search.blocked} · 新入队 {search.queued}
              {search.duplicates > 0 && ` · 队列里已有 ${search.duplicates}`}
            </div>
            {search.notes.map((note) => (
              <div key={note} className="rounded-control border border-warn bg-warn-soft px-3 py-2 text-xs text-warn">
                {note}
              </div>
            ))}

            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="text-ink-6">
                  <tr>
                    <th className="py-1.5 pr-2">选</th>
                    <th className="py-1.5 pr-2">公司</th>
                    <th className="py-1.5 pr-2">岗位</th>
                    <th className="py-1.5 pr-2">薪资</th>
                    <th className="py-1.5 pr-2">城市</th>
                    <th className="py-1.5 pr-2">匹配分</th>
                    <th className="py-1.5 pr-2">结果</th>
                  </tr>
                </thead>
                <tbody>
                  {search.candidates.map((item) => (
                    <tr key={`${item.external_id}-${item.job_posting_id}`} className="border-t border-ink-1 align-top">
                      <td className="py-2 pr-2">
                        <input
                          type="checkbox"
                          disabled={!item.passed || !item.task_id}
                          checked={item.task_id !== null && picked.includes(item.task_id)}
                          onChange={(event) => {
                            if (item.task_id === null) {
                              return
                            }
                            const id = item.task_id
                            setPicked((current) =>
                              event.target.checked ? [...current, id] : current.filter((value) => value !== id),
                            )
                          }}
                        />
                      </td>
                      <td className="py-2 pr-2">{item.company}</td>
                      <td className="py-2 pr-2">
                        {item.jd_url ? (
                          <a className="text-info hover:underline" href={item.jd_url} target="_blank" rel="noreferrer">
                            {item.title}
                          </a>
                        ) : (
                          item.title
                        )}
                      </td>
                      <td className="py-2 pr-2">{item.salary_range || '—'}</td>
                      <td className="py-2 pr-2">{item.city || '—'}</td>
                      <td className="py-2 pr-2">{item.matched_score}</td>
                      <td className="py-2 pr-2">
                        {item.passed ? (
                          <span className="text-ok">通过</span>
                        ) : (
                          <span className="text-ng">
                            已拦：{item.reasons.join('；')}
                          </span>
                        )}
                        {item.passed && item.say_hi && (
                          <textarea
                            className="mt-1 w-72 rounded-control border border-ink-2 px-2 py-1 text-xs"
                            rows={2}
                            value={drafts[item.task_id as number] ?? item.say_hi}
                            onChange={(event) =>
                              setDrafts((current) => ({ ...current, [item.task_id as number]: event.target.value }))
                            }
                          />
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                disabled={busy || picked.length === 0}
                onClick={() => void approvePicked()}
                className="rounded-control bg-ink-9 px-3 py-1.5 text-xs text-surface disabled:cursor-not-allowed disabled:bg-ink-2"
              >
                确认投递（{picked.length} 条）
              </button>
              <button
                type="button"
                disabled={busy || picked.length === 0}
                onClick={() => void skipPicked()}
                className="rounded-control border border-ink-2 px-3 py-1.5 text-xs text-ink-6 hover:bg-paper disabled:cursor-not-allowed"
              >
                跳过
              </button>
            </div>
          </div>
        )}
      </section>

      {/* 队列 */}
      <section className="rounded-card border border-ink-1 bg-surface p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">2. 待投队列</h2>
          <div className="text-xs text-ink-6">
            {snapshot
              ? `共 ${snapshot.total} · 待确认 ${snapshot.pending} · 已确认 ${snapshot.approved} · 已投 ${snapshot.sent} · 失败 ${snapshot.failed}`
              : '—'}
          </div>
        </div>

        {queue.length === 0 ? (
          <p className="mt-3 text-xs text-ink-6">队列是空的。先抓一批候选。</p>
        ) : (
          <div className="mt-3 space-y-4">
            {[
              { title: `已确认待投（${approvedQueue.length}）`, items: approvedQueue },
              { title: `待确认（${pendingQueue.length}）`, items: pendingQueue },
              { title: `已处理（${doneQueue.length}）`, items: doneQueue.slice(0, 20) },
            ].map((group) => (
              <div key={group.title}>
                <div className="text-xs font-medium text-ink-6">{group.title}</div>
                {group.items.length === 0 ? (
                  <div className="mt-1 text-xs text-ink-4">—</div>
                ) : (
                  <table className="mt-1 w-full text-left text-xs">
                    <tbody>
                      {group.items.map((item) => (
                        <tr key={item.id} className="border-t border-ink-1 align-top">
                          <td className="py-1.5 pr-2 text-ink-6">#{item.id}</td>
                          <td className="py-1.5 pr-2">
                            {item.company} · {item.title}
                          </td>
                          <td className="py-1.5 pr-2 text-ink-6">{item.salary_range || '—'}</td>
                          <td className="py-1.5 pr-2">
                            <span
                              className={
                                item.status === 'sent'
                                  ? 'text-ok'
                                  : item.status === 'failed'
                                    ? 'text-ng'
                                    : 'text-ink-6'
                              }
                            >
                              {statusLabel(item.status)}
                            </span>
                            {item.error && <div className="text-ng">{item.error}</div>}
                          </td>
                          <td className="py-1.5 pr-2 text-ink-6">{item.say_hi_text.slice(0, 40)}…</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            ))}

            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                disabled={busy || approvedQueue.length === 0 || !enabled || status?.broken}
                onClick={() => void execute()}
                className="rounded-control bg-ink-9 px-3 py-1.5 text-xs text-surface disabled:cursor-not-allowed disabled:bg-ink-2"
              >
                3. 开始投递（最多 10 条）
              </button>
              <label className="flex items-center gap-1.5 text-xs text-ink-6">
                <input type="checkbox" checked={dryRun} onChange={(event) => setDryRun(event.target.checked)} />
                演练模式（只报告会投什么，不真的发送）
              </label>
            </div>

            {outcome && (
              <div className="rounded-control border border-ink-1 bg-paper p-3 text-xs">
                <div className="font-medium text-ink-7">
                  本次：尝试 {outcome.attempted} · 成功 {outcome.sent} · 失败 {outcome.failed}
                </div>
                {outcome.stopped_reason && <div className="mt-1 text-ink-6">停止原因：{outcome.stopped_reason}</div>}
                {outcome.tripped && (
                  <div className="mt-1 text-warn">熔断：{outcome.breaker_message}</div>
                )}
                <ul className="mt-2 space-y-1">
                  {outcome.items.map((item) => (
                    <li key={`${item.task_id}-${item.detail}`} className={item.ok ? 'text-ok' : 'text-ng'}>
                      {item.ok ? '✓' : '✗'} {item.company} · {item.title} —— {item.detail}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </section>
        </div>

        <div className="min-h-0 space-y-4 overflow-y-auto pr-1">
      <section className="rounded-card border border-ink-1 bg-surface p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">2. 待投队列</h2>
          <div className="text-xs text-ink-6">
            {snapshot
              ? `共 ${snapshot.total} · 待确认 ${snapshot.pending} · 已确认 ${snapshot.approved} · 已投 ${snapshot.sent} · 失败 ${snapshot.failed}`
              : '—'}
          </div>
        </div>

        {queue.length === 0 ? (
          <p className="mt-3 text-xs text-ink-6">队列是空的。先抓一批候选。</p>
        ) : (
          <div className="mt-3 space-y-4">
            {[
              { title: `已确认待投（${approvedQueue.length}）`, items: approvedQueue },
              { title: `待确认（${pendingQueue.length}）`, items: pendingQueue },
              { title: `已处理（${doneQueue.length}）`, items: doneQueue.slice(0, 20) },
            ].map((group) => (
              <div key={group.title}>
                <div className="text-xs font-medium text-ink-6">{group.title}</div>
                {group.items.length === 0 ? (
                  <div className="mt-1 text-xs text-ink-4">—</div>
                ) : (
                  <table className="mt-1 w-full text-left text-xs">
                    <tbody>
                      {group.items.map((item) => (
                        <tr key={item.id} className="border-t border-ink-1 align-top">
                          <td className="py-1.5 pr-2 text-ink-6">#{item.id}</td>
                          <td className="py-1.5 pr-2">
                            {item.company} · {item.title}
                          </td>
                          <td className="py-1.5 pr-2 text-ink-6">{item.salary_range || '—'}</td>
                          <td className="py-1.5 pr-2">
                            <span
                              className={
                                item.status === 'sent'
                                  ? 'text-ok'
                                  : item.status === 'failed'
                                    ? 'text-ng'
                                    : 'text-ink-6'
                              }
                            >
                              {statusLabel(item.status)}
                            </span>
                            {item.error && <div className="text-ng">{item.error}</div>}
                          </td>
                          <td className="py-1.5 pr-2 text-ink-6">{item.say_hi_text.slice(0, 40)}…</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            ))}

            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                disabled={busy || approvedQueue.length === 0 || !enabled || status?.broken}
                onClick={() => void execute()}
                className="rounded-control bg-ink-9 px-3 py-1.5 text-xs text-surface disabled:cursor-not-allowed disabled:bg-ink-2"
              >
                3. 开始投递（最多 10 条）
              </button>
              <label className="flex items-center gap-1.5 text-xs text-ink-6">
                <input type="checkbox" checked={dryRun} onChange={(event) => setDryRun(event.target.checked)} />
                演练模式（只报告会投什么，不真的发送）
              </label>
            </div>

            {outcome && (
              <div className="rounded-control border border-ink-1 bg-paper p-3 text-xs">
                <div className="font-medium text-ink-7">
                  本次：尝试 {outcome.attempted} · 成功 {outcome.sent} · 失败 {outcome.failed}
                </div>
                {outcome.stopped_reason && <div className="mt-1 text-ink-6">停止原因：{outcome.stopped_reason}</div>}
                {outcome.tripped && (
                  <div className="mt-1 text-warn">熔断：{outcome.breaker_message}</div>
                )}
                <ul className="mt-2 space-y-1">
                  {outcome.items.map((item) => (
                    <li key={`${item.task_id}-${item.detail}`} className={item.ok ? 'text-ok' : 'text-ng'}>
                      {item.ok ? '✓' : '✗'} {item.company} · {item.title} —— {item.detail}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </section>

      {/* 规则 */}
      <section className="rounded-card border border-ink-1 bg-surface p-4">
        <h2 className="text-sm font-semibold">过滤规则</h2>
        <p className="mt-0.5 text-xs text-ink-6">
          优先级数字小的先判断，任何一条命中就拦下这个岗位。理由会显示在候选列表里。
        </p>
        <table className="mt-3 w-full text-left text-xs">
          <thead className="text-ink-6">
            <tr>
              <th className="py-1.5 pr-2">启用</th>
              <th className="py-1.5 pr-2">优先级</th>
              <th className="py-1.5 pr-2">规则</th>
              <th className="py-1.5 pr-2">类型</th>
              <th className="py-1.5 pr-2">参数</th>
            </tr>
          </thead>
          <tbody>
            {rules.map((rule) => (
              <tr key={rule.id} className="border-t border-ink-1">
                <td className="py-1.5 pr-2">
                  <input type="checkbox" checked={rule.enabled} onChange={() => void toggleRule(rule)} />
                </td>
                <td className="py-1.5 pr-2 text-ink-6">{rule.priority}</td>
                <td className="py-1.5 pr-2">{rule.name || '—'}</td>
                <td className="py-1.5 pr-2 text-ink-6">{RULE_KIND_LABELS[rule.kind] ?? rule.kind}</td>
                <td className="py-1.5 pr-2 text-ink-6">{ruleParams(rule)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
        </div>
      </div>
    </div>
  )
}
