"""统一时间来源。

全项目只用 **aware UTC**：
SQLModel 0.0.46 起把 `datetime` 字段默认映射为 `UTCDateTime`，naive 值会被直接拒绝
（`Datetime values must have timezone information`），所以这里统一产出带 tzinfo 的 UTC 时间。
另外 `datetime.utcnow()` 在 Python 3.12+ 已废弃，不要再用。
"""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
