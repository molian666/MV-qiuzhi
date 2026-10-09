/**
 * 投递账号管理（多账号轮换）。
 *
 * 三个刻意的设计：
 *
 * 1. **代理只显示脱敏形式，编辑框始终留空**。webhook/代理地址里可能带账号密码，
 *    留空表示"不修改"，而不是"清空它"。
 * 2. **每个账号必须有自己的浏览器目录**，界面上把这一点写出来——共用目录等于共用一个
 *    登录态，"多账号"就是假的，而用户不会自己想到这件事。
 * 3. **提供"探测代理"并显示出口 IP**。地址语法正确 ≠ 能用；用户真正想知道的是
 *    "我换成功了吗"，所以要把出口 IP 摆出来，而不是只说"配置已保存"。
 */

import { useCallback, useEffect, useState } from 'react'

import { api } from '../api/client'
import type { ApplyProfile, ApplyProfiles, ApplyProxyProbe } from '../api/types'

interface Props {
  busy: boolean
  setBusy: (value: boolean) => void
  onError: (message: string) => void
}

/** 新建账号时的表单 */
interface Draft {
  label: string
  proxy: string
  daily_limit: number
  locale: string
}

const EMPTY_DRAFT: Draft = { label: '', proxy: '', daily_limit: 20, locale: 'zh-CN' }

export default function ApplyProfilesPanel({ busy, setBusy, onError }: Props) {
  const [data, setData] = useState<ApplyProfiles | null>(null)
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT)
  const [editing, setEditing] = useState<number | null>(null)
  const [editProxy, setEditProxy] = useState('')
  const [probe, setProbe] = useState<Record<number, ApplyProxyProbe>>({})
  const [probeDraft, setProbeDraft] = useState<ApplyProxyProbe | null>(null)

  const load = useCallback(async () => {
    try {
      setData(await api.applyProfiles())
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err))
    }
  }, [onError])

  useEffect(() => {
    void load()
  }, [load])

  if (!data) {
    return <p className="text-xs text-ink-6">加载账号…</p>
  }

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true)
    onError('')
    try {
      await action()
      await load()
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const addProfile = () =>
    run(async () => {
      await api.createApplyProfile({
        label: draft.label,
        proxy: draft.proxy,
        daily_limit: draft.daily_limit,
        locale: draft.locale,
      })
      setDraft(EMPTY_DRAFT)
      setProbeDraft(null)
    })

  const saveEdit = (profile: ApplyProfile) =>
    run(async () => {
      await api.updateApplyProfile(profile.id, {
        label: profile.label,
        daily_limit: profile.daily_limit,
        enabled: profile.enabled,
        locale: profile.locale,
        // 留空表示不修改已保存的代理（不能清掉它）
        ...(editProxy.trim() ? { proxy: editProxy.trim() } : {}),
      })
      setEditing(null)
      setEditProxy('')
    })

  const testProxy = async (profile: ApplyProfile) => {
    setBusy(true)
    onError('')
    try {
      const result = await api.probeApplyProxy({ profile_id: profile.id })
      setProbe((prev) => ({ ...prev, [profile.id]: result }))
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const testDraftProxy = async () => {
    setBusy(true)
    onError('')
    try {
      setProbeDraft(await api.probeApplyProxy({ proxy: draft.proxy }))
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const toggleEnabled = (profile: ApplyProfile) =>
    run(() =>
      api.updateApplyProfile(profile.id, {
        label: profile.label,
        daily_limit: profile.daily_limit,
        enabled: !profile.enabled,
        locale: profile.locale,
      }),
    )

  const remove = (profile: ApplyProfile) =>
    run(() => api.deleteApplyProfile(profile.id))

  return (
    <div className="space-y-3">
      <p className="text-xs text-ink-6">
        一个账号接近每日额度或被风控熔断时，自动换下一个继续投。
        <strong className="text-ink-7">多账号不会提高平台允许的总量</strong>
        ，只是把风险摊开；每个账号的限额仍然保守。不配置账号时按单账号投递。
      </p>

      {data.items.length === 0 && (
        <p className="rounded-control border border-ink-1 p-3 text-xs text-ink-6">
          还没有配置账号。下面是新增表单——留空代理表示直连。
        </p>
      )}

      <ul className="space-y-2">
        {data.items.map((profile) => {
          const result = probe[profile.id]
          return (
            <li key={profile.id} className="rounded-control border border-ink-1 p-3">
              {editing === profile.id ? (
                <div className="space-y-2">
                  <input
                    className="w-full rounded-control border border-ink-2 px-2 py-1.5 text-sm"
                    value={profile.label}
                    onChange={(event) =>
                      setData({
                        ...data,
                        items: data.items.map((item) =>
                          item.id === profile.id ? { ...item, label: event.target.value } : item,
                        ),
                      })
                    }
                  />
                  <input
                    className="w-full rounded-control border border-ink-2 px-2 py-1.5 text-sm"
                    placeholder={profile.proxy_configured ? '代理已配置（留空表示不修改）' : '代理（留空＝直连）'}
                    value={editProxy}
                    onChange={(event) => setEditProxy(event.target.value)}
                  />
                  <label className="block text-xs text-ink-6">
                    每日上限
                    <input
                      type="number"
                      min={1}
                      className="mt-1 w-24 rounded-control border border-ink-2 px-2 py-1 text-sm"
                      value={profile.daily_limit}
                      onChange={(event) =>
                        setData({
                          ...data,
                          items: data.items.map((item) =>
                            item.id === profile.id
                              ? { ...item, daily_limit: Number(event.target.value) }
                              : item,
                          ),
                        })
                      }
                    />
                  </label>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void saveEdit(profile)}
                      className="btn-primary rounded-control px-3 py-1.5 text-xs"
                    >
                      保存
                    </button>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => {
                        setEditing(null)
                        setEditProxy('')
                        void load()
                      }}
                      className="rounded-control border border-ink-2 px-3 py-1.5 text-xs"
                    >
                      取消
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm text-ink-7">{profile.label}</span>
                    {!profile.enabled && (
                      <span className="rounded-full bg-sunken px-2 py-0.5 text-xs text-ink-6">已停用</span>
                    )}
                    {profile.broken && (
                      <span className="rounded-full bg-warn-soft px-2 py-0.5 text-xs text-warn">
                        熔断中
                      </span>
                    )}
                    {!profile.proxy_ok && (
                      <span className="rounded-full bg-warn-soft px-2 py-0.5 text-xs text-warn">
                        代理不可用
                      </span>
                    )}
                    <span className="text-xs text-ink-6">
                      今日 {profile.daily_sent}/{profile.daily_limit}（剩 {profile.remaining}）
                    </span>
                  </div>

                  <div className="mt-1 text-xs text-ink-6">
                    代理：{profile.proxy_configured ? profile.masked_proxy : '直连'}
                    <span className="ml-2">语言：{profile.locale}</span>
                  </div>
                  <div className="mt-0.5 text-xs text-ink-6">
                    浏览器目录：<code className="text-ink-7">{profile.user_data_dir}</code>
                  </div>

                  <div className="mt-2 flex flex-wrap gap-2">
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => {
                        setEditing(profile.id)
                        setEditProxy('')
                      }}
                      className="rounded-control border border-ink-2 px-2.5 py-1 text-xs hover:bg-paper disabled:opacity-40"
                    >
                      编辑
                    </button>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void toggleEnabled(profile)}
                      className="rounded-control border border-ink-2 px-2.5 py-1 text-xs hover:bg-paper disabled:opacity-40"
                    >
                      {profile.enabled ? '停用' : '启用'}
                    </button>
                    <button
                      type="button"
                      disabled={busy || !profile.proxy_configured}
                      onClick={() => void testProxy(profile)}
                      className="rounded-control border border-ink-2 px-2.5 py-1 text-xs hover:bg-paper disabled:opacity-40"
                    >
                      探测代理
                    </button>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void remove(profile)}
                      className="rounded-control border border-ink-2 px-2.5 py-1 text-xs text-warn hover:bg-paper disabled:opacity-40"
                    >
                      删除
                    </button>
                  </div>

                  {result && (
                    <p className={result.ok ? 'mt-2 text-xs text-ok' : 'mt-2 text-xs text-warn'}>
                      {result.detail}
                      {result.ok && result.exit_ip && (
                        <span className="ml-1">（出口 IP：{result.exit_ip}）</span>
                      )}
                      {result.local_proxy && (
                        <span className="mt-0.5 block text-warn">
                          这条代理指向本机，出口 IP 不会改变。
                        </span>
                      )}
                    </p>
                  )}
                </>
              )}
            </li>
          )
        })}
      </ul>

      <div className="rounded-control border border-ink-1 p-3">
        <div className="text-xs text-ink-7">新增账号</div>
        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          <label className="text-xs text-ink-6">
            名称
            <input
              className="mt-1 w-full rounded-control border border-ink-2 px-2 py-1.5 text-sm"
              placeholder="主号 / 小号"
              value={draft.label}
              onChange={(event) => setDraft({ ...draft, label: event.target.value })}
            />
          </label>
          <label className="text-xs text-ink-6">
            每日上限
            <input
              type="number"
              min={1}
              className="mt-1 w-full rounded-control border border-ink-2 px-2 py-1.5 text-sm"
              value={draft.daily_limit}
              onChange={(event) => setDraft({ ...draft, daily_limit: Number(event.target.value) })}
            />
          </label>
          <label className="text-xs text-ink-6 sm:col-span-2">
            代理（留空＝直连）
            <input
              className="mt-1 w-full rounded-control border border-ink-2 px-2 py-1.5 text-sm"
              placeholder="http://user:pass@host:port"
              value={draft.proxy}
              onChange={(event) => setDraft({ ...draft, proxy: event.target.value })}
            />
          </label>
          <label className="text-xs text-ink-6">
            语言 / 时区
            <select
              className="mt-1 w-full rounded-control border border-ink-2 px-2 py-1.5 text-sm"
              value={draft.locale}
              onChange={(event) => setDraft({ ...draft, locale: event.target.value })}
            >
              {data.locales.map((item) => (
                <option key={item.key} value={item.key}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={busy || !draft.label.trim()}
            onClick={() => void addProfile()}
            className="btn-primary rounded-control px-3 py-1.5 text-xs disabled:opacity-40"
          >
            新增
          </button>
          <button
            type="button"
            disabled={busy || !draft.proxy.trim()}
            onClick={() => void testDraftProxy()}
            className="rounded-control border border-ink-2 px-3 py-1.5 text-xs hover:bg-paper disabled:opacity-40"
          >
            先探测这个代理
          </button>
          <span className="text-xs text-ink-6">
            每个账号会自动分配独立的浏览器目录（共用目录等于共用一个登录态）。
          </span>
        </div>

        {probeDraft && (
          <p className={probeDraft.ok ? 'mt-2 text-xs text-ok' : 'mt-2 text-xs text-warn'}>
            {probeDraft.detail}
            {probeDraft.ok && probeDraft.exit_ip && (
              <span className="ml-1">（出口 IP：{probeDraft.exit_ip}）</span>
            )}
          </p>
        )}
      </div>
    </div>
  )
}
