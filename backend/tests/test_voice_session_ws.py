"""WebSocket 语音会话端到端（Mock 语音提供方）。

Mock 模式下事件顺序是确定的，所以这里严格按顺序读取，不用轮询等待——
一旦服务端行为变化，测试会立刻失败，而不是挂在那里等超时。
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _create_realtime_session(client: TestClient, max_questions: int = 2) -> int:
    response = client.post("/api/sessions", json={"mode": "realtime", "max_questions": max_questions})
    assert response.status_code == 201
    return int(response.json()["id"])


def test_state_then_text_answer_flow(client: TestClient) -> None:
    session_id = _create_realtime_session(client, max_questions=2)

    with client.websocket_connect(f"/ws/sessions/{session_id}") as ws:
        state = ws.receive_json()
        assert state["type"] == "state"
        assert state["audio_mode"] == "client"  # M1 默认浏览器播报
        assert state["text_fallback"] is True  # Mock 语音：转写用文字替代
        assert state["input_sample_rate"] == 16000
        assert state["session_id"] == session_id

        ws.send_json({"type": "text", "text": "我是 5 年后端工程师，负责大促订单链路。"})

        candidate = ws.receive_json()
        assert candidate["type"] == "candidate_turn"
        assert candidate["source"] == "text"
        assert candidate["text"].startswith("我是 5 年后端")
        assert candidate["end_ms"] >= candidate["start_ms"]

        question = ws.receive_json()
        assert question["type"] == "interviewer_text"
        assert question["text"].strip()

        meta = ws.receive_json()
        assert meta["type"] == "turn_meta"
        assert meta["answer_count"] == 1
        assert meta["max_questions"] == 2


def test_reaches_closing_phase_then_finished(client: TestClient) -> None:
    """答满题数后进入**收尾阶段**（不是直接结束），候选人提问后才真正 finished。

    **这是有意的行为变更**：以前发完收尾题就 `mark_finished`，候选人根本没有机会提问，
    导致评分 rubric 里的「主动性 / 反问质量」维度结构性拿不到数据。
    """
    session_id = _create_realtime_session(client, max_questions=1)

    with client.websocket_connect(f"/ws/sessions/{session_id}") as ws:
        assert ws.receive_json()["type"] == "state"
        ws.send_json({"type": "text", "text": "一轮就答完"})

        kinds: list[str] = []
        for _ in range(4):
            event = ws.receive_json()
            kinds.append(event["type"])
            if event["type"] == "awaiting_question":
                break

        assert kinds == ["candidate_turn", "interviewer_text", "turn_meta", "awaiting_question"]

        # 会话仍然是 live —— 他还能提问
        ws.send_json({"type": "text", "text": "请问团队现在主要做什么方向？"})
        seen: list[str] = []
        for _ in range(4):
            event = ws.receive_json()
            seen.append(event["type"])
            if event["type"] == "finished":
                break
        assert "finished" in seen, f"提问之后应当真正结束，实际 {seen}"


def test_reverse_question_is_recorded_as_a_question(client: TestClient) -> None:
    """候选人这一轮要落成 `kind=question`，否则评分看不出他问了什么。"""
    session_id = _create_realtime_session(client, max_questions=1)

    with client.websocket_connect(f"/ws/sessions/{session_id}") as ws:
        ws.receive_json()
        ws.send_json({"type": "text", "text": "答完"})
        for _ in range(4):
            if ws.receive_json()["type"] == "awaiting_question":
                break

        ws.send_json({"type": "text", "text": "想问一下技术栈"})
        asked = ws.receive_json()
        assert asked["type"] == "candidate_turn"
        assert asked["kind"] == "question"

    detail = client.get(f"/api/sessions/{session_id}").json()
    candidate_turns = [turn for turn in detail["turns"] if turn["speaker"] == "candidate"]
    assert any(turn["kind"] == "question" for turn in candidate_turns)


def test_unknown_message_returns_error(client: TestClient) -> None:
    session_id = _create_realtime_session(client)
    with client.websocket_connect(f"/ws/sessions/{session_id}") as ws:
        ws.receive_json()
        ws.send_json({"type": "nonsense"})
        error = ws.receive_json()
        assert error["type"] == "error"
        assert "nonsense" in error["message"]


def test_ping_pong(client: TestClient) -> None:
    session_id = _create_realtime_session(client)
    with client.websocket_connect(f"/ws/sessions/{session_id}") as ws:
        ws.receive_json()
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"


def test_finished_session_is_rejected(client: TestClient) -> None:
    """已结束的会话再连上来，必须明确报错，不能静默建连接。

    结束要走完收尾阶段（答题 → 收尾 → 提问），所以这里要多推一轮。
    """
    session_id = _create_realtime_session(client, max_questions=1)
    with client.websocket_connect(f"/ws/sessions/{session_id}") as ws:
        ws.receive_json()
        ws.send_json({"type": "text", "text": "答完"})
        for _ in range(4):
            if ws.receive_json()["type"] == "awaiting_question":
                break
        ws.send_json({"type": "text", "text": "我问一个问题"})
        for _ in range(4):
            if ws.receive_json()["type"] == "finished":
                break

    with client.websocket_connect(f"/ws/sessions/{session_id}") as ws:
        error = ws.receive_json()
        assert error["type"] == "error"
        assert "finished" in error["message"]


def test_unknown_session_is_rejected(client: TestClient) -> None:
    with client.websocket_connect("/ws/sessions/99999") as ws:
        error = ws.receive_json()
        assert error["type"] == "error"
        assert "99999" in error["message"]
