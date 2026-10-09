/**
 * 投递能力开关面板（分级风险闸门）。
 *
 * 三个刻意的设计：
 *
 * 1. **按档位分组，而不是平铺一堆开关**。用户需要先看懂"这一档会让程序做什么"，
 *    再决定要不要开——所以每一档先给后果说明，再列具体能力。
 * 2. **高风险能力要求手动输入确认短语**。勾选框会被顺手点掉，而"打一遍这句话"
 *    逼用户读一次后果。短语由后端校验，前端只是收集。
 * 3. **显示"为什么没生效"**。勾了但闸门没过时，把后端给的原因原样显示（缺哪句短语、
 *    缺哪个依赖、要不要本地运行），而不是让用户对着一个没反应的开关猜。
 */

import { useState } from 'react'

import type { ApplyCapability, ApplyCapabilities } from '../api/types'

interface Props {
  capabilities: ApplyCapabilities
  busy: boolean
  /** 保存能力选择。只传要改的字段，其余由后端保留。 */
  onSave: (next: {
    enabled: Record<string, boolean>
    signed_phrases?: string[]
    captcha_mode?: string
  }) => Promise<void>
}

function groupByTier(items: ApplyCapability[]): { tier: number; items: ApplyCapability[] }[] {
  const tiers = Array.from(new Set(items.map((item) => item.tier))).sort((a, b) => a - b)
  return tiers.map((tier) => ({ tier, items: items.filter((item) => item.tier === tier) }))
}

export default function ApplyCapabilitiesPanel({ capabilities, busy, onSave }: Props) {
  // 用户正在输入的确认短语：`能力 key -> 已输入内容`
  const [phrases, setPhrases] = useState<Record<string, string>>({})

  const enabledMap = (): Record<string, boolean> =>
    Object.fromEntries(capabilities.items.map((item) => [item.key, item.wanted]))

  const toggle = async (item: ApplyCapability, next: boolean) => {
    const enabled = { ...enabledMap(), [item.key]: next }
    // 关掉某个能力时，把它的依赖者也一起关掉——与后端 enable() 的级联保持一致，
    // 否则界面上会短暂出现"定时开着、自动没开"的矛盾状态
    if (!next) {
      for (const other of capabilities.items) {
        if (other.requires.includes(item.key)) {
          enabled[other.key] = false
        }
      }
    }

    const typed = (phrases[item.key] ?? '').trim()
    // 勾选一个需要短语的能力时，把用户**已经打出来的**那句话一起提交；
    // 没打就不提交，让后端回一个"还差哪句"，由界面提示。
    const signed = next && typed ? [typed] : undefined
    await onSave({ enabled, signed_phrases: signed })
  }

  const groups = groupByTier(capabilities.items)

  return (
    <div className="space-y-4">
      <div className="rounded border border-ink-1 bg-surface-2 p-3 text-xs text-ink-2">
        <p className="font-medium text-ink-1">所有能力默认关闭</p>
        <p className="mt-1">
          不开启就等于这些能力不存在。越往下风险越高：档位只能逐级解锁，高风险的每一档都要求你手动输入一句确认。
          验证码**不会**被自动破解——只提供「熔断停投」与「暂停等你自己处理」两种策略。
        </p>
      </div>

      {capabilities.any_auto_enabled && (
        <div className="rounded border border-warn-1 bg-warn-soft p-3 text-xs">
          <span className="font-medium">自动投递已生效。</span>
          <span className="ml-1">程序会自己决定投哪些岗位并直接发出，不再逐条等你确认。</span>
        </div>
      )}

      {/* 验证码策略：只有"停下"和"等人"，没有自动绕过 */}
      <div className="rounded border border-ink-1 p-3">
        <label className="text-sm font-medium" htmlFor="captcha-mode">
          遇到验证码 / 滑块时
        </label>
        <select
          id="captcha-mode"
          className="mt-2 w-full rounded border border-ink-1 bg-surface px-2 py-1 text-sm"
          value={capabilities.captcha_mode}
          disabled={busy}
          onChange={(event) =>
            void onSave({ enabled: enabledMap(), captcha_mode: event.target.value })
          }
        >
          {capabilities.captcha_modes.map((mode) => (
            <option key={mode.key} value={mode.key}>
              {mode.label}
            </option>
          ))}
        </select>
        <p className="mt-1 text-xs text-ink-2">
          {capabilities.captcha_modes.find((m) => m.key === capabilities.captcha_mode)?.detail}
        </p>
      </div>

      {groups.map(({ tier, items }) => (
        <div key={tier} className="rounded border border-ink-1 p-3">
          <div className="flex items-baseline justify-between">
            <h4 className="text-sm font-medium">
              {capabilities.tier_labels[String(tier)] ?? `档位 ${tier}`}
            </h4>
            <span className="text-xs text-ink-2">档位 {tier}</span>
          </div>
          <p className="mt-1 text-xs text-ink-2">{capabilities.tier_summaries[String(tier)]}</p>

          <ul className="mt-3 space-y-3">
            {items.map((item) => {
              const typed = (phrases[item.key] ?? '').trim()
              // 勾了但没生效 → 把后端的原因显示出来（缺短语 / 缺依赖 / 需本地运行）
              const needsPhrase = Boolean(item.missing_phrase)
              const canSubmitPhrase = needsPhrase && typed === item.missing_phrase

              return (
                <li key={item.key} className="border-t border-ink-1 pt-3 first:border-t-0 first:pt-0">
                  <label className="flex cursor-pointer items-start gap-2">
                    <input
                      type="checkbox"
                      className="mt-0.5"
                      checked={item.wanted}
                      disabled={busy}
                      onChange={(event) => void toggle(item, event.target.checked)}
                    />
                    <span className="flex-1">
                      <span className="text-sm">{item.label}</span>
                      <span className="mt-0.5 block text-xs text-ink-2">{item.summary}</span>
                    </span>
                    <span
                      className={
                        item.on ? 'shrink-0 text-xs text-ok-1' : 'shrink-0 text-xs text-ink-2'
                      }
                    >
                      {item.on ? '已生效' : item.wanted ? '未生效' : ''}
                    </span>
                  </label>

                  {item.wanted && !item.on && item.blocked_reason && (
                    <p className="mt-2 pl-6 text-xs text-warn-1">{item.blocked_reason}</p>
                  )}

                  {item.wanted && needsPhrase && (
                    <div className="mt-2 pl-6">
                      <label className="text-xs text-ink-2" htmlFor={`phrase-${item.key}`}>
                        请原样输入：<code className="text-ink-1">{item.missing_phrase}</code>
                      </label>
                      <div className="mt-1 flex gap-2">
                        <input
                          id={`phrase-${item.key}`}
                          className="flex-1 rounded border border-ink-1 bg-surface px-2 py-1 text-sm"
                          placeholder={item.missing_phrase}
                          value={phrases[item.key] ?? ''}
                          disabled={busy}
                          onChange={(event) =>
                            setPhrases((prev) => ({ ...prev, [item.key]: event.target.value }))
                          }
                        />
                        <button
                          type="button"
                          className="rounded border border-ink-1 px-3 py-1 text-sm disabled:opacity-40"
                          disabled={busy || !canSubmitPhrase}
                          onClick={() =>
                            void onSave({
                              enabled: enabledMap(),
                              signed_phrases: [item.missing_phrase],
                            })
                          }
                        >
                          确认开启
                        </button>
                      </div>
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        </div>
      ))}
    </div>
  )
}
