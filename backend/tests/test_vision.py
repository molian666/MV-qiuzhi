"""机器视觉题库测试（M13）。

除了常规的接口测试，这里盯的是**内容质量**——因为题库的价值全在内容上，
而内容错了测试也不会报错，只能靠约定和检查：

- 答案里不能有会过时的断言（具体价格、促销信息）；
- 每题必须有要点、答案、追问、常见错误，缺一块就是半成品；
- 播种必须幂等（reboot 不该产生重复题）。
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from app.analysis.question_bank import BankQuestion
from app.vision.import_doc import decode_bytes, parse_document
from app.vision.question import BUILTIN_ROLES, ROLE_LABELS, all_questions, by_role
from app.vision_service import _allocate, render_answer, reset_seed_flag

# --------------------------------------------------------------------------- #
# 内容质量
# --------------------------------------------------------------------------- #


def test_every_role_has_questions() -> None:
    """三类岗位都得有题——只做开发方向的题库，对应用和实施岗没有意义。

    `uploaded` 不在其中：它是**来源**而不是岗位，不该要求它有内置题。
    """
    for role in BUILTIN_ROLES:
        questions = by_role(role)
        assert len(questions) >= 5, f"{ROLE_LABELS[role]}方向只有 {len(questions)} 道题，太少"


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
    assert all(
        item["count"] >= 5 for item in body["roles"] if item["key"] in BUILTIN_ROLES
    )
    assert len(body["categories"]) >= 6
    assert all(item["description"] for item in body["roles"])


def test_meta_reports_sources_for_mix_setting(client: TestClient) -> None:
    """前端要用它判断「上传的题够不够撑起想要的占比」。"""
    sources = {item["key"]: item for item in client.get("/api/vision/meta").json()["sources"]}

    assert sources["builtin"]["count"] == len(all_questions())
    assert sources["uploaded"]["count"] == 0
    assert sources["uploaded"]["label"] == "我上传的"


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


# --------------------------------------------------------------------------- #
# 文档导入
# --------------------------------------------------------------------------- #

JSON_DOC = """[
  {"question": "什么是远心镜头，什么时候该用它？", "answer": "远心镜头把主光线做成平行光……",
   "points": ["消除透视误差", "测量场景常用"], "category": "镜头与光学", "difficulty": 3}
]"""

CSV_DOC = (
    "question,answer,points,category,difficulty\n"
    "什么叫景深？,景深是清晰成像的范围,清晰范围|影响因素,镜头与光学,4\n"
)

MD_DOC = """# 相机与传感器

选相机时分辨率该怎么估？
先算像素当量，再留 3-5 倍安全系数。

- 安全系数要留够
- 运动物体要用全局快门
"""


def test_parse_json_imports_all_fields() -> None:
    result = parse_document("bank.json", JSON_DOC)

    assert result.source_format == "json"
    assert len(result.questions) == 1
    question = result.questions[0]
    assert "远心镜头" in question.text
    assert question.points == ["消除透视误差", "测量场景常用"]
    assert question.category == "镜头与光学"
    assert question.difficulty == 3


def test_parse_csv_splits_points() -> None:
    result = parse_document("bank.csv", CSV_DOC)

    assert result.source_format == "csv"
    assert result.questions[0].points == ["清晰范围", "影响因素"]
    assert result.questions[0].difficulty == 4


def test_parse_markdown_finds_question_answer_and_category() -> None:
    result = parse_document("面经.md", MD_DOC)

    assert result.source_format == "text"
    assert len(result.questions) == 1
    question = result.questions[0]
    assert question.text.startswith("选相机时分辨率该怎么估")
    assert "像素当量" in question.answer
    assert question.category == "相机与传感器"
    assert "安全系数要留够" in question.points
    # 规则推断出来的结果必须带提醒，不能让人以为它是准的
    assert any("规则推断" in warning for warning in result.warnings)


def test_decode_gbk_document() -> None:
    """中文面经从 Windows 里导出来经常是 GBK，只认 UTF-8 会直接乱码。"""
    text, encoding = decode_bytes("中文题目：什么是标定？".encode("gbk"))

    assert "标定" in text
    assert "gbk" in encoding


def test_parse_document_without_questions_warns() -> None:
    result = parse_document("note.txt", "这里没有任何问题，只是普通说明文字。")

    assert result.questions == []
    assert result.warnings


# --------------------------------------------------------------------------- #
# 导入 API
# --------------------------------------------------------------------------- #


def upload(client: TestClient, *, filename: str, content: str, role: str = "uploaded") -> dict:
    response = client.post(
        "/api/vision/import",
        files={"file": (filename, content.encode("utf-8"), "text/plain")},
        data={"role": role},
    )
    assert response.status_code == 200, response.text
    return response.json()


def uploaded_items(client: TestClient) -> list[dict]:
    return [item for item in client.get("/api/vision/questions").json() if item["role"] == "uploaded"]


def test_import_adds_questions_to_bank(client: TestClient) -> None:
    info = upload(client, filename="面经.md", content=MD_DOC)

    assert info["added"] == 1
    assert info["source"].startswith("upload:")
    assert len(client.get("/api/vision/questions").json()) == len(all_questions()) + 1

    sources = {item["key"]: item["count"] for item in client.get("/api/vision/meta").json()["sources"]}
    assert sources["uploaded"] == 1


def test_import_is_idempotent_by_question_text(client: TestClient) -> None:
    """重复上传同一份文档只补新增：整批失败和整批重复都不能接受。"""
    upload(client, filename="面经.md", content=MD_DOC)
    second = upload(client, filename="面经.md", content=MD_DOC)

    assert second["added"] == 0
    assert second["skipped"] == 1
    assert len(client.get("/api/vision/questions").json()) == len(all_questions()) + 1


def test_import_rejects_empty_file(client: TestClient) -> None:
    response = client.post("/api/vision/import", files={"file": ("empty.txt", b"", "text/plain")})
    assert response.status_code == 400


def test_import_rejects_unparseable_document(client: TestClient) -> None:
    response = client.post(
        "/api/vision/import",
        files={"file": ("note.txt", "只是一段说明，没有问题。".encode("utf-8"), "text/plain")},
    )
    assert response.status_code == 400


def test_uploaded_question_detail_works(client: TestClient) -> None:
    upload(client, filename="面经.md", content=MD_DOC)
    items = uploaded_items(client)
    assert len(items) == 1

    detail = client.get(f"/api/vision/questions/{items[0]['id']}").json()
    assert detail["role_label"] == "我上传的"
    assert detail["points"]
    assert "像素当量" in detail["answer"]
    # 分类要从文档里取，不能被来源文件名（上传:xxx.md）顶掉
    assert detail["category"] not in {"", "未分类"}
    assert "上传:" not in detail["category"], detail["category"]


def test_uploaded_question_can_be_self_tested(client: TestClient) -> None:
    upload(client, filename="面经.md", content=MD_DOC)
    items = uploaded_items(client)

    result = client.post(
        f"/api/vision/questions/{items[0]['id']}/attempt", json={"rating": "got_it"}
    ).json()
    assert result["attempts"] == 1


def test_uploads_listing_and_delete(client: TestClient) -> None:
    info = upload(client, filename="面经.md", content=MD_DOC)

    listed = client.get("/api/vision/uploads").json()
    assert len(listed) == 1
    assert listed[0]["batch"] == info["batch"]
    assert listed[0]["filename"] == "面经.md"

    assert client.delete(f"/api/vision/uploads/{info['batch']}").json()["removed"] == 1
    assert client.get("/api/vision/uploads").json() == []
    assert len(client.get("/api/vision/questions").json()) == len(all_questions())


def test_delete_unknown_batch_404(client: TestClient) -> None:
    assert client.delete("/api/vision/uploads/nope").status_code == 404


def test_delete_upload_removes_its_attempts(client: TestClient) -> None:
    """外键约束会拦住直接删题，所以批次删除必须连自测记录一起清。"""
    info = upload(client, filename="面经.md", content=MD_DOC)
    items = uploaded_items(client)
    client.post(f"/api/vision/questions/{items[0]['id']}/attempt", json={"rating": "missed"})

    assert client.delete(f"/api/vision/uploads/{info['batch']}").json()["removed"] == 1
    assert len(client.get("/api/vision/questions").json()) == len(all_questions())


# --------------------------------------------------------------------------- #
# 题源占比
# --------------------------------------------------------------------------- #


def test_allocate_splits_exactly() -> None:
    """名额按最大余数法分配，各来源相加必须正好等于总数。"""
    assert _allocate(6, {"builtin": 1, "uploaded": 1}) == {"builtin": 3, "uploaded": 3}
    assert sum(_allocate(10, {"builtin": 1, "uploaded": 1, "other": 1}).values()) == 10

    uneven = _allocate(5, {"builtin": 2, "uploaded": 3})
    assert sum(uneven.values()) == 5
    assert uneven["uploaded"] > uneven["builtin"]


def test_allocate_skips_empty_sources() -> None:
    """不能把名额分给一个没有题的来源。"""
    quota = _allocate(4, {"builtin": 1, "uploaded": 1}, available={"builtin": 10, "uploaded": 0})
    assert quota["uploaded"] == 0
    assert quota["builtin"] >= 1


def test_session_uses_bank_mix(client: TestClient) -> None:
    """按题源占比组题：上传的题只有 1 道，所以它占 1、内置占 3。"""
    upload(client, filename="面经.md", content=MD_DOC)

    created = client.post(
        "/api/sessions",
        json={
            "max_questions": 4,
            "question_count": 4,
            "question_mix": {"builtin": 1, "uploaded": 1},
        },
    ).json()
    plan = created["plan"]

    assert len(plan["questions"]) == 4
    assert sum(plan["question_sources"].values()) == 4
    assert plan["question_sources"]["uploaded"] == 1
    assert plan["question_sources"]["builtin"] == 3
    # 要点也存进 plan，报告里要拿它对照「哪些点答到了」
    assert all(question["points"] for question in plan["questions"])


def test_session_without_mix_lets_model_choose(client: TestClient) -> None:
    """不传 mix 就退回自由出题：不会硬塞题库的题进去。"""
    created = client.post("/api/sessions", json={"max_questions": 2}).json()
    assert "questions" not in created["plan"]


def test_session_records_actual_mix_when_uploaded_is_scarce(client: TestClient) -> None:
    """请求的比例可能因为某来源题不够而被调整，实际值要如实记录。

    否则用户会以为「我设了 1:1 为什么出来的全是内置题」。
    """
    upload(client, filename="面经.md", content=MD_DOC)

    created = client.post(
        "/api/sessions",
        json={
            "max_questions": 6,
            "question_count": 6,
            "question_mix": {"builtin": 1, "uploaded": 1},
        },
    ).json()
    plan = created["plan"]

    assert len(plan["questions"]) == 6, "某来源不够时要补足，不能少给题"
    # 请求的比例仍原样记着，实际分布另存——两个都留着才解释得清
    assert plan["question_mix"] == {"builtin": 1, "uploaded": 1}
    assert plan["question_sources"]["uploaded"] == 1


# --------------------------------------------------------------------------- #
# 模型出题：草稿 → 采纳
# --------------------------------------------------------------------------- #


def generate(client: TestClient, **payload) -> dict:
    body = {"role": "development", "count": 3, **payload}
    response = client.post("/api/vision/generate", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_generate_creates_drafts_outside_the_pool(client: TestClient) -> None:
    """★ 生成的题先存为草稿：**不进题库页、也不进抽题池**。"""
    before = len(client.get("/api/vision/questions").json())
    result = generate(client)

    assert result["added"] == 3, result
    assert result["batch"]
    assert len(result["drafts"]) == 3
    assert all(question["role"] == "development" for question in result["drafts"])

    # 题库页看不到草稿
    assert len(client.get("/api/vision/questions").json()) == before
    # 待采纳数量单独统计
    assert client.get("/api/vision/meta").json()["draft_count"] == 3


def test_drafts_are_not_in_the_source_stats(client: TestClient) -> None:
    """草稿不算进来源占比——它们还没被采纳，不该影响「能抽多少题」。"""
    generate(client)
    sources = {item["key"]: item["count"] for item in client.get("/api/vision/meta").json()["sources"]}

    assert sources["generated"] == 0, sources
    assert client.get("/api/vision/drafts").json()[0]["count"] == 3


def test_adopt_moves_drafts_into_the_pool(client: TestClient) -> None:
    """★ 采纳之后才进抽题池，才可能被面试抽到。"""
    batch = generate(client)["batch"]

    adopted = client.post(f"/api/vision/drafts/{batch}/adopt").json()
    assert adopted["adopted"] == 3
    assert client.get("/api/vision/meta").json()["draft_count"] == 0

    sources = {item["key"]: item["count"] for item in client.get("/api/vision/meta").json()["sources"]}
    assert sources["generated"] == 3

    items = client.get("/api/vision/questions").json()
    assert len([item for item in items if item["role"] == "development" and item["category"] != ""]) >= 3
    assert client.get("/api/vision/drafts").json() == []


def test_discard_removes_drafts(client: TestClient) -> None:
    batch = generate(client)["batch"]

    assert client.delete(f"/api/vision/drafts/{batch}").json()["removed"] == 3
    assert client.get("/api/vision/drafts").json() == []
    assert client.get("/api/vision/meta").json()["draft_count"] == 0


def test_append_adds_to_the_same_batch(client: TestClient) -> None:
    """「继续生成」是在同一批上累加，不是另开一批。"""
    first = generate(client)
    second = generate(client, mode="append", batch=first["batch"])

    assert second["batch"] == first["batch"]
    assert second["added"] == 3
    assert len(second["drafts"]) == 6
    assert len(client.get("/api/vision/drafts").json()) == 1
    assert client.get("/api/vision/meta").json()["draft_count"] == 6


def test_replace_discards_the_old_batch(client: TestClient) -> None:
    """「重新生成」是丢掉这批重来，不是追加。"""
    first = generate(client)
    second = generate(client, mode="replace", batch=first["batch"])

    assert second["batch"] == first["batch"]
    assert len(second["drafts"]) == 3, "旧的那批该被丢掉"
    assert client.get("/api/vision/meta").json()["draft_count"] == 3


def test_replace_needs_an_existing_batch(client: TestClient) -> None:
    assert client.post(
        "/api/vision/generate",
        json={"role": "development", "count": 2, "mode": "append", "batch": "nope"},
    ).status_code == 404


def test_generate_skips_duplicates(client: TestClient) -> None:
    """同一批里继续生成时，模型给的题干可能与已有重复——要跳过而不是重号入库。"""
    first = generate(client)
    again = generate(client, mode="append", batch=first["batch"], count=3)
    texts = [item["text"] for item in again["drafts"]]
    assert len(texts) == len(set(texts)), "草稿里不该出现重复题干"


def test_generate_asks_model_not_to_repeat_existing(client: TestClient) -> None:
    """「继续生成」必须把已有题干带给模型，否则连点几次会得到一堆近义题。"""
    from app.analysis.question_generate import build_messages

    existing = [
        BankQuestion(id=1, text="怎么选相机？"),
        BankQuestion(id=2, text="景深受什么影响？"),
    ]
    messages = build_messages(role="development", count=3, existing=existing)
    body = "\n".join(message.content for message in messages)

    assert "怎么选相机？" in body
    assert "景深受什么影响？" in body
    assert "不要再出同义或近似的问题" in body


def test_mock_generated_questions_are_marked_as_placeholders() -> None:
    """Mock 生成的题必须一眼看出是占位内容。

    Mock 编不出真实题目；如果它生成的东西看起来像真题（尤其带具体数值），
    用户会误以为是可用的面试素材——那比「功能不可用」严重得多。
    """
    from app.providers.llm.base import LLMMessage
    from app.providers.llm.mock_generate import build_mock_generated_questions

    raw = build_mock_generated_questions(
        [LLMMessage(role="user", content="请出 2 道题。")], count=2
    )
    parsed = json.loads(raw)

    assert parsed["mock"] is True
    assert len(parsed["questions"]) == 2
    for question in parsed["questions"]:
        assert "Mock" in question["question"]
        assert any("Mock" in point for point in question["points"])
        assert "不是真实面试题" in question["answer"]


def test_generate_rejects_bad_role(client: TestClient) -> None:
    assert client.post(
        "/api/vision/generate", json={"role": "not-a-role", "count": 2}
    ).status_code == 422


def test_generate_rejects_out_of_range_count(client: TestClient) -> None:
    assert client.post(
        "/api/vision/generate", json={"role": "development", "count": 99}
    ).status_code == 422


def test_adopt_unknown_batch_404(client: TestClient) -> None:
    assert client.post("/api/vision/drafts/nope/adopt").status_code == 404
    assert client.delete("/api/vision/drafts/nope").status_code == 404


def test_adopted_generated_questions_can_be_interviewed(client: TestClient) -> None:
    """★ 端到端：生成 → 采纳 → 面试按占比抽到它。"""
    batch = generate(client)["batch"]
    client.post(f"/api/vision/drafts/{batch}/adopt")

    # 抽 3 道（正好是这批的数量）：要 4 道的话 generated 池不够，
    # fallback 会从内置题补足——那是设计好的行为，但会让这个断言失去意义
    created = client.post(
        "/api/sessions",
        json={
            "max_questions": 3,
            "question_count": 3,
            "question_mix": {"generated": 1},
        },
    ).json()

    assert created["plan"]["question_sources"].get("generated", 0) == 3
    assert all("Mock" in question["text"] for question in created["plan"]["questions"]), (
        "只抽模型生成的题时，抽到的几道都该是生成的那批"
    )


def test_pool_falls_back_when_generated_is_not_enough(client: TestClient) -> None:
    """某个来源题不够时从别处补足——宁可比例不准，也不能少给题。"""
    batch = generate(client)["batch"]
    client.post(f"/api/vision/drafts/{batch}/adopt")

    created = client.post(
        "/api/sessions",
        json={"max_questions": 5, "question_count": 5, "question_mix": {"generated": 1}},
    ).json()
    sources = created["plan"]["question_sources"]

    assert sum(sources.values()) == 5
    assert sources.get("generated", 0) == 3, "只有 3 道就最多给 3 道"
    assert sources.get("builtin", 0) == 2, "剩下的从内置题补"


def test_generated_questions_keep_answer_and_points(client: TestClient) -> None:
    """生成的题要带答案与要点——不然采纳了也没法自测。"""
    result = generate(client)
    batch = result["batch"]
    client.post(f"/api/vision/drafts/{batch}/adopt")

    # 直接用草稿返回的 id，别在列表里猜是哪一道
    question_id = result["drafts"][0]["id"]
    detail = client.get(f"/api/vision/questions/{question_id}").json()

    assert detail["points"], "要点要在"
    assert len(detail["answer"]) >= 100, "答案要在"
    # 追问与常见错误拼进了答案正文（与内置题同样的拼法）
    assert "## 面试官大概率会追问" in detail["answer"]
    assert "## 常见错误回答" in detail["answer"]
