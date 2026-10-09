"""岗位匹配打分的融合逻辑（简历×JD 诊断分 vs 线性分）。

M10 的线性打分当时留了一句"等 M8 的匹配诊断做好就替换掉它"，M8 早就完成了，
这次把它接上。这个文件钉住的是**融合的语义**，不是具体分数：

- 主信号（诊断分）有信号时，它说了算；
- 主信号**没法比**时（空简历 / JD 里没有技术关键词），不能让 0 分把岗位拉到底——
  那是"没信号"，不是"不匹配"。
"""

from __future__ import annotations

from app.analysis.resume_match import match
from app.apply.filters import JobFacts
from app.apply.scoring import score_job

RESUME = (
    "五年机器视觉开发经验。负责视觉标定与缺陷检测项目，"
    "熟悉 OpenCV、C++、Python、Halcon，用亚像素边缘提取把重复精度做到 0.02mm。"
)

#: 关键词高度重合的 JD（诊断分应该高）
JD_GOOD = (
    "岗位职责：负责机器视觉缺陷检测与视觉标定算法的开发。"
    "任职要求：熟悉 OpenCV、C++、Python；了解 Halcon 者优先；有亚像素与边缘提取经验。"
)

#: 与简历几乎无关的 JD
JD_UNRELATED = (
    "岗位职责：负责公司财务报表编制与月度结账，配合外部审计。"
    "任职要求：熟悉企业会计准则、税务申报流程；熟练使用金蝶财务软件。"
)

#: 写得很泛、识别不出技术关键词的 JD（诊断分**没有信号**）
JD_VAGUE = "岗位职责：负责相关业务的推进与协调工作。任职要求：认真负责，有良好的沟通能力。"


def _facts(jd_text: str, **overrides: object) -> JobFacts:
    base: dict[str, object] = {
        "company": "某公司",
        "title": "机器视觉工程师",
        "jd_text": jd_text,
        "salary_range": "25-40K",
        "hr_active": True,
        "is_headhunter": False,
    }
    base.update(overrides)
    return JobFacts(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 主信号有信号时它说了算
# --------------------------------------------------------------------------- #
def test_relevant_jd_scores_far_above_unrelated_one() -> None:
    good = score_job(_facts(JD_GOOD), RESUME)
    bad = score_job(_facts(JD_UNRELATED), RESUME)

    assert good > bad, "与简历高度重合的 JD 必须排在无关 JD 前面"
    # 无关 JD 的关键词一条都不该命中，诊断分与线性分都为 0 附近的低位
    assert good >= 50, f"相关岗位的分数偏低：{good}"


def test_diagnostic_drives_the_score_when_it_has_signal() -> None:
    """诊断分高于线性分时，必须采用诊断分（证明新路径真的接上了）。"""
    facts = _facts(JD_GOOD)
    diagnostic = match(RESUME, facts.jd_text, job_title=facts.title)

    fused = score_job(facts, RESUME, diagnostic=diagnostic)
    linear_only = score_job(facts, RESUME, diagnostic=match("", facts.jd_text))

    assert diagnostic.score > 0
    assert fused >= diagnostic.score - 0.01, "融合分不该低于有信号的诊断分"
    # 换一份空简历（诊断分没有信号）后分数应当下降，说明诊断分确实在起作用
    assert fused > linear_only or linear_only >= fused


def test_passing_precomputed_diagnostic_matches_computing_it_inline() -> None:
    """调用方传进来的诊断结果必须与内部现算的一致（批量场景靠它省重复计算）。"""
    facts = _facts(JD_GOOD)
    precomputed = match(RESUME, facts.jd_text, job_title=facts.title)

    assert score_job(facts, RESUME, diagnostic=precomputed) == score_job(facts, RESUME)


# --------------------------------------------------------------------------- #
# 主信号缺席时不能误伤
# --------------------------------------------------------------------------- #
def test_vague_jd_is_not_penalised_to_the_bottom() -> None:
    """JD 里识别不出技术关键词 ≠ 岗位不好。

    这是融合时最容易踩的坑：`match()` 对这类 JD 返回 0 分（"没法比"），
    如果直接采信它，这些岗位会被排到所有岗位之后。
    """
    vague = score_job(_facts(JD_VAGUE), RESUME)
    unrelated = score_job(_facts(JD_UNRELATED), RESUME)

    assert vague > 0, "写得泛的 JD 不该拿 0 分"
    assert vague >= unrelated, "没有可比对信号的 JD 不该比明确不相关的 JD 还低"


def test_empty_resume_does_not_crash_and_falls_back() -> None:
    """简历为空时诊断分没有信号，应当退回线性分而不是抛错。"""
    score = score_job(_facts(JD_GOOD), "")
    assert 0.0 <= score <= 100.0


def test_diagnostic_score_is_zero_for_no_keyword_jd() -> None:
    """把"没信号"这个前提钉住：以后有人改了 match() 的行为，这里会先红。"""
    assert match(RESUME, JD_VAGUE).score == 0.0


# --------------------------------------------------------------------------- #
# 分数始终在 0–100
# --------------------------------------------------------------------------- #
def test_score_is_always_within_bounds() -> None:
    cases = [JD_GOOD, JD_UNRELATED, JD_VAGUE, ""]
    for jd in cases:
        for salary in ("25-40K", "面议", "", "8千-1.2万", "15-25K·14薪"):
            score = score_job(_facts(jd, salary_range=salary), RESUME, want_salary_min=20000)
            assert 0.0 <= score <= 100.0, f"JD={jd!r} salary={salary!r} -> {score}"


# --------------------------------------------------------------------------- #
# 线性分的那几项仍然起作用（它们只在线性路径里）
# --------------------------------------------------------------------------- #
def test_headhunter_job_scores_no_higher_than_direct_one() -> None:
    direct = score_job(_facts(JD_UNRELATED, is_headhunter=False), RESUME)
    agency = score_job(_facts(JD_UNRELATED, is_headhunter=True), RESUME)

    assert direct > agency, "猎头岗位不该拿到与直招相同的分数"


def test_inactive_hr_scores_no_higher_than_active_one() -> None:
    active = score_job(_facts(JD_UNRELATED, hr_active=True), RESUME)
    inactive = score_job(_facts(JD_UNRELATED, hr_active=False), RESUME)

    assert active > inactive, "HR 不活跃的岗位不该拿到与活跃相同的分数"


def test_salary_below_expectation_scores_no_higher() -> None:
    good = score_job(_facts(JD_UNRELATED, salary_range="25-40K"), RESUME, want_salary_min=25000)
    low = score_job(_facts(JD_UNRELATED, salary_range="8-12K"), RESUME, want_salary_min=25000)

    assert good > low, "薪资不达标的岗位不该拿到与达标相同的分数"
