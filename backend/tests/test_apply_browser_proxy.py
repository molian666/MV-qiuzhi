"""浏览器适配器接上代理与指纹。

这两个能力最容易造成"以为在保护自己、其实没有"的错觉，所以测试重点在**拒绝**：
代理配置不对时必须**拒绝启动浏览器**，而不是忽略它继续跑（那会让请求走真实 IP）。
另外要验证指纹参数真的进了启动参数——而不是被 `no_viewport=True` 静默吃掉。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.apply.adapters.base import ApplyAdapterError
from app.apply.adapters.boss import BossAdapter
from app.apply.proxy import Fingerprint, fingerprint_for


def _adapter(tmp_path: Path, **kwargs: object) -> BossAdapter:
    return BossAdapter(user_data_dir=str(tmp_path / "profile"), **kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 代理：配置不对就拒绝启动
# --------------------------------------------------------------------------- #
def test_no_proxy_means_no_context_options(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    assert adapter._context_options() == {}


def test_valid_proxy_becomes_playwright_options(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, proxy="http://user:pw@proxy.example.com:8080")
    options = adapter._context_options()

    assert options["proxy"]["server"] == "http://proxy.example.com:8080"
    assert options["proxy"]["username"] == "user"
    # 凭据不能出现在 server 串里（会进日志）
    assert "pw" not in options["proxy"]["server"]


def test_socks5_proxy_is_accepted(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, proxy="socks5://10.0.0.1:1080")
    assert adapter._context_options()["proxy"]["server"] == "socks5://10.0.0.1:1080"


@pytest.mark.parametrize(
    "bad",
    [
        "proxy.example.com:8080",  # 缺协议
        "http://proxy.example.com",  # 缺端口
        "http://:8080",  # 缺主机
        "file:///etc/passwd",  # 不支持的协议
    ],
)
def test_broken_proxy_refuses_to_launch(tmp_path: Path, bad: str) -> None:
    """**核心安全属性**：坏代理必须拒绝启动，不能静默忽略。

    忽略的后果是请求走真实 IP，而用户以为自己在用代理——那比不用代理更糟。
    """
    adapter = _adapter(tmp_path, proxy=bad)

    with pytest.raises(ApplyAdapterError) as excinfo:
        adapter._context_options()

    message = str(excinfo.value)
    assert "代理配置不可用" in message
    assert "真实 IP" in message
    # 要告诉用户怎么修
    assert "清空" in message


# --------------------------------------------------------------------------- #
# 指纹：真的进启动参数
# --------------------------------------------------------------------------- #
def test_no_fingerprint_means_no_extra_args(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    assert adapter._launch_args() == []
    assert adapter._fingerprint_options() == {}


def test_fingerprint_viewport_goes_through_window_size(tmp_path: Path) -> None:
    """**不能用 `viewport=` 参数**：持久化上下文开着 `no_viewport=True`，它会被忽略。

    这条断言就是为了防止有人"顺手改成 viewport=" —— 那样指纹看起来配了、实际没生效。
    """
    print_fp = Fingerprint(locale="zh-CN", timezone_id="Asia/Shanghai", viewport_width=1600, viewport_height=900)
    adapter = _adapter(tmp_path, fingerprint=print_fp)

    args = adapter._launch_args()
    assert "--window-size=1600,900" in args
    # 而且**不能**出现在 context options 里
    assert "viewport" not in adapter._fingerprint_options()


def test_fingerprint_locale_and_timezone_are_passed(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, fingerprint=fingerprint_for(3, locale="en-US"))
    options = adapter._fingerprint_options()

    assert options["locale"] == "en-US"
    assert options["timezone_id"] == "America/New_York"


def test_existing_window_size_is_not_overridden(tmp_path: Path) -> None:
    """offscreen 模式自带 `--window-size`，指纹不该覆盖它。

    "把窗口挪到屏幕外"是这个模式的全部意义，尺寸跟着它走就好。
    """
    print_fp = fingerprint_for(1)
    adapter = _adapter(
        tmp_path,
        fingerprint=print_fp,
        extra_args=["--window-position=-2400,-2400", "--window-size=1280,900"],
    )

    args = adapter._launch_args()
    assert "--window-size=1280,900" in args
    assert not any(arg.startswith("--window-size=") and "1280,900" not in arg for arg in args)


def test_fingerprint_args_are_kept_alongside_extra_args(tmp_path: Path) -> None:
    adapter = _adapter(
        tmp_path,
        fingerprint=fingerprint_for(1),
        extra_args=["--window-position=-2400,-2400"],
    )

    args = adapter._launch_args()
    assert "--window-position=-2400,-2400" in args
    assert any(arg.startswith("--window-size=") for arg in args)


def test_user_agent_is_not_sent_by_default(tmp_path: Path) -> None:
    """默认**不塞自定义 UA**：真实 Chrome 的 UA 与它自己的特征一致，改它反而露馅。"""
    adapter = _adapter(tmp_path, fingerprint=fingerprint_for(1))
    assert "user_agent" not in adapter._fingerprint_options()


# --------------------------------------------------------------------------- #
# 组合：代理 + 指纹
# --------------------------------------------------------------------------- #
def test_proxy_and_fingerprint_are_independent(tmp_path: Path) -> None:
    adapter = _adapter(
        tmp_path,
        proxy="http://proxy.example.com:8080",
        fingerprint=fingerprint_for(2),
    )

    assert adapter._context_options()["proxy"]["server"] == "http://proxy.example.com:8080"
    assert adapter._fingerprint_options()["locale"] == "zh-CN"


def test_broken_proxy_blocks_even_with_a_valid_fingerprint(tmp_path: Path) -> None:
    """指纹配得再对，也不能让坏代理蒙混过关。"""
    adapter = _adapter(tmp_path, proxy="nonsense", fingerprint=fingerprint_for(1))

    with pytest.raises(ApplyAdapterError):
        adapter._context_options()
