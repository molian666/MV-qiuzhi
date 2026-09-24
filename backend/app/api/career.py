"""入职与长期 API（M15）：入职清单（可勾选追踪）、试用期 OKR、转正述职、长期规划与薪资基准。

路径命名上避开「固定路径被 /{id} 抢走」的坑：
/onboarding/progress、/okr/review、/benchmarks/summary 都不带 {id} 前缀。
"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from ..analysis.okr import ObjectiveView, build_objective_view
from ..deps import get_session
from ..career_service import (
    add_key_result,
    benchmarks_summary,
    create_benchmark,
    create_objective,
    create_onboarding_task,
    create_plan_item,
    delete_benchmark,
    delete_key_result,
    delete_objective,
    delete_onboarding_task,
    delete_plan_item,
    list_benchmarks,
    list_okr,
    list_onboarding,
    list_plan,
    review_draft,
    set_onboarding_done,
    timing_overview,
    update_key_result,
    update_objective,
    update_onboarding_task,
    update_plan_item,
)
from ..models.career import (
    OKR_STATUSES,
    OKR_STATUS_LABELS,
    ONBOARDING_CATEGORIES,
    PLAN_KINDS,
    PLAN_KIND_LABELS,
    PLAN_STATUSES,
    PLAN_STATUS_LABELS,
    OkrObjective,
)
from ..schemas.career import (
    BenchmarkIn,
    BenchmarkOut,
    BenchmarkSummaryOut,
    CareerOptionsOut,
    KeyResultIn,
    KeyResultProgressIn,
    KrViewOut,
    ObjectiveIn,
    ObjectiveViewOut,
    OnboardingCheckIn,
    OnboardingOut,
    OnboardingTaskIn,
    OnboardingTaskOut,
    PlanItemIn,
    PlanItemOut,
    ReviewDraftOut,
    SeedOut,
    TimingOut,
)
from ..clock import utcnow

router = APIRouter(tags=["career"])


def _iso(value: date | datetime | None) -> str | None:
    """日期与时间都要能输出。计划日期是 date（没有时刻），其余是 aware datetime。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(value.tzinfo).isoformat(timespec="seconds")
    return value.isoformat()


def _objective_out(view: ObjectiveView) -> ObjectiveViewOut:
    return ObjectiveViewOut(
        id=view.id,
        title=view.title,
        period=view.period,
        note=view.note,
        status=view.status,
        status_label=view.status_label,
        sort_order=view.sort_order,
        pct=view.pct,
        kr_done=view.kr_done,
        krs=[KrViewOut(
            id=kr.id,
            title=kr.title,
            unit=kr.unit,
            target_value=kr.target_value,
            current_value=kr.current_value,
            confidence=kr.confidence,
            sort_order=kr.sort_order,
            pct=kr.pct,
            status=kr.status,
            updated_at=_iso(kr.updated_at),
        ) for kr in view.krs],
    )


# --------------------------------------------------------------------------- #
# 入职清单
# --------------------------------------------------------------------------- #
@router.get("/api/onboarding", response_model=OnboardingOut, summary="入职清单（含进度；首次访问自动种入默认目录）")
def read_onboarding(db: Session = Depends(get_session)) -> OnboardingOut:
    payload = list_onboarding(db)
    return OnboardingOut(
        tasks=[OnboardingTaskOut.model_validate(task, from_attributes=True) for task in payload["tasks"]],
        progress=payload["progress"],  # type: ignore[arg-type]
    )


@router.post("/api/onboarding", response_model=OnboardingTaskOut, status_code=201, summary="新增清单项")
def create_onboarding(payload: OnboardingTaskIn, db: Session = Depends(get_session)) -> OnboardingTaskOut:
    if not payload.title.strip():
        raise HTTPException(status_code=422, detail="事项标题是必填的")
    task = create_onboarding_task(db, payload=payload.model_dump(exclude_none=True))
    return OnboardingTaskOut.model_validate(task, from_attributes=True)


@router.put("/api/onboarding/{task_id}", response_model=OnboardingTaskOut, summary="修改清单项")
def update_onboarding(
    task_id: int, payload: OnboardingTaskIn, db: Session = Depends(get_session)
) -> OnboardingTaskOut:
    task = update_onboarding_task(db, task_id, payload=payload.model_dump(exclude_none=True))
    if task is None:
        raise HTTPException(status_code=404, detail=f"清单项不存在：{task_id}")
    return OnboardingTaskOut.model_validate(task, from_attributes=True)


@router.put("/api/onboarding/{task_id}/check", response_model=OnboardingTaskOut, summary="勾选 / 取消勾选")
def check_onboarding(
    task_id: int, payload: OnboardingCheckIn, db: Session = Depends(get_session)
) -> OnboardingTaskOut:
    task = set_onboarding_done(db, task_id, done=payload.done)
    if task is None:
        raise HTTPException(status_code=404, detail=f"清单项不存在：{task_id}")
    return OnboardingTaskOut.model_validate(task, from_attributes=True)


@router.delete("/api/onboarding/{task_id}", status_code=204, summary="删除清单项")
def remove_onboarding(task_id: int, db: Session = Depends(get_session)) -> None:
    if not delete_onboarding_task(db, task_id):
        raise HTTPException(status_code=404, detail=f"清单项不存在：{task_id}")


@router.post("/api/onboarding/seed", response_model=SeedOut, summary="重新种入默认目录（跳过已有项）")
def seed_onboarding(db: Session = Depends(get_session)) -> SeedOut:
    from ..career_service import ensure_defaults

    return SeedOut(added=ensure_defaults(db))


# --------------------------------------------------------------------------- #
# 试用期 OKR
# --------------------------------------------------------------------------- #
@router.get("/api/okr", response_model=list[ObjectiveViewOut], summary="试用期 OKR（含进展计算）")
def read_okr(db: Session = Depends(get_session)) -> list[ObjectiveViewOut]:
    return [_objective_out(view) for view in list_okr(db)]


@router.post("/api/okr/objectives", response_model=ObjectiveViewOut, status_code=201, summary="新增目标")
def create_obj(payload: ObjectiveIn, db: Session = Depends(get_session)) -> ObjectiveViewOut:
    if not payload.title.strip():
        raise HTTPException(status_code=422, detail="目标标题是必填的")
    payload_data = payload.model_dump(exclude_none=True)
    if payload_data.get("status") and payload_data["status"] not in OKR_STATUSES:
        raise HTTPException(status_code=422, detail=f"status 必须是 {OKR_STATUSES}")
    objective = create_objective(db, payload=payload_data)
    return _objective_out(build_objective_view(objective, []))


@router.put("/api/okr/objectives/{objective_id}", response_model=ObjectiveViewOut, summary="修改目标")
def update_obj(
    objective_id: int, payload: ObjectiveIn, db: Session = Depends(get_session)
) -> ObjectiveViewOut:
    data = payload.model_dump(exclude_none=True)
    if data.get("status") and data["status"] not in OKR_STATUSES:
        raise HTTPException(status_code=422, detail=f"status 必须是 {OKR_STATUSES}")
    objective = update_objective(db, objective_id, payload=data)
    if objective is None:
        raise HTTPException(status_code=404, detail=f"目标不存在：{objective_id}")
    return _objective_out(build_objective_view(objective, _krs_of(db, objective_id)))


@router.delete("/api/okr/objectives/{objective_id}", status_code=204, summary="删除目标（连同其关键结果）")
def remove_obj(objective_id: int, db: Session = Depends(get_session)) -> None:
    if not delete_objective(db, objective_id):
        raise HTTPException(status_code=404, detail=f"目标不存在：{objective_id}")


@router.post("/api/okr/objectives/{objective_id}/krs", response_model=ObjectiveViewOut, status_code=201,
             summary="给目标加一条关键结果")
def create_kr(
    objective_id: int, payload: KeyResultIn, db: Session = Depends(get_session)
) -> ObjectiveViewOut:
    kr = add_key_result(db, objective_id, payload=payload.model_dump(exclude_none=True))
    if kr is None:
        raise HTTPException(status_code=404, detail=f"目标不存在：{objective_id}")
    objective = db.get(OkrObjective, objective_id)
    if objective is None:
        raise HTTPException(status_code=404, detail=f"目标不存在：{objective_id}")
    return _objective_out(build_objective_view(objective, _krs_of(db, objective_id)))


@router.put("/api/okr/krs/{kr_id}", response_model=KrViewOut, summary="更新关键结果（记进展）")
def update_kr(kr_id: int, payload: KeyResultIn, db: Session = Depends(get_session)) -> KrViewOut:
    kr = update_key_result(db, kr_id, payload=payload.model_dump(exclude_none=True))
    if kr is None:
        raise HTTPException(status_code=404, detail=f"关键结果不存在：{kr_id}")
    from ..analysis.okr import build_kr_view

    view = build_kr_view(kr)
    return KrViewOut(
        id=view.id, title=view.title, unit=view.unit, target_value=view.target_value,
        current_value=view.current_value, confidence=view.confidence, sort_order=view.sort_order,
        pct=view.pct, status=view.status, updated_at=_iso(view.updated_at),
    )


@router.patch("/api/okr/krs/{kr_id}/progress", response_model=KrViewOut, summary="只更新进展与信心")
def update_kr_progress(
    kr_id: int, payload: KeyResultProgressIn, db: Session = Depends(get_session)
) -> KrViewOut:
    data: dict = {"current_value": payload.current_value}
    if payload.confidence is not None:
        data["confidence"] = payload.confidence
    kr = update_key_result(db, kr_id, payload=data)
    if kr is None:
        raise HTTPException(status_code=404, detail=f"关键结果不存在：{kr_id}")
    from ..analysis.okr import build_kr_view

    view = build_kr_view(kr)
    return KrViewOut(
        id=view.id, title=view.title, unit=view.unit, target_value=view.target_value,
        current_value=view.current_value, confidence=view.confidence, sort_order=view.sort_order,
        pct=view.pct, status=view.status, updated_at=_iso(view.updated_at),
    )


@router.delete("/api/okr/krs/{kr_id}", status_code=204, summary="删除关键结果")
def remove_kr(kr_id: int, db: Session = Depends(get_session)) -> None:
    if not delete_key_result(db, kr_id):
        raise HTTPException(status_code=404, detail=f"关键结果不存在：{kr_id}")


@router.get("/api/okr/review", response_model=ReviewDraftOut, summary="转正述职稿（由 OKR 与本地记录确定性合成）")
def read_review(db: Session = Depends(get_session)) -> ReviewDraftOut:
    return ReviewDraftOut(**review_draft(db))


def _krs_of(db: Session, objective_id: int) -> list:
    from sqlmodel import select as _select

    from ..models.career import OkrKeyResult

    return list(db.exec(_select(OkrKeyResult).where(OkrKeyResult.objective_id == objective_id)).all())


# --------------------------------------------------------------------------- #
# 长期规划与薪资基准
# --------------------------------------------------------------------------- #
@router.get("/api/career-plan", response_model=list[PlanItemOut], summary="长期规划条目")
def read_plan(db: Session = Depends(get_session)) -> list[PlanItemOut]:
    return [PlanItemOut.model_validate(item, from_attributes=True) for item in list_plan(db)]


@router.post("/api/career-plan", response_model=PlanItemOut, status_code=201, summary="新增长期规划条目")
def create_plan(payload: PlanItemIn, db: Session = Depends(get_session)) -> PlanItemOut:
    if not payload.title.strip():
        raise HTTPException(status_code=422, detail="事项标题是必填的")
    data = payload.model_dump(exclude_none=True)
    if data.get("status") and data["status"] not in PLAN_STATUSES:
        raise HTTPException(status_code=422, detail=f"status 必须是 {PLAN_STATUSES}")
    if data.get("kind") and data["kind"] not in PLAN_KINDS:
        raise HTTPException(status_code=422, detail=f"kind 必须是 {PLAN_KINDS}")
    item = create_plan_item(db, payload=data)
    return PlanItemOut(
        id=item.id or 0, kind=item.kind, title=item.title, detail=item.detail,
        target_date=_iso(item.target_date), status=item.status, updated_at=_iso(item.updated_at),
    )


@router.put("/api/career-plan/{item_id}", response_model=PlanItemOut, summary="修改长期规划条目")
def update_plan(item_id: int, payload: PlanItemIn, db: Session = Depends(get_session)) -> PlanItemOut:
    data = payload.model_dump(exclude_none=True)
    item = update_plan_item(db, item_id, payload=data)
    if item is None:
        raise HTTPException(status_code=404, detail=f"规划条目不存在：{item_id}")
    return PlanItemOut(
        id=item.id or 0, kind=item.kind, title=item.title, detail=item.detail,
        target_date=_iso(item.target_date), status=item.status, updated_at=_iso(item.updated_at),
    )


@router.delete("/api/career-plan/{item_id}", status_code=204, summary="删除长期规划条目")
def remove_plan(item_id: int, db: Session = Depends(get_session)) -> None:
    if not delete_plan_item(db, item_id):
        raise HTTPException(status_code=404, detail=f"规划条目不存在：{item_id}")


@router.get("/api/benchmarks", response_model=list[BenchmarkOut], summary="薪资基准记录（只存来源与区间）")
def read_benchmarks(db: Session = Depends(get_session)) -> list[BenchmarkOut]:
    rows = list_benchmarks(db)
    return [BenchmarkOut(
        id=row.id or 0, company=row.company, title=row.title, city=row.city,
        range_low=row.range_low, range_high=row.range_high, currency=row.currency,
        period=row.period, source=row.source, source_url=row.source_url,
        verified=row.verified, note=row.note, recorded_at=_iso(row.recorded_at),
    ) for row in rows]


@router.post("/api/benchmarks", response_model=BenchmarkOut, status_code=201, summary="记一条薪资基准")
def create_bench(payload: BenchmarkIn, db: Session = Depends(get_session)) -> BenchmarkOut:
    if not payload.source.strip():
        raise HTTPException(status_code=422, detail="来源是必填的——没有来源的薪资数字不可信")
    row = create_benchmark(db, payload=payload.model_dump())
    if row is None:
        raise HTTPException(status_code=422, detail="区间上限不能小于下限")
    return BenchmarkOut(
        id=row.id or 0, company=row.company, title=row.title, city=row.city,
        range_low=row.range_low, range_high=row.range_high, currency=row.currency,
        period=row.period, source=row.source, source_url=row.source_url,
        verified=row.verified, note=row.note, recorded_at=_iso(row.recorded_at),
    )


@router.delete("/api/benchmarks/{benchmark_id}", status_code=204, summary="删除薪资基准")
def remove_bench(benchmark_id: int, db: Session = Depends(get_session)) -> None:
    if not delete_benchmark(db, benchmark_id):
        raise HTTPException(status_code=404, detail=f"薪资基准不存在：{benchmark_id}")


@router.get("/api/benchmarks/summary", response_model=BenchmarkSummaryOut, summary="薪资基准汇总（仅供参考）")
def read_benchmark_summary(db: Session = Depends(get_session)) -> BenchmarkSummaryOut:
    return BenchmarkSummaryOut(**benchmarks_summary(db))


@router.get("/api/career/timing", response_model=TimingOut, summary="晋升/跳槽时机自查清单")
def read_timing(db: Session = Depends(get_session)) -> TimingOut:
    payload = timing_overview(db)
    return TimingOut(
        promotion=payload["promotion"],  # type: ignore[arg-type]
        switch=payload["switch"],  # type: ignore[arg-type]
        signals=payload["signals"],  # type: ignore[arg-type]
        note=payload["note"],
    )


@router.get("/api/career/options", response_model=CareerOptionsOut, summary="入职与长期的选项")
def read_options() -> CareerOptionsOut:
    return CareerOptionsOut(
        categories=list(ONBOARDING_CATEGORIES),
        okr_statuses=[{"key": key, "label": OKR_STATUS_LABELS[key]} for key in OKR_STATUSES],
        plan_kinds=[{"key": key, "label": PLAN_KIND_LABELS[key]} for key in PLAN_KINDS],
        plan_statuses=[{"key": key, "label": PLAN_STATUS_LABELS[key]} for key in PLAN_STATUSES],
    )
