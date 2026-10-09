/**
 * 通知配置面板（企业微信机器人 / 通用 Webhook）。
 *
 * 两个刻意的设计：
 *
 * 1. **地址永不回显**。webhook URL 里的 `key=` 就是凭据，拿到它任何人都能往你群里发消息。
 *    所以这里只显示脱敏片段（`configured` + `masked_url`），输入框始终从空开始——
 *    留空表示"不改动已保存的地址"，而不是"清空它"。
 * 2. **成功类默认不订阅**。每投一条就响一次手机会让人把通知整个关掉，反而漏掉真正
 *    重要的熔断提醒。所以默认只勾"需要你行动"的三类。
 */

import { useEffect, useState } from 'react'

import { api } from '../api/client'
import type { ApplyNotifySettings } from '../api/types'

interface Props {
  busy: boolean
  setBusy: (value: boolean) => void
  onError: (message: string) => void
}

const EVENT_LABELS: { key: keyof ApplyNotifySettings; label: string; hint: string }[] = [
  { key: 'on_circuit', label: '熔断停投', hint: '检测到风控/验证码而停投当天——最该收到的一条' },
  { key: 'on_captcha', label: '需要手动过验证码', hint: '程序暂停，等你到浏览器里处理' },
  { key: 'on_failed', label: '投递失败', hint: '单条投递失败时' },
  { key: 'on_sent', label: '每次投递成功', hint: '默认关闭：投得多时会很吵' },
]

export default function ApplyNotifyPanel({ busy, setBusy, onError }: Props) {
  const [settings, setSettings] = useState<ApplyNotifySettings | null>(null)
  const [url, setUrl] = useState('')
  const [channel, setChannel] = useState('wechat')
  const [testResult, setTestResult] = useState<{ ok: boolean; detail: string } | null>(null)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const next = await api.applyNotifySettings()
        if (cancelled) return
        setSettings(next)
        setChannel(next.channel)
      } catch (err) {
        if (!cancelled) onError(err instanceof Error ? err.message : String(err))
      }
    })()
    return () => {
      cancelled = true
    }
  }, [onError])

  if (!settings) {
    return <p className="text-xs text-ink-6">加载通知配置…</p>
  }

  const save = async (patch: Partial<ApplyNotifySettings>) => {
    setBusy(true)
    onError('')
    try {
      const payload: Record<string, unknown> = { ...patch, channel: patch.channel ?? channel }
      // 只有真的填了新地址才提交 url —— 留空表示"保持原样"，
      // 否则一次普通的勾选保存就会把已配好的地址清掉
      if (url.trim()) {
        payload.url = url.trim()
      }
      const next = await api.updateApplyNotifySettings(payload)
      setSettings(next)
      setChannel(next.channel)
      setUrl('')
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const runTest = async () => {
    setBusy(true)
    setTestResult(null)
    try {
      const result = await api.testApplyNotify({
        url: url.trim() || undefined,
        channel,
      })
      setTestResult(result)
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-ink-6">
        把投递情况发到你自己配置的机器人。属于「对外发消息」这一档，需要先勾选风险确认。
      </p>

      <label className="block text-xs text-ink-6">
        通知通道
        <select
          className="mt-1 w-full rounded-control border border-ink-2 px-2 py-1.5 text-sm"
          value={channel}
          disabled={busy}
          onChange={(event) => {
            setChannel(event.target.value)
            void save({ channel: event.target.value })
          }}
        >
          {settings.channels.map((item) => (
            <option key={item.key} value={item.key}>
              {item.label}
            </option>
          ))}
        </select>
      </label>

      <label className="block text-xs text-ink-6">
        Webhook 地址
        <input
          className="mt-1 w-full rounded-control border border-ink-2 px-2 py-1.5 text-sm"
          placeholder={settings.configured ? '已配置（留空表示不修改）' : 'https://…'}
          value={url}
          disabled={busy}
          onChange={(event) => setUrl(event.target.value)}
        />
      </label>

      {settings.configured && (
        <p className="text-xs text-ink-6">
          当前已配置：<code className="text-ink-7">{settings.masked_url}</code>
          <span className="ml-1">（出于安全不回显完整地址，地址里含凭据）</span>
        </p>
      )}

      <fieldset className="rounded-control border border-ink-1 p-3">
        <legend className="px-1 text-xs text-ink-6">什么时候通知我</legend>
        <ul className="space-y-2">
          {EVENT_LABELS.map((item) => (
            <li key={String(item.key)}>
              <label className="flex items-start gap-2 text-xs">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={Boolean(settings[item.key])}
                  disabled={busy}
                  onChange={(event) => void save({ [item.key]: event.target.checked })}
                />
                <span>
                  <span className="text-ink-7">{item.label}</span>
                  <span className="mt-0.5 block text-ink-6">{item.hint}</span>
                </span>
              </label>
            </li>
          ))}
        </ul>
      </fieldset>

      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => void runTest()}
          className="rounded-control border border-ink-2 px-3 py-1.5 text-xs hover:bg-paper disabled:opacity-40"
        >
          发一条测试通知
        </button>
        {url.trim() && (
          <button
            type="button"
            disabled={busy}
            onClick={() => void save({})}
            className="btn-primary rounded-control px-3 py-1.5 text-xs"
          >
            保存地址
          </button>
        )}
      </div>

      {testResult && (
        <p className={testResult.ok ? 'text-xs text-ok' : 'text-xs text-warn'}>
          {testResult.ok ? '已发送：' : '发送失败：'}
          {testResult.detail}
        </p>
      )}
    </div>
  )
}
