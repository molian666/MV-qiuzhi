"""`python -m app`：按 `.env` 启动服务（部署与本地都走它）。

为什么要有这个入口，而不是在部署文档里写一长串 `uvicorn` 参数：

1. **TLS 证书路径与端口来自 `.env`**。服务化时注册命令只要写 `python -m app`，
   改端口或换证书不用去改 Windows 服务定义。
2. **不用 `--reload`**。部署时热重载会起 watcher 子进程，服务化后重启行为诡异。
3. **启动前先过安全闸**（`assert_safe_bind`），错误信息能直接打到服务日志里。

本地开发仍可直接用 `uvicorn app.main:app --reload`（见 README）。
"""

from __future__ import annotations

import sys

import uvicorn

from .config import get_settings


def _say(message: str) -> None:
    """Windows 控制台默认不是 UTF-8，中文可能变乱码——宁可降级也不要抛异常。"""
    try:
        print(message)
    except UnicodeEncodeError:
        print(message.encode("utf-8", "replace").decode("utf-8", "replace"))


def main() -> None:
    settings = get_settings()
    scheme = "https" if settings.use_tls else "http"
    _say("")
    _say(f"  {settings.app_name} 启动中 …")
    _say(f"    地址：{scheme}://{settings.host}:{settings.port}")
    _say(f"    数据：{settings.data_dir}")
    _say(f"    登录：{'已启用（AUTH_PASSWORD）' if settings.auth_password else '未启用（仅本机）'}")
    if settings.use_tls:
        _say(f"    证书：{settings.ssl_certfile}")
    static_dir = settings.resolved_static_dir
    _say(f"    前端：{static_dir if static_dir else '未找到 frontend/dist（跑后端 API 时用前端 dev server）'}")
    if settings.host not in {"127.0.0.1", "localhost", "::1"}:
        _say(f"    注意：绑在非本机地址上，投递执行与扩展 ingest 已自动禁用（见 app/deploy.py）")
    _say("")

    options: dict[str, object] = {
        "host": settings.host,
        "port": settings.port,
        "log_level": "info",
        # 语音面试是长连接：uvicorn 默认 20s 无 pong 就掐断 WebSocket，
        # 手机 Chrome 在省电/弱网下响应慢会被误杀（表现：面试开始不久"断开连接"）。
        # 放宽到 30s 探测、90s 超时，并用应用层心跳兜底（见 ws.ts）。
        "ws_ping_interval": 30.0,
        "ws_ping_timeout": 90.0,
    }
    if settings.use_tls:
        options["ssl_certfile"] = settings.ssl_certfile
        options["ssl_keyfile"] = settings.ssl_keyfile
    if settings.trust_proxy_headers:
        # 自己上 Nginx/Caddy 时，真实客户端 IP 与 scheme 在 X-Forwarded-* 里
        options["proxy_headers"] = True
        options["forwarded_allow_ips"] = "*"

    # 传字符串而不是 app 对象：这样 uvicorn 能自己导入，也支持以后加 workers
    uvicorn.run("app.main:app", **options)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        # assert_safe_bind 的报错要原样打出来，否则部署时只看到一句 traceback
        _say(f"\n启动失败：{exc}\n")
        sys.exit(1)
