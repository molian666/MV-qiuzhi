/** 让模型出题的面板。
 *
 * 流程是「生成 → 预览 → 决定要不要」：
 *
 * 1. **生成**——按岗位/主题/难度出一批；
 * 2. **继续生成**——在**同一批**上追加，已出的题干会带给模型要求它别重复；
 *    连点几次就会得到一批越来越大的草稿，而不是一堆近义题。
 * 3. **重新生成**——丢掉这批，按同样的参数重出（对结果不满意时的动作）；
 * 4. **入库**——两种粒度：**逐题**「添加」（只满意其中几道时用），
 *    或整批「全部添加」。
 *
 * 关键点：生成的题**先以草稿存在，不进抽题池**。所以「是否进题库」
 * 是一个明确的动作，而不是靠默认行为——没采纳的题绝不会在模拟面试里
 * 被抽到。这样你永远不用担心模型生成的东西悄悄混进面试。
 *
 * 「入库」与草稿列表是同一件事的两面：采纳一道就把它从列表里移掉一道，
 * 视觉上直接对上，不用额外记"哪道已经处理过了"。
 */

import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import type { VisionDraft, VisionGenerateResult, VisionMeta, VisionQuestionBrief } from '../api/types'

const DIFFICULTY_LABELS: Record<number, string> = {
  1: '1 入门',
  2: '2',
  3: '3 中等',
  4: '4',
  5: '5 资深',
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export default function QuestionGenerator({
  meta,
  onChanged,
}: {
  meta: VisionMeta | null
  onChanged: () => void
}) {
  const [role, setRole] = useState('development')
  const [category, setCategory] = useState('')
  const [difficulty, setDifficulty] = useState(0)
  const [count, setCount] = useState(3)
  const [note, setNote] = useState('')

  const [drafts, setDrafts] = useState<VisionDraft[]>([])
  const [current, setCurrent] = useState<VisionGenerateResult | null>(null)
  const [busy, setBusy] = useState<'idle' | 'generating' | 'adopting'>('idle')
  /** 正在逐题入库的题目 id，用来只禁用那一个按钮 */
  const [adoptingId, setAdoptingId] = useState<number | null>(null)
  const [error, setError] = useState('')

  const loadDrafts = useCallback(async () => {
    try {
      setDrafts(await api.visionDrafts())
    } catch {
      setDrafts([])
    }
  }, [])

  useEffect(() => {
    void loadDrafts()
  }, [loadDrafts])

  const run = async (mode: 'new' | 'append' | 'replace') => {
    setBusy('generating')
    setError('')
    try {
      const result = await api.visionGenerate({
        role,
        count,
        category: category || null,
        difficulty: difficulty || null,
        mode,
        batch: mode === 'new' ? null : (current?.batch ?? drafts[0]?.batch ?? null),
        note,
      })
      setCurrent(result)
      await loadDrafts()
      onChanged()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy('idle')
    }
  }

  const adopt = async () => {
    if (!current?.batch) {
      return
    }
    setBusy('adopting')
    setError('')
    try {
      await api.visionAdoptDraft(current.batch)
      setCurrent(null)
      await loadDrafts()
      onChanged()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy('idle')
    }
  }

  /** 逐题入库：只把这一道放进抽题池，同批其余仍是草稿。 */
  const adoptOne = async (questionId: number) => {
    setAdoptingId(questionId)
    setError('')
    try {
      await api.visionAdoptQuestions([questionId])
      setCurrent((previous) =>
        previous
          ? { ...previous, drafts: previous.drafts.filter((item) => item.id !== questionId) }
          : previous,
      )
      await loadDrafts()
      onChanged()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setAdoptingId(null)
    }
  }

  const discard = async (batch: string) => {
    setError('')
    try {
      await api.visionDiscardDraft(batch)
      if (current?.batch === batch) {
        setCurrent(null)
      }
      await loadDrafts()
      onChanged()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const draftQuestions: VisionQuestionBrief[] = current?.drafts ?? []
  const hasBatch = Boolean(current?.batch)

  return (
    <div className="rounded-card border border-ink-1 bg-surface p-3">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h4 className="text-xs font-medium text-ink-7">让模型出题</h4>
          <p className="mt-1 text-[10px] leading-relaxed text-ink-4">
            生成的题先存为草稿，<strong className="font-medium text-ink-7">不会进抽题池</strong>
            ——你觉得能用，再放进题库。
          </p>
        </div>
        {(meta?.draft_count ?? 0) > 0 && (
          <span className="shrink-0 rounded-control bg-warn-soft px-1.5 py-0.5 text-[10px] text-warn">
            待采纳 {meta?.draft_count} 题
          </span>
        )}
      </div>

      <div className="mt-2 flex flex-wrap items-end gap-2 text-[11px]">
        <label className="text-ink-6">
          岗位
          <select
            className="ml-1 rounded-control border border-ink-2 px-1.5 py-1"
            value={role}
            onChange={(event) => setRole(event.target.value)}
          >
            {(meta?.roles ?? [])
              .filter((item) => item.key !== 'uploaded')
              .map((item) => (
                <option key={item.key} value={item.key}>
                  {item.label}
                </option>
              ))}
          </select>
        </label>
        <label className="text-ink-6">
          主题
          <select
            className="ml-1 rounded-control border border-ink-2 px-1.5 py-1"
            value={category}
            onChange={(event) => setCategory(event.target.value)}
          >
            <option value="">不限</option>
            {(meta?.categories ?? []).map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <label className="text-ink-6">
          难度
          <select
            className="ml-1 rounded-control border border-ink-2 px-1.5 py-1"
            value={difficulty}
            onChange={(event) => setDifficulty(Number(event.target.value))}
          >
            <option value={0}>不限</option>
            {[1, 2, 3, 4, 5].map((level) => (
              <option key={level} value={level}>
                {DIFFICULTY_LABELS[level]}
              </option>
            ))}
          </select>
        </label>
        <label className="text-ink-6">
          数量
          <input
            type="number"
            min={1}
            max={12}
            className="ml-1 w-14 rounded-control border border-ink-2 px-1.5 py-1 font-mono"
            value={count}
            onChange={(event) =>
              setCount(Math.max(1, Math.min(12, Number(event.target.value) || 1)))
            }
          />
        </label>
      </div>

      <input
        type="text"
        value={note}
        onChange={(event) => setNote(event.target.value)}
        placeholder="补充要求（可选），如「多出一些打光和选型相关的」"
        className="mt-2 w-full rounded-control border border-ink-2 px-2 py-1.5 text-[11px] placeholder:text-ink-3"
      />

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => void run('new')}
          disabled={busy !== 'idle'}
          className="rounded-control bg-ink-9 px-3 py-1.5 text-[11px] text-surface transition-opacity hover:opacity-90 disabled:bg-ink-2"
        >
          {busy === 'generating' ? '生成中…' : '生成一批'}
        </button>

        {hasBatch && (
          <>
            <button
              type="button"
              onClick={() => void run('append')}
              disabled={busy !== 'idle'}
              className="rounded-control border border-ink-2 bg-surface px-3 py-1.5 text-[11px] text-ink-7 transition-colors hover:bg-paper disabled:text-ink-2"
              title="在同一批上追加，已出的题干会带给模型要求它别重复"
            >
              继续生成
            </button>
            <button
              type="button"
              onClick={() => void run('replace')}
              disabled={busy !== 'idle'}
              className="rounded-control border border-ink-2 bg-surface px-3 py-1.5 text-[11px] text-ink-7 transition-colors hover:bg-paper disabled:text-ink-2"
              title="丢掉这一批，按同样的参数重新生成"
            >
              重新生成
            </button>
            <button
              type="button"
              onClick={() => void discard(current?.batch ?? '')}
              disabled={busy !== 'idle'}
              className="rounded-control border border-ng bg-surface px-3 py-1.5 text-[11px] text-ng transition-colors hover:bg-ng-soft disabled:text-ink-2"
            >
              丢弃这批
            </button>
          </>
        )}
      </div>

      {error && (
        <div className="mt-2 rounded-control border border-ng bg-ng-soft px-2 py-1.5 text-[11px] text-ng">
          {error}
        </div>
      )}

      {current && (
        <div className="mt-2 rounded-control border border-ink-1 bg-paper px-2.5 py-2 text-[11px] leading-relaxed text-ink-6">
          本次新增 {current.added} 题
          {current.skipped > 0 && `，跳过 ${current.skipped} 题（题干与题库已有重复）`}
          {current.note && `。${current.note}`}
        </div>
      )}

      {/* 草稿清单：每道都能单独入库，也能整批入库 */}
      {draftQuestions.length > 0 && (
        <div className="mt-2 border-t border-ink-1 pt-2">
          <div className="mb-1 flex items-center justify-between gap-2">
            <span className="text-[10px] text-ink-6">
              这一批还有 <span className="font-mono">{draftQuestions.length}</span> 道没入库
            </span>
            <button
              type="button"
              onClick={() => void adopt()}
              disabled={busy !== 'idle' || adoptingId !== null}
              className="rounded-control bg-ok px-2.5 py-1 text-[11px] text-surface transition-opacity hover:opacity-90 disabled:bg-ink-2"
              title="这一批全部放进题库，之后模拟面试可能抽到"
            >
              {busy === 'adopting' ? '入库中…' : '全部添加'}
            </button>
          </div>
          <div className="max-h-52 space-y-1 overflow-y-auto pr-1">
            {draftQuestions.map((item) => (
              <div
                key={item.id}
                className="flex items-start gap-2 rounded-control border border-ink-1 bg-surface px-2 py-1.5"
              >
                <div className="min-w-0 flex-1">
                  <div className="text-[11px] leading-snug text-ink-7">{item.text}</div>
                  <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[10px] text-ink-4">
                    <span className="font-mono">{item.difficulty}</span>
                    <span>{item.category}</span>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => void adoptOne(item.id)}
                  disabled={adoptingId !== null || busy !== 'idle'}
                  className="shrink-0 rounded-control border border-ok px-2 py-0.5 text-[10px] text-ok transition-colors hover:bg-ok-soft disabled:border-ink-1 disabled:text-ink-3"
                >
                  {adoptingId === item.id ? '…' : '添加'}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {drafts.length > 0 && !current && (
        <div className="mt-2 space-y-1 border-t border-ink-1 pt-2">
          <div className="text-[10px] text-ink-6">之前没处理完的批次</div>
          {drafts.map((item) => (
            <div key={item.batch} className="flex items-center gap-2 text-[11px]">
              <span className="flex-1 text-ink-6">
                批次 <span className="font-mono">{item.batch}</span>
              </span>
              <span className="font-mono text-ink-4">{item.count}</span>
              <button
                type="button"
                onClick={() => void discard(item.batch)}
                className="text-ng hover:underline"
              >
                丢弃
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
