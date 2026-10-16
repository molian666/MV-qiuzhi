/** 让模型出题的面板。
 *
 * 流程是「生成 → 预览 → 决定要不要」：
 *
 * 1. **生成**——按岗位/难度/题型/领域出一批；
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
import type { VisionDraft, VisionGenerateResult, VisionMeta, VisionQuestion, VisionQuestionBrief } from '../api/types'

const DIFFICULTY_LABELS: Record<number, string> = {
  1: '1 入门',
  2: '2',
  3: '3 中等',
  4: '4',
  5: '5 资深',
}

/** 更细的领域方向。配合岗位/难度/题型拼进出题约束，把题目收窄到具体场景或技术栈。 */
const DOMAIN_OPTIONS = [
  '不限',
  '打光与光源',
  '镜头与光学',
  '相机与传感器',
  '标定与坐标',
  '定位与引导',
  '缺陷检测',
  'OCR 与测量',
  '运动控制',
  '深度学习模型',
  '软件架构与工具',
  'C#',
  'VisionPRO',
  'VisionMaster',
  'Halcon',
  'OpenCV',
  '现场实施',
]

const OPTION_LABELS = ['A', 'B', 'C', 'D', 'E', 'F']

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
  const [difficulty, setDifficulty] = useState(0)
  const [questionType, setQuestionType] = useState<'open' | 'choice'>('open')
  const [domain, setDomain] = useState('')
  const [count, setCount] = useState(3)
  const [note, setNote] = useState('')

  const [drafts, setDrafts] = useState<VisionDraft[]>([])
  const [current, setCurrent] = useState<VisionGenerateResult | null>(null)
  const [busy, setBusy] = useState<'idle' | 'generating' | 'adopting'>('idle')
  /** 正在逐题入库的题目 id，用来只禁用那一个按钮 */
  const [adoptingId, setAdoptingId] = useState<number | null>(null)
  const [error, setError] = useState('')

  // 草稿作答（生成后先答一答，答完看答案与解析）
  const [answeringId, setAnsweringId] = useState<number | null>(null)
  const [answerDetail, setAnswerDetail] = useState<VisionQuestion | null>(null)
  const [answerBusy, setAnswerBusy] = useState(false)
  /** 选择题：选中的选项下标；null = 还没选 */
  const [choicePick, setChoicePick] = useState<number | null>(null)
  /** 问答题：输入框内容 */
  const [openAnswer, setOpenAnswer] = useState('')
  /** 是否已提交作答（提交后锁定选项并展示答案/解析） */
  const [answered, setAnswered] = useState(false)

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
        difficulty: difficulty || null,
        question_type: questionType,
        domain: domain === '不限' ? null : domain || null,
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

  /** 展开作答区：按题加载详情（选项与答案只在详情里给，列表页不泄露）。 */
  const openAnswerPanel = async (questionId: number) => {
    if (answeringId === questionId) {
      // 再次点击收起
      setAnsweringId(null)
      setAnswerDetail(null)
      setChoicePick(null)
      setOpenAnswer('')
      setAnswered(false)
      return
    }
    setAnsweringId(questionId)
    setAnswerDetail(null)
    setChoicePick(null)
    setOpenAnswer('')
    setAnswered(false)
    setAnswerBusy(true)
    setError('')
    try {
      setAnswerDetail(await api.visionQuestion(questionId))
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setAnswerBusy(false)
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
          题型
          <select
            className="ml-1 rounded-control border border-ink-2 px-1.5 py-1"
            value={questionType}
            onChange={(event) => setQuestionType(event.target.value as 'open' | 'choice')}
          >
            <option value="open">问答</option>
            <option value="choice">选择题</option>
          </select>
        </label>
        <label className="text-ink-6">
          领域
          <select
            className="ml-1 rounded-control border border-ink-2 px-1.5 py-1"
            value={domain}
            onChange={(event) => setDomain(event.target.value)}
          >
            {DOMAIN_OPTIONS.map((item) => (
              <option key={item} value={item}>
                {item}
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
          className="btn-primary rounded-control px-3 py-1.5 text-[11px]"
        >
          {busy === 'generating' ? '生成中…' : '生成一批'}
        </button>

        {hasBatch && (
          <>
            <button
              type="button"
              onClick={() => void run('append')}
              disabled={busy !== 'idle'}
              className="rounded-control border border-ink-2 bg-surface px-3 py-1.5 text-[11px] text-ink-7 transition-colors hover:bg-paper disabled:border-ink-1 disabled:bg-sunken disabled:text-ink-6"
              title="在同一批上追加，已出的题干会带给模型要求它别重复"
            >
              继续生成
            </button>
            <button
              type="button"
              onClick={() => void run('replace')}
              disabled={busy !== 'idle'}
              className="rounded-control border border-ink-2 bg-surface px-3 py-1.5 text-[11px] text-ink-7 transition-colors hover:bg-paper disabled:border-ink-1 disabled:bg-sunken disabled:text-ink-6"
              title="丢掉这一批，按同样的参数重新生成"
            >
              重新生成
            </button>
            <button
              type="button"
              onClick={() => void discard(current?.batch ?? '')}
              disabled={busy !== 'idle'}
              className="rounded-control border border-ng bg-surface px-3 py-1.5 text-[11px] text-ng transition-colors hover:bg-ng-soft disabled:border-ink-1 disabled:bg-sunken disabled:text-ink-6"
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
              className="rounded-control bg-ok px-2.5 py-1 text-[11px] text-surface transition-opacity hover:opacity-90 disabled:border-ink-1 disabled:bg-sunken disabled:text-ink-6"
              title="这一批全部放进题库，之后模拟面试可能抽到"
            >
              {busy === 'adopting' ? '入库中…' : '全部添加'}
            </button>
          </div>
          <div className="max-h-72 space-y-1 overflow-y-auto pr-1">
            {draftQuestions.map((item) => {
              const isChoice = item.question_type === 'choice'
              const expanded = answeringId === item.id
              const correct =
                answerDetail != null && choicePick != null && choicePick === answerDetail.correct_index
              return (
                <div key={item.id}>
                  <div className="flex items-start gap-2 rounded-control border border-ink-1 bg-surface px-2 py-1.5">
                    <div className="min-w-0 flex-1">
                      <div className="text-[11px] leading-snug text-ink-7">{item.text}</div>
                      <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[10px] text-ink-4">
                        <span className="font-mono">{item.difficulty}</span>
                        <span>{item.category}</span>
                        {isChoice && (
                          <span className="rounded-control bg-ok-soft px-1 py-px text-ok">选择题</span>
                        )}
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => void openAnswerPanel(item.id)}
                      disabled={answerBusy}
                      className="shrink-0 rounded-control border border-ink-2 px-2 py-0.5 text-[10px] text-ink-7 transition-colors hover:bg-paper disabled:border-ink-1 disabled:bg-sunken disabled:text-ink-6"
                      title="先答一答，答完看答案与解析"
                    >
                      {expanded ? '收起' : '答题'}
                    </button>
                    <button
                      type="button"
                      onClick={() => void adoptOne(item.id)}
                      disabled={adoptingId !== null || busy !== 'idle'}
                      className="shrink-0 rounded-control border border-ok px-2 py-0.5 text-[10px] text-ok transition-colors hover:bg-ok-soft disabled:border-ink-1 disabled:bg-sunken disabled:text-ink-6"
                    >
                      {adoptingId === item.id ? '…' : '添加'}
                    </button>
                  </div>

                  {/* 作答区：选择题点选项判对错；问答题输入后看参考答案与解析 */}
                  {expanded && (
                    <div className="mt-1 rounded-control border border-ink-1 bg-paper px-2.5 py-2 text-[11px] leading-relaxed">
                      {answerBusy ? (
                        <div className="text-ink-4">加载中…</div>
                      ) : !answerDetail ? (
                        <div className="text-ng">没拿到这道题的详情，请稍后重试。</div>
                      ) : isChoice ? (
                        <div className="space-y-1.5">
                          {!answered ? (
                            <div className="grid gap-1">
                              {answerDetail.choice_options?.map((option, index) => (
                                <button
                                  key={index}
                                  type="button"
                                  onClick={() => {
                                    setChoicePick(index)
                                    setAnswered(true)
                                  }}
                                  className="flex items-start gap-1.5 rounded-control border border-ink-2 bg-surface px-2 py-1 text-left text-[11px] text-ink-7 transition-colors hover:bg-ok-soft"
                                >
                                  <span className="font-mono text-ink-4">
                                    {OPTION_LABELS[index] ?? index + 1}
                                  </span>
                                  <span className="min-w-0 flex-1">{option}</span>
                                </button>
                              ))}
                            </div>
                          ) : (
                            <div>
                              <div
                                className={`rounded-control px-2 py-1.5 ${
                                  correct
                                    ? 'bg-ok-soft text-ok'
                                    : 'bg-ng-soft text-ng'
                                }`}
                              >
                                {correct
                                  ? `答对了 ✓ 正确答案是 ${OPTION_LABELS[choicePick ?? 0] ?? ''}`
                                  : `答错了。正确答案是 ${
                                      OPTION_LABELS[answerDetail.correct_index ?? 0] ?? ''
                                    }`}
                              </div>
                              <div className="mt-2">
                                <div className="text-[10px] font-medium text-ink-6">解析</div>
                                <div className="whitespace-pre-wrap text-ink-7">
                                  {answerDetail.explanation || '（没有解析）'}
                                </div>
                              </div>
                              {(answerDetail.answer || answerDetail.points.length > 0) && (
                                <div className="mt-2 border-t border-ink-1 pt-1.5">
                                  <div className="text-[10px] font-medium text-ink-6">
                                    完整解答
                                  </div>
                                  {answerDetail.points.length > 0 && (
                                    <ul className="mt-1 list-disc space-y-0.5 pl-4 text-ink-7">
                                      {answerDetail.points.map((point, index) => (
                                        <li key={index}>{point}</li>
                                      ))}
                                    </ul>
                                  )}
                                  {answerDetail.answer && (
                                    <div className="mt-1 whitespace-pre-wrap text-ink-7">
                                      {answerDetail.answer}
                                    </div>
                                  )}
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      ) : (
                        <div className="space-y-1.5">
                          {!answered ? (
                            <>
                              <textarea
                                value={openAnswer}
                                onChange={(event) => setOpenAnswer(event.target.value)}
                                placeholder="在脑子里默答一遍，再写下你的回答（不用写太长）"
                                rows={3}
                                className="w-full rounded-control border border-ink-2 bg-surface px-2 py-1.5 text-[11px] placeholder:text-ink-3"
                              />
                              <button
                                type="button"
                                onClick={() => setAnswered(true)}
                                className="rounded-control border border-ink-2 bg-surface px-2.5 py-1 text-[11px] text-ink-7 transition-colors hover:bg-paper"
                              >
                                看答案
                              </button>
                            </>
                          ) : (
                            <div>
                              {openAnswer.trim() && (
                                <div className="rounded-control bg-sunken px-2 py-1.5 text-ink-6">
                                  <span className="text-[10px] text-ink-4">你的回答：</span>
                                  <span className="whitespace-pre-wrap">{openAnswer}</span>
                                </div>
                              )}
                              <div className="mt-2">
                                <div className="text-[10px] font-medium text-ink-6">参考答案</div>
                                {answerDetail.points.length > 0 && (
                                  <ul className="mt-1 list-disc space-y-0.5 pl-4 text-ink-7">
                                    {answerDetail.points.map((point, index) => (
                                      <li key={index}>{point}</li>
                                    ))}
                                  </ul>
                                )}
                                {answerDetail.answer && (
                                  <div className="mt-1 whitespace-pre-wrap text-ink-7">
                                    {answerDetail.answer}
                                  </div>
                                )}
                              </div>
                              {answerDetail.explanation && (
                                <div className="mt-2 border-t border-ink-1 pt-1.5">
                                  <div className="text-[10px] font-medium text-ink-6">解析</div>
                                  <div className="mt-0.5 whitespace-pre-wrap text-ink-7">
                                    {answerDetail.explanation}
                                  </div>
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
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
