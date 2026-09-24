"""入职清单（M15）：默认清单目录与进度统计。

默认清单是「整理过的常识」，不是权威指引——每条都写「为什么需要 + 去哪里办」，
涉及协议/政策的内容只说「该确认什么」，不下结论（非法律意见）。
纯函数：不碰 DB / 网络 / 文件；service 层负责落库与勾选。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from ..models.career import ONBOARDING_CATEGORIES


@dataclass(slots=True)
class TaskSpec:
    """一条默认清单项（种子数据用，落库后就是 OnboardingTask 的字段）。"""

    category: str
    title: str
    why_needed: str
    where_to_do: str


def default_tasks() -> list[TaskSpec]:
    """内置的入职清单目录。用户可以增删改——这里只是起点。"""
    return [
        # ---- 背调材料 ----
        TaskSpec(
            "背调材料",
            "确认背调范围与授权书",
            "提前知道查什么（履历、时间、头衔、离职原因），避免你填的和前公司说的对不上",
            "新公司 HR 或背调机构发来的授权邮件，逐条核对授权范围",
        ),
        TaskSpec(
            "背调材料",
            "约好前公司证明人",
            "背调通常要 2–3 位证明人（直属上级优先），不提前打招呼，对方容易措手不及或说错重点",
            "提前微信/电话征得同意，确认对方方便接听的时间",
        ),
        TaskSpec(
            "背调材料",
            "准备薪资流水或在职证明（如要求）",
            "部分公司用它核对你在面试里申报的薪资是否属实",
            "银行 APP 导出近 6–12 个月流水，或找前公司 HR 开在职证明",
        ),
        # ---- 体检 ----
        TaskSpec(
            "体检",
            "预约入职体检",
            "多数公司要求近 1–3 个月内的体检报告，体检高峰期排队会耽误入职日期",
            "公司指定医院；没指定就选三甲医院体检中心，带身份证和一寸照片",
        ),
        TaskSpec(
            "体检",
            "核对体检项目清单",
            "通用套餐可能不含公司要求的必查项，漏项复检要再跑一趟",
            "向新公司 HR 要项目清单，预约时逐项对照",
        ),
        # ---- 学历与离职证明 ----
        TaskSpec(
            "学历与离职证明",
            "下载学信网学历验证报告",
            "学历核验几乎是标配；验证报告一般 30 天有效，别太早下载",
            "学信网 chsi.com.cn → 申请《教育部学历证书电子注册备案表》",
        ),
        TaskSpec(
            "学历与离职证明",
            "开具离职证明",
            "证明你已与前公司解除劳动关系，入职当天常要交原件",
            "前公司 HR 开具，写明入职/离职日期与岗位；自己留一份扫描件",
        ),
        TaskSpec(
            "学历与离职证明",
            "准备毕业证、学位证扫描件",
            "入职材料与学信网核验都要用，提前扫描清晰版本",
            "自己扫描存档，按 HR 要求的格式命名",
        ),
        # ---- 社保与公积金 ----
        TaskSpec(
            "社保与公积金",
            "确认前公司已办停缴（减员）",
            "前公司没停缴，新公司就无法给你正常增员，影响当月社保",
            "当地人社 APP / 支付宝市民中心 / 拨 12333 查参保状态",
        ),
        TaskSpec(
            "社保与公积金",
            "办理社保转移（跨城市换工作时）",
            "养老与医疗保险的缴费年限要累计才连续，中断影响报销与购房落户等资格",
            "转入地社保局或掌上 12333 APP 申请转移，新公司 HR 可协办",
        ),
        TaskSpec(
            "社保与公积金",
            "处理公积金账户（转移 / 提取 / 封存）",
            "公积金贷款资格通常看连续缴存，怎么处理要提前想清楚",
            "全国住房公积金小程序可办转移接续，具体政策问两地公积金中心",
        ),
        # ---- 离职交接 ----
        TaskSpec(
            "离职交接",
            "列工作与账号交接清单",
            "交接干净既是对下一个人负责，也关系到你的职业口碑与背调评价",
            "和直属上级确认交接文档、接收人与账号权限回收时间表",
        ),
        TaskSpec(
            "离职交接",
            "翻出竞业与保密协议看一遍范围",
            "知道自己签过什么、有没有补偿条款，入职新公司前心里有数（非法律意见，拿不准找专业人士）",
            "查自己的签署记录（邮箱/前公司 HR），新公司若问竞业情况如实说明",
        ),
        TaskSpec(
            "离职交接",
            "结清报销与未休假期",
            "离职后再补报销、折算假期，沟通成本高很多",
            "前公司 OA / 财务，离职最后一天前提交",
        ),
        # ---- 租房与落户 ----
        TaskSpec(
            "租房与落户",
            "查目标城市的落户与人才政策",
            "各城市人才引进、补贴、落户条件差异大且常有时限，错过不补",
            "目标城市人社局官网 / 人才服务中心；新公司 HR 也常能协助",
        ),
        TaskSpec(
            "租房与落户",
            "落实住处",
            "通勤时长直接影响试用期的状态，刚入职就长途奔波很伤",
            "入职前实地看房；来不及就先短租一个月，入职后再慢慢找",
        ),
        # ---- 入职首日 ----
        TaskSpec(
            "入职首日",
            "按 HR 邮件核对报到材料",
            "缺材料当天办不了入职，白跑一趟",
            "把 HR 的报到清单打印出来逐项打勾，出发前再查一遍",
        ),
        TaskSpec(
            "入职首日",
            "办工资卡与照片",
            "代发工资常指定银行；工牌与社保要用照片",
            "按 HR 要求办一类卡；备一寸/二寸证件照电子版与纸质版",
        ),
    ]


def checklist_progress(tasks: Sequence[Any]) -> dict[str, Any]:
    """清单进度：总数、已完成、百分比、按分类统计。

    接受任何带 category / done 属性的对象（OnboardingTask 或测试用的 NS）。
    分类顺序沿用 ONBOARDING_CATEGORIES，用户新建的自定义分类排在最后（按出现顺序）。
    """
    by_category: dict[str, dict[str, int]] = {}
    for category in ONBOARDING_CATEGORIES:
        by_category[category] = {"total": 0, "done": 0}

    total = 0
    done = 0
    for task in tasks:
        category = getattr(task, "category", "") or "其它"
        done_flag = bool(getattr(task, "done", False))
        if category not in by_category:
            by_category[category] = {"total": 0, "done": 0}
        by_category[category]["total"] += 1
        if done_flag:
            by_category[category]["done"] += 1
            done += 1
        total += 1

    categories = [
        {"category": category, **counts}
        for category, counts in by_category.items()
        if counts["total"] > 0
    ]
    pct = round(done / total * 100) if total else 0
    return {
        "total": total,
        "done": done,
        "remaining": total - done,
        "pct": pct,
        "complete": total > 0 and done == total,
        "by_category": categories,
    }
