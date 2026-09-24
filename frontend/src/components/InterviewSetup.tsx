/** 面试配置面板（M11）：压力模式、面试官风格、连环面、语言。
 *
 * 抽成组件而不是在两个页面各写一份：文字面试与语音面试用的是同一套配置，
 * 各写一份的话每加一个选项就要改两处，早晚会漂移。
 */

import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { InterviewOptions, PanelStageSpec, PressureSettings, VisionMeta } from '../api/types'

/** 用哪套题：让模型自由出，还是从题库里抽。 */
export type QuestionMode = 'free' | 'builtin' | 'mixed'

export interface SetupValue {
  pressure: PressureSettings
  /** 用哪套预设；panelEnabled 为 true 时忽略 */
  styleKey: string
  panelEnabled: boolean
  panel: PanelStageSpec[]
  locale: string
  /** M13：题源。free = 模型自由出；builtin = 只用内置题库；mixed = 按比例混合 */
  questionMode: QuestionMode
  /** mixed 时的占比。两个数只是相对比例，不必凑成 100 */
  questionRatio: { builtin: number; uploaded: number }
  /** 从题库抽题时的筛选（free 模式下忽略） */
  questionRole?: string
  questionMaxDifficulty?: number
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
  questionMode: 'free',
  questionRatio: { builtin: 2, uploaded: 1 },
}

/** 把面板状态转成创建会话的参数（两个页面共用，避免传参漂移）。 */
export function setupToPayload(value: SetupValue) {
  const anyPressure = value.pressure.deep_drill || value.pressure.interrupt || value.pressure.time_limit
  // question_count 不传：服务端会按 max_questions 取，两者保持一致，
  // 否则「问 6 个问题」和「抽 6 道题」会对不上。
  const questionMix =
    value.questionMode === 'builtin'
      ? { builtin: 1 }
      : value.questionMode === 'mixed'
        ? { builtin: value.questionRatio.builtin, uploaded: value.questionRatio.uploaded }
        : null
  return {
    pressure: anyPressure ? { ...value.pressure } : null,
    style_key: value.panelEnabled ? null : value.styleKey,
    panel: value.panelEnabled && value.panel.length > 0 ? value.panel : null,
    locale: value.locale,
    question_mix: questionMix,
    question_role: value.questionMode === 'free' ? null : value.questionRole ?? null,
    question_max_difficulty: value.questionMode === 'free' ? null : value.questionMaxDifficulty ?? null,
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

  // 题源占比要显示「现有多少题」，否则用户设了比例也不知道够不够
  const [visionMeta, setVisionMeta] = useState<VisionMeta | null>(null)
  useEffect(() => {
    api
      .visionMeta()
      .then(setVisionMeta)
      .catch(() => setVisionMeta(null))
  }, [])

  const countOf = (key: string) =>
    visionMeta?.sources.find((item) => item.key === key)?.count ?? 0
  const builtinCount = countOf('builtin')
  const uploadedCount = countOf('uploaded')
  const ratioTotal = value.questionRatio.builtin + value.questionRatio.uploaded
  const builtinPercent =
    ratioTotal > 0 ? Math.round((value.questionRatio.builtin / ratioTotal) * 100) : 100

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

      {/* M13：题源。默认让面试官自由出题；也可以只从题库抽，或按比例混合 */}
      <div className="rounded-md border border-slate-200 bg-white p-2">
        <div className="flex flex-wrap items-center gap-3 text-xs">
          <span className="font-medium text-slate-700">题目来源</span>
          <select
            className="rounded-md border border-slate-300 px-2 py-1 text-xs"
            value={value.questionMode}
            disabled={disabled}
            onChange={(event) =>
              onChange({ ...value, questionMode: event.target.value as QuestionMode })
            }
          >
            <option value="free">让面试官自由出题</option>
            <option value="builtin">只用内置题库（{builtinCount} 题）</option>
            <option value="mixed">按比例混合</option>
          </select>

          {value.questionMode !== 'free' && (
            <>
              <label className="flex items-center gap-1 text-slate-500">
                岗位
                <select
                  className="rounded-md border border-slate-300 px-2 py-1 text-xs"
                  value={value.questionRole ?? ''}
                  disabled={disabled}
                  onChange={(event) =>
                    onChange({ ...value, questionRole: event.target.value || undefined })
                  }
                >
                  <option value="">不限</option>
                  {(visionMeta?.roles ?? [])
                    .filter((item) => item.key !== 'uploaded')
                    .map((item) => (
                      <option key={item.key} value={item.key}>
                        {item.label}（{item.count}）
                      </option>
                    ))}
                </select>
              </label>

              <label className="flex items-center gap-1 text-slate-500">
                难度上限
                <select
                  className="rounded-md border border-slate-300 px-2 py-1 text-xs"
                  value={value.questionMaxDifficulty ?? 0}
                  disabled={disabled}
                  onChange={(event) => {
                    const level = Number(event.target.value)
                    onChange({ ...value, questionMaxDifficulty: level || undefined })
                  }}
                >
                  <option value={0}>不限</option>
                  {[2, 3, 4, 5].map((level) => (
                    <option key={level} value={level}>
                      ≤{level}
                    </option>
                  ))}
                </select>
              </label>
            </>
          )}
        </div>

        {value.questionMode === 'mixed' && (
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-600">
            <span>占比</span>
            <label className="flex items-center gap-1">
              内置
              <input
                type="number"
                min={0}
                max={20}
                disabled={disabled}
                className="w-14 rounded border border-slate-300 px-1.5 py-0.5 text-xs"
                value={value.questionRatio.builtin}
                onChange={(event) =>
                  onChange({
                    ...value,
                    questionRatio: { ...value.questionRatio, builtin: Math.max(0, Number(event.target.value) || 0) },
                  })
                }
              />
            </label>
            <span className="text-slate-400">:</span>
            <label className="flex items-center gap-1">
              我上传的
              <input
                type="number"
                min={0}
                max={20}
                disabled={disabled}
                className="w-14 rounded border border-slate-300 px-1.5 py-0.5 text-xs"
                value={value.questionRatio.uploaded}
                onChange={(event) =>
                  onChange({
                    ...value,
                    questionRatio: { ...value.questionRatio, uploaded: Math.max(0, Number(event.target.value) || 0) },
                  })
                }
              />
            </label>
            <span className="text-slate-400">
              ≈ 内置 {builtinPercent}%、上传 {100 - builtinPercent}%
            </span>
            <span className="text-slate-400">
              （现有 内置 {builtinCount} 题、上传 {uploadedCount} 题）
            </span>
          </div>
        )}

        {value.questionMode === 'mixed' && uploadedCount === 0 && (
          <p className="mt-1.5 text-[11px] leading-relaxed text-amber-700">
            还没有上传过题库。到「CV 面试题」页可以上传自己的面经或题库文档（JSON / CSV / Markdown / TXT）。
            某个来源题不够时，会从另一个来源补足——比例是尽量满足，不是保证。
          </p>
        )}

        {value.questionMode !== 'free' && (
          <p className="mt-1.5 text-[11px] leading-relaxed text-slate-400">
            抽中的题会在开场时固定下来，中途不再更换；面试官会按顺序问，并在你的回答上追问。
          </p>
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
