/** 投递分析页（M6）：漏斗、渠道转化、周趋势、简历版本 A/B、待跟进、清单与导入。 */

import { useCallback, useEffect, useState } from 'react'
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '../api/client'
import type { ApplicationItem, ApplicationStats, ImportBatch, ImportOutcome } from '../api/types'

const CHANNEL_LABELS: Record<string, string> = {
  boss: 'Boss直聘',
  liepin: '猎聘',
  zhilian: '智联招聘',
  '51job': '前程无忧',
  lagou: '拉勾',
  referral: '内推',
  other: '其他',
}

const STATUS_LABELS: Record<string, string> = {
  applied: '已投递',
  viewed: '已查看',
  screening: '筛选中',
  interview1: '一面',
  interview2: '二面',
  hr: 'HR 面',
  offer: 'Offer',
  rejected: '被拒',
  ghosted: '无响应',
  withdrawn: '主动放弃',
}

const STATUS_ORDER = Object.keys(STATUS_LABELS)

const SAMPLE_CSV = `公司,岗位,渠道,状态,投递时间,简历版本,城市
某某科技,后端工程师,Boss直聘,已投递,2026-03-01,主简历,杭州
某某网络,Python开发,猎聘,已查看,2026-03-02,量化版,上海
某某信息,服务端工程师,智联招聘,一面,2026-03-03,主简历,北京
某某数据,后端工程师,Boss直聘,Offer,2026-03-05,量化版,深圳
某某安全,Go开发,前程无忧,不合适,2026-03-06,主简历,成都`

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function percent(value: number): string {
  return `${Math.round(value * 100)}%`
}

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString('zh-CN')
}

function channelLabel(channel: string): string {
  return CHANNEL_LABELS[channel] ?? channel
}

export default function Applications() {
  const [stats, setStats] = useState<ApplicationStats | null>(null)
  const [items, setItems] = useState<ApplicationItem[]>([])
  const [batches, setBatches] = useState<ImportBatch[]>([])
  const [draft, setDraft] = useState('')
  const [outcome, setOutcome] = useState<ImportOutcome | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const [nextStats, nextItems, nextBatches] = await Promise.all([
        api.applicationStats(8),
        api.listApplications(),
        api.listImportBatches(),
      ])
      setStats(nextStats)
      setItems(nextItems)
      setBatches(nextBatches)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const runImport = async () => {
    if (!draft.trim()) {
      setError('先把 CSV 内容粘进来（第一行是表头）')
      return
    }
    setBusy(true)
    setError('')
    try {
      const result = await api.importApplications(draft)
      setOutcome(result)
      setDraft('')
      await refresh()
    } catch (err) {
      setError(errorMessage(err))
      setOutcome(null)
    } finally {
      setBusy(false)
    }
  }

  const changeStatus = async (id: number, status: string) => {
    try {
      await api.updateApplicationStatus(id, status)
      await refresh()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const removeApplication = async (id: number) => {
    try {
      await api.deleteApplication(id)
      await refresh()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const maxFunnel = stats?.funnel[0]?.count || 1

  return (
    <div className="space-y-4">
      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {[
          { label: '总投递', value: `${stats?.total ?? 0}`, hint: '导入 + 手动 + 扩展' },
          { label: '回复率', value: percent(stats?.response_rate ?? 0), hint: `有回应 ${stats?.responded ?? 0}` },
          { label: '面试转化', value: percent(stats?.interview_rate ?? 0), hint: `进入面试 ${stats?.interviewed ?? 0}` },
          { label: 'Offer', value: `${stats?.offers ?? 0}`, hint: `转化 ${percent(stats?.offer_rate ?? 0)}` },
        ].map((card) => (
          <div key={card.label} className="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
            <p className="text-xs text-slate-500">{card.label}</p>
            <p className="mt-1 text-2xl font-semibold text-slate-900">{card.value}</p>
            <p className="text-[11px] text-slate-400">{card.hint}</p>
          </div>
        ))}
      </section>

      {error && <p className="rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</p>}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="space-y-4">
          {/* 漏斗 */}
          <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <h3 className="mb-3 text-sm font-semibold text-slate-800">投递漏斗</h3>
            {!stats || stats.total === 0 ? (
              <p className="text-xs text-slate-500">还没有投递记录。用右下角的导入框粘一份 CSV 试试。</p>
            ) : (
              <div className="space-y-3">
                {stats.funnel.map((step) => (
                  <div key={step.stage}>
                    <div className="mb-1 flex items-center justify-between text-xs">
                      <span className="text-slate-700">
                        {step.label} <span className="text-slate-400">{step.count}</span>
                      </span>
                      <span className="text-slate-500">
                        对上一阶段 {percent(step.rate_from_previous)} · 对总投递 {percent(step.rate_from_first)}
                      </span>
                    </div>
                    <div className="h-3 overflow-hidden rounded-full bg-slate-100">
                      <div
                        className="h-full rounded-full bg-sky-500"
                        style={{ width: `${Math.max(2, (step.count / maxFunnel) * 100)}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* 渠道对比 */}
          <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <h3 className="mb-3 text-sm font-semibold text-slate-800">渠道对比</h3>
            {!stats || stats.by_channel.length === 0 ? (
              <p className="text-xs text-slate-500">还没有数据。</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="bg-slate-50 text-left text-xs text-slate-500">
                    <tr>
                      <th className="px-3 py-2 font-medium">渠道</th>
                      <th className="px-3 py-2 font-medium">投递</th>
                      <th className="px-3 py-2 font-medium">有回应</th>
                      <th className="px-3 py-2 font-medium">回复率</th>
                      <th className="px-3 py-2 font-medium">面试</th>
                      <th className="px-3 py-2 font-medium">Offer</th>
                    </tr>
                  </thead>
                  <tbody>
                    {stats.by_channel.map((row) => (
                      <tr key={row.channel} className="border-t border-slate-100">
                        <td className="px-3 py-2 text-slate-800">{channelLabel(row.channel)}</td>
                        <td className="px-3 py-2 text-slate-600">{row.total}</td>
                        <td className="px-3 py-2 text-slate-600">{row.responded}</td>
                        <td className="px-3 py-2 text-slate-600">{percent(row.response_rate)}</td>
                        <td className="px-3 py-2 text-slate-600">
                          {row.interviewed}（{percent(row.interview_rate)}）
                        </td>
                        <td className="px-3 py-2 text-slate-600">{row.offers}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {/* 周趋势 */}
          <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <h3 className="mb-3 text-sm font-semibold text-slate-800">最近 8 周趋势</h3>
            {!stats || stats.weekly.every((point) => point.applied === 0) ? (
              <p className="text-xs text-slate-500">这段时间还没有投递。</p>
            ) : (
              <div className="h-56">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={stats.weekly}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="week" tick={{ fontSize: 10 }} />
                    <YAxis allowDecimals={false} tick={{ fontSize: 10 }} width={28} />
                    <Tooltip />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                    <Line type="monotone" dataKey="applied" name="投递" stroke="#0284c7" dot />
                    <Line type="monotone" dataKey="responded" name="有回应" stroke="#059669" dot />
                    <Line type="monotone" dataKey="interviewed" name="进入面试" stroke="#b45309" dot />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
          </section>

          {/* 清单 */}
          <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <h3 className="mb-3 text-sm font-semibold text-slate-800">投递清单（{items.length}）</h3>
            {items.length === 0 ? (
              <p className="text-xs text-slate-500">还没有记录。</p>
            ) : (
              <div className="max-h-[420px] overflow-y-auto">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-slate-50 text-left text-xs text-slate-500">
                    <tr>
                      <th className="px-3 py-2 font-medium">公司 / 岗位</th>
                      <th className="px-3 py-2 font-medium">渠道</th>
                      <th className="px-3 py-2 font-medium">投递</th>
                      <th className="px-3 py-2 font-medium">状态</th>
                      <th className="px-3 py-2 font-medium">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((item) => (
                      <tr key={item.id} className="border-t border-slate-100">
                        <td className="px-3 py-2">
                          <p className="text-slate-800">{item.company}</p>
                          <p className="text-[11px] text-slate-500">{item.title}</p>
                        </td>
                        <td className="px-3 py-2 text-slate-600">{channelLabel(item.channel)}</td>
                        <td className="px-3 py-2 text-slate-500">{formatDate(item.applied_at)}</td>
                        <td className="px-3 py-2">
                          <select
                            value={item.status}
                            onChange={(event) => void changeStatus(item.id, event.target.value)}
                            className="rounded border border-slate-300 px-2 py-1 text-xs"
                          >
                            {STATUS_ORDER.map((status) => (
                              <option key={status} value={status}>
                                {STATUS_LABELS[status]}
                              </option>
                            ))}
                          </select>
                        </td>
                        <td className="px-3 py-2">
                          <button
                            type="button"
                            onClick={() => void removeApplication(item.id)}
                            className="rounded border border-slate-300 px-2 py-0.5 text-[11px] text-rose-600 hover:bg-slate-50"
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
        </div>

        <aside className="space-y-4">
          {/* 导入 */}
          <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <h3 className="mb-2 text-sm font-semibold text-slate-800">导入投递记录</h3>
            <textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              rows={8}
              placeholder="粘贴 CSV / TSV（第一行是表头，例如：公司,岗位,渠道,状态,投递时间）"
              className="w-full resize-y rounded-lg border border-slate-300 px-3 py-2 font-mono text-[11px] outline-none focus:border-sky-500"
            />
            <div className="mt-2 flex gap-2">
              <button
                type="button"
                onClick={() => void runImport()}
                disabled={busy}
                className="rounded-md bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-800 disabled:opacity-40"
              >
                {busy ? '导入中…' : '导入'}
              </button>
              <button
                type="button"
                onClick={() => setDraft(SAMPLE_CSV)}
                className="rounded-md border border-slate-300 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50"
              >
                填入示例
              </button>
            </div>
            <p className="mt-2 text-[11px] leading-relaxed text-slate-500">
              表头支持中英文常见写法；日期支持 <code>2026-03-12</code>、<code>2026/3/12 14:30</code>、
              <code>2026年3月12日</code> 与 Excel 日期序列号。重复投递（同公司+岗位+同一天）会自动跳过。
            </p>

            {outcome && (
              <div className="mt-3 space-y-1 rounded-lg bg-slate-50 px-3 py-2 text-[11px] text-slate-700">
                <p className="font-medium text-slate-800">
                  新增 {outcome.inserted} · 更新 {outcome.updated} · 跳过 {outcome.skipped}
                </p>
                <p>
                  认出的列：
                  {Object.entries(outcome.matched_columns)
                    .map(([field, header]) => `${field}←${header}`)
                    .join('，')}
                </p>
                {outcome.unmatched_headers.length > 0 && (
                  <p className="text-amber-700">没认出来的列：{outcome.unmatched_headers.join('、')}</p>
                )}
                {outcome.skipped_details.length > 0 && (
                  <ul className="list-disc pl-4 text-amber-700">
                    {outcome.skipped_details.slice(0, 5).map(([line, reason]) => (
                      <li key={`${line}-${reason}`}>
                        第 {line} 行：{reason}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </section>

          {/* 简历版本 A/B */}
          <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <h3 className="mb-2 text-sm font-semibold text-slate-800">简历版本 A/B</h3>
            {!stats || stats.resume_versions.length === 0 ? (
              <p className="text-xs text-slate-500">CSV 里加一列「简历版本」就能对比效果。</p>
            ) : (
              <ul className="space-y-2">
                {stats.resume_versions.map((row) => (
                  <li key={row.version}>
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-slate-700">{row.version}</span>
                      <span className="text-slate-500">
                        {percent(row.response_rate)}（{row.responded}/{row.total}）
                      </span>
                    </div>
                    <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-100">
                      <div className="h-full bg-emerald-500" style={{ width: `${row.response_rate * 100}%` }} />
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* 响应时长 */}
          <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <h3 className="mb-2 text-sm font-semibold text-slate-800">响应时长</h3>
            {!stats || stats.response_time.samples === 0 ? (
              <p className="text-xs text-slate-500">还没有"投递 → 有回应"的样本。</p>
            ) : (
              <dl className="grid gap-1 text-xs text-slate-700">
                <div className="flex justify-between">
                  <dt className="text-slate-500">样本</dt>
                  <dd>{stats.response_time.samples}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-slate-500">中位数</dt>
                  <dd>{stats.response_time.median_hours} 小时</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-slate-500">最快 / 最慢</dt>
                  <dd>
                    {stats.response_time.fastest_hours} / {stats.response_time.slowest_hours} 小时
                  </dd>
                </div>
              </dl>
            )}
          </section>

          {/* 待跟进 */}
          <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <h3 className="mb-2 text-sm font-semibold text-slate-800">
              待跟进（{stats?.follow_ups.length ?? 0}）
            </h3>
            {!stats || stats.follow_ups.length === 0 ? (
              <p className="text-xs text-slate-500">没有超过 7 天还没动静的投递。</p>
            ) : (
              <ul className="space-y-2">
                {stats.follow_ups.map((item) => (
                  <li key={item.application_id} className="rounded-lg bg-amber-50 px-3 py-2 text-xs">
                    <p className="text-slate-800">
                      {item.company} · {item.title}
                    </p>
                    <p className="text-[11px] text-amber-700">
                      投出 {item.days_since} 天仍是「{STATUS_LABELS[item.status] ?? item.status}」，可以主动催一下
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* 导入流水 */}
          <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <h3 className="mb-2 text-sm font-semibold text-slate-800">导入流水</h3>
            {batches.length === 0 ? (
              <p className="text-xs text-slate-500">还没有导入过。</p>
            ) : (
              <ul className="space-y-1 text-[11px] text-slate-600">
                {batches.slice(0, 6).map((batch) => (
                  <li key={batch.id}>
                    #{batch.id} {batch.source} · 新增 {batch.inserted} / 更新 {batch.updated} / 跳过{' '}
                    {batch.skipped} · {new Date(batch.at).toLocaleString('zh-CN', { hour12: false })}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </aside>
      </div>
    </div>
  )
}
