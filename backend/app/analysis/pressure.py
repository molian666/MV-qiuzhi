"""压力模式：追问到底、打断、限时抢答——三种独立开关，也可以叠加。

**为什么做成"开关"而不是一个"难度档"**：
这三个是三种不同的训练目标（练深度 / 练抗打断 / 练限时表达）。
揉成一个难度滑块，用户就没法只练其中一项——而"我到底在补哪个短板"恰恰是最重要的信息。

**每个模式产出两类东西**：
- 进 prompt 的规则（影响模型怎么问）；
- 给运行时的判定函数（影响什么时候插话、什么时候算超时）。
两者用**同一份配置**，避免出现"提示词说限时 90 秒、代码按 60 秒判定"这种漂移。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: 三种模式的 key（前端开关与 API 参数都用它）
PRESSURE_MODES: tuple[str, ...] = ("deep_drill", "interrupt", "time_limit")

MODE_LABELS: dict[str, str] = {
    "deep_drill": "追问到底",
    "interrupt": "打断模式",
    "time_limit": "限时抢答",
}

MODE_SUMMARIES: dict[str, str] = {
    "deep_drill": "同一个问题最多往下追 5 层，直到你说不出新东西为止",
    "interrupt": "你还没说完就插话，逼你在被打断后接住思路",
    "time_limit": "每题有倒计时，时间到了会自动进下一题",
}

#: 各模式的取值边界（UI 与 API 都不能越过）
MAX_DEPTH_LIMIT = 8
MIN_INTERRUPT_MS = 3_000
MAX_INTERRUPT_MS = 60_000
MIN_ANSWER_SECONDS = 20
MAX_ANSWER_SECONDS = 600


@dataclass(slots=True)
class PressureConfig:
    deep_drill: bool = False
    max_depth: int = 5

    interrupt: bool = False
    #: 候选人连续说了这么久还没停，就插入一句（太小会显得没礼貌，太大会失去训练意义）
    interrupt_after_ms: int = 12_000

    time_limit: bool = False
    answer_seconds: int = 90

    #: 面试官语速倍率（压力面会说得更快，压迫感更强）
    speed: float = 1.0

    @property
    def enabled(self) -> bool:
        return self.deep_drill or self.interrupt or self.time_limit

    def modes(self) -> list[str]:
        return [
            key
            for key, on in (
                ("deep_drill", self.deep_drill),
                ("interrupt", self.interrupt),
                ("time_limit", self.time_limit),
            )
            if on
        ]

    def clamped(self) -> PressureConfig:
        """把参数夹到可用范围（前端输入与历史数据都不可信）。"""
        return PressureConfig(
            deep_drill=bool(self.deep_drill),
            max_depth=max(1, min(MAX_DEPTH_LIMIT, int(self.max_depth or 1))),
            interrupt=bool(self.interrupt),
            interrupt_after_ms=max(
                MIN_INTERRUPT_MS, min(MAX_INTERRUPT_MS, int(self.interrupt_after_ms or 12_000))
            ),
            time_limit=bool(self.time_limit),
            answer_seconds=max(
                MIN_ANSWER_SECONDS, min(MAX_ANSWER_SECONDS, int(self.answer_seconds or 90))
            ),
            speed=max(0.5, min(1.6, float(self.speed or 1.0))),
        )

    def to_dict(self) -> dict[str, Any]:
        settings = self.clamped()
        return {
            "deep_drill": settings.deep_drill,
            "max_depth": settings.max_depth,
            "interrupt": settings.interrupt,
            "interrupt_after_ms": settings.interrupt_after_ms,
            "time_limit": settings.time_limit,
            "answer_seconds": settings.answer_seconds,
            "speed": settings.speed,
        }


def normalize_pressure(data: Any) -> PressureConfig:
    """从请求体 / session.plan 里读配置（字段缺失或类型不对都退回默认）。"""
    if isinstance(data, PressureConfig):
        return data.clamped()

    source = data if isinstance(data, dict) else {}
    return PressureConfig(
        deep_drill=bool(source.get("deep_drill", False)),
        max_depth=int(source.get("max_depth") or 5),
        interrupt=bool(source.get("interrupt", False)),
        interrupt_after_ms=int(source.get("interrupt_after_ms") or 12_000),
        time_limit=bool(source.get("time_limit", False)),
        answer_seconds=int(source.get("answer_seconds") or 90),
        speed=float(source.get("speed") or 1.0),
    ).clamped()


def mode_options() -> list[dict[str, Any]]:
    """给前端渲染开关用的元信息（避免前端硬编码中文与默认值）。"""
    return [
        {
            "key": key,
            "label": MODE_LABELS[key],
            "summary": MODE_SUMMARIES[key],
            "defaults": {
                "deep_drill": {"max_depth": 5},
                "interrupt": {"interrupt_after_ms": 12_000},
                "time_limit": {"answer_seconds": 90},
            }[key],
        }
        for key in PRESSURE_MODES
    ]


# --------------------------------------------------------------------------- #
# 进 prompt
# --------------------------------------------------------------------------- #
def pressure_prompt_block(config: PressureConfig, *, locale: str = "zh") -> str:
    """把压力模式写成面试官可执行的行为规则（而不是"请制造压力"这种空话）。"""
    settings = config.clamped()
    if not settings.enabled:
        return ""

    english = locale == "en"
    lines: list[str] = []

    if settings.deep_drill:
        if english:
            lines.append(
                f"- Deep drilling: after each answer, ask ONE follow-up that goes one level deeper "
                f"(why / how it scales / what breaks / what you would change). "
                f"Do not accept a vague answer; drill at most {settings.max_depth} levels on the same topic, "
                f"then move on."
            )
        else:
            lines.append(
                f"- 追问到底：每次回答后追加**一个**更深一层的问题（为什么 / 规模上来会怎样 / 哪里会崩 / 重来怎么改）。"
                f"不接受含糊回答；同一话题最多追 {settings.max_depth} 层，追到底就换题。"
            )

    if settings.interrupt:
        seconds = round(settings.interrupt_after_ms / 1000)
        if english:
            lines.append(
                f"- Interrupting: if the candidate has been speaking for more than {seconds} seconds "
                f"without concluding, cut in with a specific question about what they just said. "
                f"Interrupt on substance, never just to be rude."
            )
        else:
            lines.append(
                f"- 打断：如果候选人连续说了超过 {seconds} 秒还没收尾，就直接插话追问**他刚说的那句**。"
                f"打断要有由头（针对内容），不要为了打断而打断。"
            )

    if settings.time_limit:
        seconds = settings.answer_seconds
        if english:
            lines.append(
                f"- Time limit: the candidate has about {seconds} seconds per answer. "
                f"Ask in a way that makes the time budget obvious (\"in one minute, tell me...\")."
            )
        else:
            lines.append(
                f"- 限时：每题给候选人约 {seconds} 秒。提问时就点明时间预算"
                f"（\"用一分钟说一下…\"），时间到了直接进入下一题，不要等他补充。"
            )

    header = "Pressure rules to follow:" if english else "压力规则（必须执行）："
    return "\n".join([header, *lines])


# --------------------------------------------------------------------------- #
# 给运行时
# --------------------------------------------------------------------------- #
def should_interrupt(elapsed_ms: int, config: PressureConfig) -> bool:
    """候选人这段已经说了多久，该不该插话。

    只判"说得太久"，具体说什么由面试官模型决定——
    这里的职责是决定**时机**，不是决定内容。
    """
    settings = config.clamped()
    if not settings.interrupt:
        return False
    return elapsed_ms >= settings.interrupt_after_ms


def exceeded_time_limit(elapsed_ms: int, config: PressureConfig) -> bool:
    settings = config.clamped()
    if not settings.time_limit:
        return False
    return elapsed_ms >= settings.answer_seconds * 1000


def remaining_seconds(elapsed_ms: int, config: PressureConfig) -> int | None:
    """剩余秒数（没开限时模式时返回 None，前端据此决定要不要显示倒计时）。"""
    settings = config.clamped()
    if not settings.time_limit:
        return None
    remaining = settings.answer_seconds - elapsed_ms // 1000
    return max(0, remaining)


def clamp_depth(current_depth: int, config: PressureConfig) -> int:
    """追问深度推进（到上限就不再往下追）。"""
    settings = config.clamped()
    if not settings.deep_drill:
        return 0
    return min(max(0, int(current_depth or 0)) + 1, settings.max_depth)


def budget_note(config: PressureConfig) -> str:
    """给前端显示的一句话（"这题你有 90 秒"）。"""
    settings = config.clamped()
    if settings.time_limit:
        return f"这题限时 {settings.answer_seconds} 秒"
    if settings.deep_drill:
        return f"这题最多会被追 {settings.max_depth} 层"
    return ""
