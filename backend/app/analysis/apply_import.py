"""投递数据导入的解析逻辑（纯函数）。

三种输入都要吃得下：自己整理的 CSV、平台导出的投递记录、浏览器扩展抓的页面表格。

关键设计：**表头用同义词表匹配**。用户不会写 `applied_at`，他会写"投递时间"；
平台导出可能写 "Apply Date"。硬编码列名必然踩坑，所以维护一张同义词表，
并把"认出了哪些列、哪些没认出来"如实返回 —— 导入这种事的失败往往不是报错，
而是**静默丢列**（用户以为导了，其实简历版本列没被识别）。
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..clock import utcnow
from ..models.apply import APPLICATION_STATUSES

# --------------------------------------------------------------------------- #
# 同义词表
# --------------------------------------------------------------------------- #
HEADER_SYNONYMS: dict[str, tuple[str, ...]] = {
    "company": ("公司", "公司名称", "企业", "企业名称", "company", "employer"),
    "title": ("岗位", "职位", "岗位名称", "职位名称", "应聘职位", "title", "position", "job"),
    "channel": ("渠道", "平台", "来源", "招聘平台", "投递渠道", "channel", "platform", "source"),
    "status": ("状态", "投递状态", "进展", "结果", "当前状态", "status", "state", "progress"),
    "applied_at": ("投递时间", "投递日期", "申请时间", "申请日期", "时间", "日期", "applied_at", "apply_date", "date"),
    "salary_range": ("薪资", "薪资范围", "期望薪资", "月薪", "salary", "salary_range"),
    "city": ("城市", "工作城市", "工作地点", "地点", "city", "location"),
    "resume_version": ("简历", "简历版本", "简历名称", "使用的简历", "resume", "resume_version"),
    "jd_url": ("链接", "职位链接", "岗位链接", "url", "link", "job_url"),
    "note": ("备注", "说明", "note", "remark", "comment"),
}

CHANNEL_SYNONYMS: tuple[tuple[str, str], ...] = (
    ("boss直聘", "boss"),
    ("boss", "boss"),
    ("直聘", "boss"),
    ("猎聘", "liepin"),
    ("liepin", "liepin"),
    ("智联招聘", "zhilian"),
    ("智联", "zhilian"),
    ("zhilian", "zhilian"),
    ("前程无忧", "51job"),
    ("51job", "51job"),
    ("51", "51job"),
    ("拉勾", "lagou"),
    ("lagou", "lagou"),
    ("内推", "referral"),
    ("referral", "referral"),
)

STATUS_SYNONYMS: tuple[tuple[str, str], ...] = (
    ("已投递", "applied"),
    ("已投", "applied"),
    ("投递成功", "applied"),
    ("已申请", "applied"),
    ("applied", "applied"),
    ("已查看", "viewed"),
    ("已读", "viewed"),
    ("被查看", "viewed"),
    ("viewed", "viewed"),
    ("筛选中", "screening"),
    ("待筛选", "screening"),
    ("screening", "screening"),
    ("一面", "interview1"),
    ("初面", "interview1"),
    ("已面试", "interview1"),
    ("面试中", "interview1"),
    ("interview", "interview1"),
    ("二面", "interview2"),
    ("复试", "interview2"),
    ("interview2", "interview2"),
    ("hr面", "hr"),
    ("终面", "hr"),
    ("hr", "hr"),
    ("已offer", "offer"),
    ("offer", "offer"),
    ("录用", "offer"),
    ("意向", "offer"),
    ("被拒", "rejected"),
    ("已拒", "rejected"),
    ("不合适", "rejected"),
    ("rejected", "rejected"),
    ("无响应", "ghosted"),
    ("石沉大海", "ghosted"),
    ("ghosted", "ghosted"),
    ("主动放弃", "withdrawn"),
    ("撤回", "withdrawn"),
    ("withdrawn", "withdrawn"),
)

#: 这些状态的投递会被视为"已收到回应"
RESPONDED_STATUSES = frozenset(APPLICATION_STATUSES) - {"applied"}


@dataclass(slots=True)
class ParsedRow:
    company: str
    title: str
    channel: str = "other"
    status: str = "applied"
    applied_at: datetime = field(default_factory=utcnow)
    salary_range: str = ""
    city: str = ""
    resume_version: str = ""
    jd_url: str = ""
    note: str = ""
    raw: dict[str, str] = field(default_factory=dict)

    @property
    def dedup_key(self) -> str:
        return make_dedup_key(self.company, self.title, self.applied_at)


@dataclass(slots=True)
class ParseResult:
    rows: list[ParsedRow] = field(default_factory=list)
    matched_columns: dict[str, str] = field(default_factory=dict)
    unmatched_headers: list[str] = field(default_factory=list)
    skipped: list[tuple[int, str]] = field(default_factory=list)
    delimiter: str = ","


# --------------------------------------------------------------------------- #
# 归一化
# --------------------------------------------------------------------------- #
def _squash(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").strip().lower().replace("－", "-"))


def normalize_channel(value: str, *, default: str = "other") -> str:
    key = _squash(value)
    if not key:
        return default
    for synonym, channel in CHANNEL_SYNONYMS:
        if key == synonym:
            return channel
    for synonym, channel in CHANNEL_SYNONYMS:
        if synonym in key:
            return channel
    return default


def normalize_status(value: str, *, default: str = "applied") -> str:
    key = _squash(value)
    if not key:
        return default
    for synonym, status in STATUS_SYNONYMS:
        if key == synonym:
            return status
    for synonym, status in STATUS_SYNONYMS:
        if synonym in key:
            return status
    return default


_EXCEL_EPOCH = datetime(1899, 12, 30, tzinfo=timezone.utc)
#: 只匹配"日期部分"，时间部分单独解析 ——
#: 把两者写在同一个正则里会导致 `\s*` 先把空格吃掉、可选的时间组直接空匹配，
#: 结果"2026/3/12 14:30"会被解析成 00:00（踩过）。
_YMD = re.compile(r"^(\d{4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})\s*日?")
_MD = re.compile(r"^(\d{1,2})\s*[-/月.]\s*(\d{1,2})\s*日?")
_COMPACT = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_CLOCK = re.compile(r"^(\d{1,2})[:：]?(\d{2})?[:：]?(\d{2})?")


def parse_datetime(value: str, *, default: datetime | None = None, default_year: int | None = None) -> datetime | None:
    """解析多种日期写法。

    支持：`2026-03-12`、`2026/3/12 14:30`、`2026年3月12日`、`03-12`、`20260312`，
    以及 Excel 导出的日期序列号（如 `45000`）。解析不出来时返回 `default`——
    宁可让这行落到默认时间，也不要因为一个日期格式把整批导入判为失败。
    """
    text = (value or "").strip()
    if not text:
        return default

    if re.fullmatch(r"\d{5}(?:\.\d+)?", text):
        serial = float(text)
        if 20_000 <= serial <= 80_000:
            return _EXCEL_EPOCH + timedelta(days=serial)

    pieces = re.split(r"[ T]+", text, maxsplit=1)
    date_part = pieces[0]
    time_part = pieces[1] if len(pieces) > 1 else ""

    year = month = day = None

    compact = _COMPACT.match(date_part)
    if compact:
        year, month, day = (int(part) for part in compact.groups())
    else:
        match = _YMD.match(date_part)
        if match:
            year, month, day = (int(part) for part in match.groups())
        else:
            match = _MD.match(date_part)
            if match:
                month, day = (int(part) for part in match.groups())
                year = default_year or utcnow().year

    if year is None or month is None or day is None:
        return default

    hour = minute = second = 0
    if time_part:
        clock = _CLOCK.match(time_part)
        if clock:
            hour = int(clock.group(1) or 0)
            minute = int(clock.group(2) or 0)
            second = int(clock.group(3) or 0)

    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
    except ValueError:
        return default


def make_dedup_key(company: str, title: str, applied_at: datetime) -> str:
    """去重键：同一天、同一公司、同一岗位视为同一次投递。

    用日期而不是精确时间，是因为各来源的时间精度不一致（CSV 常只有日期，
    扩展抓的是"投递成功"提示时刻），用精确时间会漏判重复。
    """
    normalized = "|".join(
        [
            _squash(company),
            _squash(title),
            applied_at.strftime("%Y-%m-%d"),
        ]
    )
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# 表格解析
# --------------------------------------------------------------------------- #
def detect_delimiter(text: str) -> str:
    first_line = text.splitlines()[0] if text.splitlines() else ""
    counts = {candidate: first_line.count(candidate) for candidate in (",", "\t", ";", "|")}
    delimiter = max(counts, key=lambda key: counts[key])
    return delimiter if counts[delimiter] > 0 else ","


def _match_header(header: str) -> str | None:
    key = _squash(header)
    if not key:
        return None
    for field_name, synonyms in HEADER_SYNONYMS.items():
        for synonym in synonyms:
            if _squash(synonym) == key:
                return field_name
    for field_name, synonyms in HEADER_SYNONYMS.items():
        for synonym in synonyms:
            if _squash(synonym) in key:
                return field_name
    return None


def _cell(row: list[str], mapping: dict[str, int], key: str) -> str:
    index = mapping.get(key)
    if index is None or index >= len(row):
        return ""
    return (row[index] or "").strip()


def parse_table_rows(
    headers: list[str],
    rows: list[list[str]],
    *,
    default_channel: str = "other",
    default_year: int | None = None,
) -> ParseResult:
    mapping: dict[str, int] = {}
    unmatched: list[str] = []
    for index, header in enumerate(headers):
        field_name = _match_header(header)
        if field_name is not None and field_name not in mapping:
            mapping[field_name] = index
        elif field_name is None:
            unmatched.append((header or "").strip())

    result = ParseResult(
        matched_columns={field_name: headers[index] for field_name, index in mapping.items()},
        unmatched_headers=[header for header in unmatched if header],
    )

    for offset, row in enumerate(rows):
        line_no = offset + 2  # 表头占第 1 行
        if not any((cell or "").strip() for cell in row):
            continue  # 空行直接跳过，不计入 skipped

        company = _cell(row, mapping, "company")
        title = _cell(row, mapping, "title")
        if not company and not title:
            result.skipped.append((line_no, "没有公司或岗位信息"))
            continue

        applied_at_value = _cell(row, mapping, "applied_at")
        applied_at = parse_datetime(applied_at_value, default=None, default_year=default_year)
        if applied_at is None:
            applied_at = utcnow()
            # 只在"表里有时间列、但这一行写错了"时才提醒；
            # 整张表压根没有时间列属于正常情况，对每行都报错只会误导用户。
            if "applied_at" in mapping and applied_at_value:
                result.skipped.append((line_no, "投递时间无法识别，已按当前时间处理"))

        result.rows.append(
            ParsedRow(
                company=company or "（未填写公司）",
                title=title or "（未填写岗位）",
                channel=normalize_channel(_cell(row, mapping, "channel"), default=default_channel),
                status=normalize_status(_cell(row, mapping, "status")),
                applied_at=applied_at,
                salary_range=_cell(row, mapping, "salary_range"),
                city=_cell(row, mapping, "city"),
                resume_version=_cell(row, mapping, "resume_version"),
                jd_url=_cell(row, mapping, "jd_url"),
                note=_cell(row, mapping, "note"),
                raw={header: (row[index] if index < len(row) else "") for index, header in enumerate(headers)},
            )
        )

    return result


def parse_csv_text(
    text: str,
    *,
    default_channel: str = "other",
    default_year: int | None = None,
) -> ParseResult:
    """解析 CSV/TSV 文本（自动识别分隔符、去 BOM）。"""
    cleaned = (text or "").lstrip("\ufeff")
    if not cleaned.strip():
        return ParseResult()

    delimiter = detect_delimiter(cleaned)
    reader = csv.reader(io.StringIO(cleaned), delimiter=delimiter)
    table = [row for row in reader]
    if not table:
        return ParseResult(delimiter=delimiter)

    headers = [str(cell or "") for cell in table[0]]
    body = [[str(cell or "") for cell in row] for row in table[1:]]
    result = parse_table_rows(headers, body, default_channel=default_channel, default_year=default_year)
    result.delimiter = delimiter
    return result
