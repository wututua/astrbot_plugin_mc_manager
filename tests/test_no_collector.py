"""Removing the Java collector must keep RCON and old configuration usable."""

import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_monitoring as fixture

from mc_manager.config import Settings
from mc_manager.spark import parse_spark_tps, variables_table
from tools.build_release import DIRECTORIES

ROOT = Path(__file__).resolve().parents[1]


class NoCollectorTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.MonitoringTests.asyncSetUp
    asyncTearDown = fixture.MonitoringTests.asyncTearDown
    apply = fixture.MonitoringTests.apply

    def legacy(self):
        self.raw["spark_file_max_age"] = "obsolete-invalid-value"
        self.raw["servers"][1].update(tps_source="file", spark_file="../outside.txt")
        self.apply()

    def test_legacy_configuration_is_ignored_without_mutating_input(self):
        self.legacy()
        self.assertEqual(self.raw["servers"][1]["tps_source"], "file")
        self.assertNotIn("spark_file_max_age", self.service.settings.raw)
        self.assertNotIn("tps_source", self.service.settings.raw["servers"][1])
        self.assertNotIn("spark_file", self.service.settings.raw["servers"][1])
        self.assertFalse(hasattr(self.service.settings.servers["s2"], "spark_file"))
        self.assertFalse(hasattr(self.service.settings, "spark_file_max_age"))

    async def test_old_file_mode_uses_rcon_and_never_opens_report(self):
        self.legacy()
        self.path.write_text("TPS from last 1m: 1.0", encoding="utf-8")
        with patch.object(Path, "open", side_effect=AssertionError("query must not open files")):
            result = await self.service.query("s2", "tps")
        self.assertEqual(result.info.source, "RCON")
        self.assertEqual(parse_spark_tps(result).values["tps_1m"], "15.95")
        self.assertEqual(self.commands, [("s2", "spark tps")])

    async def test_empty_spark_reply_has_actionable_advice_without_agent(self):
        self.legacy()
        self.responses["s2", "spark tps"] = ""
        result = await self.service.execute(self.actor, "tps", ["s2"])
        self.assertIn("空响应", result)
        self.assertIn("原生", result)
        self.assertNotIn("采集器", result)
        self.assertNotIn("javaagent", result)
        self.assertEqual(self.commands, [("s2", "spark tps")])

    async def test_network_failure_cannot_be_hidden_by_old_report(self):
        self.path.write_text(fixture.SAMPLE, encoding="utf-8")
        self.responses["s2", "spark tps"] = OSError("refused")
        result = await self.service.query("s2", "tps")
        self.assertFalse(result.info.rcon_online)
        self.assertIn("离线或 RCON 不可用", result)
        self.assertNotIn("13.24", result)

    async def test_retired_placeholder_is_compatible_but_not_advertised(self):
        self.raw["messages"] = {"tps": "{tps_5s}|{tps_source}|{tps_file_age_seconds}"}
        self.raw["tps_missing_value"] = "--"
        self.apply()
        self.assertEqual(await self.service.execute(self.actor, "tps", ["s2"]), "13.24|rcon|--")
        names = {row["name"] for row in variables_table()}
        self.assertNotIn("tps_file_age_seconds", names)
        self.assertEqual(len(names), 85)

    async def test_diagnostics_reports_spark_rcon_instead_of_report_file(self):
        self.legacy()
        report = await self.service.diagnostics(self.service.settings.groups["g1"])
        spark = next(row for row in report["servers"] if row["id"] == "s2")["performance"]
        self.assertEqual(
            spark, {"status": "已解析", "format": "spark", "source": "RCON", "command": "spark tps", "error": ""}
        )
        self.assertNotIn("path", spark)

    async def test_alerts_do_not_depend_on_local_report(self):
        self.legacy()
        with patch.object(self.service, "evaluate_alert") as alert:
            await self.service.check_alerts()
        kinds = [call.args[1] for call in alert.call_args_list]
        self.assertNotIn("spark 采集异常", kinds)
        self.assertIn("TPS 过低", kinds)

    def test_schema_defaults_ci_and_sources_have_no_collector(self):
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        server = schema["servers"]["templates"]["server"]["items"]
        self.assertNotIn("spark_file_max_age", schema)
        self.assertNotIn("spark_file", server)
        self.assertNotIn("tps_source", server)
        for filename in ("config.default.json", "config.example.json"):
            raw = json.loads((ROOT / filename).read_text(encoding="utf-8"))
            self.assertEqual(Settings(copy.deepcopy(raw)).raw, raw)
        self.assertNotIn("companion", DIRECTORIES)
        self.assertFalse((ROOT / "companion").exists())
        self.assertFalse((ROOT / "tools/build_spark_agent.py").exists())
        self.assertFalse((ROOT / "tests/java").exists())
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertNotIn("setup-java", workflow)
        self.assertNotIn("  agent:", workflow)
