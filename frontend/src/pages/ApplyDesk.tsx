/** 投递执行台（M10）：抓候选 → 逐条确认 → 限速投递。
 *
 * 这个页面的交互刻意做成"多一道确认"：
 * 「抓取候选」只会进队列，「确认投递」才会真的发出去。
 * 后端的 /execute 也只处理已确认的记录，所以这里即使点错也不会投出去一批。
 */

import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import ApplyCapabilitiesPanel from '../components/ApplyCapabilities'
import ApplyNotifyPanel from '../components/ApplyNotifyPanel'
import ApplyProfilesPanel from '../components/ApplyProfilesPanel'
import ApplySchedulePanel from '../components/ApplySchedulePanel'
import type {
  ApplyExecutionOutcome,
  ApplyQueueSnapshot,
  ApplyQueueTask,
  ApplyRule,
  ApplySearchOutcome,
  ApplySettings,
  ApplySettingsPayload,
  ApplyStatus,
  AutofillBlacklistResult,
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
    {
      pending: '待确认',
      approved: '已确认',
      greeted: '已打招呼',
      sent: '已投递',
      failed: '失败',
      skipped: '已跳过',
    }[status] ?? status
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
  //: 抓详情：列表页的薪资数字被平台用自定义字体混淆（数字读不出来），**详情页是正常文本**，
  //: 所以勾上它才能拿到真薪资（以及 JD/学历/HR 状态）。代价：每条岗位都要多开一次详情页，
  //: 明显更慢、也更容易触发风控，建议同时把抓取数量调小。
  const [fetchDetail, setFetchDetail] = useState(false)
  const [search, setSearch] = useState<ApplySearchOutcome | null>(null)
  const [picked, setPicked] = useState<number[]>([])
  const [drafts, setDrafts] = useState<Record<number, string>>({})
  const [outcome, setOutcome] = useState<ApplyExecutionOutcome | null>(null)
  const [dryRun, setDryRun] = useState(true)
  //: 第一次真投建议只投 1 条（选择器还没在真机上验证过时尤其重要）
  const [executeLimit, setExecuteLimit] = useState(1)

  const [riskAck, setRiskAck] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  //: 自动拉黑的预览结果。**先预览、再确认**：误判一次会让用户永远看不到一家本来有戏的公司。
  const [autofillPreview, setAutofillPreview] = useState<AutofillBlacklistResult | null>(null)
  //: 正在上传附件的那条任务（用它显示"上传中…"，避免用户以为点了没反应）
  const [uploadingFor, setUploadingFor] = useState<number | null>(null)

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

  const saveSettings = async (patch: Partial<ApplySettingsPayload>) => {
    if (!settings) {
      return
    }
    setBusy(true)
    setError('')
    try {
      // 剔除读模型里的 capabilities（它是完整状态，形状与写接口要的输入不同），
      // 再让 patch 覆盖——patch 里的 capabilities 才是写接口认的 `ApplyCapabilitiesInput`。
      const { capabilities: _readOnly, ...rest } = settings
      const next = await api.updateApplySettings({ ...rest, ...patch })
      setSettings(next)
      await refresh()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const uploadAttachment = async (taskId: number, file: File) => {
    setUploadingFor(taskId)
    setError('')
    try {
      await api.uploadApplyAttachment(file, taskId)
      await refresh()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setUploadingFor(null)
    }
  }

  const detachAttachment = async (taskId: number) => {
    setBusy(true)
    setError('')
    try {
      await api.detachApplyAttachment(taskId)
      await refresh()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const runAutofill = async (dryRun: boolean) => {    setBusy(true)
    setError('')
    try {
      const result = await api.autofillBlacklist(dryRun)
      setAutofillPreview(result)
      if (!dryRun) {
        await refresh()
      }
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
      // 城市用持久化设置里的（后端在 payload.city 为空时回退到 settings.city）
      const result = await api.searchApplyCandidates(words, candidateLimit, '', fetchDetail)
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

  //: 队列里勾选的任务（可删除 / 拉黑）。和候选表格的勾选分开，避免两处互相干扰
  const [queuePicked, setQueuePicked] = useState<number[]>([])

  const toggleQueuePick = (taskId: number, checked: boolean) => {
    setQueuePicked((current) =>
      checked ? [...current, taskId] : current.filter((value) => value !== taskId),
    )
  }

  const deleteQueuePicked = async () => {
    if (queuePicked.length === 0) {
      setError('先勾选要从队列删除的岗位')
      return
    }
    if (!window.confirm(`从队列删除这 ${queuePicked.length} 条？\n只删除队列记录，已投递的历史不受影响。`)) {
      return
    }
    setBusy(true)
    setError('')
    try {
      await api.deleteApplyTasks(queuePicked)
      setQueuePicked([])
      await refresh()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const blacklistQueuePicked = async () => {
    if (queuePicked.length === 0) {
      setError('先勾选要拉黑的公司')
      return
    }
    if (
      !window.confirm(
        `拉黑这 ${queuePicked.length} 条对应的公司？\n` +
          '以后抓取时，这些公司的岗位会被「黑名单公司」规则直接拦下，同时这些记录会从队列移除。',
      )
    ) {
      return
    }
    setBusy(true)
    setError('')
    try {
      await api.blacklistApplyTasks(queuePicked)
      setQueuePicked([])
      await refresh()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  //: 重试失败的任务：失败通常是"我们这边的适配问题"（选择器/页面结构/浏览器被关掉），
  //: 跟岗位无关。以前 failed 是死路，只能重新抓一次岗位——而重抓会撞上"已投过去重"。
  const retryFailed = async () => {
    const ids = queue.filter((item) => item.status === 'failed').map((item) => item.id)
    if (ids.length === 0) {
      setError('没有失败的任务可重试')
      return
    }
    setBusy(true)
    setError('')
    try {
      await api.approveApplyTasks(ids)
      await refresh()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const execute = async (action: 'greet' | 'resume') => {
    setBusy(true)
    setError('')
    try {
      const result = await api.executeApply(executeLimit, dryRun, action)
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

  //: 薪资区间的上下限。它才是**真正拦截**的那道门（「期望月薪下限」只是评分用的），
  //: 所以必须能在界面上看见并改掉——之前它藏在库里，用户改了设置却还是被 20000-80000 拦。
  const saveSalaryBound = async (rule: ApplyRule, key: 'min_salary' | 'max_salary', raw: string) => {
    const value = Number(raw)
    if (!Number.isFinite(value) || value < 0) {
      return
    }
    try {
      await api.updateApplyRule(rule.id, {
        ...rule,
        params: { ...(rule.params ?? {}), [key]: value },
      })
      await refresh()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const pendingQueue = queue.filter((item) => item.status === 'pending')
  const approvedQueue = queue.filter((item) => item.status === 'approved')
  const greetedQueue = queue.filter((item) => item.status === 'greeted')
  const doneQueue = queue.filter((item) => ['sent', 'failed', 'skipped'].includes(item.status))
  const enabled = status?.enabled ?? false
  //: 自动拉黑能力是否真的生效（勾了**且**过了闸门，由后端算好）。
  //: 用它控制按钮可用性，而不是只看"勾没勾"——否则用户会对一个点了没反应的按钮发懵。
  const autofillOn =
    settings?.capabilities?.items.find((item) => item.key === 'autofill_blacklist')?.on ?? false
  //: M17 两个投递动作：打招呼只发消息；投递简历发简历附件并记为一次投递。
  //: 投递简历可处理"已确认"与"已打招呼"两类——允许直接投，也允许分两阶段走。
  const resumeReadyCount = approvedQueue.length + greetedQueue.length

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
              className="mt-3 btn-primary rounded-control px-3 py-1.5 text-xs"
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
            <label className="text-xs text-ink-6">
              城市（名字或代码）
              <input
                className="mt-1 w-full rounded-control border border-ink-2 px-2 py-1.5 text-sm"
                placeholder="如 许昌 或 101180400"
                value={settings.city}
                onChange={(event) => void saveSettings({ city: event.target.value })}
              />
            </label>
            <label className="text-xs text-ink-6">
              抓取时的浏览器
              <select
                className="mt-1 w-full rounded-control border border-ink-2 px-2 py-1.5 text-sm"
                value={settings.window_mode}
                onChange={(event) => void saveSettings({ window_mode: event.target.value })}
              >
                <option value="visible">显示窗口（默认，最稳）</option>
                <option value="offscreen">静默：窗口移到屏幕外（推荐）</option>
                <option value="headless">无头（最隐蔽但最容易被风控识别）</option>
              </select>
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

      {/* 能力开关：分级风险闸门。默认全关，所以不展开也不影响原有流程 */}
      {enabled && settings?.capabilities && (
        <section className="rounded-card border border-ink-1 bg-surface p-4">
          <h2 className="text-sm font-semibold">投递能力（可选，默认全关）</h2>
          <p className="mt-0.5 text-xs text-ink-6">
            这里的能力一个都不缺，但默认都不做。开启前请先读每一档的说明——越往下风险越高。
          </p>
          <div className="mt-3">
            <ApplyCapabilitiesPanel
              capabilities={settings.capabilities}
              busy={busy}
              onSave={async (next) => {
                await saveSettings({
                  capabilities: {
                    enabled: next.enabled,
                    signed_phrases: next.signed_phrases,
                    captcha_mode: next.captcha_mode,
                    run_local: true,
                  },
                })
              }}
            />
          </div>
        </section>
      )}

      {/* 投递账号：多账号轮换 */}
      {enabled && (
        <section className="rounded-card border border-ink-1 bg-surface p-4">
          <h2 className="text-sm font-semibold">投递账号（可选，不配置就按单账号投递）</h2>
          <div className="mt-3">
            <ApplyProfilesPanel busy={busy} setBusy={setBusy} onError={setError} />
          </div>
        </section>
      )}

      {/* 通知：企业微信 / 通用 Webhook */}
      {enabled && (
        <section className="rounded-card border border-ink-1 bg-surface p-4">
          <h2 className="text-sm font-semibold">通知（可选）</h2>
          <div className="mt-3">
            <ApplyNotifyPanel busy={busy} setBusy={setBusy} onError={setError} />
          </div>
        </section>
      )}

      {/* 定时投递 */}
      {enabled && (
        <section className="rounded-card border border-ink-1 bg-surface p-4">
          <h2 className="text-sm font-semibold">定时投递（可选，默认关闭）</h2>
          <div className="mt-3">
            <ApplySchedulePanel busy={busy} setBusy={setBusy} onError={setError} />
          </div>
        </section>
      )}

      {/* 自动拉黑：先预览、再确认 */}
      {enabled && settings?.capabilities && (
        <section className="rounded-card border border-ink-1 bg-surface p-4">
          <h2 className="text-sm font-semibold">按拒绝信号自动拉黑（可选）</h2>
          <p className="mt-0.5 text-xs text-ink-6">
            扫描已有投递记录，认出「不合适」「岗位已关闭」这类<strong className="text-ink-7">明确拒绝</strong>，
            把公司加进黑名单，以后不再重复投。先预览，确认后才写。
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled={busy || !autofillOn}
              onClick={() => void runAutofill(true)}
              className="rounded-control border border-ink-2 px-3 py-1.5 text-xs hover:bg-paper disabled:opacity-40"
            >
              预览会被拉黑的公司
            </button>
            {autofillPreview && autofillPreview.candidates.length > 0 && (
              <button
                type="button"
                disabled={busy}
                onClick={() => void runAutofill(false)}
                className="btn-primary rounded-control px-3 py-1.5 text-xs"
              >
                确认拉黑这 {autofillPreview.candidates.length} 家
              </button>
            )}
            {!autofillOn && (
              <span className="text-xs text-ink-6">
                先在下面的能力开关里打开「识别到拒绝就自动拉黑」
              </span>
            )}
          </div>

          {autofillPreview && (
            <div className="mt-3 rounded-control border border-ink-1 p-3 text-xs">
              <p className="text-ink-6">
                扫描了 {autofillPreview.scanned} 条投递记录
                {autofillPreview.added > 0 && `，已拉黑 ${autofillPreview.added} 家`}
              </p>
              {autofillPreview.candidates.length === 0 ? (
                <p className="mt-1 text-ink-6">没有识别到明确拒绝的公司。</p>
              ) : (
                <ul className="mt-2 space-y-1">
                  {autofillPreview.candidates.map((item) => (
                    <li key={`${item.company}-${item.evidence}`}>
                      <span className="text-ink-7">{item.company}</span>
                      <span className="ml-1 text-ink-6">— 依据：{item.evidence}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </section>
      )}

      {/* 抓候选 */}

      {/* 左列要放下五列表格（#/公司·岗位/薪资/状态/招呼语），320-380px 会把每格挤成竖排 */}
      <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[minmax(440px,560px)_minmax(0,1fr)]">
        <div className="min-h-0 space-y-4 overflow-y-auto pr-1">
      <section className="rounded-card border border-ink-1 bg-surface p-4">
        <h2 className="text-sm font-semibold">1. 抓取候选</h2>
        <p className="mt-0.5 text-xs text-ink-6">
          抓取只会把岗位放进待确认队列，<strong className="font-medium text-ink-7">不会投递</strong>。不通过的岗位会连原因一起列出来。
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
            className="btn-primary rounded-control px-3 py-1.5 text-sm"
          >
            抓取候选
          </button>
          <label className="flex items-center gap-1.5 text-xs text-ink-6">
            <input
              type="checkbox"
              checked={fetchDetail}
              onChange={(event) => setFetchDetail(event.target.checked)}
            />
            抓详情（能拿到真薪资 / 学历 / HR 状态；每条岗位多开一次页面，更慢、更容易触发风控，建议把抓取数量调到 5–10）
          </label>
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
                    <th className="py-1.5 pr-2">公司 / 岗位</th>
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
                          aria-label={`选择 ${item.company} 的 ${item.title} 加入确认队列`}
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
                      <td className="min-w-[13rem] py-2 pr-2">
                        <div className="text-ink-9">{item.company}</div>
                        {item.jd_url ? (
                          <a className="text-info hover:underline" href={item.jd_url} target="_blank" rel="noreferrer">
                            {item.title}
                          </a>
                        ) : (
                          <div className="text-ink-6">{item.title}</div>
                        )}
                      </td>
                      <td className="whitespace-nowrap py-2 pr-2">{item.salary_range || '—'}</td>
                      <td className="whitespace-nowrap py-2 pr-2">{item.city || '—'}</td>
                      <td className="py-2 pr-2 font-mono">{item.matched_score}</td>
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
                className="btn-primary rounded-control px-3 py-1.5 text-xs"
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
          <div className="flex items-center gap-2 text-xs text-ink-6">
            <span>
              {snapshot
                ? `共 ${snapshot.total} · 待确认 ${snapshot.pending} · 已确认 ${snapshot.approved} · 已打招呼 ${snapshot.greeted} · 已投 ${snapshot.sent} · 失败 ${snapshot.failed}`
                : '—'}
            </span>
            {(snapshot?.failed ?? 0) > 0 && (
              <button
                type="button"
                disabled={busy}
                onClick={() => void retryFailed()}
                className="rounded-control border border-ink-2 px-2 py-0.5 hover:bg-paper disabled:cursor-not-allowed"
              >
                重试失败的 {snapshot?.failed} 条
              </button>
            )}
          </div>
        </div>

        {queue.length === 0 ? (
          <p className="mt-3 text-xs text-ink-6">队列是空的。先抓一批候选。</p>
        ) : (
          <div className="mt-3 space-y-4">
            {[
              { title: `已确认待投（${approvedQueue.length}）`, items: approvedQueue },
              { title: `已打招呼 · 待投简历（${greetedQueue.length}）`, items: greetedQueue },
              { title: `待确认（${pendingQueue.length}）`, items: pendingQueue },
              { title: `已处理（${doneQueue.length}）`, items: doneQueue.slice(0, 20) },
            ].map((group) => (
              <div key={group.title}>
                <div className="flex flex-wrap items-center gap-2 text-xs font-medium text-ink-6">
                  <span>{group.title}</span>
                  {group.items.length > 0 && (
                    <label className="flex items-center gap-1 font-normal text-ink-4">
                      <input
                        type="checkbox"
                        checked={group.items.every((item) => queuePicked.includes(item.id))}
                        onChange={(event) => {
                          const ids = group.items.map((item) => item.id)
                          setQueuePicked((current) =>
                            event.target.checked
                              ? Array.from(new Set([...current, ...ids]))
                              : current.filter((value) => !ids.includes(value)),
                          )
                        }}
                        aria-label={`全选「${group.title}」`}
                      />
                      全选
                    </label>
                  )}
                </div>
                {group.items.length === 0 ? (
                  <div className="mt-1 text-xs text-ink-4">—</div>
                ) : (
                  /* 清单式而不是五列表格：列一窄中文就会逐字断行
                     （「服务端开发工程师」竖成一列字）。这里每行只放一条，
                     主信息一行、招呼语一行，宽度再紧也不会断得难看。 */
                  <ul className="mt-1 space-y-1.5">
                    {group.items.map((item) => (
                      <li key={item.id} className="border-t border-ink-1 pt-1.5 text-xs">
                        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                          <input
                            type="checkbox"
                            className="mt-0.5"
                            checked={queuePicked.includes(item.id)}
                            onChange={(event) => toggleQueuePick(item.id, event.target.checked)}
                            aria-label={`选择 ${item.company} 的 ${item.title}`}
                          />
                          <span className="text-ink-4">#{item.id}</span>
                          <span className="font-medium text-ink-9">{item.company}</span>
                          <span className="text-ink-6">{item.title}</span>
                          <span className="font-mono text-ink-6">{item.salary_range || '—'}</span>
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
                        </div>
                        {item.error && <div className="mt-0.5 text-ng">{item.error}</div>}
                        <div className="mt-0.5 break-all text-ink-4">{item.say_hi_text.slice(0, 60)}…</div>
                        {/* 简历附件：只收图片。PDF 要先转成图片（见下方说明） */}
                        <div className="mt-1 flex flex-wrap items-center gap-2 text-ink-6">
                          <span>
                            附件：
                            {item.resume_image_path ? (
                              <span className="text-ok">已附加</span>
                            ) : (
                              <span className="text-ink-4">未附加（投递时发平台在线简历）</span>
                            )}
                          </span>
                          <label className="cursor-pointer rounded-control border border-ink-2 px-2 py-0.5 hover:bg-paper">
                            {uploadingFor === item.id ? '上传中…' : '选择图片'}
                            <input
                              type="file"
                              accept="image/png,image/jpeg,image/webp,image/gif"
                              className="hidden"
                              disabled={busy}
                              onChange={(event) => {
                                const file = event.target.files?.[0]
                                event.target.value = ''
                                if (file) void uploadAttachment(item.id, file)
                              }}
                            />
                          </label>
                          {item.resume_image_path && (
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() => void detachAttachment(item.id)}
                              className="rounded-control border border-ink-2 px-2 py-0.5 hover:bg-paper disabled:opacity-40"
                            >
                              移除附件
                            </button>
                          )}
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}

            {queuePicked.length > 0 && (
              <div className="flex flex-wrap items-center gap-2 rounded-control border border-ink-1 bg-paper px-3 py-2 text-xs">
                <span className="text-ink-7">已勾选 {queuePicked.length} 条</span>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void deleteQueuePicked()}
                  className="rounded-control border border-ng px-2.5 py-1 text-ng hover:bg-ng-soft disabled:cursor-not-allowed"
                >
                  从队列删除
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void blacklistQueuePicked()}
                  className="rounded-control border border-ink-2 px-2.5 py-1 text-ink-6 hover:bg-sunken disabled:cursor-not-allowed"
                >
                  拉黑这些公司（以后自动拦下）
                </button>
                <button
                  type="button"
                  onClick={() => setQueuePicked([])}
                  className="rounded-control px-2 py-1 text-ink-4 hover:bg-sunken"
                >
                  取消勾选
                </button>
              </div>
            )}

            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                disabled={busy || approvedQueue.length === 0 || !enabled || status?.broken || !status?.adapter_ready}
                onClick={() => void execute('greet')}
                className="btn-primary rounded-control px-3 py-1.5 text-xs"
              >
                打招呼（最多 {executeLimit} 条）
              </button>
              <button
                type="button"
                disabled={busy || resumeReadyCount === 0 || !enabled || status?.broken || !status?.adapter_ready}
                onClick={() => void execute('resume')}
                className="btn-primary rounded-control px-3 py-1.5 text-xs"
              >
                投递简历（最多 {executeLimit} 条）
              </button>
              <label className="flex items-center gap-1.5 text-xs text-ink-6">
                条数
                <input
                  type="number"
                  min={1}
                  max={30}
                  className="w-16 rounded-control border border-ink-2 px-1.5 py-0.5 text-xs"
                  value={executeLimit}
                  onChange={(event) =>
                    setExecuteLimit(Math.max(1, Math.min(30, Number(event.target.value) || 1)))
                  }
                />
              </label>
              <label className="flex items-center gap-1.5 text-xs text-ink-6">
                <input type="checkbox" checked={dryRun} onChange={(event) => setDryRun(event.target.checked)} />
                演练模式（只报告会投什么，不真的发送）
              </label>
            </div>
            <div className="mt-2 text-xs text-ink-4">
              打招呼只发送招呼语；投递简历会发送简历并记为一次投递。可以先用「打招呼」激活对话、看 HR 反应，再决定要不要「投递简历」。
            </div>

            {/* 适配器没就绪时按钮是灰的——必须说清为什么，否则用户只会觉得"点了没反应" */}
            {!status?.adapter_ready && (
              <div className="rounded-control border border-warn bg-warn-soft px-3 py-2 text-xs text-warn">
                「打招呼 / 投递简历」暂时不可用：{status?.adapter_note || '适配器未就绪'}
              </div>
            )}

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


      {/* 规则 */}
      <section className="rounded-card border border-ink-1 bg-surface p-4">
        <h2 className="text-sm font-semibold">过滤规则</h2>
        <p className="mt-0.5 text-xs text-ink-6">
          优先级数字小的先判断，任何一条命中就拦下这个岗位。理由会显示在候选列表里。
          <span className="text-warn">
            {' '}
            注意：「期望月薪下限」只管打分，真正按薪资拦截的是下表「薪资区间」的上下限。
          </span>
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
                  <input
                    type="checkbox"
                    checked={rule.enabled}
                    onChange={() => void toggleRule(rule)}
                    aria-label={`启用过滤规则：${rule.name || rule.kind}`}
                  />
                </td>
                <td className="py-1.5 pr-2 text-ink-6">{rule.priority}</td>
                <td className="py-1.5 pr-2">{rule.name || '—'}</td>
                <td className="py-1.5 pr-2 text-ink-6">{RULE_KIND_LABELS[rule.kind] ?? rule.kind}</td>
                <td className="py-1.5 pr-2 text-ink-6">
                  {rule.kind === 'salary' ? (
                    <span className="flex flex-wrap items-center gap-1.5">
                      <input
                        type="number"
                        min={0}
                        step={1000}
                        defaultValue={String(rule.params?.min_salary ?? 0)}
                        onBlur={(event) => void saveSalaryBound(rule, 'min_salary', event.target.value)}
                        className="w-24 rounded-control border border-ink-2 px-1.5 py-0.5 text-xs"
                        aria-label="薪资下限（元/月）"
                      />
                      <span>–</span>
                      <input
                        type="number"
                        min={0}
                        step={1000}
                        defaultValue={String(rule.params?.max_salary ?? 0)}
                        onBlur={(event) => void saveSalaryBound(rule, 'max_salary', event.target.value)}
                        className="w-24 rounded-control border border-ink-2 px-1.5 py-0.5 text-xs"
                        aria-label="薪资上限（元/月，0＝不限）"
                      />
                      <span className="text-ink-4">元/月（0＝不限；这才是真正拦截的那道门）</span>
                    </span>
                  ) : (
                    ruleParams(rule)
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
        </div>
    </div>
  )
}
