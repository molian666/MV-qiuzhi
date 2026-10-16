/** 训练页：**薄弱点训练**与**题库积累**都在这里。
 *
 * 职责边界（与「面试题题库」分开）：
 *
 * - 题库页只管**查阅**：找题、看答案、自评分。
 * - 这里管**练**与**攒**：按薄弱点出题并批改（掌握度随作答更新），
 *   以及往题库里加题——导入文档、让模型出题、决定采纳哪些。
 *
 * 为什么"练"和"攒"放一起：两者都围绕同一件事——你缺什么。
 * 薄弱点告诉你缺什么，积累则补上它；分开两页会让"补"这个动作失去上下文。
 */

import { useCallback, useEffect, useState } from 'react'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '../api/client'
import type {
  MasteryEvent,
  MasteryOverview,
  TrainingGrade,
  TrainingQuestion,
  VisionImportResult,
  VisionMeta,
  VisionUpload,
} from '../api/types'
import QuestionGenerator from '../components/QuestionGenerator'

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function formatTime(value: string | null): string {
  if (!value) {
    return '—'
  }
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-card border border-ink-1 bg-surface px-3 py-2">
      <p className="text-[11px] text-ink-4">{label}</p>
      <p className="mt-0.5 font-mono text-xl font-semibold text-ink-9">{value}</p>
      {hint && <p className="text-[10px] text-ink-4">{hint}</p>}
    </div>
  )
}

export default function Training() {
  const [overview, setOverview] = useState<MasteryOverview | null>(null)
  const [questions, setQuestions] = useState<TrainingQuestion[]>([])
  const [activeId, setActiveId] = useState<number | null>(null)
  const [draft, setDraft] = useState('')
  const [grades, setGrades] = useState<Record<number, TrainingGrade>>({})
  const [history, setHistory] = useState<{
    name: string
    points: { label: string; mastery: number }[]
  } | null>(null)
  const [loading, setLoading] = useState(true)
  const [grading, setGrading] = useState(false)
  const [error, setError] = useState('')

  // 题库积累：让模型出题要一份 meta，导入要一份批次列表
  const [meta, setMeta] = useState<VisionMeta | null>(null)
  const [uploads, setUploads] = useState<VisionUpload[]>([])
  const [importing, setImporting] = useState(false)
  const [importResult, setImportResult] = useState<VisionImportResult | null>(null)
  const [importWarnings, setImportWarnings] = useState<string[]>([])

  const refreshOverview = useCallback(async () => {
    try {
      setOverview(await api.masteryOverview())
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [])

  const refreshBank = useCallback(async () => {
    try {
      setMeta(await api.visionMeta())
      setUploads(await api.visionUploads())
    } catch {
      // 题库元信息拿不到不影响训练主流程，安静降级
      setUploads([])
    }
  }, [])

  const loadQuestions = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setQuestions(await api.nextTraining(4, 3))
      setGrades({})
      setActiveId(null)
      setDraft('')
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refreshOverview()
    void loadQuestions()
    void refreshBank()
  }, [refreshOverview, loadQuestions, refreshBank])

  const submit = async () => {
    if (activeId === null || !draft.trim()) {
      return
    }
    setGrading(true)
    setError('')
    try {
      const grade = await api.gradeTraining(activeId, draft.trim())
      setGrades((previous) => ({ ...previous, [grade.question_id]: grade }))
      setDraft('')
      await refreshOverview()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setGrading(false)
    }
  }

  const openHistory = async (topicId: number, name: string) => {
    try {
      const events = await api.masteryHistory(topicId)
      setHistory({
        name,
        points: events.map((event: MasteryEvent, index) => ({
          label: `#${index + 1}`,
          mastery: Math.round(event.mastery_after * 10) / 10,
        })),
      })
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const handleImport = async (file: File | undefined) => {
    if (!file) {
      return
    }
    setImporting(true)
    setImportResult(null)
    setImportWarnings([])
    setError('')
    try {
      const result = await api.visionImport(file)
      setImportResult(result)
      setImportWarnings(result.warnings)
      await refreshBank()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setImporting(false)
    }
  }

  const handleDeleteBatch = async (batch: string) => {
    setError('')
    try {
      await api.visionDeleteUpload(batch)
      await refreshBank()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  return (
    <div className="flex min-h-0 flex-col gap-3 lg:h-full">
      {/* M22 手机端：手机上（<lg）不锁高、不内滚——整页自然铺开由 main 统一滚动，
          不再出现"上半屏一个滚动条、下半屏又一个滚动条"的多块滑动。
          lg 以上恢复桌面式：锁高 + 面板各自内滚。 */}
      <section className="grid shrink-0 gap-2 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="平均掌握度" value={`${overview?.average_mastery ?? 0}`} hint="0–100，越高越稳" />
        <Stat
          label="高危薄弱点"
          value={`${overview?.high_risk_count ?? 0}`}
          hint="低于 40 或连续两次低分"
        />
        <Stat label="待复习" value={`${overview?.due_count ?? 0}`} hint="SM-2 已到期" />
        <Stat
          label="能力标签"
          value={`${overview?.items.length ?? 0}`}
          hint="来自面试报告的改进项"
        />
      </section>

      {error && (
        <p className="shrink-0 rounded-control bg-ng-soft px-3 py-2 text-xs text-ng">{error}</p>
      )}

      <div className="grid gap-4 lg:min-h-0 lg:flex-1 lg:grid-cols-[minmax(0,1fr)_minmax(300px,360px)]">
        {/* 今日训练：按薄弱点推题，作答后模型批改并更新掌握度 */}
        <section className="flex min-h-0 flex-col">
          <div className="flex shrink-0 items-center justify-between pb-2">
            <h3 className="text-sm font-semibold text-ink-9">今日训练</h3>
            <button
              type="button"
              onClick={() => void loadQuestions()}
              disabled={loading}
              className="rounded-control border border-ink-2 px-3 py-1.5 text-xs text-ink-7 transition-colors hover:bg-paper disabled:opacity-40"
            >
              换一批
            </button>
          </div>

          <div className="space-y-2 pr-1 lg:min-h-0 lg:flex-1 lg:overflow-y-auto">
            {loading ? (
              <p className="rounded-panel border border-ink-1 bg-surface px-4 py-6 text-sm text-ink-6">
                正在组题…
              </p>
            ) : questions.length === 0 ? (
              <p className="rounded-panel border border-dashed border-ink-2 px-4 py-6 text-sm leading-relaxed text-ink-6">
                暂时没有可用题目。先去「面试间」跑一场面试并生成报告，
                系统就会按薄弱点出题；也可以直接在右边让模型出题或导入自己的题库。
              </p>
            ) : (
              questions.map((question) => {
                const grade = grades[question.question_id]
                const isActive = question.question_id === activeId
                return (
                  <article
                    key={question.question_id}
                    className="rounded-panel border border-ink-1 bg-surface p-3.5"
                  >
                    <header className="mb-2 flex flex-wrap items-center gap-1.5 text-xs">
                      <span
                        className={
                          question.kind === 'weak'
                            ? 'rounded-control border border-ng bg-ng-soft px-1.5 py-0.5 text-ng'
                            : 'rounded-control border border-ink-1 bg-paper px-1.5 py-0.5 text-ink-6'
                        }
                      >
                        {question.kind === 'weak' ? '薄弱点' : '换换脑子'}
                      </span>
                      <span className="rounded-control bg-sunken px-1.5 py-0.5 font-mono text-[10px] text-ink-6">
                        难度 {question.difficulty}
                      </span>
                      <span className="text-[10px] text-ink-4">
                        {question.origin === 'generated' ? '新生成' : '题库'}
                      </span>
                      {question.topic_names.map((name) => (
                        <span
                          key={name}
                          className="rounded-control bg-info-soft px-1.5 py-0.5 text-[10px] text-info"
                        >
                          {name}
                        </span>
                      ))}
                    </header>

                    <p className="text-sm font-medium leading-relaxed text-ink-9">{question.text}</p>

                    <details className="mt-1 text-[11px] text-ink-6">
                      <summary className="cursor-pointer">为什么推荐这题</summary>
                      <ul className="mt-1 space-y-0.5 pl-4 font-mono">
                        <li>薄弱度：{question.reason.weakness?.toFixed(2)}</li>
                        <li>岗位匹配：{question.reason.jd_match?.toFixed(2)}</li>
                        <li>到期紧迫：{question.reason.due_urgency?.toFixed(2)}</li>
                        <li>难度适配：{question.reason.difficulty_fit?.toFixed(2)}</li>
                        <li>题型罚项：{question.reason.type_penalty?.toFixed(2)}</li>
                        <li className="font-medium text-ink-7">合计：{question.reason.total?.toFixed(2)}</li>
                      </ul>
                    </details>

                    {grade ? (
                      <div className="mt-2.5 space-y-2 rounded-card bg-paper px-3 py-2">
                        <p className="text-sm">
                          <span className="font-mono font-semibold text-ink-9">{grade.score}</span>
                          <span className="text-ink-6"> / 5</span>
                          {grade.comment && <span className="ml-2 text-ink-7">{grade.comment}</span>}
                        </p>
                        {grade.better_answer && (
                          <p className="text-xs leading-relaxed text-ink-7">
                            <span className="font-medium text-ink-6">示范：</span>
                            {grade.better_answer}
                          </p>
                        )}
                        {grade.mastery.length > 0 && (
                          <p className="text-[11px] text-ink-6">
                            掌握度变化：
                            {grade.mastery
                              .map(
                                (change) =>
                                  `${change.name} ${Math.round(change.before)} → ${Math.round(change.after)}`,
                              )
                              .join('；')}
                          </p>
                        )}
                      </div>
                    ) : isActive ? (
                      <div className="mt-2.5 space-y-2">
                        <textarea
                          value={draft}
                          onChange={(event) => setDraft(event.target.value)}
                          rows={4}
                          placeholder="像面试那样口头组织，再敲进来；写完后点提交"
                          className="w-full resize-none rounded-card border border-ink-2 px-3 py-2 text-sm outline-none placeholder:text-ink-3 focus:border-info"
                        />
                        <div className="flex gap-2">
                          <button
                            type="button"
                            onClick={() => void submit()}
                            disabled={grading || !draft.trim()}
                            className="btn-primary rounded-control px-3 py-1.5 text-xs font-medium"
                          >
                            {grading ? '批改中…' : '提交并批改'}
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              setActiveId(null)
                              setDraft('')
                            }}
                            className="rounded-control border border-ink-2 px-3 py-1.5 text-xs text-ink-7 transition-colors hover:bg-paper"
                          >
                            先跳过
                          </button>
                        </div>
                      </div>
                    ) : (
                      <button
                        type="button"
                        onClick={() => {
                          setActiveId(question.question_id)
                          setDraft('')
                        }}
                        className="mt-2.5 rounded-control bg-info px-3 py-1.5 text-xs font-medium text-surface transition-opacity hover:opacity-90"
                      >
                        开始作答
                      </button>
                    )}
                  </article>
                )
              })
            )}
          </div>
        </section>

        {/* 右栏：掌握度 + 题库积累 */}
        <aside className="flex flex-col gap-3 pr-1 lg:min-h-0 lg:overflow-y-auto">
          {history && (
            <div className="rounded-panel border border-ink-1 bg-surface p-3.5">
              <div className="mb-2 flex items-center justify-between gap-2">
                <h3 className="text-xs font-medium text-ink-7">{history.name} · 掌握度曲线</h3>
                <button
                  type="button"
                  onClick={() => setHistory(null)}
                  className="text-xs text-ink-6 hover:underline"
                >
                  收起
                </button>
              </div>
              {history.points.length === 0 ? (
                <p className="text-xs text-ink-6">还没有记录。</p>
              ) : (
                <div className="h-36">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={history.points}>
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--color-ink-1)" />
                      <XAxis dataKey="label" tick={{ fontSize: 10 }} />
                      <YAxis domain={[0, 100]} tick={{ fontSize: 10 }} width={28} />
                      <Tooltip />
                      <Line type="monotone" dataKey="mastery" stroke="var(--color-info)" dot />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              )}
            </div>
          )}

          <div className="rounded-panel border border-ink-1 bg-surface p-3.5">
            <h3 className="mb-2 text-xs font-medium text-ink-7">
              能力掌握度（<span className="font-mono">{overview?.items.length ?? 0}</span>）
            </h3>
            {!overview || overview.items.length === 0 ? (
              <p className="text-xs leading-relaxed text-ink-6">
                还没有数据。生成一份面试报告后，这里会按薄弱程度排序。
              </p>
            ) : (
              <div className="space-y-1.5 pr-1 lg:max-h-64 lg:overflow-y-auto">
                {overview.items.map((item) => (
                  <button
                    key={item.topic_id}
                    type="button"
                    onClick={() => void openHistory(item.topic_id, item.name)}
                    className="block w-full rounded-card px-2 py-1.5 text-left transition-colors hover:bg-paper"
                  >
                    <div className="flex items-baseline justify-between gap-2 text-xs">
                      <span className={item.high_risk ? 'text-ng' : 'text-ink-7'}>
                        {item.high_risk && <span className="mr-1">!</span>}
                        {item.name}
                      </span>
                      <span className="font-mono text-ink-6">{Math.round(item.mastery)}</span>
                    </div>
                    <div className="mt-1 h-1.5 overflow-hidden rounded-control bg-sunken">
                      <div
                        className={item.high_risk ? 'h-full bg-ng' : 'h-full bg-ok'}
                        style={{ width: `${Math.max(2, Math.min(100, item.mastery))}%` }}
                      />
                    </div>
                    <p className="mt-0.5 text-[10px] text-ink-4">
                      练过 <span className="font-mono">{item.attempts}</span> 次 ·{' '}
                      {formatTime(item.updated_at)}
                    </p>
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* 往题库里加题：生成与导入都在这里 */}
          <QuestionGenerator
            meta={meta}
            onChanged={() => {
              void refreshBank()
            }}
          />

          <div className="rounded-card border border-ink-1 bg-surface p-3">
            <h4 className="text-xs font-medium text-ink-7">导入自己的题库</h4>
            <p className="mt-1 text-[10px] leading-relaxed text-ink-4">
              <strong className="font-medium text-ink-6">整理好的面经</strong>（分章节 +
              加粗题干 <code className="rounded-control bg-sunken px-0.5">**Q1：问题？**</code>）会被精确解析：
              章节变分类，<code className="rounded-control bg-sunken px-0.5">【高频】</code>
              <code className="rounded-control bg-sunken px-0.5">【易错】</code> 变标签，
              <code className="rounded-control bg-sunken px-0.5">出处：</code> 保留在答案末尾。
              JSON / CSV 同样可靠；自由格式的 Markdown 与 TXT 靠「问题以问号结尾」推断，
              导入后请核对。导入的题直接进题库，去「面试题题库」查阅。
            </p>
            <input
              type="file"
              accept=".json,.csv,.md,.markdown,.txt"
              disabled={importing}
              aria-label="选择要导入的题库文件"
              onChange={(event) => {
                void handleImport(event.target.files?.[0])
                event.target.value = ''
              }}
              className="mt-2 w-full text-[11px] file:mr-2 file:rounded-control file:border-0 file:bg-ink-9 file:px-2 file:py-1 file:text-[11px] file:text-surface disabled:opacity-50"
            />
            {importing && <p className="mt-1 text-[11px] text-ink-6">正在解析…</p>}
            {importResult && (
              <div className="mt-2 rounded-control border border-ok bg-ok-soft px-2 py-1.5 text-[11px] leading-relaxed text-ok">
                {importResult.filename}（{importResult.source_format}）：解析出{' '}
                <span className="font-mono">{importResult.total}</span> 道，新增{' '}
                <span className="font-mono">{importResult.added}</span> 道
                {importResult.skipped > 0 && (
                  <>
                    ，跳过 <span className="font-mono">{importResult.skipped}</span> 道（题干已存在）
                  </>
                )}
              </div>
            )}
            {importWarnings.length > 0 && (
              <ul className="mt-1.5 space-y-0.5 text-[10px] leading-relaxed text-warn">
                {importWarnings.map((warning, index) => (
                  <li key={index} className="flex gap-1">
                    <span className="text-ink-3">!</span>
                    <span>{warning}</span>
                  </li>
                ))}
              </ul>
            )}
            {uploads.length > 0 && (
              <div className="mt-2 space-y-1 border-t border-ink-1 pt-2">
                <div className="text-[10px] text-ink-6">已导入的批次</div>
                {uploads.map((item) => (
                  <div key={item.batch} className="flex items-center gap-2 text-[11px]">
                    <span className="flex-1 truncate text-ink-6" title={item.filename}>
                      {item.filename}
                    </span>
                    <span className="shrink-0 font-mono text-ink-4">{item.count}</span>
                    <button
                      type="button"
                      onClick={() => void handleDeleteBatch(item.batch)}
                      className="shrink-0 text-ng hover:underline"
                    >
                      删除
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </aside>
      </div>
    </div>
  )
}
