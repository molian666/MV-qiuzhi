"""岗位匹配打分（纯函数）。

M10 先用一个**够用且可解释**的线性打分；M8 的「简历 × JD 匹配诊断」做好之后
会替换掉它（那时会有缺失关键词、ATS 体检这些更细的输出）。
现在不提前实现那套的原因很简单：不想在一个会被替换的地方过度投入。
"""

from __future__ import annotations

import re

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
) -> float:
    """0–100 的匹配分。

    - 关键词重合占 60：JD 里的词在简历里出现得越多越高；
    - 薪资达标占 20：岗位薪资下限达到期望才给分；
    - HR 活跃占 10：不活跃的 HR 投了也白投；
    - 非猎头占 10。
    """
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
