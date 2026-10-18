"""A properties-based onboarding flow never exposes credentials or writes game files."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mc_manager.config import MCError
from mc_manager.onboarding import MAX_BYTES, MAX_PLANS, PLAN_TTL, SetupPlans, discover_servers, parse_properties


class PropertiesTests(unittest.TestCase):
    def test_java_separators_comments_and_duplicate_keys(self):
        data = b"  # ignored\n! ignored\na:b\nc d\nkey=old\nkey=new\nblank\n"
        self.assertEqual(parse_properties(data), {"a": "b", "c": "d", "key": "new", "blank": ""})

    def test_escaped_separators_spaces_continuations_and_inline_hash(self):
        data = b"pass\\ key : a\\=b\\:c\\ d # keep\nlong=ab\\\n   cd\\\n\tef\n"
        self.assertEqual(parse_properties(data), {"pass key": "a=b:c d # keep", "long": "abcdef"})

    def test_unicode_utf8_latin1_and_surrogate_pairs(self):
        self.assertEqual(parse_properties(b"k=\\u4e2d\\u6587\\ud83d\\ude00")["k"], "中文😀")
        self.assertEqual(parse_properties("k=密码".encode())["k"], "密码")
        self.assertEqual(parse_properties(b"k=caf\xe9")["k"], "café")

    def test_unknown_escape_and_trailing_value_spaces_are_preserved(self):
        self.assertEqual(parse_properties(b"k=\\z\\tvalue  ")["k"], "z\tvalue  ")

    def test_invalid_unicode_is_rejected(self):
        for value in (b"k=\\u12", b"k=\\uZZZZ", b"k=\\ud800"):
            with self.subTest(value=value), self.assertRaises((ValueError, UnicodeError)):
                parse_properties(value)


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.base = {"servers": [], "groups": [], "messages": {"tps": "CUSTOM {perf_tps}"}}
        self.plans = SetupPlans()

    def tearDown(self):
        self.plans.clear()
        self.temp.cleanup()

    def server(self, name="s1", port=25575, mode="false", extra="", password="FixturePassword000"):
        path = self.root / name
        path.mkdir(parents=True, exist_ok=True)
        (path / "server.properties").write_text(
            f"enable-rcon=true\nrcon.port={port}\nrcon.password={password}\nonline-mode={mode}\nwhite-list=true\n"
            + extra,
            "utf-8",
        )
        (path / "whitelist.json").write_text("[]", "utf-8")
        return path

    def issue(self, config=None):
        scan = discover_servers(str(self.root), config or self.base)
        return self.plans.issue(scan, "admin", "revision-1")

    def prepare(self, result, **changes):
        payload = {
            "token": result["token"],
            "selected": [row["key"] for row in result["servers"] if row["ready"]],
            "platform_id": "bot",
            "group_id": "100",
        }
        payload.update(changes)
        return self.plans.prepare(payload, "admin", "revision-1", self.base, {"groups": [], "servers": []})

    def test_common_directory_and_public_results_are_credential_safe(self):
        self.server("one")
        self.server("two", 25576)
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        result = self.issue()
        self.assertEqual(len(result["servers"]), 2)
        self.assertNotIn("FixturePassword000", json.dumps(result))
        self.assertNotIn("rcon_password", json.dumps(result))
        self.assertEqual({p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}, before)
        raw, summary = self.prepare(result)
        self.assertEqual(summary["added"], 2)
        self.assertEqual(raw["servers"][0]["rcon_password"], "FixturePassword000")
        self.assertTrue(raw["servers"][0]["tps_auto_detect"])
        self.assertEqual(raw["messages"], self.base["messages"])
        self.assertNotIn("FixturePassword000", repr(self.plans._plans))

    def test_single_server_directory_is_accepted(self):
        path = self.server("single")
        scan = discover_servers(str(path), self.base)
        self.assertTrue(scan.single_server)
        self.assertEqual(scan.root, self.root)
        self.assertEqual(scan.records[0].key, "single")

    def test_nested_depth_is_bounded_and_ignored_folders_are_not_scanned(self):
        self.server("instances/two")
        self.server("a/b/too_deep", 25576)
        self.server("plugins/embedded", 25577)
        self.assertEqual([row.key for row in discover_servers(str(self.root), self.base).records], ["instances/two"])

    def test_disabled_rcon_and_invalid_password_are_not_ready(self):
        self.server(extra="enable-rcon=false\n")
        result = self.issue()
        self.assertFalse(result["servers"][0]["ready"])
        with self.assertRaises(MCError):
            self.prepare(result, selected=["s1"])
        self.server(password="__KEEP__")
        self.assertFalse(self.issue()["servers"][0]["ready"])

    def test_duplicate_ports_are_reported_before_saving(self):
        self.server("s1")
        self.server("s2")
        result = self.issue()
        self.assertTrue(all(not row["ready"] for row in result["servers"]))
        self.assertTrue(all(any("重复" in issue for issue in row["issues"]) for row in result["servers"]))

    def test_whitelist_switch_is_reported_without_modifying_it(self):
        path = self.server(extra="white-list=false\n")
        result = self.issue()
        self.assertFalse(result["servers"][0]["whitelist_enabled"])
        self.assertIn("white-list=false", (path / "server.properties").read_text("utf-8"))

    def test_large_and_linked_files_are_not_read(self):
        path = self.server()
        (path / "server.properties").write_bytes(b"x" * (MAX_BYTES + 1))
        self.assertFalse(self.issue()["servers"][0]["ready"])
        self.server()
        with patch("mc_manager.onboarding._linked", side_effect=lambda target: target == path / "server.properties"):
            self.assertFalse(self.issue()["servers"][0]["ready"])

    def test_non_regular_file_is_rejected_before_open(self):
        self.server()
        with (
            patch("mc_manager.onboarding.stat.S_ISREG", return_value=False),
            patch("mc_manager.onboarding.os.open") as opened,
        ):
            self.assertFalse(self.issue()["servers"][0]["ready"])
        opened.assert_not_called()

    def test_missing_directory_and_invalid_input_are_actionable(self):
        for root in ("", str(self.root / "missing"), None, []):
            with self.subTest(root=root), self.assertRaises(MCError):
                discover_servers(root, self.base)

    def test_file_changes_after_scan_require_a_new_scan(self):
        path = self.server()
        result = self.issue()
        with (path / "server.properties").open("a", encoding="utf-8") as stream:
            stream.write("motd=changed\n")
        with self.assertRaisesRegex(MCError, "已改变"):
            self.prepare(result)
        self.assertEqual(self.base["servers"], [])

    def test_owner_revision_and_expiry_are_checked(self):
        self.server()
        result = self.issue()
        payload = {"token": result["token"], "selected": ["s1"], "platform_id": "bot", "group_id": "100"}
        with self.assertRaises(MCError):
            self.plans.prepare(payload, "other", "revision-1", self.base, {})
        with self.assertRaises(MCError):
            self.plans.prepare(payload, "admin", "revision-2", self.base, {})
        result = self.issue()
        self.plans._plans[result["token"]].created -= PLAN_TTL + 1
        with self.assertRaises(MCError):
            self.prepare(result)
        self.assertNotIn(result["token"], self.plans._plans)

    def test_cache_capacity_and_clear_remove_private_plans(self):
        self.server()
        first = self.issue()
        for _ in range(MAX_PLANS):
            self.issue()
        self.assertLessEqual(len(self.plans._plans), MAX_PLANS)
        self.assertNotIn(first["token"], self.plans._plans)
        self.plans.clear()
        self.assertEqual(self.plans._plans, {})

    def test_selected_keys_cannot_inject_paths_or_other_servers(self):
        self.server()
        result = self.issue()
        for selected in (["../outside"], ["s1", "s1"], [{}], [], True):
            with self.subTest(selected=selected), self.assertRaises(MCError):
                self.prepare(result, selected=selected)

    def test_generated_ids_avoid_historical_entries(self):
        self.server()
        result = self.issue()
        raw, _ = self.plans.prepare(
            {"token": result["token"], "selected": ["s1"], "platform_id": "bot", "group_id": "100"},
            "admin",
            "revision-1",
            self.base,
            {"groups": [{"id": "group1"}], "reserved_servers": ["server1"]},
        )
        self.assertEqual(raw["servers"][0]["id"], "server2")
        self.assertEqual(raw["groups"][0]["id"], "group2")

    def test_existing_server_and_group_are_extended_without_overwriting_settings(self):
        self.server("s1")
        self.server("s2", 25576)
        self.base.update(
            server_root=str(self.root),
            servers=[
                {
                    "id": "existing",
                    "directory": "s1",
                    "rcon_port": 25575,
                    "rcon_password_env": "EXAMPLE_ENV",
                    "tps_command": "custom:perf",
                    "online_mode": False,
                }
            ],
            groups=[
                {
                    "id": "g1",
                    "platform_id": "bot",
                    "group_id": "100",
                    "server_ids": ["existing"],
                    "admin_ids": ["owner"],
                }
            ],
        )
        before = copy.deepcopy(self.base)
        result = self.issue()
        raw, summary = self.prepare(result)
        self.assertEqual(summary["added"], 1)
        self.assertEqual(len(raw["groups"]), 1)
        self.assertEqual(raw["groups"][0]["admin_ids"], ["owner"])
        self.assertEqual(raw["groups"][0]["server_ids"], ["existing", "server1"])
        self.assertEqual(raw["servers"][0], self.base["servers"][0])
        self.assertEqual(self.base, before)

    def test_mixed_uuid_modes_and_cross_group_merges_are_rejected(self):
        self.server("s1")
        self.server("s2", 25576, mode="true")
        result = self.issue()
        with self.assertRaises(MCError):
            self.prepare(result)
        self.server("s2", 25576, mode="false")
        self.base.update(
            server_root=str(self.root),
            servers=[{"id": "old", "directory": "s1", "online_mode": False}],
            groups=[
                {"id": "g1", "platform_id": "bot", "group_id": "100", "server_ids": ["old"]},
                {"id": "g2", "platform_id": "bot", "group_id": "200", "server_ids": ["old"]},
            ],
        )
        result = self.issue()
        with self.assertRaisesRegex(MCError, "不同配置组"):
            self.prepare(result, group_id="100,200")

    def test_partial_shared_group_selection_cannot_expand_other_group_access(self):
        self.server()
        self.base.update(
            server_root=str(self.root),
            servers=[{"id": "old", "directory": "s1", "online_mode": False}],
            groups=[{"id": "g1", "platform_id": "bot", "group_id": "100,200", "server_ids": ["old"]}],
        )
        result = self.issue()
        with self.assertRaisesRegex(MCError, "完整群号"):
            self.prepare(result, group_id="100")
        raw, _ = self.prepare(result, group_id="100,200")
        self.assertEqual(len(raw["groups"]), 1)

    def test_invalid_saved_connection_is_reported_without_crashing_scan(self):
        self.server()
        self.base.update(server_root=str(self.root), servers=[{"id": "old", "directory": "s1", "rcon_port": "bad"}])
        result = self.issue()
        self.assertFalse(result["servers"][0]["ready"])
        self.assertTrue(any("需要修正" in issue for issue in result["servers"][0]["issues"]))

    def test_unreadable_subdirectory_is_reported_while_other_servers_remain_available(self):
        self.server()
        denied = self.root / "private"
        denied.mkdir()
        original = __import__("os").scandir

        def scan(path):
            if Path(path) == denied:
                raise PermissionError("denied")
            return original(path)

        with patch("mc_manager.onboarding.os.scandir", side_effect=scan):
            result = self.issue()
        self.assertEqual(len(result["servers"]), 1)
        self.assertTrue(result["warnings"])

    def test_scan_entry_and_file_limits_fail_without_partial_import(self):
        self.server("one")
        self.server("two", 25576)
        with patch("mc_manager.onboarding.MAX_FILES", 1), self.assertRaises(MCError):
            self.issue()
        with patch("mc_manager.onboarding.MAX_ENTRIES", 1), self.assertRaises(MCError):
            self.issue()


class PlanLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_loop_timers_are_cancelled_when_plans_are_cleared(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = SetupPlans()
            result = cache.issue(discover_servers(temporary, {}), "admin", "v")
            handle = cache._timers[result["token"]]
            cache.clear()
            self.assertTrue(handle.cancelled())
            self.assertEqual(cache._timers, {})
