"""Mock 的出题：没有 Key 也能跑通「生成 → 预览 → 采纳」的闭环。

**生成的题目必须一眼看出是占位内容。** Mock 没有能力真的出好题；如果它编出看起来
像真的题（尤其是带上具体数值、型号、结论），用户会误以为那是可用的面试题——
在这个项目里，编造是比「功能不可用」更严重的问题。

所以这里的题干带明确标记，答案里直接说明自己不是真题。
"""

from __future__ import annotations

import json
import re

from ...vision.question import ROLE_LABELS
from .base import LLMMessage

#: 各方向可以出的分类，Mock 按它轮换，让生成结果看起来有区分度
_CATEGORY_HINTS: dict[str, list[str]] = {
    "development": ["算法与精度", "标定与坐标", "性能与工程", "深度学习与缺陷检测"],
    "application": ["相机与传感器", "镜头与光学", "光源与打光", "精度与验收"],
    "implementation": ["标定与坐标", "故障排查", "现场实施", "验收与交付"],
}

_ANGLES = [
    "你会先问清哪些条件，再决定怎么做",
    "实际做的时候最大的坑在哪，怎么规避",
    "如果条件反过来（精度要求提高一个量级 / 时间压缩一半），你的方案要怎么改",
    "你怎么判断这套做法真的有效，而不是碰巧对了一次",
    "客户不接受你的结论时，你怎么办",
]


def build_mock_generated_questions(messages: list[LLMMessage], *, count: int = 3) -> str:
    """按对话里的信息造一批**结构完整但明显是占位**的题。"""
    role = "development"
    category = ""
    difficulty = 3
    existing = 0
    wanted = count

    for message in messages:
        content = message.content
        for key, label in ROLE_LABELS.items():
            if f"**{label}**岗位" in content or f"{label}方向" in content:
                role = key
                break
        for hint in _CATEGORY_HINTS.get(role, []):
            if f"分类统一为：{hint}" in content:
                category = hint
                break
        asked = re.search(r"请出\s*(\d+)\s*道题", content)
        if asked:
            wanted = max(1, min(int(asked.group(1)), 12))
        if "已经出过了" in content:
            existing = content.count("\n- ")

    label = ROLE_LABELS.get(role, role)
    categories = [category] if category else _CATEGORY_HINTS.get(role, ["通用"])
    category_note = f"{label}方向"
    if category:
        category_note += f" · {category}"

    questions = []
    for index in range(wanted):
        angle = _ANGLES[(index + existing) % len(_ANGLES)]
        item_category = categories[index % len(categories)]
        questions.append(
            {
                "question": f"（Mock 占位题 {index + 1}）{label}方向的面试里，关于「{angle}」，你一般怎么处理？",
                "category": item_category,
                "difficulty": difficulty,
                "points": [
                    "这是 Mock 生成的占位要点——没有真实内容，不要当面试题用",
                    "配上真实模型（在系统设置里填 API Key）后，这里会是真正的答题要点",
                    "Mock 的意义是让「生成 → 预览 → 采纳 → 用于面试」这条链路能跑通",
                ],
                "answer": (
                    "这是一道 **Mock 生成的占位题**，不是真实面试题。\n\n"
                    "它的作用是让你在没有配置模型的情况下，也能把「生成题目 → 预览 → "
                    "采纳进题库 → 在模拟面试里按占比抽到它」这条链路走一遍，"
                    "确认交互和流量都正常。\n\n"
                    "配上真实模型之后，这个位置会是一道有实质内容的题：题干、答题要点、"
                    "参考答案、追问方向、常见错误都会齐全。\n\n"
                    "**不要把 Mock 生成的题当成可用的面试素材**——它没有经过任何事实核对。"
                ),
                "followups": [
                    "（Mock 生成）这道题在真实模型下会带 2-4 个追问方向",
                ],
                "pitfalls": [
                    "（Mock 生成）这道题在真实模型下会列出常见错误回答",
                    "把 Mock 生成的题当成真实面试题来背——这是最需要避免的",
                ],
            }
        )

    return json.dumps(
        {
            "questions": questions,
            "mock": True,
            "note": "以上为 Mock 占位内容，未经过事实核对，不要作为面试素材使用。",
        },
        ensure_ascii=False,
    )
