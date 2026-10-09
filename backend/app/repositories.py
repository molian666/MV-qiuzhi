"""数据访问小工具：会话回合的查询、统计与快照。

抽出来的原因：文字链路（api/sessions.py）与语音链路（realtime/session_runtime.py）
必须对"回合顺序"和"提问数/作答数"有一致理解，否则题量上限与收尾时机会不一致。
"""

from __future__ import annotations

from sqlmodel import Session, select

from .models.session import SessionTurn


def ordered_turns(db: Session, session_id: int) -> list[SessionTurn]:
    """按 idx 升序取一场会话的全部回合。"""
    statement = (
        select(SessionTurn)
        .where(SessionTurn.session_id == session_id)
        .order_by(SessionTurn.idx)  # type: ignore[arg-type]
    )
    return list(db.exec(statement).all())


def count_turns(turns: list[SessionTurn]) -> tuple[int, int]:
    """(面试官提问数, 候选人作答数)。

    两个**不计入**的回合，各自都有理由：

    - 收尾轮（`closing`）不算提问：那是"你有什么想问我的"，不是一道题；
    - 候选人在收尾阶段的**提问**（`question`）不算作答：他那一轮没有回答问题。
      以前 `answers` 只按 `speaker == "candidate"` 数，收尾阶段加入提问回合后，
      `answer_count` 会凭空多 1——那是用户直接看到的数字，也算错了"他答了几题"。
    """
    questions = sum(1 for t in turns if t.speaker == "interviewer" and t.kind != "closing")
    answers = sum(1 for t in turns if t.speaker == "candidate" and t.kind != "question")
    return questions, answers


def turn_snapshot(turn: SessionTurn) -> SessionTurn:
    """拷一份未绑定实例。

    用途：在 DB 事务之外（调 LLM 时）构造消息历史，避免长事务占用 SQLite 连接。
    """
    return SessionTurn(
        id=turn.id,
        session_id=turn.session_id,
        idx=turn.idx,
        speaker=turn.speaker,
        text=turn.text,
        kind=turn.kind,
        topic=turn.topic,
        is_followup=turn.is_followup,
        start_ms=turn.start_ms,
        end_ms=turn.end_ms,
        created_at=turn.created_at,
    )


def turn_snapshots(turns: list[SessionTurn]) -> list[SessionTurn]:
    return [turn_snapshot(t) for t in turns]
