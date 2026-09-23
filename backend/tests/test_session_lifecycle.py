"""M0 验收测试：文字面试端到端 + 落库 + 用量日志。

验收标准（PLAN §9）：浏览器里能用文字完成一场面试并落库；pytest 覆盖会话生命周期。
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _create_target(client: TestClient) -> int:
    response = client.post(
        "/api/targets",
        json={
            "company": "某某科技",
            "title": "后端工程师",
            "level": "P6",
            "jd_text": "负责订单系统；要求熟悉 Python / FastAPI / SQL，有分布式与性能优化经验。",
        },
    )
    assert response.status_code == 201
    return int(response.json()["id"])


def test_health_reports_mock_provider(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["llm_provider"] == "mock"


def test_full_text_interview_lifecycle(client: TestClient) -> None:
    target_id = _create_target(client)
    resume_response = client.post(
        "/api/resumes",
        json={"name": "主简历", "content_text": "5 年后端经验，负责过日活百万的订单系统。"},
    )
    assert resume_response.status_code == 201
    resume_id = resume_response.json()["id"]

    created = client.post(
        "/api/sessions",
        json={"target_id": target_id, "resume_id": resume_id, "max_questions": 3},
    )
    assert created.status_code == 201
    detail = created.json()
    session_id = detail["id"]

    assert detail["status"] == "live"
    assert detail["question_count"] == 1 and detail["answer_count"] == 0
    assert detail["turns"][0]["speaker"] == "interviewer"
    assert detail["turns"][0]["text"].strip()
    assert detail["target_label"] == "某某科技 · 后端工程师"

    # 前两轮：正常继续
    for index in range(2):
        response = client.post(
            f"/api/sessions/{session_id}/answer",
            json={"text": f"这是第 {index + 1} 轮回答：我在订单系统里负责下单链路的重构。"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["finished"] is False
        assert body["question"]["speaker"] == "interviewer"
        assert body["question"]["text"].strip()

    # 第三轮达到 max_questions → 自动收尾
    final = client.post(f"/api/sessions/{session_id}/answer", json={"text": "第三轮回答"}).json()
    assert final["finished"] is True
    assert final["session"]["status"] == "finished"
    assert final["session"]["duration_ms"] is not None
    assert final["question"]["kind"] == "closing"

    speakers = [turn["speaker"] for turn in final["session"]["turns"]]
    assert speakers == [
        "interviewer",
        "candidate",
        "interviewer",
        "candidate",
        "interviewer",
        "candidate",
        "interviewer",
    ]
    assert final["session"]["answer_count"] == 3

    # 结束后不允许继续作答
    blocked = client.post(f"/api/sessions/{session_id}/answer", json={"text": "还想补一句"})
    assert blocked.status_code == 409

    # 详情与列表
    stored = client.get(f"/api/sessions/{session_id}").json()
    assert stored["answer_count"] == 3
    assert [t["idx"] for t in stored["turns"]] == list(range(len(stored["turns"])))

    listed = client.get("/api/sessions").json()
    assert listed and listed[0]["id"] == session_id
    assert listed[0]["question_count"] == 3


def test_session_rejects_unknown_target_and_resume(client: TestClient) -> None:
    assert client.post("/api/sessions", json={"target_id": 999}).status_code == 404
    assert client.post("/api/sessions", json={"resume_id": 999}).status_code == 404


def test_unknown_session_returns_404(client: TestClient) -> None:
    assert client.get("/api/sessions/999").status_code == 404
    assert client.post("/api/sessions/999/finish").status_code == 404
    assert client.post("/api/sessions/999/answer", json={"text": "喂"}).status_code == 404


def test_manual_finish_marks_session_finished(client: TestClient) -> None:
    session_id = client.post("/api/sessions", json={"max_questions": 5}).json()["id"]
    assert client.post(f"/api/sessions/{session_id}/answer", json={"text": "先答一轮"}).json()["finished"] is False

    finished = client.post(f"/api/sessions/{session_id}/finish")
    assert finished.status_code == 200
    assert finished.json()["status"] == "finished"

    # 幂等：再结束一次不报错、时间不重置
    again = client.post(f"/api/sessions/{session_id}/finish")
    assert again.status_code == 200
    assert again.json()["ended_at"] == finished.json()["ended_at"]


def test_llm_calls_are_logged_with_task(client: TestClient) -> None:
    session_id = client.post("/api/sessions", json={"max_questions": 2}).json()["id"]
    client.post(f"/api/sessions/{session_id}/answer", json={"text": "答一轮"})

    usage = client.get("/api/system/usage").json()
    assert usage["total_calls"] >= 2
    assert usage["failed_calls"] == 0
    tasks = {item["task"] for item in usage["items"]}
    assert "interview_turn" in tasks


def test_turn_timestamps_are_monotonic(client: TestClient) -> None:
    """M1 起音频要对齐这些时间戳，M9 表达体检靠它定位到句子，所以现在就要单调。"""
    session_id = client.post("/api/sessions", json={"max_questions": 2}).json()["id"]
    client.post(f"/api/sessions/{session_id}/answer", json={"text": "第一轮"})
    client.post(f"/api/sessions/{session_id}/answer", json={"text": "第二轮"})

    turns = client.get(f"/api/sessions/{session_id}").json()["turns"]
    ends = [turn["end_ms"] for turn in turns if turn["end_ms"] is not None]
    assert ends == sorted(ends)

    candidates = [turn for turn in turns if turn["speaker"] == "candidate"]
    assert all(turn["start_ms"] <= turn["end_ms"] for turn in candidates)
