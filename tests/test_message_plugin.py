"""Verify configurable outbound text through both supported official SDKs."""

import json
import unittest
from unittest.mock import AsyncMock, patch

import test_plugin as plugin_fixture


class MessagePluginTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = plugin_fixture.PluginAdapterTests.asyncSetUp
    asyncTearDown = plugin_fixture.PluginAdapterTests.asyncTearDown
    real_service = plugin_fixture.PluginAdapterTests.real_service
    bound_request = plugin_fixture.PluginAdapterTests.bound_request
    dispatch_command = plugin_fixture.PluginAdapterTests.dispatch_command
    recovery_plugin = plugin_fixture.PluginAdapterTests.recovery_plugin
    save_with_fake_rcon = plugin_fixture.PluginAdapterTests.save_with_fake_rcon

    def configure(self, **messages):
        self.plugin.config["messages"] = messages
        settings = self.main.Settings(dict(self.plugin.config))
        self.plugin.settings = self.plugin.service.settings = settings
        self.main.MCCommandFilter.settings = settings

    async def test_empty_help_and_success_emit_no_message_but_execute(self):
        self.configure(help="", added="")
        _, handlers, results = await self.dispatch_command("/mc")
        self.assertTrue(handlers)
        self.assertEqual(results, [])
        _, _, results = await self.dispatch_command("/mc 添加 Steve")
        self.assertEqual(results, [])
        self.assertEqual(self.plugin.service.store.entries("g1")[0]["name"], "Steve")
        self.context.send_message = AsyncMock(return_value=True)
        self.assertTrue(await self.plugin._send("bot:GroupMessage:100", "\n "))
        self.context.send_message.assert_not_awaited()

    async def test_runtime_and_unknown_command_errors_render_configured_text(self):
        self.configure(error="ERROR {error}", error_unknown_command="COMMAND", internal_error="INTERNAL")
        _, _, results = await self.dispatch_command("/mc unknown")
        self.assertEqual(results, ["ERROR COMMAND"])
        with patch.object(self.plugin.service, "execute", side_effect=RuntimeError("private traceback")):
            _, _, results = await self.dispatch_command("/mc")
        self.assertEqual(results, ["ERROR INTERNAL"])

    async def test_valid_messages_survive_invalid_server_config(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["groups"][0]["group_id"] = ""
        raw["messages"] = {"error": "E {error}", "error_main_001": "REPAIR", "added": "{unknown}", "help": "VALID HELP"}
        await self.recovery_plugin(raw)
        self.assertEqual(self.plugin.settings.messages["help"], "VALID HELP")
        _, _, results = await self.dispatch_command("/mc")
        self.assertEqual(results, ["E REPAIR"])

    async def test_catalog_is_served_and_custom_text_persists(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["messages"] = {"error_entry_owner": "PRIVATE", "sync_success": "SYNC {server}", "perf_missing": "N/A"}
        self.assertEqual((await self.save_with_fake_rcon(raw)).status_code, 200)
        with self.bound_request():
            response = await self.plugin.page_config()
        data = json.loads(response.body)
        self.assertGreater(len(data["message_templates"]), 300)
        row = next(row for row in data["message_templates"] if row["key"] == "sync_success")
        self.assertEqual(row["variables"][0]["name"], "server")
        self.assertEqual(data["config"]["messages"]["error_entry_owner"], "PRIVATE")
        self.assertEqual(self.plugin.settings.render("sync_success", server="A"), "SYNC A")
