"""导入外部题库文档（M13）。

支持四种输入，**可靠性依次下降**——这一点要在界面上如实告诉用户，
否则他们会以为随便一个 PDF 粘进来就能用：

1. **JSON**（最可靠）：`[{"question": "...", "answer": "...", "points": [...]}]`
2. **CSV**（可靠）：表头 `question,answer,points,category,difficulty`，
   多个要点用 `|` 分隔
3. **整理好的面经 Markdown**（可靠）：分章节 + `**Q1【高频】：问题？**` 加粗题干，
   见 `_parse_sectioned`。章节变成分类，`【高频】` 这类标注变成标签，
   `出处：` 保留在答案末尾。这是"把几场面试录音整理成一份"最常见的写法
4. **自由格式 Markdown / 纯文本**（靠规则猜）：把「以问号结尾的段落」当题目，
   紧随其后的段落当答案

**为什么不用模型来抽题**：那需要 API Key，无 Key 时（Mock 模式）整条链路就断了，
而导入是本地操作，不该依赖外部服务。规则解析笨，但结果**可预测、可复现**——
抽出来的题不对，用户能通过调整文档格式改对，而不是去猜模型为什么这么切。

编码会依次尝试 UTF-8 / GBK：中文面经文档从 Windows 里导出来经常是 GBK，
只认 UTF-8 会直接乱码。
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field

#: 各字段在文档里的常见叫法，做别名兼容——用户不该为了导入去改列名
_TEXT_KEYS = ("question", "text", "q", "title", "问题", "题目", "题干")
_ANSWER_KEYS = ("answer", "a", "sample_answer", "答案", "参考答案", "解答")
_POINT_KEYS = ("points", "reference_points", "key_points", "要点", "答题要点")
_CATEGORY_KEYS = ("category", "tag", "topic", "分类", "类别", "方向")
_DIFFICULTY_KEYS = ("difficulty", "level", "难度")

#: 要点单元格里的分隔符。中文文档里这几种都常见。
_POINT_SEPARATORS = re.compile(r"\s*[|｜;；、]\s*")

_MIN_QUESTION_LENGTH = 6
_MAX_QUESTION_LENGTH = 500


@dataclass(slots=True)
class ImportedQuestion:
    text: str
    answer: str = ""
    points: list[str] = field(default_factory=list)
    category: str = ""
    difficulty: int = 3
    tags: list[str] = field(default_factory=list)
    """题目本身的标签（如「高频」「易错」）。与 `category` 不同：
    分类讲的是"属于哪个方向"，标签讲的是"这题什么性质"。"""
    followups: list[str] = field(default_factory=list)
    """面试官会追问什么。导入的文档里通常没有，模型生成时会有。"""
    pitfalls: list[str] = field(default_factory=list)
    """常见错误回答。同上。"""


@dataclass(slots=True)
class ImportResult:
    questions: list[ImportedQuestion]
    source_format: str
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)


def decode_bytes(raw: bytes) -> tuple[str, str]:
    """按 UTF-8 → GBK 依次尝试解码，返回 (文本, 用了哪种编码)。"""
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    # 全都失败时用 UTF-8 强解，坏的字符替换掉——总比整个文件导入失败好
    return raw.decode("utf-8", errors="replace"), "utf-8 (有损坏字符)"


def parse_document(filename: str, content: str) -> ImportResult:
    """按文件名后缀与内容特征选择解析方式。

    顺序是有讲究的：**先认出结构，认不出才退回猜**。整理过的面经文档
    （分章节 + `**Qn**` 加粗题干，见 `_parse_sectioned`）能精确切成题，
    就不该让它走"段落以问号结尾"那条会切错的猜测路径。
    """
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    stripped = content.lstrip()

    if suffix == "json" or stripped.startswith(("[", "{")):
        return _parse_json(content)
    if suffix == "csv" or _looks_like_csv(content):
        return _parse_csv(content)

    # 结构化的面经文档：识别到足够多的题干就用精确解析
    if suffix in {"md", "markdown"} or _HEADING_QUESTION.search(content):
        structured = _parse_sectioned(content)
        if len(structured.questions) >= _MIN_SECTIONED_QUESTIONS:
            return structured

    return _parse_text(content)


# --------------------------------------------------------------------------- #
# JSON
# --------------------------------------------------------------------------- #
def _parse_json(content: str) -> ImportResult:
    warnings: list[str] = []
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        return ImportResult(
            questions=[], source_format="json", warnings=[f"JSON 解析失败：{exc}"]
        )

    if isinstance(payload, dict):
        # 兼容 {"questions": [...]} 这种包一层的形式
        for key in ("questions", "items", "data", "题目"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
        else:
            payload = [payload]

    if not isinstance(payload, list):
        return ImportResult(
            questions=[], source_format="json", warnings=["JSON 顶层既不是数组也不是含题目数组的对象"]
        )

    questions: list[ImportedQuestion] = []
    skipped = 0
    for item in payload:
        if not isinstance(item, dict):
            skipped += 1
            continue
        question = build_imported_question(item)
        if question is None:
            skipped += 1
            continue
        questions.append(question)

    if not questions:
        warnings.append("没有解析出任何题目，检查字段名（question / answer / points）")
    return ImportResult(questions, "json", skipped, warnings)


# --------------------------------------------------------------------------- #
# CSV
# --------------------------------------------------------------------------- #
def _looks_like_csv(content: str) -> bool:
    first = content.lstrip().splitlines()[:1]
    return bool(first) and first[0].count(",") >= 2


def _parse_csv(content: str) -> ImportResult:
    warnings: list[str] = []
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        return ImportResult([], "csv", warnings=["CSV 没有表头行"])

    questions: list[ImportedQuestion] = []
    skipped = 0
    for row in reader:
        if not row:
            skipped += 1
            continue
        question = build_imported_question({k: v for k, v in row.items() if k is not None})
        if question is None:
            skipped += 1
            continue
        questions.append(question)

    if not questions:
        warnings.append("没有解析出任何题目，检查表头是否包含 question / answer 列")
    return ImportResult(questions, "csv", skipped, warnings)


# --------------------------------------------------------------------------- #
# 结构化面经（Markdown）：分章节 + **Qn** 加粗题干
# --------------------------------------------------------------------------- #
#: 题干行：`**Q12【高频·易错】（相机选型）问题文本？**`
#:
#: 三段后缀**都必须可选**——一份真实的整理文档里，163 道题是这样分布的：
#: 有标记有子标签 50、有标记无子标签 89、无标记有子标签 15、只有 Q+冒号 9。
#: 按"标准形态"写死会让一大半题漏掉。
_HEADING_QUESTION = re.compile(
    r"^\*\*\s*Q(?P<num>\d+)\s*"
    r"(?:【(?P<marks>[^】]*)】)?\s*"
    r"(?:[（(](?P<sub>[^）)]*)[）)])?\s*"
    r"[:：]?\s*"
    r"(?P<text>.+?)\s*\*\*\s*$",
    re.MULTILINE,
)

#: 章节标题。「第X部分：」这类编号前缀要剥掉，剩下的才是能用的分类名
_HEADING_SECTION = re.compile(r"^#{2,4}\s+(?P<title>.+?)\s*$", re.MULTILINE)
_PART_PREFIX = re.compile(r"^第[一二三四五六七八九十百零\d]+\s*部分\s*[:：]\s*")

#: 来源标注行。它是这道题的出处，有回溯价值，不丢
_SOURCE_LINE = re.compile(r"^出处\s*[:：]\s*(?P<value>.+?)\s*$")

#: 标记内部的分隔符：「高频·易错」要拆成两个标签
_MARK_SEPARATORS = re.compile(r"[·、,，/\s]+")

#: 识别到这么多道题才认为"这确实是结构化面经"，否则退回通用规则
_MIN_SECTIONED_QUESTIONS = 3


def _clean_section_title(title: str) -> str:
    """章节标题 → 分类名。

    以前这里是 `.strip()[:40]`，**40 字硬截断**会惹祸：像
    `## 视觉软件平台（VisionPro / VisionMaster / Halcon / VM 二开 …）` 这样的长标题，
    截断后变成 `视觉软件平台（VisionPro / VisionMaster / Halcon`——而分类里带 ` / `
    意味着"父 / 子"，于是同一个主题在界面上裂成十来个看起来像子主题的碎片。

    现在的做法：剥掉「第X部分：」前缀、上限放宽到 120 字、只清理**残缺**的括号
    （完整括号里的信息照旧保留——不做"丢掉括号内容"这种会丢信息的猜测）。
    """
    text = _PART_PREFIX.sub("", title).strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > 120:
        text = text[:120].rstrip()
    for left, right in (("（", "）"), ("(", ")")):
        if text.count(left) > text.count(right):
            text = text[: text.rfind(left)].strip()
            break
    return text


def _parse_sectioned(content: str) -> ImportResult:
    """解析整理过的面经文档。

    这类文档的结构是**确定的**（章节标题、`**Qn**` 加粗题干、`出处：` 标注），
    所以能精确切开；通用规则靠"段落以问号结尾"去猜，遇到 `**Q1…？**`
    这种"问号后面还有星号"的写法会一道都认不出来。

    章节里没有题干就不产出题目——文档开头的「目录」「速览」这些章节
    因此自然被跳过，不需要维护一张要跳过的标题清单。
    """
    questions: list[ImportedQuestion] = []
    category = ""
    pending: dict[str, object] | None = None

    def flush() -> None:
        nonlocal pending
        if pending is None:
            return
        answer = _answer_with_source(pending["lines"])  # type: ignore[arg-type]
        sub = str(pending["sub"] or "")
        if sub and category:
            full_category = f"{category} / {sub}"
        else:
            full_category = sub or category
        questions.append(
            ImportedQuestion(
                text=str(pending["text"]),
                answer=answer,
                points=_split_points_from_answer(answer),
                category=full_category,
                tags=_mark_tags(str(pending["marks"] or "")),
            )
        )
        pending = None

    for line in content.splitlines():
        heading = _HEADING_QUESTION.match(line)
        if heading:
            flush()
            pending = {
                "text": heading.group("text").strip(),
                "marks": heading.group("marks"),
                "sub": heading.group("sub"),
                "lines": [],
            }
            continue

        section = _HEADING_SECTION.match(line)
        if section:
            flush()
            category = _clean_section_title(section.group("title"))
            continue

        if pending is not None:
            pending["lines"].append(line)  # type: ignore[union-attr]

    flush()

    warnings: list[str] = []
    # 题干可能跨行导致漏切。宁可少报也不要漏报——把差额说出来
    raw_heads = len(re.findall(r"^\s*\*\*Q\d", content, re.MULTILINE))
    if raw_heads > len(questions):
        warnings.append(
            f"文档里有 {raw_heads} 行以 **Q 开头，只识别出 {len(questions)} 道——"
            "可能有题干写了多行或没加粗，请核对。"
        )
    if questions:
        warnings.append(
            f"按整理好的面经格式解析：识别出 {len(questions)} 道题。"
            "章节变成分类，「高频」「易错」这类标记变成标签。"
        )
    return ImportResult(questions, "markdown-面经", 0, warnings)


def _mark_tags(raw: str) -> list[str]:
    """「高频·易错」→ ['高频', '易错']。"""
    if not raw:
        return []
    return [item for item in _MARK_SEPARATORS.split(raw) if item]


def _answer_with_source(lines: list[str]) -> str:
    """把正文当答案，`出处：` 行抽出来附在末尾。

    出处是"这题从哪场面试整理来的"，有回溯价值，所以不丢；
    但它是**元信息**不是答案内容，所以单列一段，不混进正文里读。
    """
    body: list[str] = []
    sources: list[str] = []
    for line in lines:
        matched = _SOURCE_LINE.match(line.strip())
        if matched:
            sources.append(matched.group("value"))
        else:
            body.append(line)

    answer = "\n".join(body).strip()
    if sources:
        note = "出处：" + "、".join(sources)
        answer = f"{answer}\n\n{note}" if answer else note
    return answer


# --------------------------------------------------------------------------- #
# Markdown / 纯文本（自由格式，靠规则猜）
# --------------------------------------------------------------------------- #
_QUESTION_MARK = re.compile(r"[？?]\s*$")


def _parse_text(content: str) -> ImportResult:
    """把「以问号结尾的段落」当题目，把它**之后、下一个问题之前**的内容当答案。

    答案区可能跨多个段落，其中带 `-` / `1.` 前缀的行会被提出来当答题要点——
    很多面经的答案本来就是分条写的，这一取就得到了要点。

    这个规则对面经类文档有效（本来就是一个问题跟一段回答），但对格式自由的
    笔记会猜错——所以 `warnings` 里要如实提醒用户核对结果。
    """
    warnings: list[str] = []
    blocks: list[str] = []
    for chunk in re.split(r"\n\s*\n", content):
        cleaned = chunk.strip()
        if cleaned:
            blocks.append(cleaned)

    questions: list[ImportedQuestion] = []
    skipped = 0
    category = ""
    index = 0

    while index < len(blocks):
        block = blocks[index]

        heading = re.match(r"^#{1,4}\s+(.*)$", block)
        if heading:
            category = heading.group(1).strip()[:40]
            index += 1
            continue

        candidate = _strip_list_marker(block.splitlines()[0].strip())
        if _QUESTION_MARK.search(candidate) and _MIN_QUESTION_LENGTH <= len(candidate) <= _MAX_QUESTION_LENGTH:
            # 段落自带的后续行 + 紧随其后的段落，都算答案区
            answer_lines = [line for line in block.splitlines()[1:] if line.strip()]
            index += 1
            while index < len(blocks) and _is_answer_block(blocks[index]):
                answer_lines.append(blocks[index])
                index += 1

            answer = "\n".join(answer_lines).strip()
            questions.append(
                ImportedQuestion(
                    text=candidate,
                    answer=answer,
                    points=_split_points_from_answer(answer),
                    category=category,
                )
            )
            continue

        skipped += 1
        index += 1

    if not questions:
        warnings.append(
            "没有识别出题目。纯文本导入靠「段落以问号结尾」判断，"
            "格式不合请改用 JSON 或 CSV。"
        )
    else:
        warnings.append(
            f"文本导入是规则推断的：识别出 {len(questions)} 道题、跳过 {skipped} 段。"
            "答案与要点可能不完整，请核对后再用于面试。"
        )
    return ImportResult(questions, "text", skipped, warnings)


def _strip_list_marker(line: str) -> str:
    return re.sub(r"^\s*(?:[-*+]|\d+[.、)])\s*", "", line).strip()


def _is_answer_block(block: str) -> bool:
    """判断一个段落是否属于上一道题的答案区。

    标题一定不是；以问号结尾的段落是新问题，也不是。
    """
    if re.match(r"^#{1,4}\s+", block):
        return False
    if re.match(r"^```", block):
        return False
    return not _QUESTION_MARK.search(_strip_list_marker(block.splitlines()[0].strip()))


def _split_points_from_answer(answer: str) -> list[str]:
    """答案里带列表前缀的行当作答题要点。

    取不到也没关系：面试时不带要点一样能问，只是追问会少一点依据。
    """
    points: list[str] = []
    for line in answer.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        item = _strip_list_marker(stripped)
        if item and item != stripped and len(item) <= 200:
            points.append(item)
    return points[:10]


# --------------------------------------------------------------------------- #
# 公共
# --------------------------------------------------------------------------- #
def _pick_alias(payload: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def build_imported_question(payload: dict) -> ImportedQuestion | None:
    """从一份字段结构里构造题目。

    导入文档与模型生成**共用这一个函数**：两者的目标结构相同，
    各写一套解析早晚会漂移（一边加了字段别名，另一边没加）。
    """
    text = _pick_alias(payload, _TEXT_KEYS)
    if not (_MIN_QUESTION_LENGTH <= len(text) <= _MAX_QUESTION_LENGTH):
        return None

    raw_points = None
    for key in _POINT_KEYS:
        if key in payload:
            raw_points = payload[key]
            break

    if isinstance(raw_points, list):
        points = [str(item).strip() for item in raw_points if str(item).strip()]
    elif isinstance(raw_points, str):
        points = [item for item in _POINT_SEPARATORS.split(raw_points) if item.strip()]
    else:
        points = []

    difficulty = 3
    raw_difficulty = None
    for key in _DIFFICULTY_KEYS:
        if key in payload:
            raw_difficulty = payload[key]
            break
    if raw_difficulty is not None:
        try:
            difficulty = max(1, min(5, int(float(str(raw_difficulty)))))
        except (TypeError, ValueError):
            difficulty = 3

    return ImportedQuestion(
        text=text,
        answer=_pick_alias(payload, _ANSWER_KEYS),
        points=points,
        category=_pick_alias(payload, _CATEGORY_KEYS),
        difficulty=difficulty,
        followups=_string_list(payload.get("followups") or payload.get("追问")),
        pitfalls=_string_list(payload.get("pitfalls") or payload.get("常见错误")),
    )


def _string_list(value: Any) -> list[str]:
    """接受字符串数组或分隔符串起来的字符串——结构化的用前者，手写的用后者。"""
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in _POINT_SEPARATORS.split(value) if item.strip()]
    return []
