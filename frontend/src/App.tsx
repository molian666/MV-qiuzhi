import { useEffect, useState } from 'react'
import { api } from './api/client'
import type { AuthStatus } from './api/types'
import Applications from './pages/Applications'
import ApplyDesk from './pages/ApplyDesk'
import CareerDesk from './pages/CareerDesk'
import Expression from './pages/Expression'
import Login from './pages/Login'
import InterviewLive from './pages/InterviewLive'
import InterviewVoice from './pages/InterviewVoice'
import NetworkDesk from './pages/NetworkDesk'
import OfferDesk from './pages/OfferDesk'
import Report from './pages/Report'
import Recordings from './pages/Recordings'
import ResumeHealth from './pages/ResumeHealth'
import Sessions from './pages/Sessions'
import SpeechKit from './pages/SpeechKit'
import System from './pages/System'
import Training from './pages/Training'

const TABS = [
  { key: 'interview', label: '面试间' },
  { key: 'voice', label: '语音面试' },
  { key: 'report', label: '报告' },
  { key: 'expression', label: '表达体检' },
  { key: 'training', label: '训练' },
  { key: 'pacing', label: '节奏学习' },
  { key: 'resume', label: '简历体检' },
  { key: 'speech', label: '故事与话术' },
  { key: 'apply', label: '投递执行台' },
  { key: 'applications', label: '投递分析' },
  { key: 'offer', label: 'Offer 与谈判' },
  { key: 'network', label: '人脉与学习' },
  { key: 'career', label: '入职与长期' },
  { key: 'sessions', label: '历史面试' },
  { key: 'system', label: '系统与用量' },
] as const

type TabKey = (typeof TABS)[number]['key']

export default function App() {
  const [tab, setTab] = useState<TabKey>('interview')
  const [provider, setProvider] = useState('')
  const [reportSessionId, setReportSessionId] = useState<number | null>(null)
  const [auth, setAuth] = useState<AuthStatus | null>(null)

  useEffect(() => {
    api
      .health()
      .then((health) => setProvider(health.llm_provider))
      .catch(() => setProvider('未连接'))
    // 要不要登录由服务端说了算：本地 loopback + 没配密码时 auth_enabled=false，直接进。
    // 查不到时按「需要登录」处理——宁可多要一次密码，也不要默认把数据敞开。
    api
      .authStatus()
      .then(setAuth)
      .catch(() => setAuth({ auth_enabled: true, logged_in: false, ttl_days: 0 }))
  }, [])

  /** 从任意页面跳到某场面试的报告 */
  const openReport = (sessionId: number) => {
    setReportSessionId(sessionId)
    setTab('report')
  }

  // 未登录时只渲染登录页：其他页面若先发请求，会把 401 显示成业务错误，误导人
  if (auth === null) {
    return <div className="p-6 text-sm text-slate-500">正在连接服务…</div>
  }
  if (auth.auth_enabled && !auth.logged_in) {
    return <Login onSuccess={() => setAuth({ ...auth, logged_in: true })} />
  }

  return (
    <div className="min-h-full bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3">
          <div className="flex items-baseline gap-3">
            <h1 className="text-lg font-semibold">mianshi · 求职操作系统</h1>
            <span className="text-xs text-slate-500">M15：入职与长期 + 导出</span>
          </div>
          <div className="flex items-center gap-3">
            <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-600">
              模型：{provider || '…'}
            </span>
            {auth.auth_enabled && (
              <button
                type="button"
                onClick={() => {
                  void api.logout().then(() => setAuth({ ...auth, logged_in: false }))
                }}
                className="rounded-md border border-slate-300 px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50"
                title={`登录有效期 ${auth.ttl_days} 天`}
              >
                退出登录
              </button>
            )}
            <nav className="flex gap-1">
              {TABS.map((item) => (
                <button
                  key={item.key}
                  type="button"
                  onClick={() => setTab(item.key)}
                  className={
                    item.key === tab
                      ? 'rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white'
                      : 'rounded-md px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100'
                  }
                >
                  {item.label}
                </button>
              ))}
            </nav>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-5">
        {tab === 'interview' && <InterviewLive onOpenReport={openReport} />}
        {tab === 'voice' && <InterviewVoice onOpenReport={openReport} />}
        {tab === 'sessions' && <Sessions onOpenReport={openReport} />}
        {tab === 'report' && <Report sessionId={reportSessionId} onSelectSession={setReportSessionId} />}
        {tab === 'expression' && <Expression />}
        {tab === 'training' && <Training />}
        {tab === 'pacing' && <Recordings />}
        {tab === 'resume' && <ResumeHealth />}
        {tab === 'speech' && <SpeechKit />}
        {tab === 'apply' && <ApplyDesk />}
        {tab === 'applications' && <Applications />}
        {tab === 'offer' && <OfferDesk />}
        {tab === 'network' && <NetworkDesk />}
        {tab === 'career' && <CareerDesk />}
        {tab === 'system' && <System />}
      </main>
    </div>
  )
}
