"""系统设计白板测试（M13）。

这个模块的验收标准与算法题不同：**系统设计没有唯一正确答案**，所以测试盯的是
「评估有没有如实反映候选人说没说」——尤其是那个最容易出错的点：
**模型会不会替候选人补上他没提到的要点**。

出于同样原因，「题目本身的评分维度是否合理」「题面是否给了必要的约束」这些
也进测试：设计题的质量全在题面上，而题面错了测试不会报错。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.design_service import MIN_WRITEUP_LENGTH
from app.providers.llm.mock_design import _coverage
from app.vision.design import all_design_questions, get_design_question
from app.vision.design_review import (
    build_input_payload,
    extract_scene_labels,
    parse_design_review,
)

# --------------------------------------------------------------------------- #
# 题目内容
# --------------------------------------------------------------------------- #


def test_design_questions_are_complete() -> None:
    for question in all_design_questions():
        assert question.title.strip()
        assert 1 <= question.difficulty <= 5
        assert len(question.statement) >= 150, f"{question.slug} 题面太短"
        assert question.constraints, f"{question.slug} 没有给约束"
        assert question.open_questions, f"{question.slug} 没有「需要自己问的」清单"
        assert question.rubric, f"{question.slug} 没有评分维度"
        assert question.followups, f"{question.slug} 没有追问"


def test_every_question_has_a_core_dimension() -> None:
    """至少要有一个 core 维度——否则判定就没有及格线。"""
    for question in all_design_questions():
        assert any(item.weight == "core" for item in question.rubric), question.slug


def test_rubric_dimensions_have_expectations() -> None:
    for question in all_design_questions():
        for item in question.rubric:
            assert item.expects, f"{question.slug} 的「{item.dimension}」没有对照要点"


def test_questions_require_clarification() -> None:
    """系统设计题的核心考点是「先问清再设计」，每题都该有需要自己问的东西。"""
    for question in all_design_questions():
        assert len(question.open_questions) >= 4, f"{question.slug} 的可问项太少"


def test_question_slugs_are_unique() -> None:
    slugs = [question.slug for question in all_design_questions()]
    assert len(slugs) == len(set(slugs))


# --------------------------------------------------------------------------- #
# 画布标签提取
# --------------------------------------------------------------------------- #


def test_extract_scene_labels_pulls_text_elements() -> None:
    scene = json.dumps(
        {
            "elements": [
                {"type": "text", "text": "工业相机"},
                {"type": "rectangle"},
                {"type": "text", "text": "PLC"},
                {"type": "text", "text": "工业相机"},  # 重复的只留一次
            ]
        },
        ensure_ascii=False,
    )
    assert extract_scene_labels(scene) == ["工业相机", "PLC"]


def test_extract_scene_labels_tolerates_garbage() -> None:
    """画布内容来自前端，不该因为格式异常就让整次提交失败。"""
    assert extract_scene_labels("") == []
    assert extract_scene_labels("不是 JSON") == []
    assert extract_scene_labels('{"elements": "not-a-list"}') == []
    assert extract_scene_labels(json.dumps([{"type": "text", "text": "裸数组也认"}])) == ["裸数组也认"]


# --------------------------------------------------------------------------- #
# 评估解析：不许替候选人补
# --------------------------------------------------------------------------- #


def _review(text: str):
    question = all_design_questions()[0]
    return parse_design_review(text, question=question)


def test_parse_keeps_mentioned_and_missing_separate() -> None:
    question = all_design_questions()[0]
    first = question.rubric[0]
    text = json.dumps(
        {
            "dimensions": [
                {
                    "dimension": first.dimension,
                    "score": "ok",
                    "assessment": "问了节拍，没问精度",
                    "mentioned": [{"point": "问了节拍", "kind": "stated"}],
                    "missing": ["没有确认最小可检缺陷尺寸"],
                }
            ],
            "gaps": ["需求澄清只做了一半"],
            "summary": "整体还行",
        },
        ensure_ascii=False,
    )
    review = _review(text)

    result = next(item for item in review.dimensions if item.dimension == first.dimension)
    assert result.score == "ok"
    assert [item.point for item in result.mentioned] == ["问了节拍"]
    assert result.missing == ["没有确认最小可检缺陷尺寸"]
    assert review.gaps == ["需求澄清只做了一半"]


def test_missing_dimensions_are_filled_in_not_dropped() -> None:
    """模型漏给某一维时，界面上仍该有这一维——否则看起来像它不存在。"""
    question = all_design_questions()[0]
    review = _review(json.dumps({"dimensions": []}, ensure_ascii=False))

    assert len(review.dimensions) == len(question.rubric)
    assert all(item.score == "missing" for item in review.dimensions)


def test_extra_dimensions_from_model_are_discarded() -> None:
    """模型自造的维度不算——评分维度以题目为准。"""
    question = all_design_questions()[0]
    review = _review(
        json.dumps(
            {
                "dimensions": [
                    {"dimension": "我自己加的维度", "score": "strong", "missing": []},
                ]
            },
            ensure_ascii=False,
        )
    )
    assert {item.dimension for item in review.dimensions} == {
        item.dimension for item in question.rubric
    }


def test_unknown_kind_defaults_to_inferred() -> None:
    """分不清是「他说的」还是「你推断的」，就按推断算。"""
    question = all_design_questions()[0]
    review = _review(
        json.dumps(
            {
                "dimensions": [
                    {
                        "dimension": question.rubric[0].dimension,
                        "score": "ok",
                        "mentioned": [{"point": "提到了支架刚性"}],
                    }
                ]
            },
            ensure_ascii=False,
        )
    )
    result = review.dimensions[0]
    assert result.mentioned[0].kind == "inferred"


def test_parse_degrades_instead_of_raising() -> None:
    review = _review("模型今天不想输出 JSON")
    assert review.verdict == "unknown"
    assert "未生成" in review.notes


def test_verdict_follows_core_dimensions() -> None:
    """core 维度是及格线：核心维度没覆盖，总判定就不该是 strong。"""
    question = all_design_questions()[0]
    core = [item for item in question.rubric if item.weight == "core"]

    all_strong = _review(
        json.dumps(
            {
                "dimensions": [
                    {"dimension": item.dimension, "score": "strong"} for item in question.rubric
                ]
            },
            ensure_ascii=False,
        )
    )
    assert all_strong.verdict == "strong"

    core_missing = _review(
        json.dumps(
            {
                "dimensions": [
                    {
                        "dimension": item.dimension,
                        "score": "missing" if item in core else "strong",
                    }
                    for item in question.rubric
                ]
            },
            ensure_ascii=False,
        )
    )
    assert core_missing.verdict != "strong"
    assert "未覆盖" in core_missing.notes


def test_input_payload_carries_rubric_and_candidate_text() -> None:
    question = all_design_questions()[0]
    payload = build_input_payload(
        question, writeup="我会先问节拍", scene_labels=["工业相机", "PLC"]
    )

    assert payload["rubric"][0]["dimension"] == question.rubric[0].dimension
    assert payload["rubric"][0]["expects"] == list(question.rubric[0].expects)
    assert payload["candidate"]["writeup"] == "我会先问节拍"
    assert payload["candidate"]["whiteboard_labels"] == ["工业相机", "PLC"]


def test_mock_coverage_is_not_diluted_by_a_long_answer() -> None:
    """Mock 的匹配不能用 Jaccard。

    作答比单条要点长得多，Jaccard 的分母（并集）会被作答主导，于是写得越详细、
    每条要点越难达标——量纲反了。实测就是这样：一段写足了成像细节的作答，
    被判成「成像方案：未覆盖」。这个 bug 冒烟抓到的，加个单测钉住。
    """
    expect = "针对粉尘与振动：保护镜、正压吹气、刚性支架、缩短曝光"
    long_answer = "先确认节拍与误检代价。" * 3 + expect + "另外还有些无关内容。" * 5

    assert _coverage(expect, long_answer) >= 0.9, "要点出现在长作答里，覆盖率就该接近 1"
    assert _coverage(expect, "完全无关的另一段话") < 0.3, "无关内容不该被判成提到"


def test_mock_coverage_handles_empty_input() -> None:
    assert _coverage("", "任何内容") == 0.0


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


def test_list_questions(client: TestClient) -> None:
    items = client.get("/api/design/questions").json()
    assert len(items) == len(all_design_questions())
    assert items[0]["dimensions"]
    assert "expects" not in str(items), "评分参照不能提前下发"


def test_question_detail_includes_open_questions(client: TestClient) -> None:
    body = client.get("/api/design/questions/design-inline-defect-pipeline").json()

    assert body["constraints"]
    assert len(body["open_questions"]) >= 4
    assert body["hints"]
    assert "expects" not in str(body)


def test_question_detail_404(client: TestClient) -> None:
    assert client.get("/api/design/questions/nope").status_code == 404


def test_submit_attempt_records_and_reviews(client: TestClient) -> None:
    scene = json.dumps(
        {"elements": [{"type": "text", "text": "工业相机"}, {"type": "text", "text": "PLC 剔除"}]},
        ensure_ascii=False,
    )
    response = client.post(
        "/api/design/attempts",
        json={
            "question_slug": "design-inline-defect-pipeline",
            "writeup": "先问节拍和精度要求，再定成像方案；振动用刚性支架，粉尘用保护镜。",
            "scene": scene,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["question_title"]
    assert body["scene_labels"] == ["工业相机", "PLC 剔除"]
    assert body["review"]["dimensions"], "要按维度给评估"
    assert body["review"]["summary"], "要有总评"
    assert body["verdict"]


def test_attempt_writeup_alone_is_accepted(client: TestClient) -> None:
    """只写文字不画图也能评——白板是帮自己想清楚，不是提交的必要条件。"""
    response = client.post(
        "/api/design/attempts",
        json={
            "question_slug": "design-precision-measurement",
            "writeup": "先把 ±0.02 拆成重复性与准确度，再用标准件实测验证，机械治具的重复性要一起算。",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["scene_labels"] == []


def test_attempt_scene_alone_is_accepted_if_long_enough(client: TestClient) -> None:
    """只画不写也能评：画布上的文字标签会被抽出来当方案的一部分。"""
    scene = json.dumps(
        {
            "elements": [
                {"type": "text", "text": "相机"},
                {"type": "text", "text": "光源"},
                {"type": "text", "text": "PLC 握手"},
            ]
        },
        ensure_ascii=False,
    )
    response = client.post(
        "/api/design/attempts",
        json={"question_slug": "design-inline-defect-pipeline", "scene": scene},
    )
    assert response.status_code == 200, response.text
    assert response.json()["scene_labels"] == ["相机", "光源", "PLC 握手"]


def test_empty_submission_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/design/attempts",
        json={"question_slug": "design-inline-defect-pipeline", "writeup": "", "scene": ""},
    )
    assert response.status_code == 400
    assert "没法评估" in response.text


def test_too_short_writeup_without_scene_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/design/attempts",
        json={"question_slug": "design-inline-defect-pipeline", "writeup": "先看需求"},
    )
    assert response.status_code == 400
    assert str(MIN_WRITEUP_LENGTH) in response.text


def test_submit_unknown_question_404(client: TestClient) -> None:
    response = client.post(
        "/api/design/attempts", json={"question_slug": "nope", "writeup": "一段足够长的说明文字"}
    )
    assert response.status_code == 404


def test_attempts_list_omits_scene(client: TestClient) -> None:
    """画布 JSON 可能几 MB，列表不该带上它。"""
    scene = json.dumps({"elements": [{"type": "text", "text": "相机"}] * 50}, ensure_ascii=False)
    client.post(
        "/api/design/attempts",
        json={"question_slug": "design-inline-defect-pipeline", "writeup": "一段足够长的方案说明文字", "scene": scene},
    )

    items = client.get("/api/design/attempts").json()
    assert len(items) == 1
    assert items[0]["scene"] == ""
    assert items[0]["scene_labels"]


def test_attempt_detail_returns_scene_for_reload(client: TestClient) -> None:
    scene = json.dumps({"elements": [{"type": "text", "text": "相机"}]}, ensure_ascii=False)
    created = client.post(
        "/api/design/attempts",
        json={"question_slug": "design-inline-defect-pipeline", "writeup": "一段足够长的方案说明文字", "scene": scene},
    ).json()

    detail = client.get(f"/api/design/attempts/{created['id']}").json()
    assert detail["scene"] == scene, "详情要给完整画布，前端才能载入继续画"


def test_attempt_detail_404(client: TestClient) -> None:
    assert client.get("/api/design/attempts/99999").status_code == 404


def test_attempts_filter_by_question(client: TestClient) -> None:
    client.post(
        "/api/design/attempts",
        json={
            "question_slug": "design-inline-defect-pipeline",
            "writeup": "先问清节拍与最小可检缺陷尺寸，再定成像方案；振动用刚性支架，粉尘用保护镜与正压吹气。",
        },
    )
    client.post(
        "/api/design/attempts",
        json={
            "question_slug": "design-retrofit-existing-line",
            "writeup": "先去现场量安装空间与可用工作距离，再确认停机窗口与 PLC 改动排期，把做不到的部分写进方案。",
        },
    )

    filtered = client.get(
        "/api/design/attempts", params={"slug": "design-retrofit-existing-line"}
    ).json()
    assert len(filtered) == 1
    assert filtered[0]["question_slug"] == "design-retrofit-existing-line"


def test_question_list_shows_progress(client: TestClient) -> None:
    client.post(
        "/api/design/attempts",
        json={
            "question_slug": "design-inline-defect-pipeline",
            "writeup": "先确认节拍与误检代价，再把成像按振动、环境光、粉尘三个不利条件分别应对。",
        },
    )
    item = next(
        row
        for row in client.get("/api/design/questions").json()
        if row["slug"] == "design-inline-defect-pipeline"
    )
    assert item["attempts"] == 1
    assert item["last_verdict"]


def test_design_attempts_do_not_touch_interview_sessions(client: TestClient) -> None:
    client.post(
        "/api/design/attempts",
        json={"question_slug": "design-inline-defect-pipeline", "writeup": "一段足够长的方案说明文字"},
    )
    assert client.get("/api/sessions").json() == []
