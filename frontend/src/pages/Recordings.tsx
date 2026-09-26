/**
 * 节奏学习页（M4）：上传真实面试对话 → 学出面试官画像 → 在模拟里按那个节奏面你。
 *
 * **入口是「对话文本」，不是音频**：节奏分析靠 `[面试官] / [我]` 这类标记切分问答，
 * 而本项目没有接语音转写——上传音频不会自动变成文字。
 * 所以页面上把「上传文本文稿 / 粘贴文本」做成主路径，
 * 并明确写出音频走不通，免得白传一个几百 MB 的录音。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import Markdown from '../components/Markdown'
import type { DocCheck, ProfileDetail, ProfileListItem, Recording, RecordingDetail } from '../api/types'

const SAMPLE_DIALOG = `[面试官] 先做个自我介绍吧
[我] 我是 5 年后端，最近在做订单系统重构
[面试官] 讲讲这个项目，你具体负责哪一部分？
[我] 我负责下单链路的重构
[面试官] 那当时有没有考虑过别的方案？
[我] 有，考虑过消息队列削峰
`

/** 支持的文稿扩展名，与后端 `SUPPORTED_SUFFIXES` 保持一致 */
const DOC_ACCEPT = '.txt,.md,.markdown,.text,.log,.csv,.tsv'

const STATUS_LABEL: Record<string, string> = {  uploaded: '待分析',
  transcribing: '转写中',
  analyzing: '分析中',
  done: '已完成',
  failed: '失败',
}

/**
 * 拆解"生成画像"的目标选择。
 *
 * 一个下拉表达三种模式：`new`（新建）/ `append:<id>`（追加到已有）/
 * `replace:<id>`（重新生成已有）。后两者都要画像 id。
 */
function parseTarget(key: string): { mode: 'new' | 'append' | 'replace'; id: number | null } {
  const [mode, rawId] = key.split(':')
  if (mode === 'append' || mode === 'replace') {
    const id = Number(rawId)
    return { mode, id: Number.isFinite(id) ? id : null }
  }
  return { mode: 'new', id: null }
}

function errorMessage(error: unknown): string {  return error instanceof Error ? error.message : String(error)
}

function formatTime(value: string): string {  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

export default function Recordings() {
  const [recordings, setRecordings] = useState<Recording[]>([])
  const [profiles, setProfiles] = useState<ProfileListItem[]>([])
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const [detail, setDetail] = useState<RecordingDetail | null>(null)
  const [profile, setProfile] = useState<ProfileDetail | null>(null)

  const [title, setTitle] = useState('')
  const [rawText, setRawText] = useState('')
  const [docFile, setDocFile] = useState<File | null>(null)
  const [check, setCheck] = useState<DocCheck | null>(null)
  const [checking, setChecking] = useState(false)
  const [profileName, setProfileName] = useState('')
  /**
   * 生成画像的目标。用单个 key 表达三种模式：
   * `new`（新建）/ `append:<id>`（追加到）/ `replace:<id>`（重新生成）。
   * 一个下拉就够，比三个单选清爽。
   */
  const [target, setTarget] = useState('new')

  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [recordingList, profileList] = await Promise.all([api.listRecordings(), api.listProfiles()])
      setRecordings(recordingList)
      setProfiles(profileList)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  /** 选了文件就先体检一遍：当场告诉用户"能切出多少问答、缺什么"，
   *  而不是等他上传完、生成画像失败后才发现没标说话人。 */
  const pickDocument = async (picked: File | null) => {
    setDocFile(picked)
    setCheck(null)
    setError('')
    if (picked === null) {
      return
    }
    setChecking(true)
    try {
      setCheck(await api.inspectDocument(picked))
    } catch (err) {
      setError(errorMessage(err))
      setDocFile(null)
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }
    } finally {
      setChecking(false)
    }
  }

  const resetForm = () => {
    setTitle('')
    setRawText('')
    setDocFile(null)
    setCheck(null)
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  /** 保存：有文件就走文稿上传（后端识别编码并直接落段落），否则存粘贴的文本 */
  const submitRecording = async () => {
    if (docFile === null && !rawText.trim()) {
      setError('请上传对话文稿（.txt / .md），或把对话粘贴进文本框')
      return
    }
    setBusy(true)
    setError('')
    try {
      if (docFile !== null) {
        await api.uploadDocument(docFile, title)
      } else {
        await api.createRecording({ title, raw_text: rawText })
      }
      resetForm()
      await refresh()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const buildProfile = async () => {
    if (selectedIds.length === 0) {
      setError('先勾选至少一段对话')
      return
    }
    const { mode, id } = parseTarget(target)
    if (mode !== 'new' && id === null) {
      setError('先选要更新哪个画像')
      return
    }
    setBusy(true)
    setError('')
    try {
      const created = await api.createProfile(
        selectedIds,
        profileName,
        mode,
        mode === 'new' ? undefined : (id ?? undefined),
      )
      setProfile(created)
      setProfileName('')
      setSelectedIds([])
      await refresh()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  /** 点同一条就收起（之前只能展开，收不回去）；点另一条则切换过去 */
  const toggleRecording = async (id: number) => {
    if (detail?.id === id) {
      setDetail(null)
      return
    }
    try {
      setDetail(await api.getRecording(id))
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const openProfile = async (id: number) => {
    try {
      setProfile(await api.getProfile(id))
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const removeRecording = async (id: number) => {
    try {
      await api.deleteRecording(id)
      setSelectedIds((previous) => previous.filter((item) => item !== id))
      if (detail?.id === id) {
        setDetail(null)
      }
      await refresh()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const removeProfile = async (id: number) => {
    try {
      await api.deleteProfile(id)
      if (profile?.id === id) {
        setProfile(null)
      }
      await refresh()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const inputClass = 'w-full rounded-card border border-ink-2 px-3 py-2 text-sm outline-none focus:border-info'

  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      {error && (
        <p className="shrink-0 rounded-control bg-ng-soft px-3 py-2 text-sm text-ng">{error}</p>
      )}

      <section className="grid min-h-0 flex-1 gap-4 lg:grid-cols-2">
        {/* 左：对话来源 */}
        <div className="min-h-0 space-y-3 overflow-y-auto pr-1">
          <div className="rounded-panel border border-ink-1 bg-surface p-4">
            <h3 className="mb-1 text-sm font-semibold text-ink-9">① 添加一段真实面试对话</h3>
            {/* 用 Markdown 组件而不是 <p>：这里的 `[面试官]` 要用行内代码样式，
                **对话文稿** 要加粗——直接写在 <p> 里会把星号原样显示出来 */}
            <div className="mb-3 text-[11px] leading-relaxed [&_p]:m-0 [&_p]:text-[11px] [&_p]:leading-relaxed [&_p]:text-ink-6">
              <Markdown text="上传**对话文稿**（.txt / .md），或把对话粘贴到下面的文本框。分析靠 `[面试官]` / `[我]` 这类标记切分问答，所以文稿里要标出谁在说话。" />
            </div>

            <input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              aria-label="这段对话的标题"
              placeholder="标题（例如：某公司一面 3/12）"
              className={`${inputClass} mb-2`}
            />

            {/* 主路径：上传文稿文件 */}
            <div className="rounded-card border border-dashed border-ink-2 bg-paper p-3">
              <div className="flex flex-wrap items-center gap-2">
                <input
                  ref={fileInputRef}
                  type="file"
                  accept={DOC_ACCEPT}
                  aria-label="上传面试对话文稿（.txt 或 .md）"
                  onChange={(event) => void pickDocument(event.target.files?.[0] ?? null)}
                  className="text-xs text-ink-6"
                />
                {checking && <span className="text-[11px] text-ink-6">正在解析…</span>}
              </div>
              <p className="mt-1.5 text-[11px] text-ink-4">
                支持 .txt / .md；GBK 与 UTF-8 都会自动识别。
              </p>

              {check && (
                <div className="mt-2 rounded-control border border-ink-1 bg-surface p-2.5">
                  <p className={`text-[11px] font-medium ${check.usable ? 'text-ok' : 'text-warn'}`}>
                    {check.filename} · 编码 {check.encoding || '未知'}
                  </p>
                  <p className="mt-1 text-[11px] leading-relaxed text-ink-7">{check.summary}</p>

                  {check.inferred_roles && (
                    <p className="mt-1 text-[11px] leading-relaxed text-info">
                      {check.role_note}
                      （原文只标了「发言人」这类编号，没有谁是谁的标记。）
                    </p>
                  )}

                  {check.warnings.length > 0 && (
                    <ul className="mt-1.5 space-y-0.5">
                      {check.warnings.map((warning) => (
                        <li key={warning} className="text-[11px] leading-relaxed text-warn">
                          — {warning}
                        </li>
                      ))}
                    </ul>
                  )}

                  {check.preview.length > 0 && (
                    <details className="mt-1.5">
                      <summary className="cursor-pointer text-[11px] text-ink-6">
                        看前 {check.preview.length} 段切分结果
                      </summary>
                      <div className="mt-1 space-y-0.5">
                        {check.preview.map((segment) => (
                          <p key={segment.idx} className="text-[11px] leading-relaxed text-ink-6">
                            {/* 角色是推断出来的时候，把原文标签一起显示：
                                用户才能核对"面试官到底是不是发言人1"，猜反了也好去改 */}
                            <span
                              className={
                                segment.speaker === 'interviewer'
                                  ? 'text-info'
                                  : segment.speaker === 'candidate'
                                    ? 'text-ok'
                                    : 'text-warn'
                              }
                            >
                              {segment.speaker === 'interviewer'
                                ? '面试官'
                                : segment.speaker === 'candidate'
                                  ? '我'
                                  : '未标注'}
                            </span>
                            {segment.source_label && (
                              <span className="text-ink-4">（{segment.source_label}）</span>
                            )}{' '}
                            {segment.text}
                          </p>
                        ))}
                      </div>
                    </details>
                  )}
                </div>
              )}
            </div>

            <div className="my-2 text-center text-[11px] text-ink-4">或者直接粘贴</div>

            <textarea
              value={rawText}
              onChange={(event) => setRawText(event.target.value)}
              rows={7}
              aria-label="粘贴面试对话文稿"
              placeholder="每行一段，标出谁在说话：&#10;[面试官] 先做个自我介绍吧&#10;[我] 我是 5 年后端…&#10;（也支持 `00:12 面试官：…` 或 `Q: / A:`，带时间戳则等待时长更准）"
              className={`${inputClass} resize-y font-mono text-xs`}
            />

            <div className="mt-2 flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => setRawText(SAMPLE_DIALOG)}
                className="rounded-control border border-ink-2 px-2.5 py-1.5 text-xs text-ink-6 hover:bg-paper"
              >
                填入示例
              </button>
              <button
                type="button"
                onClick={() => void submitRecording()}
                disabled={busy || checking || (docFile === null && !rawText.trim())}
                className="ml-auto rounded-control bg-ink-9 px-3 py-1.5 text-xs font-medium text-surface hover:bg-ink-9 disabled:border-ink-1 disabled:bg-sunken disabled:text-ink-6"
              >
                {busy ? '保存中…' : '保存这段对话'}
              </button>
            </div>

            <div className="mt-2 rounded-control bg-warn-soft px-2.5 py-2 text-[11px] leading-relaxed [&_p]:m-0 [&_p]:text-[11px] [&_p]:leading-relaxed [&_p]:text-warn">
              <Markdown text="**音频不会被转成文字**：本项目未接入语音识别，上传录音只能保存文件，分析拿不到任何内容。请上传对话文稿或粘贴文本。" />
            </div>
          </div>

          <div className="rounded-panel border border-ink-1 bg-surface p-4">
            <div className="mb-2 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-ink-9">对话（{recordings.length}）</h3>
              <span className="text-xs text-ink-6">勾选后可在右侧生成画像</span>
            </div>

            {recordings.length === 0 ? (
              <p className="text-xs text-ink-6">还没有对话。上传一份文稿或粘一段文本试试。</p>
            ) : (
              <ul className="space-y-2">
                {recordings.map((item) => (
                  <li key={item.id} className="flex items-start gap-2 rounded-card bg-paper px-3 py-2">
                    <input
                      type="checkbox"
                      aria-label={`选择「${item.title}」用于生成画像`}
                      checked={selectedIds.includes(item.id)}
                      onChange={(event) =>
                        setSelectedIds((previous) =>
                          event.target.checked ? [...previous, item.id] : previous.filter((id) => id !== item.id),
                        )
                      }
                      className="mt-1"
                    />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm text-ink-9">
                        #{item.id} {item.title}
                      </p>
                      <p className="text-[11px] text-ink-6">
                        {STATUS_LABEL[item.status] ?? item.status}
                        {item.has_text ? ' · 有文稿' : ''}
                        {item.has_audio ? ' · 有音频' : ''} · {formatTime(item.created_at)}
                      </p>
                      {item.error && <p className="text-[11px] text-ng">{item.error}</p>}
                    </div>
                    <div className="flex shrink-0 gap-1">
                      <button
                        type="button"
                        onClick={() => void toggleRecording(item.id)}
                        aria-expanded={detail?.id === item.id}
                        className="rounded-control border border-ink-2 px-2 py-0.5 text-[11px] text-ink-6 hover:bg-surface"
                      >
                        {detail?.id === item.id ? '收起' : '看段落'}
                      </button>
                      <button
                        type="button"
                        onClick={() => void removeRecording(item.id)}
                        className="rounded-control border border-ink-2 px-2 py-0.5 text-[11px] text-ng hover:bg-surface"
                      >
                        删除
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}

            {detail && (
              <div className="mt-3 rounded-card border border-ink-1 p-3">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <p className="text-xs font-medium text-ink-7">
                    #{detail.id} 解析出的段落（{detail.segments.length}）
                  </p>
                  <button
                    type="button"
                    onClick={() => setDetail(null)}
                    className="rounded-control border border-ink-2 px-2 py-0.5 text-[11px] text-ink-6 hover:bg-paper"
                  >
                    收起
                  </button>
                </div>
                <div className="max-h-56 space-y-1 overflow-y-auto">
                  {detail.segments.map((segment) => (
                    <p key={segment.idx} className="text-[11px] leading-relaxed text-ink-6">
                      <span className={segment.speaker === 'interviewer' ? 'text-info' : 'text-ok'}>
                        {segment.speaker === 'interviewer' ? '面试官' : segment.speaker === 'candidate' ? '我' : '未知'}
                      </span>
                      {segment.start_ms !== null ? ` (${(segment.start_ms / 1000).toFixed(1)}s)` : ''} {segment.text}
                    </p>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* 右：画像 */}
        <div className="min-h-0 space-y-3 overflow-y-auto pr-1">
          <div className="rounded-panel border border-ink-1 bg-surface p-4">
            <h3 className="mb-3 text-sm font-semibold text-ink-9">② 学出面试官画像</h3>
            <div className="flex items-center gap-2">
              <input
                value={profileName}
                onChange={(event) => setProfileName(event.target.value)}
                placeholder="画像名称（例如：某公司技术面）"
                className={inputClass}
              />
              <button
                type="button"
                onClick={() => void buildProfile()}
                disabled={busy || selectedIds.length === 0}
                className="shrink-0 rounded-control bg-info px-3 py-2 text-xs font-medium text-surface hover:bg-info disabled:opacity-40"
              >
                {(() => {
                  const { mode } = parseTarget(target)
                  const label = mode === 'append' ? '追加' : mode === 'replace' ? '重新生成' : '生成画像'
                  return `${label}（${selectedIds.length}）`
                })()}
              </button>
            </div>
            <p className="mt-2 text-[11px] text-ink-6">
              多选几段同一个面试官的对话会合并统计，节奏特征更稳。
            </p>

            {/* 生成目标：新建 / 追加到已有画像 / 重新生成已有画像。
                追加与重新生成都**更新同一条画像**（保留 id），
                所以已经选了该画像的模拟面试不会失效。 */}
            <div className="mt-2 space-y-1">
              <label className="block text-[11px] text-ink-6">
                生成到
                <select
                  value={target}
                  onChange={(event) => setTarget(event.target.value)}
                  disabled={busy}
                  className="mt-1 w-full rounded-control border border-ink-2 px-2 py-1.5 text-xs"
                >
                  <option value="new">新建一个画像</option>
                  {profiles.map((item) => (
                    <option key={`append-${item.id}`} value={`append:${item.id}`}>
                      追加到：{item.name}
                    </option>
                  ))}
                  {profiles.map((item) => (
                    <option key={`replace-${item.id}`} value={`replace:${item.id}`}>
                      重新生成：{item.name}
                    </option>
                  ))}
                </select>
              </label>
              <p className="text-[10px] leading-relaxed text-ink-4">
                {(() => {
                  const { mode } = parseTarget(target)
                  if (mode === 'append') {
                    return '把勾选的对话并进这个画像后重算。旧录音不重复转写（真 ASR 按量计费），只转新加的。'
                  }
                  if (mode === 'replace') {
                    return '用勾选的对话换掉这个画像的全部录音并重算（会重新转写）。画像 id 保留，已选它的面试不受影响。'
                  }
                  return '新建一个独立的画像。'
                })()}
              </p>
            </div>
          </div>

          <div className="rounded-panel border border-ink-1 bg-surface p-4">
            <h3 className="mb-2 text-sm font-semibold text-ink-9">画像（{profiles.length}）</h3>
            {profiles.length === 0 ? (
              <p className="text-xs text-ink-6">还没有画像。</p>
            ) : (
              <ul className="space-y-2">
                {profiles.map((item) => (
                  <li key={item.id} className="flex items-start gap-2 rounded-card bg-paper px-3 py-2">
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm text-ink-9">
                        #{item.id} {item.name}
                      </p>
                      <p className="text-[11px] text-ink-6">
                        {item.question_count} 题 · 压力 {item.stress_level || '—'} · 样本 {item.sample_count}
                      </p>
                      {item.style_tags.length > 0 && (
                        <p className="mt-1 flex flex-wrap gap-1">
                          {item.style_tags.map((tag) => (
                            <span key={tag} className="rounded-full bg-info-soft px-2 py-0.5 text-[11px] text-info">
                              {tag}
                            </span>
                          ))}
                        </p>
                      )}
                    </div>
                    <div className="flex shrink-0 gap-1">
                      <button
                        type="button"
                        onClick={() => void openProfile(item.id)}
                        className="rounded-control border border-ink-2 px-2 py-0.5 text-[11px] text-ink-6 hover:bg-surface"
                      >
                        详情
                      </button>
                      <button
                        type="button"
                        onClick={() => void removeProfile(item.id)}
                        className="rounded-control border border-ink-2 px-2 py-0.5 text-[11px] text-ng hover:bg-surface"
                      >
                        删除
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {profile && (
            <div className="rounded-panel border border-ink-1 bg-surface p-4">
              <h3 className="mb-2 text-sm font-semibold text-ink-9">{profile.name} · 会怎么面你</h3>
              <dl className="grid gap-1 text-xs text-ink-7">
                <div className="flex justify-between">
                  <dt className="text-ink-6">判断你说完的等待时长</dt>
                  <dd>{profile.policy.silence_ms} ms</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-ink-6">最多追问</dt>
                  <dd>{profile.policy.max_followup_depth} 层</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-ink-6">压力水平</dt>
                  <dd>{profile.policy.stress_level}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-ink-6">面试官语速</dt>
                  <dd>{profile.policy.interviewer_wpm} 字/分</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-ink-6">开场 / 收尾</dt>
                  <dd>
                    {profile.policy.opening_pattern || '—'} / {profile.policy.closing_pattern || '—'}
                  </dd>
                </div>
              </dl>

              {Object.keys(profile.policy.topic_mix).length > 0 && (
                <div className="mt-2">
                  <p className="mb-1 text-xs text-ink-6">题型配比（模拟时会按它出题）</p>
                  <div className="flex flex-wrap gap-1">
                    {Object.entries(profile.policy.topic_mix).map(([label, ratio]) => (
                      <span key={label} className="rounded-control bg-sunken px-2 py-0.5 text-[11px] text-ink-7">
                        {label} {Math.round(ratio * 100)}%
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {profile.policy.followup_triggers.length > 0 && (
                <p className="mt-2 text-[11px] text-ink-6">
                  追问触发：{profile.policy.followup_triggers.join('、')}
                </p>
              )}

              {profile.samples.length > 0 && (
                <div className="mt-3 max-h-56 overflow-y-auto">
                  <table className="w-full text-[11px]">
                    <thead className="bg-paper text-left text-ink-6">
                      <tr>
                        <th className="px-2 py-1 font-medium">题目</th>
                        <th className="px-2 py-1 font-medium">题型</th>
                        <th className="px-2 py-1 font-medium">追问</th>
                        <th className="px-2 py-1 font-medium">等待</th>
                      </tr>
                    </thead>
                    <tbody>
                      {profile.samples.map((sample, index) => (
                        <tr key={`${sample.question}-${index}`} className="border-t border-ink-1">
                          <td className="px-2 py-1 text-ink-7">{sample.question.slice(0, 26)}</td>
                          <td className="px-2 py-1 text-ink-6">{sample.topic || sample.question_type}</td>
                          <td className="px-2 py-1 text-ink-6">{sample.followup_count}</td>
                          <td className="px-2 py-1 text-ink-6">
                            {sample.wait_ms !== null ? `${(sample.wait_ms / 1000).toFixed(1)}s` : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>
      </section>
    </div>
  )
}
