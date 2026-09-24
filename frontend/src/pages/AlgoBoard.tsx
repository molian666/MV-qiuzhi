/** 算法题白板（M13）。
 *
 * 两件事在这里汇合：
 * 1. **判题**：在浏览器端沙箱跑（`src/sandbox/`），不在服务端跑代码；
 * 2. **评估**：把判题结果和代码交给模型，评「思路—取舍—边界」。
 *
 * 界面上刻意把两者分开显示，并且在评估里区分**实测**与**推断**——
 * 「测试用例 3 没过」是事实，「这里可能超时」是猜测，混在一起看会误导。
 *
 * **默认筛到机器视觉**：这个项目主要为机器视觉方向的求职服务，通用 LeetCode
 * 是补充，不是主线。
 */

import { Suspense, lazy, useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
import type {
  AlgoAttempt,
  AlgoCaseReport,
  AlgoProblem,
  AlgoProblemBrief,
  AlgoReview,
} from '../api/types'
import { runTests, toReportedCases, type SandboxCaseResult } from '../sandbox/runner'
import Markdown from '../components/Markdown'

// Monaco 约 2MB，只有打开这个页面才下载
const CodeEditor = lazy(() => import('../components/CodeEditor'))

const TRACKS = [
  { key: 'vision', label: '机器视觉' },
  { key: 'general', label: '通用算法' },
  { key: '', label: '全部' },
] as const

const LANGUAGE_LABELS: Record<string, string> = { python: 'Python', javascript: 'JavaScript' }
const DIFFICULTY_LABELS: Record<string, string> = { easy: '简单', medium: '中等', hard: '困难' }
const DIFFICULTY_STYLES: Record<string, string> = {
  easy: 'bg-emerald-50 text-emerald-700',
  medium: 'bg-amber-50 text-amber-700',
  hard: 'bg-rose-50 text-rose-700',
}
const VERDICT_LABELS: Record<string, string> = {
  correct: '方向正确',
  partial: '部分正确',
  wrong: '方向有问题',
  unknown: '未能判断',
}
const VERDICT_STYLES: Record<string, string> = {
  correct: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  partial: 'bg-amber-50 text-amber-700 border-amber-200',
  wrong: 'bg-rose-50 text-rose-700 border-rose-200',
  unknown: 'bg-slate-50 text-slate-600 border-slate-200',
}
const EDGE_LABELS: Record<string, string> = {
  covered: '已覆盖',
  missed: '漏了',
  untested: '未测',
}
const EDGE_STYLES: Record<string, string> = {
  covered: 'text-emerald-700',
  missed: 'text-rose-700',
  untested: 'text-slate-400',
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function show(value: unknown): string {
  if (value === undefined) {
    return 'undefined'
  }
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

/** 题干用共享的 Markdown 组件渲染（与题库答案同一套解析，避免两份实现漂移）。 */
function Statement({ text }: { text: string }) {
  return <Markdown text={text} />
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h4 className="text-xs font-semibold text-slate-500">{title}</h4>
      <div className="mt-1.5">{children}</div>
    </div>
  )
}

/** 评估面板：思路 / 取舍 / 边界。 */
function ReviewPanel({ review }: { review: AlgoReview }) {
  const verdict = review.approach_verdict || 'unknown'
  return (
    <div className="space-y-4">
      <div className={`rounded-lg border px-3 py-2 ${VERDICT_STYLES[verdict] ?? VERDICT_STYLES.unknown}`}>
        <span className="text-sm font-semibold">{VERDICT_LABELS[verdict] ?? verdict}</span>
        {review.approach_summary && (
          <p className="mt-1 text-xs leading-relaxed opacity-90">{review.approach_summary}</p>
        )}
      </div>

      {review.notes && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          {review.notes}
        </div>
      )}

      <Section title="思路">
        {review.complexity.time || review.complexity.space ? (
          <p className="text-xs text-slate-600">
            时间 <code className="rounded bg-slate-100 px-1">{review.complexity.time || '—'}</code>
            {'　'}
            空间 <code className="rounded bg-slate-100 px-1">{review.complexity.space || '—'}</code>
          </p>
        ) : null}
        {review.complexity.assessment && (
          <p className="mt-1 text-xs text-slate-600">{review.complexity.assessment}</p>
        )}
        {review.summary && <p className="mt-2 text-xs leading-relaxed text-slate-700">{review.summary}</p>}
        {!review.complexity.assessment && !review.summary && (
          <p className="text-xs text-slate-400">这一项没有给出内容。</p>
        )}
      </Section>

      <Section title="取舍">
        {review.tradeoffs.length === 0 ? (
          <p className="text-xs text-slate-400">这一项没有给出内容。</p>
        ) : (
          <ul className="space-y-1.5">
            {review.tradeoffs.map((item, index) => (
              <li key={index} className="text-xs leading-relaxed">
                <div className="flex items-start gap-1.5">
                  {/* 实测 / 推断必须一眼可分：一个是事实，一个是猜测 */}
                  <span
                    className={`mt-0.5 shrink-0 rounded px-1 text-[10px] ${
                      item.kind === 'measured'
                        ? 'bg-emerald-100 text-emerald-800'
                        : 'bg-slate-100 text-slate-500'
                    }`}
                    title={item.kind === 'measured' ? '有测试结果直接支撑' : '模型从代码推断，未实测'}
                  >
                    {item.kind === 'measured' ? '实测' : '推断'}
                  </span>
                  <span className="text-slate-700">
                    <span className="font-medium text-slate-900">{item.point}</span>
                    {item.assessment ? ` — ${item.assessment}` : ''}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="边界">
        {review.edge_cases.length === 0 ? (
          <p className="text-xs text-slate-400">这一项没有给出内容。</p>
        ) : (
          <ul className="space-y-1">
            {review.edge_cases.map((item, index) => (
              <li key={index} className="text-xs leading-relaxed">
                <span className={`font-medium ${EDGE_STYLES[item.status] ?? 'text-slate-500'}`}>
                  [{EDGE_LABELS[item.status] ?? item.status}]
                </span>{' '}
                <span className="text-slate-700">{item.case}</span>
                {item.detail && <span className="text-slate-500"> — {item.detail}</span>}
              </li>
            ))}
          </ul>
        )}
      </Section>

      {review.risks.length > 0 && (
        <Section title="风险">
          <ul className="list-disc space-y-1 pl-4 text-xs text-slate-700">
            {review.risks.map((item, index) => (
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
        这是模型对代码的判断，不是标准答案；标「推断」的条目没有实测支撑，请自己验证。
      </p>
    </div>
  )
}

function TestResultList({
  results,
  reports,
  interrupted,
}: {
  results: SandboxCaseResult[]
  reports: AlgoCaseReport[] | null
  interrupted: boolean
}) {
  // 提交之后以服务端记录的完整用例为准（含未执行的），未提交时看本地沙箱结果
  const rows = reports
    ? reports.map((item) => ({
        index: item.index,
        passed: item.passed,
        note: item.note,
        error: item.error,
        actual: item.actual,
        expected: item.expected,
        durationMs: null as number | null,
        stdout: '',
      }))
    : results.map((item) => ({
        index: item.index,
        passed: item.passed,
        note: '',
        error: item.error,
        actual: item.actual,
        expected: undefined as unknown,
        durationMs: item.durationMs,
        stdout: item.stdout ?? '',
      }))

  return (
    <div className="space-y-1">
      {interrupted && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-xs text-amber-800">
          沙箱超时被终止：只跑完了前 {results.length} 例。
          <strong className="font-semibold">没跑完不等于通过</strong>
          ——常见原因是死循环，检查循环的退出条件。
        </div>
      )}
      {rows.map((row) => (
        <div
          key={row.index}
          className={`rounded-md border px-2.5 py-1.5 text-xs ${
            row.passed ? 'border-emerald-200 bg-emerald-50' : 'border-rose-200 bg-rose-50'
          }`}
        >
          <div className="flex items-center gap-2">
            <span className={row.passed ? 'text-emerald-700' : 'text-rose-700'}>
              {row.passed ? '✓' : '✗'}
            </span>
            <span className="font-medium text-slate-700">用例 {row.index + 1}</span>
            {row.note && <span className="text-slate-500">{row.note}</span>}
            {row.durationMs !== null && (
              <span className="ml-auto text-[10px] text-slate-400">{row.durationMs}ms</span>
            )}
          </div>
          {!row.passed && (
            <div className="mt-1 space-y-0.5 pl-5 text-[11px] text-slate-600">
              {row.error ? (
                <div className="text-rose-700">{row.error}</div>
              ) : (
                <>
                  <div>期望：{show(row.expected)}</div>
                  <div>实际：{show(row.actual)}</div>
                </>
              )}
            </div>
          )}
          {row.stdout && (
            <pre className="mt-1 overflow-x-auto rounded bg-slate-900 px-2 py-1 pl-5 font-mono text-[10px] text-slate-300">
              {row.stdout}
            </pre>
          )}
        </div>
      ))}
    </div>
  )
}

export default function AlgoBoard() {
  const [track, setTrack] = useState<string>('vision')
  const [problems, setProblems] = useState<AlgoProblemBrief[]>([])
  const [slug, setSlug] = useState<string>('')
  const [problem, setProblem] = useState<AlgoProblem | null>(null)
  const [code, setCode] = useState('')
  const [results, setResults] = useState<SandboxCaseResult[]>([])
  const [interrupted, setInterrupted] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [attempt, setAttempt] = useState<AlgoAttempt | null>(null)
  const [history, setHistory] = useState<AlgoAttempt[]>([])
  const [hintsShown, setHintsShown] = useState(0)
  const [lastRunMs, setLastRunMs] = useState<number | null>(null)
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState<'idle' | 'running' | 'submitting'>('idle')
  const [error, setError] = useState('')

  // 换方向就重拉清单；清单变了要把当前题切到新列表里的第一道，否则会显示空
  useEffect(() => {
    let cancelled = false
    api
      .algoProblems(track || undefined)
      .then((items) => {
        if (cancelled) {
          return
        }
        setProblems(items)
        setSlug(items[0]?.slug ?? '')
      })
      .catch((err) => setError(errorMessage(err)))
    return () => {
      cancelled = true
    }
  }, [track])

  const loadHistory = useCallback(async (target: string) => {
    try {
      setHistory(await api.algoAttempts(target))
    } catch {
      setHistory([])
    }
  }, [])

  useEffect(() => {
    if (!slug) {
      setProblem(null)
      return
    }
    setError('')
    setAttempt(null)
    setResults([])
    setInterrupted(false)
    setLoadError('')
    setHintsShown(0)
    setStatus('')
    api
      .algoProblem(slug)
      .then((detail) => {
        setProblem(detail)
        const language = detail.languages[0] ?? 'javascript'
        setCode(detail.starters[language] ?? '')
      })
      .catch((err) => setError(errorMessage(err)))
    void loadHistory(slug)
  }, [slug, loadHistory])

  const language = problem?.languages[0] ?? 'javascript'
  const functionName = problem ? (problem.function_names[language] ?? '') : ''

  const run = async () => {
    if (!problem) {
      return
    }
    setBusy('running')
    setError('')
    setAttempt(null)
    setStatus('')
    try {
      const tests = problem.tests.map((test) => ({
        index: test.index,
        args: test.args,
        expected: test.expected,
      }))
      // pythonRunner 走动态 import：不跑 Python 的人不该下载它的代码
      const outcome =
        language === 'python'
          ? await (
              await import('../sandbox/pythonRunner')
            ).runPythonTests({ code, functionName, tests, onStatus: setStatus })
          : await runTests({ code, functionName, tests })

      setResults(outcome.results)
      setInterrupted(outcome.interrupted)
      setLoadError(outcome.loadError)
      setLastRunMs(outcome.totalMs)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setStatus('')
      setBusy('idle')
    }
  }

  const submit = async () => {
    if (!problem) {
      return
    }
    setBusy('submitting')
    setError('')
    try {
      const created = await api.submitAlgoAttempt({
        problem_slug: problem.slug,
        language,
        code,
        duration_ms: lastRunMs ?? undefined,
        cases: toReportedCases(results),
      })
      setAttempt(created)
      await loadHistory(problem.slug)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy('idle')
    }
  }

  const passedCount = useMemo(() => results.filter((item) => item.passed).length, [results])
  const canSubmit = results.length > 0 && busy === 'idle'

  return (
    <div className="grid gap-5 lg:grid-cols-[240px_1fr]">
      {/* 题目清单 */}
      <aside className="space-y-2">
        <div className="flex gap-1">
          {TRACKS.map((item) => (
            <button
              key={item.key}
              type="button"
              onClick={() => setTrack(item.key)}
              className={`flex-1 rounded-md px-2 py-1 text-[11px] transition ${
                item.key === track
                  ? 'bg-slate-900 text-white'
                  : 'border border-slate-200 bg-white text-slate-600 hover:bg-slate-50'
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>

        <div className="space-y-1.5">
          {problems.map((item) => (
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
              <div className="font-medium text-slate-900">{item.title}</div>
              <div className="mt-1 flex flex-wrap items-center gap-1.5">
                <span
                  className={`rounded px-1.5 py-0.5 text-[10px] ${DIFFICULTY_STYLES[item.difficulty] ?? 'bg-slate-100 text-slate-600'}`}
                >
                  {DIFFICULTY_LABELS[item.difficulty] ?? item.difficulty}
                </span>
                {item.languages.map((lang) => (
                  <span key={lang} className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600">
                    {LANGUAGE_LABELS[lang] ?? lang}
                  </span>
                ))}
              </div>
              <div className="mt-1 truncate text-[10px] text-slate-400">{item.tags.join(' · ')}</div>
            </button>
          ))}
          {problems.length === 0 && (
            <p className="px-1 text-xs text-slate-400">这个方向下暂时没有题目。</p>
          )}
        </div>
      </aside>

      {/* 主区 */}
      <div className="space-y-4">
        {error && (
          <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
            {error}
          </div>
        )}

        {problem && (
          <>
            <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-sm font-semibold text-slate-900">{problem.title}</h3>
                <span
                  className={`rounded px-1.5 py-0.5 text-[10px] ${DIFFICULTY_STYLES[problem.difficulty] ?? 'bg-slate-100 text-slate-600'}`}
                >
                  {DIFFICULTY_LABELS[problem.difficulty] ?? problem.difficulty}
                </span>
                <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600">
                  {LANGUAGE_LABELS[language] ?? language}
                </span>
                <span className="text-[10px] text-slate-400">{problem.tags.join(' · ')}</span>
              </div>
              <div className="mt-3">
                <Statement text={problem.statement} />
              </div>
              {problem.signatures[language] && (
                <p className="mt-3 rounded-md bg-slate-50 px-2.5 py-1.5 font-mono text-[11px] text-slate-600">
                  {problem.signatures[language]}
                </p>
              )}
            </div>

            <div>
              <Suspense
                fallback={
                  <div className="rounded-lg border border-slate-300 bg-white p-4 text-xs text-slate-400">
                    正在加载编辑器…
                  </div>
                }
              >
                <CodeEditor value={code} onChange={setCode} language={language} />
              </Suspense>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => void run()}
                disabled={busy !== 'idle'}
                className="rounded-md bg-slate-900 px-3 py-1.5 text-xs text-white disabled:bg-slate-300"
              >
                {busy === 'running' ? '运行中…' : '跑测试用例'}
              </button>
              <button
                type="button"
                onClick={() => void submit()}
                disabled={!canSubmit}
                className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-700 disabled:text-slate-300"
                title={canSubmit ? '把判题结果交给模型评「思路—取舍—边界」' : '先跑一次测试'}
              >
                {busy === 'submitting' ? '评估中…' : '提交并评估'}
              </button>
              <button
                type="button"
                onClick={() => setCode(problem.starters[language] ?? '')}
                className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-600"
              >
                重置代码
              </button>
              {hintsShown < problem.hints.length && (
                <button
                  type="button"
                  onClick={() => setHintsShown((count) => count + 1)}
                  className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-600"
                >
                  看提示（{hintsShown}/{problem.hints.length}）
                </button>
              )}
              {results.length > 0 && (
                <span className="ml-auto text-xs text-slate-500">
                  通过 {passedCount}/{results.length}
                  {interrupted && <span className="ml-1 text-amber-700">（被中断）</span>}
                </span>
              )}
            </div>

            {status && (
              <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
                {status}
              </div>
            )}

            {language === 'python' && results.length === 0 && !loadError && (
              <p className="text-[11px] leading-relaxed text-slate-400">
                首次跑 Python 会加载运行时（约 13MB，之后走浏览器缓存），通常几秒到十几秒。
              </p>
            )}

            {hintsShown > 0 && (
              <div className="rounded-lg border border-slate-200 bg-white p-3">
                <h4 className="text-xs font-semibold text-slate-500">提示</h4>
                <ul className="mt-1.5 list-disc space-y-1 pl-4 text-xs text-slate-700">
                  {problem.hints.slice(0, hintsShown).map((hint, index) => (
                    <li key={index}>{hint}</li>
                  ))}
                </ul>
              </div>
            )}

            {loadError && (
              <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
                代码无法运行：{loadError}
              </div>
            )}

            {results.length > 0 && (
              <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                <h4 className="mb-2 text-xs font-semibold text-slate-500">测试结果</h4>
                <TestResultList
                  results={results}
                  reports={attempt ? attempt.test_report.cases : null}
                  interrupted={interrupted}
                />
              </div>
            )}

            {attempt && (
              <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                <h4 className="mb-3 text-xs font-semibold text-slate-500">评估</h4>
                <ReviewPanel review={attempt.review} />
              </div>
            )}

            {history.length > 0 && (
              <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                <h4 className="mb-2 text-xs font-semibold text-slate-500">这道题的历史（点一下载入代码）</h4>
                <div className="space-y-1">
                  {history.slice(0, 6).map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => {
                        setCode(item.code)
                        setAttempt(item)
                        setResults([])
                        setInterrupted(false)
                        setLoadError('')
                      }}
                      className="w-full rounded-md border border-slate-200 px-2.5 py-1.5 text-left text-xs hover:bg-slate-50"
                    >
                      <div className="flex items-center gap-2">
                        <span className={item.passed === item.total ? 'text-emerald-700' : 'text-amber-700'}>
                          {item.passed}/{item.total}
                        </span>
                        <span className="text-slate-600">
                          {VERDICT_LABELS[item.approach_verdict] ?? (item.approach_verdict || '—')}
                        </span>
                        <span className="text-[10px] text-slate-400">
                          {LANGUAGE_LABELS[item.language] ?? item.language}
                        </span>
                        <span className="ml-auto text-[10px] text-slate-400">
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

        {!problem && !error && (
          <div className="rounded-xl border border-slate-200 bg-white p-6 text-xs text-slate-400">
            从左边选一道题开始。
          </div>
        )}
      </div>
    </div>
  )
}
