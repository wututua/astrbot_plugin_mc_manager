"""Recognize capacity failures without confusing them with access or path errors."""

from __future__ import annotations

import errno
import sqlite3


def capacity_issue(error: BaseException) -> str | None:
    """Return space/quota for OS or SQLite failures, including wrapped causes."""
    visited = set()
    current = error
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if isinstance(current, OSError):
            quota = getattr(errno, "EDQUOT", None)
            if quota is not None and current.errno == quota:
                return "quota"
            if current.errno == errno.ENOSPC or getattr(current, "winerror", None) in {39, 112}:
                return "space"
        if isinstance(current, sqlite3.Error):
            code = getattr(current, "sqlite_errorcode", None)
            if (isinstance(code, int) and code & 255 == sqlite3.SQLITE_FULL) or str(
                current
            ).casefold() == "database or disk is full":
                return "space"
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
    return None


def capacity_message(error: BaseException, operation="保存配置") -> str:
    reason = "存储配额已用尽" if capacity_issue(error) == "quota" else "写入位置可用空间不足，或存储配额已用尽"
    return f"{operation}失败：{reason}。请在运行 AstrBot 的服务器上释放对应磁盘空间或调整配额后重试。"
