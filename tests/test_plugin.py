"""Check the plugin adapter using AstrBot's actual public Web API implementation.

Skipped when neither AstrBot nor the researched release API is available.
"""

import asyncio
import importlib.util
import json
import logging
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parent.parent
RELEASE_ROOT = Path(os.environ.get("MC_TEST_ASTRBOT_RELEASE", ROOT / ".research/release/astrbot"))
API_SOURCE = RELEASE_ROOT / "api/web.py"
if not API_SOURCE.exists():
    try:
        spec = importlib.util.find_spec("astrbot")
        if spec and spec.origin:
            API_SOURCE = Path(spec.origin).parent / "api/web.py"
    except (ImportError, ValueError):
        pass
HAS_WEB = API_SOURCE.is_file() and importlib.util.find_spec("fastapi") is not None


@unittest.skipUnless(HAS_WEB, "requires AstrBot public Web API and FastAPI")
class PluginAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "server1").mkdir()
        (self.root / "server1" / "whitelist.json").write_text("[]", encoding="utf-8")
        self.modules = {}
        for name in [
            "astrbot",
            "astrbot.api",
            "astrbot.api.event",
            "astrbot.api.star",
            "astrbot.api.message_components",
            "astrbot.core",
            "astrbot.core.utils",
            "adapter_fixture",
        ]:
            module = types.ModuleType(name)
            module.__path__ = []
            self.modules[name] = module
        self.modules["adapter_fixture"].__path__ = [str(ROOT)]
        self.patcher = patch.dict(sys.modules, self.modules)
        self.patcher.start()
        upload_source = RELEASE_ROOT / "core/utils/upload.py"
        if upload_source.is_file():
            upload_spec = importlib.util.spec_from_file_location("astrbot.core.utils.upload", upload_source)
            upload_module = importlib.util.module_from_spec(upload_spec)
            sys.modules["astrbot.core.utils.upload"] = upload_module
            upload_spec.loader.exec_module(upload_module)
        webspec = importlib.util.spec_from_file_location("astrbot.api.web", API_SOURCE)
        self.web = importlib.util.module_from_spec(webspec)
        sys.modules["astrbot.api.web"] = self.web
        webspec.loader.exec_module(self.web)

        class Config(dict):
            def save_config(inner):
                self.saved = dict(inner)

            async def save_config_async(inner):
                inner.save_config()
                return True

        self.Config = Config
        self.modules["astrbot.api"].AstrBotConfig = Config
        self.modules["astrbot.api"].logger = logging.getLogger("adapter-tests")

        class Star:
            def __init__(inner, context, config=None):
                inner.context = context
                inner.logger = logging.getLogger("adapter-tests")

        class StarTools:
            @classmethod
            def get_data_dir(inner, name):
                return self.root / "data"

        self.modules["astrbot.api.star"].Star = Star
        self.modules["astrbot.api.star"].StarTools = StarTools
        self.modules["astrbot.api.star"].Context = object

        class Plain:
            def __init__(inner, text):
                inner.text = text

        class MessageChain:
            def __init__(inner, chain=None):
                inner.chain = chain or []

            def message(inner, text):
                inner.chain.append(Plain(text))
                return inner

        self.modules["astrbot.api.message_components"].Plain = Plain
        self.modules["astrbot.api.event"].MessageChain = MessageChain
        self.modules["astrbot.api.event"].AstrMessageEvent = object
        self.modules["astrbot.api.event"].filter = types.SimpleNamespace(
            CustomFilter=object, custom_filter=lambda _: lambda fn: fn
        )
        module_spec = importlib.util.spec_from_file_location("adapter_fixture.main", ROOT / "main.py")
        self.main = importlib.util.module_from_spec(module_spec)
        sys.modules["adapter_fixture.main"] = self.main
        module_spec.loader.exec_module(self.main)
        self.main.ServiceOriginal = self.main.Service
        from test_core import FakeRcon

        self.FakeRcon = FakeRcon
        self.FakeRcon.offline = set()
        self.FakeRcon.disconnected = True
        self.FakeRcon.startup_fail = False

        class Context:
            def __init__(inner):
                inner.registered_web_apis = []

            def register_web_api(inner, *args):
                inner.registered_web_apis.append(args)

            async def send_message(inner, origin, chain):
                return True

        self.context = Context()
        config = Config(
            {
                "server_root": str(self.root),
                "servers": [{"id": "server1", "directory": "server1", "rcon_password": "private-secret"}],
                "groups": [{"id": "g1", "platform_id": "bot", "group_id": "100", "server_ids": ["server1"]}],
            }
        )
        self.plugin = self.main.MCManagerPlugin(self.context, config)
        self.saved = None
        with patch.object(
            self.main,
            "Service",
            lambda settings, path, send, **kwargs: self.real_service(settings, path, send, **kwargs),
        ):
            await self.plugin.initialize()
        if self.plugin.service.background:
            self.plugin.service.background.cancel()
            await asyncio.gather(self.plugin.service.background, return_exceptions=True)
            self.plugin.service.background = None

    def real_service(self, settings, path, send, **kwargs):
        return self.main.ServiceOriginal(settings, path, send, rcon_factory=self.FakeRcon, **kwargs)

    async def asyncTearDown(self):
        await self.plugin.terminate()
        self.patcher.stop()
        for name in list(sys.modules):
            if name.startswith("adapter_fixture.") or name in {"astrbot.api.web", "astrbot.core.utils.upload"}:
                sys.modules.pop(name, None)
        self.temp.cleanup()

    def bound_request(self, payload=None, query="", username="admin"):
        from starlette.requests import Request

        async def receive():
            return {"type": "http.request", "body": json.dumps(payload or {}).encode(), "more_body": False}

        raw = Request(
            {"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": query.encode()}, receive
        )
        return self.web.bind_request_context(self.web.PluginRequest(raw, username=username))

    async def test_registered_routes_and_unload_cleanup(self):
        self.assertEqual(len(self.context.registered_web_apis), 6)
        self.assertTrue(all(x[0].startswith("/astrbot_plugin_mc_manager/") for x in self.context.registered_web_apis))
        await self.plugin.terminate()
        self.assertEqual(self.context.registered_web_apis, [])

    async def test_config_secrets_redacted(self):
        with self.bound_request():
            response = await self.plugin.page_config()
        data = json.loads(response.body)
        self.assertEqual(data["config"]["servers"][0]["rcon_password"], "__KEEP__")
        self.assertNotIn("private-secret", response.body.decode())

    async def test_api_requires_dashboard_identity(self):
        with self.bound_request(username=None):
            response = await self.plugin.page_snapshot()
        self.assertEqual(response.status_code, 400)
        self.assertIn("登录", json.loads(response.body)["message"])

    async def test_dashboard_add_remove_and_logs(self):
        with self.bound_request({"group": "g1", "action": "add", "args": ["Steve"]}):
            response = await self.plugin.page_action()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.plugin.service.store.entries("g1")[0]["owner"], "dashboard:admin")
        with self.bound_request(query="group=g1"):
            response = await self.plugin.page_logs()
        self.assertIn("dashboard:admin", response.body.decode())
        with self.bound_request({"group": "g1", "action": "remove", "args": ["1"]}):
            response = await self.plugin.page_action()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.plugin.service.store.entries("g1"), [])

    async def test_invalid_api_action_and_group_rejected(self):
        for payload in [
            {"group": "g1", "action": "raw_command", "args": ["stop"]},
            {"group": "other", "action": "add", "args": ["Steve"]},
        ]:
            with self.bound_request(payload):
                response = await self.plugin.page_action()
            self.assertEqual(response.status_code, 400)

    async def test_hot_save_preserves_secret_and_updates_command_filter(self):
        raw = dict(self.plugin.config)
        raw = json.loads(json.dumps(raw))
        raw["servers"][0]["rcon_password"] = "__KEEP__"
        raw["commands"] = {"prefix": "minecraft", "add": "add"}
        with patch.object(
            self.main,
            "Service",
            lambda settings, path, send, **kwargs: self.real_service(settings, path, send, **kwargs),
        ):
            with self.bound_request({"config": raw}):
                response = await self.plugin.page_save_config()
        self.assertEqual(response.status_code, 200, response.body)
        self.assertEqual(self.plugin.config["servers"][0]["rcon_password"], "private-secret")
        self.assertEqual(self.main.MCCommandFilter.settings.extract("/minecraft add Steve"), "add Steve")
        self.assertIsNotNone(self.saved)

    async def test_invalid_hot_save_leaves_current_service_and_config_intact(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["commands"] = {"add": "help"}
        current = self.plugin.service
        with self.bound_request({"config": raw}):
            response = await self.plugin.page_save_config()
        self.assertEqual(response.status_code, 400)
        self.assertIs(self.plugin.service, current)
        self.assertFalse(current.closed)
        self.assertNotIn("commands", self.plugin.config)

    async def test_failed_persistence_restores_configuration(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["page_size"] = 20
        current = self.plugin.service
        with patch.object(self.plugin.config, "save_config", side_effect=OSError("disk full")):
            with self.bound_request({"config": raw}):
                response = await self.plugin.page_save_config()
        self.assertEqual(response.status_code, 400)
        self.assertIs(self.plugin.service, current)
        self.assertFalse(current.closed)
        self.assertNotIn("page_size", self.plugin.config)

    async def test_failed_new_scope_save_leaves_no_reserved_bindings(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["servers"].append({"id": "server2", "directory": "server2", "rcon_port": 25576})
        raw["groups"].append({"id": "g2", "platform_id": "bot", "group_id": "200", "server_ids": ["server2"]})
        current = self.plugin.service
        with patch.object(self.plugin.config, "save_config", side_effect=OSError("disk full")):
            with self.bound_request({"config": raw}):
                response = await self.plugin.page_save_config()
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(current.store.db.execute("SELECT * FROM scopes WHERE id='g2'").fetchone())
        self.assertIsNone(current.store.db.execute("SELECT * FROM bindings WHERE server='server2'").fetchone())

    async def test_config_exposes_spark_template_and_documented_variables(self):
        with self.bound_request():
            response = await self.plugin.page_config()
        data = json.loads(response.body)
        self.assertIn("{tps_5s}", data["tps_default_template"])
        self.assertIn("{cpu_process_15m}", data["tps_default_template"])
        metrics = [row for row in data["tps_variables"] if row["category"] == "指标"]
        self.assertEqual(len(metrics), 19)
        by_name = {row["name"]: row for row in metrics}
        self.assertEqual(by_name["mspt_10s_p95"]["unit"], "ms")
        self.assertEqual(by_name["cpu_process_1m"]["unit"], "%")

    async def test_config_form_schema_and_platform_options_do_not_leak_credentials(self):
        platform = types.SimpleNamespace(
            meta=lambda: types.SimpleNamespace(
                id="qq-primary",
                name="aiocqhttp",
                adapter_display_name="QQ / OneBot",
                default_config_tmpl={"token": "private-platform-token"},
            )
        )
        self.context.platform_manager = types.SimpleNamespace(get_insts=lambda: [platform])
        with self.bound_request():
            response = await self.plugin.page_config()
        data = json.loads(response.body)
        self.assertEqual(data["platforms"], [{"id": "qq-primary", "name": "aiocqhttp", "display_name": "QQ / OneBot"}])
        self.assertIn("group_id", data["schema"]["groups"]["templates"]["group"]["items"])
        self.assertIn("rcon_password", data["schema"]["servers"]["templates"]["server"]["items"])
        self.assertTrue(data["revision"])
        self.assertEqual(data["bindings"]["groups"], [{"id": "g1", "platform": "bot", "group_id": "100"}])
        self.assertEqual(data["bindings"]["servers"], [{"server": "server1", "scope": "g1"}])
        self.assertNotIn("private-platform-token", response.body.decode())
        self.assertNotIn("private-secret", response.body.decode())

    async def test_stale_config_revision_is_rejected_without_changes(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["page_size"] = 20
        current = self.plugin.service
        with self.bound_request({"config": raw, "revision": "stale-browser-tab"}):
            response = await self.plugin.page_save_config()
        self.assertEqual(response.status_code, 400)
        self.assertIn("其他页面", json.loads(response.body)["message"])
        self.assertIs(self.plugin.service, current)
        self.assertFalse(current.closed)
        self.assertIsNone(self.saved)

    async def test_form_save_updates_group_admins_and_preserves_password(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["servers"][0]["rcon_password"] = "__KEEP__"
        raw["groups"][0]["admin_ids"] = ["admin-a", "admin-b"]
        raw["groups"][0]["max_entries_per_user"] = 3
        raw["commands"] = {"prefix": "minecraft", "add": "add,申请"}
        raw["messages"] = {"restart_success": "恢复 {server}，用时 {seconds} 秒"}
        old_revision = self.plugin.config_revision
        with patch.object(
            self.main,
            "Service",
            lambda settings, path, send, **kwargs: self.real_service(settings, path, send, **kwargs),
        ):
            with self.bound_request({"config": raw, "revision": old_revision}):
                response = await self.plugin.page_save_config()
        self.assertEqual(response.status_code, 200, response.body)
        self.assertNotEqual(self.plugin.config_revision, old_revision)
        group = self.plugin.settings.groups["g1"]
        self.assertEqual(group.admins, ("admin-a", "admin-b"))
        self.assertEqual(group.max_entries, 3)
        self.assertEqual(self.plugin.config["servers"][0]["rcon_password"], "private-secret")
        with self.bound_request():
            data = json.loads((await self.plugin.page_config()).body)
        self.assertEqual(data["config"]["servers"][0]["rcon_password"], "__KEEP__")
        with self.bound_request({"config": raw, "revision": old_revision}):
            response = await self.plugin.page_save_config()
        self.assertEqual(response.status_code, 400)

    async def test_explicit_password_clear_is_persisted(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["servers"][0]["rcon_password"] = ""
        raw["servers"][0]["rcon_password_env"] = "NEW_MC_RCON_ENV"
        with patch.object(
            self.main,
            "Service",
            lambda settings, path, send, **kwargs: self.real_service(settings, path, send, **kwargs),
        ):
            with self.bound_request({"config": raw, "revision": self.plugin.config_revision}):
                response = await self.plugin.page_save_config()
        self.assertEqual(response.status_code, 200, response.body)
        self.assertEqual(self.plugin.config["servers"][0]["rcon_password"], "")
        with self.bound_request():
            response = await self.plugin.page_config()
        self.assertEqual(json.loads(response.body)["config"]["servers"][0]["rcon_password"], "")

    async def test_invalid_form_shapes_return_errors_and_keep_current_service(self):
        for key, invalid in [
            ("servers", {}),
            ("servers", [None]),
            ("groups", "bad"),
            ("commands", []),
            ("messages", []),
        ]:
            with self.subTest(key=key, invalid=invalid):
                raw = json.loads(json.dumps(dict(self.plugin.config)))
                raw[key] = invalid
                current = self.plugin.service
                with self.bound_request({"config": raw}):
                    response = await self.plugin.page_save_config()
                self.assertEqual(response.status_code, 400)
                self.assertIs(self.plugin.service, current)
                self.assertFalse(current.closed)

    async def test_web_form_cannot_reassign_persisted_group_identity(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["groups"][0]["group_id"] = "different-group"
        current = self.plugin.service
        with self.bound_request({"config": raw, "revision": self.plugin.config_revision}):
            response = await self.plugin.page_save_config()
        self.assertEqual(response.status_code, 400)
        self.assertIn("不能改绑", json.loads(response.body)["message"])
        self.assertIs(self.plugin.service, current)
        self.assertEqual(self.plugin.settings.groups["g1"].group_id, "100")

    async def test_web_routes_are_registered_after_initialization_and_preserve_other_plugins(self):
        def another_handler():
            return None

        other = ("/another_plugin/status", another_handler, ["GET"], "another plugin")
        self.context.registered_web_apis.append(other)
        await self.plugin.initialize()
        self.assertEqual(len(self.context.registered_web_apis), 7)
        await self.plugin.terminate()
        self.assertEqual(self.context.registered_web_apis, [other])

    async def test_broadcast_uses_public_message_chain_and_context_sender(self):
        received = []

        async def send(origin, chain):
            received.append((origin, [component.text for component in chain.chain]))
            return True

        self.context.send_message = send
        self.assertTrue(await self.plugin._send("bot:GroupMessage:100", "重启成功"))
        self.assertEqual(received, [("bot:GroupMessage:100", ["重启成功"])])

    async def test_cancelled_config_write_finishes_before_cancellation_returns(self):
        started = asyncio.Event()
        release = asyncio.Event()
        finished = asyncio.Event()

        async def save():
            started.set()
            await release.wait()
            finished.set()
            return True

        with patch.object(self.plugin.config, "save_config_async", side_effect=save):
            task = asyncio.create_task(self.plugin._persist_config())
            await started.wait()
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(finished.is_set())

    async def test_superseded_framework_config_save_keeps_current_service(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["page_size"] = 20
        current = self.plugin.service
        with patch.object(self.plugin.config, "save_config_async", new=AsyncMock(side_effect=[False, True])):
            with self.bound_request({"config": raw, "revision": self.plugin.config_revision}):
                response = await self.plugin.page_save_config()
        self.assertEqual(response.status_code, 400)
        self.assertIn("配置覆盖", json.loads(response.body)["message"])
        self.assertIs(self.plugin.service, current)
        self.assertFalse(current.closed)
        self.assertNotIn("page_size", self.plugin.config)
