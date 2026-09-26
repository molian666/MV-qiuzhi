"""对话文稿文件的读取与体检：把"上传一个文档"变成可核对的结果。

为什么要单独一层：
1. **编码**。Windows 中文环境下从记事本 / Word / 微信导出的 .txt 常是 GBK；
   一律按 UTF-8 读会得到满屏乱码，而用户只会觉得"这软件坏了"。
   这里按 BOM → 严格 UTF-8 → GB18030 的顺序试，并把**实际用了哪种编码**回报出去。
2. **体检**。节奏分析靠 `[面试官] / [我]` 这类说话人标记来切分问答；
   没标说话人的文稿会被静默丢掉（`split_questions` 只认 interviewer/candidate），
   结果就是"上传成功了但生成画像时说没识别出问题"。
   所以上传前先说清楚：解析出多少段、其中多少段有说话人标记、有没有时间戳。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .pacing import parse_transcript

#: 支持的扩展名（.txt/.md 是纯文本；.csv/.tsv 按纯文本读——对话稿里很少有真 CSV）
SUPPORTED_SUFFIXES = (".txt", ".md", ".markdown", ".text", ".log", ".csv", ".tsv")

#: 单个文稿的大小上限。一份两小时的对话转写约 200KB，1MB 足够宽松
MAX_DOC_BYTES = 1_000_000

#: 按顺序尝试的编码。GB18030 是 GBK/GB2312 的超集，放最后兜底。
_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030")


class TranscriptDocError(ValueError):
    """文稿读不出来（编码不认识、文件太大、扩展名不支持等）。"""


@dataclass(slots=True)
class DocCheck:
    """文稿体检结果——给用户看的，字段名要能直接显示。"""

    encoding: str
    total_lines: int = 0
    segments: int = 0  # 解析出的段落数
    labeled: int = 0  # 其中有说话人标记的
    interviewer: int = 0
    candidate: int = 0
    timestamped: int = 0  # 带时间戳的段落数（带则"等待时长"是实测，否则按字数估算）
    looks_like_questions: int = 0  # 疑似问句的行数（无说话人标记时用来兜底提示）
    #: 角色是不是**推断**出来的（原文只标了"发言人1/2"这类无角色语义的说话人）
    inferred_roles: bool = False
    role_note: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        """能不能用来学画像：至少要有一段面试官发言。"""
        return self.interviewer > 0

    def summary(self) -> str:
        if self.segments == 0:
            return "这份文档里没有解析出任何对话行（可能是空文件或整篇没有分行）。"
        if self.interviewer == 0:
            return (
                f"解析出 {self.segments} 行对话，但**没有一行标出谁在说话**，"
                "所以切不出问答。请在每行前面加上 `[面试官]` 或 `[我]`。"
            )
        return (
            f"解析出 {self.segments} 行对话：面试官 {self.interviewer} 行、"
            f"你 {self.candidate} 行"
            + (f"、{self.timestamped} 行带时间戳（等待时长按实测算）" if self.timestamped else "（无时间戳，等待时长按字数估算）")
            + ("。角色是按「谁在提问」推断的" if self.inferred_roles else "")
        )


def decode_document(data: bytes) -> tuple[str, str]:
    """把文件字节解成文本，返回 (文本, 用了哪种编码)。

    顺序：BOM → 严格 UTF-8 → GB18030。GB18030 几乎不会失败（它能解出任意字节序列），
    所以放在最后兜底；也正因为如此，走到它时才需要额外提示"编码可能是猜的"。
    """
    if not data:
        raise TranscriptDocError("文件是空的")
    if not data.strip():
        # 只有空白/换行的文件：能解码，但一个字都没有。
        # 放过去的话，用户要到"生成画像"才收到"没有识别出任何问题"，白绕一圈。
        raise TranscriptDocError("文件里只有空白字符，没有任何内容")
    if len(data) > MAX_DOC_BYTES:
        raise TranscriptDocError(
            f"文件太大（{len(data) // 1024}KB），上限是 {MAX_DOC_BYTES // 1000}KB。"
            "请只上传面试对话的部分。"
        )

    for encoding in _ENCODINGS:
        try:
            text = data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        # 解出来大量替换符说明这不是"干净的解码"，继续试下一种
        if text.count("\ufffd") > max(3, len(text) // 500):
            continue
        return text, encoding
    raise TranscriptDocError(
        "认不出这个文件的编码（试过 UTF-8 与 GB18030）。"
        "请用记事本另存为 UTF-8，或直接把文稿粘贴进文本框。"
    )


def check_document(text: str, *, encoding: str = "utf-8") -> DocCheck:
    """解析并体检一份对话文稿。"""
    check = DocCheck(encoding=encoding)
    check.total_lines = len([line for line in (text or "").splitlines() if line.strip()])

    roles_info: list[str] = []
    drafts = parse_transcript(text, roles_info=roles_info)
    check.segments = len(drafts)
    for draft in drafts:
        if draft.speaker == "interviewer":
            check.interviewer += 1
            check.labeled += 1
        elif draft.speaker == "candidate":
            check.candidate += 1
            check.labeled += 1

    # 「发言人1/2」这类文稿没有角色语义，角色是按"谁在提问"**推断**的。
    # 推断就是推断：必须让用户看见，否则猜反了他也不知道该改哪里。
    check.inferred_roles = bool(roles_info)
    check.role_note = roles_info[0] if roles_info else ""
    if roles_info:
        check.warnings.append(roles_info[0])

    # 时间戳：parse_transcript 会给每一段都补上 start_ms（按字数估算），
    # 所以不能看"有没有 start_ms"，要看原文里是否真的有形如 00:12 的标记。
    #
    # 但**必须用同一个解析器来判断**：转写软件那种「说话人一行、时间一行」的格式里，
    # 时间戳原本独立成行，直接对原文做行首匹配会数不到，于是误报
    # "没有时间戳，等待时长按字数估算"——而等待时长其实是实测出来的。
    # 这种自相矛盾的提示比不提示更糟。
    from .pacing import _TIMESTAMP, _merge_speaker_headers  # 复用同一套规则，避免两处漂移

    check.timestamped = len(
        [
            line
            for line in _merge_speaker_headers((text or "").splitlines())
            if _TIMESTAMP.match(line.strip())
        ]
    )
    check.looks_like_questions = len(
        [line for line in (text or "").splitlines() if line.strip().endswith(("？", "?"))]
    )

    # 顺序有讲究：最"致命"的问题排最前面。
    # 缺说话人标记时，"没识别到面试官"只是它的**后果**——
    # 两条都说一遍等于让用户自己猜哪个才是要改的。
    #
    # 注意：`labeled` 在"发言人1/2 靠推断定角色"的文稿上也会 > 0，
    # 所以要先用 inferred_roles 把那种情况分出去——否则会给一份
    # **已经能正常分析**的文稿报"没有一行带说话人标记"，纯属吓人。
    if check.segments and not check.labeled:
        check.warnings.append(
            f"没有一行带说话人标记（解析出 {check.segments} 行、"
            f"其中 {check.looks_like_questions} 行看着像提问，但不知道是谁问的）。"
            "节奏分析靠 `[面试官]` / `[我]` 区分谁在说，缺了它切不出问答——"
            "请在每行前面补上，例如 `[面试官] 亚像素定位怎么做？`"
        )
    elif check.inferred_roles:
        # 角色是推断的：不报错（能正常用），但要如实说明并给"猜反了怎么办"
        pass
    else:
        if check.segments and check.interviewer == 0:
            check.warnings.append("没有识别到面试官的发言，生成画像会失败。")
        if check.segments and check.candidate == 0:
            check.warnings.append(
                "只识别到面试官发言。追问深度、等待时长这类指标需要你的回答作对照，"
                "结果会偏薄。"
            )
    if check.segments and check.timestamped == 0:
        check.warnings.append(
            "没有时间戳（如 `00:12`）。等待时长与语速会按字数估算，不是实测值。"
        )
    if check.total_lines and not check.segments:
        check.warnings.append("每一行都解析成了空内容，请检查格式。")
    return check
