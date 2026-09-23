/** 面试配置面板（M11）：压力模式、面试官风格、连环面、语言。
 *
 * 抽成组件而不是在两个页面各写一份：文字面试与语音面试用的是同一套配置，
 * 各写一份的话每加一个选项就要改两处，早晚会漂移。
 */

import type { InterviewOptions, PanelStageSpec, PressureSettings } from '../api/types'

export interface SetupValue {
  pressure: PressureSettings
  /** 用哪套预设；panelEnabled 为 true 时忽略 */
  styleKey: string
  panelEnabled: boolean
  panel: PanelStageSpec[]
  locale: string
}

export const DEFAULT_SETUP: SetupValue = {
  pressure: {
    deep_drill: false,
    max_depth: 5,
    interrupt: false,
    interrupt_after_ms: 12_000,
    time_limit: false,
    answer_seconds: 90,
    speed: 1,
  },
  styleKey: 'technical',
  panelEnabled: false,
  panel: [],
  locale: 'zh',
}

/** 把面板状态转成创建会话的参数（两个页面共用，避免传参漂移）。 */
export function setupToPayload(value: SetupValue) {
  const anyPressure = value.pressure.deep_drill || value.pressure.interrupt || value.pressure.time_limit
  return {
    pressure: anyPressure ? { ...value.pressure } : null,
    style_key: value.panelEnabled ? null : value.styleKey,
    panel: value.panelEnabled && value.panel.length > 0 ? value.panel : null,
    locale: value.locale,
  }
}

/** 从「当前面试官」信息里读出来（各页面用来显示"现在是谁在问"） */
export function interviewerOf(meta: Record<string, unknown> | undefined) {
  const raw = meta?.interviewer
  if (!raw || typeof raw !== 'object') {
    return null
  }
  return raw as {
    index: number
    total: number
    style_key: string
    label: string
    voice: string
    voice_label: string
    is_handover: boolean
  }
}

export default function InterviewSetup({
  options,
  value,
  onChange,
  disabled = false,
}: {
  options: InterviewOptions | null
  value: SetupValue
  onChange: (next: SetupValue) => void
  disabled?: boolean
}) {
  const styles = options?.styles ?? []
  const pressureModes = options?.pressure_modes ?? []
  const locales = options?.locales ?? [{ key: 'zh', label: '中文', speech_note: '' }]

  const patchPressure = (patch: Partial<PressureSettings>) =>
    onChange({ ...value, pressure: { ...value.pressure, ...patch } })

  const updateStage = (index: number, patch: Partial<PanelStageSpec>) =>
    onChange({
      ...value,
      panel: value.panel.map((stage, position) => (position === index ? { ...stage, ...patch } : stage)),
    })

  const styleFor = (key: string) => styles.find((item) => item.key === key)

  return (
    <div className="space-y-3 rounded-md border border-slate-200 bg-slate-50 p-3">
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-xs text-slate-600">
          面试语言
          <select
            className="mt-1 rounded-md border border-slate-300 px-2 py-1.5 text-sm"
            value={value.locale}
            disabled={disabled}
            onChange={(event) => onChange({ ...value, locale: event.target.value })}
          >
            {locales.map((item) => (
              <option key={item.key} value={item.key}>
                {item.label}
              </option>
            ))}
          </select>
        </label>

        <label className="text-xs text-slate-600">
          面试官
          <select
            className="mt-1 w-56 rounded-md border border-slate-300 px-2 py-1.5 text-sm"
            value={value.panelEnabled ? '__panel__' : value.styleKey}
            disabled={disabled}
            onChange={(event) => {
              const next = event.target.value
              if (next === '__panel__') {
                onChange({
                  ...value,
                  panelEnabled: true,
                  panel: value.panel.length > 0 ? value.panel : (options?.default_panel ?? []),
                })
                return
              }
              onChange({ ...value, panelEnabled: false, styleKey: next })
            }}
          >
            {styles.map((item) => (
              <option key={item.key} value={item.key}>
                {item.label} · {item.voice_label}
              </option>
            ))}
            <option value="__panel__">多角色连环面</option>
          </select>
        </label>

        {!value.panelEnabled && styleFor(value.styleKey) && (
          <span className="max-w-md text-[11px] text-slate-500">
            {styleFor(value.styleKey)?.summary}（{styleFor(value.styleKey)?.tone}）
          </span>
        )}
      </div>

      {value.panelEnabled && (
        <div className="rounded-md border border-slate-200 bg-white p-2">
          <div className="text-xs font-medium text-slate-700">连环面编排（音色自动错开，避免听不出换人）</div>
          <div className="mt-2 space-y-2">
            {value.panel.map((stage, index) => (
              <div key={`${stage.style_key}-${index}`} className="flex flex-wrap items-center gap-2 text-xs">
                <span className="text-slate-400">第 {index + 1} 位</span>
                <select
                  className="rounded-md border border-slate-300 px-2 py-1"
                  value={stage.style_key}
                  disabled={disabled}
                  onChange={(event) => updateStage(index, { style_key: event.target.value })}
                >
                  {styles.map((item) => (
                    <option key={item.key} value={item.key}>
                      {item.label}
                    </option>
                  ))}
                </select>
                <label className="flex items-center gap-1">
                  问
                  <input
                    type="number"
                    min={1}
                    max={8}
                    className="w-14 rounded-md border border-slate-300 px-2 py-1"
                    value={stage.rounds}
                    disabled={disabled}
                    onChange={(event) => updateStage(index, { rounds: Number(event.target.value) })}
                  />
                  轮
                </label>
                <button
                  type="button"
                  className="rounded border border-slate-300 px-2 py-0.5 text-slate-600 hover:bg-slate-50"
                  disabled={disabled}
                  onClick={() => onChange({ ...value, panel: value.panel.filter((_, position) => position !== index) })}
                >
                  删除
                </button>
              </div>
            ))}
          </div>
          <button
            type="button"
            className="mt-2 rounded border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50"
            disabled={disabled}
            onClick={() => onChange({ ...value, panel: [...value.panel, { style_key: 'hr', rounds: 2 }] })}
          >
            加一位面试官
          </button>
        </div>
      )}

      <div>
        <div className="text-xs font-medium text-slate-700">压力模式（可同时打开）</div>
        <div className="mt-2 grid gap-2 md:grid-cols-3">
          {pressureModes.map((mode) => {
            const on =
              mode.key === 'deep_drill'
                ? value.pressure.deep_drill
                : mode.key === 'interrupt'
                  ? value.pressure.interrupt
                  : value.pressure.time_limit
            const toggle = () => {
              if (mode.key === 'deep_drill') {
                patchPressure({ deep_drill: !on })
              } else if (mode.key === 'interrupt') {
                patchPressure({ interrupt: !on })
              } else {
                patchPressure({ time_limit: !on })
              }
            }

            return (
              <div
                key={mode.key}
                className={on ? 'rounded-md border border-rose-200 bg-rose-50 p-2' : 'rounded-md border border-slate-200 bg-white p-2'}
              >
                <label className="flex items-center gap-2 text-xs font-medium text-slate-800">
                  <input type="checkbox" checked={on} disabled={disabled} onChange={toggle} />
                  {mode.label}
                </label>
                <div className="mt-1 text-[11px] leading-relaxed text-slate-500">{mode.summary}</div>

                {on && mode.key === 'deep_drill' && (
                  <label className="mt-1 flex items-center gap-1 text-[11px] text-slate-600">
                    最多追问
                    <input
                      type="number"
                      min={1}
                      max={8}
                      className="w-14 rounded border border-slate-300 px-1.5 py-0.5"
                      value={value.pressure.max_depth}
                      disabled={disabled}
                      onChange={(event) => patchPressure({ max_depth: Number(event.target.value) })}
                    />
                    层
                  </label>
                )}

                {on && mode.key === 'interrupt' && (
                  <label className="mt-1 flex items-center gap-1 text-[11px] text-slate-600">
                    说满
                    <input
                      type="number"
                      min={3}
                      max={60}
                      className="w-16 rounded border border-slate-300 px-1.5 py-0.5"
                      value={Math.round(value.pressure.interrupt_after_ms / 1000)}
                      disabled={disabled}
                      onChange={(event) => patchPressure({ interrupt_after_ms: Number(event.target.value) * 1000 })}
                    />
                    秒就插话
                  </label>
                )}

                {on && mode.key === 'time_limit' && (
                  <label className="mt-1 flex items-center gap-1 text-[11px] text-slate-600">
                    每题限时
                    <input
                      type="number"
                      min={20}
                      max={600}
                      className="w-16 rounded border border-slate-300 px-1.5 py-0.5"
                      value={value.pressure.answer_seconds}
                      disabled={disabled}
                      onChange={(event) => patchPressure({ answer_seconds: Number(event.target.value) })}
                    />
                    秒
                  </label>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
