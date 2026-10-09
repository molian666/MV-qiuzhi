import { useEffect, useState } from 'react'
import { api } from './api/client'
import type { AuthStatus } from './api/types'
import Applications from './pages/Applications'
import ApplyDesk from './pages/ApplyDesk'
import Expression from './pages/Expression'
import InterviewRoom from './pages/InterviewRoom'
import Login from './pages/Login'
import OfferDesk from './pages/OfferDesk'
import Report from './pages/Report'
import Recordings from './pages/Recordings'
import ResumeHealth from './pages/ResumeHealth'
import Sessions from './pages/Sessions'
import SpeechKit from './pages/SpeechKit'
import System from './pages/System'
import Training from './pages/Training'
import VisionBank from './pages/VisionBank'

/** 导航按**真实的使用顺序**分组，而不是把十几项平铺成一排。
 *
 *  面试 → 复盘 → 准备 → 投递是一条真实的时间线：面完看报告找问题，
 *  针对性练，准备好了再去投。分组本身就在告诉人"现在该做什么"。
 */
type NavKey =
  | 'interview'
  | 'sessions'
  | 'report'
  | 'expression'
  | 'pacing'
  | 'training'
  | 'bank'
  | 'resume'
  | 'speech'
  | 'apply'
  | 'applications'
  | 'offer'
  | 'system'

type NavItem = { key: NavKey; label: string; note: string }

const NAV: { group: string; items: NavItem[] }[] = [
  {
    group: '面试',
    items: [
      { key: 'interview', label: '面试间', note: '文字或语音，一次一场' },
      { key: 'sessions', label: '历史面试', note: '过往场次' },
    ],
  },
  {
    group: '复盘',
    items: [
      { key: 'report', label: '报告', note: '评分与改进项' },
      { key: 'expression', label: '表达体检', note: '语速与口头禅' },
      { key: 'pacing', label: '节奏学习', note: '录音回听' },
    ],
  },
  {
    group: '准备',
    items: [
      { key: 'training', label: '训练', note: '薄弱点、出题与入库' },
      { key: 'bank', label: '面试题题库', note: '按主题查阅' },
      { key: 'resume', label: '简历体检', note: '对齐岗位 JD' },
      { key: 'speech', label: '故事与话术', note: '故事卡与自述' },
    ],
  },
  {
    group: '投递',
    items: [
      { key: 'apply', label: '投递执行台', note: '筛选与确认' },
      { key: 'applications', label: '投递分析', note: '漏斗与反馈' },
      { key: 'offer', label: 'Offer 与谈判', note: '对比与决策' },
    ],
  },
  {
    group: '系统',
    items: [{ key: 'system', label: '系统与用量', note: '模型与成本' }],
  },
]

const ALL_ITEMS = NAV.flatMap((section) => section.items)

export default function App() {
  const [tab, setTab] = useState<NavKey>('interview')
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
    return <div className="p-6 text-sm text-ink-4">正在连接服务…</div>
  }
  if (auth.auth_enabled && !auth.logged_in) {
    return <Login onSuccess={() => setAuth({ ...auth, logged_in: true })} />
  }

  const current = ALL_ITEMS.find((item) => item.key === tab)

  // 整体锁在视口高度内：滚动发生在页面**内部的**面板里，而不是整页。
  // 工具型界面这样更好用——切页时导航和标题不会跟着跑掉。
  // 侧栏做成暗色"工位状态栏"（HMI 语义）：深底 + 当前工位亮底取景框 + 信号条。
  //
  // M19：用 `.app-viewport`（100dvh）而不是 `h-screen`（100vh）。
  // 手机浏览器地址栏伸缩时 `vh` 按最大高度算，底部会被顶到地址栏外面；
  // 底部还有 44px 触控目标时这一点会直接导致"按钮点不到"。
  return (
    <div className="app-viewport flex flex-col overflow-hidden md:flex-row">
      {/* 侧栏。窄屏时变成横向可滚动的条带——十几项在手机上只能这样摆。 */}
      <aside className="shrink-0 border-b border-dark-rail bg-dark md:flex md:w-52 md:flex-col md:border-b-0 md:border-r">
        <div className="flex items-center justify-between gap-3 px-4 py-3 md:block md:py-4">
          <div>
            <div className="text-sm font-semibold tracking-tight text-surface">mianshi</div>
            <div className="mt-0.5 font-mono text-[10px] tracking-wide text-dark-ink-4">
              机器视觉求职训练
            </div>
          </div>
          {auth.auth_enabled && (
            <button
              type="button"
              onClick={() => {
                void api.logout().then(() => setAuth({ ...auth, logged_in: false }))
              }}
              className="rounded-control border border-dark-rail px-2 py-1 font-mono text-[10px] text-dark-ink-4 transition-colors hover:bg-dark-rail md:mt-3 md:w-full"
              title={`登录有效期 ${auth.ttl_days} 天`}
            >
              退出登录
            </button>
          )}
        </div>

        <nav className="flex gap-4 overflow-x-auto px-4 pb-3 md:min-h-0 md:flex-1 md:block md:gap-0 md:overflow-y-auto md:pb-4">
          {NAV.map((section) => (
            <div key={section.group} className="shrink-0 md:mb-4">
              <div className="mb-1 font-mono text-[10px] tracking-wide text-dark-ink-3 md:px-2">
                {section.group}
              </div>
              <div className="flex gap-1 md:block md:space-y-0.5">
                {section.items.map((item) => {
                  const active = item.key === tab
                  return (
                    <button
                      key={item.key}
                      type="button"
                      onClick={() => setTab(item.key)}
                      aria-current={active ? 'page' : undefined}
                      className={
                        active
                          ? 'block whitespace-nowrap rounded-control border-l-2 border-ok bg-surface px-2.5 py-1.5 pl-2 text-left text-[13px] font-medium text-ink-9 md:w-full'
                          : 'block whitespace-nowrap rounded-control px-2.5 py-1.5 text-left text-[13px] text-dark-ink-4 transition-colors hover:bg-dark-rail md:w-full'
                      }
                    >
                      {item.label}
                    </button>
                  )
                })}
              </div>
            </div>
          ))}
        </nav>
      </aside>

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <header className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-ink-1 bg-surface px-4 py-3">
          <div className="flex min-w-0 items-baseline gap-3">
            <h1 className="truncate text-sm font-semibold">{current?.label ?? ''}</h1>
            {current?.note && (
              <span className="hidden truncate font-mono text-[11px] text-ink-4 sm:inline">
                {current.note}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2 text-[11px]">
            <span className="font-mono text-ink-4">模型</span>
            <span className="flex items-center gap-1.5 rounded-control bg-sunken px-2 py-0.5">
              <span
                className={
                  provider && provider !== '未连接'
                    ? 'signal-dot text-ok'
                    : 'signal-dot text-warn'
                }
              />
              <span className="font-mono text-ink-6">{provider || '…'}</span>
            </span>
          </div>
        </header>

        {/* overflow-hidden：默认不整页滚动。
            里面每个页面自己决定哪块区域滚（h-full + 内部 overflow-y-auto），
            内容确实撑不下的页面由这一层兜底，避免被裁掉看不见。 */}
        <main className="min-h-0 flex-1 overflow-hidden bg-grid-field p-4">
          <div className="h-full overflow-y-auto">
            {tab === 'interview' && <InterviewRoom onOpenReport={openReport} />}
            {tab === 'sessions' && <Sessions onOpenReport={openReport} />}
            {tab === 'report' && (
              <Report sessionId={reportSessionId} onSelectSession={setReportSessionId} />
            )}
            {tab === 'expression' && <Expression />}
            {tab === 'pacing' && <Recordings />}
            {tab === 'training' && <Training />}
            {tab === 'bank' && <VisionBank />}
            {tab === 'resume' && <ResumeHealth />}
            {tab === 'speech' && <SpeechKit />}
            {tab === 'apply' && <ApplyDesk />}
            {tab === 'applications' && <Applications />}
            {tab === 'offer' && <OfferDesk />}
            {tab === 'system' && <System />}
          </div>
        </main>
      </div>
    </div>
  )
}
