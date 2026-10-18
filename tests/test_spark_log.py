"""Forge spark-worker console reports with empty RCON, without a JVM agent."""

import asyncio
import copy
import struct
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_monitoring as fixture
from test_rcon import packet
from test_spark import EXPECTED, SAMPLE

from mc_manager.config import MCError, Settings
from mc_manager.monitoring import query_values
from mc_manager.rcon import Rcon
from mc_manager.spark import parse_spark_tps
from mc_manager.spark_log import MAX_BYTES, SparkLogCapture

SCREENSHOT = """TPS from last 5s, 10s, 1m, 5m, 15m:
*20.0, *20.0, *20.0, *20.0, *20.0

Tick durations (min/med/95%ile/max ms) from last 10s, 1m:
0.8/0.9/1.0/2.4; 0.8/0.9/1.1/3.1

CPU usage from last 10s, 1m, 15m:
1%, 1%, 0%  (system)
0%, 0%, 0%  (process)
"""


def forge_report(report=SAMPLE, thread="spark-worker-pool-1-thread-2", encoding="utf-8"):
    return "".join(
        f"[17:32:57] [{thread}/INFO] [minecraft/MinecraftServer]: §e[?] §r{line}\n" for line in report.splitlines()
    ).encode(encoding)


class SparkLogParserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "s1/logs").mkdir(parents=True)
        self.server = Settings({"server_root": str(self.root), "servers": [{"id": "s1"}]}).servers["s1"]
        self.path = self.root / "s1/logs/latest.log"
        self.path.write_bytes(b"")
        self.capture = SparkLogCapture(self.server)
        self.capture.mark()

    def tearDown(self):
        self.temp.cleanup()

    def append(self, data):
        with self.path.open("ab") as stream:
            stream.write(data)

    def test_screenshot_forge_stars_colors_and_all_19_metrics(self):
        self.append(forge_report(SCREENSHOT))
        self.capture.read()
        values = parse_spark_tps(self.capture.report())
        self.assertEqual(values.status, "完整")
        self.assertEqual(values.values["tps_5s"], "20.0")
        self.assertEqual(values.values["mspt_1m_p95"], "1.1")
        self.assertEqual(values.values["cpu_process_15m"], "0")
        self.assertNotIn("§", self.capture.report())

    def test_preexisting_full_report_and_partial_line_are_skipped(self):
        self.path.write_bytes(forge_report() + b"historical half line")
        self.capture.mark()
        self.append(b" finish\n")
        self.capture.read()
        self.assertEqual(self.capture.report(), "")
        self.append(forge_report(SCREENSHOT))
        self.capture.read()
        self.assertEqual(parse_spark_tps(self.capture.report()).values["tps_5s"], "20.0")

    def test_multibyte_and_crlf_split_across_reads(self):
        data = forge_report().replace(b"\n", b"\r\n")
        split = data.index("§".encode()) + 1
        self.append(data[:split])
        self.capture.read()
        self.assertEqual(self.capture.report(), "")
        self.append(data[split:])
        self.capture.read()
        self.assertEqual(parse_spark_tps(self.capture.report()).values, EXPECTED)

    def test_windows_gb18030_encoding(self):
        self.capture = SparkLogCapture(replace(self.server, spark_log_encoding="gb18030"))
        self.capture.mark()
        self.append(forge_report(encoding="gb18030"))
        self.capture.read()
        self.assertEqual(parse_spark_tps(self.capture.report()).values, EXPECTED)

    def test_paper_console_and_server_thread_shapes(self):
        for prefix in ("[17:32:57 INFO]: [spark] ", "[17:32:57] [Server thread/INFO]: [spark] "):
            with self.subTest(prefix=prefix):
                self.capture = SparkLogCapture(self.server)
                self.capture.mark()
                self.append("".join(prefix + line + "\n" for line in SAMPLE.splitlines()).encode())
                self.capture.read()
                self.assertEqual(parse_spark_tps(self.capture.report()).values, EXPECTED)

    def test_player_chat_and_rcon_echo_cannot_spoof_report(self):
        for prefix in (
            "[17:32:57] [Server thread/INFO]: <Steve> ",
            "[17:32:57] [Async Chat Thread - #1/INFO]: <Steve> ",
            "[17:32:57] [RCON Client /127.0.0.1/INFO]: ",
        ):
            self.append("".join(prefix + line + "\n" for line in SAMPLE.splitlines()).encode())
        self.capture.read()
        self.assertEqual(self.capture.report(), "")

    def test_different_worker_cannot_fill_missing_tps_values(self):
        self.append(forge_report("TPS from last 5s, 10s, 1m, 5m, 15m:\n"))
        self.append(forge_report("20, 20, 20, 20, 20\n", thread="spark-worker-pool-1-thread-3"))
        self.capture.read()
        self.assertEqual(self.capture.report(), "")

    def test_competing_reports_rejected_instead_of_merged(self):
        self.append(forge_report() + forge_report(SCREENSHOT, thread="spark-worker-pool-1-thread-3"))
        with self.assertRaisesRegex(MCError, "多份"):
            self.capture.read()

    def test_rotation_and_truncate_do_not_replay_history(self):
        self.path.rename(self.path.with_suffix(".old"))
        self.path.write_bytes(forge_report())
        with self.assertRaisesRegex(MCError, "轮转"):
            self.capture.read()
        self.capture = SparkLogCapture(self.server)
        self.capture.mark()
        self.path.write_bytes(b"")
        with self.assertRaisesRegex(MCError, "截断"):
            self.capture.read()

    def test_rewrite_of_same_length_is_rejected(self):
        self.path.write_bytes(b"original\n")
        self.capture.mark()
        self.path.write_bytes(b"replaced\n")
        with self.assertRaisesRegex(MCError, "重写"):
            self.capture.read()

    def test_scan_volume_is_bounded(self):
        self.append(b"a" * (MAX_BYTES + 1))
        with self.assertRaisesRegex(MCError, "256 KB"):
            for _ in range(5):
                self.capture.read()
        self.assertLessEqual(len(self.capture.pending), 8192)

    def test_paths_and_regular_file_requirement(self):
        for relative in ("../other/latest.log", str(self.root / "outside.log"), "logs"):
            with self.subTest(relative=relative), self.assertRaises(MCError):
                SparkLogCapture(replace(self.server, spark_log_path=relative)).mark()
        original = Path.is_symlink
        with (
            patch.object(Path, "is_symlink", lambda path: path == self.path or original(path)),
            self.assertRaises(MCError),
        ):
            SparkLogCapture(self.server).mark()


class SparkLogQueryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.MonitoringTests.asyncSetUp
    asyncTearDown = fixture.MonitoringTests.asyncTearDown
    apply = fixture.MonitoringTests.apply

    def prepare(self, sid="s2"):
        path = self.root / sid / "logs/latest.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(forge_report(SCREENSHOT))  # Deliberately old history.
        self.service.settings.servers[sid] = replace(self.service.settings.servers[sid], spark_log_wait_seconds=0.15)
        return path

    def append(self, path, data):
        with path.open("ab") as stream:
            stream.write(data)

    async def test_empty_rcon_reads_current_report_and_populates_source_variables(self):
        path = self.prepare()
        self.responses["s2", "spark tps"] = lambda: self.append(path, forge_report()) or ""
        result = await self.service.query("s2", "tps")
        self.assertEqual(parse_spark_tps(result).values, EXPECTED)
        self.assertEqual(query_values(result)["tps_source"], "log")
        self.assertEqual(result.info.source, "服务端新增日志")
        self.assertTrue(result.info.rcon_online)
        self.assertEqual(self.commands, [("s2", "spark tps")])
        self.service.settings.messages["tps"] = "{query_source}|{tps_source}|{tps_5s}|{query_command}"
        self.assertEqual(self.service.format_tps("s2", result), "服务端新增日志|log|13.24|spark tps")

    async def test_delayed_async_report_can_arrive_after_rcon_returns(self):
        path = self.prepare()
        self.service.settings.servers["s2"] = replace(self.service.settings.servers["s2"], spark_log_wait_seconds=1)

        async def write():
            await asyncio.sleep(0.02)
            self.append(path, forge_report())

        pending = []

        def response():
            pending.append(asyncio.create_task(write()))
            return ""

        self.responses["s2", "spark tps"] = response
        try:
            result = await self.service.query("s2", "tps")
        finally:
            await asyncio.gather(*pending)
        self.assertEqual(parse_spark_tps(result).values, EXPECTED)

    async def test_native_spark_command_returns_log_report_without_applying_template(self):
        path = self.prepare("s1")
        self.service.settings.servers["s1"] = replace(self.service.settings.servers["s1"], tps_command="spark tps")
        self.responses["s1", "spark tps"] = lambda: self.append(path, forge_report()) or ""
        self.service.settings.messages["tps"] = "DO_NOT_RENDER"
        text = await self.service.execute(self.actor, "tps", ["s1"])
        self.assertIn("TPS from last", text)
        self.assertIn("CPU usage", text)
        self.assertNotIn("DO_NOT_RENDER", text)
        self.assertNotIn("§", text)

    async def test_native_auto_detect_accepts_log_before_trying_next_command(self):
        path = self.prepare("s1")
        self.service.settings.servers["s1"] = replace(
            self.service.settings.servers["s1"], tps_command="spark tps", tps_auto_detect=True
        )
        self.responses["s1", "spark tps"] = lambda: self.append(path, forge_report()) or ""
        text = await self.service.query("s1", "tps")
        self.assertEqual(text.info.source, "服务端新增日志")
        self.assertEqual(self.commands, [("s1", "spark tps")])
        self.assertEqual(self.service.tps_capabilities["s1"][1], "spark tps")

    async def test_missing_log_does_not_break_valid_rcon(self):
        result = await self.service.query("s2", "tps")
        self.assertEqual(result.info.source, "RCON")
        self.assertEqual(parse_spark_tps(result).values, EXPECTED)
        self.assertEqual(result.info.warning, "")

    async def test_only_old_history_times_out_instead_of_returning_stale_report(self):
        self.prepare()
        self.responses["s2", "spark tps"] = ""
        result = await self.service.query("s2", "tps")
        self.assertIn("未发现本次", result)
        self.assertNotIn("20.0", result)
        self.assertEqual(result.info.source, "RCON")

    async def test_disable_and_unknown_command_skip_log_wait(self):
        path = self.prepare()
        self.service.settings.servers["s2"] = replace(self.service.settings.servers["s2"], spark_log_fallback=False)
        self.responses["s2", "spark tps"] = lambda: self.append(path, forge_report()) or ""
        self.assertEqual((await self.service.query("s2", "tps")).info.source, "RCON")
        self.service.settings.servers["s2"] = replace(self.service.settings.servers["s2"], spark_log_fallback=True)
        self.responses["s2", "spark tps"] = lambda: self.append(path, forge_report()) or "Unknown command"
        result = await self.service.query("s2", "tps")
        self.assertEqual(result.info.source, "RCON")
        self.assertIn("不支持", result.info.error)

    async def test_network_failure_never_reads_preexisting_report(self):
        self.prepare()
        self.responses["s2", "spark tps"] = OSError("refused")
        result = await self.service.query("s2", "tps")
        self.assertFalse(result.info.rcon_online)
        self.assertNotIn("20.0", result)

    async def test_shared_queries_issue_one_spark_command(self):
        path = self.prepare()
        self.responses["s2", "spark tps"] = lambda: self.append(path, forge_report()) or ""
        results = await asyncio.gather(*(self.service.cached_query("s2", "tps") for _ in range(5)))
        self.assertTrue(all(result.info.source == "服务端新增日志" for result in results))
        self.assertEqual(self.commands, [("s2", "spark tps")])
        self.assertTrue((await self.service.cached_query("s2", "tps")).info.cached)

    async def test_cancellation_closes_waiter_and_releases_server_lock(self):
        self.prepare()
        self.service.settings.servers["s2"] = replace(self.service.settings.servers["s2"], spark_log_wait_seconds=10)
        self.responses["s2", "spark tps"] = ""
        started = asyncio.Event()
        original = SparkLogCapture.wait

        async def wait(capture, seconds):
            started.set()
            return await original(capture, seconds)

        with patch.object(SparkLogCapture, "wait", wait):
            task = asyncio.create_task(self.service.cached_query("s2", "tps"))
            await asyncio.wait_for(started.wait(), 2)
            await self.service.pause()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(self.service.server_locks["s2"].locked())
        self.assertEqual(self.service.query_inflight, {})

    async def test_log_permission_failure_is_not_reported_as_rcon_offline(self):
        self.responses["s2", "spark tps"] = ""
        with patch.object(SparkLogCapture, "mark", side_effect=PermissionError("test denied")):
            result = await self.service.query("s2", "tps")
        self.assertTrue(result.info.rcon_online)
        self.assertIn("test denied", result.info.warning)
        self.assertEqual(self.service.last_status["s2"], "在线")

    async def test_log_values_are_server_scoped(self):
        path = self.prepare("s1")
        self.responses["s2", "spark tps"] = lambda: self.append(path, forge_report()) or ""
        result = await self.service.query("s2", "tps")
        self.assertNotEqual(result.info.source, "服务端新增日志")
        self.assertNotIn("13.24", result)

    def test_config_path_encoding_and_wait_validation(self):
        for key, value in (
            ("spark_log_fallback", "false"),
            ("spark_log_path", "../other.log"),
            ("spark_log_path", ""),
            ("spark_log_encoding", "bad"),
            ("spark_log_wait_seconds", 0),
            ("spark_log_wait_seconds", 16),
        ):
            raw = copy.deepcopy(self.raw)
            raw["servers"][1][key] = value
            with self.subTest(key=key), self.assertRaises(MCError):
                Settings(raw)

    async def test_real_rcon_empty_frame_and_async_forge_file_report(self):
        path = self.prepare()
        handlers, writers = set(), []

        async def write_log():
            await asyncio.sleep(0.02)
            self.append(path, forge_report(SCREENSHOT))

        async def handle(reader, writer):
            task = asyncio.current_task()
            handlers.add(task)
            try:
                while True:
                    length = struct.unpack("<i", await reader.readexactly(4))[0]
                    data = await reader.readexactly(length)
                    rid, kind = struct.unpack("<ii", data[:8])
                    command = data[8:-2].decode()
                    if kind == 3:
                        writer.write(packet(rid, 2, b""))
                    else:
                        self.commands.append(("s2", command))
                        writer.write(packet(rid, 0, b"The time is 123" if command == "time query gametime" else b""))
                        if command == "spark tps":
                            writers.append(asyncio.create_task(write_log()))
                    await writer.drain()
            except (asyncio.IncompleteReadError, ConnectionError):
                pass
            finally:
                writer.close()
                await writer.wait_closed()
                handlers.discard(task)

        listener = await asyncio.start_server(handle, "127.0.0.1", 0)
        self.service.settings.servers["s2"] = replace(
            self.service.settings.servers["s2"], host="127.0.0.1", port=listener.sockets[0].getsockname()[1]
        )
        self.service.rcon_factory = Rcon
        try:
            result = await self.service.query("s2", "tps")
            self.assertEqual(result.info.source, "服务端新增日志")
            self.assertEqual(parse_spark_tps(result).values["tps_5s"], "20.0")
            self.assertEqual([command for _, command in self.commands], ["spark tps", "time query gametime"])
        finally:
            listener.close()
            await listener.wait_closed()
            await asyncio.gather(*writers, return_exceptions=True)
            tasks = list(handlers)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
