"""FastAPI 应用装配。

建库放在 lifespan 里（而不是 import 时），这样测试可以先设好 DATA_DIR 再创建应用，
不会往真实 data 目录里写东西。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__
from .api import api_router
from .config import get_settings
from .db import init_db
from .providers.llm.base import LLMError


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        init_db()
        yield

    app = FastAPI(
        title="mianshi 求职操作系统 API",
        version=__version__,
        description="面试模拟与评分、薄弱点训练、投递分析。详见 docs/PLAN.md。",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        # 浏览器扩展发来的请求 Origin 是 chrome-extension://<id>，用正则放行
        allow_origin_regex=r"chrome-extension://.*",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router)

    @app.exception_handler(LLMError)
    async def _llm_error_handler(_request: Request, exc: LLMError) -> JSONResponse:
        """模型侧故障统一 502，并带上可读原因（缺 Key / 额度 / 网络）。"""
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    return app


app = create_app()
