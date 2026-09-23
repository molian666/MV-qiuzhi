"""表达体检服务：读音频算音量包络 → 纯函数分析 → 落库 + 并入评分报告。

**关于读音频**：Python 3.13 起标准库移除了 `audioop`，所以这里用 `array` 手算 RMS。
另外做了**子采样**：停顿检测关心的是"这一段整体够不够安静"，
不需要每个采样都算一遍——10 分钟的录音逐采样跑 Python 循环要好几秒，得不偿失。
"""

from __future__ import annotations

import array
import wave
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence

from sqlmodel import Session, select

from .analysis.speech_features import ExpressionReport, TurnSpan, analyze
from .clock import utcnow
from .config import get_settings
from .models.expression import ExpressionSnapshot
from .models.session import SessionTurn

#: 每帧最多取这么多采样来算 RMS（见模块说明）
SUBSAMPLE = 400


def rms_envelope(path: Path, *, frame_ms: int = 50) -> list[float]:
    """把 WAV 读成逐帧 RMS 包络（0–1）。文件不存在或格式不对时返回空列表。"""
    if not path.exists():
        return []

    try:
        with wave.open(str(path), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            raw = handle.readframes(handle.getnframes())
    except (wave.Error, OSError):
        return []

    if width != 2 or not raw or rate <= 0:
        # 只处理 16-bit PCM（audio_store 写出来的就是这种）
        return []

    samples = array.array("h")
    samples.frombytes(raw)
    if channels > 1:
        # M1 存的是单声道；这里只是防御，多声道取平均
        mixed = array.array("h")
        for start in range(0, len(samples) - channels + 1, channels):
            mixed.append(int(sum(samples[start : start + channels]) / channels))
        samples = mixed

    frame_samples = max(1, int(rate * frame_ms / 1000))
    peak = 32768.0
    envelope: list[float] = []

    for start in range(0, len(samples), frame_samples):
        chunk = samples[start : start + frame_samples]
        if not chunk:
            continue
        if len(chunk) > SUBSAMPLE:
            step = len(chunk) // SUBSAMPLE
            chunk = chunk[::step]

        total = 0.0
        for value in chunk:
            total += float(value) * float(value)
        envelope.append(round((total / len(chunk)) ** 0.5 / peak, 5))

    return envelope


def turns_for_session(db: Session, session_id: int, *, speaker: str = "candidate") -> list[TurnSpan]:
    """把回合快照转成体检用的输入（只取有文字、有时长的那些）。"""
    rows = db.exec(
        select(SessionTurn).where(SessionTurn.session_id == session_id).order_by(SessionTurn.idx)  # type: ignore[arg-type]
    ).all()

    spans: list[TurnSpan] = []
    for turn in rows:
        if speaker and turn.speaker != speaker:
            continue
        text = (turn.text or "").strip()
        if not text:
            continue
        spans.append(
            TurnSpan(
                index=turn.idx,
                speaker=turn.speaker,
                text=text,
                start_ms=int(turn.start_ms or 0),
                end_ms=int(turn.end_ms or 0),
                topic=turn.topic or "",
            )
        )
    return spans


def envelope_for_session(session_id: int) -> list[float]:
    path = get_settings().audio_dir / str(session_id) / "full.wav"
    return rms_envelope(path)


def analyze_session(
    db: Session,
    session_id: int,
    *,
    frame_ms: int = 50,
    with_audio: bool = True,
) -> ExpressionReport:
    spans = turns_for_session(db, session_id)
    volume = envelope_for_session(session_id) if with_audio else []
    return analyze(spans, volume=volume or None, frame_ms=frame_ms)


def _filler_payload(hit: Any) -> dict[str, Any]:
    # weighted 是 property，asdict 拿不到，得手动补上（PauseSpan.seconds 同理）
    return {**asdict(hit), "weighted": hit.weighted}


def _pause_payload(pause: Any) -> dict[str, Any]:
    return {**asdict(pause), "seconds": pause.seconds}


def _payload(report: ExpressionReport) -> dict[str, Any]:
    return {
        "speaking_ms": report.speaking_ms,
        "chars": report.chars,
        "overall_cpm": report.overall_cpm,
        "filler_total": report.filler_total,
        "filler_weighted": report.filler_weighted,
        "filler_per_kchar": report.filler_per_kchar,
        "reliable": report.reliable,
        "fillers": [_filler_payload(item) for item in report.fillers],
        "pauses": [_pause_payload(item) for item in report.pauses],
        "longest_pause": _pause_payload(report.longest_pause) if report.longest_pause else None,
        "windows": [asdict(item) for item in report.windows],
        "pace_cv": report.pace_cv,
        "volume_cv": report.volume_cv,
        "tension": report.tension,
        "issues": [asdict(item) for item in report.issues],
        "suggestions": list(report.suggestions),
    }


def generate(
    db: Session,
    *,
    session_id: int,
    recording_id: int | None = None,
    frame_ms: int = 50,
    with_audio: bool = True,
) -> ExpressionSnapshot:
    """跑一次表达体检并存档（同一场重复跑会覆盖上一条）。"""
    report = analyze_session(db, session_id, frame_ms=frame_ms, with_audio=with_audio)
    payload = _payload(report)

    existing = load(db, session_id=session_id, recording_id=recording_id)
    if existing is None:
        existing = ExpressionSnapshot(session_id=session_id, recording_id=recording_id)
    else:
        existing.generated_at = utcnow()

    existing.overall = report.score
    existing.summary = report.summary
    existing.report = payload
    existing.has_audio = report.has_audio
    existing.frame_ms = frame_ms

    db.add(existing)
    db.commit()
    db.refresh(existing)
    return existing


def load(
    db: Session,
    *,
    session_id: int | None = None,
    recording_id: int | None = None,
) -> ExpressionSnapshot | None:
    statement = select(ExpressionSnapshot)
    if session_id is not None:
        statement = statement.where(ExpressionSnapshot.session_id == session_id)
    if recording_id is not None:
        statement = statement.where(ExpressionSnapshot.recording_id == recording_id)
    return db.exec(statement.order_by(ExpressionSnapshot.id.desc())).first()  # type: ignore[arg-type]


def attach_to_report(db: Session, *, session_id: int, report_row: Any) -> ExpressionSnapshot | None:
    """把表达分并进 M3 的评分报告（`dimension_scores` 加一个「表达」维度）。

    这是 PLAN §9.1 第 3 条预留的设计：加维度只是加 key，不改表结构。
    分析失败时静默跳过——表达体检是加分项，不该让整份报告生成失败。
    """
    try:
        snapshot = generate(db, session_id=session_id)
    except Exception:  # noqa: BLE001 - 见上面的说明
        return None

    if snapshot.overall <= 0:
        return None

    scores = dict(report_row.dimension_scores or {})
    # 结构必须与 rubric 维度一致（reports.py 里对非 dict 的值会直接跳过），
    # 且 score 要落回 5 分制——表达分本身是 0–10 更细，这里映射过来，雷达图的轴才一致。
    scores["表达"] = {
        "label": "表达",
        "score": max(1, min(5, int(round(snapshot.overall / 2)))),
        "comment": f"{snapshot.summary}（表达体检原始分 {snapshot.overall}/10）",
        "evidence_turn_ids": [],
    }
    report_row.dimension_scores = scores
    db.add(report_row)
    db.commit()
    return snapshot
