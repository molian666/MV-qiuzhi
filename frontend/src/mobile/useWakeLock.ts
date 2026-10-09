/**
 * 屏幕常亮（M19 手机端）。
 *
 * 为什么必须有：手机默认 30 秒黑屏，而"思考一道题"很容易超过这个时间。
 * 一旦黑屏，浏览器会挂起页面 → 麦克风轨道失效、WebSocket 断开 →
 * 用户回来看到的是"页面还在但什么都不动"。这个体验比直接报错更糟
 * （见 `api/ws.ts` 的重连与 `InterviewVoice.tsx` 的可见性恢复，那两条是兜底）。
 *
 * 两个现实约束，都要如实告诉用户而不是静默失败：
 * 1. **iOS 低电量模式会拒绝 Wake Lock**；
 * 2. 页面切到后台时浏览器会**自动释放**锁，回到前台必须重新申请。
 */

import { useCallback, useEffect, useRef, useState } from 'react'

export type WakeLockState = 'unsupported' | 'idle' | 'active' | 'denied'

export interface WakeLockHandle {
  state: WakeLockState
  /** 现在能不能申请（不支持时为 false，UI 据此只提示"别锁屏"） */
  supported: boolean
  request: () => Promise<void>
  release: () => Promise<void>
}

export function useWakeLock(): WakeLockHandle {
  const supported =
    typeof navigator !== 'undefined' && 'wakeLock' in navigator
  const [state, setState] = useState<WakeLockState>(supported ? 'idle' : 'unsupported')
  const sentinelRef = useRef<{ release: () => Promise<void> } | null>(null)

  const release = useCallback(async () => {
    const sentinel = sentinelRef.current
    sentinelRef.current = null
    if (sentinel) {
      try {
        await sentinel.release()
      } catch {
        // 页面已经在后台时 release 可能抛错，忽略
      }
    }
    setState((previous) => (previous === 'unsupported' ? previous : 'idle'))
  }, [])

  const request = useCallback(async () => {
    if (!supported) {
      setState('unsupported')
      return
    }
    if (sentinelRef.current) {
      setState('active')
      return
    }
    try {
      const wakeLock = (navigator as unknown as {
        wakeLock: { request: (type: 'screen') => Promise<{ release: () => Promise<void> }> }
      }).wakeLock
      const sentinel = await wakeLock.request('screen')
      sentinelRef.current = sentinel
      setState('active')
    } catch {
      // iOS 低电量模式、或权限策略拒绝
      setState('denied')
    }
  }, [supported])

  useEffect(() => {
    if (!supported) {
      return
    }
    const onVisibility = () => {
      // 回到前台时锁已被系统释放，需要重新申请（这是规范行为，不是 bug）
      if (document.visibilityState === 'visible' && sentinelRef.current === null) {
        void request()
      }
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [request, supported])

  useEffect(
    () => () => {
      void release()
    },
    [release],
  )

  return { state, supported, request, release }
}
