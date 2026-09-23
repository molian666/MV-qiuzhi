"""半自动投递 API：默认关闭、人工确认闸门、状态与熔断。

这一层要钉住的核心是**不可能绕过确认**：
/search 只入队，/execute 只处理 approved。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlmodel import select

from app.db import session_scope
from app.models.core import Resume


def _enable(client: TestClient, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "enabled": True,
        "risk_acknowledged": True,
        "platform": "mock",
        "default_channel": "boss",
        "want_salary_min": 0,
        "per_minute": 5,
        "per_day": 30,
        "jitter_min_seconds": 0,
        "jitter_max_seconds": 0,
    }
    payload.update(overrides)
    response = client.put("/api/apply/settings", json=payload)
    assert response.status_code == 200
    return response.json()


def _resume_ready(client: TestClient) -> None:
    response = client.post(
        "/api/resumes",
        json={
            "name": "主简历",
            "content_text": "五年后端开发，负责订单系统，熟悉 Python、Redis、MySQL，QPS 从 800 提升到 4000。",
        },
    )
    assert response.status_code in (200, 201)


def _search(client: TestClient, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"keywords": [], "limit": 20}
    payload.update(overrides)
    response = client.post("/api/apply/search", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# 默认状态
# --------------------------------------------------------------------------- #
def test_status_defaults_to_disabled_and_mock(client: TestClient) -> None:
    body = client.get("/api/apply/status").json()
    assert body["enabled"] is False
    assert body["risk_acknowledged"] is False
    assert body["platform"] == "mock"
    assert body["broken"] is False
    assert body["daily_sent"] == 0
    assert body["remaining_quota"] == 30
    assert body["adapter_ready"] is True


def test_settings_default_disabled(client: TestClient) -> None:
    body = client.get("/api/apply/settings").json()
    assert body["enabled"] is False
    # 默认值就是 PLAN 里的保守值
    assert body["per_minute"] == 3
    assert body["per_day"] == 30
    assert body["max_per_day"] == 60


def test_settings_are_clamped_over_api(client: TestClient) -> None:
    body = _enable(client, per_minute=100, per_day=1000)
    assert body["per_minute"] == 10
    assert body["per_day"] == 60


def test_search_is_refused_while_disabled(client: TestClient) -> None:
    response = client.post("/api/apply/search", json={"keywords": []})
    assert response.status_code == 403
    assert "关闭" in response.json()["detail"]


def test_execute_is_refused_while_disabled(client: TestClient) -> None:
    response = client.post("/api/apply/execute", json={"limit": 5})
    assert response.status_code == 403


# --------------------------------------------------------------------------- #
# 规则
# --------------------------------------------------------------------------- #
def test_default_rules_are_created_on_first_read(client: TestClient) -> None:
    rules = client.get("/api/apply/rules").json()
    kinds = [rule["kind"] for rule in rules]
    assert "blacklist" in kinds
    assert "dedup" in kinds
    assert "salary" in kinds
    # 第二次读不会重复创建
    assert len(client.get("/api/apply/rules").json()) == len(rules)


def test_rule_crud(client: TestClient) -> None:
    created = client.post(
        "/api/apply/rules",
        json={"name": "只投杭州", "kind": "city", "priority": 70, "params": {"allow": ["杭州"]}},
    )
    assert created.status_code == 201
    rule_id = created.json()["id"]

    updated = client.put(
        f"/api/apply/rules/{rule_id}",
        json={"name": "只投杭州", "kind": "city", "priority": 70, "enabled": False, "params": {"allow": ["杭州"]}},
    )
    assert updated.json()["enabled"] is False

    assert client.delete(f"/api/apply/rules/{rule_id}").status_code == 204
    assert client.put(f"/api/apply/rules/{rule_id}", json={"kind": "city"}).status_code == 404


# --------------------------------------------------------------------------- #
# 候选 → 确认 → 执行
# --------------------------------------------------------------------------- #
def test_search_queues_candidates_with_reasons(client: TestClient) -> None:
    _enable(client)
    _resume_ready(client)

    body = _search(client)
    assert body["fetched"] == 3
    assert body["queued"] == 2
    assert body["blocked"] == 1

    queued = [item for item in body["candidates"] if item["passed"]]
    blocked = [item for item in body["candidates"] if not item["passed"]]
    assert all(item["say_hi"] for item in queued)
    assert all(item["task_id"] for item in queued)
    assert blocked[0]["reasons"]


def test_search_requires_a_discoverable_adapter(client: TestClient, monkeypatch: Any) -> None:
    _enable(client, platform="mock")
    body = _search(client)
    assert body["notes"] == []  # Mock 一定能抓到岗位


def test_execute_without_approval_does_nothing(client: TestClient) -> None:
    _enable(client)
    _search(client)

    body = client.post("/api/apply/execute", json={"limit": 10}).json()
    assert body["sent"] == 0
    assert body["attempted"] == 0
    assert "没有已确认" in body["stopped_reason"]

    with session_scope() as db:
        from app.models.apply import Application

        assert list(db.exec(select(Application)).all()) == []


def test_approve_then_execute_end_to_end(client: TestClient) -> None:
    _enable(client)
    _search(client)

    queue = client.get("/api/apply/queue", params={"status": "pending"}).json()
    assert len(queue) == 2
    task_ids = [item["id"] for item in queue]

    # 人工确认：其中一条替换成自己写的文案
    approved = client.post(
        "/api/apply/queue/approve",
        json={"task_ids": task_ids, "say_hi_overrides": {task_ids[0]: "  你好，看到你在招后端，我之前把订单接口的 QPS 从 800 提到 4000，方便聊聊吗？  "}},
    )
    assert approved.json()["approved"] == 2

    approved_queue = client.get("/api/apply/queue", params={"status": "approved"}).json()
    assert len(approved_queue) == 2
    edited = next(item for item in approved_queue if item["id"] == task_ids[0])
    assert edited["say_hi_text"].startswith("你好")  # 文案被采用且去掉了首尾空格

    outcome = client.post("/api/apply/execute", json={"limit": 10}).json()
    assert outcome["sent"] == 2
    assert outcome["failed"] == 0
    assert all(item["ok"] for item in outcome["items"])

    status = client.get("/api/apply/status").json()
    assert status["daily_sent"] == 2
    assert status["remaining_quota"] == 28

    snapshot = client.get("/api/apply/queue/snapshot").json()
    assert snapshot["sent"] == 2
    assert snapshot["pending"] == 0

    # 投出去的会出现在投递分析里（source=apply_bot）
    applications = client.get("/api/applications").json()
    sources = [item["source"] for item in applications]
    assert sources.count("apply_bot") == 2


def test_skip_removes_from_execution(client: TestClient) -> None:
    _enable(client)
    _search(client)

    queue = client.get("/api/apply/queue", params={"status": "pending"}).json()
    skipped = client.post("/api/apply/queue/skip", json={"task_ids": [queue[0]["id"]]})
    assert skipped.json()["skipped"] == 1

    assert client.get("/api/apply/queue", params={"status": "skipped"}).json()[0]["id"] == queue[0]["id"]
    assert client.post("/api/apply/execute", json={"limit": 10}).json()["sent"] == 0


def test_dry_run_reports_what_would_be_sent(client: TestClient) -> None:
    _enable(client)
    _search(client)
    queue = client.get("/api/apply/queue", params={"status": "pending"}).json()
    client.post("/api/apply/queue/approve", json={"task_ids": [item["id"] for item in queue]})

    body = client.post("/api/apply/execute", json={"limit": 10, "dry_run": True}).json()
    assert body["attempted"] == 2
    assert body["sent"] == 0
    assert all("演练" in item["detail"] for item in body["items"])


def test_queue_reports_job_details(client: TestClient) -> None:
    _enable(client)
    _search(client)
    queue = client.get("/api/apply/queue").json()
    item = queue[0]
    assert item["company"]
    assert item["title"]
    assert item["salary_range"]
    assert isinstance(item["matched_score"], float)


def test_search_uses_selected_resume(client: TestClient) -> None:
    _enable(client)
    _resume_ready(client)

    with session_scope() as db:
        resume = db.exec(select(Resume)).first()
        assert resume is not None
        resume_id = int(resume.id or 0)

    body = _search(client, resume_id=resume_id)
    assert body["queued"] == 2

    missing = client.post("/api/apply/search", json={"keywords": [], "resume_id": 99999})
    assert missing.status_code == 404
