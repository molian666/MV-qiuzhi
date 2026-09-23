"""pytest 公共夹具：每个测试用独立的临时数据目录与 Mock LLM。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.config import get_settings
from app.deps import reset_llm_client


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """隔离的应用实例：数据落在 tmp_path，模型走 Mock，不碰真实 data 目录。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    get_settings.cache_clear()
    reset_llm_client()
    db_module.reset_engine()

    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client

    get_settings.cache_clear()
    reset_llm_client()
    db_module.reset_engine()
