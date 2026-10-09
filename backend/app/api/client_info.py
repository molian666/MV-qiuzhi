"""客户端环境提示（M19 手机端）。

前端在"面试间"里要回答一个很实际的问题：**现在这台设备到底能不能录音？**
手机浏览器上失败原因有几种，表现却几乎一样（点了没反应 / 通用报错）：

1. 用 `http://192.168.x.x:8000` 打开——不是安全上下文，浏览器**根本不会给麦克风**
   （iOS Safari 与 Android Chrome 都是硬性拒绝，且不给可读原因）；
2. 服务端压根没配 HTTPS（`use_tls=False`，也没走 Tailscale/serve 这类反代）；
3. 权限被拒 / 设备没有麦克风。

后端只能回答其中**部署侧**的部分（有没有可能提供安全上下文），
`isSecureContext` 与权限状态必须由浏览器自己判断——所以这个端点刻意只给"事实"，不下结论。

**不需要登录**：它在登录页就要能用（用户可能正在 HTTPS/HTTP 之间排查，
卡在登录页时更需要这条信息）。返回内容全是部署形态，不含任何隐私数据。
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..auth import auth_enabled
from ..config import get_settings
from ..deploy import is_local_deployment

router = APIRouter(prefix="/api/client-info", tags=["client-info"])


class ClientInfoOut(BaseModel):
    """前端据此拼"手机就绪体检"卡片。"""

    #: 浏览器是不是安全上下文（由**前端**用 window.isSecureContext 判定）。
    #: 这里给的是"服务端部署上有没有可能提供"：配了 TLS 或走本机访问时为 True。
    secure_context_possible: bool
    #: 用 HTTP 访问非本机地址时，麦克风一定会失败——前端据此提前给可执行的提示
    mic_requires_https: bool
    use_tls: bool
    #: 服务是否绑在本机（本机访问恒为安全上下文；也决定投递类功能是否可用）
    local_deployment: bool
    auth_enabled: bool
    #: 前端该把"实时语音"当可用还是降级（Mock 模式下仍能跑通"文字替身"流程）
    speech_provider: str
    host: str
    port: int


@router.get("", response_model=ClientInfoOut)
def client_info() -> ClientInfoOut:
    settings = get_settings()
    local = is_local_deployment()
    return ClientInfoOut(
        # 三种情况浏览器会给安全上下文：HTTPS(TLS)、反代在外部终止 TLS、或本机 loopback。
        # 反代那一路服务端看不出来，所以 use_tls=False 时不下"不安全"的结论，
        # 只说"可能没有"——真正的判断在前端。
        secure_context_possible=settings.use_tls or local,
        mic_requires_https=not settings.use_tls,
        use_tls=settings.use_tls,
        local_deployment=local,
        auth_enabled=auth_enabled(),
        speech_provider=settings.speech_provider,
        host=settings.host,
        port=settings.port,
    )
