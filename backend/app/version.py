"""本地版本信息（「检测更新」的数据来源）。

项目是 git 仓库，所以"我现在的版本"就是本地 HEAD 的 commit sha：
前端拿它跟 GitHub 远程最新 commit 对比，不同就说明有新版本可以拉取。
git 命令不可用（例如以源码包方式分发、没有 .git）时返回 None，
前端会隐藏更新检查入口，而不是报错。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

#: 仓库根目录（backend/app/version.py → backend → 仓库根）
REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(args: list[str]) -> str:
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    return (result.stdout or "").strip()


def read_local_version() -> dict[str, Any] | None:
    """读本地 git 状态；不是 git 仓库或 git 不可用时返回 None。"""
    try:
        commit = _git(["rev-parse", "--short", "HEAD"])
        if not commit:
            return None
        date = _git(["log", "-1", "--format=%cI"])
        remote = _git(["remote", "get-url", "origin"])
    except (OSError, subprocess.SubprocessError):
        return None

    return {
        "local_commit": commit,
        "local_date": date,
        "repo_url": remote if remote.startswith(("https://", "git@")) else "",
    }
