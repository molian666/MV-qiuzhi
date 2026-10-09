"""从沟通文本里识别"对方已经明确拒绝"的信号（纯函数）。

用途：自动把公司加进黑名单（`reason="auto_rejected"`），不再重复投。

**为什么必须是一条保守的规则**：这里的动作是"以后再也不投这家公司"，代价不对称——
漏判一次只是下次还看到它；**误判一次会让用户永远看不到一家本来有戏的公司**。
所以：

1. **只认"明确拒绝 / 岗位关闭"**。"等通知""再看看""简历已收到"这类一律不算——
   它们既可能是不理你，也可能是还在流程里。
2. **否定语境必须排除**：「我们不会拒绝优秀的候选人」里有"拒绝"但完全相反。
   所以拒绝类词前面出现"不会 / 没有 / 未"时不算命中。
3. **判定依据留存**：返回命中的原句，方便用户在界面上一眼核对"它凭什么拉黑这家"。

`AUTO_REJECT` 的置信度最高；`UNRESPONSIVE`（长时间无回应）单独一类，
由调用方按天数判断，不由这里推断——**"没回你"和"拒绝你"是两件事**。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: 拒绝信号的类别。写成枚举值而不是布尔，因为两类在界面上的措辞不同。
REASON_AUTO_REJECTED = "auto_rejected"
REASON_UNRESPONSIVE = "unresponsive"

#: 明确拒绝的表述。这些几乎不会被用在"我们不会 X" 之外的肯定语境里。
_REJECT_PATTERNS: tuple[str, ...] = (
    "不合适",
    "不太合适",
    "不匹配",
    "与岗位不",
    "不符合",
    "不予考虑",
    "不考虑了",
    "已招满",
    "招满了",
    "岗位已关闭",
    "职位已关闭",
    "岗位关闭",
    "已停止招聘",
    "停止招聘",
    "已有人选",
    "找到合适的人",
    "感谢您的关注",  # 招聘平台的标准婉拒结尾
    "祝您求职顺利",
    "祝早日找到",
    "抱歉",
)

#: 出现这些词时，同句里的拒绝词很可能是**否定/假设**语境，整句不算命中。
_NEGATION_MARKERS: tuple[str, ...] = (
    "不会拒绝",
    "没有拒绝",
    "未拒绝",
    "不是拒绝",
    "并非拒绝",
    "怎么会拒绝",
    "如果被拒",  # 假设语气
    "被拒绝",  # "被拒绝"是候选人自述，不是对方在拒他
)

_SENTENCE_SPLIT = re.compile(r"[。！？!?\n\r；;]+")


@dataclass(frozen=True, slots=True)
class RejectionSignal:
    """一次命中：命中的原句 + 是哪类信号。"""

    reason: str
    evidence: str


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_SPLIT.split(text or "") if part.strip()]


def detect_rejection(text: str) -> RejectionSignal | None:
    """识别明确拒绝。命中返回原句作为依据，否则返回 None。

    逐句判断（不是全文 grep）：这样"我们不会拒绝优秀的候选人。不过这个岗位不太合适。"
    里第二句仍能命中，而第一句的否定不会把整段否掉。
    """
    for sentence in _sentences(text):
        if any(marker in sentence for marker in _NEGATION_MARKERS):
            continue
        for pattern in _REJECT_PATTERNS:
            if pattern in sentence:
                return RejectionSignal(reason=REASON_AUTO_REJECTED, evidence=sentence[:200])
    return None


def detect_rejections(texts: list[str]) -> RejectionSignal | None:
    """多段文本（如整个会话）里找第一条明确拒绝。"""
    for text in texts:
        signal = detect_rejection(text)
        if signal is not None:
            return signal
    return None


def should_blacklist(
    texts: list[str],
    *,
    company: str,
    already_blacklisted: bool = False,
) -> RejectionSignal | None:
    """决定要不要因为拒绝信号拉黑这家公司。

    `company` 为空时**不拉黑**：黑名单是按公司名匹配的，空名字会命中一堆无关岗位
    （`filters` 里的黑名单规则是 `company` 精确比对，空串会与空串相撞）。
    """
    if already_blacklisted or not (company or "").strip():
        return None
    return detect_rejections(texts)


@dataclass(frozen=True, slots=True)
class BlacklistCandidate:
    """一条"应该被拉黑"的判定结果（含依据，便于在界面上核对）。"""

    company: str
    platform: str
    reason: str
    evidence: str


def blacklist_candidates(
    rows: list[tuple[str, str, list[str]]],
    *,
    blacklisted: set[str] | None = None,
) -> list[BlacklistCandidate]:
    """从一批 `(公司, 平台, 相关文本)` 里挑出该拉黑的（去重、跳过已拉黑的）。

    **纯函数**：调用方负责查库把行准备好、并负责写回。这样"哪家公司会被拉黑、
    凭什么"能直接单测，不用起服务。

    公司名按"去空格 + 小写"归并：同一家公司在不同岗位卡片上的写法可能差一个空格，
    不归并会在黑名单里留下两条几乎一样的记录。
    """
    existing = blacklisted or set()
    seen: set[str] = set()
    found: list[BlacklistCandidate] = []

    for company, platform, texts in rows:
        name = (company or "").strip()
        key = name.lower()
        if not name or key in seen or key in existing:
            continue

        signal = detect_rejections(texts)
        if signal is None:
            continue

        seen.add(key)
        found.append(
            BlacklistCandidate(
                company=name,
                platform=platform or "",
                reason=signal.reason,
                evidence=signal.evidence,
            )
        )
    return found
