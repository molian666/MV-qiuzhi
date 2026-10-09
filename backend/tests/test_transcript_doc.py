"""对话文稿上传（M4 补充）：编码识别 + 格式体检。

这一组测试盯着两件**用户会真实踩到**的事：

1. **GBK 文稿**。Windows 中文环境下从记事本/Word 导出的 .txt 常是 GBK。
   一律按 UTF-8 读会得到满屏乱码，而用户只会觉得"这软件坏了"。
   所以要断言：GBK 的中文能被正确读出来，且回报的编码名是 GB18030。
2. **没标说话人的文稿**。节奏分析只认 `[面试官] / [我]`；
   缺标记时那些行会被静默丢掉，最后只在生成画像时报一句
   "没有识别出任何面试问题"。所以要**在体检阶段就明确说出来**。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.analysis.pacing import parse_transcript
from app.analysis.transcript_doc import (
    MAX_DOC_BYTES,
    TranscriptDocError,
    check_document,
    decode_document,
    extract_document_text,
)

GOOD_DIALOG = """[面试官] 先做个自我介绍吧
[我] 我是 5 年机器视觉工程师
[面试官] 亚像素定位是怎么做的？
[我] 用形状匹配拿到亚像素坐标
[面试官] 还有呢？
[我] 再用拟合做一次精修
"""

UNLABELED = """先做个自我介绍吧？
我是 5 年机器视觉工程师。
亚像素定位是怎么做的？
用形状匹配拿到亚像素坐标。
"""


def _upload(client: TestClient, name: str, data: bytes):
    return client.post(
        "/api/recordings/document",
        files={"file": (name, data, "text/plain")},
    )


def _inspect(client: TestClient, name: str, data: bytes):
    return client.post(
        "/api/recordings/inspect",
        files={"file": (name, data, "text/plain")},
    )


# --------------------------------------------------------------------------- #
# 编码
# --------------------------------------------------------------------------- #
def test_decode_utf8_and_gbk_give_same_text() -> None:
    """同一份中文文稿，UTF-8 与 GBK 两种字节都要能读成同一段文字。"""
    utf8_text, utf8_encoding = decode_document(GOOD_DIALOG.encode("utf-8"))
    gbk_text, gbk_encoding = decode_document(GOOD_DIALOG.encode("gb18030"))

    assert utf8_text == GOOD_DIALOG
    assert gbk_text == GOOD_DIALOG, "GBK 文稿读出来必须是原文，不是乱码"
    assert utf8_encoding.startswith("utf-8")
    # GBK 是 GB18030 的子集，兜底编码就是它
    assert gbk_encoding == "gb18030"


def test_utf8_with_bom_is_stripped() -> None:
    """记事本"UTF-8 带 BOM"很常见：BOM 不能留在正文第一行里（会污染说话人匹配）。"""
    text, encoding = decode_document(GOOD_DIALOG.encode("utf-8-sig"))
    assert not text.startswith("\ufeff")
    assert text.startswith("[面试官]"), "BOM 没剥干净会让第一行匹配不到说话人"
    assert encoding == "utf-8-sig"


def test_empty_and_oversized_files_are_rejected() -> None:
    for bad in (b"", b"   \n\n"):
        try:
            decode_document(bad)
        except TranscriptDocError as exc:
            assert "空" in str(exc)
        else:
            raise AssertionError(f"应当拒绝：{bad!r}")

    try:
        decode_document(b"x" * (MAX_DOC_BYTES + 1))
    except TranscriptDocError as exc:
        assert "太大" in str(exc)
    else:
        raise AssertionError("超限文件应当被拒绝")


# --------------------------------------------------------------------------- #
# 体检
# --------------------------------------------------------------------------- #
def test_check_counts_speakers_and_timestamps() -> None:
    check = check_document(GOOD_DIALOG, encoding="utf-8")
    assert check.segments == 6
    assert check.interviewer == 3
    assert check.candidate == 3
    assert check.labeled == 6
    assert check.usable is True
    assert check.timestamped == 0
    assert any("时间戳" in warning for warning in check.warnings), "没时间戳要说明是估算"


def test_check_infers_roles_from_unlabeled_document() -> None:
    """没标说话人 → 按内容自动分辨谁是面试官，并如实告诉用户这是推断。"""
    check = check_document(UNLABELED, encoding="utf-8")
    assert check.segments == 4
    assert check.interviewer == 2
    assert check.candidate == 2
    assert check.usable is True, "推断出面试官发言后应当能用来学画像"
    assert check.inferred_roles is True, "角色是推断的，必须让用户看见"
    assert any("推断" in warning for warning in check.warnings)


def test_check_counts_real_timestamps_only() -> None:
    """带 `00:12` 的文稿要数出来——它决定"等待时长"是实测还是估算。"""
    text = "00:00 面试官：讲讲你的项目\n00:08 我：我做订单系统\n"
    check = check_document(text, encoding="utf-8")
    assert check.timestamped == 2
    assert not any("没有时间戳" in warning for warning in check.warnings)
    assert "实测算" in check.summary()


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
def test_inspect_endpoint_returns_preview(client: TestClient) -> None:
    response = _inspect(client, "一面.txt", GOOD_DIALOG.encode("utf-8"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["usable"] is True
    assert body["interviewer"] == 3
    assert body["filename"] == "一面.txt"
    assert body["preview"], "要能预览前几段，让用户核对切分"
    assert body["preview"][0]["speaker"] == "interviewer"


def test_inspect_endpoint_infers_roles_on_unlabeled_document(client: TestClient) -> None:
    body = _inspect(client, "没标.txt", UNLABELED.encode("utf-8")).json()
    assert body["usable"] is True
    assert body["inferred_roles"] is True
    assert body["warnings"], "要有推断说明"


def test_upload_document_accepts_gbk_and_creates_recording(client: TestClient) -> None:
    """GBK 文稿走完整链路：上传 → 存成条目 → 生成画像。"""
    response = _upload(client, "某公司一面.txt", GOOD_DIALOG.encode("gb18030"))
    assert response.status_code == 201, response.text
    recording = response.json()
    assert recording["has_text"] is True
    assert recording["has_audio"] is False
    assert recording["title"] == "某公司一面", "标题默认取文件名（去扩展名）"

    # 文稿真的落库了：段落能读出来，且说话人分得开
    detail = client.get(f"/api/recordings/{recording['id']}").json()
    profile = client.post(
        "/api/interviewer-profiles",
        json={"recording_ids": [recording["id"]], "name": "GBK 面"},
    )
    assert profile.status_code == 201, profile.text
    assert profile.json()["samples"], "GBK 文稿也要能切出问答"

    assert detail["segments"], "上传文稿后直接就有段落，不需要再跑一次转写"


def test_upload_document_rejects_unsupported_suffix(client: TestClient) -> None:
    response = _upload(client, "录音.mp3", b"\x00\x01\x02")
    assert response.status_code == 400
    assert "不支持的文件类型" in response.json()["detail"]


def test_upload_document_rejects_empty_file(client: TestClient) -> None:
    response = _upload(client, "空的.txt", b"")
    assert response.status_code == 400
    assert "空" in response.json()["detail"]


def test_upload_document_ignores_path_traversal_in_filename(client: TestClient) -> None:
    """文件名里的路径不能影响标题（更不该被当成路径用）。"""
    response = _upload(client, "..\\..\\evil.txt", GOOD_DIALOG.encode("utf-8"))
    assert response.status_code == 201
    assert "evil" in response.json()["title"]
    assert ".." not in response.json()["title"]


def test_pasted_text_also_gets_segments_immediately(client: TestClient) -> None:
    """**粘贴**文稿也要立刻有段落，不能等到生成画像才有。

    只有上传路径写段落时，粘贴出来的对话点「看段落」是一片空白——
    看起来像功能坏了。两条路必须给出同样的结果。
    """
    created = client.post(
        "/api/recordings",
        json={"title": "粘贴的对话", "raw_text": GOOD_DIALOG},
    ).json()

    detail = client.get(f"/api/recordings/{created['id']}").json()
    assert detail["segments"], "粘贴后应当立刻有段落，而不是空面板"
    assert {segment["speaker"] for segment in detail["segments"]} == {"interviewer", "candidate"}


def test_recording_without_text_has_no_segments(client: TestClient) -> None:
    """只有音频、没文稿时没有段落——不该凭空造出空的段落行。"""
    created = client.post("/api/recordings", json={"title": "只有音频"}).json()
    assert client.get(f"/api/recordings/{created['id']}").json()["segments"] == []


def test_delete_recording_removes_its_segments(client: TestClient) -> None:
    """删对话必须连带删段落——**包括生成过画像之后**。

    这个端点原先没有测试覆盖，而它漏了一层依赖：`pacing_sample.recording_id`
    也指向 `recording`。于是"删掉一段已经学过画像的对话"会 500
    （`FOREIGN KEY constraint failed`）——只在有画像时才炸，所以很容易漏掉。

    孤儿段落同样危险但不报错：它会在 id 复用后**悄悄污染新对话的节奏分析**。
    """
    created = client.post(
        "/api/recordings",
        json={"title": "待删除", "raw_text": GOOD_DIALOG},
    ).json()
    recording_id = created["id"]

    # 先让它产生段落与节奏样本（生成画像两样都会写）
    profile = client.post(
        "/api/interviewer-profiles",
        json={"recording_ids": [recording_id], "name": "临时画像"},
    ).json()
    assert client.get(f"/api/recordings/{recording_id}").json()["segments"], "应该已有段落"

    # 有画像时删除：这正是原先 500 的场景
    response = client.delete(f"/api/recordings/{recording_id}")
    assert response.status_code == 204, f"删不掉（应连带清理依赖）：{response.text}"

    assert client.get(f"/api/recordings/{recording_id}").status_code == 404
    assert client.delete(f"/api/recordings/{recording_id}").status_code == 404

    # 依赖它的画像也必须一起清理：否则画像还引用着已删除的录音，
    # 用户看到的是一份"说不出从哪学来的"画像
    assert client.get(f"/api/interviewer-profiles/{profile['id']}").status_code == 404


def test_deleting_document_leaves_no_orphan_segments(client: TestClient) -> None:
    """删掉上传的文稿后，段落不能留着。

    孤儿段落不会报错，但 id 复用后会**悄悄混进新对话的节奏分析**。
    所以这里用"上传文稿"（会写段落的那条路）验证，而不是 JSON 粘贴
    （它本来就不写段落）。
    """
    first = _upload(client, "第一份.txt", GOOD_DIALOG.encode("utf-8")).json()
    assert client.get(f"/api/recordings/{first['id']}").json()["segments"], "上传后应有段落"
    assert client.delete(f"/api/recordings/{first['id']}").status_code == 204

    second = _upload(client, "第二份.txt", GOOD_DIALOG.encode("utf-8")).json()
    segments = client.get(f"/api/recordings/{second['id']}").json()["segments"]
    assert segments, "新对话应当有自己的段落"
    assert all(segment["text"] in GOOD_DIALOG for segment in segments), "新对话里混进了旧段落"
    # 段落数应与文稿本身的段落数一致（没有翻倍 = 没有残留）
    assert len(segments) == len(client.get(f"/api/recordings/{second['id']}").json()["segments"])


def test_deleting_one_recording_keeps_other_profiles(client: TestClient) -> None:
    """只删掉依赖被删录音的画像，别误伤其它画像。"""
    first = client.post("/api/recordings", json={"title": "甲", "raw_text": GOOD_DIALOG}).json()
    second = client.post("/api/recordings", json={"title": "乙", "raw_text": GOOD_DIALOG}).json()
    keep = client.post(
        "/api/interviewer-profiles",
        json={"recording_ids": [second["id"]], "name": "要保留的画像"},
    ).json()
    doomed = client.post(
        "/api/interviewer-profiles",
        json={"recording_ids": [first["id"]], "name": "会被清理的画像"},
    ).json()

    assert client.delete(f"/api/recordings/{first['id']}").status_code == 204

    assert client.get(f"/api/interviewer-profiles/{keep['id']}").status_code == 200
    assert client.get(f"/api/interviewer-profiles/{doomed['id']}").status_code == 404


# --------------------------------------------------------------------------- #
# Word(docx) 文稿
# --------------------------------------------------------------------------- #
def _make_docx(text: str) -> bytes:
    """用 python-docx 现场造一份对话转写（段落即对话行）。"""
    from io import BytesIO

    from docx import Document

    doc = Document()
    for line in text.splitlines():
        doc.add_paragraph(line)
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_extract_document_text_reads_docx() -> None:
    """docx 走 python-docx 提取，返回原文与解析方式 docx；纯文本路径不受影响。"""
    data = _make_docx(GOOD_DIALOG)
    text, encoding = extract_document_text("一面.docx", data)
    # GOOD_DIALOG 的多行字符串自带尾随换行；docx 按段落 join 不产生尾换行——语义等价即可
    assert text.strip() == GOOD_DIALOG.strip()
    assert encoding == "docx"

    text2, encoding2 = extract_document_text("一面.txt", GOOD_DIALOG.encode("utf-8"))
    assert text2.strip() == GOOD_DIALOG.strip()
    assert encoding2.startswith("utf-8")


def test_docx_upload_endpoint_creates_recording(client: TestClient) -> None:
    """上传 .docx 文稿：201、标题取文件名、落库后立刻有分段，且能切出问答。"""
    response = _upload(client, "某公司一面.docx", _make_docx(GOOD_DIALOG))
    assert response.status_code == 201, response.text
    recording = response.json()
    assert recording["has_text"] is True
    assert recording["title"] == "某公司一面"

    detail = client.get(f"/api/recordings/{recording['id']}").json()
    assert detail["segments"], "docx 上传后也要立刻有段落，不能等生成画像"
    assert {segment["speaker"] for segment in detail["segments"]} == {"interviewer", "candidate"}


def test_docx_inspect_endpoint(client: TestClient) -> None:
    """体检 .docx：能切出问答、报 usable，预览第一段是面试官。"""
    body = _inspect(client, "一面.docx", _make_docx(GOOD_DIALOG)).json()
    assert body["usable"] is True
    assert body["interviewer"] == 3
    assert body["preview"][0]["speaker"] == "interviewer"


# --------------------------------------------------------------------------- #
# 无说话人标记：按内容自动分辨
# --------------------------------------------------------------------------- #
def test_parse_infers_roles_from_unlabeled_dialog() -> None:
    """纯对话（无任何标记）按问句/自述/交替分辨出 面试官-你-面试官-你。"""
    info: list[str] = []
    drafts = parse_transcript(UNLABELED, roles_info=info)
    assert [d.speaker for d in drafts] == ["interviewer", "candidate", "interviewer", "candidate"]
    assert info, "推断必须给出说明，让用户知道这是猜测"
    assert "按内容" in info[0]


def test_parse_neutral_lines_are_not_inferred() -> None:
    """整份都是「嗯/对」这类中性短句 → 没有任何可核对依据，不硬猜。"""
    neutral = "嗯。\n对。\n嗯。\n对。\n"
    info: list[str] = []
    drafts = parse_transcript(neutral, roles_info=info)
    assert [d.speaker for d in drafts] == ["unknown", "unknown", "unknown", "unknown"]
    assert info == []


def test_parse_mixed_labels_and_unlabeled_lines() -> None:
    """部分行标了 `[面试官]`、部分没标 → 未标行按已标角色取反补全。"""
    mixed = "[面试官] 先做个自我介绍吧\n我是 5 年机器视觉工程师\n[面试官] 亚像素定位怎么做的？\n用形状匹配拿到亚像素坐标"
    info: list[str] = []
    drafts = parse_transcript(mixed, roles_info=info)
    assert [d.speaker for d in drafts] == ["interviewer", "candidate", "interviewer", "candidate"]
    assert info, "混合文稿的未标行是推断出来的，也要说明"


def test_inspect_unlabeled_document_now_usable(client: TestClient) -> None:
    """体检无标记文稿：推断出角色后 usable，并标注是推断。"""
    body = _inspect(client, "没标.txt", UNLABELED.encode("utf-8")).json()
    assert body["usable"] is True
    assert body["inferred_roles"] is True
    assert body["warnings"], "要有推断说明"


def test_upload_unlabeled_document_infers_segments(client: TestClient) -> None:
    """上传无标记文稿：落库的段落直接带推断出的角色，可生成画像。"""
    response = _upload(client, "某公司无标记.txt", UNLABELED.encode("utf-8"))
    assert response.status_code == 201, response.text
    recording = response.json()
    detail = client.get(f"/api/recordings/{recording['id']}").json()
    speakers = {segment["speaker"] for segment in detail["segments"]}
    assert speakers == {"interviewer", "candidate"}, f"段落应带推断角色：{speakers}"
    profile = client.post(
        "/api/interviewer-profiles",
        json={"recording_ids": [recording["id"]], "name": "无标记面"},
    )
    assert profile.status_code == 201, profile.text
    assert profile.json()["samples"], "无标记文稿也要能切出问答"
