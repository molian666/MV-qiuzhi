/**
 * 画像预览：选了面试官画像后，明确显示"这场会怎么面你"。
 *
 * 为什么需要它：画像的全部意义是**让模拟更像那个真实面试官**，
 * 但在此之前，选完画像只能看到一个名字——用户无法判断学习到底有没有生效，
 * 于是"节奏学习"看起来就只是个分析报告。
 *
 * 这里把真正被注入面试官 prompt 的东西摊开：
 * 会问到哪些真实原题、题型配比、追问深度、开场话术。
 */

import type { ProfilePolicy } from '../api/types'

const STRESS_LABEL: Record<string, string> = {
  low: '低',
  medium: '中',
  high: '高',
}

export default function ProfilePreview({ policy }: { policy: ProfilePolicy }) {
  const typeMix = Object.entries(policy.type_mix ?? {}).sort((a, b) => b[1] - a[1])
  const questions = policy.sample_questions ?? []

  return (
    <div className="rounded-card border border-info bg-info-soft p-3">
      <p className="text-xs font-medium text-ink-9">
        这场会按「{policy.profile_name}」的节奏面你
      </p>

      <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-ink-7">
        <div className="flex justify-between gap-2">
          <dt className="text-ink-6">追问深度</dt>
          <dd>最多 {policy.max_followup_depth} 层</dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt className="text-ink-6">压力</dt>
          <dd>{STRESS_LABEL[policy.stress_level] ?? policy.stress_level}</dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt className="text-ink-6">语速</dt>
          <dd>{policy.interviewer_wpm} 字/分</dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt className="text-ink-6">等你开口</dt>
          <dd>{(policy.silence_ms / 1000).toFixed(1)}s</dd>
        </div>
      </dl>

      {typeMix.length > 0 && (
        <div className="mt-2">
          <p className="text-[11px] text-ink-6">题型配比（按它出题）</p>
          <div className="mt-1 flex flex-wrap gap-1">
            {typeMix.map(([label, ratio]) => (
              <span key={label} className="rounded-control bg-surface px-1.5 py-0.5 text-[10px] text-ink-7">
                {label} {Math.round(ratio * 100)}%
              </span>
            ))}
          </div>
        </div>
      )}

      {questions.length > 0 && (
        <details className="mt-2">
          <summary className="cursor-pointer text-[11px] text-ink-7">
            他真实问过的 {questions.length} 道题（会作为素材喂给面试官）
          </summary>
          <ol className="mt-1 max-h-40 space-y-0.5 overflow-y-auto">
            {questions.map((question, index) => (
              <li key={`${index}-${question}`} className="text-[11px] leading-relaxed text-ink-6">
                {index + 1}. {question}
              </li>
            ))}
          </ol>
        </details>
      )}

      {policy.opening_line && (
        <p className="mt-2 text-[11px] leading-relaxed text-ink-6">
          开场会像这样：<span className="text-ink-9">{policy.opening_line}</span>
        </p>
      )}

      <p className="mt-2 text-[10px] leading-relaxed text-ink-4">
        这些来自你上传的真实面试对话。面试官会围绕这些题材提问，但会结合本次岗位与简历改编，
        不是把原题照念一遍。
      </p>
    </div>
  )
}
