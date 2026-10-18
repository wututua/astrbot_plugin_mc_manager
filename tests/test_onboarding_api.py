"""Authenticated properties discovery feeds the existing atomic configuration writer."""

import copy
import errno
import json
import unittest
from unittest.mock import patch

import test_plugin as fixture


class OnboardingAPITests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.PluginAdapterTests.asyncSetUp
    asyncTearDown = fixture.PluginAdapterTests.asyncTearDown
    real_service = fixture.PluginAdapterTests.real_service
    bound_request = fixture.PluginAdapterTests.bound_request
    dispatch_command = fixture.PluginAdapterTests.dispatch_command
    save_with_fake_rcon = fixture.PluginAdapterTests.save_with_fake_rcon

    async def initialize_properties(self):
        await self.plugin.service.pause()
        for name, port in (("server1", 25575), ("new_server", 25576)):
            path = self.root / name
            path.mkdir(exist_ok=True)
            (path / "server.properties").write_text(
                f"enable-rcon=true\nrcon.port={port}\nrcon.password=NewPropertiesSecret000\nonline-mode=false\nwhite-list=true\n",
                "utf-8",
            )
            if not (path / "whitelist.json").exists():
                (path / "whitelist.json").write_text("[]", "utf-8")

    async def scan(self, username="admin"):
        with self.bound_request({"action": "discover_servers", "server_root": str(self.root)}, username=username):
            return await self.plugin.page_operations()

    async def commit(self, scan, group="200", selected=None, username="admin"):
        body = {
            "quick_setup": {
                "token": scan["token"],
                "selected": selected or ["new_server"],
                "platform_id": "bot",
                "group_id": group,
            },
            "revision": scan["revision"],
        }
        with (
            patch.object(
                self.main, "Service", lambda settings, path, send, **kw: self.real_service(settings, path, send, **kw)
            ),
            self.bound_request(body, username=username),
        ):
            return await self.plugin.page_save_config()

    async def test_scan_is_read_only_and_never_exposes_credentials(self):
        await self.initialize_properties()
        before = copy.deepcopy(dict(self.plugin.config))
        changes = self.plugin.service.store.db.total_changes
        files = {p: p.read_bytes() for p in self.root.rglob("*") if p.name in {"server.properties", "whitelist.json"}}
        response = await self.scan()
        self.assertEqual(response.status_code, 200, response.body)
        self.assertNotIn("NewPropertiesSecret000", response.body.decode())
        self.assertNotIn("private-secret", response.body.decode())
        self.assertEqual(dict(self.plugin.config), before)
        self.assertEqual(self.plugin.service.store.db.total_changes, changes)
        self.assertEqual({p: p.read_bytes() for p in files}, files)

    async def test_quick_setup_saves_group_and_server_once_and_commands_work(self):
        await self.initialize_properties()
        scan = json.loads((await self.scan()).body)
        response = await self.commit(scan)
        self.assertEqual(response.status_code, 200, response.body)
        await self.plugin.service.pause()
        data = json.loads(response.body)
        identifier = data["setup"]["servers"][0]
        self.assertEqual(data["setup"]["added"], 1)
        added = next(server for server in self.plugin.config["servers"] if server["id"] == identifier)
        self.assertEqual(added["directory"], "new_server")
        self.assertEqual(added["rcon_password"], "NewPropertiesSecret000")
        self.assertTrue(added["tps_auto_detect"])
        self.assertFalse(self.plugin.setup_plans._plans)
        self.assertNotIn("token", self.plugin.config)
        _, _, replies = await self.dispatch_command("/mc 添加 NewPlayer", group="200")
        self.assertIn("已添加", replies[0])
        self.assertEqual(self.plugin.service.store.entries(data["setup"]["group_id"])[0]["owner"], "member")
        with self.bound_request():
            payload = json.loads((await self.plugin.page_config()).body)
        self.assertNotIn("NewPropertiesSecret000", json.dumps(payload))
        self.assertNotIn("private-secret", json.dumps(payload))

    async def test_existing_group_entries_custom_commands_and_password_are_preserved(self):
        await self.initialize_properties()
        actor = self.main.Actor("bot", "100", "member")
        await self.plugin.service.execute(actor, "add", ["Steve"])
        scan = json.loads((await self.scan()).body)
        response = await self.commit(scan, group="100", selected=["server1", "new_server"])
        self.assertEqual(response.status_code, 200, response.body)
        await self.plugin.service.pause()
        self.assertEqual(len(self.plugin.config["groups"]), 1)
        self.assertEqual(self.plugin.config["servers"][0]["rcon_password"], "private-secret")
        self.assertEqual(self.plugin.service.store.entries("g1")[0]["owner"], "member")
        self.assertEqual(len(self.plugin.config["groups"][0]["server_ids"]), 2)

    async def test_changed_file_stale_revision_and_other_owner_cannot_commit(self):
        await self.initialize_properties()
        scan = json.loads((await self.scan()).body)
        before = copy.deepcopy(dict(self.plugin.config))
        self.assertEqual((await self.commit(scan, username="different-admin")).status_code, 400)
        with (self.root / "new_server/server.properties").open("a", encoding="utf-8") as stream:
            stream.write("motd=changed\n")
        self.assertEqual((await self.commit(scan)).status_code, 400)
        self.assertEqual(dict(self.plugin.config), before)
        scan = json.loads((await self.scan()).body)
        self.plugin.config_revision = "newer-revision"
        self.assertEqual((await self.commit(scan)).status_code, 400)
        self.assertEqual(dict(self.plugin.config), before)

    async def test_scan_and_commit_require_backend_authentication(self):
        await self.initialize_properties()
        self.assertEqual((await self.scan(username="")).status_code, 400)
        scan = json.loads((await self.scan()).body)
        self.assertEqual((await self.commit(scan, username="")).status_code, 400)
        self.assertEqual(len(self.plugin.config["servers"]), 1)

    async def test_failed_atomic_save_preserves_old_config_and_plan_can_retry(self):
        await self.initialize_properties()
        scan = json.loads((await self.scan()).body)
        old = copy.deepcopy(dict(self.plugin.config))
        service = self.plugin.service
        with patch.object(self.plugin.config, "save_config_async", side_effect=OSError(errno.ENOSPC, "disk full")):
            response = await self.commit(scan)
        self.assertEqual(response.status_code, 400)
        self.assertIs(self.plugin.service, service)
        self.assertEqual(dict(self.plugin.config), old)
        self.assertIn(scan["token"], self.plugin.setup_plans._plans)
        self.assertEqual((await self.commit(scan)).status_code, 200)

    async def test_normal_config_save_invalidates_old_scans(self):
        await self.initialize_properties()
        scan = json.loads((await self.scan()).body)
        self.assertEqual((await self.save_with_fake_rcon(copy.deepcopy(dict(self.plugin.config)))).status_code, 200)
        self.assertFalse(self.plugin.setup_plans._plans)
        self.assertEqual((await self.commit(scan)).status_code, 400)
