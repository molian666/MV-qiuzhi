/** M14 教练模式点评卡：文字面试与语音面试共用。 */

import type { CoachInfo } from '../api/types'

export default function CoachCard({ coach }: { coach: CoachInfo | null | undefined }) {
  if (!coach) {
    return null
  }
  const points = coach.points?.trim()
  const missed = coach.missed?.trim()
  const better = coach.better?.trim()
  if (!points && !missed && !better) {
    return null
  }
  return (
    <div className="mt-1.5 max-w-[80%] rounded-control border border-info/30 bg-info-soft px-3 py-2 text-xs leading-relaxed">
      <div className="mb-1 font-medium text-info">教练点评</div>
      {points && (
        <div className="text-ink-8">
          <span className="text-ink-4">这题要点：</span>
          {points}
        </div>
      )}
      {missed && (
        <div className="mt-0.5 text-ink-8">
          <span className="text-ink-4">你漏了：</span>
          {missed}
        </div>
      )}
      {better && (
        <div className="mt-0.5 text-ink-8">
          <span className="text-ink-4">更优说法：</span>
          {better}
        </div>
      )}
    </div>
  )
}
