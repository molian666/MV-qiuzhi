"""代理与指纹。

这两件事都是"配错了比不配更糟"，所以测试重点全在**拒绝**那一侧：
代理配置不对时必须让账号不可用（而不是走真实 IP 裸奔），指纹必须自洽且稳定。
"""

from __future__ import annotations

from typing import Any

from app.apply.proxy import (
    Fingerprint,
    fingerprint_conflicts,
    fingerprint_for,
    localhost_warning,
    parse_proxy,
    probe_proxy,
    proxy_is_usable,
)

# --------------------------------------------------------------------------- #
# 代理解析
# --------------------------------------------------------------------------- #
def test_parses_a_basic_proxy() -> None:
    spec, problem = parse_proxy("http://127.0.0.1:7890")

    assert problem == ""
    assert spec is not None
    assert spec.server == "http://127.0.0.1:7890"
    assert spec.has_credentials is False


def test_parses_credentials_out_of_the_url() -> None:
    spec, _ = parse_proxy("http://alice:s3cret@proxy.example.com:8080")

    assert spec is not None
    assert spec.username == "alice"
    assert spec.password == "s3cret"
    # 凭据不能留在 server 里（否则会出现在日志与报错里）
    assert "s3cret" not in spec.server
    assert "alice" not in spec.server


def test_supports_socks5() -> None:
    spec, _ = parse_proxy("socks5://10.0.0.1:1080")
    assert spec is not None
    assert spec.server.startswith("socks5://")


def test_playwright_payload_shape() -> None:
    spec, _ = parse_proxy("http://u:p@h:1")
    assert spec is not None
    payload = spec.to_playwright()
    assert payload["server"] == "http://h:1"
    assert payload["username"] == "u"
    assert payload["password"] == "p"


def test_rejects_missing_scheme() -> None:
    """`host:port` 这种写法 Playwright 会报一个与真实原因无关的错，所以在入口就拒。"""
    spec, problem = parse_proxy("proxy.example.com:8080")
    assert spec is None
    assert "协议" in problem


def test_rejects_unsupported_scheme() -> None:
    spec, problem = parse_proxy("file:///etc/passwd")
    assert spec is None
    assert "不支持" in problem


def test_rejects_missing_port() -> None:
    """没端口的代理不是一个能用的代理。"""
    spec, problem = parse_proxy("http://proxy.example.com")
    assert spec is None
    assert "端口" in problem


def test_rejects_missing_host() -> None:
    spec, problem = parse_proxy("http://:8080")
    assert spec is None
    assert "主机名" in problem


def test_rejects_empty() -> None:
    spec, problem = parse_proxy("")
    assert spec is None
    assert spec is None and problem


def test_whitespace_is_tolerated() -> None:
    spec, _ = parse_proxy("  http://h:1  ")
    assert spec is not None


# --------------------------------------------------------------------------- #
# 可用性判定：这是"别裸奔"的闸门
# --------------------------------------------------------------------------- #
def test_proxy_is_usable_only_for_well_formed_values() -> None:
    assert proxy_is_usable("http://h:1") is True
    assert proxy_is_usable("socks5://h:1") is True
    # 以下都会让请求走真实 IP，必须判为不可用
    assert proxy_is_usable("") is False
    assert proxy_is_usable("h:1") is False
    assert proxy_is_usable("http://h") is False
    assert proxy_is_usable("ftp://h:1") is False


def test_localhost_proxy_is_flagged() -> None:
    """配了代理却指向本机是很常见的误会：出口 IP 完全没变。"""
    assert "出口 IP 不会改变" in localhost_warning("http://127.0.0.1:7890")
    assert "出口 IP 不会改变" in localhost_warning("http://localhost:7890")
    assert localhost_warning("http://proxy.example.com:8080") == ""
    assert localhost_warning("garbage") == ""


# --------------------------------------------------------------------------- #
# 指纹：稳定 + 自洽
# --------------------------------------------------------------------------- #
def test_fingerprint_is_stable_for_the_same_profile() -> None:
    """**同一个账号每次必须拿到同一份指纹。**

    两次访问之间视口/语言变化，本身比固定值更像机器。
    """
    first = fingerprint_for(7)
    second = fingerprint_for(7)

    assert first == second


def test_different_profiles_get_different_viewports() -> None:
    """不同账号用不同视口——两个账号完全一样也是一种关联特征。"""
    viewports = {
        (fingerprint_for(index).viewport_width, fingerprint_for(index).viewport_height)
        for index in range(6)
    }
    assert len(viewports) > 1


def test_viewport_is_a_common_size_not_random() -> None:
    """随机出来的 1237x813 本身就是特征，所以只用常见视口。"""
    common = {
        (1920, 1080),
        (1680, 1050),
        (1600, 900),
        (1440, 900),
        (1366, 768),
        (2560, 1440),
    }
    for index in range(12):
        item = fingerprint_for(index)
        assert (item.viewport_width, item.viewport_height) in common


def test_locale_implies_a_consistent_timezone() -> None:
    """语言与时不矛盾：中文不能配纽约时区。"""
    zh = fingerprint_for(1, locale="zh-CN")
    assert zh.locale == "zh-CN"
    assert zh.timezone_id == "Asia/Shanghai"

    en = fingerprint_for(1, locale="en-US")
    assert en.locale == "en-US"
    assert en.timezone_id == "America/New_York"


def test_unknown_locale_falls_back_to_chinese() -> None:
    item = fingerprint_for(1, locale="xx-YY")
    assert item.locale == "zh-CN"


def test_context_args_include_the_essentials() -> None:
    args = fingerprint_for(3).to_context_args()
    assert args["locale"]
    assert args["timezone_id"]
    assert set(args["viewport"]) == {"width", "height"}


def test_user_agent_only_included_when_set() -> None:
    """默认**不塞自定义 UA**：真实 Chrome 的 UA 与它自己的特征一致，改它反而露馅。"""
    assert "user_agent" not in fingerprint_for(1).to_context_args()

    custom = Fingerprint(user_agent="X/1")
    assert custom.to_context_args()["user_agent"] == "X/1"


# --------------------------------------------------------------------------- #
# 指纹与出口 IP 的矛盾
# --------------------------------------------------------------------------- #
def test_conflict_is_reported_when_timezone_does_not_match_the_exit_ip() -> None:
    """时区说上海、出口在德国——矛盾本身就是特征。"""
    item = fingerprint_for(1, locale="en-US")  # America/New_York
    problem = fingerprint_conflicts(item, exit_ip_country="CN")

    assert problem
    assert "Asia/Shanghai" in problem


def test_no_conflict_when_consistent() -> None:
    item = fingerprint_for(1, locale="zh-CN")  # Asia/Shanghai
    assert fingerprint_conflicts(item, exit_ip_country="CN") == ""


def test_no_conflict_check_without_a_known_country() -> None:
    """查不到出口地区时不做判断——**不猜**。"""
    item = fingerprint_for(1, locale="en-US")
    assert fingerprint_conflicts(item, exit_ip_country="") == ""


def test_unknown_country_is_not_flagged() -> None:
    """认不出的国家不能瞎报矛盾。"""
    item = fingerprint_for(1, locale="en-US")
    assert fingerprint_conflicts(item, exit_ip_country="ZZ") == ""


# --------------------------------------------------------------------------- #
# 代理连通性探测（不发真实请求：客户端可注入）
# --------------------------------------------------------------------------- #
class _FakeProbeResponse:
    def __init__(self, status_code: int = 200, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _FakeProbeClient:
    """记录构造参数与请求，按脚本返回。`raises` 模拟代理层异常。"""

    def __init__(self, response: _FakeProbeResponse | None = None, raises: Exception | None = None) -> None:
        self.response = response or _FakeProbeResponse(text="203.0.113.7")
        self.raises = raises
        self.kwargs: dict[str, Any] = {}
        self.gets: list[str] = []

    async def __aenter__(self) -> "_FakeProbeClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def get(self, url: str) -> _FakeProbeResponse:
        self.gets.append(url)
        if self.raises is not None:
            raise self.raises
        return self.response


def _probe_factory(client: _FakeProbeClient) -> Any:
    def factory(proxy: str, **kwargs: Any) -> _FakeProbeClient:
        client.kwargs = {"proxy": proxy, **kwargs}
        return client
    return factory


async def test_probe_returns_the_exit_ip() -> None:
    """**出口 IP 才是用户想知道的**（"我换成功了吗"）。"""
    client = _FakeProbeClient(_FakeProbeResponse(text="198.51.100.9"))

    result = await probe_proxy("http://proxy.example.com:8080", client_factory=_probe_factory(client))

    assert result.ok is True
    assert result.exit_ip == "198.51.100.9"
    assert "198.51.100.9" in result.detail


async def test_probe_builds_a_client_through_the_proxy() -> None:
    """探测必须**真的走代理**，否则它只会证明"本机能上网"。"""
    client = _FakeProbeClient()

    await probe_proxy("socks5://10.0.0.1:1080", client_factory=_probe_factory(client))

    assert client.kwargs["proxy"] == "socks5://10.0.0.1:1080"
    assert client.gets and client.gets[0].startswith("https://")


async def test_probe_does_not_use_the_environment_proxy() -> None:
    """本机常有系统代理；探测本身走代理，不该再叠一层环境代理。"""
    client = _FakeProbeClient()

    await probe_proxy("http://proxy.example.com:8080", client_factory=_probe_factory(client))

    assert client.kwargs.get("trust_env") is False


async def test_probe_reports_a_network_failure() -> None:
    client = _FakeProbeClient(raises=RuntimeError("连接被拒绝"))

    result = await probe_proxy("http://proxy.example.com:8080", client_factory=_probe_factory(client))

    assert result.ok is False
    assert "连接被拒绝" in result.detail


async def test_probe_reports_an_http_error_status() -> None:
    client = _FakeProbeClient(_FakeProbeResponse(status_code=407, text="proxy auth required"))

    result = await probe_proxy("http://proxy.example.com:8080", client_factory=_probe_factory(client))

    assert result.ok is False
    assert "407" in result.detail


async def test_probe_does_not_hit_the_network_for_a_bad_address() -> None:
    """地址本身不合法时不该浪费一次往返。"""
    client = _FakeProbeClient()

    result = await probe_proxy("not-a-proxy", client_factory=_probe_factory(client))

    assert result.ok is False
    assert client.gets == []


async def test_probe_flags_a_localhost_proxy() -> None:
    """指向本机的代理出口 IP 不会变——要在结果里点明。"""
    client = _FakeProbeClient(_FakeProbeResponse(text="127.0.0.1"))

    result = await probe_proxy("http://127.0.0.1:7890", client_factory=_probe_factory(client))

    assert result.ok is True
    assert result.local_proxy is True
    assert "出口 IP 不会改变" in result.detail


async def test_probe_truncates_an_absurd_response() -> None:
    """探测接口返回一大坨东西时不该整段塞进界面。"""
    client = _FakeProbeClient(_FakeProbeResponse(text="x" * 500))

    result = await probe_proxy("http://proxy.example.com:8080", client_factory=_probe_factory(client))

    assert len(result.exit_ip) <= 64
