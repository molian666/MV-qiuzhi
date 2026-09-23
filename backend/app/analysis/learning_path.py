"""学习路径（纯函数）：把「我薄弱」与「岗位需要」合成一份有优先级的学习清单。

**为什么优先级要算而不是拍**：想学的东西永远比时间多。
如果优先级只凭"我感觉这块弱"，结果常常是学了三件不重要的事。
所以这里把三件可算的东西加权：

1. **薄弱程度**：掌握度低 + 训练正确率低。样本太少时降权——
   两次训练错一次不算"薄弱"，那只是运气。
2. **岗位需求程度**：这个技能在多少份 JD 里出现过、是硬性要求还是加分项。
3. **覆盖广度**：补这一项能顺带解决多少个**别的**缺口。
   「高并发」这种词覆盖到缓存、限流、分库分表、压测——补它一个顶四个。

每条建议都带一句**具体做法**，而不是"建议加强学习"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

#: 三块权重（加起来 1）
WEIGHT_WEAKNESS = 0.5
WEIGHT_DEMAND = 0.3
WEIGHT_BREADTH = 0.2

#: 训练次数低于这个值就只按掌握度估，并且打对折
MIN_ATTEMPTS = 3

#: 覆盖广度：补这一项能顺带覆盖哪些别的技能。
#: 手写的领域常识，不是模型猜的——猜出来的覆盖关系会把你带偏。
COVERAGE_MAP: dict[str, tuple[str, ...]] = {
    "高并发": ("缓存", "限流", "分库分表", "性能优化", "消息队列", "压测"),
    "分布式": ("一致性", "幂等", "消息队列", "分布式", "限流"),
    "微服务": ("服务治理", "链路追踪", "灰度发布", "限流", "熔断"),
    "性能优化": ("缓存", "SQL", "压测", "火焰图", "索引"),
    "Kubernetes": ("Docker", "CI/CD", "监控告警", "云原生"),
    "数据仓库": ("ETL", "SQL", "Hive", "Spark", "数据建模"),
    "机器学习": ("特征工程", "模型评估", "Python", "数据清洗"),
    "系统设计": ("架构设计", "容量规划", "高可用", "扩展性"),
    "算法": ("数据结构", "复杂度", "动态规划"),
    "消息队列": ("削峰", "解耦", "幂等", "顺序消息"),
    "缓存": ("穿透", "雪崩", "一致性", "热点"),
}

#: 各主题的估时（小时）。没列的用 DEFAULT_HOURS。
HOURS_MAP: dict[str, float] = {
    "高并发": 12,
    "分布式": 16,
    "微服务": 14,
    "性能优化": 10,
    "Kubernetes": 12,
    "Docker": 4,
    "数据仓库": 16,
    "机器学习": 20,
    "系统设计": 12,
    "算法": 20,
    "消息队列": 6,
    "缓存": 5,
    "Redis": 5,
    "MySQL": 6,
    "Kafka": 6,
}
DEFAULT_HOURS = 4.0

#: 具体做法模板（key 命中就用它，否则用通用模板）
ACTION_MAP: dict[str, str] = {
    "高并发": "做一个压测实验：自己写一个接口，用 wrk 压出瓶颈，再依次加缓存/限流/批量写，"
    "记录每次改动后的 QPS 与 P99。这条经历能直接写进简历。",
    "分布式": "把一个单机服务拆成两段并引入消息队列，重点处理幂等与顺序；"
    "写下你遇到的重复消费与补偿问题（面试最常追问的就是这个）。",
    "微服务": "为一个已有项目补上服务治理：加超时、重试、熔断与链路追踪，"
    "并记录一次人为注入故障后的恢复过程。",
    "性能优化": "挑一个慢接口做完整优化：先埋点定位瓶颈（SQL / 序列化 / 锁），"
    "再一项一项改，最后给出改动前后的对比数据。",
    "Kubernetes": "把一个本地项目容器化并部署到 minikube，练会探针、资源限制与滚动更新；"
    "重点是能讲清「探针配置错了会怎样」。",
    "系统设计": "用白板练三个经典题（短链、限流器、秒杀），每次都要写清容量估算、"
    "瓶颈、以及你**没有**做的取舍理由。",
    "算法": "按题型刷而不是按题号刷：每类先做 3 道理解模板，再各做 2 道变体；"
    "重点是把「为什么想到这个解法」讲出来。",
    "缓存": "把缓存穿透/击穿/雪崩三种场景各写一个最小复现，"
    "再分别给出解决方案与代价（面试最喜欢问代价）。",
    "消息队列": "做一个异步解耦的小改造，重点处理重复消费与消息积压；"
    "记录一次人为把消费者停掉后的表现。",
}
DEFAULT_ACTION = (
    "给「{topic}」做一件**能写进简历**的事：在自己的项目里用它解决一个真实问题，"
    "并记录改动前后的可核对数字。只读文档不算学会，面试也讲不出细节。"
)


@dataclass(slots=True)
class WeaknessInput:
    topic: str
    #: 掌握度 0–1（来自 M5 的指数滑动）
    mastery: float = 0.0
    #: 训练正确率 0–1
    accuracy: float = 0.0
    attempts: int = 0


@dataclass(slots=True)
class GapInput:
    word: str
    #: JD 权重（1.0 硬性 / 0.7 职责 / 0.5 加分）
    weight: float = 0.7
    #: 在多少份 JD 里出现过
    count: int = 1
    group: str = "other"


@dataclass(slots=True)
class LearningSuggestion:
    title: str
    topic: str = ""
    source: str = "manual"  # weakness | jd_gap | manual
    priority: float = 0.0
    reason: str = ""
    action: str = ""
    hours: float = DEFAULT_HOURS
    #: 顺带覆盖到的其它缺口
    covered_gaps: list[str] = field(default_factory=list)
    #: 优先级的三块明细（可解释，方便你不同意时说"我就想先学那个"）
    breakdown: dict[str, float] = field(default_factory=dict)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def weakness_score(item: WeaknessInput) -> float:
    """薄弱度 0–1。

    样本不足时只按掌握度、并且打个对折：两次训练错一次不代表薄弱，
    那可能只是陌生；把这个当成"薄弱"会让你去学一堆其实会的东西。
    """
    mastery_gap = 1.0 - _clamp(item.mastery)
    if item.attempts < MIN_ATTEMPTS:
        return round(mastery_gap * 0.5, 4)

    accuracy_gap = 1.0 - _clamp(item.accuracy)
    return round(mastery_gap * 0.6 + accuracy_gap * 0.4, 4)


def demand_score(item: GapInput, *, max_count: int = 1) -> float:
    """岗位需求度 0–1：出现得越多、越是硬性要求，分越高。"""
    peak = max(1, int(max_count or 1))
    frequency = min(1.0, float(item.count or 1) / peak)
    return round(_clamp(item.weight) * 0.7 + frequency * 0.3, 4)


def breadth_score(topic: str, gaps: Sequence[GapInput]) -> tuple[float, list[str]]:
    """覆盖广度 0–1，以及它顺带覆盖到了哪些缺口。"""
    related = set(COVERAGE_MAP.get(topic, ()))
    if not related or not gaps:
        return 0.0, []

    words = {item.word for item in gaps}
    covered = sorted(word for word in words if word in related or word == topic)
    # 归一化：覆盖到的缺口占全部缺口的比例（覆盖 3 个已经算很宽了）
    score = min(1.0, len(covered) / max(1, min(len(words), 4)))
    return round(score, 4), covered


def estimate_hours(topic: str) -> float:
    return float(HOURS_MAP.get(topic, DEFAULT_HOURS))


def action_for(topic: str) -> str:
    return ACTION_MAP.get(topic) or DEFAULT_ACTION.format(topic=topic)


def _title_for(topic: str, source: str) -> str:
    if source == "jd_gap":
        return f"补上「{topic}」（JD 里要，但你的简历没体现）"
    return f"补强「{topic}」"


def build_path(
    weaknesses: Iterable[WeaknessInput],
    gaps: Iterable[GapInput],
    *,
    limit: int = 8,
) -> list[LearningSuggestion]:
    """合成一份学习清单。

    同一个主题既薄弱又是岗位缺口时**合并成一条**（优先级取两者较高），
    否则清单里会出现「补强 Redis」和「补上 Redis」两条，看着像两件事。
    """
    gap_rows = [item for item in gaps if item.word]
    max_count = max([item.count for item in gap_rows], default=1)
    gaps_by_word: dict[str, GapInput] = {}
    for item in gap_rows:
        existing = gaps_by_word.get(item.word)
        if existing is None or (item.weight, item.count) > (existing.weight, existing.count):
            gaps_by_word[item.word] = item

    merged: dict[str, LearningSuggestion] = {}

    for item in weaknesses:
        topic = (item.topic or "").strip()
        if not topic:
            continue
        weak = weakness_score(item)
        gap = gaps_by_word.get(topic)
        demand = demand_score(gap, max_count=max_count) if gap else 0.0
        breadth, covered = breadth_score(topic, gap_rows)

        priority = WEIGHT_WEAKNESS * weak + WEIGHT_DEMAND * demand + WEIGHT_BREADTH * breadth
        reasons = [f"掌握度 {round(_clamp(item.mastery) * 100)}%，训练正确率 {round(_clamp(item.accuracy) * 100)}%"]
        if item.attempts < MIN_ATTEMPTS:
            reasons.append(f"样本少（{item.attempts} 次），已按保守估计处理")
        if gap:
            reasons.append(f"这个岗位把它列为{'硬性要求' if gap.weight >= 1 else '加分项'}")

        merged[topic] = LearningSuggestion(
            title=_title_for(topic, "weakness" if not gap else "jd_gap"),
            topic=topic,
            source="jd_gap" if gap else "weakness",
            priority=round(priority * 100, 1),
            reason="；".join(reasons),
            action=action_for(topic),
            hours=estimate_hours(topic),
            covered_gaps=covered,
            breakdown={
                "weakness": round(weak, 4),
                "demand": round(demand, 4),
                "breadth": round(breadth, 4),
            },
        )

    for word, gap in gaps_by_word.items():
        if word in merged:
            continue
        demand = demand_score(gap, max_count=max_count)
        breadth, covered = breadth_score(word, gap_rows)
        priority = WEIGHT_DEMAND * demand + WEIGHT_BREADTH * breadth

        reasons = [
            f"在 {gap.count} 份 JD 里出现过，{'硬性要求' if gap.weight >= 1 else '职责或加分项'}"
        ]
        if not breadth:
            reasons.append("简历与练习里都没有它的影子")

        merged[word] = LearningSuggestion(
            title=_title_for(word, "jd_gap"),
            topic=word,
            source="jd_gap",
            priority=round(priority * 100, 1),
            reason="；".join(reasons),
            action=action_for(word),
            hours=estimate_hours(word),
            covered_gaps=covered,
            breakdown={"weakness": 0.0, "demand": round(demand, 4), "breadth": round(breadth, 4)},
        )

    rows = sorted(merged.values(), key=lambda item: (-item.priority, item.topic))
    return rows[: max(1, limit)]


def summarize_path(rows: Sequence[LearningSuggestion]) -> dict[str, Any]:
    """给前端的一句话概览。"""
    if not rows:
        return {"count": 0, "hours": 0.0, "top": "", "note": "还没有可推荐的项：先去练几场面试。"}

    total_hours = round(sum(item.hours for item in rows), 1)
    weakest = [item.topic for item in rows if item.breakdown.get("weakness", 0) >= 0.5][:3]
    note = f"共 {len(rows)} 项、约 {total_hours} 小时"
    if weakest:
        note += f"；最该补的是：{'、'.join(weakest)}"
    return {"count": len(rows), "hours": total_hours, "top": rows[0].topic, "note": note}
