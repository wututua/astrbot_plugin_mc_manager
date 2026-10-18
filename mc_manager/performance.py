"""Normalize common TPS reports without inventing measured TPS or process data."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from .message_catalog import join_text
from .message_catalog import text as message_text
from .monitoring import query_values
from .spark import DEFAULT_TPS_TEMPLATE, METRIC_FIELDS, parse_spark_tps
from .text import safe_text

PERFORMANCE_VARIABLES = (
    (
        "perf_format",
        "识别的格式：spark / Paper/Spigot / Forge/NeoForge / tick query / 自定义 / 未识别",
        "",
        "Forge/NeoForge",
    ),
    ("perf_parse_status", "跨核心解析结果：已解析 / 部分 / 未解析", "", "已解析"),
    ("perf_tps", "总体或最短可用窗口的 TPS；目标 Tick 速率不会填入", "TPS", "19.98"),
    ("perf_tps_windows", "本次回复实际提供的 TPS 窗口，保持标题顺序", "", "1m / 5m / 15m"),
    ("perf_tps_values", "与窗口对应的 TPS，保留 * 标记和缺失位置", "TPS", "20.0* / 19.9 / 19.8"),
    ("perf_mspt", "核心明确标注的 Tick 耗时；只写 Tick time 时不推断统计类型，不以中位数充当平均值", "ms", "12.3"),
    ("perf_target_rate", "原版目标/配置 Tick 速率，非实测 TPS", "tick/s", "20"),
    ("perf_tick_state", "原版明确报告的运行状态，未知时显示缺失值", "", "正常"),
    (
        "perf_dimensions",
        "Forge/NeoForge 所有维度与总体明细，不跨维度计算平均",
        "",
        "minecraft:overworld：TPS 20 · Tick 12 ms",
    ),
    ("perf_memory", "回复中实际提供的内存信息；未自行探测 JVM/进程", "", "1024 / 2048 MB"),
    ("perf_entities", "回复中实际提供的实体统计；保留 ticking/non-ticking 含义", "", "1327 ticking / 12 non-ticking"),
    ("perf_tps_line", "带标签的 TPS 汇总行", "", "⚡ TPS（1m / 5m / 15m）：20.0 / 19.9 / 19.8"),
    ("perf_tick_lines", "带标签的 Tick 明细，可能多行", "", "⏱ Tick 平均耗时：12.3 ms"),
    ("perf_cpu_lines", "带标签的 CPU 明细，缺失时明确说明", "", "🖥 CPU（进程）：14%"),
    ("perf_memory_line", "带标签的内存行，缺失时明确说明", "", "💾 内存：本次回复未提供"),
    ("perf_entities_line", "带标签的实体行，缺失时明确说明", "", "🧍 实体：本次回复未提供"),
    ("perf_notice", "解析说明，包含目标速率、星号或缺失提醒", "", "\n说明：目标速率不等于实测 TPS"),
)
PERFORMANCE_VARIABLES += (
    ("tps_1s", "回复明确标注的最近 1 秒 TPS", "TPS", "19.99"),
    ("tps_30s", "回复明确标注的最近 30 秒 TPS", "TPS", "19.95"),
    ("tps_overall", "Forge/NeoForge 明确标注的 Overall TPS，非维度平均", "TPS", "19.98"),
    ("mspt_overall", "Forge/NeoForge 明确标注的 Overall 平均 Tick 耗时", "ms", "13.5"),
    ("mspt_avg", "回复明确报告的平均 Tick 耗时；spark 中位数不填此变量", "ms", "12.3"),
    ("mspt_p50", "tick query 明确标注的第 50 百分位 Tick 耗时", "ms", "11.9"),
    ("mspt_p95", "tick query 明确标注的第 95 百分位 Tick 耗时", "ms", "22.4"),
    ("mspt_p99", "tick query 明确标注的第 99 百分位 Tick 耗时", "ms", "31.0"),
    ("cpu_usage", "自定义回复中仅标注 CPU 的数值；不假定是进程或系统", "%", "35"),
    ("memory_used", "内存已用量原始数值，需配合 memory_used_unit", "", "1024"),
    ("memory_used_unit", "内存已用量单位，保留回复原始单位", "", "MB"),
    ("memory_total", "内存总量原始数值，需配合 memory_total_unit", "", "2048"),
    ("memory_total_unit", "内存总量单位，保留回复原始单位", "", "MB"),
    ("memory_max", "回复另行明确标注的内存最大值，不能从 total 推断", "", "4096"),
    ("memory_max_unit", "内存最大值的原始单位", "", "MB"),
    ("entities_ticking", "实体统计明确标注的 ticking 数量", "个", "1327"),
    ("entities_non_ticking", "实体统计明确标注的 non-ticking 数量", "个", "12"),
    ("entities_total", "明确的实体总量，或上述两项的和，仅限回复覆盖范围", "个", "1339"),
    ("forge_dimension_count", "回复列出的维度条数，不包含 Overall 汇总行", "个", "2"),
    (
        "forge_dimensions_json",
        "全部维度与 Overall 的 JSON 数组；不可解析项为 null",
        "",
        '[{"dimension":"minecraft:overworld","tps":"20","mspt":"12.3"}]',
    ),
    ("forge_overworld_tps", "主世界明确报告的 TPS；识别 minecraft:overworld 或旧 Dim 0", "TPS", "20"),
    ("forge_overworld_mspt", "主世界明确报告的平均 Tick 耗时", "ms", "12.3"),
    ("forge_nether_tps", "下界明确报告的 TPS；识别 minecraft:the_nether 或旧 Dim -1", "TPS", "20"),
    ("forge_nether_mspt", "下界明确报告的平均 Tick 耗时", "ms", "1.2"),
    ("forge_end_tps", "末地明确报告的 TPS；识别 minecraft:the_end 或旧 Dim 1", "TPS", "19.8"),
    ("forge_end_mspt", "末地明确报告的平均 Tick 耗时", "ms", "2.5"),
)
PERFORMANCE_FIELDS = frozenset(row[0] for row in PERFORMANCE_VARIABLES)

NUM = r"[+]?\d+(?:\.\d+)?(?![\d.eE+-])"
LOCAL_NUM = r"[+]?\d+(?:[.,]\d+)?(?![\d.,eE+-])"
SCALAR = rf"(?:\*?\s*{NUM}\s*\*?|N/A|NaN|unknown|--|—)"
WINDOW = re.compile(r"\bTPS\s+(?:from\s+last\s+([^:\r\n]+)|\(([^)\r\n]+)\))\s*[:：]", re.I)
LABELS = {"5s": "5秒", "10s": "10秒", "1m": "1分钟", "5m": "5分钟", "15m": "15分钟"}


LABELS = {window: message_text("perf_window_" + window) for window in ("1s", "5s", "10s", "30s", "1m", "5m", "15m")}


def number(value):
    value = value.strip().replace(",", ".").removeprefix("+")
    try:
        parsed = Decimal(value)
        return value if parsed.is_finite() and parsed >= 0 else None
    except InvalidOperation:
        return None


def console_text(raw):
    # Strip actual logger envelopes, never measurement labels or dimension IDs.
    lines = []
    for line in safe_text(raw, None).splitlines():
        line = re.sub(r"^(?:\[[^\]\r\n]+\]\s*)+(?::\s*)?", "", line.strip())
        lines.append(line)
    return "\n".join(lines)


@dataclass
class Performance:
    raw: str
    format: str = "未识别"
    values: dict[str, str] = field(default_factory=dict)
    rates: list[tuple[str, str]] = field(default_factory=list)
    valid_windows: set[str] = field(default_factory=set)
    dimensions: list[tuple[str, str | None, str | None]] = field(default_factory=list)
    ticks: list[str] = field(default_factory=list)
    cpus: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    recognized: bool = False
    partial: bool = False
    memory: str = ""
    entities: str = ""


def parse_performance(raw, command="", missing="暂无数据"):
    report = Performance(safe_text(raw, None))
    text = console_text(raw)
    spark = parse_spark_tps(text, missing)
    report.values = dict(spark.values)
    spark_known = METRIC_FIELDS - set(spark.missing)
    headers = list(WINDOW.finditer(text))
    if len(headers) == 1:
        header = headers[0]
        windows = [part.strip().casefold().replace(" ", "") for part in (header[1] or header[2]).split(",")]
        body = text[header.end() :].lstrip()
        body = re.split(r"\r?\n|\b(?:Tick\s+durations|CPU\s+usage|MSPT)\b", body, maxsplit=1, flags=re.I)[0].strip()
        tokens = [token.strip() for token in body.split(",")]
        valid = (
            bool(windows)
            and len(windows) <= 32
            and len(set(windows)) == len(windows)
            and all(re.fullmatch(r"\d+[smh]", window) for window in windows)
            and len(tokens) == len(windows)
            and all(re.fullmatch(SCALAR, token, re.I) for token in tokens)
        )
        report.partial = not valid
        if valid:
            for window, token in zip(windows, tokens):
                value = number(token.replace("*", "").strip())
                report.rates.append((window, value + ("*" if "*" in token else "") if value is not None else missing))
                if value is not None:
                    report.valid_windows.add(window)
                if value is not None and "tps_" + window in METRIC_FIELDS | PERFORMANCE_FIELDS:
                    report.values["tps_" + window] = value
                elif value is None:
                    report.partial = True
            if report.valid_windows:
                report.recognized = True
                report.format = (
                    "spark"
                    if re.search(r"(?:Tick\s+durations|CPU\s+usage)", text, re.I)
                    or re.search(r"(?:^|:)spark\s+tps", command, re.I)
                    else "Paper/Spigot"
                )
            if any("*" in value for _, value in report.rates):
                report.notices.append(message_text("perf_notice_star"))
    elif len(headers) > 1:
        report.notices.append(message_text("perf_notice_multiple"))
        report.values = dict.fromkeys(METRIC_FIELDS, missing)
        report.partial = True

    # Spark retains exact window/statistic labels, never assumes the median is
    # a mean, and reports only the available statistics in the unified view.
    if len(headers) <= 1:
        for window in ("10s", "1m"):
            pairs = [
                (label, report.values[f"mspt_{window}_{key}"])
                for key, label in (
                    ("min", message_text("perf_label_min")),
                    ("med", message_text("perf_label_median")),
                    ("p95", message_text("perf_label_p95")),
                    ("max", message_text("perf_label_max")),
                )
                if f"mspt_{window}_{key}" in spark_known
            ]
            if pairs:
                report.ticks.append(
                    message_text(
                        "perf_tick_spark_line",
                        window=LABELS[window],
                        labels=join_text(" / ", (label for label, _ in pairs)),
                        values=join_text(" / ", (value for _, value in pairs)),
                    )
                )
        for scope, label in (
            ("system", message_text("perf_label_system")),
            ("process", message_text("perf_label_process")),
        ):
            pairs = [
                (window, report.values[f"cpu_{scope}_{window}"])
                for window in ("10s", "1m", "15m")
                if f"cpu_{scope}_{window}" in spark_known
            ]
            if pairs:
                report.cpus.append(
                    message_text(
                        "perf_cpu_spark_line",
                        scope=label,
                        windows=join_text(" / ", (LABELS[window] for window, _ in pairs)),
                        values=join_text(" / ", (value for _, value in pairs)),
                    )
                )
        if report.ticks or report.cpus:
            report.format, report.recognized = "spark", True

    # Forge/NeoForge label the window as Mean, without promising a 1m/5m span.
    forge_lines = 0
    for line in text.splitlines():
        if re.search(r"Mean\s+(?:tick\s+time|TPS)\s*:", line, re.I):
            forge_lines += 1
            ms = re.search(rf"Mean\s+tick\s+time\s*:\s*({LOCAL_NUM})\s*ms", line, re.I)
            tps = re.search(rf"Mean\s+TPS\s*:\s*({LOCAL_NUM})", line, re.I)
            label = re.split(r"\s*:\s*Mean\s+", line, maxsplit=1, flags=re.I)[0]
            label = re.sub(r"^Dim(?:ension)?\s+", "", label, flags=re.I).strip()
            if ms or tps:
                tick, rate = number(ms[1]) if ms else None, number(tps[1]) if tps else None
                report.dimensions.append((label, rate, tick))
                if re.fullmatch(r"Overall|All|总体|总计", label, re.I):
                    if rate is not None:
                        report.values["perf_tps"] = rate
                        report.values["tps_overall"] = rate
                    if tick is not None:
                        report.values["perf_mspt"] = tick
                        report.values["mspt_overall"] = tick
                        report.values["mspt_avg"] = tick
                report.partial |= rate is None or tick is None
    if report.dimensions:
        report.values["forge_dimension_count"] = str(
            sum(not re.fullmatch(r"Overall|All|总体|总计", label, re.I) for label, _, _ in report.dimensions)
        )
        report.values["forge_dimensions_json"] = json.dumps(
            [{"dimension": label, "tps": rate, "mspt": tick} for label, rate, tick in report.dimensions],
            ensure_ascii=False,
        )
        aliases = {
            "minecraft:overworld": "overworld",
            "0": "overworld",
            "minecraft:the_nether": "nether",
            "-1": "nether",
            "minecraft:the_end": "end",
            "1": "end",
        }
        for label, rate, tick in report.dimensions:
            alias = aliases.get(label.casefold())
            if alias:
                if rate is not None:
                    report.values[f"forge_{alias}_tps"] = rate
                if tick is not None:
                    report.values[f"forge_{alias}_mspt"] = tick
        report.format, report.recognized = "Forge/NeoForge", True
        report.partial |= len(report.dimensions) != forge_lines

    # Vanilla's tick rate is a configuration/target, not measured TPS.
    if re.search(r"tick\s+rate|ticks?\s+per\s+second|time\s+per\s+tick|game is (?:frozen|sprinting)", text, re.I):
        target = re.search(rf"(?:target\s+|current\s+)?tick\s+rate\s*[:=]?\s*({NUM})", text, re.I)
        if not target:
            target = re.search(rf"game\s+is\s+running[^\n]*?\bat\s+({NUM})\s+ticks?\s+per\s+second", text, re.I)
        average = re.search(
            rf"(?:average\s+time\s+per\s+tick|average\s+tick\s+time)\s*[:=]?\s*({NUM})\s*ms", text, re.I
        )
        state = (
            message_text("perf_state_frozen")
            if re.search(r"game is frozen", text, re.I)
            else message_text("perf_state_sprinting")
            if re.search(r"game is sprinting|sprinting", text, re.I)
            else message_text("perf_state_overloaded")
            if re.search(r"can't keep up|running behind|behind by", text, re.I)
            else message_text("perf_state_normal")
            if re.search(r"game is running normally", text, re.I)
            else missing
        )
        if target or average or state != missing:
            report.format, report.recognized = "tick query", True
            if target:
                report.values["perf_target_rate"] = number(target[1]) or missing
            if average:
                report.values["perf_mspt"] = number(average[1]) or missing
                report.values["mspt_avg"] = number(average[1]) or missing
                report.ticks.append(message_text("perf_tick_average_line", mspt_avg=average[1]))
            percentiles = [
                (key, value) for key, value in re.findall(rf"\b(50th|95th|99th)\s*:\s*({NUM})\s*ms", text, re.I)
            ]
            if percentiles:
                report.values.update(
                    {
                        "mspt_" + {"50th": "p50", "95th": "p95", "99th": "p99"}[key.casefold()]: value
                        for key, value in percentiles
                    }
                )
                report.ticks.append(
                    message_text(
                        "perf_tick_percentile_line",
                        entries=join_text(
                            " / ",
                            (
                                message_text("perf_percentile_entry", percentile=key, value=value)
                                for key, value in percentiles
                            ),
                        ),
                    )
                )
            report.values["perf_tick_state"] = state
            if target:
                report.notices.append(message_text("perf_notice_target"))

    if len(headers) <= 1:
        scalar = re.search(
            rf"^\s*(?:TPS|Actual\s+TPS|Measured\s+TPS|当前\s*TPS|实时\s*TPS)\s*[:：=]\s*({NUM})\s*\*?\s*$",
            text,
            re.I | re.M,
        )
        if scalar and not report.rates and not report.dimensions:
            report.format, report.recognized = "自定义", True
            report.values["perf_tps"] = number(scalar[1]) or missing
    if not report.ticks:
        ms = re.search(
            rf"^\s*(?:MSPT|Tick\s+time|Average\s+tick\s+time)\s*[:：=]\s*({NUM})\s*ms\s*$", text, re.I | re.M
        )
        if ms:
            report.values["perf_mspt"] = number(ms[1]) or missing
            if re.search(r"^\s*Average\s+tick\s+time", ms[0], re.I):
                report.values["mspt_avg"] = number(ms[1]) or missing
            report.ticks.append(message_text("perf_tick_custom_line", perf_mspt=ms[1]))
    if not report.cpus:
        cpu = re.search(rf"^\s*CPU\s*[:：=]\s*({NUM})\s*%\s*$", text, re.I | re.M)
        if cpu:
            report.values["cpu_usage"] = number(cpu[1]) or missing
            report.cpus.append(message_text("perf_cpu_custom_line", cpu_usage=cpu[1]))

    memory = re.search(
        rf"^\s*(?:Current\s+)?Memory(?:\s+usage)?\s*[:：]\s*({NUM})\s*(KiB|MiB|GiB|KB|MB|GB)?\s*/\s*({NUM})\s*(KiB|MiB|GiB|KB|MB|GB)([^\n]*)",
        text,
        re.I | re.M,
    )
    if memory:
        report.values.update(
            memory_used=memory[1],
            memory_used_unit=memory[2] or memory[4],
            memory_total=memory[3],
            memory_total_unit=memory[4],
        )
        maximum = re.search(rf"\bMax(?:imum)?\s*[:：]\s*({NUM})\s*(KiB|MiB|GiB|KB|MB|GB)", memory[5], re.I)
        if maximum:
            report.values.update(memory_max=maximum[1], memory_max_unit=maximum[2])
        report.memory = message_text(
            "perf_memory_summary",
            memory_used=memory[1],
            memory_used_unit=memory[2] or memory[4],
            memory_total=memory[3],
            memory_total_unit=memory[4],
            detail=memory[5].rstrip(),
        )
    entities = re.search(r"Total\s+Ticking\s*:\s*(\d+)\s*,?\s*Total\s+Non-Ticking\s*:\s*(\d+)", text, re.I)
    if entities:
        report.values.update(
            entities_ticking=entities[1],
            entities_non_ticking=entities[2],
            entities_total=str(int(entities[1]) + int(entities[2])),
        )
        report.entities = message_text(
            "perf_entities_summary", entities_ticking=entities[1], entities_non_ticking=entities[2]
        )
    else:
        entities = re.search(r"^\s*(?:Entities|实体(?:数量)?)\s*[:：]\s*(\d+)\s*$", text, re.I | re.M)
        if entities:
            report.values["entities_total"] = entities[1]
            report.entities = entities[1]
    return report


def performance_values(report, result, missing="暂无数据"):
    values = dict.fromkeys(PERFORMANCE_FIELDS, missing)
    values.update(report.values)
    candidates = [(window, value) for window, value in report.rates if window in report.valid_windows]
    if "perf_tps" not in report.values:
        if candidates:
            window, value = min(
                candidates, key=lambda pair: int(pair[0][:-1]) * {"s": 1, "m": 60, "h": 3600}[pair[0][-1]]
            )
            values["perf_tps"] = value.replace("*", "")
    values.update(query_values(result, missing))
    values.update(
        perf_format=message_text(
            {
                "spark": "perf_format_spark",
                "Paper/Spigot": "perf_format_paper",
                "Forge/NeoForge": "perf_format_forge",
                "tick query": "perf_format_tick",
                "自定义": "perf_format_custom",
                "未识别": "perf_format_unknown",
            }[report.format]
        ),
        perf_parse_status=(message_text("parse_partial") if report.partial else message_text("parse_complete"))
        if report.recognized
        else message_text("parse_failed"),
        perf_tps_windows=" / ".join(window for window, _ in report.rates) or missing,
        perf_tps_values=" / ".join(value for _, value in report.rates) or missing,
        perf_memory=report.memory or missing,
        perf_entities=report.entities or missing,
    )
    if report.rates:
        values["perf_tps_line"] = message_text(
            "perf_tps_windows_line",
            windows=join_text(" / ", (LABELS.get(window, window) for window, _ in report.rates)),
            values=values["perf_tps_values"],
        )
    elif "perf_tps" in report.values or candidates:
        values["perf_tps_line"] = message_text("perf_tps_overall_line", perf_tps=values["perf_tps"])
    elif report.dimensions:
        values["perf_tps_line"] = message_text("perf_tps_dimensions_line")
    elif report.format == "tick query":
        values["perf_tps_line"] = message_text(
            "perf_tps_target_line",
            perf_target_rate=values["perf_target_rate"],
            perf_tick_state=values["perf_tick_state"],
        )
    else:
        values["perf_tps_line"] = message_text("perf_tps_unparsed_line")
    values["perf_dimensions"] = (
        join_text(
            "\n",
            (
                message_text(
                    "perf_dimension_entry",
                    dimension=label,
                    perf_tps=rate if rate is not None else missing,
                    perf_mspt=tick if tick is not None else missing,
                )
                for label, rate, tick in report.dimensions
            ),
        )
        or missing
    )
    ticks = list(report.ticks)
    if report.dimensions:
        ticks.append(message_text("perf_dimensions_line", entries=values["perf_dimensions"]))
    values["perf_tick_lines"] = join_text("\n", ticks) or message_text("perf_tick_empty")
    values["perf_cpu_lines"] = join_text("\n", report.cpus) or message_text("perf_cpu_empty")
    values["perf_memory_line"] = message_text(
        "perf_memory_line", perf_memory=report.memory or message_text("perf_missing")
    )
    values["perf_entities_line"] = message_text(
        "perf_entities_line", perf_entities=report.entities or message_text("perf_missing")
    )
    notices = list(report.notices)
    if report.partial:
        notices.append(message_text("perf_notice_partial"))
    info = getattr(result, "info", None)
    if info is not None and info.error:
        notices.append(message_text("perf_notice_query", error=info.error))
    if info is not None and info.warning:
        notices.append(info.warning)
    values["perf_notice"] = message_text("perf_notice", entries=join_text("；", notices)) if notices else ""
    return values


def variable_formats(name):
    """Potential providers, not a guarantee that a particular reply includes it."""
    if name.startswith("forge_") or name in {"tps_overall", "mspt_overall", "perf_dimensions"}:
        return ["forge"]
    if name in {"perf_target_rate", "perf_tick_state", "mspt_p50", "mspt_p95", "mspt_p99"}:
        return ["tick"]
    if name in {"mspt_avg", "perf_mspt"}:
        return ["forge", "tick", "custom"]
    if name.startswith(("mspt_10s_", "mspt_1m_", "cpu_system_", "cpu_process_")):
        return ["spark"]
    if re.fullmatch(r"tps_\d+[sm]", name):
        return ["spark", "paper"]
    if name == "cpu_usage":
        return ["custom"]
    if name == "perf_tps":
        return ["spark", "paper", "forge", "custom"]
    if name.startswith("players_") or name == "status":
        return ["status"]
    return ["spark", "paper", "forge", "tick", "custom", "status"]


def template_examples():
    """Editable starting templates, each with internally consistent sample data."""
    from .monitoring import QueryInfo, QueryResult
    from .spark import SPARK_TPS_EXAMPLE_TEMPLATE

    spark = (
        "TPS from last 5s, 10s, 1m, 5m, 15m:\n13.24, 13.37, 15.95, 19.03, 19.67\n\n"
        "Tick durations (min/med/95%ile/max ms) from last 10s, 1m:\n69.1/74.3/79.6/103.2; 31.1/64.6/77.0/103.5\n\n"
        "CPU usage from last 10s, 1m, 15m:\n33%, 35%, 35% (system)\n14%, 14%, 15% (process)\n"
    )
    paper = "TPS from last 1m, 5m, 15m: *20.0, 19.9, 19.8"
    forge = (
        "Dim minecraft:overworld: Mean tick time: 12.3 ms. Mean TPS: 20.0\n"
        "Dim minecraft:the_nether: Mean tick time: 1.2 ms. Mean TPS: 20.0\n"
        "Overall: Mean tick time: 13.5 ms. Mean TPS: 19.98"
    )
    tick = "The game is running normally. Target tick rate: 20 per second; Average time per tick: 12.3ms (50th: 11.9ms, 95th: 22.4ms, 99th: 31.0ms)"
    custom = "TPS: 19.97\nAverage tick time: 12.4 ms\nCPU: 35%\nMemory: 1024/2048 MB (Max: 4096 MB)\nTotal Ticking: 1327, Total Non-Ticking: 12"
    specs = (
        ("universal", "通用初始模板", "spark", "spark tps", spark, DEFAULT_TPS_TEMPLATE),
        ("spark", "spark 完整指标", "spark", "spark tps", spark, SPARK_TPS_EXAMPLE_TEMPLATE),
        (
            "paper",
            "Paper / Spigot TPS",
            "paper",
            "tps",
            paper,
            "[{server}] TPS\n1分钟：{tps_1m}\n5分钟：{tps_5m}\n15分钟：{tps_15m}\n来源：{query_source} · {query_latency_ms} ms",
        ),
        (
            "forge",
            "Forge / NeoForge 维度指标",
            "forge",
            "forge tps",
            forge,
            "[{server}] Forge 性能\n总体 TPS：{tps_overall}\n总体平均 Tick：{mspt_overall} ms\n主世界：{forge_overworld_tps} TPS / {forge_overworld_mspt} ms\n下界：{forge_nether_tps} TPS / {forge_nether_mspt} ms\n维度数：{forge_dimension_count}\n{perf_dimensions}\n来源：{query_source} · {query_latency_ms} ms",
        ),
        (
            "tick",
            "原版 tick query 指标",
            "tick",
            "tick query",
            tick,
            "[{server}] Tick 状态\n目标速率：{perf_target_rate} tick/s（非实测 TPS）\n状态：{perf_tick_state}\n平均耗时：{mspt_avg} ms\nP50 / P95 / P99：{mspt_p50} / {mspt_p95} / {mspt_p99} ms\n来源：{query_source} · {query_latency_ms} ms",
        ),
        (
            "custom",
            "自定义性能回复",
            "custom",
            "custom:health",
            custom,
            "[{server}] 性能\nTPS：{perf_tps}\n平均 Tick：{mspt_avg} ms\nCPU：{cpu_usage}%\n内存：{memory_used} {memory_used_unit} / {memory_total} {memory_total_unit}\n最大内存：{memory_max} {memory_max_unit}\n实体：{entities_total}（{entities_ticking} ticking / {entities_non_ticking} non-ticking）\n来源：{query_source} · {query_latency_ms} ms",
        ),
    )
    result = []
    for key, label, kind, command, raw, template in specs:
        parsed = parse_performance(raw, command)
        response = QueryResult(
            raw,
            QueryInfo(
                source="RCON",
                command=command,
                latency_ms=12.4,
                checked_at="2026-10-17T02:00:00+00:00",
                rcon_online=True,
            ),
        )
        values = performance_values(parsed, response)
        metrics = parse_spark_tps(raw)
        values.update(
            server="服务器 1",
            server_id="server1",
            result=raw,
            tps_raw=raw,
            tps_command=command,
            tps_parse_status=metrics.status if kind == "spark" else "已解析",
            tps_missing_fields=", ".join(metrics.missing),
            tps_warning="",
        )
        result.append(
            {"id": key, "label": label, "format": kind, "command": command, "template": template, "preview": values}
        )
    return result
