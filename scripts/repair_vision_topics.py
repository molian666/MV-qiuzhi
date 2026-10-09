"""一次性修复：把导入时被 `[:40]` 截断的分类名合并回干净的父主题。

**问题现场**：整理过的面经里，章节标题可能很长，例如

    ## 视觉软件平台（VisionPro / VisionMaster / Halcon / VM 二开 / VP 脚本 / 授权）

导入代码当时写的是 `title.strip()[:40]`，于是它被截成

    视觉软件平台（VisionPro / VisionMaster / Halcon

而分类串里的 ` / ` 表示"父 / 子"，所以界面上就出现了十来个
"视觉软件平台（VisionPro / VM 二开" 这样的碎片主题。

**修复判据**（只认这一个指纹，不猜别的）：分类的**父名里有不配对的左括号** ⇒
它是被截断的产物 ⇒ 把父名截到那个括号之前。这样：

    视觉软件平台（VisionPro / VM 二开   →   视觉软件平台 / VM 二开
    视觉软件平台（VisionPro             →   视觉软件平台

不动题目正文、答案、要点、标记，只改分类名。导入代码本身已经修好（上限 120 字 +
清理残缺括号），所以**这个脚本只处理历史数据**。

用法：
    backend\\.venv\\Scripts\\python.exe scripts\\repair_vision_topics.py            # 只打印会怎么改
    backend\\.venv\\Scripts\\python.exe scripts\\repair_vision_topics.py --apply    # 真写入
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(r"D:\VibeCoding\mianshi\backend")))

from sqlmodel import select  # noqa: E402

from app.db import session_scope  # noqa: E402
from app.models.core import QuestionBank  # noqa: E402
from app.vision_service import TOPIC_SEPARATOR, is_deleted  # noqa: E402

#: 分类名带这些前缀的都不是分类（跟 vision_service 里的约定一致）
SKIP_PREFIXES = ("上传:", "标记:")


def _truncated_parent(category: str) -> str | None:
    """分类名是不是"被截断的"？是就返回干净的父名，否则 None。

    判据只有一个：**左括号没有对应的右括号**（正常标题不会这样收尾）。

    为什么整段塌回父名、而不是保留最后一段当子主题：被截断的标题**无法可靠还原**。
    实测那一串是 `视觉软件平台（VisionPro / VisionMaster / Halcon / VM 二开 / …`——
    `VM 二开` 既可能是章节里列举的平台版本，也可能是题目括号里的真子主题，分不出来。
    分不出来就别猜：全部塌成「视觉软件平台」，至少父主题是确定正确的
    （题目的正文、答案、要点、标记都不动）。
    """
    for left, right in (("（", "）"), ("(", ")")):
        if category.count(left) > category.count(right) and left in category:
            cleaned = category[: category.rfind(left)].strip()
            return cleaned or None
    return None


def repair_row(tags: list[str]) -> tuple[list[str], str, str] | None:
    """返回 `(新 tags, 旧分类, 新分类)`；不需要改就返回 None。"""
    category = next((tag for tag in tags if not tag.startswith(SKIP_PREFIXES)), "")
    if not category:
        return None
    cleaned = _truncated_parent(category)
    if cleaned is None or cleaned == category:
        return None
    new_tags = [cleaned if tag == category else tag for tag in tags]
    return new_tags, category, cleaned


def main() -> int:
    apply = "--apply" in sys.argv
    changes: dict[tuple[str, str], int] = {}
    touched = 0

    with session_scope() as db:
        rows = list(db.exec(select(QuestionBank)).all())
        for row in rows:
            if is_deleted(row):
                continue  # 墓碑不参与（列表本来就不显示）
            result = repair_row(list(row.tags or []))
            if result is None:
                continue
            new_tags, old, new = result
            changes[(old, new)] = changes.get((old, new), 0) + 1
            touched += 1
            if apply:
                row.tags = new_tags  # 赋新列表：JSON 列原地改 SQLAlchemy 检测不到
                db.add(row)
        if apply and touched:
            db.commit()

    if not changes:
        print("没有需要修复的分类（历史数据已经干净）")
        return 0

    print(f"{'已修复' if apply else '将会修复'} {touched} 道题的分类，涉及 {len(changes)} 条改名：")
    for (old, new), count in sorted(changes.items(), key=lambda item: -item[1]):
        print(f"  {count:>4} 道  {old}  →  {new}")
    if not apply:
        print("\n（这只是预览。确认无误后加 --apply 真正写入）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
