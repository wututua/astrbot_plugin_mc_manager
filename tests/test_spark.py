"""Regression tests using the exact spark response supplied by the user."""

import json
import tempfile
import unittest
from pathlib import Path

from mc_manager.config import Actor, MCError, Settings
from mc_manager.service import Service
from mc_manager.spark import METRIC_FIELDS, parse_spark_tps, variables_table

SAMPLE = """TPS from last 5s, 10s, 1m, 5m, 15m:
13.24, 13.37, 15.95, 19.03, 19.67

Tick durations (min/med/95%ile/max ms) from last 10s, 1m:
69.1/74.3/79.6/103.2; 31.1/64.6/77.0/103.5

CPU usage from last 10s, 1m, 15m:
33%, 35%, 35%  (system)
14%, 14%, 15%  (process)
"""
EXPECTED = {
    "tps_5s": "13.24",
    "tps_10s": "13.37",
    "tps_1m": "15.95",
    "tps_5m": "19.03",
    "tps_15m": "19.67",
    "mspt_10s_min": "69.1",
    "mspt_10s_med": "74.3",
    "mspt_10s_p95": "79.6",
    "mspt_10s_max": "103.2",
    "mspt_1m_min": "31.1",
    "mspt_1m_med": "64.6",
    "mspt_1m_p95": "77.0",
    "mspt_1m_max": "103.5",
    "cpu_system_10s": "33",
    "cpu_system_1m": "35",
    "cpu_system_15m": "35",
    "cpu_process_10s": "14",
    "cpu_process_1m": "14",
    "cpu_process_15m": "15",
}


class SparkParserTests(unittest.TestCase):
    def test_all_19_values_match_user_example_exactly(self):
        parsed = parse_spark_tps(SAMPLE)
        self.assertEqual(parsed.values, EXPECTED)
        self.assertEqual(parsed.status, "完整")
        self.assertEqual(parsed.missing, ())
        self.assertEqual(parsed.raw, SAMPLE.strip())

    def test_color_codes_ansi_hex_and_line_prefixes(self):
        colored = "\n".join(
            "[12:34:56 INFO] [spark] §x§f§f§0§0§a§a\x1b[33m" + line + "§r\x1b[0m" for line in SAMPLE.splitlines()
        )
        parsed = parse_spark_tps(colored)
        self.assertEqual(parsed.values, EXPECTED)
        self.assertNotIn("§", parsed.raw)
        self.assertNotIn("\x1b", parsed.raw)

    def test_response_without_linebreaks_and_crlf(self):
        for text in [SAMPLE.replace("\n", " "), SAMPLE.replace("\n", "\r\n")]:
            with self.subTest(text=text[:20]):
                self.assertEqual(parse_spark_tps(text).values, EXPECTED)

    def test_tps_windows_are_mapped_by_labels_not_fixed_positions(self):
        text = SAMPLE.replace("TPS from last 5s, 10s, 1m, 5m, 15m:", "TPS from last 15m, 5m, 1m, 10s, 5s:")
        text = text.replace("13.24, 13.37, 15.95, 19.03, 19.67", "19.67, 19.03, 15.95, 13.37, 13.24")
        self.assertEqual(parse_spark_tps(text).values, EXPECTED)

    def test_duration_stats_are_mapped_by_header(self):
        text = SAMPLE.replace("min/med/95%ile/max ms", "max/min/median/p95 ms")
        text = text.replace("69.1/74.3/79.6/103.2; 31.1/64.6/77.0/103.5", "103.2/69.1/74.3/79.6; 103.5/31.1/64.6/77.0")
        self.assertEqual(parse_spark_tps(text).values, EXPECTED)

    def test_duration_windows_can_be_reordered(self):
        text = SAMPLE.replace("from last 10s, 1m:", "from last 1m, 10s:", 1)
        text = text.replace("69.1/74.3/79.6/103.2; 31.1/64.6/77.0/103.5", "31.1/64.6/77.0/103.5; 69.1/74.3/79.6/103.2")
        self.assertEqual(parse_spark_tps(text).values, EXPECTED)

    def test_cpu_rows_and_windows_can_be_reordered(self):
        text = SAMPLE.replace("CPU usage from last 10s, 1m, 15m:", "CPU usage from last 15m, 1m, 10s:")
        text = text.replace(
            "33%, 35%, 35%  (system)\n14%, 14%, 15%  (process)", "15%, 14%, 14%  (process)\n35%, 35%, 33%  (system)"
        )
        self.assertEqual(parse_spark_tps(text).values, EXPECTED)

    def test_asterisks_and_decimal_precision_are_preserved_correctly(self):
        parsed = parse_spark_tps(SAMPLE.replace("13.24", "*20.00").replace("33%", "33.50%"))
        self.assertEqual(parsed.values["tps_5s"], "20.00")
        self.assertEqual(parsed.values["cpu_system_10s"], "33.50")

    def test_missing_duration_section_preserves_other_values(self):
        text = SAMPLE[: SAMPLE.index("Tick durations")] + SAMPLE[SAMPLE.index("CPU usage") :]
        parsed = parse_spark_tps(text)
        self.assertEqual(parsed.status, "部分")
        self.assertEqual(parsed.values["mspt_1m_p95"], "暂无数据")
        self.assertEqual(parsed.values["tps_5s"], "13.24")
        self.assertEqual(parsed.values["cpu_process_15m"], "15")
        self.assertEqual(len(parsed.missing), 8)

    def test_invalid_single_value_does_not_shift_neighbors(self):
        parsed = parse_spark_tps(SAMPLE.replace("13.37", "NaN").replace("79.6", "N/A"))
        self.assertEqual(parsed.values["tps_10s"], "暂无数据")
        self.assertEqual(parsed.values["tps_1m"], "15.95")
        self.assertEqual(parsed.values["mspt_10s_p95"], "暂无数据")
        self.assertEqual(parsed.values["mspt_10s_max"], "103.2")
        self.assertEqual(len(parsed.missing), 2)

    def test_truncated_and_extra_tps_vectors_are_rejected(self):
        for values in ["13.24, 13.37", "1, 13.24, 13.37, 15.95, 19.03, 19.67"]:
            parsed = parse_spark_tps(SAMPLE.replace("13.24, 13.37, 15.95, 19.03, 19.67", values))
            self.assertTrue(all(parsed.values[k] == "暂无数据" for k in EXPECTED if k.startswith("tps_")))
            self.assertEqual(parsed.values["mspt_10s_min"], "69.1")

    def test_extra_cpu_values_are_not_shifted_into_wrong_windows(self):
        parsed = parse_spark_tps(SAMPLE.replace("33%, 35%, 35%", "1%, 33%, 35%, 35%"))
        self.assertEqual(parsed.values["cpu_system_10s"], "暂无数据")
        self.assertEqual(parsed.values["cpu_process_10s"], "14")

    def test_negative_metrics_are_missing_and_zero_is_a_real_value(self):
        parsed = parse_spark_tps(SAMPLE.replace("13.24", "0.00").replace("33%", "-1%"))
        self.assertEqual(parsed.values["tps_5s"], "0.00")
        self.assertEqual(parsed.values["cpu_system_10s"], "暂无数据")

    def test_unrecognized_stats_do_not_get_assumed_to_be_median(self):
        parsed = parse_spark_tps(SAMPLE.replace("min/med/95%ile/max ms", "min/mean/95%ile/max ms"))
        self.assertEqual(parsed.values["mspt_10s_med"], "暂无数据")
        self.assertEqual(parsed.values["tps_5s"], "13.24")

    def test_unknown_command_offline_or_empty_never_invents_metrics(self):
        for text in ["", "Unknown command spark tps", "RCON timeout", "Server offline"]:
            with self.subTest(text=text):
                parsed = parse_spark_tps(text)
                self.assertEqual(parsed.status, "未解析")
                self.assertEqual(set(parsed.missing), METRIC_FIELDS)
                self.assertTrue(all(value == "暂无数据" for value in parsed.values.values()))

    def test_custom_missing_placeholder(self):
        self.assertTrue(all(v == "--" for v in parse_spark_tps("offline", "--").values.values()))

    def test_variable_table_and_default_config_are_consistent(self):
        table = variables_table()
        self.assertEqual(sum(row["category"] == "指标" for row in table), 19)
        self.assertEqual({row["name"] for row in table if row["category"] == "指标"}, set(EXPECTED))
        for filename in ["config.default.json", "config.example.json"]:
            raw = json.loads(Path(filename).read_text(encoding="utf-8"))
            settings = Settings(raw)
            self.assertEqual(settings.messages["tps"], Settings({}).messages["tps"])
            for name in EXPECTED:
                self.assertIn("{" + name + "}", settings.messages["tps"])
        stored = json.loads(Path("tps.variables.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["variables"], table)

    def test_metric_template_names_are_validated(self):
        settings = Settings({"messages": {"tps": "{server_id} {tps_5s} {mspt_10s_p95} {cpu_process_1m}%"}})
        self.assertIn(
            "33%", settings.render("tps", server_id="s1", tps_5s="13", mspt_10s_p95="80", cpu_process_1m="33")
        )
        for template in ["{tps_5s.__class__}", "{tps_5s[0]}", "{cpu_idle_1m}", "{tps_5s:.2f}"]:
            with self.assertRaises(MCError):
                Settings({"messages": {"tps": template}})

    def test_missing_value_configuration_validation(self):
        for value in ["", "x" * 33, "missing\nvalue", 0, None]:
            with self.assertRaises(MCError):
                Settings({"tps_missing_value": value})


class SparkServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.commands = []
        self.responses = {"s1": SAMPLE, "s2": SAMPLE.replace("13.24", "12.50")}
        owner = self

        class Client:
            def __init__(self, server, timeout):
                self.server = server

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                pass

            async def command(self, command):
                owner.commands.append((self.server.id, command))
                value = owner.responses[self.server.id]
                if isinstance(value, Exception):
                    raise value
                return value

        self.raw = {
            "server_root": str(self.root),
            "servers": [{"id": "s1", "directory": "s1"}, {"id": "s2", "directory": "s2", "rcon_port": 25576}],
            "groups": [{"id": "g1", "platform_id": "bot", "group_id": "100", "server_ids": ["s1", "s2"]}],
        }

        async def send(*_):
            return True

        self.service = Service(Settings(self.raw), self.root / "data", send, Client)
        self.actor = Actor("bot", "100", "user")

    async def asyncTearDown(self):
        await self.service.close()
        self.temp.cleanup()

    async def test_default_query_executes_spark_and_formats_all_metrics(self):
        result = await self.service.execute(self.actor, "tps", ["s1"])
        self.assertEqual(self.commands, [("s1", "spark tps")])
        self.assertIn("13.24 / 13.37 / 15.95 / 19.03 / 19.67", result)
        self.assertIn("MC进程：14 / 14 / 15", result)
        self.assertNotIn("部分 spark", result)

    async def test_custom_template_formats_independent_server_values(self):
        self.raw["messages"] = {
            "tps": "{server_id}: {tps_5s} / {mspt_1m_p95}ms / {cpu_system_1m}% / {cpu_process_15m}% ({tps_parse_status})"
        }
        self.service.settings = Settings(self.raw)
        result = await self.service.execute(self.actor, "tps", [])
        self.assertEqual(result, "s1: 13.24 / 77.0ms / 35% / 15% (完整)\ns2: 12.50 / 77.0ms / 35% / 15% (完整)")

    async def test_legacy_raw_template_keeps_original_response(self):
        self.raw["messages"] = {"tps": "[{server}] TPS：{result}"}
        self.service.settings = Settings(self.raw)
        result = await self.service.execute(self.actor, "tps", ["s1"])
        self.assertEqual(result, "[s1] TPS：" + SAMPLE.strip())

    async def test_missing_metrics_show_placeholder_warning_and_original(self):
        self.responses["s1"] = SAMPLE[: SAMPLE.index("CPU usage")]
        result = await self.service.execute(self.actor, "tps", ["s1"])
        self.assertIn("系统：暂无数据 / 暂无数据 / 暂无数据", result)
        self.assertIn("cpu_system_10s", result)
        self.assertIn("原始输出", result)

    async def test_offline_error_and_unknown_command_use_configurable_error_template(self):
        self.raw["messages"] = {"tps_error": "查询失败 {server_id} ({tps_parse_status})：{tps_raw}"}
        self.service.settings = Settings(self.raw)
        for response in [OSError("offline"), "Unknown command spark"]:
            self.responses["s1"] = response
            result = await self.service.execute(self.actor, "tps", ["s1"])
            self.assertIn("查询失败 s1 (未解析)", result)
            self.assertNotIn("13.24", result)

    async def test_custom_warning_and_command_are_respected(self):
        self.responses["s1"] = "TPS from last 1m, 5m, 15m: 20.00, 19.9, 19.8"
        self.raw["servers"][0]["tps_command"] = "custom:spark tps"
        self.raw["messages"] = {
            "tps": "{tps_command}: {tps_1m}{tps_warning}",
            "tps_warning": " [缺失: {tps_missing_fields}]",
        }
        self.service.settings = Settings(self.raw)
        result = await self.service.execute(self.actor, "tps", ["s1"])
        self.assertIn("custom:spark tps: 20.00 [缺失: tps_5s, tps_10s", result)
        self.assertEqual(self.commands[-1], ("s1", "custom:spark tps"))
