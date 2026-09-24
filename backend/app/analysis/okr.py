"""试用期 OKR 与转正述职（M15）。

两个纯函数块：
- 进展计算：KR 的 pct = current / target，目标的 pct = 各 KR 的平均。
  target 未设（<=0）时进展显示 0 并标「未设目标值」，而不是拿 0 去除；
- 转正述职稿：**确定性模板合成**，不走 LLM。原因：
  述职稿里的每个数字都该来自你自己的记录，模板拼装不会编造数字，
  也让「稿子内容 == 数据记录」可以被单测钉死。留白的地方用【】标出，由用户自己填。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Sequence

from ..models.career import OKR_STATUS_LABELS


# --------------------------------------------------------------------------- #
# 进展计算
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class KrView:
    """一条 KR 的展示视图（含算出来的进展）。"""

    id: int
    title: str
    unit: str
    target_value: float
    current_value: float
    confidence: int
    sort_order: int
    pct: float
    status: str  # 未设目标值 | 未开始 | 进行中 | 已完成
    updated_at: datetime | None = None


@dataclass(slots=True)
class ObjectiveView:
    """一个目标的展示视图（含 KR 列表与整体进展）。"""

    id: int
    title: str
    period: str
    note: str
    status: str
    status_label: str
    sort_order: int
    krs: list[KrView] = field(default_factory=list)

    @property
    def pct(self) -> float:
        if not self.krs:
            return 0.0
        return sum(kr.pct for kr in self.krs) / len(self.krs)

    @property
    def kr_done(self) -> int:
        return sum(1 for kr in self.krs if kr.status == "已完成")


def kr_status(pct: float, target_value: float) -> str:
    if target_value <= 0:
        return "未设目标值"
    if pct <= 0:
        return "未开始"
    if pct >= 100:
        return "已完成"
    return "进行中"


def kr_pct(current_value: float, target_value: float) -> float:
    """进展百分比：target<=0 视为未设目标，进展记 0；负数当前值按 0 处理。"""
    if target_value <= 0:
        return 0.0
    return round(max(current_value, 0.0) / target_value * 100, 1)


def build_kr_view(kr: Any) -> KrView:
    pct = kr_pct(float(getattr(kr, "current_value", 0.0)), float(getattr(kr, "target_value", 0.0)))
    return KrView(
        id=getattr(kr, "id", 0) or 0,
        title=getattr(kr, "title", ""),
        unit=getattr(kr, "unit", ""),
        target_value=float(getattr(kr, "target_value", 0.0)),
        current_value=float(getattr(kr, "current_value", 0.0)),
        confidence=int(getattr(kr, "confidence", 0)),
        sort_order=int(getattr(kr, "sort_order", 100)),
        pct=pct,
        status=kr_status(pct, float(getattr(kr, "target_value", 0.0))),
        updated_at=getattr(kr, "updated_at", None),
    )


def build_objective_view(objective: Any, krs: Sequence[Any]) -> ObjectiveView:
    status = getattr(objective, "status", "active")
    return ObjectiveView(
        id=getattr(objective, "id", 0) or 0,
        title=getattr(objective, "title", ""),
        period=getattr(objective, "period", ""),
        note=getattr(objective, "note", ""),
        status=status,
        status_label=OKR_STATUS_LABELS.get(status, status),
        sort_order=int(getattr(objective, "sort_order", 100)),
        krs=sorted((build_kr_view(kr) for kr in krs), key=lambda item: (item.sort_order, item.id)),
    )


# --------------------------------------------------------------------------- #
# 转正述职稿
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class ReviewDraft:
    """述职稿：结构化小节 + 渲染好的 Markdown。"""

    title: str
    generated_at: datetime | None
    sections: list[dict[str, Any]] = field(default_factory=list)
    markdown: str = ""
    note: str = ""


def _fmt_number(value: float) -> str:
    if float(value) == int(value):
        return str(int(value))
    return f"{value:g}"


def build_review_draft(
    objectives: Sequence[Any],
    krs_by_objective: Mapping[int, Sequence[Any]],
    *,
    stats: Mapping[str, Any] | None = None,
    generated_at: datetime | None = None,
    title: str = "转正述职",
) -> ReviewDraft:
    """把目标与进展合成一份述职稿。

    稿子里只出现两类内容：来自 OKR/统计记录的数字，和【】留白让用户自己填的部分。
    stats 由 service 层从本地数据算好传入（模拟面试场次、平均分、学习完成数等）。
    """
    sections: list[dict[str, Any]] = []
    views = [build_objective_view(objective, krs_by_objective.get(getattr(objective, "id", 0) or 0, []))
             for objective in objectives]

    kr_total = sum(len(view.krs) for view in views)
    overall_pct = round(sum(view.pct for view in views) / len(views), 1) if views else 0.0
    sections.append({
        "heading": "一、目标回顾",
        "bullets": [
            f"试用期内共制定 {len(views)} 个目标、{kr_total} 条关键结果，整体完成度 {overall_pct}%。"
            if views else "试用期还没有记录任何目标——先去「试用期 OKR」把目标和关键结果补上。",
        ],
    })

    for index, view in enumerate(views, start=1):
        bullets: list[str] = []
        if view.period:
            bullets.append(f"周期：{view.period}")
        if not view.krs:
            bullets.append("这个目标还没有关键结果。")
        for kr in view.krs:
            line = f"- {kr.title}：{_fmt_number(kr.current_value)} / {_fmt_number(kr.target_value)} {kr.unit}（{kr.pct}%，{kr.status}）"
            if kr.status not in ("已完成", "未设目标值") and kr.target_value > 0:
                line += f"，还差 {_fmt_number(max(kr.target_value - kr.current_value, 0.0))} {kr.unit}"
            bullets.append(line)
        sections.append({"heading": f"二.{index} 目标：{view.title}（{view.kr_done}/{len(view.krs)} 条完成）", "bullets": bullets})

    stat_bullets: list[str] = []
    if stats:
        if "sessions_finished" in stats:
            stat_bullets.append(f"试用期内完成模拟面试 {stats.get('sessions_finished', 0)} 场")
        if stats.get("reports_avg_overall") is not None:
            stat_bullets.append(f"面试评分平均 {stats.get('reports_avg_overall')} 分")
        if "mastery_topics" in stats:
            stat_bullets.append(f"在练能力点 {stats.get('mastery_topics', 0)} 个")
        if "learning_done" in stats:
            stat_bullets.append(f"学习清单完成 {stats.get('learning_done', 0)} 项")
    if stat_bullets:
        sections.append({"heading": "三、过程数据（来自本系统的记录）", "bullets": stat_bullets})

    sections.append({
        "heading": "四、下一阶段（【】里自己填，别让模板替你编）",
        "bullets": [
            "转正后最想推进的一件事：【 】",
            "需要团队/上级支持的资源：【 】",
            "给自己定的下个季度的衡量标准：【 】",
        ],
    })

    lines = [f"# {title}", ""]
    if generated_at is not None:
        lines.append(f"> 生成时间：{generated_at.isoformat(timespec='seconds')}；内容由 OKR 记录确定性合成，数字未做任何修饰。")
        lines.append("")
    for section in sections:
        lines.append(f"## {section['heading']}")
        lines.append("")
        for bullet in section["bullets"]:
            if bullet.startswith("- "):
                lines.append(bullet)
            else:
                lines.append(f"- {bullet}")
        lines.append("")

    return ReviewDraft(
        title=title,
        generated_at=generated_at,
        sections=sections,
        markdown="\n".join(lines),
        note="稿中数字全部来自你的 OKR 与练习记录；【】留白处需要你自己补充，系统不会替你编。",
    )
