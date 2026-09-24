"""机器视觉题库测试（M13）。

除了常规的接口测试，这里盯的是**内容质量**——因为题库的价值全在内容上，
而内容错了测试也不会报错，只能靠约定和检查：

- 答案里不能有会过时的断言（具体价格、促销信息）；
- 每题必须有要点、答案、追问、常见错误，缺一块就是半成品；
- 播种必须幂等（reboot 不该产生重复题）。
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.vision.question import ROLE_LABELS, all_questions, by_role
from app.vision_service import render_answer, reset_seed_flag

# --------------------------------------------------------------------------- #
# 内容质量
# --------------------------------------------------------------------------- #


def test_every_role_has_questions() -> None:
    """三类岗位都得有题——只做开发方向的题库，对应用和实施岗没有意义。"""
    for role, label in ROLE_LABELS.items():
        questions = by_role(role)
        assert len(questions) >= 5, f"{label}方向只有 {len(questions)} 道题，太少"


def test_question_texts_are_unique() -> None:
    texts = [question.text for question in all_questions()]
    assert len(texts) == len(set(texts))


def test_every_question_is_complete() -> None:
    """缺任何一块都算半成品：只有问题没有答案的题库没法用来自测。"""
    for question in all_questions():
        assert question.text.strip(), "题干为空"
        assert 1 <= question.difficulty <= 5, f"{question.text[:20]} 难度越界"
        assert question.category.strip(), f"{question.text[:20]} 没有分类"
        assert len(question.points) >= 3, f"{question.text[:20]} 要点太少（自评清单不够用）"
        assert len(question.answer) >= 300, f"{question.text[:20]} 答案太短"
        assert question.followups, f"{question.text[:20]} 没有追问方向"
        assert question.pitfalls, f"{question.text[:20]} 没有常见错误"


def test_questions_are_phrased_as_interview_questions() -> None:
    """题干要写成面试官会问的样子，不是知识点标题。"""
    for question in all_questions():
        assert question.text.endswith(("？", "?")), f"{question.text[:30]} 不像一个问题"


#: 价格类表述。只匹配真正的价格写法，不要匹配「打折」「优惠」这类在中文里
#: 有正常用法的词——实测「要打折扣用」就被误伤过。
_PRICY = re.compile(r"\d+\s*元|￥|\$\s*\d|块钱|\d+\s*(?:万|千)\s*(?:块|元)")


def test_answers_avoid_perishable_claims() -> None:
    """答案里不能出现具体价格。

    价格会过时，而且这个项目没有权威来源去核实——写了就是在编造。
    需要引用参数的地方一律指向「厂商规格书」。
    """
    for question in all_questions():
        haystack = "\n".join(
            [question.answer, *question.points, *question.followups, *question.pitfalls]
        )
        match = _PRICY.search(haystack)
        assert match is None, f"{question.text[:24]} 里出现了会过时的表述：{match.group(0)!r}"


def test_answers_separate_standard_practice_from_experience() -> None:
    """涉及经验值的地方要标明是经验，不能让读者当成定理。

    覆盖标准的题不少，所以这里只要求「存在这类标注」，不逐题强求。
    """
    marked = sum(1 for question in all_questions() if "经验" in question.answer)
    assert marked >= 3, "几乎没有题目标明哪些是经验值"


def test_render_answer_includes_followups_and_pitfalls() -> None:
    question = next(iter(all_questions()))
    rendered = render_answer(question)
    assert question.answer.strip() in rendered
    assert "追问" in rendered
    assert "常见错误" in rendered


# --------------------------------------------------------------------------- #
# 播种
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _fresh_seed_flag() -> None:
    """播种标记是进程级的，跨测试会残留；数据目录却是每个测试一份。"""
    reset_seed_flag()


def test_seeding_is_idempotent(client: TestClient) -> None:
    first = client.get("/api/vision/meta").json()["total"]
    assert first == len(all_questions())

    # 再走一次（清掉进程标记，强制重新去重）
    reset_seed_flag()
    second = client.get("/api/vision/meta").json()["total"]
    assert second == first, "重复播种产生了重复题"


def test_reseeding_after_restart_does_not_duplicate(client: TestClient) -> None:
    """模拟重启：进程内的播种标记清掉、去重逻辑重跑一遍，题目数不该变。"""
    before = client.get("/api/vision/meta").json()["total"]
    reset_seed_flag()
    assert client.get("/api/vision/meta").json()["total"] == before


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


def test_meta_lists_roles_and_categories(client: TestClient) -> None:
    body = client.get("/api/vision/meta").json()

    assert body["total"] == len(all_questions())
    assert {item["key"] for item in body["roles"]} == set(ROLE_LABELS)
    assert all(item["count"] >= 5 for item in body["roles"])
    assert len(body["categories"]) >= 6
    assert all(item["description"] for item in body["roles"])


def test_list_questions_hides_answers(client: TestClient) -> None:
    """列表页要能快速扫几十道题，答案在详情里给。"""
    items = client.get("/api/vision/questions").json()
    assert len(items) == len(all_questions())
    assert "answer" not in items[0]
    assert "points" not in items[0]
    assert "sample_answer" not in str(items)


def test_list_can_filter_by_role(client: TestClient) -> None:
    everything = client.get("/api/vision/questions").json()
    development = client.get("/api/vision/questions", params={"role": "development"}).json()

    assert len(development) == len(by_role("development"))
    assert all(item["role"] == "development" for item in development)
    assert all(item["role_label"] == "开发工程师" for item in development)
    assert len(development) < len(everything)


def test_list_can_filter_by_difficulty_and_category(client: TestClient) -> None:
    easy = client.get("/api/vision/questions", params={"max_difficulty": 3}).json()
    assert easy
    assert all(item["difficulty"] <= 3 for item in easy)

    category = easy[0]["category"]
    same = client.get("/api/vision/questions", params={"category": category}).json()
    assert same
    assert all(item["category"] == category for item in same)


def test_list_can_search_text(client: TestClient) -> None:
    hits = client.get("/api/vision/questions", params={"keyword": "打光"}).json()
    assert hits
    assert all("打光" in item["text"] for item in hits)


def test_question_detail_gives_everything(client: TestClient) -> None:
    brief = client.get("/api/vision/questions").json()[0]
    detail = client.get(f"/api/vision/questions/{brief['id']}").json()

    assert detail["text"] == brief["text"]
    assert len(detail["points"]) >= 3
    assert len(detail["answer"]) >= 300
    assert detail["followups"]
    assert detail["pitfalls"]


def test_question_detail_404(client: TestClient) -> None:
    assert client.get("/api/vision/questions/999999").status_code == 404


def test_attempt_records_and_tallies(client: TestClient) -> None:
    question_id = client.get("/api/vision/questions").json()[0]["id"]

    first = client.post(
        f"/api/vision/questions/{question_id}/attempt",
        json={"rating": "missed", "note": "打光那题完全没思路"},
    ).json()
    assert first["attempts"] == 1
    assert first["last_rating"] == "missed"
    assert first["tally"] == {"missed": 1}

    second = client.post(
        f"/api/vision/questions/{question_id}/attempt",
        json={"rating": "got_it"},
    ).json()
    assert second["attempts"] == 2
    assert second["last_rating"] == "got_it"
    assert second["tally"] == {"missed": 1, "got_it": 1}

    # 列表上要能直接看到进度，不用再点进去
    brief = next(
        item for item in client.get("/api/vision/questions").json() if item["id"] == question_id
    )
    assert brief["attempts"] == 2
    assert brief["last_rating"] == "got_it"


def test_attempt_rejects_unknown_question(client: TestClient) -> None:
    response = client.post("/api/vision/questions/999999/attempt", json={"rating": "got_it"})
    assert response.status_code == 404


def test_attempt_rejects_invalid_rating(client: TestClient) -> None:
    question_id = client.get("/api/vision/questions").json()[0]["id"]
    response = client.post(
        f"/api/vision/questions/{question_id}/attempt",
        json={"rating": "8分"},
    )
    assert response.status_code == 422, "自评只有三档，打分数是无效输入"


def test_attempts_do_not_leak_into_interview_sessions(client: TestClient) -> None:
    """自测不属于任何模拟面试，不该出现在会话统计里。"""
    question_id = client.get("/api/vision/questions").json()[0]["id"]
    client.post(f"/api/vision/questions/{question_id}/attempt", json={"rating": "partial"})

    sessions = client.get("/api/sessions").json()
    assert sessions == []
