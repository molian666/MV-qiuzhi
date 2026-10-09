"""单用户密码登录：部署到云服务器时启用（M13 前置的部署适配）。

**为什么要有它**：项目原本假设「本地优先，无需账号体系」，只在 `127.0.0.1` 上监听。
一旦 `HOST=0.0.0.0` 上公网而没有认证，任何扫到端口的人都能读走你的简历、录音、
面试聊天记录与投递记录，并且能直接盗用你配置的模型 API Key 花钱。
所以这里不做「可选」——**绑非本机地址却没配密码就拒绝启动**。

三条设计取舍：

1. **密码直接放环境变量，不 hash、不落库**。它是配置项（与 API Key 同级），
   没有第二份副本；hash 只防「数据库泄露后反推」，而这个库里没有密码。
2. **token 是自签的 HMAC，不引 PyJWT**。格式 `payload.signature`，payload 里只有
   版本、签发时间与过期时间——没有用户 ID，因为本来就只有一个人。
3. **同时支持 header 与 cookie**。API 走 `Authorization: Bearer`（前端存
   localStorage），WebSocket 走 cookie——浏览器的 `WebSocket` 构造函数**不能**
   自定义请求头，只能靠 cookie 或 URL 参数；cookie 不会像 URL 参数那样漏进访问日志。

不做的：找回密码、多用户、角色权限、token 刷新（过期就重登，单用户场景够了）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from pathlib import Path

from fastapi import Header, HTTPException, Request, WebSocket

from .config import get_settings

#: 会话 cookie 名。**不能用连字符**：cookie 名的字符集不允许它。
COOKIE_NAME = "mianshi_session"

#: token 有效期（天）。单用户自用，30 天省得天天登。
TOKEN_TTL_DAYS = 30
TOKEN_TTL_SECONDS = TOKEN_TTL_DAYS * 24 * 3600

#: 免鉴权路径：健康检查（探活）、登录、登录状态查询，以及客户端环境体检
PUBLIC_PATHS = frozenset({
    "/api/health",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/status",
    # 客户端环境体检（M19 手机端）：只回答"这次部署能不能提供安全上下文"，
    # 全是部署形态的事实，不含任何隐私数据（见 api/client_info.py）。
    # 放公开路径是**故意的**：用户最可能卡在登录页排查"为什么没麦克风"，
    # 那时他还没有 cookie；若这里要鉴权，就正好在最需要它的时候返回 401。
    "/api/client-info",
})

#: 视为「本机」的绑定地址
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


# --------------------------------------------------------------------------- #
# 配置判断
# --------------------------------------------------------------------------- #
def auth_enabled() -> bool:
    """配了密码才启用鉴权——本地开发（loopback + 无密码）保持零摩擦。"""
    return bool(get_settings().auth_password.strip())


def is_public_bind(host: str) -> bool:
    """这个绑定地址会不会被本机之外的人访问到。"""
    return host.strip().lower() not in LOOPBACK_HOSTS


def assert_safe_bind() -> None:
    """上公网却没设密码 → 直接拒绝启动，不给「裸奔了但没人告诉我」的机会。"""
    settings = get_settings()
    if is_public_bind(settings.host) and not auth_enabled():
        raise RuntimeError(
            f"拒绝启动：HOST={settings.host} 会让本机之外的人访问，但 AUTH_PASSWORD 为空。\n"
            "  要么在 backend/.env 里设置 AUTH_PASSWORD=<强密码> 后重启，\n"
            "  要么把 HOST 改回 127.0.0.1（只本机用）。\n"
            "  见 docs/DEPLOY.md 与 backend/.env.example。"
        )


# --------------------------------------------------------------------------- #
# 签名密钥与 token
# --------------------------------------------------------------------------- #
def _secret_path() -> Path:
    return get_settings().data_dir / ".auth_secret"


def _secret() -> bytes:
    """签名密钥：优先用配置的 AUTH_SECRET，否则在 data/ 下持久化一个。

    持久化而不是每次进程启动随机生成——否则服务一重启，所有已登录设备都被踢下线。
    """
    configured = get_settings().auth_secret.strip()
    if configured:
        return configured.encode("utf-8")

    path = _secret_path()
    if path.exists():
        value = path.read_bytes()
        if len(value) >= 32:
            return value

    value = secrets.token_bytes(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    try:
        path.chmod(0o600)  # Windows 上支持有限，失败不影响可用性
    except OSError:
        pass
    return value


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_token(now: float | None = None) -> tuple[str, int]:
    """签发会话 token，返回 (token, 过期时间戳)。"""
    issued = int(time.time() if now is None else now)
    expires = issued + TOKEN_TTL_SECONDS
    payload = _b64encode(f"v1:{issued}:{expires}".encode("ascii"))
    signature = _b64encode(hmac.new(_secret(), payload.encode("ascii"), hashlib.sha256).digest())
    return f"{payload}.{signature}", expires


def verify_token(token: str, now: float | None = None) -> bool:
    """校验签名与有效期。任何一步不对都返回 False，不区分原因（不给探测线索）。"""
    if not token or "." not in token:
        return False
    payload, _, signature = token.partition(".")
    expected = _b64encode(hmac.new(_secret(), payload.encode("ascii", "ignore"), hashlib.sha256).digest())
    if not secrets.compare_digest(signature, expected):
        return False
    try:
        version, _issued, expires = _b64decode(payload).decode("ascii").split(":")
    except (ValueError, UnicodeDecodeError):
        return False
    if version != "v1":
        return False
    try:
        return int(expires) > int(time.time() if now is None else now)
    except ValueError:
        return False


def check_password(password: str) -> bool:
    """常数时间比对。用 bytes 而非 str：compare_digest 对非 ASCII 的 str 会抛错。"""
    expected = get_settings().auth_password
    if not expected:
        return False
    return secrets.compare_digest(password.encode("utf-8"), expected.encode("utf-8"))


# --------------------------------------------------------------------------- #
# token 提取与依赖
# --------------------------------------------------------------------------- #
def token_from_request(request: Request) -> str:
    """按优先级取 token：Authorization 头 → cookie → URL 参数。

    URL 参数排最后：它会进服务器访问日志与浏览器历史，只方便 curl 调试时用。
    """
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return request.cookies.get(COOKIE_NAME) or request.query_params.get("token", "")


def is_authenticated(request: Request) -> bool:
    if not auth_enabled():
        return True
    return verify_token(token_from_request(request))


async def require_auth(
    authorization: str | None = Header(default=None),
    mianshi_session: str | None = None,
) -> None:
    """路由级依赖。全局中间件已覆盖 `/api/*`，这个留给显式声明的场景。"""
    del mianshi_session  # cookie 由中间件统一读取，这里只是为了在文档里出现该字段
    if not auth_enabled():
        return
    scheme, _, value = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not verify_token(value.strip()):
        raise HTTPException(status_code=401, detail="未登录或登录已过期，请重新登录")


def websocket_authenticated(websocket: WebSocket) -> bool:
    """WebSocket 的鉴权：浏览器 WS 不能自定义请求头，只能读 cookie 或 URL 参数。"""
    if not auth_enabled():
        return True
    token = websocket.cookies.get(COOKIE_NAME) or websocket.query_params.get("token", "")
    return verify_token(token)
