"""入职清单、OKR、长期规划的纯函数测试（M15）。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace as NS

from app.analysis.career import MIN_RELIABLE_SAMPLES, benchmark_summary, timing_checklist
from app.analysis.okr import build_objective_view, build_review_draft, kr_pct, kr_status
from app.analysis.onboarding import checklist_progress, default_tasks

WHEN = datetime(2026, 9, 24, 10, 0, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# 入职清单
# --------------------------------------------------------------------------- #
def test_default_tasks_cover_plan_categories() -> None:
    tasks = default_tasks()
    categories = {task.category for task in tasks}
    # PLAN 验收标准点名的分类一个都不能少
    for required in ("背调材料", "体检", "学历与离职证明", "离职交接", "租房与落户"):
        assert required in categories, f"默认清单缺分类：{required}"
    # 社保转移在 PLAN 的原文里（「社保转移」），模型里叫「社保与公积金」
    assert "社保与公积金" in categories


def test_default_tasks_all_have_why_and_where() -> None:
    # 验收点是「每项带为什么需要 + 去哪里办」，空话清单不如不给
    for task in default_tasks():
        assert task.why_needed.strip(), f"{task.title} 缺「为什么需要」"
        assert task.where_to_do.strip(), f"{task.title} 缺「去哪里办」"


def test_checklist_progress_counts_and_pct() -> None:
    tasks = [
        NS(category="体检", done=True),
        NS(category="体检", done=False),
        NS(category="背调材料", done=True),
    ]
    progress = checklist_progress(tasks)
    assert progress["total"] == 3
    assert progress["done"] == 2
    assert progress["remaining"] == 1
    assert progress["pct"] == 67  # round(2/3*100)
    assert not progress["complete"]
    by_category = {item["category"]: item for item in progress["by_category"]}
    assert by_category["体检"] == {"category": "体检", "total": 2, "done": 1}


def test_checklist_progress_complete_and_empty() -> None:
    assert checklist_progress([])["pct"] == 0
    assert checklist_progress([])["complete"] is False
    done_all = checklist_progress([NS(category="体检", done=True)])
    assert done_all["complete"] is True


def test_checklist_progress_custom_category_not_dropped() -> None:
    progress = checklist_progress([NS(category="自定义分类", done=False)])
    assert progress["by_category"] == [{"category": "自定义分类", "total": 1, "done": 0}]


# --------------------------------------------------------------------------- #
# OKR 进展
# --------------------------------------------------------------------------- #
def test_kr_pct_guards() -> None:
    assert kr_pct(0, 0) == 0.0  # 未设目标值不做除零
    assert kr_pct(1, -5) == 0.0
    assert kr_pct(-3, 10) == 0.0  # 负当前值按 0 处理
    assert kr_pct(1, 4) == 25.0
    assert kr_pct(8, 4) == 200.0  # 超额完成如实展示


def test_kr_status_labels() -> None:
    assert kr_status(0, 0) == "未设目标值"
    assert kr_status(0, 5) == "未开始"
    assert kr_status(40, 5) == "进行中"
    assert kr_status(100, 5) == "已完成"


def test_objective_view_orders_krs_and_averages() -> None:
    objective = NS(id=1, title="融入团队", period="试用期", note="", status="active", sort_order=1)
    krs = [
        NS(id=11, title="KR-B", unit="次", target_value=4, current_value=4, confidence=80,
           sort_order=2, updated_at=WHEN),
        NS(id=10, title="KR-A", unit="次", target_value=10, current_value=5, confidence=50,
           sort_order=1, updated_at=WHEN),
    ]
    view = build_objective_view(objective, krs)
    assert [kr.title for kr in view.krs] == ["KR-A", "KR-B"]  # 按 sort_order 排
    assert view.pct == 75.0  # (50 + 100) / 2
    assert view.kr_done == 1
    assert view.status_label == "进行中"


def test_objective_without_krs_has_zero_pct() -> None:
    view = build_objective_view(NS(id=2, title="空目标", period="", note="", status="draft", sort_order=1), [])
    assert view.pct == 0.0


# --------------------------------------------------------------------------- #
# 转正述职稿
# --------------------------------------------------------------------------- #
def test_review_draft_contains_numbers_from_records_only() -> None:
    objective = NS(id=1, title="独挡一面", period="2026 Q4", note="", status="active", sort_order=1)
    krs = {
        1: [
            NS(id=10, title="独立交付需求", unit="个", target_value=5, current_value=3,
               confidence=70, sort_order=1, updated_at=WHEN),
            NS(id=11, title="文档沉淀", unit="篇", target_value=4, current_value=4,
               confidence=90, sort_order=2, updated_at=WHEN),
        ]
    }
    draft = build_review_draft([objective], krs, stats={"sessions_finished": 6, "reports_avg_overall": 7.2,
                                                        "mastery_topics": 8, "learning_done": 3},
                               generated_at=WHEN)
    assert "整体完成度 80.0%" in draft.markdown  # (60 + 100) / 2
    assert "3 / 5 个（60.0%，进行中），还差 2 个" in draft.markdown
    assert "4 / 4 篇（100.0%，已完成）" in draft.markdown
    assert "完成模拟面试 6 场" in draft.markdown
    # 不编造：下一阶段全部留白
    assert "【 】" in draft.markdown
    assert "40 万" not in draft.markdown


def test_review_draft_empty_okr_is_honest() -> None:
    draft = build_review_draft([], {}, generated_at=WHEN)
    assert "还没有记录任何目标" in draft.markdown


def test_review_draft_kr_without_target_not_divided() -> None:
    objective = NS(id=1, title="X", period="", note="", status="active", sort_order=1)
    krs = {1: [NS(id=9, title="KR", unit="次", target_value=0, current_value=2, confidence=0,
                  sort_order=1, updated_at=WHEN)]}
    draft = build_review_draft([objective], krs, generated_at=WHEN)
    assert "未设目标值" in draft.markdown
    assert "nan" not in draft.markdown.lower()


# --------------------------------------------------------------------------- #
# 长期规划：时机清单与薪资基准
# --------------------------------------------------------------------------- #
def test_timing_checklist_covers_promotion_and_switch() -> None:
    kinds = {item.kind for item in timing_checklist()}
    assert kinds == {"promotion", "switch"}
    for item in timing_checklist():
        assert item.question.strip() and item.where.strip(), "时机项必须带问题与数据来源"


def test_benchmark_summary_small_sample_warns() -> None:
    rows = [NS(range_low=300_000.0, range_high=380_000.0, source="同学A", verified=False)]
    summary = benchmark_summary(rows)
    assert summary["count"] == 1
    assert f"样本不足（仅 1 条）" in summary["note"]
    assert "不构成定价建议" in summary["note"]


def test_benchmark_summary_medians() -> None:
    rows = [
        NS(range_low=20.0, range_high=30.0, source="A", verified=False),
        NS(range_low=30.0, range_high=45.0, source="B", verified=True),
        NS(range_low=25.0, range_high=36.0, source="C", verified=False),
        NS(range_low=100.0, range_high=140.0, source="A", verified=False),  # 离群值也如实统计
    ]
    summary = benchmark_summary(rows)
    assert summary["count"] == 4
    assert summary["median_low"] == 27.5  # (25+30)/2
    assert summary["median_high"] == 40.5  # (36+45)/2
    assert summary["sources"] == ["A", "B", "C"]  # 去重且保序
    assert summary["verified_count"] == 1
    assert "样本不足" not in summary["note"]  # >= MIN_RELIABLE_SAMPLES


def test_benchmark_summary_empty() -> None:
    summary = benchmark_summary([])
    assert summary["count"] == 0
    assert summary["note"].startswith("还没有记录")
    assert MIN_RELIABLE_SAMPLES >= 3
