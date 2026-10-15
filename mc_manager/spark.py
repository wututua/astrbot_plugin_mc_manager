"""Parse spark tps without inventing values or relying on fixed line numbers."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .text import safe_text

# (variable, explanation, unit, example from the user's spark response)
METRICS = (
    ("tps_5s", "最近 5 秒的 TPS", "TPS", "13.24"),
    ("tps_10s", "最近 10 秒的 TPS", "TPS", "13.37"),
    ("tps_1m", "最近 1 分钟的 TPS", "TPS", "15.95"),
    ("tps_5m", "最近 5 分钟的 TPS", "TPS", "19.03"),
    ("tps_15m", "最近 15 分钟的 TPS", "TPS", "19.67"),
    ("mspt_10s_min", "最近 10 秒的最小 Tick 耗时", "ms", "69.1"),
    ("mspt_10s_med", "最近 10 秒的 Tick 耗时中位数", "ms", "74.3"),
    ("mspt_10s_p95", "最近 10 秒的 Tick 耗时第 95 百分位", "ms", "79.6"),
    ("mspt_10s_max", "最近 10 秒的最大 Tick 耗时", "ms", "103.2"),
    ("mspt_1m_min", "最近 1 分钟的最小 Tick 耗时", "ms", "31.1"),
    ("mspt_1m_med", "最近 1 分钟的 Tick 耗时中位数", "ms", "64.6"),
    ("mspt_1m_p95", "最近 1 分钟的 Tick 耗时第 95 百分位", "ms", "77.0"),
    ("mspt_1m_max", "最近 1 分钟的最大 Tick 耗时", "ms", "103.5"),
    ("cpu_system_10s", "最近 10 秒的系统整体 CPU 使用率", "%", "33"),
    ("cpu_system_1m", "最近 1 分钟的系统整体 CPU 使用率", "%", "35"),
    ("cpu_system_15m", "最近 15 分钟的系统整体 CPU 使用率", "%", "35"),
    ("cpu_process_10s", "最近 10 秒的 MC Java 进程 CPU 使用率", "%", "14"),
    ("cpu_process_1m", "最近 1 分钟的 MC Java 进程 CPU 使用率", "%", "14"),
    ("cpu_process_15m", "最近 15 分钟的 MC Java 进程 CPU 使用率", "%", "15"),
)
HELPERS = (
    ("server", "服务器显示名称", "", "服务器 1"),
    ("server_id", "服务器配置 ID", "", "server1"),
    ("tps_command", "配置的控制台命令（RCON 查询时使用）", "", "spark tps"),
    ("result", "去掉颜色代码后的原始响应（兼容旧模板）", "", "TPS from last ..."),
    ("tps_raw", "去掉颜色代码后的原始响应，与 result 相同", "", "TPS from last ..."),
    ("tps_parse_status", "完整、部分或未解析", "", "完整"),
    ("tps_missing_fields", "缺失或无效指标的变量名列表", "", "mspt_10s_min, mspt_10s_med"),
    ("tps_warning", "根据 tps_warning 模板生成的缺失提示；完整时为空", "", "部分 spark 数据不可用…"),
)
METRIC_FIELDS = frozenset(row[0] for row in METRICS)
SPARK_FIELDS = METRIC_FIELDS | frozenset(row[0] for row in HELPERS)
DEFAULT_TPS_TEMPLATE = (
    "[{server}] spark 性能\n"
    "TPS（5秒 / 10秒 / 1分钟 / 5分钟 / 15分钟）：\n"
    "{tps_5s} / {tps_10s} / {tps_1m} / {tps_5m} / {tps_15m}\n"
    "Tick 耗时（毫秒，最小 / 中位数 / P95 / 最大）：\n"
    "最近10秒：{mspt_10s_min} / {mspt_10s_med} / {mspt_10s_p95} / {mspt_10s_max}\n"
    "最近1分钟：{mspt_1m_min} / {mspt_1m_med} / {mspt_1m_p95} / {mspt_1m_max}\n"
    "CPU（单位%，10秒 / 1分钟 / 15分钟）：\n"
    "系统：{cpu_system_10s} / {cpu_system_1m} / {cpu_system_15m}\n"
    "MC进程：{cpu_process_10s} / {cpu_process_1m} / {cpu_process_15m}"
    "{tps_warning}"
)
DEFAULT_TPS_WARNING = "\n部分 spark 数据不可用：{tps_missing_fields}\n原始输出：\n{tps_raw}"
DEFAULT_TPS_ERROR = "[{server}] spark TPS 查询失败或无法解析：\n{tps_raw}"

NUMBER = r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)"
TOKEN = rf"(?:\*?{NUMBER}|N/A|NaN|unknown|—|--)"
PERCENT_TOKEN = rf"(?:\*?{NUMBER}\s*%|(?:N/A|NaN|unknown|—|--)(?:\s*%)?)"
HEADER = re.compile(
    r"\b(?P<kind>TPS|Tick\s+durations(?:\s*\((?P<stats>[^)]*)\))?|CPU\s+usage)"
    r"\s+from\s+last\s+(?P<windows>[^:\r\n]+):",
    re.IGNORECASE,
)
STAT_NAMES = {"min": "min", "med": "med", "median": "med", "95%ile": "p95", "p95": "p95", "max": "max"}


def variables_table() -> list[dict[str, str]]:
    return [
        {
            "name": name,
            "placeholder": "{" + name + "}",
            "description": description,
            "unit": unit,
            "example": example,
            "category": "指标" if name in METRIC_FIELDS else "辅助",
        }
        for name, description, unit, example in (*METRICS, *HELPERS)
    ]


def _number(token: str) -> str | None:
    value = token.strip().removeprefix("*").strip()
    value = re.sub(r"\s*(?:%|ms)\s*$", "", value, flags=re.IGNORECASE).strip()
    if not re.fullmatch(NUMBER, value) or value.startswith("-"):
        return None
    return value.removeprefix("+")


def _windows(text: str) -> list[str]:
    windows = [part.strip().lower().replace(" ", "") for part in text.split(",")]
    if not windows or len(windows) > 10 or len(set(windows)) != len(windows):
        return []
    if any(not re.fullmatch(r"\d+[sm]", window) for window in windows):
        return []
    return windows


def _vector(pattern: str, text: str) -> str | None:
    # Requiring exact comma/slash/semicolon counts prevents a truncated row
    # from moving another window's value into the missing position.
    for match in re.finditer(rf"(?<![\w.,/+\-])(?P<vector>{pattern})(?![\w.]|\s*[,/;])", text, re.IGNORECASE):
        if text[: match.start()].rstrip().endswith((",", "/", ";")):
            continue
        return match.group("vector")
    return None


@dataclass(frozen=True)
class SparkMetrics:
    raw: str
    values: dict[str, str]
    missing: tuple[str, ...]

    @property
    def status(self) -> str:
        if not self.missing:
            return "完整"
        return "未解析" if len(self.missing) == len(METRIC_FIELDS) else "部分"


def parse_spark_tps(raw: str, missing_value: str = "暂无数据") -> SparkMetrics:
    clean = safe_text(raw).strip()
    # Server/spark line prefixes are not measurement values. Remove them only
    # for parsing; the raw variable retains the readable original response.
    text = re.sub(r"\[[^\]\r\n]{1,100}\]\s*", "", clean)
    headers = list(HEADER.finditer(text))
    parsed: dict[str, str] = {}
    for index, header in enumerate(headers):
        windows = _windows(header.group("windows"))
        if not windows:
            continue
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        body = text[header.end() : end]
        kind = header.group("kind").lower()
        if kind == "tps":
            vector = _vector(rf"{TOKEN}(?:\s*,\s*{TOKEN}){{{len(windows) - 1}}}", body)
            if vector is None:
                continue
            for window, token in zip(windows, vector.split(",")):
                value = _number(token)
                key = "tps_" + window
                if key in METRIC_FIELDS and value is not None:
                    parsed[key] = value
        elif kind.startswith("tick"):
            raw_stats = re.sub(r"\s*ms\s*$", "", header.group("stats") or "", flags=re.IGNORECASE)
            stats = [STAT_NAMES.get(part.strip().lower()) for part in raw_stats.split("/")]
            if not stats or any(stat is None for stat in stats) or len(set(stats)) != len(stats):
                continue
            duration = rf"{TOKEN}(?:\s*ms)?"
            group = rf"{duration}(?:\s*/\s*{duration}){{{len(stats) - 1}}}"
            vector = _vector(rf"{group}(?:\s*;\s*{group}){{{len(windows) - 1}}}", body)
            if vector is None:
                continue
            for window, measurements in zip(windows, vector.split(";")):
                for stat, token in zip(stats, re.findall(duration, measurements, re.IGNORECASE)):
                    value = _number(token)
                    key = f"mspt_{window}_{stat}"
                    if key in METRIC_FIELDS and value is not None:
                        parsed[key] = value
        elif kind.startswith("cpu"):
            row = rf"(?<![\w.,/+\-])(?P<vector>{PERCENT_TOKEN}(?:\s*,\s*{PERCENT_TOKEN}){{{len(windows) - 1}}})\s*\((?P<scope>system|process)\)"
            for match in re.finditer(row, body, re.IGNORECASE):
                if body[: match.start()].rstrip().endswith(","):
                    continue
                for window, token in zip(windows, match.group("vector").split(",")):
                    value = _number(token)
                    key = f"cpu_{match.group('scope').lower()}_{window}"
                    if key in METRIC_FIELDS and value is not None:
                        parsed[key] = value
    missing = tuple(name for name, *_ in METRICS if name not in parsed)
    values = {name: parsed.get(name, missing_value) for name, *_ in METRICS}
    return SparkMetrics(clean, values, missing)
