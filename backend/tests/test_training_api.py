"""M5 端到端：报告 → 掌握度 → 今日训练 → 作答 → 掌握度变化。

这条链路是 M5 的核心价值，所以用 API 层测试把整条串起来，
而不只是分别测纯函数。
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _prepare_report(client: TestClient, *, answers: int = 2) -> int:
    """跑一场面试并生成报告（报告会把能力标签累积成掌握度）。"""
    created = client.post("/api/sessions", json={"max_questions": answers}).json()
    session_id = int(created["id"])
    for index in range(answers):
        client.post(
            f"/api/sessions/{session_id}/answer",
            json={"text": f"第 {index + 1} 轮：我负责订单链路，QPS 从 800 提到 4200，P99 降到 90ms。"},
        )
    report = client.post(f"/api/sessions/{session_id}/report")
    assert report.status_code == 200
    return session_id


def test_report_accumulates_mastery(client: TestClient) -> None:
    _prepare_report(client)

    overview = client.get("/api/skills/mastery").json()
    assert overview["items"], "报告里的能力标签应该变成掌握度条目"
    assert overview["average_mastery"] > 0
    assert overview["high_risk_count"] >= 0

    for item in overview["items"]:
        assert item["attempts"] >= 1
        assert 0 <= item["mastery"] <= 100
        assert item["name"]


def test_mastery_items_sorted_weakest_first(client: TestClient) -> None:
    """最该练的排最前——训练页第一眼就要看到它。"""
    _prepare_report(client)
    items = client.get("/api/skills/mastery").json()["items"]
    masteries = [item["mastery"] for item in items]
    assert masteries == sorted(masteries)


def test_mastery_history_explains_the_change(client: TestClient) -> None:
    _prepare_report(client)
    item = client.get("/api/skills/mastery").json()["items"][0]

    history = client.get(f"/api/skills/{item['topic_id']}/history").json()
    assert history
    event = history[0]
    assert event["source"] == "report"
    assert event["mastery_before"] == 0.0  # 首次观察：从 0 直接到目标值
    assert event["mastery_after"] == item["mastery"]
    assert 1 <= event["score"] <= 5
    assert event["note"]


def test_training_set_prioritises_weak_topics(client: TestClient) -> None:
    _prepare_report(client)

    questions = client.post("/api/training/next?limit=4&weak_count=3").json()
    assert questions, "应该能组出训练题"
    assert len(questions) <= 4

    weak = [question for question in questions if question["kind"] == "weak"]
    assert weak, "必须包含针对薄弱点的题"
    for question in weak:
        assert question["text"]
        assert question["reason"], "每道题都要能说明为什么推荐它"
        assert question["reason"]["total"] is not None


def test_training_question_origin_is_traceable(client: TestClient) -> None:
    _prepare_report(client)
    questions = client.post("/api/training/next?limit=3&weak_count=3").json()
    assert all(question["origin"] in {"bank", "generated"} for question in questions)


def test_training_answer_updates_mastery_and_attempts(client: TestClient) -> None:
    _prepare_report(client)
    questions = client.post("/api/training/next?limit=2&weak_count=1").json()
    question = questions[0]

    graded = client.post(
        "/api/training/answer",
        json={
            "question_id": question["question_id"],
            "answer": "首先结论：我把下单 QPS 从 800 提到 4200。做法是预扣库存，因为要保强一致。结果是 P99 降到 90ms。",
        },
    )
    assert graded.status_code == 200
    body = graded.json()

    assert 1 <= body["score"] <= 5
    assert body["comment"]
    assert body["mastery"], "答完应该连带更新相关能力的掌握度"

    change = body["mastery"][0]
    assert 0 <= change["before"] <= 100
    assert 0 <= change["after"] <= 100

    # 掌握度历史里应该出现这次训练
    history = client.get(f"/api/skills/{change['topic_id']}/history").json()
    assert any(event["source"] == "training" for event in history)

    # 计数也涨了（报告一次 + 训练一次）
    topic = next(item for item in client.get("/api/skills/mastery").json()["items"] if item["topic_id"] == change["topic_id"])
    assert topic["attempts"] >= 2


def test_training_answer_rejects_unknown_question(client: TestClient) -> None:
    assert client.post("/api/training/answer", json={"question_id": 9999, "answer": "随便答"}).status_code == 404


def test_training_answer_requires_non_empty_text(client: TestClient) -> None:
    assert client.post("/api/training/answer", json={"question_id": 1, "answer": ""}).status_code == 422


def test_new_user_still_gets_training_questions(client: TestClient) -> None:
    """还没练过任何东西时也要能出题，否则新用户打开训练页是空白。"""
    questions = client.post("/api/training/next").json()
    assert questions
    assert all(question["text"] for question in questions)


def test_training_set_fills_up_to_requested_limit(client: TestClient) -> None:
    """请求 4 道就给 4 道：题库为空时要靠现场生成补足，否则"3 薄弱 + 1 随机"落不了地。"""
    questions = client.post("/api/training/next?limit=4&weak_count=3").json()
    assert len(questions) == 4
    assert any(question["kind"] == "weak" for question in questions)


def test_training_never_repeats_the_same_text(client: TestClient) -> None:
    """即使题库里已有重复题干（历史数据 / 手动导入），同一次训练也不该出两道一样的题。"""
    for _ in range(2):
        client.post(
            "/api/questions",
            json={"text": "重复的题目：讲一次你没做成的项目", "type": "training", "tags": ["技术取舍"]},
        )

    questions = client.post("/api/training/next?limit=4&weak_count=3").json()
    texts = [question["text"] for question in questions]
    assert len(texts) == len(set(texts)), "同一次训练里出现了重复题"


def test_generated_questions_land_in_the_bank(client: TestClient) -> None:
    """为薄弱点生成的题会写进题库，下次可以直接复用。"""
    before = len(client.get("/api/questions").json())
    client.post("/api/training/next?limit=3&weak_count=3")
    after = client.get("/api/questions").json()
    assert len(after) > before
    assert any(question["source"] == "generated" for question in after)


def test_repeated_training_does_not_pile_up_duplicates(client: TestClient) -> None:
    """连续组题不能把同一道题反复写进题库，也不能在同一次里出现重复题。

    注意断言的是"没有重复题干"，而不是"题库不再增长"——
    题库里有更多不同的题是好事，重复才是问题。
    """
    first = client.post("/api/training/next?limit=4&weak_count=3").json()
    assert len({question["question_id"] for question in first}) == len(first), "同一次组题出现重复"

    client.post("/api/training/next?limit=4&weak_count=3")

    texts = [question["text"] for question in client.get("/api/questions?limit=500").json()]
    assert len(texts) == len(set(texts)), "题库里出现了重复题干"
