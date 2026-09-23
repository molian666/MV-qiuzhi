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
