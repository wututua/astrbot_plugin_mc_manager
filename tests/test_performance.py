"""Cross-core TPS samples, report styles and honest fallback regressions."""

import copy
import json
import unittest
from dataclasses import replace
from pathlib import Path

import test_monitoring as fixture
from test_spark import SAMPLE

from mc_manager.config import MCError, Settings
from mc_manager.monitoring import QueryInfo, QueryResult
from mc_manager.performance import parse_performance, performance_values

PAPER = "TPS from last 1m, 5m, 15m:\n*20.0, 19.9, 19.8\n"
FORGE = """Dim minecraft:overworld: Mean tick time: 12.300 ms. Mean TPS: 20.000
Dim minecraft:the_nether: Mean tick time: 1.200 ms. Mean TPS: 20.000
Overall: Mean tick time: 13.500 ms. Mean TPS: 19.980
"""
VANILLA = "The game is running normally. Target tick rate: 20 per second; Average time per tick: 12.3ms (50th: 11.9ms, 95th: 22.4ms, 99th: 31.0ms)"


class PerformanceParserTests(unittest.TestCase):
    def values(self, text, command="tps", missing="暂无数据"):
        parsed = parse_performance(text, command, missing)
        return parsed, performance_values(parsed, QueryResult(text, QueryInfo(source="RCON", command=command)), missing)

    def test_paper_spigot_windows_stars_and_shortest_available_tps(self):
        report, values = self.values(PAPER)
        self.assertEqual(report.format, "Paper/Spigot")
        self.assertTrue(report.recognized)
        self.assertEqual(values["perf_tps_windows"], "1m / 5m / 15m")
        self.assertEqual(values["perf_tps_values"], "20.0* / 19.9 / 19.8")
        self.assertEqual(values["perf_tps"], "20.0")
        self.assertEqual(values["tps_1m"], "20.0")
        self.assertIn("*", values["perf_notice"])

    def test_alternate_parenthesized_header_and_unknown_but_labeled_window(self):
        report, values = self.values("TPS (30s, 1m, 5m): 19.1*, 19.2, 19.3")
        self.assertTrue(report.recognized)
        self.assertEqual(values["perf_tps_windows"], "30s / 1m / 5m")
        self.assertEqual(values["perf_tps"], "19.1")
        self.assertIn("30s", values["perf_tps_line"])

    def test_window_order_preserved_but_scalar_uses_shortest_window(self):
        _, values = self.values("TPS from last 15m, 5m, 1m: 18.2, 19.3, 19.9")
        self.assertEqual(values["perf_tps_values"], "18.2 / 19.3 / 19.9")
        self.assertEqual(values["perf_tps"], "19.9")
        self.assertEqual(values["tps_1m"], "19.9")

    def test_truncated_duplicate_or_extra_vector_never_shifts_numbers(self):
        for text in (
            "TPS from last 1m, 5m, 15m: 20.0, 19.0",
            "TPS from last 1m, 1m, 15m: 20.0, 19.0, 18.0",
            "TPS from last 1m, 5m, 15m: 20.0, 19.0, 18.0, 17.0",
            "TPS from last 1m, 5m: 20.0, 19.9\nTPS from last 1m, 5m: 1.0, 2.0",
        ):
            with self.subTest(text=text):
                report, values = self.values(text)
                self.assertFalse(report.recognized)
                self.assertEqual(values["perf_tps"], "暂无数据")
                self.assertEqual(values["tps_1m"], "暂无数据")

    def test_missing_vector_slot_stays_missing(self):
        report, values = self.values("TPS from last 1m, 5m, 15m: 20.0, NaN, 18.0")
        self.assertTrue(report.partial)
        self.assertEqual(values["tps_5m"], "暂无数据")
        self.assertEqual(values["tps_15m"], "18.0")
        self.assertEqual(values["perf_tps_values"], "20.0 / 暂无数据 / 18.0")

    def test_spark_keeps_19_metrics_and_does_not_invent_mean_mspt(self):
        report, values = self.values(SAMPLE, "spark tps")
        self.assertEqual(report.format, "spark")
        self.assertEqual(values["tps_5s"], "13.24")
        self.assertEqual(values["mspt_10s_p95"], "79.6")
        self.assertEqual(values["cpu_process_1m"], "14")
        self.assertEqual(values["perf_mspt"], "暂无数据")
        self.assertIn("中位", values["perf_tick_lines"])
        self.assertIn("系统", values["perf_cpu_lines"])
        self.assertIn("MC进程", values["perf_cpu_lines"])

    def test_real_zero_values_remain_available_even_with_numeric_missing_text(self):
        report, values = self.values(
            SAMPLE.replace("13.24", "0").replace("14%, 14%, 15%", "0%, 0%, 0%"), "spark tps", missing="0"
        )
        self.assertTrue(report.recognized)
        self.assertEqual(values["tps_5s"], "0")
        self.assertIn("MC进程", values["perf_cpu_lines"])
        self.assertIn("0 / 0 / 0", values["perf_cpu_lines"])

    def test_spark_prefixes_colors_and_crlf(self):
        text = "\r\n".join(
            "[17:32:57] [spark-worker-pool-1-thread-2/INFO] [minecraft/MinecraftServer]: §a[?] " + line
            for line in SAMPLE.splitlines()
        )
        report, values = self.values(text, "spark tps")
        self.assertEqual(report.format, "spark")
        self.assertEqual(values["tps_5s"], "13.24")
        self.assertEqual(values["cpu_system_1m"], "35")
        self.assertNotIn("§", report.raw)

    def test_forge_preserves_dimensions_and_overall_without_time_window_claim(self):
        report, values = self.values(FORGE, "forge tps")
        self.assertEqual(report.format, "Forge/NeoForge")
        self.assertEqual(values["perf_tps"], "19.980")
        self.assertEqual(values["perf_mspt"], "13.500")
        self.assertEqual(values["tps_overall"], "19.980")
        self.assertEqual(values["mspt_overall"], "13.500")
        self.assertEqual(values["forge_overworld_tps"], "20.000")
        self.assertEqual(values["forge_nether_mspt"], "1.200")
        self.assertEqual(values["forge_dimension_count"], "2")
        self.assertEqual(len(json.loads(values["forge_dimensions_json"])), 3)
        self.assertEqual(values["tps_1m"], "暂无数据")
        self.assertEqual(len(report.dimensions), 3)
        self.assertIn("minecraft:the_nether", values["perf_dimensions"])
        self.assertIn("Overall", values["perf_dimensions"])

    def test_neoforge_numeric_dimension_ids_and_local_decimal_comma(self):
        report, values = self.values(
            "Dimension -1: Mean tick time: 1,25 ms. Mean TPS: 19,75\nOverall: Mean tick time: 1,25 ms. Mean TPS: 19,75",
            "neoforge tps",
        )
        self.assertEqual(report.dimensions[0], ("-1", "19.75", "1.25"))
        self.assertEqual(values["perf_tps"], "19.75")

    def test_forge_without_overall_does_not_average_dimensions(self):
        report, values = self.values(FORGE.split("Overall")[0], "forge tps")
        self.assertTrue(report.recognized)
        self.assertEqual(values["perf_tps"], "暂无数据")
        self.assertIn("未报告总体", values["perf_tps_line"])

    def test_bad_forge_metric_is_missing_and_report_is_partial(self):
        report, values = self.values("Overall: Mean tick time: NaN ms. Mean TPS: 19.9", "forge tps")
        self.assertTrue(report.partial)
        self.assertEqual(values["perf_mspt"], "暂无数据")
        self.assertEqual(values["perf_tps"], "19.9")

    def test_vanilla_target_and_percentiles_are_not_measured_tps(self):
        report, values = self.values(VANILLA, "tick query")
        self.assertEqual(report.format, "tick query")
        self.assertEqual(values["perf_target_rate"], "20")
        self.assertEqual(values["perf_tps"], "暂无数据")
        self.assertEqual(values["tps_1m"], "暂无数据")
        self.assertEqual(values["perf_mspt"], "12.3")
        self.assertEqual(values["mspt_avg"], "12.3")
        self.assertEqual(values["mspt_p50"], "11.9")
        self.assertEqual(values["mspt_p95"], "22.4")
        self.assertEqual(values["mspt_p99"], "31.0")
        self.assertIn("95th 22.4", values["perf_tick_lines"])
        self.assertIn("非", values["perf_notice"].replace("不等于", "非"))

    def test_vanilla_frozen_sprint_and_normal_running_at_shape(self):
        for text, state, rate in (
            ("The game is frozen.", "冻结", "暂无数据"),
            ("The game is sprinting. Current tick rate: 50", "加速", "50"),
            ("The game is running normally at 20.0 ticks per second.", "正常", "20.0"),
        ):
            with self.subTest(text=text):
                _, values = self.values(text, "tick query")
                self.assertEqual(values["perf_tick_state"], state)
                self.assertEqual(values["perf_target_rate"], rate)
                self.assertEqual(values["perf_tps"], "暂无数据")

    def test_custom_explicit_tps_mspt_cpu_memory_and_entities(self):
        report, values = self.values(
            "TPS: 19.97\nTick time: 12.4 ms\nCPU: 35%\nMemory: 1024/2048 MB (Max: 4096 MB)\nTotal Ticking: 1327, Total Non-Ticking: 12",
            "custom:health",
        )
        self.assertEqual(report.format, "自定义")
        self.assertEqual(values["perf_tps"], "19.97")
        self.assertEqual(values["perf_mspt"], "12.4")
        self.assertIn("35", values["perf_cpu_lines"])
        self.assertIn("1024 MB / 2048 MB", values["perf_memory"])
        self.assertEqual(values["memory_used"], "1024")
        self.assertEqual(values["memory_total"], "2048")
        self.assertEqual(values["memory_max"], "4096")
        self.assertEqual(values["memory_used_unit"], "MB")
        self.assertEqual(values["cpu_usage"], "35")
        self.assertEqual(values["entities_ticking"], "1327")
        self.assertEqual(values["entities_non_ticking"], "12")
        self.assertEqual(values["entities_total"], "1339")
        self.assertEqual(values["perf_entities"], "1327 ticking / 12 non-ticking")

    def test_custom_measured_tps_and_average_tick_are_independent_fields(self):
        report, values = self.values("TPS: 19.7\nAverage time per tick: 12.3ms", "custom:health")
        self.assertEqual(report.format, "自定义")
        self.assertEqual(values["perf_tps"], "19.7")
        self.assertEqual(values["mspt_avg"], "12.3")
        self.assertEqual(values["perf_target_rate"], "暂无数据")

    def test_paper_additional_explicit_mspt_is_not_lost(self):
        _, values = self.values(PAPER + "MSPT: 12.7 ms\n")
        self.assertEqual(values["perf_mspt"], "12.7")
        self.assertIn("12.7", values["perf_tick_lines"])

    def test_unknown_or_target_only_payload_never_fabricates_measured_tps(self):
        for text in (
            "Target TPS: 20",
            "players=20, cpu=19.99",
            "Unknown command",
            "TPS: NaN",
            "TPS: -1",
            "TPS: 1.2.3",
            "TPS: 1e999",
            "TPS: Infinity",
        ):
            with self.subTest(text=text):
                report, values = self.values(text)
                self.assertFalse(report.recognized)
                self.assertEqual(values["perf_tps"], "暂无数据")

    def test_native_extra_windows_memory_units_and_zero_counts_are_individual_variables(self):
        report, values = self.values(
            "TPS from last 1s, 30s, 1m: 19.5, 19.8, 19.9\nMemory: 1.5 GiB / 4 GiB\nEntities: 0"
        )
        self.assertTrue(report.recognized)
        self.assertEqual(values["tps_1s"], "19.5")
        self.assertEqual(values["tps_30s"], "19.8")
        self.assertEqual(values["memory_used"], "1.5")
        self.assertEqual(values["memory_used_unit"], "GiB")
        self.assertEqual(values["memory_max"], "暂无数据")
        self.assertEqual(values["entities_total"], "0")
        self.assertEqual(values["entities_ticking"], "暂无数据")

    def test_each_initial_example_uses_only_registered_variables_and_is_internally_consistent(self):
        import string

        from mc_manager.performance import template_examples
        from mc_manager.spark import variables_table

        names = {row["name"] for row in variables_table()}
        examples = template_examples()
        self.assertEqual(len(examples), 6)
        for example in examples:
            with self.subTest(example=example["id"]):
                for _, token, _, _ in string.Formatter().parse(example["template"]):
                    if token is not None:
                        self.assertIn(token, names)
                rendered = example["template"].format_map(example["preview"])
                self.assertNotIn("{query_source}", rendered)
                if example["format"] == "tick":
                    self.assertEqual(example["preview"]["perf_tps"], "暂无数据")
                    self.assertEqual(example["preview"]["mspt_p95"], "22.4")
                if example["format"] == "forge":
                    self.assertEqual(example["preview"]["tps_1m"], "暂无数据")
                    self.assertEqual(example["preview"]["tps_overall"], "19.98")

    def test_unknown_memory_and_entities_are_not_zero_or_claimed_unsupported(self):
        _, values = self.values(PAPER)
        self.assertEqual(values["perf_memory"], "暂无数据")
        self.assertIn("未提供", values["perf_memory_line"])
        self.assertIn("未提供", values["perf_entities_line"])
        self.assertNotIn("0", values["perf_entities_line"])
        self.assertNotIn("不支持", values["perf_entities_line"])

    def test_all_dimensions_are_kept(self):
        text = "\n".join(f"Dim custom:world_{i}: Mean tick time: 1.0 ms. Mean TPS: 20.0" for i in range(500))
        report, values = self.values(text, "forge tps")
        self.assertEqual(len(report.dimensions), 500)
        self.assertIn("world_499", values["perf_dimensions"])
        self.assertGreater(len(values["perf_dimensions"]), 16000)


class PerformanceServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.MonitoringTests.asyncSetUp(self)
        for row in self.raw["servers"]:
            row["tps_output"] = "template"
        self.apply()

    asyncTearDown = fixture.MonitoringTests.asyncTearDown
    apply = fixture.MonitoringTests.apply

    async def test_default_spark_and_native_share_report_style(self):
        for sid in ("s1", "s2"):
            text = await self.service.execute(self.actor, "tps", [sid])
            self.assertTrue(text.startswith("📈 服务器性能"))
            self.assertIn("⚡ TPS", text)
            self.assertIn("💾 内存：本次回复未提供", text)
            self.assertIn("指令：", text)
            self.assertIn("耗时：", text)
        self.assertEqual(self.commands, [("s1", "tps"), ("s2", "spark tps")])

    async def test_native_forge_and_tick_reports(self):
        for command, raw, expected in (("forge tps", FORGE, "19.980"), ("tick query", VANILLA, "目标/配置速率：20")):
            self.service.settings.servers["s1"] = replace(self.service.settings.servers["s1"], tps_command=command)
            self.service.invalidate_queries("s1")
            self.responses["s1", command] = raw
            text = await self.service.execute(self.actor, "tps", ["s1"])
            self.assertIn(expected, text)
            self.assertNotIn("spark TPS 查询失败", text)

    async def test_query_mode_does_not_force_reply_parser(self):
        self.responses["s2", "spark tps"] = FORGE
        result = await self.service.query("s2", "tps")
        self.assertEqual(result.info.error, "")
        self.assertIn("Forge/NeoForge", self.service.format_tps("s2", result))

    async def test_template_output_works_for_native_and_spark(self):
        self.raw["messages"] = {"tps": "{server_id}|{perf_format}|{perf_tps}|{query_command}"}
        for server in self.raw["servers"]:
            server["tps_output"] = "template"
        self.apply()
        text = await self.service.execute(self.actor, "tps", [])
        self.assertEqual(text, "s1|Paper/Spigot|20.0|tps\ns2|spark|13.24|spark tps")

    async def test_all_forge_and_vanilla_scalar_variables_can_be_used_in_bot_templates(self):
        self.service.settings.messages["tps"] = (
            "{tps_overall}|{forge_overworld_tps}|{forge_nether_mspt}|{forge_dimension_count}|{mspt_avg}|{mspt_p95}|{perf_target_rate}"
        )
        self.responses["s1", "tps"] = FORGE
        self.assertEqual(
            await self.service.execute(self.actor, "tps", ["s1"]), "19.980|20.000|1.200|2|13.500|暂无数据|暂无数据"
        )
        self.service.invalidate_queries("s1")
        self.responses["s1", "tps"] = VANILLA
        self.assertEqual(
            await self.service.execute(self.actor, "tps", ["s1"]), "暂无数据|暂无数据|暂无数据|暂无数据|12.3|22.4|20"
        )

    async def test_raw_output_is_complete_for_any_query_mode(self):
        raw = "unrecognized custom data\n" * 1100 + "END\n"
        for sid, command in (("s1", "tps"), ("s2", "spark tps")):
            self.service.settings.servers[sid] = replace(self.service.settings.servers[sid], tps_output="raw")
            self.responses[sid, command] = raw
            self.assertEqual(await self.service.execute(self.actor, "tps", [sid]), raw)

    async def test_unknown_report_appends_full_original_past_old_template_limit(self):
        raw = "custom unknown {perf_tps}\n" * 1200 + "END\n"
        self.responses["s1", "tps"] = raw
        text = await self.service.execute(self.actor, "tps", ["s1"])
        self.assertTrue(text.endswith(raw))
        self.assertIn("原始输出", text)
        self.assertIn("无法解析", text)

    async def test_large_dimension_report_is_not_truncated(self):
        self.responses["s1", "tps"] = "\n".join(
            f"Dim custom:world_{i}: Mean tick time: 1 ms. Mean TPS: 20" for i in range(500)
        )
        text = await self.service.execute(self.actor, "tps", ["s1"])
        self.assertGreater(len(text), 16000)
        self.assertIn("world_499", text)

    async def test_partial_report_keeps_unknown_slot_and_original(self):
        self.responses["s1", "tps"] = "TPS from last 1m, 5m, 15m: 20.0, NaN, 19.8"
        text = await self.service.execute(self.actor, "tps", ["s1"])
        self.assertIn("20.0 / 暂无数据 / 19.8", text)
        self.assertIn("原始输出", text)
        self.assertNotIn("20.0 / 0", text)

    async def test_error_report_does_not_show_old_good_values(self):
        self.responses["s1", "tps"] = OSError("refused")
        text = await self.service.execute(self.actor, "tps", ["s1"])
        self.assertIn("refused", text)
        self.assertIn("无法解析", text)
        self.assertNotIn("20.0", text)

    async def test_custom_report_template_is_literal_data_and_can_use_values(self):
        self.service.settings.messages["tps"] = "{server_id}|{perf_tps}|{perf_format}|{{literal}}"
        self.assertEqual(await self.service.execute(self.actor, "tps", ["s1"]), "s1|20.0|Paper/Spigot|{literal}")

    async def test_source_metadata_and_cache_stay_attached_to_each_report(self):
        first = await self.service.execute(self.actor, "tps", ["s1"])
        second = await self.service.execute(self.actor, "tps", ["s1"])
        self.assertIn("缓存：否", first)
        self.assertIn("缓存：是", second)
        self.assertEqual(self.commands, [("s1", "tps")])

    def test_new_default_and_legacy_custom_template_selection(self):
        raw = copy.deepcopy(self.raw)
        for row in raw["servers"]:
            row.pop("tps_output")
        settings = Settings(raw)
        self.assertTrue(all(server.tps_output == "template" for server in settings.servers.values()))
        raw["messages"] = {"tps": "CUSTOM {tps_5s}"}
        settings = Settings(raw)
        self.assertEqual(settings.servers["s2"].tps_output, "template")
        self.assertEqual(settings.servers["s1"].tps_output, "template")
        self.assertEqual(raw["messages"]["tps"], "CUSTOM {tps_5s}")
        raw["servers"][1]["tps_output"] = "raw"
        self.assertEqual(Settings(raw).servers["s2"].tps_output, "raw")

    def test_output_validation_and_variable_directory(self):
        for output in ("auto", None, True, [], {}):
            raw = copy.deepcopy(self.raw)
            raw["servers"][0]["tps_output"] = output
            with self.subTest(output=output), self.assertRaises(MCError):
                Settings(raw)
        root = Path(__file__).resolve().parents[1]
        variables = json.loads((root / "tps.variables.json").read_text(encoding="utf-8"))["variables"]
        names = {row["name"] for row in variables}
        self.assertEqual(len(names), len(variables))
        self.assertIn("perf_dimensions", names)
        self.assertTrue(all("spark" in row["scopes"] for row in variables if row["name"].startswith("perf_")))
