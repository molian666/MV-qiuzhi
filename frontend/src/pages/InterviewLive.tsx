/** 面试间：文字模式端到端（M1 会在这里叠加麦克风与实时语音）。 */

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
} from '../api/types'

const SPEAKER_LABEL: Record<string, string> = {
  interviewer: '面试官',
  candidate: '我',
  system: '系统',
}

const STATUS_LABEL: Record<string, string> = {
  live: '进行中',
  finished: '已结束',
  created: '未开始',
  failed: '失败',
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

interface Props {
  onOpenReport?: (sessionId: number) => void
  /** 上报"是否有一场进行中的面试"，供面试间容器决定能不能切换模式 */
  onSessionChange?: (active: boolean) => void
}

export default function InterviewLive({ onOpenReport, onSessionChange }: Props) {
  const [targets, setTargets] = useState<JobTarget[]>([])
  const [resumes, setResumes] = useState<ResumeItem[]>([])
  const [profiles, setProfiles] = useState<ProfileListItem[]>([])
  const [profileId, setProfileId] = useState('')
  // 选中画像的完整策略：用来展示"这场会怎么面你"（而不是只显示一个名字）
  const [profilePolicy, setProfilePolicy] = useState<ProfilePolicy | null>(null)
  const [targetId, setTargetId] = useState('')
  const [resumeId, setResumeId] = useState('')
  const [maxQuestions, setMaxQuestions] = useState(5)
  const [style, setStyle] = useState('标准技术面')
  // M11：压力模式 / 面试官风格 / 连环面 / 语言
  const [options, setOptions] = useState<InterviewOptions | null>(null)
  const [setup, setSetup] = useState<SetupValue>(DEFAULT_SETUP)
  const [session, setSession] = useState<SessionDetail | null>(null)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  // 上一条回答已落库、但面试官回复生成失败：界面给出「重试生成回复」入口，
  // 而不是把人晾在只有输入框的状态（只重试生成，不再重复提交回答）
  const [answerFailed, setAnswerFailed] = useState(false)
  const [creating, setCreating] = useState<'target' | 'resume' | null>(null)
  // M16：简历文件上传（PDF/docx/txt/md）
  const resumeFileRef = useRef<HTMLInputElement>(null)
  const [uploadingResume, setUploadingResume] = useState(false)
  const [targetDraft, setTargetDraft] = useState({ company: '', title: '', jd_text: '' })
  const [resumeDraft, setResumeDraft] = useState({ name: '', content_text: '' })

  const streamRef = useRef<HTMLDivElement>(null)

  const refreshOptions = useCallback(async () => {
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
  }, [])

  useEffect(() => {
    void refreshOptions()
  }, [refreshOptions])

  // 选中画像时把它"会怎么面你"读出来展示——否则用户只看到一个名字，
  // 无法判断节奏学习到底有没有作用到这场模拟上
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

  useEffect(() => {
    streamRef.current?.scrollTo({ top: streamRef.current.scrollHeight, behavior: 'smooth' })
  }, [session])

  const isLive = session?.status === 'live'

  const currentInterviewer = session
    ? interviewerOf(session.turns.filter((turn) => turn.speaker === 'interviewer').slice(-1)[0]?.meta)
    : null

  useEffect(() => {
    api
      .interviewOptions()
      .then(setOptions)
      .catch(() => setOptions(null))
  }, [])

  const startSession = async () => {
    setBusy(true)
    setError('')
    try {
      const created = await api.createSession({
        target_id: targetId ? Number(targetId) : null,
        resume_id: resumeId ? Number(resumeId) : null,
        interviewer_profile_id: profileId ? Number(profileId) : null,
        max_questions: maxQuestions,
        style,
        // M11：压力模式 / 面试官风格 / 连环面 / 语言
        ...setupToPayload(setup),
      })
      setSession(created)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const submitAnswer = async () => {
    if (!session || busy) return
    const text = draft.trim()
    if (!text) return

    const previous = session
    setDraft('')
    setBusy(true)
    setError('')
    setAnswerFailed(false)
    // 乐观上屏：本地后端很快，但"我发的消息立刻出现"对手感影响很大
    setSession({
      ...previous,
      answer_count: previous.answer_count + 1,
      turns: [
        ...previous.turns,
        {
          id: -Date.now(),
          idx: previous.turns.length,
          speaker: 'candidate',
          text,
          kind: 'answer',
          topic: '',
          is_followup: false,
          start_ms: previous.turns.at(-1)?.end_ms ?? null,
          end_ms: null,
          created_at: new Date().toISOString(),
          audio_url: null,
        },
      ],
    })

    try {
      const result = await api.answer(session.id, text)
      setSession(result.session)
    } catch (err) {
      setError(errorMessage(err))
      setAnswerFailed(true)
      // 失败时以服务端状态为准（例如已达到题量上限而返回 409）
      try {
        setSession(await api.getSession(session.id))
      } catch {
        setSession(previous)
      }
    } finally {
      setBusy(false)
    }
  }

  /** 面试官回复生成失败后的重试：只重新生成面试官那条回复，不重复提交我的回答。 */
  const retryInterviewer = async () => {
    if (!session || busy) return
    setBusy(true)
    setError('')
    try {
      const result = await api.retryQuestion(session.id)
      setSession(result.session)
      setAnswerFailed(false)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const finishSession = async () => {
    if (!session) return
    setBusy(true)
    setError('')
    try {
      setSession(await api.finish(session.id))
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const submitTarget = async () => {
    if (!targetDraft.title.trim() && !targetDraft.company.trim()) return
    setBusy(true)
    setError('')
    try {
      const created = await api.createTarget(targetDraft)
      setTargetDraft({ company: '', title: '', jd_text: '' })
      setCreating(null)
      await refreshOptions()
      setTargetId(String(created.id))
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const uploadResumeFile = async (file: File) => {
    setUploadingResume(true)
    setError('')
    try {
      const created = await api.uploadResume(file)
      await refreshOptions()
      setResumeId(String(created.id))
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setUploadingResume(false)
      if (resumeFileRef.current) resumeFileRef.current.value = ''
    }
  }

  const submitResume = async () => {
    if (!resumeDraft.name.trim() && !resumeDraft.content_text.trim()) return
    setBusy(true)
    setError('')
    try {
      const created = await api.createResume({
        name: resumeDraft.name || '我的简历',
        content_text: resumeDraft.content_text,
      })
      setResumeDraft({ name: '', content_text: '' })
      setCreating(null)
      await refreshOptions()
      setResumeId(String(created.id))
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const inputClass =
    'w-full rounded-card border border-ink-2 px-3 py-2 text-sm outline-none focus:border-info disabled:bg-paper'

  return (
    <div className="grid h-full min-h-0 gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
      {/* 手机端：未开始时配置优先（会话区隐藏）；桌面用 order 保持"左会话右配置" */}
      <aside
        className={`min-h-0 space-y-4 overflow-y-auto pr-1 lg:order-2 ${
          session ? 'hidden lg:block' : ''
        }`}
      >
        <div className="rounded-panel border border-ink-1 bg-surface p-4">
          <h3 className="mb-3 text-sm font-semibold text-ink-9">本场配置</h3>

          <label htmlFor="live-target" className="mb-1 block text-xs text-ink-6">目标岗位</label>
          <select
            id="live-target"
            value={targetId}
            onChange={(event) => setTargetId(event.target.value)}
            className={`${inputClass} mb-3`}
          >
            <option value="">不指定（通用面试）</option>
            {targets.map((target) => (
              <option key={target.id} value={target.id}>
                {[target.company, target.title].filter(Boolean).join(' · ') || `岗位 #${target.id}`}
              </option>
            ))}
          </select>

          <div className="mb-1 flex items-center justify-between">
            <label htmlFor="live-resume" className="text-xs text-ink-6">简历</label>
            <button
              type="button"
              onClick={() => resumeFileRef.current?.click()}
              disabled={busy || uploadingResume}
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
          <select
            id="live-resume"
            value={resumeId}
            onChange={(event) => setResumeId(event.target.value)}
            className={`${inputClass} mb-3`}
          >
            <option value="">不指定</option>
            {resumes.map((resume) => (
              <option key={resume.id} value={resume.id}>
                {resume.name || `简历 #${resume.id}`}（v{resume.version}）
              </option>
            ))}
          </select>

          <label htmlFor="live-maxq" className="mb-1 block text-xs text-ink-6">题量上限</label>
          <input
            id="live-maxq"
            type="number"
            min={1}
            max={20}
            value={maxQuestions}
            onChange={(event) => setMaxQuestions(Number(event.target.value) || 1)}
            className={`${inputClass} mb-3`}
          />

          <label htmlFor="live-style" className="mb-1 block text-xs text-ink-6">面试风格</label>
          <input
            id="live-style"
            value={style}
            onChange={(event) => setStyle(event.target.value)}
            className={`${inputClass} mb-3`}
          />

          <label htmlFor="live-profile" className="mb-1 block text-xs text-ink-6">面试官画像（可选）</label>
          <select
            id="live-profile"
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

          <InterviewSetup options={options} value={setup} onChange={setSetup} disabled={busy} />

          <button
            type="button"
            onClick={() => void startSession()}
            disabled={busy}
            className="w-full rounded-card bg-info px-4 py-2 text-sm font-medium text-surface hover:bg-info disabled:opacity-50"
          >
            {busy ? '正在生成开场问题…' : '开始面试'}
          </button>
          {session && (
            <p className="mt-2 text-xs text-ink-6">
              会话 #{session.id} · 提供方 {session.provider}
              {session.plan.pacing ? ` · 画像：${session.plan.pacing.profile_name}` : ''}
            </p>
          )}
        </div>

        <div className="rounded-panel border border-ink-1 bg-surface p-4">
          <h3 className="mb-3 text-sm font-semibold text-ink-9">快速新建</h3>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => setCreating(creating === 'target' ? null : 'target')}
              className="flex-1 rounded-control border border-ink-2 px-3 py-1.5 text-xs text-ink-7 hover:bg-paper"
            >
              岗位 / JD
            </button>
            <button
              type="button"
              onClick={() => setCreating(creating === 'resume' ? null : 'resume')}
              className="flex-1 rounded-control border border-ink-2 px-3 py-1.5 text-xs text-ink-7 hover:bg-paper"
            >
              简历
            </button>
          </div>

          {creating === 'target' && (
            <div className="mt-3 space-y-2">
              <input
                placeholder="公司（可空）"
                value={targetDraft.company}
                onChange={(event) => setTargetDraft({ ...targetDraft, company: event.target.value })}
                className={inputClass}
              />
              <input
                placeholder="岗位名称"
                value={targetDraft.title}
                onChange={(event) => setTargetDraft({ ...targetDraft, title: event.target.value })}
                className={inputClass}
              />
              <textarea
                placeholder="粘贴岗位 JD（M8 会用它做匹配诊断）"
                rows={5}
                value={targetDraft.jd_text}
                onChange={(event) => setTargetDraft({ ...targetDraft, jd_text: event.target.value })}
                className={`${inputClass} resize-none`}
              />
              <button
                type="button"
                onClick={() => void submitTarget()}
                disabled={busy}
                className="w-full btn-primary rounded-control px-3 py-1.5 text-xs"
              >
                保存岗位
              </button>
            </div>
          )}

          {creating === 'resume' && (
            <div className="mt-3 space-y-2">
              <input
                placeholder="简历名称"
                value={resumeDraft.name}
                onChange={(event) => setResumeDraft({ ...resumeDraft, name: event.target.value })}
                className={inputClass}
              />
              <textarea
                placeholder="粘贴简历正文（纯文本即可）"
                rows={6}
                value={resumeDraft.content_text}
                onChange={(event) => setResumeDraft({ ...resumeDraft, content_text: event.target.value })}
                className={`${inputClass} resize-none`}
              />
              <button
                type="button"
                onClick={() => void submitResume()}
                disabled={busy}
                className="w-full btn-primary rounded-control px-3 py-1.5 text-xs"
              >
                保存简历
              </button>
            </div>
          )}
        </div>
      </aside>

      {/* 会话区：手机端开始后才显示；桌面始终在左列 */}
      <section
        className={`${session ? 'flex' : 'hidden lg:flex'} h-full min-h-0 flex-col rounded-panel border border-ink-1 bg-surface lg:order-1`}
      >
        <header className="flex flex-wrap items-center justify-end gap-3 border-b border-ink-1 px-4 py-2.5">
          <div className="flex items-center gap-2">
            {session && (
              <span className="rounded-full bg-sunken px-2.5 py-1 text-xs text-ink-6">
                {STATUS_LABEL[session.status] ?? session.status}
              </span>
            )}
            {isLive && (
              <button
                type="button"
                onClick={() => void finishSession()}
                disabled={busy}
                className="rounded-control border border-ink-2 px-3 py-1.5 text-xs text-ink-7 hover:bg-paper disabled:opacity-50"
              >
                结束本场
              </button>
            )}
            {session && !isLive && (
              <>
                {onOpenReport && (
                  <button
                    type="button"
                    onClick={() => onOpenReport(session.id)}
                    className="rounded-control bg-info px-3 py-1.5 text-xs text-surface hover:bg-info"
                  >
                    查看报告
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setSession(null)}
                  className="btn-primary rounded-control px-3 py-1.5 text-xs"
                >
                  新开一场
                </button>
              </>
            )}
          </div>
        </header>

        {currentInterviewer && (
          <div className="border-b border-info bg-info-soft px-4 py-1.5 text-xs text-info">
            当前面试官：第 {currentInterviewer.index + 1}/{currentInterviewer.total} 位 · {currentInterviewer.label} ·{' '}
            {currentInterviewer.voice_label}
            {currentInterviewer.is_handover && <span className="ml-2 font-medium">（刚换人）</span>}
          </div>
        )}

        <div ref={streamRef} className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
          {!session && (
            <div className="rounded-card border border-dashed border-ink-2 p-6 text-sm leading-relaxed text-ink-6">
              还没有进行中的面试。右侧选好目标岗位与简历后点「开始面试」。
              <br />
              没有岗位或简历也可以先在下方的「快速新建」里录入一份 JD 与简历要点。
            </div>
          )}

          {session?.turns.map((turn) => {
            const isCandidate = turn.speaker === 'candidate'
            // 防御：空内容的面试官回合不渲染（后端已保证不再落空回合，这里兜底，
            // 避免历史库里已有的空回合把界面显示成"只有标签没有内容"）
            if (!isCandidate && !turn.text.trim()) return null
            // M14 教练模式：点评随面试官回合的 meta 落库，文字链路从这里读
            const coach = !isCandidate ? (turn.meta?.coach as CoachInfo | null | undefined) : null
            return (
              <div key={turn.idx} className={isCandidate ? 'flex justify-end' : 'flex justify-start'}>
                <div className="flex max-w-[80%] flex-col items-start gap-1.5">
                  <div
                    className={
                      isCandidate
                        ? 'rounded-panel rounded-br-control bg-info px-4 py-2 text-sm text-surface'
                        : 'rounded-panel rounded-bl-control bg-sunken px-4 py-2 text-sm text-ink-9'
                    }
                  >
                    <div
                      className={
                        isCandidate
                          ? 'mb-1 flex flex-wrap items-baseline gap-x-1.5 text-[11px] opacity-80'
                          : 'mb-1 flex flex-wrap items-baseline gap-x-1.5 text-[11px] text-ink-6'
                      }
                    >
                      <span>{SPEAKER_LABEL[turn.speaker] ?? turn.speaker}</span>
                      {turn.kind === 'followup' && (
                        <span className="rounded-control bg-ink-9/10 px-1">追问</span>
                      )}
                      {turn.kind === 'scene' && (
                        <span className="rounded-control bg-ng-soft px-1 text-ng">突发场景</span>
                      )}
                      {turn.topic && <span className="text-ink-4">{turn.topic}</span>}
                    </div>
                    <div className="whitespace-pre-wrap leading-relaxed">{turn.text}</div>
                  </div>
                  {coach && <CoachCard coach={coach} />}
                </div>
              </div>
            )
          })}

          {busy && <p className="text-xs text-ink-4">面试官正在思考…</p>}
        </div>

        {/* M22：pb-safe——iPhone Home 条不占 padding，底部输入条会被手势区压住 */}
        <footer className="pb-safe border-t border-ink-1 px-4 py-3">
          {error && (
            <div className="mb-2 flex flex-wrap items-center gap-2 rounded-control bg-ng-soft px-3 py-2 text-xs text-ng">
              <span className="min-w-0 flex-1">{error}</span>
              {answerFailed && isLive && (
                <button
                  type="button"
                  onClick={() => void retryInterviewer()}
                  disabled={busy}
                  className="rounded-control bg-info px-2.5 py-1 font-medium text-surface hover:bg-info disabled:opacity-50"
                >
                  {busy ? '正在重试…' : '重试生成面试官回复'}
                </button>
              )}
            </div>
          )}
          <div className="flex items-end gap-2">
            <textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault()
                  void submitAnswer()
                }
              }}
              disabled={!isLive || busy}
              rows={3}
              placeholder={isLive ? '输入你的回答；Enter 发送，Shift+Enter 换行' : '先开始一场面试'}
              className={`${inputClass} resize-none`}
            />
            <button
              type="button"
              onClick={() => void submitAnswer()}
              disabled={!isLive || busy || !draft.trim()}
              className="btn-primary rounded-control px-4 py-2 text-sm font-medium"
            >
              发送
            </button>
          </div>
        </footer>
      </section>
    </div>
  )
}
