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
import type { InterviewOptions, JobTarget, ProfileListItem, ResumeItem, SessionDetail, Turn } from '../api/types'
import type { ServerEvent } from '../api/ws'
import { SessionSocket } from '../api/ws'
import { startMicCapture } from '../audio/mic'
import type { MicCapture } from '../audio/mic'
import { AgentSpeaker } from '../audio/player'

const SPEAKER_LABEL: Record<string, string> = {
  interviewer: '面试官',
  candidate: '我',
  system: '系统',
}

/** 本地打断阈值：超过这个音量就认为用户在说话（与后端 VAD 的 min_energy 同量级） */
const BARGE_IN_LEVEL = 0.02

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function nowIso(): string {
  return new Date().toISOString()
}

interface Props {
  onOpenReport?: (sessionId: number) => void
}

export default function InterviewVoice({ onOpenReport }: Props) {
  const [targets, setTargets] = useState<JobTarget[]>([])
  const [resumes, setResumes] = useState<ResumeItem[]>([])
  const [profiles, setProfiles] = useState<ProfileListItem[]>([])
  const [profileId, setProfileId] = useState('')
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
  const [status, setStatus] = useState('未开始')
  const [awaitingText, setAwaitingText] = useState('')
  const [audioMode, setAudioMode] = useState<'client' | 'server'>('client')
  const [textFallback, setTextFallback] = useState(true)
  const [audioAvailable, setAudioAvailable] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [draft, setDraft] = useState('')

  const socketRef = useRef<SessionSocket | null>(null)
  const micRef = useRef<MicCapture | null>(null)
  const speakerRef = useRef<AgentSpeaker>(new AgentSpeaker())
  const streamRef = useRef<HTMLDivElement>(null)
  const sessionIdRef = useRef<number | null>(null)

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

  const handleEvent = useCallback(
    (event: ServerEvent) => {
      switch (event.type) {
        case 'pressure':
          // M11：压力模式到点了 —— 服务端已经结束了这一回合，这里只负责说清楚为什么
          setPressureNote(event.message)
          setStatus(event.mode === 'interrupt' ? '面试官插话了' : '这题时间到了')
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
          })
          speakerRef.current.speak(event.text)
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
          break

        case 'asr_partial':
          setPartial(event.text)
          break

        case 'asr_final':
          setPartial('')
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

  const startSession = async () => {
    setBusy(true)
    setError('')
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
        onClose: () => {
          setConnected(false)
          setStatus('连接已断开')
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
    if (recording || !connected) {
      return
    }
    setError('')
    try {
      const mic = await startMicCapture({
        onChunk: (pcm) => socketRef.current?.sendAudio(pcm),
        onLevel: (value) => {
          setLevel(value)
          // 本地打断：用户一开口就压掉面试官的声音（毫秒级，比等后端 barge_in 快）
          if (value > BARGE_IN_LEVEL && speakerRef.current.speaking) {
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
    if (!text || !connected) {
      return
    }
    setDraft('')
    setAwaitingText('')
    socketRef.current?.sendText(text)
  }

  const isLive = session?.status === 'live' && connected

  const buttonBase = 'rounded-control px-3 py-1.5 text-xs font-medium disabled:opacity-40'
  const inputClass = 'w-full rounded-card border border-ink-2 px-3 py-2 text-sm outline-none focus:border-info'

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
      <section className="flex min-h-[70vh] flex-col rounded-panel border border-ink-1 bg-surface">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-1 px-4 py-3">
          <div>
            <h2 className="text-base font-semibold text-ink-9">语音面试间</h2>
            <p className="text-xs text-ink-6">
              {session ? `会话 #${session.id} · ${session.target_label || '通用面试'}` : '右侧选好岗位与简历，然后开始'}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span
              className={
                connected
                  ? 'rounded-full bg-ok-soft px-2.5 py-1 text-xs text-ok'
                  : 'rounded-full bg-sunken px-2.5 py-1 text-xs text-ink-6'
              }
            >
              {connected ? '已连接' : '未连接'}
            </span>
            {isLive && (
              <button type="button" onClick={() => void finishSession()} className={`${buttonBase} border border-ink-2 text-ink-7 hover:bg-paper`}>
                结束并保存
              </button>
            )}
          </div>
        </header>

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

        <div ref={streamRef} className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
          {turns.length === 0 && (
            <div className="rounded-card border border-dashed border-ink-2 p-6 text-sm leading-relaxed text-ink-6">
              语音面试：面试官会用浏览器朗读提问，你对着麦克风回答。
              <br />
              首次点击「开始说话」时浏览器会请求麦克风权限。
            </div>
          )}

          {turns.map((turn) => {
            const isCandidate = turn.speaker === 'candidate'
            return (
              <div key={turn.idx} className={isCandidate ? 'flex justify-end' : 'flex justify-start'}>
                <div
                  className={
                    isCandidate
                      ? 'max-w-[80%] rounded-2xl rounded-br-sm bg-info px-4 py-2 text-sm text-surface'
                      : 'max-w-[80%] rounded-2xl rounded-bl-sm bg-sunken px-4 py-2 text-sm text-ink-9'
                  }
                >
                  <div className={isCandidate ? 'mb-1 text-[11px] opacity-75' : 'mb-1 text-[11px] text-ink-6'}>
                    {SPEAKER_LABEL[turn.speaker] ?? turn.speaker}
                    {turn.kind === 'followup' ? ' · 追问' : ''}
                    {turn.topic ? ` · ${turn.topic}` : ''}
                  </div>
                  <div className="whitespace-pre-wrap leading-relaxed">{turn.text}</div>
                </div>
              </div>
            )
          })}

          {partial && (
            <div className="flex justify-end">
              <div className="max-w-[80%] rounded-2xl rounded-br-sm border border-dashed border-info px-4 py-2 text-sm text-info">
                {partial}
              </div>
            </div>
          )}
        </div>

        <footer className="space-y-3 border-t border-ink-1 px-4 py-3">
          {error && <p className="rounded-control bg-ng-soft px-3 py-2 text-xs text-ng">{error}</p>}
          {awaitingText && <p className="rounded-control bg-warn-soft px-3 py-2 text-xs text-warn">{awaitingText}</p>}

          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => (recording ? void stopRecording() : void startRecording())}
              disabled={!isLive}
              className={`${buttonBase} ${recording ? 'bg-ng text-surface hover:bg-ng' : 'bg-ink-9 text-surface hover:bg-ink-9'}`}
            >
              {recording ? '暂停收音' : '开始说话'}
            </button>
            <button
              type="button"
              onClick={endTurn}
              disabled={!isLive}
              className={`${buttonBase} border border-ink-2 text-ink-7 hover:bg-paper`}
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
              className="rounded-card bg-ink-9 px-4 py-2 text-sm font-medium text-surface hover:bg-ink-9 disabled:opacity-40"
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

      <aside className="space-y-4">
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

          <label className="mb-1 block text-xs text-ink-6">简历</label>
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

          <InterviewSetup options={options} value={setup} onChange={setSetup} disabled={busy || connected} />

          <button
            type="button"
            onClick={() => void startSession()}
            disabled={busy || connected}
            className="w-full rounded-card bg-info px-4 py-2 text-sm font-medium text-surface hover:bg-info disabled:opacity-50"
          >
            {connected ? '进行中…' : '开始语音面试'}
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
