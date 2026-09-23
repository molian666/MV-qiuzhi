/** 故事与话术（M8）：STAR 故事卡、自我介绍（含计时练习）、敏感题、反问清单。
 *
 * 这些内容在模拟面试里是**会被真正引用**的：面试官追问时会用到故事卡里的细节，
 * 所以这里编辑的不只是"一段文字"，而是面试的事实底稿。
 */

import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import type { AnswerDraft, AnswerMeta, ResumeItem, StoryCardItem, StoryList } from '../api/types'

const SAMPLE_JD = `岗位职责：负责核心业务系统的后端设计与开发；参与高并发场景下的性能优化。
任职要求：必须精通 Python，熟悉 Redis、MySQL；有 Kafka 使用经验；了解 Kubernetes 者优先。`

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function formatSeconds(value: number): string {
  const minutes = Math.floor(value / 60)
  const seconds = value % 60
  return `${minutes}:${String(seconds).padStart(2, '0')}`
}

export default function SpeechKit() {
  const [meta, setMeta] = useState<AnswerMeta | null>(null)
  const [resumes, setResumes] = useState<ResumeItem[]>([])
  const [resumeId, setResumeId] = useState<number | null>(null)

  const [stories, setStories] = useState<StoryList | null>(null)
  const [drafts, setDrafts] = useState<AnswerDraft[]>([])
  const [jd, setJd] = useState('')
  const [company, setCompany] = useState('')

  const [introSeconds, setIntroSeconds] = useState(60)
  const [intro, setIntro] = useState<AnswerDraft | null>(null)
  const [sensitiveKind, setSensitiveKind] = useState('leave')
  const [sensitive, setSensitive] = useState<AnswerDraft | null>(null)
  const [questions, setQuestions] = useState<AnswerDraft | null>(null)

  const [elapsed, setElapsed] = useState(0)
  const [running, setRunning] = useState(false)

  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    try {
      const [nextMeta, nextResumes, nextStories, nextDrafts] = await Promise.all([
        api.answerMeta(),
        api.listResumes(),
        api.listStories(),
        api.listAnswers(),
      ])
      setMeta(nextMeta)
      setResumes(nextResumes)
      setResumeId((current) => current ?? nextResumes[0]?.id ?? null)
      setStories(nextStories)
      setDrafts(nextDrafts)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  // 计时练习：按目标时长练到不用看稿
  useEffect(() => {
    if (!running) {
      return
    }
    const timer = window.setInterval(() => setElapsed((value) => value + 1), 1000)
    return () => window.clearInterval(timer)
  }, [running])

  const run = async (task: () => Promise<void>) => {
    setBusy(true)
    setError('')
    try {
      await task()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const generateStories = () =>
    run(async () => {
      const created = await api.generateStories(resumeId, 4)
      if (created.length === 0) {
        setError('这次没抽出新故事卡（可能是简历里的经历都已经成卡了）')
      }
      setStories(await api.listStories())
    })

  const toggleStar = (card: StoryCardItem) =>
    run(async () => {
      await api.updateStory(card.id, { ...card, starred: !card.starred })
      setStories(await api.listStories())
    })

  const removeStory = (card: StoryCardItem) =>
    run(async () => {
      await api.deleteStory(card.id)
      setStories(await api.listStories())
    })

  const composeIntro = () =>
    run(async () => {
      const draft = await api.composeSelfIntro({ resume_id: resumeId, seconds: introSeconds, jd_text: jd })
      setIntro(draft)
      setElapsed(0)
      setRunning(false)
      setDrafts(await api.listAnswers())
    })

  const composeSensitive = () =>
    run(async () => {
      const draft = await api.composeSensitive({ kind: sensitiveKind, resume_id: resumeId, jd_text: jd })
      setSensitive(draft)
      setDrafts(await api.listAnswers())
    })

  const composeQuestions = () =>
    run(async () => {
      if (!jd.trim()) {
        setError('反问清单要先有 JD，才能问出跟岗位相关的问题')
        return
      }
      const draft = await api.composeQuestions({ jd_text: jd, company, resume_id: resumeId, count: 5 })
      setQuestions(draft)
      setDrafts(await api.listAnswers())
    })

  const target = intro?.target_seconds ?? introSeconds
  const introDelta = elapsed - target
  const sensitiveMeta = meta?.sensitive.find((item) => item.key === sensitiveKind)

  return (
    <div className="space-y-4">
      {error && (
        <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</div>
      )}

      {/* 共用上下文 */}
      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <h2 className="text-sm font-semibold">素材来源</h2>
        <p className="mt-0.5 text-xs text-slate-500">
          故事卡与话术都从你的简历长出来；填上 JD 会让自我介绍和反问更贴这个岗位。
        </p>
        <div className="mt-3 grid gap-3 lg:grid-cols-3">
          <label className="text-xs text-slate-600">
            简历
            <select
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
              value={resumeId ?? ''}
              onChange={(event) => setResumeId(Number(event.target.value))}
            >
              <option value="">（未选择）</option>
              {resumes.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name} v{item.version}
                </option>
              ))}
            </select>
          </label>
          <label className="text-xs text-slate-600">
            公司（可选）
            <input
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
              value={company}
              onChange={(event) => setCompany(event.target.value)}
            />
          </label>
          <label className="text-xs text-slate-600">
            JD（可选）
            <textarea
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
              rows={3}
              placeholder="粘一段 JD，自我介绍与反问会更有针对性"
              value={jd}
              onChange={(event) => setJd(event.target.value)}
            />
          </label>
        </div>
        <button
          type="button"
          onClick={() => setJd(SAMPLE_JD)}
          className="mt-2 rounded-md border border-slate-200 px-2.5 py-1 text-xs text-slate-500 hover:bg-slate-50"
        >
          填入示例 JD
        </button>
      </section>

      {/* 故事卡 */}
      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold">STAR 故事卡</h2>
            <p className="mt-0.5 text-xs text-slate-500">
              模拟面试里「讲一个你最有成就感的项目」这类问题，面试官会基于这里的内容追问——
              所以卡片里必须是你真做过的事，工具只负责整理，不负责编。
            </p>
          </div>
          <button
            type="button"
            disabled={busy || resumeId === null}
            onClick={() => void generateStories()}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-xs text-white disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            从简历抽取故事卡
          </button>
        </div>

        {stories && (
          <div className="mt-3 text-xs text-slate-600">
            覆盖情况：
            {Object.entries(stories.coverage).map(([tag, count]) => (
              <span key={tag} className="ml-2">
                {stories.tag_labels[tag] ?? tag}
                <span className={count === 0 ? 'text-rose-600' : 'text-emerald-700'}> {count}</span>
              </span>
            ))}
            {stories.missing_tags.length > 0 && (
              <div className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-amber-800">
                还没有这些类型的故事：
                {stories.missing_tags.map((tag) => stories.tag_labels[tag] ?? tag).join('、')}
                —— 被问到「说一次失败的经历」时就没有素材可讲。
              </div>
            )}
          </div>
        )}

        <div className="mt-3 space-y-2">
          {stories?.items.length === 0 && (
            <p className="text-xs text-slate-500">还没有故事卡。点上面的按钮从简历抽一批，之后可以手动改。</p>
          )}
          {stories?.items.map((card) => (
            <div key={card.id} className="rounded-md border border-slate-200 p-3 text-xs">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-slate-800">{card.title}</span>
                  {card.tags.map((tag) => (
                    <span key={tag} className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600">
                      {stories.tag_labels[tag] ?? tag}
                    </span>
                  ))}
                  {card.used_count > 0 && (
                    <span className="text-[11px] text-slate-400">被引用 {card.used_count} 次</span>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void toggleStar(card)}
                    className={
                      card.starred
                        ? 'rounded bg-amber-100 px-2 py-0.5 text-[11px] text-amber-800'
                        : 'rounded border border-slate-300 px-2 py-0.5 text-[11px] text-slate-600'
                    }
                  >
                    {card.starred ? '已主推' : '设为主推'}
                  </button>
                  <button
                    type="button"
                    onClick={() => void removeStory(card)}
                    className="rounded border border-slate-300 px-2 py-0.5 text-[11px] text-slate-600 hover:bg-slate-50"
                  >
                    删除
                  </button>
                </div>
              </div>
              <div className="mt-1 space-y-0.5 text-slate-600">
                {card.situation && <div>背景：{card.situation}</div>}
                {card.task && <div>目标：{card.task}</div>}
                {card.action && <div>做法：{card.action}</div>}
                <div className={card.result.includes('未写明') ? 'text-amber-700' : ''}>结果：{card.result}</div>
                {card.metrics && <div>数字：{card.metrics}</div>}
              </div>
              {card.result.includes('未写明') && (
                <div className="mt-1 text-[11px] text-amber-700">
                  这段的结果还没写。被追问时说不出影响，是故事卡最常见的失分点——补上你能核对的那个数字。
                </div>
              )}
            </div>
          ))}
        </div>
      </section>

      {/* 自我介绍 */}
      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <h2 className="text-sm font-semibold">自我介绍</h2>
        <p className="mt-0.5 text-xs text-slate-500">
          按目标时长控制字数（中文口语约 3.3 字/秒）。同一个内容要能说得完，才叫准备好了。
        </p>

        <div className="mt-3 flex flex-wrap items-center gap-3">
          <label className="text-xs text-slate-600">
            时长
            <select
              className="ml-2 rounded-md border border-slate-300 px-2 py-1.5 text-sm"
              value={introSeconds}
              onChange={(event) => setIntroSeconds(Number(event.target.value))}
            >
              {(meta?.self_intro_options ?? [{ seconds: 60, label: '60 秒', target_chars: 198 }]).map((item) => (
                <option key={item.seconds} value={item.seconds}>
                  {item.label}（约 {item.target_chars} 字）
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            disabled={busy || resumeId === null}
            onClick={() => void composeIntro()}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-xs text-white disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            生成自我介绍
          </button>
          <button
            type="button"
            onClick={() => {
              setElapsed(0)
              setRunning((value) => !value)
            }}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-50"
          >
            {running ? '停止计时' : '开始计时练习'}
          </button>
          <span className="text-xs text-slate-500">
            {formatSeconds(elapsed)} / 目标 {formatSeconds(target)}
            {elapsed > 0 && introDelta !== 0 && (
              <span className={introDelta > 0 ? 'ml-2 text-rose-600' : 'ml-2 text-emerald-700'}>
                {introDelta > 0 ? `超出 ${formatSeconds(introDelta)}` : `还剩 ${formatSeconds(-introDelta)}`}
              </span>
            )}
          </span>
        </div>

        {intro && (
          <div className="mt-3 space-y-2">
            <div className="rounded-md border border-slate-200 bg-slate-50 p-3 text-sm leading-relaxed">
              {intro.content}
            </div>
            <div className="text-[11px] text-slate-500">
              {intro.content.length} 字 · 预计说 {String(intro.basis.estimated_seconds ?? '—')} 秒
            </div>
          </div>
        )}
      </section>

      {/* 敏感题 */}
      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <h2 className="text-sm font-semibold">敏感题</h2>
        <p className="mt-0.5 text-xs text-slate-500">
          离职原因、空窗期、期望薪资这些问题有通行策略，但事实必须你自己有。
          答案里带方括号的部分是要你补的——工具不会替你编一段经历。
        </p>

        <div className="mt-3 flex flex-wrap items-center gap-3">
          <label className="text-xs text-slate-600">
            问题
            <select
              className="ml-2 rounded-md border border-slate-300 px-2 py-1.5 text-sm"
              value={sensitiveKind}
              onChange={(event) => setSensitiveKind(event.target.value)}
            >
              {(meta?.sensitive ?? []).map((item) => (
                <option key={item.key} value={item.key}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            disabled={busy || resumeId === null}
            onClick={() => void composeSensitive()}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-xs text-white disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            生成回答
          </button>
        </div>

        {sensitiveMeta && (
          <div className="mt-3 rounded-md border border-sky-200 bg-sky-50 p-3 text-xs text-sky-900">
            <div className="font-medium">面试官会问：{sensitiveMeta.question}</div>
            <div className="mt-1">策略：{sensitiveMeta.strategy}</div>
          </div>
        )}

        {sensitive && (
          <div className="mt-2 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm leading-relaxed">
            {sensitive.content}
          </div>
        )}
      </section>

      {/* 反问清单 */}
      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold">反问清单</h2>
            <p className="mt-0.5 text-xs text-slate-500">
              「你有什么想问的」是在考察你关不关心这个岗位。问能听出信息的问题，别问只能得到套话的问题。
            </p>
          </div>
          <button
            type="button"
            disabled={busy}
            onClick={() => void composeQuestions()}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-xs text-white disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            生成反问清单
          </button>
        </div>

        {questions && (
          <ul className="mt-3 space-y-1.5 text-sm">
            {questions.content.split('\n').map((line) => (
              <li key={line} className="rounded-md border border-slate-200 px-3 py-1.5">
                {line}
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* 历史草稿 */}
      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <h2 className="text-sm font-semibold">历史草稿（{drafts.length}）</h2>
        <p className="mt-0.5 text-xs text-slate-500">
          每版都留着——这些话术要反复打磨，留历史才看得出改版之后是不是更顺了。
        </p>
        <div className="mt-3 space-y-2">
          {drafts.length === 0 && <p className="text-xs text-slate-500">还没有生成过任何话术。</p>}
          {drafts.map((draft) => (
            <div key={draft.id} className="rounded-md border border-slate-100 px-3 py-2 text-xs">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-medium text-slate-700">{draft.title}</span>
                <span className="text-[11px] text-slate-400">
                  {new Date(draft.created_at).toLocaleString('zh-CN')}
                </span>
              </div>
              <div className="mt-1 whitespace-pre-wrap text-slate-600">{draft.content}</div>
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}
