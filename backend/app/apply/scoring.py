"""岗位匹配打分（纯函数）。

两条打分路径**并存**，最终取较高者：

1. `analysis/resume_match.match()` 的简历×JD 诊断分（主信号）。它才是"这个岗位和我
   匹不匹配"的完整答案——按 JD 关键词的**权重**（硬性要求 / 加分项 / 日常）算覆盖率，
   还带年限惩罚与 ATS 体检。M10 当时留了线性打分并写着"等 M8 做好就替换"，
   M8 早已完成，这次把它接上。
2. 本模块的线性分（`_linear_score`）：关键词重合 + 薪资 + HR 活跃 + 非猎头。

**为什么取 max 而不是直接换成诊断分**：`match()` 在两种情况下会返回 **0 分**——
简历是空的，或这段 JD 里识别不出任何技术关键词（写得很泛、或正文没贴全）。
直接采信它会让这些岗位排到最底下，而它们未必差、只是**没有可比对的信号**。
取 max 的含义是"任一条路径认为它匹配，就不给它打低分"，不会因为主信号缺席而误伤。
"""

from __future__ import annotations

import re

from ..analysis.resume_match import MatchResult, match
from .filters import JobFacts, parse_salary

#: 从简历里抠关键词时用的分隔符（粗糙但够用，且不做分词器依赖）
_SPLITTER = re.compile(r"[\s,，、;；/|\n\r\t（）()\[\]【】:：]+")
_STOP = {
    "负责",
    "参与",
    "熟悉",
    "了解",
    "掌握",
    "经验",
    "能力",
    "以及",
    "以上",
    "相关",
    "项目",
    "工作",
    "使用",
}

#: 权重要加起来是 100，方便直接当分数看
WEIGHT_KEYWORD = 60.0
WEIGHT_SALARY = 20.0
WEIGHT_HR_ACTIVE = 10.0
WEIGHT_NOT_HEADHUNTER = 10.0


def keywords_of(text: str, *, limit: int = 40) -> list[str]:
    """把一段文本切成关键词集合（去停用词、去重、限长）。"""
    tokens: list[str] = []
    for token in _SPLITTER.split(text or ""):
        word = token.strip()
        if len(word) < 2 or len(word) > 12 or word in _STOP:
            continue
        if word not in tokens:
            tokens.append(word)
        if len(tokens) >= limit:
            break
    return tokens


def score_job(
    facts: JobFacts,
    resume_text: str,
    *,
    want_salary_min: int = 0,
    diagnostic: MatchResult | None = None,
) -> float:
    """0–100 的匹配分：取「简历×JD 诊断分」与「线性分」的较高者。

    `diagnostic` 可以由调用方传进来复用：一次抓取里 JD 各不相同，但
    `match()` 会做句子切分与关键词抽取，批量场景下调用方自己算一次更省。
    不传就在这里现算。
    """
    if diagnostic is None:
        diagnostic = match(resume_text, facts.jd_text, job_title=facts.title)

    semantic = _diagnostic_score(diagnostic)
    linear = _linear_score(facts, resume_text, want_salary_min=want_salary_min)
    return round(max(semantic, linear), 1)


def _diagnostic_score(result: MatchResult) -> float:
    """把匹配诊断的结果折算成 0–100。

    **不能直接用 `result.score`**：那个字段在"没识别出关键词"时是 0.0——
    那是"没法比"的意思，不是"完全不匹配"。所以这里显式把它当成"没有信号"，
    返回 0 让 `score_job` 完全交给线性分（而不是让 0 分参与 max 拉低结果）。
    """
    if result.grade == "—" or result.jd_keyword_count <= 0:
        return 0.0
    return min(100.0, max(0.0, float(result.score)))


def _linear_score(
    facts: JobFacts,
    resume_text: str,
    *,
    want_salary_min: int = 0,
) -> float:
    """线性分：关键词重合 + 薪资 + HR 活跃 + 非猎头（各权重加起来正好 100）。"""
    resume_keywords = {word.lower() for word in keywords_of(resume_text)}
    jd_keywords = keywords_of(facts.jd_text)[:20]

    if jd_keywords and resume_keywords:
        hits = sum(1 for word in jd_keywords if word.lower() in resume_keywords)
        keyword_ratio = hits / len(jd_keywords)
    else:
        keyword_ratio = 0.0

    score = WEIGHT_KEYWORD * keyword_ratio

    parsed = parse_salary(facts.salary_range)
    if want_salary_min <= 0:
        score += WEIGHT_SALARY * 0.5  # 没设定期望时给一半，不做评判
    elif parsed is not None and parsed[1] >= want_salary_min:
        score += WEIGHT_SALARY

    if facts.hr_active is not False:
        score += WEIGHT_HR_ACTIVE
    if not facts.is_headhunter:
        score += WEIGHT_NOT_HEADHUNTER

    return round(min(100.0, max(0.0, score)), 1)
