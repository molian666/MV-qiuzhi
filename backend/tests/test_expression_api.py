"""表达体检 API：生成、读取、音频定位、以及与 M3 报告的合并。"""

from __future__ import annotations

import array
import math
import wave
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlmodel import select

from app.config import get_settings
from app.db import session_scope
from app.models.expression import ExpressionSnapshot
from app.models.session import InterviewSession, SessionTurn

ANSWER = (
    "嗯，这块主要是那个缓存穿透的问题，就是说我们当时的做法是加布隆过滤器，"
    "然后配合空值缓存，把数据库的压力降下来了，QPS 从 800 提升到 4000。"
)


def _write_wav(path: Path, segments: list[tuple[float, float]], *, rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = array.array("h")
    for seconds, amplitude in segments:
        for index in range(int(rate * seconds)):
            samples.append(int(amplitude * 32767 * math.sin(2 * math.pi * 220 * index / rate)))

    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(samples.tobytes())


def _make_session(client: TestClient, *, answer: str = ANSWER, duration_ms: int = 24_000) -> int:
    """建一场会话并塞入一段候选人发言（带时间戳，这样定位才有意义）。"""
    created = client.post("/api/sessions", json={"mode": "realtime", "max_questions": 3})
    assert created.status_code in (200, 201), created.text
    session_id = int(created.json()["id"])

    with session_scope() as db:
        db.add(
            SessionTurn(
                session_id=session_id,
                idx=1,
                speaker="interviewer",
                text="说说你处理过的一个线上问题",
                kind="new_topic",
                start_ms=0,
                end_ms=1000,
            )
        )
        db.add(
            SessionTurn(
                session_id=session_id,
                idx=2,
                speaker="candidate",
                text=answer,
                kind="answer",
                start_ms=1000,
                end_ms=1000 + duration_ms,
            )
        )
        db.commit()

    return session_id


# --------------------------------------------------------------------------- #
# 基本行为
# --------------------------------------------------------------------------- #
def test_expression_404_for_unknown_session(client: TestClient) -> None:
    assert client.get("/api/sessions/99999/expression").status_code == 404
    assert client.post("/api/sessions/99999/expression").status_code == 404


def test_expression_is_404_until_generated(client: TestClient) -> None:
    session_id = _make_session(client)
    response = client.get(f"/api/sessions/{session_id}/expression")
    assert response.status_code == 404
    assert "还没有表达体检" in response.json()["detail"]


def test_generate_expression_without_audio(client: TestClient) -> None:
    session_id = _make_session(client)
    response = client.post(f"/api/sessions/{session_id}/expression?with_audio=false")
    assert response.status_code == 201, response.text

    body = response.json()
    assert body["chars"] > 0
    assert body["overall_cpm"] > 0
    assert body["has_audio"] is False
    assert body["audio_url"] is None
    assert body["pauses"] == []
    # 没有音频也要给出文字层面的分析：填充词与语速
    assert body["filler_total"] > 0
    assert {item["word"] for item in body["fillers"]} >= {"嗯", "那个", "就是说"}
    assert body["suggestions"]
    assert body["summary"]


def test_expression_422_without_candidate_turns(client: TestClient) -> None:
    created = client.post("/api/sessions", json={"mode": "realtime", "max_questions": 3})
    session_id = int(created.json()["id"])
    response = client.post(f"/api/sessions/{session_id}/expression?with_audio=false")
    assert response.status_code == 422
    assert "没有你的发言内容" in response.json()["detail"]


def test_regenerate_overwrites_previous(client: TestClient) -> None:
    session_id = _make_session(client)
    client.post(f"/api/sessions/{session_id}/expression?with_audio=false")
    client.post(f"/api/sessions/{session_id}/expression?with_audio=false")

    with session_scope() as db:
        rows = list(db.exec(select(ExpressionSnapshot)).all())
    assert len(rows) == 1


# --------------------------------------------------------------------------- #
# 有音频时：停顿与跳播
# --------------------------------------------------------------------------- #
def test_expression_reads_full_wav_for_pauses(client: TestClient) -> None:
    session_id = _make_session(client, duration_ms=6000)
    # 前 1 秒在说、中间 2 秒停顿、后 3 秒在说
    _write_wav(get_settings().audio_dir / str(session_id) / "full.wav", [(1.0, 0.5), (2.0, 0.0), (3.0, 0.5)])

    body = client.post(f"/api/sessions/{session_id}/expression").json()

    assert body["has_audio"] is True
    assert body["audio_url"] == f"/api/sessions/{session_id}/audio"
    assert body["pauses"], "应当检测到中间那 2 秒停顿"
    pause = body["pauses"][0]
    assert pause["duration_ms"] == 2000
    # 静音从 WAV 的第 1 秒开始（这条发言从会话的 1000ms 处开始，两者是不同坐标系）
    assert pause["start_ms"] == 1000
    assert pause["kind"] == "long"


def test_issues_carry_replay_position(client: TestClient) -> None:
    session_id = _make_session(client, duration_ms=9000)
    # 中间 3 秒长停顿 → 必须报出一条带时间点的 long_pause
    _write_wav(get_settings().audio_dir / str(session_id) / "full.wav", [(2.0, 0.5), (3.0, 0.0), (5.0, 0.5)])

    body = client.post(f"/api/sessions/{session_id}/expression").json()
    pauses = [item for item in body["issues"] if item["kind"] == "long_pause"]

    assert pauses
    assert pauses[0]["at_ms"] == 2000
    assert pauses[0]["severity"] in {"warning", "info"}
    assert "3.0 秒" in pauses[0]["detail"] or "3 秒" in pauses[0]["detail"]


def test_windows_cover_the_whole_answer(client: TestClient) -> None:
    session_id = _make_session(client, duration_ms=45_000)
    body = client.post(f"/api/sessions/{session_id}/expression?with_audio=false").json()

    assert body["windows"]
    # 45 秒的回答 → 4 个 10 秒窗口 + 1 个 5 秒窗口
    assert len(body["windows"]) == 5
    assert body["windows"][0]["start_ms"] == 1000
    assert body["windows"][-1]["end_ms"] == 46_000


# --------------------------------------------------------------------------- #
# 与 M3 报告合并
# --------------------------------------------------------------------------- #
def test_expression_score_is_merged_into_score_report(client: TestClient) -> None:
    session_id = _make_session(client)
    # 先给这场会话生成一份评分报告（Mock 模型）
    report = client.post(f"/api/sessions/{session_id}/report")
    assert report.status_code in (200, 201), report.text

    dimensions = {item["key"]: item for item in report.json()["dimensions"]}
    # PLAN §9.1 第 3 条预留的正是这个：加维度只是加 key，不改表结构
    assert "表达" in dimensions
    assert dimensions["表达"]["label"] == "表达"
    # 与 rubric 其它维度同为 5 分制
    assert 1 <= dimensions["表达"]["score"] <= 5


def test_expression_snapshot_is_stored_when_report_generates(client: TestClient) -> None:
    session_id = _make_session(client)
    client.post(f"/api/sessions/{session_id}/report")

    with session_scope() as db:
        row = db.exec(select(ExpressionSnapshot).where(ExpressionSnapshot.session_id == session_id)).first()
    assert row is not None
    assert row.report.get("chars", 0) > 0


def test_report_generation_survives_audio_errors(client: TestClient) -> None:
    """录音损坏时报告仍要能出——表达体检是加分项，不该拖垮整份报告。"""
    session_id = _make_session(client)
    broken = get_settings().audio_dir / str(session_id) / "full.wav"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_bytes(b"not a wav at all")

    report = client.post(f"/api/sessions/{session_id}/report")
    assert report.status_code in (200, 201), report.text
    assert "表达" in {item["key"] for item in report.json()["dimensions"]}


def test_session_detail_still_works(client: TestClient) -> None:
    session_id = _make_session(client)
    client.post(f"/api/sessions/{session_id}/expression?with_audio=false")
    detail = client.get(f"/api/sessions/{session_id}")
    assert detail.status_code == 200
