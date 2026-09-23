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


def init_db() -> None:
    """建表 + 准备数据目录（M0 用 create_all，M2 起换 Alembic）。"""
    from . import models  # noqa: F401  导入以注册全部模型

    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.audio_dir.mkdir(parents=True, exist_ok=True)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(get_engine())


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
