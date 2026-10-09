"""拒绝信号识别（自动拉黑）

这里的代价是不对称的：漏判只是下次还看到那家公司，**误判会让用户永远看不到一家
本来有戏的公司**。所以测试的重点几乎全在"不该命中"那一侧。
"""

from __future__ import annotations

from app.analysis.rejection import (
    REASON_AUTO_REJECTED,
    blacklist_candidates,
    detect_rejection,
    detect_rejections,
    should_blacklist,
)

# --------------------------------------------------------------------------- #
# 该命中的
# --------------------------------------------------------------------------- #
def test_explicit_rejection_is_detected() -> None:
    signal = detect_rejection("不好意思，您的情况和我们的岗位不太合适，祝您求职顺利。")

    assert signal is not None
    assert signal.reason == REASON_AUTO_REJECTED
    assert "不合适" in signal.evidence or "祝您求职顺利" in signal.evidence


def test_closed_posting_is_detected() -> None:
    """岗位关闭也是"别再投了"，但它不是拒绝，措辞不同——同样要认。"""
    for text in ("这个岗位已关闭", "该职位已关闭，感谢关注", "已经招满了", "已有人选"):
        assert detect_rejection(text) is not None, text


def test_standard_polite_ending_is_detected() -> None:
    assert detect_rejection("感谢您的关注，我们会保留您的简历。") is not None


# --------------------------------------------------------------------------- #
# 绝不该命中的（误判代价最高）
# --------------------------------------------------------------------------- #
def test_negated_sentence_is_not_a_rejection() -> None:
    """「我们不会拒绝优秀的候选人」里有"拒绝"，含义完全相反。"""
    assert detect_rejection("我们不会拒绝优秀的候选人。") is None


def test_candidate_self_reference_is_not_a_rejection() -> None:
    """「我之前被拒绝过一次」是候选人在自述，不是对方在拒他。"""
    assert detect_rejection("我之前被拒绝过一次，所以这次想准备充分一点。") is None


def test_hypothetical_is_not_a_rejection() -> None:
    assert detect_rejection("如果被拒了也没关系，我想先了解一下流程。") is None


def test_candidates_waiting_language_is_not_a_rejection() -> None:
    """**最危险的一类**：这些话既可能是不理你，也可能是还在流程里。

    把它们当拒绝，用户就会因为"HR 说了句客套话"而永久失去这些公司。
    """
    waiting = [
        "简历已收到，我们会尽快安排。",
        "请等待我们的通知。",
        "我先把简历给部门看一下。",
        "方便的话下周再聊聊？",
        "您好，请问期望薪资是多少？",
        "还在评估中，有消息会联系您。",
    ]
    for text in waiting:
        assert detect_rejection(text) is None, f"误判为拒绝：{text}"


def test_empty_input_is_not_a_rejection() -> None:
    assert detect_rejection("") is None
    assert detect_rejection("   ") is None
    assert detect_rejections([]) is None


# --------------------------------------------------------------------------- #
# 逐句判断：否定句不该把整段否掉
# --------------------------------------------------------------------------- #
def test_negation_does_not_mask_a_later_real_rejection() -> None:
    """前一句有否定语境，后一句是明确拒绝 → 仍要命中后一句。"""
    text = "我们不会拒绝优秀的候选人。不过这个岗位确实和您不太合适。"

    signal = detect_rejection(text)
    assert signal is not None
    assert "不太合适" in signal.evidence


def test_mixed_conversation_is_scanned_until_first_rejection() -> None:
    conversation = [
        "你好，方便发一份简历吗？",
        "收到，我看一下。",
        "抱歉，这个岗位已经招满了。",
        "后面有合适的再联系你。",
    ]
    signal = detect_rejections(conversation)
    assert signal is not None
    assert "招满" in signal.evidence


def test_conversation_without_rejection_returns_none() -> None:
    conversation = ["你好", "简历已收到", "下周联系"]
    assert detect_rejections(conversation) is None


# --------------------------------------------------------------------------- #
# should_blacklist 的边界
# --------------------------------------------------------------------------- #
def test_should_blacklist_returns_signal_for_real_rejection() -> None:
    signal = should_blacklist(["抱歉，不合适。"], company="某公司")
    assert signal is not None


def test_should_blacklist_skips_company_already_blacklisted() -> None:
    """已经拉黑过的就不用再判一次（避免重复写库）。"""
    signal = should_blacklist(["抱歉，不合适。"], company="某公司", already_blacklisted=True)
    assert signal is None


def test_should_blacklist_requires_a_company_name() -> None:
    """公司名为空时不能拉黑：黑名单是按公司名精确比对的，空名字会命中一堆无关岗位。"""
    for blank in ("", "   ", None):
        assert should_blacklist(["抱歉，不合适。"], company=blank or "") is None  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 批量判定（喂给自动拉黑）
# --------------------------------------------------------------------------- #
def test_blacklist_candidates_picks_only_rejections() -> None:
    rows = [
        ("有戏公司", "boss", ["简历已收到，下周联系"]),
        ("拒绝公司", "boss", ["抱歉，这个岗位不太合适。"]),
    ]
    found = blacklist_candidates(rows)

    assert [item.company for item in found] == ["拒绝公司"]
    assert found[0].reason == REASON_AUTO_REJECTED
    # 依据要留着，用户在界面上能核对"它凭什么拉黑这家"
    assert "合适" in found[0].evidence


def test_blacklist_candidates_skips_already_blacklisted() -> None:
    rows = [("拒绝公司", "boss", ["抱歉，不合适。"])]
    assert blacklist_candidates(rows, blacklisted={"拒绝公司"}) == []


def test_blacklist_candidates_skips_blank_company_name() -> None:
    rows = [("", "boss", ["抱歉，不合适。"]), ("   ", "boss", ["抱歉，不合适。"])]
    assert blacklist_candidates(rows) == []


def test_blacklist_candidates_dedupes_case_and_space_variants() -> None:
    """同一家公司在不同卡片上的写法可能差个空格或大小写，不该在黑名单里留两条。"""
    rows = [
        ("Acme Corp", "boss", ["抱歉，不合适。"]),
        ("acme corp ", "boss", ["抱歉，不合适。"]),
    ]
    found = blacklist_candidates(rows)

    assert len(found) == 1
    assert found[0].company == "Acme Corp"


def test_blacklist_candidates_uses_first_matching_text_as_evidence() -> None:
    rows = [("某公司", "boss", ["你好", "岗位已关闭", "谢谢"])]
    found = blacklist_candidates(rows)

    assert len(found) == 1
    assert "已关闭" in found[0].evidence


def test_blacklist_candidates_on_empty_input() -> None:
    assert blacklist_candidates([]) == []
