"""面试配置预设的对外模型（M11）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class InterviewOptionsOut(BaseModel):
    """前端渲染配置面板所需的全部选项（免得前端硬编码中文与默认值）。"""

    pressure_modes: list[dict[str, Any]] = Field(default_factory=list)
    styles: list[dict[str, Any]] = Field(default_factory=list)
    default_panel: list[dict[str, Any]] = Field(default_factory=list)
    voices: list[dict[str, Any]] = Field(default_factory=list)
    locales: list[dict[str, Any]] = Field(default_factory=list)
