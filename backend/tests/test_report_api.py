"""报告 API 端到端（Mock 模型）。

Mock 的评分是启发式的（读回答长度、数字、结构词），所以这里能顺带断言
"不同质量的回答得到不同分数"——这比只断言"接口不报错"有价值得多。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.analysis.rubric import dimension_keys


def _run_interview(client: TestClient, *, answers: int = 2, text: str | None = None) -> int:
    created = client.post("/api/sessions", json={"max_questions": answers}).json()
    session_id = int(created["id"])
    for index in range(answers):
        client.post(
            f"/api/sessions/{session_id}/answer",
            json={"text": text or f"第 {index + 1} 轮回答：QPS 从 800 提升到 4200，P99 从 380ms 降到 90ms。"},
        )
    return session_id


def _score_of(report: dict, key: str) -> int:
    return next(item["score"] for item in report["dimensions"] if item["key"] == key)


def test_report_generation_then_read(client: TestClient) -> None:
    session_id = _run_interview(client, answers=2)

    # 还没生成过 → GET 404（让前端决定要不要触发生成，避免白烧 token）
    assert client.get(f"/api/sessions/{session_id}/report").status_code == 404

    created = client.post(f"/api/sessions/{session_id}/report")
    assert created.status_code == 200
    report = created.json()

    assert report["reused"] is False
    assert 0 < report["overall"] <= 100
    assert report["summary"]
    keys = [dimension["key"] for dimension in report["dimensions"]]
    # rubric 的维度必须齐全且保持顺序；M9 起还会多出「表达」（表达体检并入的维度），
    # 所以这里断言的是"包含关系"而不是"恰好相等"
    assert keys[: len(dimension_keys())] == list(dimension_keys())
    assert set(dimension_keys()) <= set(keys)
    assert all(1 <= dimension["score"] <= 5 for dimension in report["dimensions"])
    assert report["items"], "报告至少要有一条改进项"

    # 证据必须是真实存在的回合，并且带原文（前端直接展示，不再拉一次会话）
    turn_ids = {turn["id"] for turn in client.get(f"/api/sessions/{session_id}").json()["turns"]}
    for item in report["items"]:
        assert item["severity"] in {"high", "medium", "low"}
        assert item["problem"]
        for evidence in item["evidence"]:
            assert evidence["turn_id"] in turn_ids
            assert evidence["text"]

    fetched = client.get(f"/api/sessions/{session_id}/report").json()
    assert fetched["id"] == report["id"]
    assert fetched["reused"] is True
    assert fetched["overall"] == report["overall"]

    # 重新生成：旧报告被替换（不堆叠），且这次不是复用
    regenerated = client.post(f"/api/sessions/{session_id}/report").json()
    assert regenerated["reused"] is False
    listed = client.get("/api/reports").json()
    assert len(listed) == 1
    assert listed[0]["id"] == regenerated["id"]


def test_report_requires_at_least_one_answer(client: TestClient) -> None:
    session_id = client.post("/api/sessions", json={"max_questions": 2}).json()["id"]
    response = client.post(f"/api/sessions/{session_id}/report")
    assert response.status_code == 400
    assert "没有" in response.json()["detail"]


def test_report_for_unknown_session(client: TestClient) -> None:
    assert client.post("/api/sessions/999/report").status_code == 404
    assert client.get("/api/sessions/999/report").status_code == 404


def test_reports_list_returns_summaries(client: TestClient) -> None:
    session_id = _run_interview(client, answers=1)
    client.post(f"/api/sessions/{session_id}/report")

    listed = client.get("/api/reports").json()
    assert listed
    assert listed[0]["session_id"] == session_id
    assert listed[0]["summary"]


def test_report_summary_is_written_back_to_session(client: TestClient) -> None:
    """会话摘要同步一份，历史列表不用打开报告就能看到大概。"""
    session_id = _run_interview(client, answers=1)
    report = client.post(f"/api/sessions/{session_id}/report").json()

    detail = client.get(f"/api/sessions/{session_id}").json()
    assert detail["summary"]
    assert detail["summary"][:20] == report["summary"][:20]


def test_mock_scoring_reflects_answer_quality(client: TestClient) -> None:
    """Mock 评分不是随机数：带数字与结构的回答，量化/结构维度分更高。"""
    weak_id = _run_interview(client, answers=1, text="嗯，就是做了些事情吧。")
    weak = client.post(f"/api/sessions/{weak_id}/report").json()

    strong_id = _run_interview(
        client,
        answers=1,
        text=(
            "首先结论：我把下单 QPS 从 800 提到 4200。"
            "其次做法：用预扣库存替代同步扣减，因为业务要求强一致。"
            "最后结果是 P99 从 380ms 降到 90ms，成本增加 5%。"
        ),
    )
    strong = client.post(f"/api/sessions/{strong_id}/report").json()

    assert _score_of(strong, "quantification") > _score_of(weak, "quantification")
    assert _score_of(strong, "structure") > _score_of(weak, "structure")
    assert strong["overall"] > weak["overall"]


def test_scoring_call_is_logged_with_task(client: TestClient) -> None:
    """评分也要进 llm_call 流水（成本与效果对比依赖它）。"""
    session_id = _run_interview(client, answers=1)
    before = client.get("/api/system/usage").json()["total_calls"]
    client.post(f"/api/sessions/{session_id}/report")

    usage = client.get("/api/system/usage").json()
    assert usage["total_calls"] > before
    assert any(item["task"] == "scoring" for item in usage["items"])
