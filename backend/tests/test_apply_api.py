"""投递数据 API 端到端（M6）。

重点覆盖三件事：导入能出漏斗、重复导入不重复记、旧数据不把新状态覆盖回去。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

CSV_TEXT = """公司,岗位,渠道,状态,投递时间,简历版本
某某科技,后端工程师,Boss直聘,已投递,2026-03-01,主简历
某某网络,Python开发,猎聘,已查看,2026-03-02,量化版
某某信息,服务端工程师,Boss直聘,一面,2026-03-03,主简历
"""


def test_import_then_funnel_and_channels(client: TestClient) -> None:
    outcome = client.post("/api/applications/import", json={"text": CSV_TEXT}).json()

    assert outcome["inserted"] == 3
    assert outcome["skipped"] == 0
    assert outcome["unmatched_headers"] == []
    assert {"company", "title", "channel", "status", "applied_at"} <= set(outcome["matched_columns"])

    stats = client.get("/api/applications/stats").json()
    assert stats["total"] == 3
    assert {item["channel"] for item in stats["by_channel"]} == {"boss", "liepin"}

    boss = next(item for item in stats["by_channel"] if item["channel"] == "boss")
    assert boss["total"] == 2
    assert boss["interviewed"] == 1
    assert boss["interview_rate"] == 0.5

    funnel = {step["stage"]: step["count"] for step in stats["funnel"]}
    assert funnel["applied"] == 3
    assert funnel["viewed"] == 2
    assert funnel["interview"] == 1
    assert funnel["offer"] == 0


def test_reimport_is_deduplicated(client: TestClient) -> None:
    client.post("/api/applications/import", json={"text": CSV_TEXT})
    again = client.post("/api/applications/import", json={"text": CSV_TEXT}).json()

    assert again["inserted"] == 0
    assert again["updated"] == 0
    assert again["skipped"] == 3
    assert len(client.get("/api/applications").json()) == 3


def test_reimport_with_newer_status_updates(client: TestClient) -> None:
    client.post("/api/applications/import", json={"text": CSV_TEXT})
    advanced = client.post(
        "/api/applications/import",
        json={"text": CSV_TEXT.replace("已投递", "二面")},
    ).json()

    assert advanced["updated"] == 1
    apps = client.get("/api/applications").json()
    target = next(item for item in apps if item["company"] == "某某科技")
    assert target["status"] == "interview2"


def test_reimport_with_older_status_does_not_regress(client: TestClient) -> None:
    """旧数据不该把更靠后的状态覆盖回去（导入是最容易踩这个的场景）。"""
    client.post("/api/applications/import", json={"text": CSV_TEXT})
    apps = client.get("/api/applications").json()
    target = next(item for item in apps if item["company"] == "某某科技")

    client.post(f"/api/applications/{target['id']}/status", json={"to_status": "offer"})
    assert client.post("/api/applications/import", json={"text": CSV_TEXT}).json()["updated"] == 0

    refreshed = next(item for item in client.get("/api/applications").json() if item["id"] == target["id"])
    assert refreshed["status"] == "offer"


def test_import_reports_unmatched_headers(client: TestClient) -> None:
    outcome = client.post("/api/applications/import", json={"text": "公司,岗位,神秘列\nA,B,C"}).json()
    assert "神秘列" in outcome["unmatched_headers"]


def test_import_without_usable_content_fails_readably(client: TestClient) -> None:
    response = client.post("/api/applications/import", json={"text": "只有一行没有表头的内容"})
    assert response.status_code == 400
    assert "表头" in response.json()["detail"]


def test_ingest_from_extension(client: TestClient) -> None:
    payload = {
        "platform": "Boss直聘",
        "rows": [
            {"公司": "某某科技", "岗位": "后端", "状态": "已投递", "投递时间": "2026-03-05"},
            {"公司": "某某网络", "岗位": "Python", "状态": "已查看", "投递时间": "2026-03-06"},
        ],
    }
    outcome = client.post("/api/applications/ingest", json=payload).json()
    assert outcome["inserted"] == 2
    assert outcome["source"] == "extension"

    apps = client.get("/api/applications").json()
    assert {item["channel"] for item in apps} == {"boss"}
    assert all(item["source"] == "extension" for item in apps)


def test_ingest_without_recognisable_columns_fails_readably(client: TestClient) -> None:
    response = client.post("/api/applications/ingest", json={"platform": "boss", "rows": [{"foo": "bar"}]})
    assert response.status_code == 400
    assert "公司" in response.json()["detail"]


def test_manual_create_and_status_change(client: TestClient) -> None:
    created = client.post("/api/applications", json={"company": "某公司", "title": "后端", "channel": "boss"}).json()
    assert created["status"] == "applied"
    assert created["source"] == "manual"

    updated = client.post(f"/api/applications/{created['id']}/status", json={"to_status": "offer"}).json()
    assert updated["status"] == "offer"

    stats = client.get("/api/applications/stats").json()
    assert stats["offers"] == 1
    assert stats["offer_rate"] == 1.0


def test_unknown_status_is_rejected(client: TestClient) -> None:
    created = client.post("/api/applications", json={"company": "X"}).json()
    response = client.post(f"/api/applications/{created['id']}/status", json={"to_status": "瞎写"})
    assert response.status_code == 400


def test_import_flow_is_recorded(client: TestClient) -> None:
    client.post("/api/applications/import", json={"text": CSV_TEXT})
    imports = client.get("/api/applications/imports").json()

    assert imports
    assert imports[0]["source"] == "csv"
    assert imports[0]["inserted"] == 3


def test_list_filters_and_delete(client: TestClient) -> None:
    client.post("/api/applications/import", json={"text": CSV_TEXT})

    boss_only = client.get("/api/applications?channel=boss").json()
    assert len(boss_only) == 2

    interviewed = client.get("/api/applications?status=interview1").json()
    assert len(interviewed) == 1

    target_id = boss_only[0]["id"]
    assert client.delete(f"/api/applications/{target_id}").status_code == 204
    assert client.delete(f"/api/applications/{target_id}").status_code == 404
    assert len(client.get("/api/applications").json()) == 2


def test_imported_status_does_not_fake_response_time(client: TestClient) -> None:
    """导入时补写的"当前状态"事件不能被当成"回应"，否则响应时长全是 0。"""
    client.post(
        "/api/applications/import",
        json={"text": "公司,岗位,状态,投递时间\nA,B,已查看,2026-03-01"},
    )
    stats = client.get("/api/applications/stats").json()

    assert stats["response_time"]["samples"] == 0
    # 但"有回应"的判定不受影响：状态就是已查看
    assert stats["responded"] == 1
    assert stats["response_rate"] == 1.0


def test_manual_status_change_records_response_time(client: TestClient) -> None:
    created = client.post(
        "/api/applications",
        json={"company": "A", "title": "B", "applied_at": "2026-03-01T00:00:00Z"},
    ).json()
    client.post(f"/api/applications/{created['id']}/status", json={"to_status": "viewed"})

    stats = client.get("/api/applications/stats").json()
    assert stats["response_time"]["samples"] == 1
    assert stats["response_time"]["median_hours"] > 0


def test_follow_ups_appear_in_stats(client: TestClient) -> None:
    client.post(
        "/api/applications",
        json={"company": "老公司", "title": "后端", "applied_at": "2026-01-01T00:00:00Z"},
    )
    stats = client.get("/api/applications/stats").json()
    assert stats["follow_ups"], "投出去很久没动静的应该出现在待跟进里"
    assert stats["follow_ups"][0]["company"] == "老公司"
