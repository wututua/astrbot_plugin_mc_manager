"""Dashboard draft probing uses authenticated, bounded RCON without config writes."""

import copy
import json
import unittest
from unittest.mock import patch

import test_plugin as fixture


class ConnectionAPITests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.PluginAdapterTests.asyncSetUp
    asyncTearDown = fixture.PluginAdapterTests.asyncTearDown
    real_service = fixture.PluginAdapterTests.real_service
    bound_request = fixture.PluginAdapterTests.bound_request

    def payload(self):
        row = copy.deepcopy(dict(self.plugin.config)["servers"][0])
        row.update(rcon_password="__KEEP__", players_command="stop")
        return {"action": "test_connection", "server_root": str(self.root), "server": row}

    async def test_saved_password_resolves_without_saving_or_changing_store(self):
        await self.plugin.service.pause()
        before = copy.deepcopy(dict(self.plugin.config))
        revision = self.plugin.config_revision
        changes = self.plugin.service.store.db.total_changes
        sent = []

        async def command(client, value):
            sent.append((value, client.server.password))
            return "There are 0 of a max of 20 players online:"

        with patch.object(self.FakeRcon, "command", command), self.bound_request(self.payload()):
            response = await self.plugin.page_operations()
        self.assertEqual(response.status_code, 200, response.body)
        self.assertEqual(sent, [("list", "private-secret")])
        self.assertNotIn("private-secret", response.body.decode())
        self.assertEqual(dict(self.plugin.config), before)
        self.assertEqual(self.plugin.config_revision, revision)
        self.assertEqual(self.plugin.service.store.db.total_changes, changes)

    async def test_new_unsaved_server_can_be_probed_and_is_not_added(self):
        payload = self.payload()
        payload["server"].update(id="new_server", rcon_password="temporary-password", rcon_port=25580)
        with self.bound_request(payload):
            response = await self.plugin.page_operations()
        self.assertEqual(response.status_code, 200, response.body)
        self.assertEqual(json.loads(response.body)["server_id"], "new_server")
        self.assertEqual(len(self.plugin.config["servers"]), 1)

    async def test_unknown_keep_marker_and_unauthenticated_request_are_denied(self):
        payload = self.payload()
        payload["server"]["id"] = "unknown"
        with self.bound_request(payload):
            self.assertEqual((await self.plugin.page_operations()).status_code, 400)
        with self.bound_request(self.payload(), username=""):
            self.assertEqual((await self.plugin.page_operations()).status_code, 400)

    async def test_query_failure_is_actionable_and_does_not_change_config(self):
        before = copy.deepcopy(dict(self.plugin.config))
        with (
            patch.object(self.FakeRcon, "command", side_effect=OSError("connection refused")),
            self.bound_request(self.payload()),
        ):
            response = await self.plugin.page_operations()
        self.assertEqual(response.status_code, 400)
        self.assertIn("RCON", response.body.decode())
        self.assertEqual(dict(self.plugin.config), before)
