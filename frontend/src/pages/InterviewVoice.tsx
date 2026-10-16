/**
 * 语音面试间（M1）。
 *
 * 与文字面试间共用同一套后端落库与编排，只是传输层换成 WebSocket + 音频。
 * 刻意做成独立页面：语音链路的状态机（连接 / 录音 / 播报 / 打断）比文字复杂得多，
 * 混在一个组件里两边都会难维护。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import InterviewSetup, { DEFAULT_SETUP, interviewerOf, setupToPayload, type SetupValue } from '../components/InterviewSetup'
import ProfilePreview from '../components/ProfilePreview'
import CoachCard from '../components/CoachCard'
import type {
  CoachInfo,
  InterviewOptions,
  JobTarget,
  ProfileListItem,
  ProfilePolicy,
  ResumeItem,
  SessionDetail,
  Turn,
} from '../api/types'
import type { ClientInfo } from '../api/types'
import type { ServerEvent, SocketStatus } from '../api/ws'
import { SessionSocket } from '../api/ws'
import { BargeInDetector, type BargeInOptions } from '../audio/bargein'
import { startMicCapture } from '../audio/mic'
import type { MicCapture } from '../audio/mic'
import { AgentSpeaker } from '../audio/player'
import { thinkingPauseMs } from '../audio/thinking'
import { unlockAudio } from '../audio/unlock'
import PhonePreflight from '../components/PhonePreflight'
import { useDevice } from '../mobile/useDevice'
import { useWakeLock } from '../mobile/useWakeLock'

const SPEAKER_LABEL: Record<string, string> = {
  interviewer: '面试官',
  candidate: '我',
  system: '系统',
}

/**
 * 本地打断（barge-in）判定（M19 改造）。
 *
 * 原来这里是一个固定阈值 `0.02`，那是按笔记本调出来的。手机上不成立：
 * 扬声器与麦克风相距不到 1cm，面试官的声音会被自己收进去 —— 固定阈值会
 * "面试官一说话就把自己打断"。改成按底噪自适应 + 冷却窗，
 * 判定逻辑本身在 `audio/bargein.ts` 里（可单测），这里只负责接麦克风。
 *
 * 用 `useRef` 而不是模块级单例：模块级会跨页面实例共享状态，
 * 而底噪估计是"这一场、这台设备"的事——新开一场必须从零开始估。
 */
function useBargeInDetector(options: BargeInOptions = {}) {
  const ref = useRef<BargeInDetector | null>(null)
  if (ref.current === null) {
    ref.current = new BargeInDetector(options)
  }
  return ref.current
}

/**
 * 麦克风"静默"多久算被系统掐了（毫秒）。
 *
 * 40ms 一个分片，正常说话时远小于这个值；安静时前端 VAD 也仍在收分片
 * （`onChunk` 是按聚合窗口回调的，与有没有声音无关），所以 1.5 秒没有分片＝真的断了。
 */
const MIC_STALL_MS = 1500

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function nowIso(): string {
  return new Date().toISOString()
}

interface Props {
  onOpenReport?: (sessionId: number) => void
  /** 上报"是否有一场进行中的面试"，供面试间容器决定能不能切换模式 */
  onSessionChange?: (active: boolean) => void
}

export default function InterviewVoice({ onOpenReport, onSessionChange }: Props) {
  const [targets, setTargets] = useState<JobTarget[]>([])
  const [resumes, setResumes] = useState<ResumeItem[]>([])
  const [profiles, setProfiles] = useState<ProfileListItem[]>([])
  const [profileId, setProfileId] = useState('')
  // 选中画像的策略：展示"这场会怎么面你"，也让"节奏学习真的生效了"看得见
  const [profilePolicy, setProfilePolicy] = useState<ProfilePolicy | null>(null)
  const [targetId, setTargetId] = useState('')
  const [resumeId, setResumeId] = useState('')
  const [maxQuestions, setMaxQuestions] = useState(4)
  // M11：压力模式 / 面试官风格 / 连环面 / 语言
  const [options, setOptions] = useState<InterviewOptions | null>(null)
  const [setup, setSetup] = useState<SetupValue>(DEFAULT_SETUP)
  const [pressureNote, setPressureNote] = useState('')

  const [session, setSession] = useState<SessionDetail | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [connected, setConnected] = useState(false)
  const [recording, setRecording] = useState(false)
  const [level, setLevel] = useState(0)
  const [partial, setPartial] = useState('')
  /** 术语纠错记录：ASR 把「亚像素」听成「亚相素」时，界面要说清改了什么 */
  const [corrections, setCorrections] = useState<{ wrong: string; right: string }[]>([])
  const [status, setStatus] = useState('未开始')
  const [awaitingText, setAwaitingText] = useState('')
  const [audioMode, setAudioMode] = useState<'client' | 'server'>('client')
  const [textFallback, setTextFallback] = useState(true)
  const [audioAvailable, setAudioAvailable] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [draft, setDraft] = useState('')
  // M16：简历文件上传（PDF/docx/txt/md）
  const resumeFileRef = useRef<HTMLInputElement>(null)
  const [uploadingResume, setUploadingResume] = useState(false)

  const socketRef = useRef<SessionSocket | null>(null)
  const micRef = useRef<MicCapture | null>(null)
  const speakerRef = useRef<AgentSpeaker>(new AgentSpeaker())
  //: 上一轮候选人的回答：用来算面试官开口前的"思考停顿"（见 audio/thinking.ts）
  const lastAnswerRef = useRef('')
  const streamRef = useRef<HTMLDivElement>(null)
  const sessionIdRef = useRef<number | null>(null)

  // ---- M19 手机端：设备能力、唤醒锁、连接状态、服务端体检 ----
  const device = useDevice()
  const wakeLock = useWakeLock()
  const bargeIn = useBargeInDetector()
  const [info, setInfo] = useState<ClientInfo | null>(null)
  const [infoError, setInfoError] = useState('')
  const [socketStatus, setSocketStatus] = useState<SocketStatus>('disconnected')
  /** 最近一次断开的原因码（RFC 6455）。1005/1006=网络层被掐；1000=正常；1001=页面离开。 */
  const [disconnectCode, setDisconnectCode] = useState<number | null>(null)
  /** 从后台回来时的恢复结果，直接显示给用户（不要静默） */
  const [recoveryNote, setRecoveryNote] = useState('')
  /** 正在恢复中：避免 visibilitychange 连发时重复跑恢复流程 */
  const recoveringRef = useRef(false)
  /**
   * 最近一次收到音频分片的时刻（毫秒）。
   *
   * 用它判断"麦克风是不是被系统掐了"——比猜 `MediaStreamTrack.readyState` 可靠：
   * 手机上切后台后轨道常常仍是 `live`，但**再也不产生数据**，从轨道状态看不出来。
   */
  const lastChunkAtRef = useRef(Date.now())

  const appendTurn = useCallback((turn: Turn) => {
    setTurns((previous) => (previous.some((item) => item.idx === turn.idx) ? previous : [...previous, turn]))
  }, [])

  const currentInterviewer = interviewerOf(
    turns.filter((turn) => turn.speaker === 'interviewer').slice(-1)[0]?.meta,
  )

  useEffect(() => {
    api
      .interviewOptions()
      .then(setOptions)
      .catch(() => setOptions(null))
  }, [])

  // M19：读服务端的部署形态，给 PhonePreflight 用。
  // 这个端点是免鉴权的（BACKEND auth.PUBLIC_PATHS），所以登录页附近也能拿到。
  useEffect(() => {
    let cancelled = false
    api
      .clientInfo()
      .then((value) => {
        if (!cancelled) {
          setInfo(value)
          setInfoError('')
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setInfoError(errorMessage(err))
        }
      })
    return () => {
      cancelled = true
    }
  }, [])

  // 选中画像时读出它的策略：展示"这场会怎么面你"，让学习效果可见
  useEffect(() => {
    if (!profileId) {
      setProfilePolicy(null)
      return
    }
    let cancelled = false
    api
      .getProfile(Number(profileId))
      .then((detail) => {
        if (!cancelled) {
          setProfilePolicy(detail.policy)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setProfilePolicy(null)
        }
      })
    return () => {
      cancelled = true
    }
  }, [profileId])

  // 上报会话状态：面试间容器据此决定能否切换文字/语音模式
  useEffect(() => {
    onSessionChange?.(session !== null)
  }, [session, onSessionChange])

  const handleEvent = useCallback(
    (event: ServerEvent) => {
      switch (event.type) {
        case 'pressure':
          // M11：压力模式到点了 —— 服务端已经结束了这一回合，这里只负责说清楚为什么
          setPressureNote(event.message)
          setStatus(event.mode === 'interrupt' ? '面试官插话了' : '这题时间到了')
          break

        case 'silence':
          // 沉默看门狗：必须显示出来，**否则用户会以为程序卡住了**——
          // 而"卡住"正是这个功能要解决的问题。
          setPressureNote(event.message)
          setStatus(event.action === 'nudge' ? '还没听到你说话' : '这一题先跳过，进入下一题')
          break

        case 'awaiting_question':
          // 收尾阶段：轮到他提问。会话仍是 live，所以输入框要能继续用。
          setPressureNote(event.message)
          setStatus('轮到你提问了')
          break

        case 'state':
          setAudioMode(event.audio_mode)
          setTextFallback(event.text_fallback)
          speakerRef.current.setMode(event.audio_mode)
          setStatus(
            event.text_fallback ? '已连接：未接入语音识别，回答请用文字提交（音频照常录制）' : '已连接，点「开始说话」就行',
          )
          break

        case 'interviewer_text':
          appendTurn({
            id: event.turn_id,
            idx: event.idx,
            speaker: 'interviewer',
            text: event.text,
            kind: event.kind,
            topic: event.topic,
            is_followup: event.is_followup,
            start_ms: null,
            end_ms: null,
            created_at: nowIso(),
            audio_url: null,
            // M14 教练模式：语音链路通过 WS 事件下发点评，装进 meta 与文字链路同构
            meta: event.coach ? { coach: event.coach } : undefined,
          })
          speakerRef.current.speak(event.text, thinkingPauseMs(lastAnswerRef.current))
          setStatus('面试官正在提问')
          break

        case 'candidate_turn':
          appendTurn({
            id: event.turn_id,
            idx: event.idx,
            speaker: 'candidate',
            text: event.text,
            kind: 'answer',
            topic: '',
            is_followup: false,
            start_ms: event.start_ms,
            end_ms: event.end_ms,
            created_at: nowIso(),
            audio_url: null,
          })
          setPartial('')
          setAwaitingText('')
          // 记下这次回答：下一题的"思考停顿"按它的长度算（长回答想一下更自然）
          lastAnswerRef.current = event.text
          break

        case 'asr_partial':
          setPartial(event.text)
          break

        case 'asr_final':
          setPartial('')
          setCorrections(event.corrections ?? [])
          break

        case 'speech_state':
          // 本地已经先停了，这里是兜底
          if (event.state === 'speech_started') {
            speakerRef.current.stop()
          }
          break

        case 'barge_in':
          if (!event.ack) {
            speakerRef.current.stop()
          }
          break

        case 'audio_chunk': {
          const { pcm, sampleRate } = SessionSocket.decodeAudio(event)
          speakerRef.current.play(pcm, sampleRate)
          break
        }

        case 'metrics':
          setStatus(event.speaking ? '正在听你说…' : '等待你说话…')
          break

        case 'awaiting_text':
          setAwaitingText(event.message)
          break

        case 'finished': {
          setStatus('面试已结束')
          setAudioAvailable(event.audio_available)
          speakerRef.current.stop()
          setRecording(false)
          setLevel(0)
          const mic = micRef.current
          micRef.current = null
          if (mic) {
            void mic.stop()
          }
          const id = sessionIdRef.current
          if (id !== null) {
            void api
              .getSession(id)
              .then((detail) => {
                setSession(detail)
                setTurns(detail.turns)
              })
              .catch(() => undefined)
          }
          break
        }

        case 'error':
          setError(event.message)
          break

        case 'pong':
        case 'turn_meta':
          break
      }
    },
    [appendTurn],
  )

  useEffect(() => {
    void (async () => {
      try {
        const [targetList, resumeList, profileList] = await Promise.all([
          api.listTargets(),
          api.listResumes(),
          api.listProfiles(),
        ])
        setTargets(targetList)
        setResumes(resumeList)
        setProfiles(profileList)
        setTargetId((current) => current || (targetList[0] ? String(targetList[0].id) : ''))
        setResumeId((current) => current || (resumeList[0] ? String(resumeList[0].id) : ''))
      } catch (err) {
        setError(errorMessage(err))
      }
    })()
  }, [])

  useEffect(() => {
    streamRef.current?.scrollTo({ top: streamRef.current.scrollHeight, behavior: 'smooth' })
  }, [turns, partial])

  // 卸载时务必收掉麦克风与连接，否则麦克风指示灯会一直亮着
  useEffect(
    () => () => {
      const mic = micRef.current
      micRef.current = null
      if (mic) {
        void mic.stop()
      }
      socketRef.current?.close()
      speakerRef.current.stop()
    },
    [],
  )

  // M19：有会话时就申请屏幕常亮。手机 30 秒黑屏会直接掐掉麦克风与 WebSocket，
  // 而"思考一道题"很容易超过 30 秒。iOS 低电量模式会拒绝，失败也不拦流程，
  // 只在界面上提示"别锁屏"（见 useWakeLock 的 denied 分支）。
  useEffect(() => {
    if (session === null) {
      void wakeLock.release()
      return
    }
    void wakeLock.request()
  }, [session, wakeLock])

  /**
   * M19：从后台/锁屏回来时的恢复。
   *
   * 手机切到别的 App 再切回来，会发生三件**互不相同**的事，必须逐个检查：
   * 1. WebSocket 断开 → **先恢复连接**（锁屏期间退避 timer 被冻结，回来要主动重启，
   *    且恢复麦克风依赖连接状态，顺序必须在最前）；
   * 2. 麦克风轨道被系统挂起 → 重新 getUserMedia（权限已授予时不会二次弹窗）；
   * 3. `AudioContext` 被暂停 → 面试官的声音会静默消失（`unlockAudio` 会 resume）。
   *
   * **关键是把结果说出来**。静默地"看起来连上了但什么都不动"，比直接报错更糟——
   * 用户会以为是自己操作错了。
   *
   * 依赖只放 `session` / `recording`：把 `startRecording` 放进依赖会让这个 effect
   * 每次渲染都重挂监听（它们每次渲染都是新函数），而监听器内部读到的
   * 也是当次渲染的闭包——那才是真正会出 stale 问题的地方。
   */
  useEffect(() => {
    if (typeof document === 'undefined') {
      return
    }
    const onVisibility = () => {
      if (document.visibilityState !== 'visible' || session === null) {
        return
      }
      if (recoveringRef.current) {
        return
      }
      recoveringRef.current = true
      void (async () => {
        const notes: string[] = []
        try {
          // 1) 连接：**必须先恢复管道再碰麦克风**。
          //    麦克风的 startRecording 用连接状态把关——若先重启麦克风，此刻 WS
          //    还没连上（connected=false），恢复会被门禁静默吞掉，之后没人再触发，
          //    表现为"熄屏回来连上了却没法继续说话"。
          //    'reconnecting' 也立即重启：锁屏期间退避 timer 被冻结，解冻后不保证
          //    会执行，不能干等它。
          if (socketRef.current !== null && !socketRef.current.connected) {
            const socket = socketRef.current
            const canRestart =
              (socket.connectionStatus === 'disconnected' ||
                socket.connectionStatus === 'reconnecting') &&
              !socket.manuallyClosed
            if (canRestart) {
              // connect() 在 onopen 时 resolve；加 8s 兜底，握手卡住不拖住后面的恢复
              await Promise.race([
                socket.connect(),
                new Promise<void>((resolve) => setTimeout(resolve, 8_000)),
              ]).catch(() => {})
              notes.push('已重新连接服务（这一场面试还在继续）')
            } else {
              notes.push('正在重连服务（这一场面试还在继续）')
            }
          }

          // 2) 麦克风：看"最近还有没有音频分片上来"，而不是猜轨道状态
          //    （此时连接已恢复，startRecording 的门禁能放行）
          if (recording && micRef.current !== null) {
            const silentFor = Date.now() - lastChunkAtRef.current
            if (silentFor > MIC_STALL_MS) {
              await stopRecording()
              await startRecording()
              notes.push('麦克风已重新连接')
            }
          }

          // 3) 面试官的播放链路
          const unlocked = unlockAudio()
          if (unlocked.context !== null && unlocked.context.state !== 'running') {
            notes.push('音频输出被系统暂停了，若听不到面试官说话请点一下屏幕')
          }

          // 4) 常亮锁在切后台时会被系统释放，回来要重新申请
          await wakeLock.request()
        } catch (err) {
          notes.push(`恢复失败：${errorMessage(err)}`)
        } finally {
          recoveringRef.current = false
        }
        setRecoveryNote(notes.join('；'))
      })()
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      document.removeEventListener('visibilitychange', onVisibility)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session, recording, wakeLock])

  /**
   * 退避用尽后的自动恢复。
   *
   * SessionSocket 的重连计划（8 次）在"手机锁屏/切后台后连接反复被系统掐掉"
   * 时会用尽并停在 disconnected。这里在 disconnected 且会话仍 live 时，
   * 隔几秒重启一次连接循环——否则用户回来只能看到"已断开"卡死。
   * 用户主动关闭（manuallyClosed）或会话已结束时不重启。
   */
  useEffect(() => {
    if (socketStatus !== 'disconnected' || session === null || session.status === 'finished') {
      return
    }
    const timer = setTimeout(() => {
      const socket = socketRef.current
      if (socket !== null && !socket.manuallyClosed) {
        void socket.connect()
      }
    }, 5_000)
    return () => clearTimeout(timer)
  }, [socketStatus, session])

  const uploadResumeFile = async (file: File) => {
    setUploadingResume(true)
    setError('')
    try {
      const created = await api.uploadResume(file)
      const resumeList = await api.listResumes()
      setResumes(resumeList)
      setResumeId(String(created.id))
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setUploadingResume(false)
      if (resumeFileRef.current) resumeFileRef.current.value = ''
    }
  }

  const startSession = async () => {
    setBusy(true)
    setError('')
    setRecoveryNote('')
    // M19：**必须在用户手势里**先解锁音频。iOS 上 AudioContext 与 speechSynthesis
    // 只有在手势调用栈里才会生效，否则表现是"面试官不出声"且不报错。
    // 这个按钮就是最自然的手势点。
    const unlocked = unlockAudio()
    if (unlocked.skipped) {
      setRecoveryNote(`音频能力受限：${unlocked.skipped}`)
    }
    try {
      const created = await api.createSession({
        target_id: targetId ? Number(targetId) : null,
        resume_id: resumeId ? Number(resumeId) : null,
        interviewer_profile_id: profileId ? Number(profileId) : null,
        max_questions: maxQuestions,
        mode: 'realtime',
        // M11：压力模式 / 面试官风格 / 连环面 / 语言
        ...setupToPayload(setup),
      })
      setSession(created)
      setTurns(created.turns)
      setAudioAvailable(false)
      sessionIdRef.current = created.id
      speakerRef.current.stop()

      const socket = new SessionSocket(created.id, {
        onEvent: handleEvent,
        // M19：连接状态要显示出来。手机切后台/换网一定会断，
        // 静默重连会让用户以为程序坏了。
        onStatus: setSocketStatus,
        onClose: (code) => {
          setConnected(false)
          setDisconnectCode(code ?? null)
        },
        // 关键：**每次（重）连上都要恢复 connected**。以前只在 startSession 里
        // 置一次 true，断线重连成功后界面仍显示"已断开"，而且 startRecording
        // 用 connected 把关，用户回来根本没法继续说话——"重连上了却恢复不了"
        // 就是这里断的。
        onOpen: () => {
          setConnected(true)
          setDisconnectCode(null)
        },
      })
      socketRef.current = socket
      await socket.connect()
      setConnected(true)
      setStatus('已连接')
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const startRecording = async () => {
    // 门禁用**真实 socket 状态**而非 React 的 connected：
    // 熄屏回来恢复时 setConnected 是异步的（下一渲染才生效），而 socket.connected
    // 是同步的真值——recovery 里 await connect() 完成后立刻就能放行。
    if (recording || socketRef.current === null || !socketRef.current.connected) {
      return
    }
    setError('')
    // 再解锁一次：用户可能先点了"开始面试"、又隔了一会儿才点"开始说话"
    unlockAudio()
    try {
      // 换输入设备/重新采集时底噪要重新估，否则上一台设备的底噪会污染阈值
      bargeIn.reset()
      lastChunkAtRef.current = Date.now()
      const mic = await startMicCapture({
        onChunk: (pcm) => {
          lastChunkAtRef.current = Date.now()
          socketRef.current?.sendAudio(pcm)
        },
        onLevel: (value) => {
          setLevel(value)
          // 底噪估计**一直**更新：只在"面试官说话时"采样会把他的声音当成环境噪声
          bargeIn.observe(value)
          // 本地打断：用户一开口就压掉面试官的声音（毫秒级，比等后端 barge_in 快）。
          // 阈值是自适应的，避免手机外放时面试官把自己打断（见 audio/bargein.ts）。
          if (speakerRef.current.speaking && bargeIn.shouldTrigger(value, performance.now())) {
            bargeIn.markTriggered(performance.now())
            speakerRef.current.stop()
          }
        },
      })
      micRef.current = mic
      setRecording(true)
      setStatus('正在录音')
    } catch (err) {
      setError(`无法访问麦克风：${errorMessage(err)}`)
    }
  }

  const stopRecording = async () => {
    const mic = micRef.current
    micRef.current = null
    setRecording(false)
    setLevel(0)
    if (mic) {
      await mic.stop()
    }
  }

  const endTurn = () => {
    socketRef.current?.endTurn()
  }

  const interrupt = () => {
    speakerRef.current.stop()
    socketRef.current?.interrupt()
    setStatus('已打断，接着说吧')
  }

  const finishSession = async () => {
    socketRef.current?.finish()
    await stopRecording()
    speakerRef.current.stop()
  }

  const submitText = () => {
    const text = draft.trim()
    if (!text || socketRef.current === null || !socketRef.current.connected) {
      return
    }
    setDraft('')
    setAwaitingText('')
    socketRef.current?.sendText(text)
  }

  const isLive = session?.status === 'live' && connected

  // M19：连接状态要**如实**显示，包括"正在重连"。
  // 手机切后台/换网一定会断；如果界面只写"已连接"，用户看到的是一动不动却"看起来正常"的页面。
  const connectionLabel =
    socketStatus === 'connected' && connected
      ? '已连接'
      : socketStatus === 'reconnecting'
        ? '重连中…'
        : socketStatus === 'connecting'
          ? '连接中…'
          : session !== null
            ? `已断开${disconnectCode !== null ? `（断开码 ${disconnectCode}）` : ''}`
            : '未连接'
  const connectionOk = socketStatus === 'connected' && connected

  const buttonBase = 'rounded-control px-3 py-1.5 text-xs font-medium disabled:opacity-40'
  const inputClass = 'w-full rounded-card border border-ink-2 px-3 py-2 text-sm outline-none focus:border-info'

  return (
    <div className="grid h-full min-h-0 gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
      {/* 会话区：手机端开始后才显示；桌面始终在左列 */}
      <section
        className={`${session ? 'flex' : 'hidden lg:flex'} h-full min-h-0 flex-col rounded-panel border border-ink-1 bg-surface lg:order-1`}
      >
        <header className="flex flex-wrap items-center justify-end gap-3 border-b border-ink-1 px-4 py-2.5">
          <div className="flex items-center gap-2">
            <span
              className={
                connectionOk
                  ? 'rounded-full bg-ok-soft px-2.5 py-1 text-xs text-ok'
                  : socketStatus === 'reconnecting' || socketStatus === 'connecting'
                    ? 'rounded-full bg-warn-soft px-2.5 py-1 text-xs text-warn'
                    : 'rounded-full bg-sunken px-2.5 py-1 text-xs text-ink-6'
              }
              title={
                socketStatus === 'reconnecting'
                  ? '连接断了，正在自动重连；这一场面试在服务端仍在继续'
                  : undefined
              }
            >
              {connectionLabel}
            </span>
            {isLive && (
              <button type="button" onClick={() => void finishSession()} className={`${buttonBase} border border-ink-2 text-ink-7 hover:bg-paper`}>
                结束并保存
              </button>
            )}
          </div>
        </header>

        {/* 设备体检：未开始时显示完整卡片；面试开始后让位给聊天流——
            手机上体检卡（尤其语音播报不可用时）会展开占掉 200px+，
            把聊天流挤成一行高，长问题只能看到头一句。 */}
        {session === null ? (
          <div className="border-b border-ink-1 px-3 py-1.5">
            <PhonePreflight device={device} info={info} error={infoError} />
          </div>
        ) : (
          <div className="border-b border-ink-1 px-4 py-1.5 text-[11px] text-ink-6">
            面试进行中 ·{' '}
            {device.hasSpeechSynthesis
              ? '面试官语音：浏览器播报'
              : '语音播报不可用，面试官问题会以文字显示，看文字回答即可'}
          </div>
        )}

        {recoveryNote && (
          <div className="border-b border-warn bg-warn-soft px-4 py-1.5 text-xs text-warn">
            {recoveryNote}
            <button type="button" className="ml-2 underline" onClick={() => setRecoveryNote('')}>
              知道了
            </button>
          </div>
        )}

        {(currentInterviewer || pressureNote) && (
          <div className="space-y-1 border-b border-info bg-info-soft px-4 py-1.5 text-xs text-info">
            {currentInterviewer && (
              <div>
                当前面试官：第 {currentInterviewer.index + 1}/{currentInterviewer.total} 位 ·{' '}
                {currentInterviewer.label} · {currentInterviewer.voice_label}
                {currentInterviewer.is_handover && <span className="ml-2 font-medium">（刚换人）</span>}
              </div>
            )}
            {pressureNote && (
              <div className="text-ng">
                {pressureNote}
                <button type="button" className="ml-2 underline" onClick={() => setPressureNote('')}>
                  知道了
                </button>
              </div>
            )}
          </div>
        )}

        {/* 手机上聊天流至少要有能放下两三行问题的空间，不能全被体检卡/操作条挤没 */}
        <div ref={streamRef} className="min-h-40 flex-1 space-y-3 overflow-y-auto px-4 py-4">
          {turns.length === 0 && (
            <div className="rounded-card border border-dashed border-ink-2 p-6 text-sm leading-relaxed text-ink-6">
              语音面试：面试官会用浏览器朗读提问，你对着麦克风回答。
              <br />
              首次点击「开始说话」时浏览器会请求麦克风权限。
            </div>
          )}

          {turns.map((turn) => {
            const isCandidate = turn.speaker === 'candidate'
            // M14 教练模式：点评装在 meta 里，与文字链路同构
            const coach = !isCandidate ? (turn.meta?.coach as CoachInfo | null | undefined) : null
            return (
              <div key={turn.idx} className={isCandidate ? 'flex justify-end' : 'flex justify-start'}>
                <div className="flex max-w-[80%] flex-col items-start gap-1.5">
                  <div
                    className={
                      isCandidate
                        ? 'rounded-2xl rounded-br-sm bg-info px-4 py-2 text-sm text-surface'
                        : 'rounded-2xl rounded-bl-sm bg-sunken px-4 py-2 text-sm text-ink-9'
                    }
                  >
                    <div className={isCandidate ? 'mb-1 text-[11px] opacity-75' : 'mb-1 flex flex-wrap items-baseline gap-x-1.5 text-[11px] text-ink-6'}>
                      <span>{SPEAKER_LABEL[turn.speaker] ?? turn.speaker}</span>
                      {turn.kind === 'followup' && <span className="rounded-control bg-ink-9/10 px-1">追问</span>}
                      {turn.kind === 'scene' && <span className="rounded-control bg-ng-soft px-1 text-ng">突发场景</span>}
                      {turn.topic && <span className="text-ink-4">· {turn.topic}</span>}
                    </div>
                    <div className="whitespace-pre-wrap leading-relaxed">{turn.text}</div>
                  </div>
                  {coach && <CoachCard coach={coach} />}
                </div>
              </div>
            )
          })}

          {partial && (
            <div className="flex justify-end">
              <div className="max-w-[80%] rounded-panel rounded-br-control border border-dashed border-info px-4 py-2 text-sm text-info">
                {partial}
              </div>
            </div>
          )}

          {/* 纠错提示：说清系统改了哪几个字。
              静默替换是没法核对的，用户有权知道自己的话被怎么处理了。 */}
          {corrections.length > 0 && (
            <div className="rounded-control bg-paper px-3 py-1.5 text-[11px] leading-relaxed text-ink-6">
              <span className="text-ink-4">已按机器视觉名词表纠正：</span>
              {corrections.map((item) => (
                <span key={`${item.wrong}-${item.right}`} className="ml-2 font-mono">
                  {item.wrong} → {item.right}
                </span>
              ))}
            </div>
          )}
        </div>

        {/* M22：pb-safe——iPhone 的 Home 条会占掉底部一段，操作条不加安全区
             padding，底部按钮就会被手势区压住一半、点不准 */}
        <footer className="pb-safe space-y-3 border-t border-ink-1 px-4 py-3">
          {error && <p className="rounded-control bg-ng-soft px-3 py-2 text-xs text-ng">{error}</p>}
          {awaitingText && <p className="rounded-control bg-warn-soft px-3 py-2 text-xs text-warn">{awaitingText}</p>}

          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => (recording ? void stopRecording() : void startRecording())}
              disabled={!isLive}
              className={`${buttonBase} touch-target ${recording ? 'bg-ng text-surface hover:bg-ng/90' : 'bg-ink-9 text-surface hover:bg-ink-7'}`}
            >
              {recording ? '暂停收音' : '开始说话'}
            </button>
            <button
              type="button"
              onClick={endTurn}
              disabled={!isLive}
              className={`${buttonBase} touch-target border border-ink-2 text-ink-7 hover:bg-paper`}
            >
              我答完了
            </button>
            <button
              type="button"
              onClick={interrupt}
              disabled={!isLive}
              className={`${buttonBase} border border-ink-2 text-ink-7 hover:bg-paper`}
            >
              打断面试官
            </button>

            <div className="ml-auto flex items-center gap-2">
              <span className="text-[11px] text-ink-6">麦克风</span>
              <div className="h-2 w-28 overflow-hidden rounded-full bg-ink-1">
                <div
                  className={recording ? 'h-full bg-ok transition-all' : 'h-full bg-ink-2'}
                  style={{ width: `${Math.min(100, Math.round(level * 400))}%` }}
                />
              </div>
            </div>
          </div>

          <p className="text-[11px] text-ink-6">
            状态：{status}
            {audioMode === 'client' ? ' · 面试官语音：浏览器播报' : ' · 面试官语音：服务端流'}
            {textFallback ? ' · 转写：文字替身' : ' · 转写：实时语音识别'}
          </p>

          <div className="flex items-end gap-2">
            <textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault()
                  submitText()
                }
              }}
              disabled={!isLive}
              rows={2}
              placeholder={textFallback ? '这里输入你的回答（Mock 模式下的转写替身）' : '也可以直接打字作答；Enter 提交'}
              className={`${inputClass} resize-none`}
            />
            <button
              type="button"
              onClick={submitText}
              disabled={!isLive || !draft.trim()}
              className="btn-primary rounded-control px-4 py-2 text-sm font-medium"
            >
              提交
            </button>
          </div>

          {session !== null && session.status === 'finished' && (
            <div className="space-y-2 rounded-card bg-paper px-3 py-2">
              {audioAvailable ? (
                <div className="flex items-center gap-3">
                  <span className="text-xs text-ink-6">本场录音</span>
                  <audio controls src={`/api/sessions/${session.id}/audio`} className="h-8 w-full max-w-md" />
                </div>
              ) : (
                <p className="text-xs text-ink-6">本场没有音频（可能一直在文字模式作答）。</p>
              )}
              {onOpenReport && (
                <button
                  type="button"
                  onClick={() => onOpenReport(session.id)}
                  className="rounded-control bg-info px-3 py-1.5 text-xs font-medium text-surface hover:bg-info"
                >
                  去看评分报告
                </button>
              )}
            </div>
          )}
        </footer>
      </section>

      {/* 配置区：手机端未开始时优先显示（含设备体检）；桌面始终在右列 */}
      <aside
        className={`min-h-0 space-y-4 overflow-y-auto pr-1 lg:order-2 ${
          session ? 'hidden lg:block' : ''
        }`}
      >
        {/* 手机端未开始时，把设备体检也放在配置上方（会话区此时隐藏） */}
        <div className="lg:hidden">
          {session === null && <PhonePreflight device={device} info={info} error={infoError} />}
        </div>

        <div className="rounded-panel border border-ink-1 bg-surface p-4">
          <h3 className="mb-3 text-sm font-semibold text-ink-9">本场配置</h3>

          <label className="mb-1 block text-xs text-ink-6">目标岗位</label>
          <select value={targetId} onChange={(event) => setTargetId(event.target.value)} className={`${inputClass} mb-3`}>
            <option value="">不指定（通用面试）</option>
            {targets.map((target) => (
              <option key={target.id} value={target.id}>
                {[target.company, target.title].filter(Boolean).join(' · ') || `岗位 #${target.id}`}
              </option>
            ))}
          </select>

          <div className="mb-1 flex items-center justify-between">
            <label className="text-xs text-ink-6">简历</label>
            <button
              type="button"
              onClick={() => resumeFileRef.current?.click()}
              disabled={busy || connected || uploadingResume}
              className="rounded-control border border-ink-2 px-2 py-0.5 text-[11px] text-ink-6 hover:bg-paper disabled:opacity-50"
            >
              {uploadingResume ? '解析中…' : '上传文件'}
            </button>
          </div>
          <input
            ref={resumeFileRef}
            type="file"
            accept=".pdf,.docx,.txt,.md"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0]
              if (file) void uploadResumeFile(file)
            }}
          />
          <select value={resumeId} onChange={(event) => setResumeId(event.target.value)} className={`${inputClass} mb-3`}>
            <option value="">不指定</option>
            {resumes.map((resume) => (
              <option key={resume.id} value={resume.id}>
                {resume.name || `简历 #${resume.id}`}（v{resume.version}）
              </option>
            ))}
          </select>

          <label className="mb-1 block text-xs text-ink-6">题量上限</label>
          <input
            type="number"
            min={1}
            max={20}
            value={maxQuestions}
            onChange={(event) => setMaxQuestions(Number(event.target.value) || 1)}
            className={`${inputClass} mb-3`}
          />

          <label className="mb-1 block text-xs text-ink-6">面试官画像（可选）</label>
          <select
            aria-label="面试官画像"
            value={profileId}
            onChange={(event) => setProfileId(event.target.value)}
            className={`${inputClass} mb-3`}
          >
            <option value="">默认节奏</option>
            {profiles.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}（{item.question_count} 题 · {item.stress_level || '—'}）
              </option>
            ))}
          </select>

          {profilePolicy && (
            <div className="mb-3">
              <ProfilePreview policy={profilePolicy} />
            </div>
          )}

          <InterviewSetup options={options} value={setup} onChange={setSetup} disabled={busy || connected} />

          <button
            type="button"
            onClick={() => void startSession()}
            disabled={busy || connected}
            className="w-full rounded-card bg-info px-4 py-2 text-sm font-medium text-surface hover:bg-info disabled:opacity-50"
          >
            {connected ? '进行中…' : busy ? '正在生成开场问题…' : '开始语音面试'}
          </button>
          {connected && (
            <button
              type="button"
              onClick={() => {
                socketRef.current?.close()
                void stopRecording()
                speakerRef.current.stop()
                setSession(null)
                setTurns([])
                setConnected(false)
                setStatus('未开始')
              }}
              className="mt-2 w-full rounded-card border border-ink-2 px-4 py-2 text-xs text-ink-7 hover:bg-paper"
            >
              断开并新开一场
            </button>
          )}
        </div>

        <div className="rounded-panel border border-ink-1 bg-surface p-4 text-xs leading-relaxed text-ink-6">
          <h3 className="mb-2 text-sm font-semibold text-ink-9">怎么工作</h3>
          <p>音频在本地采集为 pcm16@16k，经 WebSocket 送到后端；后端做 VAD（判断你什么时候说完）并把每回合切片存成 WAV。</p>
          <p className="mt-2">你开口时面试官的声音会立刻停掉（打断），服务端也会收到打断事件。</p>
          <p className="mt-2">想接真实语音识别：在后端 .env 里设 SPEECH_PROVIDER=qwen 并填 DASHSCOPE_API_KEY。</p>
        </div>
      </aside>
    </div>
  )
}
