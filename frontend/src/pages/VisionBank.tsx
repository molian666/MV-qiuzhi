/** 机器视觉面试题库（M13）。
 *
 * 这是当前的主线功能。核心交互是**「先自己答，再看答案」**：
 * 详情默认只显示题干，答案要点与参考答案收在「看答案」后面。
 * 直接看到答案会产生「我会了」的错觉——自测时先说一遍，再对照要点，
 * 才是这个页面真正有用的用法。
 *
 * 三类岗位（开发 / 应用 / 实施）的面试重点差别很大，所以按岗位分第一级，
 * 而不是按难度分。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
import type { VisionMeta, VisionQuestion, VisionQuestionBrief } from '../api/types'
import Markdown from '../components/Markdown'

const DIFFICULTY_STYLES: Record<number, string> = {
  1: 'bg-emerald-50 text-emerald-700',
  2: 'bg-emerald-50 text-emerald-700',
  3: 'bg-amber-50 text-amber-700',
  4: 'bg-orange-50 text-orange-700',
  5: 'bg-rose-50 text-rose-700',
}

const RATING_LABELS: Record<string, string> = {
  got_it: '答上来了',
  partial: '答了一半',
  missed: '没答上来',
}

const RATING_STYLES: Record<string, string> = {
  got_it: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  partial: 'bg-amber-50 text-amber-700 border-amber-200',
  missed: 'bg-rose-50 text-rose-700 border-rose-200',
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

  const currentRole = useMemo(
    () => meta?.roles.find((item) => item.key === role),
    [meta, role],
  )

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
    <div className="grid gap-5 lg:grid-cols-[320px_1fr]">
      <aside className="space-y-3">
        {/* 岗位方向 */}
        <div className="space-y-1.5">
          {(meta?.roles ?? []).map((item) => (
            <button
              key={item.key}
              type="button"
              onClick={() => {
                setRole(item.key)
                setSelectedId(null)
                setCategory('')
              }}
              className={`w-full rounded-lg border px-3 py-2 text-left transition ${
                item.key === role
                  ? 'border-slate-900 bg-white shadow-sm'
                  : 'border-slate-200 bg-white/60 hover:bg-white'
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-slate-900">{item.label}</span>
                <span className="text-[10px] text-slate-400">{item.count} 题</span>
              </div>
              <p className="mt-1 text-[10px] leading-relaxed text-slate-500">{item.description}</p>
            </button>
          ))}
        </div>

        {/* 筛选 */}
        <div className="space-y-2 rounded-lg border border-slate-200 bg-white p-3">
          <input
            type="search"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            placeholder="搜关键词，如「打光」「标定」"
            className="w-full rounded-md border border-slate-300 px-2.5 py-1.5 text-xs"
          />
          <div className="flex flex-wrap gap-1">
            <button
              type="button"
              onClick={() => setCategory('')}
              className={`rounded px-1.5 py-0.5 text-[10px] ${
                category === '' ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-600'
              }`}
            >
              全部
            </button>
            {(meta?.categories ?? []).map((item) => (
              <button
                key={item}
                type="button"
                onClick={() => setCategory(item === category ? '' : item)}
                className={`rounded px-1.5 py-0.5 text-[10px] ${
                  item === category ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-600'
                }`}
              >
                {item}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-slate-500">难度上限</span>
            {[0, 2, 3, 4, 5].map((level) => (
              <button
                key={level}
                type="button"
                onClick={() => setMaxDifficulty(level)}
                className={`rounded px-1.5 py-0.5 text-[10px] ${
                  maxDifficulty === level ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-600'
                }`}
              >
                {level === 0 ? '不限' : `≤${level}`}
              </button>
            ))}
          </div>
          {progress.total > 0 && (
            <p className="border-t border-slate-100 pt-2 text-[10px] leading-relaxed text-slate-500">
              这 {progress.total} 题里自测过 {progress.tested} 题
              {progress.tested > 0 && (
                <>
                  ，其中答上来 {progress.solid} 题
                  {progress.shaky > 0 && (
                    <span className="text-rose-600">，还没答上来 {progress.shaky} 题</span>
                  )}
                </>
              )}
            </p>
          )}
        </div>

        {/* 题目列表 */}
        <div className="space-y-1.5">
          {loading && items.length === 0 && <p className="px-1 text-xs text-slate-400">加载中…</p>}
          {!loading && items.length === 0 && (
            <p className="px-1 text-xs text-slate-400">没有符合条件的题。</p>
          )}
          {items.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setSelectedId(item.id)}
              className={`w-full rounded-lg border px-3 py-2 text-left text-xs transition ${
                item.id === selectedId
                  ? 'border-slate-900 bg-white shadow-sm'
                  : 'border-slate-200 bg-white/60 hover:bg-white'
              }`}
            >
              <div className="flex items-start gap-2">
                <span className="flex-1 font-medium leading-snug text-slate-900">{item.text}</span>
                {item.last_rating && (
                  <span
                    className={`shrink-0 rounded border px-1 py-0.5 text-[10px] ${
                      RATING_STYLES[item.last_rating] ?? 'border-slate-200 text-slate-500'
                    }`}
                  >
                    {RATING_LABELS[item.last_rating] ?? item.last_rating}
                  </span>
                )}
              </div>
              <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                <span
                  className={`rounded px-1.5 py-0.5 text-[10px] ${
                    DIFFICULTY_STYLES[item.difficulty] ?? 'bg-slate-100 text-slate-600'
                  }`}
                >
                  难度 {item.difficulty}
                </span>
                <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600">
                  {item.category}
                </span>
                {item.attempts > 0 && (
                  <span className="text-[10px] text-slate-400">测过 {item.attempts} 次</span>
                )}
              </div>
            </button>
          ))}
        </div>
      </aside>

      <div className="space-y-4">
        {error && (
          <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
            {error}
          </div>
        )}

        {!detail && !error && (
          <div className="rounded-xl border border-slate-200 bg-white p-6 text-xs leading-relaxed text-slate-400">
            <p>{currentRole?.description ?? '从左边选一道题开始。'}</p>
            <p className="mt-2">
              建议的用法：先自己把答案说一遍（或写下来），再点「看答案」对照要点。
            </p>
          </div>
        )}

        {detail && (
          <>
            <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded bg-slate-900 px-1.5 py-0.5 text-[10px] text-white">
                  {detail.role_label}
                </span>
                <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600">
                  {detail.category}
                </span>
                <span
                  className={`rounded px-1.5 py-0.5 text-[10px] ${
                    DIFFICULTY_STYLES[detail.difficulty] ?? 'bg-slate-100 text-slate-600'
                  }`}
                >
                  难度 {detail.difficulty}
                </span>
                {detail.attempts > 0 && (
                  <span className="text-[10px] text-slate-400">
                    自测 {detail.attempts} 次
                    {detail.last_rating && `，最近：${RATING_LABELS[detail.last_rating] ?? ''}`}
                  </span>
                )}
              </div>
              <h3 className="mt-3 text-base font-semibold leading-relaxed text-slate-900">
                {detail.text}
              </h3>

              {!revealed && (
                <div className="mt-4 rounded-lg border border-dashed border-slate-300 bg-slate-50 px-3 py-3">
                  <p className="text-xs text-slate-500">
                    先自己答一遍。想不起来的，可以先列出你打算从哪几个角度说，
                    再对照下面的要点看看漏了什么。
                  </p>
                  <button
                    type="button"
                    onClick={() => setRevealed(true)}
                    className="mt-2 rounded-md bg-slate-900 px-3 py-1.5 text-xs text-white"
                  >
                    看答案
                  </button>
                </div>
              )}
            </div>

            {revealed && (
              <>
                <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                  <h4 className="text-xs font-semibold text-slate-500">答题要点（对照这个自评）</h4>
                  <ol className="mt-2 space-y-1.5">
                    {detail.points.map((point, index) => (
                      <li key={index} className="flex gap-2 text-xs leading-relaxed text-slate-700">
                        <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-slate-100 text-[10px] text-slate-600">
                          {index + 1}
                        </span>
                        <span>{point}</span>
                      </li>
                    ))}
                  </ol>
                </div>

                <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                  <h4 className="mb-3 text-xs font-semibold text-slate-500">参考答案</h4>
                  <Markdown text={detail.answer} />
                </div>

                {detail.followups.length > 0 && (
                  <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                    <h4 className="text-xs font-semibold text-slate-500">面试官大概率会追问</h4>
                    <p className="mt-1 text-[11px] text-slate-400">
                      答对主问题只是入场券，追问才是分水岭。
                    </p>
                    <ul className="mt-2 list-disc space-y-1 pl-5 text-xs leading-relaxed text-slate-700">
                      {detail.followups.map((item, index) => (
                        <li key={index}>{item}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {detail.pitfalls.length > 0 && (
                  <div className="rounded-xl border border-amber-200 bg-amber-50/60 p-4">
                    <h4 className="text-xs font-semibold text-amber-900">常见错误回答</h4>
                    <ul className="mt-2 list-disc space-y-1 pl-5 text-xs leading-relaxed text-amber-900">
                      {detail.pitfalls.map((item, index) => (
                        <li key={index}>{item}</li>
                      ))}
                    </ul>
                  </div>
                )}

                <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                  <h4 className="text-xs font-semibold text-slate-500">自评</h4>
                  <p className="mt-1 text-[11px] text-slate-400">
                    按上面的要点看：说到了几条？能不能展开讲？
                  </p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {RATINGS.map((item) => (
                      <button
                        key={item.key}
                        type="button"
                        onClick={() => void rate(item.key)}
                        className={`rounded-md border px-3 py-1.5 text-xs transition ${
                          detail.last_rating === item.key
                            ? RATING_STYLES[item.key]
                            : 'border-slate-300 bg-white text-slate-700 hover:bg-slate-50'
                        }`}
                        title={item.hint}
                      >
                        {item.label}
                      </button>
                    ))}
                  </div>
                  <p className="mt-2 text-[10px] text-slate-400">
                    记录只留在这里，不会混进模拟面试的历史（两者是分开统计的）。
                  </p>
                </div>
              </>
            )}
          </>
        )}
      </div>
    </div>
  )
}
