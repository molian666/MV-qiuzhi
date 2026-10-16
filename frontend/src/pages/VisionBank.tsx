/** 面试题题库：**查阅**用的页面。
 *
 * 职责边界（与「训练」分开）：
 *
 * - **这里只负责看**：按岗位与主题找题、看要点与参考答案、给自己打个自评分。
 * - **往里加题**（导入文档、让模型出题、决定采纳哪些）都在「训练」页——
 *   那些是"积累题库"的动作，和"刷题"是两种心态，混在一页里两边都做不好。
 * - **删题在这里**：加题归训练页（积累），删题归这里（清理）——一边翻题一边
 *   才发现"这道不要了"，就地删掉最顺手。删除**不可逆**（连自测记录一起删），
 *   所以单题与批量都要二次确认。
 *
 * 一类岗位的面试重点差别很大，所以按岗位分第一级，而不是按难度分。
 * 详情默认只显示题干：**先自己答一遍再看答案**，否则会产生"我会了"的错觉。
 */

import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
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

/** 预设标记标签：点一下打上，再点一下摘下。高频 / 易错用专门的配色，
 *  自定义标签用普通描边样式（见列表项里的同一套映射）。 */
const PRESET_TAGS = ['高频', '易错'] as const

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
  const [category, setCategory] = useState('')
  const [maxDifficulty, setMaxDifficulty] = useState(0)
  const [keyword, setKeyword] = useState('')
  const [items, setItems] = useState<VisionQuestionBrief[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [detail, setDetail] = useState<VisionQuestion | null>(null)
  const [revealed, setRevealed] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  /** 勾选集合：批量删除用。用 Set 而不是数组，勾/取消都是 O(1) */
  const [checkedIds, setCheckedIds] = useState<Set<number>>(new Set())
  /** 删除进行中：按钮要禁用，避免连点两次发出两个请求 */
  const [deleting, setDeleting] = useState(false)
  /** 标签编辑中：预设按钮与自定义输入都要禁用，避免连点发重复请求 */
  const [tagSaving, setTagSaving] = useState(false)
  const [tagInput, setTagInput] = useState('')
  const [tagError, setTagError] = useState('')
  /** 标签筛选：多选。一道题可以同时带「高频」和「易错」，
   *  选了这两个标签时后端取**交集**——只筛出两个都带的题。 */
  const [selectedTags, setSelectedTags] = useState<Set<string>>(new Set())

  //: 两级主题：平铺父主题，选中父主题（或它的某个子主题）后再显示第二行子主题。
  //: 分类串形如「相机与图像基础 / 相机二开」，父名就是第一段。
  const topics = meta?.topics ?? []
  //: 标记标签（高频 / 易错 / 自定义）：筛选用，meta 里按题数降序
  const markTags = meta?.mark_tags ?? []
  const activeTopic = topics.find(
    (topic) => topic.name === category || category.startsWith(`${topic.name} / `),
  )

  // 只认「当前列表里还看得见」的勾选：列表一变（换筛选、刷新、删除之后）集合里
  // 可能剩下不在屏幕上的 id——那会让「删除选中」连带删掉根本看不见的题，
  // 是最危险的一种行为。这里在渲染期过滤而不是用 effect 清 state：
  // 在 effect 里 setState 会多触发一轮渲染（lint 也会拦）。
  const checkedVisible = useMemo(
    () => new Set(items.filter((item) => checkedIds.has(item.id)).map((item) => item.id)),
    [items, checkedIds],
  )

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
      // 不按岗位方向筛：题量本来就不大，一份列表按「主题 + 标签 + 难度 + 关键词」查更直接
      setItems(
        await api.visionQuestions({
          category: category || undefined,
          maxDifficulty: maxDifficulty || undefined,
          keyword: keyword.trim() || undefined,
          tags: selectedTags.size > 0 ? [...selectedTags] : undefined,
        }),
      )
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setLoading(false)
    }
  }, [category, maxDifficulty, keyword, selectedTags])

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

  /** 保存标签变更：改完刷新列表、meta（标签计数会变）与详情。
   *  不刷新 meta 的话，刚打上的自定义标签不会出现在筛选器里，
   *  要等刷新页面才有——"打上就能筛"就落空了。
   *  `add` / `remove` 一次只传一个方向——预设按钮与自定义标签都各自发一次。 */
  const saveTags = async (add: string[], remove: string[]) => {
    if (selectedId === null) {
      return
    }
    setTagSaving(true)
    setTagError('')
    try {
      await api.visionUpdateQuestionTags(selectedId, add, remove)
      await loadList()
      setMeta(await api.visionMeta())
      setDetail(await api.visionQuestion(selectedId))
    } catch (err) {
      setTagError(errorMessage(err))
    } finally {
      setTagSaving(false)
    }
  }

  const addCustomTag = (event: FormEvent) => {
    event.preventDefault()
    const tag = tagInput.trim()
    if (!tag) {
      return
    }
    setTagInput('')
    void saveTags([tag], [])
  }

  const toggleTag = (name: string) => {
    setSelectedTags((prev) => {
      const next = new Set(prev)
      if (next.has(name)) {
        next.delete(name)
      } else {
        next.add(name)
      }
      return next
    })
  }

  // 岗位切换后旧的选中项可能不在列表里了
  useEffect(() => {
    if (selectedId !== null && items.length > 0 && !items.some((item) => item.id === selectedId)) {
      setSelectedId(null)
    }
  }, [items, selectedId])

  // 列表一变（换筛选、刷新、删除），勾选集合里可能剩下已经不在列表里的 id。
  // 读的时候统一走 checkedVisible（见上），所以这里不必再清一次 state。

  /** 删除后不只是列表变了：分类标签与总数（meta）也跟着变，两个都要刷。 */
  const refresh = useCallback(async () => {
    await loadList()
    try {
      setMeta(await api.visionMeta())
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [loadList])

  /** 删一道题。**不可逆**（题与它的自测记录一起没了），所以必须先确认。 */
  const removeOne = async (item: VisionQuestionBrief) => {
    if (!window.confirm(`删除这道题？它的自测记录也会一起删掉，不能撤销。\n\n${item.text}`)) {
      return
    }
    setDeleting(true)
    setError('')
    try {
      await api.visionDeleteQuestion(item.id)
      // 删掉的正是当前打开的那道：详情要收起来，否则会停在一条已经不存在的题上
      if (selectedId === item.id) {
        setSelectedId(null)
        setDetail(null)
      }
      setCheckedIds((prev) => {
        if (!prev.has(item.id)) {
          return prev
        }
        const next = new Set(prev)
        next.delete(item.id)
        return next
      })
      await refresh()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setDeleting(false)
    }
  }

  /** 批量删除勾选的题。数量写进确认框——多选最容易在没数清的情况下点下去。 */
  const removeChecked = async () => {
    const ids = [...checkedVisible]
    if (ids.length === 0) {
      return
    }
    if (!window.confirm(`删除选中的 ${ids.length} 道题？它们的自测记录也会一起删掉，不能撤销。`)) {
      return
    }
    setDeleting(true)
    setError('')
    try {
      await api.visionDeleteQuestions(ids)
      if (selectedId !== null && checkedVisible.has(selectedId)) {
        setSelectedId(null)
        setDetail(null)
      }
      setCheckedIds(new Set())
      await refresh()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setDeleting(false)
    }
  }

  const toggleChecked = (id: number) => {
    setCheckedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }

  /** 全选 / 全不选：当前列表（也就是当前筛选结果）全都要删时省得逐个点 */
  const allChecked = items.length > 0 && checkedVisible.size === items.length

  const toggleAllChecked = () => {
    setCheckedIds(allChecked ? new Set() : new Set(items.map((item) => item.id)))
  }

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
    /* 左列从 208px 放宽到 248px：去掉岗位切换后，筛选（尤其 13 个主题标签）
       成了唯一的缩小范围手段，208px 会把「深度学习与缺陷检测」这类标签折断 */
    <div className="grid min-h-0 gap-4 lg:h-full xl:grid-cols-[248px_minmax(240px,320px)_1fr]">
      <aside className="flex min-h-0 flex-col gap-3">
        {/* 一句话说明这里是什么，替代原来的岗位方向切换 */}
        <div className="rounded-card border border-ink-1 bg-surface p-2.5">
          <p className="text-[10px] leading-relaxed text-ink-4">
            全部内置题在一起查。用下面的「主题 / 难度 / 关键词」缩小范围。
            不要的题在列表里点「删除」；勾选多道可以批量删（删前都会确认一次）。
          </p>
        </div>

        {/* 筛选 */}
        <div className="space-y-2 rounded-card border border-ink-1 bg-surface p-2.5">
          <input
            type="search"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            placeholder="搜关键词或标签，如「标定」「高频」"
            className="w-full rounded-control border border-ink-2 px-2 py-1.5 text-xs placeholder:text-ink-3"
          />
          {/* 标签：搜索条件之一。点一下选中（可多选、与关键词叠加），再点取消。
              关键词框里直接输入标签名（如「高频」）也能搜到带这个标签的题。 */}
          <div>
            <div className="mb-1 flex items-center gap-2 text-[10px] text-ink-4">
              <span>标签</span>
              <span className="text-ink-3">多选，可叠加</span>
            </div>
            {markTags.length > 0 ? (
              <div className="flex flex-wrap gap-1">
                {markTags.map((item) => {
                  const active = selectedTags.has(item.name)
                  return (
                    <button
                      key={item.name}
                      type="button"
                      onClick={() => toggleTag(item.name)}
                      className={`-my-0.5 whitespace-nowrap rounded-control px-1.5 py-1 text-[10px] ${
                        active ? 'bg-ink-9 text-surface' : 'bg-sunken text-ink-6'
                      }`}
                      title={`${item.name}：${item.count} 道${active ? '（点一下取消筛选）' : '（点一下按它筛）'}`}
                    >
                      {item.name} {item.count}
                    </button>
                  )
                })}
              </div>
            ) : (
              <p className="text-[10px] leading-relaxed text-ink-4">
                还没有标签可筛。打开一道题，在详情里点「高频 / 易错」或加自定义标签，这里就会出现。
              </p>
            )}
          </div>
          <div>
            <div className="mb-1 flex items-center gap-2 text-[10px] text-ink-4">
              <span>主题</span>
              <span className="text-ink-3">
                {topics.length} 个方向
                {activeTopic && activeTopic.children.length > 0 && ` · 「${activeTopic.name}」下 ${activeTopic.children.length} 个子主题`}
              </span>
            </div>
            {/* 两级主题：只平铺父主题（否则 80 多个标签铺满一屏、40 多个还只有 1 道题）。
                想看细分就点开父主题，第二行才出子主题。 */}
            <div className="flex flex-wrap gap-1">
              <button
                type="button"
                onClick={() => setCategory('')}
                className={`-my-0.5 rounded-control px-1.5 py-1 text-[10px] ${
                  category === '' ? 'bg-ink-9 text-surface' : 'bg-sunken text-ink-6'
                }`}
              >
                全部 {meta?.total ?? 0}
              </button>
              {topics.map((item) => (
                <button
                  key={item.name}
                  type="button"
                  onClick={() => setCategory(item.name === category ? '' : item.name)}
                  className={`-my-0.5 whitespace-nowrap rounded-control px-1.5 py-1 text-[10px] ${
                    item.name === category ? 'bg-ink-9 text-surface' : 'bg-sunken text-ink-6'
                  }`}
                  title={`${item.name}（${item.count} 道${item.children.length ? `，含 ${item.children.length} 个子主题` : ''}）`}
                >
                  {item.name} {item.count}
                </button>
              ))}
            </div>
            {activeTopic && activeTopic.children.length > 0 && (
              <div className="mt-1 flex flex-wrap gap-1 border-l-2 border-ink-1 pl-2">
                {activeTopic.children.map((child) => (
                  <button
                    key={child.full}
                    type="button"
                    onClick={() => setCategory(child.full === category ? activeTopic.name : child.full)}
                    className={`-my-0.5 whitespace-nowrap rounded-control px-1.5 py-1 text-[10px] ${
                      child.full === category ? 'bg-ink-9 text-surface' : 'bg-sunken text-ink-6'
                    }`}
                  >
                    {child.name} {child.count}
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-[10px] text-ink-6">难度上限</span>
            {[0, 2, 3, 4, 5].map((level) => (
              <button
                key={level}
                type="button"
                onClick={() => setMaxDifficulty(level)}
                className={`-my-0.5 rounded-control px-1.5 py-1 font-mono text-[10px] ${
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
        <div className="flex shrink-0 items-center justify-between pb-1">
          {/* 全选框放在"题目"左边：它的作用范围就是下面这一列，位置本身在说明这件事 */}
          <label className="flex cursor-pointer items-center gap-1.5 text-[11px] text-ink-6">
            <input
              type="checkbox"
              checked={allChecked}
              onChange={toggleAllChecked}
              disabled={items.length === 0}
              className="h-3 w-3 accent-ng disabled:opacity-40"
              aria-label="全选当前列表的题"
            />
            题目
          </label>
          <span className="font-mono text-[11px] text-ink-4">{items.length}</span>
        </div>

        {/* 批量删除工具条：只有真的勾了题才出现，平时不占版面 */}
        {checkedVisible.size > 0 && (
          <div className="mb-1 flex shrink-0 items-center gap-2 rounded-card border border-ng bg-ng-soft px-2 py-1.5 text-[11px] text-ng">
            <span className="flex-1">
              已选 <span className="font-mono">{checkedVisible.size}</span> 道
            </span>
            <button
              type="button"
              onClick={() => void removeChecked()}
              disabled={deleting}
              className="rounded-control border border-ng px-2 py-0.5 transition-colors hover:bg-surface disabled:opacity-50"
            >
              {deleting ? '删除中…' : '删除选中'}
            </button>
            <button
              type="button"
              onClick={() => setCheckedIds(new Set())}
              className="text-ink-6 hover:underline"
            >
              取消
            </button>
          </div>
        )}

        <div className="space-y-1 pr-1 lg:min-h-0 lg:flex-1 lg:overflow-y-auto">
          {loading && items.length === 0 && <p className="px-1 text-xs text-ink-4">加载中…</p>}
          {!loading && items.length === 0 && (
            <p className="px-1 text-xs text-ink-4">没有符合条件的题。</p>
          )}
          {items.map((item) => {
            const active = item.id === selectedId
            const checked = checkedVisible.has(item.id)
            return (
              /* 外面这层 div 是必须的：勾选框与删除按钮不能塞进"选中这道题"的 button 里
                 （button 里嵌 button/input 是无效结构，点击也会互相打架） */
              <div
                key={item.id}
                className={`flex items-stretch rounded-card border transition-colors ${
                  active
                    ? 'border-ink-9 bg-surface'
                    : 'border-ink-1 bg-surface hover:border-ink-2 hover:bg-paper'
                }`}
              >
                <label
                  className="flex shrink-0 cursor-pointer items-center pl-2"
                  title="勾选后可批量删除"
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleChecked(item.id)}
                    className="h-3 w-3 accent-ng"
                    aria-label={`勾选：${item.text}`}
                  />
                </label>
                <button
                  type="button"
                  onClick={() => setSelectedId(item.id)}
                  className="min-w-0 flex-1 py-2 pr-1 pl-1.5 text-left text-xs"
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
                {/* 单题删除：常显而不是 hover 才出——用户来这个页面的原因之一就是"删不掉"，
                    藏起来等于没有。用低对比的灰色，hover 才变红。 */}
                <button
                  type="button"
                  onClick={() => void removeOne(item)}
                  disabled={deleting}
                  title="删除这道题（连同它的自测记录，不能撤销）"
                  className="shrink-0 self-stretch rounded-r-card px-1.5 text-[10px] text-ink-4 transition-colors hover:bg-ng-soft hover:text-ng disabled:opacity-40"
                >
                  删除
                </button>
              </div>
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
            <p>从左边选一道题开始。题目按「主题」和「难度」筛，也可以用关键词搜。</p>
            <p className="mt-2">
              建议的用法：先自己把答案说一遍（或写下来），再点「看答案」对照要点。
            </p>
            <p className="mt-2">
              想往题库里加题（导入文档、让模型出题）去「训练」页——那里管积累，这里管查阅。
              不要的题就地删：单题点列表右侧的「删除」，多道勾选后走上面的「删除选中」，
              两种都会先让你确认（连带自测记录一起删，不能撤销）。
            </p>
          </div>
        )}

        {detail && (
          <div className="space-y-3 pr-1 lg:min-h-0 lg:flex-1 lg:overflow-y-auto">
            <div className="rounded-panel border border-ink-1 bg-surface p-4">
              <div className="flex flex-wrap items-center gap-2">
                <span className="btn-primary rounded-control px-1.5 py-0.5 text-[10px]">
                  {detail.role_label}
                </span>
                <span className="rounded-control bg-sunken px-1.5 py-0.5 text-[10px] text-ink-6">
                  {detail.category}
                </span>
                {detail.question_type === 'choice' && (
                  <span className="rounded-control bg-ok-soft px-1.5 py-0.5 text-[10px] text-ok">
                    选择题
                  </span>
                )}
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

              {/* 标签编辑：预设「高频 / 易错」一键打上或摘下，自定义标签可加可摘。
                  tags 里第一个元素是分类，剩下的才是标记——所以过滤掉分类再编辑。 */}
              <div className="mt-3 rounded-card border border-ink-1 bg-paper px-3 py-2.5">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[10px] text-ink-4">
                    标签 · 点一下打上 / 摘下（「高频」「易错」或自己加的）
                  </span>
                  {tagError && <span className="text-[10px] text-ng">{tagError}</span>}
                </div>
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                  {PRESET_TAGS.map((tag) => {
                    const active = detail.tags.includes(tag)
                    return (
                      <button
                        key={tag}
                        type="button"
                        disabled={tagSaving}
                        onClick={() => void saveTags(active ? [] : [tag], active ? [tag] : [])}
                        className={`-my-0.5 rounded-control border px-2 py-0.5 text-[10px] transition-colors disabled:opacity-40 ${
                          active
                            ? tag === '高频'
                              ? 'border-warn bg-warn-soft text-warn'
                              : 'border-ng bg-ng-soft text-ng'
                            : 'border-ink-2 bg-surface text-ink-6 hover:bg-paper'
                        }`}
                        title={
                          active
                            ? `摘下「${tag}」`
                            : tag === '高频'
                              ? '多场面试反复问到'
                              : '录音里有人答错或被追问'
                        }
                      >
                        {tag} {active ? '✓' : '+'}
                      </button>
                    )
                  })}
                  {detail.tags
                    .filter((tag) => tag !== detail.category)
                    .filter((tag) => !(PRESET_TAGS as readonly string[]).includes(tag))
                    .map((tag) => (
                      <span
                        key={tag}
                        className="flex items-center gap-1 rounded-control border border-ink-1 bg-surface px-1.5 py-0.5 text-[10px] text-ink-6"
                      >
                        {tag}
                        <button
                          type="button"
                          disabled={tagSaving}
                          onClick={() => void saveTags([], [tag])}
                          className="text-ink-4 transition-colors hover:text-ng disabled:opacity-40"
                          title={`摘下「${tag}」`}
                        >
                          ×
                        </button>
                      </span>
                    ))}
                  <form
                    onSubmit={addCustomTag}
                    className="ml-auto flex items-center gap-1"
                    aria-label="添加自定义标签"
                  >
                    <input
                      type="text"
                      value={tagInput}
                      onChange={(event) => setTagInput(event.target.value)}
                      maxLength={20}
                      placeholder="自定义标签"
                      className="w-24 rounded-control border border-ink-2 bg-surface px-1.5 py-0.5 text-[10px] placeholder:text-ink-3"
                    />
                    <button
                      type="submit"
                      disabled={tagSaving || !tagInput.trim()}
                      className="btn-primary rounded-control px-2 py-0.5 text-[10px]"
                    >
                      添加
                    </button>
                  </form>
                </div>
              </div>

              {!revealed && (
                <div className="mt-4 rounded-card border border-dashed border-ink-2 bg-paper px-3 py-3">
                  <p className="text-xs text-ink-6">
                    先自己答一遍。想不起来的，可以先列出你打算从哪几个角度说，
                    再对照下面的要点看看漏了什么。
                  </p>
                  <button
                    type="button"
                    onClick={() => setRevealed(true)}
                    className="mt-2 btn-primary rounded-control px-3 py-1.5 text-xs"
                  >
                    看答案
                  </button>
                </div>
              )}
            </div>

            {revealed && (
              <>
                {detail.question_type === 'choice' ? (
                  <>
                    <div className="rounded-panel border border-ink-1 bg-surface p-4">
                      <h4 className="text-xs font-medium text-ink-6">选项</h4>
                      <div className="mt-2 space-y-1.5">
                        {(detail.choice_options ?? []).map((option, index) => {
                          const isCorrect = index === detail.correct_index
                          return (
                            <div
                              key={index}
                              className={`flex items-start gap-2 rounded-control border px-2.5 py-2 text-xs leading-relaxed ${
                                isCorrect
                                  ? 'border-ok bg-ok-soft text-ok'
                                  : 'border-ink-1 bg-paper text-ink-7'
                              }`}
                            >
                              <span className="mt-px font-mono text-[10px] text-ink-4">
                                {String.fromCharCode(65 + index)}
                              </span>
                              <span className="min-w-0 flex-1">{option}</span>
                              {isCorrect && (
                                <span className="shrink-0 text-[10px] font-medium">✓ 正确答案</span>
                              )}
                            </div>
                          )
                        })}
                      </div>
                      {detail.explanation && (
                        <div className="mt-3">
                          <h4 className="text-xs font-medium text-ink-6">解析</h4>
                          <div className="mt-1 whitespace-pre-wrap text-xs leading-relaxed text-ink-7">
                            {detail.explanation}
                          </div>
                        </div>
                      )}
                    </div>

                    {(detail.points.length > 0 || detail.answer) && (
                      <div className="rounded-panel border border-ink-1 bg-surface p-4">
                        <h4 className="text-xs font-medium text-ink-6">完整解答</h4>
                        {detail.points.length > 0 && (
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
                        )}
                        {detail.answer && (
                          <div className="mt-2">
                            <Markdown text={detail.answer} />
                          </div>
                        )}
                      </div>
                    )}
                  </>
                ) : (
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
                  </>
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
