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
    <div className="flex min-h-full items-center justify-center bg-slate-50 px-4 text-slate-900">
      <form
        className="w-full max-w-sm rounded-lg border border-slate-200 bg-white p-6"
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        <h1 className="text-base font-semibold">mianshi · 求职操作系统</h1>
        <p className="mt-1 text-xs text-slate-500">
          这台服务设了访问密码。密码在 <code className="rounded bg-slate-100 px-1">backend/.env</code> 的{' '}
          <code className="rounded bg-slate-100 px-1">AUTH_PASSWORD</code> 里。
        </p>

        <input
          type="password"
          autoFocus
          autoComplete="current-password"
          className="mt-4 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
          placeholder="访问密码"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />

        {error && (
          <div className="mt-2 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={busy || !password}
          className="mt-4 w-full rounded-md bg-slate-900 px-3 py-2 text-sm text-white disabled:bg-slate-300"
        >
          {busy ? '登录中…' : '登录'}
        </button>

        <p className="mt-3 text-[11px] leading-relaxed text-slate-400">
          这个服务里存着你的简历、面试录音与投递记录，所以即使只给自己用也要有密码——
          否则任何扫到这个端口的人都能读走，并盗用你配置的模型 Key。
        </p>
      </form>
    </div>
  )
}
