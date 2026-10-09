"""自动拉黑的端到端（能力开关 + 预览/落库）。

两件事要证明：

1. **没开能力就不许动黑名单**（403 且说明怎么开）——用户的名单不能被偷偷改。
2. **默认只预览**：先给出"会被拉黑谁、凭什么"，确认后才写库。
"""

from __future__ import annotations

from typing import Any

from sqlmodel import select

from app.apply.capabilities import CAPABILITIES
from app.db import session_scope
from app.models.apply import Application, Blacklist

SETTINGS_URL = "/api/apply/settings"
AUTOFILL_URL = "/api/apply/blacklist/autofill"


def _seed_applications() -> None:
    """造三条投递记录：一条明确拒绝、一条岗位关闭、一条只是"等通知"。"""
    with session_scope() as db:
        db.add(
            Application(
                company="拒绝科技",
                title="视觉工程师",
                status="rejected",
                note="HR 回复：抱歉，您的情况和我们的岗位不太合适。",
            )
        )
        db.add(
            Application(
                company="关闭科技",
                title="算法工程师",
                status="ghosted",
                note="页面上写着：该职位已关闭，感谢关注。",
            )
        )
        db.add(
            Application(
                company="有戏科技",
                title="视觉工程师",
                status="viewed",
                note="HR：简历已收到，我们下周再联系您。",
            )
        )
        db.commit()


def _enable_autofill(client: Any) -> None:
    enabled = {item.key: False for item in CAPABILITIES}
    enabled["autofill_blacklist"] = True
    response = client.put(SETTINGS_URL, json={"enabled": True, "capabilities": {"enabled": enabled}})
    assert response.status_code == 200, response.text


def test_autofill_is_refused_while_capability_is_off(client: Any) -> None:
    """能力默认关闭：即使有明确拒绝的数据，也不许改黑名单。"""
    _seed_applications()

    response = client.post(AUTOFILL_URL)
    assert response.status_code == 403
    assert "自动拉黑" in response.json()["detail"]

    with session_scope() as db:
        assert list(db.exec(select(Blacklist)).all()) == []


def test_dry_run_reports_candidates_without_writing(client: Any) -> None:
    _seed_applications()
    _enable_autofill(client)

    response = client.post(AUTOFILL_URL, params={"dry_run": True})
    assert response.status_code == 200
    payload = response.json()

    assert payload["dry_run"] is True
    assert payload["scanned"] == 3
    assert payload["added"] == 0

    companies = {item["company"] for item in payload["candidates"]}
    assert companies == {"拒绝科技", "关闭科技"}, "只该认出明确拒绝与岗位关闭"
    assert "有戏科技" not in companies, "「下周再联系」不是拒绝，不能拉黑"

    # 预览不能落库
    with session_scope() as db:
        assert list(db.exec(select(Blacklist)).all()) == []


def test_candidates_carry_evidence(client: Any) -> None:
    """依据必须给出来，用户在界面上才能核对"它凭什么拉黑这家"。"""
    _seed_applications()
    _enable_autofill(client)

    payload = client.post(AUTOFILL_URL, params={"dry_run": True}).json()
    for item in payload["candidates"]:
        assert item["evidence"], f"{item['company']} 缺少判定依据"
        assert item["reason"] == "auto_rejected"


def test_confirming_writes_the_blacklist(client: Any) -> None:
    _seed_applications()
    _enable_autofill(client)

    response = client.post(AUTOFILL_URL, params={"dry_run": False})
    assert response.status_code == 200
    assert response.json()["added"] == 2

    with session_scope() as db:
        rows = list(db.exec(select(Blacklist)).all())

    assert {row.company for row in rows} == {"拒绝科技", "关闭科技"}
    # 原因写成 auto_rejected 而不是 manual，用户以后能区分"我自己拉黑的"和"程序认出来的"
    assert all(row.reason == "auto_rejected" for row in rows)


def test_second_run_does_not_duplicate(client: Any) -> None:
    """已经拉黑过的公司不该再写一条，也不该再出现在候选里。"""
    _seed_applications()
    _enable_autofill(client)

    client.post(AUTOFILL_URL, params={"dry_run": False})
    second = client.post(AUTOFILL_URL, params={"dry_run": False}).json()

    assert second["added"] == 0
    assert second["candidates"] == []

    with session_scope() as db:
        rows = list(db.exec(select(Blacklist)).all())
    assert len(rows) == 2, "黑名单里不该出现重复的公司"


def test_autofill_on_empty_database_is_harmless(client: Any) -> None:
    _enable_autofill(client)

    payload = client.post(AUTOFILL_URL, params={"dry_run": False}).json()
    assert payload["scanned"] == 0
    assert payload["added"] == 0
    assert payload["candidates"] == []
