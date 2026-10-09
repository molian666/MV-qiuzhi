/** Offer 与决策（M12）：对比矩阵与翻转点、薪资谈判、避坑信号、合同提醒。
 *
 * 这一页刻意分成四块，因为它们是四个不同时刻要做的事：
 * 拿到 offer 时看矩阵、谈薪时用谈判、投之前扫避坑、签之前看合同。
 */

import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import type {
  ContractReport,
  DecisionOptions,
  Negotiation,
  OfferItem,
  OfferMatrix,
  RiskReport,
} from '../api/types'

type Panel = 'matrix' | 'negotiation' | 'risk' | 'contract'

const PANELS: { key: Panel; label: string }[] = [
  { key: 'matrix', label: '对比矩阵' },
  { key: 'negotiation', label: '薪资谈判' },
  { key: 'risk', label: '避坑检查' },
  { key: 'contract', label: '合同提醒' },
]

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function wan(value: number | null | undefined): string {
  if (!value) {
    return '—'
  }
  return `${Math.round((value / 10000) * 10) / 10} 万`
}

export default function OfferDesk() {
  const [panel, setPanel] = useState<Panel>('matrix')
  const [options, setOptions] = useState<DecisionOptions | null>(null)
  const [error, setError] = useState('')

  // 矩阵
  const [offers, setOffers] = useState<OfferItem[]>([])
  const [matrix, setMatrix] = useState<OfferMatrix | null>(null)
  const [weights, setWeights] = useState<Record<string, number>>({})
  const [draft, setDraft] = useState({
    company: '',
    title: '',
    monthly_salary: 0,
    months_per_year: 14,
    annual_bonus: 0,
    signing_bonus: 0,
  })

  // 谈判
  const [negotiations, setNegotiations] = useState<Negotiation[]>([])
  const [current, setCurrent] = useState<Negotiation | null>(null)
  const [negForm, setNegForm] = useState({ company: '', title: '', bottom_line: 0, target: 0 })
  const [negDraft, setNegDraft] = useState('')

  // 风险与合同
  const [riskText, setRiskText] = useState('')
  const [riskReport, setRiskReport] = useState<RiskReport | null>(null)
  const [contractText, setContractText] = useState('')
  const [contractReport, setContractReport] = useState<ContractReport | null>(null)

  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const [nextOptions, nextOffers, nextNegotiations] = await Promise.all([
        api.decisionOptions(),
        api.listOffers(),
        api.listNegotiations(),
      ])
      setOptions(nextOptions)
      setOffers(nextOffers)
      setNegotiations(nextNegotiations)
      setWeights((current) =>
        Object.keys(current).length > 0
          ? current
          : Object.fromEntries(nextOptions.dimensions.map((item) => [item.key, item.default])),
      )
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  // 权重一变就重算：翻转点正是这么被发现的
  useEffect(() => {
    if (offers.length === 0 || Object.keys(weights).length === 0) {
      setMatrix(null)
      return
    }
    let cancelled = false
    api
      .offerMatrix(weights)
      .then((data) => {
        if (!cancelled) {
          setMatrix(data)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setMatrix(null)
        }
      })
    return () => {
      cancelled = true
    }
  }, [offers, weights])

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

  /**
   * 调某一项权重，其余按比例缩放使总和仍为 1。
   *
   * **必须这么算**：后端的敏感性分析（翻转点）就是在这个语义下扫描的。
   * 如果这里只改一项、把总和留给后端去归一，同一组数字会得到不同的排名，
   * 用户按提示拖过去也看不到翻转——那是"算出来好看但没法用"。
   */
  const setWeight = (key: string, value: number) => {
    const others = Object.entries(weights).filter(([name]) => name !== key)
    const total = others.reduce((sum, [, amount]) => sum + amount, 0)
    const rest = Math.max(0, 1 - value)
    const next: Record<string, number> = { [key]: value }
    for (const [name, amount] of others) {
      next[name] = total > 0 ? (amount / total) * rest : rest / Math.max(1, others.length)
    }
    setWeights(next)
  }

  const addOffer = () =>
    run(async () => {
      if (!draft.company.trim()) {
        setError('先填公司名')
        return
      }
      await api.createOffer({ ...draft, factors: {} })
      setDraft({ company: '', title: '', monthly_salary: 0, months_per_year: 14, annual_bonus: 0, signing_bonus: 0 })
      await refresh()
    })

  const removeOffer = (id: number) =>
    run(async () => {
      await api.deleteOffer(id)
      await refresh()
    })

  const patchFactor = (id: number, key: string, value: number) =>
    run(async () => {
      const offer = offers.find((item) => item.id === id)
      if (!offer) {
        return
      }
      await api.updateOffer(id, { ...offer, factors: { ...offer.factors, [key]: value } })
      await refresh()
    })

  const startNegotiation = () =>
    run(async () => {
      const created = await api.startNegotiation(negForm)
      setCurrent(created)
      await refresh()
    })

  const sendReply = () =>
    run(async () => {
      if (!current || !negDraft.trim()) {
        return
      }
      const next = await api.replyNegotiation(current.id, negDraft.trim())
      setNegDraft('')
      setCurrent(next)
    })

  const openNegotiation = (item: Negotiation) =>
    run(async () => {
      setCurrent(await api.getNegotiation(item.id))
    })

  const scanRisk = () =>
    run(async () => {
      setRiskReport(await api.scanRisk(riskText))
    })

  const scanContract = () =>
    run(async () => {
      setContractReport(await api.scanContract(contractText))
    })

  const brief = current?.brief ?? null

  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      {error && (
        <div className="shrink-0 rounded-control border border-ng bg-ng-soft px-3 py-2 text-sm text-ng">
          {error}
        </div>
      )}

      <div className="flex shrink-0 flex-wrap gap-1">
        {PANELS.map((item) => (
          <button
            key={item.key}
            type="button"
            onClick={() => setPanel(item.key)}
            className={
              item.key === panel
                ? 'btn-primary rounded-control px-3 py-1.5 text-sm'
                : 'rounded-control px-3 py-1.5 text-sm text-ink-6 transition-colors hover:bg-paper'
            }
          >
            {item.label}
          </button>
        ))}
      </div>

      {/* 页签内容：切页签时高度不变，内容在内部滚动 */}
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">

      {/* ---------------------------- 对比矩阵 ---------------------------- */}
      {panel === 'matrix' && (
        <>
          <section className="rounded-card border border-ink-1 bg-surface p-4">
            <h2 className="text-sm font-semibold">登记 offer</h2>
            <p className="mt-0.5 text-xs text-ink-6">
              薪资拆开填：月薪 × 月数 + 年终 + 签字费。签字费只算首年——
              把它当成每年都有是 offer 对比里最常见的自欺。
            </p>

            <div className="mt-3 grid gap-2 sm:grid-cols-3 lg:grid-cols-6">
              <input
                className="rounded-control border border-ink-2 px-2 py-1.5 text-sm"
                placeholder="公司"
                value={draft.company}
                onChange={(event) => setDraft({ ...draft, company: event.target.value })}
              />
              <input
                className="rounded-control border border-ink-2 px-2 py-1.5 text-sm"
                placeholder="岗位"
                value={draft.title}
                onChange={(event) => setDraft({ ...draft, title: event.target.value })}
              />
              <label className="text-[11px] text-ink-6">
                月薪
                <input
                  type="number"
                  className="mt-0.5 w-full rounded-control border border-ink-2 px-2 py-1 text-sm"
                  value={draft.monthly_salary}
                  onChange={(event) => setDraft({ ...draft, monthly_salary: Number(event.target.value) })}
                />
              </label>
              <label className="text-[11px] text-ink-6">
                发薪月数
                <input
                  type="number"
                  className="mt-0.5 w-full rounded-control border border-ink-2 px-2 py-1 text-sm"
                  value={draft.months_per_year}
                  onChange={(event) => setDraft({ ...draft, months_per_year: Number(event.target.value) })}
                />
              </label>
              <label className="text-[11px] text-ink-6">
                年终奖
                <input
                  type="number"
                  className="mt-0.5 w-full rounded-control border border-ink-2 px-2 py-1 text-sm"
                  value={draft.annual_bonus}
                  onChange={(event) => setDraft({ ...draft, annual_bonus: Number(event.target.value) })}
                />
              </label>
              <label className="text-[11px] text-ink-6">
                签字费
                <input
                  type="number"
                  className="mt-0.5 w-full rounded-control border border-ink-2 px-2 py-1 text-sm"
                  value={draft.signing_bonus}
                  onChange={(event) => setDraft({ ...draft, signing_bonus: Number(event.target.value) })}
                />
              </label>
            </div>
            <button
              type="button"
              disabled={busy}
              onClick={() => void addOffer()}
              className="mt-2 btn-primary rounded-control px-3 py-1.5 text-xs"
            >
              添加
            </button>

            {offers.length > 0 && (
              <table className="mt-3 w-full text-left text-xs">
                <thead className="text-ink-6">
                  <tr>
                    <th className="py-1 pr-2">公司</th>
                    <th className="py-1 pr-2">年包（首年）</th>
                    <th className="py-1 pr-2" />
                  </tr>
                </thead>
                <tbody>
                  {offers.map((offer) => (
                    <tr key={offer.id} className="border-t border-ink-1">
                      <td className="py-1 pr-2">
                        {offer.company}
                        {offer.title ? ` · ${offer.title}` : ''}
                      </td>
                      <td className="py-1 pr-2">{wan(offer.total_comp)}</td>
                      <td className="py-1 pr-2 text-right">
                        <button
                          type="button"
                          onClick={() => void removeOffer(offer.id)}
                          className="rounded-control border border-ink-2 px-2 py-0.5 text-[11px] text-ink-6 hover:bg-paper"
                        >
                          删除
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          {offers.length > 0 && options && (
            <section className="rounded-card border border-ink-1 bg-surface p-4">
              <h2 className="text-sm font-semibold">维度权重（拖动看排名怎么变）</h2>
              <p className="mt-0.5 text-xs text-ink-6">
                薪酬这一项是按同批 offer 里的最高年包归一算出来的，不用自己打分；其余维度给你自己评（0–10）。
              </p>

              <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {options.dimensions.map((dimension) => (
                  <label key={dimension.key} className="text-[11px] text-ink-6">
                    {dimension.label} · {Math.round((weights[dimension.key] ?? 0) * 100)}%
                    <input
                      type="range"
                      min={0}
                      max={0.6}
                      step={0.01}
                      value={weights[dimension.key] ?? 0}
                      onChange={(event) => setWeight(dimension.key, Number(event.target.value))}
                      className="mt-1 w-full"
                    />
                  </label>
                ))}
              </div>
            </section>
          )}

          {offers.length > 0 && (
            <section className="rounded-card border border-ink-1 bg-surface p-4">
              <h2 className="text-sm font-semibold">给自己打分（0–10）</h2>
              <div className="mt-3 space-y-2">
                {offers.map((offer) => (
                  <div key={offer.id} className="rounded-control border border-ink-1 p-2">
                    <div className="text-xs font-medium text-ink-7">{offer.company}</div>
                    <div className="mt-1 grid gap-2 sm:grid-cols-3 lg:grid-cols-5">
                      {(options?.dimensions ?? [])
                        .filter((item) => item.key !== 'compensation')
                        .map((dimension) => (
                          <label key={dimension.key} className="text-[11px] text-ink-6">
                            {dimension.label}
                            <input
                              type="number"
                              min={0}
                              max={10}
                              step={0.5}
                              className="mt-0.5 w-full rounded-control border border-ink-2 px-1.5 py-0.5 text-sm"
                              value={offer.factors[dimension.key] ?? 0}
                              onChange={(event) =>
                                void patchFactor(offer.id, dimension.key, Number(event.target.value))
                              }
                            />
                          </label>
                        ))}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {matrix && (
            <section className="rounded-card border border-ink-1 bg-surface p-4">
              <h2 className="text-sm font-semibold">排名与翻转点</h2>
              <table className="mt-3 w-full text-left text-xs">
                <thead className="text-ink-6">
                  <tr>
                    <th className="py-1 pr-2">#</th>
                    <th className="py-1 pr-2">公司</th>
                    <th className="py-1 pr-2">加权总分</th>
                    <th className="py-1 pr-2">年包</th>
                  </tr>
                </thead>
                <tbody>
                  {matrix.rows.map((row) => (
                    <tr key={row.offer_id} className="border-t border-ink-1">
                      <td className="py-1 pr-2">{row.rank}</td>
                      <td className="py-1 pr-2">{row.company}</td>
                      <td className="py-1 pr-2 font-medium">{row.total}</td>
                      <td className="py-1 pr-2">
                        {wan(offers.find((item) => item.id === row.offer_id)?.total_comp ?? 0)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {matrix.flips.length > 0 && (
                <div className="mt-3 space-y-2">
                  <div className="text-xs font-medium text-warn">权重一变，排名就会翻转：</div>
                  {matrix.flips.map((flip) => (
                    <div
                      key={`${flip.factor}-${flip.winner}-${flip.loser}`}
                      className="rounded-control border border-warn bg-warn-soft px-3 py-2 text-xs text-warn"
                    >
                      {flip.message}
                      <button
                        type="button"
                        className="ml-2 underline"
                        onClick={() => setWeight(flip.factor, flip.weight + 0.03)}
                      >
                        试试
                      </button>
                    </div>
                  ))}
                </div>
              )}

              {matrix.notes.map((note) => (
                <div key={note} className="mt-2 text-[11px] text-ink-6">
                  {note}
                </div>
              ))}
              <div className="mt-2 text-[11px] text-ink-4">{matrix.disclaimer}</div>
            </section>
          )}
        </>
      )}

      {/* ---------------------------- 薪资谈判 ---------------------------- */}
      {panel === 'negotiation' && (
        <>
          <section className="rounded-card border border-ink-1 bg-surface p-4">
            <h2 className="text-sm font-semibold">开始一场谈判演练</h2>
            <p className="mt-0.5 text-xs text-ink-6">
              填你的底线与目标（年薪）。对方（HR 角色）有预算上限并且不会一次让到位，
              结束后会给一份简报：底线守住没有、让步是不是太快、哪几招没用上。
            </p>
            <div className="mt-3 grid gap-2 sm:grid-cols-4">
              <input
                className="rounded-control border border-ink-2 px-2 py-1.5 text-sm"
                placeholder="公司"
                value={negForm.company}
                onChange={(event) => setNegForm({ ...negForm, company: event.target.value })}
              />
              <input
                className="rounded-control border border-ink-2 px-2 py-1.5 text-sm"
                placeholder="岗位"
                value={negForm.title}
                onChange={(event) => setNegForm({ ...negForm, title: event.target.value })}
              />
              <label className="text-[11px] text-ink-6">
                底线（年薪）
                <input
                  type="number"
                  step={10000}
                  className="mt-0.5 w-full rounded-control border border-ink-2 px-2 py-1 text-sm"
                  value={negForm.bottom_line}
                  onChange={(event) => setNegForm({ ...negForm, bottom_line: Number(event.target.value) })}
                />
              </label>
              <label className="text-[11px] text-ink-6">
                目标（年薪）
                <input
                  type="number"
                  step={10000}
                  className="mt-0.5 w-full rounded-control border border-ink-2 px-2 py-1 text-sm"
                  value={negForm.target}
                  onChange={(event) => setNegForm({ ...negForm, target: Number(event.target.value) })}
                />
              </label>
            </div>
            <button
              type="button"
              disabled={busy}
              onClick={() => void startNegotiation()}
              className="mt-2 btn-primary rounded-control px-3 py-1.5 text-xs"
            >
              开始谈判
            </button>
          </section>

          {negotiations.length > 0 && (
            <section className="rounded-card border border-ink-1 bg-surface p-4">
              <div className="text-xs font-medium text-ink-7">历史谈判</div>
              <div className="mt-2 flex flex-wrap gap-2">
                {negotiations.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => void openNegotiation(item)}
                    className="rounded-control border border-ink-2 px-2 py-1 text-[11px] text-ink-6 hover:bg-paper"
                  >
                    #{item.id} {item.company || '未填公司'}
                  </button>
                ))}
              </div>
            </section>
          )}

          {current && (
            <section className="rounded-card border border-ink-1 bg-surface p-4">
              <h2 className="text-sm font-semibold">
                {current.company || '谈判'} · 你的底线 {wan(Number(current.plan.bottom_line ?? 0))}
              </h2>
              <div className="mt-3 max-h-80 space-y-2 overflow-y-auto">
                {current.turns.map((turn, index) => (
                  <div
                    key={`${turn.speaker}-${index}`}
                    className={
                      turn.speaker === 'candidate'
                        ? 'ml-8 rounded-card bg-info-soft px-3 py-2 text-sm text-info'
                        : 'mr-8 rounded-card bg-sunken px-3 py-2 text-sm text-ink-9'
                    }
                  >
                    <div className="text-[11px] text-ink-6">{turn.speaker === 'candidate' ? '你' : 'HR'}</div>
                    {turn.text}
                  </div>
                ))}
              </div>

              <div className="mt-3 flex gap-2">
                <input
                  className="flex-1 rounded-control border border-ink-2 px-2 py-1.5 text-sm"
                  placeholder="你想说什么（可以带数字）"
                  value={negDraft}
                  onChange={(event) => setNegDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                      void sendReply()
                    }
                  }}
                />
                <button
                  type="button"
                  disabled={busy || !negDraft.trim()}
                  onClick={() => void sendReply()}
                  className="btn-primary rounded-control px-3 py-1.5 text-xs"
                >
                  说
                </button>
              </div>

              {brief && (
                <div className="mt-4 rounded-control border border-ink-1 bg-paper p-3">
                  <div className="flex flex-wrap items-center gap-3">
                    <span className="text-2xl font-semibold">{brief.score}</span>
                    <span className="text-xs text-ink-6">/ 100</span>
                    <span
                      className={
                        brief.bottom_line_held === true
                          ? 'rounded-full bg-ok-soft px-2.5 py-1 text-xs text-ok'
                          : brief.bottom_line_held === false
                            ? 'rounded-full bg-ng-soft px-2.5 py-1 text-xs text-ng'
                            : 'rounded-full bg-sunken px-2.5 py-1 text-xs text-ink-6'
                      }
                    >
                      {brief.bottom_line_held === true
                        ? '底线守住了'
                        : brief.bottom_line_held === false
                          ? '低于你的底线'
                          : '还看不出底线'}
                    </span>
                  </div>
                  <div className="mt-2 text-xs text-ink-7">{brief.summary}</div>

                  <div className="mt-2 grid gap-2 text-[11px] text-ink-6 sm:grid-cols-3">
                    <div>你的首次报价：{wan(brief.first_ask)}</div>
                    <div>你的最后报价：{wan(brief.last_ask)}</div>
                    <div>对方最终给到：{wan(brief.final_offer)}</div>
                  </div>

                  {brief.issues.length > 0 && (
                    <ul className="mt-2 space-y-1 text-xs text-ng">
                      {brief.issues.map((issue) => (
                        <li key={issue}>— {issue}</li>
                      ))}
                    </ul>
                  )}

                  {brief.tactics_missed.length > 0 && (
                    <div className="mt-2 text-[11px] text-ink-6">
                      没用上的招：
                      {brief.tactics_missed
                        .map((key) => options?.tactics.find((item) => item.key === key)?.label ?? key)
                        .join('、')}
                    </div>
                  )}
                  <div className="mt-2 text-[11px] text-ink-4">{brief.caveat}</div>
                </div>
              )}
            </section>
          )}
        </>
      )}

      {/* ---------------------------- 避坑检查 ---------------------------- */}
      {panel === 'risk' && (
        <section className="rounded-card border border-ink-1 bg-surface p-4">
          <h2 className="text-sm font-semibold">避坑检查</h2>
          <p className="mt-0.5 text-xs text-ink-6">
            把 JD、聊天记录或 offer 文本粘进来。这里<strong className="font-medium text-ink-7">只给信号和核实渠道，不下结论</strong>——
            同一句话在不同上下文里含义可以完全相反。
          </p>
          <textarea
            className="mt-3 w-full rounded-control border border-ink-2 px-2 py-1.5 text-sm"
            rows={6}
            placeholder="例如：急招后端，入职需交培训费 2000 元，试用期不上社保"
            value={riskText}
            onChange={(event) => setRiskText(event.target.value)}
          />
          <button
            type="button"
            disabled={busy || !riskText.trim()}
            onClick={() => void scanRisk()}
            className="mt-2 btn-primary rounded-control px-3 py-1.5 text-xs"
          >
            扫描
          </button>

          {riskReport && (
            <div className="mt-4 space-y-2">
              {riskReport.signals.length === 0 && (
                <p className="text-xs text-ok">没有扫到值得停下来问的信号。</p>
              )}
              {riskReport.signals.map((signal) => (
                <div
                  key={signal.key}
                  className={
                    signal.severity === 'warn'
                      ? 'rounded-control border border-warn bg-warn-soft p-3 text-xs'
                      : 'rounded-control border border-ink-1 p-3 text-xs'
                  }
                >
                  <div className="font-medium text-ink-9">
                    [{signal.severity === 'warn' ? '注意' : '了解一下'}] {signal.label}
                  </div>
                  <div className="mt-1 text-ink-6">命中原文：{signal.matched.join('、')}</div>
                  <div className="mt-1 text-ink-6">{signal.why}</div>
                  <div className="mt-1 text-ink-9">下一步：{signal.action}</div>
                  <div className="mt-1 text-[11px] text-ink-6">
                    去核实：
                    {signal.sources.map((label) => {
                      const source = riskReport.sources.find((item) => item.label === label)
                      return (
                        <a
                          key={label}
                          href={source?.url ?? '#'}
                          target="_blank"
                          rel="noreferrer"
                          className="ml-1.5 text-info underline"
                        >
                          {label}
                        </a>
                      )
                    })}
                  </div>
                </div>
              ))}

              <div className="rounded-control border border-ink-1 bg-paper p-3 text-[11px] text-ink-6">
                <div className="font-medium">核实渠道</div>
                <ul className="mt-1 space-y-0.5">
                  {riskReport.sources.map((source) => (
                    <li key={source.label}>
                      <a href={source.url} target="_blank" rel="noreferrer" className="text-info underline">
                        {source.label}
                      </a>
                      —— {source.what}
                      <span className="ml-1 text-ink-4">（本工具未访问该来源）</span>
                    </li>
                  ))}
                </ul>
                <div className="mt-2 text-ink-4">{riskReport.disclaimer}</div>
              </div>
            </div>
          )}
        </section>
      )}

      {/* ---------------------------- 合同提醒 ---------------------------- */}
      {panel === 'contract' && (
        <section className="rounded-card border border-ink-1 bg-surface p-4">
          <h2 className="text-sm font-semibold">合同与竞业提醒</h2>
          <p className="mt-0.5 text-xs text-ink-6">
            还没拿到合同也可以先看清单——那是你最该先看的时候。粘入合同或 offer 文本后，
            提到过的条款会排到前面。
          </p>
          <textarea
            className="mt-3 w-full rounded-control border border-ink-2 px-2 py-1.5 text-sm"
            rows={6}
            placeholder="粘入合同 / offer 文本（可选）"
            value={contractText}
            onChange={(event) => setContractText(event.target.value)}
          />
          <button
            type="button"
            disabled={busy}
            onClick={() => void scanContract()}
            className="mt-2 btn-primary rounded-control px-3 py-1.5 text-xs"
          >
            生成清单
          </button>

          {contractReport && (
            <div className="mt-4 space-y-2">
              {contractReport.mentioned.length > 0 && (
                <div className="text-xs font-medium text-warn">文本里提到过的（优先问）</div>
              )}
              {(contractReport.mentioned.length > 0 ? contractReport.mentioned : contractReport.checklist).map(
                (hit) => (
                  <div
                    key={hit.clause.key}
                    className={
                      hit.clause.severity === 'must_ask'
                        ? 'rounded-control border border-ng bg-ng-soft p-3 text-xs'
                        : 'rounded-control border border-ink-1 p-3 text-xs'
                    }
                  >
                    <div className="font-medium text-ink-9">
                      [{hit.clause.severity === 'must_ask' ? '必须问' : '建议问'}] {hit.clause.label}
                    </div>
                    <div className="mt-1 text-ink-9">问 HR：{hit.clause.question}</div>
                    <div className="mt-1 text-ink-6">{hit.clause.why}</div>
                    <div className="mt-1 text-warn">常见坑：{hit.clause.trap}</div>
                  </div>
                ),
              )}
              <div className="text-[11px] text-ink-4">{contractReport.disclaimer}</div>
            </div>
          )}
        </section>
      )}
      </div>
    </div>
  )
}
