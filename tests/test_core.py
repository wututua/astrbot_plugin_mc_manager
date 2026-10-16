import asyncio
import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from mc_manager.config import Actor, MCError, Settings
from mc_manager.files import offline_uuid
from mc_manager.service import Service


class FakeRcon:
    commands = []
    offline = set()
    disconnected = True
    startup_fail = False

    def __init__(self, server, timeout):
        self.server = server

    async def __aenter__(self):
        if self.server.id in self.offline:
            raise OSError("test offline")
        return self

    async def __aexit__(self, *_):
        pass

    async def command(self, command):
        self.commands.append((self.server.id, command))
        if command in {"tps", "spark tps"}:
            return "TPS from last 1m, 5m, 15m: 20.0, 19.9, 20.0"
        if command == "list":
            return "There are 1 of a max of 20 players online: Steve"
        return "Whitelist reloaded"

    async def stop(self):
        self.commands.append((self.server.id, "stop"))
        if self.startup_fail:
            self.offline.add(self.server.id)

    async def wait_disconnect(self, timeout):
        if not self.disconnected:
            raise TimeoutError()


class CoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for name in ["s1", "s2", "s3"]:
            (self.root / name).mkdir()
            (self.root / name / "whitelist.json").write_text("[]", encoding="utf-8")
        self.raw = {
            "server_root": str(self.root),
            "servers": [
                {"id": name, "directory": name, "rcon_port": 25575 + i, "rcon_password": "secret"}
                for i, name in enumerate(["s1", "s2", "s3"])
            ],
            "groups": [
                {
                    "id": "g1",
                    "platform_id": "bot",
                    "group_id": "100",
                    "server_ids": ["s1", "s2"],
                    "admin_ids": ["admin"],
                },
                {"id": "g2", "platform_id": "bot", "group_id": "200", "server_ids": ["s3"], "admin_ids": ["admin2"]},
            ],
        }
        self.sent = []

        async def send(origin, text):
            self.sent.append((origin, text))
            return True

        FakeRcon.commands = []
        FakeRcon.offline = set()
        FakeRcon.disconnected = True
        FakeRcon.startup_fail = False
        self.service = Service(Settings(self.raw), self.root / "data", send, FakeRcon)
        self.service.settings.poll_interval = 0.001
        self.service.settings.restart_timeout = 0.04
        self.user = Actor("bot", "100", "user1", "测试用户", False, "bot:GroupMessage:100")
        self.other = Actor("bot", "100", "user2")
        self.admin = Actor("bot", "100", "admin", "管理员", False, "bot:GroupMessage:100")
        self.group2 = Actor("bot", "200", "user1", "第二群成员", False, "bot:GroupMessage:200")

    async def asyncTearDown(self):
        await self.service.close()
        self.temp.cleanup()

    async def execute(self, action, args=(), actor=None):
        return await self.service.execute(actor or self.user, action, list(args))

    def file_names(self, sid):
        return [r["name"] for r in json.loads((self.root / sid / "whitelist.json").read_text(encoding="utf-8"))]

    async def wait_for_status(self, sid, status):
        async def wait():
            while self.service.last_status.get(sid) != status:
                await asyncio.sleep(0.001)

        await asyncio.wait_for(wait(), 2)

    async def test_start_probes_all_servers_even_if_unbound_offline_or_whitelist_invalid(self):
        raw = copy.deepcopy(self.raw)
        raw["groups"] = raw["groups"][:1]
        self.service.settings = Settings(raw)
        (self.root / "s1" / "whitelist.json").write_text("{broken", encoding="utf-8")
        FakeRcon.offline.add("s2")
        await self.service.start()
        for sid, status in [("s1", "在线"), ("s2", "离线或 RCON 不可用"), ("s3", "在线")]:
            await self.wait_for_status(sid, status)
        await self.service.pause()
        self.assertIn(("s1", "list"), FakeRcon.commands)
        self.assertIn(("s3", "list"), FakeRcon.commands)
        self.assertEqual((self.root / "s1" / "whitelist.json").read_text(encoding="utf-8"), "{broken")
        self.assertEqual(self.service.snapshot()["servers"][1]["status"], "离线或 RCON 不可用")

    async def test_background_probe_detects_offline_and_recovery_without_user_commands(self):
        self.service.settings.sync_interval = 0.01
        await self.service.start()
        await self.wait_for_status("s1", "在线")
        FakeRcon.offline.add("s1")
        await self.wait_for_status("s1", "离线或 RCON 不可用")
        FakeRcon.offline.remove("s1")
        await self.wait_for_status("s1", "在线")
        await self.service.pause()
        self.assertGreaterEqual(FakeRcon.commands.count(("s1", "list")), 2)

    async def test_slow_startup_probe_does_not_block_loading_or_other_servers(self):
        entered, release = asyncio.Event(), asyncio.Event()

        class SlowRcon(FakeRcon):
            async def __aenter__(inner):
                if inner.server.id == "s1":
                    entered.set()
                    await release.wait()
                return await super().__aenter__()

        self.service.rcon_factory = SlowRcon
        try:
            await asyncio.wait_for(self.service.start(), 1)
            await asyncio.wait_for(entered.wait(), 2)
            await self.wait_for_status("s2", "在线")
            await self.wait_for_status("s3", "在线")
            self.assertEqual(self.service.snapshot()["servers"][0]["status"], "探测中")
        finally:
            release.set()
        await self.wait_for_status("s1", "在线")
        await self.service.pause()

    async def test_pause_cancels_pending_probe_and_resume_reprobes(self):
        entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class SlowRcon(FakeRcon):
            async def __aenter__(inner):
                if inner.server.id == "s1":
                    entered.set()
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        cancelled.set()
                        raise
                return await super().__aenter__()

        self.service.rcon_factory = SlowRcon
        await self.service.start()
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.wait_for(self.service.pause(), 2)
        self.assertTrue(cancelled.is_set())
        self.assertIsNone(self.service.background)
        release.set()
        self.service.resume()
        await self.wait_for_status("s1", "在线")
        await self.service.pause()

    async def test_group_union_sync_does_not_touch_other_group(self):
        await self.execute("add", ["Steve"])
        self.assertEqual(self.file_names("s1"), ["Steve"])
        self.assertEqual(self.file_names("s2"), ["Steve"])
        self.assertEqual(self.file_names("s3"), [])
        await self.execute("add", ["Alex"], self.group2)
        self.assertEqual(self.file_names("s3"), ["Alex"])
        self.assertEqual(self.file_names("s1"), ["Steve"])

    async def test_own_entry_only_and_admin_override(self):
        await self.execute("add", ["Steve"])
        for action, args in [("remove", ["1"]), ("modify", ["Steve", "Alex"])]:
            with self.assertRaisesRegex(MCError, "自己的"):
                await self.execute(action, args, self.other)
        await self.execute("remove", ["1"], self.admin)
        self.assertEqual(self.file_names("s1"), [])

    async def test_truthy_non_boolean_admin_flag_cannot_bypass_ownership(self):
        await self.execute("add", ["Steve"])
        before = self.service.store.entries("g1")
        for flag in ["false", "admin", 1]:
            actor = Actor("bot", "100", "user2", admin=flag)
            for action, args in [("remove", ["1"]), ("modify", ["Steve", "Alex"]), ("logs", [])]:
                with self.subTest(flag=flag, action=action), self.assertRaises(MCError):
                    await self.execute(action, args, actor)
                self.assertEqual(self.service.store.entries("g1"), before)

    async def test_missing_sender_identity_is_rejected_even_with_admin_flag(self):
        await self.execute("add", ["Steve"])
        before = self.service.store.entries("g1")
        for user in ["", "   "]:
            actor = Actor("bot", "100", user, admin=True)
            for action, args in [("add", ["Alex"]), ("remove", ["1"]), ("modify", ["1", "Alex"]), ("logs", [])]:
                with self.subTest(user=user, action=action), self.assertRaisesRegex(MCError, "身份"):
                    await self.execute(action, args, actor)
                self.assertEqual(self.service.store.entries("g1"), before)

    async def test_other_group_admin_cannot_remove_or_modify_entries(self):
        await self.execute("add", ["Steve"])
        actor = Actor("bot", "100", "admin2")
        for action, args in [("remove", ["1"]), ("remove", ["Steve"]), ("modify", ["1", "Alex"])]:
            with self.subTest(action=action, args=args), self.assertRaisesRegex(MCError, "自己的"):
                await self.execute(action, args, actor)
        self.assertEqual(self.service.store.entries("g1")[0]["owner"], "user1")

    async def test_owner_change_during_player_lookup_cannot_modify_new_owners_entry(self):
        await self.execute("add", ["Steve"])
        logs_before = self.service.store.logs("g1", 1, 100)

        async def resolve_with_owner_change(group, name):
            with self.service.store.transaction():
                self.service.store.db.execute("UPDATE entries SET owner='user2' WHERE scope='g1' AND id=1")
            return name, offline_uuid(name)

        with patch.object(self.service, "resolve_player", side_effect=resolve_with_owner_change):
            with self.assertRaisesRegex(MCError, "归属|自己的|权限"):
                await self.execute("modify", ["1", "Alex"])
        row = self.service.store.entries("g1")[0]
        self.assertEqual((row["name"], row["owner"]), ("Steve", "user2"))
        self.assertEqual(self.file_names("s1"), ["Steve"])
        self.assertEqual(self.service.store.logs("g1", 1, 100), logs_before)

    async def test_stable_numbers_persist_across_deletion_and_reload(self):
        await self.execute("add", ["Steve"])
        await self.execute("remove", ["1"])
        await self.execute("add", ["Alex"])
        self.assertEqual(self.service.store.entries("g1")[0]["id"], 2)
        await self.service.close()

        async def send(*_):
            return True

        self.service = Service(Settings(self.raw), self.root / "data", send, FakeRcon)
        result = await self.execute("list")
        self.assertIn("#2 Alex", result)

    async def test_modify_keeps_id_and_records_remove_add(self):
        await self.execute("add", ["Steve"])
        await self.execute("modify", ["1", "Alex"])
        row = self.service.store.entries("g1")[0]
        self.assertEqual((row["id"], row["name"], row["owner"]), (1, "Alex", "user1"))
        self.assertEqual(self.file_names("s2"), ["Alex"])
        logs, total = self.service.store.logs("g1", 1, 100)
        self.assertEqual([r["action"] for r in logs[:2]], ["add", "remove"])
        self.assertTrue(all(r["group_id"] == "100" for r in logs))

    async def test_duplicate_and_quota(self):
        await self.execute("add", ["Steve"])
        with self.assertRaisesRegex(MCError, "最多"):
            await self.execute("add", ["Alex"])
        with self.assertRaisesRegex(MCError, "已在"):
            await self.execute("add", ["steve"], self.other)
        self.assertEqual(len(self.service.store.entries("g1")), 1)

    async def test_different_platform_same_group_is_denied(self):
        with self.assertRaisesRegex(MCError, "未绑定"):
            await self.execute("list", actor=Actor("another-bot", "100", "user1"))

    async def test_private_and_unbound_groups_are_denied(self):
        for actor in [Actor("bot", "", "user1"), Actor("bot", "300", "user1")]:
            with self.assertRaises(MCError):
                await self.execute("help", actor=actor)

    async def test_cross_group_server_queries_and_restarts_denied(self):
        for action in ["players", "tps", "status", "restart"]:
            with self.assertRaisesRegex(MCError, "不属于"):
                await self.execute(action, ["s3"], self.admin)
        self.assertFalse(any(s == "s3" for s, _ in FakeRcon.commands))

    async def test_admin_only_actions(self):
        for action, args in [("logs", []), ("sync", []), ("import", []), ("restart", ["s1"])]:
            with self.assertRaisesRegex(MCError, "管理员"):
                await self.execute(action, args)
        self.assertFalse(any(c == "stop" for _, c in FakeRcon.commands))

    async def test_global_admin_can_manage_bound_group(self):
        actor = Actor("bot", "100", "global", admin=True, origin=self.user.origin)
        self.assertIn("暂无日志", await self.execute("logs", actor=actor))

    async def test_initial_union_preserves_unowned_existing_entries(self):
        for sid, name in [("s1", "Steve"), ("s2", "Alex")]:
            (self.root / sid / "whitelist.json").write_text(
                json.dumps([{"name": name, "uuid": offline_uuid(name)}]), encoding="utf-8"
            )
        await self.execute("list")
        rows = self.service.store.entries("g1")
        self.assertEqual({r["name"] for r in rows}, {"Steve", "Alex"})
        self.assertTrue(all(not r["owner"] for r in rows))
        with self.assertRaisesRegex(MCError, "历史导入"):
            await self.execute("remove", ["1"])
        await self.execute("sync", actor=self.admin)
        self.assertEqual(self.file_names("s1"), ["Steve", "Alex"])

    async def test_corrupt_first_import_does_not_overwrite_any_server(self):
        path = self.root / "s2" / "whitelist.json"
        path.write_text("{broken", encoding="utf-8")
        with self.assertRaisesRegex(MCError, "损坏"):
            await self.execute("add", ["Steve"])
        self.assertFalse(self.service.store.initialized("g1"))
        self.assertEqual(self.file_names("s1"), [])
        self.assertEqual(path.read_text(encoding="utf-8"), "{broken")

    async def test_post_import_corruption_is_pending_and_preserved(self):
        await self.execute("list")
        path = self.root / "s2" / "whitelist.json"
        path.write_text("{broken", encoding="utf-8")
        result = await self.execute("add", ["Steve"])
        self.assertIn("待同步", result)
        self.assertEqual(path.read_text(encoding="utf-8"), "{broken")
        self.assertIn("s2", self.service.store.dirty())
        self.assertEqual(self.file_names("s1"), ["Steve"])

    async def test_offline_server_files_written_and_pending_cleared_on_retry(self):
        FakeRcon.offline.add("s2")
        result = await self.execute("add", ["Steve"])
        self.assertIn("待同步", result)
        self.assertEqual(self.file_names("s2"), ["Steve"])
        self.assertIn("s2", self.service.store.dirty())
        FakeRcon.offline.clear()
        await self.execute("sync", actor=self.admin)
        self.assertFalse(self.service.store.dirty())

    async def test_uuid_conflict_rejects_initial_import(self):
        for sid, name in [("s1", "Steve"), ("s2", "Alex")]:
            (self.root / sid / "whitelist.json").write_text(
                json.dumps([{"name": name, "uuid": offline_uuid("Steve")}]), encoding="utf-8"
            )
        with self.assertRaisesRegex(MCError, "UUID"):
            await self.execute("list")
        self.assertEqual(self.service.store.entries("g1"), [])

    async def test_no_periodic_reimport_of_deleted_entries(self):
        await self.execute("add", ["Steve"])
        await self.execute("remove", ["Steve"])
        path = self.root / "s2" / "whitelist.json"
        path.write_text(json.dumps([{"name": "Steve", "uuid": offline_uuid("Steve")}]), encoding="utf-8")
        await self.service.ensure_group(self.service.settings.groups["g1"])
        await self.service.sync_group(self.service.settings.groups["g1"])
        self.assertEqual(self.file_names("s2"), [])
        self.assertEqual(self.service.store.entries("g1"), [])

    async def test_help_aliases_and_custom_messages(self):
        raw = copy.deepcopy(self.raw)
        raw["commands"] = {"prefix": "minecraft", "add": "add", "help": "help,帮助"}
        raw["messages"] = {"help": "欢迎！{prefix} {add} <玩家名>", "added": "加入 {player}，编号 {id}\n{sync}"}
        settings = Settings(raw)
        self.assertEqual(settings.extract("/minecraft add Steve"), "add Steve")
        self.assertIsNone(settings.extract("/minecraftish add Steve"))
        self.assertEqual(settings.route("add Steve"), ("add", ["Steve"]))
        self.assertIn("/minecraft add", settings.render("help"))
        self.assertIn("加入 Steve", settings.render("added", player="Steve", id=1))

    async def test_pagination_and_empty_pages(self):
        with self.assertRaisesRegex(MCError, "页码"):
            await self.execute("list", ["0"])
        with self.assertRaisesRegex(MCError, "超出"):
            await self.execute("list", ["2"])
        with self.assertRaisesRegex(MCError, "参数"):
            await self.execute("remove")

    async def test_injection_is_rejected(self):
        for name in ["Steve;stop", "Steve\nstop", "@a", "ab", "x" * 17]:
            with self.assertRaises(MCError):
                await self.execute("add", [name])
        self.assertFalse(any(c == "stop" for _, c in FakeRcon.commands))

    async def test_logs_filter_and_group_isolation(self):
        await self.execute("add", ["Steve"])
        await self.execute("add", ["Alex"], self.group2)
        await self.execute("remove", ["1"])
        result = await self.execute("logs", ["1", "user1"], self.admin)
        self.assertIn("Steve", result)
        self.assertNotIn("Alex", result)
        self.assertIn("100", result)
        self.assertIn("user1", result)

    async def test_restart_success_and_game_broadcast_are_scoped(self):
        await self.execute("list")
        result = await self.execute("restart", ["s1"], self.admin)
        self.assertIn("正在重启", result)
        tasks = list(self.service.restart_tasks.values())
        await asyncio.gather(*tasks)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][0], self.user.origin)
        self.assertIn("重启成功", self.sent[0][1])
        self.assertEqual([s for s, c in FakeRcon.commands if c == "stop"], ["s1"])
        self.assertTrue(any(c.startswith("tellraw @a ") for s, c in FakeRcon.commands if s == "s1"))
        self.assertFalse(any(s == "s3" for s, c in FakeRcon.commands))

    async def test_restart_old_connection_stays_open_is_failure(self):
        FakeRcon.disconnected = False
        await self.execute("restart", ["s1"], self.admin)
        await asyncio.gather(*list(self.service.restart_tasks.values()))
        self.assertIn("重启失败", self.sent[0][1])
        self.assertNotIn("重启成功", self.sent[0][1])
        self.assertEqual(self.service.store.jobs()[0]["status"], "failed")

    async def test_restart_does_not_start_new_connections_before_disconnect(self):
        await self.execute("restart", ["s1"], self.admin)
        with self.assertRaisesRegex(MCError, "已有"):
            await self.execute("restart", ["s1"], self.admin)
        await asyncio.gather(*list(self.service.restart_tasks.values()))
        self.assertEqual(sum(c == "stop" for s, c in FakeRcon.commands), 1)

    async def test_restart_timeout_when_startup_fails(self):
        FakeRcon.startup_fail = True
        await self.execute("restart", ["s1"], self.admin)
        await asyncio.gather(*list(self.service.restart_tasks.values()))
        self.assertIn("启动超时", self.sent[0][1])
        self.assertEqual(self.service.store.jobs()[0]["status"], "failed")

    async def test_notifications_retry_after_send_failure(self):
        async def fail(*args):
            return False

        self.service.send = fail
        await self.execute("restart", ["s1"], self.admin)
        await asyncio.gather(*list(self.service.restart_tasks.values()))
        self.assertEqual(len(self.service.store.notifications()), 1)
        self.assertEqual(self.service.store.outbox(), [])
        self.service.store.retry_notification(self.service.store.notifications()[0]["id"], "g1")

        async def success(origin, text):
            self.sent.append((origin, text))
            return True

        self.service.send = success
        await self.service.deliver()
        self.assertEqual(self.service.store.outbox(), [])
        self.assertIn("重启成功", self.sent[0][1])

    async def test_interrupted_monitor_recovered_as_failure_not_success(self):
        self.service.store.job("s1", "g1", self.user.origin)
        await self.service.start()
        await self.service.deliver()
        self.assertIn("监控曾中断", self.sent[0][1])
        self.assertNotIn("重启成功", self.sent[0][1])
        self.assertEqual(self.service.store.jobs()[0]["status"], "interrupted")

    async def test_group_number_can_change_with_entries_preserved_and_session_reset(self):
        await self.execute("add", ["Steve"])
        before = self.service.store.entries("g1")
        raw = copy.deepcopy(self.raw)
        raw["groups"][0]["group_id"] = "300"
        updated = Settings(raw)
        self.service.store.validate_scopes(updated)
        self.assertEqual(self.service.store.entries("g1"), before)
        self.assertEqual(self.service.store.origin("g1"), "")
        row = self.service.store.db.execute("SELECT group_id FROM scopes WHERE id='g1'").fetchone()
        self.assertEqual(row[0], "300")
        with self.assertRaisesRegex(MCError, "未绑定"):
            updated.group_for(self.user)
        self.assertEqual(updated.group_for(Actor("bot", "300", "user1")).id, "g1")

    async def test_server_can_move_to_another_group_without_importing_old_members(self):
        await self.execute("add", ["Steve"])
        raw = copy.deepcopy(self.raw)
        raw["groups"][0]["server_ids"] = ["s1"]
        raw["groups"][1]["server_ids"] = ["s2", "s3"]
        updated = Settings(raw)
        self.service.store.validate_scopes(updated)
        self.service.settings = updated
        await self.service.ensure_group(updated.groups["g2"])
        await self.service.sync_group(updated.groups["g2"])
        self.assertEqual(self.file_names("s2"), [])
        self.assertEqual(self.file_names("s1"), ["Steve"])
        self.assertEqual(self.service.store.entries("g1")[0]["name"], "Steve")
        self.assertEqual(self.service.store.entries("g2"), [])
        self.assertEqual(
            self.service.store.db.execute("SELECT scope FROM bindings WHERE server='s2'").fetchone()[0], "g2"
        )

    async def test_backups_and_atomic_writes(self):
        await self.execute("add", ["Steve"])
        backups = list((self.root / "s1" / ".mcwl-backups").glob("*.json"))
        self.assertEqual(json.loads(backups[0].read_text(encoding="utf-8")), [])
        self.assertEqual(list((self.root / "s1").glob(".mcwl-*.tmp")), [])

    async def test_cancelled_disk_write_finishes_before_return(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()

        def slow_write(server, entries):
            started.set()
            release.wait(2)
            finished.set()
            return False

        with patch("mc_manager.service.write_whitelist", side_effect=slow_write):
            task = asyncio.create_task(self.service.disk_write(self.service.settings.servers["s1"], []))
            while not started.is_set():
                await asyncio.sleep(0.001)
            try:
                task.cancel()
                await asyncio.sleep(0.01)
                self.assertFalse(task.done())
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(finished.is_set())

    async def test_invalid_uuid_type_preserves_initial_file(self):
        path = self.root / "s1" / "whitelist.json"
        content = json.dumps([{"name": "Steve", "uuid": {"bad": "type"}}])
        path.write_text(content, encoding="utf-8")
        with self.assertRaisesRegex(MCError, "损坏"):
            await self.execute("list")
        self.assertEqual(path.read_text(encoding="utf-8"), content)

    async def test_online_uuid_uses_canonical_name_from_lookup(self):
        self.raw["servers"][0]["online_mode"] = True
        self.raw["servers"][1]["online_mode"] = True
        self.service.settings = Settings(self.raw)
        identifier = "069a79f4-44e9-4726-a5be-fca90e38aaf5"
        with patch("mc_manager.service.lookup_uuid", return_value=("Steve", identifier)) as lookup:
            await self.execute("add", ["steve"])
        lookup.assert_called_once_with("steve")
        row = self.service.store.entries("g1")[0]
        self.assertEqual((row["name"], row["uuid"]), ("Steve", identifier))

    async def test_uuid_lookup_failure_does_not_create_entry(self):
        self.raw["servers"][0]["online_mode"] = True
        self.raw["servers"][1]["online_mode"] = True
        self.service.settings = Settings(self.raw)
        with patch("mc_manager.service.lookup_uuid", side_effect=MCError("联网失败")):
            with self.assertRaisesRegex(MCError, "联网"):
                await self.execute("add", ["Steve"])
        self.assertEqual(self.service.store.entries("g1"), [])
        self.assertEqual(self.file_names("s1"), [])

    async def test_audit_failure_rolls_back_entry_before_files_are_written(self):
        await self.execute("list")
        with patch.object(self.service.store, "audit", side_effect=RuntimeError("audit write failed")):
            with self.assertRaisesRegex(RuntimeError, "audit"):
                await self.execute("add", ["Steve"])
        self.assertEqual(self.service.store.entries("g1"), [])
        self.assertEqual(self.file_names("s1"), [])


class ConfigurationTests(unittest.TestCase):
    def base(self):
        return {
            "server_root": str(Path.cwd()),
            "servers": [{"id": "s1", "directory": "s1"}],
            "groups": [{"id": "g1", "platform_id": "bot", "group_id": "1", "server_ids": ["s1"]}],
        }

    def test_boolean_string_values_are_rejected(self):
        with self.assertRaisesRegex(MCError, "布尔"):
            Settings({"game_broadcast": "false"})
        raw = self.base()
        raw["servers"][0]["online_mode"] = "false"
        with self.assertRaisesRegex(MCError, "布尔"):
            Settings(raw)

    def test_incomplete_group_reports_exact_item_and_fields(self):
        raw = self.base()
        raw["groups"][0]["platform_id"] = ""
        raw["groups"][0]["group_id"] = None
        with self.assertRaisesRegex(MCError, "第 1 条.*platform_id.*group_id"):
            Settings(raw)

    def test_duplicate_group_id_reports_item_number(self):
        raw = self.base()
        raw["groups"].append({"id": "g1", "platform_id": "bot", "group_id": "2", "server_ids": ["s1"]})
        with self.assertRaisesRegex(MCError, "第 2 条.*重复"):
            Settings(raw)

    def test_empty_install_configuration(self):
        self.assertEqual(Settings({}).groups, {})

    def test_duplicate_aliases(self):
        with self.assertRaisesRegex(MCError, "重复"):
            Settings({"commands": {"add": "help"}})

    def test_template_attribute_access_rejected(self):
        for template in ["{player.__class__}", "{player[0]}", "{undefined}", "{id:>100}", "{player!r}", "{"]:
            with self.assertRaises(MCError):
                Settings({"messages": {"added": template}})

    def test_directory_traversal_rejected(self):
        raw = self.base()
        raw["servers"][0]["directory"] = "../outside"
        with self.assertRaisesRegex(MCError, "目录"):
            Settings(raw)

    def test_duplicate_rcon_endpoints_rejected(self):
        raw = self.base()
        raw["servers"].append({"id": "s2", "directory": "s2"})
        with self.assertRaisesRegex(MCError, "RCON"):
            Settings(raw)

    def test_server_can_be_shared_between_groups(self):
        raw = self.base()
        raw["groups"].append({"id": "g2", "platform_id": "bot", "group_id": "2", "server_ids": ["s1"]})
        settings = Settings(raw)
        self.assertEqual([group.id for group in settings.server_groups["s1"]], ["g1", "g2"])

    def test_mixed_uuid_modes_rejected(self):
        raw = self.base()
        raw["servers"].append({"id": "s2", "directory": "s2", "rcon_port": 25576, "online_mode": True})
        raw["groups"][0]["server_ids"].append("s2")
        with self.assertRaisesRegex(MCError, "UUID"):
            Settings(raw)

    def test_control_characters_in_query_rejected(self):
        raw = self.base()
        raw["servers"][0]["tps_command"] = "tps\nstop"
        with self.assertRaises(MCError):
            Settings(raw)

    def test_offline_uuid_matches_java_known_vector(self):
        self.assertEqual(offline_uuid("Notch"), "b50ad385-829d-3141-a216-7e7d7539ba7f")

    def test_schema_defaults_valid_and_example_valid(self):
        schema = json.loads(Path("_conf_schema.json").read_text(encoding="utf-8"))
        defaults = {k: v["default"] for k, v in schema.items() if "default" in v}
        Settings(defaults)
        example = json.loads(Path("config.example.json").read_text(encoding="utf-8"))
        example["server_root"] = str(Path.cwd())
        Settings(example)


if __name__ == "__main__":
    unittest.main()
