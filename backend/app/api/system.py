"""系统信息：提供方状态、配置修改、用量统计（成本可见，PLAN §11「API 费用失控」对策）。"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlmodel import Session, select

from ..clock import utcnow
from ..config import get_settings
from ..deps import get_llm, get_session
from ..env_store import EnvError, public_view, write_env
from ..models.session import LLMCall
from ..providers.llm.base import LLMError, LLMMessage
from ..providers.llm.openai_compat import provider_label
from ..schemas.system import LlmTestOut, ProviderSettingsIn

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/providers", summary="当前模型/语音提供方与可用状态")
def providers() -> dict[str, Any]:
    settings = get_settings()

    llm_ready = True
    if settings.llm_enabled:
        llm_ready = bool(settings.resolved_llm_api_key and settings.resolved_llm_base_url)

    # 语音链路需要百炼的 Key；没配时语音面试的实时部分用不了（Mock 仍能跑）
    speech_ready = bool(settings.dashscope_api_key)

    if not settings.llm_enabled:
        # Mock 是「可用」状态，但必须说清它的局限——否则用户会以为功能坏了
        llm_hint = (
            "当前是 mock：不调用真实模型，题目与评分都是固定的示例内容，"
            "不会看你的岗位 JD 和简历，所以会显得没深度、没场景。"
            "在下面的「模型与密钥」里填上 LLM_BASE_URL、LLM_API_KEY 与模型名，"
            "并把提供方切到 openai，才会真的按岗位和简历出题。"
        )
    elif not llm_ready:
        llm_hint = (
            "已选 openai 但 LLM_BASE_URL 或 LLM_API_KEY 没填全，"
            "请在下面的「模型与密钥」里补上"
        )
    else:
        llm_hint = ""

    label = provider_label(settings.resolved_llm_base_url) if llm_ready else ""

    return {
        "llm": {
            "provider": settings.llm_provider,
            "label": label,
            "proxy_ready": llm_ready,
            "ready": llm_ready,
            "chat_model": settings.resolved_llm_chat_model,
            "reasoner_model": settings.resolved_llm_reasoner_model,
            "base_url": settings.resolved_llm_base_url if settings.llm_enabled else "",
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


@router.post("/test-llm", response_model=LlmTestOut, summary="用当前配置真发一次请求，确认模型能用")
async def test_llm() -> LlmTestOut:
    """**真发一次请求**——这是唯一能确定"配好了没有"的办法。

    常见的自欺是"点保存没报错就以为通了"，其实保存只验证了白名单与格式；
    地址写错、Key 过期、模型名不存在，都要真的调用一次才会暴露。

    永远返回 200、用 `ok` 表达结果：测试失败是正常结果而不是接口错误，
    包成 4xx/5xx 只会让前端拿到一句笼统的报错，看不到原始信息。
    """
    settings = get_settings()
    if not settings.llm_enabled:
        return LlmTestOut(
            ok=False,
            error="当前是 mock 模式，并没有在调用真实模型。",
            hint="把提供方切成 openai，并填好接口地址、API Key 与模型名。",
        )

    client = get_llm()
    try:
        result = await client.complete(
            task="connectivity_test",
            messages=[
                LLMMessage(role="user", content="只回两个字：可用"),
            ],
            temperature=0,
        )
    except LLMError as exc:
        message = str(exc)
        return LlmTestOut(ok=False, error=message, hint=_test_hint(message))

    return LlmTestOut(
        ok=True,
        reply=result.text.strip()[:200],
        model=result.model,
        latency_ms=result.latency_ms,
    )


def _test_hint(message: str) -> str:
    """把常见错误翻成"下一步做什么"。

    认不出来就返回空串——**别硬凑建议**，指错方向比不给建议更浪费时间。
    """
    lowered = message.lower()

    if "401" in lowered or "authentication" in lowered or "invalid" in lowered and "key" in lowered:
        return "API Key 无效或已过期。去对应平台的控制台重新生成一把，注意复制完整。"
    if "model" in lowered and ("not found" in lowered or "not exist" in lowered or "不存在" in message):
        return "模型名可能不对。核对平台的模型列表——同一个模型在不同平台叫法不一样。"
    if "404" in lowered:
        return "接口地址可能不对。确认填的是该平台的 OpenAI 兼容地址（多数要带 /v1）。"
    if "429" in lowered or "quota" in lowered or "insufficient" in lowered or "balance" in lowered:
        return "额度不足或触发了限流。检查账户余额与并发限制。"
    if "请求失败" in message or "timeout" in lowered or "connect" in lowered:
        return "连不上接口地址。确认网络能通；国内平台不要把请求走系统代理（TRUST_ENV_PROXY=false）。"
    return ""


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


@router.get("/version", summary="本地版本信息（git commit），供「检查更新」对比远程")
def app_version() -> dict[str, Any]:
    """读本地 git 提交与远程地址，返回给前端做版本对比。

    检测更新的数据来源是 git 仓库本身：本地 HEAD 的 commit sha 就是"我现在的版本"，
    前端再拿它与 GitHub 远程的最新 commit 对比，不同就是有新版本。
    不是 git 仓库（或 git 不可用）时返回 None 字段——前端据此隐藏更新检查入口。
    """
    from ..version import read_local_version

    info = read_local_version()
    if info is None:
        return {"available": False}
    return {"available": True, **info}
