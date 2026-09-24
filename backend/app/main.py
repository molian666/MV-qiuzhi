"""FastAPI 应用装配。

建库放在 lifespan 里（而不是 import 时），这样测试可以先设好 DATA_DIR 再创建应用，
不会往真实 data 目录里写东西。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import api_router
from .auth import PUBLIC_PATHS, assert_safe_bind, is_authenticated
from .config import Settings, get_settings
from .db import init_db
from .providers.llm.base import LLMError


def create_app() -> FastAPI:
    settings = get_settings()
    # 上公网却没配密码 → 在这里就崩，而不是等你发现简历和录音被人读走了
    assert_safe_bind()

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

    @app.middleware("http")
    async def _require_login(request: Request, call_next) -> Response:
        """`/api/*` 统一鉴权。

        用中间件而不是给每个 router 挂 `Depends`：以后新增路由文件时不存在
        「忘了挂鉴权」的可能——这类遗漏在公网部署里就是数据泄露。
        免鉴权路径见 `auth.PUBLIC_PATHS`（健康检查、登录、登录状态）。
        """
        path = request.url.path
        if path.startswith("/api/") and path not in PUBLIC_PATHS and not is_authenticated(request):
            return JSONResponse(status_code=401, content={"detail": "未登录或登录已过期，请重新登录"})
        return await call_next(request)

    app.include_router(api_router)

    @app.exception_handler(LLMError)
    async def _llm_error_handler(_request: Request, exc: LLMError) -> JSONResponse:
        """模型侧故障统一 502，并带上可读原因（缺 Key / 额度 / 网络）。"""
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    _mount_frontend(app, settings)
    return app


def _mount_frontend(app: FastAPI, settings: Settings) -> None:
    """托管前端构建产物，让「一个端口 + 一张证书」就能用。

    部署到云服务器时不该再跑 vite dev（热更新、不安全、还要占第二个端口），
    直接 serve `frontend/dist` 最省事——也正好避开自签证书下第二个端口要再信任一次的麻烦。

    SPA fallback：前端用 tab 切换、没有真路由，但刷新页面或直接访问深层路径时
    仍要落回 index.html，否则会 404。
    """
    static_dir = settings.resolved_static_dir
    if static_dir is None:
        return

    assets_dir = static_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    index_file = static_dir / "index.html"
    if not index_file.is_file():
        return

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa(full_path: str) -> FileResponse:
        # 路由按注册顺序匹配，API 在前面已命中；走到这里还带这些前缀的是真的不存在，
        # 不能拿 index.html 顶替（否则前端会把 HTML 当 JSON 解析，报一堆莫名其妙的错）
        if full_path.startswith(("api/", "ws/")) or full_path in {"docs", "redoc", "openapi.json"}:
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = static_dir / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index_file)


app = create_app()
