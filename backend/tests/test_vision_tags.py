"""题目标签编辑（M13 补充）：用户自己给题目添加 / 移除「高频」「易错」等标记。

背景：题库页的标签（高频 / 易错 / 文档自带的标注）以前是**只读**的——
内置题和上传题带什么就显示什么。需求是让用户能自己为题目打标记：
预设「高频」「易错」一键切换，也能自定义标签，还能再摘掉。

实现要点（`tags` 一列靠前缀区分三类信息，见 `vision_service._TAG_MARK_PREFIX`）：

- 只动 `标记:` 那一类：分类（tags 的第一个元素）与「上传:」来源是题目自身的
  属性，不该被"打标签"改到；
- 添加的标记**只追加到末尾**——插到前面会把第一个分类挤掉，
  显示时标记就会被当成分类；
- 保留值不允许当用户标签（空串 / 超长 / 「上传:」「标记:」开头 /
  「已删除」「草稿」/ 含「 / 」分隔符），防止破坏三类区分与系统约定；
- 移除不存在的标签是静默的：重复点击不报错。

防的回归：

- 「高频」「易错」是**题目性质**，不能混进主题 / 分类列表（meta）；
- 上传题的「上传:文件名」来源标记不能被标签编辑误删。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.db import session_scope
from app.models.core import QuestionBank
from app.vision_service import reset_seed_flag


@pytest.fixture(autouse=True)
def _fresh_seed_flag() -> Iterator[None]:
    """播种标记是进程级的，跨测试会残留；数据目录却是每个测试一份。"""
    reset_seed_flag()
    yield
    reset_seed_flag()


def first_builtin(client: TestClient) -> dict:
    """取一道内置题（题库页第一条）作测试对象。"""
    return client.get("/api/vision/questions").json()[0]


def set_tags(client: TestClient, question_id: int, *, add=None, remove=None) -> dict:
    response = client.post(
        f"/api/vision/questions/{question_id}/tags",
        json={"add": add or [], "remove": remove or []},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_add_and_remove_preset_tags(client: TestClient) -> None:
    """「高频」「易错」一键打上、再一键摘下；分类始终不动。"""
    item = first_builtin(client)
    qid, category = item["id"], item["category"]

    updated = set_tags(client, qid, add=["高频", "易错"])
    assert "高频" in updated["tags"]
    assert "易错" in updated["tags"]
    assert updated["category"] == category  # 分类不能被标签编辑改掉

    # 摘掉一个：另一个还在，分类还是不动
    updated = set_tags(client, qid, remove=["高频"])
    assert "高频" not in updated["tags"]
    assert "易错" in updated["tags"]
    assert updated["category"] == category

    # 列表与详情跟接口返回一致（前端改完标签要用它们刷新）
    listed = client.get("/api/vision/questions").json()
    row = next(row for row in listed if row["id"] == qid)
    assert "易错" in row["tags"] and "高频" not in row["tags"]
    detail = client.get(f"/api/vision/questions/{qid}").json()
    assert "易错" in detail["tags"] and "高频" not in detail["tags"]


def test_custom_tag_add_and_remove(client: TestClient) -> None:
    """自定义标签（如「背下来」）能加也能摘。"""
    item = first_builtin(client)
    qid = item["id"]

    updated = set_tags(client, qid, add=["背下来"])
    assert "背下来" in updated["tags"]

    updated = set_tags(client, qid, remove=["背下来"])
    assert "背下来" not in updated["tags"]


def test_add_duplicate_is_idempotent(client: TestClient) -> None:
    """同一个标签连加两次不会重复——重复点击不该让列表里冒出两个「高频」。"""
    item = first_builtin(client)
    qid = item["id"]

    set_tags(client, qid, add=["高频"])
    updated = set_tags(client, qid, add=["高频"])
    assert [tag for tag in updated["tags"] if tag == "高频"] == ["高频"]


def test_reserved_or_invalid_tags_are_ignored(client: TestClient) -> None:
    """会破坏「来源 / 标记 / 分类」区分、或撞系统保留约定的取值一律被忽略。"""
    item = first_builtin(client)
    qid = item["id"]
    before = set_tags(client, qid).get("tags", [])

    updated = set_tags(
        client,
        qid,
        add=["", "   ", "上传:某文件", "标记:高频", "已删除", "草稿", "含 / 分隔符", "超" * 21],
    )
    assert updated["tags"] == before  # 一条都没加进去


def test_unknown_and_deleted_question_404(client: TestClient) -> None:
    """不存在的题、以及已删除（墓碑）的题，标签编辑返回 404。"""
    assert (
        client.post(
            "/api/vision/questions/999999/tags", json={"add": ["高频"]}
        ).status_code
        == 404
    )

    item = first_builtin(client)
    qid = item["id"]
    assert client.delete(f"/api/vision/questions/{qid}").json() == {"deleted": 1}
    assert (
        client.post(f"/api/vision/questions/{qid}/tags", json={"add": ["高频"]}).status_code
        == 404
    )


def test_marks_never_enter_topics(client: TestClient) -> None:
    """打上「高频」后，它不能混进主题 / 分类列表（那是筛选用的，不是题目性质）。"""
    item = first_builtin(client)
    qid = item["id"]
    set_tags(client, qid, add=["高频"])

    info = client.get("/api/vision/meta").json()
    assert "高频" not in info["categories"]
    assert all("高频" not in topic["name"] for topic in info["topics"])


def test_uploaded_question_keeps_source_marker(client: TestClient) -> None:
    """上传题的「上传:文件名」来源标记是来源信息，标签编辑不能把它删掉。"""
    response = client.post(
        "/api/vision/import",
        files={
            "file": (
                "标签测试.md",
                "# 标签测试\n\n光源频闪是为什么？\n供电纹波。\n".encode("utf-8"),
                "text/plain",
            )
        },
    )
    assert response.status_code == 200, response.text

    uploaded = client.get("/api/vision/questions", params={"role": "uploaded"}).json()
    assert uploaded, "应该导入了至少一道题"
    item = uploaded[0]
    qid = item["id"]

    # 上传批次信息在 uploads 里以「上传:文件名」的形式存在库里
    with session_scope() as db:
        row = db.exec(select(QuestionBank).where(QuestionBank.id == qid)).first()
        assert row is not None
        source_marker = next(tag for tag in (row.tags or []) if tag.startswith("上传:"))

    # 加个标签再摘掉：来源标记必须原样还在（库里和接口两个层面都查一遍）
    set_tags(client, qid, add=["高频"])
    set_tags(client, qid, remove=["高频"])

    with session_scope() as db:
        row = db.exec(select(QuestionBank).where(QuestionBank.id == qid)).first()
        assert row is not None
        assert source_marker in (row.tags or [])

    detail = client.get(f"/api/vision/questions/{qid}").json()
    assert "高频" not in detail["tags"]  # 接口返回里标记已摘掉
    assert detail["category"]  # 分类仍在，来源标记不占分类位


def test_filter_by_mark_tag(client: TestClient) -> None:
    """按标签筛题：多选取交集——同时带「高频」和「易错」的题才命中。

    打上的标签应该能立刻当筛选条件用，否则"给题打标签"就只是换个颜色。
    """
    items = client.get("/api/vision/questions", params={"limit": 10}).json()
    assert len(items) >= 3, "内置题库应该至少有 3 道题可用来做交集测试"
    a, b, c = items[0], items[1], items[2]
    set_tags(client, a["id"], add=["高频"])
    set_tags(client, b["id"], add=["易错"])
    set_tags(client, c["id"], add=["高频", "易错"])

    only_high = client.get("/api/vision/questions", params={"tag": "高频"}).json()
    ids = {row["id"] for row in only_high}
    assert {a["id"], c["id"]} <= ids, "带「高频」的题必须都被筛出来"
    assert b["id"] not in ids, "只带「易错」的题不该出现在「高频」的结果里"

    both = client.get(
        "/api/vision/questions", params=[("tag", "高频"), ("tag", "易错")]
    ).json()
    assert {row["id"] for row in both} == {c["id"]}, "多选是交集，不是并集"

    # 摘掉一个标签后，交集筛子要立刻放它走
    set_tags(client, c["id"], remove=["易错"])
    both = client.get(
        "/api/vision/questions", params=[("tag", "高频"), ("tag", "易错")]
    ).json()
    assert c["id"] not in {row["id"] for row in both}


def test_keyword_search_matches_mark_tags(client: TestClient) -> None:
    """关键词搜索不只匹配题干，也匹配标签：搜「高频」能找到带该标签的题。

    需求是"搜索也应该可以选标签搜索"——如果关键词框里输入「高频」搜不出
    高频题（题干往往不带这两个字），用户会以为标签白打了。
    """
    items = client.get("/api/vision/questions", params={"limit": 50}).json()
    target = next(
        item
        for item in items
        if "高频" not in item["text"] and "高频" not in item["tags"]
    )
    set_tags(client, target["id"], add=["高频"])

    found = client.get("/api/vision/questions", params={"keyword": "高频"}).json()
    ids = {row["id"] for row in found}
    assert target["id"] in ids, "题干没有『高频』二字、但带了该标签的题，必须被搜出来"

    # 摘掉标签后，同样的搜索就不再命中它（标签是搜索范围的一部分，不是永久绑定）
    set_tags(client, target["id"], remove=["高频"])
    found = client.get("/api/vision/questions", params={"keyword": "高频"}).json()
    assert target["id"] not in {row["id"] for row in found}


def test_meta_lists_mark_tags_with_counts(client: TestClient) -> None:
    """meta 聚合出标记标签及各自题数（筛选器按钮就长这样），按题数降序。"""
    items = client.get("/api/vision/questions", params={"limit": 10}).json()
    set_tags(client, items[0]["id"], add=["高频"])
    set_tags(client, items[1]["id"], add=["高频", "必背"])

    mark_tags = client.get("/api/vision/meta").json()["mark_tags"]
    counts = {entry["name"]: entry["count"] for entry in mark_tags}
    assert counts["高频"] == 2
    assert counts["必背"] == 1
    # 覆盖面大的排前面：高频（2 题）必须在必背（1 题）之前
    assert mark_tags[0]["name"] == "高频"
