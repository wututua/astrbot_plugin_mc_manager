import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_spark import SAMPLE

from mc_manager.config import Actor, MCError, Settings
from mc_manager.service import Service
from mc_manager.text import message_chunks


class TPSModeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for sid in ["s1", "s2"]:
            (self.root / sid).mkdir()
        self.responses = {"s1": "TPS from last 1m, 5m, 15m:\n20.0, 19.9, 19.8\n", "s2": SAMPLE}
        self.commands = []
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
                result = owner.responses[inner.server.id]
                if isinstance(result, Exception):
                    raise result
                return result

        self.raw = {
            "server_root": str(self.root),
            "servers": [
                {"id": "s1", "tps_mode": "native"},
                {"id": "s2", "rcon_port": 25576, "tps_mode": "spark"},
            ],
            "groups": [{"id": "g1", "platform_id": "bot", "group_id": "100", "server_ids": ["s1", "s2"]}],
        }

        async def send(*_):
            return True

        self.service = Service(Settings(self.raw), self.root / "data", send, Client)
        self.actor = Actor("bot", "100", "member")

    async def asyncTearDown(self):
        await self.service.close()
        self.temp.cleanup()

    def apply(self):
        self.service.settings = Settings(self.raw)

    async def test_native_default_command_and_complete_output(self):
        result = await self.service.execute(self.actor, "tps", ["s1"])
        self.assertEqual(result, self.responses["s1"])
        self.assertEqual(self.commands, [("s1", "tps")])
        self.assertEqual(self.service.last_status["s1"], "在线")

    async def test_missing_mode_preserves_existing_spark_behavior(self):
        del self.raw["servers"][1]["tps_mode"]
        self.apply()
        result = await self.service.execute(self.actor, "tps", ["s2"])
        self.assertEqual(self.service.settings.servers["s2"].tps_mode, "spark")
        self.assertEqual(self.commands, [("s2", "spark tps")])
        self.assertIn("13.24 / 13.37", result)

    async def test_native_custom_command_is_used_verbatim(self):
        self.raw["servers"][0]["tps_command"] = "mycore:health detail"
        self.apply()
        self.assertEqual(await self.service.execute(self.actor, "tps", ["s1"]), self.responses["s1"])
        self.assertEqual(self.commands, [("s1", "mycore:health detail")])

    async def test_native_ignores_spark_templates_even_for_spark_shaped_reply(self):
        self.responses["s1"] = SAMPLE
        self.raw["messages"] = {
            "tps": "SHOULD_NOT_RENDER {tps_5s}",
            "tps_warning": "NO_WARNING",
            "tps_error": "NO_SPARK_ERROR",
        }
        self.apply()
        with patch("mc_manager.service.parse_spark_tps", side_effect=AssertionError("native must not parse spark")):
            result = await self.service.execute(self.actor, "tps", ["s1"])
        self.assertEqual(result, SAMPLE)

    async def test_native_ignores_local_collector_and_spark_only_settings(self):
        self.raw["servers"][0].update(tps_source="file", spark_file="../outside.txt")
        self.apply()
        with patch("mc_manager.service.read_spark_report", side_effect=AssertionError("native must not read files")):
            result = await self.service.execute(self.actor, "tps", ["s1"])
        self.assertEqual(result, self.responses["s1"])
        self.assertEqual(self.commands, [("s1", "tps")])

    async def test_native_returns_every_line_past_old_query_and_template_limits(self):
        text = "  BEGIN {{custom}} {tps_5s}\n" + "原生服务器性能指标 19.97 ms\n" * 1800 + "END\n"
        self.responses["s1"] = text
        result = await self.service.execute(self.actor, "tps", ["s1"])
        self.assertGreater(len(text), 16000)
        self.assertEqual(result, text)
        self.assertTrue(result.endswith("END\n"))

    async def test_native_unknown_command_is_returned_without_a_spark_error(self):
        self.responses["s1"] = "Unknown or incomplete command, see below for error\ntps<--[HERE]\n"
        self.assertEqual(await self.service.execute(self.actor, "tps", ["s1"]), self.responses["s1"])

    async def test_native_empty_response_is_explicit_without_collector_advice(self):
        self.raw["servers"][0]["tps_command"] = "spark tps"
        self.apply()
        for value in ["", "   \n"]:
            with self.subTest(value=value):
                self.responses["s1"] = value
                self.assertEqual(await self.service.execute(self.actor, "tps", ["s1"]), "服务端返回空响应")

    async def test_native_connection_failure_remains_an_rcon_error(self):
        self.responses["s1"] = OSError("connection refused")
        result = await self.service.execute(self.actor, "tps", ["s1"])
        self.assertIn("离线或 RCON 不可用：connection refused", result)
        self.assertNotIn("spark TPS", result)

    async def test_native_sanitizes_colors_without_truncating_text(self):
        text = "§aTPS\n\x1b[31m" + "19.99 " * 5000 + "\x1b[0m"
        self.responses["s1"] = text
        expected = "TPS\n" + "19.99 " * 5000
        self.assertEqual(await self.service.execute(self.actor, "tps", ["s1"]), expected)

    async def test_mixed_server_query_formats_only_spark(self):
        self.raw["messages"] = {"tps": "SPARK {server_id}: {tps_5s}"}
        self.apply()
        result = await self.service.execute(self.actor, "tps", [])
        self.assertEqual(result, self.responses["s1"] + "\nSPARK s2: 13.24")
        self.assertEqual(self.commands, [("s1", "tps"), ("s2", "spark tps")])

    async def test_multiple_native_servers_keep_full_ordered_replies(self):
        self.raw["servers"][1]["tps_mode"] = "native"
        self.apply()
        self.responses["s1"] = "FIRST\n" * 3000
        self.responses["s2"] = "SECOND\n" * 3000
        result = await self.service.execute(self.actor, "tps", [])
        self.assertEqual(result, self.responses["s1"] + "\n" + self.responses["s2"])

    async def test_native_cannot_query_an_unbound_group(self):
        with self.assertRaisesRegex(MCError, "未绑定"):
            await self.service.execute(Actor("bot", "200", "member"), "tps", ["s1"])
        self.assertEqual(self.commands, [])

    def test_invalid_mode_is_rejected(self):
        for value in ["other", "", None, True, ["native"], {"mode": "spark"}]:
            raw = copy.deepcopy(self.raw)
            raw["servers"][0]["tps_mode"] = value
            with self.subTest(value=value), self.assertRaisesRegex(MCError, "TPS 查询方式"):
                Settings(raw)

    def test_native_query_still_rejects_multiline_or_empty_commands(self):
        for command in ["", "tps\nstop", "tps\x00", "x" * 513]:
            raw = copy.deepcopy(self.raw)
            raw["servers"][0]["tps_command"] = command
            with self.subTest(command=command), self.assertRaisesRegex(MCError, "查询命令"):
                Settings(raw)

    def test_reply_chunking_preserves_all_text_and_empty_template(self):
        text = ("性能 {tps_5s}\n" * 3000) + "END"
        chunks = message_chunks(text)
        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(chunk) <= 3500 for chunk in chunks))
        self.assertEqual(message_chunks(""), [""])
