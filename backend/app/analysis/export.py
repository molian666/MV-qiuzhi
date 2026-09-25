"""导出（M15）：把各表的数据转成结构化中间格式，再渲染成 Markdown / CSV / JSON。

核心设计：**先转中间结构（ExportBundle）再渲染**。
「导出内容完整」要能被单测钉住——测试直接断言 bundle 里的板块、列和行，
而不是去解析渲染出来的 Markdown（脆弱，而且测不出缺表缺列）。
渲染层只做格式化，不参与数据取舍；数据取舍全部发生在 build_* 构造器里。

纯函数约定：本模块不 import models、不碰 DB / 网络 / 文件。
构造器接受"任何具有对应属性的对象"（SQLModel 实例、SimpleNamespace、dict 都行），
需要 join 的列（公司名、题目文本、联系人名……）由调用方先拼好再传进来。
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Sequence


# --------------------------------------------------------------------------- #
# 中间格式
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class ExportColumn:
    """一列：取值键 + 表头。"""

    key: str
    header: str


@dataclass(slots=True)
class ExportSection:
    """一个板块：一张同构的表。"""

    key: str
    title: str
    columns: list[ExportColumn] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return len(self.rows)


@dataclass(slots=True)
class ExportBundle:
    """一次全量导出的中间结构。"""

    generated_at: datetime
    sections: list[ExportSection] = field(default_factory=list)

    def section(self, key: str) -> ExportSection | None:
        for section in self.sections:
            if section.key == key:
                return section
        return None

    @property
    def row_count(self) -> int:
        return sum(section.row_count for section in self.sections)


def _get(row: Any, key: str) -> Any:
    """从 dict 或对象里取一列，缺列返回 None（渲染层会变成空串）。"""
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


def _section(key: str, title: str, columns: Sequence[tuple[str, str]], rows: Sequence[Any]) -> ExportSection:
    """按 [(字段, 表头)] 直接映射字段名构造板块（大部分表都用这个）。"""
    return ExportSection(
        key=key,
        title=title,
        columns=[ExportColumn(field_key, header) for field_key, header in columns],
        rows=[{field_key: _get(row, field_key) for field_key, _ in columns} for row in rows],
    )


# --------------------------------------------------------------------------- #
# 各板块构造器（字段取舍在这里定，测试钉住这里）
# --------------------------------------------------------------------------- #
def build_targets(rows: Sequence[Any]) -> ExportSection:
    return _section(
        "targets",
        "目标岗位",
        [
            ("company", "公司"),
            ("title", "岗位"),
            ("level", "层级"),
            ("jd_url", "JD 链接"),
            ("tags", "标签"),
            ("created_at", "创建时间"),
        ],
        rows,
    )


def build_resumes(rows: Sequence[Any]) -> ExportSection:
    # 简历正文全量带出：导出的意义之一就是备份，截断的备份是假备份
    return _section(
        "resumes",
        "简历与版本",
        [
            ("name", "名称"),
            ("version", "版本号"),
            ("variant_label", "版本标注"),
            ("target_job_id", "关联岗位 ID"),
            ("content_text", "简历正文"),
            ("created_at", "创建时间"),
        ],
        rows,
    )


def build_applications(rows: Sequence[Any]) -> ExportSection:
    return _section(
        "applications",
        "投递记录",
        [
            ("applied_at", "投递时间"),
            ("company", "公司"),
            ("title", "岗位"),
            ("channel", "渠道"),
            ("status", "状态"),
            ("salary_range", "薪资范围"),
            ("city", "城市"),
            ("resume_version", "简历版本"),
            ("jd_url", "链接"),
            ("note", "备注"),
        ],
        rows,
    )


def build_application_events(rows: Sequence[dict[str, Any]]) -> ExportSection:
    # 调用方先把 application_id 换成 company 字段（事件表只有外键，公司名在投递表）
    return _section(
        "application_events",
        "投递状态流转",
        [
            ("at", "时间"),
            ("company", "公司"),
            ("from_status", "从"),
            ("to_status", "到"),
            ("raw_ref", "来源线索"),
        ],
        rows,
    )


def build_sessions(rows: Sequence[Any]) -> ExportSection:
    # 谈判模拟复用 interview_session（mode="negotiation"），mode 列会把两类区分开
    return _section(
        "sessions",
        "面试与谈判会话",
        [
            ("id", "会话 ID"),
            ("created_at", "创建时间"),
            ("mode", "模式"),
            ("status", "状态"),
            ("duration_ms", "时长(ms)"),
            ("summary", "小结"),
        ],
        rows,
    )


def build_turns(rows: Sequence[Any]) -> ExportSection:
    return _section(
        "turns",
        "逐轮问答",
        [
            ("session_id", "会话 ID"),
            ("idx", "序号"),
            ("speaker", "角色"),
            ("kind", "类型"),
            ("topic", "主题"),
            ("text", "内容"),
            ("created_at", "时间"),
        ],
        rows,
    )


def build_reports(rows: Sequence[Any]) -> ExportSection:
    return _section(
        "reports",
        "评分报告",
        [
            ("generated_at", "生成时间"),
            ("session_id", "会话 ID"),
            ("recording_id", "录音 ID"),
            ("overall", "总分"),
            ("dimension_scores", "维度分"),
            ("summary", "小结"),
        ],
        rows,
    )


def build_improvements(rows: Sequence[Any]) -> ExportSection:
    return _section(
        "improvements",
        "改进项",
        [
            ("report_id", "报告 ID"),
            ("severity", "严重度"),
            ("dimension", "维度"),
            ("problem", "问题"),
            ("suggestion", "建议"),
            ("drill", "立刻可做的练习"),
            ("topics", "标签"),
        ],
        rows,
    )


def build_expression(rows: Sequence[Any]) -> ExportSection:
    return _section(
        "expression",
        "表达体检",
        [
            ("generated_at", "生成时间"),
            ("session_id", "会话 ID"),
            ("recording_id", "录音 ID"),
            ("overall", "表达分"),
            ("summary", "小结"),
            ("has_audio", "含音频"),
        ],
        rows,
    )


def build_mastery(rows: Sequence[dict[str, Any]]) -> ExportSection:
    # 调用方把 topic_id join 成 topic / category（名字在 skill_topic 表）
    return _section(
        "mastery",
        "薄弱点与掌握度",
        [
            ("topic", "主题"),
            ("category", "分类"),
            ("mastery", "掌握度"),
            ("attempts", "作答次数"),
            ("correct_hits", "命中次数"),
            ("last_seen_at", "最近练习"),
            ("updated_at", "更新时间"),
        ],
        rows,
    )


def build_training(rows: Sequence[dict[str, Any]]) -> ExportSection:
    # 调用方把 question_id join 成 question 文本（题干在 question_bank 表）
    return _section(
        "training",
        "训练记录",
        [
            ("asked_at", "时间"),
            ("question", "题目"),
            ("answer_text", "回答"),
            ("scores", "得分"),
            ("session_id", "会话 ID"),
        ],
        rows,
    )


def build_stories(rows: Sequence[Any]) -> ExportSection:
    return _section(
        "stories",
        "故事卡",
        [
            ("title", "标题"),
            ("tags", "适用题型"),
            ("starred", "主推"),
            ("used_count", "引用次数"),
            ("metrics", "可核对的数字"),
            ("situation", "情境"),
            ("task", "任务"),
            ("action", "行动"),
            ("result", "结果"),
        ],
        rows,
    )


def build_answers(rows: Sequence[Any]) -> ExportSection:
    return _section(
        "answers",
        "话术草稿",
        [
            ("kind", "类型"),
            ("title", "标题"),
            ("target_seconds", "目标时长(秒)"),
            ("content", "内容"),
            ("created_at", "创建时间"),
        ],
        rows,
    )


def build_offers(rows: Sequence[Any]) -> ExportSection:
    return _section(
        "offers",
        "Offer",
        [
            ("company", "公司"),
            ("title", "岗位"),
            ("status", "状态"),
            ("monthly_salary", "月薪"),
            ("months_per_year", "每年月数"),
            ("annual_bonus", "年终奖"),
            ("equity_per_year", "年股票价值"),
            ("signing_bonus", "签字费"),
            ("currency", "币种"),
            ("deadline", "截止时间"),
            ("application_id", "关联投递 ID"),
            ("note", "备注"),
        ],
        rows,
    )


# --------------------------------------------------------------------------- #
# M15 补充：之前漏掉的"我的数据"
#
# 这些不是"能重新生成的东西"——录音是你上传的真实素材、题库与练习记录是你的积累、
# 过滤规则与黑名单是你一条条配的。导出如果少了它们，就不叫"全量备份"。
# 唯一**故意不导出**的是 `app_setting` 与 `provider_config`（可能含 API Key），
# 以及 `llm_call`（成本日志，动辄上万行，导出里没有阅读价值）。
# --------------------------------------------------------------------------- #
def build_job_postings(rows: Sequence[Any]) -> ExportSection:
    return _section(
        "job_postings",
        "抓到的岗位",
        [
            ("platform", "平台"),
            ("external_id", "平台内 ID"),
            ("company", "公司"),
            ("title", "岗位"),
            ("salary_range", "薪资"),
            ("city", "城市"),
            ("hr_name", "HR"),
            ("hr_active", "HR 活跃"),
            ("is_headhunter", "猎头岗"),
            ("matched_score", "匹配分"),
            ("filter_verdict", "过滤结论"),
            ("jd_url", "JD 链接"),
            ("jd_text", "JD 正文"),
            ("fetched_at", "抓取时间"),
        ],
        rows,
    )


def build_apply_tasks(rows: Sequence[Any]) -> ExportSection:
    return _section(
        "apply_tasks",
        "待投队列",
        [
            ("company", "公司"),
            ("title", "岗位"),
            ("status", "状态"),
            ("say_hi_text", "招呼语"),
            ("attempts", "尝试次数"),
            ("created_at", "入队时间"),
            ("approved_at", "确认时间"),
            ("sent_at", "投出时间"),
            ("error", "错误"),
        ],
        rows,
    )


def build_apply_rules(rows: Sequence[Any]) -> ExportSection:
    return _section(
        "apply_rules",
        "投递过滤规则",
        [
            ("platform", "平台"),
            ("name", "名称"),
            ("kind", "类型"),
            ("enabled", "启用"),
            ("priority", "优先级"),
            ("params", "参数"),
        ],
        rows,
    )


def build_blacklist(rows: Sequence[Any]) -> ExportSection:
    return _section(
        "blacklist",
        "黑名单",
        [
            ("platform", "平台"),
            ("company", "公司"),
            ("job_title", "岗位"),
            ("hr_name", "HR"),
            ("reason", "原因"),
            ("added_at", "加入时间"),
        ],
        rows,
    )


def build_platform_accounts(rows: Sequence[Any]) -> ExportSection:
    return _section(
        "platform_accounts",
        "平台账号状态",
        [
            ("platform", "平台"),
            ("daily_sent", "今日已投"),
            ("daily_limit", "每日上限"),
            ("last_login_at", "最近登录"),
            ("circuit_broken_until", "熔断截止"),
            ("note", "备注"),
        ],
        rows,
    )


def build_import_batches(rows: Sequence[Any]) -> ExportSection:
    return _section(
        "import_batches",
        "导入流水",
        [
            ("at", "时间"),
            ("channel", "渠道"),
            ("source", "来源"),
            ("rows", "行数"),
            ("inserted", "新增"),
            ("updated", "更新"),
            ("skipped", "跳过"),
            ("note", "备注"),
        ],
        rows,
    )


def build_topics(rows: Sequence[Any]) -> ExportSection:
    return _section(
        "topics",
        "技能主题",
        [
            ("name", "主题"),
            ("category", "分类"),
            ("parent", "上级主题"),
            ("created_at", "创建时间"),
        ],
        rows,
    )


def build_mastery_events(rows: Sequence[Any]) -> ExportSection:
    """掌握度变化流水。导它的理由：它是"掌握度为什么变了"的唯一解释来源。"""
    return _section(
        "mastery_events",
        "掌握度变化",
        [
            ("at", "时间"),
            ("topic", "主题"),
            ("before", "变化前"),
            ("after", "变化后"),
            ("delta", "变化"),
            ("source", "来源"),
            ("note", "说明"),
        ],
        rows,
    )


def build_questions(rows: Sequence[Any]) -> ExportSection:
    return _section(
        "questions",
        "题库",
        [
            ("text", "题干"),
            ("category", "分类"),
            ("difficulty", "难度"),
            ("topics", "关联主题"),
            ("source", "来源"),
            ("created_at", "创建时间"),
        ],
        rows,
    )


def build_reviews(rows: Sequence[Any]) -> ExportSection:
    return _section(
        "reviews",
        "复习任务（SM-2）",
        [
            ("topic", "主题"),
            ("question", "题目"),
            ("due_at", "到期时间"),
            ("done_at", "完成时间"),
            ("interval_days", "间隔天数"),
            ("repetitions", "连续答对"),
            ("ease", "难度系数"),
        ],
        rows,
    )


def build_recordings(rows: Sequence[Any]) -> ExportSection:
    return _section(
        "recordings",
        "真实面试录音",
        [
            ("title", "标题"),
            ("company", "公司"),
            ("role", "岗位"),
            ("kind", "类型"),
            ("status", "状态"),
            ("created_at", "上传时间"),
        ],
        rows,
    )


def build_transcripts(rows: Sequence[Any]) -> ExportSection:
    return _section(
        "transcripts",
        "转写段落",
        [
            ("recording", "录音"),
            ("idx", "序号"),
            ("speaker", "说话人"),
            ("text", "内容"),
            ("start_ms", "开始(ms)"),
            ("end_ms", "结束(ms)"),
        ],
        rows,
    )


def build_profiles(rows: Sequence[Any]) -> ExportSection:
    return _section(
        "profiles",
        "面试官画像",
        [
            ("name", "画像名"),
            ("source", "来源"),
            ("style_tags", "风格标签"),
            ("question_count", "题目数"),
            ("created_at", "创建时间"),
            ("notes", "备注"),
        ],
        rows,
    )


#: 板块清单（顺序即导出顺序）。测试用它钉住「该有的板块一个不少」。
EXPORT_SECTION_KEYS: tuple[str, ...] = (
    "targets",
    "resumes",
    "applications",
    "application_events",
    "sessions",
    "turns",
    "reports",
    "improvements",
    "expression",
    "mastery",
    "training",
    "stories",
    "answers",
    "offers",
    "job_postings",
    "apply_tasks",
    "apply_rules",
    "blacklist",
    "platform_accounts",
    "import_batches",
    "topics",
    "mastery_events",
    "questions",
    "reviews",
    "recordings",
    "transcripts",
    "profiles",
)


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #
def format_cell(value: Any) -> str:
    """把任意单元格值变成适合表格的字符串（JSON/CSV/Markdown 共用）。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _jsonable(value: Any) -> Any:
    """JSON 导出保留原始类型（数字还是数字），只把无法序列化的转成字符串。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return str(value)


def _md_escape(text: str) -> str:
    # 单元格里不能出现裸 | 和换行，否则整张表就散架了
    return text.replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>")


def bundle_stats(bundle: ExportBundle) -> list[dict[str, Any]]:
    """各板块行数概览（预览接口与文档头都用它）。"""
    return [
        {"key": section.key, "title": section.title, "rows": section.row_count}
        for section in bundle.sections
    ]


def render_markdown(bundle: ExportBundle) -> str:
    lines: list[str] = [
        "# 求职操作系统 · 全量导出",
        "",
        f"- 生成时间：{format_cell(bundle.generated_at)}",
        f"- 板块数：{len(bundle.sections)}；数据行数：{bundle.row_count}",
        "",
        "| 板块 | 行数 |",
        "|---|---|",
    ]
    for stat in bundle_stats(bundle):
        lines.append(f"| {stat['title']} | {stat['rows']} |")
    lines.append("")

    for section in bundle.sections:
        lines.append(f"## {section.title}")
        lines.append("")
        if not section.rows:
            lines.append("（暂无数据）")
            lines.append("")
            continue
        header = [column.header for column in section.columns]
        lines.append("| " + " | ".join(header) + " |")
        lines.append("|" + "---|" * len(header))
        for row in section.rows:
            cells = [_md_escape(format_cell(_get(row, column.key))) for column in section.columns]
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
    return "\n".join(lines)


def render_csv_sections(bundle: ExportBundle) -> list[tuple[str, str]]:
    """每个板块一个 CSV（文件名 = 板块 key）。多表 CSV 的载体是 ZIP，由 service 层打包。"""
    files: list[tuple[str, str]] = []
    for section in bundle.sections:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([column.header for column in section.columns])
        for row in section.rows:
            writer.writerow([format_cell(_get(row, column.key)) for column in section.columns])
        files.append((f"{section.key}.csv", buffer.getvalue()))
    return files


def render_json(bundle: ExportBundle) -> dict[str, Any]:
    """JSON 导出 = 中间格式本身，保留原始类型，可直接当作备份再导入的输入。"""
    payload = asdict(bundle)
    return _jsonable(payload)
