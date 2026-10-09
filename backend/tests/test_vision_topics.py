"""题库主题：用户反馈"导入的面试题主题又多又细"，这里把三条成因各自钉住。

背景（实测数据）：导入一份整理过的面经后，题库页出现 **81 个主题**，其中 40 多个
只有 1 道题——铺在一排标签里根本没法用。三个不同的原因：

1. `meta()` 只挡了「上传:」前缀，**「标记:高频」「标记:易错」也混进了主题列表**；
2. 主题过滤是**精确匹配**，所以"按父主题筛"会把它的子主题全漏掉
   （分类串是「相机与图像基础 / 相机二开」，点父主题一道都筛不出来）；
3. 导入时章节标题被 `.strip()[:40]` **硬截断**，长标题碎成十来个看起来像子主题的碎片。
"""

from __future__ import annotations

from app.models.core import QuestionBank
from app.vision.import_doc import _clean_section_title, parse_document
from app.vision_service import (
    TOPIC_SEPARATOR,
    get_question,
    list_questions,
    meta,
)


def _add(db, text: str, category: str, *, tags: list[str] | None = None) -> None:
    db.add(
        QuestionBank(
            type="development",
            text=text,
            answer="答案",
            tags=[category, *(tags or [])],
            source="builtin:vision",
            difficulty=3,
        )
    )


def test_marks_are_not_topics(client) -> None:
    """「标记:高频」是题目性质，不是主题——以前它会出现在主题列表里。"""
    from app.db import session_scope

    with session_scope() as db:
        _add(db, "标记题测试 A", "主题甲", tags=["标记:高频"])
        _add(db, "标记题测试 B", "主题甲", tags=["标记:易错"])
        db.commit()

    with session_scope() as db:
        info = meta(db)

    assert "主题甲" in info["categories"]
    assert not [item for item in info["categories"] if item.startswith("标记:")]
    names = [topic["name"] for topic in info["topics"]]  # type: ignore[index]
    assert "主题甲" in names
    assert not [name for name in names if name.startswith("标记:")]


def test_parent_topic_filter_includes_children(client) -> None:
    """点父主题必须能筛出它的子主题——这是"81 个主题"能不能用起来的关键。"""
    from app.db import session_scope

    parent = "父主题测试"
    with session_scope() as db:
        _add(db, "父主题直挂的题", parent)
        _add(db, "子主题 A 的题", f"{parent}{TOPIC_SEPARATOR}子 A")
        _add(db, "子主题 B 的题", f"{parent}{TOPIC_SEPARATOR}子 B")
        _add(db, "别的方向的题", "另一个主题")
        db.commit()

    with session_scope() as db:
        by_parent = list_questions(db, category=parent, limit=50)
        texts = {item["text"] for item in by_parent}
        assert {"父主题直挂的题", "子主题 A 的题", "子主题 B 的题"} <= texts
        assert "别的方向的题" not in texts

        # 子主题仍然要能精确筛（不能因为支持父主题而把精确匹配弄坏）
        by_child = list_questions(db, category=f"{parent}{TOPIC_SEPARATOR}子 A", limit=50)
        assert [item["text"] for item in by_child] == ["子主题 A 的题"]


def test_meta_topics_expose_children_with_counts(client) -> None:
    from app.db import session_scope

    parent = "两级主题测试"
    with session_scope() as db:
        _add(db, "两级 1", parent)
        _add(db, "两级 2", f"{parent}{TOPIC_SEPARATOR}细一")
        _add(db, "两级 3", f"{parent}{TOPIC_SEPARATOR}细一")
        _add(db, "两级 4", f"{parent}{TOPIC_SEPARATOR}细二")
        db.commit()

    with session_scope() as db:
        info = meta(db)

    topic = next(item for item in info["topics"] if item["name"] == parent)  # type: ignore[index]
    assert topic["count"] == 4  # 父主题的计数含全部子主题
    children = {child["name"]: child["count"] for child in topic["children"]}  # type: ignore[index]
    assert children == {"细一": 2, "细二": 1}
    assert topic["children"][0]["full"] == f"{parent}{TOPIC_SEPARATOR}细一"  # type: ignore[index]


def test_section_title_is_not_truncated_into_fake_hierarchy() -> None:
    """真机现场：`## 视觉软件平台（VisionPro / VisionMaster / Halcon / VM 二开 …）`
    被 `[:40]` 截成 `视觉软件平台（VisionPro / VisionMaster / Halcon`，
    里面那个 ` / ` 让界面把它当成"父 / 子"，同一个主题裂成十来个碎片。"""
    long_title = "视觉软件平台（VisionPro / VisionMaster / Halcon / VM 二开 / VP 脚本 / 授权 / 配方）"
    cleaned = _clean_section_title(long_title)
    assert cleaned == long_title  # 完整保留，不再截断
    assert cleaned.count("（") == cleaned.count("）")

    # 残缺括号（标题本身没写全）时才清理，且不留下孤立的左括号
    assert _clean_section_title("某个方向（没写完") == "某个方向"

    # 编号前缀照旧剥掉
    assert _clean_section_title("第三部分：相机与图像基础") == "相机与图像基础"


def test_parse_document_keeps_long_section_as_one_category() -> None:
    content = (
        "## 视觉软件平台（VisionPro / VisionMaster / Halcon / VM 二开 / VP 脚本）\n"
        "**Q1：怎么选平台？**\n"
        "看项目预算与算法复杂度。\n"
        "**Q2：VisionMaster 的脚本能力如何？**\n"
        "够用但不如 Halcon 灵活。\n"
        "**Q3：授权怎么算？**\n"
        "按加密狗或按模块。\n"
    )
    parsed = parse_document("题库.md", content)

    categories = {question.category for question in parsed.questions}
    assert len(categories) == 1
    assert "视觉软件平台（VisionPro / VisionMaster / Halcon / VM 二开 / VP 脚本）" in categories


def test_repair_script_collapses_truncated_categories() -> None:
    """一次性修复脚本的判据：**左括号不配对**就是"被 40 字截断"的指纹。

    实测被截断的标题无法可靠还原（`VM 二开` 可能是章节里列举的平台，也可能是题目的真子主题），
    所以整段塌回干净的父名——宁可少一个子主题，也不要编一个错的。
    """
    import importlib.util
    from pathlib import Path

    module_path = Path(r"D:\VibeCoding\mianshi\scripts\repair_vision_topics.py")
    spec = importlib.util.spec_from_file_location("repair_vision_topics", module_path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]

    # 真机现场的 10 种碎片名，全部塌回父名
    for broken in (
        "视觉软件平台（VisionPro / VisionMaster / Halcon",
        "视觉软件平台（VisionPro / VisionMaster / Halcon / VM 二开",
        "视觉软件平台（VisionPro / VisionMaster / Halcon / VP 模板匹配",
    ):
        result = module.repair_row([broken, "标记:高频"])
        assert result is not None, broken
        new_tags, old, new = result
        assert old == broken
        assert new == "视觉软件平台"
        assert new_tags == ["视觉软件平台", "标记:高频"]  # 标记不动

    # 正常的"父 / 子"分类不能被误改（括号是配对的）
    assert module.repair_row(["相机与图像基础 / 相机二开"]) is None
    assert module.repair_row(["人事与综合面（HR 高频问题）"]) is None
    # 没有分类的题也不动
    assert module.repair_row(["标记:高频"]) is None


def test_deleted_question_is_not_counted_in_topics(client) -> None:
    """删除过的题（墓碑）不该继续撑起一个主题——否则删完标签还在。"""
    from app.db import session_scope
    from app.vision_service import delete_question

    with session_scope() as db:
        _add(db, "墓碑主题唯一的题", "墓碑主题")
        db.commit()
        row = db.exec(
            __import__("sqlmodel").select(QuestionBank).where(QuestionBank.text == "墓碑主题唯一的题")
        ).first()
        assert row is not None
        question_id = int(row.id or 0)

    with session_scope() as db:
        assert delete_question(db, question_id) is True

    with session_scope() as db:
        info = meta(db)
        assert "墓碑主题" not in [topic["name"] for topic in info["topics"]]  # type: ignore[index]
        assert get_question(db, question_id) is None
