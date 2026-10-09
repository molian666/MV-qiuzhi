"""M18：薄弱点面试——抽题优先命中掌握度低的技能点。

要证明三件事：
1. weak 抽题优先抽「标签命中薄弱能力点」的题（最弱技能点 → 相关题先被抽到）；
2. 没有任何掌握度记录时用默认薄弱方向，也能把题抽满（不会开不起来）；
3. 会话 plan 如实记录这场是不是薄弱点面试。

注意：conftest 给每个测试独立临时库，而 `ensure_seeded` 带进程内缓存
（第二个测试的库不会再播种内置题）——所以本文件统一用「自己上传的题」
当抽题池，不依赖内置题库。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import session_scope
from app.learning_service import apply_observation, ensure_topic

WEAK_TAG = "亚像素定位"  # 上传题 Q1 的标签，测试里把它打成全库最弱


def _upload(client: TestClient, *, filename: str, content: str) -> dict:
    response = client.post(
        "/api/vision/import",
        files={"file": (filename, content.encode("utf-8"), "text/plain")},
        data={"role": "uploaded"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _make_topic_weak(*, name: str, score: float = 1) -> None:
    """把某个能力点打进掌握度低谷：分数 1（最低），此后它就是全库最弱。"""
    with session_scope() as db:
        topic = ensure_topic(db, name)
        assert topic is not None and topic.id is not None
        apply_observation(db, topic_id=int(topic.id), score=score, source="test", note="M18 测试")


def _create_session(client: TestClient, *, weak: bool, mix: dict | None = None) -> dict:
    body = {
        "max_questions": 4,
        "question_count": 4,
        "question_mix": mix if mix is not None else {"uploaded": 1},
        "weak_questions": weak,
    }
    response = client.post("/api/sessions", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _picked_texts(created: dict) -> list[str]:
    return [question["text"] for question in (created["plan"].get("questions") or [])]


def test_weak_pick_prefers_questions_matching_weakest_topic(client: TestClient) -> None:
    """★ 核心：最弱技能点是「亚像素定位」，weak 抽题必须优先抽到带该标签的题。"""
    _upload(client, filename="weak面试.md", content=WEAK_QUESTION_DOC)
    _make_topic_weak(name=WEAK_TAG)

    created = _create_session(client, weak=True)
    plan = created["plan"]

    assert plan.get("weak_questions") is True, "plan 要如实记录这场是薄弱点面试"
    texts = _picked_texts(created)
    assert len(texts) == 4, "不管 weak 不 weak，都该把题抽满"
    assert any(WEAK_TAG in text for text in texts), f"薄弱点面试却没抽到薄弱标签题：{texts}"


def test_weak_pick_fills_count_when_no_mastery_records(client: TestClient) -> None:
    """没有掌握度记录（新用户）时回退默认薄弱方向，仍然把题抽满。"""
    _upload(client, filename="weak面试.md", content=WEAK_QUESTION_DOC)
    created = _create_session(client, weak=True)
    assert len(_picked_texts(created)) == 4, "没有掌握度也不该让面试开不起来"


def test_weak_without_mix_is_free_mode(client: TestClient) -> None:
    """weak 只作用于「从题库抽题」；不传 question_mix 时仍是自由出题（不抽题库）。"""
    response = client.post("/api/sessions", json={"max_questions": 3, "weak_questions": True})
    assert response.status_code == 201, response.text
    plan = response.json()["plan"]
    assert "questions" not in plan, "自由出题模式不该有固定的题库题"


def test_non_weak_pick_ignores_mastery(client: TestClient) -> None:
    """对照组：不开 weak 时按题源占比抽，不保证命中薄弱点，plan 也不报 weak_questions。"""
    _upload(client, filename="weak面试.md", content=WEAK_QUESTION_DOC)
    _make_topic_weak(name="九点标定", score=1)
    created = _create_session(client, weak=False)
    assert created["plan"].get("weak_questions") is False
    assert len(_picked_texts(created)) == 4


WEAK_QUESTION_DOC = """# M18 薄弱点面试测试

**Q1【亚像素定位】为什么用亚像素定位而不直接用整像素？**

因为亚像素在插值后给出小数精度的边缘/角点坐标，配合合适的拟合方法能把定位重复性从像素级提到亚像素级。

**Q2【九点标定】九点标定解决什么问题？**

建立图像坐标与机器人坐标的映射，标定完机械手才能按图像里算出的位置去抓。

**Q3【打光方式】低角度光适合什么缺陷？**

适合凸显表面划痕、凹陷等有高度差的缺陷，让缺陷处亮度突变。

**Q4【手眼标定】眼在手上和眼在手外区别？**

眼在手上时相机跟着手走，标定求解的是相机与末端的变换；眼在手外时相机固定，求解相机与基座的变换。
"""
