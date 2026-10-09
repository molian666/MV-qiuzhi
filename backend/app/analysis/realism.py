"""拟真与反馈（M14）：教练模式、沉默施压、现场突发、简历深挖。

与压力模式（pressure.py）同一个套路：**规则进 prompt、行为可叠加、开关独立**。

四个开关对应四类不同的训练目标，揉成一个"难度档"会让用户没法只练其中一项：

- `coach`：练"答得完整"——每题答完先给点评（要点 / 漏点 / 更优说法），让用户**当场**修正。
  真实面试没有这一环，它是为提升速度设计的，所以默认关，想练的时候开；
- `silence_pressure`：练"把话说完"——答完不急着接话，逼候选人自己补全；
- `scene`：练"临场排查"——中途插入一道现场故障题，答案没法背；
- `resume_dive`：练"经得起挖"——按简历逐行追问，回答必须落在自己写过的项目上。

落地位置：全部在 prompt 层（不动运行时、不动回合 JSON 结构）——
唯一超出 prompt 的是 coach：它要求面试官在输出 JSON 时额外带一个 `coach` 字段，
由 `realtime/interviewer.py` 解析后随回合落库、前端据此展示点评卡。
"""

from __future__ import annotations


def realism_prompt_block(
    *,
    coach: bool = False,
    silence_pressure: bool = False,
    scene: bool = False,
    resume_dive: bool = False,
    locale: str = "zh",
) -> str:
    """把四个拟真 / 反馈开关写成面试官可执行的行为规则。

    顺序有讲究（越靠近末尾的指令越容易被模型遵守）：
    沉默施压（回合行为）→ 现场突发（时机）→ 简历深挖（全程行为）→
    教练模式（JSON 输出要求，必须压在最末才不会被挤掉）。
    全部关着时返回空串，调用方直接跳过。
    """
    if not any((coach, silence_pressure, scene, resume_dive)):
        return ""

    english = locale == "en"
    lines: list[str] = []

    if silence_pressure:
        if english:
            lines.append(
                "- Silence pressure: after the candidate finishes an answer, do NOT immediately move on. "
                "Leave a short pause; if they do not add anything, press with one short line "
                '(e.g. "Anything else to add?", "Is that all?") and wait for their completion '
                "before asking the next question."
            )
        else:
            lines.append(
                "- 沉默施压：候选人回答结束后，**不要立刻接话**。先留出几秒停顿，"
                "若他没有补充，再用一句简短的话逼他补全（如「还有要补充的吗？」「就这些？」），"
                "等他补完或明确说「没有了」再进入下一题。"
            )

    if scene:
        if english:
            lines.append(
                "- Scene drill: right after the candidate has answered the THIRD question, you MUST insert "
                'ONE on-site emergency question (equipment suddenly failing / customer pressure on site / '
                "takt time not met / environment interference etc.), opening with a hypothetical like "
                '"Imagine you are on site right now, …". Set its kind to "scene" (not followup/new_topic) '
                "and require an immediate step-by-step troubleshooting approach with tradeoffs. Break off "
                "any ongoing follow-up chain to ask it. Only ONE such question per interview."
            )
        else:
            lines.append(
                "- 现场突发：候选人答完第 3 题之后，**必须**插入**一道**现场突发情境题"
                "（设备突然故障 / 客户现场发难 / 节拍不够 / 环境干扰等），"
                "用「假设现在在现场，…」这类假设开头，kind 固定为 `scene`（不是 followup / new_topic），"
                "要求候选人当场给出排查步骤与取舍。即使正在连续追问，也要先打住换成这道题。"
                "整场只插这一道，不要重复。"
            )

    if resume_dive:
        if english:
            lines.append(
                "- Resume deep dive: after the introduction, question like you actually hold the resume and "
                "are reading it line by line: for each listed project drill at least two levels down — the "
                "candidate's own role (lead vs. participate), concrete technology choices and tradeoffs, "
                "quantified results, and pitfalls encountered. Do not gloss over anything the resume claims."
            )
        else:
            lines.append(
                "- 简历深挖：开场与自我介绍后，像手里真拿着候选人的简历**逐行追问**："
                "每个项目至少往下挖两层——他在其中的角色（主导还是参与）、技术选型与取舍、"
                "量化结果、踩过的坑。简历里写了但答不上来的点，追问到底，不要绕开。"
            )

    if coach:
        if english:
            lines.append(
                '- Coaching mode: for every question EXCEPT the closing, additionally output a "coach" field: '
                '{"points": "the key point of this question (one sentence)", '
                '"missed": "what the candidate just left out (one or two sentences; empty if nothing)", '
                '"better": "a better way to phrase the answer (one sentence)"}. '
                "Keep it concrete, not generic. It is shown to the candidate BEFORE the next question, "
                "so comment on the answer they just gave, then ask the new question."
            )
        else:
            lines.append(
                "- 教练模式：每一道问题（收尾除外）都在 JSON 里额外输出 `coach` 字段："
                "`{\"points\": \"这题的关键点（一句话）\", "
                "\"missed\": \"候选人刚才漏说的要点（一两句；没漏就留空）\", "
                "\"better\": \"一句更优的说法\"}`。点评要具体、不空泛；"
                "它会展示在候选人看到下一题之前——所以先点评上一题的回答，再问新问题。"
            )

    header = "Realism & feedback rules to follow:" if english else "拟真与反馈规则（必须执行）："
    return "\n".join([header, *lines])
