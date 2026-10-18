"""Read-only monitoring helpers and query metadata, independent of AstrBot."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from .message_catalog import text as message_text
from .text import safe_text

QUERY_VARIABLES = (
    ("query_source", "本次实际来源：RCON / 服务端新增日志；后者仍由本次 RCON 命令触发", "", "RCON"),
    ("query_command", "本次实际执行的命令", "", "forge tps"),
    ("query_latency_ms", "实际采集耗时，包含排队时间；缓存命中保留原耗时", "ms", "12.4"),
    ("query_checked_at", "采集完成时间，ISO 8601 / UTC", "", "2026-10-17T02:00:00+00:00"),
    ("query_cached", "是否使用短缓存：是 / 否", "", "否"),
    ("query_age_seconds", "距离本次结果采集完成的时间", "s", "0.2"),
    ("query_error", "失败原因；成功时为空", "", "服务端返回空响应"),
    ("query_warning", "补充说明；正常时为空", "", "在线人数未能解析"),
    ("rcon_online", "本次是否验证 RCON：是 / 否 / 未验证", "", "是"),
    ("tps_source", "spark 查询来源：rcon / log / unavailable", "", "log"),
)
STATUS_VARIABLES = (
    ("status", "本次状态查询结果，不等同于游戏端口检测", "", "在线（RCON 查询成功）"),
    ("players_online", "list 回复中的在线人数；无法解析时不填 0", "人", "2"),
    ("players_max", "list 回复中的人数上限", "人", "20"),
    ("players_names", "list 回复中的玩家列表；明确 0 人时为 无", "", "Steve, Alex"),
    ("players_raw", "去除颜色码后的原始玩家查询回复", "", "There are 2 of a max of 20 players online: Steve, Alex"),
)
# Retain only the retired placeholder as a missing value so old templates load.
MONITOR_FIELDS = frozenset(row[0] for row in (*QUERY_VARIABLES, *STATUS_VARIABLES)) | {"tps_file_age_seconds"}
ERROR = re.compile(
    r"(?:^|\n)\s*(?:\[[^\]\n]+\]\s*)?(?:"
    r"unknown (?:or incomplete )?command|unknown command|incorrect argument|"
    r"you (?:do not|don't) have permission|insufficient permission|"
    r"未知(?:或不完整的)?(?:的)?(?:命令|指令)|命令不完整|参数错误|没有权限|权限不足)",
    re.I,
)


def command_error(text):
    return bool(ERROR.search(safe_text(text)))


def parse_players(text, missing="暂无数据"):
    raw = safe_text(text, 6000).strip()
    values = dict(players_online=missing, players_max=missing, players_names=missing, players_raw=raw)
    patterns = (
        r"There are\s+(\d+)\s+(?:of a max of|out of (?:a )?maximum(?: of)?)\s+(\d+)\s+players online[.:：]?\s*(.*)",
        r"当前有\s*(\d+)\s*(?:名|个)?玩家在线[，,；;\s]+(?:最大玩家数(?:量)?为|最多允许|最大人数为)\s*(\d+)\s*(?:人|名玩家)?[。.:：]?\s*(.*)",
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, raw, re.I | re.S)
        if match:
            online, maximum, names = match.groups()
            values.update(
                players_online=online,
                players_max=maximum,
                players_names=names.strip() or (message_text("players_none") if online == "0" else missing),
            )
            break
    return values


@dataclass(frozen=True)
class QueryInfo:
    source: str = message_text("source_unavailable")
    command: str = ""
    checked_at: str = ""
    measured: float = 0
    latency_ms: float = 0
    cached: bool = False
    error: str = ""
    warning: str = ""
    rcon_online: bool | None = None
    raw: str = ""


class QueryResult(str):
    """A string-compatible result carrying its own immutable query context."""

    def __new__(cls, value, info=None):
        instance = super().__new__(cls, value)
        instance.info = info or QueryInfo()
        instance.message = value.message if isinstance(value, QueryResult) else value
        return instance

    def localize(self, settings, depth=0):
        return settings.localize(self.message, depth + 1)

    def cached_copy(self):
        return QueryResult(self, replace(self.info, cached=True))


def finish_query(result, started):
    if not isinstance(result, QueryResult):
        return result
    return QueryResult(
        result,
        replace(
            result.info,
            checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            measured=time.monotonic(),
            latency_ms=round((time.monotonic() - started) * 1000, 1),
        ),
    )


def query_values(result, missing="暂无数据"):
    info = result.info if isinstance(result, QueryResult) else QueryInfo()
    age = max(0, time.monotonic() - info.measured) if info.measured else 0
    return dict(
        query_source=message_text("source_rcon") if info.source == "RCON" else info.source,
        query_command=info.command,
        query_latency_ms=f"{info.latency_ms:g}",
        query_checked_at=info.checked_at or missing,
        query_cached=message_text("yes") if info.cached else message_text("no"),
        query_age_seconds=f"{age:.1f}",
        query_error=info.error,
        query_warning=info.warning,
        rcon_online=message_text("unverified")
        if info.rcon_online is None
        else message_text("yes")
        if info.rcon_online
        else message_text("no"),
        tps_source="log" if info.source == "服务端新增日志" else "rcon" if info.source == "RCON" else "unavailable",
        tps_file_age_seconds=missing,
    )


def list_result(result, kind):
    if not isinstance(result, QueryResult) or result.info.error or not result.info.raw:
        return result
    return QueryResult(message_text("status_online") if kind == "status" else result.info.raw, result.info)
