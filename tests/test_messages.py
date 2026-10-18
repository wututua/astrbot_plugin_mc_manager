"""User-visible templates are rendered without changing canonical control state."""

import ast
import copy
import json
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_core as core

from mc_manager.config import MESSAGES, MCError, Settings, message_templates
from mc_manager.message_catalog import EXTRA_MESSAGES, join_text, text
from mc_manager.monitoring import QueryInfo, QueryResult
from mc_manager.performance import parse_performance, performance_values

ROOT = Path(__file__).resolve().parents[1]
SPARK = (
    "TPS from last 5s, 10s, 1m, 5m, 15m:\n13.24, 13.37, 15.95, 19.03, 19.67\n"
    "Tick durations (min/med/95%ile/max ms) from last 10s, 1m:\n69.1/74.3/79.6/103.2; 31.1/64.6/77.0/103.5\n"
    "CPU usage from last 10s, 1m, 15m:\n33%, 35%, 35% (system)\n14%, 14%, 15% (process)"
)


class MessageCatalogTests(unittest.TestCase):
    def test_defaults_schema_catalog_and_docs_cover_all_messages(self):
        defaults = json.loads((ROOT / "config.default.json").read_text("utf-8"))
        schema = json.loads((ROOT / "_conf_schema.json").read_text("utf-8"))["messages"]["items"]
        catalog = json.loads((ROOT / "messages.variables.json").read_text("utf-8"))["templates"]
        self.assertEqual(defaults["messages"], MESSAGES)
        self.assertEqual(catalog, message_templates())
        self.assertEqual(set(schema), set(MESSAGES))
        docs = (ROOT / "docs/MESSAGES.md").read_text("utf-8")
        for key, default in MESSAGES.items():
            self.assertEqual(schema[key]["default"], default, key)
            self.assertIn("`" + key + "`", docs)
        Settings(defaults)
        Settings(json.loads((ROOT / "config.example.json").read_text("utf-8")))

    def test_all_literal_runtime_message_keys_exist(self):
        for path in [ROOT / "main.py", *(ROOT / "mc_manager").glob("*.py")]:
            if path.name == "message_catalog.py":
                continue
            for node in ast.walk(ast.parse(path.read_text("utf-8"))):
                if (
                    not isinstance(node, ast.Call)
                    or not isinstance(node.func, ast.Name)
                    or node.func.id != "message_text"
                ):
                    continue
                if node.args and isinstance(node.args[0], ast.Constant):
                    self.assertIn(node.args[0].value, EXTRA_MESSAGES, f"{path}:{node.lineno}")

    def test_errors_use_explicit_templates_instead_of_string_matching(self):
        for path in [ROOT / "main.py", *(ROOT / "mc_manager").glob("*.py")]:
            for node in ast.walk(ast.parse(path.read_text("utf-8"))):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in {"MCError", "WhitelistReadError"}
                    and node.args
                ):
                    self.assertNotIsInstance(node.args[0], (ast.Constant, ast.JoinedStr), f"{path}:{node.lineno}")

    def test_nested_text_and_exception_keep_canonical_value(self):
        nested = MCError(text("error_entry_owner"))
        settings = Settings({"messages": {"error_entry_owner": "DENIED", "query_offline": "OFF: {error}"}})
        value = QueryResult(text("query_offline", error=nested), QueryInfo(error=nested))
        self.assertIn("只能修改", str(value))
        self.assertEqual(settings.localize(value.cached_copy()), "OFF: DENIED")
        self.assertEqual(settings.localize(join_text(" / ", [text("yes"), text("no")])), "是 / 否")

    def test_unknown_exceptions_can_hide_system_details(self):
        settings = Settings({"messages": {"error_unknown": "请联系管理员"}})
        self.assertEqual(settings.localize(MCError("OS details")), "请联系管理员")
        self.assertEqual(str(MCError("OS details")), "OS details")

    def test_braces_in_values_are_literal_and_invalid_fields_are_rejected(self):
        settings = Settings({"messages": {"sync_pending": "{{{server}}} {error}"}})
        self.assertEqual(
            settings.render("sync_pending", server="s", error="{player.__class__}"), "{s} {player.__class__}"
        )
        for value in ("{player.__class__}", "{player[0]}", "{unknown_variable}", "{server!r}", "{seconds:2d}"):
            with self.subTest(value=value), self.assertRaises(MCError):
                Settings({"messages": {"sync_pending": value}})

    def test_missing_templates_do_not_reset_custom_existing_text(self):
        settings = Settings({"messages": {"added": "自定义 {player}", "tps": "自定义 {tps_1m}"}})
        self.assertEqual(settings.messages["added"], "自定义 {player}")
        self.assertEqual(settings.messages["tps"], "自定义 {tps_1m}")
        self.assertEqual(settings.messages["sync_success"], MESSAGES["sync_success"])


class MessageServiceTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = core.CoreTests.asyncSetUp
    asyncTearDown = core.CoreTests.asyncTearDown
    execute = core.CoreTests.execute
    file_names = core.CoreTests.file_names

    def configure(self, **messages):
        raw = copy.deepcopy(self.raw)
        raw["messages"] = messages
        self.service.settings = Settings(raw)
        self.service.settings.poll_interval = 0.001
        self.service.settings.restart_timeout = 1

    async def test_success_nested_sync_list_empty_and_server_rows(self):
        self.configure(
            added="ADD {player} #{id}\n{sync}",
            sync_success="OK {server}",
            empty_whitelist="EMPTY",
            server_entry="ID={server_id}",
        )
        self.assertIn("EMPTY", await self.execute("list"))
        self.assertEqual(await self.execute("servers"), "本群服务器：\nID=s1\nID=s2")
        self.assertEqual(await self.execute("add", ["Steve"]), "ADD Steve #1\nOK s1\nOK s2")

    async def test_custom_owner_error_keeps_remove_and_modify_denied(self):
        self.configure(error_entry_owner="NO ACCESS", error="ERR {error}")
        await self.execute("add", ["Steve"])
        for action, args in (("remove", ["1"]), ("modify", ["1", "Alex"])):
            with self.subTest(action=action), self.assertRaises(MCError) as caught:
                await self.execute(action, args, self.other)
            self.assertEqual(self.service.settings.render("error", error=caught.exception), "ERR NO ACCESS")
            self.assertEqual(self.file_names("s1"), ["Steve"])
        self.assertIn("已移除", await self.execute("remove", ["1"], self.admin))

    async def test_unbound_and_admin_errors_are_customizable(self):
        self.configure(error_group_unbound="UNBOUND", error_admin_required="ADMIN")
        for actor, action, expected in (
            (self.group2, "logs", "ADMIN"),
            (core.Actor("bot", "999", "member"), "help", "UNBOUND"),
        ):
            with self.assertRaises(MCError) as caught:
                await self.service.execute(actor, action, [])
            self.assertEqual(self.service.settings.localize(caught.exception), expected)

    async def test_empty_reply_does_not_cancel_whitelist_change(self):
        self.configure(added="", sync_success="")
        self.assertEqual(await self.execute("add", ["Steve"]), "")
        self.assertEqual(self.file_names("s1"), ["Steve"])

    async def test_invalid_player_and_page_errors_are_customizable(self):
        self.configure(error_config_002="INVALID PLAYER", error_config_004="INVALID {label}")
        for action, args, expected in (("add", ["bad name"], "INVALID PLAYER"), ("list", ["x"], "INVALID 页码")):
            with self.assertRaises(MCError) as caught:
                await self.execute(action, args)
            self.assertEqual(self.service.settings.localize(caught.exception), expected)

    async def test_corrupt_whitelist_sync_reason_and_disk_fragment(self):
        await self.execute("add", ["Steve"])
        (self.root / "s1/whitelist.json").write_text("broken", "utf-8")
        key = next(key for key, default in EXTRA_MESSAGES.items() if "白名单损坏或无法读取" in default)
        self.configure(
            **{key: "BROKEN {id}", "sync_pending": "WAIT {server}|{error}|{detail}", "sync_disk_failed": "NO WRITE"}
        )
        result = await self.execute("sync", actor=self.admin)
        self.assertIn("WAIT s1|BROKEN s1|NO WRITE", result)
        self.assertEqual((self.root / "s1/whitelist.json").read_text("utf-8"), "broken")

    async def test_status_cache_and_alert_detection_use_canonical_state(self):
        self.configure(status="{status}|{query_cached}|{rcon_online}", status_online="UP", yes="Y", no="N")
        first = await self.execute("status", ["s1"])
        second = await self.execute("status", ["s1"])
        self.assertEqual(first, "UP|N|Y")
        self.assertEqual(second, "UP|Y|Y")
        self.assertEqual(self.service.last_status["s1"], "在线")

    async def test_connection_failure_can_customize_both_wrapper_and_detail(self):
        self.configure(status="{result}", query_offline="DOWN {error}", error_unknown="ADMIN NEEDED")
        core.FakeRcon.offline.add("s1")
        self.assertEqual(await self.execute("status", ["s1"]), "DOWN ADMIN NEEDED")
        self.assertEqual(self.service.last_status["s1"], "离线或 RCON 不可用")

    async def test_tps_nested_lines_labels_and_missing_text(self):
        self.configure(
            tps="{perf_tps_line}\n{perf_tick_lines}\n{perf_cpu_lines}\n{perf_memory_line}",
            perf_tps_windows_line="RATE {windows}={values}",
            perf_window_5s="5sec",
            perf_label_min="MIN",
            perf_memory_line="MEM {perf_memory}",
            perf_missing="N/A",
        )
        result = self.service.format_tps("s1", SPARK)
        self.assertIn("RATE 5sec / 10秒", result)
        self.assertIn("MIN / 中位", result)
        self.assertIn("MEM N/A", result)
        self.assertNotIn("⚡", result)

    async def test_raw_tps_wrapper_preserves_long_reply(self):
        self.configure(raw_tps="[{server_id}]\n{result}")
        self.service.settings.servers["s1"] = replace(self.service.settings.servers["s1"], tps_output="raw")
        raw = "a" * 18000 + "\nEND {player}"
        self.assertEqual(self.service.format_tps("s1", QueryResult(raw)), "[s1]\n" + raw)

    async def test_diagnostics_and_request_empty_fragments(self):
        self.configure(
            diagnostics="{result}",
            diagnostic_server="S {server_id} {writable}/{readable}",
            diagnostic_worker="W {worker_state} {error}",
            no_error="NONE",
            boolean_true="T",
            requests="{entries}",
            empty_requests="NO REQUESTS",
        )
        self.service.worker_health["sync"] = dict(state="waiting", last_error="")
        result = await self.execute("diagnose", ["s1"], self.admin)
        self.assertEqual(result, "S s1 T/T\nW waiting NONE")
        self.assertEqual(await self.execute("requests"), "NO REQUESTS")

    async def test_log_action_and_new_audit_detail_templates(self):
        self.configure(
            audit_add_1="CREATED {id}/{uuid}",
            logs="{entries}",
            log_entry="{action}:{detail}",
            action_add="ADD",
            log_detail="DETAIL {detail}",
        )
        await self.execute("add", ["Steve"])
        result = await self.execute("logs", actor=self.admin)
        self.assertIn("ADD:DETAIL CREATED 1/", result)
        self.assertIn("CREATED 1/", self.service.store.logs("g1", 1, 10)[0][0]["detail"])

    async def test_alert_labels_details_and_queued_empty_notification(self):
        self.configure(alert="{alert_type}|{alert_detail}", alert_type_disk="DISK", alert_disk_detail="FREE={free_mb}")
        self.service.store.remember("g1", self.user.origin, "100")
        for _ in range(self.service.settings.alert_consecutive):
            self.service.evaluate_alert("s1", "磁盘空间不足", True, text("alert_disk_detail", free_mb=10))
        await self.service.deliver()
        self.assertEqual(self.sent[0][1], "DISK|FREE=10")

    async def test_custom_parse_states_and_tick_target_remain_distinct(self):
        self.configure(
            tps="{perf_parse_status}|{perf_tick_state}|{perf_target_rate}|{perf_tps}",
            parse_complete="PARSED",
            perf_state_normal="NORMAL",
        )
        raw = "The game is running normally. Target tick rate: 20 per second; Average time per tick: 12.3ms"
        result = self.service.format_tps("s1", raw)
        self.assertEqual(result, "PARSED|NORMAL|20|暂无数据")
        parsed = parse_performance(raw)
        self.assertNotIn("perf_tps", parsed.values)
        self.assertEqual(str(performance_values(parsed, raw)["perf_tick_state"]), "正常")

    async def test_native_probe_failure_and_timeout_text_are_deferred(self):
        self.configure(
            query_native_failure="NO NATIVE {entries}",
            query_command_failure="CMD={query_command}:{error}",
            query_timeout="SLOW {seconds}",
        )
        server = self.service.settings.servers["s1"]
        self.service.settings.servers["s1"] = replace(server, tps_auto_detect=True, tps_output="raw")

        async def unsupported(client, command):
            return "Unknown command"

        with patch.object(core.FakeRcon, "command", unsupported):
            result = await self.service.query("s1", "tps")
        self.assertTrue(self.service.format_tps("s1", result).startswith("NO NATIVE CMD="))
        with patch.object(self.service, "_query_rcon", side_effect=TimeoutError):
            result = await self.service.query("s1", "tps")
        self.assertIn("SLOW", self.service.format_tps("s1", result))

    async def test_restart_countdown_group_and_game_broadcasts_are_independent(self):
        self.configure(
            restart_countdown="CHAT {server}/{seconds}",
            game_restart_countdown="GAME {seconds}",
            restart_success="READY {server}",
            game_restart_success="WELCOME {server}",
        )
        self.service.settings.restart_countdown = 1
        await self.execute("restart", ["s1"], self.admin)
        await self.service.restart_tasks["s1"]
        game = [
            json.loads(command.removeprefix("tellraw @a "))["text"]
            for sid, command in core.FakeRcon.commands
            if command.startswith("tellraw @a ")
        ]
        self.assertEqual(game, ["GAME 1", "WELCOME s1"])
        self.assertIn("READY s1", [message for _, message in self.sent])
        self.assertIn("CHAT s1/1", [message for _, message in self.sent])
        self.assertIn(("s1", "stop"), core.FakeRcon.commands)

    async def test_empty_restart_notification_is_consumed_without_sending(self):
        self.configure(restart_success="", game_restart_success="")
        await self.execute("restart", ["s1"], self.admin)
        await self.service.restart_tasks["s1"]
        self.assertEqual(self.sent, [])
        self.assertEqual(self.service.store.outbox(), [])
        self.assertEqual(self.service.store.jobs()[0]["status"], "success")

    async def test_restart_failure_reason_is_customizable(self):
        self.configure(restart_failed="FAIL {server}/{error}", restart_timeout="BOOT TIMEOUT")
        core.FakeRcon.disconnected = False
        await self.execute("restart", ["s1"], self.admin)
        await self.service.restart_tasks["s1"]
        self.assertIn("FAIL s1/BOOT TIMEOUT", [message for _, message in self.sent])

    async def test_approval_row_kind_and_default_rejection(self):
        self.configure(
            requests="{entries}",
            request_entry="{request_kind}/{player}/{user}",
            request_kind_add="NEW",
            default_rejection="TRY AGAIN",
            request_rejected="REJECT {detail}",
        )
        self.service.settings.groups["g1"] = replace(self.service.settings.groups["g1"], approval_required=True)
        await self.execute("add", ["Steve"])
        self.assertEqual(await self.execute("requests"), "NEW/Steve/user1")
        self.assertEqual(await self.execute("reject", ["1"], self.admin), "REJECT TRY AGAIN")
        self.assertEqual(self.service.store.entries("g1"), [])

    async def test_rcon_template_error_remains_json_serializable_in_status_and_diagnostics(self):
        self.configure(error_rcon_005="AUTH", query_offline="DOWN {error}", status="{result}")
        with patch.object(core.FakeRcon, "__aenter__", side_effect=MCError(text("error_rcon_005"))):
            result = await self.service.query("s1", "status")
            self.assertIsInstance(result.info.error, str)
            self.assertEqual(self.service.format_query("s1", "status", result), "DOWN AUTH")
            json.dumps(self.service.snapshot())
            json.dumps(await self.service.diagnostics(self.service.settings.groups["g1"]))
