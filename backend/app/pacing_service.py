"""节奏学习的编排：录音 → 转写 → 切分 → 打标 → 画像 → 节奏样本。

纯逻辑都在 `analysis/pacing.py`（可单测、可离线复算），这里只负责 I/O 与落库。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import delete
from sqlmodel import Session, select

from .analysis.pacing import (
    QuestionUnit,
    apply_labels,
    build_params,
    build_policy,
    compute_features,
    parse_transcript,
    split_questions,
)
from .clock import utcnow
from .models.pacing import InterviewerProfile, PacingSample, Recording, TranscriptSegment
from .models.session import InterviewSession
from .providers.asr.base import (
    BatchTranscriber,
    SegmentDraft,
    TranscriptionError,
    TranscriptionResult,
)
from .providers.llm.base import (
    RECORD_MARKER_END,
    RECORD_MARKER_START,
    LLMClient,
    LLMError,
    LLMMessage,
    extract_json,
)
from .services import log_llm_error, log_llm_result

PACING_LABEL_PROMPT = """你是面试分析助手。请给下面这些面试问题分类，并抽出考点。

要求：
1. type 从这些里选一个：self_intro（自我介绍）、project_deep（项目深挖）、tech（技术细节）、
   behavior（行为面试）、algorithm（算法题）、design（系统设计）、scenario（场景题）、
   salary（薪资与期望）、reverse（反问环节）、general（其他）。
2. topic 用不超过 8 个字概括这一题在问什么。
3. key_points 给 2–4 条"好答案需要覆盖什么"。
4. 全部中文。

只输出 JSON，不要输出其他内容：
{"labels": {"<问题序号>": {"type": "tech", "topic": "并发写", "key_points": ["...", "..."]}}}
"""


class PacingError(RuntimeError):
    """节奏学习相关的业务错误。"""


def _marker_payload(payload: dict[str, Any]) -> str:
    return f"{RECORD_MARKER_START}\n{json.dumps(payload, ensure_ascii=False)}\n{RECORD_MARKER_END}"


# --------------------------------------------------------------------------- #
# 打标
# --------------------------------------------------------------------------- #
async def label_questions(db: Session, *, llm: LLMClient, units: list[QuestionUnit]) -> dict[int, dict[str, Any]]:
    """给问题单元打标（题型 / 主题 / 考点）。失败时返回空字典，不阻断画像生成。"""
    if not units:
        return {}

    payload = {"questions": [{"idx": unit.idx, "question": unit.question} for unit in units[:60]]}
    messages = [
        LLMMessage(role="system", content=PACING_LABEL_PROMPT),
        LLMMessage(role="user", content=_marker_payload(payload)),
    ]

    try:
        result = await llm.complete(task="pacing_label", messages=messages, temperature=0.2, json_object=True)
    except LLMError as exc:
        log_llm_error(db, task="pacing_label", provider=getattr(llm, "name", ""), error=str(exc))
        return {}

    log_llm_result(db, result)

    try:
        data = extract_json(result.text)
    except ValueError:
        return {}

    raw = data.get("labels")
    labels: dict[int, dict[str, Any]] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            try:
                idx = int(key)
            except (TypeError, ValueError):
                continue
            labels[idx] = {
                "type": str(value.get("type") or "general"),
                "topic": str(value.get("topic") or ""),
                "key_points": [str(item).strip() for item in (value.get("key_points") or []) if str(item).strip()][:4],
            }
    return labels


# --------------------------------------------------------------------------- #
# 转写
# --------------------------------------------------------------------------- #
async def transcribe_recording(
    db: Session,
    *,
    recording: Recording,
    transcriber: BatchTranscriber,
) -> tuple[list[SegmentDraft], TranscriptionResult]:
    recording.status = "transcribing"
    recording.error = ""
    db.add(recording)
    db.commit()

    try:
        result = await transcriber.transcribe(
            audio_path=recording.path or None,
            fallback_text=recording.raw_text,
        )
    except TranscriptionError as exc:
        recording.status = "failed"
        recording.error = str(exc)
        db.add(recording)
        db.commit()
        raise

    recording_id = recording.id or 0
    db.exec(delete(TranscriptSegment).where(TranscriptSegment.recording_id == recording_id))
    db.commit()

    for draft in result.segments:
        db.add(
            TranscriptSegment(
                recording_id=recording_id,
                idx=draft.idx,
                speaker=draft.speaker,
                text=draft.text,
                start_ms=draft.start_ms,
                end_ms=draft.end_ms,
                confidence=draft.confidence,
            )
        )

    if result.duration_ms is not None:
        recording.duration_ms = result.duration_ms
    db.add(recording)
    db.commit()

    return result.segments, result


# --------------------------------------------------------------------------- #
# 画像
# --------------------------------------------------------------------------- #
async def build_profile(
    db: Session,
    *,
    recording_ids: list[int],
    transcriber: BatchTranscriber,
    llm: LLMClient,
    name: str = "",
    notes: str = "",
) -> InterviewerProfile:
    """从 1..N 段录音里学出一个面试官画像。

    多段录音会**合并统计**（同一个面试官的不同场次），而不是各出一份画像——
    样本越多，节奏特征越稳（尤其是追问深度和打断率这类比例型指标）。
    """
    if not recording_ids:
        raise PacingError("至少选择一段录音")

    all_segments: list[SegmentDraft] = []
    all_units: list[QuestionUnit] = []
    unit_source: dict[int, int] = {}
    offset = 0

    for recording_id in recording_ids:
        recording = db.get(Recording, recording_id)
        if recording is None:
            raise LookupError(f"录音不存在：{recording_id}")

        try:
            segments, _result = await transcribe_recording(db, recording=recording, transcriber=transcriber)
        except TranscriptionError as exc:
            # 转写失败是"用户可修复"的问题（稿子没填/格式不对）→ 归到业务错误，
            # 让 API 返回 400 + 可读提示，而不是 500
            raise PacingError(str(exc)) from exc

        units = split_questions(segments)
        labels = await label_questions(db, llm=llm, units=units)
        apply_labels(units, labels)

        # 多段录音的 unit.idx 必须错开，否则落样本/打标会串
        for unit in units:
            unit.idx = offset + unit.idx
            unit_source[unit.idx] = recording_id
        offset += len(units)

        all_segments.extend(segments)
        all_units.extend(units)

        recording.status = "done"
        recording.error = ""
        db.add(recording)

    db.commit()

    if not all_units:
        for recording_id in recording_ids:
            recording = db.get(Recording, recording_id)
            if recording is not None:
                recording.status = "failed"
                recording.error = "没有从文稿里识别出任何问题（检查格式：[面试官] / [我]）"
                db.add(recording)
        db.commit()
        raise PacingError("没有从录音里识别出任何面试问题，无法生成画像")

    features = compute_features(all_units, all_segments)
    params = build_params(features, all_units)
    params["source_features"]["tapped_recordings"] = len(recording_ids)

    profile = InterviewerProfile(
        name=name.strip() or f"画像 {utcnow().strftime('%m-%d %H:%M')}",
        source="extracted",
        recording_ids=list(recording_ids),
        style_tags=[str(tag) for tag in (params.get("style_tags") or [])],
        params=params,
        notes=notes,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    profile_id = profile.id or 0

    # 节奏样本：让"画像怎么来的"可追溯（也能用来解释某一题为何被算作追问）
    for unit in all_units:
        db.add(
            PacingSample(
                profile_id=profile_id,
                recording_id=unit_source.get(unit.idx),
                question_type=unit.question_type,
                topic=unit.topic,
                question=unit.question,
                ask_duration_ms=unit.ask_duration_ms,
                wait_ms=unit.wait_ms,
                answer_ms=unit.answer_ms,
                followup_count=unit.depth,
                interviewer_wpm=features.interviewer_wpm,
                candidate_ratio=features.speaking_ratio,
                interrupted=unit.interrupted,
            )
        )
    db.commit()

    return profile


def profile_samples(db: Session, profile_id: int) -> list[PacingSample]:
    return list(
        db.exec(
            select(PacingSample)
            .where(PacingSample.profile_id == profile_id)
            .order_by(PacingSample.id)  # type: ignore[arg-type]
        ).all()
    )


def load_policy_for_session(
    db: Session,
    session_row: InterviewSession,
    *,
    default_silence_ms: int = 900,
):
    """取这场会话该用的面试官策略（没选画像就返回 None，走默认行为）。"""
    if not session_row.interviewer_profile_id:
        return None
    profile = db.get(InterviewerProfile, session_row.interviewer_profile_id)
    if profile is None:
        return None
    return build_policy(
        profile.params,
        default_silence_ms=default_silence_ms,
        profile_id=profile.id,
        profile_name=profile.name,
    )


def parse_manual_transcript(text: str) -> list[SegmentDraft]:
    """把粘贴的文稿解析成段落（供 API 预览用）。"""
    drafts = parse_transcript(text)
    return [
        SegmentDraft(
            idx=draft.idx,
            speaker=draft.speaker,
            text=draft.text,
            start_ms=draft.start_ms,
            end_ms=draft.end_ms,
        )
        for draft in drafts
    ]
