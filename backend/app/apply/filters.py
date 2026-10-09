"""投递过滤链：把"不想投的"挡在候选之外。

规则以**数据**形式存在 `apply_rule` 表里（kind + params），这里只做求值——
好处有两个：
1. 每条规则的行为都能单测（投递决策错了代价很大：投了外包/重复/已拉黑的公司）；
2. 规则能在 UI 上开关调参，而不用改代码。

注意：这一层只回答"要不要投这个岗位"，不负责"怎么投"（那是 adapter 的事）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from .pua import has_obfuscated

# --------------------------------------------------------------------------- #
# 岗位事实（过滤器的输入）
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class JobFacts:
    """过滤规则需要看到的岗位信息。

    单独定义而不是直接用 JobPosting：这样单测不需要构造 ORM 对象，
    也让"过滤需要哪些字段"这件事显式化。
    """

    platform: str = ""
    company: str = ""
    title: str = ""
    jd_text: str = ""
    salary_range: str = ""
    city: str = ""
    experience_req: str = ""
    degree_req: str = ""
    company_size: str = ""
    company_stage: str = ""
    industry: str = ""
    hr_name: str = ""
    hr_active: bool | None = None
    is_headhunter: bool | None = None

    # 计算字段（由调用方在装配候选时填好）
    already_applied: bool = False
    blacklisted: bool = False
    matched_score: float | None = None

    @property
    def haystack(self) -> str:
        return " ".join([self.title, self.jd_text, self.company]).lower()


@dataclass(slots=True)
class FilterRule:
    kind: str
    enabled: bool = True
    priority: int = 100
    params: dict[str, Any] = field(default_factory=dict)
    name: str = ""


@dataclass(slots=True)
class FilterVerdict:
    passed: bool
    blocked_by: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return "；".join(self.reasons) if self.reasons else "通过"


# --------------------------------------------------------------------------- #
# 薪资解析
# --------------------------------------------------------------------------- #
_UNITS: dict[str, float] = {"": 1.0, "k": 1000.0, "千": 1000.0, "万": 10000.0}
#: 数字 + 紧随其后的单位（"15k"、"1.2万"、"8000"）——**每个数字各带各的单位**，
#: 因为"8千-1.2万"这种写法里上下界的单位是不一样的
_NUMBER_UNIT = re.compile(r"(\d+(?:\.\d+)?)([k千万]?)")
_SPLIT_RANGE = re.compile(r"[-~到至]")
#: "·14薪"、"*15薪" 这类付薪月数，不是薪资数字
_PAY_MONTHS = re.compile(r"[·*x×]?\s*\d+(?:\.\d+)?\s*薪")
#: "面议"这类无法量化的一律返回 None（不做猜测）
_NEGOTIABLE = ("面议", "面谈", "薪资面议", "待遇面议")


def parse_salary(text: str) -> tuple[float, float] | None:
    """把薪资文本解析成月薪区间（元）。

    支持：`15-25K`、`15k-25k`、`15-25K·14薪`、`20-40万/年`、`8千-1.2万`、`15000-25000`、`15-25`。
    "面议"或解析不出时返回 None —— **宁可让规则跳过这一项，也不要猜错薪资**。

    平台用自定义字体混淆数字时（Boss 的薪资就是），文本里是一串私用区码点：
    这里**显式**返回 None，而不是让正则碰巧匹配不上——两者的区别在于前者是"我们知道
    这里有问题"，后者是"我们不知道"。**注意这意味着薪资规则对这类岗位完全不生效**，
    所以调用方必须把这件事告诉用户（见 `runner.search_and_queue` 的 notes），
    不能让用户以为"薪资区间"规则在起作用。
    """
    raw = (text or "").strip()
    if not raw or has_obfuscated(raw) or any(word in raw for word in _NEGOTIABLE):
        return None

    lowered = raw.lower().replace(" ", "")
    per_year = any(mark in lowered for mark in ("/年", "年薪", "年包"))

    cleaned = lowered.replace("/年", "").replace("年薪", "").replace("年包", "")
    cleaned = _PAY_MONTHS.sub("", cleaned)

    parts = [part for part in _SPLIT_RANGE.split(cleaned, maxsplit=1) if part]
    if not parts:
        return None

    def _split_number(part: str) -> tuple[float, str] | None:
        match = _NUMBER_UNIT.match(part)
        if not match:
            return None
        return float(match.group(1)), match.group(2)

    first = _split_number(parts[0])
    if first is None:
        return None
    # 只有一个数字（"15K"）时上下界相同
    second = _split_number(parts[1]) if len(parts) > 1 and parts[1] else None

    low_number, low_unit = first
    high_number, high_unit = second if second is not None else first

    # 一边写了单位另一边省略时（"15-25K"），沿用另一边的单位
    if low_unit and not high_unit:
        high_unit = low_unit
    elif high_unit and not low_unit:
        low_unit = high_unit

    low = low_number * _UNITS[low_unit]
    high = high_number * _UNITS[high_unit]

    # "15-25" 这种整体省略单位的写法：数字很小就默认按 K（元的话不至于这么小）
    if not low_unit and low < 200 and high < 200:
        low *= 1000
        high *= 1000

    if per_year:
        low /= 12
        high /= 12

    if low > high:
        low, high = high, low
    return round(low, 2), round(high, 2)


# --------------------------------------------------------------------------- #
# 规则求值
# --------------------------------------------------------------------------- #
def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None or value == "":
        return []
    return [str(value).strip()]


def _matches_any(haystack: str, words: Sequence[str]) -> list[str]:
    lowered = haystack.lower()
    return [word for word in words if word and word.lower() in lowered]


def _salary_conflict(facts: JobFacts, params: dict[str, Any]) -> str | None:
    """岗位薪资与期望区间**完全没有交集**时才拦。

    岗位没写薪资（面议）时不拦——很多好岗位不写，拦掉会漏掉机会。
    """
    parsed = parse_salary(facts.salary_range)
    if parsed is None:
        return None

    want_low = float(params.get("min_salary") or 0)
    want_high = float(params.get("max_salary") or 10**9)
    job_low, job_high = parsed
    if job_high < want_low or job_low > want_high:
        return f"薪资 {facts.salary_range} 与期望 {int(want_low)}-{int(want_high)} 无交集"
    return None


def _evaluate_rule(facts: JobFacts, rule: FilterRule) -> str | None:
    """返回"命中原因"（说明为什么拦），没命中返回 None。"""
    params = rule.params or {}

    if rule.kind == "blacklist":
        if facts.blacklisted:
            return f"公司在黑名单里（{facts.company}）"
        return None

    if rule.kind == "dedup":
        if facts.already_applied:
            return "这个岗位已经投过了"
        return None

    if rule.kind == "headhunter":
        if params.get("deny", True) and facts.is_headhunter:
            return "猎头岗位"
        return None

    if rule.kind == "hr_active":
        if params.get("require_active", True) and facts.hr_active is False:
            return "HR 长期不活跃"
        return None

    if rule.kind == "salary":
        return _salary_conflict(facts, params)

    if rule.kind == "experience":
        allowed = _as_list(params.get("allow"))
        if allowed and facts.experience_req and facts.experience_req not in allowed:
            return f"经验要求 {facts.experience_req} 不在允许范围"
        return None

    if rule.kind == "degree":
        allowed = _as_list(params.get("allow"))
        if allowed and facts.degree_req and facts.degree_req not in allowed:
            return f"学历要求 {facts.degree_req} 不在允许范围"
        return None

    if rule.kind == "company_size":
        allowed = _as_list(params.get("allow"))
        if allowed and facts.company_size and facts.company_size not in allowed:
            return f"公司规模 {facts.company_size} 不在允许范围"
        return None

    if rule.kind == "company_stage":
        allowed = _as_list(params.get("allow"))
        if allowed and facts.company_stage and facts.company_stage not in allowed:
            return f"融资阶段 {facts.company_stage} 不在允许范围"
        return None

    if rule.kind == "industry":
        allowed = _as_list(params.get("allow"))
        if allowed and facts.industry and facts.industry not in allowed:
            return f"行业 {facts.industry} 不在允许范围"
        return None

    if rule.kind == "keyword":
        denied = _as_list(params.get("deny"))
        hits = _matches_any(facts.haystack, denied)
        if hits:
            return f"命中屏蔽词：{'、'.join(hits)}"

        required = _as_list(params.get("require"))
        if required:
            # 要求词是"加分项"而不是硬门槛：全都缺席才拦（避免过拟合到几个词）
            if not _matches_any(facts.haystack, required):
                return f"没有命中任何期望关键词：{'、'.join(required[:3])}"
        return None

    return None


def evaluate(facts: JobFacts, rules: Sequence[FilterRule]) -> FilterVerdict:
    """按优先级依次求值。**任何一条命中就拒绝**（过滤链是"一票否决"）。

    顺序按 priority 升序（数字小先判断），这样"黑名单"这类硬规则可以先短路，
    UI 上也能解释"是被哪条规则拦的"。
    """
    verdict = FilterVerdict(passed=True)
    for rule in sorted(rules, key=lambda item: item.priority):
        if not rule.enabled:
            continue
        reason = _evaluate_rule(facts, rule)
        if reason is None:
            continue
        verdict.passed = False
        verdict.blocked_by.append(rule.name or rule.kind)
        verdict.reasons.append(reason)
    return verdict
