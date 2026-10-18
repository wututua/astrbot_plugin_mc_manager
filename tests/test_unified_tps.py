"""Command-driven queries and migration after removing the old TPS selector."""

import copy
import json
import unittest
from unittest.mock import patch

import test_tps_modes as fixture
from test_spark import SAMPLE

from mc_manager.config import Settings, without_collector_config
from mc_manager.monitoring import QueryInfo, QueryResult


class UnifiedTPSTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = fixture.TPSModeTests.asyncTearDown
    apply = fixture.TPSModeTests.apply

    async def asyncSetUp(self):
        await fixture.TPSModeTests.asyncSetUp(self)
        self.raw = copy.deepcopy(self.service.settings.raw)

    def test_schema_and_defaults_have_no_selector(self):
        schema = json.loads((fixture.Path(__file__).resolve().parents[1] / "_conf_schema.json").read_text("utf-8"))
        self.assertNotIn("tps_mode", schema["servers"]["templates"]["server"]["items"])
        for server in self.service.settings.servers.values():
            self.assertFalse(hasattr(server, "tps_mode"))
        self.assertTrue(all("tps_mode" not in server for server in self.raw["servers"]))

    def test_legacy_defaults_are_materialized_without_mutating_input(self):
        for mode, command in (("native", "tps"), ("spark", "spark tps"), (None, "spark tps")):
            raw = copy.deepcopy(self.raw)
            server = raw["servers"][0]
            server.pop("tps_command", None)
            if mode is not None:
                server["tps_mode"] = mode
            before = copy.deepcopy(raw)
            migrated = without_collector_config(raw)
            self.assertEqual(migrated["servers"][0]["tps_command"], command)
            self.assertNotIn("tps_mode", migrated["servers"][0])
            self.assertEqual(without_collector_config(migrated), migrated)
            self.assertEqual(Settings(migrated).servers["s1"].tps_command, command)
            self.assertEqual(raw, before)

    def test_custom_commands_and_other_settings_survive_migration(self):
        for mode in ("spark", "native"):
            raw = copy.deepcopy(self.raw)
            raw["servers"][0].update(
                tps_mode=mode, tps_command="custom:perf detail", tps_auto_detect=True, future_option="keep"
            )
            migrated = Settings(raw).raw["servers"][0]
            self.assertEqual(migrated["tps_command"], "custom:perf detail")
            self.assertEqual(migrated["future_option"], "keep")
            self.assertTrue(migrated["tps_auto_detect"])
            self.assertNotIn("tps_mode", migrated)

    async def test_all_formats_use_one_pipeline_with_no_selector(self):
        self.raw["servers"][0].update(tps_output="template", tps_command="core:perf")
        self.raw["messages"] = {"tps": "{perf_format}|{perf_tps}|{perf_target_rate}"}
        self.apply()
        cases = (
            (SAMPLE, "spark|13.24|暂无数据"),
            ("TPS from last 1m, 5m, 15m: 20, 19.9, 19.8", "Paper/Spigot|20|暂无数据"),
            ("Overall: Mean tick time: 75 ms. Mean TPS: 13.33", "Forge/NeoForge|13.33|暂无数据"),
            ("Target tick rate: 20; Average time per tick: 12ms", "tick query|暂无数据|20"),
            ("TPS: 12.3", "自定义|12.3|暂无数据"),
        )
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.responses["s1"] = raw
                result = await self.service.query("s1", "tps")
                self.assertEqual(self.service.format_tps("s1", result), expected)
        self.assertTrue(all(command == "core:perf" for _, command in self.commands))

    async def test_auto_detect_is_available_even_for_legacy_spark_config(self):
        self.raw["servers"][0].update(tps_mode="spark", tps_command="custom:perf", tps_auto_detect=True)
        self.apply()
        commands = []

        async def query(client, server, command, kind):
            commands.append(command)
            result = "TPS: 19.9" if command == "forge tps" else "Unknown command"
            return QueryResult(result, QueryInfo(source="RCON", command=command, rcon_online=True))

        with patch.object(self.service, "_query_command", side_effect=query):
            first = await self.service.query("s1", "tps")
            second = await self.service.query("s1", "tps")
        self.assertEqual(first.info.command, "forge tps")
        self.assertEqual(second.info.command, "forge tps")
        self.assertEqual(commands, ["custom:perf", "tps", "forge tps", "forge tps"])

    async def test_disabled_detection_does_not_change_the_configured_command(self):
        self.raw["servers"][0].update(tps_command="custom:perf", tps_auto_detect=False)
        self.apply()
        self.responses["s1"] = "Unknown command"
        result = await self.service.query("s1", "tps")
        self.assertEqual(self.commands, [("s1", "custom:perf")])
        self.assertEqual(self.service.format_tps("s1", result), "Unknown command")

    async def test_diagnostics_recognizes_each_actual_core(self):
        report = await self.service.diagnostics(self.service.settings.groups["g1"])
        servers = {row["id"]: row for row in report["servers"]}
        self.assertEqual(servers["s1"]["performance"]["format"], "Paper/Spigot")
        self.assertEqual(servers["s2"]["performance"]["format"], "spark")
        self.assertEqual(servers["s1"]["performance"]["command"], "tps")
        self.assertTrue(all("spark" not in row for row in report["servers"]))

    async def test_forge_measurement_can_trigger_low_tps_alert(self):
        self.responses["s1"] = "Overall: Mean tick time: 75 ms. Mean TPS: 13.33"
        with patch.object(self.service, "evaluate_alert") as alert:
            await self.service.check_alerts()
        sample = next(call for call in alert.call_args_list if call.args[:2] == ("s1", "TPS 过低"))
        self.assertTrue(sample.args[2])
        self.assertEqual(str(sample.args[3]), "本次返回的 TPS=13.33")

    async def test_target_rate_and_numeric_missing_label_never_trigger_tps_alert(self):
        self.raw["tps_missing_value"] = "0"
        self.apply()
        for sid in self.responses:
            self.responses[sid] = "Target tick rate: 1; Average time per tick: 100ms"
        with patch.object(self.service, "evaluate_alert") as alert:
            await self.service.check_alerts()
        self.assertFalse(any(call.args[1] == "TPS 过低" for call in alert.call_args_list))

    async def test_measured_zero_is_not_confused_with_numeric_missing_label(self):
        self.raw["tps_missing_value"] = "0"
        self.apply()
        self.responses["s1"] = "TPS from last 1m, 5m, 15m: 0, 0, 0"
        with patch.object(self.service, "evaluate_alert") as alert:
            await self.service.check_alerts()
        sample = next(call for call in alert.call_args_list if call.args[:2] == ("s1", "TPS 过低"))
        self.assertTrue(sample.args[2])
        self.assertEqual(str(sample.args[3]), "最近 1 分钟 TPS=0.0")
