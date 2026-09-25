"""系统配置与提供方状态的 IO 契约。

写配置只接受白名单里的键（白名单在 `env_store` 里定义），
所以这里不加额外的 Literal 限制——两处各写一份键名会漂移。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProviderSettingsIn(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)
    """要改的键：不在白名单里会返回 400，不会静默丢弃。"""


class LlmTestOut(BaseModel):
    """「测试连接」的结果。

    设计成**永远返回 200**、用 `ok` 表示成败：测试失败是正常结果，
    不是接口错误。用 4xx/5xx 表达会让前端只能拿到一句笼统的报错，
    而这里最需要的是把**原始错误**摊开给用户看。
    """

    ok: bool
    reply: str = ""
    """模型的一句话回答，用来确认"确实通了、不是缓存" """
    model: str = ""
    latency_ms: int = 0
    error: str = ""
    """原始错误信息，原样给出，不做加工"""
    hint: str = ""
    """针对这个错误的排查建议。说不出具体原因时留空，别硬凑"""


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
