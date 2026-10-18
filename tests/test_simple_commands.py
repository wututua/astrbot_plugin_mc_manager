"""Convenience commands retain ownership, explicit choices and custom messages."""

import copy
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_core as fixture

from mc_manager.config import MCError, Settings


class SimpleCommandTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.CoreTests.asyncSetUp
    asyncTearDown = fixture.CoreTests.asyncTearDown
    execute = fixture.CoreTests.execute
    file_names = fixture.CoreTests.file_names

    async def test_short_and_full_help_and_existing_custom_menu(self):
        common = await self.execute("help")
        full = await self.execute("help", ["all"])
        self.assertLess(len(common.splitlines()), 11)
        self.assertIn("帮助 all", common)
        self.assertIn("操作日志", full)
        raw = copy.deepcopy(self.raw)
        raw["messages"] = {"help": "CUSTOM", "help_full": "FULL CUSTOM"}
        self.service.settings = Settings(raw)
        self.assertEqual(await self.execute("help"), "CUSTOM")
        self.assertEqual(await self.execute("help", ["all"]), "FULL CUSTOM")

    async def test_argument_help_is_actionable_and_customizable(self):
        self.assertIn("例如", await self.execute("add"))
        self.assertIn("只有一条", await self.execute("modify"))
        self.service.settings.messages["usage_add"] = "ADD {prefix} {add}"
        self.assertEqual(await self.execute("add"), "ADD /mc 添加")
        self.assertEqual(self.service.store.entries("g1"), [])

    async def test_remove_without_selector_only_removes_the_sole_owned_entry(self):
        await self.execute("add", ["Steve"])
        await self.execute("add", ["Alex"], self.other)
        reply = await self.execute("remove")
        self.assertIn("已移除 #1 Steve", reply)
        self.assertEqual(self.file_names("s1"), ["Alex"])
        self.assertEqual(self.service.store.entries("g1")[0]["owner"], self.other.user)

    async def test_modify_with_only_new_name_infers_sole_owned_entry(self):
        await self.execute("add", ["Steve"])
        self.assertIn("Steve → Alex", await self.execute("modify", ["Alex"]))
        row = self.service.store.entries("g1")[0]
        self.assertEqual((row["id"], row["owner"], row["name"]), (1, self.user.user, "Alex"))

    async def test_multiple_owned_entries_require_an_explicit_choice(self):
        self.service.settings.groups["g1"] = replace(self.service.settings.groups["g1"], max_entries=3)
        await self.execute("add", ["Steve"])
        await self.execute("add", ["Alex"])
        before = self.service.store.entries("g1")
        for action, args in (("remove", []), ("modify", ["NewPlayer"])):
            result = await self.execute(action, args)
            self.assertIn("#1 Steve", result)
            self.assertIn("#2 Alex", result)
            self.assertEqual(self.service.store.entries("g1"), before)

    async def test_no_owned_entry_never_infers_another_users_or_an_imported_row(self):
        await self.execute("add", ["Steve"], self.other)
        before = self.service.store.entries("g1")
        self.assertIn("还没有自己的白名单", await self.execute("remove"))
        self.assertIn("还没有自己的白名单", await self.execute("remove", actor=self.admin))
        self.assertEqual(self.service.store.entries("g1"), before)

    async def test_group_scope_still_applies_to_implicit_selection(self):
        await self.execute("add", ["Steve"])
        self.assertIn("还没有自己的白名单", await self.execute("remove", actor=self.group2))
        self.assertEqual(self.file_names("s1"), ["Steve"])

    async def test_ownership_change_during_name_lookup_is_still_denied(self):
        await self.execute("add", ["Steve"])
        resolve = self.service.resolve_player

        async def change(group, name):
            value = await resolve(group, name)
            with self.service.store.transaction():
                self.service.store.db.execute(
                    "UPDATE entries SET owner=? WHERE scope='g1' AND id=1", (self.other.user,)
                )
            return value

        with patch.object(self.service, "resolve_player", side_effect=change), self.assertRaises(MCError):
            await self.execute("modify", ["Alex"])
        self.assertEqual(self.service.store.entries("g1")[0]["name"], "Steve")

    async def test_restart_without_selector_requires_a_single_server_and_admin(self):
        self.assertIn("请选择服务器", await self.execute("restart", actor=self.admin))
        self.assertFalse(self.service.restart_tasks)
        with self.assertRaises(MCError):
            await self.execute("restart")
        self.service.settings.groups["g1"] = replace(self.service.settings.groups["g1"], servers=("s1",))
        await self.execute("restart", actor=self.admin)
        await self.service.restart_tasks["s1"]
        self.assertEqual([sid for sid, command in fixture.FakeRcon.commands if command == "stop"], ["s1"])

    async def test_implicit_modify_still_uses_approval(self):
        await self.execute("add", ["Steve"])
        self.service.settings.groups["g1"] = replace(self.service.settings.groups["g1"], approval_required=True)
        reply = await self.execute("modify", ["Alex"])
        self.assertIn("等待管理员审核", reply)
        self.assertEqual(self.service.store.entries("g1")[0]["name"], "Steve")
        self.assertEqual(self.service.store.pending_requests("g1")[0]["player"], "Alex")
