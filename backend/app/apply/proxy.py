"""代理与浏览器指纹（纯函数）。

这两件事都属于"配置错了比不配更糟"：

- **代理配错会让请求走真实 IP**。用户以为自己在用代理，实际平台看到的是他家里的 IP——
  这比不用代理更糟，因为他会对"已经在隐藏自己"产生错误的安全感。所以这里的判据是
  "**只要代理配置看着不对，就当成这个账号不可用**"，宁可停投也不裸奔。
- **指纹伪装需要克制**。项目用的是 patchright（真实 Chrome + 持久化上下文），
  它的价值就在于**减少**自动化痕迹；主动伪造一堆不一致的特征反而更容易被识别
  （比如时区说东京、语言说中文、IP 在德国）。所以这里只做**一致性**：
  让视口、语言、时区在同一个账号内自洽，而不是随机生成。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

#: 支持的代理协议。**只放行 http/https/socks5**：`file://` 之类会让 Playwright
#: 报一个与真实原因无关的错，而用户完全看不出是协议写错了。
_ALLOWED_SCHEMES = frozenset({"http", "https", "socks5", "socks5h"})

#: 常见视口。用"常见值"而不是随机值：随机出来的 1237x813 本身就是个特征。
_COMMON_VIEWPORTS: tuple[tuple[int, int], ...] = (
    (1920, 1080),
    (1680, 1050),
    (1600, 900),
    (1440, 900),
    (1366, 768),
    (2560, 1440),
)


@dataclass(frozen=True, slots=True)
class ProxySpec:
    """一个代理。`server` 是 Playwright 要的形式（含协议）。"""

    server: str
    username: str = ""
    password: str = ""

    @property
    def has_credentials(self) -> bool:
        return bool(self.username)

    def to_playwright(self) -> dict[str, str]:
        payload: dict[str, str] = {"server": self.server}
        if self.username:
            payload["username"] = self.username
        if self.password:
            payload["password"] = self.password
        return payload


def parse_proxy(raw: str) -> tuple[ProxySpec | None, str]:
    """把 `http://user:pass@host:port` 解析成 `ProxySpec`。

    返回 `(spec, 问题说明)`：解析失败时 spec 为 None 且说明非空。
    **不做"尽力而为"的解析**——半个代理等于没有代理，而它会让请求走真实 IP。
    """
    text = (raw or "").strip()
    if not text:
        return None, "代理地址为空"

    parsed = urlparse(text)
    if not parsed.scheme:
        return None, "代理地址缺少协议（要写成 http://host:port 这种形式）"
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return None, f"不支持的代理协议 `{parsed.scheme}`（只支持 http / https / socks5）"
    if not parsed.hostname:
        return None, "代理地址缺少主机名"
    if parsed.port is None:
        return None, "代理地址缺少端口"

    server = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
    return (
        ProxySpec(
            server=server,
            username=parsed.username or "",
            password=parsed.password or "",
        ),
        "",
    )


def proxy_is_usable(raw: str) -> bool:
    """配置的代理是否可用。**调用方据此把账号标成不可用**，而不是硬着头皮发请求。"""
    spec, _ = parse_proxy(raw)
    return spec is not None


def localhost_warning(raw: str) -> str:
    """代理指向本机时给一句提醒。

    "配了代理却指向 127.0.0.1"是很常见的误会（比如把本地抓包工具当成了代理），
    这时出口 IP 完全没变，用户却以为已经换了。
    """
    spec, _ = parse_proxy(raw)
    if spec is None:
        return ""
    host = urlparse(spec.server).hostname or ""
    if host in {"127.0.0.1", "localhost", "::1"}:
        return "代理指向本机，出口 IP 不会改变；如果你只是想抓包请忽略这条。"
    return ""


# --------------------------------------------------------------------------- #
# 指纹：只做"自洽"，不做"伪造"
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Fingerprint:
    """与账号绑定的浏览器特征。**同一个账号每次都要得到同一份**——
    特征在两次访问之间变化，本身就是最明显的自动化信号。"""

    locale: str = "zh-CN"
    timezone_id: str = "Asia/Shanghai"
    viewport_width: int = 1920
    viewport_height: int = 1080
    user_agent: str = ""

    @property
    def viewport(self) -> dict[str, int]:
        return {"width": self.viewport_width, "height": self.viewport_height}

    def to_context_args(self) -> dict[str, Any]:
        """给 `launch_persistent_context` 的参数。"""
        payload: dict[str, Any] = {
            "locale": self.locale,
            "timezone_id": self.timezone_id,
            "viewport": self.viewport,
        }
        if self.user_agent:
            payload["user_agent"] = self.user_agent
        return payload


#: 语言 → 该语言下"通常所在"的时区。用来保证语言与时不矛盾。
_LOCALE_TIMEZONES: dict[str, str] = {
    "zh-CN": "Asia/Shanghai",
    "zh-TW": "Asia/Taipei",
    "en-US": "America/New_York",
    "en-GB": "Europe/London",
}


def fingerprint_for(profile_id: int, *, locale: str = "zh-CN") -> Fingerprint:
    """按账号 id 稳定地生成一份指纹。

    用 `profile_id` 取模选视口，而不是随机：**同一个账号必须每次都一样**。
    随机会让这个账号的视口在两次访问之间变化，那比固定值更像机器。
    """
    normalized = locale if locale in _LOCALE_TIMEZONES else "zh-CN"
    width, height = _COMMON_VIEWPORTS[abs(int(profile_id)) % len(_COMMON_VIEWPORTS)]
    return Fingerprint(
        locale=normalized,
        timezone_id=_LOCALE_TIMEZONES[normalized],
        viewport_width=width,
        viewport_height=height,
    )


def fingerprint_conflicts(fingerprint: Fingerprint, *, exit_ip_country: str = "") -> str:
    """指纹与出口 IP 所在国家是否矛盾。返回矛盾说明，一致时返回空串。

    这是"伪造指纹反而更容易被识别"的典型场景：时区说上海、代理出口在德国。
    本项目**不自动改写**指纹去迎合 IP（那会变成指纹农场），只把矛盾指出来让用户自己决定。
    """
    country = (exit_ip_country or "").strip().upper()
    if not country:
        return ""

    tz = fingerprint.timezone_id
    if country == "CN" and not tz.startswith("Asia/Shang"):
        return f"出口 IP 在中国，但时区是 {tz}——两者矛盾本身就是特征，建议改成 Asia/Shanghai。"
    if country in {"US"} and not tz.startswith("America/"):
        return f"出口 IP 在美国，但时区是 {tz}，建议与出口地区保持一致。"
    if country in {"GB", "UK"} and not tz.startswith("Europe/"):
        return f"出口 IP 在英国，但时区是 {tz}，建议与出口地区保持一致。"
    return ""


# --------------------------------------------------------------------------- #
# 连通性探测
# --------------------------------------------------------------------------- #
#: 用来问"我是谁"的服务。挑 ipify 是因为它返回纯文本 IP，不需要解析 JSON。
PROBE_URL = "https://api.ipify.org"

#: 探测超时（秒）。代理不通时用户不该等太久。
PROBE_TIMEOUT = 10.0


@dataclass(frozen=True, slots=True)
class ProxyProbeResult:
    """一次代理探测的结果。

    `exit_ip` 是**出口 IP**——这是用户真正想知道的东西（"我换成功了吗"）。
    """

    ok: bool
    exit_ip: str = ""
    detail: str = ""
    local_proxy: bool = False


async def probe_proxy(
    raw: str,
    *,
    timeout: float = PROBE_TIMEOUT,
    trust_env: bool = False,
    client_factory: Any = None,
) -> ProxyProbeResult:
    """通过代理请求一次"我的 IP 是什么"，验证它真的能用。

    **为什么必须真的发一次请求**：代理地址语法正确 ≠ 能用（密码错、已过期、防火墙挡了
    都会失败）。而"配了但不能用"是最坏的情况——用户以为在隐藏自己，实际请求可能直接失败
    或者走了真实 IP。所以这里宁可花一次网络往返把它问清楚。

    `client_factory` 可注入（测试用假客户端，不发真实请求）。它的签名是
    `factory(proxy_server, *, timeout, trust_env)`，返回一个支持
    `async with` 与 `await client.get(url)` 的对象。
    """
    spec, problem = parse_proxy(raw)
    if spec is None:
        return ProxyProbeResult(ok=False, detail=problem)

    import httpx

    factory = client_factory or (
        lambda server, **kwargs: httpx.AsyncClient(proxy=server, **kwargs)  # type: ignore[arg-type]
    )

    try:
        async with factory(spec.server, timeout=timeout, trust_env=trust_env) as client:
            response = await client.get(PROBE_URL)
    except Exception as exc:  # noqa: BLE001 - 探测失败是正常结果，不是异常
        return ProxyProbeResult(
            ok=False,
            detail=f"通过代理请求失败：{type(exc).__name__}: {exc}",
            local_proxy=bool(localhost_warning(raw)),
        )

    if response.status_code >= 400:
        return ProxyProbeResult(
            ok=False,
            detail=f"探测接口返回 {response.status_code}（代理可能有限制或需要认证）",
            local_proxy=bool(localhost_warning(raw)),
        )

    exit_ip = (response.text or "").strip()[:64]
    hint = localhost_warning(raw)
    detail = f"代理可用，出口 IP：{exit_ip}" if exit_ip else "代理可用"
    if hint:
        detail = f"{detail}。{hint}"
    return ProxyProbeResult(ok=True, exit_ip=exit_ip, detail=detail, local_proxy=bool(hint))
