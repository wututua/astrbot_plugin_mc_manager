"""Compatibility for compact server-reference lists without widening account permissions."""

import copy
import tempfile
import unittest
from pathlib import Path

from mc_manager.config import Actor, MCError, Settings, server_ids


class ServerIdConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for directory in ("test", "XCServer2.7.1"):
            (self.root / directory).mkdir()
        self.raw = {
            "server_root": str(self.root),
            "servers": [
                {"id": "server1", "directory": "test", "rcon_port": 25575},
                {"id": "server2", "directory": "XCServer2.7.1", "rcon_port": 25576},
            ],
            "groups": [{"id": "g1", "platform_id": "bot", "group_id": "100,101", "server_ids": ["server1", "server2"]}],
        }

    def test_compact_and_mixed_list_forms_are_normalized(self):
        for value in (
            ["server1", "server2"],
            ["server1,server2"],
            [" server1，server2 "],
            ["server1\r\nserver2"],
            ["server1,", "\n server2 "],
            "server1,server2",
            "server1\nserver2",
        ):
            with self.subTest(value=value):
                raw = copy.deepcopy(self.raw)
                raw["groups"][0]["server_ids"] = value
                settings = Settings(raw)
                self.assertEqual(settings.groups["g1"].servers, ("server1", "server2"))
                self.assertEqual([group.id for group in settings.server_groups["server1"]], ["g1"])
                self.assertEqual(settings.groups["g1"].group_ids, ("100", "101"))

    def test_startup_parsing_does_not_mutate_original_configuration(self):
        self.raw["groups"][0]["server_ids"] = ["server1,server2"]
        original = copy.deepcopy(self.raw)
        settings = Settings(self.raw)
        self.assertEqual(settings.groups["g1"].servers, ("server1", "server2"))
        self.assertEqual(self.raw, original)

    def test_duplicates_inside_one_group_are_still_rejected(self):
        self.raw["groups"][0]["server_ids"] = ["server1,server2", "server1"]
        with self.assertRaisesRegex(MCError, "重复"):
            Settings(self.raw)

    def test_unknown_ids_report_the_specific_reference_and_group(self):
        self.raw["groups"][0]["server_ids"] = ["server1,missing_server"]
        with self.assertRaisesRegex(MCError, "群 g1.*服务器 ID missing_server 不存在"):
            Settings(self.raw)

    def test_invalid_shapes_and_identifier_characters_are_rejected(self):
        for value in (None, [None], False, {"id": "server1"}, [["server1"]], ["server1 server2"], ["server1|server2"]):
            with self.subTest(value=value):
                raw = copy.deepcopy(self.raw)
                raw["groups"][0]["server_ids"] = value
                with self.assertRaises(MCError):
                    Settings(raw)

    def test_account_ids_and_command_aliases_remain_literal_list_items(self):
        self.raw["groups"][0].update(
            server_ids=["server1,server2"],
            admin_ids=["reader,admin"],
            whitelist_admin_ids=["whitelist,manager"],
            restart_admin_ids=["restart,operator"],
        )
        self.raw["commands"] = {"add": ["add,append"]}
        settings = Settings(self.raw)
        group = settings.groups["g1"]
        self.assertEqual(group.admins, ("reader,admin",))
        self.assertEqual(group.whitelist_admins, ("whitelist,manager",))
        self.assertEqual(group.restart_admins, ("restart,operator",))
        self.assertFalse(settings.is_admin(Actor("bot", "100", "admin"), group))
        self.assertFalse(settings.can_manage(Actor("bot", "100", "manager"), group))
        self.assertFalse(settings.can_restart(Actor("bot", "100", "operator"), group, "server1"))
        self.assertEqual(settings.route("add,append Steve"), ("add", ["Steve"]))
        with self.assertRaises(MCError):
            settings.route("add Steve")

    def test_numeric_identifier_items_keep_string_identity(self):
        self.assertEqual(server_ids([1, "2,3"]), ("1", "2", "3"))
