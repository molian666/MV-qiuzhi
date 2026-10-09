/**
 * 设备能力探测（M19 手机端）。
 *
 * 为什么值得单独一个模块：手机上的"点了没反应"有好几种完全不同的原因，
 * 表现却几乎一样。用户无法区分，就会以为是自己操作错了：
 *
 * | 现象 | 真实原因 |
 * |------|----------|
 * | 点"开始说话"没反应 | 用 HTTP 打开的非本机地址 → 不是安全上下文，浏览器直接不给麦克风 |
 * | 面试官一直不出声 | iOS 上 speechSynthesis 没在手势里预热（见 `audio/unlock.ts`） |
 * | 页面被键盘/刘海遮住 | 用的是 `100vh` 而不是 `100dvh`，也没留 safe-area |
 * | 面试中途不动了 | 切后台导致 WebSocket 断连（见 `api/ws.ts` 的重连） |
 *
 * 这个 hook 只负责把**浏览器侧的事实**读出来（服务端那部分在 `/api/client-info`），
 * 由 `PhonePreflight` 逐项给绿灯或给出可执行的修复指引。
 */

import { useCallback, useEffect, useState } from 'react'

export interface DeviceFacts {
  /** 浏览器是否处于安全上下文。**这是麦克风能不能用的硬前提** */
  secureContext: boolean
  hasMediaDevices: boolean
  hasAudioContext: boolean
  hasSpeechSynthesis: boolean
  /** 屏幕宽度（判"是不是手机"用，不依赖 UA 嗅探） */
  width: number
  height: number
  /** 是否支持 Screen Wake Lock（iOS 低电量模式下即使支持也会失败） */
  hasWakeLock: boolean
  isTouch: boolean
  /** 当前页面协议，用于在提示里说清"你正用 HTTP 打开" */
  protocol: string
}

function readFacts(): DeviceFacts {
  if (typeof window === 'undefined') {
    return {
      secureContext: false,
      hasMediaDevices: false,
      hasAudioContext: false,
      hasSpeechSynthesis: false,
      width: 0,
      height: 0,
      hasWakeLock: false,
      isTouch: false,
      protocol: 'unknown',
    }
  }

  const scope = globalThis as unknown as {
    AudioContext?: unknown
    webkitAudioContext?: unknown
  }

  return {
    // 刻意读浏览器的判定而不是自己按协议猜：localhost 与 127.0.0.1 也算安全上下文
    secureContext: window.isSecureContext === true,
    hasMediaDevices: typeof navigator !== 'undefined' && Boolean(navigator.mediaDevices?.getUserMedia),
    hasAudioContext: Boolean(scope.AudioContext ?? scope.webkitAudioContext),
    hasSpeechSynthesis: typeof window.speechSynthesis !== 'undefined',
    width: window.innerWidth,
    height: window.innerHeight,
    hasWakeLock: typeof navigator !== 'undefined' && 'wakeLock' in navigator,
    isTouch: typeof window.matchMedia === 'function' && window.matchMedia('(pointer: coarse)').matches,
    protocol: window.location.protocol,
  }
}

export interface DeviceState extends DeviceFacts {
  /** 窄屏（手机竖屏那类）。按宽度判而不是 UA：平板与桌面小窗口同样受益 */
  isPhone: boolean
  refresh: () => void
}

/** 视口变化的节流刷新：键盘弹出/旋转屏幕都会触发 resize。 */
export function useDevice(): DeviceState {
  const [facts, setFacts] = useState<DeviceFacts>(() => readFacts())

  const refresh = useCallback(() => {
    setFacts(readFacts())
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }
    let frame = 0
    const onResize = () => {
      // rAF 节流：iOS 上键盘动画期间 resize 会连发几十次
      if (frame !== 0) {
        return
      }
      frame = window.requestAnimationFrame(() => {
        frame = 0
        setFacts(readFacts())
      })
    }
    window.addEventListener('resize', onResize)
    window.addEventListener('orientationchange', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      window.removeEventListener('orientationchange', onResize)
      if (frame !== 0) {
        window.cancelAnimationFrame(frame)
      }
    }
  }, [])

  return { ...facts, isPhone: facts.width > 0 && facts.width < 900, refresh }
}
