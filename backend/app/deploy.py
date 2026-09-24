"""本机专属功能的守卫（部署到云服务器时的分流）。

用户选定：**除投递执行与浏览器扩展外，全部上云**。这份文件就是那条分界线的执行者。

为什么抓取与投递必须留在本机，两个理由都不是"保守"：

1. **风控按 IP 判**。Boss 直聘 / 猎聘对云服务器 IP（尤其是阿里云/腾讯云的机房段）
   的容忍度极低，用它投递几乎必然触发验证码 → 熔断停投，还不如不投。
2. **依赖本机的浏览器状态**。`patchright` 需要的 Chromium 与登录态（`storage_state`）
   都在你自己电脑上，云服务器上既没有也没有意义。

所以这里不是"禁用功能"，而是**把明显不可能成功的动作拦在前面，并说清为什么**——
比让它跑起来然后失败在半路、还留下风控记录要好。
"""

from __future__ import annotations

from fastapi import HTTPException

from .config import get_settings

#: 在这两个地址上运行时，本机专属功能可用
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


def is_local_deployment() -> bool:
    """当前进程是否绑在本机（即"本机专属功能"能不能用）。"""
    return get_settings().host.strip().lower() in _LOCAL_HOSTS


def require_local_run() -> None:
    """依赖：本机专属端点用它。非本机运行时给 403 + 可执行的建议。"""
    if is_local_deployment():
        return
    settings = get_settings()
    raise HTTPException(
        status_code=403,
        detail=(
            f"该功能只能在运行本机时使用（当前 HOST={settings.host}）。"
            "抓岗位与投递要复用你本机的浏览器登录态，且平台会按 IP 风控——"
            "用云服务器 IP 投递基本等于立刻触发验证码。"
            "请在本地电脑上启动本项目后再操作，云端可以继续查看队列与投递分析。"
        ),
    )
