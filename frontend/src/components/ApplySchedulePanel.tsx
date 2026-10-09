/**
 * 定时投递面板。
 *
 * 两个刻意的设计：
 *
 * 1. **模式分成"只入队"与"自动投递"，并把后果写明**。「只抓岗位入队」不会投出去任何东西；
 *    选了自动投递才会真的发出去。这两件事在界面上必须一眼可分。
 * 2. **认不出的时间点要显示出来**。用户填了 `9:30`（少了前导零）或 `25:00` 时，
 *    后端会丢掉它并返回 `parsed_times`。这里把"实际生效的是哪几个"显式列出来，
 *    否则用户会以为设好了、其实那个时间点永远不会触发。
 */

import { useCallback, useEffect, useState } from 'react'

import { api } from '../api/client'
import type { ApplySchedule } from '../api/types'

interface Props {
  busy: boolean
  setBusy: (value: boolean) => void
  onError: (message: string) => void
}

/** "HH:MM" → 是否合法。与后端 `schedule.parse_hhmm` 同一套判据。 */
function isValidTime(text: string): boolean {
  const match = /^(\d{1,2}):(\d{2})$/.exec(text.trim())
  if (!match) return false
  const hour = Number(match[1])
  const minute = Number(match[2])
  return hour >= 0 && hour <= 23 && minute >= 0 && minute <= 59
}

export default function ApplySchedulePanel({ busy, setBusy, onError }: Props) {
  const [schedule, setSchedule] = useState<ApplySchedule | null>(null)
  const [draft, setDraft] = useState('')
  const [keywords, setKeywords] = useState('')

  const load = useCallback(async () => {
    try {
      const next = await api.applySchedule()
      setSchedule(next)
      setKeywords(next.keywords.join(' '))
      return next
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err))
      return null
    }
  }, [onError])

  useEffect(() => {
    void load()
  }, [load])

  if (!schedule) {
    return <p className="text-xs text-ink-6">加载定时配置…</p>
  }

  const save = async (patch: Partial<ApplySchedule>) => {
    setBusy(true)
    onError('')
    try {
      const next = await api.updateApplySchedule({
        enabled: patch.enabled ?? schedule.enabled,
        times: patch.times ?? schedule.times,
        mode: patch.mode ?? schedule.mode,
        limit: patch.limit ?? schedule.limit,
        keywords: patch.keywords ?? schedule.keywords,
        city: patch.city ?? schedule.city,
        fetch_detail: patch.fetch_detail ?? schedule.fetch_detail,
        grace_minutes: patch.grace_minutes ?? schedule.grace_minutes,
      })
      setSchedule(next)
      setKeywords(next.keywords.join(' '))
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err))
      // 失败时把界面拉回后端的真实状态（例如模式被闸门拒绝时）
      await load()
    } finally {
      setBusy(false)
    }
  }

  const addTime = () => {
    const text = draft.trim()
    if (!isValidTime(text)) {
      onError(`时间格式要写成 HH:MM（例如 09:30），收到的是「${text}」`)
      return
    }
    if (schedule.times.includes(text)) {
      setDraft('')
      return
    }
    void save({ times: [...schedule.times, text].sort() })
    setDraft('')
  }

  const removeTime = (value: string) => {
    void save({ times: schedule.times.filter((item) => item !== value) })
  }

  const droppedTimes = schedule.times.filter((item) => !schedule.parsed_times.includes(item))

  return (
    <div className="space-y-3">
      <p className="text-xs text-ink-6">
        到点自动抓岗位。<strong className="text-ink-7">程序没在运行时不会执行</strong>
        （不常驻后台）；错过时间点后 {schedule.grace_minutes} 分钟内启动仍会补跑。
      </p>

      <label className="flex items-start gap-2 text-xs">
        <input
          type="checkbox"
          className="mt-0.5"
          checked={schedule.enabled}
          disabled={busy}
          onChange={(event) => void save({ enabled: event.target.checked })}
        />
        <span>
          <span className="text-ink-7">启用定时投递</span>
          <span className="mt-0.5 block text-ink-6">{schedule.description}</span>
        </span>
      </label>

      <div className="rounded-control border border-ink-1 p-3">
        <div className="text-xs text-ink-6">
          每天在哪些时刻跑（最多 {schedule.max_slots} 个）
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            className="w-28 rounded-control border border-ink-2 px-2 py-1 text-sm"
            placeholder="09:30"
            value={draft}
            disabled={busy}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') addTime()
            }}
          />
          <button
            type="button"
            disabled={busy || !draft.trim()}
            onClick={addTime}
            className="rounded-control border border-ink-2 px-2.5 py-1 text-xs hover:bg-paper disabled:opacity-40"
          >
            添加
          </button>
          {schedule.times.map((item) => (
            <span
              key={item}
              className="flex items-center gap-1 rounded-full bg-sunken px-2.5 py-1 text-xs"
            >
              {item}
              <button
                type="button"
                disabled={busy}
                onClick={() => removeTime(item)}
                className="text-ink-6 hover:text-warn"
                aria-label={`删除 ${item}`}
              >
                ×
              </button>
            </span>
          ))}
          {schedule.times.length === 0 && <span className="text-xs text-ink-6">还没有时间点</span>}
        </div>

        {droppedTimes.length > 0 && (
          <p className="mt-2 text-xs text-warn">
            这些时间点认不出来，<strong>不会生效</strong>：{droppedTimes.join('、')}。
            请改写成 HH:MM 格式。
          </p>
        )}
      </div>

      <label className="block text-xs text-ink-6">
        这个时间点做什么
        <select
          className="mt-1 w-full rounded-control border border-ink-2 px-2 py-1.5 text-sm"
          value={schedule.mode}
          disabled={busy}
          onChange={(event) => void save({ mode: event.target.value })}
        >
          {Object.entries(schedule.mode_labels).map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
        {schedule.mode === 'apply' && (
          <span className="mt-1 block text-xs text-warn">
            自动投递会真的把简历发出去，不再逐条等你确认。仅在本机运行时执行。
          </span>
        )}
      </label>

      <div className="grid gap-3 sm:grid-cols-3">
        <label className="text-xs text-ink-6">
          每次抓多少条
          <input
            type="number"
            min={1}
            max={60}
            className="mt-1 w-full rounded-control border border-ink-2 px-2 py-1.5 text-sm"
            value={schedule.limit}
            disabled={busy}
            onChange={(event) => void save({ limit: Number(event.target.value) })}
          />
        </label>
        <label className="text-xs text-ink-6 sm:col-span-2">
          关键词（空格分隔，留空＝不限）
          <input
            className="mt-1 w-full rounded-control border border-ink-2 px-2 py-1.5 text-sm"
            value={keywords}
            disabled={busy}
            onChange={(event) => setKeywords(event.target.value)}
            onBlur={() =>
              void save({ keywords: keywords.split(/\s+/).map((w) => w.trim()).filter(Boolean) })
            }
          />
        </label>
      </div>

      <label className="flex items-center gap-2 text-xs text-ink-6">
        <input
          type="checkbox"
          checked={schedule.fetch_detail}
          disabled={busy}
          onChange={(event) => void save({ fetch_detail: event.target.checked })}
        />
        抓详情（能拿到真薪资 / 学历 / HR 状态；更慢、更容易触发风控，建议抓取数量调到 5–10）
      </label>

      <div className="rounded-control bg-paper p-3 text-xs text-ink-6">
        <div>
          状态：
          {schedule.active ? (
            <span className="text-ok">已排期</span>
          ) : (
            <span className="text-ink-6">未排期（不会自动跑）</span>
          )}
        </div>
        {schedule.next_run_at && <div className="mt-1">下次运行：{schedule.next_run_at}</div>}
        {schedule.last_run_at && (
          <div className="mt-1">
            上次运行：{schedule.last_run_at}
            {schedule.last_result && <span className="ml-1">· {schedule.last_result}</span>}
          </div>
        )}
      </div>
    </div>
  )
}
