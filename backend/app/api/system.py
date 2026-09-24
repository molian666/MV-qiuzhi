"""系统信息：提供方状态、配置修改、用量统计（成本可见，PLAN §11「API 费用失控」对策）。"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlmodel import Session, select

from ..clock import utcnow
from ..config import get_settings
from ..deps import get_session
from ..env_store import EnvError, public_view, write_env
from ..models.session import LLMCall
from ..schemas.system import ProviderSettingsIn

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/providers", summary="当前模型/语音提供方与可用状态")
def providers() -> dict[str, Any]:
    settings = get_settings()

    llm_ready = True
    if settings.llm_provider == "deepseek":
        llm_ready = bool(settings.deepseek_api_key)

    # 语音链路需要百炼的 Key；没配时语音面试的实时部分用不了（Mock 仍能跑）
    speech_ready = bool(settings.dashscope_api_key)

    if settings.llm_provider == "mock":
        # Mock 是「可用」状态，但必须说清它的局限——否则用户会以为功能坏了
        llm_hint = (
            "当前是 mock：不调用真实模型，题目与评分都是固定的示例内容，"
            "不会看你的岗位 JD 和简历，所以会显得没深度、没场景。"
            "在下面的「模型与密钥」填上 DEEPSEEK_API_KEY 并把提供方切到 deepseek，"
            "才会真的按岗位和简历出题。"
        )
    elif not llm_ready:
        llm_hint = "已选 deepseek 但没填 DEEPSEEK_API_KEY，请在下面的「模型与密钥」里填上"
    else:
        llm_hint = ""

    return {
        "llm": {
            "provider": settings.llm_provider,
            "ready": llm_ready,
            "chat_model": settings.deepseek_chat_model,
            "reasoner_model": settings.deepseek_reasoner_model,
            "base_url": settings.deepseek_base_url if settings.llm_provider == "deepseek" else "",
            "hint": llm_hint,
        },
        "speech": {
            "provider": f"阿里云百炼 Qwen 实时语音（{settings.speech_provider}）",
            "ready": speech_ready,
            "hint": "" if speech_ready else "填上 DASHSCOPE_API_KEY 才能用实时语音面试",
        },
    }


@router.get("/settings", summary="可配置项（密钥只报是否已配，不回显内容）")
def read_settings() -> dict[str, Any]:
    return {
        "settings": public_view(),
        "note": (
            "写进 backend/.env 并立即生效，不需要重启。"
            "密钥不会存进数据库，也不会出现在导出里。"
        ),
    }


@router.put("/settings", summary="修改模型与密钥（写入 backend/.env）")
def update_settings(payload: ProviderSettingsIn) -> dict[str, Any]:
    try:
        updated = write_env(payload.values)
    except EnvError as exc:
        # 键不在白名单、值不合法：是调用方的问题
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "updated": updated,
        "settings": public_view(),
        "providers": providers(),
        "note": "已写入 backend/.env 并立即生效，不需要重启。",
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
