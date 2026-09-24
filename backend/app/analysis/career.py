"""长期规划（M15）：晋升/跳槽时机自查清单 + 薪资基准汇总。

两条纪律在这里落地：
- **薪资只给参考区间与来源**。汇总只做「你记录的区间」的 min/median/max 统计，
  并始终带样本量与「未交叉验证」标注；绝不产出「你应该要 X 万」的结论句；
- **时机不替你判断**。清单只给「该问自己什么 + 数据去哪看」，结论由用户自己下。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(slots=True)
class TimingItem:
    """一条时机自查项。kind: promotion（晋升）| switch（跳槽）。"""

    kind: str
    question: str
    why: str
    where: str


def timing_checklist() -> list[TimingItem]:
    """晋升/跳槽时机的自查清单（静态内容，结论自己下）。"""
    return [
        TimingItem(
            "promotion",
            "过去半年有没有一件能一句话说清价值的成果？",
            "晋升答辩的核心是成果叙事：一句话说不清的事，答辩时也讲不圆",
            "翻自己的 OKR 与故事卡（本系统「故事与话术」页）",
        ),
        TimingItem(
            "promotion",
            "对照过目标职级的公开标准吗？",
            "很多公司的职级标准是公开的，先对照差距再谈，比闷头做一年更有效",
            "公司内网职级说明 / 1on1 时直接问上级",
        ),
        TimingItem(
            "promotion",
            "上级知道你想晋升吗？",
            "不少公司晋升要主管提名，不报名等于自动错过窗口",
            "最近一次 1on1 的记录（可记在「人脉与学习」的沟通时间线里）",
        ),
        TimingItem(
            "promotion",
            "本组近一年晋升的人做的事，和你在做的像吗？",
            "这是最接近真实的「晋升画像」，比职级标准还准",
            "内网公示 / 与同组前辈聊",
        ),
        TimingItem(
            "switch",
            "最近 3 个月的面试通过率与得分在升还是平？",
            "练习数据在涨说明市场会为你的能力买单；一直平说明还没准备好",
            "本系统「报告」「训练」页的趋势",
        ),
        TimingItem(
            "switch",
            "简历与话术超过 6 个月没更新了吗？",
            "旧简历投出去的回复率会真实地告诉你市场怎么看你",
            "本系统「简历体检」「故事与话术」页；投递记录的渠道回复率",
        ),
        TimingItem(
            "switch",
            "目标岗位在市场上的招聘量如何？",
            "招聘量是供需的粗信号，注意它是未经验证的外部数据",
            "招聘平台搜索结果数（自己查，注明查询日期）",
        ),
        TimingItem(
            "switch",
            "现薪资与你收集的基准区间差多少？",
            "只比较你自己记录的来源区间；样本少时差异说明不了什么",
            "本页「薪资基准」的记录与汇总",
        ),
        TimingItem(
            "switch",
            "想离开的原因，下一家能解决吗？",
            "「换个地方」解决不了的问题，跳完还会再遇到一遍",
            "自己写下来，放进「长期规划」条目里对照",
        ),
    ]


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


#: 样本少于这个数，汇总只敢说「仅供参考」
MIN_RELIABLE_SAMPLES = 3


def benchmark_summary(rows: Sequence[Any]) -> dict[str, Any]:
    """薪资基准汇总：只统计你自己记录的区间，输出区间与来源，不给结论。

    接受任何带 range_low / range_high / source / verified 属性的对象。
    """
    lows = [float(getattr(row, "range_low", 0.0) or 0.0) for row in rows]
    highs = [float(getattr(row, "range_high", 0.0) or 0.0) for row in rows]
    sources: list[str] = []
    for row in rows:
        source = (getattr(row, "source", "") or "").strip()
        if source and source not in sources:
            sources.append(source)
    verified_count = sum(1 for row in rows if bool(getattr(row, "verified", False)))

    count = len(rows)
    notes: list[str] = ["区间全部来自你记录的来源，未做交叉验证，不构成定价建议"]
    if count == 0:
        return {"count": 0, "sources": [], "note": "还没有记录。先从同学打听、匿名社区等来源收集几条带来源的区间。"}
    if count < MIN_RELIABLE_SAMPLES:
        notes.append(f"样本不足（仅 {count} 条），仅供参考")

    return {
        "count": count,
        "verified_count": verified_count,
        "min_low": min(lows),
        "median_low": _median(lows),
        "max_low": max(lows),
        "min_high": min(highs),
        "median_high": _median(highs),
        "max_high": max(highs),
        "median_range": [_median(lows), _median(highs)],
        "sources": sources,
        "note": "；".join(notes),
    }
