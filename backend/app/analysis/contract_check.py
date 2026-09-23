"""合同与竞业提醒：**这不是法律意见**。

这里做的是「把该问清楚的问题列出来」，而不是「判断条款是否合法」——
判断合法性需要律师看完整合同并结合当地规定，工具做不到，也不该假装能做到。

所以每一条都给一句**可以直接拿去问 HR 的话**，以及一句常见坑的提示。
措辞上刻意避免「违法」「无效」这类结论性判断：那些说法需要依据，
而依据不在这个工具的能力范围内。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ContractClause:
    key: str
    label: str
    severity: str  # must_ask | should_ask
    question: str
    why: str
    trap: str
    #: 合同/offer 文本里可能出现的关键词
    patterns: tuple[str, ...] = ()


CLAUSES: tuple[ContractClause, ...] = (
    ContractClause(
        key="salary_structure",
        label="薪资构成与发放条件",
        severity="must_ask",
        question="月薪、年终奖、绩效各占多少？年终奖的发放条件与时间是什么？",
        why="很多薪资争议都出在「年终奖要不要发」上：写清构成与条件，后面才说得清。",
        trap="常见写法是「年终奖发放时需在职」——如果你打算年中离职，这一条会直接决定你损失多少。",
        patterns=("年终奖", "绩效", "13薪", "14薪", "奖金"),
    ),
    ContractClause(
        key="probation",
        label="试用期时长与待遇",
        severity="must_ask",
        question="试用期多久？试用期工资是多少？考核标准是什么、由谁评？",
        why="试用期时长与试用期工资下限都有规定，但你需要知道自己的具体数字才算得清。",
        trap="常见坑是试用期工资「转正后补发」，或者考核标准含糊——含糊的考核标准等于随时可延长。",
        patterns=("试用期", "实习期", "考察期"),
    ),
    ContractClause(
        key="social_insurance",
        label="社保公积金基数与起缴月",
        severity="must_ask",
        question="社保公积金按什么基数缴？从入职当月还是次月开始？",
        why="基数直接影响你的公积金贷款额度、医保报销与落户积分，它是实打实的钱。",
        trap="按最低基数缴是普遍做法，但它会明显压低你的实际收益——先问清楚再比较 offer。",
        patterns=("社保", "公积金", "五险一金", "六险一金"),
    ),
    ContractClause(
        key="equity",
        label="股权 / 期权条款",
        severity="must_ask",
        question="授予多少、行权价多少、分几年归属、离职时已授予未归属的部分怎么处理？",
        why="股权的价值全在这四个数字里，缺一个就没法估算。",
        trap="常见坑是「离职即全部作废」与「回购价格由公司单方决定」——这两条会显著改变股权的实际价值。",
        patterns=("期权", "股权", "限制性股票", "RSU", "ESOP"),
    ),
    ContractClause(
        key="non_compete",
        label="竞业限制",
        severity="must_ask",
        question="是否有竞业限制？期限多久、范围包括哪些公司、补偿金是多少、怎么发？",
        why="竞业限制会直接限制你离职后的去向，而且它需要伴随补偿才谈得上合理。",
        trap="常见坑是范围写得极宽（例如「所有互联网公司」）或没写补偿标准——这两点都值得当场问清楚。",
        patterns=("竞业", "竞业限制", "保密协议", "限制从业"),
    ),
    ContractClause(
        key="ip_clause",
        label="知识产权归属",
        severity="should_ask",
        question="职务作品的归属范围是什么？我在业余时间做的项目算不算？",
        why="如果条款写得过宽，可能连你下班后的个人项目都被算进去。",
        trap="常见坑是「与公司业务相关的所有成果」这种宽泛表述——个人副业最容易撞上这一条。",
        patterns=("知识产权", "职务作品", "专利", "著作权"),
    ),
    ContractClause(
        key="overtime",
        label="工时制度与加班",
        severity="should_ask",
        question="实行哪种工时制度？加班有没有调休或加班费？日常下班时间大概是几点？",
        why="工时制度决定了你的时间边界，而它往往只写在合同的一句话里，容易被忽略。",
        trap="「弹性工作制」「综合计算工时」这类写法可能意味着没有固定下班时间，也未必有加班费。",
        patterns=("工时", "加班", "弹性工作", "综合计算工时", "不定时"),
    ),
    ContractClause(
        key="termination",
        label="解除与赔偿责任",
        severity="should_ask",
        question="双方各自解除合同的条件是什么？有没有约定违约金？",
        why="离职条款决定你走的时候会不会被卡——而离职比入职更容易出现争议。",
        trap="普通岗位约定「离职赔钱」的条款值得当场问清依据；只有特定情形才谈得上服务期违约。",
        patterns=("违约金", "服务期", "解除", "赔偿", "离职"),
    ),
    ContractClause(
        key="training_bond",
        label="培训与服务期",
        severity="should_ask",
        question="培训是公司出资的专项培训吗？服务期多久？违约金怎么算？",
        why="服务期违约金通常只有在公司真的出资做过专项培训时才站得住。",
        trap="常见坑是把「入职培训」「内部学习平台账号」也算成专项培训，并要求服务期。",
        patterns=("服务期", "培训协议", "出资培训"),
    ),
    ContractClause(
        key="relocation",
        label="工作地点与调岗",
        severity="should_ask",
        question="工作地点写的是哪？公司能否单方调岗或外派？",
        why="工作地点写在合同里，它决定你的通勤与生活半径；调岗条款决定你被调走的可能性。",
        trap="常见坑是地点写成「公司及关联公司所在地」——这给了很大的单方调整空间。",
        patterns=("工作地点", "调岗", "外派", "出差"),
    ),
)

CLAUSE_KEYS: tuple[str, ...] = tuple(clause.key for clause in CLAUSES)

DISCLAIMER = (
    "**这不是法律意见，也不能替代律师。** "
    "上面列的是「值得问清楚的问题」与常见写法提示，不是对条款合法性的判断——"
    "那需要律师看完整合同并结合当地规定。真要签署前，请以合同原文与专业意见为准。"
)


@dataclass(slots=True)
class ClauseHit:
    clause: ContractClause
    matched: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ContractReport:
    #: 在文本里提到过的条款（优先问这些）
    mentioned: list[ClauseHit] = field(default_factory=list)
    #: 通用清单（按该问的优先级排序）
    checklist: list[ClauseHit] = field(default_factory=list)
    #: 合同里完全没提到、但你应该主动问的
    missing: list[ClauseHit] = field(default_factory=list)
    disclaimer: str = DISCLAIMER
    checked_chars: int = 0


def _order(clause: ContractClause) -> tuple[int, str]:
    return ({"must_ask": 0, "should_ask": 1}.get(clause.severity, 2), clause.key)


def build_report(text: str = "") -> ContractReport:
    """没给合同文本时也返回完整清单——「还没拿到合同」正是最该先看清单的时候。"""
    content = text or ""
    report = ContractReport(checked_chars=len(content.strip()))

    mentioned: list[ClauseHit] = []
    missing: list[ClauseHit] = []
    for clause in CLAUSES:
        matched = [pattern for pattern in clause.patterns if pattern in content]
        hit = ClauseHit(clause=clause, matched=matched)
        (mentioned if matched else missing).append(hit)

    report.mentioned = sorted(mentioned, key=lambda item: _order(item.clause))
    report.missing = sorted(missing, key=lambda item: _order(item.clause))
    report.checklist = [ClauseHit(clause=clause) for clause in sorted(CLAUSES, key=_order)]
    return report


def clause_options() -> list[dict[str, Any]]:
    return [
        {
            "key": clause.key,
            "label": clause.label,
            "severity": clause.severity,
            "question": clause.question,
            "why": clause.why,
            "trap": clause.trap,
        }
        for clause in CLAUSES
    ]
