"""AI 打招呼语：像人写的第一句话，不是群发模板。

PLAN §6.7 的硬要求：结合**用户真实经历 + 岗位 JD**，禁止群发式空话。
所以 prompt 里同时给简历要点与 JD，并明确列出一批禁用开头
（"您好，我对贵公司岗位很感兴趣"这类话，HR 一眼就能看出是机器发的）。

`normalize_say_hi` 是纯函数：模型输出常带换行、引号、自我介绍式长段落，
而招呼语必须短、平、能一眼看完——所以入库前统一收敛。
"""

from __future__ import annotations

import re

from ..providers.llm.base import (
    RECORD_MARKER_END,
    RECORD_MARKER_START,
    LLMMessage,
)

#: 招呼语长度上限（Boss 这类平台首条消息太长体验很差）
MAX_SAY_HI_CHARS = 150

BANNED_OPENINGS = (
    "您好，我对贵公司",
    "您好，我对该岗位",
    "我对贵公司的这个岗位很感兴趣",
    "很高兴看到贵公司的招聘信息",
    "我是一名",
    "本人从事",
)

SAY_HI_PROMPT = """你是求职者的助手，任务是写一条发给 HR 的第一条打招呼消息。

要求：
1. **像人说话**，不要客套开场白。禁止出现："您好，我对贵公司/该岗位很感兴趣"、"我是一名…"、"很高兴看到…"。
2. 必须包含至少一个**具体的匹配点**：来自候选人的真实经历，且与 JD 的要求对应。
3. 尽量带一个**可核对的数字**（如果有的话）；简历里没有就别编。
4. 一句话到两句话，总共不超过 120 字；不要换行、不要列点、不要表情符号。
5. 不承诺任何简历里没有的事实，不要写"精通"这类无法证明的词。
6. 全部中文。

只输出一段话，不要输出 JSON、不要输出引号或其他解释。
"""


def _marker_payload(payload_text: str) -> str:
    return f"{RECORD_MARKER_START}\n{payload_text}\n{RECORD_MARKER_END}"


def build_say_hi_messages(*, job_title: str, company: str, jd_text: str, resume_text: str) -> list[LLMMessage]:
    import json

    payload = {
        "company": company,
        "title": job_title,
        "jd_excerpt": (jd_text or "")[:1200],
        "resume_excerpt": (resume_text or "")[:1500],
    }
    return [
        LLMMessage(role="system", content=SAY_HI_PROMPT),
        LLMMessage(
            role="user",
            content=_marker_payload(json.dumps(payload, ensure_ascii=False, indent=2)),
        ),
    ]


def normalize_say_hi(text: str, *, limit: int = MAX_SAY_HI_CHARS) -> str:
    """把模型输出收敛成一条能直接发的招呼语。

    - 去掉换行/表格/项目符号，压成一段；
    - 去掉包裹的引号（模型很爱加）；
    - 命中禁用开场白时，把那段客套话剥掉（保留后面的实质内容）；
    - 超长则截断到最后一个完整句子。
    """
    raw = (text or "").strip()
    if not raw:
        return ""

    # 去掉代码块围栏与行内的 markdown 标记
    raw = re.sub(r"```[a-zA-Z]*", "", raw).replace("```", "")
    raw = re.sub(r"^\s*[-*•\d]+[.、)]\s*", "", raw, flags=re.MULTILINE)
    raw = raw.replace("\r", "\n")

    # 压成一段
    single = " ".join(part.strip() for part in raw.splitlines() if part.strip())
    single = re.sub(r"\s{2,}", " ", single).strip()

    # 去掉整体包裹的引号
    if len(single) >= 2 and single[0] in "\"'“”" and single[-1] in "\"'“”":
        single = single[1:-1].strip()

    # 剥掉客套开场白（只剥一次，避免把正文也吃掉）
    for banned in BANNED_OPENINGS:
        index = single.find(banned)
        if index == 0:
            tail = single[len(banned) :].lstrip("，,。. ：: ")
            if tail:
                single = tail
            break
        if index > 0:
            single = single[:index].strip() or single
            break

    if len(single) > limit:
        # 尽量在句末截断，读起来不像被砍断
        head = single[:limit]
        for mark in ("。", "！", "!", "；", ";"):
            cut = head.rfind(mark)
            if cut >= limit // 2:
                head = head[: cut + 1]
                break
        single = head.strip()

    return single
