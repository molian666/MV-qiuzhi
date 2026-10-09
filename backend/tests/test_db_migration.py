"""数据库迁移：给已有库补列。

项目没有 Alembic（M2 的工程债），所以"给旧库加列"全靠 `db._add_missing_columns`。
这段代码出问题的后果特别严重——**应用直接起不来**，用户只会看到一堆 traceback，
而他的数据就在库里。所以这里把几种真实情形都钉住。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import create_engine
from sqlmodel import Session, text

from app import db as db_module


def _engine_for(path: Path):
    return create_engine(f"sqlite:///{path.as_posix()}")


def _columns(engine, table: str) -> set[str]:
    with Session(engine) as session:
        return {row[1] for row in session.exec(text(f"PRAGMA table_info({table})")).all()}


# --------------------------------------------------------------------------- #
# 补列
# --------------------------------------------------------------------------- #
def test_adds_missing_columns_to_an_existing_table(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE apply_task (id INTEGER PRIMARY KEY, status VARCHAR)")
    con.commit()
    con.close()

    engine = _engine_for(path)
    db_module._add_missing_columns(engine, "apply_task", {"greeted_at": "DATETIME"})

    assert "greeted_at" in _columns(engine, "apply_task")


def test_is_idempotent(tmp_path: Path) -> None:
    """跑两次不能报错（每次启动都会跑一遍）。"""
    path = tmp_path / "old.db"
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE apply_task (id INTEGER PRIMARY KEY)")
    con.commit()
    con.close()

    engine = _engine_for(path)
    for _ in range(3):
        db_module._add_missing_columns(engine, "apply_task", {"profile_id": "INTEGER"})

    assert "profile_id" in _columns(engine, "apply_task")


def test_preserves_existing_rows(tmp_path: Path) -> None:
    """**补列绝不能丢数据。**"""
    path = tmp_path / "old.db"
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE apply_task (id INTEGER PRIMARY KEY, status VARCHAR)")
    con.execute("INSERT INTO apply_task (id, status) VALUES (1, 'pending'), (2, 'sent')")
    con.commit()
    con.close()

    engine = _engine_for(path)
    db_module._add_missing_columns(engine, "apply_task", {"profile_id": "INTEGER"})

    with Session(engine) as session:
        rows = list(session.exec(text("SELECT id, status FROM apply_task ORDER BY id")).all())
    assert rows == [(1, "pending"), (2, "sent")]


# --------------------------------------------------------------------------- #
# 表不存在时必须安静跳过（否则应用起不来）
# --------------------------------------------------------------------------- #
def test_missing_table_is_skipped_silently(tmp_path: Path) -> None:
    """**这条是防"应用起不来"的关键。**

    `PRAGMA table_info(<不存在的表>)` 返回空集而不是报错，所以原来的写法会走到
    `ALTER TABLE`，然后抛 `no such table`——只要调用顺序稍有变化（比如在 create_all
    之前跑），应用就直接启动失败。
    """
    path = tmp_path / "empty.db"
    engine = _engine_for(path)

    # 不抛异常就是通过
    db_module._add_missing_columns(engine, "apply_task", {"profile_id": "INTEGER"})


def test_one_bad_column_does_not_block_the_others(tmp_path: Path) -> None:
    """单列补不上（例如 SQLite 不允许的 REFERENCES 列）不该拖垮整个迁移。

    缺那一列的后果是"那个功能不可用"，而不是"整个应用不可用"。
    """
    path = tmp_path / "old.db"
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE apply_task (id INTEGER PRIMARY KEY)")
    con.commit()
    con.close()

    engine = _engine_for(path)
    db_module._add_missing_columns(
        engine,
        "apply_task",
        {
            # 这一条在 SQLite 上必然失败（ADD COLUMN 不允许带 REFERENCES）
            "profile_id": "INTEGER REFERENCES platform_profile(id)",
            "greeted_at": "DATETIME",
        },
    )

    columns = _columns(engine, "apply_task")
    assert "greeted_at" in columns, "一列失败不该让后面的列也补不上"


# --------------------------------------------------------------------------- #
# 真实的迁移入口
# --------------------------------------------------------------------------- #
def test_init_db_creates_the_new_table(tmp_path: Path, monkeypatch) -> None:
    """全新库要建出 platform_profile（多账号的基本单位）。"""
    from app.config import get_settings

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    db_module.reset_engine()
    try:
        db_module.init_db()
        engine = db_module.get_engine()
        with Session(engine) as session:
            tables = {
                row[0]
                for row in session.exec(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).all()
            }
        assert "platform_profile" in tables
        assert "profile_id" in _columns(engine, "apply_task")
    finally:
        db_module.reset_engine()
        get_settings.cache_clear()


def test_init_db_survives_a_partial_legacy_schema(tmp_path: Path, monkeypatch) -> None:
    """旧库只有部分表时，`init_db` 也要能跑完（create_all 会补建缺失的表）。

    **注意用 `get_settings().database_path` 取真实路径**，不要自己拼一个文件名：
    这个数据库叫 `app.db`，自己拼 `mianshi.db` 会得到一个由 sqlite3 静默新建的空库，
    于是断言看起来"数据丢了"，实际读的是另一个文件（这个坑我自己踩过一次）。
    """
    from app.config import get_settings

    data_dir = tmp_path / "legacy"
    data_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("DATA_DIR", str(data_dir))
    get_settings.cache_clear()
    db_module.reset_engine()
    try:
        # 先按真实路径建一个"旧库"：只有 apply_task 且缺列
        database_path = get_settings().database_path
        con = sqlite3.connect(str(database_path))
        con.execute("CREATE TABLE apply_task (id INTEGER PRIMARY KEY, status VARCHAR)")
        con.execute("INSERT INTO apply_task (id, status) VALUES (7, 'pending')")
        con.commit()
        con.close()

        db_module.init_db()

        assert "profile_id" in _columns(db_module.get_engine(), "apply_task")
        con = sqlite3.connect(str(database_path))
        rows = list(con.execute("SELECT id FROM apply_task"))
        con.close()
        assert rows == [(7,)], "旧数据必须还在"
    finally:
        db_module.reset_engine()
        get_settings.cache_clear()
