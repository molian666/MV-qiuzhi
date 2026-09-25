"""导出覆盖面（M15）：**新补的板块要有真数据落地**。

「板块存在」和「数据真的进了导出」是两回事：前者只要常量里有这个 key，
后者要求查库、join、字段映射都对得上。这个文件专门测后者。

背景：接手时导出只有 21 个板块，漏掉了节奏学习的录音与画像、题库与练习记录、
投递规则与黑名单这些**"不能重新生成"**的数据——导出少了它们就不叫全量备份。
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.analysis.export import EXPORT_SECTION_KEYS
from app.db import session_scope
from app.models.apply import (
    ApplyRule,
    ApplyTask,
    Blacklist,
    ImportBatch,
    JobPosting,
    PlatformAccount,
)
from app.models.core import QuestionBank
from app.models.pacing import InterviewerProfile, Recording, TranscriptSegment
from app.models.scoring import MasteryEvent, ReviewTask, SkillTopic

AUTH = {"X-Export-Token": "good"}

#:: 这批表大多没有（或很难走）API 写入路径，直接落库造数据
EXTRA_SECTION_KEYS = (
    "job_postings",
    "apply_tasks",
    "apply_rules",
    "blacklist",
    "platform_accounts",
    "import_batches",
    "topics",
    "mastery_events",
    "questions",
    "reviews",
    "recordings",
    "transcripts",
    "profiles",
)


def _seed_extra() -> None:
    with session_scope() as db:
        topic = SkillTopic(name="缓存", category="skill")
        db.add(topic)
        db.commit()
        db.refresh(topic)
        topic_id = int(topic.id or 0)

        db.add(SkillTopic(name="高并发", category="skill", parent_id=topic_id))
        db.add(MasteryEvent(topic_id=topic_id, mastery_before=40, mastery_after=55))

        question = QuestionBank(text="说说缓存穿透怎么解决", category="technical", difficulty="medium")
        db.add(question)
        db.commit()
        db.refresh(question)
        question_id = int(question.id or 0)

        db.add(
            ReviewTask(
                topic_id=topic_id,
                question_id=question_id,
                interval_days=6,
                repetitions=2,
                ease=2.6,
            )
        )

        posting = JobPosting(
            platform="boss",
            external_id="x-1",
            company="某某科技",
            title="后端工程师",
            jd_text="要求熟悉 Redis 与高并发",
        )
        db.add(posting)
        db.commit()
        db.refresh(posting)
        posting_id = int(posting.id or 0)

        db.add(ApplyTask(job_posting_id=posting_id, status="pending", say_hi_text="你好，看到贵司在招后端"))
        db.add(ApplyRule(platform="boss", name="排除外包", kind="keyword", params={"deny": ["外包"]}))
        db.add(Blacklist(platform="boss", company="某某外包", reason="manual"))
        db.add(PlatformAccount(platform="boss", daily_sent=3, daily_limit=30))
        db.add(ImportBatch(channel="boss", source="csv", rows=6, inserted=6))

        recording = Recording(title="真实面试 1", company="某某科技", role="后端")
        db.add(recording)
        db.commit()
        db.refresh(recording)
        recording_id = int(recording.id or 0)

        db.add(TranscriptSegment(recording_id=recording_id, idx=0, speaker="interviewer", text="先自我介绍一下"))
        db.add(
            InterviewerProfile(
                name="严格技术面",
                source="extracted",
                style_tags=["深挖", "追问"],
                params={"question_count": 8},
            )
        )
        db.commit()


def _sections(client: TestClient) -> dict[str, dict]:
    payload = json.loads(client.get("/api/export/json", headers=AUTH).text)
    return {section["key"]: section for section in payload["sections"]}


# --------------------------------------------------------------------------- #
# 覆盖面
# --------------------------------------------------------------------------- #
def test_new_sections_are_declared() -> None:
    for key in EXTRA_SECTION_KEYS:
        assert key in EXPORT_SECTION_KEYS, f"{key} 没有登记进板块清单"


def test_new_sections_carry_real_rows(client: TestClient) -> None:
    _seed_extra()
    sections = _sections(client)

    for key in EXTRA_SECTION_KEYS:
        assert sections[key]["rows"], f"{key} 板块是空的——数据没落地"


def test_secrets_and_cost_logs_are_not_exported() -> None:
    """配置表可能含 API Key，llm_call 是上万行的内部日志——这两个都不该进导出。"""
    for forbidden in ("app_setting", "provider_config", "llm_call"):
        assert forbidden not in EXPORT_SECTION_KEYS


# --------------------------------------------------------------------------- #
# join 是否正确（这是最容易被漏掉的一环）
# --------------------------------------------------------------------------- #
def test_apply_task_carries_job_company_and_title(client: TestClient) -> None:
    _seed_extra()
    row = _sections(client)["apply_tasks"]["rows"][0]

    # 队列表里只有 job_posting_id，公司/岗位名必须 join 出来，否则导出没法读
    assert row["company"] == "某某科技"
    assert row["title"] == "后端工程师"
    assert row["status"] == "pending"
    assert row["say_hi_text"]


def test_mastery_event_carries_topic_and_delta(client: TestClient) -> None:
    _seed_extra()
    row = _sections(client)["mastery_events"]["rows"][0]

    assert row["topic"] == "缓存"
    assert row["delta"] == 15.0  # 55 - 40，导出时算好，不用读的人再算


def test_review_joins_topic_and_question(client: TestClient) -> None:
    _seed_extra()
    row = _sections(client)["reviews"]["rows"][0]

    assert row["topic"] == "缓存"
    assert "缓存穿透" in row["question"]
    assert row["interval_days"] == 6.0


def test_topic_carries_parent_name(client: TestClient) -> None:
    _seed_extra()
    rows = _sections(client)["topics"]["rows"]
    child = next(row for row in rows if row["name"] == "高并发")

    # 标签树导出的是上级名称，不是 parent_id（否则读的人还得自己查表）
    assert child["parent"] == "缓存"


def test_transcript_carries_recording_title(client: TestClient) -> None:
    _seed_extra()
    row = _sections(client)["transcripts"]["rows"][0]

    assert row["recording"] == "真实面试 1"
    assert row["speaker"] == "interviewer"


def test_profile_carries_question_count(client: TestClient) -> None:
    _seed_extra()
    row = _sections(client)["profiles"]["rows"][0]

    assert row["name"] == "严格技术面"
    assert row["question_count"] == 8


def test_apply_rule_keeps_params(client: TestClient) -> None:
    _seed_extra()
    row = _sections(client)["apply_rules"]["rows"][0]

    # 规则参数是用户一条条配的，丢了等于要重配；JSON 要原样带出
    assert row["params"] == {"deny": ["外包"]}
    assert row["kind"] == "keyword"


# --------------------------------------------------------------------------- #
# 三种格式都要包含新板块
# --------------------------------------------------------------------------- #
def test_markdown_contains_new_sections(client: TestClient) -> None:
    _seed_extra()
    body = client.get("/api/export/markdown", headers=AUTH).text

    for title in ("## 黑名单", "## 题库", "## 真实面试录音", "## 待投队列"):
        assert title in body, f"Markdown 里缺板块：{title}"


def test_csv_zip_contains_new_section_files(client: TestClient) -> None:
    import io
    import zipfile

    _seed_extra()
    response = client.get("/api/export/csv", headers=AUTH)
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = set(archive.namelist())

    assert {"blacklist.csv", "questions.csv", "recordings.csv", "apply_tasks.csv"} <= names
    blacklist_csv = archive.read("blacklist.csv").decode("utf-8-sig")
    assert "某某外包" in blacklist_csv


def test_preview_counts_new_sections(client: TestClient) -> None:
    _seed_extra()
    payload = client.get("/api/export/preview", headers=AUTH).json()
    rows = {section["key"]: section["rows"] for section in payload["sections"]}

    assert rows["blacklist"] == 1
    assert rows["apply_tasks"] == 1
    assert rows["recordings"] == 1
    assert rows["topics"] == 2
    # 板块数与常量一致（不多不少）
    assert len(payload["sections"]) == len(EXPORT_SECTION_KEYS)
