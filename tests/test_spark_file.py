"""Optional Java 8 smoke test plus local-source freshness and isolation checks."""

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from test_spark import EXPECTED, SAMPLE

from mc_manager.config import Actor, MCError, Settings
from mc_manager.files import read_spark_report
from mc_manager.service import Service
from mc_manager.spark import parse_spark_tps

ROOT = Path(__file__).resolve().parent.parent
COMPILER = ROOT / ".research/agent/ecj-3.26.0.jar"
AGENT = ROOT / "companion/astrbot-spark-agent.jar"


class SparkFileTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "s1").mkdir()
        self.raw = {
            "server_root": str(self.root),
            "servers": [{"id": "s1", "directory": "s1"}],
            "groups": [{"id": "g1", "platform_id": "bot", "group_id": "100", "server_ids": ["s1"]}],
        }
        self.commands = []
        owner = self

        class Client:
            def __init__(self, server, timeout):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                pass

            async def command(self, command):
                owner.commands.append(command)
                return ""

        async def send(*_):
            return True

        self.service = Service(Settings(self.raw), self.root / "data", send, Client)
        self.actor = Actor("bot", "100", "member")
        self.path = self.root / "s1" / ".astrbot-spark-tps.txt"

    async def asyncTearDown(self):
        await self.service.close()
        self.temp.cleanup()

    async def test_auto_prefers_fresh_spark_api_file(self):
        self.path.write_text(SAMPLE, encoding="utf-8")
        result = await self.service.execute(self.actor, "tps", [])
        self.assertIn("13.24 / 13.37", result)
        self.assertEqual(self.commands, [])

    async def test_file_mode_does_not_issue_rcon(self):
        self.raw["servers"][0]["tps_source"] = "file"
        self.service.settings = Settings(self.raw)
        result = await self.service.execute(self.actor, "tps", [])
        self.assertIn("未发现本地", result)
        self.assertEqual(self.commands, [])

    async def test_stale_snapshot_is_never_used(self):
        self.path.write_text(SAMPLE, encoding="utf-8")
        stamp = time.time() - 120
        os.utime(self.path, (stamp, stamp))
        self.raw["servers"][0]["tps_source"] = "file"
        self.service.settings = Settings(self.raw)
        result = await self.service.execute(self.actor, "tps", [])
        self.assertIn("过期", result)
        self.assertNotIn("13.24", result)

    async def test_rcon_mode_ignores_file_and_explains_async_empty_reply(self):
        self.path.write_text(SAMPLE, encoding="utf-8")
        self.raw["servers"][0]["tps_source"] = "rcon"
        self.service.settings = Settings(self.raw)
        result = await self.service.execute(self.actor, "tps", [])
        self.assertEqual(self.commands, ["spark tps"])
        self.assertIn("异步", result)
        self.assertNotIn("13.24", result)

    async def test_cross_server_report_path_is_rejected(self):
        self.raw["servers"][0]["spark_file"] = "../other-server/report.txt"
        with self.assertRaisesRegex(MCError, "本服"):
            Settings(self.raw)

    async def test_unready_agent_does_not_fabricate_values(self):
        self.path.write_text("Spark API unavailable: not ready", encoding="utf-8")
        result = await self.service.execute(self.actor, "tps", [])
        self.assertIn("查询失败", result)
        self.assertNotIn("13.24", result)

    async def test_oversized_and_future_dated_reports_are_rejected(self):
        server = self.service.settings.servers["s1"]
        self.path.write_text("x" * (128 * 1024 + 1), encoding="utf-8")
        with self.assertRaisesRegex(MCError, "128"):
            read_spark_report(server, 10)
        self.path.write_text(SAMPLE, encoding="utf-8")
        stamp = time.time() + 60
        os.utime(self.path, (stamp, stamp))
        with self.assertRaisesRegex(MCError, "未来"):
            read_spark_report(server, 10)


@unittest.skipUnless(
    shutil.which("java") and (shutil.which("javac") or COMPILER.is_file()) and AGENT.is_file(),
    "requires Java and javac or the development compiler"
)
class SparkAgentTests(unittest.TestCase):
    def test_real_jvm_agent_reflection_and_atomic_report_match_all_values(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            classes = directory / "classes"
            source = ROOT / "tests" / "java"
            javac = shutil.which("javac")
            compiler = [javac, "-source", "8", "-target", "8"] if javac else ["java", "-jar", str(COMPILER), "-1.8"]
            compile_run = subprocess.run(
                [*compiler, "-encoding", "UTF-8", "-d", str(classes), *[str(p) for p in source.rglob("*.java")]],
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(compile_run.returncode, 0, compile_run.stderr)
            run = subprocess.run(
                ["java", "-javaagent:" + str(AGENT), "-cp", str(classes), "me.lucko.spark.api.SparkProvider"],
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            report = directory / ".astrbot-spark-tps.txt"
            self.assertTrue(report.is_file(), run.stdout)
            self.assertIn("spark API ready", run.stdout)
            parsed = parse_spark_tps(report.read_text(encoding="utf-8"))
            self.assertEqual(parsed.values, EXPECTED)
            self.assertEqual(parsed.status, "完整")
            self.assertEqual(list(directory.glob(".astrbot-spark-*.tmp")), [])
