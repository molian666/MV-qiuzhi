"""机器视觉题库的**删除**功能测试（M13 补充）。

这个功能的坑不在"能不能删"，而在**删完以后别的地方还在引用它**，所以每条用例
都写清了它在防哪一类回归：

- 内置题是**按题干幂等播种**进 `question_bank` 的（见 `vision_service.ensure_seeded`）。
  如果把行硬删掉，下一次播种（重启、或清掉进程内标记）就会把它原样加回来——
  「删了又自己回来」比删不掉更让人困惑，所以删除做成墓碑（tags 里加 `已删除`），
  播种去重必须继续认得它。
- `question_attempt.question_id` 有外键指向题目：不清理自测记录就是悬挂引用。
- 已删除的题不能出现在列表、统计、上传批次、**抽题池**里——最后一条最要紧：
  "删掉的题还在面试里被问到"比列表里多一行严重得多。
- 草稿不能从这个入口删：草稿还没进题库，该走 `DELETE /api/vision/drafts/{batch}`。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.db import session_scope
from app.models.core import DELETED_TAG, QuestionAttempt, QuestionBank
from app.vision.question import all_questions
from app.vision_service import reset_seed_flag

#: 两道题的 JSON 文档：用它验证"批次里的题数会跟着删除变化"。
TWO_QUESTIONS_DOC = """[
  {"question": "删除测试用题 A：远心镜头什么时候该用？", "answer": "需要消除透视误差时。"},
  {"question": "删除测试用题 B：景深不够该动什么？", "answer": "先动光圈，再考虑光源与机械稳定性。"}
]"""

#: 一道题的 Markdown 文档：用它验证"删掉之后还能重新导入回来"。
ONE_QUESTION_DOC = """# 删除测试

删掉之后还能导回来吗？
删掉的题必须能被重新导入，否则删一次就等于永久拉黑这个题干。

- 墓碑不该挡住重新导入
"""


@pytest.fixture(autouse=True)
def _fresh_seed_flag() -> Iterator[None]:
    """播种标记是进程级的，跨测试会残留；数据目录却是每个测试一份。"""
    reset_seed_flag()
    yield
    reset_seed_flag()


def bank_items(client: TestClient) -> list[dict]:
    return client.get("/api/vision/questions").json()


def upload(client: TestClient, *, filename: str, content: str) -> dict:
    response = client.post(
        "/api/vision/import",
        files={"file": (filename, content.encode("utf-8"), "text/plain")},
        data={"role": "uploaded"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def generate(client: TestClient, **payload) -> dict:
    body = {"role": "development", "count": 3, **payload}
    response = client.post("/api/vision/generate", json=body)
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# 单题删除
# --------------------------------------------------------------------------- #


def test_delete_question_removes_it_from_the_bank(client: TestClient) -> None:
    """删掉一道题之后：详情 404、列表里没有、总数减一。

    这三处任何一处漏掉，用户都会看到一道"已经删了却还在"的题。
    """
    total_before = client.get("/api/vision/meta").json()["total"]
    target = bank_items(client)[0]

    response = client.delete(f"/api/vision/questions/{target['id']}")
    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": 1}

    assert client.get(f"/api/vision/questions/{target['id']}").status_code == 404
    assert all(item["id"] != target["id"] for item in bank_items(client))
    assert client.get("/api/vision/meta").json()["total"] == total_before - 1


def test_delete_unknown_question_404(client: TestClient) -> None:
    """删不存在的 id 要 404：静默成功会让调用方以为删掉了别的东西。"""
    assert client.delete("/api/vision/questions/999999").status_code == 404


def test_delete_twice_reports_404_the_second_time(client: TestClient) -> None:
    """同一道题删两次：第二次如实说"没有这道题"，而不是再报一次"删了 1 条"。"""
    target = bank_items(client)[0]

    assert client.delete(f"/api/vision/questions/{target['id']}").json() == {"deleted": 1}
    assert client.delete(f"/api/vision/questions/{target['id']}").status_code == 404


def test_deleted_builtin_question_does_not_come_back_after_reseed(client: TestClient) -> None:
    """★ 防的回归：内置题硬删之后，重启（重新播种）会把它原样加回来。

    播种是按题干幂等的，所以删除必须留下墓碑让去重继续认得它——
    否则用户会遇到"删了，重启一下又回来了"，然后彻底不信任这个功能。
    """
    target = bank_items(client)[0]
    assert client.delete(f"/api/vision/questions/{target['id']}").status_code == 200

    reset_seed_flag()  # 模拟重启：进程内标记清掉，播种逻辑重跑一遍

    assert client.get("/api/vision/meta").json()["total"] == len(all_questions()) - 1
    assert all(item["id"] != target["id"] for item in bank_items(client))
    assert client.get(f"/api/vision/questions/{target['id']}").status_code == 404


def test_delete_clears_self_test_attempts(client: TestClient) -> None:
    """★ 防的回归：自测记录有外键指向题目，删除必须连它一起清。

    不清的话就是悬挂引用——题已经从界面上消失了，`question_attempt` 里却还留着
    指向它的行（统计里会一直挂着一个取不到的 question_id）。
    """
    target = bank_items(client)[0]
    for rating in ("missed", "got_it"):
        client.post(f"/api/vision/questions/{target['id']}/attempt", json={"rating": rating})

    with session_scope() as session:
        before = session.exec(
            select(QuestionAttempt).where(QuestionAttempt.question_id == target["id"])
        ).all()
    assert len(before) == 2, "前提没成立：自测记录没写进去"

    assert client.delete(f"/api/vision/questions/{target['id']}").json() == {"deleted": 1}

    with session_scope() as session:
        after = session.exec(
            select(QuestionAttempt).where(QuestionAttempt.question_id == target["id"])
        ).all()
        row = session.get(QuestionBank, target["id"])

    assert after == [], "自测记录没被清理，成了悬挂引用"
    # 行本身要留着做墓碑：删行会让重新播种把题加回来
    assert row is not None
    assert DELETED_TAG in (row.tags or [])


# --------------------------------------------------------------------------- #
# 批量删除
# --------------------------------------------------------------------------- #


def test_batch_delete_multiple_questions(client: TestClient) -> None:
    """用户往往是多选删除：一次删多道，返回真正删掉的条数。"""
    ids = [item["id"] for item in bank_items(client)[:3]]

    response = client.post("/api/vision/questions/delete", json={"question_ids": ids})
    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": 3}

    remaining = {item["id"] for item in bank_items(client)}
    assert not remaining & set(ids)
    assert len(remaining) == len(all_questions()) - 3


def test_batch_delete_ignores_unknown_and_repeat(client: TestClient) -> None:
    """界面上的勾选集合可能比库里的旧：重传、夹带不存在的 id 都只按实际删掉的计数。

    报错只会让用户重试一个注定失败的请求；返回 0 才说明"这批里没有可删的了"。
    """
    target = bank_items(client)[0]["id"]
    body = {"question_ids": [target, 999999]}

    assert client.post("/api/vision/questions/delete", json=body).json() == {"deleted": 1}
    assert client.post("/api/vision/questions/delete", json=body).json() == {"deleted": 0}


def test_batch_delete_rejects_empty_list(client: TestClient) -> None:
    """空列表是调用方搞错了（前端没勾选时不会发请求）：直接拒掉，
    而不是返回一个"删了 0 条"的成功把 bug 掩盖过去。"""
    assert client.post("/api/vision/questions/delete", json={"question_ids": []}).status_code == 422


def test_batch_delete_does_not_shadow_adopt(client: TestClient) -> None:
    """路由顺序的回归：`/questions/delete` 与 `/questions/adopt` 同处一级，
    批量删除不能把按题采纳那条路顶掉（反过来也一样）。"""
    drafts = generate(client)["drafts"]
    picked = [drafts[0]["id"], drafts[1]["id"]]

    assert client.post("/api/vision/questions/delete", json={"question_ids": picked}).json() == {
        "deleted": 0
    }
    assert client.post("/api/vision/questions/adopt", json={"question_ids": picked}).json() == {
        "adopted": 2
    }


# --------------------------------------------------------------------------- #
# 与既有流程的边界
# --------------------------------------------------------------------------- #


def test_drafts_cannot_be_deleted_from_this_entry(client: TestClient) -> None:
    """★ 草稿该走 `DELETE /drafts/{batch}`：这个入口只删"已入库"的题。

    否则用户会以为自己把草稿删了（其实请求被静默忽略），或者反过来——
    把还没决定采纳的题顺手删掉，那是"我不确定要不要"变成"没了"。
    """
    result = generate(client)
    ids = [item["id"] for item in result["drafts"]]

    for question_id in ids:
        assert client.delete(f"/api/vision/questions/{question_id}").status_code == 404
    assert client.post("/api/vision/questions/delete", json={"question_ids": ids}).json() == {
        "deleted": 0
    }

    # 草稿一道没少，仍然可以被丢弃（原有流程没被这条新路径影响）
    assert client.get("/api/vision/drafts").json()[0]["count"] == 3
    assert client.delete(f"/api/vision/drafts/{result['batch']}").json()["removed"] == 3


def test_deleted_question_is_not_picked_for_interview(client: TestClient) -> None:
    """★ 防的回归：抽题池忘了筛墓碑。

    "删掉的题还在面试里被问到"比列表里多一行严重得多——那是在用一道你明确
    表示不要了的题。这里把已采纳的生成题全删掉，再要一批 generated 的题。
    """
    result = generate(client)
    client.post(f"/api/vision/drafts/{result['batch']}/adopt")
    ids = [item["id"] for item in result["drafts"]]
    assert client.post("/api/vision/questions/delete", json={"question_ids": ids}).json() == {
        "deleted": 3
    }

    created = client.post(
        "/api/sessions",
        json={"max_questions": 3, "question_count": 3, "question_mix": {"generated": 1}},
    ).json()
    plan = created["plan"]

    assert plan["question_sources"].get("generated", 0) == 0, "生成的那批已经全删了"
    assert len(plan["questions"]) == 3, "某个来源没题了要从别处补足，不能少给题"
    # Mock 生成的题题干里带 "Mock"（见 test_vision.py）：一道都不该再被抽到
    assert all("Mock" not in question["text"] for question in plan["questions"])


def test_deleted_question_leaves_the_upload_batch_count(client: TestClient) -> None:
    """上传批次上显示的题数要跟着删除走：删了一道批次还显示原数量，
    用户会以为删除没生效。"""
    upload(client, filename="删除测试.json", content=TWO_QUESTIONS_DOC)
    uploaded = client.get("/api/vision/questions", params={"role": "uploaded"}).json()
    assert len(uploaded) == 2
    assert client.get("/api/vision/uploads").json()[0]["count"] == 2

    client.delete(f"/api/vision/questions/{uploaded[0]['id']}")

    assert client.get("/api/vision/uploads").json()[0]["count"] == 1


def test_deleted_question_can_be_imported_again(client: TestClient) -> None:
    """★ 防的回归：墓碑如果算进「题干已存在」，删掉一道题之后就再也导不回来。

    那种情况下导入还会显示"跳过 1 道（题干已存在）"——用户只会觉得导入坏了，
    而且界面上没有任何地方提示"这个题干被拉黑了"。
    """
    upload(client, filename="删除测试.md", content=ONE_QUESTION_DOC)
    target = client.get("/api/vision/questions", params={"role": "uploaded"}).json()[0]
    assert client.delete(f"/api/vision/questions/{target['id']}").json() == {"deleted": 1}

    again = upload(client, filename="删除测试.md", content=ONE_QUESTION_DOC)

    assert again["added"] == 1, again
    assert again["skipped"] == 0, "刚删掉的题不该被当成「已存在」而跳过"
    assert len(client.get("/api/vision/questions", params={"role": "uploaded"}).json()) == 1
