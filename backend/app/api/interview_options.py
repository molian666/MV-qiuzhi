"""面试配置预设（M11）：压力模式、面试官风格、连环面、语言。

这些是**代码里的常量**，不是表。理由：
它们是"产品给的选项"，用户改不了——改了就变成自定义画像了，
而自定义画像已经有 `interviewer_profile`（M4）负责。两者的边界要清楚：
预设是"开箱可用的几套"，画像是"从你的真实录音学出来的那套"。
"""

from __future__ import annotations

from fastapi import APIRouter

from ..analysis.interviewer_styles import DEFAULT_PANEL, VOICE_LABELS, style_options
from ..analysis.locale import locale_options
from ..analysis.pressure import mode_options
from ..schemas.interview_options import InterviewOptionsOut

router = APIRouter(prefix="/api/interview-options", tags=["interview"])


@router.get("", response_model=InterviewOptionsOut, summary="面试配置选项（压力模式/风格/连环面/语言）")
def read_options() -> InterviewOptionsOut:
    return InterviewOptionsOut(
        pressure_modes=mode_options(),
        styles=style_options(),
        default_panel=[{"style_key": key, "rounds": rounds} for key, rounds in DEFAULT_PANEL],
        voices=[{"key": key, "label": label} for key, label in VOICE_LABELS.items()],
        locales=locale_options(),
    )
