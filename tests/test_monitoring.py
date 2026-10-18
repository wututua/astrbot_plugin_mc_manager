"""Monitoring accuracy, bounded queries and per-result variable isolation."""

import asyncio
import copy
import inspect
import tempfile
import unittest
from pathlib import Path

from logger_stub import logger_stub
from test_spark import SAMPLE

from mc_manager.config import Actor, MCError, Settings
from mc_manager.monitoring import command_error, parse_players, query_values
from mc_manager.service import Service


class MonitoringParserTests(unittest.TestCase):
    def test_online_players_and_explicit_zero(self):
        values = parse_players("There are 2 of a max of 20 players online: Steve, Alex")
        self.assertEqual(
            (values["players_online"], values["players_max"], values["players_names"]), ("2", "20", "Steve, Alex")
        )
        self.assertEqual(parse_players("There are 0 of a max of 20 players online:")["players_names"], "无")
        self.assertEqual(parse_players("当前有 2 名玩家在线，最大玩家数为 20：Steve, Alex")["players_online"], "2")

    def test_unknown_data_is_not_zero_and_names_are_not_command_errors(self):
        self.assertEqual(parse_players("custom unparsed reply")["players_online"], "暂无数据")
        self.assertFalse(command_error("There are 1 of a max of 20 players online: UnknownCommand"))
        for text in (
            "Unknown or incomplete command, see below",
            "You do not have permission to use this command",
            "未知命令",
            "Incorrect argument for command",
        ):
            self.assertTrue(command_error(text), text)

    def test_monitoring_fields_and_configuration_validation(self):
        Settings(
            {
                "messages": {
                    "status": "{players_online}/{players_max} {query_source} {query_latency_ms} {query_cached}",
                    "tps": "{tps_source}/{tps_file_age_seconds}/{rcon_online}",
                }
            }
        )
        for key, value in (
            ("query_timeout_seconds", -1),
            ("query_timeout_seconds", True),
            ("tps_capability_cache_seconds", 86401),
        ):
            with self.subTest(key=key), self.assertRaises(MCError):
                Settings({key: value})


class MonitoringTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for sid in ("s1", "s2"):
            (self.root / sid).mkdir()
            (self.root / sid / "whitelist.json").write_text("[]", encoding="utf-8")
        self.raw = {
            "server_root": str(self.root),
            "servers": [
                {"id": "s1", "tps_mode": "native", "tps_output": "raw", "rcon_password": "secret"},
                {"id": "s2", "tps_mode": "spark", "rcon_port": 25576, "rcon_password": "secret"},
            ],
            "groups": [{"id": "g1", "platform_id": "bot", "group_id": "100", "server_ids": ["s1", "s2"]}],
        }
        self.commands = []
        self.responses = {
            ("s1", "tps"): "TPS from last 1m, 5m, 15m: 20.0, 19.9, 19.8",
            ("s2", "spark tps"): SAMPLE,
            **{(sid, "list"): "There are 2 of a max of 20 players online: Steve, Alex" for sid in ("s1", "s2")},
        }
        owner = self

        class Client:
            def __init__(inner, server, timeout):
                inner.server = server

            async def __aenter__(inner):
                return inner

            async def __aexit__(inner, *_):
                pass

            async def command(inner, command):
                owner.commands.append((inner.server.id, command))
                result = owner.responses.get((inner.server.id, command), "Unknown command")
                if callable(result):
                    result = result()
                if inspect.isawaitable(result):
                    result = await result
                if isinstance(result, Exception):
                    raise result
                return result

        async def send(*_):
            return True

        self.service = Service(
            Settings(copy.deepcopy(self.raw)), self.root / "data", send, Client, logger=logger_stub()
        )
        self.actor = Actor("bot", "100", "user")
        self.path = self.root / "s2" / ".astrbot-spark-tps.txt"

    async def asyncTearDown(self):
        await self.service.close()
        self.temp.cleanup()

    def apply(self):
        self.service.settings = Settings(copy.deepcopy(self.raw))
        self.service.invalidate_queries("s1")
        self.service.invalidate_queries("s2")

    async def test_status_template_has_players_and_query_metadata(self):
        self.service.settings.messages["status"] = (
            "{server_id}|{status}|{players_online}/{players_max}|{players_names}|{query_source}|{rcon_online}"
        )
        text = await self.service.execute(self.actor, "status", ["s1"])
        self.assertEqual(text, "s1|在线（RCON 查询成功）|2/20|Steve, Alex|RCON|是")

    async def test_unsupported_status_is_not_success_or_offline(self):
        self.responses["s1", "list"] = "Unknown or incomplete command"
        result = await self.service.query("s1", "status")
        self.assertNotIn("查询成功", result)
        self.assertEqual(self.service.last_status["s1"], "RCON 可达，查询指令异常")
        self.assertEqual(query_values(result)["rcon_online"], "是")
        self.assertTrue(result.info.error)

    async def test_empty_status_is_not_success(self):
        self.responses["s1", "list"] = ""
        result = await self.service.query("s1", "status")
        self.assertEqual(result, "服务端返回空响应")
        self.assertNotEqual(self.service.last_status["s1"], "在线")

    async def test_network_failure_has_no_invented_players(self):
        self.responses["s1", "list"] = OSError("refused")
        self.service.settings.messages["status"] = "{status}|{players_online}|{rcon_online}"
        result = await self.service.execute(self.actor, "status", ["s1"])
        self.assertIn("暂无数据|否", result)
        self.assertIn("离线或 RCON 不可用", result)

    async def test_status_and_players_share_transport_and_cache_is_immutable(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def response():
            started.set()
            await release.wait()
            return "There are 2 of a max of 20 players online: Steve, Alex"

        self.responses["s1", "list"] = response
        status = asyncio.create_task(self.service.cached_query("s1", "status"))
        await started.wait()
        players = asyncio.create_task(self.service.cached_query("s1", "players"))
        await asyncio.sleep(0)
        release.set()
        first, second = await asyncio.gather(status, players)
        self.assertIn("查询成功", first)
        self.assertTrue(second.startswith("There are 2"))
        self.assertEqual(self.commands, [("s1", "list")])
        cached = await self.service.cached_query("s1", "status")
        self.assertTrue(cached.info.cached)
        self.assertFalse(first.info.cached)
        self.assertFalse(second.info.cached)
        # A later unrelated failure must not rewrite this result's state.
        self.service.last_status["s1"] = "离线或 RCON 不可用"
        self.service.settings.messages["players"] = "{status}|{players_online}"
        self.assertEqual(self.service.format_query("s1", "players", second), "在线（RCON 查询成功）|2")
        self.assertEqual(self.service.server_health["s1"]["players_command"], "list")

    async def test_fresh_probe_bypasses_cache(self):
        await self.service.cached_query("s1", "status")
        await self.service.cached_query("s1", "status", fresh=True)
        self.assertEqual(self.commands, [("s1", "list"), ("s1", "list")])

    async def test_native_auto_detect_and_capability_cache(self):
        self.raw["servers"][0]["tps_auto_detect"] = True
        self.apply()
        self.responses["s1", "tps"] = "Unknown command"
        self.responses["s1", "forge tps"] = "Dim minecraft:overworld: Mean tick time: 10 ms. Mean TPS: 20"
        first = await self.service.query("s1", "tps")
        self.assertEqual(first.info.command, "forge tps")
        self.assertEqual(self.commands, [("s1", "tps"), ("s1", "forge tps")])
        self.commands.clear()
        second = await self.service.query("s1", "tps")
        self.assertEqual(first, second)
        self.assertEqual(self.commands, [("s1", "forge tps")])
        self.service.invalidate_queries("s1")
        self.assertNotIn("s1", self.service.tps_capabilities)

    async def test_custom_command_changes_invalidate_learned_choice(self):
        self.raw["servers"][0]["tps_auto_detect"] = True
        self.apply()
        await self.service.query("s1", "tps")
        self.raw["servers"][0]["tps_command"] = "custom:perf"
        self.service.settings = Settings(self.raw)
        self.responses["s1", "custom:perf"] = "custom report"
        self.commands.clear()
        self.assertEqual(await self.service.query("s1", "tps"), "custom report")
        self.assertEqual(self.commands, [("s1", "custom:perf")])

    async def test_tick_query_is_raw_and_not_reported_as_measured_tps(self):
        self.raw["servers"][0]["tps_auto_detect"] = True
        self.apply()
        self.responses["s1", "tps"] = "Unknown command"
        self.responses["s1", "tick query"] = "Target tick rate: 20; Average time per tick: 12.3ms"
        self.assertEqual(await self.service.execute(self.actor, "tps", ["s1"]), self.responses["s1", "tick query"])
        self.assertEqual([command for _, command in self.commands], ["tps", "forge tps", "tick query"])

    async def test_all_unsupported_has_explicit_failure_and_read_only_commands(self):
        self.raw["servers"][0]["tps_auto_detect"] = True
        self.apply()
        self.responses["s1", "tps"] = "Unknown command"
        result = await self.service.query("s1", "tps")
        self.assertIn("未找到可用", result)
        self.assertTrue(result.info.error)
        self.assertTrue(all(command in {"tps", "forge tps", "tick query"} for _, command in self.commands))

    async def test_network_error_does_not_trigger_multiple_capability_probes(self):
        self.raw["servers"][0]["tps_auto_detect"] = True
        self.apply()
        self.responses["s1", "tps"] = OSError("refused")
        result = await self.service.query("s1", "tps")
        self.assertFalse(result.info.rcon_online)
        self.assertEqual(self.commands, [("s1", "tps")])

    async def test_total_deadline_includes_server_lock_wait(self):
        self.service.settings.query_timeout_seconds = 0.02
        lock = self.service.server_locks["s1"]
        await lock.acquire()
        try:
            result = await asyncio.wait_for(self.service.query("s1", "status"), 2)
        finally:
            lock.release()
        self.assertIn("含排队", result)
        self.assertEqual(self.service.last_status["s1"], "查询超时，状态未确认")
        self.assertIsNone(result.info.rcon_online)
        self.assertEqual(self.commands, [])

    async def test_spark_template_receives_metadata_without_cross_server_leak(self):
        self.service.settings.messages["tps"] = "{server_id}|{query_source}|{query_command}|{tps_5s}|{query_cached}"
        first = await self.service.execute(self.actor, "tps", ["s2"])
        second = await self.service.execute(self.actor, "tps", ["s2"])
        self.assertEqual(first, "s2|RCON|spark tps|13.24|否")
        self.assertEqual(second, "s2|RCON|spark tps|13.24|是")
