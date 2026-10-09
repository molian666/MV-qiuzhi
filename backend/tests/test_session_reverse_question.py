"""收尾阶段（反问环节）：候选人提问 → 面试官作答 → 结束。

**这个功能的由来**：以前收尾题一发出就 `mark_finished`，候选人**根本没有机会提问**，
于是评分 rubric 里的「主动性 / 反问质量」维度结构性拿不到数据——不是打分不好，是没数据。

所以要守住三件事：
1. 收尾阶段会话必须保持 **live**（否则前端会把他锁在输入框外）；
2. 候选人的提问要落成 **`kind="question"`**（评分靠它取证，混进 `answer` 就白做了）；
3. **不能卡住**：他不想提问时必须能结束。
"""

from __future__ import annotations

from typing import Any

from app.db import session_scope
from app.models.session import InterviewSession

ANSWERS = ["第一轮回答", "第二轮回答"]


def _live_session(client: Any, max_questions: int = 2) -> int:
    created = client.post("/api/sessions", json={"mode": "text", "max_questions": max_questions})
    assert created.status_code == 201
    return int(created.json()["id"])


def _answer_all(client: Any, session_id: int, max_questions: int = 2) -> dict[str, Any]:
    """答满题数，返回进入收尾阶段的那次响应。"""
    body: dict[str, Any] = {}
    for index in range(max_questions):
        response = client.post(f"/api/sessions/{session_id}/answer", json={"text": f"第 {index + 1} 轮回答"})
        assert response.status_code == 200, response.text
        body = response.json()
    return body


# --------------------------------------------------------------------------- #
# 收尾阶段仍要 live
# --------------------------------------------------------------------------- #
def test_closing_phase_keeps_the_session_live(client: Any) -> None:
    session_id = _live_session(client)

    closing = _answer_all(client, session_id)

    assert closing["finished"] is False, "收尾不是结束"
    assert closing["session"]["status"] == "live"
    assert closing["question"]["kind"] == "closing"
    # 界面要能把这件事说清楚，否则用户不知道该干什么
    assert "提问" in closing["message"]


def test_closing_phase_invites_questions_and_does_not_announce_the_end(client: Any) -> None:
    """收尾题不该说「面试结束」——后面还有他提问的环节。"""
    session_id = _live_session(client)

    closing = _answer_all(client, session_id)
    text = closing["question"]["text"]

    assert "提问" in text or "想问" in text or "了解" in text
    assert "结束" not in text


# --------------------------------------------------------------------------- #
# 候选人的提问要单独落库
# --------------------------------------------------------------------------- #
def test_candidate_question_is_stored_with_kind_question(client: Any) -> None:
    """**评分要靠这个 kind 取证**：混进 `answer` 的话主动性维度还是没数据。"""
    session_id = _live_session(client)
    _answer_all(client, session_id)

    final = client.post(f"/api/sessions/{session_id}/answer", json={"text": "请问团队现在主要做什么方向？"})

    assert final.status_code == 200
    body = final.json()
    assert body["finished"] is True
    assert body["session"]["status"] == "finished"

    turns = body["session"]["turns"]
    assert turns[-2]["kind"] == "question"
    assert turns[-2]["speaker"] == "candidate"
    # 面试官最后那一轮是回答他的问题
    assert turns[-1]["speaker"] == "interviewer"


def test_answer_count_does_not_include_the_candidate_question(client: Any) -> None:
    """**用户直接看到的数字**：他答了 3 题就该显示 3，不能因为多问了句问题变成 4。"""
    session_id = _live_session(client, max_questions=3)

    for index in range(3):
        client.post(f"/api/sessions/{session_id}/answer", json={"text": f"第 {index + 1} 轮回答"})

    final = client.post(f"/api/sessions/{session_id}/answer", json={"text": "我有个问题"}).json()
    assert final["session"]["answer_count"] == 3


def test_question_count_does_not_include_the_closing_turn(client: Any) -> None:
    session_id = _live_session(client, max_questions=2)
    _answer_all(client, session_id)

    final = client.post(f"/api/sessions/{session_id}/answer", json={"text": "我有个问题"}).json()

    # 两轮回答＝两道题；收尾那一问与最后的回答都不算题
    assert final["session"]["question_count"] == 2


# --------------------------------------------------------------------------- #
# 不能卡住
# --------------------------------------------------------------------------- #
def test_finish_endpoint_works_from_the_closing_phase(client: Any) -> None:
    """不想提问的人必须能直接结束——**不能把他锁在收尾阶段**。"""
    session_id = _live_session(client)
    _answer_all(client, session_id)

    response = client.post(f"/api/sessions/{session_id}/finish")

    assert response.status_code == 200
    assert response.json()["status"] == "finished"


def test_after_finish_further_answers_are_rejected(client: Any) -> None:
    session_id = _live_session(client)
    _answer_all(client, session_id)
    client.post(f"/api/sessions/{session_id}/finish")

    blocked = client.post(f"/api/sessions/{session_id}/answer", json={"text": "再补一句"})
    assert blocked.status_code == 409


# --------------------------------------------------------------------------- #
# 与旧会话的兼容
# --------------------------------------------------------------------------- #
def test_session_without_a_phase_key_behaves_as_interview(client: Any) -> None:
    """旧会话的 plan 里没有 `phase`——必须当成正常面试，而不是收尾。"""
    session_id = _live_session(client)

    with session_scope() as db:
        row = db.get(InterviewSession, session_id)
        assert row is not None
        plan = dict(row.plan or {})
        plan.pop("phase", None)
        row.plan = plan
        db.add(row)
        db.commit()

    response = client.post(f"/api/sessions/{session_id}/answer", json={"text": "正常回答"})

    assert response.status_code == 200
    assert response.json()["finished"] is False
    # 落成 answer 而不是 question。
    # 注意 `question` 字段是**新问出来的那道题**（面试官），所以候选人的回合在倒数第二个。
    turns = response.json()["session"]["turns"]
    assert turns[-2]["kind"] == "answer"
    assert turns[-1]["kind"] != "question"
