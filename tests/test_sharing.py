import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_core import FakeRcon

from mc_manager.config import Actor, MCError, Settings
from mc_manager.files import offline_uuid
from mc_manager.service import Service


class SharingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for sid in ["s1", "s2", "s3"]:
            (self.root / sid).mkdir()
            (self.root / sid / "whitelist.json").write_text("[]", encoding="utf-8")
        self.raw = {
            "server_root": str(self.root),
            "servers": [
                {"id": sid, "rcon_port": 25575 + index, "rcon_password": "test"}
                for index, sid in enumerate(["s1", "s2", "s3"])
            ],
            "groups": [
                {
                    "id": "g1",
                    "platform_id": "bot",
                    "group_id": "100,101",
                    "server_ids": ["s1", "s2"],
                    "admin_ids": ["admin1"],
                },
                {
                    "id": "g2",
                    "platform_id": "bot",
                    "group_id": "200",
                    "server_ids": ["s1", "s3"],
                    "admin_ids": ["admin2"],
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
        self.service = Service(Settings(self.raw), self.root / "data", send, FakeRcon)
        self.service.settings.poll_interval = 0.001
        # Hosted Windows timers and file flushes can exceed a few milliseconds.
        self.service.settings.restart_timeout = 1
        self.a = Actor("bot", "100", "owner1", origin="bot:GroupMessage:100")
        self.a2 = Actor("bot", "101", "owner1", origin="bot:GroupMessage:101")
        self.b = Actor("bot", "200", "owner2", origin="bot:GroupMessage:200")

    async def asyncTearDown(self):
        await self.service.close()
        self.temp.cleanup()

    async def command(self, actor, action, *args):
        return await self.service.execute(actor, action, list(args))

    def names(self, sid):
        return {entry["name"] for entry in json.loads((self.root / sid / "whitelist.json").read_text(encoding="utf-8"))}

    def seed(self, sid, name):
        (self.root / sid / "whitelist.json").write_text(
            json.dumps([{"name": name, "uuid": offline_uuid(name)}]), encoding="utf-8"
        )

    async def test_concurrent_groups_union_only_on_shared_server(self):
        await asyncio.wait_for(
            asyncio.gather(self.command(self.a, "add", "Steve"), self.command(self.b, "add", "Alex")), 3
        )
        self.assertEqual(self.names("s1"), {"Steve", "Alex"})
        self.assertEqual(self.names("s2"), {"Steve"})
        self.assertEqual(self.names("s3"), {"Alex"})
        self.assertEqual([row["name"] for row in self.service.store.entries("g1")], ["Steve"])
        self.assertEqual([row["name"] for row in self.service.store.entries("g2")], ["Alex"])
        self.assertEqual(self.service.store.dirty(), {})

    async def test_multiple_chats_share_one_scope_but_keep_owner_permissions(self):
        await self.command(self.a, "add", "Steve")
        self.assertIn("#1 Steve", await self.command(self.a2, "list"))
        with self.assertRaisesRegex(MCError, "自己的"):
            await self.command(Actor("bot", "101", "other"), "remove", "1")
        await self.command(self.a2, "remove", "1")
        self.assertEqual(self.names("s1"), set())
        logs, _ = self.service.store.logs("g1", 1, 100)
        self.assertEqual(next(row for row in logs if row["action"] == "remove")["group_id"], "101")
        self.assertEqual(next(row for row in logs if row["action"] == "add")["group_id"], "100")

    async def test_same_player_in_two_scopes_survives_one_membership_deletion(self):
        await self.command(self.a, "add", "Steve")
        await self.command(self.b, "add", "Steve")
        self.assertEqual(len(json.loads((self.root / "s1/whitelist.json").read_text(encoding="utf-8"))), 1)
        await self.command(self.a, "remove", "1")
        self.assertEqual(self.names("s1"), {"Steve"})
        self.assertEqual(self.names("s2"), set())
        self.assertEqual(self.names("s3"), {"Steve"})
        await self.command(self.b, "remove", "1")
        self.assertEqual(self.names("s1"), set())

    async def test_fixed_ids_and_admin_permissions_stay_per_scope(self):
        await self.command(self.a, "add", "Steve")
        await self.command(self.b, "add", "Alex")
        self.assertEqual(self.service.store.entries("g1")[0]["id"], 1)
        self.assertEqual(self.service.store.entries("g2")[0]["id"], 1)
        with self.assertRaisesRegex(MCError, "自己的"):
            await self.command(Actor("bot", "200", "admin1"), "remove", "1")
        with self.assertRaisesRegex(MCError, "未找到"):
            await self.command(self.a, "remove", "Alex")
        self.assertEqual(self.names("s1"), {"Steve", "Alex"})

    async def test_first_import_preserves_shared_legacy_and_private_entries(self):
        self.seed("s1", "Steve")
        self.seed("s2", "Alice")
        self.seed("s3", "Bobby")
        await self.command(self.b, "add", "Alex")
        self.assertEqual({row["name"] for row in self.service.store.entries("g1")}, {"Steve", "Alice"})
        self.assertEqual({row["name"] for row in self.service.store.entries("g2")}, {"Bobby", "Alex"})
        await self.service.sync_group(self.service.settings.groups["g1"])
        self.assertEqual(self.names("s1"), {"Steve", "Alice", "Bobby", "Alex"})
        self.assertEqual(self.names("s2"), {"Steve", "Alice"})
        self.assertEqual(self.names("s3"), {"Bobby", "Alex"})

    async def test_admin_import_does_not_claim_another_groups_members(self):
        await self.command(self.a, "add", "Steve")
        await self.command(self.b, "add", "Alex")
        await self.command(Actor("bot", "100", "admin1"), "import")
        self.assertEqual([row["name"] for row in self.service.store.entries("g1")], ["Steve"])
        await self.command(self.b, "remove", "1")
        self.assertEqual(self.names("s1"), {"Steve"})

    async def test_removing_one_binding_keeps_remaining_group_online(self):
        await self.command(self.a, "add", "Steve")
        await self.command(self.b, "add", "Alex")
        raw = copy.deepcopy(self.raw)
        raw["groups"] = raw["groups"][1:]
        settings = Settings(raw)
        self.service.store.validate_scopes(settings)
        self.service.settings = settings
        await self.service.sync_group(settings.groups["g2"])
        self.assertEqual(self.names("s1"), {"Alex"})
        self.assertEqual(self.names("s3"), {"Alex"})
        self.assertEqual(self.names("s2"), {"Steve"})
        self.assertEqual(self.service.store.entries("g1")[0]["name"], "Steve")
        self.assertEqual(
            self.service.store.db.execute("SELECT scope FROM bindings WHERE server='s1'").fetchone()[0], "g2"
        )

    async def test_opposite_server_order_does_not_deadlock(self):
        raw = copy.deepcopy(self.raw)
        raw["groups"][1]["server_ids"] = ["s2", "s1"]
        self.service.settings = Settings(raw)
        self.service.store.validate_scopes(self.service.settings)
        for index in range(6):
            await asyncio.wait_for(
                asyncio.gather(self.command(self.a, "add", "Steve"), self.command(self.b, "add", "Alex")), 3
            )
            self.assertEqual(self.names("s1"), {"Steve", "Alex"})
            self.assertEqual(self.names("s2"), {"Steve", "Alex"})
            await asyncio.wait_for(
                asyncio.gather(
                    self.command(self.a, "remove", str(index + 1)), self.command(self.b, "remove", str(index + 1))
                ),
                3,
            )
            self.assertEqual(self.names("s1"), set())
            self.assertEqual(self.names("s2"), set())

    async def test_cancelled_competing_write_retains_dirty_revision(self):
        await self.command(self.a, "add", "Steve")
        await self.command(self.b, "add", "Alex")
        entered, release = asyncio.Event(), asyncio.Event()
        real_write = self.service.disk_write
        first = True

        async def slow_write(server, entries):
            nonlocal first
            if server.id == "s1" and first:
                first = False
                entered.set()
                await release.wait()
            return await real_write(server, entries)

        with patch.object(self.service, "disk_write", side_effect=slow_write):
            syncing = asyncio.create_task(self.service.sync_group(self.service.settings.groups["g1"], force=True))
            await asyncio.wait_for(entered.wait(), 2)
            removing = asyncio.create_task(self.command(self.b, "remove", "1"))

            async def committed():
                while self.service.store.entries("g2"):
                    await asyncio.sleep(0.001)

            try:
                await asyncio.wait_for(committed(), 2)
                removing.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await removing
            finally:
                release.set()
            await asyncio.wait_for(syncing, 3)
        self.assertIn("s1", self.service.store.dirty())
        await self.service.sync_group(self.service.settings.groups["g2"])
        self.assertEqual(self.names("s1"), {"Steve"})
        self.assertNotIn("s1", self.service.store.dirty())

    async def test_shared_restart_broadcasts_to_each_chat_once(self):
        for actor in [self.a, self.a2, self.b]:
            await self.command(actor, "help")
        await self.command(Actor("bot", "100", "admin1", origin=self.a.origin), "restart", "s1")
        await asyncio.wait_for(asyncio.gather(*list(self.service.restart_tasks.values())), 3)
        self.assertEqual({origin for origin, text in self.sent}, {self.a.origin, self.a2.origin, self.b.origin})
        self.assertEqual(len(self.sent), 3)
        self.assertTrue(all("重启成功" in text for _, text in self.sent))

    async def test_shared_restart_failure_reaches_all_bound_chats(self):
        for actor in [self.a, self.a2, self.b]:
            await self.command(actor, "help")
        FakeRcon.startup_fail = True
        await self.command(Actor("bot", "200", "admin2", origin=self.b.origin), "restart", "s1")
        await asyncio.wait_for(asyncio.gather(*list(self.service.restart_tasks.values())), 3)
        self.assertEqual({origin for origin, _ in self.sent}, {self.a.origin, self.a2.origin, self.b.origin})
        self.assertTrue(all("重启失败" in text for _, text in self.sent))

    async def test_private_restart_only_reaches_its_multiple_chats(self):
        for actor in [self.a, self.a2, self.b]:
            await self.command(actor, "help")
        await self.command(Actor("bot", "100", "admin1", origin=self.a.origin), "restart", "s2")
        await asyncio.wait_for(asyncio.gather(*list(self.service.restart_tasks.values())), 3)
        self.assertEqual({origin for origin, _ in self.sent}, {self.a.origin, self.a2.origin})

    async def test_adding_chat_preserves_existing_sessions_and_removes_deleted_chat(self):
        for actor in [self.a, self.a2]:
            await self.command(actor, "help")
        raw = copy.deepcopy(self.raw)
        raw["groups"][0]["group_id"] = ["101", "102"]
        self.service.store.validate_scopes(Settings(raw))
        self.assertEqual(self.service.store.origins("g1"), {"101": self.a2.origin})

    async def test_v123_single_binding_schema_migrates_without_losing_entries(self):
        raw = copy.deepcopy(self.raw)
        raw["groups"][0]["group_id"] = "100"
        raw["groups"][1]["server_ids"] = ["s3"]
        self.service.settings = Settings(raw)
        self.service.store.validate_scopes(self.service.settings)
        await self.command(self.a, "add", "Steve")
        await self.command(self.b, "add", "Alex")
        before = self.service.store.entries("g1")
        with self.service.store.transaction():
            db = self.service.store.db
            db.execute("ALTER TABLE bindings RENAME TO bindings_many")
            db.execute("CREATE TABLE bindings(server TEXT PRIMARY KEY,scope TEXT NOT NULL)")
            db.execute("INSERT INTO bindings SELECT server,scope FROM bindings_many")
            db.execute("DROP TABLE bindings_many")
        await self.service.close()

        async def send(origin, text):
            return True

        self.service = Service(Settings(self.raw), self.root / "data", send, FakeRcon)
        self.assertEqual(self.service.store.entries("g1"), before)
        columns = {row[1]: row[5] for row in self.service.store.db.execute("PRAGMA table_info(bindings)")}
        self.assertEqual(columns, {"server": 1, "scope": 2})
        self.assertEqual(self.service.store.origins("g1")["100"], self.a.origin)
        await self.service.sync_group(self.service.settings.groups["g1"])
        self.assertEqual(self.names("s1"), {"Steve", "Alex"})

    async def test_shared_identity_conflict_rolls_back_membership_changes(self):
        raw = copy.deepcopy(self.raw)
        raw["groups"][1]["server_ids"] = ["s3"]
        settings = Settings(raw)
        self.service.store.validate_scopes(settings)
        self.service.settings = settings
        await self.command(self.a, "add", "Steve")
        await self.command(self.b, "add", "Alex")
        with self.service.store.transaction():
            self.service.store.db.execute("UPDATE entries SET name='Steve' WHERE scope='g2'")
        before = [tuple(row) for row in self.service.store.db.execute("SELECT * FROM bindings ORDER BY server,scope")]
        await self.command(self.a2, "help")
        origins_before = self.service.store.origins("g1")
        changed = copy.deepcopy(self.raw)
        changed["groups"][0]["group_id"] = "100,102"
        with self.assertRaisesRegex(MCError, "冲突"):
            self.service.store.validate_scopes(Settings(changed))
        self.assertEqual(self.service.store.origins("g1"), origins_before)
        self.assertEqual(
            self.service.store.db.execute("SELECT group_id FROM scopes WHERE id='g1'").fetchone()[0], "100,101"
        )
        self.assertEqual(
            [tuple(row) for row in self.service.store.db.execute("SELECT * FROM bindings ORDER BY server,scope")],
            before,
        )


class MultiChatConfigurationTests(unittest.TestCase):
    def test_comma_chinese_comma_newline_and_array(self):
        for identities in ["100,101", "100，101", "100\n101", ["100", "101"], "100,100,101"]:
            with self.subTest(identities=identities):
                settings = Settings(
                    {
                        "server_root": str(Path.cwd()),
                        "servers": [{"id": "s1"}],
                        "groups": [{"id": "g1", "platform_id": "bot", "group_id": identities, "server_ids": ["s1"]}],
                    }
                )
                self.assertEqual(settings.groups["g1"].group_ids, ("100", "101"))
                self.assertEqual(settings.group_for(Actor("bot", "101", "member")).id, "g1")
                self.assertTrue(settings.is_admin(Actor("bot", "101", "admin", admin=True), settings.groups["g1"]))

    def test_overlapping_chat_id_cannot_route_to_multiple_scopes(self):
        raw = {
            "server_root": str(Path.cwd()),
            "servers": [{"id": "s1"}],
            "groups": [
                {"id": "g1", "platform_id": "bot", "group_id": "100,101", "server_ids": ["s1"]},
                {"id": "g2", "platform_id": "bot", "group_id": "101,102", "server_ids": ["s1"]},
            ],
        }
        with self.assertRaisesRegex(MCError, "重复绑定"):
            Settings(raw)

    def test_invalid_group_id_shapes_rejected(self):
        for value in [True, {}, [None], [False], "100\x00", "100 101"]:
            raw = {
                "server_root": str(Path.cwd()),
                "servers": [{"id": "s1"}],
                "groups": [{"id": "g1", "platform_id": "bot", "group_id": value, "server_ids": ["s1"]}],
            }
            with self.subTest(value=value), self.assertRaises(MCError):
                Settings(raw)
