"""Offer 对比矩阵与敏感性分析（纯函数）。

**为什么必须有敏感性分析**：加权总分最大的问题是"权重是你拍的"——
换个权重排名就变，而一个孤零零的总分不会告诉你这件事。
所以这里不只给总分，还会明确说：
「把「薪酬」的权重提到 62% 以上，A 公司就会超过 B」。

这句话比"B 总分 7.8 比 A 的 7.5 高"有用得多：它告诉你这个结论有多稳。

**薪酬怎么算**：不靠感觉打分，而是把薪资结构拆开算首年总包，再按所有 offer 里的
最高值归一到 0–10。其余维度（成长、通勤、团队…）才由你自己打分——那些没人能替你算。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

#: 维度 key → 中文标签（顺序即前端展示顺序）
DIMENSIONS: dict[str, str] = {
    "compensation": "薪酬",
    "growth": "成长空间",
    "stability": "稳定性",
    "commute": "通勤",
    "team": "团队氛围",
    "tech_stack": "技术栈",
    "industry": "行业前景",
    "wlb": "工作生活平衡",
    "brand": "公司品牌",
    "location": "城市与落户",
}

#: 默认权重（加起来是 1）。这是"刚毕业三年、想往上走"的常见取向，
#: 但**只是个起点**——用户改完权重看到排名翻转，才是这一页真正的价值。
DEFAULT_WEIGHTS: dict[str, float] = {
    "compensation": 0.30,
    "growth": 0.20,
    "stability": 0.15,
    "commute": 0.10,
    "team": 0.08,
    "tech_stack": 0.07,
    "industry": 0.04,
    "wlb": 0.03,
    "brand": 0.02,
    "location": 0.01,
}

#: 扫描权重时最多扫到多少（再高就是"只看这一项"了）
MAX_SCAN_WEIGHT = 0.8


@dataclass(slots=True)
class OfferInput:
    id: int
    company: str
    title: str = ""
    #: 首年总包（元）——由薪资结构算出，不是手填
    total_comp: float = 0.0
    #: 各维度 0–10 分（compensation 会被自动覆盖）
    factors: dict[str, float] = field(default_factory=dict)
    status: str = "pending"


@dataclass(slots=True)
class ScoreRow:
    offer_id: int
    company: str
    title: str = ""
    total: float = 0.0
    rank: int = 0
    #: 维度 → 加权贡献（能看出分数是被哪一项抬起来的）
    breakdown: dict[str, float] = field(default_factory=dict)
    #: 维度 → 原始分（用户打的分 / 自动算的薪酬分）
    stars: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class FlipPoint:
    """某个维度的权重变到多少时，两个 offer 的排名会翻转。"""

    factor: str
    weight: float
    winner: int
    winner_company: str
    loser: int
    loser_company: str
    message: str = ""


@dataclass(slots=True)
class MatrixResult:
    rows: list[ScoreRow] = field(default_factory=list)
    flips: list[FlipPoint] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    disclaimer: str = ""


# --------------------------------------------------------------------------- #
# 权重
# --------------------------------------------------------------------------- #
def normalize_weights(weights: Mapping[str, Any] | None = None) -> dict[str, float]:
    """归一化权重：丢掉未知维度、负数按 0 处理、和为 1。"""
    source = dict(weights) if isinstance(weights, Mapping) else {}
    cleaned = {
        key: max(0.0, float(source.get(key, DEFAULT_WEIGHTS.get(key, 0.0)) or 0.0))
        for key in DIMENSIONS
    }
    total = sum(cleaned.values())
    if total <= 0:
        # 全填 0 等于没填 → 回到默认，而不是除以 0
        cleaned = dict(DEFAULT_WEIGHTS)
        total = sum(cleaned.values())
    return {key: round(value / total, 6) for key, value in cleaned.items()}


def with_weight(weights: Mapping[str, float], factor: str, value: float) -> dict[str, float]:
    """把某个维度的权重设成 value，其余维度按原比例缩放到剩下的份额。

    这样扫描出来的曲线才是"只动这一项"的干净结果。

    注意其余维度分摊的是 **1 - value**：一开始写成按 value 缩放，
    结果权重和变成 2×value，整个敏感性分析全错（测试里的"和必须为 1"抓到了）。
    """
    base = normalize_weights(weights)
    target = max(0.0, min(1.0, float(value)))
    rest = max(0.0, 1.0 - target)

    others = {key: weight for key, weight in base.items() if key != factor}
    if not others:
        return {factor: 1.0}

    total_other = sum(others.values())
    if total_other <= 0:
        share = rest / len(others)
        scaled = {key: share for key in others}
    else:
        scaled = {key: weight / total_other * rest for key, weight in others.items()}

    scaled[factor] = target
    return scaled


# --------------------------------------------------------------------------- #
# 打分
# --------------------------------------------------------------------------- #
def compensation_scores(offers: Sequence[OfferInput]) -> dict[int, float]:
    """年包 → 0–10 分（按同批 offer 里的最高值归一）。

    相对归一而不是绝对阈值：30 万在一线城市是及格线，在三线城市是不错的数——
    绝对值打分只会把"城市"这个因素偷偷混进"薪酬"维度。
    """
    peak = max((float(offer.total_comp or 0.0) for offer in offers), default=0.0)
    if peak <= 0:
        return {offer.id: 0.0 for offer in offers}
    return {offer.id: round(min(10.0, float(offer.total_comp or 0.0) / peak * 10), 2) for offer in offers}


def _stars_for(offer: OfferInput, comp_score: float) -> dict[str, float]:
    stars: dict[str, float] = {}
    for key in DIMENSIONS:
        if key == "compensation":
            stars[key] = comp_score
            continue
        raw = (offer.factors or {}).get(key, 0)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = 0.0
        stars[key] = round(max(0.0, min(10.0, value)), 2)
    return stars


def score_offers(offers: Sequence[OfferInput], weights: Mapping[str, Any] | None = None) -> list[ScoreRow]:
    """按权重算总分并排名（同分时按年包高者在前，避免排名随机）。"""
    settings = normalize_weights(weights)
    comp = compensation_scores(offers)

    rows: list[ScoreRow] = []
    for offer in offers:
        stars = _stars_for(offer, comp.get(offer.id, 0.0))
        breakdown = {key: round(stars[key] * settings[key], 4) for key in DIMENSIONS}
        rows.append(
            ScoreRow(
                offer_id=offer.id,
                company=offer.company,
                title=offer.title,
                total=round(sum(breakdown.values()), 3),
                breakdown=breakdown,
                stars=stars,
            )
        )

    rows.sort(key=lambda row: (-row.total, -_comp_of(offers, row.offer_id)))
    for index, row in enumerate(rows, start=1):
        row.rank = index
    return rows


def _comp_of(offers: Sequence[OfferInput], offer_id: int) -> float:
    for offer in offers:
        if offer.id == offer_id:
            return float(offer.total_comp or 0.0)
    return 0.0


# --------------------------------------------------------------------------- #
# 敏感性分析
# --------------------------------------------------------------------------- #
def scan_ranking(
    offers: Sequence[OfferInput],
    weights: Mapping[str, Any],
    *,
    factor: str,
    steps: int = 33,
    hi: float = MAX_SCAN_WEIGHT,
) -> list[tuple[float, int | None]]:
    """把某个维度的权重从 0 扫到 hi，返回 [(权重, 第一名 offer_id)]。"""
    if factor not in DIMENSIONS or len(offers) < 2:
        return []

    base = normalize_weights(weights)
    points: list[tuple[float, int | None]] = []
    for step in range(steps + 1):
        value = hi * step / steps
        rows = score_offers(offers, with_weight(base, factor, value))
        points.append((round(value, 4), rows[0].offer_id if rows else None))
    return points


def find_flips(
    offers: Sequence[OfferInput],
    weights: Mapping[str, Any] | None = None,
    *,
    factor: str | None = None,
) -> list[FlipPoint]:
    """找出所有"权重变一点，第一名就换人"的临界点。

    用扫描 + 取中点，而不是解方程：这里的打分是带并列与离散取整的，
    解析解反而容易给出一个实际不成立的值。
    """
    if len(offers) < 2:
        return []

    base = normalize_weights(weights)
    factors = [factor] if factor else list(DIMENSIONS)
    by_id = {offer.id: offer for offer in offers}

    flips: list[FlipPoint] = []
    for key in factors:
        points = scan_ranking(offers, base, factor=key)
        if not points:
            continue

        previous_weight, previous_top = points[0]
        for weight, top in points[1:]:
            if top is None or previous_top is None or top == previous_top:
                previous_weight, previous_top = weight, top
                continue

            critical = round((previous_weight + weight) / 2, 3)
            winner = by_id.get(top)
            loser = by_id.get(previous_top)
            if winner is None or loser is None:
                previous_weight, previous_top = weight, top
                continue

            label = DIMENSIONS.get(key, key)
            flips.append(
                FlipPoint(
                    factor=key,
                    weight=critical,
                    winner=top,
                    winner_company=winner.company,
                    loser=previous_top,
                    loser_company=loser.company,
                    message=(
                        f"把「{label}」的权重提到 {round(critical * 100)}% 以上，"
                        f"{winner.company} 就会超过 {loser.company}"
                    ),
                )
            )
            previous_weight, previous_top = weight, top

    # 同一维度只留最关键的那个（扫出来的多个点容易淹没重点）
    unique: dict[tuple[str, int, int], FlipPoint] = {}
    for flip in flips:
        unique.setdefault((flip.factor, flip.winner, flip.loser), flip)
    return sorted(unique.values(), key=lambda item: item.weight)


def build_matrix(
    offers: Sequence[OfferInput],
    weights: Mapping[str, Any] | None = None,
) -> MatrixResult:
    """完整的一次矩阵计算（打分 + 排名 + 翻转点 + 提醒）。"""
    settings = normalize_weights(weights)
    result = MatrixResult(rows=score_offers(offers, settings), weights=settings)
    result.flips = find_flips(offers, settings)

    notes: list[str] = []
    missing_comp = [offer.company for offer in offers if float(offer.total_comp or 0) <= 0]
    if missing_comp:
        notes.append(
            f"{'、'.join(missing_comp)} 没填薪资结构，薪酬维度按 0 分算——这会明显压低它的总分。"
        )

    if len(offers) < 2:
        notes.append("只有一个 offer 时没有可比性；先把其它 offer 也录进来。")
    elif not result.flips:
        notes.append("在当前维度范围内怎么调权重，排名都不会翻转——这个结论比较稳。")

    no_delta = [
        offer.company
        for offer in offers
        if not any(float(value or 0) > 0 for key, value in (offer.factors or {}).items() if key != "compensation")
    ]
    if no_delta:
        notes.append(f"{'、'.join(no_delta)} 只填了薪资、没打其它维度的分，总分基本只剩薪酬在起作用。")

    result.notes = notes
    result.disclaimer = (
        "总分只是把你自己给的权重与打分乘起来——它不改变事实，只是把你的取向显式化。"
        "看到翻转点之后应该问自己的是：我到底更在意哪一项？"
    )
    return result


# --------------------------------------------------------------------------- #
# 展示
# --------------------------------------------------------------------------- #
def format_comp(value: float) -> str:
    """年包可读化（385000 → 38.5 万）。"""
    amount = float(value or 0)
    if amount <= 0:
        return "—"
    if amount >= 10_000:
        return f"{round(amount / 10_000, 1)} 万"
    return f"{round(amount)} 元"


def annual_comp(
    *,
    monthly_salary: float = 0,
    months_per_year: float = 12,
    annual_bonus: float = 0,
    equity_per_year: float = 0,
    signing_bonus: float = 0,
) -> float:
    """首年总包：月薪 × 月数 + 年终奖 + 股票 + 签字费。

    签字费只算首年——把它算进"每年"会高估长期收入，这是 offer 对比里最常见的自欺。
    """
    base = max(0.0, float(monthly_salary or 0)) * max(0.0, float(months_per_year or 0))
    return round(
        base
        + max(0.0, float(annual_bonus or 0))
        + max(0.0, float(equity_per_year or 0))
        + max(0.0, float(signing_bonus or 0)),
        2,
    )


def dimension_options() -> list[dict[str, Any]]:
    """给前端渲染权重滑块用。"""
    return [
        {"key": key, "label": label, "default": DEFAULT_WEIGHTS.get(key, 0.0)}
        for key, label in DIMENSIONS.items()
    ]


def offers_from_rows(rows: Iterable[Any]) -> list[OfferInput]:
    """把 ORM 行转成打分输入（service 层用）。"""
    converted: list[OfferInput] = []
    for row in rows:
        total = annual_comp(
            monthly_salary=float(getattr(row, "monthly_salary", 0) or 0),
            months_per_year=float(getattr(row, "months_per_year", 12) or 12),
            annual_bonus=float(getattr(row, "annual_bonus", 0) or 0),
            equity_per_year=float(getattr(row, "equity_per_year", 0) or 0),
            signing_bonus=float(getattr(row, "signing_bonus", 0) or 0),
        )
        converted.append(
            OfferInput(
                id=int(getattr(row, "id", 0) or 0),
                company=str(getattr(row, "company", "") or ""),
                title=str(getattr(row, "title", "") or ""),
                total_comp=total,
                factors=dict(getattr(row, "factors", None) or {}),
                status=str(getattr(row, "status", "pending") or "pending"),
            )
        )
    return converted
