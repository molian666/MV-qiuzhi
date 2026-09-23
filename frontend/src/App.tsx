import { useEffect, useState } from 'react'
import { api } from './api/client'
import Applications from './pages/Applications'
import ApplyDesk from './pages/ApplyDesk'
import InterviewLive from './pages/InterviewLive'
import InterviewVoice from './pages/InterviewVoice'
import Report from './pages/Report'
import Recordings from './pages/Recordings'
import Sessions from './pages/Sessions'
import System from './pages/System'
import Training from './pages/Training'

const TABS = [
  { key: 'interview', label: '面试间' },
  { key: 'voice', label: '语音面试' },
  { key: 'report', label: '报告' },
  { key: 'training', label: '训练' },
  { key: 'pacing', label: '节奏学习' },
  { key: 'apply', label: '投递执行台' },
  { key: 'applications', label: '投递分析' },
  { key: 'sessions', label: '历史面试' },
  { key: 'system', label: '系统与用量' },
] as const

type TabKey = (typeof TABS)[number]['key']

export default function App() {
  const [tab, setTab] = useState<TabKey>('interview')
  const [provider, setProvider] = useState('')
  const [reportSessionId, setReportSessionId] = useState<number | null>(null)

  useEffect(() => {
    api
      .health()
      .then((health) => setProvider(health.llm_provider))
      .catch(() => setProvider('未连接'))
  }, [])

  /** 从任意页面跳到某场面试的报告 */
  const openReport = (sessionId: number) => {
    setReportSessionId(sessionId)
    setTab('report')
  }

  return (
    <div className="min-h-full bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3">
          <div className="flex items-baseline gap-3">
            <h1 className="text-lg font-semibold">mianshi · 求职操作系统</h1>
            <span className="text-xs text-slate-500">M10：半自动投递</span>
          </div>
          <div className="flex items-center gap-3">
            <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-600">
              模型：{provider || '…'}
            </span>
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
        {tab === 'training' && <Training />}
        {tab === 'pacing' && <Recordings />}
        {tab === 'apply' && <ApplyDesk />}
        {tab === 'applications' && <Applications />}
        {tab === 'system' && <System />}
      </main>
    </div>
  )
}
