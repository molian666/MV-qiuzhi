/** 面试间：文字模式端到端（M1 会在这里叠加麦克风与实时语音）。 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { JobTarget, ResumeItem, SessionDetail } from '../api/types'

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

export default function InterviewLive() {
  const [targets, setTargets] = useState<JobTarget[]>([])
  const [resumes, setResumes] = useState<ResumeItem[]>([])
  const [targetId, setTargetId] = useState('')
  const [resumeId, setResumeId] = useState('')
  const [maxQuestions, setMaxQuestions] = useState(5)
  const [style, setStyle] = useState('标准技术面')
  const [session, setSession] = useState<SessionDetail | null>(null)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [creating, setCreating] = useState<'target' | 'resume' | null>(null)
  const [targetDraft, setTargetDraft] = useState({ company: '', title: '', jd_text: '' })
  const [resumeDraft, setResumeDraft] = useState({ name: '', content_text: '' })

  const streamRef = useRef<HTMLDivElement>(null)

  const refreshOptions = useCallback(async () => {
    try {
      const [targetList, resumeList] = await Promise.all([api.listTargets(), api.listResumes()])
      setTargets(targetList)
      setResumes(resumeList)
      setTargetId((current) => current || (targetList[0] ? String(targetList[0].id) : ''))
      setResumeId((current) => current || (resumeList[0] ? String(resumeList[0].id) : ''))
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [])

  useEffect(() => {
    void refreshOptions()
  }, [refreshOptions])

  useEffect(() => {
    streamRef.current?.scrollTo({ top: streamRef.current.scrollHeight, behavior: 'smooth' })
  }, [session])

  const isLive = session?.status === 'live'
  const maxQ = session?.plan.max_questions ?? maxQuestions

  const startSession = async () => {
    setBusy(true)
    setError('')
    try {
      const created = await api.createSession({
        target_id: targetId ? Number(targetId) : null,
        resume_id: resumeId ? Number(resumeId) : null,
        max_questions: maxQuestions,
        style,
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
    'w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-sky-500 disabled:bg-slate-50'

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
      <section className="flex min-h-[70vh] flex-col rounded-xl border border-slate-200 bg-white shadow-sm">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-4 py-3">
          <div>
            <h2 className="text-base font-semibold text-slate-800">面试间（文字模式）</h2>
            <p className="text-xs text-slate-500">
              {session
                ? `${session.target_label || '未指定岗位'} · 已问 ${session.question_count}/${maxQ} 题`
                : '在右侧选好岗位与简历，然后开始'}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {session && (
              <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-600">
                {STATUS_LABEL[session.status] ?? session.status}
              </span>
            )}
            {isLive && (
              <button
                type="button"
                onClick={() => void finishSession()}
                disabled={busy}
                className="rounded-md border border-slate-300 px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-50 disabled:opacity-50"
              >
                结束本场
              </button>
            )}
            {session && !isLive && (
              <button
                type="button"
                onClick={() => setSession(null)}
                className="rounded-md bg-slate-900 px-3 py-1.5 text-xs text-white hover:bg-slate-800"
              >
                新开一场
              </button>
            )}
          </div>
        </header>

        <div ref={streamRef} className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
          {!session && (
            <div className="rounded-lg border border-dashed border-slate-300 p-6 text-sm leading-relaxed text-slate-500">
              还没有进行中的面试。右侧选好目标岗位与简历后点「开始面试」。
              <br />
              没有岗位或简历也可以先在下方的「快速新建」里录入一份 JD 与简历要点。
            </div>
          )}

          {session?.turns.map((turn) => {
            const isCandidate = turn.speaker === 'candidate'
            return (
              <div key={turn.idx} className={isCandidate ? 'flex justify-end' : 'flex justify-start'}>
                <div
                  className={
                    isCandidate
                      ? 'max-w-[80%] rounded-2xl rounded-br-sm bg-sky-600 px-4 py-2 text-sm text-white'
                      : 'max-w-[80%] rounded-2xl rounded-bl-sm bg-slate-100 px-4 py-2 text-sm text-slate-800'
                  }
                >
                  <div className={isCandidate ? 'mb-1 text-[11px] opacity-75' : 'mb-1 text-[11px] text-slate-500'}>
                    {SPEAKER_LABEL[turn.speaker] ?? turn.speaker}
                    {turn.kind === 'followup' ? ' · 追问' : ''}
                    {turn.topic ? ` · ${turn.topic}` : ''}
                  </div>
                  <div className="whitespace-pre-wrap leading-relaxed">{turn.text}</div>
                </div>
              </div>
            )
          })}

          {busy && <p className="text-xs text-slate-400">面试官正在思考…</p>}
        </div>

        <footer className="border-t border-slate-200 px-4 py-3">
          {error && (
            <p className="mb-2 rounded-md bg-rose-50 px-3 py-2 text-xs text-rose-700">{error}</p>
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
              className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-40"
            >
              发送
            </button>
          </div>
        </footer>
      </section>

      <aside className="space-y-4">
        <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <h3 className="mb-3 text-sm font-semibold text-slate-800">本场配置</h3>

          <label className="mb-1 block text-xs text-slate-500">目标岗位</label>
          <select
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

          <label className="mb-1 block text-xs text-slate-500">简历</label>
          <select
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

          <label className="mb-1 block text-xs text-slate-500">题量上限</label>
          <input
            type="number"
            min={1}
            max={20}
            value={maxQuestions}
            onChange={(event) => setMaxQuestions(Number(event.target.value) || 1)}
            className={`${inputClass} mb-3`}
          />

          <label className="mb-1 block text-xs text-slate-500">面试风格</label>
          <input
            value={style}
            onChange={(event) => setStyle(event.target.value)}
            className={`${inputClass} mb-3`}
          />

          <button
            type="button"
            onClick={() => void startSession()}
            disabled={busy}
            className="w-full rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-700 disabled:opacity-50"
          >
            开始面试
          </button>
          {session && (
            <p className="mt-2 text-xs text-slate-500">
              会话 #{session.id} · 提供方 {session.provider}
            </p>
          )}
        </div>

        <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <h3 className="mb-3 text-sm font-semibold text-slate-800">快速新建</h3>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => setCreating(creating === 'target' ? null : 'target')}
              className="flex-1 rounded-md border border-slate-300 px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-50"
            >
              岗位 / JD
            </button>
            <button
              type="button"
              onClick={() => setCreating(creating === 'resume' ? null : 'resume')}
              className="flex-1 rounded-md border border-slate-300 px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-50"
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
                className="w-full rounded-md bg-slate-900 px-3 py-1.5 text-xs text-white hover:bg-slate-800 disabled:opacity-50"
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
                className="w-full rounded-md bg-slate-900 px-3 py-1.5 text-xs text-white hover:bg-slate-800 disabled:opacity-50"
              >
                保存简历
              </button>
            </div>
          )}
        </div>
      </aside>
    </div>
  )
}
