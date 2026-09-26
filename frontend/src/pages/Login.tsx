/** 登录页：只在服务端配了 AUTH_PASSWORD 时出现。
 *
 * 本地（loopback + 没配密码）时 `auth_enabled=false`，App 直接跳过这一页，
 * 开发体验和之前完全一样——防护不该让本地变麻烦。
 */

import { useState } from 'react'
import { api } from '../api/client'

export default function Login({ onSuccess }: { onSuccess: () => void }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    setBusy(true)
    setError('')
    try {
      await api.login(password)
      onSuccess()
    } catch (err) {
      // 401 时前端已经把本地 token 清掉了，这里直接把服务端原因显示出来（「密码不正确」）
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-full items-center justify-center bg-paper px-4 text-ink-9">
      <form
        className="w-full max-w-sm rounded-card border border-ink-1 bg-surface p-6"
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        <h1 className="text-base font-semibold">mianshi · 求职操作系统</h1>
        <p className="mt-1 text-xs text-ink-6">
          这台服务设了访问密码。密码在 <code className="rounded-control bg-sunken px-1">backend/.env</code> 的{' '}
          <code className="rounded-control bg-sunken px-1">AUTH_PASSWORD</code> 里。
        </p>

        <input
          type="password"
          autoFocus
          autoComplete="current-password"
          className="mt-4 w-full rounded-control border border-ink-2 px-3 py-2 text-sm"
          placeholder="访问密码"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />

        {error && (
          <div className="mt-2 rounded-control border border-ng bg-ng-soft px-3 py-2 text-xs text-ng">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={busy || !password}
          className="mt-4 w-full rounded-control bg-ink-9 px-3 py-2 text-sm text-surface disabled:border-ink-1 disabled:bg-sunken disabled:text-ink-6"
        >
          {busy ? '登录中…' : '登录'}
        </button>

        <p className="mt-3 text-[11px] leading-relaxed text-ink-4">
          这个服务里存着你的简历、面试录音与投递记录，所以即使只给自己用也要有密码——
          否则任何扫到这个端口的人都能读走，并盗用你配置的模型 Key。
        </p>
      </form>
    </div>
  )
}
