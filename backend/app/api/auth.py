"""登录接口（M13 前置的部署适配）。

只有三个端点，都不需要已登录即可访问（否则会死锁）：
- `GET  /api/auth/status`：前端启动时问「要不要登录、我现在算不算已登录」
- `POST /api/auth/login`：校验密码 → 签发 token → 同时写 cookie
- `POST /api/auth/logout`：清 cookie

**为什么既返回 token 又写 cookie**：API 调用用 header 里的 token（前端存 localStorage，
刷新页面不用重登），而 WebSocket 与「直接点链接下载导出文件」只能靠 cookie
（浏览器的 WebSocket 构造函数不能自定义请求头，`<a href>` 导航也不会带 Authorization）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from ..auth import (
    COOKIE_NAME,
    TOKEN_TTL_SECONDS,
    auth_enabled,
    check_password,
    is_authenticated,
    issue_token,
)
from ..clock import utcnow
from ..config import get_settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    password: str


class StatusOut(BaseModel):
    auth_enabled: bool
    logged_in: bool
    ttl_days: int


class LoginOut(BaseModel):
    token: str
    expires_in: int
    note: str


class LogoutOut(BaseModel):
    ok: bool


@router.get("/status", response_model=StatusOut, summary="是否需要登录 / 当前是否已登录")
def status(request: Request) -> StatusOut:
    return StatusOut(
        auth_enabled=auth_enabled(),
        logged_in=is_authenticated(request),
        ttl_days=TOKEN_TTL_SECONDS // 86400,
    )


@router.post("/login", response_model=LoginOut, summary="用密码换 token")
def login(payload: LoginIn, response: Response) -> LoginOut:
    if not auth_enabled():
        raise HTTPException(
            status_code=400,
            detail="当前未启用登录：AUTH_PASSWORD 为空。只绑本机时这是正常的，直接使用即可。",
        )
    if not check_password(payload.password):
        # 刻意不区分「密码错」与「没配密码」，也不回显正确格式，避免给探测线索
        raise HTTPException(status_code=401, detail="密码不正确")

    token, _expires = issue_token()
    settings = get_settings()
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=TOKEN_TTL_SECONDS,
        httponly=True,  # 前端读不到，防 XSS 直接偷走
        secure=settings.cookie_secure,  # HTTPS 部署时设 true
        samesite="lax",  # 跨站 POST 不带 cookie，CSRF 的基本盘
        path="/",
    )
    return LoginOut(
        token=token,
        expires_in=TOKEN_TTL_SECONDS,
        note=f"已登录（{TOKEN_TTL_SECONDS // 86400} 天内免登）。token 也会写在 cookie 里，"
        "浏览器直接下载导出文件时靠它。",
    )


@router.post("/logout", response_model=LogoutOut, summary="退出登录（清 cookie）")
def logout(response: Response) -> LogoutOut:
    response.delete_cookie(key=COOKIE_NAME, path="/")
    # 服务端不维护会话表，所以「登出」= 前端丢掉 token + 浏览器丢掉 cookie。
    # token 在到期前仍有效——单用户本地/自建服务，不做黑名单换取零状态。
    return LogoutOut(ok=True)


@router.get("/whoami", summary="当前服务端的鉴权配置摘要（自查用，不含任何秘密）")
def whoami(request: Request) -> dict[str, object]:
    settings = get_settings()
    return {
        "auth_enabled": auth_enabled(),
        "logged_in": is_authenticated(request),
        "bind_host": settings.host,
        "public_bind": settings.host.strip().lower() not in {"127.0.0.1", "localhost", "::1", "[::1]"},
        "cookie_secure": settings.cookie_secure,
        "tls": settings.use_tls,
        "static_dir": str(settings.resolved_static_dir) if settings.resolved_static_dir else None,
        "server_time": utcnow().isoformat(timespec="seconds"),
        "depends": "none" if not auth_enabled() else "password",
    }
