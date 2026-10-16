"""通用 OpenAI 兼容客户端（httpx 直连 `/chat/completions`，不引入厂商 SDK）。

**为什么是「通用」而不是「DeepSeek 专用」**：市面上绝大多数模型服务都提供
OpenAI 兼容接口——DeepSeek、阿里云百炼（DashScope 兼容模式）、智谱、月之暗面、
硅基流动、OpenRouter 等云端服务，以及 vLLM / Ollama 等本地推理。
换平台通常只需要改 `LLM_BASE_URL` + `LLM_API_KEY` + 模型名，
所以这里不硬编码任何厂商地址与型号，全部由配置传入。

模型分工：`LLM_CHAT_MODEL` 用于实时对话/追问（延迟优先），
`LLM_REASONER_MODEL` 用于评分/纠错/简历诊断等（质量优先）。
两者都只是"模型名"，填该平台的实际型号即可。
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

import httpx

from .base import LLMError, LLMClient, LLMMessage, LLMResult

# 这些任务要"想清楚再答"，用 reasoner 模型
REASONER_TASKS = {
    "scoring",
    "correction",
    "resume_match",
    "prep_pack",
    "rejection_attribution",
    "pacing_extract",
}

#: 从 base_url 猜一个简短的服务商名，只用于展示与用量统计的可读性。
#: 猜不中就直接用域名，**不影响任何功能**——这只是一个标签。
_KNOWN_HOSTS: dict[str, str] = {
    "api.deepseek.com": "deepseek",
    "dashscope.aliyuncs.com": "bailian",
    "open.bigmodel.cn": "zhipu",
    "api.moonshot.cn": "moonshot",
    "api.siliconflow.cn": "siliconflow",
    "openrouter.ai": "openrouter",
    "api.openai.com": "openai",
    "localhost": "local",
    "127.0.0.1": "local",
}


def provider_label(base_url: str) -> str:
    """把 base_url 变成一个人能看懂的短标签（猜不出就用域名）。"""
    host = (urlparse(base_url).hostname or "").lower()
    if not host:
        return "openai-compat"
    if host in _KNOWN_HOSTS:
        return _KNOWN_HOSTS[host]
    # 自定义/自建网关：api.example.com -> example，其余原样
    parts = host.split(".")
    if len(parts) >= 2 and parts[0] in {"api", "www"}:
        return parts[-2]
    return host


class OpenAICompatClient(LLMClient):  # type: ignore[misc]
    """任何 OpenAI 兼容服务的客户端。"""

    name = "openai-compat"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        chat_model: str,
        reasoner_model: str | None = None,
        timeout: float = 60.0,
        trust_env: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        # 用户可能填 https://api.deepseek.com 也可能填 .../v1，两种都当合法输入；
        # 末尾斜杠统一去掉，拼接时自己补 /chat/completions。
        self._base_url = base_url.strip().rstrip("/")
        self._chat_model = chat_model
        # 没单独配 reasoner 就退回 chat 模型：很多平台只有一档型号
        self._reasoner_model = reasoner_model or chat_model
        self._timeout = timeout
        # trust_env 默认 False：httpx 默认会读 HTTP_PROXY/HTTPS_PROXY 环境变量，
        # 而国内接口走墙外代理往往直接超时或 403（get_jobs 的 README 也专门提醒
        # "必须关闭墙外代理"）。需要走代理的用户把 TRUST_ENV_PROXY 打开即可。
        self._trust_env = trust_env
        # 测试注入用；None 时按正常网络请求。
        self._transport = transport
        self._label = provider_label(self._base_url)

    @property
    def label(self) -> str:
        """展示用的服务商短名（不影响请求）。"""
        return self._label

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
            raise LLMError(
                "未配置 LLM_API_KEY：请在 backend/.env 里填写，或把 LLM_PROVIDER 设为 mock",
                retryable=False,
            )
        if not self._base_url:
            raise LLMError(
                "未配置 LLM_BASE_URL：请填所选平台的 OpenAI 兼容地址，"
                "例如 https://api.deepseek.com 或 "
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
                retryable=False,
            )

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
            async with httpx.AsyncClient(
                timeout=self._timeout, trust_env=self._trust_env, transport=self._transport
            ) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                )
        except httpx.TimeoutException as exc:
            # 读/连超时：整段超时（默认 30s）内服务一个字都没回，基本等于不可达。
            # 重试只会把同样的 30s 再等一遍，直接抛给上层快速失败，别让面试开场
            # 或答题干等几分钟。断连/协议错仍走下面的 retryable=True 分支。
            raise LLMError(
                f"{self._label} 无响应（超过 {self._timeout:.0f} 秒未返回）——"
                "模型服务可能不可达，请检查 LLM_BASE_URL 的网络连通性，或换一个可用的模型通道",
                retryable=False,
            ) from exc
        except httpx.HTTPError as exc:  # 网络层：断连等瞬时错误，值得重试
            raise LLMError(f"{self._label} 请求失败：{exc}") from exc

        latency_ms = int((time.perf_counter() - started) * 1000)

        if response.status_code >= 400:
            # 4xx（Key/模型/参数错）重试必败；429 限流与 5xx 服务端抖动才重试
            retryable = response.status_code == 429 or response.status_code >= 500
            raise LLMError(
                f"{self._label} 返回 {response.status_code}：{response.text[:300]}",
                retryable=retryable,
            )

        data = response.json()
        try:
            content = data["choices"][0]["message"].get("content")
            text = content.strip() if isinstance(content, str) else ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"{self._label} 返回体结构异常：{str(data)[:300]}") from exc

        # 空返回体是"正在思考但什么都没生成"的头号原因（reasoner 模型把 token 花在
        # 思考上、或模型抽风直接返回空）。以前这里静默当成成功，面试官回合就落了
        # 一条空白内容——前端表现正是"思考一会儿后什么都不输出"。现在按失败处理，
        # 让上层重试一次再决定是否放弃。
        if not text:
            raise LLMError(
                f"{self._label} 返回了空内容（choices[0].message.content 为空，模型可能把全部输出花在了思考上）"
            )

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
