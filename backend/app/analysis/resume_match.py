"""简历 × JD 匹配诊断（纯函数）。

设计上的三个取舍：

1. **加权而不是数关键词**：JD 里"必须精通 Python"和"了解 Kafka 优先"不是一个量级。
   所以按句子判断语气给权重（硬性 1.0 / 日常职责 0.7 / 加分项 0.5）。
2. **年限要合并重叠区间**：简历里"2021.03-2023.06 在 A 公司"和"2022.01-2022.12 兼职项目"
   是重叠的，直接相加会把 3 年经历算成 4 年多。合并后才是真实年限。
3. **改写建议不编造**：缺失关键词只给"句式模板 + 你必须自己填的数字"，
   绝不生成"我在某公司用 Kafka 把吞吐提升 300%"这种听起来漂亮但可能不存在的经历。
   求职材料里编造事实的代价远高于少一条建议。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from ..clock import utcnow
from .ats import METRIC, AtsIssue, check_ats
from .skill_dict import find_skills, group_label, skill_group

# --------------------------------------------------------------------------- #
# 权重
# --------------------------------------------------------------------------- #
WEIGHT_REQUIREMENT = 1.0
WEIGHT_DAILY = 0.7
WEIGHT_BONUS = 0.5

REQUIREMENT_MARKERS = ("必须", "要求", "至少", "熟练掌握", "精通", "必备", "需要具备", "硬性", "大量")
BONUS_MARKERS = ("优先", "加分", "更佳", "最好", "锦上添花", "nice to have", "plus", "preferred")

#: 低于这个年限就算不满足（给半年宽容：3 年要求 + 2.6 年实际通常也能过）
YEAR_TOLERANCE = 0.5


@dataclass(slots=True)
class JdKeyword:
    word: str
    weight: float = WEIGHT_DAILY
    group: str = "other"
    mentions: int = 1
    sentence: str = ""

    @property
    def is_required(self) -> bool:
        return self.weight >= WEIGHT_REQUIREMENT

    @property
    def is_bonus(self) -> bool:
        return self.weight <= WEIGHT_BONUS


@dataclass(slots=True)
class ResumeHit:
    word: str
    evidence: str = ""
    mentions: int = 1
    has_metric: bool = False


@dataclass(slots=True)
class MatchSuggestion:
    kind: str  # add_keyword | fix_ats | fix_years
    target: str
    why: str
    where: str = ""
    draft: str = ""
    weight: float = 0.0


@dataclass(slots=True)
class MatchResult:
    score: float = 0.0
    grade: str = ""
    coverage: float = 0.0
    jd_keyword_count: int = 0
    covered: list[JdKeyword] = field(default_factory=list)
    missing: list[JdKeyword] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    missing_groups: list[str] = field(default_factory=list)
    years_required: int | None = None
    years_actual: float | None = None
    years_ok: bool | None = None
    ats: list[AtsIssue] = field(default_factory=list)
    suggestions: list[MatchSuggestion] = field(default_factory=list)
    summary: str = ""


# --------------------------------------------------------------------------- #
# 文本切分
# --------------------------------------------------------------------------- #
_SENTENCE_SPLIT = re.compile(r"[。；！？\n\r]+|(?<=[a-zA-Z0-9])\.\s")


def split_sentences(text: str) -> list[str]:
    parts = [part.strip() for part in _SENTENCE_SPLIT.split(text or "")]
    return [part for part in parts if part]


def _sentence_weight(sentence: str) -> float:
    lowered = sentence.lower()
    if any(marker in sentence for marker in REQUIREMENT_MARKERS) or "required" in lowered or "must " in lowered:
        return WEIGHT_REQUIREMENT
    if any(marker in sentence for marker in BONUS_MARKERS) or "preferred" in lowered:
        return WEIGHT_BONUS
    return WEIGHT_DAILY


# --------------------------------------------------------------------------- #
# 抽取
# --------------------------------------------------------------------------- #
def extract_jd_keywords(jd_text: str) -> list[JdKeyword]:
    """从 JD 里抽出技能关键词并按语气加权。

    同一个词在多句里出现时取**最高权重**（"熟悉 Redis" 与 "必须精通 Redis" 同时出现 → 按硬性算）。
    """
    found: dict[str, JdKeyword] = {}

    for sentence in split_sentences(jd_text):
        hits = find_skills(sentence)
        if not hits:
            continue
        weight = _sentence_weight(sentence)
        for word in hits:
            if word in found:
                found[word].mentions += 1
                found[word].weight = max(found[word].weight, weight)
            else:
                found[word] = JdKeyword(
                    word=word,
                    weight=weight,
                    group=skill_group(word),
                    sentence=sentence[:140],
                )

    return sorted(found.values(), key=lambda item: (-item.weight, item.word))


def _evidence_for(text: str, word: str) -> str:
    for sentence in split_sentences(text):
        if word in find_skills(sentence):
            return sentence.strip()[:200]
    return ""


def extract_resume_hits(resume_text: str) -> dict[str, ResumeHit]:
    hits = find_skills(resume_text)
    result: dict[str, ResumeHit] = {}
    for word, occurrences in hits.items():
        evidence = _evidence_for(resume_text, word)
        result[word] = ResumeHit(
            word=word,
            evidence=evidence,
            mentions=len(occurrences),
            has_metric=bool(METRIC.search(evidence)),
        )
    return result


# --------------------------------------------------------------------------- #
# 年限
# --------------------------------------------------------------------------- #
_CHINESE_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_NUM = r"(?:\d{1,2}|[一二两三四五六七八九十])"

_RANGE_YEARS = re.compile(rf"({_NUM})\s*[-~到至]\s*({_NUM})\s*年")
_MIN_YEARS = re.compile(rf"({_NUM})\s*年(?:以上|及以上|\+|起步|经验)")
#: "开发经验 6 年" 这种顺序
_EXP_YEARS_AFTER = re.compile(rf"(?:工作|从业|开发|测试|运维|相关)经验\s*({_NUM})\s*年")
#: "6 年开发经验" 这种顺序（更常见的写法）
_EXP_YEARS_BEFORE = re.compile(rf"({_NUM})\s*年(?:[^。；\n\d]{{0,8}})?经验")

_CLOSED_SPAN = re.compile(
    r"((?:19|20)\d{2})\s*[年./\-]?\s*(\d{1,2})?\s*月?\s*[-~至到]\s*((?:19|20)\d{2})\s*[年./\-]?\s*(\d{1,2})?",
)
_OPEN_SPAN = re.compile(
    r"((?:19|20)\d{2})\s*[年./\-]?\s*(\d{1,2})?\s*月?\s*[-~至到]\s*(至今|现在|目前|present|now)",
    re.IGNORECASE,
)


def _to_number(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    return _CHINESE_NUM.get(token)


def required_years(jd_text: str) -> int | None:
    """JD 要求的最低年限（"3-5年" 取 3，"5年以上" 取 5）。"""
    text = jd_text or ""

    match = _RANGE_YEARS.search(text)
    if match:
        low = _to_number(match.group(1))
        high = _to_number(match.group(2))
        candidates = [value for value in (low, high) if value is not None]
        if candidates:
            return min(candidates)

    for pattern in (_MIN_YEARS, _EXP_YEARS_AFTER, _EXP_YEARS_BEFORE):
        match = pattern.search(text)
        if match:
            value = _to_number(match.group(1))
            if value is not None:
                return value

    return None


def _month_index(year: int, month: int) -> int:
    return year * 12 + max(1, min(12, month or 1)) - 1


def merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """合并重叠/相邻的时间区间（并行经历不能重复计时长）。"""
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def resume_years(resume_text: str, *, today: date | None = None) -> float | None:
    """从简历的日期区间推算工作年限（合并重叠区间；推不出来时返回 None）。"""
    text = resume_text or ""
    anchor = today or utcnow().date()
    anchor_index = _month_index(anchor.year, anchor.month)

    spans: list[tuple[int, int]] = []
    for match in _CLOSED_SPAN.finditer(text):
        start = _month_index(int(match.group(1)), int(match.group(2) or 1))
        end = _month_index(int(match.group(3)), int(match.group(4) or 12))
        if end < start:
            end = start
        spans.append((start, end))

    for match in _OPEN_SPAN.finditer(text):
        start = _month_index(int(match.group(1)), int(match.group(2) or 1))
        spans.append((start, anchor_index))

    if spans:
        months = sum(end - start + 1 for start, end in merge_spans(spans))
        return round(months / 12, 1)

    # 没有日期区间时退一步看正文里的"X 年经验"
    for pattern in (_EXP_YEARS_AFTER, _EXP_YEARS_BEFORE):
        match = pattern.search(text)
        if match:
            value = _to_number(match.group(1))
            if value is not None:
                return float(value)

    return None


# --------------------------------------------------------------------------- #
# 建议
# --------------------------------------------------------------------------- #
def _anchor_for(keyword: JdKeyword, hits: dict[str, ResumeHit]) -> str:
    """把建议挂到简历里最相关的那段经历上（同类技能优先，其次证据最长的一条）。"""
    same_group = [hit for hit in hits.values() if skill_group(hit.word) == keyword.group and hit.evidence]
    if same_group:
        return f"「{same_group[0].evidence[:60]}」这段经历里"

    with_evidence = [hit for hit in hits.values() if hit.evidence]
    if with_evidence:
        longest = max(with_evidence, key=lambda hit: len(hit.evidence))
        return f"「{longest.evidence[:60]}」这段经历里"

    return "最近一段工作经历里"


def _draft_sentence(word: str, where: str) -> str:
    """不编造事实的句式模板：数字位置全部留空并明确标注。"""
    return (
        f"在{where}补一句你与 {word} 的真实做法，句式：\n"
        f"「负责 …（写你真实做过的事），使用 {word} 完成 …，把 … 从 A 改善到 B」\n"
        f"（省略号与 A/B 必须换成你自己的真实情况——本工具不会替你编经历）"
    )


def build_suggestions(
    *,
    missing: list[JdKeyword],
    hits: dict[str, ResumeHit],
    ats: list[AtsIssue],
    years_required: int | None,
    years_actual: float | None,
    years_ok: bool | None,
    limit: int = 6,
) -> list[MatchSuggestion]:
    suggestions: list[MatchSuggestion] = []

    for keyword in missing[:limit]:
        if keyword.is_required:
            why = f"JD 把「{keyword.word}」列为硬性要求"
        elif keyword.is_bonus:
            why = f"JD 把「{keyword.word}」列为加分项"
        else:
            why = f"JD 的职责描述里提到了「{keyword.word}」"
        where = _anchor_for(keyword, hits)
        suggestions.append(
            MatchSuggestion(
                kind="add_keyword",
                target=keyword.word,
                why=why,
                where=where,
                draft=_draft_sentence(keyword.word, where),
                weight=keyword.weight,
            )
        )

    if years_ok is False and years_required is not None:
        actual = f"{years_actual} 年" if years_actual is not None else "无法从简历推算出"
        suggestions.append(
            MatchSuggestion(
                kind="fix_years",
                target=f"{years_required} 年",
                why=f"JD 要求 {years_required} 年经验，简历推算为 {actual}",
                where="顶部的工作年限与每段经历的起止年月",
                draft=(
                    "两种处理，选一种：\n"
                    "1）如果确实差得不远，把并行/兼职/实习经历也写上起止年月（合并后年限会更准）；\n"
                    "2）如果确实差得多，就别硬凑年限——把最近项目里最能打的成果写足，"
                    "并在沟通里用「同等工作量/复杂度」来对齐。"
                ),
            )
        )

    for issue in ats:
        if issue.severity == "error":
            suggestions.append(
                MatchSuggestion(
                    kind="fix_ats",
                    target=issue.kind,
                    why=issue.message,
                    draft=issue.suggestion,
                )
            )

    return suggestions


def _grade(score: float) -> str:
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "C"
    return "D"


def _summary(result: MatchResult) -> str:
    coverage = f"{round(result.coverage * 100)}%"
    head = f"关键词覆盖 {coverage}（{len(result.covered)}/{result.jd_keyword_count}）"

    if result.years_ok is False and result.years_required is not None:
        head += f"；年限要求 {result.years_required} 年，简历推算 {result.years_actual} 年，这条通常是先被筛掉的"

    top_missing = [keyword.word for keyword in result.missing if keyword.is_required][:3]
    if top_missing:
        head += f"；最该补的硬性要求：{'、'.join(top_missing)}"
    elif result.missing:
        head += f"；还差：{'、'.join(keyword.word for keyword in result.missing[:3])}"
    else:
        head += "；JD 里提到的技能你都覆盖到了"

    errors = [issue for issue in result.ats if issue.is_error]
    if errors:
        head += f"；另有 {len(errors)} 个会直接影响筛选的格式问题（见体检清单）"

    return head


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def match(
    resume_text: str,
    jd_text: str,
    *,
    job_title: str = "",
    today: date | None = None,
) -> MatchResult:
    """做一次完整的匹配诊断。"""
    result = MatchResult()

    jd_keywords = extract_jd_keywords(jd_text)
    result.jd_keyword_count = len(jd_keywords)

    if not (resume_text or "").strip():
        result.grade = "D"
        result.summary = "简历是空的——先在「简历」里贴一份内容，诊断才有意义。"
        return result

    if not jd_keywords:
        result.grade = "—"
        hits = extract_resume_hits(resume_text)
        result.ats = check_ats(resume_text, skill_count=len(hits))
        result.summary = (
            "这段 JD 里没识别出可比较的技术关键词。"
            f"（{job_title or '该岗位'}可能写得比较泛，或者 JD 正文没贴全）"
        )
        return result

    hits = extract_resume_hits(resume_text)

    result.covered = [keyword for keyword in jd_keywords if keyword.word in hits]
    result.missing = [keyword for keyword in jd_keywords if keyword.word not in hits]

    total_weight = sum(keyword.weight for keyword in jd_keywords)
    got_weight = sum(keyword.weight for keyword in result.covered)
    result.coverage = round(got_weight / total_weight, 4) if total_weight else 0.0

    matched_words = {keyword.word for keyword in jd_keywords}
    extras = [word for word in hits if word not in matched_words]
    result.extra = sorted(extras, key=lambda word: (skill_group(word), word))[:8]

    missing_groups: list[str] = []
    for keyword in result.missing:
        label = group_label(keyword.group)
        if label not in missing_groups and keyword.group != "soft":
            missing_groups.append(label)
    result.missing_groups = missing_groups[:4]

    result.years_required = required_years(jd_text)
    result.years_actual = resume_years(resume_text, today=today)
    if result.years_required is not None and result.years_actual is not None:
        result.years_ok = result.years_actual + YEAR_TOLERANCE >= result.years_required

    score = result.coverage * 100
    if result.years_ok is False:
        score *= 0.8  # 年限不满足通常直接卡在 HR 初筛，权重给足
    result.score = round(min(100.0, max(0.0, score)), 1)
    result.grade = _grade(result.score)

    result.ats = check_ats(resume_text, skill_count=len(hits))
    result.suggestions = build_suggestions(
        missing=result.missing,
        hits=hits,
        ats=result.ats,
        years_required=result.years_required,
        years_actual=result.years_actual,
        years_ok=result.years_ok,
    )
    result.summary = _summary(result)
    return result
