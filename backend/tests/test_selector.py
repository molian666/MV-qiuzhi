"""加权抽题的测试。

核心断言：**薄弱度权重最高**——这是 M5 验收里"低分题在下次出题中被明显优先命中"的落地。
同时验证岗位匹配、到期、题型罚项的算法行为，以及排序稳定性（同分不能随机跳动）。
"""

from __future__ import annotations

from app.analysis.selector import (
    Candidate,
    SelectorWeights,
    apply_type_penalty,
    difficulty_fit,
    explain,
    jd_match_score,
    rank,
    score_candidate,
    select,
)


def test_difficulty_fit_peaks_at_target() -> None:
    assert difficulty_fit(3) == 1.0
    assert difficulty_fit(2) == 0.5
    assert difficulty_fit(4) == 0.5
    assert difficulty_fit(1) == 0.0
    assert difficulty_fit(5) == 0.0


def test_jd_match_uses_bidirectional_containment() -> None:
    assert jd_match_score([], ["并发"]) == 0.0
    assert jd_match_score(["高并发"], ["并发"]) == 1.0  # JD 写"高并发"，标签写"并发"也算命中
    assert jd_match_score(["高并发", "Redis"], ["并发"]) == 0.5
    assert jd_match_score(["k8s"], ["并发"]) == 0.0


def test_weakness_outweighs_everything_else() -> None:
    """薄弱度权重最高：一个"很弱但别的项都差"的题，也必须排在前面。"""
    weak = Candidate(question_id=1, text="弱", weakness=1.0, difficulty=3)
    strong = Candidate(question_id=2, text="强", weakness=0.0, jd_match=1.0, due_urgency=1.0, difficulty=3)
    assert rank([strong, weak])[0].question_id == 1


def test_due_urgency_can_break_ties_among_equally_weak() -> None:
    urgent = Candidate(question_id=1, text="到期", weakness=0.5, due_urgency=1.0, difficulty=3)
    calm = Candidate(question_id=2, text="未到期", weakness=0.5, due_urgency=0.0, difficulty=3)
    assert rank([calm, urgent])[0].question_id == 1


def test_type_penalty_pushes_recently_practiced_types_down() -> None:
    candidates = [
        Candidate(question_id=1, text="行为题", type="behavior", weakness=0.5, difficulty=3),
        Candidate(question_id=2, text="技术题", type="tech", weakness=0.5, difficulty=3),
    ]
    apply_type_penalty(candidates, ["behavior", "behavior", "tech"])
    assert candidates[0].type_penalty == 1.0
    assert candidates[1].type_penalty == 0.5
    assert rank(candidates)[0].question_id == 2


def test_apply_type_penalty_is_noop_without_history() -> None:
    candidates = [Candidate(question_id=1, text="题", type="tech")]
    apply_type_penalty(candidates, [])
    assert candidates[0].type_penalty == 0.0


def test_explain_exposes_every_contribution() -> None:
    candidate = Candidate(question_id=1, text="题", weakness=1.0, jd_match=1.0, due_urgency=1.0, difficulty=3)
    detail = explain(candidate)
    assert detail["weakness"] == 3.0
    assert detail["jd_match"] == 1.2
    assert detail["due_urgency"] == 1.5
    assert detail["difficulty_fit"] == 0.5
    assert detail["type_penalty"] == 0.0
    assert detail["total"] == round(score_candidate(candidate), 3)


def test_select_is_stable_on_ties() -> None:
    candidates = [Candidate(question_id=index, text="题", weakness=0.5, difficulty=3) for index in range(5)]
    picked = select(candidates, limit=2)
    assert [item.question_id for item in picked] == [0, 1]


def test_select_with_zero_limit() -> None:
    candidates = [Candidate(question_id=1, text="题")]
    assert select(candidates, limit=0) == []
    assert select(candidates, limit=-1) == []


def test_weights_are_configurable() -> None:
    candidate = Candidate(question_id=1, text="题", weakness=1.0, jd_match=1.0, difficulty=3)
    weakness_only = score_candidate(candidate, SelectorWeights(weakness=10.0, jd_match=0.0, due_urgency=0.0, difficulty_fit=0.0, type_balance=0.0))
    assert weakness_only == 10.0
