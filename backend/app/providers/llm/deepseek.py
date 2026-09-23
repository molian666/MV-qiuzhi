"""DeepSeek 客户端（OpenAI 兼容接口，用 httpx 直连，不引入额外 SDK）。

模型分工：实时对话/追问用 `deepseek-chat`（延迟优先），
评分/纠错/简历诊断等用 `deepseek-reasoner`（质量优先）。
"""

from __future__ import annotations

import time

import httpx

from .base import LLMError, LLMClient, LLMMessage, LLMResult

# 这些任务要"想清楚再答"，用 reasoner
REASONER_TASKS = {
    "scoring",
    "correction",
    "resume_match",
    "prep_pack",
    "rejection_attribution",
    "pacing_extract",
}


class DeepSeekClient(LLMClient):  # type: ignore[misc]
    name = "deepseek"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        chat_model: str = "deepseek-chat",
        reasoner_model: str = "deepseek-reasoner",
        timeout: float = 60.0,
        trust_env: bool = False,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._chat_model = chat_model
        self._reasoner_model = reasoner_model
        self._timeout = timeout
        # trust_env 默认 False：httpx 默认会读 HTTP_PROXY/HTTPS_PROXY 环境变量，
        # 而 DeepSeek 这类国内接口走墙外代理往往直接超时或 403
        # （get_jobs 的 README 也专门提醒"必须关闭墙外代理"）。
        # 需要走代理的用户把 TRUST_ENV_PROXY 打开即可。
        self._trust_env = trust_env

    def model_for(self, task: str) -> str:
        return self._reasoner_model if task in REASONER_TASKS else self._chat_model

    async def complete(
        self,
        *,
        task: str,
        messages: list[LLMMessage],
        temperature: float = 0.7,
        model: str | None = None,
        json_object: bool = False,
    ) -> LLMResult:
        if not self._api_key:
            raise LLMError("未配置 DEEPSEEK_API_KEY：请在 backend/.env 里填写，或把 LLM_PROVIDER 设为 mock")

        chosen = model or self.model_for(task)
        payload: dict[str, object] = {
            "model": chosen,
            "messages": [m.to_payload() for m in messages],
            "temperature": temperature,
            "stream": False,
        }
        if json_object:
            payload["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self._timeout, trust_env=self._trust_env) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                )
        except httpx.HTTPError as exc:  # 网络层
            raise LLMError(f"DeepSeek 请求失败：{exc}") from exc

        latency_ms = int((time.perf_counter() - started) * 1000)

        if response.status_code >= 400:
            raise LLMError(f"DeepSeek 返回 {response.status_code}：{response.text[:300]}")

        data = response.json()
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"DeepSeek 返回体结构异常：{str(data)[:300]}") from exc

        usage = data.get("usage") or {}
        return LLMResult(
            text=text,
            task=task,
            model=chosen,
            provider=self.name,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            latency_ms=latency_ms,
            raw=data,
        )
