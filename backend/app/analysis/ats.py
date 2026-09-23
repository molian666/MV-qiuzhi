"""简历格式与 ATS 可读性体检。

**只做文本层面可证伪的检查**：
"用了表格/分栏/图标"这类问题需要拿到原文件（PDF/DOCX）才能判断，而本工具拿到的是文本，
所以不假装能看出来。这里检查的都是"只要有文本就能确定"的问题——
全角字符会让关键词匹配直接失效、没有年份时间线就无法计算年限，
这些不是风格偏好，是会真实影响结果的缺陷。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .skill_dict import FULLWIDTH

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
#: 中国大陆手机号（不匹配前后紧邻数字的情况，避免命中身份证/工号）
PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
#: 能用来算年限的年份写法
YEAR_LINE = re.compile(r"(?:19|20)\d{2}\s*[年./\-]")
#: 真正算得上"量化"的表达。中文简历的写法很杂，三种都得覆盖；
#: **对比写法放在最前面**，因为它信息最完整（"从 800 提升到 4000" 比 "QPS 从 800" 更有用）
METRIC = re.compile(
    r"(?:从|由)\s*\d+(?:\.\d+)?\s*(?:提升|降低|下降|减少|增加|增长|缩短|优化)(?:到|至|了)?\s*\d+"
    r"|(?:QPS|TPS|PV|UV|响应时间|耗时|吞吐|延迟|成功率|覆盖率|准确率)\s*(?:从|由)?\s*\d+"
    r"|\d+(?:\.\d+)?\s*(?:%|％|倍|万|亿|毫秒|ms|秒|分钟|小时|人|次|条|QPS|TPS|PV|UV)",
    re.IGNORECASE,
)
TAB_OR_WIDE_GAP = re.compile(r"\t|[^\s]{1}\s{4,}[^\s]{1}")
BULLET_MARKS = ("•", "·", "▪", "◦", "●", "-", "*")

#: 单块文字超过这个长度就算"一大坨"（HR 平均看一份简历 7 秒）
BLOATED_BLOCK_CHARS = 800
#: 低于这个字数就认为读不到有效信息（120 字大约只有一段经历）
MIN_BODY_CHARS = 120


@dataclass(slots=True)
class AtsIssue:
    severity: str  # error | warning | info
    kind: str
    message: str
    suggestion: str = ""

    @property
    def is_error(self) -> bool:
        return self.severity == "error"


def _blocks(text: str) -> list[str]:
    """把连续的非空行合并成块（简历常见写法是"经历一段多行"，不是一个空行分隔）。"""
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.strip():
            current.append(line.strip())
        elif current:
            blocks.append(" ".join(current))
            current = []
    if current:
        blocks.append(" ".join(current))
    return blocks


def check_ats(text: str, *, skill_count: int = 0) -> list[AtsIssue]:
    """返回体检结果（按严重程度排序：error 在前）。

    `skill_count` 由调用方传入（它已经算过技能数，这里不重复算）。
    """
    content = text or ""
    body = content.strip()

    if len(body) < MIN_BODY_CHARS:
        return [
            AtsIssue(
                severity="error",
                kind="too_short",
                message=f"简历正文只有 {len(body)} 字，ATS 与 HR 都读不到有效信息",
                suggestion="按「做了什么 + 怎么做 + 结果」把最近两段经历写开，每段至少 3 条要点",
            )
        ]

    issues: list[AtsIssue] = []

    has_email = bool(EMAIL.search(content))
    has_phone = bool(PHONE.search(content))
    if not has_email and not has_phone:
        issues.append(
            AtsIssue(
                severity="error",
                kind="no_contact",
                message="没找到邮箱或手机号",
                suggestion="在顶部写明手机号与邮箱——ATS 靠它建索引，HR 靠它找你",
            )
        )
    elif not has_phone:
        issues.append(
            AtsIssue(severity="warning", kind="no_phone", message="没找到手机号", suggestion="补上手机号")
        )
    elif not has_email:
        issues.append(
            AtsIssue(severity="warning", kind="no_email", message="没找到邮箱", suggestion="补上常用邮箱")
        )

    if not YEAR_LINE.search(content):
        issues.append(
            AtsIssue(
                severity="error",
                kind="no_timeline",
                message="没有可解析的年份时间线（如 2021.07-2023.06）",
                suggestion="每段经历都写上起止年月；没有时间线，ATS 无法计算你的年限，也容易被判定为经历不清",
            )
        )

    if FULLWIDTH.search(content):
        sample = FULLWIDTH.search(content)
        issues.append(
            AtsIssue(
                severity="error",
                kind="fullwidth",
                message=f"存在全角字母或数字（如「{sample.group(0) if sample else ''}」、Ｐｙｔｈｏｎ、２０２１）",
                suggestion="全部改成半角——ATS 的关键词匹配对全角字符不命中，等于这个词白写",
            )
        )

    bloated = [block for block in _blocks(content) if len(block) > BLOATED_BLOCK_CHARS]
    if bloated:
        issues.append(
            AtsIssue(
                severity="warning",
                kind="bloated_block",
                message=f"有 {len(bloated)} 段连续文字超过 {BLOATED_BLOCK_CHARS} 字（没有分行）",
                suggestion="拆成要点，一条一件事；HR 平均在一份简历上停留 7 秒",
            )
        )

    used_marks = {
        mark for mark in BULLET_MARKS if re.search(rf"^\s*{re.escape(mark)}", content, re.MULTILINE)
    }
    if len(used_marks) >= 3:
        issues.append(
            AtsIssue(
                severity="warning",
                kind="mixed_bullets",
                message=f"项目符号混用了 {len(used_marks)} 种（{' '.join(sorted(used_marks))}）",
                suggestion="统一用同一种符号，格式一致性本身就是「没做过排版」的信号",
            )
        )

    if TAB_OR_WIDE_GAP.search(content):
        issues.append(
            AtsIssue(
                severity="warning",
                kind="tab_or_gap",
                message="存在制表符或连续大段空格",
                suggestion="这通常是「从 PDF 复制出来」的痕迹，会把一段话拆成互不相关的碎片",
            )
        )

    if not METRIC.search(content):
        issues.append(
            AtsIssue(
                severity="warning",
                kind="no_metric",
                message="成果描述里没找到量化数字",
                suggestion="至少给两段经历补上可核对的数字（QPS、耗时、成本、人数、百分比）",
            )
        )

    if skill_count and skill_count < 5:
        issues.append(
            AtsIssue(
                severity="warning",
                kind="few_keywords",
                message=f"只识别到 {skill_count} 个技术关键词",
                suggestion="把技术栈写具体（用什么、怎么用、什么场景），否则 ATS 会认为你不匹配这个岗位",
            )
        )

    if skill_count and skill_count / max(1.0, len(body) / 100) > 10:
        issues.append(
            AtsIssue(
                severity="info",
                kind="keyword_stuffing",
                message="关键词密度偏高，读起来像堆砌",
                suggestion="每个关键词都应该出现在一句描述它用法的话里，而不是列成一串",
            )
        )

    order = {"error": 0, "warning": 1, "info": 2}
    return sorted(issues, key=lambda item: (order.get(item.severity, 3), item.kind))
