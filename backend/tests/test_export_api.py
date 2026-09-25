"""导出 API 测试（M15）：token 校验、三种格式、内容完整性（落到真实查库链路）。"""

from __future__ import annotations

import io
import json
import zipfile

from fastapi.testclient import TestClient

#: 测试令牌与 conftest 里的 EXPORT_TOKEN 对应；导出接口都要带上
AUTH = {"X-Export-Token": "good"}


def _seed(client: TestClient) -> None:
    """造一点各模块的数据，让导出有东西可导。"""
    target = client.post("/api/targets", json={"company": "甲公司", "title": "后端工程师", "jd_text": "懂 Python"}).json()
    client.post("/api/resumes", json={"name": "主简历", "content_text": "五年后端经验"})
    app = client.post("/api/applications", json={"company": "甲公司", "title": "后端", "channel": "boss", "status": "applied"}).json()
    client.put(f"/api/applications/{app['id']}/status", json={"to_status": "interview1"})
    session = client.post("/api/sessions", json={"target_id": target["id"]}).json()
    client.post(f"/api/sessions/{session['id']}/answer", json={"text": "我的项目经历是……"})
    client.post(f"/api/sessions/{session['id']}/finish")
    client.post(f"/api/sessions/{session['id']}/report")
    client.post("/api/offers", json={"company": "乙公司", "title": "后端", "monthly_salary": 25000})


def test_export_requires_token(client: TestClient) -> None:
    _seed(client)
    for endpoint in ("/api/export/preview", "/api/export/markdown", "/api/export/csv", "/api/export/json"):
        response = client.get(endpoint)
        assert response.status_code == 401, endpoint


def test_export_disabled_without_token_configured(client: TestClient, monkeypatch) -> None:
    """EXPORT_TOKEN 未配置时：401 + 指路报错，而不是裸奔。"""
    from app.config import get_settings

    monkeypatch.setenv("EXPORT_TOKEN", "")
    get_settings.cache_clear()
    response = client.get("/api/export/preview")
    get_settings.cache_clear()  # 还原，让同一测试里的后续请求仍用配置好的令牌
    assert response.status_code == 401
    assert "EXPORT_TOKEN" in response.json()["detail"]


def test_export_rejects_wrong_token(client: TestClient) -> None:
    response = client.get("/api/export/preview", headers={"X-Export-Token": "wrong"})
    assert response.status_code == 401
    assert "令牌不正确" in response.json()["detail"]


def test_export_query_token_accepted(client: TestClient) -> None:
    response = client.get("/api/export/preview?token=good")
    assert response.status_code == 200


def test_preview_lists_all_sections(client: TestClient) -> None:
    _seed(client)
    payload = client.get("/api/export/preview", headers=AUTH).json()
    keys = {section["key"] for section in payload["sections"]}
    expected = {"targets", "resumes", "applications", "application_events", "sessions", "turns",
                "reports", "improvements", "expression", "mastery", "training", "stories",
                "answers", "offers"}
    assert expected <= keys
    rows = {section["key"]: section["rows"] for section in payload["sections"]}
    assert rows["applications"] >= 1
    assert rows["turns"] >= 2  # 面试官提问 + 我的回答
    # Notion/飞书必须如实标注「未接入」，不能假装有
    integrations = {item["key"]: item["status"] for item in payload["integrations"]}
    assert integrations == {"notion": "not_configured", "feishu": "not_configured"}


def test_markdown_export_contains_seeded_data(client: TestClient) -> None:
    _seed(client)
    response = client.get("/api/export/markdown", headers=AUTH)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    body = response.text
    for expected in ("甲公司", "乙公司", "## 投递记录"):
        assert expected in body, f"导出的 Markdown 里没有：{expected}"


def test_csv_zip_has_one_file_per_section(client: TestClient) -> None:
    _seed(client)
    response = client.get("/api/export/csv", headers=AUTH)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = set(archive.namelist())
    assert {"applications.csv", "turns.csv", "offers.csv", "sessions.csv"} <= names
    # 带 BOM：Excel 直接打开中文不乱码
    offers_csv = archive.read("offers.csv").decode("utf-8-sig")
    assert "乙公司" in offers_csv
    # turns.csv 里逐轮问答的内容在
    turns_csv = archive.read("turns.csv").decode("utf-8-sig")
    assert "我的项目经历是……" in turns_csv


def test_json_export_round_trips(client: TestClient) -> None:
    _seed(client)
    payload = json.loads(client.get("/api/export/json", headers=AUTH).text)
    assert payload["generated_at"]
    sections = {section["key"]: section for section in payload["sections"]}
    offer_rows = sections["offers"]["rows"]
    assert offer_rows and offer_rows[0]["monthly_salary"] == 25000.0  # 数字类型保留


def test_integrations_return_501_with_readable_error(client: TestClient) -> None:
    for key in ("notion", "feishu"):
        response = client.post(f"/api/export/integrations/{key}", headers=AUTH)
        assert response.status_code == 501, key
        assert "尚未实现" in response.json()["detail"]
