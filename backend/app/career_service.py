"""入职与长期的服务层（M15）。

- 入职清单：首次访问自动种入默认目录（ensure_defaults），之后完全是用户的清单——
  删掉的项目不会被加回来，勾选状态只属于用户；
- 转正述职：数字统计在这里查（面试场次 / 平均分 / 学习完成数），稿子由 analysis 确定性合成；
- 薪资基准：只存与汇总来源区间，任何「建议值」都不会在这里出现。
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
from typing import Any

from sqlmodel import Session, func, select

from .analysis.career import benchmark_summary as _benchmark_summary
from .analysis.career import timing_checklist
from .analysis.okr import ObjectiveView, build_objective_view, build_review_draft
from .analysis.onboarding import checklist_progress, default_tasks
from .clock import utcnow
from .models.career import (
    CareerPlanItem,
    OkrKeyResult,
    OkrObjective,
    OnboardingTask,
    SalaryBenchmark,
)
from .models.network import LearningItem
from .models.scoring import ScoreReport, SkillMastery
from .models.session import InterviewSession


# --------------------------------------------------------------------------- #
# 入职清单
# --------------------------------------------------------------------------- #
def ensure_defaults(db: Session) -> int:
    """把默认目录里（分类+标题）尚不存在的项补进来，返回新增数。

    用（分类, 标题）做指纹而不是整行比对：用户改过 why/where 的项不该被覆盖，
    用户删掉的项也不会复活。
    """
    existing = {(task.category, task.title) for task in db.exec(select(OnboardingTask)).all()}
    added = 0
    for index, spec in enumerate(default_tasks()):
        if (spec.category, spec.title) in existing:
            continue
        db.add(OnboardingTask(
            category=spec.category,
            title=spec.title,
            why_needed=spec.why_needed,
            where_to_do=spec.where_to_do,
            sort_order=(index + 1) * 10,
        ))
        added += 1
    if added:
        db.commit()
    return added


def list_onboarding(db: Session) -> dict[str, Any]:
    ensure_defaults(db)
    tasks = list(
        db.exec(select(OnboardingTask).order_by(OnboardingTask.sort_order, OnboardingTask.id)).all()
    )
    return {"tasks": tasks, "progress": checklist_progress(tasks)}


def create_onboarding_task(db: Session, *, payload: dict[str, Any]) -> OnboardingTask:
    task = OnboardingTask(**payload)
    task.updated_at = utcnow()
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def update_onboarding_task(db: Session, task_id: int, *, payload: dict[str, Any]) -> OnboardingTask | None:
    task = db.get(OnboardingTask, task_id)
    if task is None:
        return None
    for key, value in payload.items():
        setattr(task, key, value)
    task.updated_at = utcnow()
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def set_onboarding_done(db: Session, task_id: int, *, done: bool) -> OnboardingTask | None:
    """勾选/取消勾选。勾上记时间、取消清时间——进度条只看 done。"""
    task = db.get(OnboardingTask, task_id)
    if task is None:
        return None
    task.done = done
    task.done_at = utcnow() if done else None
    task.updated_at = utcnow()
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def delete_onboarding_task(db: Session, task_id: int) -> bool:
    task = db.get(OnboardingTask, task_id)
    if task is None:
        return False
    db.delete(task)
    db.commit()
    return True


# --------------------------------------------------------------------------- #
# 试用期 OKR
# --------------------------------------------------------------------------- #
def list_okr(db: Session) -> list[ObjectiveView]:
    objectives = list(db.exec(select(OkrObjective).order_by(OkrObjective.sort_order, OkrObjective.id)).all())
    krs = list(db.exec(select(OkrKeyResult).order_by(OkrKeyResult.sort_order, OkrKeyResult.id)).all())
    grouped: dict[int, list[OkrKeyResult]] = {}
    for kr in krs:
        grouped.setdefault(kr.objective_id, []).append(kr)
    return [build_objective_view(objective, grouped.get(objective.id or 0, [])) for objective in objectives]


def create_objective(db: Session, *, payload: dict[str, Any]) -> OkrObjective:
    objective = OkrObjective(**payload)
    objective.updated_at = utcnow()
    db.add(objective)
    db.commit()
    db.refresh(objective)
    return objective


def update_objective(db: Session, objective_id: int, *, payload: dict[str, Any]) -> OkrObjective | None:
    objective = db.get(OkrObjective, objective_id)
    if objective is None:
        return None
    for key, value in payload.items():
        setattr(objective, key, value)
    objective.updated_at = utcnow()
    db.add(objective)
    db.commit()
    db.refresh(objective)
    return objective


def delete_objective(db: Session, objective_id: int) -> bool:
    objective = db.get(OkrObjective, objective_id)
    if objective is None:
        return False
    # KR 跟着目标走：留着就是指向不存在目标的孤儿行
    for kr in db.exec(select(OkrKeyResult).where(OkrKeyResult.objective_id == objective_id)).all():
        db.delete(kr)
    db.delete(objective)
    db.commit()
    return True


def add_key_result(db: Session, objective_id: int, *, payload: dict[str, Any]) -> OkrKeyResult | None:
    if db.get(OkrObjective, objective_id) is None:
        return None
    kr = OkrKeyResult(objective_id=objective_id, **payload)
    kr.updated_at = utcnow()
    db.add(kr)
    db.commit()
    db.refresh(kr)
    return kr


def update_key_result(db: Session, kr_id: int, *, payload: dict[str, Any]) -> OkrKeyResult | None:
    kr = db.get(OkrKeyResult, kr_id)
    if kr is None:
        return None
    for key, value in payload.items():
        setattr(kr, key, value)
    kr.updated_at = utcnow()
    db.add(kr)
    db.commit()
    db.refresh(kr)
    return kr


def delete_key_result(db: Session, kr_id: int) -> bool:
    kr = db.get(OkrKeyResult, kr_id)
    if kr is None:
        return False
    db.delete(kr)
    db.commit()
    return True


def _review_stats(db: Session) -> dict[str, Any]:
    """述职稿的佐证数字：只查本地已有的记录，查不到的字段不进稿。"""
    now = utcnow()
    since = now - timedelta(days=90)
    sessions_finished = db.exec(
        select(func.count()).select_from(InterviewSession).where(
            InterviewSession.status == "finished",
            InterviewSession.created_at >= since,
        )
    ).one()
    avg_overall = db.exec(
        select(func.avg(ScoreReport.overall)).where(ScoreReport.generated_at >= since)
    ).one()
    mastery_topics = db.exec(select(func.count()).select_from(SkillMastery)).one()
    learning_done = db.exec(
        select(func.count()).select_from(LearningItem).where(LearningItem.status == "done")
    ).one()
    stats: dict[str, Any] = {
        "sessions_finished": int(sessions_finished or 0),
        "mastery_topics": int(mastery_topics or 0),
        "learning_done": int(learning_done or 0),
    }
    if avg_overall is not None:
        stats["reports_avg_overall"] = round(float(avg_overall), 1)
    return stats


def review_draft(db: Session, *, title: str = "转正述职") -> dict[str, Any]:
    objectives = list(db.exec(select(OkrObjective).order_by(OkrObjective.sort_order, OkrObjective.id)).all())
    krs = list(db.exec(select(OkrKeyResult).order_by(OkrKeyResult.sort_order, OkrKeyResult.id)).all())
    grouped: dict[int, list[OkrKeyResult]] = {}
    for kr in krs:
        grouped.setdefault(kr.objective_id, []).append(kr)
    draft = build_review_draft(
        objectives,
        grouped,
        stats=_review_stats(db),
        generated_at=utcnow(),
        title=title,
    )
    return {
        "title": draft.title,
        "sections": draft.sections,
        "markdown": draft.markdown,
        "note": draft.note,
    }


# --------------------------------------------------------------------------- #
# 长期规划
# --------------------------------------------------------------------------- #
def list_plan(db: Session) -> list[CareerPlanItem]:
    return list(db.exec(select(CareerPlanItem).order_by(CareerPlanItem.target_date, CareerPlanItem.id)).all())


def create_plan_item(db: Session, *, payload: dict[str, Any]) -> CareerPlanItem:
    item = CareerPlanItem(**payload)
    item.updated_at = utcnow()
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def update_plan_item(db: Session, item_id: int, *, payload: dict[str, Any]) -> CareerPlanItem | None:
    item = db.get(CareerPlanItem, item_id)
    if item is None:
        return None
    for key, value in payload.items():
        setattr(item, key, value)
    item.updated_at = utcnow()
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def delete_plan_item(db: Session, item_id: int) -> bool:
    item = db.get(CareerPlanItem, item_id)
    if item is None:
        return False
    db.delete(item)
    db.commit()
    return True


def list_benchmarks(db: Session) -> list[SalaryBenchmark]:
    return list(db.exec(select(SalaryBenchmark).order_by(SalaryBenchmark.recorded_at.desc())).all())


def create_benchmark(db: Session, *, payload: dict[str, Any]) -> SalaryBenchmark | None:
    if payload.get("range_high", 0) < payload.get("range_low", 0):
        return None
    benchmark = SalaryBenchmark(**payload)
    db.add(benchmark)
    db.commit()
    db.refresh(benchmark)
    return benchmark


def delete_benchmark(db: Session, benchmark_id: int) -> bool:
    benchmark = db.get(SalaryBenchmark, benchmark_id)
    if benchmark is None:
        return False
    db.delete(benchmark)
    db.commit()
    return True


def benchmarks_summary(db: Session) -> dict[str, Any]:
    return _benchmark_summary(list_benchmarks(db))


def timing_overview(db: Session) -> dict[str, Any]:
    """时机自查清单 + 少量本地数据信号。清单是静态的，信号只报事实不下判断。"""
    weakest = db.exec(
        select(SkillMastery).order_by(SkillMastery.mastery).limit(3)
    ).all()
    signals: list[dict[str, Any]] = []
    if weakest:
        signals.append({
            "label": "掌握度最低的能力点（最该先补的短板）",
            "items": [f"{row.mastery:.0f} 分" for row in weakest],
        })
    learning_doing = db.exec(
        select(func.count()).select_from(LearningItem).where(LearningItem.status == "doing")
    ).one()
    if int(learning_doing or 0) > 0:
        signals.append({"label": "进行中的学习项", "items": [f"{int(learning_doing)} 项"]})
    items = timing_checklist()
    return {
        "promotion": [asdict(item) for item in items if item.kind == "promotion"],
        "switch": [asdict(item) for item in items if item.kind == "switch"],
        "signals": signals,
        "note": "清单只提供「该问什么、去哪看数据」；下结论的是你自己。",
    }
