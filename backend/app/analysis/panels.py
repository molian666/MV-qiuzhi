"""连环面：一场会话里按轮次切换面试官。

**关键约束：每个角色的音色必须不同。**
同一个人换个头衔继续问，对"适应不同面试官"这件事没有训练价值——那只是换了个名字。
所以 `assign_voices` 会在检测到音色重复时自动换一个（真实需求：用户完全可能配两轮技术面）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from .interviewer_styles import (
    DEFAULT_PANEL,
    VOICE_LABELS,
    InterviewerStyle,
    resolve_style,
)

#: 可用的逻辑音色池（voice id 的实际映射在 TTS provider 里）
VOICE_POOL: tuple[str, ...] = (
    "male_calm",
    "female_clear",
    "male_deep",
    "female_warm",
    "male_sharp",
    "female_bright",
    "neutral",
)

#: 单场连环面的轮次上限（再多就变成折磨了）
MAX_TOTAL_ROUNDS = 40


@dataclass(slots=True)
class PanelStage:
    style_key: str
    rounds: int
    voice: str = ""
    #: 换人时的过渡语（由 prompt 生成，这里只是一个开关标记）
    handover: bool = True

    @property
    def style(self) -> InterviewerStyle:
        return resolve_style(self.style_key)


@dataclass(slots=True)
class PanelPlan:
    stages: list[PanelStage] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.stages = assign_voices(self.stages)

    def total_rounds(self) -> int:
        return sum(max(0, stage.rounds) for stage in self.stages)

    def stage_index_for(self, round_index: int) -> int | None:
        """第 N 轮（0 起）由哪位面试官来问。"""
        if not self.stages:
            return None
        remaining = max(0, int(round_index or 0))
        for index, stage in enumerate(self.stages):
            if remaining < max(0, stage.rounds):
                return index
            remaining -= max(0, stage.rounds)
        return len(self.stages) - 1

    def stage_for(self, round_index: int) -> PanelStage | None:
        index = self.stage_index_for(round_index)
        return self.stages[index] if index is not None else None

    def style_for(self, round_index: int) -> InterviewerStyle | None:
        stage = self.stage_for(round_index)
        return stage.style if stage is not None else None

    def voice_for(self, round_index: int) -> str:
        stage = self.stage_for(round_index)
        return stage.voice if stage is not None else "neutral"

    def is_handover(self, round_index: int) -> bool:
        """这一轮是不是换人后的第一轮（用于插入过渡语）。"""
        index = self.stage_index_for(round_index)
        if index is None or index == 0:
            return False
        previous_total = sum(max(0, stage.rounds) for stage in self.stages[:index])
        return round_index == previous_total

    def timeline(self) -> list[dict[str, Any]]:
        """给前端画"接下来由谁问几轮"。"""
        rows: list[dict[str, Any]] = []
        cursor = 0
        for stage in self.stages:
            style = stage.style
            rows.append(
                {
                    "style_key": stage.style_key,
                    "label": style.label,
                    "summary": style.summary,
                    "voice": stage.voice,
                    "voice_label": VOICE_LABELS.get(stage.voice, stage.voice),
                    "start_round": cursor,
                    "rounds": stage.rounds,
                }
            )
            cursor += max(0, stage.rounds)
        return rows

    def current(self, round_index: int) -> dict[str, Any]:
        """当前面试官的信息（前端顶部显示"现在是谁在问"）。"""
        index = self.stage_index_for(round_index)
        if index is None:
            return {}

        stage = self.stages[index]
        style = stage.style
        return {
            "index": index,
            "total": len(self.stages),
            "style_key": stage.style_key,
            "label": style.label,
            "voice": stage.voice,
            "voice_label": VOICE_LABELS.get(stage.voice, stage.voice),
            "is_handover": self.is_handover(round_index),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "stages": [
                {"style_key": stage.style_key, "rounds": stage.rounds, "voice": stage.voice}
                for stage in self.stages
            ],
            "total_rounds": self.total_rounds(),
            "timeline": self.timeline(),
        }


def assign_voices(stages: Sequence[PanelStage]) -> list[PanelStage]:
    """保证每个角色音色不同：撞了就按池子顺序换一个没用过的。"""
    used: set[str] = set()
    result: list[PanelStage] = []

    for stage in stages:
        voice = stage.voice or stage.style.voice
        if voice in used:
            replacement = next(
                (candidate for candidate in VOICE_POOL if candidate not in used),
                voice,  # 池子用完了就只能重复（正常编排不会到这一步）
            )
            voice = replacement
        used.add(voice)
        result.append(PanelStage(style_key=stage.style_key, rounds=stage.rounds, voice=voice, handover=stage.handover))

    return result


def build_panel(spec: Sequence[tuple[str, int]] | None = None) -> PanelPlan:
    """从 [(风格, 轮数)] 构造（默认编排：技术 → 交叉 → 主管 → HR）。"""
    source = spec or DEFAULT_PANEL
    stages: list[PanelStage] = []

    for style_key, rounds in source:
        count = max(0, int(rounds or 0))
        if count <= 0:
            continue
        stages.append(PanelStage(style_key=str(style_key), rounds=count))

    if not stages:
        stages = [PanelStage(style_key="technical", rounds=6)]

    # 轮次总量兜底（防止配置写成 100 轮）
    total = sum(stage.rounds for stage in stages)
    if total > MAX_TOTAL_ROUNDS:
        ratio = MAX_TOTAL_ROUNDS / total
        for stage in stages:
            stage.rounds = max(1, int(stage.rounds * ratio))

    return PanelPlan(stages=stages)


def parse_panel(data: Any) -> PanelPlan | None:
    """从请求体 / session.plan 里读连环面配置；没配就返回 None（走单面试官模式）。"""
    if isinstance(data, PanelPlan):
        return data
    if not isinstance(data, (list, tuple)) or not data:
        if isinstance(data, dict):
            stages = data.get("stages")
            if isinstance(stages, list) and stages:
                return build_panel(
                    [(str(item.get("style_key") or ""), int(item.get("rounds") or 0)) for item in stages if isinstance(item, dict)]
                )
        return None

    spec: list[tuple[str, int]] = []
    for item in data:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            spec.append((str(item[0]), int(item[1])))
        elif isinstance(item, dict):
            spec.append((str(item.get("style_key") or ""), int(item.get("rounds") or 0)))
    return build_panel(spec) if spec else None


def panel_prompt_block(
    plan: PanelPlan,
    round_index: int,
    *,
    locale: str = "zh",
) -> str:
    """告诉模型"你现在是谁、上一位是谁、哪些问题不该再由你问"。"""
    info = plan.current(round_index)
    if not info:
        return ""

    style = plan.style_for(round_index)
    if style is None:
        return ""

    previous = plan.style_for(max(0, round_index - 1))
    english = locale == "en"

    lines: list[str] = []
    if english:
        lines.append(
            f"【You are interviewer {info['index'] + 1} of {info['total']}: {style.label}】"
        )
        if info["is_handover"]:
            lines.append(
                "This is your first question: briefly introduce yourself, then ask. "
                "Do not repeat topics the previous interviewer already covered."
            )
        if previous is not None and previous.key != style.key:
            lines.append(f"The previous interviewer was: {previous.label}. Stay in your own lane.")
        lines.append(f"Keep the length of this stage in mind ({plan.stage_for(round_index).rounds} questions).")
        return "\n".join(lines)

    lines.append(f"【你现在是第 {info['index'] + 1} 位面试官（共 {info['total']} 位）：{style.label}】")
    if info["is_handover"]:
        lines.append("这是你第一次提问：先简单自我介绍一句，再开始问。**不要重复上一位问过的内容**。")
    if previous is not None and previous.key != style.key:
        lines.append(f"上一位面试官是「{previous.label}」，你只负责你这个角色关心的问题。")
    stage = plan.stage_for(round_index)
    if stage is not None:
        lines.append(f"这个角色这一场大概问 {stage.rounds} 轮。")
    return "\n".join(lines)
