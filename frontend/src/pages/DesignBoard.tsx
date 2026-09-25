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
  1: 'bg-ok-soft text-ok',
  2: 'bg-ok-soft text-ok',
  3: 'bg-warn-soft text-warn',
  4: 'bg-warn-soft text-warn',
  5: 'bg-ng-soft text-ng',
}

const VERDICT_LABELS: Record<string, string> = {
  strong: '很完整',
  ok: '基本到位',
  partial: '有明显缺口',
  weak: '偏薄弱',
  unknown: '未能判断',
}
const VERDICT_STYLES: Record<string, string> = {
  strong: 'border-ok bg-ok-soft text-ok',
  ok: 'border-ok bg-ok-soft text-ok',
  partial: 'border-warn bg-warn-soft text-warn',
  weak: 'border-ng bg-ng-soft text-ng',
  unknown: 'border-ink-1 bg-paper text-ink-6',
}

const SCORE_LABELS: Record<string, string> = {
  strong: '到位',
  ok: '基本到位',
  weak: '偏薄',
  missing: '未覆盖',
}
const SCORE_STYLES: Record<string, string> = {
  strong: 'bg-ok-soft text-ok',
  ok: 'bg-ok-soft text-ok',
  weak: 'bg-warn-soft text-warn',
  missing: 'bg-ng-soft text-ng',
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h4 className="text-xs font-semibold text-ink-6">{title}</h4>
      <div className="mt-1.5">{children}</div>
    </div>
  )
}

function ReviewPanel({ review }: { review: DesignReview }) {
  const verdict = review.verdict || 'unknown'
  return (
    <div className="space-y-4">
      <div className={`rounded-card border px-3 py-2 ${VERDICT_STYLES[verdict] ?? VERDICT_STYLES.unknown}`}>
        <span className="text-sm font-semibold">{VERDICT_LABELS[verdict] ?? verdict}</span>
        {review.summary && (
          <p className="mt-1 text-xs leading-relaxed opacity-90">{review.summary}</p>
        )}
      </div>

      {review.notes && (
        <div className="rounded-card border border-warn bg-warn-soft px-3 py-2 text-xs text-warn">
          {review.notes}
        </div>
      )}

      {/* 漏了什么 —— 这份评估最该被看到的部分，所以放在最前面加粗显示 */}
      {review.gaps.length > 0 && (
        <div className="rounded-card border border-ng bg-ng-soft px-3 py-2.5">
          <h4 className="text-xs font-semibold text-ng">最关键的遗漏</h4>
          <ul className="mt-1.5 list-disc space-y-1 pl-4 text-xs leading-relaxed text-ng">
            {review.gaps.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </div>
      )}

      <Section title="各维度表现">
        <div className="space-y-2">
          {review.dimensions.map((item) => (
            <div key={item.dimension} className="rounded-card border border-ink-1 bg-surface p-2.5">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-medium text-ink-9">{item.dimension}</span>
                <span
                  className={`rounded-control px-1.5 py-0.5 text-[10px] ${SCORE_STYLES[item.score] ?? 'bg-sunken text-ink-6'}`}
                >
                  {SCORE_LABELS[item.score] ?? item.score}
                </span>
                {item.weight === 'core' && (
                  <span className="rounded-control bg-ink-9 px-1.5 py-0.5 text-[10px] text-surface">
                    及格线
                  </span>
                )}
                {item.weight === 'bonus' && (
                  <span className="rounded-control bg-sunken px-1.5 py-0.5 text-[10px] text-ink-6">
                    加分项
                  </span>
                )}
              </div>
              {item.assessment && (
                <p className="mt-1 text-xs leading-relaxed text-ink-6">{item.assessment}</p>
              )}
              {item.mentioned.length > 0 && (
                <ul className="mt-1.5 space-y-0.5">
                  {item.mentioned.map((mention, index) => (
                    <li key={index} className="flex items-start gap-1.5 text-[11px] text-ink-7">
                      {/* 说到的也要区分「他明确说了」和「模型推断的」 */}
                      <span
                        className={`mt-0.5 shrink-0 rounded-control px-1 text-[10px] ${
                          mention.kind === 'stated'
                            ? 'bg-ok-soft text-ok'
                            : 'bg-sunken text-ink-6'
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
                    <li key={index} className="flex items-start gap-1.5 text-[11px] text-ng">
                      <span className="mt-0.5 shrink-0 rounded-control bg-ng-soft px-1 text-[10px] text-ng">
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
          <ul className="list-disc space-y-1 pl-4 text-xs text-ink-7">
            {review.strengths.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </Section>
      )}

      {review.followups.length > 0 && (
        <Section title="面试官会追问">
          <ul className="list-disc space-y-1 pl-4 text-xs text-ink-7">
            {review.followups.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </Section>
      )}

      <p className="text-[11px] leading-relaxed text-ink-4">
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
            className={`w-full rounded-card border px-3 py-2 text-left text-xs transition ${
              item.slug === slug
                ? 'border-ink-9 bg-surface'
                : 'border-ink-1 bg-paper hover:bg-surface'
            }`}
          >
            <div className="font-medium leading-snug text-ink-9">{item.title}</div>
            <div className="mt-1 flex flex-wrap items-center gap-1.5">
              <span
                className={`rounded-control px-1.5 py-0.5 text-[10px] ${DIFFICULTY_STYLES[item.difficulty] ?? 'bg-sunken text-ink-6'}`}
              >
                难度 {item.difficulty}
              </span>
              {item.attempts > 0 && (
                <span className="text-[10px] text-ink-4">做过 {item.attempts} 次</span>
              )}
            </div>
            <div className="mt-1 text-[10px] text-ink-4">
              {item.dimensions.length} 个评分维度
            </div>
          </button>
        ))}
      </aside>

      <div className="space-y-4">
        {error && (
          <div className="rounded-card border border-ng bg-ng-soft px-3 py-2 text-xs text-ng">
            {error}
          </div>
        )}

        {detail && (
          <>
            <div className="rounded-panel border border-ink-1 bg-surface p-4">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-sm font-semibold text-ink-9">{detail.title}</h3>
                <span
                  className={`rounded-control px-1.5 py-0.5 text-[10px] ${DIFFICULTY_STYLES[detail.difficulty] ?? 'bg-sunken text-ink-6'}`}
                >
                  难度 {detail.difficulty}
                </span>
                <span className="text-[10px] text-ink-4">{detail.tags.join(' · ')}</span>
                {currentBrief && currentBrief.attempts > 0 && (
                  <span className="text-[10px] text-ink-4">
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
                <div className="rounded-card bg-paper p-3">
                  <h4 className="text-xs font-semibold text-ink-6">已知约束</h4>
                  <ul className="mt-1.5 list-disc space-y-1 pl-4 text-[11px] leading-relaxed text-ink-6">
                    {detail.constraints.map((item, index) => (
                      <li key={index}>{item}</li>
                    ))}
                  </ul>
                </div>
                <div className="rounded-card border border-dashed border-ink-2 p-3">
                  <div className="flex items-center justify-between">
                    <h4 className="text-xs font-semibold text-ink-6">评分维度</h4>
                    <span className="text-[10px] text-ink-4">会从这几个方向看</span>
                  </div>
                  <ul className="mt-1.5 flex flex-wrap gap-1.5">
                    {detail.dimensions.map((item) => (
                      <li
                        key={item.dimension}
                        className={`rounded-control px-1.5 py-0.5 text-[10px] ${
                          item.weight === 'core'
                            ? 'bg-ink-9 text-surface'
                            : item.weight === 'bonus'
                              ? 'bg-sunken text-ink-6'
                              : 'bg-sunken text-ink-6'
                        }`}
                      >
                        {item.dimension}
                        {item.weight === 'core' && '（及格线）'}
                        {item.weight === 'bonus' && '（加分）'}
                      </li>
                    ))}
                  </ul>
                  <p className="mt-1.5 text-[10px] leading-relaxed text-ink-4">
                    具体希望谈到什么不给出来——那是评分参照，看到就变成照抄了。
                  </p>
                </div>
              </div>

              <div className="mt-3 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => setShowOpenQuestions((value) => !value)}
                  className="rounded-control border border-ink-2 bg-surface px-3 py-1.5 text-xs text-ink-6"
                >
                  {showOpenQuestions ? '收起' : '看「可以先问什么」'}（{detail.open_questions.length}）
                </button>
                <button
                  type="button"
                  onClick={() => setHintsShown((value) => !value)}
                  className="rounded-control border border-ink-2 bg-surface px-3 py-1.5 text-xs text-ink-6"
                >
                  {hintsShown ? '收起' : '卡住了，看提示'}
                </button>
              </div>

              {showOpenQuestions && (
                <div className="mt-3 rounded-card border border-ink-1 bg-surface p-3">
                  <h4 className="text-xs font-semibold text-ink-6">
                    题面没给、需要你自己问清的
                  </h4>
                  <p className="mt-1 text-[11px] leading-relaxed text-ink-6">
                    系统设计题最容易失分的地方就是跳过这一步直接给方案。真想练，就先不看这个列表，
                    自己列一遍再对照。
                  </p>
                  <ul className="mt-1.5 list-disc space-y-1 pl-4 text-xs text-ink-7">
                    {detail.open_questions.map((item, index) => (
                      <li key={index}>{item}</li>
                    ))}
                  </ul>
                </div>
              )}

              {hintsShown && (
                <div className="mt-3 rounded-card border border-ink-1 bg-surface p-3">
                  <h4 className="text-xs font-semibold text-ink-6">切入点</h4>
                  <ul className="mt-1.5 list-disc space-y-1 pl-4 text-xs text-ink-7">
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
                <h4 className="text-xs font-semibold text-ink-6">白板</h4>
                <span className="text-[10px] text-ink-4">
                  画出来是帮自己想清楚；画布上的文字会被抽出来一起交给评估
                </span>
              </div>
              <Suspense
                fallback={
                  <div className="rounded-card border border-ink-2 bg-surface p-4 text-xs text-ink-4">
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
                <h4 className="text-xs font-semibold text-ink-6">方案说明</h4>
                <span className="text-[10px] text-ink-4">
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
                className="w-full rounded-card border border-ink-2 px-3 py-2 text-xs leading-relaxed"
              />
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => void submit()}
                disabled={!canSubmit}
                className="rounded-control bg-ink-9 px-3 py-1.5 text-xs text-surface disabled:bg-ink-2"
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
                className="rounded-control border border-ink-2 bg-surface px-3 py-1.5 text-xs text-ink-6"
              >
                清空重做
              </button>
              <span className="text-[11px] text-ink-4">
                {writeup.trim().length} 字
                {scene.trim() ? '，画布已记录' : '，画布为空'}
              </span>
            </div>

            {attempt && (
              <div className="rounded-panel border border-ink-1 bg-surface p-4">
                <h4 className="mb-3 text-xs font-semibold text-ink-6">评估</h4>
                <ReviewPanel review={attempt.review} />
              </div>
            )}

            {history.length > 0 && (
              <div className="rounded-panel border border-ink-1 bg-surface p-4">
                <h4 className="mb-2 text-xs font-semibold text-ink-6">
                  这道题的历史（点一下载入，画布和说明都能接着改）
                </h4>
                <div className="space-y-1">
                  {history.slice(0, 6).map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => void loadAttempt(item.id)}
                      className="w-full rounded-control border border-ink-1 px-2.5 py-1.5 text-left text-xs hover:bg-paper"
                    >
                      <div className="flex items-center gap-2">
                        <span
                          className={`rounded-control px-1.5 py-0.5 text-[10px] ${
                            VERDICT_STYLES[item.verdict] ?? 'border-ink-1 text-ink-6'
                          }`}
                        >
                          {VERDICT_LABELS[item.verdict] ?? (item.verdict || '—')}
                        </span>
                        <span className="flex-1 truncate text-ink-6">
                          {item.writeup.slice(0, 40) || '（只画了图）'}
                        </span>
                        <span className="shrink-0 text-[10px] text-ink-4">
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
