"""M14 的 API：人脉跟进、学习清单、猎聘适配。

两个验收点：**到期联系人不再漏跟进**（`/api/follow-ups` 按逾期排序并给话术）、
**学习路径与薄弱点联动**（清单的原料直接来自 M5 的掌握度与 M8 的 JD 缺口）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.db import session_scope
from app.models.apply import Application
from app.models.core import Resume
from app.models.scoring import SkillMastery, SkillTopic

NOW = datetime.now(timezone.utc)


def _contact(client: TestClient, **overrides: object) -> dict:
    payload: dict[str, object] = {"name": "老王", "relation": "referral", "company": "某某科技"}
    payload.update(overrides)
    return client.post("/api/contacts", json=payload).json()


# --------------------------------------------------------------------------- #
# 选项
# --------------------------------------------------------------------------- #
def test_network_options(client: TestClient) -> None:
    body = client.get("/api/network/options").json()
    assert {item["key"] for item in body["relations"]} >= {"referral", "alumni", "hr", "headhunter"}
    assert body["interaction_kinds"]
    assert {item["key"] for item in body["learning_statuses"]} >= {"todo", "doing", "done"}
    assert all(item["default_follow_up_days"] > 0 for item in body["relations"])


# --------------------------------------------------------------------------- #
# 联系人
# --------------------------------------------------------------------------- #
def test_contact_crud(client: TestClient) -> None:
    created = _contact(client)
    contact_id = created["id"]
    assert created["name"] == "老王"

    updated = client.put(f"/api/contacts/{contact_id}", json={**created, "company": "新公司"}).json()
    assert updated["company"] == "新公司"

    assert len(client.get("/api/contacts").json()) == 1
    assert client.delete(f"/api/contacts/{contact_id}").status_code == 204
    assert client.get("/api/contacts").json() == []
    assert client.get(f"/api/contacts/{contact_id}").status_code == 404


def test_contact_requires_a_name(client: TestClient) -> None:
    response = client.post("/api/contacts", json={"relation": "referral"})
    assert response.status_code == 422
    assert "名字" in response.json()["detail"]


def test_interaction_builds_a_timeline(client: TestClient) -> None:
    contact = _contact(client)
    contact_id = contact["id"]

    first = client.post(
        f"/api/contacts/{contact_id}/interactions",
        json={"summary": "第一次聊，他在某某科技", "promised": ""},
    )
    assert first.status_code == 201

    second = client.post(
        f"/api/contacts/{contact_id}/interactions",
        json={"summary": "帮我内推了后端岗", "kind": "referral", "promised": "帮你问一下 HC"},
    )
    assert second.status_code == 201

    detail = client.get(f"/api/contacts/{contact_id}").json()
    timeline = detail["timeline"]
    assert len(timeline) == 2
    # 最近的在前
    assert timeline[0]["summary"] == "帮我内推了后端岗"
    assert timeline[0]["kind_label"] == "内推"

    # last_contact_at 被同步（列表页不用 join）
    assert detail["contact"]["last_contact_at"] is not None


def test_delete_interaction_recomputes_last_contact(client: TestClient) -> None:
    contact = _contact(client)
    contact_id = contact["id"]

    older = client.post(
        f"/api/contacts/{contact_id}/interactions",
        json={"at": (NOW - timedelta(days=10)).isoformat(), "summary": "旧的一次"},
    ).json()
    client.post(f"/api/contacts/{contact_id}/interactions", json={"summary": "新的一次"})

    assert client.delete(f"/api/interactions/{older['id']}").status_code == 204
    detail = client.get(f"/api/contacts/{contact_id}").json()
    assert len(detail["timeline"]) == 1
    assert detail["contact"]["last_contact_at"] is not None


def test_interaction_404_for_unknown_contact(client: TestClient) -> None:
    assert client.post("/api/contacts/99999/interactions", json={"summary": "x"}).status_code == 404


# --------------------------------------------------------------------------- #
# 跟进（验收 1）
# --------------------------------------------------------------------------- #
def test_overdue_contact_shows_up_first(client: TestClient) -> None:
    fresh = _contact(client, name="刚聊过")
    stale = _contact(client, name="好久没聊")

    client.post(
        f"/api/contacts/{fresh['id']}/interactions",
        json={"at": (NOW - timedelta(days=1)).isoformat(), "summary": "昨天聊过"},
    )
    client.post(
        f"/api/contacts/{stale['id']}/interactions",
        json={"at": (NOW - timedelta(days=30)).isoformat(), "summary": "一个月前"},
    )

    rows = client.get("/api/follow-ups", params={"horizon_days": 3}).json()
    assert rows
    assert rows[0]["name"] == "好久没聊"
    assert rows[0]["urgency"] == "overdue"
    assert rows[0]["days_overdue"] >= 25
    # 每条都要给可直接发出的开场，而不是"记得跟进"
    assert len(rows[0]["suggested"]) > 20
    assert rows[0]["relation_label"] == "内推人"


def test_follow_up_uses_promise_and_suggests_pinging_it(client: TestClient) -> None:
    contact = _contact(client)
    client.post(
        f"/api/contacts/{contact['id']}/interactions",
        json={
            "at": (NOW - timedelta(days=20)).isoformat(),
            "summary": "帮我内推了",
            "promised": "帮你问一下 HC",
        },
    )

    row = client.get("/api/follow-ups").json()[0]
    assert row["promise"] == "帮你问一下 HC"
    assert "HC" in row["suggested"]


def test_follow_ups_empty_when_nothing_due(client: TestClient) -> None:
    contact = _contact(client, name="刚加进来")
    client.post(
        f"/api/contacts/{contact['id']}/interactions",
        json={"at": NOW.isoformat(), "summary": "刚聊完"},
    )
    assert client.get("/api/follow-ups", params={"horizon_days": 2}).json() == []


def test_birthday_greeting_surfaces_in_detail(client: TestClient) -> None:
    today = datetime.now()
    contact = _contact(client, key_dates=[{"label": "生日", "month": today.month, "day": today.day}])
    detail = client.get(f"/api/contacts/{contact['id']}").json()
    assert detail["greeting"] and "生日" in detail["greeting"]


def test_contact_health_shape(client: TestClient) -> None:
    contact = _contact(client)
    detail = client.get(f"/api/contacts/{contact['id']}").json()
    assert detail["health"]["level"] in {"warm", "due", "stale", "cold"}


# --------------------------------------------------------------------------- #
# 学习清单（验收 2：与薄弱点联动）
# --------------------------------------------------------------------------- #
def _seed_mastery(topic: str, *, mastery: float, attempts: int, correct: int) -> None:
    with session_scope() as db:
        row = SkillTopic(name=topic)
        db.add(row)
        db.commit()
        db.refresh(row)
        db.add(SkillMastery(topic_id=int(row.id or 0), mastery=mastery, attempts=attempts, correct_hits=correct))
        db.commit()


def test_learning_generate_from_mastery(client: TestClient) -> None:
    _seed_mastery("高并发", mastery=20, attempts=6, correct=1)

    items = client.post("/api/learning/generate", json={"limit": 8}).json()
    topics = [item["topic"] for item in items]
    assert "高并发" in topics

    item = next(row for row in items if row["topic"] == "高并发")
    assert item["source"] == "weakness"
    assert item["priority"] > 0
    assert "掌握度" in item["reason"]
    assert len(item["action"]) > 15
    # 三块明细要能解释排序
    assert set(item["breakdown"]) == {"weakness", "demand", "breadth"}


def test_learning_generate_from_jd_gaps(client: TestClient) -> None:
    """M8 的诊断结果要能进学习清单——这是"投递反馈驱动学习"的落点。"""
    with session_scope() as db:
        db.add(
            Resume(
                name="主简历",
                content_text="五年后端，熟悉 Python、Redis、MySQL，负责订单系统重构，QPS 从 800 提到 4000。",
            )
        )
        db.add(
            Application(
                company="某某科技",
                title="后端工程师",
                status="applied",
                jd_text="岗位要求：必须精通 Python；熟悉 Kafka 与 Kubernetes；有高并发经验。",
            )
        )
        db.commit()

    items = client.post("/api/learning/generate", json={"limit": 10}).json()
    topics = [item["topic"] for item in items]
    assert "Kafka" in topics or "Kubernetes" in topics

    kafka = next((row for row in items if row["topic"] == "Kafka"), None)
    if kafka is not None:
        assert "JD" in kafka["reason"] or "份 JD" in kafka["reason"]


def test_learning_generate_is_idempotent_by_topic(client: TestClient) -> None:
    _seed_mastery("分布式", mastery=10, attempts=5, correct=0)
    client.post("/api/learning/generate", json={"limit": 8})
    client.post("/api/learning/generate", json={"limit": 8})

    items = client.get("/api/learning").json()
    topics = [item["topic"] for item in items]
    assert len(topics) == len(set(topics))


def test_replace_keeps_in_progress_items(client: TestClient) -> None:
    _seed_mastery("缓存", mastery=30, attempts=5, correct=1)
    items = client.post("/api/learning/generate", json={"limit": 8}).json()
    first = items[0]

    client.put(f"/api/learning/{first['id']}", json={**first, "status": "doing"})
    client.post("/api/learning/generate", json={"limit": 8, "replace": True})

    remaining = client.get("/api/learning").json()
    assert any(item["id"] == first["id"] and item["status"] == "doing" for item in remaining)


def test_learning_status_and_summary(client: TestClient) -> None:
    created = client.post(
        "/api/learning",
        json={"title": "手写一个限流器", "topic": "限流", "hours": 6, "status": "todo"},
    ).json()

    done = client.put(f"/api/learning/{created['id']}", json={**created, "status": "done"}).json()
    assert done["status"] == "done"
    assert done["done_at"] is not None

    summary = client.get("/api/learning/summary").json()
    assert summary["total"] == 1
    assert summary["done"] == 1
    assert summary["progress"] == 1.0
    assert summary["hours_done"] == 6


def test_learning_insight_flags_done_but_not_mastered(client: TestClient) -> None:
    """「学完了」和「掌握了」的差距要看得见：学完但掌握度还低就该提醒再练。"""
    _seed_mastery("消息队列", mastery=30, attempts=4, correct=1)
    items = client.post("/api/learning/generate", json={"limit": 8}).json()
    target = next(row for row in items if row["topic"] == "消息队列")
    client.put(f"/api/learning/{target['id']}", json={**target, "status": "done"})

    rows = client.get("/api/learning/insight").json()
    row = next(item for item in rows if item["topic"] == "消息队列")
    assert row["status"] == "done"
    assert row["has_data"] is True
    assert row["mastery"] == 30.0
    assert "掌握度仍低于" in row["note"]


def test_insight_distinguishes_no_data_from_low_mastery(client: TestClient) -> None:
    """没数据 ≠ 掌握度 0：把"还没练过"说成"掌握度 0"会让人以为白学了。"""
    created = client.post(
        "/api/learning",
        json={"title": "手写一个限流器", "topic": "从没练过的主题", "hours": 4, "status": "done"},
    ).json()
    assert created["topic"]

    rows = client.get("/api/learning/insight").json()
    row = next(item for item in rows if item["topic"] == "从没练过的主题")
    assert row["has_data"] is False
    assert "还没有掌握度数据" in row["note"]
    assert "掌握度仍低于" not in row["note"]


def test_learning_crud(client: TestClient) -> None:
    created = client.post("/api/learning", json={"title": "手动加一项", "topic": "自定义"}).json()
    assert created["id"]

    updated = client.put(f"/api/learning/{created['id']}", json={**created, "title": "改个名"}).json()
    assert updated["title"] == "改个名"

    assert client.delete(f"/api/learning/{created['id']}").status_code == 204
    assert client.put(f"/api/learning/{created['id']}", json={"title": "x"}).status_code == 404


def test_fixed_paths_are_not_shadowed_by_id_routes(client: TestClient) -> None:
    """/api/follow-ups 与 /api/learning/summary 这类固定路径不能被 /{id} 抢走。"""
    assert client.get("/api/follow-ups").status_code == 200
    assert client.get("/api/learning/summary").status_code == 200
    assert client.get("/api/learning/insight").status_code == 200


# --------------------------------------------------------------------------- #
# 猎聘适配
# --------------------------------------------------------------------------- #
def test_liepin_is_registered_as_a_platform(client: TestClient) -> None:
    from app.apply.adapters import PLATFORM_LABELS, PLATFORMS

    assert "liepin" in PLATFORMS
    assert PLATFORM_LABELS["liepin"] == "猎聘"


def test_liepin_adapter_reuses_the_browser_base() -> None:
    from app.apply.adapters import BrowserAdapter, LiepinAdapter
    from app.apply.adapters.base import SearchQuery

    adapter = LiepinAdapter(user_data_dir="tmp")
    assert isinstance(adapter, BrowserAdapter)
    assert "liepin.com" in adapter._search_url(SearchQuery(keywords=["后端"]))


def test_switching_platform_to_liepin_reports_uncalibrated(client: TestClient) -> None:
    client.put(
        "/api/apply/settings",
        json={"enabled": True, "risk_acknowledged": True, "platform": "liepin"},
    )
    status = client.get("/api/apply/status").json()
    assert status["platform"] == "liepin"
    # 没装 patchright 或没校准选择器时都必须明确报出来，而不是假装就绪
    assert status["adapter_ready"] is False
    assert status["adapter_note"]
