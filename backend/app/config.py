"""应用配置：全部来自环境变量 / backend/.env，密钥不入库、不入库日志。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 服务
    app_name: str = "mianshi"
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"

    # 数据
    data_dir: Path = BACKEND_DIR / "data"

    # LLM
    llm_provider: str = "mock"  # mock | deepseek
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_chat_model: str = "deepseek-chat"
    deepseek_reasoner_model: str = "deepseek-reasoner"
    llm_timeout_seconds: float = 60.0

    # 面试默认值
    default_max_questions: int = 6

    # 语音（M1）
    speech_provider: str = "mock"  # mock | qwen
    dashscope_api_key: str = ""
    dashscope_realtime_endpoint: str = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
    dashscope_asr_model: str = "qwen3-asr-flash-realtime"
    # VAD：连续静音多久算"说完"（M4 起由面试官画像的 wait_ms 覆盖）
    speech_silence_ms: int = 900
    # 短于这个时长的"说话"忽略（多半是咳嗽/噪音）
    min_answer_ms: int = 700
    # 判定说完后，等待 ASR 给出 final 的时间
    asr_wait_ms: int = 1500
    # 是否让 httpx / websockets 读取系统代理（HTTP_PROXY 等）。
    # 默认 False：DeepSeek、百炼都是国内直连，跟着墙外代理走通常直接失败。
    trust_env_proxy: bool = False

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
