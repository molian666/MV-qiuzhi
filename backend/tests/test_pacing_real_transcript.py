"""用**真实**转写稿跑通节奏学习全链路（回归测试）。

这份稿子来自真实面试的转写软件导出（见 `fixtures_real_transcript.py`），
它曾经**完全无法生成画像**：

    "没有从录音里识别出任何面试问题，无法生成画像"

三个叠加的原因，每一个都能单独让链路断掉：
1. 说话人与时间戳各占一行（`发言人1` / `00:10` / 正文），时间戳吃不到；
2. 说话人是「发言人1/2」，没有任何角色语义 → 全是 unknown；
3. 于是 `split_questions` 一个面试官发言都看不到 → 0 个问题。

所以这个文件钉住的是"用户直接粘一份转写就能用"，而不是某个内部函数行为。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.analysis.pacing import infer_speaker_roles, parse_transcript, split_questions
from tests.fixtures_real_transcript import RAW_TRANSCRIPT


def test_parses_standalone_speaker_header_lines() -> None:
    """说话人与时间各占一行时，两者都要正确取到。"""
    drafts = parse_transcript(RAW_TRANSCRIPT)
    assert len(drafts) > 100, f"不该把 238 行原文当成 238 段对话，实际 {len(drafts)}"

    # 时间戳是真的读到了（不是按字数估算出来的）：00:10 应等于 10000ms
    first = next(d for d in drafts if d.start_ms == 10_000)
    assert first.text.startswith("我们那边没去吃饭")
    assert "发言人" not in first.text, "说话人标签必须从正文里剥掉，不该留在内容里"
    assert "00:10" not in first.text, "时间戳不该污染正文"


def test_infers_roles_for_numbered_speakers() -> None:
    """发言人1/2 没有角色语义 → 必须按"谁在提问"推断出面试官。"""
    drafts = parse_transcript(RAW_TRANSCRIPT)
    speakers = {draft.speaker for draft in drafts}
    assert speakers == {"interviewer", "candidate"}, f"角色推断失败：{speakers}"

    # 方向不能反：面试官那一侧应该是提问多的（"是做视觉工程师是吧？"）
    interviewer_text = " ".join(d.text for d in drafts if d.speaker == "interviewer")
    candidate_text = " ".join(d.text for d in drafts if d.speaker == "candidate")
    assert "是做视觉工程师是吧" in interviewer_text
    assert "我叫方玉荣" in candidate_text, "自我介绍是候选人说的，不能被判成面试官"


def test_infers_roles_from_answer_length_when_both_ask() -> None:
    """★ 技术讨论式面试：两边都在用反问确认，只看"谁在提问"会判不出来。

    真实样本上的两路信号（会议软件导出的技术面转写）：
        提问密度  0.797 vs 0.810   ← 几乎一样，这一路废了
        平均长度  39 字 vs 89 字   ← 差 2.3 倍，这一路很干净

    技术面的主体是候选人在讲方案，所以"发言长度"是主判据，
    提问密度退为辅助。这段文稿刻意造得两人都在提问。
    """
    doc = (
        "发言人1   00:00\n"
        "怎么标定好的？\n"
        "发言人2   00:02\n"
        "什么就是您把刚才那个工件的情况可以再麻烦说一遍吗？\n"
        "发言人1   00:07\n"
        "就是一个杆状的，有一头有个缺口，另一头是光的。我是想能标出缺口那头的旋转角度，"
        "还有它的中心坐标，是吧？\n"
        "发言人2   00:34\n"
        "可以先做一个模板匹配，先匹配到整体形状的朝向，然后在缺口端做圆弧匹配，"
        "拟合出圆弧的两个端点，再拟合成一条线段，通过计算得到圆弧的中心点坐标，"
        "最后结合标定矩阵把像素坐标换算成物理坐标和角度，对吧？\n"
    )

    drafts = parse_transcript(doc)
    speakers = {draft.speaker for draft in drafts}
    assert speakers == {"interviewer", "candidate"}, f"角色推断失败：{speakers}"

    # 方向不能反：讲方案的那位（发言人2）是候选人
    candidate_texts = [d for d in drafts if d.speaker == "candidate"]
    assert candidate_texts, "没有候选人"
    assert all(len(d.text) > 20 for d in candidate_texts), "长的那些该是候选人的发言"
    assert any("模板匹配" in d.text for d in candidate_texts)


def test_header_title_without_interview_keyword() -> None:
    """★「深圳杰美康_原文」不含"面试"二字，曾经没被认成文件头。

    后果不只是多两段噪音：头部判断原来是 `not drafts and ...`，
    第一行漏网之后 `drafts` 就不空了，**后面每一行的头部检查全部失效**，
    于是日期行也跟着进了正文。
    """
    doc = (
        "深圳杰美康_原文\n"
        "2026年09月24日 09:12\n"
        "发言人1   00:00\n"
        "怎么标定好的？\n"
        "发言人2   00:02\n"
        "可以先做模板匹配，然后拟合圆弧取中心点，再结合标定矩阵换算物理坐标和角度。\n"
    )

    drafts = parse_transcript(doc)
    assert drafts, "不该一段都解析不出来"
    assert drafts[0].text == "怎么标定好的？", f"头部没被跳过：{drafts[0].text!r}"
    joined = " ".join(d.text for d in drafts)
    assert "原文" not in joined, "标题行漏进了正文"
    assert "2026年" not in joined, "导出时间漏进了正文"


def test_splits_into_questions_with_real_wait_times() -> None:
    """切出问题，并且等待时长是**实测**的（有时间戳，不是按字数估的）。"""
    units = split_questions(parse_transcript(RAW_TRANSCRIPT))
    assert len(units) >= 15, f"真实一场面试至少该切出十几道题，实际 {len(units)}"
    measured = [unit.wait_ms for unit in units if unit.wait_ms is not None]
    assert measured, "有时间戳却算不出等待时长，说明时间轴没接上"
    assert all(value >= 0 for value in measured)


def test_end_to_end_profile_from_real_transcript(client: TestClient) -> None:
    """全链路：粘贴 → 落库 → 生成画像。这是用户实际会走的路径。"""
    created = client.post(
        "/api/recordings",
        json={"title": "华海达面试（真实转写）", "raw_text": RAW_TRANSCRIPT},
    )
    assert created.status_code == 201, created.text
    recording_id = created.json()["id"]

    profile = client.post(
        "/api/interviewer-profiles",
        json={"recording_ids": [recording_id], "name": "华海达面试官"},
    )
    assert profile.status_code == 201, f"仍然生成不出画像：{profile.text}"

    body = profile.json()
    assert body["samples"], "画像必须留下节奏样本"
    assert body["params"]["source_features"]["question_count"] >= 15
    assert body["style_tags"], "至少要能给出风格标签"
    assert body["policy"]["silence_ms"] > 0


def test_uploaded_document_endpoint_also_works(client: TestClient) -> None:
    """同样的文本走"上传文件"那条路，结果必须一致。"""
    response = client.post(
        "/api/recordings/document",
        files={"file": ("华海达.txt", RAW_TRANSCRIPT.encode("utf-8"), "text/plain")},
        data={"title": "上传路径验证"},
    )
    assert response.status_code == 201, response.text
    recording_id = response.json()["id"]

    detail = client.get(f"/api/recordings/{recording_id}").json()
    assert detail["segments"], "上传后应直接落段落"
    speakers = {segment["speaker"] for segment in detail["segments"]}
    assert speakers == {"interviewer", "candidate"}


# --------------------------------------------------------------------------- #
# 不要把已有的明确标记弄坏
# --------------------------------------------------------------------------- #
def test_explicit_labels_are_never_overridden() -> None:
    """用户明确写了 [面试官]/[我] 时，不该被"推断"覆盖。"""
    labeled = "[面试官] 先做个自我介绍吧\n[我] 我是 5 年机器视觉工程师\n"
    drafts = parse_transcript(labeled)
    assert infer_speaker_roles(drafts) == "", "已有明确标记时不该做推断"
    assert [d.speaker for d in drafts] == ["interviewer", "candidate"]


def test_single_speaker_is_left_unknown() -> None:
    """只有一个人说话时没有可比较信号，不该硬猜。"""
    drafts = parse_transcript("发言人1 00:01：今天天气不错，我们开始吧。\n")
    assert drafts, "至少要解析出一段"
    assert {d.speaker for d in drafts} == {"unknown"}


def test_ambiguous_two_speakers_stay_unknown() -> None:
    """两边提问密度接近时宁可保持 unknown，也不要猜反。"""
    text = (
        "发言人1 00:01：\n你们公司是做什么的？\n"
        "发言人2 00:05：\n你们这边加班多吗？\n"
        "发言人1 00:09：\n薪资结构是怎么样的？\n"
        "发言人2 00:13：\n有年终奖吗？\n"
    )
    drafts = parse_transcript(text)
    assert {d.speaker for d in drafts} == {"unknown"}, "分不清就该保持未标注"
