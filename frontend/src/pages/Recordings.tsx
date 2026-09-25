/**
 * 节奏学习页（M4）：上传真实面试对话 → 学出面试官画像 → 在模拟里按那个节奏面你。
 *
 * 关键点：没有批量 ASR Key 时，用「粘贴对话文稿」跑通整条链路——
 * 音频照旧可以上传保存，但转写文本来自文稿，页面上会如实标注。
 */

import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import type { ProfileDetail, ProfileListItem, Recording, RecordingDetail } from '../api/types'

const SAMPLE_DIALOG = `[面试官] 先做个自我介绍吧
[我] 我是 5 年后端，最近在做订单系统重构
[面试官] 讲讲这个项目，你具体负责哪一部分？
[我] 我负责下单链路的重构
[面试官] 那当时有没有考虑过别的方案？
[我] 有，考虑过消息队列削峰
`

const STATUS_LABEL: Record<string, string> = {
  uploaded: '待分析',
  transcribing: '转写中',
  analyzing: '分析中',
  done: '已完成',
  failed: '失败',
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function formatTime(value: string): string {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

export default function Recordings() {
  const [recordings, setRecordings] = useState<Recording[]>([])
  const [profiles, setProfiles] = useState<ProfileListItem[]>([])
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const [detail, setDetail] = useState<RecordingDetail | null>(null)
  const [profile, setProfile] = useState<ProfileDetail | null>(null)

  const [title, setTitle] = useState('')
  const [rawText, setRawText] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [profileName, setProfileName] = useState('')

  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

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

  const submitRecording = async () => {
    if (!rawText.trim() && file === null) {
      setError('请粘贴对话文稿，或选择一段音频文件')
      return
    }
    setBusy(true)
    setError('')
    try {
      if (file !== null) {
        await api.uploadRecording(file, title, rawText)
      } else {
        await api.createRecording({ title, raw_text: rawText })
      }
      setTitle('')
      setRawText('')
      setFile(null)
      await refresh()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const buildProfile = async () => {
    if (selectedIds.length === 0) {
      setError('先勾选至少一段录音')
      return
    }
    setBusy(true)
    setError('')
    try {
      const created = await api.createProfile(selectedIds, profileName)
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

  const openRecording = async (id: number) => {
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
        {/* 左：录音 */}
        <div className="min-h-0 space-y-3 overflow-y-auto pr-1">
          <div className="rounded-panel border border-ink-1 bg-surface p-4">
            <h3 className="mb-3 text-sm font-semibold text-ink-9">① 添加一段真实面试对话</h3>

            <input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="标题（例如：某公司一面 3/12）"
              className={`${inputClass} mb-2`}
            />
            <textarea
              value={rawText}
              onChange={(event) => setRawText(event.target.value)}
              rows={8}
              placeholder="粘贴对话文稿，每行一段：&#10;[面试官] 先做个自我介绍吧&#10;[我] 我是 5 年后端…&#10;（也支持 `00:12 面试官：…` 或 `Q: / A:` 格式，带时间戳则等待时长更准）"
              className={`${inputClass} mb-2 resize-y font-mono text-xs`}
            />

            <div className="flex flex-wrap items-center gap-2">
              <input
                type="file"
                accept="audio/*"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                className="text-xs text-ink-6"
              />
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
                disabled={busy}
                className="ml-auto rounded-control bg-ink-9 px-3 py-1.5 text-xs font-medium text-surface hover:bg-ink-9 disabled:opacity-40"
              >
                保存
              </button>
            </div>

            <p className="mt-2 text-[11px] leading-relaxed text-ink-6">
              当前是 Mock 转写：文本来自你的文稿，**不会识别音频内容**（音频只做保存）。
              要接真实语音识别需配置 <code>ASR_PROVIDER=qwen-file</code> 与 Key（尚未接入）。
            </p>
          </div>

          <div className="rounded-panel border border-ink-1 bg-surface p-4">
            <div className="mb-2 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-ink-9">录音（{recordings.length}）</h3>
              <span className="text-xs text-ink-6">勾选后可在右侧生成画像</span>
            </div>

            {recordings.length === 0 ? (
              <p className="text-xs text-ink-6">还没有录音。粘一段对话文稿试试。</p>
            ) : (
              <ul className="space-y-2">
                {recordings.map((item) => (
                  <li key={item.id} className="flex items-start gap-2 rounded-card bg-paper px-3 py-2">
                    <input
                      type="checkbox"
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
                        onClick={() => void openRecording(item.id)}
                        className="rounded-control border border-ink-2 px-2 py-0.5 text-[11px] text-ink-6 hover:bg-surface"
                      >
                        看段落
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
                <p className="mb-2 text-xs font-medium text-ink-7">
                  #{detail.id} 的转写段落（{detail.segments.length}）
                </p>
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
                生成画像（{selectedIds.length}）
              </button>
            </div>
            <p className="mt-2 text-[11px] text-ink-6">
              多选几段同一个面试官的录音会合并统计，节奏特征更稳。
            </p>
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
