/** 人脉与学习计划（M14）：该跟进谁、以及该学什么。
 *
 * 两件事放在一页，因为它们互为因果：人脉给你机会，学习让你接得住。
 */

import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import type {
  ContactDetail,
  ContactItem,
  FollowUpItem,
  LearningItemData,
  LearningSummaryData,
  MasteryLink,
  NetworkOptions,
} from '../api/types'

type Panel = 'people' | 'learning'

const URGENCY_STYLE: Record<string, string> = {
  overdue: 'border-rose-200 bg-rose-50',
  today: 'border-amber-200 bg-amber-50',
  soon: 'border-sky-200 bg-sky-50',
  later: 'border-slate-200 bg-white',
}

const URGENCY_LABEL: Record<string, string> = {
  overdue: '已逾期',
  today: '今天该联系',
  soon: '马上到期',
  later: '计划内',
}

const HEALTH_LABEL: Record<string, string> = {
  warm: '联系正常',
  due: '该联系了',
  stale: '很久没联系',
  cold: '还没打过招呼',
}

const STATUS_LABEL: Record<string, string> = {
  todo: '待开始',
  doing: '进行中',
  done: '已完成',
  dropped: '已放弃',
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleDateString('zh-CN') : '—'
}

export default function NetworkDesk() {
  const [panel, setPanel] = useState<Panel>('people')
  const [options, setOptions] = useState<NetworkOptions | null>(null)
  const [error, setError] = useState('')

  const [contacts, setContacts] = useState<ContactItem[]>([])
  const [followUps, setFollowUps] = useState<FollowUpItem[]>([])
  const [detail, setDetail] = useState<ContactDetail | null>(null)
  const [contactDraft, setContactDraft] = useState({ name: '', relation: 'referral', company: '' })
  const [logDraft, setLogDraft] = useState({ summary: '', promised: '', kind: 'message' })

  const [learning, setLearning] = useState<LearningItemData[]>([])
  const [summary, setSummary] = useState<LearningSummaryData | null>(null)
  const [insight, setInsight] = useState<MasteryLink[]>([])

  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const [nextOptions, nextContacts, nextFollowUps, nextLearning, nextSummary, nextInsight] = await Promise.all([
        api.networkOptions(),
        api.listContacts(),
        api.followUps(14, false),
        api.listLearning(),
        api.learningSummary(),
        api.learningInsight(),
      ])
      setOptions(nextOptions)
      setContacts(nextContacts)
      setFollowUps(nextFollowUps)
      setLearning(nextLearning)
      setSummary(nextSummary)
      setInsight(nextInsight)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [])

  useEffect(() => {
    void refresh()
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

  const addContact = () =>
    run(async () => {
      if (!contactDraft.name.trim()) {
        setError('先填名字——不然跟进的时候想不起是谁')
        return
      }
      await api.createContact(contactDraft)
      setContactDraft({ name: '', relation: 'referral', company: '' })
      await refresh()
    })

  const openContact = (id: number) =>
    run(async () => {
      setDetail(await api.getContact(id))
    })

  const logInteraction = () =>
    run(async () => {
      if (!detail || !logDraft.summary.trim()) {
        return
      }
      await api.addInteraction(detail.contact.id, logDraft)
      setLogDraft({ summary: '', promised: '', kind: 'message' })
      await refresh()
      setDetail(await api.getContact(detail.contact.id))
    })

  const removeContact = (id: number) =>
    run(async () => {
      await api.deleteContact(id)
      if (detail?.contact.id === id) {
        setDetail(null)
      }
      await refresh()
    })

  const generateLearning = (replace: boolean) =>
    run(async () => {
      await api.generateLearning({ limit: 8, replace })
      await refresh()
    })

  const setLearningStatus = (item: LearningItemData, status: string) =>
    run(async () => {
      await api.updateLearning(item.id, { ...item, status })
      await refresh()
    })

  const removeLearning = (id: number) =>
    run(async () => {
      await api.deleteLearning(id)
      await refresh()
    })

  return (
    <div className="space-y-4">
      {error && (
        <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</div>
      )}

      <div className="flex flex-wrap gap-1">
        {(
          [
            { key: 'people', label: `人脉跟进${followUps.length ? `（${followUps.length}）` : ''}` },
            { key: 'learning', label: '学习计划' },
          ] as { key: Panel; label: string }[]
        ).map((item) => (
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

      {/* ------------------------------ 人脉 ------------------------------ */}
      {panel === 'people' && (
        <>
          <section className="rounded-lg border border-slate-200 bg-white p-4">
            <h2 className="text-sm font-semibold">该跟进的人</h2>
            <p className="mt-0.5 text-xs text-slate-500">
              按逾期天数排序，逾期的一定在最上面。每条都给一句**可以直接发出去**的开场——
              「记得跟进」这种提示知道了也没用。
            </p>

            {followUps.length === 0 && (
              <p className="mt-3 text-xs text-emerald-700">最近两周没有需要跟进的人。</p>
            )}

            <div className="mt-3 space-y-2">
              {followUps.map((item) => (
                <div
                  key={item.contact_id}
                  className={`rounded-md border p-3 text-xs ${URGENCY_STYLE[item.urgency] ?? ''}`}
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="font-medium text-slate-800">
                      {item.name} · {item.relation_label}
                      {item.company ? ` · ${item.company}` : ''}
                    </div>
                    <span className="text-slate-600">
                      {URGENCY_LABEL[item.urgency] ?? item.urgency}
                      {item.days_overdue > 0 ? ` ${item.days_overdue} 天` : ''}
                      {item.due_at ? ` · 计划 ${formatDate(item.due_at)}` : ''}
                    </span>
                  </div>
                  <div className="mt-1 text-slate-600">{item.reason}</div>
                  <div className="mt-2 rounded bg-white/70 px-2 py-1.5 text-slate-800">{item.suggested}</div>
                  <div className="mt-1 flex gap-2">
                    <button
                      type="button"
                      onClick={() => void openContact(item.contact_id)}
                      className="rounded border border-slate-300 px-2 py-0.5 text-[11px] text-slate-600 hover:bg-white"
                    >
                      看时间线 / 记一次沟通
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section className="rounded-lg border border-slate-200 bg-white p-4">
            <h2 className="text-sm font-semibold">通讯录（{contacts.length}）</h2>

            <div className="mt-3 flex flex-wrap items-end gap-2 text-xs">
              <input
                className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                placeholder="名字"
                value={contactDraft.name}
                onChange={(event) => setContactDraft({ ...contactDraft, name: event.target.value })}
              />
              <select
                className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                value={contactDraft.relation}
                onChange={(event) => setContactDraft({ ...contactDraft, relation: event.target.value })}
              >
                {(options?.relations ?? []).map((item) => (
                  <option key={item.key} value={item.key}>
                    {item.label}（默认 {item.default_follow_up_days} 天跟进）
                  </option>
                ))}
              </select>
              <input
                className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                placeholder="公司"
                value={contactDraft.company}
                onChange={(event) => setContactDraft({ ...contactDraft, company: event.target.value })}
              />
              <button
                type="button"
                disabled={busy}
                onClick={() => void addContact()}
                className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white disabled:bg-slate-300"
              >
                加进通讯录
              </button>
            </div>

            <div className="mt-3 flex flex-wrap gap-2">
              {contacts.map((contact) => (
                <button
                  key={contact.id}
                  type="button"
                  onClick={() => void openContact(contact.id)}
                  className="rounded border border-slate-300 px-2 py-1 text-[11px] text-slate-600 hover:bg-slate-50"
                >
                  {contact.name}
                  {contact.company ? ` · ${contact.company}` : ''}
                </button>
              ))}
              {contacts.length === 0 && <span className="text-xs text-slate-500">还没有联系人。</span>}
            </div>
          </section>

          {detail && (
            <section className="rounded-lg border border-slate-200 bg-white p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 className="text-sm font-semibold">
                  {detail.contact.name} · {detail.contact.company || '未填公司'}
                </h2>
                <button
                  type="button"
                  onClick={() => void removeContact(detail.contact.id)}
                  className="rounded border border-slate-300 px-2 py-0.5 text-[11px] text-slate-600 hover:bg-slate-50"
                >
                  删除联系人
                </button>
              </div>

              <div className="mt-2 flex flex-wrap gap-3 text-[11px] text-slate-600">
                <span>关系：{options?.relations.find((item) => item.key === detail.contact.relation)?.label}</span>
                <span>状态：{HEALTH_LABEL[detail.health.level ?? ''] ?? detail.health.level}</span>
                <span>
                  下次跟进：{formatDate(detail.next_follow_up_at)}
                </span>
                <span>上次联系：{formatDate(detail.contact.last_contact_at)}</span>
              </div>

              {detail.greeting && (
                <div className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                  {detail.greeting}
                </div>
              )}

              <div className="mt-3 space-y-1">
                {detail.timeline.length === 0 && (
                  <p className="text-xs text-slate-500">还没有沟通记录。聊过一次就记一条——时间线是你自己的记忆。</p>
                )}
                {detail.timeline.map((row, index) => (
                  <div key={`${row.at}-${index}`} className="rounded-md border border-slate-100 px-3 py-1.5 text-xs">
                    <div className="flex flex-wrap items-center justify-between gap-2 text-slate-500">
                      <span>
                        {formatDate(row.at)} · {row.direction_label} · {row.kind_label}
                      </span>
                      {row.next_follow_up_at && <span>约定下次：{formatDate(row.next_follow_up_at)}</span>}
                    </div>
                    <div className="mt-0.5 text-slate-800">{row.summary}</div>
                    {row.promised && <div className="mt-0.5 text-amber-800">他答应过：{row.promised}</div>}
                  </div>
                ))}
              </div>

              <div className="mt-3 space-y-2 rounded-md border border-slate-200 bg-slate-50 p-3 text-xs">
                <div className="font-medium text-slate-700">记一次沟通</div>
                <textarea
                  className="w-full rounded border border-slate-300 px-2 py-1 text-sm"
                  rows={2}
                  placeholder="聊了什么"
                  value={logDraft.summary}
                  onChange={(event) => setLogDraft({ ...logDraft, summary: event.target.value })}
                />
                <div className="flex flex-wrap items-center gap-2">
                  <select
                    className="rounded border border-slate-300 px-2 py-1"
                    value={logDraft.kind}
                    onChange={(event) => setLogDraft({ ...logDraft, kind: event.target.value })}
                  >
                    {(options?.interaction_kinds ?? []).map((item) => (
                      <option key={item.key} value={item.key}>
                        {item.label}
                      </option>
                    ))}
                  </select>
                  <input
                    className="flex-1 rounded border border-slate-300 px-2 py-1"
                    placeholder="他答应过什么（比如：帮你问一下 HC）"
                    value={logDraft.promised}
                    onChange={(event) => setLogDraft({ ...logDraft, promised: event.target.value })}
                  />
                  <button
                    type="button"
                    disabled={busy || !logDraft.summary.trim()}
                    onClick={() => void logInteraction()}
                    className="rounded bg-slate-900 px-3 py-1 text-white disabled:bg-slate-300"
                  >
                    记下来
                  </button>
                </div>
              </div>
            </section>
          )}
        </>
      )}

      {/* ------------------------------ 学习 ------------------------------ */}
      {panel === 'learning' && (
        <>
          <section className="rounded-lg border border-slate-200 bg-white p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="text-sm font-semibold">学习计划</h2>
                <p className="mt-0.5 text-xs text-slate-500">
                  优先级是算出来的：**薄弱程度**（掌握度 + 训练正确率）× **岗位需求**（这个技能在多少份
                  JD 里出现过）× **覆盖广度**（补它能顺带解决几个别的缺口）。
                </p>
              </div>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void generateLearning(false)}
                  className="rounded-md bg-slate-900 px-3 py-1.5 text-xs text-white disabled:bg-slate-300"
                >
                  按薄弱点与 JD 缺口生成
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void generateLearning(true)}
                  className="rounded-md border border-slate-300 px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-50"
                >
                  重算（保留进行中/已完成）
                </button>
              </div>
            </div>

            {summary && (
              <div className="mt-3">
                <div className="flex items-center justify-between text-xs text-slate-600">
                  <span>{summary.note}</span>
                  <span>
                    {summary.done}/{summary.total} 项 · 已投入 {summary.hours_done}h / 共 {summary.hours_total}h
                  </span>
                </div>
                <div className="mt-1 h-2 w-full overflow-hidden rounded-full bg-slate-100">
                  <div
                    className="h-full rounded-full bg-emerald-500"
                    style={{ width: `${Math.round(summary.progress * 100)}%` }}
                  />
                </div>
              </div>
            )}
          </section>

          <section className="space-y-2">
            {learning.map((item) => (
              <div
                key={item.id}
                className={
                  item.status === 'done'
                    ? 'rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-xs'
                    : 'rounded-lg border border-slate-200 bg-white p-3 text-xs'
                }
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="font-medium text-slate-800">
                    {item.title}
                    <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600">
                      优先级 {item.priority} · 约 {item.hours}h
                    </span>
                    <span className="ml-1 rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600">
                      {STATUS_LABEL[item.status] ?? item.status}
                    </span>
                  </div>
                  <div className="flex gap-1">
                    {(['todo', 'doing', 'done'] as const).map((status) => (
                      <button
                        key={status}
                        type="button"
                        onClick={() => void setLearningStatus(item, status)}
                        className={
                          item.status === status
                            ? 'rounded bg-slate-900 px-2 py-0.5 text-[11px] text-white'
                            : 'rounded border border-slate-300 px-2 py-0.5 text-[11px] text-slate-600 hover:bg-slate-50'
                        }
                      >
                        {STATUS_LABEL[status]}
                      </button>
                    ))}
                    <button
                      type="button"
                      onClick={() => void removeLearning(item.id)}
                      className="rounded border border-slate-300 px-2 py-0.5 text-[11px] text-slate-600 hover:bg-slate-50"
                    >
                      删除
                    </button>
                  </div>
                </div>
                <div className="mt-1 text-slate-600">{item.reason}</div>
                <div className="mt-1 rounded bg-slate-50 px-2 py-1.5 text-slate-800">{item.action}</div>
              </div>
            ))}
            {learning.length === 0 && (
              <div className="rounded-lg border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">
                还没有学习项。先练几场面试、投几份简历，再点上面的「生成」——
                那样清单才是从你的真实差距里长出来的。
              </div>
            )}
          </section>

          {insight.filter((row) => row.note).length > 0 && (
            <section className="rounded-lg border border-slate-200 bg-white p-4">
              <h2 className="text-sm font-semibold">「学完了」与「掌握了」的差距</h2>
              <p className="mt-0.5 text-xs text-slate-500">
                学完但掌握度没动，说明练法不对（只看文档、没动手做）。这张表就是让这个差距可见。
              </p>
              <ul className="mt-2 space-y-1 text-xs">
                {insight
                  .filter((row) => row.note)
                  .map((row) => (
                    <li key={row.topic} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-amber-900">
                      <span className="font-medium">{row.topic}</span>
                      {row.has_data ? `（掌握度 ${row.mastery}，练习 ${row.attempts} 次）` : '（还没有掌握度数据）'}
                      —— {row.note}
                    </li>
                  ))}
              </ul>
            </section>
          )}
        </>
      )}
    </div>
  )
}
