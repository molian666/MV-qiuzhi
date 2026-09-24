"""入职清单 / OKR / 长期规划 API 测试（M15）。验收点：可勾选追踪。"""

from __future__ import annotations

from fastapi.testclient import TestClient


# --------------------------------------------------------------------------- #
# 入职清单
# --------------------------------------------------------------------------- #
def test_onboarding_seeds_defaults_on_first_read(client: TestClient) -> None:
    payload = client.get("/api/onboarding").json()
    assert payload["progress"]["total"] >= 15
    categories = {item["category"] for item in payload["progress"]["by_category"]}
    assert {"背调材料", "体检", "学历与离职证明", "社保与公积金", "离职交接", "租房与落户"} <= categories
    # 每一项都带「为什么需要 + 去哪里办」
    for task in payload["tasks"]:
        assert task["why_needed"].strip() and task["where_to_do"].strip()


def test_onboarding_seed_is_idempotent(client: TestClient) -> None:
    first = client.get("/api/onboarding").json()["progress"]["total"]
    added = client.post("/api/onboarding/seed").json()["added"]
    second = client.get("/api/onboarding").json()["progress"]["total"]
    assert added == 0
    assert second == first


def test_onboarding_check_updates_progress(client: TestClient) -> None:
    payload = client.get("/api/onboarding").json()
    first_task = payload["tasks"][0]
    total = payload["progress"]["total"]

    checked = client.put(f"/api/onboarding/{first_task['id']}/check", json={"done": True}).json()
    assert checked["done"] is True
    assert checked["done_at"]

    progress = client.get("/api/onboarding").json()["progress"]
    assert progress["done"] == 1
    assert progress["pct"] == round(1 / total * 100)

    # 取消勾选 → 进度回退
    client.put(f"/api/onboarding/{first_task['id']}/check", json={"done": False})
    progress = client.get("/api/onboarding").json()["progress"]
    assert progress["done"] == 0
    assert progress["pct"] == 0


def test_onboarding_custom_task_crud(client: TestClient) -> None:
    created = client.post("/api/onboarding", json={"category": "租房与落户", "title": "办居住登记",
                                                   "why_needed": "落户要用", "where_to_do": "派出所"}).json()
    assert created["id"]
    updated = client.put(f"/api/onboarding/{created['id']}", json={"note": "问 HR 要材料清单"}).json()
    assert updated["note"] == "问 HR 要材料清单"
    deleted = client.delete(f"/api/onboarding/{created['id']}")
    assert deleted.status_code == 204
    assert client.put(f"/api/onboarding/{created['id']}/check", json={"done": True}).status_code == 404


# --------------------------------------------------------------------------- #
# 试用期 OKR
# --------------------------------------------------------------------------- #
def test_okr_crud_and_progress(client: TestClient) -> None:
    obj = client.post("/api/okr/objectives", json={"title": "试用期站稳", "period": "2026Q4"}).json()
    kr = client.post(f"/api/okr/objectives/{obj['id']}/krs",
                     json={"title": "独立交付需求", "unit": "个", "target_value": 5}).json()
    assert kr["krs"][0]["pct"] == 0.0 and kr["krs"][0]["status"] == "未开始"

    progress = client.patch(f"/api/okr/krs/{kr['krs'][0]['id']}/progress",
                            json={"current_value": 3, "confidence": 70}).json()
    assert progress["pct"] == 60.0
    assert progress["status"] == "进行中"

    okr = client.get("/api/okr").json()
    assert okr[0]["pct"] == 60.0
    assert okr[0]["status_label"] == "进行中"

    # 删目标 → KR 一起没了
    client.delete(f"/api/okr/objectives/{obj['id']}")
    assert client.get("/api/okr").json() == []


def test_okr_review_draft_from_records(client: TestClient) -> None:
    obj = client.post("/api/okr/objectives", json={"title": "独挡一面"}).json()
    client.post(f"/api/okr/objectives/{obj['id']}/krs",
                json={"title": "交付需求", "unit": "个", "target_value": 4, "current_value": 4})
    payload = client.get("/api/okr/review").json()
    assert "4 / 4 个（100.0%，已完成）" in payload["markdown"]
    assert payload["note"]
    # 空场景不假装有内容
    # （另一个隔离的测试环境才会是空的；这里只验证有数据时的行为）


def test_okr_review_empty_is_honest(client: TestClient) -> None:
    payload = client.get("/api/okr/review").json()
    assert "还没有记录任何目标" in payload["markdown"]


def test_okr_validation(client: TestClient) -> None:
    assert client.post("/api/okr/objectives", json={"title": "  "}).status_code == 422
    assert client.post("/api/okr/objectives", json={"title": "X", "status": "bogus"}).status_code == 422
    assert client.post("/api/okr/objectives/999/krs", json={"title": "Y"}).status_code == 404
    assert client.patch("/api/okr/krs/999/progress", json={"current_value": 1}).status_code == 404


# --------------------------------------------------------------------------- #
# 长期规划与薪资基准
# --------------------------------------------------------------------------- #
def test_career_plan_crud(client: TestClient) -> None:
    item = client.post("/api/career-plan", json={"kind": "promotion", "title": "明年冲击高级",
                                                 "detail": "先补系统设计", "target_date": "2027-06-30"}).json()
    assert item["kind"] == "promotion"
    items = client.get("/api/career-plan").json()
    assert any(entry["title"] == "明年冲击高级" for entry in items)
    assert client.put(f"/api/career-plan/{item['id']}", json={"status": "doing"}).json()["status"] == "doing"
    assert client.delete(f"/api/career-plan/{item['id']}").status_code == 204
    assert client.post("/api/career-plan", json={"kind": "nope", "title": "X"}).status_code == 422


def test_benchmarks_require_source_and_summary_flags_small_sample(client: TestClient) -> None:
    # 没来源的薪资数字不可信 → 422
    assert client.post("/api/benchmarks", json={"company": "甲", "range_low": 30, "range_high": 40}).status_code == 422
    # 区间倒挂 → 422
    assert client.post("/api/benchmarks", json={"source": "同学", "range_low": 40, "range_high": 30}).status_code == 422

    client.post("/api/benchmarks", json={"company": "甲公司", "title": "后端", "city": "上海",
                                         "range_low": 300000, "range_high": 380000, "period": "2026",
                                         "source": "同学A打听", "verified": False})
    rows = client.get("/api/benchmarks").json()
    assert rows and rows[0]["verified"] is False

    summary = client.get("/api/benchmarks/summary").json()
    assert summary["count"] == 1
    assert "仅供参考" in summary["note"]
    assert "不构成定价建议" in summary["note"]
    assert summary["sources"] == ["同学A打听"]


def test_timing_checklist_and_options(client: TestClient) -> None:
    timing = client.get("/api/career/timing").json()
    kinds = {item["kind"] for item in timing["promotion"]} | {item["kind"] for item in timing["switch"]}
    assert kinds == {"promotion", "switch"}
    assert "该问什么" in timing["note"]

    options = client.get("/api/career/options").json()
    assert "体检" in options["categories"]
    assert {item["key"] for item in options["plan_kinds"]} == {"promotion", "switch", "skill", "other"}
