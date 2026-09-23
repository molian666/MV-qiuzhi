"""REST 路由汇总。"""

from __future__ import annotations

from fastapi import APIRouter

from ..config import get_settings
from . import questions, reports, resumes, sessions, system, targets, training, ws

api_router = APIRouter()


@api_router.get("/api/health", tags=["system"], summary="健康检查")
def health() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "app": settings.app_name,
        "llm_provider": settings.llm_provider,
        "data_dir": str(settings.data_dir),
    }


api_router.include_router(sessions.router)
api_router.include_router(targets.router)
api_router.include_router(resumes.router)
api_router.include_router(questions.router)
api_router.include_router(system.router)
api_router.include_router(reports.router)
api_router.include_router(training.router)
api_router.include_router(ws.router)
