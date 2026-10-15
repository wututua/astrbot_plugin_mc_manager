"""Current binding ownership, group migration and old-database regressions."""

import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_core import FakeRcon

from mc_manager.config import Actor, MCError, Settings
from mc_manager.files import offline_uuid
from mc_manager.service import Service
from mc_manager.store import Store


class RebindingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for sid in ["s1", "s2"]:
            (self.root / sid).mkdir()
            (self.root / sid / "whitelist.json").write_text("[]", encoding="utf-8")
        self.raw = {
            "server_root": str(self.root),
            "servers": [
                {"id": "s1", "directory": "s1", "rcon_password": "secret"},
                {"id": "s2", "directory": "s2", "rcon_password": "secret", "rcon_port": 25576},
            ],
            "groups": [{"id": "g1", "platform_id": "bot", "group_id": "100", "server_ids": ["s1", "s2"]}],
        }
        self.sent = []
        self.actor = Actor("bot", "100", "member", "群成员", False, "bot:GroupMessage:100")
        self.admin = Actor("", "", "dashboard:admin", "后台管理员", True)
        FakeRcon.commands = []
        FakeRcon.offline = set()
        FakeRcon.startup_fail = False
        FakeRcon.disconnected = True
        self.service = Service(Settings(self.raw), self.root / "data", self.send, FakeRcon)

    async def send(self, origin, text):
        self.sent.append((origin, text))
        return True

    async def asyncTearDown(self):
        await self.service.close()
        self.temp.cleanup()

    async def add_old(self):
        await self.service.execute(self.actor, "add", ["Steve"])

    def names(self, sid):
        return [row["name"] for row in json.loads((self.root / sid / "whitelist.json").read_text(encoding="utf-8"))]

    async def apply(self, raw):
        settings = Settings(raw)
        self.service.store.validate_scopes(settings, actor=self.admin)
        await self.service.close()
        self.service = Service(settings, self.root / "data", self.send, FakeRcon)

    async def test_delete_group_releases_all_servers_but_archives_entries_and_logs(self):
        await self.add_old()
        before = self.service.store.entries("g1")
        raw = copy.deepcopy(self.raw)
        raw["groups"] = []
        await self.apply(raw)
        self.assertEqual(list(self.service.store.db.execute("SELECT * FROM bindings")), [])
        self.assertEqual(self.service.store.entries("g1"), before)
        self.assertEqual(self.service.store.db.execute("SELECT active FROM scopes WHERE id='g1'").fetchone()[0], 0)
        self.assertEqual(self.service.store.dirty(), {})
        self.assertEqual(self.names("s1"), ["Steve"])
        logs, _ = self.service.store.logs("g1", 1, 100)
        self.assertTrue(any(row["action"] == "unbind_group" and row["user"] == "dashboard:admin" for row in logs))

    async def test_released_server_can_bind_new_group_without_old_member_import(self):
        await self.add_old()
        removed = copy.deepcopy(self.raw)
        removed["groups"] = []
        await self.apply(removed)
        new = copy.deepcopy(self.raw)
        new["groups"] = [{"id": "g2", "platform_id": "bot", "group_id": "200", "server_ids": ["s1"]}]
        await self.apply(new)
        group = self.service.settings.groups["g2"]
        self.assertTrue(self.service.store.initialized("g2"))
        await self.service.ensure_group(group)
        await self.service.sync_group(group)
        self.assertEqual(self.names("s1"), [])
        self.assertEqual(self.service.store.entries("g2"), [])
        self.assertEqual(self.service.store.entries("g1")[0]["name"], "Steve")
        self.assertEqual(
            self.service.store.db.execute("SELECT scope FROM bindings WHERE server='s1'").fetchone()[0], "g2"
        )

    async def test_delete_and_rebind_in_one_commit_uses_new_groups_entries(self):
        await self.add_old()
        raw = copy.deepcopy(self.raw)
        raw["groups"] = [{"id": "g2", "platform_id": "bot", "group_id": "200", "server_ids": ["s1", "s2"]}]
        await self.apply(raw)
        actor = Actor("bot", "200", "new-member", origin="bot:GroupMessage:200")
        await self.service.execute(actor, "add", ["Alex"])
        self.assertEqual(self.names("s1"), ["Alex"])
        self.assertEqual(self.names("s2"), ["Alex"])
        self.assertEqual(self.service.store.entries("g1")[0]["name"], "Steve")
        with self.assertRaisesRegex(MCError, "未绑定"):
            await self.service.execute(self.actor, "list", [])

    async def test_move_one_server_away_keeps_remaining_server_and_scope(self):
        await self.add_old()
        raw = copy.deepcopy(self.raw)
        raw["groups"][0]["server_ids"] = ["s2"]
        raw["groups"].append({"id": "g2", "platform_id": "bot", "group_id": "200", "server_ids": ["s1"]})
        await self.apply(raw)
        await self.service.sync_group(self.service.settings.groups["g2"])
        self.assertEqual(self.names("s1"), [])
        self.assertEqual(self.names("s2"), ["Steve"])
        with self.assertRaisesRegex(MCError, "不属于"):
            await self.service.execute(self.actor, "tps", ["s1"])
        self.assertIn("Steve", await self.service.execute(self.actor, "list", []))

    async def test_group_number_change_preserves_ids_owners_and_original_audit_fields(self):
        await self.add_old()
        before = self.service.store.entries("g1")
        raw = copy.deepcopy(self.raw)
        raw["groups"][0]["group_id"] = "300"
        await self.apply(raw)
        self.assertEqual(self.service.store.entries("g1"), before)
        self.assertEqual(self.service.store.origin("g1"), "")
        logs, _ = self.service.store.logs("g1", 1, 100)
        original = next(row for row in logs if row["action"] == "add")
        self.assertEqual((original["group_id"], original["user"]), ("100", "member"))
        change = next(row for row in logs if row["action"] == "change_group")
        self.assertEqual((change["group_id"], change["user"]), ("300", "dashboard:admin"))
        new_actor = Actor("bot", "300", "member", origin="bot:GroupMessage:300")
        await self.service.execute(new_actor, "remove", ["1"])
        self.assertEqual(self.names("s1"), [])

    async def test_platform_change_clears_user_ownership_instead_of_reusing_same_user_id(self):
        await self.add_old()
        raw = copy.deepcopy(self.raw)
        raw["groups"][0].update(platform_id="other-bot", group_id="300")
        await self.apply(raw)
        row = self.service.store.entries("g1")[0]
        self.assertEqual((row["name"], row["id"], row["owner"]), ("Steve", 1, ""))
        with self.assertRaisesRegex(MCError, "自己的"):
            await self.service.execute(Actor("other-bot", "300", "member"), "remove", ["1"])
        with self.assertRaisesRegex(MCError, "自己的"):
            await self.service.execute(Actor("other-bot", "300", ""), "remove", ["1"])
        logs, _ = self.service.store.logs("g1", 1, 100)
        self.assertTrue(
            any(row["action"] == "add" and row["platform"] == "bot" and row["user"] == "member" for row in logs)
        )

    async def test_old_pending_notification_is_cancelled_when_group_number_changes(self):
        await self.add_old()
        self.service.store.job("s1", "g1", self.actor.origin)
        self.service.store.finish_job("s1", "success", "", self.actor.origin, "old notification")
        self.assertEqual(len(self.service.store.outbox()), 1)
        raw = copy.deepcopy(self.raw)
        raw["groups"][0]["group_id"] = "300"
        await self.apply(raw)
        await self.service.deliver()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.service.store.outbox(), [])
        self.service.store.job("s1", "g1", "bot:GroupMessage:300")
        self.service.store.finish_job("s1", "success", "", "bot:GroupMessage:300", "new notification")
        await self.service.deliver()
        self.assertEqual(self.sent, [("bot:GroupMessage:300", "new notification")])

    async def test_remove_group_clears_sessions_pending_messages_and_running_job_state(self):
        self.service.store.remember("g1", self.actor.origin)
        self.service.store.job("s1", "g1", self.actor.origin)
        with self.service.store.transaction():
            self.service.store.db.execute(
                "INSERT INTO outbox(origin,text,created,scope) VALUES(?,?,?,?)", (self.actor.origin, "old", "now", "g1")
            )
        raw = copy.deepcopy(self.raw)
        raw["groups"] = []
        await self.apply(raw)
        self.assertEqual(self.service.store.origin("g1"), "")
        self.assertEqual(self.service.store.outbox(), [])
        self.assertEqual(self.service.store.jobs()[0]["status"], "configuration_changed")
        await self.service.deliver()
        self.assertEqual(self.sent, [])

    async def test_dry_run_neither_releases_current_bindings_nor_resets_sessions(self):
        await self.add_old()
        raw = copy.deepcopy(self.raw)
        raw["groups"] = []
        self.service.store.validate_scopes(Settings(raw), reserve=False)
        self.assertEqual(
            self.service.store.db.execute("SELECT scope FROM bindings WHERE server='s1'").fetchone()[0], "g1"
        )
        self.assertEqual(self.service.store.origin("g1"), self.actor.origin)
        self.assertEqual(self.service.store.entries("g1")[0]["name"], "Steve")

    async def test_reconciliation_error_rolls_back_binding_identity_and_session(self):
        await self.add_old()
        raw = copy.deepcopy(self.raw)
        raw["groups"][0]["group_id"] = "300"
        with patch.object(self.service.store, "audit", side_effect=RuntimeError("audit database unavailable")):
            with self.assertRaisesRegex(RuntimeError, "audit"):
                self.service.store.validate_scopes(Settings(raw), actor=self.admin)
        self.assertEqual(
            self.service.store.db.execute("SELECT group_id FROM scopes WHERE id='g1'").fetchone()[0], "100"
        )
        self.assertEqual(self.service.store.origin("g1"), self.actor.origin)
        self.assertEqual(
            self.service.store.db.execute("SELECT scope FROM bindings WHERE server='s1'").fetchone()[0], "g1"
        )

    async def test_same_config_reload_keeps_current_session_and_does_not_duplicate_logs(self):
        await self.add_old()
        _, before = self.service.store.logs("g1", 1, 100)
        self.service.store.validate_scopes(Settings(copy.deepcopy(self.raw)))
        _, after = self.service.store.logs("g1", 1, 100)
        self.assertEqual(after, before)
        self.assertEqual(self.service.store.origin("g1"), self.actor.origin)

    async def test_restoring_same_archived_scope_keeps_its_whitelist(self):
        await self.add_old()
        raw = copy.deepcopy(self.raw)
        raw["groups"] = []
        await self.apply(raw)
        await self.apply(copy.deepcopy(self.raw))
        self.assertEqual(self.service.store.entries("g1")[0]["name"], "Steve")
        self.assertEqual(self.service.store.db.execute("SELECT active FROM scopes WHERE id='g1'").fetchone()[0], 1)
        self.assertEqual(self.service.store.origin("g1"), "")

    async def test_stale_manual_origin_is_not_used_after_native_group_number_change(self):
        raw = copy.deepcopy(self.raw)
        raw["groups"][0].update(group_id="300", broadcast_origin="bot:GroupMessage:100")
        await self.apply(raw)
        group = self.service.settings.groups["g1"]
        self.assertEqual(self.service.broadcast_origin(group, Actor("", "", "")), "")
        self.assertFalse(self.service.snapshot()["groups"][0]["broadcast_ready"])
        actor = Actor("bot", "300", "member", origin="bot:GroupMessage:300")
        await self.service.execute(actor, "help", [])
        self.assertEqual(self.service.broadcast_origin(group, Actor("", "", "")), actor.origin)

    async def test_unselecting_server_releases_only_that_server(self):
        raw = copy.deepcopy(self.raw)
        raw["groups"][0]["server_ids"] = ["s1"]
        await self.apply(raw)
        self.assertEqual([row["server"] for row in self.service.store.db.execute("SELECT * FROM bindings")], ["s1"])
        self.assertEqual(
            self.service.store.db.execute("SELECT scope FROM server_history WHERE server='s2'").fetchone()[0], "g1"
        )

    async def test_legacy_permanent_bindings_are_released_without_deleting_archived_data(self):
        directory = self.root / "legacy"
        directory.mkdir()
        db = sqlite3.connect(directory / "mc.sqlite3")
        db.executescript("""
        CREATE TABLE scopes(id TEXT PRIMARY KEY,platform TEXT,group_id TEXT,next_id INTEGER DEFAULT 1,initialized INTEGER DEFAULT 0);
        CREATE TABLE bindings(server TEXT PRIMARY KEY,scope TEXT);
        CREATE TABLE entries(scope TEXT,id INTEGER,name TEXT,uuid TEXT,owner TEXT,created TEXT,PRIMARY KEY(scope,id));
        CREATE TABLE sessions(scope TEXT PRIMARY KEY,origin TEXT);
        CREATE TABLE outbox(id INTEGER PRIMARY KEY AUTOINCREMENT,origin TEXT,text TEXT,created TEXT,attempts INTEGER DEFAULT 0);
        INSERT INTO scopes VALUES('legacy','bot','100',2,1);
        INSERT INTO bindings VALUES('s1','legacy');
        INSERT INTO sessions VALUES('legacy','bot:GroupMessage:100');
        INSERT INTO outbox(origin,text,created) VALUES('bot:GroupMessage:100','legacy message','now');
        """)
        db.execute(
            "INSERT INTO entries VALUES(?,?,?,?,?,?)", ("legacy", 1, "Steve", offline_uuid("Steve"), "member", "now")
        )
        db.commit()
        db.close()
        store = Store(directory)
        try:
            raw = copy.deepcopy(self.raw)
            raw["groups"] = []
            store.validate_scopes(Settings(raw))
            self.assertEqual(list(store.db.execute("SELECT * FROM bindings")), [])
            self.assertEqual(store.entries("legacy")[0]["name"], "Steve")
            self.assertEqual(
                store.db.execute("SELECT scope FROM server_history WHERE server='s1'").fetchone()[0], "legacy"
            )
            self.assertEqual(store.outbox(), [])
            new = copy.deepcopy(self.raw)
            new["groups"] = [{"id": "new", "platform_id": "bot", "group_id": "300", "server_ids": ["s1"]}]
            store.validate_scopes(Settings(new))
            self.assertTrue(store.initialized("new"))
            self.assertEqual(store.entries("new"), [])
        finally:
            store.close()
