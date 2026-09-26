"""节奏学习的编排：录音 → 转写 → 切分 → 打标 → 画像 → 节奏样本。

纯逻辑都在 `analysis/pacing.py`（可单测、可离线复算），这里只负责 I/O 与落库。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import delete
from sqlmodel import Session, select

from .analysis.glossary import correct_transcript
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
   behavior（行为面试）、algorithm（算法题）、scenario（场景题）、
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
                # 与实时链路同一套术语纠错：录音转写的误识别和实时的一样多，
                # 慢跑一遍没有实时性压力，没理由不做
                text=correct_transcript(draft.text)[0],
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
    mode: str = "new",
    profile_id: int | None = None,
) -> InterviewerProfile:
    """从 1..N 段录音里学出一个面试官画像。

    多段录音会**合并统计**（同一个面试官的不同场次），而不是各出一份画像——
    样本越多，节奏特征越稳（尤其是追问深度和打断率这类比例型指标）。

    三种模式：

    - `new`：新建一个画像；
    - `append`：**追加到已有画像**——把新录音并进它的录音列表后重算。
      旧录音的转写会**复用已存的段落**，不重跑 ASR（真 ASR 按量计费，
      追加一次就把历史全转一遍是不可接受的）；
    - `replace`：**重新生成已有画像**——换掉录音列表重算，并**重跑转写**
      （"重新生成"就该重来）。画像 id 保留，引用它的会话不会失效。
    """
    if mode not in ("new", "append", "replace"):
        raise PacingError(f"不支持的 mode：{mode}")

    target: InterviewerProfile | None = None
    if mode in ("append", "replace"):
        if profile_id is None:
            raise PacingError(f"mode={mode} 需要指定 profile_id")
        target = db.get(InterviewerProfile, profile_id)
        if target is None:
            raise LookupError(f"画像不存在：{profile_id}")

    if mode == "append" and target is not None:
        # 合并录音：旧的在前、新的补在后面，去重且保持顺序
        merged = list(target.recording_ids or [])
        for recording_id in recording_ids:
            if recording_id not in merged:
                merged.append(recording_id)
        recording_ids = merged

    # append 时旧录音已经转写过，复用它们的段落；replace 则全部重转
    reusable = set(target.recording_ids or []) if mode == "append" and target else set()

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

        segments: list[SegmentDraft] | None = None
        if recording_id in reusable:
            segments = _stored_segments(db, recording_id)

        if segments is None:
            try:
                segments, _result = await transcribe_recording(
                    db, recording=recording, transcriber=transcriber
                )
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

    # append / replace 都**更新同一条记录**（保留 id）：
    # 会话是拿 profile_id 引用画像的，换一条新记录会让已有会话指向旧画像。
    if target is not None:
        profile = target
        profile.recording_ids = list(recording_ids)
        profile.style_tags = [str(tag) for tag in (params.get("style_tags") or [])]
        profile.params = params
        # 名字：调用方给了就用新的，没给就保留原名（追加时不该悄悄改名）
        if name.strip():
            profile.name = name.strip()
        if notes:
            profile.notes = notes
        # 样本要重算，先清掉旧的
        db.exec(delete(PacingSample).where(PacingSample.profile_id == profile.id))
    else:
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
    """取这场会话该用的面试官策略（没选画像就返回 None，走默认行为）。

    这里要把**节奏样本一起读出来**：真实原题存在样本表里，
    只传 params 的话模型拿不到"这个人上次到底问了什么"。
    """
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
        samples=profile_samples(db, profile.id or 0),
    )


def _stored_segments(db: Session, recording_id: int) -> list[SegmentDraft] | None:
    """把某段录音**已存好的**转写段落读回来；没有就返回 None。

    用于 `append` 模式：追加新录音时，旧录音没必要重跑 ASR——
    真 ASR 按量计费，每追加一次就把历史全转一遍是不可接受的。
    这也是 `replace`（"重新生成"）与 `append` 的区别所在：
    前者故意重转，后者复用。
    """
    rows = list(
        db.exec(
            select(TranscriptSegment)
            .where(TranscriptSegment.recording_id == recording_id)
            .order_by(TranscriptSegment.idx)  # type: ignore[attr-defined]
        ).all()
    )
    if not rows:
        return None
    return [
        SegmentDraft(
            idx=row.idx,
            speaker=row.speaker or "unknown",
            text=row.text,
            start_ms=row.start_ms,
            end_ms=row.end_ms,
        )
        for row in rows
    ]


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
