"""故事卡：把你的真实经历抽成 STAR 结构，并在模拟面试里按题型引用。

**核心约束**：故事卡是事实底稿，不是让模型发挥的素材。
所以 `story_card_prompt_block` 里写死了「只能引用、不得改写或新增事实」，
`normalize_stories` 也会丢掉缺 action/result 的卡片——半张卡引用了反而危险。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..models.story import STORY_TAG_ORDER, STORY_TAGS
from ..providers.llm.base import RECORD_MARKER_END, RECORD_MARKER_START, LLMMessage

#: 每类问题的线索词：用来把"面试官刚问的那句话"和"你手里的故事"对上
TAG_CUES: dict[str, tuple[str, ...]] = {
    "failure": ("失败", "挫折", "没做好", "做错", "踩坑", "教训", "出过什么问题", "不理想"),
    "conflict": ("冲突", "分歧", "不配合", "沟通不畅", "说服", "跨部门", "意见不一致", "推不动"),
    "leadership": ("带人", "带团队", "管理", "推动", "协调", "主导", "牵头", "组织"),
    "challenge": ("难点", "难题", "最困难", "技术挑战", "攻坚", "瓶颈", "复杂", "硬骨头"),
    "achievement": ("成就", "亮点", "最有成就", "最自豪", "最满意", "价值最大", "最成功"),
    "growth": ("学习", "成长", "提升", "转变", "新技术", "自学"),
    "efficiency": ("效率", "优化", "加速", "性能", "成本", "自动化", "提速"),
    "quality": ("质量", "稳定", "故障", "线上问题", "事故", "可用性", "崩溃"),
}

#: 一张故事卡至少要有的字段（缺 action/result 的引用比不引用更糟）
REQUIRED_FIELDS = ("title", "action", "result")

STORY_PROMPT = """你在帮求职者把他**已有的经历**整理成面试用的 STAR 故事卡。

硬规则（必须遵守）：
1. **只使用简历里已经写出来的事实**。不得新增公司、项目、技术栈、数字或成果。
2. 如果某段经历的"结果"在简历里没有写，就留空字符串，**不要推测**。
3. 数字只允许照抄简历里出现的；简历里没有数字就留空。
4. 每张卡对应一段**真实经历**（一个项目 / 一次攻关 / 一次冲突），不要编造多段。
5. 全部用中文；`title` 不超过 20 字。

输出 JSON（只输出 JSON，不要解释、不要代码块围栏）：
{
  "stories": [
    {
      "title": "一句话标题",
      "situation": "当时的背景（1-2 句）",
      "task": "你要解决的问题与目标（1-2 句）",
      "action": "你具体做了什么（2-4 句，写清你的角色）",
      "result": "结果与影响（1-2 句）",
      "metrics": "可核对的数字，没有就空字符串",
      "tags": ["achievement", "challenge"],
      "topics": ["性能优化", "Redis"]
    }
  ]
}
`tags` 只能从这个集合里选：%s
"""


def build_story_messages(
    *,
    resume_text: str,
    existing_titles: Sequence[str] = (),
    count: int = 4,
) -> list[LLMMessage]:
    payload = {
        "resume_excerpt": (resume_text or "")[:4000],
        "already_have": list(existing_titles)[:20],
        "how_many": count,
    }
    system = STORY_PROMPT % "、".join(STORY_TAG_ORDER)
    return [
        LLMMessage(role="system", content=system),
        LLMMessage(
            role="user",
            content=f"{RECORD_MARKER_START}\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n{RECORD_MARKER_END}",
        ),
    ]


def _load_json(text: str) -> Any:
    raw = (text or "").strip()
    if not raw:
        return None

    # 模型有时会套代码块围栏
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1).strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 退一步：截取第一个 { 到最后一个 }
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def _clean_tags(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    cleaned: list[str] = []
    for item in values:
        key = str(item).strip()
        if key in STORY_TAGS and key not in cleaned:
            cleaned.append(key)
    return cleaned


def _clean_topics(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    cleaned: list[str] = []
    for item in values:
        topic = str(item).strip()[:40]
        if topic and topic not in cleaned:
            cleaned.append(topic)
    return cleaned[:6]


def story_from_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    """把一条模型输出规整成可入库的字段；缺关键字段则丢弃。"""
    if not isinstance(payload, dict):
        return None

    story = {
        "title": str(payload.get("title") or "").strip()[:60],
        "situation": str(payload.get("situation") or "").strip(),
        "task": str(payload.get("task") or "").strip(),
        "action": str(payload.get("action") or "").strip(),
        "result": str(payload.get("result") or "").strip(),
        "metrics": str(payload.get("metrics") or "").strip(),
        "tags": _clean_tags(payload.get("tags")),
        "topics": _clean_topics(payload.get("topics")),
    }

    if any(not story[field_name] for field_name in REQUIRED_FIELDS):
        return None
    if not story["tags"]:
        # 没打标签的卡在模拟里选不出来，给一个最保守的默认值
        story["tags"] = ["achievement"]
    return story


def normalize_stories(text: str, *, limit: int = 6) -> list[dict[str, Any]]:
    """解析模型输出。解析不出来就返回空列表（由调用方决定怎么提示）。"""
    data = _load_json(text)
    if isinstance(data, dict):
        items = data.get("stories")
    elif isinstance(data, list):
        items = data
    else:
        items = None

    if not isinstance(items, list):
        return []

    stories: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for item in items:
        story = story_from_payload(item if isinstance(item, dict) else {})
        if story is None:
            continue
        key = story["title"].lower()
        if key in seen_titles:
            continue
        seen_titles.add(key)
        stories.append(story)
        if len(stories) >= limit:
            break
    return stories


# --------------------------------------------------------------------------- #
# 在模拟中引用
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class StoryRef:
    """引用一张故事卡所需的最小信息（便于纯函数测试）。"""

    id: int | None
    title: str
    tags: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    situation: str = ""
    task: str = ""
    action: str = ""
    result: str = ""
    metrics: str = ""
    starred: bool = False
    used_count: int = 0


def tags_for_question(question: str) -> list[str]:
    """根据问题文本推测它在问哪类故事。"""
    text = question or ""
    matched: list[str] = []
    for tag, cues in TAG_CUES.items():
        if any(cue in text for cue in cues):
            matched.append(tag)
    return matched


def score_story(ref: StoryRef, *, tags: Sequence[str], topics: Sequence[str]) -> float:
    """给故事卡打分：题型契合 > 主题重叠 > 主推；用过的适当降权（别老讲同一段）。"""
    score = 0.0

    if tags:
        overlap = len(set(ref.tags) & set(tags))
        score += overlap * 2.0

    if topics:
        topic_set = {topic.lower() for topic in topics}
        overlap = len({topic.lower() for topic in ref.topics} & topic_set)
        score += overlap * 1.5

    if ref.starred:
        score += 0.5

    # 引用次数越多越往后退（一场面试里反复讲同一个项目很假）
    score -= min(ref.used_count, 5) * 0.2
    return round(score, 3)


def select_stories(
    cards: Sequence[StoryRef],
    *,
    question: str,
    topics: Sequence[str] = (),
    limit: int = 2,
) -> list[StoryRef]:
    """为当前问题挑故事卡（没有任何契合时返回空列表，由调用方决定要不要硬引）。"""
    if not cards:
        return []

    tags = tags_for_question(question)
    scored = [(score_story(card, tags=tags, topics=topics), card) for card in cards]
    # 有明确题型线索时才要求正向匹配，否则"问题里有成就两个字"也不至于一张都选不出
    ranked = [item for item in sorted(scored, key=lambda pair: -pair[0]) if item[0] > 0]
    if not ranked and cards:
        ranked = sorted(scored, key=lambda pair: -pair[0])[:1]

    return [card for _, card in ranked[:limit]]


def story_card_prompt_block(cards: Sequence[StoryRef]) -> str:
    """把故事卡拼成面试官 prompt 里的一段（含"不得编造"的硬约束）。"""
    if not cards:
        return ""

    lines = [
        "【候选人提供的真实经历（只能引用，不得改写或新增事实）】",
    ]
    for index, card in enumerate(cards, start=1):
        tag_labels = "、".join(STORY_TAGS.get(tag, tag) for tag in card.tags) or "未分类"
        lines.append(f"{index}. {card.title}（{tag_labels}）")
        if card.situation:
            lines.append(f"   背景：{card.situation}")
        if card.task:
            lines.append(f"   目标：{card.task}")
        if card.action:
            lines.append(f"   做法：{card.action}")
        if card.result:
            lines.append(f"   结果：{card.result}")
        if card.metrics:
            lines.append(f"   关键数字：{card.metrics}")

    lines.append(
        "引用规则：问到相关话题时可以自然带出上面的细节，也可以就上面某一句继续追问细节；"
        "**不得编造上面没有的数据或经历**。"
    )
    return "\n".join(lines)


def story_coverage(cards: Sequence[StoryRef]) -> dict[str, int]:
    """每类故事各有几张——用来提示「你缺失败复盘类的故事」这种盲区。"""
    coverage = {tag: 0 for tag in STORY_TAG_ORDER}
    for card in cards:
        for tag in card.tags:
            if tag in coverage:
                coverage[tag] += 1
    return coverage


def missing_story_tags(cards: Sequence[StoryRef]) -> list[str]:
    """一张卡都没有的题型（面试里被问到就答不上来）。"""
    coverage = story_coverage(cards)
    return [tag for tag in STORY_TAG_ORDER if coverage[tag] == 0]
