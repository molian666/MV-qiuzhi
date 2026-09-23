"""按匹配分区间看回复率：把"投得准不准"和"有没有回应"对上。

关键是要**诚实**：样本不够就明说样本不够，不要给一个看起来很确定的百分比。
"""

from __future__ import annotations

from app.analysis.match_stats import MIN_SAMPLE, BandInput, build_bands

RESUME = "5 年后端开发经验，熟悉 Python、Redis、MySQL、Kafka、Docker、高并发"

# 一段 JD：能被上面的简历全覆盖
FULL_JD = "要求：Python、Redis、MySQL、Kafka、Docker、高并发"
# 一段完全对不上的 JD
FAR_JD = "要求：Photoshop、Illustrator、Premiere、After Effects"


def test_empty_when_no_jd_text() -> None:
    report = build_bands(
        [BandInput(jd_text="", status="offer"), BandInput(jd_text="", status="viewed")],
        RESUME,
    )
    assert report.total_records == 2
    assert report.matched_records == 0
    assert all(stat.count == 0 for stat in report.bands)
    assert "没有 JD 正文" in report.confidence_note
    assert "补上 JD" in report.recommendation


def test_records_land_in_the_right_band() -> None:
    report = build_bands(
        [
            BandInput(jd_text=FULL_JD, status="interview1"),
            BandInput(jd_text=FAR_JD, status="ghosted"),
        ],
        RESUME,
    )
    assert report.matched_records == 2

    high = next(stat for stat in report.bands if stat.label == "85 分以上")
    low = next(stat for stat in report.bands if stat.label == "55 分以下")
    assert high.count == 1
    assert low.count == 1


def test_response_and_interview_rates_use_project_conventions() -> None:
    # 口径必须与 M6 一致：viewed 算回应，interview1 算面试，ghosted 不算回应
    report = build_bands(
        [
            BandInput(jd_text=FULL_JD, status="viewed"),
            BandInput(jd_text=FULL_JD, status="interview1"),
            BandInput(jd_text=FULL_JD, status="offer"),
            BandInput(jd_text=FULL_JD, status="ghosted"),
        ],
        RESUME,
    )
    band = next(stat for stat in report.bands if stat.count == 4)
    assert band.responded == 3
    assert band.interviewed == 2
    assert band.offers == 1
    assert band.response_rate == 0.75
    assert band.interview_rate == 0.5


def test_identical_jd_is_scored_once() -> None:
    report = build_bands([BandInput(jd_text=FULL_JD, status="viewed") for _ in range(5)], RESUME)
    assert report.matched_records == 5  # 计数照算
    assert sum(stat.count for stat in report.bands) == 5


def test_small_sample_is_flagged() -> None:
    report = build_bands([BandInput(jd_text=FULL_JD, status="viewed")], RESUME)
    assert f"少于 {MIN_SAMPLE} 条" in report.confidence_note


def test_recommendation_when_high_scores_clearly_win() -> None:
    records = [BandInput(jd_text=FULL_JD, status="viewed") for _ in range(4)]
    records += [BandInput(jd_text=FAR_JD, status="ghosted") for _ in range(4)]
    report = build_bands(records, RESUME)
    assert "匹配度确实在起作用" in report.recommendation


def test_recommendation_when_all_bands_are_quiet() -> None:
    records = [BandInput(jd_text=FULL_JD, status="applied") for _ in range(4)]
    records += [BandInput(jd_text=FAR_JD, status="ghosted") for _ in range(4)]
    report = build_bands(records, RESUME)
    assert "整体回复率" in report.recommendation or "差别不大" in report.recommendation


def test_recommendation_when_sample_too_small() -> None:
    report = build_bands([BandInput(jd_text=FULL_JD, status="viewed")], RESUME)
    assert "样本还不足以得出结论" in report.recommendation


def test_missing_jd_count_reported() -> None:
    report = build_bands(
        [
            BandInput(jd_text=FULL_JD, status="viewed"),
            BandInput(jd_text="", status="applied"),
            BandInput(jd_text="", status="applied"),
        ],
        RESUME,
    )
    assert report.total_records == 3
    assert report.matched_records == 1
    assert "2 条没有 JD 正文" in report.confidence_note
