"""系统信息：提供方状态与用量统计（成本可见，PLAN §11「API 费用失控」对策）。"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlmodel import Session, select

from ..clock import utcnow
from ..config import get_settings
from ..deps import get_session
from ..models.session import LLMCall

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/providers", summary="当前模型/语音提供方与可用状态")
def providers() -> dict[str, Any]:
    settings = get_settings()

    llm_ready = True
    if settings.llm_provider == "deepseek":
        llm_ready = bool(settings.deepseek_api_key)

    return {
        "llm": {
            "provider": settings.llm_provider,
            "ready": llm_ready,
            "chat_model": settings.deepseek_chat_model,
            "reasoner_model": settings.deepseek_reasoner_model,
            "base_url": settings.deepseek_base_url if settings.llm_provider == "deepseek" else "",
            "hint": "" if llm_ready else "请在后端 .env 里填 DEEPSEEK_API_KEY，或把 LLM_PROVIDER 设为 mock",
        },
        "speech": {
            "provider": "阿里云百炼 Qwen 实时语音",
            "ready": False,
            "hint": "M1 接入（实时 ASR → DeepSeek → TTS）",
        },
    }


@router.get("/usage", summary="LLM 用量与失败统计")
def usage(days: int = Query(default=7, ge=1, le=90), db: Session = Depends(get_session)) -> dict[str, Any]:
    since = utcnow() - timedelta(days=days)
    statement = (
        select(
            LLMCall.task,
            LLMCall.model,
            func.count(LLMCall.id),
            func.sum(LLMCall.prompt_tokens),
            func.sum(LLMCall.completion_tokens),
            func.avg(LLMCall.latency_ms),
        )
        .where(LLMCall.created_at >= since)
        .group_by(LLMCall.task, LLMCall.model)
    )

    items: list[dict[str, Any]] = []
    for task, model, calls, prompt_tokens, completion_tokens, avg_latency in db.exec(statement).all():
        items.append(
            {
                "task": task,
                "model": model,
                "calls": int(calls or 0),
                "prompt_tokens": int(prompt_tokens or 0),
                "completion_tokens": int(completion_tokens or 0),
                "avg_latency_ms": int(avg_latency or 0),
            }
        )

    failures = db.exec(
        select(func.count(LLMCall.id)).where(LLMCall.created_at >= since, LLMCall.ok == False)  # noqa: E712
    ).one()
    total = db.exec(select(func.count(LLMCall.id)).where(LLMCall.created_at >= since)).one()

    return {
        "window_days": days,
        "total_calls": int(total or 0),
        "failed_calls": int(failures or 0),
        "items": sorted(items, key=lambda x: x["calls"], reverse=True),
        "note": "token 为单位用量，具体金额请按所选模型单价换算",
    }
