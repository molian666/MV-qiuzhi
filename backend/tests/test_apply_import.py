"""投递数据导入解析的测试（纯逻辑）。

导入这类功能的失败往往不是报错，而是**静默丢列**：
用户以为导进去了，其实"简历版本"那一列根本没被识别。
所以这些断言既测解析结果，也测"有没有如实报告认不出的列"。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.analysis.apply_import import (
    make_dedup_key,
    normalize_channel,
    normalize_status,
    parse_csv_text,
    parse_datetime,
)

CSV_CHINESE = """公司,岗位,渠道,状态,投递时间,简历版本,城市
某某科技,后端工程师,Boss直聘,已投递,2026-03-01,主简历,杭州
某某网络,Python开发,猎聘,已查看,2026/3/2,量化版,上海
某某信息,服务端工程师,智联招聘,一面,2026年3月3日,主简历,北京
"""


def test_parses_common_chinese_headers() -> None:
    result = parse_csv_text(CSV_CHINESE)

    assert len(result.rows) == 3
    first = result.rows[0]
    assert first.company == "某某科技"
    assert first.title == "后端工程师"
    assert first.channel == "boss"
    assert first.status == "applied"
    assert first.resume_version == "主简历"
    assert first.city == "杭州"
    assert (first.applied_at.year, first.applied_at.month, first.applied_at.day) == (2026, 3, 1)

    assert set(result.matched_columns) >= {
        "company",
        "title",
        "channel",
        "status",
        "applied_at",
        "resume_version",
        "city",
    }
    assert result.unmatched_headers == []


def test_matches_english_headers_and_tab_delimiter() -> None:
    text = "Company\tPosition\tPlatform\tStatus\tApply Date\nAcme\tBackend\tBoss\tViewed\t2026-03-05"
    result = parse_csv_text(text)

    assert result.delimiter == "\t"
    assert result.rows[0].company == "Acme"
    assert result.rows[0].title == "Backend"
    assert result.rows[0].channel == "boss"
    assert result.rows[0].status == "viewed"


def test_reports_unmatched_headers_instead_of_silently_dropping() -> None:
    result = parse_csv_text("公司,岗位,神秘列\nA,B,C")
    assert "神秘列" in result.unmatched_headers


def test_skips_blank_and_unusable_rows() -> None:
    # 第三行 "" 与第五行 "2026-03-04" 都还有内容（备注列），但公司/岗位为空 → 应被跳过并报告原因
    text = "公司,岗位,备注\nA,B,\n,,只有备注没有公司岗位\nC,D,\n,,只写了时间"
    result = parse_csv_text(text)

    assert [row.company for row in result.rows] == ["A", "C"]
    assert any("没有公司或岗位" in reason for _, reason in result.skipped)


def test_empty_lines_are_silently_ignored() -> None:
    """整行空白不算"错误行"，不该塞满 skipped 列表。"""
    result = parse_csv_text("公司,岗位\nA,B\n,\nC,D\n,,")
    assert [row.company for row in result.rows] == ["A", "C"]
    assert result.skipped == []


def test_strips_excel_bom() -> None:
    result = parse_csv_text("\ufeff公司,岗位\nA,B")
    assert result.rows and result.rows[0].company == "A"


@pytest.mark.parametrize(
    ("value", "year", "month", "day", "hour", "minute"),
    [
        ("2026-03-12", 2026, 3, 12, 0, 0),
        ("2026/3/12 14:30", 2026, 3, 12, 14, 30),
        ("2026年3月12日", 2026, 3, 12, 0, 0),
        ("20260312", 2026, 3, 12, 0, 0),
        ("03-12", 2025, 3, 12, 0, 0),  # 缺年份 → 用 default_year
        ("3月12日", 2025, 3, 12, 0, 0),
    ],
)
def test_parses_datetime_formats(
    value: str,
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
) -> None:
    parsed = parse_datetime(value, default_year=2025)
    assert parsed is not None
    assert (parsed.year, parsed.month, parsed.day, parsed.hour, parsed.minute) == (year, month, day, hour, minute)
    assert parsed.tzinfo is not None  # 落库要求 aware（SQLModel 的 UTCDateTime 拒绝 naive）


def test_parses_excel_serial_date() -> None:
    parsed = parse_datetime("45000")  # Excel 序列号 → 2023-03-14 附近
    assert parsed is not None
    assert parsed.year == 2023


def test_unparsable_datetime_falls_back_to_default() -> None:
    assert parse_datetime("前天", default=None) is None
    fallback = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert parse_datetime("不知道", default=fallback) == fallback


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Boss直聘", "boss"),
        ("boss", "boss"),
        ("BOSS 直聘", "boss"),
        ("猎聘网", "liepin"),
        ("智联招聘", "zhilian"),
        ("前程无忧", "51job"),
        ("内推", "referral"),
        ("某个没见过的平台", "other"),
        ("", "other"),
    ],
)
def test_channel_normalization(raw: str, expected: str) -> None:
    assert normalize_channel(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("已投递", "applied"),
        ("投递成功", "applied"),
        ("已读", "viewed"),
        ("面试中", "interview1"),
        ("二面", "interview2"),
        ("HR面", "hr"),
        ("已offer", "offer"),
        ("不合适", "rejected"),
        ("石沉大海", "ghosted"),
        ("未知状态", "applied"),
        ("", "applied"),
    ],
)
def test_status_normalization(raw: str, expected: str) -> None:
    assert normalize_status(raw) == expected


def test_dedup_key_uses_date_granularity() -> None:
    morning = make_dedup_key("某某科技", "后端工程师", datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc))
    evening = make_dedup_key(" 某某科技 ", "后端工程师", datetime(2026, 3, 1, 21, 0, tzinfo=timezone.utc))
    next_day = make_dedup_key("某某科技", "后端工程师", datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc))

    # 同一天同一公司岗位 = 同一次投递（各来源时间精度不同，用精确时间会漏判重复）
    assert morning == evening
    assert morning != next_day
