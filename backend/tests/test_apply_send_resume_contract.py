"""真实浏览器适配器的简历投递契约（不需要浏览器的那部分）。

背景：`send_greeting` 一直收着 `resume_image` 参数却**从未使用**它——界面上那个
「投递简历」按钮实际只发了一句招呼语。附件投递现在是一个独立动作，这个文件钉住它的
**失败语义**：宁可明确报错，也不能安静地降级成"只打了招呼"，更不能谎报成功。

需要用浏览器的路径（真的点按钮、真的传文件）不在这里——那要真机校准，见
`docs/APPLY_SELECTORS.md`。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.apply.adapters.boss import BossAdapter
from app.apply.adapters.base import ApplyAdapterError, JobCard

#: 一套"看起来已校准"的选择器：只要覆盖 send_resume 用到的字段即可，
#: 这样 `require()` 不会先把我们拦下来，好让测试走到真正想验的逻辑。
_CALIBRATED = {
    "search_input": "input.input",
    "job_card": "ul.rec-job-list li.job-card-box",
    "card_title": "a.job-name",
    "greet_button": ".btn-startchat",
    "greet_input": "#chat-input",
    "greet_send": ".btn-send",
    "resume_send_button": ".btn-send-resume",
}


def _adapter(user_data_dir: Path, **selectors: str) -> BossAdapter:
    merged = {**_CALIBRATED, **selectors}
    return BossAdapter(user_data_dir=str(user_data_dir), selectors=merged, headless=True)


def _card() -> JobCard:
    return JobCard(external_id="job-1", company="某公司", title="视觉工程师", jd_url="https://example.com/job/1")


# --------------------------------------------------------------------------- #
# 选择器未校准 → 明确拒绝，不去开浏览器
# --------------------------------------------------------------------------- #
def test_send_resume_refuses_when_selector_missing(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, resume_send_button="")

    try:
        asyncio.run(adapter.send_resume(_card(), None))
    except ApplyAdapterError as exc:
        assert "resume_send_button" in str(exc)
        assert "APPLY_SELECTORS" in str(exc)
    else:  # pragma: no cover - 走到这里说明没有拒绝
        raise AssertionError("未校准选择器时必须明确报错，而不是继续")


# --------------------------------------------------------------------------- #
# 附件路径不存在 → 在碰浏览器之前就报清楚
# --------------------------------------------------------------------------- #
def test_send_resume_reports_missing_attachment_without_opening_browser(tmp_path: Path) -> None:
    """附件丢了要说"附件不存在"，而不是抛一个来自 Playwright 的、看不出原因的错误。"""
    adapter = _adapter(tmp_path)
    missing = str(tmp_path / "已经删掉的简历.pdf")

    result = asyncio.run(adapter.send_resume(_card(), missing))

    assert result.ok is False
    assert "简历附件不存在" in result.detail
    assert missing in result.detail
    # 这是"我们这边的问题"，不该熔断当天
    assert result.signal is None


def test_send_resume_does_not_require_an_attachment(tmp_path: Path) -> None:
    """不带附件是合法调用（发平台的在线简历）。

    这条**故意不去真的调 `send_resume`**：那会启动一个浏览器，而用例只想验证
    "附件校验放行"这一步。启动浏览器还会留下没关掉的子进程传输对象，
    pytest 会把它报成 `PytestUnraisableExceptionWarning`（偶发地让整轮变红），
    所以这里只验证校验函数的判据。
    """
    from app.apply.adapters.browser import _attachment_problem

    assert _attachment_problem(None) == ""
    assert _attachment_problem("") == ""


def test_attachment_problem_reports_a_missing_file(tmp_path: Path) -> None:
    from app.apply.adapters.browser import _attachment_problem

    missing = str(tmp_path / "已经删掉的简历.pdf")
    problem = _attachment_problem(missing)
    assert "简历附件不存在" in problem
    assert missing in problem


def test_attachment_problem_accepts_a_real_file(tmp_path: Path) -> None:
    from app.apply.adapters.browser import _attachment_problem

    real = tmp_path / "resume.png"
    real.write_bytes(b"not-really-a-png")
    assert _attachment_problem(str(real)) == ""


# --------------------------------------------------------------------------- #
# 协议层面：Mock 与真实适配器都必须实现这个动作
# --------------------------------------------------------------------------- #
def test_mock_adapter_implements_send_resume() -> None:
    """Mock 也必须实现——否则"Mock 能跑通全链路"就是假的，而 Mock 是唯一的无 Key 验证手段。"""
    import inspect

    from app.apply.adapters.mock import MockPlatformAdapter

    signature = inspect.signature(MockPlatformAdapter.send_resume)
    assert list(signature.parameters) == ["self", "card", "resume_image"]
