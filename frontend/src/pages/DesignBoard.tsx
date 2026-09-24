/** 系统设计白板（M13）。
 *
 * 与算法题白板的关键差别：**这里没有对错，只有「想到了没有」**。
 * 所以界面把重点放在两处：
 *
 * 1. **评分维度先给、评分参照不给**——你能看到会从哪几个方向评（需求澄清、
 *    架构、取舍、边界……），但看不到每个维度希望你说到什么。提前看到参照，
 *    这道题就失去意义了。
 * 2. **评估结果里「漏了什么」要最醒目**——那是这份评估真正有用的部分。
 *
 * 白板（Excalidraw）与文字说明都要：画是帮自己想清楚，写是能把方案讲出来的证据。
 * 评估以文字为主，但画布上的文字标签会被抽出来一起交给模型。
 */

import { Suspense, lazy, useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
import type {
  DesignAttempt,
  DesignQuestion,
  DesignQuestionBrief,
  DesignReview,
} from '../api/types'
import Markdown from '../components/Markdown'

// Excalidraw 体积不小，只有切到这个页面才下载
const Whiteboard = lazy(() => import('../components/Whiteboard'))

const DIFFICULTY_STYLES: Record<number, string> = {
  1: 'bg-emerald-50 text-emerald-700',
  2: 'bg-emerald-50 text-emerald-700',
  3: 'bg-amber-50 text-amber-700',
  4: 'bg-orange-50 text-orange-700',
  5: 'bg-rose-50 text-rose-700',
}

const VERDICT_LABELS: Record<string, string> = {
  strong: '很完整',
  ok: '基本到位',
  partial: '有明显缺口',
  weak: '偏薄弱',
  unknown: '未能判断',
}
const VERDICT_STYLES: Record<string, string> = {
  strong: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  ok: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  partial: 'border-amber-200 bg-amber-50 text-amber-700',
  weak: 'border-rose-200 bg-rose-50 text-rose-700',
  unknown: 'border-slate-200 bg-slate-50 text-slate-600',
}

const SCORE_LABELS: Record<string, string> = {
  strong: '到位',
  ok: '基本到位',
  weak: '偏薄',
  missing: '未覆盖',
}
const SCORE_STYLES: Record<string, string> = {
  strong: 'bg-emerald-100 text-emerald-800',
  ok: 'bg-emerald-50 text-emerald-700',
  weak: 'bg-amber-100 text-amber-800',
  missing: 'bg-rose-100 text-rose-800',
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h4 className="text-xs font-semibold text-slate-500">{title}</h4>
      <div className="mt-1.5">{children}</div>
    </div>
  )
}

function ReviewPanel({ review }: { review: DesignReview }) {
  const verdict = review.verdict || 'unknown'
  return (
    <div className="space-y-4">
      <div className={`rounded-lg border px-3 py-2 ${VERDICT_STYLES[verdict] ?? VERDICT_STYLES.unknown}`}>
        <span className="text-sm font-semibold">{VERDICT_LABELS[verdict] ?? verdict}</span>
        {review.summary && (
          <p className="mt-1 text-xs leading-relaxed opacity-90">{review.summary}</p>
        )}
      </div>

      {review.notes && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          {review.notes}
        </div>
      )}

      {/* 漏了什么 —— 这份评估最该被看到的部分，所以放在最前面加粗显示 */}
      {review.gaps.length > 0 && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2.5">
          <h4 className="text-xs font-semibold text-rose-900">最关键的遗漏</h4>
          <ul className="mt-1.5 list-disc space-y-1 pl-4 text-xs leading-relaxed text-rose-900">
            {review.gaps.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </div>
      )}

      <Section title="各维度表现">
        <div className="space-y-2">
          {review.dimensions.map((item) => (
            <div key={item.dimension} className="rounded-lg border border-slate-200 bg-white p-2.5">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-medium text-slate-900">{item.dimension}</span>
                <span
                  className={`rounded px-1.5 py-0.5 text-[10px] ${SCORE_STYLES[item.score] ?? 'bg-slate-100 text-slate-600'}`}
                >
                  {SCORE_LABELS[item.score] ?? item.score}
                </span>
                {item.weight === 'core' && (
                  <span className="rounded bg-slate-900 px-1.5 py-0.5 text-[10px] text-white">
                    及格线
                  </span>
                )}
                {item.weight === 'bonus' && (
                  <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">
                    加分项
                  </span>
                )}
              </div>
              {item.assessment && (
                <p className="mt-1 text-xs leading-relaxed text-slate-600">{item.assessment}</p>
              )}
              {item.mentioned.length > 0 && (
                <ul className="mt-1.5 space-y-0.5">
                  {item.mentioned.map((mention, index) => (
                    <li key={index} className="flex items-start gap-1.5 text-[11px] text-slate-700">
                      {/* 说到的也要区分「他明确说了」和「模型推断的」 */}
                      <span
                        className={`mt-0.5 shrink-0 rounded px-1 text-[10px] ${
                          mention.kind === 'stated'
                            ? 'bg-emerald-100 text-emerald-800'
                            : 'bg-slate-100 text-slate-500'
                        }`}
                        title={mention.kind === 'stated' ? '候选人明确说了' : '模型从他的表述推断'}
                      >
                        {mention.kind === 'stated' ? '说了' : '推断'}
                      </span>
                      <span>{mention.point}</span>
                    </li>
                  ))}
                </ul>
              )}
              {item.missing.length > 0 && (
                <ul className="mt-1.5 space-y-0.5">
                  {item.missing.map((text, index) => (
                    <li key={index} className="flex items-start gap-1.5 text-[11px] text-rose-700">
                      <span className="mt-0.5 shrink-0 rounded bg-rose-100 px-1 text-[10px] text-rose-800">
                        没提
                      </span>
                      <span>{text}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ))}
        </div>
      </Section>

      {review.strengths.length > 0 && (
        <Section title="做得好的地方">
          <ul className="list-disc space-y-1 pl-4 text-xs text-slate-700">
            {review.strengths.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </Section>
      )}

      {review.followups.length > 0 && (
        <Section title="面试官会追问">
          <ul className="list-disc space-y-1 pl-4 text-xs text-slate-700">
            {review.followups.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </Section>
      )}

      <p className="text-[11px] leading-relaxed text-slate-400">
        系统设计没有唯一答案，这是按题目给的维度做的对照，不是判分。
        标「推断」的条目是模型从你的表述推出来的，未必你真这么想。
      </p>
    </div>
  )
}

export default function DesignBoard() {
  const [questions, setQuestions] = useState<DesignQuestionBrief[]>([])
  const [slug, setSlug] = useState('')
  const [detail, setDetail] = useState<DesignQuestion | null>(null)
  const [writeup, setWriteup] = useState('')
  const [scene, setScene] = useState('')
  const [attempt, setAttempt] = useState<DesignAttempt | null>(null)
  const [history, setHistory] = useState<DesignAttempt[]>([])
  const [hintsShown, setHintsShown] = useState(false)
  const [showOpenQuestions, setShowOpenQuestions] = useState(false)
  /** 载入历史作答时要靠它强制重挂 Excalidraw——initialData 只在挂载时生效 */
  const [sceneKey, setSceneKey] = useState(0)
  const [busy, setBusy] = useState<'idle' | 'submitting'>('idle')
  const [error, setError] = useState('')

  useEffect(() => {
    api
      .designQuestions()
      .then((items) => {
        setQuestions(items)
        if (items.length > 0) {
          setSlug((current) => current || items[0].slug)
        }
      })
      .catch((err) => setError(errorMessage(err)))
  }, [])

  const loadHistory = useCallback(async (target: string) => {
    try {
      setHistory(await api.designAttempts(target))
    } catch {
      setHistory([])
    }
  }, [])

  useEffect(() => {
    if (!slug) {
      setDetail(null)
      return
    }
    setError('')
    setAttempt(null)
    setWriteup('')
    setScene('')
    setHintsShown(false)
    setShowOpenQuestions(false)
    api
      .designQuestion(slug)
      .then(setDetail)
      .catch((err) => setError(errorMessage(err)))
    void loadHistory(slug)
  }, [slug, loadHistory])

  const submit = async () => {
    if (!detail) {
      return
    }
    setBusy('submitting')
    setError('')
    try {
      const created = await api.submitDesignAttempt({
        question_slug: detail.slug,
        writeup,
        scene,
      })
      setAttempt(created)
      await loadHistory(detail.slug)
      setQuestions(await api.designQuestions())
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy('idle')
    }
  }

  /** 载入一次历史作答：画布能继续画，说明也能接着改 */
  const loadAttempt = async (attemptId: number) => {
    setError('')
    try {
      const full = await api.designAttempt(attemptId)
      setAttempt(full)
      setWriteup(full.writeup)
      setScene(full.scene)
      setSceneKey((value) => value + 1)
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const canSubmit = busy === 'idle' && (writeup.trim().length > 0 || scene.trim().length > 0)
  const currentBrief = useMemo(
    () => questions.find((item) => item.slug === slug),
    [questions, slug],
  )

  return (
    <div className="grid gap-5 lg:grid-cols-[240px_1fr]">
      <aside className="space-y-1.5">
        {questions.map((item) => (
          <button
            key={item.slug}
            type="button"
            onClick={() => setSlug(item.slug)}
            className={`w-full rounded-lg border px-3 py-2 text-left text-xs transition ${
              item.slug === slug
                ? 'border-slate-900 bg-white shadow-sm'
                : 'border-slate-200 bg-white/60 hover:bg-white'
            }`}
          >
            <div className="font-medium leading-snug text-slate-900">{item.title}</div>
            <div className="mt-1 flex flex-wrap items-center gap-1.5">
              <span
                className={`rounded px-1.5 py-0.5 text-[10px] ${DIFFICULTY_STYLES[item.difficulty] ?? 'bg-slate-100 text-slate-600'}`}
              >
                难度 {item.difficulty}
              </span>
              {item.attempts > 0 && (
                <span className="text-[10px] text-slate-400">做过 {item.attempts} 次</span>
              )}
            </div>
            <div className="mt-1 text-[10px] text-slate-400">
              {item.dimensions.length} 个评分维度
            </div>
          </button>
        ))}
      </aside>

      <div className="space-y-4">
        {error && (
          <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
            {error}
          </div>
        )}

        {detail && (
          <>
            <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-sm font-semibold text-slate-900">{detail.title}</h3>
                <span
                  className={`rounded px-1.5 py-0.5 text-[10px] ${DIFFICULTY_STYLES[detail.difficulty] ?? 'bg-slate-100 text-slate-600'}`}
                >
                  难度 {detail.difficulty}
                </span>
                <span className="text-[10px] text-slate-400">{detail.tags.join(' · ')}</span>
                {currentBrief && currentBrief.attempts > 0 && (
                  <span className="text-[10px] text-slate-400">
                    做过 {currentBrief.attempts} 次
                    {currentBrief.last_verdict &&
                      `，最近：${VERDICT_LABELS[currentBrief.last_verdict] ?? ''}`}
                  </span>
                )}
              </div>
              <div className="mt-3">
                <Markdown text={detail.statement} />
              </div>

              <div className="mt-4 grid gap-3 sm:grid-cols-2">
                <div className="rounded-lg bg-slate-50 p-3">
                  <h4 className="text-xs font-semibold text-slate-600">已知约束</h4>
                  <ul className="mt-1.5 list-disc space-y-1 pl-4 text-[11px] leading-relaxed text-slate-600">
                    {detail.constraints.map((item, index) => (
                      <li key={index}>{item}</li>
                    ))}
                  </ul>
                </div>
                <div className="rounded-lg border border-dashed border-slate-300 p-3">
                  <div className="flex items-center justify-between">
                    <h4 className="text-xs font-semibold text-slate-600">评分维度</h4>
                    <span className="text-[10px] text-slate-400">会从这几个方向看</span>
                  </div>
                  <ul className="mt-1.5 flex flex-wrap gap-1.5">
                    {detail.dimensions.map((item) => (
                      <li
                        key={item.dimension}
                        className={`rounded px-1.5 py-0.5 text-[10px] ${
                          item.weight === 'core'
                            ? 'bg-slate-900 text-white'
                            : item.weight === 'bonus'
                              ? 'bg-slate-100 text-slate-500'
                              : 'bg-slate-100 text-slate-600'
                        }`}
                      >
                        {item.dimension}
                        {item.weight === 'core' && '（及格线）'}
                        {item.weight === 'bonus' && '（加分）'}
                      </li>
                    ))}
                  </ul>
                  <p className="mt-1.5 text-[10px] leading-relaxed text-slate-400">
                    具体希望谈到什么不给出来——那是评分参照，看到就变成照抄了。
                  </p>
                </div>
              </div>

              <div className="mt-3 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => setShowOpenQuestions((value) => !value)}
                  className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-600"
                >
                  {showOpenQuestions ? '收起' : '看「可以先问什么」'}（{detail.open_questions.length}）
                </button>
                <button
                  type="button"
                  onClick={() => setHintsShown((value) => !value)}
                  className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-600"
                >
                  {hintsShown ? '收起' : '卡住了，看提示'}
                </button>
              </div>

              {showOpenQuestions && (
                <div className="mt-3 rounded-lg border border-slate-200 bg-white p-3">
                  <h4 className="text-xs font-semibold text-slate-500">
                    题面没给、需要你自己问清的
                  </h4>
                  <p className="mt-1 text-[11px] leading-relaxed text-slate-500">
                    系统设计题最容易失分的地方就是跳过这一步直接给方案。真想练，就先不看这个列表，
                    自己列一遍再对照。
                  </p>
                  <ul className="mt-1.5 list-disc space-y-1 pl-4 text-xs text-slate-700">
                    {detail.open_questions.map((item, index) => (
                      <li key={index}>{item}</li>
                    ))}
                  </ul>
                </div>
              )}

              {hintsShown && (
                <div className="mt-3 rounded-lg border border-slate-200 bg-white p-3">
                  <h4 className="text-xs font-semibold text-slate-500">切入点</h4>
                  <ul className="mt-1.5 list-disc space-y-1 pl-4 text-xs text-slate-700">
                    {detail.hints.map((item, index) => (
                      <li key={index}>{item}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>

            {/* 白板 */}
            <div>
              <div className="mb-1.5 flex flex-wrap items-center gap-2">
                <h4 className="text-xs font-semibold text-slate-500">白板</h4>
                <span className="text-[10px] text-slate-400">
                  画出来是帮自己想清楚；画布上的文字会被抽出来一起交给评估
                </span>
              </div>
              <Suspense
                fallback={
                  <div className="rounded-lg border border-slate-300 bg-white p-4 text-xs text-slate-400">
                    正在加载白板…
                  </div>
                }
              >
                <Whiteboard
                  key={`${detail.slug}-${sceneKey}`}
                  initialScene={scene}
                  onChange={setScene}
                />
              </Suspense>
            </div>

            {/* 文字说明 */}
            <div>
              <div className="mb-1.5 flex flex-wrap items-center gap-2">
                <h4 className="text-xs font-semibold text-slate-500">方案说明</h4>
                <span className="text-[10px] text-slate-400">
                  评估以这段文字为主。建议按「需求澄清 → 架构 → 取舍 → 边界」写
                </span>
              </div>
              <textarea
                value={writeup}
                onChange={(event) => setWriteup(event.target.value)}
                rows={10}
                placeholder={
                  '先把你要问清的约束列出来，再讲方案：\n' +
                  '1. 我会先确认……（哪些信息题面没给）\n' +
                  '2. 整体方案是……\n' +
                  '3. 关键取舍是……（为什么这么选，代价是什么）\n' +
                  '4. 异常与长期维护……'
                }
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-xs leading-relaxed"
              />
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => void submit()}
                disabled={!canSubmit}
                className="rounded-md bg-slate-900 px-3 py-1.5 text-xs text-white disabled:bg-slate-300"
              >
                {busy === 'submitting' ? '评估中…' : '提交并评估'}
              </button>
              <button
                type="button"
                onClick={() => {
                  setWriteup('')
                  setScene('')
                  setSceneKey((value) => value + 1)
                  setAttempt(null)
                }}
                className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-600"
              >
                清空重做
              </button>
              <span className="text-[11px] text-slate-400">
                {writeup.trim().length} 字
                {scene.trim() ? '，画布已记录' : '，画布为空'}
              </span>
            </div>

            {attempt && (
              <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                <h4 className="mb-3 text-xs font-semibold text-slate-500">评估</h4>
                <ReviewPanel review={attempt.review} />
              </div>
            )}

            {history.length > 0 && (
              <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                <h4 className="mb-2 text-xs font-semibold text-slate-500">
                  这道题的历史（点一下载入，画布和说明都能接着改）
                </h4>
                <div className="space-y-1">
                  {history.slice(0, 6).map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => void loadAttempt(item.id)}
                      className="w-full rounded-md border border-slate-200 px-2.5 py-1.5 text-left text-xs hover:bg-slate-50"
                    >
                      <div className="flex items-center gap-2">
                        <span
                          className={`rounded px-1.5 py-0.5 text-[10px] ${
                            VERDICT_STYLES[item.verdict] ?? 'border-slate-200 text-slate-500'
                          }`}
                        >
                          {VERDICT_LABELS[item.verdict] ?? (item.verdict || '—')}
                        </span>
                        <span className="flex-1 truncate text-slate-600">
                          {item.writeup.slice(0, 40) || '（只画了图）'}
                        </span>
                        <span className="shrink-0 text-[10px] text-slate-400">
                          {new Date(item.created_at).toLocaleString('zh-CN', { hour12: false })}
                        </span>
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
