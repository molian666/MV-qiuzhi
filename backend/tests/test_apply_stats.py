"""投递漏斗与转化统计的测试。

这类统计最容易"看着合理但其实算错"，所以每个指标都用构造数据钉死，
尤其是"面到终面才被拒要不要算进面试"这种容易让漏斗系统性偏低的地方。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.analysis.apply_stats import (
    ApplicationSnapshot,
    build_funnel,
    by_channel,
    follow_ups,
    overview,
    response_time,
    resume_version_ab,
    stage_of,
    weekly_trend,
)

NOW = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)


def _snap(
    identifier: int,
    channel: str,
    status: str,
    applied_at: datetime,
    *,
    max_stage: str = "",
    responded_at: datetime | None = None,
    resume: str = "主简历",
) -> ApplicationSnapshot:
    return ApplicationSnapshot(
        id=identifier,
        company=f"公司{identifier}",
        title="后端工程师",
        channel=channel,
        status=status,
        applied_at=applied_at,
        resume_version=resume,
        max_stage=max_stage,
        first_response_at=responded_at,
    )


def _sample() -> list[ApplicationSnapshot]:
    return [
        _snap(1, "boss", "rejected", NOW - timedelta(days=10), max_stage="interview"),
        _snap(2, "boss", "applied", NOW - timedelta(days=5)),
        _snap(3, "liepin", "offer", NOW - timedelta(days=8), max_stage="offer"),
    ]


def test_stage_mapping() -> None:
    assert stage_of("screening") == "viewed"  # "筛选中"归到"已查看"
    assert stage_of("hr") == "interview"
    assert stage_of("rejected") == "applied"  # 终态本身不代表阶段，要靠 max_stage
    assert stage_of("什么鬼") == "applied"


def test_responded_flag() -> None:
    assert _snap(1, "boss", "rejected", NOW).responded is True
    assert _snap(2, "boss", "viewed", NOW).responded is True
    assert _snap(3, "boss", "applied", NOW).responded is False
    # 石沉大海就是"没回应"
    assert _snap(4, "boss", "ghosted", NOW).responded is False


def test_funnel_counts_historical_stages() -> None:
    """面到终面才被拒，也要算进「面试」这一档，否则漏斗会系统性偏低。"""
    steps = {step.stage: step for step in build_funnel(_sample())}

    assert steps["applied"].count == 3
    assert steps["viewed"].count == 2  # 被拒那条 + offer 那条
    assert steps["interview"].count == 2
    assert steps["offer"].count == 1


def test_funnel_rates_are_relative_to_previous_stage() -> None:
    steps = {step.stage: step for step in build_funnel(_sample())}

    assert steps["applied"].rate_from_previous == 1.0
    assert steps["viewed"].rate_from_previous == 0.6667  # 2/3
    assert steps["interview"].rate_from_previous == 1.0  # 2/2
    assert steps["offer"].rate_from_previous == 0.5  # 1/2
    assert steps["offer"].rate_from_first == 0.3333  # 1/3


def test_funnel_handles_empty_input() -> None:
    steps = build_funnel([])
    assert all(step.count == 0 for step in steps)
    assert all(step.rate_from_previous == 0.0 for step in steps)


def test_channel_stats_and_rates() -> None:
    apps = [
        _snap(1, "boss", "viewed", NOW),
        _snap(2, "boss", "rejected", NOW, max_stage="interview"),
        _snap(3, "liepin", "applied", NOW),
    ]
    stats = {item.channel: item for item in by_channel(apps)}

    assert stats["boss"].total == 2
    assert stats["boss"].responded == 2
    assert stats["boss"].interviewed == 1
    assert stats["boss"].response_rate == 1.0
    assert stats["boss"].interview_rate == 0.5
    assert stats["liepin"].response_rate == 0.0


def test_weekly_trend_fills_empty_weeks() -> None:
    apps = [_snap(1, "boss", "viewed", NOW - timedelta(days=1))]
    points = weekly_trend(apps, weeks=3, now=NOW)

    assert len(points) == 3  # 空白周也要出现，否则折线会跳
    assert sum(point.applied for point in points) == 1
    assert sum(point.responded for point in points) == 1


def test_weekly_trend_ignores_out_of_window() -> None:
    apps = [_snap(1, "boss", "applied", NOW - timedelta(days=400))]
    points = weekly_trend(apps, weeks=4, now=NOW)
    assert sum(point.applied for point in points) == 0


def test_resume_version_ab_sorted_by_response_rate() -> None:
    apps = [
        _snap(1, "boss", "viewed", NOW, resume="主简历"),
        _snap(2, "boss", "applied", NOW, resume="主简历"),
        _snap(3, "boss", "viewed", NOW, resume="量化版"),
        _snap(4, "boss", "offer", NOW, resume="量化版"),
    ]
    stats = resume_version_ab(apps)

    assert stats[0].version == "量化版"
    assert stats[0].response_rate == 1.0
    assert stats[1].version == "主简历"
    assert stats[1].response_rate == 0.5


def test_resume_version_without_label_is_grouped() -> None:
    apps = [_snap(1, "boss", "viewed", NOW, resume="")]
    assert resume_version_ab(apps)[0].version == "（未标注）"


def test_response_time_stats() -> None:
    apps = [
        _snap(1, "boss", "viewed", NOW - timedelta(hours=10), responded_at=NOW - timedelta(hours=5)),
        _snap(2, "boss", "viewed", NOW - timedelta(hours=20), responded_at=NOW - timedelta(hours=5)),
        _snap(3, "boss", "applied", NOW - timedelta(hours=1)),  # 没回应，不参与统计
    ]
    stats = response_time(apps)

    assert stats.samples == 2
    assert stats.median_hours == 10.0
    assert stats.average_hours == 10.0
    assert stats.fastest_hours == 5.0
    assert stats.slowest_hours == 15.0


def test_response_time_without_samples() -> None:
    assert response_time([_snap(1, "boss", "applied", NOW)]).samples == 0


def test_follow_ups_only_for_stale_active_ones() -> None:
    apps = [
        _snap(1, "boss", "applied", NOW - timedelta(days=10)),
        _snap(2, "boss", "applied", NOW - timedelta(days=2)),  # 还太新
        _snap(3, "boss", "interview1", NOW - timedelta(days=20)),  # 已推进，不用催
        _snap(4, "boss", "rejected", NOW - timedelta(days=30)),  # 已有结果
    ]
    items = follow_ups(apps, now=NOW, after_days=7)

    assert [item.application_id for item in items] == [1]
    assert items[0].days_since == 10.0


def test_overview_shape_and_numbers() -> None:
    data = overview(_sample(), now=NOW)

    assert data["total"] == 3
    assert data["offers"] == 1
    assert data["interviewed"] == 2
    assert data["response_rate"] == 0.6667
    assert {step["stage"] for step in data["funnel"]} == {"applied", "viewed", "interview", "offer"}
    assert data["by_channel"]
    assert len(data["weekly"]) == 8
    assert "response_time" in data
    assert "follow_ups" in data
