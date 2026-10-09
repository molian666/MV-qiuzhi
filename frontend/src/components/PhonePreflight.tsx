/**
 * 手机就绪体检卡（M19）。
 *
 * 存在的理由：手机上的"点了没反应"有好几种原因，**表现几乎一样**，
 * 用户无法区分就会归因成"这软件坏了"。这张卡片把三件事逐项说清楚：
 *
 * 1. **HTTPS**：非安全上下文时浏览器直接不给麦克风（iOS / Android 都是硬性拒绝）。
 *    这是唯一"用户无法绕过"的一条，所以必须给出可执行的修复办法（用哪条命令、走哪个地址）。
 * 2. **麦克风**：API 在不在、权限给没给。
 * 3. **语音播报**：iOS 上 `speechSynthesis` 要用户先点一下才出声（见 `audio/unlock.ts`）。
 *
 * 刻意做成"能不看就不看"：全部正常时只显示一条细线，出问题时才展开细节。
 * 手机屏幕上空间宝贵，把体检报告糊在用户脸上只会挤走真正的内容。
 */

import type { ClientInfo } from '../api/types'
import type { DeviceState } from '../mobile/useDevice'

interface Props {
  device: DeviceState
  info: ClientInfo | null
  /** 服务端信息拿不到时（离线/接口挂了）也要能显示，不要因此空着 */
  error?: string
  className?: string
}

type CheckState = 'ok' | 'warn' | 'bad'

interface Check {
  key: string
  label: string
  state: CheckState
  detail: string
  /** 只在出问题时展示的可执行建议 */
  fix?: string
}

function stateColor(state: CheckState): string {
  if (state === 'ok') {
    return 'text-ok'
  }
  return state === 'warn' ? 'text-warn' : 'text-ng'
}

function stateLabel(state: CheckState): string {
  if (state === 'ok') {
    return '就绪'
  }
  return state === 'warn' ? '注意' : '不可用'
}

export function buildChecks(device: DeviceState, info: ClientInfo | null): Check[] {
  const checks: Check[] = []

  // ---- 1. 安全上下文 ----
  if (!device.secureContext) {
    const isHttpNonLocal =
      device.protocol === 'http:' && info !== null && !info.local_deployment
    checks.push({
      key: 'secure',
      label: 'HTTPS',
      state: 'bad',
      detail: `当前是 ${device.protocol}// 打开的非本机地址，浏览器不会提供麦克风。`,
      fix: isHttpNonLocal
        ? '改用 HTTPS 打开：在电脑上跑 pwsh -File scripts\\mobile.ps1（会用 Tailscale 签真证书），再扫它给出的二维码。'
        : '改用 HTTPS 打开，或直接在电脑上用 http://127.0.0.1:8000（localhost 算安全上下文）。',
    })
  } else {
    checks.push({
      key: 'secure',
      label: 'HTTPS',
      state: 'ok',
      detail: device.protocol === 'https:' ? '已用 HTTPS 打开。' : '本机访问，浏览器视为安全上下文。',
    })
  }

  // ---- 2. 麦克风 ----
  if (!device.hasMediaDevices) {
    checks.push({
      key: 'mic',
      label: '麦克风',
      state: 'bad',
      detail: '这个浏览器没有暴露录音接口（getUserMedia）。',
      fix: '换个浏览器：iOS 用 Safari，Android 用 Chrome。微信内置浏览器等 WebView 常常不给麦克风。',
    })
  } else if (!device.secureContext) {
    checks.push({
      key: 'mic',
      label: '麦克风',
      state: 'bad',
      detail: '接口在，但非安全上下文下会被直接拒绝。',
      fix: '先解决上面的 HTTPS 问题，这一步会自动好。',
    })
  } else {
    checks.push({
      key: 'mic',
      label: '麦克风',
      state: 'warn',
      detail: '首次点「开始说话」时浏览器会弹权限询问，选允许即可。',
    })
  }

  // ---- 3. 面试官的声音 ----
  if (!device.hasSpeechSynthesis || !device.hasAudioContext) {
    const missing = [
      device.hasSpeechSynthesis ? null : 'speechSynthesis',
      device.hasAudioContext ? null : 'AudioContext',
    ]
      .filter((item): item is string => item !== null)
      .join(' / ')
    checks.push({
      key: 'speak',
      label: '语音播报',
      state: 'warn',
      detail: `缺少 ${missing}，面试官的声音可能听不到。`,
      fix: '仍然可以用：面试官的问题会以文字显示，你正常回答即可。',
    })
  } else {
    checks.push({
      key: 'speak',
      label: '语音播报',
      state: 'ok',
      detail: '浏览器自带语音合成可用（点一次「开始」后才会出声，这是 iOS 的限制）。',
    })
  }

  return checks
}

export default function PhonePreflight({ device, info, error, className = '' }: Props) {
  const checks = buildChecks(device, info)
  const problems = checks.filter((check) => check.state !== 'ok')

  // 一切正常：只留一条细线，别占手机屏幕
  if (problems.length === 0) {
    return (
      <div className={`flex items-center gap-2 px-1 py-1 font-mono text-[11px] text-ink-4 ${className}`}>
        <span className="signal-dot text-ok" />
        设备就绪：HTTPS / 麦克风 / 语音播报
        {info?.speech_provider === 'mock' ? '（语音识别为 Mock，回答用文字替身）' : ''}
      </div>
    )
  }

  return (
    <section className={`rounded-panel border border-warn/40 bg-warn-soft p-3 ${className}`}>
      <div className="flex items-center gap-2">
        <span className="signal-dot text-warn" />
        <h3 className="text-xs font-semibold text-ink-9">这台设备还不能正常面试</h3>
      </div>

      <ul className="mt-2 space-y-2">
        {checks.map((check) => (
          <li key={check.key} className="text-[11px] leading-relaxed">
            <div className="flex items-baseline gap-2">
              <span className={`font-mono text-[10px] ${stateColor(check.state)}`}>
                [{stateLabel(check.state)}]
              </span>
              <span className="font-medium text-ink-7">{check.label}</span>
            </div>
            <p className="mt-0.5 text-ink-6">{check.detail}</p>
            {check.fix && check.state !== 'ok' && (
              <p className="mt-0.5 text-ink-6">
                <span className="text-ink-4">怎么办：</span>
                {check.fix}
              </p>
            )}
          </li>
        ))}
      </ul>

      {error && (
        <p className="mt-2 font-mono text-[10px] text-ink-4">服务端信息读取失败：{error}</p>
      )}

      {info && (
        <p className="mt-2 border-t border-warn/30 pt-2 font-mono text-[10px] text-ink-4">
          服务端：{info.host}:{info.port} · TLS {info.use_tls ? '开' : '关'} ·{' '}
          {info.local_deployment ? '本机部署' : '非本机部署'} · 语音 {info.speech_provider}
        </p>
      )}
    </section>
  )
}
