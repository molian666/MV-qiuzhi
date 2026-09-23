"""系统配置表。

`ProviderConfig.secret_ref` 只存"密钥在本地 keystore 中的引用名"，**不存明文**；
M0 阶段密钥统一放 backend/.env，M7 再补本地加密 keystore。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Column, Text
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel

from ..clock import utcnow


class ProviderConfig(SQLModel, table=True):
    """LLM / 语音 / ASR / 浏览器自动化等外部提供方的配置。"""

    __tablename__ = "provider_config"

    id: int | None = Field(default=None, primary_key=True)
    kind: str = Field(default="llm", index=True)  # llm | speech | asr | tts | browser
    name: str = Field(default="", index=True)
    base_url: str = ""
    model: str = ""
    secret_ref: str = ""
    enabled: bool = True
    extra: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    updated_at: datetime = Field(default_factory=utcnow)


class AppSetting(SQLModel, table=True):
    """键值配置（视图偏好、限速阈值、开关等）。"""

    __tablename__ = "app_setting"

    id: int | None = Field(default=None, primary_key=True)
    key: str = Field(default="", index=True, unique=True)
    value: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    updated_at: datetime = Field(default_factory=utcnow)
