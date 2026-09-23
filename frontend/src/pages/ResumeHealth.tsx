/** 简历体检（M8）：简历 × JD 匹配诊断、ATS 格式体检、多版本管理、投递分段统计。
 *
 * 这一页要回答两个很具体的问题：
 * 1. 「我这份简历投这个岗位，差在哪」——差的关键词、差的年限、该改哪句；
 * 2. 「我投的那些岗位，匹配度和回音对得上吗」——按匹配分区间看回复率。
 */

import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import type { AtsIssue, MatchBandReport, MatchResult, ResumeItem } from '../api/types'

const SEVERITY_STYLE: Record<string, string> = {
  error: 'border-rose-200 bg-rose-50 text-rose-800',
  warning: 'border-amber-200 bg-amber-50 text-amber-800',
  info: 'border-slate-200 bg-slate-50 text-slate-600',
}

const SEVERITY_LABEL: Record<string, string> = {
  error: '必须改',
  warning: '建议改',
  info: '可选',
}

const GRADE_STYLE: Record<string, string> = {
  A: 'bg-emerald-50 text-emerald-700',
  B: 'bg-sky-50 text-sky-700',
  C: 'bg-amber-50 text-amber-800',
  D: 'bg-rose-50 text-rose-700',
}

const SAMPLE_JD = `岗位职责：
1. 负责核心业务系统的后端设计与开发；
2. 参与高并发场景下的性能优化与稳定性建设。

任职要求：
1. 本科及以上学历，3-5 年后端开发经验；
2. 必须精通 Python，熟悉 Redis、MySQL；
3. 有 Kafka 等消息队列使用经验；
4. 了解 Kubernetes 与微服务架构者优先。`

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function weightLabel(weight: number): string {
  if (weight >= 1) {
    return '硬性要求'
  }
  if (weight <= 0.5) {
    return '加分项'
  }
  return '职责相关'
}

export default function ResumeHealth() {
  const [resumes, setResumes] = useState<ResumeItem[]>([])
  const [resumeId, setResumeId] = useState<number | null>(null)
  const [versions, setVersions] = useState<ResumeItem[]>([])

  const [jd, setJd] = useState('')
  const [jobTitle, setJobTitle] = useState('')
  const [result, setResult] = useState<MatchResult | null>(null)
  const [atsOnly, setAtsOnly] = useState<AtsIssue[] | null>(null)
  const [bands, setBands] = useState<MatchBandReport | null>(null)

  const [newResumeName, setNewResumeName] = useState('')
  const [newResumeText, setNewResumeText] = useState('')
  const [cloneLabel, setCloneLabel] = useState('')

  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const loadResumes = useCallback(async () => {
    try {
      const items = await api.listResumes()
      setResumes(items)
      setResumeId((current) => current ?? items[0]?.id ?? null)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [])

  const loadBands = useCallback(async () => {
    try {
      setBands(await api.matchBands())
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [])

  useEffect(() => {
    void loadResumes()
    void loadBands()
  }, [loadResumes, loadBands])

  useEffect(() => {
    if (resumeId === null) {
      setVersions([])
      return
    }
    api
      .resumeVersions(resumeId)
      .then(setVersions)
      .catch(() => setVersions([]))
  }, [resumeId])

  const diagnose = async () => {
    if (resumeId === null) {
      setError('先建一份简历')
      return
    }
    if (!jd.trim()) {
      setError('把 JD 粘进来才能算匹配度（不知道这个岗位要什么，就谈不上匹配）')
      return
    }
    setBusy(true)
    setError('')
    try {
      setResult(await api.matchResume(resumeId, { jd_text: jd, job_title: jobTitle }))
      setAtsOnly(null)
    } catch (err) {
      setError(errorMessage(err))
      setResult(null)
    } finally {
      setBusy(false)
    }
  }

  const runAts = async () => {
    if (resumeId === null) {
      return
    }
    setBusy(true)
    setError('')
    try {
      setAtsOnly(await api.resumeAts(resumeId))
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const cloneVersion = async () => {
    if (resumeId === null) {
      return
    }
    setBusy(true)
    setError('')
    try {
      const clone = await api.cloneResume(resumeId, { variant_label: cloneLabel || '新版本' })
      setCloneLabel('')
      setResumeId(clone.id)
      await loadResumes()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const createResume = async () => {
    if (!newResumeName.trim() || !newResumeText.trim()) {
      setError('简历名称与内容都要填')
      return
    }
    setBusy(true)
    setError('')
    try {
      const created = await api.createResume({ name: newResumeName, content_text: newResumeText })
      setNewResumeName('')
      setNewResumeText('')
      setResumeId(created.id)
      await loadResumes()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const issues = result ? result.ats : (atsOnly ?? [])

  return (
    <div className="space-y-4">
      {error && (
        <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</div>
      )}

      {/* 简历选择与版本 */}
      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <h2 className="text-sm font-semibold">简历与版本</h2>
        <p className="mt-0.5 text-xs text-slate-500">
          多版本是为了投递 A/B：同一个岗位可以投不同侧重点的简历，之后在投递分析里比较回复率。
        </p>

        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="text-xs text-slate-600">
            当前简历
            <select
              className="mt-1 w-64 rounded-md border border-slate-300 px-2 py-1.5 text-sm"
              value={resumeId ?? ''}
              onChange={(event) => setResumeId(Number(event.target.value))}
            >
              <option value="">（未选择）</option>
              {resumes.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name} v{item.version}
                  {item.variant_label ? ` · ${item.variant_label}` : ''}
                </option>
              ))}
            </select>
          </label>
          <label className="text-xs text-slate-600">
            新版本标注
            <input
              className="mt-1 w-40 rounded-md border border-slate-300 px-2 py-1.5 text-sm"
              placeholder="如：量化版"
              value={cloneLabel}
              onChange={(event) => setCloneLabel(event.target.value)}
            />
          </label>
          <button
            type="button"
            disabled={busy || resumeId === null}
            onClick={() => void cloneVersion()}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed"
          >
            另存为新版本
          </button>
        </div>

        {versions.length > 1 && (
          <div className="mt-2 text-xs text-slate-500">
            该简历共有 {versions.length} 个版本：
            {versions.map((item) => (
              <span key={item.id} className="ml-2 rounded bg-slate-100 px-1.5 py-0.5">
                v{item.version} {item.variant_label || '未标注'}
              </span>
            ))}
          </div>
        )}

        {resumes.length === 0 && (
          <div className="mt-3 rounded-md border border-slate-200 bg-slate-50 p-3">
            <div className="text-xs font-medium text-slate-700">还没有简历，先建一份：</div>
            <input
              className="mt-2 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
              placeholder="简历名称（如：主简历）"
              value={newResumeName}
              onChange={(event) => setNewResumeName(event.target.value)}
            />
            <textarea
              className="mt-2 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
              rows={6}
              placeholder="把简历正文粘进来（纯文本，多版本与诊断都基于它）"
              value={newResumeText}
              onChange={(event) => setNewResumeText(event.target.value)}
            />
            <button
              type="button"
              disabled={busy}
              onClick={() => void createResume()}
              className="mt-2 rounded-md bg-slate-900 px-3 py-1.5 text-xs text-white disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              创建简历
            </button>
          </div>
        )}
      </section>

      {/* 匹配诊断 */}
      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <h2 className="text-sm font-semibold">简历 × JD 匹配诊断</h2>
        <p className="mt-0.5 text-xs text-slate-500">
          只做文本层面的判断：JD 里「必须精通」算硬性要求，「优先」算加分项，权重不一样。
        </p>

        <div className="mt-3 grid gap-3 lg:grid-cols-3">
          <label className="text-xs text-slate-600 lg:col-span-2">
            JD 正文
            <textarea
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
              rows={8}
              placeholder="把招聘页的岗位描述整段粘进来"
              value={jd}
              onChange={(event) => setJd(event.target.value)}
            />
          </label>
          <div className="space-y-3 text-xs text-slate-600">
            <label className="block">
              岗位名称（可选）
              <input
                className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                placeholder="如：后端工程师"
                value={jobTitle}
                onChange={(event) => setJobTitle(event.target.value)}
              />
            </label>
            <button
              type="button"
              disabled={busy || resumeId === null}
              onClick={() => void diagnose()}
              className="w-full rounded-md bg-slate-900 px-3 py-1.5 text-xs text-white disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              开始诊断
            </button>
            <button
              type="button"
              disabled={busy || resumeId === null}
              onClick={() => void runAts()}
              className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed"
            >
              只做格式体检
            </button>
            <button
              type="button"
              onClick={() => setJd(SAMPLE_JD)}
              className="w-full rounded-md border border-slate-200 px-3 py-1.5 text-xs text-slate-500 hover:bg-slate-50"
            >
              填入示例 JD
            </button>
          </div>
        </div>

        {result && (
          <div className="mt-4 space-y-4">
            <div className="flex flex-wrap items-center gap-3">
              <div className="flex items-baseline gap-2">
                <span className="text-3xl font-semibold">{result.score}</span>
                <span className="text-xs text-slate-500">/ 100</span>
              </div>
              <span className={`rounded-full px-2.5 py-1 text-xs ${GRADE_STYLE[result.grade] ?? 'bg-slate-100'}`}>
                等级 {result.grade}
              </span>
              <span className="text-xs text-slate-600">{result.summary}</span>
            </div>

            <div className="grid gap-3 md:grid-cols-3">
              <div>
                <div className="text-xs font-medium text-emerald-700">已覆盖（{result.covered.length}）</div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {result.covered.map((item) => (
                    <span key={item.word} className="rounded bg-emerald-50 px-1.5 py-0.5 text-xs text-emerald-700">
                      {item.word}
                    </span>
                  ))}
                </div>
              </div>
              <div>
                <div className="text-xs font-medium text-rose-700">缺失（{result.missing.length}）</div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {result.missing.map((item) => (
                    <span
                      key={item.word}
                      title={`${weightLabel(item.weight)}：${item.sentence}`}
                      className={
                        item.weight >= 1
                          ? 'rounded bg-rose-100 px-1.5 py-0.5 text-xs text-rose-800'
                          : 'rounded bg-rose-50 px-1.5 py-0.5 text-xs text-rose-600'
                      }
                    >
                      {item.word}
                      <span className="ml-1 text-[10px] opacity-70">{weightLabel(item.weight)}</span>
                    </span>
                  ))}
                </div>
              </div>
              <div>
                <div className="text-xs font-medium text-slate-600">简历里有、JD 没提（{result.extra.length}）</div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {result.extra.map((word) => (
                    <span key={word} className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600">
                      {word}
                    </span>
                  ))}
                </div>
                <div className="mt-1 text-[11px] text-slate-400">这些不是缺点，只是这一版可以精简的部分</div>
              </div>
            </div>

            <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-700">
              年限：JD 要求{' '}
              {result.years_required !== null ? `${result.years_required} 年` : '未写明'}，简历推算{' '}
              {result.years_actual !== null ? `${result.years_actual} 年` : '未能推算'}
              {result.years_ok === false && <span className="ml-2 text-rose-700">不满足——这条通常先被筛掉</span>}
              {result.years_ok === true && <span className="ml-2 text-emerald-700">满足</span>}
            </div>

            {result.missing_groups.length > 0 && (
              <div className="text-xs text-slate-600">
                缺失集中在：{result.missing_groups.join('、')}
              </div>
            )}

            {result.suggestions.length > 0 && (
              <div>
                <div className="text-xs font-medium text-slate-700">按优先级要改的地方</div>
                <div className="mt-2 space-y-2">
                  {result.suggestions.map((item) => (
                    <div key={`${item.kind}-${item.target}`} className="rounded-md border border-slate-200 p-3 text-xs">
                      <div className="font-medium text-slate-800">
                        {item.kind === 'add_keyword' ? '补关键词' : item.kind === 'fix_ats' ? '改格式' : '改年限表述'}：
                        {item.target}
                      </div>
                      <div className="mt-1 text-slate-600">{item.why}</div>
                      {item.where && <div className="mt-1 text-slate-500">位置：{item.where}</div>}
                      {item.draft && (
                        <pre className="mt-2 whitespace-pre-wrap rounded bg-slate-50 px-2 py-1.5 text-[11px] text-slate-700">
                          {item.draft}
                        </pre>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </section>

      {/* ATS 体检 */}
      {(issues.length > 0 || atsOnly !== null) && (
        <section className="rounded-lg border border-slate-200 bg-white p-4">
          <h2 className="text-sm font-semibold">格式与可读性体检（ATS）</h2>
          <p className="mt-0.5 text-xs text-slate-500">
            「必须改」里的问题会直接影响筛选结果，不只是风格问题——比如全角字符会让关键词匹配直接失效。
          </p>

          {issues.length === 0 ? (
            <p className="mt-3 text-xs text-emerald-700">没发现格式问题，简历可以被正常解析。</p>
          ) : (
            <ul className="mt-3 space-y-2">
              {issues.map((item) => (
                <li
                  key={`${item.kind}-${item.message}`}
                  className={`rounded-md border px-3 py-2 text-xs ${SEVERITY_STYLE[item.severity] ?? ''}`}
                >
                  <div className="font-medium">
                    [{SEVERITY_LABEL[item.severity] ?? item.severity}] {item.message}
                  </div>
                  {item.suggestion && <div className="mt-1 opacity-90">{item.suggestion}</div>}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {/* 匹配分段统计 */}
      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <h2 className="text-sm font-semibold">匹配度 vs 回音</h2>
        <p className="mt-0.5 text-xs text-slate-500">
          用投递记录里的 JD 反推每条投递的匹配分，再看各分数段的回复率——这是"为什么没回复"最直接的证据。
        </p>

        {bands && (
          <div className="mt-3 space-y-3">
            <div className="text-xs text-slate-600">
              共 {bands.total_records} 条投递，其中 {bands.matched_records} 条有 JD 可比较
            </div>

            <table className="w-full text-left text-xs">
              <thead className="text-slate-500">
                <tr>
                  <th className="py-1.5 pr-2">匹配分</th>
                  <th className="py-1.5 pr-2">投递</th>
                  <th className="py-1.5 pr-2">有回应</th>
                  <th className="py-1.5 pr-2">面试</th>
                  <th className="py-1.5 pr-2">Offer</th>
                  <th className="py-1.5 pr-2">回复率</th>
                </tr>
              </thead>
              <tbody>
                {bands.bands.map((band) => (
                  <tr key={band.label} className="border-t border-slate-100">
                    <td className="py-1.5 pr-2">{band.label}</td>
                    <td className="py-1.5 pr-2">{band.count}</td>
                    <td className="py-1.5 pr-2">{band.responded}</td>
                    <td className="py-1.5 pr-2">{band.interviewed}</td>
                    <td className="py-1.5 pr-2">{band.offers}</td>
                    <td className="py-1.5 pr-2">{band.count ? `${Math.round(band.response_rate * 100)}%` : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-700">
              {bands.recommendation}
            </div>
            {bands.confidence_note && <div className="text-[11px] text-slate-500">{bands.confidence_note}</div>}
          </div>
        )}
      </section>
    </div>
  )
}
