"""面试官风格库：六套预设，每套都是一组**能落地执行**的参数。

预设不是"人设文案"。每一套都要能同时影响三件事，否则就只是换个说法：
1. **prompt**：题型配比、追问深度、语气（模型据此换问法）；
2. **运行时**：语速倍率、压力水平（影响 TTS 与 VAD 节奏）；
3. **音色**：多角色连环面时，不同角色必须听得出是不同的人。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: 题型 key 与 M4 的节奏分析保持一致（那样"画像学到什么"与"预设给什么"是同一套刻度）
TOPIC_KEYS: tuple[str, ...] = (
    "self_intro",
    "project_deep",
    "tech",
    "behavior",
    "algorithm",
    "design",
    "scenario",
    "salary",
    "reverse",
)


@dataclass(frozen=True, slots=True)
class InterviewerStyle:
    key: str
    label: str
    summary: str
    #: 题型配比（会被归一化，不必自己凑够 1.0）
    question_mix: dict[str, float] = field(default_factory=dict)
    #: 追问深度与频率
    followup_depth: int = 2
    followup_rate: float = 0.5
    #: 压力水平 0–1（同时影响 prompt 语气与语速）
    stress_level: float = 0.3
    #: 语速倍率（1.0 为常规）
    speed: float = 1.0
    #: 逻辑音色名。真正的 voice id 由 TTS provider 映射（见 providers/tts/base.py）
    voice: str = "neutral"
    tone: str = ""
    #: 直接进 prompt 的行为要求
    instructions: tuple[str, ...] = ()


STYLES: dict[str, InterviewerStyle] = {
    "technical": InterviewerStyle(
        key="technical",
        label="技术面",
        summary="深挖技术细节与项目实现，关注正确性与边界",
        question_mix={"project_deep": 0.35, "tech": 0.35, "design": 0.15, "algorithm": 0.15},
        followup_depth=3,
        followup_rate=0.7,
        stress_level=0.35,
        speed=1.0,
        voice="male_calm",
        tone="就事论事、追问细节、不寒暄",
        instructions=(
            "重点问「怎么实现的、为什么这么选、边界在哪」",
            "候选人答得含糊时追问具体数字与失败场景",
            "不要问薪资、规划这类非技术问题",
        ),
    ),
    "cross": InterviewerStyle(
        key="cross",
        label="交叉面",
        summary="来自相邻团队的面试官，偏系统设计与协作视角",
        question_mix={"design": 0.35, "scenario": 0.3, "project_deep": 0.2, "behavior": 0.15},
        followup_depth=3,
        followup_rate=0.6,
        stress_level=0.45,
        speed=1.0,
        voice="female_clear",
        tone="好奇、会挑战你的方案、关注取舍",
        instructions=(
            "多问「如果规模扩大 10 倍会怎样」「你考虑过哪些替代方案」",
            "对被质疑要有回应：要么说服，要么承认",
        ),
    ),
    "manager": InterviewerStyle(
        key="manager",
        label="主管面",
        summary="关注业务判断、推动力与结果，而不是技术细节",
        question_mix={"project_deep": 0.3, "scenario": 0.3, "behavior": 0.25, "design": 0.15},
        followup_depth=2,
        followup_rate=0.5,
        stress_level=0.5,
        speed=0.95,
        voice="male_deep",
        tone="务实、关注结果与影响面",
        instructions=(
            "多问「这件事的业务价值是什么」「你和别人意见不一致时怎么办」",
            "关注你做了什么决策、带来了什么改变，而不是用了什么技术",
        ),
    ),
    "hr": InterviewerStyle(
        key="hr",
        label="HR 面",
        summary="动机、稳定性、薪资与规划",
        question_mix={"self_intro": 0.2, "behavior": 0.35, "salary": 0.25, "reverse": 0.2},
        followup_depth=2,
        followup_rate=0.5,
        stress_level=0.25,
        speed=1.0,
        voice="female_warm",
        tone="温和但会追问细节与前后一致性",
        instructions=(
            "关注离职原因、职业规划、期望薪资与到岗时间",
            "简历上的时间线或说法有矛盾时，礼貌地问清楚",
        ),
    ),
    "behavioral": InterviewerStyle(
        key="behavioral",
        label="行为面",
        summary="用 STAR 追问真实经历：冲突、失败、推动、成长",
        question_mix={"behavior": 0.7, "project_deep": 0.3},
        followup_depth=3,
        followup_rate=0.75,
        stress_level=0.4,
        speed=1.0,
        voice="female_bright",
        tone="耐心、会追问「当时你怎么想的」",
        instructions=(
            "每个问题都要求具体事例（时间、人物、你的动作、结果）",
            "候选人讲成抽象总结时，把他拉回具体场景",
        ),
    ),
    "stress": InterviewerStyle(
        key="stress",
        label="压力面",
        summary="高压追问、质疑式提问，练抗压与临场",
        question_mix={"tech": 0.3, "project_deep": 0.3, "scenario": 0.25, "behavior": 0.15},
        followup_depth=5,
        followup_rate=0.9,
        stress_level=0.9,
        speed=1.15,
        voice="male_sharp",
        tone="直接、会打断、会质疑你的结论",
        instructions=(
            "对回答里的结论直接反问「依据是什么」「如果我说这是错的呢」",
            "连续追问，不给太多缓冲；但**不做人身攻击**，只针对内容",
            "候选人被问住时不要放水，等他给出真实反应",
        ),
    ),
}

STYLE_ORDER: tuple[str, ...] = tuple(STYLES.keys())

#: 多角色连环面的默认编排：技术 → 交叉 → 主管 → HR
DEFAULT_PANEL: tuple[tuple[str, int], ...] = (("technical", 3), ("cross", 2), ("manager", 2), ("hr", 2))

#: 音色必须互不相同，否则"连环面"就只是换个人名继续问
VOICE_LABELS: dict[str, str] = {
    "male_calm": "沉稳男声",
    "male_deep": "低沉男声",
    "male_sharp": "锐利男声",
    "female_clear": "清亮女声",
    "female_warm": "温和女声",
    "female_bright": "明亮女声",
    "neutral": "中性音色",
}


def resolve_style(key: str | None) -> InterviewerStyle:
    """未知 key 回退到技术面（而不是报错：风格是增强项，不该让会话开不起来）。"""
    return STYLES.get(str(key or "").strip()) or STYLES["technical"]


def normalize_mix(mix: dict[str, float]) -> dict[str, float]:
    """把题型配比归一化，并丢掉未知 key。"""
    known = {key: float(value) for key, value in (mix or {}).items() if key in TOPIC_KEYS}
    total = sum(known.values())
    if total <= 0:
        return {}
    return {key: round(value / total, 4) for key, value in known.items()}


def style_options() -> list[dict[str, Any]]:
    """给前端渲染风格选择用的元信息。"""
    return [
        {
            "key": style.key,
            "label": style.label,
            "summary": style.summary,
            "tone": style.tone,
            "voice": style.voice,
            "voice_label": VOICE_LABELS.get(style.voice, style.voice),
            "followup_depth": style.followup_depth,
            "stress_level": style.stress_level,
            "question_mix": normalize_mix(style.question_mix),
        }
        for style in STYLES.values()
    ]


def style_prompt_block(style: InterviewerStyle, *, locale: str = "zh") -> str:
    """把一套风格写成面试官的设定（进 prompt）。"""
    english = locale == "en"
    mix = normalize_mix(style.question_mix)

    if english:
        title = f"【Interviewer: {style.label}】"
        lines = [title, f"Focus: {style.summary}", f"Tone: {style.tone}."]
        lines.append(
            f"Ask at most {style.followup_depth} follow-ups deep; "
            f"follow up on roughly {round(style.followup_rate * 100)}% of answers."
        )
        if mix:
            share = "、".join(f"{key} {round(ratio * 100)}%" for key, ratio in mix.items())
            lines.append(f"Question mix to stay close to: {share}")
        lines.extend(f"- {item}" for item in style.instructions)
        return "\n".join(lines)

    lines = [
        f"【面试官角色：{style.label}】",
        f"定位：{style.summary}",
        f"语气：{style.tone}。",
        f"追问：最多 {style.followup_depth} 层，约 {round(style.followup_rate * 100)}% 的回答要追问。",
    ]
    if mix:
        share = "、".join(f"{TOPIC_KEY_LABELS.get(key, key)} {round(ratio * 100)}%" for key, ratio in mix.items())
        lines.append(f"整场题型配比（尽量贴近）：{share}")
    lines.extend(f"- {item}" for item in style.instructions)
    return "\n".join(lines)


#: 题型 key → 中文标签（与 M4 的 TOPIC_LABELS 对齐，这里只放风格库会用到的）
TOPIC_KEY_LABELS: dict[str, str] = {
    "self_intro": "自我介绍",
    "project_deep": "项目深挖",
    "tech": "技术细节",
    "behavior": "行为面试",
    "algorithm": "算法题",
    "design": "系统设计",
    "scenario": "场景题",
    "salary": "薪资与期望",
    "reverse": "反问环节",
}


def style_params(style: InterviewerStyle) -> dict[str, Any]:
    """转成 M4 的 policy params 结构（这样压力与风格能走同一条注入通路）。"""
    return {
        "topic_mix": normalize_mix(style.question_mix),
        "followup_depth": style.followup_depth,
        "followup_rate": style.followup_rate,
        "stress_level": style.stress_level,
        "speed": style.speed,
        "voice": style.voice,
        "tone": style.tone,
        "style_key": style.key,
        "style_label": style.label,
    }
