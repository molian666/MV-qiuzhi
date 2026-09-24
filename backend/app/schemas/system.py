"""系统配置与提供方状态的 IO 契约。

写配置只接受白名单里的键（白名单在 `env_store` 里定义），
所以这里不加额外的 Literal 限制——两处各写一份键名会漂移。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProviderSettingsIn(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)
    """要改的键值。键不在白名单里会返回 400，不会静默丢弃。"""


class SettingItemOut(BaseModel):
    is_secret: bool
    configured: bool
    value: str = ""
    """非密钥项的当前值；密钥项为空"""
    masked: str = ""
    """展示用：密钥给末四位，非密钥给原值"""
    note: str = ""


class SystemSettingsOut(BaseModel):
    settings: dict[str, SettingItemOut]
    note: str = ""


class SystemSettingsUpdatedOut(BaseModel):
    updated: list[str]
    settings: dict[str, SettingItemOut]
    providers: dict
    note: str = ""
