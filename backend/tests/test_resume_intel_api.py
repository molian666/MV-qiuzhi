"""M8 的 API：匹配诊断、ATS、简历多版本、故事卡、话术、匹配分段统计。

其中最重要的是 `test_prompt_block_picks_story_by_question_type`：
它是「故事卡能在模拟中被自动引用」这条验收标准的直接证据。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlmodel import select

from app.analysis.answer_bank import char_budget
from app.db import session_scope
from app.models.apply import Application
from app.models.story import AnswerDraft, StoryCard
from app.story_service import prompt_block_for

RESUME_TEXT = (
    "联系方式：13800138000 / dev@example.com\n"
    "5 年后端开发经验。\n"
    "2021.03-2024.06 某某科技 · 后端开发\n"
    "- 负责订单系统重构，使用 Python 与 Redis，把核心接口 QPS 从 800 提升到 4000。\n"
    "- 主导支付链路高可用改造，故障恢复时间从 30 分钟降到 3 分钟。\n"
    "- 熟悉分布式系统设计，做过限流、降级与链路追踪等稳定性建设。\n"
    "- 带过 3 人小组完成季度目标，负责需求拆分与进度跟踪，配合测试完成上线验证。\n"
    "熟悉 MySQL、消息队列、Docker。"
)

JD_TEXT = "岗位要求：必须精通 Python；熟悉 Kafka 与 Redis；3-5 年经验。\n职责：负责高并发系统设计与性能优化。"


def _resume(client: TestClient) -> int:
    response = client.post("/api/resumes", json={"name": "主简历", "content_text": RESUME_TEXT})
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def _card(**overrides: Any) -> StoryCard:
    payload: dict[str, Any] = {
        "title": "订单系统重构",
        "situation": "订单接口高峰期超时",
        "task": "把核心链路吞吐提上去",
        "action": "重构订单核心，引入缓存与批处理",
        "result": "核心接口 QPS 从 800 提升到 4000",
        "metrics": "QPS 800 → 4000",
        "tags": ["achievement", "efficiency"],
        "topics": ["性能优化", "Redis"],
    }
    payload.update(overrides)
    return StoryCard(**payload)


# --------------------------------------------------------------------------- #
# 匹配诊断
# --------------------------------------------------------------------------- #
def test_match_endpoint(client: TestClient) -> None:
    resume_id = _resume(client)
    response = client.post(f"/api/resumes/{resume_id}/match", json={"jd_text": JD_TEXT})
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["jd_keyword_count"] > 0
    assert 0 <= body["score"] <= 100
    assert body["grade"] in {"A", "B", "C", "D"}
    assert body["years_required"] == 3
    assert body["years_actual"] == 3.3

    missing = {item["word"] for item in body["missing"]}
    assert "Kafka" in missing
    # 每个缺失关键词都带着分组标签，UI 才能说"你缺的是中间件类技能"
    assert all(item["group_label"] for item in body["missing"])
    assert body["suggestions"]


def test_match_requires_a_jd(client: TestClient) -> None:
    resume_id = _resume(client)
    response = client.post(f"/api/resumes/{resume_id}/match", json={"jd_text": "   "})
    assert response.status_code == 422
    assert "没有 JD 正文" in response.json()["detail"]


def test_match_can_take_jd_from_an_application(client: TestClient) -> None:
    resume_id = _resume(client)
    with session_scope() as db:
        application = Application(company="某某科技", title="后端工程师", jd_text=JD_TEXT, status="applied")
        db.add(application)
        db.commit()
        db.refresh(application)
        application_id = int(application.id or 0)

    body = client.post(f"/api/resumes/{resume_id}/match", json={"application_id": application_id}).json()
    assert body["jd_keyword_count"] > 0

    missing = client.post(f"/api/resumes/{resume_id}/match", json={"application_id": 99999})
    assert missing.status_code == 404


def test_ats_endpoint_without_jd(client: TestClient) -> None:
    resume_id = _resume(client)
    response = client.post(f"/api/resumes/{resume_id}/ats")
    assert response.status_code == 200
    issues = response.json()
    # 干净简历不该有 error 级问题
    assert [item for item in issues if item["severity"] == "error"] == []


def test_ats_endpoint_reports_real_problems(client: TestClient) -> None:
    response = client.post(
        "/api/resumes",
        json={"name": "粗糙版", "content_text": RESUME_TEXT + "\n熟悉 Ｐｙｔｈｏｎ 与 Ｋａｆｋａ"},
    )
    resume_id = int(response.json()["id"])
    kinds = {item["kind"] for item in client.post(f"/api/resumes/{resume_id}/ats").json()}
    assert "fullwidth" in kinds


# --------------------------------------------------------------------------- #
# 简历多版本
# --------------------------------------------------------------------------- #
def test_clone_and_list_versions(client: TestClient) -> None:
    resume_id = _resume(client)

    clone = client.post(f"/api/resumes/{resume_id}/clone", json={"variant_label": "量化版"})
    assert clone.status_code == 201
    body = clone.json()
    assert body["variant_label"] == "量化版"
    assert body["version"] == 2
    assert body["parent_resume_id"] == resume_id
    assert body["content_text"] == RESUME_TEXT  # 内容复制过来，之后各自改

    versions = client.get(f"/api/resumes/{resume_id}/versions").json()
    assert len(versions) == 2
    assert [item["version"] for item in versions] == [1, 2]


def test_clone_missing_resume(client: TestClient) -> None:
    assert client.post("/api/resumes/99999/clone", json={}).status_code == 404


# --------------------------------------------------------------------------- #
# 故事卡
# --------------------------------------------------------------------------- #
def test_generate_stories_from_resume(client: TestClient) -> None:
    resume_id = _resume(client)
    response = client.post("/api/stories/generate", json={"resume_id": resume_id, "count": 3})
    assert response.status_code == 201, response.text

    cards = response.json()
    assert cards
    for card in cards:
        assert card["title"]
        assert card["action"]
        assert card["result"]  # 缺结果的卡会被丢弃（引用半张卡比不引用更危险）
        assert card["tags"]


def test_generate_stories_never_duplicates_a_title(client: TestClient) -> None:
    resume_id = _resume(client)
    first = client.post("/api/stories/generate", json={"resume_id": resume_id, "count": 3}).json()
    second = client.post("/api/stories/generate", json={"resume_id": resume_id, "count": 3}).json()

    # 第二次可能从简历里抽到**还没成卡的其他经历**（这是对的），
    # 但同一段经历绝不能变成两张卡——所以这里验证的是"标题不重复"，不是"第二次为空"
    titles = [card["title"] for card in first + second]
    assert titles
    assert len(titles) == len(set(titles))

    stored = client.get("/api/stories").json()["items"]
    assert len(stored) == len(titles)


def test_generate_stories_without_resume(client: TestClient) -> None:
    assert client.post("/api/stories/generate", json={"resume_id": 99999}).status_code == 404


def test_story_crud(client: TestClient) -> None:
    created = client.post(
        "/api/stories",
        json={
            "title": "线上故障复盘",
            "action": "定位并修复缓存穿透",
            "result": "故障恢复时间从 30 分钟降到 3 分钟",
            "tags": ["quality"],
        },
    )
    assert created.status_code == 201
    card_id = created.json()["id"]

    updated = client.put(
        f"/api/stories/{card_id}",
        json={
            "title": "线上故障复盘",
            "action": "定位并修复缓存穿透",
            "result": "故障恢复时间从 30 分钟降到 3 分钟",
            "tags": ["quality"],
            "starred": True,
        },
    )
    assert updated.json()["starred"] is True

    assert client.delete(f"/api/stories/{card_id}").status_code == 204
    assert client.put(f"/api/stories/{card_id}", json={"title": "x"}).status_code == 404


def test_story_coverage_and_missing_tags(client: TestClient) -> None:
    client.post(
        "/api/stories",
        json={"title": "订单重构", "action": "重构", "result": "QPS 提升", "tags": ["achievement"]},
    )
    body = client.get("/api/stories").json()

    assert body["coverage"]["achievement"] == 1
    assert body["coverage"]["failure"] == 0
    # 一张卡都没有的题型要显式列出来——被问到就答不上来
    assert "failure" in body["missing_tags"]
    assert body["tag_labels"]["achievement"] == "成就与亮点"


# --------------------------------------------------------------------------- #
# 话术
# --------------------------------------------------------------------------- #
def test_self_intro_respects_char_budget(client: TestClient) -> None:
    resume_id = _resume(client)
    with session_scope() as db:
        db.add(_card())
        db.commit()

    response = client.post("/api/answers/self-intro", json={"resume_id": resume_id, "seconds": 30})
    assert response.status_code == 201, response.text

    body = response.json()
    budget = char_budget(30)
    assert len(body["content"]) <= int(budget * 1.2)
    assert "\n" not in body["content"]  # 自我介绍是要说出口的，得是一段话
    assert body["basis"]["estimated_seconds"] > 0


def test_self_intro_keeps_history(client: TestClient) -> None:
    resume_id = _resume(client)
    client.post("/api/answers/self-intro", json={"resume_id": resume_id, "seconds": 30})
    client.post("/api/answers/self-intro", json={"resume_id": resume_id, "seconds": 60})

    drafts = client.get("/api/answers", params={"kind": "self_intro_60"}).json()
    assert len(drafts) == 1
    assert drafts[0]["target_seconds"] == 60


def test_sensitive_answer_marks_blanks(client: TestClient) -> None:
    resume_id = _resume(client)
    response = client.post("/api/answers/sensitive", json={"kind": "leave", "resume_id": resume_id})
    assert response.status_code == 201

    body = response.json()
    assert body["kind"] == "sensitive_leave"
    # 需要本人补充的地方必须留成空白，而不是编一个理由
    assert "补：" in body["content"]
    assert body["basis"]["strategy"]


def test_sensitive_unknown_kind(client: TestClient) -> None:
    response = client.post("/api/answers/sensitive", json={"kind": "not_a_kind"})
    assert response.status_code == 404


def test_questions_require_jd(client: TestClient) -> None:
    assert client.post("/api/answers/questions", json={"jd_text": ""}).status_code == 422


def test_questions_vary_with_jd(client: TestClient) -> None:
    response = client.post("/api/answers/questions", json={"jd_text": JD_TEXT, "company": "某某科技", "count": 4})
    assert response.status_code == 201

    lines = response.json()["content"].splitlines()
    assert len(lines) == 4
    # 问题里带上了 JD 里的技术点，才显得"真的看过这个岗位"
    assert any("Python" in line or "Kafka" in line or "Redis" in line for line in lines)


def test_answer_meta_lists_questions_and_lengths(client: TestClient) -> None:
    body = client.get("/api/answers/meta").json()
    keys = [item["key"] for item in body["sensitive"]]
    assert {"leave", "gap", "plan", "salary"} <= set(keys)
    assert [item["seconds"] for item in body["self_intro_options"]] == [30, 60, 180]


# --------------------------------------------------------------------------- #
# 故事卡在模拟中被引用（M8 验收标准）
# --------------------------------------------------------------------------- #
def test_prompt_block_picks_story_by_question_type(client: TestClient) -> None:
    _resume(client)
    with session_scope() as db:
        db.add(_card())
        db.add(
            _card(
                title="支付链路高可用改造",
                action="主导支付链路高可用改造",
                result="故障恢复时间从 30 分钟降到 3 分钟",
                tags=["quality"],
                topics=["高可用"],
            )
        )
        db.commit()

    with session_scope() as db:
        fault_block = prompt_block_for(db, question="讲一个你线上出过故障、怎么处理的例子", limit=1)
    assert "支付链路高可用改造" in fault_block
    assert "不得编造" in fault_block

    with session_scope() as db:
        achievement_block = prompt_block_for(db, question="说说你最有成就感的一个项目", limit=1)
    assert "订单系统重构" in achievement_block


def test_prompt_block_is_empty_without_cards(client: TestClient) -> None:
    _resume(client)
    with session_scope() as db:
        assert prompt_block_for(db, question="说说你的项目") == ""


def test_story_used_count_increases_when_referenced(client: TestClient) -> None:
    from app.story_service import mark_used, referenced_ids

    _resume(client)
    with session_scope() as db:
        db.add(_card())
        db.commit()

    with session_scope() as db:
        ids = referenced_ids(db, question="说说你最有成就感的项目")
    assert ids

    with session_scope() as db:
        mark_used(db, ids)
    with session_scope() as db:
        cards = list(db.exec(select(StoryCard)).all())
    assert all(card.used_count == 1 for card in cards)


def test_session_answer_works_with_story_cards(client: TestClient) -> None:
    """把故事卡接进 prompt 之后，文字面试主链路必须照常能跑。"""
    resume_id = _resume(client)
    with session_scope() as db:
        db.add(_card())
        db.commit()

    created = client.post("/api/sessions", json={"mode": "text", "resume_id": resume_id, "max_questions": 3})
    assert created.status_code in (200, 201), created.text
    session_id = created.json()["id"]

    answer = client.post(
        f"/api/sessions/{session_id}/answer",
        json={"text": "我在某某科技重构了订单系统，把 QPS 从 800 提到 4000。"},
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["question"]


# --------------------------------------------------------------------------- #
# 匹配分段统计
# --------------------------------------------------------------------------- #
def test_match_bands_endpoint(client: TestClient) -> None:
    resume_id = _resume(client)
    with session_scope() as db:
        db.add(Application(company="A", title="后端", jd_text=JD_TEXT, status="interview1"))
        db.add(Application(company="B", title="后端", jd_text="要求：Photoshop、Illustrator", status="ghosted"))
        db.add(Application(company="C", title="后端", jd_text="", status="applied"))
        db.commit()

    body = client.get("/api/applications/match-bands", params={"resume_id": resume_id}).json()
    assert body["total_records"] == 3
    assert body["matched_records"] == 2
    assert sum(item["count"] for item in body["bands"]) == 2
    assert "1 条没有 JD 正文" in body["confidence_note"]
    assert body["recommendation"]


def test_match_bands_missing_resume(client: TestClient) -> None:
    assert client.get("/api/applications/match-bands", params={"resume_id": 99999}).status_code == 404


def test_match_bands_without_any_resume(client: TestClient) -> None:
    with session_scope() as db:
        db.add(Application(company="A", title="后端", jd_text=JD_TEXT, status="applied"))
        db.commit()

    body = client.get("/api/applications/match-bands").json()
    # 没有简历时不该崩，只是匹配分为 0
    assert body["matched_records"] == 1


def test_answer_drafts_are_persisted(client: TestClient) -> None:
    resume_id = _resume(client)
    client.post("/api/answers/self-intro", json={"resume_id": resume_id, "seconds": 60})

    with session_scope() as db:
        drafts = list(db.exec(select(AnswerDraft)).all())
    assert len(drafts) == 1
    assert drafts[0].kind == "self_intro_60"
