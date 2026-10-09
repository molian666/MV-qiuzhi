"""投递数据编排：导入落库、状态流转、统计装配。

纯逻辑在 `analysis/apply_import.py`（解析）与 `analysis/apply_stats.py`（统计），
这里只负责 I/O 与"导入时该怎么处理已存在的记录"这类业务规则。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlmodel import Session, select

from .analysis.apply_import import ParseResult, ParsedRow, normalize_channel, parse_csv_text, parse_table_rows
from .analysis.apply_stats import ApplicationSnapshot, overview, stage_of, status_rank
from .analysis.rejection import blacklist_candidates
from .clock import utcnow
from .models.apply import (
    APPLICATION_STATUSES,
    TERMINAL_STATUSES,
    Application,
    ApplicationEvent,
    Blacklist,
    ImportBatch,
)


class ApplyImportError(RuntimeError):
    """导入失败（没有内容、没有可识别的列等）。"""


@dataclass(slots=True)
class ImportOutcome:
    batch_id: int
    source: str
    rows: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    matched_columns: dict[str, str] = field(default_factory=dict)
    unmatched_headers: list[str] = field(default_factory=list)
    skipped_details: list[tuple[int, str]] = field(default_factory=list)
    note: str = ""


# --------------------------------------------------------------------------- #
# 状态流转
# --------------------------------------------------------------------------- #
def should_update_status(current: str, incoming: str) -> bool:
    """导入的旧数据不该把更靠后的真实状态覆盖回去。

    规则：
    - 状态没变 → 不动；
    - 新状态是 offer → 一定是更新的真相（被捞回也算）；
    - 当前已是终止态（被拒/无响应/撤回）→ 不再被旧数据改写；
    - 其余情况只在"进度更靠后"时更新。
    """
    if current == incoming or not incoming:
        return False
    if incoming == "offer":
        return True
    if current in TERMINAL_STATUSES:
        return False
    return status_rank(incoming) > status_rank(current)


def change_status(
    db: Session,
    *,
    application_id: int,
    to_status: str,
    raw_ref: str = "手动",
    at=None,
) -> Application:
    application = db.get(Application, application_id)
    if application is None:
        raise LookupError(f"投递记录不存在：{application_id}")
    if to_status not in APPLICATION_STATUSES:
        raise ApplyImportError(f"未知状态：{to_status}")

    moment = at or utcnow()
    previous = application.status
    application.status = to_status
    db.add(application)
    db.add(
        ApplicationEvent(
            application_id=application_id,
            at=moment,
            from_status=previous,
            to_status=to_status,
            raw_ref=raw_ref,
        )
    )
    db.commit()
    db.refresh(application)
    return application


# --------------------------------------------------------------------------- #
# 导入
# --------------------------------------------------------------------------- #
def _persist(db: Session, *, parsed: ParseResult, source: str, channel_hint: str) -> ImportOutcome:
    inserted = updated = skipped = 0

    for row in parsed.rows:
        key = row.dedup_key
        existing = db.exec(select(Application).where(Application.dedup_key == key)).first()
        if existing is not None:
            if should_update_status(existing.status, row.status):
                # 用"导入时刻"作为事件时间：我们并不知道真实变化时间，
                # 拿投递时间当变化时间会让响应时长统计全部变成 0
                change_status(
                    db,
                    application_id=int(existing.id or 0),
                    to_status=row.status,
                    raw_ref=f"导入更新（{source}）",
                )
                updated += 1
            else:
                skipped += 1
            continue

        application = Application(
            company=row.company,
            title=row.title,
            channel=row.channel or channel_hint,
            applied_at=row.applied_at,
            status=row.status,
            salary_range=row.salary_range,
            city=row.city,
            resume_version=row.resume_version,
            jd_url=row.jd_url,
            note=row.note,
            source=source,
            dedup_key=key,
        )
        db.add(application)
        db.commit()
        db.refresh(application)

        db.add(
            ApplicationEvent(
                application_id=int(application.id or 0),
                at=row.applied_at,
                from_status="",
                to_status=row.status,
                raw_ref=f"导入（{source}）",
            )
        )
        db.commit()
        inserted += 1

    batch = ImportBatch(
        channel=channel_hint,
        source=source,
        rows=len(parsed.rows),
        inserted=inserted,
        updated=updated,
        skipped=skipped,
        dedup_key_hash="",
        note="；".join(f"第{line}行：{reason}" for line, reason in parsed.skipped[:10]),
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)

    return ImportOutcome(
        batch_id=int(batch.id or 0),
        source=source,
        rows=len(parsed.rows),
        inserted=inserted,
        updated=updated,
        skipped=skipped,
        matched_columns=parsed.matched_columns,
        unmatched_headers=parsed.unmatched_headers,
        skipped_details=parsed.skipped,
    )


def import_csv(
    db: Session,
    *,
    text: str,
    source: str = "csv",
    channel_hint: str = "other",
    default_year: int | None = None,
) -> ImportOutcome:
    parsed = parse_csv_text(text, default_channel=channel_hint, default_year=default_year)
    if not parsed.rows:
        raise ApplyImportError(
            "没有解析出任何投递记录。请确认第一行是表头（例如：公司,岗位,渠道,状态,投递时间），"
            "且至少有一行数据。"
        )
    return _persist(db, parsed=parsed, source=source, channel_hint=channel_hint)


def import_raw_rows(
    db: Session,
    *,
    rows: list[dict[str, Any]],
    source: str = "extension",
    platform: str = "",
) -> ImportOutcome:
    """导入"表头 + 行"结构的数据（浏览器扩展抓页面用同一套解析）。"""
    if not rows:
        raise ApplyImportError("没有收到任何投递记录")

    headers: list[str] = []
    for row in rows:
        for key in row:
            if key not in headers:
                headers.append(str(key))
    if not headers:
        raise ApplyImportError("收到的行里没有任何字段")

    body = [[str(row.get(header, "") or "") for header in headers] for row in rows]
    channel_hint = normalize_channel(platform, default="other")
    parsed = parse_table_rows(headers, body, default_channel=channel_hint)

    ignored = {"company", "title", "applied_at"}
    matched = parsed.matched_columns
    if not any(key in matched for key in ignored):
        raise ApplyImportError(
            f"没能从这些字段里认出公司和岗位：{headers[:8]}。"
            "请在扩展里调整字段映射（至少要有「公司」与「岗位」）。"
        )

    return _persist(db, parsed=parsed, source=source, channel_hint=channel_hint)


# --------------------------------------------------------------------------- #
# 统计装配
# --------------------------------------------------------------------------- #
def _snapshot(application: Application, events: list[ApplicationEvent]) -> ApplicationSnapshot:
    max_stage = stage_of(application.status)
    best_index = _stage_index(max_stage)
    first_response = None

    for event in sorted(events, key=lambda item: item.at):
        stage = stage_of(event.to_status)
        index = _stage_index(stage)
        if index > best_index:
            best_index = index
            max_stage = stage
        # 只有"发生在投递之后"的状态变化才算回应：
        # 导入时我们会补写一条"当前状态"事件，它的时间就是投递时间；
        # 不排除它的话，所有导入记录的响应时长都会变成 0。
        if (
            first_response is None
            and event.to_status
            and event.to_status != "applied"
            and event.at > application.applied_at
        ):
            first_response = event.at

    return ApplicationSnapshot(
        id=int(application.id or 0),
        company=application.company,
        title=application.title,
        channel=application.channel,
        status=application.status,
        applied_at=application.applied_at,
        resume_version=application.resume_version,
        max_stage=max_stage,
        first_response_at=first_response,
    )


def _stage_index(stage: str) -> int:
    order = ["applied", "viewed", "interview", "offer"]
    try:
        return order.index(stage)
    except ValueError:
        return 0


def snapshots(db: Session) -> list[ApplicationSnapshot]:
    applications = list(
        db.exec(select(Application).order_by(Application.applied_at)).all()  # type: ignore[arg-type]
    )
    if not applications:
        return []

    ids = [int(app.id) for app in applications if app.id is not None]
    grouped: dict[int, list[ApplicationEvent]] = {identifier: [] for identifier in ids}
    if ids:
        for event in db.exec(
            select(ApplicationEvent).where(ApplicationEvent.application_id.in_(ids))  # type: ignore[attr-defined]
        ).all():
            grouped.setdefault(int(event.application_id), []).append(event)

    return [_snapshot(app, grouped.get(int(app.id or 0), [])) for app in applications]


def statistics(db: Session, *, weeks: int = 8) -> dict:
    return overview(snapshots(db), weeks=weeks)


# --------------------------------------------------------------------------- #
# 自动拉黑（能力：autofill_blacklist）
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class RejectionScanOutcome:
    """一次扫描的结果。`candidates` 带依据，便于在界面上核对。"""

    scanned: int = 0
    added: int = 0
    candidates: list[dict[str, str]] = field(default_factory=list)


def _rejection_texts(db: Session, application: Application) -> list[str]:
    """这家公司目前留存的、可能含拒绝信号的文本。

    **现在只有两条来源**：应用记录的备注、以及状态流转里的线索（`raw_ref` 会存
    邮件主题 / 页面摘要这类原文）。沟通记录的抓取接上之后再补一条即可——
    判定逻辑本身在 `analysis/rejection.py`，与来源无关。
    """
    texts: list[str] = []
    if application.note:
        texts.append(application.note)

    events = db.exec(
        select(ApplicationEvent).where(ApplicationEvent.application_id == application.id)
    ).all()
    for event in events:
        if event.raw_ref:
            texts.append(event.raw_ref)
    return texts


def autofill_blacklist(
    db: Session,
    *,
    platform: str = "",
    dry_run: bool = False,
) -> RejectionScanOutcome:
    """扫描已有投递记录，把识别出"明确拒绝"的公司加进黑名单。

    判据刻意保守（见 `analysis/rejection.py`）：**误判一次会让用户永远看不到一家
    本来有戏的公司**，所以只认明确拒绝与岗位关闭，不认"等通知"这类模糊表述。

    `dry_run=True` 只返回会被拉黑的公司与依据、**不写库**——先让用户看一眼再决定，
    比默默拉黑一批公司安全得多。
    """
    applications = list(db.exec(select(Application)).all())
    blacklisted_names = {
        str(row.company).strip().lower()
        for row in db.exec(select(Blacklist)).all()
        if row.company
    }

    rows: list[tuple[str, str, list[str]]] = [
        (
            application.company,
            platform or application.channel or "",
            _rejection_texts(db, application),
        )
        for application in applications
    ]

    found = blacklist_candidates(rows, blacklisted=blacklisted_names)
    outcome = RejectionScanOutcome(
        scanned=len(applications),
        candidates=[
            {
                "company": item.company,
                "platform": item.platform,
                "reason": item.reason,
                "evidence": item.evidence,
            }
            for item in found
        ],
    )
    if dry_run or not found:
        return outcome

    for item in found:
        db.add(Blacklist(platform=item.platform, company=item.company, reason=item.reason))
    db.commit()
    outcome.added = len(found)
    return outcome
