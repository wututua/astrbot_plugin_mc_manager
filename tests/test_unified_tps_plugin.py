"""Official SDK persistence and legacy selector migration at the Web API."""

import json
import unittest

import test_plugin as fixture


class UnifiedTPSPluginTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.PluginAdapterTests.asyncSetUp
    asyncTearDown = fixture.PluginAdapterTests.asyncTearDown
    real_service = fixture.PluginAdapterTests.real_service
    bound_request = fixture.PluginAdapterTests.bound_request
    save_with_fake_rcon = fixture.PluginAdapterTests.save_with_fake_rcon
    dispatch_command = fixture.PluginAdapterTests.dispatch_command

    async def test_legacy_native_default_migrates_through_save_read_and_command(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["servers"][0].update(tps_mode="native", tps_output="raw")
        raw["servers"][0].pop("tps_command", None)
        self.assertEqual((await self.save_with_fake_rcon(raw)).status_code, 200)
        await self.plugin.service.pause()
        self.assertNotIn("tps_mode", self.plugin.config["servers"][0])
        self.assertEqual(self.plugin.config["servers"][0]["tps_command"], "tps")
        with self.bound_request():
            data = json.loads((await self.plugin.page_config()).body)
        self.assertEqual(data["config"]["servers"][0]["tps_command"], "tps")
        self.assertNotIn("tps_mode", data["schema"]["servers"]["templates"]["server"]["items"])
        self.assertNotIn("tps_mode", data["config"]["servers"][0])
        _, _, replies = await self.dispatch_command("/mc tps server1")
        self.assertTrue(replies[0].startswith("TPS from last"))
        self.assertIn(("server1", "tps"), self.FakeRcon.commands)

    async def test_command_only_configuration_saves_without_selector(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["servers"][0].update(tps_command="tick query", tps_auto_detect=True, tps_output="template")
        self.assertNotIn("tps_mode", raw["servers"][0])
        self.assertEqual((await self.save_with_fake_rcon(raw)).status_code, 200)
        self.assertEqual(self.plugin.service.settings.servers["server1"].tps_command, "tick query")
        with self.bound_request():
            data = json.loads((await self.plugin.page_config()).body)
        self.assertEqual(data["config"]["servers"][0]["tps_command"], "tick query")
        self.assertTrue(data["config"]["servers"][0]["tps_auto_detect"])
        self.assertEqual(self.plugin.config["servers"][0]["rcon_password"], "private-secret")
