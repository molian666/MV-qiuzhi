"""投递数据 API（M6）：导入、清单、状态流转、漏斗统计、扩展 ingest。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from ..apply_service import (
    ApplyImportError,
    ImportOutcome,
    change_status,
    import_csv,
    import_raw_rows,
    statistics,
)
from ..clock import utcnow
from ..deploy import require_local_run
from ..deps import get_session
from ..models.apply import APPLICATION_STATUSES, Application, ApplicationEvent, ImportBatch
from ..schemas.apply import (
    ApplicationCreate,
    ApplicationOut,
    ImportBatchOut,
    ImportIn,
    ImportOutcomeOut,
    IngestIn,
    StatusChangeIn,
)

router = APIRouter(prefix="/api/applications", tags=["applications"])


def _outcome(outcome: ImportOutcome) -> ImportOutcomeOut:
    return ImportOutcomeOut(
        batch_id=outcome.batch_id,
        source=outcome.source,
        rows=outcome.rows,
        inserted=outcome.inserted,
        updated=outcome.updated,
        skipped=outcome.skipped,
        matched_columns=outcome.matched_columns,
        unmatched_headers=outcome.unmatched_headers,
        skipped_details=outcome.skipped_details,
        note=outcome.note,
    )


@router.post("/import", response_model=ImportOutcomeOut, summary="导入 CSV/TSV 文本")
def import_applications(payload: ImportIn, db: Session = Depends(get_session)) -> ImportOutcomeOut:
    try:
        outcome = import_csv(
            db,
            text=payload.text,
            source="csv",
            channel_hint=payload.channel,
            default_year=payload.default_year,
        )
    except ApplyImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _outcome(outcome)


@router.post(
    "/ingest",
    response_model=ImportOutcomeOut,
    dependencies=[Depends(require_local_run)],
    summary="浏览器扩展推送投递记录（仅本机）",
)
def ingest_applications(payload: IngestIn, db: Session = Depends(get_session)) -> ImportOutcomeOut:
    try:
        outcome = import_raw_rows(db, rows=payload.rows, source="extension", platform=payload.platform)
    except ApplyImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _outcome(outcome)


@router.get("/stats", summary="投递统计（漏斗 / 渠道 / 周趋势 / 简历版本 / 响应时长 / 待跟进）")
def read_stats(weeks: int = Query(default=8, ge=1, le=52), db: Session = Depends(get_session)) -> dict[str, Any]:
    return statistics(db, weeks=weeks)


@router.get("", response_model=list[ApplicationOut], summary="投递清单")
def list_applications(
    channel: str | None = None,
    status: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_session),
) -> list[Application]:
    statement = select(Application)
    if channel:
        statement = statement.where(Application.channel == channel)
    if status:
        statement = statement.where(Application.status == status)
    statement = (
        statement.order_by(Application.applied_at.desc())  # type: ignore[attr-defined]
        .offset(offset)
        .limit(limit)
    )
    return list(db.exec(statement).all())


@router.post("", response_model=ApplicationOut, status_code=201, summary="手动新增一条投递")
def create_application(payload: ApplicationCreate, db: Session = Depends(get_session)) -> Application:
    if payload.status not in APPLICATION_STATUSES:
        raise HTTPException(status_code=400, detail=f"未知状态：{payload.status}")

    from ..analysis.apply_import import make_dedup_key

    applied_at = payload.applied_at or utcnow()
    application = Application(
        **payload.model_dump(exclude={"applied_at"}),
        applied_at=applied_at,
        source="manual",
        dedup_key=make_dedup_key(payload.company, payload.title, applied_at),
    )
    db.add(application)
    db.commit()
    db.refresh(application)

    db.add(
        ApplicationEvent(
            application_id=int(application.id or 0),
            at=applied_at,
            from_status="",
            to_status=payload.status,
            raw_ref="手动新增",
        )
    )
    db.commit()
    return application


@router.post("/{application_id}/status", response_model=ApplicationOut, summary="更新投递状态")
def update_status(
    application_id: int,
    payload: StatusChangeIn,
    db: Session = Depends(get_session),
) -> Application:
    try:
        return change_status(
            db,
            application_id=application_id,
            to_status=payload.to_status,
            raw_ref=payload.raw_ref,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ApplyImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{application_id}", status_code=204, summary="删除投递（连带事件）")
def delete_application(application_id: int, db: Session = Depends(get_session)) -> None:
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail=f"投递记录不存在：{application_id}")

    from sqlalchemy import delete as sql_delete

    db.exec(sql_delete(ApplicationEvent).where(ApplicationEvent.application_id == application_id))
    db.delete(application)
    db.commit()


@router.get("/imports", response_model=list[ImportBatchOut], summary="导入流水")
def list_imports(
    limit: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_session),
) -> list[ImportBatch]:
    statement = (
        select(ImportBatch)
        .order_by(ImportBatch.id.desc())  # type: ignore[attr-defined]
        .limit(limit)
    )
    return list(db.exec(statement).all())
