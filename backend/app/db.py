"""SQLite 引擎与会话管理。

设计要点：
- 单文件 SQLite + WAL：单用户本地应用最大的简化武器，不做连接池调优。
- 引擎懒加载，`reset_engine()` 供测试切换数据目录。
- `PRAGMA foreign_keys=ON` 让外键约束真正生效（SQLite 默认关闭）。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from .config import get_settings

_engine: Engine | None = None


def _build_engine(path: Path) -> Engine:
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{path.as_posix()}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, _record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return engine


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = _build_engine(get_settings().database_path)
    return _engine


def reset_engine() -> None:
    """测试用：丢弃当前引擎，下次访问时按最新配置重建。"""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def _add_missing_columns(engine: Engine, table: str, expected: dict[str, str]) -> None:
    """给已存在的表补缺列（幂等）。表不存在时什么都不做。

    **为什么不直接 ALTER**：`PRAGMA table_info(<不存在的表>)` 返回空集而不是报错，
    于是后续的 `ALTER TABLE` 会抛 `no such table`。原来那两处迁移就是这么写的，
    只要调用顺序稍有变化（比如在 `create_all` 之前跑），应用就起不来。
    迁移代码**出问题不该让应用启动失败**——这是"能启动"比"迁移完美"更重要的地方，
    所以这里既查表存在性，又把每个 ALTER 单独兜住。
    """
    from sqlalchemy.exc import SQLAlchemyError
    from sqlmodel import Session, text

    with Session(engine) as session:
        exists = session.exec(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name=:name"),  # type: ignore[call-arg]
            params={"name": table},
        ).first()
        if exists is None:
            return

        existing = {row[1] for row in session.exec(text(f"PRAGMA table_info({table})")).all()}
        for column, ddl in expected.items():
            if column in existing:
                continue
            try:
                session.exec(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            except SQLAlchemyError:
                # 单个列补不上（例如 SQLite 不允许的 REFERENCES 列）不该拖垮启动；
                # 缺这一列的后果是那个功能不可用，而不是整个应用不可用。
                session.rollback()
                continue
        session.commit()


def _ensure_resume_columns(engine: Engine) -> None:
    """create_all 只建新表，不会给已存在的表加列。

    现有用户库的 resume 表建于 source/file_name/file_path 字段加入之前，
    这里查缺列并 ALTER 补上（SQLite 的 ADD COLUMN 幂等、不丢数据）。
    """
    _add_missing_columns(
        engine,
        "resume",
        {
            "source": "VARCHAR NOT NULL DEFAULT 'manual'",
            "file_name": "VARCHAR NOT NULL DEFAULT ''",
            "file_path": "VARCHAR NOT NULL DEFAULT ''",
        },
    )


def _ensure_apply_columns(engine: Engine) -> None:
    """给 apply_task 补 M17/M18 新增的列（旧库建于这些列之前）。

    `profile_id` 带外键：SQLite 的 `ADD COLUMN` **不允许**加带 REFERENCES 的列
    （"Cannot add a REFERENCES column with non-NULL default value"）。
    所以这里只加普通整数列。代价是失去数据库层的引用完整性，但多账号是可选能力，
    而"让旧库能平滑升级"更重要——SQLModel 建的新库仍然带外键约束。
    """
    _add_missing_columns(
        engine,
        "apply_task",
        {
            "greeted_at": "DATETIME",
            "profile_id": "INTEGER",
        },
    )


def _ensure_question_bank_columns(engine: Engine) -> None:
    """给 question_bank 补题型列（旧库建于这些字段加入之前）。

    选择题功能只影响新生成的题；旧题全部按问答题处理（question_type 默认 open），
    照常参与抽题，行为不变。
    """
    _add_missing_columns(
        engine,
        "question_bank",
        {
            "question_type": "VARCHAR NOT NULL DEFAULT 'open'",
            "choice_options": "JSON",
            "correct_index": "INTEGER",
            "explanation": "TEXT",
        },
    )


def init_db() -> None:
    """建表 + 准备数据目录（M0 用 create_all，M2 起换 Alembic）。"""
    from . import models  # noqa: F401  导入以注册全部模型

    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.audio_dir.mkdir(parents=True, exist_ok=True)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    engine = get_engine()
    SQLModel.metadata.create_all(engine)
    _ensure_resume_columns(engine)
    _ensure_apply_columns(engine)
    _ensure_question_bank_columns(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    # expire_on_commit=False：commit 后仍可读已加载的属性，
    # 这样"落库（短事务）→ 调 LLM → 再落库"的两段式流程不必反复 refresh，
    # 也不会在事务外访问属性时炸 DetachedInstanceError。
    with Session(get_engine(), expire_on_commit=False) as session:
        yield session


def get_session() -> Iterator[Session]:
    """FastAPI 依赖。"""
    with Session(get_engine(), expire_on_commit=False) as session:
        yield session
