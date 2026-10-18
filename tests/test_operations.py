"""Failure recovery and administrator operations; all data and connections are local fakes."""

import asyncio
import base64
import copy
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from logger_stub import logger_stub
from test_core import FakeRcon

from mc_manager.backups import BackupManager
from mc_manager.config import Actor, MCError, Settings
from mc_manager.service import Service


class OperationsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for name in ("s1", "s2", "s3"):
            (self.root / name).mkdir()
            (self.root / name / "whitelist.json").write_text("[]", encoding="utf-8")
        self.raw = {
            "server_root": str(self.root),
            "servers": [
                {"id": name, "directory": name, "rcon_port": 25575 + index, "rcon_password": "only-test-secret"}
                for index, name in enumerate(("s1", "s2", "s3"))
            ],
            "groups": [
                {
                    "id": "g1",
                    "platform_id": "bot",
                    "group_id": "100",
                    "server_ids": ["s1", "s2"],
                    "admin_ids": ["admin"],
                    "whitelist_admin_ids": ["manager"],
                    "restart_admin_ids": ["operator"],
                },
                {
                    "id": "g2",
                    "platform_id": "bot",
                    "group_id": "200",
                    "server_ids": ["s3"],
                    "admin_ids": ["other-admin"],
                },
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
        self.service = Service(
            Settings(copy.deepcopy(self.raw)), self.root / "data", send, FakeRcon, logger=logger_stub()
        )
        self.service.settings.poll_interval = 0.001
        # Hosted Windows timers and file flushes can exceed a few milliseconds.
        self.service.settings.restart_timeout = 1
        self.user = Actor("bot", "100", "user", "member", False, "bot:GroupMessage:100")
        self.admin = Actor("bot", "100", "admin", "admin", False, self.user.origin)
        self.manager = Actor("bot", "100", "manager", "manager", False, self.user.origin)
        self.operator = Actor("bot", "100", "operator", "operator", False, self.user.origin)
        self.group = self.service.settings.groups["g1"]
        self.service.store.remember("g1", self.user.origin, "100")

    async def asyncTearDown(self):
        await self.service.close()
        self.temp.cleanup()

    async def execute(self, action, args=(), actor=None):
        return await self.service.execute(actor or self.user, action, list(args))

    def queue(self, origin, text="message"):
        with self.service.store.transaction():
            self.service.store.enqueue([("g1", origin)], text)

    async def test_failed_first_twenty_do_not_starve_new_notification(self):
        for index in range(20):
            self.queue("bad-" + str(index))
        self.queue("good")
        attempted = []

        async def send(origin, text):
            attempted.append(origin)
            return origin == "good"

        self.service.send = send
        await self.service.deliver()
        self.assertNotIn("good", attempted)
        await self.service.deliver()
        self.assertIn("good", attempted)
        self.assertEqual(len(self.service.store.notifications()), 20)

    async def test_max_attempts_failed_queue_and_manual_retry(self):
        self.service.settings.notification_retry_seconds = 0
        self.service.settings.notification_max_attempts = 2

        async def send(*args):
            return False

        self.service.send = send
        self.queue(self.user.origin)
        await self.service.deliver()
        await self.service.deliver()
        item = self.service.store.notifications()[0]
        self.assertEqual(item["state"], "failed")
        self.assertEqual(self.service.store.outbox(), [])
        with self.assertRaises(MCError):
            self.service.store.retry_notification(item["id"], "g2")
        self.service.store.retry_notification(item["id"], "g1")
        self.assertEqual(self.service.store.outbox()[0]["attempts"], 0)

    async def test_expired_notifications_are_not_sent(self):
        self.queue(self.user.origin)
        with self.service.store.transaction():
            self.service.store.db.execute("UPDATE outbox SET created='1990-01-01T00:00:00+00:00'")
        await self.service.deliver()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.service.store.notifications()[0]["state"], "failed")

    async def test_background_recovers_after_database_write_failure(self):
        self.service.settings.background_retry_seconds = 0.01
        self.service.settings.sync_interval = 0.01
        self.queue(self.user.origin)
        original = self.service.store.delivered
        count = 0

        def fail_once(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 1:
                raise sqlite3.OperationalError("database or disk is full")
            return original(*args, **kwargs)

        with patch.object(self.service.store, "delivered", side_effect=fail_once):
            self.service.resume()
            async with asyncio.timeout(2):
                while self.service.store.notifications():
                    await asyncio.sleep(0.01)
        self.assertFalse(self.service.delivery_task.done())
        self.assertIn("database or disk", self.service.worker_health["notifications"]["last_error"])
        self.service.logger.exception.assert_any_call("MC %s 后台任务失败，%s 秒后重试", "notifications", 0.01)

    async def test_query_coalescing_cache_and_cancelled_waiter(self):
        started, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def slow(sid, kind):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return "result"

        with patch.object(self.service, "query", side_effect=slow):
            first = asyncio.create_task(self.service.cached_query("s1", "tps"))
            await started.wait()
            second = asyncio.create_task(self.service.cached_query("s1", "tps"))
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            release.set()
            self.assertEqual(await second, "result")
            self.assertEqual(await self.service.cached_query("s1", "tps"), "result")
            self.assertEqual(calls, 1)
            self.service.invalidate_queries("s1")
            await self.service.cached_query("s1", "tps")
            self.assertEqual(calls, 2)

    async def test_query_cooldown_does_not_block_help(self):
        self.service.settings.query_user_cooldown = 5
        await self.execute("status", ["s1"])
        with self.assertRaisesRegex(MCError, "频繁"):
            await self.execute("status", ["s1"])
        self.assertIn("管理菜单", await self.execute("help"))

    async def test_scoped_diagnostics_do_not_expose_passwords(self):
        report = await self.service.diagnostics(self.group)
        self.assertEqual({row["id"] for row in report["servers"]}, {"s1", "s2"})
        self.assertTrue(all(row["directory"]["writable"] for row in report["servers"]))
        self.assertNotIn("only-test-secret", json.dumps(report))
        with self.assertRaises(MCError):
            await self.execute("diagnose")
        self.assertIn("MC 诊断", await self.execute("diagnose", actor=self.manager))

    async def test_backup_preserves_wal_data_config_ownership_and_number(self):
        await self.execute("add", ["Steve"])
        backup = await self.service.backups.create(self.raw)
        content = base64.b64decode(self.service.backups.download(backup["name"])["content_base64"])
        config, database, counts = self.service.backups.inspect(content)
        self.assertEqual(counts["entries"], 1)
        self.assertEqual(config["servers"][0]["rcon_password"], "only-test-secret")
        with tempfile.TemporaryDirectory() as temp:
            restored = Path(temp) / "copy.sqlite3"
            restored.write_bytes(database)
            with closing(sqlite3.connect(restored)) as connection:
                self.assertEqual(
                    connection.execute("SELECT id,name,owner FROM entries").fetchone(), (1, "Steve", "user")
                )

    async def test_backup_preview_is_single_use_and_rejects_tampering(self):
        backup = await self.service.backups.create(self.raw)
        preview = self.service.backups.preview(name=backup["name"])
        self.assertEqual(preview["servers"], ["s1", "s2", "s3"])
        self.service.backups.consume(preview["token"])
        with self.assertRaises(MCError):
            self.service.backups.consume(preview["token"])
        original = self.service.backups.path(backup["name"]).read_bytes()
        stream = io.BytesIO()
        with ZipFile(io.BytesIO(original)) as source, ZipFile(stream, "w", ZIP_DEFLATED) as target:
            for name in source.namelist():
                target.writestr(name, b"{}" if name == "config.json" else source.read(name))
        with self.assertRaisesRegex(MCError, "校验"):
            self.service.backups.inspect(stream.getvalue())
        with self.assertRaises(MCError):
            self.service.backups.download("../mc.sqlite3")

    async def test_backup_retention_and_unknown_members(self):
        manager = BackupManager(self.root / "data", keep=1)
        await manager.create(self.raw)
        await manager.create(self.raw)
        self.assertEqual(len(manager.list()), 1)
        stream = io.BytesIO()
        with ZipFile(stream, "w") as archive:
            archive.writestr("../outside.txt", "bad")
        with self.assertRaises(MCError):
            manager.inspect(stream.getvalue())

    async def test_automatic_backup_is_opt_in(self):
        await self.service.maintenance()
        self.assertEqual(self.service.backups.list(), [])
        self.service.settings.backup_interval_hours = 1
        await self.service.maintenance()
        self.assertEqual(len(self.service.backups.list()), 1)
        await self.service.maintenance()
        self.assertEqual(len(self.service.backups.list()), 1)

    async def test_retention_preview_does_not_delete_and_apply_is_single_use(self):
        with self.service.store.transaction():
            for index in range(5):
                self.service.store.audit(self.group, self.admin, "test", str(index), "test")
        self.service.settings.logs_max_entries = 2
        preview = self.service.cleanup_preview()
        self.assertEqual(preview["count"], 3)
        self.assertEqual(self.service.store.logs("g1", 1, 20)[1], 5)
        self.assertEqual(self.service.cleanup_apply(preview["token"]), 3)
        with self.assertRaises(MCError):
            self.service.cleanup_apply(preview["token"])

    async def test_log_filters_are_scoped_and_parameterized(self):
        await self.execute("add", ["Steve"])
        rows, total = self.service.store.filtered_logs("g1", 1, 20, player="ste", user="user", action="add")
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["player"], "Steve")
        self.assertEqual(self.service.store.filtered_logs("g2", 1, 20, player="Steve")[1], 0)
        self.assertEqual(self.service.store.filtered_logs("g1", 1, 20, user="' OR 1=1 --")[1], 0)
        with self.assertRaises(MCError):
            self.service.store.filtered_logs("g1", 1, 20, start="bad-date")

    async def test_bulk_preview_apply_preserves_ids_and_rejects_stale_state(self):
        preview = await self.service.bulk_preview(self.admin, self.group, "name,owner\nSteve,user\nAlex,user2\n", "csv")
        self.assertEqual(preview["errors"], [])
        self.assertEqual(self.service.store.entries("g1"), [])
        result = await self.service.bulk_apply(self.admin, self.group, preview["token"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(
            [(row["id"], row["owner"]) for row in self.service.store.entries("g1")], [(1, "user"), (2, "user2")]
        )
        stale = await self.service.bulk_preview(self.admin, self.group, "Alice", "text")
        await self.execute("remove", ["1"], self.admin)
        with self.assertRaisesRegex(MCError, "变化"):
            await self.service.bulk_apply(self.admin, self.group, stale["token"])

    async def test_invalid_bulk_preview_cannot_apply_and_other_scope_denied(self):
        preview = await self.service.bulk_preview(self.admin, self.group, "Steve\nbad name", "text")
        self.assertEqual(len(preview["errors"]), 1)
        with self.assertRaises(MCError):
            await self.service.bulk_apply(self.admin, self.group, preview["token"])
        with self.assertRaises(MCError):
            await self.service.bulk_preview(self.user, self.group, "Steve", "text")
        self.assertEqual(self.service.store.entries("g1"), [])

    async def test_bulk_transfer_is_audited_and_does_not_grant_old_owner_access(self):
        await self.execute("add", ["Steve"])
        await self.service.bulk_change(self.admin, self.group, [1], "transfer", "new-owner")
        with self.assertRaisesRegex(MCError, "自己的"):
            await self.execute("remove", ["1"])
        with self.assertRaises(MCError):
            await self.service.bulk_change(self.user, self.group, [1], "remove")
        self.assertEqual(self.service.store.entries("g1")[0]["owner"], "new-owner")

    async def test_whitelist_and_restart_roles_are_separate(self):
        await self.execute("add", ["Steve"])
        await self.execute("remove", ["1"], self.manager)
        with self.assertRaises(MCError):
            await self.execute("restart", ["s1"], self.manager)
        await self.execute("add", ["Alex"])
        with self.assertRaises(MCError):
            await self.execute("remove", ["2"], self.operator)
        await self.execute("restart", ["s1"], self.operator)
        await asyncio.gather(*list(self.service.restart_tasks.values()))

    async def test_shared_server_owner_policy_cannot_be_bypassed_by_group_admin(self):
        self.service.settings.servers["s1"] = replace(
            self.service.settings.servers["s1"], restart_admins=("bot:owner",)
        )
        with self.assertRaisesRegex(MCError, "权限"):
            await self.execute("restart", ["s1"], self.admin)
        global_admin = Actor("bot", "100", "global", admin=True, origin=self.user.origin)
        await self.execute("restart", ["s1"], global_admin)
        await asyncio.gather(*list(self.service.restart_tasks.values()))

    async def test_approval_does_not_write_before_approval_and_preserves_requester(self):
        self.service.settings.groups["g1"] = self.group = replace(self.group, approval_required=True)
        reply = await self.execute("add", ["Steve"])
        self.assertIn("等待管理员", reply)
        self.assertEqual(self.service.store.entries("g1"), [])
        request_id = self.service.store.pending_requests("g1")[0]["id"]
        with self.assertRaises(MCError):
            await self.execute("approve", [str(request_id)])
        await self.execute("approve", [str(request_id)], self.manager)
        self.assertEqual(self.service.store.entries("g1")[0]["owner"], "user")

    async def test_duplicate_pending_request_and_other_scope_review_denied(self):
        self.service.settings.groups["g1"] = self.group = replace(self.group, approval_required=True)
        await self.execute("add", ["Steve"])
        with self.assertRaisesRegex(MCError, "待审核"):
            await self.execute("add", ["Alex"])
        other = self.service.settings.groups["g2"]
        with self.assertRaises(MCError):
            await self.service.review_request(Actor("bot", "200", "other-admin"), other, 1, True)
        self.assertEqual(len(self.service.store.pending_requests("g1")), 1)

    async def test_modify_approval_rechecks_owner_and_original_content(self):
        await self.execute("add", ["Steve"])
        self.service.settings.groups["g1"] = self.group = replace(self.group, approval_required=True)
        await self.execute("modify", ["1", "Alex"])
        await self.service.bulk_change(self.admin, self.group, [1], "transfer", "new-owner")
        with self.assertRaisesRegex(MCError, "变化"):
            await self.execute("approve", ["1"], self.manager)
        self.assertEqual(self.service.store.entries("g1")[0]["name"], "Steve")

    async def test_alert_threshold_cooldown_recovery_and_group_isolation(self):
        for _ in range(2):
            self.service.evaluate_alert("s1", "test", True, "bad")
        self.assertEqual(self.service.store.notifications(), [])
        self.service.evaluate_alert("s1", "test", True, "bad")
        self.service.evaluate_alert("s1", "test", True, "bad again")
        self.assertEqual(len(self.service.store.notifications()), 1)
        self.service.evaluate_alert("s1", "test", False, "good")
        self.assertEqual(len(self.service.store.notifications()), 2)
        await self.service.deliver()
        self.assertTrue(all(origin == self.user.origin for origin, _ in self.sent))
        self.assertIn("已恢复", self.sent[-1][1])

    async def test_countdown_can_be_cancelled_before_stop(self):
        self.service.settings.restart_countdown = 5
        await self.execute("restart", ["s1"], self.operator)
        async with asyncio.timeout(1):
            while self.service.store.jobs()[0]["phase"] != "countdown":
                await asyncio.sleep(0)
        await self.execute("cancel", ["s1"], self.operator)
        self.assertFalse(any(command == "stop" for _, command in FakeRcon.commands))
        self.assertEqual(self.service.store.jobs()[0]["status"], "cancelled")
        self.assertEqual(self.service.restart_tasks, {})

    async def test_backup_command_requires_global_admin(self):
        with self.assertRaisesRegex(MCError, "全局"):
            await self.execute("backup", actor=self.admin)
        reply = await self.execute("backup", actor=Actor("bot", "100", "global", admin=True))
        self.assertIn("mc-backup-", reply)

    async def test_new_default_aliases_preserve_existing_custom_alias(self):
        raw = copy.deepcopy(self.raw)
        raw["commands"] = {"add": "添加,申请"}
        settings = Settings(raw)
        self.assertEqual(settings.route("申请 Steve"), ("add", ["Steve"]))
        self.assertEqual(settings.route("requests"), ("requests", []))

    async def test_notification_wait_does_not_block_periodic_probe(self):
        waiting = asyncio.Event()

        async def slow_send(*args):
            waiting.set()
            await asyncio.Event().wait()

        self.service.send = slow_send
        self.service.settings.sync_interval = 0.01
        self.queue(self.user.origin)
        self.service.resume()
        await asyncio.wait_for(waiting.wait(), 1)
        FakeRcon.offline.add("s1")
        async with asyncio.timeout(1):
            while self.service.last_status.get("s1") != "离线或 RCON 不可用":
                await asyncio.sleep(0.01)
        await self.service.pause()
        self.assertIsNone(self.service.delivery_task)

    async def test_unknown_reply_does_not_produce_low_tps_alert(self):
        with (
            patch.object(self.service, "cached_query", return_value="no metrics") as query,
            patch.object(self.service, "evaluate_alert") as alert,
        ):
            await self.service.check_alerts()
        self.assertIn("s1", [call.args[0] for call in query.await_args_list])
        self.assertNotIn("TPS 过低", [call.args[1] for call in alert.call_args_list])

    async def test_pending_requests_cancelled_when_platform_changes(self):
        self.service.settings.groups["g1"] = self.group = replace(self.group, approval_required=True)
        await self.execute("add", ["Steve"])
        raw = copy.deepcopy(self.raw)
        raw["groups"][0]["platform_id"] = "new-bot"
        self.service.store.validate_scopes(Settings(raw))
        self.assertEqual(self.service.store.pending_requests("g1"), [])
        self.assertEqual(self.service.store.db.execute("SELECT status FROM requests").fetchone()[0], "cancelled")

    async def test_countdown_wakes_notification_worker_before_stop(self):
        self.service.settings.restart_countdown = 5
        self.service.resume()
        await asyncio.sleep(0.01)
        await self.execute("restart", ["s1"], self.operator)
        async with asyncio.timeout(1):
            while not any("将在" in text for _, text in self.sent):
                await asyncio.sleep(0.01)
        self.assertFalse(any(command == "stop" for _, command in FakeRcon.commands))
        await self.execute("cancel", ["s1"], self.operator)

    async def test_restart_result_retries_database_failure_without_duplicate_notifications(self):
        self.service.settings.background_retry_seconds = 0.001
        original = self.service.store.finish_job
        calls = 0

        def fail_once(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise sqlite3.OperationalError("database or disk is full")
            return original(*args, **kwargs)

        with patch.object(self.service.store, "finish_job", side_effect=fail_once):
            await self.execute("restart", ["s1"], self.admin)
            await asyncio.gather(*list(self.service.restart_tasks.values()))
        self.assertEqual(calls, 2)
        self.assertEqual(self.service.store.jobs()[0]["status"], "success")
        self.assertEqual(sum("重启成功" in text for _, text in self.sent), 1)

    async def test_group_cooldown_uses_actual_chat_when_two_chats_share_configuration(self):
        self.service.settings.groups["g1"] = replace(self.group, group_id="100,101")
        self.service.settings.query_group_cooldown = 5
        await self.execute("status", ["s1"])
        another_chat = Actor("bot", "101", "different-user")
        await self.execute("status", ["s1"], another_chat)
        with self.assertRaisesRegex(MCError, "频繁"):
            await self.execute("status", ["s1"], Actor("bot", "100", "different-user"))
