/** 面试题题库：**查阅**用的页面。
 *
 * 职责边界（与「训练」分开）：
 *
 * - **这里只负责看**：按岗位与主题找题、看要点与参考答案、给自己打个自评分。
 * - **往里加题**（导入文档、让模型出题、决定采纳哪些）都在「训练」页——
 *   那些是"积累题库"的动作，和"刷题"是两种心态，混在一页里两边都做不好。
 *
 * 一类岗位的面试重点差别很大，所以按岗位分第一级，而不是按难度分。
 * 详情默认只显示题干：**先自己答一遍再看答案**，否则会产生"我会了"的错觉。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
import type { VisionMeta, VisionQuestion, VisionQuestionBrief } from '../api/types'
import Markdown from '../components/Markdown'

/** 难度色：绿（基础）→ 琥珀（要展开）→ 红（要真懂原理）。
 *
 *  只分三档而不是五档——五档在视觉上分不出来，反而让人以为自己需要分辨。
 */
const DIFFICULTY_STYLES: Record<number, string> = {
  1: 'bg-ok-soft text-ok',
  2: 'bg-ok-soft text-ok',
  3: 'bg-warn-soft text-warn',
  4: 'bg-ng-soft text-ng',
  5: 'bg-ng-soft text-ng',
}

const RATING_LABELS: Record<string, string> = {
  got_it: '答上来了',
  partial: '答了一半',
  missed: '没答上来',
}

const RATING_STYLES: Record<string, string> = {
  got_it: 'bg-ok-soft text-ok border-ok',
  partial: 'bg-warn-soft text-warn border-warn',
  missed: 'bg-ng-soft text-ng border-ng',
}

const RATINGS = [
  { key: 'got_it', label: '答上来了', hint: '要点基本都说到，能展开' },
  { key: 'partial', label: '答了一半', hint: '方向对但不完整' },
  { key: 'missed', label: '没答上来', hint: '说不到点子上' },
] as const

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export default function VisionBank() {
  const [meta, setMeta] = useState<VisionMeta | null>(null)
  const [role, setRole] = useState('development')
  const [category, setCategory] = useState('')
  const [maxDifficulty, setMaxDifficulty] = useState(0)
  const [keyword, setKeyword] = useState('')
  const [items, setItems] = useState<VisionQuestionBrief[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [detail, setDetail] = useState<VisionQuestion | null>(null)
  const [revealed, setRevealed] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api
      .visionMeta()
      .then(setMeta)
      .catch((err) => setError(errorMessage(err)))
  }, [])

  const loadList = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setItems(
        await api.visionQuestions({
          role: role || undefined,
          category: category || undefined,
          maxDifficulty: maxDifficulty || undefined,
          keyword: keyword.trim() || undefined,
        }),
      )
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setLoading(false)
    }
  }, [role, category, maxDifficulty, keyword])

  useEffect(() => {
    void loadList()
  }, [loadList])

  // 详情永远从干净状态进入：换题必须把答案重新收起来
  useEffect(() => {
    if (selectedId === null) {
      setDetail(null)
      return
    }
    setRevealed(false)
    setDetail(null)
    api
      .visionQuestion(selectedId)
      .then(setDetail)
      .catch((err) => setError(errorMessage(err)))
  }, [selectedId])

  const rate = async (rating: string) => {
    if (selectedId === null) {
      return
    }
    try {
      await api.rateVisionQuestion(selectedId, rating)
      await loadList()
      setDetail(await api.visionQuestion(selectedId))
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const currentRole = useMemo(() => meta?.roles.find((item) => item.key === role), [meta, role])

  // 岗位切换后旧的选中项可能不在列表里了
  useEffect(() => {
    if (selectedId !== null && items.length > 0 && !items.some((item) => item.id === selectedId)) {
      setSelectedId(null)
    }
  }, [items, selectedId])

  const progress = useMemo(() => {
    const tested = items.filter((item) => item.attempts > 0)
    return {
      total: items.length,
      tested: tested.length,
      solid: items.filter((item) => item.last_rating === 'got_it').length,
      shaky: items.filter((item) => item.last_rating === 'missed').length,
    }
  }, [items])

  return (
    <div className="grid h-full min-h-0 gap-4 xl:grid-cols-[208px_minmax(240px,320px)_1fr]">
      <aside className="flex min-h-0 flex-col gap-3">
        {/* 岗位方向：机器视觉的三类岗位面试重点差别很大，这是第一级分类 */}
        <div className="space-y-1">
          {(meta?.roles ?? []).map((item) => {
            const active = item.key === role
            return (
              <button
                key={item.key}
                type="button"
                onClick={() => {
                  setRole(item.key)
                  setSelectedId(null)
                  setCategory('')
                }}
                className={`w-full rounded-card border px-2.5 py-2 text-left transition-colors ${
                  active
                    ? 'border-ink-9 bg-ink-9 text-surface'
                    : 'border-ink-1 bg-surface text-ink-7 hover:bg-paper'
                }`}
              >
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-xs font-medium">{item.label}</span>
                  <span className={`font-mono text-[10px] ${active ? 'text-ink-3' : 'text-ink-4'}`}>
                    {item.count}
                  </span>
                </div>
                <p
                  className={`mt-1 text-[10px] leading-relaxed ${
                    active ? 'text-ink-3' : 'text-ink-4'
                  }`}
                >
                  {item.description}
                </p>
              </button>
            )
          })}
        </div>

        {/* 筛选 */}
        <div className="space-y-2 rounded-card border border-ink-1 bg-surface p-2.5">
          <input
            type="search"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            placeholder="搜关键词，如「打光」「标定」"
            className="w-full rounded-control border border-ink-2 px-2 py-1.5 text-xs placeholder:text-ink-3"
          />
          <div>
            <div className="mb-1 text-[10px] text-ink-4">主题</div>
            <div className="flex flex-wrap gap-1">
              <button
                type="button"
                onClick={() => setCategory('')}
                className={`rounded-control px-1.5 py-0.5 text-[10px] ${
                  category === '' ? 'bg-ink-9 text-surface' : 'bg-sunken text-ink-6'
                }`}
              >
                全部
              </button>
              {(meta?.categories ?? []).map((item) => (
                <button
                  key={item}
                  type="button"
                  onClick={() => setCategory(item === category ? '' : item)}
                  className={`rounded-control px-1.5 py-0.5 text-[10px] ${
                    item === category ? 'bg-ink-9 text-surface' : 'bg-sunken text-ink-6'
                  }`}
                >
                  {item}
                </button>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-[10px] text-ink-6">难度上限</span>
            {[0, 2, 3, 4, 5].map((level) => (
              <button
                key={level}
                type="button"
                onClick={() => setMaxDifficulty(level)}
                className={`rounded-control px-1.5 py-0.5 font-mono text-[10px] ${
                  maxDifficulty === level ? 'bg-ink-9 text-surface' : 'bg-sunken text-ink-6'
                }`}
              >
                {level === 0 ? '不限' : `≤${level}`}
              </button>
            ))}
          </div>
        </div>

        {/* 自测进度：用一条灰阶直方图表示，而不是写一句话。
            这个界面的主角本来就是灰度——一眼看出"练了多少、漏了多少"。 */}
        <div className="mt-auto rounded-card border border-ink-1 bg-surface p-2.5">
          <div className="flex items-baseline justify-between text-[10px] text-ink-6">
            <span>自测进度</span>
            <span className="font-mono">
              {progress.tested}/{progress.total}
            </span>
          </div>
          <div className="mt-1 flex h-1.5 overflow-hidden rounded-control bg-sunken">
            <div
              className="bg-ok"
              style={{ width: `${progress.total ? (progress.solid / progress.total) * 100 : 0}%` }}
              title={`答上来 ${progress.solid} 题`}
            />
            <div
              className="bg-warn"
              style={{
                width: `${
                  progress.total
                    ? ((progress.tested - progress.solid - progress.shaky) / progress.total) * 100
                    : 0
                }%`,
              }}
              title="答了一半"
            />
            <div
              className="bg-ng"
              style={{ width: `${progress.total ? (progress.shaky / progress.total) * 100 : 0}%` }}
              title={`还没答上来 ${progress.shaky} 题`}
            />
          </div>
        </div>
      </aside>

      {/* 题目列表：单独占一列，与"选中的题"分开——
          翻找列表时不丢详情，这是这个页面最主要的动作。 */}
      <div className="flex min-h-0 flex-col">
        <div className="flex shrink-0 items-baseline justify-between pb-1">
          <span className="text-[11px] text-ink-6">题目</span>
          <span className="font-mono text-[11px] text-ink-4">{items.length}</span>
        </div>
        <div className="min-h-0 flex-1 space-y-1 overflow-y-auto pr-1">
          {loading && items.length === 0 && <p className="px-1 text-xs text-ink-4">加载中…</p>}
          {!loading && items.length === 0 && (
            <p className="px-1 text-xs text-ink-4">没有符合条件的题。</p>
          )}
          {items.map((item) => {
            const active = item.id === selectedId
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => setSelectedId(item.id)}
                className={`w-full rounded-card border px-2.5 py-2 text-left text-xs transition-colors ${
                  active
                    ? 'border-ink-9 bg-surface'
                    : 'border-ink-1 bg-surface hover:border-ink-2 hover:bg-paper'
                }`}
              >
                <div className="flex items-start gap-2">
                  <span className="flex-1 font-medium leading-snug text-ink-9">{item.text}</span>
                  {item.last_rating && (
                    <span
                      className={`shrink-0 rounded-control border px-1 py-0.5 text-[10px] ${
                        RATING_STYLES[item.last_rating] ?? 'border-ink-1 text-ink-6'
                      }`}
                    >
                      {RATING_LABELS[item.last_rating] ?? item.last_rating}
                    </span>
                  )}
                </div>
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                  <span
                    className={`rounded-control px-1.5 py-0.5 font-mono text-[10px] ${
                      DIFFICULTY_STYLES[item.difficulty] ?? 'bg-sunken text-ink-6'
                    }`}
                  >
                    {item.difficulty}
                  </span>
                  <span className="rounded-control bg-sunken px-1.5 py-0.5 text-[10px] text-ink-6">
                    {item.category}
                  </span>
                  {/* 只显示"性质"标签：「高频」「易错」这类来自文档标注的信息。
                      分类已经单独显示了，重复一遍没有意义。 */}
                  {item.tags
                    .filter((tag) => tag !== item.category)
                    .map((tag) => (
                      <span
                        key={tag}
                        className={
                          tag === '高频'
                            ? 'rounded-control bg-warn-soft px-1.5 py-0.5 text-[10px] text-warn'
                            : tag === '易错'
                              ? 'rounded-control bg-ng-soft px-1.5 py-0.5 text-[10px] text-ng'
                              : 'rounded-control border border-ink-1 px-1.5 py-0.5 text-[10px] text-ink-4'
                        }
                        title={tag === '高频' ? '多场面试反复问到' : tag === '易错' ? '录音里有人答错或被追问' : ''}
                      >
                        {tag}
                      </span>
                    ))}
                  {item.attempts > 0 && (
                    <span className="font-mono text-[10px] text-ink-4">×{item.attempts}</span>
                  )}
                </div>
              </button>
            )
          })}
        </div>
      </div>

      <div className="flex min-h-0 flex-col gap-3">
        {error && (
          <div className="rounded-card border border-ng bg-ng-soft px-3 py-2 text-xs text-ng">
            {error}
          </div>
        )}

        {!detail && !error && (
          <div className="rounded-panel border border-ink-1 bg-surface p-5 text-xs leading-relaxed text-ink-4">
            <p>{currentRole?.description ?? '从左边选一道题开始。'}</p>
            <p className="mt-2">
              建议的用法：先自己把答案说一遍（或写下来），再点「看答案」对照要点。
            </p>
            <p className="mt-2">
              想往题库里加题（导入文档、让模型出题）去「训练」页——那里管积累，这里管查阅。
            </p>
          </div>
        )}

        {detail && (
          <div className="min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
            <div className="rounded-panel border border-ink-1 bg-surface p-4">
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-control bg-ink-9 px-1.5 py-0.5 text-[10px] text-surface">
                  {detail.role_label}
                </span>
                <span className="rounded-control bg-sunken px-1.5 py-0.5 text-[10px] text-ink-6">
                  {detail.category}
                </span>
                <span
                  className={`rounded-control px-1.5 py-0.5 font-mono text-[10px] ${
                    DIFFICULTY_STYLES[detail.difficulty] ?? 'bg-sunken text-ink-6'
                  }`}
                >
                  难度 {detail.difficulty}
                </span>
                {detail.attempts > 0 && (
                  <span className="text-[10px] text-ink-4">
                    自测 <span className="font-mono">{detail.attempts}</span> 次
                    {detail.last_rating && `，最近：${RATING_LABELS[detail.last_rating] ?? ''}`}
                  </span>
                )}
              </div>
              <h3 className="mt-3 text-base font-semibold leading-relaxed text-ink-9">
                {detail.text}
              </h3>

              {!revealed && (
                <div className="mt-4 rounded-card border border-dashed border-ink-2 bg-paper px-3 py-3">
                  <p className="text-xs text-ink-6">
                    先自己答一遍。想不起来的，可以先列出你打算从哪几个角度说，
                    再对照下面的要点看看漏了什么。
                  </p>
                  <button
                    type="button"
                    onClick={() => setRevealed(true)}
                    className="mt-2 rounded-control bg-ink-9 px-3 py-1.5 text-xs text-surface transition-opacity hover:opacity-90"
                  >
                    看答案
                  </button>
                </div>
              )}
            </div>

            {revealed && (
              <>
                <div className="rounded-panel border border-ink-1 bg-surface p-4">
                  <h4 className="text-xs font-medium text-ink-6">答题要点（对照这个自评）</h4>
                  <ol className="mt-2 space-y-1.5">
                    {detail.points.map((point, index) => (
                      <li key={index} className="flex gap-2 text-xs leading-relaxed text-ink-7">
                        <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-control bg-sunken font-mono text-[10px] text-ink-6">
                          {index + 1}
                        </span>
                        <span>{point}</span>
                      </li>
                    ))}
                  </ol>
                </div>

                <div className="rounded-panel border border-ink-1 bg-surface p-4">
                  <h4 className="mb-3 text-xs font-medium text-ink-6">参考答案</h4>
                  <Markdown text={detail.answer} />
                </div>

                {detail.followups.length > 0 && (
                  <div className="rounded-panel border border-ink-1 bg-surface p-4">
                    <h4 className="text-xs font-medium text-ink-6">面试官大概率会追问</h4>
                    <p className="mt-1 text-[11px] text-ink-4">
                      答对主问题只是入场券，追问才是分水岭。
                    </p>
                    <ul className="mt-2 list-disc space-y-1 pl-5 text-xs leading-relaxed text-ink-7">
                      {detail.followups.map((item, index) => (
                        <li key={index}>{item}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {detail.pitfalls.length > 0 && (
                  <div className="rounded-panel border border-warn bg-warn-soft p-4">
                    <h4 className="text-xs font-medium text-warn">常见错误回答</h4>
                    <ul className="mt-2 list-disc space-y-1 pl-5 text-xs leading-relaxed text-warn">
                      {detail.pitfalls.map((item, index) => (
                        <li key={index}>{item}</li>
                      ))}
                    </ul>
                  </div>
                )}

                <div className="rounded-panel border border-ink-1 bg-surface p-4">
                  <h4 className="text-xs font-medium text-ink-6">自评</h4>
                  <p className="mt-1 text-[11px] text-ink-4">
                    按上面的要点看：说到了几条？能不能展开讲？
                  </p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {RATINGS.map((item) => (
                      <button
                        key={item.key}
                        type="button"
                        onClick={() => void rate(item.key)}
                        className={`rounded-control border px-3 py-1.5 text-xs transition-colors ${
                          detail.last_rating === item.key
                            ? RATING_STYLES[item.key]
                            : 'border-ink-2 bg-surface text-ink-7 hover:bg-paper'
                        }`}
                        title={item.hint}
                      >
                        {item.label}
                      </button>
                    ))}
                  </div>
                  <p className="mt-2 text-[10px] text-ink-4">
                    记录只留在这里，不会混进模拟面试的历史（两者是分开统计的）。
                  </p>
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
