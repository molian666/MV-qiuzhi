"""在 DSH 沙箱下跑 pytest 的包装器。

沙箱只为"本次命令里新建的路径"授予写权限，而 pytest 在会话结束时会对
basetemp 做一次 `scandir` 清理，那一步会被拒绝（我们只是来跑测试的，
不需要它清理）。所以这里把 `cleanup_dead_symlinks` 变成空操作，
再把 basetemp 交给调用方传入的、刚刚创建好的那个目录。

这不是绕过测试隔离：每个用例仍然拿到自己的 `tmp_path`。
"""

from __future__ import annotations

import sys

import pytest
import _pytest.tmpdir as tmpdir_module

tmpdir_module.cleanup_dead_symlinks = lambda *args, **kwargs: None  # type: ignore[assignment]

sys.exit(pytest.main(sys.argv[1:]))
