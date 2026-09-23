"""公司风险提示：只给信号、原文摘录与核实渠道，**不下结论**。

PLAN §6.9 的纪律：这类判断涉及法律与声誉，工具不该下结论。
所以这里做的是「把这些信号指出来，并告诉你该去哪里核实」，
而不是「这家公司是骗子」——后者需要证据和责任主体，我们两者都没有。

每条提示都带：命中原文（你可以自己看上下文）、为什么值得注意、可执行的下一步、
以及能去哪个官方渠道核实。

`SourceRef.verified` 永远是 False：工具没有访问过这些来源，
标注出来比含糊过去更负责任。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence


@dataclass(frozen=True, slots=True)
class SourceRef:
    label: str
    url: str
    what: str
    #: 工具**没有访问过**这些来源，所以永远是 False（诚实标注，不是占位符）
    verified: bool = False


SOURCES: tuple[SourceRef, ...] = (
    SourceRef(
        "国家企业信用信息公示系统",
        "https://www.gsxt.gov.cn/",
        "工商注册信息、股东、行政处罚、经营异常名录",
    ),
    SourceRef(
        "中国裁判文书网",
        "https://wenshu.court.gov.cn/",
        "涉诉记录（劳动争议、欠薪、合同纠纷等）",
    ),
    SourceRef(
        "中国执行信息公开网",
        "http://zxgk.court.gov.cn/",
        "是否被列为失信被执行人",
    ),
    SourceRef(
        "国家社会保险公共服务平台",
        "http://si.12333.gov.cn/",
        "社保缴纳记录（入职后核对是否按实际工资缴纳）",
    ),
    SourceRef(
        "招聘平台企业主页",
        "https://www.zhipin.com/",
        "企业认证信息、在职规模、历史招聘岗位与频率",
    ),
    SourceRef(
        "脉脉 / 知乎等社区",
        "https://www.maimai.cn/",
        "员工评价——这类内容**未经核实**，只能当线索，不能当证据",
    ),
)

SOURCE_LABELS: tuple[str, ...] = tuple(source.label for source in SOURCES)


@dataclass(slots=True)
class RiskSignal:
    key: str
    label: str
    severity: str  # warn | info
    why: str
    action: str
    sources: list[str] = field(default_factory=list)
    matched: list[str] = field(default_factory=list)
    disclaimer: str = "这只是文本层面的信号，不代表事实；请用下面的官方渠道核实后再判断。"


@dataclass(slots=True)
class CompanyRiskReport:
    signals: list[RiskSignal] = field(default_factory=list)
    sources: list[SourceRef] = field(default_factory=list)
    disclaimer: str = ""
    checked_chars: int = 0


#: 每条规则都是「值得停下来问一句」的信号，不是「这就是骗子公司」
RULES: tuple[dict[str, Any], ...] = (
    {
        "key": "upfront_fee",
        "label": "入职前收费",
        "severity": "warn",
        "patterns": ("培训费", "押金", "保证金", "服装费", "工牌费", "体检费自理", "先交", "付费培训"),
        "why": "正规用人单位不会向求职者收取任何费用。收费是招聘诈骗最常见的入口。",
        "action": "直接问：这笔钱用于什么、能不能入职后从工资里扣。对方含糊或坚持先付就别继续了。",
        "sources": ("国家企业信用信息公示系统", "脉脉 / 知乎等社区"),
    },
    {
        "key": "paid_referral",
        "label": "付费内推 / 包就业",
        "severity": "warn",
        "patterns": ("付费内推", "内推费", "简历费", "保offer", "保过", "包就业"),
        "why": "收费的内推与「包就业」通常与真实招聘无关；承诺「保过」本身就是不可能的事。",
        "action": "去招聘平台核对这个岗位是否真实在招；真实的员工内推不会向你收钱。",
        "sources": ("招聘平台企业主页", "脉脉 / 知乎等社区"),
    },
    {
        "key": "no_contract",
        "label": "合同与社保异常",
        "severity": "warn",
        "patterns": ("不签合同", "先试岗", "试用期不上社保", "转正后补缴", "不缴社保", "无社保"),
        "why": "不签书面合同、试用期不缴社保是明确违规的做法，也会影响你的落户、贷款与工龄认定。",
        "action": "问清：劳动合同什么时候签、社保公积金从哪个月开始、按什么基数缴，并要求写进 offer 或合同。",
        "sources": ("国家社会保险公共服务平台", "中国裁判文书网"),
    },
    {
        "key": "urgency",
        "label": "催促当天决定",
        "severity": "warn",
        "patterns": ("当天面试当天入职", "今天就要答复", "名额有限", "限时", "马上到岗", "急招"),
        "why": "越是催你当场决定，越说明它不希望你去做背景核实、也不希望你比较其它机会。",
        "action": "明确说「我需要看合同和书面 offer 再决定」，观察对方反应——这本身就是重要信息。",
        "sources": ("国家企业信用信息公示系统", "脉脉 / 知乎等社区"),
    },
    {
        "key": "outsourcing",
        "label": "外包 / 派遣 / 驻场",
        "severity": "info",
        "patterns": ("外包", "外派", "驻场", "派遣", "人服", "人力资源服务"),
        "why": "外包与驻场不一定是坏选择，但它影响你的合同主体、社保基数、涨薪路径与简历归属。",
        "action": "问清：劳动合同跟谁签、有没有转正编的机会与比例、项目结束怎么安排。",
        "sources": ("招聘平台企业主页", "国家企业信用信息公示系统"),
    },
    {
        "key": "vague_salary",
        "label": "薪资表述含糊",
        "severity": "info",
        "patterns": ("上不封顶", "多劳多得", "高提成", "底薪加提成", "面议", "看能力给"),
        "why": "只谈「上不封顶」不谈底薪，意味着大部分收入不确定；这类岗位的流动率通常也偏高。",
        "action": "问清底薪、提成比例、达成条件，以及近半年该岗位的实际平均收入。",
        "sources": ("脉脉 / 知乎等社区", "招聘平台企业主页"),
    },
    {
        "key": "overtime",
        "label": "加班与作息提示",
        "severity": "info",
        "patterns": ("大小周", "无加班费", "弹性工作", "随时待命", "狼性", "能吃苦", "不计较加班"),
        "why": "「弹性」「狼性」这类词往往意味着没有明确的工时边界。它不会写进合同，但会决定你的生活。",
        "action": "问清：日常下班时间、周末是否需到岗、有没有调休或加班费、近三个月的实际工时。",
        "sources": ("脉脉 / 知乎等社区",),
    },
    {
        "key": "equity_only",
        "label": "只谈股权不谈现金",
        "severity": "info",
        "patterns": ("期权", "股权激励", "画饼", "未来上市", "合伙人"),
        "why": "股权是长期变量，现金才是当月账单。只看股权容易低估风险。",
        "action": "问清：授予数量与占比、行权价、归属节奏、离职时已授予未归属部分怎么处理。",
        "sources": ("国家企业信用信息公示系统", "招聘平台企业主页"),
    },
    {
        "key": "no_interview",
        "label": "几乎不面就录用",
        "severity": "warn",
        "patterns": ("无需面试", "面试简单", "当天入职", "零经验可做", "不限学历不限经验"),
        "why": "正常的用人流程一定会评估你。几乎不面试就录用，往往意味着这个岗位没有人愿意做。",
        "action": "问清这个岗位招了多久、上一任在职多久、每天具体做什么。",
        "sources": ("招聘平台企业主页", "脉脉 / 知乎等社区"),
    },
)

DEFAULT_DISCLAIMER = (
    "以上只是**文本层面的信号**，不代表任何结论。"
    "同一句话在不同上下文里的含义可以完全相反——请务必用下面的官方渠道核实，再做判断。"
)


def scan_text(text: str, *, sources: Sequence[SourceRef] | None = None) -> CompanyRiskReport:
    """扫一段 JD / 聊天记录 / offer 文本，找出值得停下来问一句的信号。"""
    content = text or ""
    report = CompanyRiskReport(
        sources=list(sources or SOURCES),
        disclaimer=DEFAULT_DISCLAIMER,
        checked_chars=len(content.strip()),
    )

    for rule in RULES:
        matched = [pattern for pattern in rule["patterns"] if pattern in content]
        if not matched:
            continue
        report.signals.append(
            RiskSignal(
                key=str(rule["key"]),
                label=str(rule["label"]),
                severity=str(rule["severity"]),
                why=str(rule["why"]),
                action=str(rule["action"]),
                sources=[label for label in rule.get("sources", ()) if label in SOURCE_LABELS],
                matched=matched,
            )
        )

    # 严重的排前面
    report.signals.sort(key=lambda item: (0 if item.severity == "warn" else 1, item.key))
    return report


def source_options() -> list[dict[str, Any]]:
    return [
        {"label": source.label, "url": source.url, "what": source.what, "verified": source.verified}
        for source in SOURCES
    ]


def signal_catalog() -> list[dict[str, Any]]:
    """全部规则（前端可以展示「我们会看哪些信号」）。"""
    return [
        {
            "key": str(rule["key"]),
            "label": str(rule["label"]),
            "severity": str(rule["severity"]),
            "why": str(rule["why"]),
            "action": str(rule["action"]),
            "sources": [label for label in rule.get("sources", ()) if label in SOURCE_LABELS],
        }
        for rule in RULES
    ]
