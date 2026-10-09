"""自动通过闸门的策略。

这是项目里唯一"程序替用户做不可撤销决定"的地方，所以测试的重点是
**它不会投出不该投的东西**：低分不投、被规则拦下的不投、超额度不投、非 pending 不投。
"""

from __future__ import annotations

from app.apply.autopilot import (
    DEFAULT_DAILY_LIMIT,
    DEFAULT_MAX_PER_RUN,
    DEFAULT_MIN_SCORE,
    ApprovalOutcome,
    AutoApproval,
    Candidate,
    select_auto_approvals,
)


def _candidates(*scores: float, **kwargs: object) -> list[Candidate]:
    return [Candidate(task_id=index + 1, matched_score=score, **kwargs) for index, score in enumerate(scores)]  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 阈值
# --------------------------------------------------------------------------- #
def test_high_score_is_approved_with_a_reason() -> None:
    outcome = select_auto_approvals(_candidates(85.0), min_score=70.0)

    assert outcome.count == 1
    item = outcome.approved[0]
    assert item.task_id == 1
    assert item.score == 85.0
    # 理由要能被用户读懂，事后审计靠它
    assert "85" in item.reason
    assert "70" in item.reason


def test_score_below_threshold_is_skipped_with_reason() -> None:
    outcome = select_auto_approvals(_candidates(45.0), min_score=70.0)

    assert outcome.count == 0
    task_id, reason = outcome.skipped[0]
    assert task_id == 1
    assert "低于" in reason


def test_boundary_score_is_approved() -> None:
    """恰好等于阈值算通过——边界必须明确。"""
    outcome = select_auto_approvals(_candidates(70.0), min_score=70.0)
    assert outcome.count == 1


def test_threshold_is_strict_about_a_tenth_below() -> None:
    outcome = select_auto_approvals(_candidates(69.9), min_score=70.0)
    assert outcome.count == 0


def test_default_threshold_is_conservative() -> None:
    """默认阈值不能太低，否则自动模式会投出一堆不匹配的岗位。"""
    assert DEFAULT_MIN_SCORE >= 60.0


# --------------------------------------------------------------------------- #
# 过滤链优先
# --------------------------------------------------------------------------- #
def test_blocked_candidate_is_never_approved_even_with_a_high_score() -> None:
    """**过滤链表达的是用户的明确意图，不该被一个高分覆盖。**"""
    outcome = select_auto_approvals(
        [Candidate(task_id=1, matched_score=99.0, passed=False)],
        min_score=70.0,
    )

    assert outcome.count == 0
    assert "过滤规则" in outcome.skipped[0][1]


def test_only_pending_candidates_are_touched() -> None:
    """已经在队列里其它状态的任务不该被自动模式改动。"""
    for status in ("approved", "greeted", "sent", "failed", "skipped"):
        outcome = select_auto_approvals(
            [Candidate(task_id=1, matched_score=95.0, status=status)],
            min_score=70.0,
        )
        assert outcome.count == 0, status
        assert status in outcome.skipped[0][1]


# --------------------------------------------------------------------------- #
# 名额与额度
# --------------------------------------------------------------------------- #
def test_ranking_is_by_score_descending() -> None:
    outcome = select_auto_approvals(_candidates(75.0, 95.0, 85.0), max_per_run=3)

    assert [item.task_id for item in outcome.approved] == [2, 3, 1]
    assert [item.score for item in outcome.approved] == [95.0, 85.0, 75.0]


def test_ties_are_broken_deterministically() -> None:
    """同分时顺序必须可复现——否则同一个库两次跑可能投出**不同的集合**。"""
    first = select_auto_approvals(_candidates(80.0, 80.0, 80.0), max_per_run=2)
    second = select_auto_approvals(_candidates(80.0, 80.0, 80.0), max_per_run=2)

    assert [item.task_id for item in first.approved] == [1, 2]
    assert [item.task_id for item in first.approved] == [item.task_id for item in second.approved]


def test_max_per_run_caps_the_batch() -> None:
    outcome = select_auto_approvals(_candidates(*([90.0] * 10)), max_per_run=3)

    assert outcome.count == 3
    assert len(outcome.skipped) == 7
    assert "名额" in outcome.skipped[0][1]


def test_daily_remaining_caps_the_batch_further() -> None:
    outcome = select_auto_approvals(_candidates(*([90.0] * 10)), max_per_run=8, daily_remaining=2)

    assert outcome.count == 2
    assert all("额度" in reason or "名额" in reason for _, reason in outcome.skipped)


def test_zero_quota_approves_nothing() -> None:
    outcome = select_auto_approvals(_candidates(99.0), daily_remaining=0)

    assert outcome.count == 0
    assert "额度已用完" in outcome.skipped[0][1]


def test_zero_max_per_run_approves_nothing() -> None:
    outcome = select_auto_approvals(_candidates(99.0), max_per_run=0)
    assert outcome.count == 0


def test_defaults_are_lower_than_manual_mode() -> None:
    """自动模式的额度要**明显低于**手动（手动是每日 30 / 每分钟 3）。

    无人值守时人不在旁边，出问题也没人踩刹车。
    """
    assert DEFAULT_MAX_PER_RUN < 30
    assert DEFAULT_DAILY_LIMIT < 30


# --------------------------------------------------------------------------- #
# 空输入
# --------------------------------------------------------------------------- #
def test_empty_candidates_approve_nothing() -> None:
    outcome = select_auto_approvals([])

    assert isinstance(outcome, ApprovalOutcome)
    assert outcome.count == 0
    assert outcome.skipped == []


def test_every_candidate_is_accounted_for() -> None:
    """不变量：每条候选要么被通过、要么带原因被跳过——不能有一条无声消失。

    "无声消失"正是审计最难查的情况：用户看到队列里少了一条，却不知道为什么。
    """
    pool = _candidates(95.0, 40.0, 80.0, 70.0)

    outcome = select_auto_approvals(pool, min_score=70.0, max_per_run=2)

    seen = {item.task_id for item in outcome.approved} | {task_id for task_id, _ in outcome.skipped}
    assert seen == {1, 2, 3, 4}
