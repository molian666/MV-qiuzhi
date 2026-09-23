"""横切服务：把 LLM 调用流水落库（PLAN §9.1 第 5 条）。

每次调用都记 task / model / 用时 / token，用于后续按任务换模型、核算成本、
以及对比不同 prompt 版本的效果。
"""

from __future__ import annotations

from sqlmodel import Session

from .models.session import LLMCall
from .providers.llm.base import LLMResult


def log_llm_result(db: Session, result: LLMResult, *, session_id: int | None = None) -> LLMCall:
    """记录一次成功的 LLM 调用。"""
    row = LLMCall(
        task=result.task,
        provider=result.provider,
        model=result.model,
        session_id=session_id,
        latency_ms=result.latency_ms,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        total_tokens=result.total_tokens,
        ok=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def log_llm_error(
    db: Session,
    *,
    task: str,
    provider: str = "",
    model: str = "",
    error: str = "",
    session_id: int | None = None,
) -> LLMCall:
    """记录一次失败的 LLM 调用（失败也要留痕，否则成本与排障都是黑洞）。"""
    row = LLMCall(
        task=task,
        provider=provider,
        model=model,
        session_id=session_id,
        ok=False,
        error=error[:2000],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
