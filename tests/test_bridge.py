"""Bridge isolation, safe encoding and real filesystem tailing regressions."""

import asyncio
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from logger_stub import logger_stub
from test_core import FakeRcon

from mc_manager.bridge import LogTail, legacy_components, parse_game_event, tellraw_command
from mc_manager.config import Actor, MCError, Settings
from mc_manager.service import Service


def log(content, thread="Server thread", category=""):
    return f"[12:34:56] [{thread}/INFO]{category}: {content}\n"


class BridgeEncodingTests(unittest.TestCase):
    def test_recognizes_all_four_event_types(self):
        for content, kind, player in (
            ("<Steve> hello §a世界", "chat", "Steve"),
            ("[Not Secure] <Alex> hi", "chat", "Alex"),
            ("Steve joined the game", "join", "Steve"),
            ("Alex left the game", "leave", "Alex"),
            ("Steve was slain by Zombie", "death", "Steve"),
            ("Alex hit the ground too hard", "death", "Alex"),
            ("Steve drowned", "death", "Steve"),
            ("Steve was obliterated by a sonically-charged shriek", "death", "Steve"),
        ):
            with self.subTest(content=content):
                event = parse_game_event(log(content))
                self.assertEqual((event.kind, event.player), (kind, player))
                self.assertNotIn("§", event.message)

    def test_paper_async_and_forge_category_logs(self):
        for thread, category in (("Async Chat Thread - #1", ""), ("Server thread", " [minecraft/MinecraftServer]")):
            self.assertEqual(parse_game_event(log("<Steve> hello", thread, category)).kind, "chat")

    def test_chat_cannot_spoof_join_or_death(self):
        self.assertEqual(parse_game_event(log("<Steve> Alex joined the game")).kind, "chat")
        self.assertEqual(parse_game_event(log("<Steve> Alex was slain by Steve")).kind, "chat")

    def test_console_rcon_and_plugin_lines_are_ignored(self):
        for line in (
            "Steve joined the game",
            log("[Rcon: hello]"),
            log("[Plugin] Steve joined the game"),
            log("<Steve> hi", "RCON Client /127.0.0.1"),
            log("Steve lost connection: Disconnected"),
            "[12:00:00] [Server thread/WARN]: Steve joined the game",
            log("Steve was promoted to operator"),
        ):
            with self.subTest(line=line):
                self.assertIsNone(parse_game_event(line))

    def test_legacy_color_style_and_reset(self):
        parts = legacy_components("hi §bblue§lbold§rplain§cred")
        self.assertEqual(parts[1]["color"], "aqua")
        self.assertTrue(parts[2]["bold"])
        self.assertFalse(parts[3]["bold"])
        self.assertEqual(parts[3]["color"], "white")
        self.assertEqual(parts[4]["color"], "red")

    def test_hex_colors_and_all_style_flags(self):
        parts = legacy_components("§x§1§2§A§b§0§Fhex§k§l§m§n§ostyle")
        self.assertEqual(parts[0]["color"], "#12ab0f")
        self.assertTrue(all(parts[1][key] for key in ("obfuscated", "bold", "strikethrough", "underlined", "italic")))

    def test_json_injection_and_newlines_are_only_message_data(self):
        content = 'hi "]} /stop\nstop\r\x00 @a {"clickEvent":{"action":"run_command","value":"op Steve"}}'
        command = tellraw_command(content)
        self.assertTrue(command.startswith("tellraw @a "))
        self.assertNotIn("\n", command)
        self.assertNotIn("\r", command)
        self.assertNotIn("\x00", command)
        data = json.loads(command.removeprefix("tellraw @a "))
        self.assertNotIn("clickEvent", data["extra"][0])
        self.assertIn("op Steve", data["extra"][0]["text"])

    def test_color_flood_and_unicode_fit_real_rcon_packet(self):
        command = tellraw_command("§a字§b🙂" * 1500)
        self.assertLessEqual(len(command.encode("utf-8")), 4080)
        json.loads(command.removeprefix("tellraw @a "))


class LogTailTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "latest.log"
        self.tail = LogTail(self.root, "latest.log")

    def tearDown(self):
        self.temp.cleanup()

    def append(self, data):
        with self.path.open("ab") as stream:
            stream.write(data)

    def test_skips_history_but_reads_new_lines(self):
        self.path.write_bytes(b"history\n")
        self.assertEqual(self.tail.read(), [])
        self.append(b"new\n")
        self.assertEqual(self.tail.read(), ["new"])
        self.assertEqual(self.tail.read(), [])

    def test_new_file_after_missing_log_is_read_from_start(self):
        self.assertEqual(self.tail.read(), [])
        self.path.write_bytes(b"new\n")
        self.assertEqual(self.tail.read(), ["new"])

    def test_partial_utf8_and_crlf_are_preserved(self):
        self.path.write_bytes(b"")
        self.tail.read()
        encoded = "中文🙂".encode()
        self.append(encoded[:5])
        self.assertEqual(self.tail.read(), [])
        self.append(encoded[5:] + b"\r\n")
        self.assertEqual(self.tail.read(), ["中文🙂"])

    def test_incomplete_historical_line_is_not_replayed(self):
        self.path.write_bytes(b"old")
        self.tail.read()
        self.append(b" continuation\nnew\n")
        self.assertEqual(self.tail.read(), ["new"])

    def test_rotation_reads_new_file_once(self):
        self.path.write_bytes(b"old\n")
        self.tail.read()
        self.path.rename(self.root / "rotated.log")
        self.path.write_bytes(b"replacement\n")
        self.assertEqual(self.tail.read(), ["replacement"])
        self.assertEqual(self.tail.read(), [])

    def test_truncation_and_equal_size_rewrite_are_detected(self):
        self.path.write_bytes(b"old message\n")
        self.tail.read()
        self.path.write_bytes(b"new message\n")
        self.assertEqual(self.tail.read(), ["new message"])
        self.path.write_bytes(b"short\n")
        self.assertEqual(self.tail.read(), ["short"])

    def test_oversize_line_is_dropped_without_losing_next_line(self):
        self.path.write_bytes(b"")
        self.tail.read()
        self.append(b"a" * 10000)
        self.assertEqual(self.tail.read(), [])
        self.append(b"discarded\nvalid\n")
        self.assertEqual(self.tail.read(), ["valid"])
        self.assertLessEqual(len(self.tail.partial), 8192)

    def test_directory_cannot_be_used_as_a_log(self):
        self.path.mkdir()
        with self.assertRaises(MCError):
            self.tail.read()

    def test_windows_chinese_log_encoding(self):
        self.path.write_bytes(b"")
        tail = LogTail(self.root, "latest.log", "gb18030")
        tail.read()
        self.append("中文消息\n".encode("gb18030"))
        self.assertEqual(tail.read(), ["中文消息"])

    def test_dynamic_symlink_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "outside.log"
            target.write_text("secret\n", encoding="utf-8")
            try:
                self.path.symlink_to(target)
            except OSError:
                # Windows can require elevated privileges to create a symlink.
                # Exercise the same post-resolution boundary with a redirected
                # resolve result when a real link is unavailable.
                original = Path.resolve

                def resolve(path, *args, **kwargs):
                    return target if path == self.path else original(path, *args, **kwargs)

                with patch.object(Path, "resolve", resolve), self.assertRaises(MCError):
                    self.tail.read()
            else:
                with self.assertRaises(MCError):
                    self.tail.read()


class BridgeServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for name in ("s1", "s2", "s3"):
            (self.root / name / "logs").mkdir(parents=True)
            (self.root / name / "logs/latest.log").write_text(log("<Steve> history"), encoding="utf-8")
        self.raw = {
            "server_root": str(self.root),
            "bridge_enabled": True,
            "bridge_cooldown_seconds": 0,
            "servers": [
                {"id": sid, "rcon_port": 25575 + index, "rcon_password": "secret"}
                for index, sid in enumerate(("s1", "s2", "s3"))
            ],
            "groups": [
                {
                    "id": "g1",
                    "platform_id": "bot",
                    "group_id": "100,101",
                    "server_ids": ["s1", "s2"],
                    "bridge_enabled": True,
                },
                {
                    "id": "g2",
                    "platform_id": "bot",
                    "group_id": "200",
                    "server_ids": ["s1", "s3"],
                    "bridge_enabled": True,
                },
                {"id": "g3", "platform_id": "other", "group_id": "100", "server_ids": ["s1"], "bridge_enabled": False},
            ],
        }
        self.sent = []

        async def send(origin, text):
            self.sent.append((origin, text))
            return True

        FakeRcon.commands = []
        FakeRcon.offline = set()
        self.service = Service(Settings(self.raw), self.root / "data", send, FakeRcon, logger=logger_stub())
        for gid, actual in (("g1", "100"), ("g1", "101"), ("g2", "200"), ("g3", "100")):
            platform = "other" if gid == "g3" else "bot"
            self.service.store.remember(gid, f"{platform}:GroupMessage:{actual}", actual)
        self.actor = Actor("bot", "100", "user", "昵称", False, "bot:GroupMessage:100")
        await self.service.bridge_poll()

    async def asyncTearDown(self):
        await self.service.close()
        self.temp.cleanup()

    def append(self, sid, content):
        with (self.root / sid / "logs/latest.log").open("a", encoding="utf-8") as stream:
            stream.write(log(content))

    async def events(self, sid, content):
        self.append(sid, content)
        await self.service.bridge_poll()
        await self.service.bridge_flush()

    async def test_group_only_targets_directly_bound_servers(self):
        self.assertEqual(self.service.relay_group(self.actor, "§ahello"), 2)
        await self.service.bridge_flush()
        self.assertEqual({sid for sid, _ in FakeRcon.commands}, {"s1", "s2"})
        self.assertEqual(self.sent, [])
        data = json.loads(FakeRcon.commands[0][1].removeprefix("tellraw @a "))
        self.assertIn("昵称", "".join(part["text"] for part in data["extra"]))
        self.assertEqual(data["extra"][-1]["color"], "green")

    async def test_shared_server_targets_enabled_groups_and_all_actual_ids(self):
        await self.events("s1", "<Steve> hello")
        self.assertEqual(
            {origin for origin, _ in self.sent},
            {"bot:GroupMessage:100", "bot:GroupMessage:101", "bot:GroupMessage:200"},
        )

    async def test_exclusive_server_does_not_leak_to_other_group(self):
        await self.events("s2", "Steve joined the game")
        self.assertEqual({origin for origin, _ in self.sent}, {"bot:GroupMessage:100", "bot:GroupMessage:101"})

    async def test_all_event_templates_and_plain_color_removal(self):
        for content, expected in (
            ("<Steve> §c你好", "<Steve> 你好"),
            ("Steve joined the game", "Steve 加入了游戏"),
            ("Steve left the game", "Steve 离开了游戏"),
            ("Steve fell from a high place", "Steve fell from a high place"),
        ):
            self.sent.clear()
            await self.events("s2", content)
            self.assertEqual(len(self.sent), 2)
            self.assertIn(expected, self.sent[0][1])
            self.assertNotIn("§", self.sent[0][1])

    async def test_each_game_event_switch_and_direction(self):
        for flag, content in (
            ("bridge_chat", "<Steve> hi"),
            ("bridge_join", "Steve joined the game"),
            ("bridge_leave", "Steve left the game"),
            ("bridge_death", "Steve drowned"),
            ("bridge_to_group", "<Steve> hi"),
            ("bridge_enabled", "<Steve> hi"),
        ):
            raw = copy.deepcopy(self.raw)
            raw["groups"][0][flag] = False
            self.service.settings = Settings(raw)
            self.sent.clear()
            await self.events("s2", content)
            self.assertEqual(self.sent, [], flag)

    async def test_disabled_group_and_wrong_platform_cannot_forward(self):
        for actor in (
            Actor("other", "100", "user", origin="other:GroupMessage:100"),
            Actor("bot", "999", "user", origin="bot:GroupMessage:999"),
            Actor("bot", "", "user"),
            Actor("bot", "100", "", origin="bot:GroupMessage:100"),
        ):
            self.assertEqual(self.service.relay_group(actor, "hello"), 0)
        self.assertEqual(self.service.bridge_queue.qsize(), 0)

    async def test_global_and_group_to_game_switches(self):
        for key in ("bridge_enabled", "bridge_to_game"):
            raw = copy.deepcopy(self.raw)
            raw["groups"][0][key] = False
            self.service.settings = Settings(raw)
            self.assertEqual(self.service.relay_group(self.actor, "hello"), 0)
        self.raw["bridge_enabled"] = False
        self.service.settings = Settings(self.raw)
        self.assertEqual(self.service.relay_group(self.actor, "hello"), 0)

    async def test_prefix_commands_and_custom_alias_are_filtered(self):
        raw = copy.deepcopy(self.raw)
        raw["commands"] = {"prefix": "custom"}
        raw["groups"][0]["bridge_prefix"] = "#mc"
        self.service.settings = Settings(raw)
        for text in ("hello", "#mc", "#mc /stop", "#mc !help", "#mc custom add Steve", "#mc mc add Steve"):
            self.assertEqual(self.service.relay_group(self.actor, text), 0, text)
        self.assertEqual(self.service.relay_group(self.actor, "#mc hello"), 2)
        await self.service.bridge_flush()
        self.assertNotIn("#mc", FakeRcon.commands[0][1])

    async def test_dedup_and_per_user_per_actual_group_cooldown(self):
        self.service.settings.bridge_cooldown_seconds = 2
        self.assertEqual(self.service.relay_group(self.actor, "hello", "msg1"), 2)
        self.assertEqual(self.service.relay_group(self.actor, "hello", "msg1"), 0)
        self.assertEqual(self.service.relay_group(self.actor, "next", "msg2"), 0)
        other = Actor("bot", "101", "user", origin="bot:GroupMessage:101")
        self.assertEqual(self.service.relay_group(other, "hello", "msg1"), 2)

    async def test_game_only_group_records_actual_adapter_session(self):
        raw = copy.deepcopy(self.raw)
        raw["groups"][0]["bridge_to_game"] = False
        self.service.settings = Settings(raw)
        actor = Actor("bot", "101", "user", origin="bot:GroupMessage:isolated_101")
        self.assertEqual(self.service.relay_group(actor, "hello"), 0)
        self.assertEqual(self.service.broadcast_origins(self.service.settings.groups["g1"])["101"], actor.origin)

    async def test_queue_is_bounded_and_expired_messages_are_dropped(self):
        for _ in range(220):
            self.service._bridge_enqueue("group", "bot:GroupMessage:100", "hi")
        self.assertEqual(self.service.bridge_queue.qsize(), 200)
        self.assertEqual(self.service.bridge_stats["dropped"], 20)
        with patch("mc_manager.bridge.time.monotonic", return_value=time.monotonic() + 31):
            await self.service.bridge_flush()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.service.bridge_stats["dropped"], 70)

    async def test_offline_target_does_not_block_other_server(self):
        FakeRcon.offline.add("s1")
        self.service.relay_group(self.actor, "hello")
        await self.service.bridge_flush()
        self.assertEqual([sid for sid, _ in FakeRcon.commands], ["s2"])
        self.assertEqual(self.service.bridge_stats["failed"], 1)
        self.assertEqual(self.service.bridge_stats["sent"], 1)
        self.service.logger.debug.assert_called_once_with("MC 消息互通投递失败：%s", "test offline")

    async def test_failed_group_send_does_not_block_other_groups(self):
        original = self.service.send

        async def send(origin, text):
            return False if origin.endswith(":100") else await original(origin, text)

        self.service.send = send
        await self.events("s1", "<Steve> hello")
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.service.bridge_stats["failed"], 1)

    async def test_configurable_templates_use_actual_group_and_ids(self):
        self.service.settings.messages["bridge_join"] = "{server_id}/{group}/{event}/{player}"
        await self.events("s2", "Steve joined the game")
        self.assertEqual({text for _, text in self.sent}, {"s2/100/join/Steve", "s2/101/join/Steve"})

    async def test_rcon_echo_does_not_loop_back_into_groups(self):
        await self.events("s1", "[Rcon: [群 100] 昵称: hello]")
        await self.events("s1", "[Not Secure] [群 100] 昵称: hello")
        self.assertEqual(self.sent, [])

    async def test_restart_in_progress_drops_game_messages(self):
        self.service.restart_tasks["s1"] = asyncio.create_task(asyncio.sleep(0))
        self.assertEqual(self.service.relay_group(self.actor, "hello"), 1)
        await self.service.bridge_flush()
        self.assertEqual([sid for sid, _ in FakeRcon.commands], ["s2"])

    async def test_lifecycle_starts_pauses_and_resumes_bridge_workers(self):
        self.service.resume()
        tasks = (self.service.bridge_poll_task, self.service.bridge_delivery_task)
        self.assertTrue(all(task is not None for task in tasks))
        await asyncio.sleep(0)
        await self.service.pause()
        self.assertTrue(all(task.done() for task in tasks))
        self.service.resume()
        self.assertIsNot(self.service.bridge_poll_task, tasks[0])
        await self.service.close()
        self.assertTrue(self.service.bridge_poll_task.done())

    async def test_turning_off_global_switch_blocks_poll_and_queued_delivery(self):
        self.service.relay_group(self.actor, "hello")
        self.append("s1", "<Steve> hi")
        self.service.settings.bridge_enabled = False
        await self.service.bridge_poll()
        await self.service.bridge_flush()
        self.assertEqual(self.sent, [])
        self.assertEqual(FakeRcon.commands, [])

    async def test_default_disabled_spawns_no_bridge_workers(self):
        await self.service.close()
        raw = copy.deepcopy(self.raw)
        raw.pop("bridge_enabled")
        self.service = Service(Settings(raw), self.root / "disabled", self.service.send, FakeRcon, logger=logger_stub())
        self.service.resume()
        self.assertIsNone(self.service.bridge_poll_task)
        self.assertIsNone(self.service.bridge_delivery_task)
        self.assertFalse(self.service.bridge_snapshot()["enabled"])

    async def test_bad_log_path_does_not_block_healthy_server(self):
        with patch.object(self.service.bridge_tails["s1"], "read", side_effect=PermissionError("test denied")):
            await self.events("s2", "<Steve> hello")
        self.assertEqual(len(self.sent), 2)
        self.assertIn("test denied", self.service.bridge_snapshot()["logs"]["s1"])

    def test_bridge_configuration_validation(self):
        for key, value in (
            ("bridge_enabled", "false"),
            ("bridge_poll_seconds", 0),
            ("bridge_max_chars", 2001),
            ("bridge_cooldown_seconds", -1),
        ):
            raw = copy.deepcopy(self.raw)
            raw[key] = value
            with self.subTest(key=key), self.assertRaises(MCError):
                Settings(raw)
        for key, value in (("bridge_log_path", "../outside.log"), ("bridge_log_encoding", "unknown")):
            raw = copy.deepcopy(self.raw)
            raw["servers"][0][key] = value
            with self.subTest(key=key), self.assertRaises(MCError):
                Settings(raw)
        for key, value in (("bridge_enabled", "false"), ("bridge_prefix", "bad prefix")):
            raw = copy.deepcopy(self.raw)
            raw["groups"][0][key] = value
            with self.subTest(key=key), self.assertRaises(MCError):
                Settings(raw)


if __name__ == "__main__":
    unittest.main()
