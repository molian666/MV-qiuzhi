"""REST 路由汇总。"""

from __future__ import annotations

from fastapi import APIRouter

from ..config import get_settings
from . import (
    applications,
    apply,
    auth,
    decision,
    design,
    export,
    expression,
    interview_options,
    profiles,
    questions,
    recordings,
    reports,
    resumes,
    sessions,
    story,
    system,
    targets,
    training,
    vision,
    ws,
)

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
api_router.include_router(auth.router)
api_router.include_router(targets.router)
api_router.include_router(resumes.router)
api_router.include_router(questions.router)
api_router.include_router(system.router)
api_router.include_router(reports.router)
api_router.include_router(training.router)
api_router.include_router(vision.router)
api_router.include_router(design.router)
api_router.include_router(recordings.router)
api_router.include_router(profiles.router)
api_router.include_router(applications.router)
api_router.include_router(apply.router)
api_router.include_router(story.router)
api_router.include_router(expression.router)
api_router.include_router(interview_options.router)
api_router.include_router(decision.router)
api_router.include_router(export.router)
api_router.include_router(ws.router)
