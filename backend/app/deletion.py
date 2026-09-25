"""删除编排（M13 补充）：把一场面试、一次作答连带产生的东西清干净。

**为什么单独放一个模块**：删除的难点不在「按 id 删一行」，而在两件事——

1. **顺序**。`PRAGMA foreign_keys=ON` 会拦住违反约束的删除，所以要先删引用方
   （回合、作答、报告、音频登记……）再删会话本身。顺序写错了会直接抛外键错误，
   而且报出来的信息不好懂。
2. **孤儿文件**。音频是存在磁盘上的，只删数据库行会在 `data/audio/` 里攒一堆
   永远没人引用的文件——几个月后没人敢动它们。

还有一条刻意的取舍：**不删 `llm_call`**。那是用量流水，独立于会话存在
（`session_id` 只是索引、没有外键），删掉它等于篡改成本账。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import delete
from sqlmodel import Session, select

from .config import get_settings
from .models.core import QuestionAttempt
from .models.expression import ExpressionSnapshot
from .models.scoring import ImprovementItem, MasteryEvent, ScoreReport
from .models.session import AudioAsset, InterviewSession, SessionTurn


class DeletionError(RuntimeError):
    """要删的东西不存在。"""


def _remove_session_audio(session_id: int) -> int:
    """删掉这场会话的整个音频目录，返回删掉的文件数。

    音频是**按会话分目录**存的（`audio_dir/<session_id>/full.wav`、
    `turn_000.wav`……），所以删目录比逐个文件可靠——不会漏掉某个切片，
    也不会在磁盘上攒下孤儿文件。

    路径由 `session_id` 拼出来、不来自用户输入；再加一道「必须在 data 目录内」
    的检查，免得这个接口变成「删任意目录」。
    """
    settings = get_settings()
    target = settings.audio_dir / str(session_id)
    data_dir = settings.data_dir.resolve()

    try:
        resolved = target.resolve()
    except OSError:
        return 0

    if not resolved.is_dir() or data_dir not in resolved.parents:
        return 0

    removed = len(list(resolved.glob("*.wav")))
    shutil.rmtree(resolved, ignore_errors=True)
    return removed


def _safe_unlink(path_str: str) -> bool:
    """删掉一个文件，**只允许删数据目录内的**。

    作为目录删除的兜底：`AudioAsset.path` 记的是字符串路径，
    万一指向会话目录之外，这条也能覆盖到——同样限制在数据目录内。
    """
    if not path_str:
        return False

    settings = get_settings()
    data_dir = settings.data_dir.resolve()
    candidate = Path(path_str)

    for target in {candidate, data_dir / candidate}:
        try:
            resolved = target.resolve()
        except OSError:
            continue
        if not resolved.is_file() or data_dir not in resolved.parents:
            continue
        try:
            resolved.unlink()
            return True
        except OSError:
            # 正被占用或权限不足：数据库记录已经删了，这里不阻断整体删除
            return False
    return False


def delete_session(db: Session, session_id: int) -> dict[str, int]:
    """删一场面试，连同它的回合、作答、报告、音频登记与音频文件。

    **用 `delete()` 语句而不是 `db.delete(row)`**：后者只是把对象标记成待删除，
    真正执行推迟到 commit，顺序由 SQLAlchemy 自己排——它并不总能排对外键依赖，
    实测会在最后删会话时撞上 `FOREIGN KEY constraint failed`。
    `delete()` 语句是立即执行的，顺序完全由这里决定。
    """
    session_row = db.get(InterviewSession, session_id)
    if session_row is None:
        raise DeletionError(f"没有这场面试：{session_id}")

    turns = list(db.exec(select(SessionTurn).where(SessionTurn.session_id == session_id)).all())
    assets = list(db.exec(select(AudioAsset).where(AudioAsset.session_id == session_id)).all())
    attempts = list(
        db.exec(select(QuestionAttempt).where(QuestionAttempt.session_id == session_id)).all()
    )
    reports = list(db.exec(select(ScoreReport).where(ScoreReport.session_id == session_id)).all())
    snapshots = list(
        db.exec(select(ExpressionSnapshot).where(ExpressionSnapshot.session_id == session_id)).all()
    )
    mastery = list(
        db.exec(select(MasteryEvent).where(MasteryEvent.session_id == session_id)).all()
    )

    # 先删文件：数据库行删掉之后，路径信息就没了
    removed_files = _remove_session_audio(session_id)
    for turn in turns:
        if _safe_unlink(turn.audio_path or ""):
            removed_files += 1
    for asset in assets:
        if _safe_unlink(asset.path or ""):
            removed_files += 1

    # 再按「引用方 → 被引用方」的顺序删：报告的从属表必须在报告之前
    report_ids = [row.id for row in reports if row.id is not None]
    if report_ids:
        db.exec(
            delete(ImprovementItem).where(
                ImprovementItem.report_id.in_(report_ids)  # type: ignore[attr-defined]
            )
        )
    db.exec(delete(ScoreReport).where(ScoreReport.session_id == session_id))
    db.exec(delete(SessionTurn).where(SessionTurn.session_id == session_id))
    db.exec(delete(AudioAsset).where(AudioAsset.session_id == session_id))
    db.exec(delete(QuestionAttempt).where(QuestionAttempt.session_id == session_id))
    db.exec(delete(ExpressionSnapshot).where(ExpressionSnapshot.session_id == session_id))
    db.exec(delete(MasteryEvent).where(MasteryEvent.session_id == session_id))
    db.exec(delete(InterviewSession).where(InterviewSession.id == session_id))
    db.commit()

    return {
        "removed_turns": len(turns),
        "removed_attempts": len(attempts),
        "removed_reports": len(reports),
        "removed_files": removed_files,
    }
