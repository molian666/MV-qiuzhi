"""应用配置：全部来自环境变量 / backend/.env，密钥不入库、不入库日志。"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _is_frozen() -> bool:
    """PyInstaller 打包后 sys.frozen 为 True。"""
    return bool(getattr(sys, "frozen", False))


def _app_root() -> Path:
    """代码所在目录：frozen 时是 PyInstaller 解压目录（onefile 每次启动的临时目录）。"""
    if _is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent.parent


def _user_root() -> Path:
    """用户可写目录（.env 与 data 都放这里，跟随 exe 走，不会被清掉）：
    frozen 时是 exe 所在目录；开发/测试时是 backend/ 目录。
    """
    if _is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


BACKEND_DIR = _app_root()


def _env_file() -> Path:
    """配置文件位置：frozen 时在 exe 旁（密钥不打包，用户自己放）；否则 backend/.env。"""
    if _is_frozen():
        return _user_root() / ".env"
    return BACKEND_DIR / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 服务
    app_name: str = "mianshi"
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"

    # 数据：frozen 时在 exe 旁（可写、持久）；开发/测试时在 backend/data
    data_dir: Path = Field(
        default_factory=lambda: (_user_root() / "data" if _is_frozen() else BACKEND_DIR / "data")
    )

    # LLM（通用 OpenAI 兼容：任何提供 /chat/completions 的平台都能用）
    # provider: mock 不联网、返回固定示例；openai 走真实模型。
    # 兼容旧值 "deepseek"（等同 openai），老的 .env 不用改也能继续跑。
    llm_provider: str = "mock"  # mock | openai（旧值 deepseek 等价于 openai）
    llm_base_url: str = ""  # 例：https://api.deepseek.com
    llm_api_key: str = ""
    llm_chat_model: str = ""  # 日常对话/追问，延迟优先
    llm_reasoner_model: str = ""  # 评分/纠错，质量优先；不填则复用 chat 模型
    # 单次 LLM 请求超时（秒）。30 秒对 fast 档模型（deepseek-flash / qwen-*）足够；
    # 太长会让「模型服务不可达」时整个界面干等（历史上 120s + 3 次重试 ≈ 6 分钟，
    # 表现为点了开始面试一直没反应）。报告/评分这类长任务若偶发超时，在 .env 里
    # 把 LLM_TIMEOUT_SECONDS 调大（如 60）即可，不必改代码。
    llm_timeout_seconds: float = 30.0

    # 旧名（M13 之前只支持 DeepSeek）。新配置请用上面的 LLM_*，
    # 这两组会在 resolved_* 里被优先取新值，只在用户没改过时才兜底。
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_chat_model: str = "deepseek-chat"
    deepseek_reasoner_model: str = "deepseek-reasoner"

    # 面试默认值
    default_max_questions: int = 6

    # 语音（M1）
    speech_provider: str = "mock"  # mock | qwen
    dashscope_api_key: str = ""
    dashscope_realtime_endpoint: str = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
    dashscope_asr_model: str = "qwen3-asr-flash-realtime"
    # 批量转写（M4，上传录音做节奏分析）：mock 用粘贴的文稿；qwen-file 待接入
    asr_provider: str = "mock"  # mock | qwen-file
    dashscope_asr_file_model: str = "paraformer-v2"
    # VAD：连续静音多久算"说完"（M4 起由面试官画像的 wait_ms 覆盖）
    speech_silence_ms: int = 900
    # 短于这个时长的"说话"忽略（多半是咳嗽/噪音）
    min_answer_ms: int = 700
    # 判定说完后，等待 ASR 给出 final 的时间
    asr_wait_ms: int = 1500
    # 是否让 httpx / websockets 读取系统代理（HTTP_PROXY 等）。
    # 默认 False：国内厂商（DeepSeek、百炼等）多数直连，跟着墙外代理走通常直接失败。
    trust_env_proxy: bool = False

    # 定时投递的后台循环。**测试里必须关掉**：它会随应用启动跑在 TestClient 自己的
    # 事件循环里，于是"到点没到点"取决于真实时间，测试结果会随跑的时间不同而变
    # （实测它会让 tick 计数与"同一天不跑两次"这类断言随机失败）。
    apply_scheduler_enabled: bool = True

    # 导出接口的访问令牌（M15）。为空时 /api/export/* 一律 401 并提示如何配置——
    # 本地也不能裸奔：同网段的任何进程都能读你的全量求职数据。
    export_token: str = ""

    # ---------- 部署（M13 前置）：单用户登录与 TLS ----------
    # 登录密码。为空时**只允许** HOST 绑本机：绑非本机地址却没设密码，应用会拒绝启动
    # （见 app/auth.py 的 assert_safe_bind）。密码直接放这里，不 hash 不落库——
    # 它是配置项，与 API Key 同级，库里没有第二份副本。
    auth_password: str = ""
    # 会话签名密钥。留空则在 data/.auth_secret 下持久化一个（重启不会把所有设备踢下线）。
    auth_secret: str = ""
    # 走 HTTPS 时设 true：会话 cookie 只经 TLS 发送。
    # 用 IP + 自签证书部署时也要设 true（浏览器会把"用户手动信任过的自签站点"当安全上下文）。
    cookie_secure: bool = False
    # TLS 证书（PEM）。两个都配才用 HTTPS 启动；用法见 docs/DEPLOY.md。
    ssl_certfile: str = ""
    ssl_keyfile: str = ""
    # 前端构建产物目录。留空则自动探测 frontend/dist（存在就由后端托管，单端口部署）。
    static_dir: str = ""
    # 是否信任反向代理传来的 X-Forwarded-*（自己上 Nginx/Caddy 时设 true）。
    trust_proxy_headers: bool = False

    # ---------- LLM 生效值（新键优先，旧键兜底）----------
    # 目的：老的 .env（只有 DEEPSEEK_*）不改也能继续跑，
    # 新用户填 LLM_* 即可，两者混填时新的说了算。
    @property
    def llm_enabled(self) -> bool:
        """是否走真实模型。旧值 "deepseek" 等价于 "openai"。"""
        return self.llm_provider.strip().lower() in {"openai", "deepseek"}

    @property
    def _uses_new_llm_keys(self) -> bool:
        """用户是否已经在用新的通用键。

        这个判断很关键：旧键的**默认值**（DeepSeek 的地址与型号）不能用来
        兜底"正在用新键、只是没填这一项"的情况。否则填了硅基流动的 Key 与型号、
        却漏填 LLM_BASE_URL 时，请求会被静默发到 DeepSeek 官方地址——
        报个 401 让人一头雾水。宁可留空，让客户端给出明确的"缺 base_url"提示。
        """
        return bool(
            self.llm_base_url.strip()
            or self.llm_api_key.strip()
            or self.llm_chat_model.strip()
            or self.llm_reasoner_model.strip()
        )

    @property
    def resolved_llm_base_url(self) -> str:
        if self._uses_new_llm_keys:
            return self.llm_base_url.strip()
        return self.deepseek_base_url.strip()

    @property
    def resolved_llm_api_key(self) -> str:
        return self.llm_api_key.strip() or self.deepseek_api_key.strip()

    @property
    def resolved_llm_chat_model(self) -> str:
        if self._uses_new_llm_keys:
            return self.llm_chat_model.strip()
        # 老 .env：沿用旧的默认型号
        return self.deepseek_chat_model.strip() or "deepseek-chat"

    @property
    def resolved_llm_reasoner_model(self) -> str:
        if self.llm_reasoner_model.strip():
            return self.llm_reasoner_model.strip()
        # 新配置下留空 = "和 chat 同一档"，兼容只有单一型号的平台
        if self._uses_new_llm_keys:
            return self.resolved_llm_chat_model
        # 老 .env：沿用旧的两档分工
        return self.deepseek_reasoner_model.strip() or self.resolved_llm_chat_model

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def database_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def resolved_static_dir(self) -> Path | None:
        """前端构建产物目录：配了就按配的，没配就找 frontend/dist。

        frozen 时 dist 被打包进 exe（解压目录），不依赖外置目录；
        开发/测试时找 backend 旁边的 frontend/dist。
        """
        if self.static_dir.strip():
            path = Path(self.static_dir).expanduser()
            return path if path.is_dir() else None
        if _is_frozen():
            default = (
                Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
                / "frontend"
                / "dist"
            )
        else:
            default = BACKEND_DIR.parent / "frontend" / "dist"
        return default if default.is_dir() else None

    @property
    def use_tls(self) -> bool:
        return bool(self.ssl_certfile.strip() and self.ssl_keyfile.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
