"""Opt-in whitelist recovery must retain backups, ownership and scope guards."""

import copy
import json
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_core as core_fixture

from mc_manager.config import MCError, Settings
from mc_manager.files import offline_uuid, read_whitelist, write_whitelist


class WhitelistOverwriteTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = core_fixture.CoreTests.asyncSetUp
    asyncTearDown = core_fixture.CoreTests.asyncTearDown
    execute = core_fixture.CoreTests.execute
    file_names = core_fixture.CoreTests.file_names

    def enable(self, *ids):
        raw = copy.deepcopy(self.raw)
        for server in raw["servers"]:
            server["whitelist_overwrite"] = server["id"] in ids
        self.service.settings = Settings(raw)

    def corrupt(self, sid="s2", data=b"{broken"):
        path = self.root / sid / "whitelist.json"
        path.write_bytes(data)
        return path

    def backups(self, sid="s2"):
        return list((self.root / sid / ".mcwl-backups").glob("whitelist-*.json"))

    def test_default_off_and_boolean_validation(self):
        self.assertTrue(all(not server.whitelist_overwrite for server in self.service.settings.servers.values()))
        schema = json.loads(Path("_conf_schema.json").read_text(encoding="utf-8"))
        self.assertFalse(schema["servers"]["templates"]["server"]["items"]["whitelist_overwrite"]["default"])
        for value in ("true", "false", 1, None, []):
            raw = copy.deepcopy(self.raw)
            raw["servers"][0]["whitelist_overwrite"] = value
            with self.subTest(value=value), self.assertRaises(MCError):
                Settings(raw)

    async def test_default_preserves_corrupt_file_and_reports_no_write(self):
        await self.execute("list")
        path = self.corrupt()
        count = len(self.backups())
        result = await self.execute("add", ["qicaiyu"])
        self.assertIn("白名单文件未更新", result)
        self.assertNotIn("下次启动", result)
        self.assertEqual(path.read_bytes(), b"{broken")
        self.assertEqual(len(self.backups()), count)
        self.assertEqual(self.file_names("s1"), ["qicaiyu"])

    async def test_enabled_repairs_pending_file_and_preserves_entry_identity(self):
        await self.execute("add", ["qicaiyu"])
        original = dict(self.service.store.entries("g1")[0])
        self.corrupt()
        self.enable("s2")
        count = set(self.backups())
        result = await self.execute("sync", actor=self.admin)
        self.assertNotIn("待同步", result)
        self.assertEqual(self.file_names("s2"), ["qicaiyu"])
        self.assertEqual(dict(self.service.store.entries("g1")[0]), original)
        self.assertEqual([path.read_bytes() for path in set(self.backups()) - count], [b"{broken"])
        self.assertNotIn("s2", self.service.store.dirty())
        self.assertIn(("s2", "whitelist reload"), core_fixture.FakeRcon.commands)

    async def test_initial_corruption_can_be_skipped_but_healthy_entries_are_imported(self):
        (self.root / "s1" / "whitelist.json").write_text(
            json.dumps([{"name": "Alex", "uuid": offline_uuid("Alex")}]), encoding="utf-8"
        )
        self.corrupt(data=b"")
        self.enable("s2")
        await self.execute("add", ["qicaiyu"])
        self.assertEqual(set(self.file_names("s2")), {"Alex", "qicaiyu"})
        alex = next(row for row in self.service.store.entries("g1") if row["name"] == "Alex")
        self.assertFalse(alex["owner"])
        self.assertTrue(any(path.read_bytes() == b"" for path in self.backups()))

    def test_empty_binary_and_invalid_entries_are_backed_up_before_rebuild(self):
        server = replace(self.service.settings.servers["s2"], whitelist_overwrite=True)
        cases = (
            b"",
            b"{",
            b"{}",
            '[{"name":"Alex","uuid":"bad"}]'.encode(),
            b'[{"name":"Alex"}]',
            "[]".encode("utf-16"),
            json.dumps([{"name": "bad name", "uuid": offline_uuid("Alex")}]).encode(),
            json.dumps([{"name": "Alex", "uuid": offline_uuid("Alex")}] * 2).encode(),
        )
        for content in cases:
            with self.subTest(content=content):
                self.corrupt(data=content)
                before = set(self.backups())
                self.assertTrue(write_whitelist(server, []))
                self.assertEqual(self.file_names("s2"), [])
                self.assertEqual([path.read_bytes() for path in set(self.backups()) - before], [content])
                self.assertFalse(write_whitelist(server, []))

    async def test_backup_failure_retains_original_and_never_attempts_rcon_reload(self):
        await self.execute("add", ["qicaiyu"])
        path = self.corrupt()
        self.enable("s2")
        core_fixture.FakeRcon.commands.clear()
        with patch("mc_manager.files.shutil.copy2", side_effect=PermissionError("test backup denied")):
            result = await self.execute("sync", actor=self.admin)
        self.assertIn("备份失败，已拒绝覆盖", result)
        self.assertIn("白名单文件未更新", result)
        self.assertEqual(path.read_bytes(), b"{broken")
        self.assertNotIn(("s2", "whitelist reload"), core_fixture.FakeRcon.commands)
        self.assertEqual(list((self.root / "s2").glob(".mcwl-*.tmp")), [])
        self.assertIn("s2", self.service.store.dirty())

    async def test_switch_is_per_server_and_disabled_server_remains_protected(self):
        await self.execute("add", ["qicaiyu"])
        bad = self.corrupt("s1")
        self.corrupt("s2")
        self.enable("s2")
        result = await self.execute("sync", actor=self.admin)
        self.assertIn("s1：待同步", result)
        self.assertIn("s2：已同步", result)
        self.assertEqual(bad.read_bytes(), b"{broken")
        self.assertEqual(self.file_names("s2"), ["qicaiyu"])

    async def test_turning_switch_off_restores_protection(self):
        await self.execute("list")
        self.enable("s2")
        self.corrupt()
        await self.execute("sync", actor=self.admin)
        self.enable()
        path = self.corrupt(data=b"second corruption")
        result = await self.execute("sync", actor=self.admin)
        self.assertIn("已拒绝覆盖", result)
        self.assertEqual(path.read_bytes(), b"second corruption")

    async def test_offline_after_rebuild_truthfully_reports_disk_ready(self):
        await self.execute("add", ["qicaiyu"])
        self.enable("s2")
        self.corrupt()
        core_fixture.FakeRcon.offline.add("s2")
        result = await self.execute("sync", actor=self.admin)
        self.assertIn("白名单文件已就绪", result)
        self.assertIn("下次启动", result)
        self.assertNotIn("白名单文件未更新", result)
        self.assertEqual(self.file_names("s2"), ["qicaiyu"])
        self.assertIn("s2", self.service.store.dirty())

    async def test_manual_import_and_diagnostics_remain_strict(self):
        await self.execute("list")
        self.enable("s2")
        path = self.corrupt()
        with self.assertRaises(MCError):
            await self.execute("import", actor=self.admin)
        with self.assertRaises(MCError):
            read_whitelist(self.service.settings.servers["s2"])
        self.assertEqual(path.read_bytes(), b"{broken")

    def test_overwrite_never_bypasses_file_or_backup_symlink_guard(self):
        server = replace(self.service.settings.servers["s2"], whitelist_overwrite=True)
        path = self.corrupt()
        original = Path.is_symlink
        for linked in (path, server.directory / ".mcwl-backups"):
            with self.subTest(linked=linked):

                def is_link(candidate):
                    return candidate == linked or original(candidate)

                with patch.object(Path, "is_symlink", is_link), self.assertRaises(MCError):
                    write_whitelist(server, [])
                self.assertEqual(path.read_bytes(), b"{broken")

    async def test_shared_server_rebuild_uses_union_without_cross_copy(self):
        raw = copy.deepcopy(self.raw)
        raw["groups"][1]["server_ids"] = ["s1", "s3"]
        raw["servers"][0]["whitelist_overwrite"] = True
        self.service.settings = Settings(raw)
        self.service.store.validate_scopes(self.service.settings)
        await self.execute("add", ["Steve"])
        await self.execute("add", ["Alex"], actor=self.group2)
        self.corrupt("s1")
        result = await self.execute("sync", actor=self.admin)
        self.assertNotIn("待同步", result)
        self.assertEqual(set(self.file_names("s1")), {"Steve", "Alex"})
        self.assertEqual(self.file_names("s2"), ["Steve"])
        self.assertEqual(self.file_names("s3"), ["Alex"])
