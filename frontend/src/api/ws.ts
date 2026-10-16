/**
 * 语音会话的 WebSocket 客户端。
 *
 * 事件类型与后端 `app/realtime/session_runtime.py` 的下行事件一一对应；
 * 所有事件都带 `seq`，前端据此忽略重复事件（断线重连时有用）。
 */

import type { CoachInfo } from './types'
import { ReconnectPlan } from './backoff'

export type AudioMode = 'client' | 'server'

export interface StateEvent {
  type: 'state'
  session_id: number
  audio_mode: AudioMode
  speech_provider: string
  text_fallback: boolean
  input_sample_rate: number
  silence_ms: number
  seq: number
}

export interface InterviewerTextEvent {
  type: 'interviewer_text'
  turn_id: number
  idx: number
  text: string
  kind: string
  topic: string
  is_followup: boolean
  /** M14 教练模式：上一题点评（null = 本场没开教练或本题无点评） */
  coach?: CoachInfo | null
  seq: number
}

export interface CandidateTurnEvent {
  type: 'candidate_turn'
  turn_id: number
  idx: number
  text: string
  start_ms: number
  end_ms: number
  source: string
  seq: number
}

export interface AsrEvent {
  type: 'asr_partial' | 'asr_final'
  text: string
  seq: number
  /** 术语纠错记录：ASR 把「亚像素」听成「亚相素」时，这里说明改了什么 */
  corrections?: { wrong: string; right: string }[]
}

export interface SpeechStateEvent {
  type: 'speech_state'
  state: 'speech_started' | 'speech_stopped'
  at_ms: number
  seq: number
}

export interface BargeInEvent {
  type: 'barge_in'
  at_ms?: number
  ack?: boolean
  seq: number
}

export interface AudioChunkEvent {
  type: 'audio_chunk'
  pcm: string
  sample_rate: number
  seq: number
}

export interface MetricsEvent {
  type: 'metrics'
  audio_ms: number
  speaking: boolean
  threshold: number
  seq: number
}

export interface TurnMetaEvent {
  type: 'turn_meta'
  question_count: number
  answer_count: number
  max_questions: number
  audio_ms: number
  seq: number
}

export interface AwaitingTextEvent {
  type: 'awaiting_text'
  message: string
  reason?: string
  seq: number
}

export interface FinishedEvent {
  type: 'finished'
  session_id: number
  manual: boolean
  audio_ms: number
  audio_available: boolean
  seq: number
}

export interface ErrorEvent {
  type: 'error'
  message: string
  seq: number
}

export interface PongEvent {
  type: 'pong'
  seq: number
}

export interface PressureEvent {
  type: 'pressure'
  /** 每个下行事件都带 seq，前端据此去重与续接 */
  seq: number
  /** interrupt=面试官插话；time_limit=这题时间到了 */
  mode: 'interrupt' | 'time_limit'
  elapsed_ms: number
  message: string
  limit_seconds?: number
  after_ms?: number
}

/**
 * 沉默看门狗：候选人一段时间没说话。
 *
 * `nudge` 只是提醒一次（"还在吗"），`advance` 表示已超时、这一轮被收掉并进入下一题。
 * 前端要把它显示出来——**不然用户会以为程序卡住了**，而这恰恰是这个功能要解决的问题。
 */
export interface SilenceEvent {
  type: 'silence'
  seq: number
  action: 'nudge' | 'advance'
  elapsed_ms: number
  message: string
}

/**
 * 进入收尾阶段：轮到候选人提问。
 *
 * 这时候会话仍然是 live——**收尾不是结束**。前端要提示他可以提问，
 * 否则他只会看到一个没有反应的界面（而这个阶段的提问正是评分里"主动性"的依据）。
 */
export interface AwaitingQuestionEvent {
  type: 'awaiting_question'
  seq: number
  message: string
}

export type ServerEvent =
  | StateEvent
  | InterviewerTextEvent
  | CandidateTurnEvent
  | AsrEvent
  | SpeechStateEvent
  | BargeInEvent
  | AudioChunkEvent
  | MetricsEvent
  | TurnMetaEvent
  | AwaitingTextEvent
  | FinishedEvent
  | PressureEvent
  | SilenceEvent
  | AwaitingQuestionEvent
  | ErrorEvent
  | PongEvent

export interface SocketHandlers {
  onEvent: (event: ServerEvent) => void
  onOpen?: () => void
  /**
   * 连接被关闭。`code` 用于诊断：1000=正常关闭；1001=页面离开/导航；
   * 1005/1006=异常断开（没收到关闭帧，多为网络层被掐断）；其他见 RFC 6455。
   */
  onClose?: (code?: number) => void
  /**
   * 连接状态变化（M19 手机端）。
   *
   * `reconnecting` 时**会话还在服务端继续跑**，只是这条管道断了；UI 必须把这件事
   * 显示出来，否则用户会以为程序卡死（这正是手机切后台最常见的表现）。
   */
  onStatus?: (status: SocketStatus) => void
}

/** 连接状态。`reconnecting`/`disconnected` 都要在界面上如实呈现。 */
export type SocketStatus =
  | 'connecting'
  | 'connected'
  | 'reconnecting'
  | 'disconnected'

function toBase64(pcm: Int16Array): string {
  const bytes = new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength)
  let binary = ''
  const step = 0x2000 // 分块拼接，避免超长字符串一次性构造
  for (let offset = 0; offset < bytes.length; offset += step) {
    const slice = bytes.subarray(offset, Math.min(offset + step, bytes.length))
    binary += String.fromCharCode(...slice)
  }
  return btoa(binary)
}

function fromBase64(encoded: string): Int16Array {
  const binary = atob(encoded)
  const bytes = new Uint8Array(binary.length)
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index)
  }
  return new Int16Array(bytes.buffer)
}

export class SessionSocket {
  private readonly sessionId: number
  private readonly handlers: SocketHandlers
  private socket: WebSocket | null = null
  private lastSeq = 0
  /** 断线自动重连的计划（延迟与次数由 backoff.ts 决定，这里只管什么时候用） */
  private readonly plan = new ReconnectPlan()
  private retryTimer: ReturnType<typeof setTimeout> | null = null
  /** 用户主动关闭（或会话已结束）：不再自动重连 */
  private manualClose = false
  private status: SocketStatus = 'connecting'
  /** 最后一次收到下行消息的时间。心跳据此判断连接是否"假死"。 */
  private lastMessageAt = 0
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null
  /** 应用层心跳：服务端 runtime 收到 `{type:'ping'}` 会回 pong（见 session_runtime.handle）。
   *  语音面试是长连接，光靠服务端 ping 不够——手机切后台/换网时 TCP 可能半开，
   *  服务端感知不到，前端必须自己能发现并重连。
   *
   *  间隔刻意压到 2 秒：手机侧连接曾在「收到 state 后空闲约 2 秒」被网络层掐断
   *  （路由器 NAT 空闲超时 / 手机省电策略，断开无关闭帧、服务端记 1005）。
   *  高频心跳让连接永不空闲，空闲超时型掐断就无从下手；代价只是每分钟约
   *  30 条几字节的消息，可以忽略。 */
  private static readonly HEARTBEAT_MS = 2_000
  /** 超过这么久没收到任何下行消息（含 pong）就判定假死，主动关连接走退避重连。 */
  private static readonly STALE_MS = 10_000

  constructor(sessionId: number, handlers: SocketHandlers) {
    this.sessionId = sessionId
    this.handlers = handlers
  }

  get connected(): boolean {
    return this.socket?.readyState === WebSocket.OPEN
  }

  get connectionStatus(): SocketStatus {
    return this.status
  }

  /** 用户是否主动关闭过（或会话已结束）：为 true 时外部不应再自动重连。 */
  get manuallyClosed(): boolean {
    return this.manualClose
  }

  private setStatus(status: SocketStatus): void {
    if (this.status === status) {
      return
    }
    this.status = status
    this.handlers.onStatus?.(status)
  }

  connect(): Promise<void> {
    this.manualClose = false
    // 外部主动重启时，清掉可能还挂着的退避 timer（锁屏期间 timer 被冻结，
    // 解冻后可能迟到触发 → 双开连接；这里先清掉，防双开）。
    if (this.retryTimer !== null) {
      clearTimeout(this.retryTimer)
      this.retryTimer = null
    }
    return this.openSocket()
  }

  private openSocket(): Promise<void> {
    const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const url = `${scheme}://${window.location.host}/ws/sessions/${this.sessionId}`
    this.setStatus(this.plan.attempt > 0 ? 'reconnecting' : 'connecting')

    return new Promise((resolve, reject) => {
      const socket = new WebSocket(url)
      this.socket = socket
      let settled = false

      socket.onopen = () => {
        settled = true
        this.plan.reset()
        this.setStatus('connected')
        this.lastMessageAt = Date.now()
        this.startHeartbeat()
        this.handlers.onOpen?.()
        resolve()
      }
      socket.onerror = () => {
        if (!settled) {
          settled = true
          reject(new Error('WebSocket 连接失败：请确认后端已启动'))
        }
      }
      socket.onclose = (event: CloseEvent) => {
        this.stopHeartbeat()
        this.handlers.onClose?.(event.code)
        if (this.manualClose) {
          this.setStatus('disconnected')
          return
        }
        this.scheduleReconnect()
      }
      socket.onmessage = (event: MessageEvent<string>) => {
        // 任何下行消息都说明连接活着，刷新心跳基准
        this.lastMessageAt = Date.now()
        let parsed: ServerEvent
        try {
          parsed = JSON.parse(event.data) as ServerEvent
        } catch {
          return
        }
        // 去重：同一 seq 只处理一次（断线续接时可能重复下发）
        if (typeof parsed.seq === 'number') {
          if (parsed.seq <= this.lastSeq) {
            return
          }
          this.lastSeq = parsed.seq
        }
        this.handlers.onEvent(parsed)
      }
    })
  }

  /**
   * 退避重连。
   *
   * 注意 `lastSeq` **刻意不清零**：重连后服务端若把之前的事件重发一遍，
   * 去重会挡住它们，不会让界面出现重复的问答。
   */
  private scheduleReconnect(): void {
    const delay = this.plan.next()
    if (delay === null) {
      // 重试用尽：交给 UI 明确提示"请手动重连"，不假装还在连
      this.setStatus('disconnected')
      return
    }
    this.setStatus('reconnecting')
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null
      if (this.manualClose) {
        return
      }
      void this.openSocket().catch(() => {
        // 连不上就再排一次（onclose 也会走到这里，两条路都收敛到退避）
        if (!this.manualClose) {
          this.scheduleReconnect()
        }
      })
    }, delay)
  }

  /**
   * 应用层心跳循环：每 HEARTBEAT_MS 发一次 ping；若 STALE_MS 内没收到任何下行消息，
   * 判定连接假死并主动关闭（onclose 会走退避重连）。
   */
  private startHeartbeat(): void {
    this.stopHeartbeat()
    // 连上就立刻打一个 ping：如果掐断发生在"空闲约 2 秒"，第一个心跳不能等
    // HEARTBEAT_MS（2 秒）之后才发，否则还是留了接近 2 秒的空档。
    this.send({ type: 'ping' })
    this.heartbeatTimer = setInterval(() => {
      this.send({ type: 'ping' })
      if (Date.now() - this.lastMessageAt > SessionSocket.STALE_MS) {
        // 传 1000 规范关闭：服务端能区分"心跳判死"与"网络层被掐"（后者是 1005/1006）
        this.socket?.close(1000, 'stale')
      }
    }, SessionSocket.HEARTBEAT_MS)
  }

  private stopHeartbeat(): void {
    if (this.heartbeatTimer !== null) {
      clearInterval(this.heartbeatTimer)
      this.heartbeatTimer = null
    }
  }

  sendAudio(pcm: Int16Array): void {
    this.send({ type: 'audio_append', pcm: toBase64(pcm) })
  }

  sendText(text: string): void {
    this.send({ type: 'text', text })
  }

  /** 用户点"我答完了"：立刻收口这一轮。 */
  endTurn(): void {
    this.send({ type: 'control', action: 'end_turn' })
  }

  /** 用户主动打断面试官。 */
  interrupt(): void {
    this.send({ type: 'interrupt' })
  }

  finish(): void {
    this.send({ type: 'finish' })
  }

  close(): void {
    this.manualClose = true
    this.stopHeartbeat()
    if (this.retryTimer !== null) {
      clearTimeout(this.retryTimer)
      this.retryTimer = null
    }
    this.socket?.close()
    this.socket = null
    this.setStatus('disconnected')
  }

  static decodeAudio(event: AudioChunkEvent): { pcm: Int16Array; sampleRate: number } {
    return { pcm: fromBase64(event.pcm), sampleRate: event.sample_rate }
  }

  private send(payload: Record<string, unknown>): void {
    if (this.socket === null || this.socket.readyState !== WebSocket.OPEN) {
      return
    }
    this.socket.send(JSON.stringify(payload))
  }
}
