"""按匹配分区间看回复率：把"投的岗位匹配度"和"有没有回应"对上。

这是"为什么没回复"最直接的证据来源：
- 高分段有回应、低分段没有 → 少投低匹配岗位，匹配度确实在起作用；
- 各段都石沉大海 → 问题不在匹配度（去看招呼语、投递时机、岗位竞争度）；
- 样本太少 → **明说样本太少**，而不是给一个看起来很确定但毫无意义的百分比。

口径与 M6 的投递统计保持一致（复用 `RESPONDED_STATUSES`），避免同一个词在两页里算法不同。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .apply_stats import RESPONDED_STATUSES
from .resume_match import match

#: 分数段（上界开区间，避免 85 分同时落进两段）
BANDS: tuple[tuple[float, float, str], ...] = (
    (85.0, 101.0, "85 分以上"),
    (70.0, 85.0, "70–84 分"),
    (55.0, 70.0, "55–69 分"),
    (0.0, 55.0, "55 分以下"),
)

#: 低于这个样本量的分段不做结论
MIN_SAMPLE = 3

INTERVIEW_STATUSES = frozenset({"interview1", "interview2", "hr", "offer"})


@dataclass(slots=True)
class BandInput:
    """做匹配分段需要的字段（不依赖 ORM）。"""

    jd_text: str = ""
    status: str = "applied"
    company: str = ""


@dataclass(slots=True)
class BandStat:
    label: str
    min_score: float
    count: int = 0
    responded: int = 0
    interviewed: int = 0
    offers: int = 0
    response_rate: float = 0.0
    interview_rate: float = 0.0


@dataclass(slots=True)
class MatchBandReport:
    resume_id: int | None = None
    total_records: int = 0
    #: 有 JD 正文、能算匹配分的记录数
    matched_records: int = 0
    bands: list[BandStat] = field(default_factory=list)
    recommendation: str = ""
    confidence_note: str = ""


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def build_bands(
    records: Sequence[BandInput],
    resume_text: str,
    *,
    resume_id: int | None = None,
    min_sample: int = MIN_SAMPLE,
) -> MatchBandReport:
    """按匹配分把投递记录分段，统计各段的回复率/面试率。"""
    report = MatchBandReport(resume_id=resume_id, total_records=len(records))

    buckets: dict[str, BandStat] = {
        label: BandStat(label=label, min_score=low) for low, _, label in BANDS
    }
    # 同一条 JD 只算一次分（投递记录里常有多条指向同一个岗位描述）
    score_cache: dict[str, float] = {}

    for record in records:
        jd_text = (record.jd_text or "").strip()
        if not jd_text:
            continue

        if jd_text not in score_cache:
            score_cache[jd_text] = match(resume_text, jd_text).score
        score = score_cache[jd_text]

        for low, high, label in BANDS:
            if low <= score < high:
                stat = buckets[label]
                stat.count += 1
                if record.status in RESPONDED_STATUSES:
                    stat.responded += 1
                if record.status in INTERVIEW_STATUSES:
                    stat.interviewed += 1
                if record.status == "offer":
                    stat.offers += 1
                break

    report.matched_records = sum(stat.count for stat in buckets.values())
    report.bands = [buckets[label] for _, _, label in BANDS]

    for stat in report.bands:
        stat.response_rate = _rate(stat.responded, stat.count)
        stat.interview_rate = _rate(stat.interviewed, stat.count)

    report.confidence_note = _confidence_note(report, min_sample=min_sample)
    report.recommendation = _recommendation(report, min_sample=min_sample)
    return report


def _confidence_note(report: MatchBandReport, *, min_sample: int) -> str:
    if report.matched_records == 0:
        return (
            "还没有可比较的记录：投递记录里没有 JD 正文。"
            "用「投递执行台」抓的岗位会自动带 JD；CSV 导入的记录可以在投递明细里补上 JD。"
        )
    if report.total_records > report.matched_records:
        missing = report.total_records - report.matched_records
        note = f"{report.total_records} 条投递里有 {missing} 条没有 JD 正文，未纳入比较。"
    else:
        note = ""

    small = [stat.label for stat in report.bands if 0 < stat.count < min_sample]
    if small:
        note += f" {'、'.join(small)} 的样本少于 {min_sample} 条，百分比仅供参考。"
    return note.strip()


def _recommendation(report: MatchBandReport, *, min_sample: int) -> str:
    if report.matched_records == 0:
        return "补上 JD 之后再回来看这一页——没有 JD 就没有匹配度，也就无从比较。"

    high = report.bands[0]
    low_band = report.bands[-1]
    overall = _rate(sum(stat.responded for stat in report.bands), report.matched_records)

    if high.count >= min_sample and low_band.count >= min_sample:
        if high.response_rate >= low_band.response_rate + 0.2:
            return (
                f"匹配度确实在起作用：85 分以上的回复率 {round(high.response_rate * 100)}%，"
                f"而 55 分以下只有 {round(low_band.response_rate * 100)}%。"
                "把精力集中在高匹配岗位上，低匹配的少投或不投。"
            )
        return (
            f"高低分段的回复率差别不大（{round(high.response_rate * 100)}% vs "
            f"{round(low_band.response_rate * 100)}%），说明卡住你的可能不是匹配度，"
            "更可能是招呼语、投递时机或岗位竞争度——先改这三样再回头看数据。"
        )

    if overall < 0.15 and report.matched_records >= min_sample:
        return (
            f"整体回复率只有 {round(overall * 100)}%（{report.matched_records} 条记录）。"
            "这个水平通常不是简历匹配度的问题：先检查招呼语是不是群发感太重、"
            "投递时间是否在工作日白天、以及是不是都在投竞争极激烈的岗位。"
        )

    return "样本还不足以得出结论。再投一段时间、让每个分数段都有几条记录，这里的对比才有意义。"
