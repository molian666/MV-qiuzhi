"""导出服务（M15）：查库 → 组中间格式 → 渲染文件。

analysis/export.py 负责字段取舍与渲染；这里只做三件 analysis 不能做的事：
查库、跨表 join（把外键换成可读的名字）、把多个 CSV 打成 ZIP。
"""

from __future__ import annotations

import io
import zipfile
from typing import Any

from sqlmodel import Session, select

from .analysis.export import (
    ExportBundle,
    build_answers,
    build_application_events,
    build_applications,
    build_apply_rules,
    build_apply_tasks,
    build_benchmarks,
    build_blacklist,
    build_career_plan,
    build_contacts,
    build_expression,
    build_import_batches,
    build_improvements,
    build_interactions,
    build_job_postings,
    build_learning,
    build_mastery,
    build_mastery_events,
    build_offers,
    build_okr,
    build_onboarding,
    build_platform_accounts,
    build_profiles,
    build_questions,
    build_recordings,
    build_reports,
    build_resumes,
    build_reviews,
    build_sessions,
    build_stories,
    build_targets,
    build_topics,
    build_training,
    build_transcripts,
    build_turns,
    render_csv_sections,
    render_json,
    render_markdown,
)
from .analysis.okr import kr_pct
from .clock import utcnow
from .models.apply import (
    Application,
    ApplicationEvent,
    ApplyRule,
    ApplyTask,
    Blacklist,
    ImportBatch,
    JobPosting,
    PlatformAccount,
)
from .models.career import CareerPlanItem, OkrKeyResult, OkrObjective, OnboardingTask, SalaryBenchmark
from .models.core import JobTarget, QuestionAttempt, QuestionBank, Resume
from .models.expression import ExpressionSnapshot
from .models.network import Contact, ContactInteraction, LearningItem
from .models.offer import Offer
from .models.pacing import InterviewerProfile, Recording, TranscriptSegment
from .models.scoring import (
    ImprovementItem,
    MasteryEvent,
    ReviewTask,
    ScoreReport,
    SkillMastery,
    SkillTopic,
)
from .models.session import InterviewSession, SessionTurn
from .models.story import AnswerDraft, StoryCard


def build_bundle(db: Session) -> ExportBundle:
    """全量导出：一个板块一张表，顺序固定（与 analysis/export.EXPORT_SECTION_KEYS 一致）。"""
    targets = list(db.exec(select(JobTarget).order_by(JobTarget.id)).all())
    resumes = list(db.exec(select(Resume).order_by(Resume.id)).all())
    applications = list(db.exec(select(Application).order_by(Application.id)).all())
    app_company = {app.id: app.company for app in applications}
    events = [
        {
            "at": event.at,
            "company": app_company.get(event.application_id, f"投递#{event.application_id}"),
            "from_status": event.from_status,
            "to_status": event.to_status,
            "raw_ref": event.raw_ref,
        }
        for event in db.exec(select(ApplicationEvent).order_by(ApplicationEvent.id)).all()
    ]
    sessions = list(db.exec(select(InterviewSession).order_by(InterviewSession.id)).all())
    turns = list(db.exec(select(SessionTurn).order_by(SessionTurn.session_id, SessionTurn.idx)).all())
    reports = list(db.exec(select(ScoreReport).order_by(ScoreReport.id)).all())
    improvements = list(db.exec(select(ImprovementItem).order_by(ImprovementItem.id)).all())
    expressions = list(db.exec(select(ExpressionSnapshot).order_by(ExpressionSnapshot.id)).all())

    topics = {topic.id: topic for topic in db.exec(select(SkillTopic)).all()}
    mastery_rows = [
        {
            "topic": topics[row.topic_id].name if row.topic_id in topics else f"主题#{row.topic_id}",
            "category": topics[row.topic_id].category if row.topic_id in topics else "",
            "mastery": row.mastery,
            "attempts": row.attempts,
            "correct_hits": row.correct_hits,
            "last_seen_at": row.last_seen_at,
            "updated_at": row.updated_at,
        }
        for row in db.exec(select(SkillMastery).order_by(SkillMastery.id)).all()
    ]

    question_rows = list(db.exec(select(QuestionBank).order_by(QuestionBank.id)).all())
    questions = {row.id: row.text for row in question_rows}
    training_rows = [
        {
            "asked_at": attempt.asked_at,
            "question": questions.get(attempt.question_id or 0, "（题目已删除）"),
            "answer_text": attempt.answer_text,
            "scores": attempt.scores,
            "session_id": attempt.session_id,
        }
        for attempt in db.exec(select(QuestionAttempt).order_by(QuestionAttempt.id)).all()
    ]

    stories = list(db.exec(select(StoryCard).order_by(StoryCard.id)).all())
    answers = list(db.exec(select(AnswerDraft).order_by(AnswerDraft.id)).all())
    offers = list(db.exec(select(Offer).order_by(Offer.id)).all())
    contacts = list(db.exec(select(Contact).order_by(Contact.id)).all())
    contact_names = {contact.id: contact.name for contact in contacts}
    interactions = [
        {
            "at": row.at,
            "contact": contact_names.get(row.contact_id, f"联系人#{row.contact_id}"),
            "direction": row.direction,
            "kind": row.kind,
            "summary": row.summary,
            "promised": row.promised,
            "next_follow_up_at": row.next_follow_up_at,
        }
        for row in db.exec(select(ContactInteraction).order_by(ContactInteraction.id)).all()
    ]
    learning = list(db.exec(select(LearningItem).order_by(LearningItem.id)).all())

    onboarding = list(db.exec(select(OnboardingTask).order_by(OnboardingTask.sort_order, OnboardingTask.id)).all())
    objectives = list(db.exec(select(OkrObjective).order_by(OkrObjective.sort_order, OkrObjective.id)).all())
    krs = list(db.exec(select(OkrKeyResult).order_by(OkrKeyResult.sort_order, OkrKeyResult.id)).all())
    okr_rows = [
        {
            "period": objective.period,
            "objective": objective.title,
            "objective_status": objective.status,
            "key_result": kr.title,
            "unit": kr.unit,
            "target_value": kr.target_value,
            "current_value": kr.current_value,
            "progress": kr_pct(kr.current_value, kr.target_value),
            "confidence": kr.confidence,
            "updated_at": kr.updated_at,
        }
        for objective in objectives
        for kr in krs
        if kr.objective_id == objective.id
    ]
    plan_items = list(db.exec(select(CareerPlanItem).order_by(CareerPlanItem.id)).all())
    benchmarks = list(db.exec(select(SalaryBenchmark).order_by(SalaryBenchmark.id)).all())

    # ---- M15 补充：投递侧的"配置与队列" ----
    postings = list(db.exec(select(JobPosting).order_by(JobPosting.id)).all())
    posting_labels = {row.id: (row.company, row.title) for row in postings}
    apply_tasks = [
        {
            "company": posting_labels.get(task.job_posting_id, ("", ""))[0],
            "title": posting_labels.get(task.job_posting_id, ("", ""))[1],
            "status": task.status,
            "say_hi_text": task.say_hi_text,
            "attempts": task.attempts,
            "created_at": task.created_at,
            "approved_at": task.approved_at,
            "sent_at": task.sent_at,
            "error": task.error,
        }
        for task in db.exec(select(ApplyTask).order_by(ApplyTask.id)).all()
    ]
    apply_rules = list(db.exec(select(ApplyRule).order_by(ApplyRule.priority, ApplyRule.id)).all())
    blacklist = list(db.exec(select(Blacklist).order_by(Blacklist.id)).all())
    platform_accounts = list(db.exec(select(PlatformAccount).order_by(PlatformAccount.id)).all())
    import_batches = list(db.exec(select(ImportBatch).order_by(ImportBatch.id)).all())

    # ---- M15 补充：学习侧的历史（掌握度变化 + 复习任务 + 题库）----
    topic_names = {topic_id: topic.name for topic_id, topic in topics.items()}
    topic_rows = [
        {
            "name": topic.name,
            "category": topic.category,
            "parent": topic_names.get(topic.parent_id or 0, ""),
            "created_at": topic.created_at,
        }
        for topic in topics.values()
    ]
    mastery_events = [
        {
            "at": event.at,
            "topic": topic_names.get(event.topic_id, f"主题#{event.topic_id}"),
            "before": event.mastery_before,
            "after": event.mastery_after,
            "delta": round(float(event.mastery_after or 0) - float(event.mastery_before or 0), 2),
            "source": getattr(event, "source", ""),
            "note": getattr(event, "note", ""),
        }
        for event in db.exec(select(MasteryEvent).order_by(MasteryEvent.id)).all()
    ]
    reviews = [
        {
            "topic": topic_names.get(task.topic_id or 0, ""),
            "question": questions.get(task.question_id or 0, ""),
            "due_at": task.due_at,
            "done_at": task.done_at,
            "interval_days": task.interval_days,
            "repetitions": task.repetitions,
            "ease": task.ease,
        }
        for task in db.exec(select(ReviewTask).order_by(ReviewTask.id)).all()
    ]

    # ---- M15 补充：节奏学习的素材（录音是你上传的真实内容，最不该丢）----
    recordings = list(db.exec(select(Recording).order_by(Recording.id)).all())
    recording_titles = {row.id: row.title for row in recordings}
    transcripts = [
        {
            "recording": recording_titles.get(segment.recording_id, f"录音#{segment.recording_id}"),
            "idx": segment.idx,
            "speaker": segment.speaker,
            "text": segment.text,
            "start_ms": segment.start_ms,
            "end_ms": segment.end_ms,
        }
        for segment in db.exec(
            select(TranscriptSegment).order_by(TranscriptSegment.recording_id, TranscriptSegment.idx)
        ).all()
    ]
    profiles = [
        {
            "name": profile.name,
            "source": profile.source,
            "style_tags": profile.style_tags,
            "question_count": (profile.params or {}).get("question_count", ""),
            "created_at": profile.created_at,
            "notes": profile.notes,
        }
        for profile in db.exec(select(InterviewerProfile).order_by(InterviewerProfile.id)).all()
    ]

    return ExportBundle(
        generated_at=utcnow(),
        sections=[
            build_targets(targets),
            build_resumes(resumes),
            build_applications(applications),
            build_application_events(events),
            build_sessions(sessions),
            build_turns(turns),
            build_reports(reports),
            build_improvements(improvements),
            build_expression(expressions),
            build_mastery(mastery_rows),
            build_training(training_rows),
            build_stories(stories),
            build_answers(answers),
            build_offers(offers),
            build_contacts(contacts),
            build_interactions(interactions),
            build_learning(learning),
            build_onboarding(onboarding),
            build_okr(okr_rows),
            build_career_plan(plan_items),
            build_benchmarks(benchmarks),
            build_job_postings(postings),
            build_apply_tasks(apply_tasks),
            build_apply_rules(apply_rules),
            build_blacklist(blacklist),
            build_platform_accounts(platform_accounts),
            build_import_batches(import_batches),
            build_topics(topic_rows),
            build_mastery_events(mastery_events),
            build_questions(question_rows),
            build_reviews(reviews),
            build_recordings(recordings),
            build_transcripts(transcripts),
            build_profiles(profiles),
        ],
    )


def export_markdown(db: Session) -> str:
    return render_markdown(build_bundle(db))


def export_json_payload(db: Session) -> dict[str, Any]:
    return render_json(build_bundle(db))


def export_csv_zip(db: Session) -> bytes:
    """每个板块一个 CSV，打成一个 ZIP（不引入 pandas/openpyxl：CSV 本身 Excel 就能开）。"""
    files = render_csv_sections(build_bundle(db))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename, content in files:
            # utf-8-sig 带 BOM，Excel 双击打开中文不乱码
            archive.writestr(filename, "\ufeff" + content)
    return buffer.getvalue()
