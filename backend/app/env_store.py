"""运行时读写 `backend/.env`（M13 补充）。

**为什么密钥落在 `.env` 而不是数据库**：这是项目原本的设计——
`ProviderConfig.secret_ref` 的注释写明「只存密钥在本地 keystore 中的引用，
不存明文；M0 阶段密钥统一放 backend/.env」。所以界面上填的 Key 写进 `.env`，
**不进库**。好处很直接：导出、备份、直接看数据库都拿不到密钥。

两条安全约束：

1. **只允许改白名单里的键**。不给「任意写环境变量」的能力——
   那等于把 `HOST`、`DATA_DIR` 这类东西也暴露成可远程修改的配置项。
2. **密钥类不回显**。读取时只说「配了没有」和末四位，绝不把完整密钥发出去。

改完之后**同时更新进程内的环境变量并清掉 settings 缓存**，
所以不用重启就生效；写进 `.env` 只是让它下次启动时还在。
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from .config import BACKEND_DIR

ENV_PATH = BACKEND_DIR / ".env"
ENV_EXAMPLE_PATH = BACKEND_DIR / ".env.example"

#: 密钥类：读取时不回显内容，只报「配了没有」+ 末四位
SECRET_KEYS: frozenset[str] = frozenset(
    {"LLM_API_KEY", "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "EXPORT_TOKEN", "AUTH_PASSWORD"}
)

#: 允许通过界面修改的键。其余（HOST / PORT / DATA_DIR / SSL_* 等）
#: 要么涉及绑定与安全，要么改了必须重启，不允许远程改。
EDITABLE_KEYS: tuple[str, ...] = (
    "LLM_PROVIDER",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_CHAT_MODEL",
    "LLM_REASONER_MODEL",
    "SPEECH_PROVIDER",
    "DASHSCOPE_API_KEY",
    "DASHSCOPE_REALTIME_ENDPOINT",
    "DASHSCOPE_ASR_MODEL",
    "ASR_PROVIDER",
    "DASHSCOPE_ASR_FILE_MODEL",
    "TRUST_ENV_PROXY",
    "EXPORT_TOKEN",
    # 旧名（只支持 DeepSeek 时的配置）。仍然可改、仍然生效，
    # 仅在 LLM_* 没填时兜底，方便已有 .env 的用户平滑过渡。
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_CHAT_MODEL",
    "DEEPSEEK_REASONER_MODEL",
)

#: 界面上给这些键的说明。也用作输入框的占位提示——**保持一行内**，
#: 太长的说明在窄输入框里看不全。
KEY_NOTES: dict[str, str] = {
    "LLM_PROVIDER": "mock 不需要 Key；openai 才真的调用模型",
    "LLM_BASE_URL": "所选平台的 OpenAI 兼容地址",
    "LLM_API_KEY": "模型服务的 API Key",
    "LLM_CHAT_MODEL": "对话与追问用的模型名",
    "LLM_REASONER_MODEL": "评分与纠错用的模型名，留空复用上一个",
    "SPEECH_PROVIDER": "语音面试的提供方：mock | qwen",
    "DASHSCOPE_API_KEY": "阿里云百炼的 Key，实时语音与转写要用",
    "DASHSCOPE_REALTIME_ENDPOINT": "百炼实时语音的 WebSocket 地址",
    "DASHSCOPE_ASR_MODEL": "实时语音识别模型名",
    "ASR_PROVIDER": "录音批量转写的提供方：mock | qwen-file",
    "DASHSCOPE_ASR_FILE_MODEL": "录音文件转写的模型名",
    "TRUST_ENV_PROXY": "是否让请求读系统代理：true | false",
    "EXPORT_TOKEN": "导出接口的令牌，留空则导出一律 401",
    "DEEPSEEK_API_KEY": "（旧名）等同 LLM_API_KEY，仅在后者为空时生效",
    "DEEPSEEK_BASE_URL": "（旧名）等同 LLM_BASE_URL",
    "DEEPSEEK_CHAT_MODEL": "（旧名）等同 LLM_CHAT_MODEL",
    "DEEPSEEK_REASONER_MODEL": "（旧名）等同 LLM_REASONER_MODEL",
}

_LINE = re.compile(r"^(?P<key>[A-Z_][A-Z0-9_]*)\s*=(?P<value>.*)$")


class EnvError(RuntimeError):
    """配置写入失败（键不在白名单、值不合法）。"""


def ensure_env_file() -> Path:
    """`.env` 不存在时从 `.env.example` 拷一份——用户不该为了改一项去手工创建。"""
    if not ENV_PATH.exists() and ENV_EXAMPLE_PATH.exists():
        shutil.copyfile(ENV_EXAMPLE_PATH, ENV_PATH)
    return ENV_PATH


def read_env() -> dict[str, str]:
    """读配置的**当前生效值**。

    进程环境变量**优先于** `.env` 文件——那才是实际起作用的值。只看文件会出现
    「界面显示未配置，但服务其实在用环境变量里那把 Key」这种对不上的情况。
    """
    values: dict[str, str] = {}

    path = ensure_env_file()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            match = _LINE.match(line.strip())
            if match:
                values[match.group("key")] = match.group("value").strip()

    for key in EDITABLE_KEYS:
        env_value = os.environ.get(key)
        if env_value is not None:
            values[key] = env_value

    return values


def mask(value: str) -> str:
    """密钥的展示形式：只给末四位，够用来确认「填的是不是那把」。"""
    if not value:
        return ""
    return f"****{value[-4:]}" if len(value) > 4 else "****"


def public_view() -> dict[str, dict[str, str | bool]]:
    """给界面的视图：密钥只报配置状态，不回显内容。"""
    values = read_env()
    view: dict[str, dict[str, str | bool]] = {}
    for key in EDITABLE_KEYS:
        raw = values.get(key, "")
        view[key] = {
            "is_secret": key in SECRET_KEYS,
            "configured": bool(raw),
            "value": "" if key in SECRET_KEYS else raw,
            "masked": mask(raw) if key in SECRET_KEYS else raw,
            "note": KEY_NOTES.get(key, ""),
        }
    return view


def _validate(key: str, value: str) -> str:
    if key not in EDITABLE_KEYS:
        raise EnvError(f"不允许通过界面修改 {key}")
    cleaned = value.strip()
    if "\n" in cleaned or "\r" in cleaned:
        raise EnvError(f"{key} 的值不能包含换行")
    if len(cleaned) > 2000:
        raise EnvError(f"{key} 的值过长")
    return cleaned


def write_env(updates: dict[str, str]) -> list[str]:
    """把若干键写进 `.env`，并**立即在进程内生效**。返回被改动的键名。

    会保留文件里的注释与其它行、保留原有顺序，只替换对应键的值；
    键不存在就追加到末尾。这样用户手工加过的注释不会被抹掉。
    """
    if not updates:
        return []

    cleaned = {key: _validate(key, value) for key, value in updates.items()}
    path = ensure_env_file()

    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(cleaned)
    replaced: set[str] = set()
    output: list[str] = []

    for line in lines:
        match = _LINE.match(line.strip())
        if match and match.group("key") in remaining:
            key = match.group("key")
            output.append(f"{key}={remaining.pop(key)}")
            replaced.add(key)
        else:
            output.append(line)

    for key, value in remaining.items():
        output.append(f"{key}={value}")

    path.write_text("\n".join(output) + "\n", encoding="utf-8")

    # 立即生效：写进程环境变量 + 清 settings 缓存，不用重启
    for key, value in cleaned.items():
        os.environ[key] = value
    _clear_settings_cache()

    return sorted(cleaned)


def _clear_settings_cache() -> None:
    """配置变了，两个缓存都必须清。

    `get_settings` 是配置的来源；`deps._client_singleton` 是**按配置建出来的**
    LLM 客户端（`lru_cache`）。只清前者会留下一个"配置已更新但客户端还是旧的"
    的状态——界面上贴着"立即生效、不需要重启"，实际却要重启，
    这种不一致比直接报错更难排查。

    延迟导入：避免 config ↔ env_store 循环依赖。
    """
    from .config import get_settings
    from .deps import reset_llm_client

    get_settings.cache_clear()
    reset_llm_client()
