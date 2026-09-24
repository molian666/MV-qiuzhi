"""导出中间格式与渲染的纯函数测试（M15）。

这里钉住的是「导出内容完整」：板块清单、每张表的列、类型格式化、
Markdown 转义。这些断言不碰 DB——构造器接受任意同形对象。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace as NS
from typing import Any

import pytest

from app.analysis.export import (
    EXPORT_SECTION_KEYS,
    ExportBundle,
    ExportSection,
    build_applications,
    build_benchmarks,
    build_okr,
    build_onboarding,
    build_resumes,
    bundle_stats,
    format_cell,
    render_csv_sections,
    render_json,
    render_markdown,
)

WHEN = datetime(2026, 9, 24, 10, 0, 0, tzinfo=UTC)


def make_bundle(sections: list[ExportSection]) -> ExportBundle:
    return ExportBundle(generated_at=WHEN, sections=sections)


# --------------------------------------------------------------------------- #
# format_cell
# --------------------------------------------------------------------------- #
def test_format_cell_none_bool_datetime() -> None:
    assert format_cell(None) == ""
    assert format_cell(True) == "是"
    assert format_cell(False) == "否"
    assert format_cell(WHEN) == "2026-09-24T10:00:00+00:00"


def test_format_cell_json_types() -> None:
    assert format_cell(["a", "b"]) == '["a", "b"]'
    assert format_cell({"表达": 7.5}) == '{"表达": 7.5}'
    assert format_cell(3.5) == "3.5"


# --------------------------------------------------------------------------- #
# 构造器：字段取舍被钉住
# --------------------------------------------------------------------------- #
def test_build_resumes_keeps_full_text() -> None:
    resume = NS(name="主简历", version=2, variant_label="A", target_job_id=3,
                content_text="第一行\n第二行", created_at=WHEN)
    section = build_resumes([resume])
    assert [column.key for column in section.columns] == [
        "name", "version", "variant_label", "target_job_id", "content_text", "created_at",
    ]
    assert section.rows[0]["content_text"] == "第一行\n第二行"


def test_build_applications_status_and_channel_kept() -> None:
    app = NS(applied_at=WHEN, company="甲公司", title="后端", channel="boss", status="interview1",
             salary_range="25-35k", city="杭州", resume_version="v2", jd_url="http://x", note="内推")
    section = build_applications([app])
    assert section.rows[0]["status"] == "interview1"
    assert section.rows[0]["channel"] == "boss"
    assert section.row_count == 1


def test_build_okr_flattened_row() -> None:
    row = {"period": "试用期", "objective": "融入团队", "objective_status": "进行中",
           "key_result": "完成 3 次需求交付", "unit": "次", "target_value": 3,
           "current_value": 1, "progress": 33.3, "confidence": 70, "updated_at": WHEN}
    section = build_okr([row])
    assert section.rows[0]["progress"] == 33.3
    assert "关键结果" in [column.header for column in section.columns]


def test_build_onboarding_columns() -> None:
    task = NS(category="体检", title="预约入职体检", why_needed="公司要求", where_to_do="三甲体检中心",
              done=True, done_at=WHEN, note="")
    section = build_onboarding([task])
    assert [column.key for column in section.columns] == [
        "category", "title", "why_needed", "where_to_do", "done", "done_at", "note",
    ]


def test_missing_attribute_becomes_empty_cell() -> None:
    section = build_applications([{"company": "只有公司的残行"}])
    assert section.rows[0]["status"] is None
    assert format_cell(section.rows[0]["status"]) == ""


# --------------------------------------------------------------------------- #
# Markdown 渲染
# --------------------------------------------------------------------------- #
def test_markdown_escapes_pipe_and_newline() -> None:
    resume = NS(name="含|竖线\n换行", version=1, variant_label="", target_job_id=None,
                content_text="", created_at=WHEN)
    markdown = render_markdown(make_bundle([build_resumes([resume])]))
    table_lines = [line for line in markdown.splitlines() if line.startswith("| ")]
    joined = "\n".join(table_lines)
    # 单元格里不允许出现裸 | 与换行
    assert "含\\|竖线<br>换行" in joined


def test_markdown_empty_section_gets_placeholder() -> None:
    markdown = render_markdown(make_bundle([build_applications([])]))
    assert "（暂无数据）" in markdown


def test_markdown_header_counts_rows() -> None:
    apps = [NS(applied_at=WHEN, company=f"公司{i}", title="", channel="", status="",
               salary_range="", city="", resume_version="", jd_url="", note="") for i in range(3)]
    markdown = render_markdown(make_bundle([build_applications(apps)]))
    assert "| 投递记录 | 3 |" in markdown


# --------------------------------------------------------------------------- #
# CSV 渲染
# --------------------------------------------------------------------------- #
def test_csv_quotes_commas_and_newlines() -> None:
    resume = NS(name="多,逗号", version=1, variant_label="", target_job_id=None,
                content_text="第一行\n第二行", created_at=WHEN)
    files = dict(render_csv_sections(make_bundle([build_resumes([resume])])))
    body = files["resumes.csv"]
    assert '"多,逗号"' in body
    assert '"第一行\n第二行"' in body


def test_csv_one_file_per_section() -> None:
    bundle = make_bundle([build_applications([]), build_benchmarks([])])
    files = render_csv_sections(bundle)
    assert [name for name, _ in files] == ["applications.csv", "benchmarks.csv"]


# --------------------------------------------------------------------------- #
# JSON 渲染
# --------------------------------------------------------------------------- #
def test_json_keeps_native_types() -> None:
    bench = NS(company="乙公司", title="后端", city="上海", range_low=300000.0, range_high=400000.0,
               currency="CNY", period="2026", source="同学打听", source_url="", verified=False,
               note="", recorded_at=WHEN)
    payload = render_json(make_bundle([build_benchmarks([bench])]))
    round_tripped = json.loads(json.dumps(payload, ensure_ascii=False))
    row = round_tripped["sections"][0]["rows"][0]
    assert row["range_low"] == 300000.0  # 数字没被变字符串
    assert row["verified"] is False
    assert row["recorded_at"] == "2026-09-24T10:00:00+00:00"


def test_json_is_bundle_shape() -> None:
    payload: dict[str, Any] = render_json(make_bundle([build_applications([])]))
    assert set(payload.keys()) == {"generated_at", "sections"}
    assert payload["sections"][0]["key"] == "applications"
    assert payload["sections"][0]["columns"][0]["header"] == "投递时间"


# --------------------------------------------------------------------------- #
# 板块完整性
# --------------------------------------------------------------------------- #
def test_export_section_keys_cover_all_data_tables() -> None:
    # 验收标准里的每一类数据都必须有对应板块，少一个这个测试就红
    required = {
        "targets": "目标岗位",
        "resumes": "简历与版本",
        "applications": "投递记录",
        "sessions": "面试会话",
        "turns": "逐轮问答",
        "reports": "评分报告",
        "improvements": "改进项",
        "mastery": "薄弱点与掌握度",
        "training": "训练记录",
        "stories": "故事卡",
        "answers": "话术",
        "offers": "Offer",
        "contacts": "人脉",
        "interactions": "跟进",
        "learning": "学习清单",
        "onboarding": "入职清单",
        "okr": "OKR",
        "career_plan": "长期规划",
        "benchmarks": "薪资基准",
    }
    missing = set(required) - set(EXPORT_SECTION_KEYS)
    assert not missing, f"缺板块：{missing}"


def test_bundle_stats_and_row_count() -> None:
    apps = [NS(applied_at=WHEN, company="x", title="", channel="", status="",
               salary_range="", city="", resume_version="", jd_url="", note="")]
    bundle = make_bundle([build_applications(apps), build_benchmarks([])])
    stats = bundle_stats(bundle)
    assert stats == [
        {"key": "applications", "title": "投递记录", "rows": 1},
        {"key": "benchmarks", "title": "薪资基准", "rows": 0},
    ]
    assert bundle.row_count == 1
    assert bundle.section("benchmarks") is not None
    assert bundle.section("nope") is None


def test_markdown_renders_without_error_on_all_keys() -> None:
    # 每个板块都能渲染（构造器与渲染器签名对得上）
    builders = {
        "applications": build_applications,
        "resumes": build_resumes,
        "onboarding": build_onboarding,
    }
    bundle = make_bundle([builder([]) for builder in builders.values()])
    assert render_markdown(bundle)


@pytest.mark.parametrize("key", list(EXPORT_SECTION_KEYS))
def test_every_section_key_is_snake_case(key: str) -> None:
    # CSV 文件名 = key.csv，保持简单字符，避免平台差异
    assert key == key.lower()
    assert " " not in key and "/" not in key
