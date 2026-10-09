"""自动通过闸门的策略（纯函数）。

**为什么这件事必须是纯函数**：自动投递是本项目里唯一"程序替你做了不可撤销决定"的能力。
所以"哪一条被自动投了、凭什么"必须能脱离数据库与网络直接单测——用户事后要能审计，
而审计的前提是判定逻辑本身可复现。

三个刻意的约束：

1. **分数阈值是硬的**，不做"按比例取前 N 名"。按比例取会把低分岗位也推进去
   （投递量少的时候，"前 3 名"里可能全是 40 分）。阈值让"不够好就不投"成立。
2. **每条都留下理由**。`AutoApproval.reason` 会被写进任务，用户能在队列里看到
   "因为分数 78 ≥ 70，且没有规则拦截"。
3. **被过滤链拦下的一律不通过**，哪怕分数很高——过滤链表达的是用户的明确意图
   （黑名单、猎头、HR 不活跃…），不该被一个高分覆盖。

`limit` 与 `daily_remaining` 双重截断：自动模式的额度**独立于**手动，默认更低，
因为无人值守时人不会在旁边发现异常。
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: 默认阈值。低于它的岗位在自动模式下不投——宁愿少投，也不要投一堆不匹配的。
DEFAULT_MIN_SCORE = 70.0

#: 自动模式一次最多通过多少条（比手动低：无人值守时没人看着）
DEFAULT_MAX_PER_RUN = 5

#: 自动模式每日上限（比手动 30 条低得多）
DEFAULT_DAILY_LIMIT = 10


@dataclass(frozen=True, slots=True)
class AutoApproval:
    """一条"被自动通过"的判定，带可审计的理由。"""

    task_id: int
    score: float
    reason: str


@dataclass(frozen=True, slots=True)
class Candidate:
    """自动审批需要看到的候选信息（与 ORM 解耦，方便单测）。"""

    task_id: int
    matched_score: float
    #: 过滤链是否放行（False = 被规则拦下，一律不自动通过）
    passed: bool = True
    #: 排队里已有的状态；非 pending 的一律不动
    status: str = "pending"


@dataclass(slots=True)
class ApprovalOutcome:
    approved: list[AutoApproval] = field(default_factory=list)
    skipped: list[tuple[int, str]] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.approved)


def select_auto_approvals(
    candidates: list[Candidate],
    *,
    min_score: float = DEFAULT_MIN_SCORE,
    max_per_run: int = DEFAULT_MAX_PER_RUN,
    daily_remaining: int | None = None,
) -> ApprovalOutcome:
    """挑出可以被自动投递的候选。

    按分数从高到低取，取满 `max_per_run` 或额度为止。
    返回的每一条都带理由，没被选中的也带原因（便于在界面上解释"为什么没投它"）。
    """
    outcome = ApprovalOutcome()

    budget = max(0, int(max_per_run))
    if daily_remaining is not None:
        budget = min(budget, max(0, int(daily_remaining)))
    if budget <= 0:
        for item in candidates:
            outcome.skipped.append((item.task_id, "自动投递额度已用完"))
        return outcome

    # 分数降序；同分时按 task_id 升序，保证结果**可复现**（否则同分岗位的顺序取决于
    # 查询返回顺序，同一个库两次跑可能投出不同的集合）
    ordered = sorted(candidates, key=lambda item: (-item.matched_score, item.task_id))

    for item in ordered:
        if len(outcome.approved) >= budget:
            outcome.skipped.append((item.task_id, "本次自动通过的名额已用完"))
            continue
        if item.status != "pending":
            outcome.skipped.append((item.task_id, f"状态是 {item.status}，不在待确认队列里"))
            continue
        if not item.passed:
            outcome.skipped.append((item.task_id, "被过滤规则拦下"))
            continue
        if item.matched_score < min_score:
            outcome.skipped.append(
                (item.task_id, f"匹配分 {item.matched_score} 低于自动投递阈值 {min_score:g}")
            )
            continue
        outcome.approved.append(
            AutoApproval(
                task_id=item.task_id,
                score=item.matched_score,
                reason=f"匹配分 {item.matched_score:g} ≥ {min_score:g}，且没有规则拦截（自动投递）",
            )
        )

    return outcome
