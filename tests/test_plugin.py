"""Check the plugin adapter using AstrBot's actual public Web API implementation.

Skipped when neither AstrBot nor the researched release API is available.
"""

import asyncio
import importlib.util
import inspect
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from logger_stub import logger_stub

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
        self.modules["astrbot.api"].logger = logger_stub("adapter-tests")

        class Star:
            def __init__(inner, context, config=None):
                inner.context = context
                inner.logger = logger_stub("adapter-tests")

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
        from command_runtime import make_command_runtime

        self.command_runtime = make_command_runtime(RELEASE_ROOT)
        self.modules["astrbot.api.event"].filter = self.command_runtime.api_filter
        module_spec = importlib.util.spec_from_file_location("adapter_fixture.main", ROOT / "main.py")
        self.main = importlib.util.module_from_spec(module_spec)
        sys.modules["adapter_fixture.main"] = self.main
        module_spec.loader.exec_module(self.main)
        self.command_runtime.namespace["star_map"]["adapter_fixture.main"] = types.SimpleNamespace(
            name="astrbot_plugin_mc_manager", activated=True, reserved=False
        )
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
                for index, existing in enumerate(inner.registered_web_apis):
                    if existing[0] == args[0] and existing[2] == args[2]:
                        inner.registered_web_apis[index] = args
                        return
                inner.registered_web_apis.append(args)

            async def send_message(inner, origin, chain):
                return True

            def get_config(inner, umo=None):
                return self.astrbot_config

        self.astrbot_config = {"admins_id": ["admin"]}
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

    async def wait_for_automatic_probe(self):
        async def wait():
            while self.plugin.service.last_status.get("server1") != "在线":
                await asyncio.sleep(0.001)

        await asyncio.wait_for(wait(), 2)

    async def test_web_overwrite_switch_repairs_pending_file_without_reassigning_owner(self):
        actor = self.main.Actor("bot", "100", "member", origin="bot:GroupMessage:100")
        await self.plugin.service.execute(actor, "add", ["qicaiyu"])
        before = dict(self.plugin.service.store.entries("g1")[0])
        path = self.root / "server1" / "whitelist.json"
        path.write_bytes(b"")
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["servers"][0]["whitelist_overwrite"] = True
        with (
            self.bound_request({"config": raw}),
            patch.object(
                self.main,
                "Service",
                lambda settings, path, send, **kwargs: self.real_service(settings, path, send, **kwargs),
            ),
        ):
            response = await self.plugin.page_save_config()
        self.assertEqual(response.status_code, 200, response.body)
        await self.plugin.service.pause()
        self.assertTrue(self.plugin.service.settings.servers["server1"].whitelist_overwrite)
        await self.plugin.service.execute(self.main.Actor("bot", "100", "admin", admin=True), "sync", [])
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))[0]["name"], "qicaiyu")
        self.assertEqual(dict(self.plugin.service.store.entries("g1")[0]), before)
        backups = list((self.root / "server1" / ".mcwl-backups").glob("*.json"))
        self.assertTrue(any(backup.read_bytes() == b"" for backup in backups))
        self.assertIs(self.plugin.service.logger, self.plugin.logger)

    async def test_web_spark_log_options_save_and_action_returns_async_report(self):
        import test_spark_log as log_fixture

        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["servers"][0].update(
            spark_log_fallback=True,
            spark_log_path="logs/latest.log",
            spark_log_encoding="utf-8",
            spark_log_wait_seconds=2,
        )
        raw["messages"] = {"tps": "{server_id}|{query_source}|{tps_source}|{tps_5s}|{mspt_1m_p95}"}
        self.assertEqual((await self.save_with_fake_rcon(raw)).status_code, 200)
        await self.plugin.service.pause()
        path = self.root / "server1/logs/latest.log"
        path.parent.mkdir()
        path.write_bytes(log_fixture.forge_report(log_fixture.SCREENSHOT))
        original = self.FakeRcon.command

        async def command(client, text):
            if text == "spark tps":
                self.FakeRcon.commands.append((client.server.id, text))
                with path.open("ab") as stream:
                    stream.write(log_fixture.forge_report())
                return ""
            return await original(client, text)

        with (
            patch.object(self.FakeRcon, "command", command),
            self.bound_request({"group": "g1", "action": "tps", "args": ["server1"]}),
        ):
            response = await self.plugin.page_action()
        self.assertEqual(response.status_code, 200, response.body)
        self.assertEqual(json.loads(response.body)["message"], "server1|服务端新增日志|log|13.24|77.0")
        self.assertEqual(self.plugin.config["servers"][0]["spark_log_wait_seconds"], 2)

    async def test_old_collector_config_is_hidden_and_cleaned_on_save(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["spark_file_max_age"] = "retired"
        raw["servers"][0].update(tps_source="file", spark_file="../outside.txt")
        raw["messages"] = {"tps": "{tps_1m} {tps_file_age_seconds}"}
        self.plugin.config.update(raw)
        with self.bound_request():
            visible = json.loads((await self.plugin.page_config()).body)["config"]
        self.assertNotIn("spark_file_max_age", visible)
        self.assertNotIn("spark_file", visible["servers"][0])
        with (
            self.bound_request({"config": raw}),
            patch.object(
                self.main,
                "Service",
                lambda settings, path, send, **kwargs: self.real_service(settings, path, send, **kwargs),
            ),
        ):
            response = await self.plugin.page_save_config()
        self.assertEqual(response.status_code, 200, response.body)
        self.assertFalse(self.plugin.config_error)
        self.assertNotIn("spark_file_max_age", self.plugin.config)
        self.assertNotIn("tps_source", self.plugin.config["servers"][0])
        self.assertEqual(self.plugin.config["messages"]["tps"], raw["messages"]["tps"])
        self.assertEqual(self.plugin.config["servers"][0]["rcon_password"], "private-secret")

    async def test_service_and_replacement_factory_share_star_logger(self):
        self.assertIs(self.plugin.service.logger, self.plugin.logger)
        candidate = self.plugin._new_service(self.plugin.settings)
        try:
            self.assertIs(candidate.logger, self.plugin.logger)
        finally:
            await candidate.close()

    async def test_initialize_probes_without_dashboard_refresh_or_chat_command(self):
        await self.plugin.terminate()
        self.FakeRcon.commands.clear()
        with patch.object(
            self.main,
            "Service",
            lambda settings, path, send, **kwargs: self.real_service(settings, path, send, **kwargs),
        ):
            await self.plugin.initialize()
        await self.wait_for_automatic_probe()
        await self.plugin.service.pause()
        with self.bound_request():
            response = await self.plugin.page_snapshot()
        data = json.loads(response.body)
        self.assertEqual(data["servers"][0]["status"], "在线")
        self.assertIn(("server1", "list"), self.FakeRcon.commands)

    async def test_saving_configuration_starts_probe_without_dashboard_refresh(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        self.assertEqual((await self.save_with_fake_rcon(raw)).status_code, 200)
        await self.wait_for_automatic_probe()
        await self.plugin.service.pause()
        with self.bound_request():
            response = await self.plugin.page_snapshot()
        self.assertEqual(json.loads(response.body)["servers"][0]["status"], "在线")

    async def test_web_config_accepts_multiple_chats_sharing_one_server(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["groups"][0]["group_id"] = "100,101"
        raw["groups"].append({"id": "g2", "platform_id": "bot", "group_id": ["200", "201"], "server_ids": ["server1"]})
        response = await self.save_with_fake_rcon(raw)
        self.assertEqual(response.status_code, 200, response.body)
        await self.plugin.service.pause()
        await self.dispatch_command("/mc 添加 Steve", group="101", user="owner1")
        await self.dispatch_command("/mc 添加 Alex", group="201", user="owner2")
        names = {row["name"] for row in json.loads((self.root / "server1/whitelist.json").read_text(encoding="utf-8"))}
        self.assertEqual(names, {"Steve", "Alex"})
        with self.bound_request():
            response = await self.plugin.page_snapshot()
        groups = json.loads(response.body)["groups"]
        self.assertEqual(groups[0]["group_ids"], ["100", "101"])
        self.assertEqual(groups[1]["group_ids"], ["200", "201"])
        self.assertEqual(len(self.plugin._config_bindings()["servers"]), 2)

    async def test_dashboard_can_manage_a_multi_chat_configuration(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["groups"][0]["group_id"] = "100,101"
        self.assertEqual((await self.save_with_fake_rcon(raw)).status_code, 200)
        await self.plugin.service.pause()
        with self.bound_request({"group": "g1", "action": "add", "args": ["Steve"]}):
            response = await self.plugin.page_action()
        self.assertEqual(response.status_code, 200, response.body)
        with self.bound_request({"group": "g1", "action": "remove", "args": ["1"]}):
            response = await self.plugin.page_action()
        self.assertEqual(response.status_code, 200, response.body)

    async def test_multi_chat_overlap_is_rejected_with_existing_service_preserved(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["groups"][0]["group_id"] = "100,101"
        raw["groups"].append({"id": "g2", "platform_id": "bot", "group_id": "101,200", "server_ids": ["server1"]})
        original = self.plugin.service
        response = await self.save_with_fake_rcon(raw)
        self.assertEqual(response.status_code, 400)
        self.assertIn("重复绑定", response.body.decode())
        self.assertIs(self.plugin.service, original)

    async def test_native_core_variables_roundtrip_with_examples_in_official_web_api(self):
        import test_performance as performance_fixture

        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["servers"][0].update(tps_mode="native", tps_command="forge tps", tps_output="template")
        raw["messages"] = {"tps": "{tps_overall}|{mspt_overall}|{forge_dimension_count}|{forge_nether_tps}"}
        response = await self.save_with_fake_rcon(raw)
        self.assertEqual(response.status_code, 200, response.body)
        await self.plugin.service.pause()
        original = self.FakeRcon.command

        async def command(client, text):
            return performance_fixture.FORGE if text == "forge tps" else await original(client, text)

        with (
            patch.object(self.FakeRcon, "command", command),
            self.bound_request({"group": "g1", "action": "tps", "args": ["server1"]}),
        ):
            response = await self.plugin.page_action()
        self.assertEqual(response.status_code, 200, response.body)
        self.assertEqual(json.loads(response.body)["message"], "19.980|13.500|2|20.000")
        with self.bound_request():
            config = json.loads((await self.plugin.page_config()).body)
        self.assertEqual(len(config["tps_examples"]), 6)
        self.assertEqual(config["config"]["servers"][0]["tps_output"], "template")
        tick = next(example for example in config["tps_examples"] if example["id"] == "tick")
        self.assertEqual(tick["preview"]["tps_overall"], "暂无数据")
        self.assertEqual(tick["preview"]["mspt_p95"], "22.4")
        self.assertEqual(self.plugin.config["servers"][0]["rcon_password"], "private-secret")

    async def test_native_tps_mode_roundtrips_and_chat_returns_raw_command_reply(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["servers"][0].update(tps_mode="native", tps_command="native:query", tps_output="raw", tps_source="file")
        raw["messages"] = {"tps": "DO_NOT_FORMAT {tps_5s}"}
        self.assertEqual((await self.save_with_fake_rcon(raw)).status_code, 200)
        await self.plugin.service.pause()
        with self.bound_request():
            config = json.loads((await self.plugin.page_config()).body)
        self.assertNotIn("tps_mode", config["config"]["servers"][0])
        self.assertEqual(config["config"]["servers"][0]["tps_command"], "native:query")
        payload = "Core TPS: 19.97\nTick time: 12.4 ms\nCPU: 35%\n"
        original = self.FakeRcon.command

        async def command(client, value):
            return payload if value == "native:query" else await original(client, value)

        with patch.object(self.FakeRcon, "command", command):
            _, _, replies = await self.dispatch_command("/mc tps server1")
        self.assertEqual(replies, [payload])

    async def test_native_tps_chat_sends_every_chunk_and_web_returns_full_text(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["servers"][0].update(tps_mode="native", tps_command="native:query", tps_output="raw")
        self.assertEqual((await self.save_with_fake_rcon(raw)).status_code, 200)
        await self.plugin.service.pause()
        payload = "Core metrics line: 19.99 TPS 40 ms\n" * 1200 + "END"
        original = self.FakeRcon.command

        async def command(client, value):
            return payload if value == "native:query" else await original(client, value)

        with patch.object(self.FakeRcon, "command", command):
            event, _, replies = await self.dispatch_command("/mc tps server1")
            self.assertEqual(replies, [])
            self.assertGreater(len(event.sent), 1)
            self.assertEqual("".join(event.sent), payload)
            self.assertTrue(all(len(reply) <= 3500 for reply in event.sent))
            with self.bound_request({"group": "g1", "action": "tps", "args": ["server1"]}):
                response = await self.plugin.page_action()
            self.assertEqual(json.loads(response.body)["message"], payload)

    async def test_registered_routes_and_unload_cleanup(self):
        self.assertEqual(len(self.context.registered_web_apis), 8)
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
        self.assertIn("{perf_tps_line}", data["tps_default_template"])
        self.assertIn("{perf_cpu_lines}", data["tps_default_template"])
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
        self.assertEqual(data["bindings"]["groups"], [{"id": "g1", "platform": "bot", "group_id": "100", "active": 1}])
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

    async def test_web_form_can_change_group_number_and_resets_old_broadcast(self):
        self.plugin.service.store.remember("g1", "bot:GroupMessage:100")
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["groups"][0]["group_id"] = "200"
        raw["groups"][0]["broadcast_origin"] = "bot:GroupMessage:100"
        with patch.object(
            self.main,
            "Service",
            lambda settings, path, send, **kwargs: self.real_service(settings, path, send, **kwargs),
        ):
            with self.bound_request({"config": raw, "revision": self.plugin.config_revision}):
                response = await self.plugin.page_save_config()
        self.assertEqual(response.status_code, 200, response.body)
        self.assertEqual(self.plugin.settings.groups["g1"].group_id, "200")
        self.assertEqual(self.plugin.config["groups"][0]["broadcast_origin"], "")
        self.assertEqual(self.plugin.service.store.origin("g1"), "")
        logs, _ = self.plugin.service.store.logs("g1", 1, 100)
        changed = next(row for row in logs if row["action"] == "change_group")
        self.assertEqual(changed["user"], "dashboard:admin")
        self.assertIn("100", changed["detail"])
        self.assertIn("200", changed["detail"])

    async def test_web_routes_are_registered_after_initialization_and_preserve_other_plugins(self):
        def another_handler():
            return None

        other = ("/another_plugin/status", another_handler, ["GET"], "another plugin")
        self.context.registered_web_apis.append(other)
        await self.plugin.initialize()
        self.assertEqual(len(self.context.registered_web_apis), 9)
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

    async def recovery_plugin(self, raw):
        await self.plugin.terminate()
        self.plugin = self.main.MCManagerPlugin(self.context, self.Config(raw))
        with patch.object(
            self.main,
            "Service",
            lambda settings, path, send, **kwargs: self.real_service(settings, path, send, **kwargs),
        ):
            await self.plugin.initialize()
        self.assertIsNone(self.plugin.service)
        self.assertTrue(self.plugin.config_error)
        self.assertEqual(len(self.context.registered_web_apis), 8)

    async def test_incomplete_group_config_loads_repair_ui_without_mutating_original(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["groups"] = [{"id": "new_group", "platform_id": "", "group_id": "", "server_ids": ["server1"]}]
        original = json.loads(json.dumps(raw))
        await self.recovery_plugin(raw)
        self.assertIn("第 1 条", self.plugin.config_error)
        self.assertIn("platform_id", self.plugin.config_error)
        self.assertIn("group_id", self.plugin.config_error)
        self.assertEqual(dict(self.plugin.config), original)
        self.assertEqual((self.root / "server1/whitelist.json").read_text(encoding="utf-8"), "[]")

    async def test_repair_config_api_keeps_secrets_masked_and_history_available(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["groups"][0]["platform_id"] = ""
        await self.recovery_plugin(raw)
        with self.bound_request():
            response = await self.plugin.page_config()
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.body)
        self.assertIn("platform_id", data["config_error"])
        self.assertEqual(data["config"]["servers"][0]["rcon_password"], "__KEEP__")
        self.assertEqual(data["config"]["groups"][0]["platform_id"], "")
        self.assertEqual(data["bindings"]["groups"], [{"id": "g1", "platform": "bot", "group_id": "100", "active": 1}])
        self.assertNotIn("private-secret", response.body.decode())
        self.assertIn("groups", data["schema"])

    async def test_repair_snapshot_succeeds_but_commands_are_blocked(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["groups"][0]["group_id"] = ""
        await self.recovery_plugin(raw)
        self.FakeRcon.commands = []
        with self.bound_request(query="probe=1"):
            response = await self.plugin.page_snapshot()
        data = json.loads(response.body)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["configuration_required"])
        self.assertEqual(data["servers"], [])
        self.assertEqual(self.FakeRcon.commands, [])
        with self.assertRaisesRegex(self.main.MCError, "配置待修正"):
            self.plugin._service()
        with self.bound_request({"group": "g1", "action": "restart", "args": ["server1"]}):
            response = await self.plugin.page_action()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.FakeRcon.commands, [])

    async def test_corrected_config_activates_service_and_preserves_password(self):
        corrected = json.loads(json.dumps(dict(self.plugin.config)))
        raw = json.loads(json.dumps(corrected))
        raw["groups"][0]["platform_id"] = ""
        raw["groups"][0]["group_id"] = ""
        await self.recovery_plugin(raw)
        old_revision = self.plugin.config_revision
        corrected["servers"][0]["rcon_password"] = "__KEEP__"
        with patch.object(
            self.main,
            "Service",
            lambda settings, path, send, **kwargs: self.real_service(settings, path, send, **kwargs),
        ):
            with self.bound_request({"config": corrected, "revision": old_revision}):
                response = await self.plugin.page_save_config()
        self.assertEqual(response.status_code, 200, response.body)
        self.assertEqual(self.plugin.config_error, "")
        self.assertIsNotNone(self.plugin.service)
        self.assertEqual(self.plugin.settings.groups["g1"].group_id, "100")
        self.assertEqual(self.plugin.config["servers"][0]["rcon_password"], "private-secret")
        self.assertNotEqual(self.plugin.config_revision, old_revision)
        with self.bound_request():
            snapshot = json.loads((await self.plugin.page_snapshot()).body)
        self.assertFalse(snapshot["configuration_required"])
        self.assertEqual(snapshot["config_error"], "")

    async def test_bad_repair_save_preserves_recovery_state_and_history(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["groups"][0]["group_id"] = ""
        await self.recovery_plugin(raw)
        error = self.plugin.config_error
        revision = self.plugin.config_revision
        with self.bound_request({"config": raw, "revision": revision}):
            response = await self.plugin.page_save_config()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.plugin.config_error, error)
        self.assertEqual(self.plugin.config_revision, revision)
        self.assertIsNone(self.plugin.service)
        self.assertEqual(self.plugin._config_bindings()["groups"][0]["group_id"], "100")

    async def test_valid_native_group_number_change_loads_as_current_binding(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["groups"][0]["group_id"] = "200"
        await self.plugin.terminate()
        self.plugin = self.main.MCManagerPlugin(self.context, self.Config(raw))
        with patch.object(
            self.main,
            "Service",
            lambda settings, path, send, **kwargs: self.real_service(settings, path, send, **kwargs),
        ):
            await self.plugin.initialize()
        self.assertEqual(self.plugin.config_error, "")
        self.assertIsNotNone(self.plugin.service)
        self.assertEqual(self.plugin._config_bindings()["groups"][0]["group_id"], "200")

    async def test_malformed_server_structure_can_be_read_and_fixed_in_repair_mode(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["servers"] = {"rcon_password": "private-secret"}
        await self.recovery_plugin(raw)
        with self.bound_request():
            response = await self.plugin.page_config()
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.body)
        self.assertEqual(data["config"]["servers"]["rcon_password"], "__KEEP__")
        self.assertNotIn("private-secret", response.body.decode())

    async def test_repair_mode_does_not_skip_dashboard_authentication(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["groups"] = [{"id": "", "platform_id": "", "group_id": ""}]
        await self.recovery_plugin(raw)
        with self.bound_request(username=None):
            response = await self.plugin.page_config()
        self.assertEqual(response.status_code, 400)
        self.assertIn("登录", json.loads(response.body)["message"])
        with self.bound_request(username=None):
            response = await self.plugin.page_snapshot()
        self.assertEqual(response.status_code, 400)

    async def save_with_fake_rcon(self, raw):
        with patch.object(
            self.main,
            "Service",
            lambda settings, path, send, **kwargs: self.real_service(settings, path, send, **kwargs),
        ):
            with self.bound_request({"config": raw, "revision": self.plugin.config_revision}):
                return await self.plugin.page_save_config()

    async def test_removed_group_releases_server_in_config_api_and_keeps_archive(self):
        actor = self.main.Actor("bot", "100", "member", origin="bot:GroupMessage:100")
        await self.plugin.service.execute(actor, "add", ["Steve"])
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["groups"] = []
        response = await self.save_with_fake_rcon(raw)
        self.assertEqual(response.status_code, 200, response.body)
        with self.bound_request():
            data = json.loads((await self.plugin.page_config()).body)
        self.assertEqual(data["bindings"]["servers"], [])
        self.assertEqual(data["bindings"]["groups"][0]["active"], 0)
        self.assertEqual(self.plugin.service.store.entries("g1")[0]["name"], "Steve")
        self.assertEqual(self.plugin.service.store.origin("g1"), "")

    async def test_released_server_can_bind_new_group_through_config_save(self):
        await self.plugin.service.execute(self.main.Actor("bot", "100", "member"), "add", ["Steve"])
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["groups"] = []
        response = await self.save_with_fake_rcon(raw)
        self.assertEqual(response.status_code, 200)
        raw["groups"] = [{"id": "g2", "platform_id": "bot", "group_id": "200", "server_ids": ["server1"]}]
        response = await self.save_with_fake_rcon(raw)
        self.assertEqual(response.status_code, 200, response.body)
        await self.plugin.service.pause()
        await self.plugin.service.sync_group(self.plugin.settings.groups["g2"])
        self.assertEqual(self.plugin.service.store.entries("g2"), [])
        self.assertEqual(json.loads((self.root / "server1/whitelist.json").read_text(encoding="utf-8")), [])
        self.assertEqual(self.plugin._config_bindings()["servers"], [{"server": "server1", "scope": "g2"}])
        self.assertEqual(self.plugin.service.store.entries("g1")[0]["name"], "Steve")

    async def test_rebinding_database_error_rolls_back_config_and_resumes_original_service(self):
        original = self.plugin.service
        original.store.remember("g1", "bot:GroupMessage:100")
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["groups"][0]["group_id"] = "200"
        store_type = type(original.store)
        with patch.object(store_type, "audit", side_effect=RuntimeError("binding audit unavailable")):
            response = await self.save_with_fake_rcon(raw)
        self.assertEqual(response.status_code, 400)
        self.assertIs(self.plugin.service, original)
        self.assertFalse(original.closed)
        self.assertIsNotNone(original.background)
        self.assertEqual(self.plugin.config["groups"][0]["group_id"], "100")
        self.assertEqual(original.store.origin("g1"), "bot:GroupMessage:100")
        self.assertEqual(original.store.db.execute("SELECT group_id FROM scopes WHERE id='g1'").fetchone()[0], "100")

    async def test_deleting_all_groups_cleans_up_legacy_sticky_bindings_on_load(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["groups"] = []
        await self.plugin.terminate()
        self.plugin = self.main.MCManagerPlugin(self.context, self.Config(raw))
        with patch.object(
            self.main,
            "Service",
            lambda settings, path, send, **kwargs: self.real_service(settings, path, send, **kwargs),
        ):
            await self.plugin.initialize()
        self.assertEqual(self.plugin.config_error, "")
        self.assertEqual(self.plugin._config_bindings()["servers"], [])
        self.assertEqual(self.plugin._config_bindings()["groups"][0]["active"], 0)

    async def dispatch_command(
        self, text, *, group="100", platform="bot", user="member", wake_prefix=None, event_role="member", extras=None
    ):
        from command_runtime import CommandEvent, wake_event

        event = CommandEvent(text, self.command_runtime.namespace["MessageType"].GROUP_MESSAGE, group, platform, user)
        event.role = event_role
        for key, value in (extras or {}).items():
            event.set_extra(key, value)
        handlers = await wake_event(self.command_runtime, event, wake_prefix=wake_prefix)
        results = []
        for handler in handlers:
            kwargs = event.get_extra("handlers_parsed_params", {}).get(handler.handler_full_name, {})
            response = handler.handler(self.plugin, event, **kwargs)
            if inspect.isawaitable(response):
                await response
            else:
                async for result in response:
                    results.append(result)
        return event, handlers, results

    async def enable_bridge(self):
        self.plugin.config["bridge_enabled"] = True
        self.plugin.config["groups"][0]["bridge_enabled"] = True
        self.plugin.config["bridge_cooldown_seconds"] = 0
        settings = self.main.Settings(dict(self.plugin.config))
        self.plugin.settings = self.plugin.service.settings = settings
        self.main.MCCommandFilter.settings = settings

    async def dispatch_bridge(self, text, *, user="member", group="100", platform="bot"):
        from command_runtime import CommandEvent, wake_event

        event = CommandEvent(text, self.command_runtime.namespace["MessageType"].GROUP_MESSAGE, group, platform, user)
        plain = self.modules["astrbot.api.message_components"].Plain(text)
        event.get_messages = lambda: [plain]
        handlers = await wake_event(self.command_runtime, event)
        for handler in handlers:
            if handler.handler_name == "relay_group_message":
                await handler.handler(self.plugin, event)
        return event, handlers

    async def test_bridge_receives_ordinary_group_message_without_waking_llm_or_stopping_event(self):
        await self.enable_bridge()
        event, handlers = await self.dispatch_bridge("hello 世界")
        self.assertEqual([item.handler_name for item in handlers], ["relay_group_message"])
        self.assertFalse(event.is_at_or_wake_command)
        self.assertFalse(event.stopped)
        self.assertEqual(self.plugin.service.bridge_queue.qsize(), 1)
        await self.plugin.service.bridge_flush()
        self.assertTrue(any(command.startswith("tellraw @a ") for _, command in self.FakeRcon.commands))

    async def test_bridge_does_not_relay_mc_commands_or_bot_self_or_other_group(self):
        await self.enable_bridge()
        for text, kwargs in (
            ("/mc help", {}),
            ("mc 添加 Steve", {}),
            ("hello", {"user": "bot-self"}),
            ("hello", {"group": "999"}),
            ("hello", {"platform": "wrong"}),
            ("hello", {"group": ""}),
        ):
            with self.subTest(text=text, kwargs=kwargs):
                _, handlers = await self.dispatch_bridge(text, **kwargs)
                self.assertFalse(any(item.handler_name == "relay_group_message" for item in handlers))
        self.assertEqual(self.plugin.service.bridge_queue.qsize(), 0)

    async def test_bridge_switches_can_be_saved_and_applied_through_authenticated_web_api(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["bridge_enabled"] = True
        raw["groups"][0].update(bridge_enabled=True, bridge_prefix="#mc", bridge_death=False)
        with (
            self.bound_request({"config": raw}),
            patch.object(
                self.main,
                "Service",
                lambda settings, path, send, **kwargs: self.real_service(settings, path, send, **kwargs),
            ),
        ):
            response = await self.plugin.page_save_config()
        self.assertEqual(response.status_code, 200, response.body)
        self.assertTrue(self.plugin.service.settings.bridge_enabled)
        self.assertIs(self.plugin.service.logger, self.plugin.logger)
        self.assertEqual(self.plugin.service.settings.groups["g1"].bridge_prefix, "#mc")
        self.assertFalse(self.plugin.service.settings.groups["g1"].bridge_death)
        self.assertIsNotNone(self.plugin.service.bridge_poll_task)

    async def test_mc_is_present_in_real_command_registry_with_native_filter(self):
        handlers = self.command_runtime.registry.get_handlers_by_event_type(
            self.command_runtime.namespace["EventType"].AdapterMessageEvent
        )
        self.assertEqual(len(handlers), 2)
        handlers = [item for item in handlers if item.handler_name == "mc"]
        self.assertEqual(len(handlers), 1)
        native = [item for item in handlers[0].event_filters if isinstance(item, self.command_runtime.command_filter)]
        self.assertEqual(len(native), 1)
        self.assertEqual(native[0].command_name, "mc")
        self.assertEqual(native[0].get_complete_command_names(), ["mc"])
        self.assertIsInstance(handlers[0].event_filters[0], self.main.MCCommandFilter)
        self.assertIn("/mc", handlers[0].desc)

    async def test_slash_mc_and_both_help_aliases_wake_and_reply_once(self):
        for text in ["/mc", "/mc help", "/mc 帮助"]:
            with self.subTest(text=text):
                event, handlers, results = await self.dispatch_command(text)
                self.assertTrue(event.is_wake)
                self.assertEqual(len(handlers), 1)
                self.assertEqual(len(results), 1)
                self.assertIn("Minecraft 管理菜单", results[0])

    async def test_plain_mc_and_slash_mc_work_with_different_global_wake_prefix(self):
        for text in ["mc", "/mc", "MC HELP"]:
            with self.subTest(text=text):
                event, handlers, results = await self.dispatch_command(text, wake_prefix=["!"])
                self.assertTrue(event.is_wake)
                self.assertEqual(len(handlers), 1)
                self.assertIn("Minecraft 管理菜单", results[0])

    async def test_mc_still_works_when_global_wake_prefix_consumes_its_name(self):
        for text, prefixes in [("/mc", ["/mc"]), ("/mc help", ["/mc"]), ("mc 帮助", ["mc"]), ("/mc 查看", ["/mc "])]:
            with self.subTest(text=text, prefixes=prefixes):
                _, handlers, results = await self.dispatch_command(text, wake_prefix=prefixes)
                self.assertEqual(len(handlers), 1)
                self.assertEqual(len(results), 1)
                self.assertNotIn("未知子指令", results[0])

        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["commands"] = {"add": "mc"}
        self.assertEqual((await self.save_with_fake_rcon(raw)).status_code, 200)
        _, handlers, results = await self.dispatch_command("/mc mc Steve", wake_prefix=["/mc"])
        self.assertEqual(len(handlers), 1)
        self.assertIn("已添加 #1 Steve", results[0])

    async def test_registered_command_preserves_all_add_list_remove_arguments(self):
        _, _, result = await self.dispatch_command("/mc 添加 Steve")
        self.assertIn("已添加 #1 Steve", result[0])
        self.assertEqual(self.plugin.service.store.entries("g1")[0]["name"], "Steve")
        _, _, result = await self.dispatch_command("/mc 查看 1")
        self.assertIn("#1 Steve", result[0])
        _, _, result = await self.dispatch_command("/mc 修改 1 Alex")
        self.assertIn("Steve → Alex", result[0])
        _, _, result = await self.dispatch_command("/mc 移除 1")
        self.assertIn("已移除 #1 Alex", result[0])
        self.assertEqual(self.plugin.service.store.entries("g1"), [])

    async def test_custom_prefix_routes_through_native_mc_and_fixed_mc_still_works(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["commands"] = {"prefix": "minecraft,我的世界", "add": "add,申请"}
        response = await self.save_with_fake_rcon(raw)
        self.assertEqual(response.status_code, 200)
        for text in ["/minecraft", "我的世界 帮助", "/mc help"]:
            event, handlers, results = await self.dispatch_command(text)
            self.assertEqual(len(handlers), 1)
            self.assertEqual(len(results), 1)
            self.assertIn("Minecraft 管理菜单", results[0])
            self.assertEqual(event.message_str.split()[0], "mc")
        _, _, result = await self.dispatch_command("/minecraft add Steve")
        self.assertIn("已添加 #1 Steve", result[0])

    async def test_near_prefix_matches_do_not_wake_or_write(self):
        for text in ["mctps", "/mcfoo 添加 Steve", "有人说 mc 添加 Steve", "/m c", ""]:
            event, handlers, results = await self.dispatch_command(text)
            self.assertEqual(handlers, [])
            self.assertEqual(results, [])
        self.assertEqual(self.plugin.service.store.entries("g1"), [])

    async def test_native_command_disable_blocks_fixed_and_custom_prefixes(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["commands"] = {"prefix": "minecraft"}
        self.assertEqual((await self.save_with_fake_rcon(raw)).status_code, 200)
        metadata = next(iter(self.command_runtime.registry.star_handlers_map.values()))
        metadata.enabled = False
        for text in ["/mc", "mc", "/minecraft", "minecraft"]:
            _, handlers, results = await self.dispatch_command(text)
            self.assertEqual(handlers, [])
            self.assertEqual(results, [])
        self.assertEqual(self.plugin.service.store.entries("g1"), [])

    async def test_registered_command_does_not_bypass_group_or_owner_permissions(self):
        _, _, results = await self.dispatch_command("/mc 添加 Steve", group="unbound")
        self.assertIn("未绑定", results[0])
        _, _, results = await self.dispatch_command("/mc 添加 Steve", group="")
        self.assertIn("仅允许在绑定的群聊", results[0])
        await self.dispatch_command("/mc 添加 Steve", user="owner")
        _, _, results = await self.dispatch_command("/mc 移除 1", user="other")
        self.assertIn("自己的", results[0])
        self.assertEqual(self.plugin.service.store.entries("g1")[0]["name"], "Steve")

    async def test_chat_admin_role_requires_configured_identity_for_all_mutations(self):
        await self.dispatch_command("/mc 添加 Steve", user="owner")
        entries_before = self.plugin.service.store.entries("g1")
        logs_before = self.plugin.service.store.logs("g1", 1, 100)
        file_path = self.root / "server1" / "whitelist.json"
        file_before = file_path.read_bytes()
        self.FakeRcon.commands.clear()
        commands = [
            "/mc 移除 1",
            "/mc 移除 Steve",
            "/mc 删除 1",
            "/mc remove Steve",
            "/mc 修改 1 Alex",
            "/mc edit Steve Alex",
            "/mc 日志",
            "/mc 同步",
            "/mc 导入",
            "/mc 重启 server1",
        ]
        for command in commands:
            with self.subTest(command=command):
                _, _, results = await self.dispatch_command(command, user="other", event_role="admin")
                self.assertIn("MC 操作失败", results[0])
                self.assertEqual(self.plugin.service.store.entries("g1"), entries_before)
                self.assertEqual(self.plugin.service.store.logs("g1", 1, 100), logs_before)
                self.assertEqual(file_path.read_bytes(), file_before)
                self.assertEqual(self.FakeRcon.commands, [])

    async def test_truthy_non_boolean_admin_result_does_not_grant_permissions(self):
        from command_runtime import CommandEvent

        await self.dispatch_command("/mc 添加 Steve", user="owner")
        for value in ["false", "admin", 1]:
            with self.subTest(value=value), patch.object(CommandEvent, "is_admin", return_value=value):
                _, _, results = await self.dispatch_command("/mc 移除 1", user="other")
                self.assertIn("自己的", results[0])
                self.assertEqual(self.plugin.service.store.entries("g1")[0]["owner"], "owner")

    async def test_configured_global_admin_can_modify_and_remove_other_entries(self):
        await self.dispatch_command("/mc 添加 Steve", user="owner")
        _, _, results = await self.dispatch_command("/mc 修改 1 Alex", user="admin")
        self.assertIn("已修改", results[0])
        self.assertEqual(self.plugin.service.store.entries("g1")[0]["owner"], "owner")
        _, _, results = await self.dispatch_command("/mc 移除 1", user="admin")
        self.assertIn("已移除", results[0])
        self.assertEqual(self.plugin.service.store.entries("g1"), [])

    async def test_group_admin_permission_does_not_require_global_role(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["groups"][0]["admin_ids"] = ["group-admin"]
        self.assertEqual((await self.save_with_fake_rcon(raw)).status_code, 200)
        await self.dispatch_command("/mc 添加 Steve", user="owner")
        _, _, results = await self.dispatch_command("/mc 移除 1", user="group-admin")
        self.assertIn("已移除", results[0])

    async def test_api_key_admin_downgrade_cannot_be_overridden_by_stale_role(self):
        await self.dispatch_command("/mc 添加 Steve", user="owner")
        _, _, results = await self.dispatch_command(
            "/mc 移除 1", user="admin", event_role="admin", extras={"_api_key_allow_admin_role": False}
        )
        self.assertIn("自己的", results[0])
        self.assertEqual(self.plugin.service.store.entries("g1")[0]["owner"], "owner")

    async def test_global_admin_config_error_does_not_grant_access(self):
        await self.dispatch_command("/mc 添加 Steve", user="owner")
        with patch.object(self.context, "get_config", side_effect=RuntimeError("config unavailable")):
            _, _, results = await self.dispatch_command("/mc 移除 1", user="admin")
        self.assertIn("自己的", results[0])
        self.assertEqual(self.plugin.service.store.entries("g1")[0]["owner"], "owner")

    async def test_malformed_global_admin_list_does_not_use_substring_membership(self):
        await self.dispatch_command("/mc 添加 Steve", user="owner")
        self.astrbot_config["admins_id"] = "prefix-other-suffix"
        _, _, results = await self.dispatch_command("/mc 移除 1", user="other", event_role="admin")
        self.assertIn("自己的", results[0])
        self.assertEqual(self.plugin.service.store.entries("g1")[0]["owner"], "owner")

    async def test_custom_command_aliases_enforce_ownership_with_stale_admin_role(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["commands"] = {"prefix": "minecraft", "remove": "drop", "modify": "rename"}
        self.assertEqual((await self.save_with_fake_rcon(raw)).status_code, 200)
        await self.dispatch_command("/mc 添加 Steve", user="owner")
        before = self.plugin.service.store.entries("g1")
        for command in ["/mc drop 1", "/minecraft drop Steve", "minecraft rename 1 Alex"]:
            with self.subTest(command=command):
                _, _, results = await self.dispatch_command(command, user="other", event_role="admin")
                self.assertIn("自己的", results[0])
                self.assertEqual(self.plugin.service.store.entries("g1"), before)
        _, _, results = await self.dispatch_command("/minecraft drop Steve", user="owner")
        self.assertIn("已移除", results[0])

    async def test_chat_member_cannot_remove_dashboard_owned_entry(self):
        with self.bound_request({"group": "g1", "action": "add", "args": ["Steve"]}):
            self.assertEqual((await self.plugin.page_action()).status_code, 200)
        _, _, results = await self.dispatch_command("/mc 移除 1", user="other", event_role="admin")
        self.assertIn("自己的", results[0])
        self.assertEqual(self.plugin.service.store.entries("g1")[0]["owner"], "dashboard:admin")

    async def test_chat_without_sender_identity_does_not_create_whitelist_entries(self):
        for role in ["member", "admin"]:
            with self.subTest(role=role):
                _, _, results = await self.dispatch_command("/mc 添加 Steve", user="", event_role=role)
                self.assertIn("身份", results[0])
                self.assertEqual(self.plugin.service.store.entries("g1"), [])
                self.assertFalse(self.plugin.service.store.initialized("g1"))

    async def test_registered_mc_shows_configuration_repair_error_when_setup_invalid(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["groups"][0]["group_id"] = ""
        await self.recovery_plugin(raw)
        _, handlers, results = await self.dispatch_command("/mc")
        self.assertEqual(len(handlers), 1)
        self.assertIn("配置待修正", results[0])

    async def test_compact_server_binding_save_persists_canonical_ids_only(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        (self.root / "server2").mkdir()
        (self.root / "server2/whitelist.json").write_text("[]", encoding="utf-8")
        raw["servers"].append(
            {"id": "server2", "directory": "server2", "rcon_port": 25576, "rcon_password": "test-only"}
        )
        raw["groups"][0]["server_ids"] = ["server1,server2"]
        raw["groups"][0]["admin_ids"] = ["member,admin"]
        with (
            self.bound_request({"config": raw, "revision": self.plugin.config_revision}),
            patch.object(
                self.main,
                "Service",
                lambda settings, path, send, **kwargs: self.real_service(settings, path, send, **kwargs),
            ),
        ):
            response = await self.plugin.page_save_config()
        self.assertEqual(response.status_code, 200, response.body)
        self.assertEqual(self.plugin.config["groups"][0]["server_ids"], ["server1", "server2"])
        self.assertEqual(self.saved["groups"][0]["server_ids"], ["server1", "server2"])
        self.assertEqual(self.plugin.settings.groups["g1"].servers, ("server1", "server2"))
        self.assertEqual(self.plugin.config["groups"][0]["admin_ids"], ["member,admin"])
        self.assertFalse(
            self.plugin.settings.is_admin(self.main.Actor("bot", "100", "admin"), self.plugin.settings.groups["g1"])
        )

    async def test_repair_compact_binding_keeps_existing_entry_owner_and_number(self):
        actor = self.main.Actor("bot", "100", "member")
        await self.plugin.service.execute(actor, "add", ["Steve"])
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        (self.root / "server2").mkdir()
        (self.root / "server2/whitelist.json").write_text("[]", encoding="utf-8")
        raw["servers"].append(
            {"id": "server2", "directory": "server2", "rcon_port": 25576, "rcon_password": "test-only"}
        )
        raw["groups"][0]["server_ids"] = ["missing_server"]
        await self.recovery_plugin(raw)
        self.assertIn("missing_server", self.plugin.config_error)
        raw["groups"][0]["server_ids"] = ["server1,server2"]
        with (
            self.bound_request({"config": raw, "revision": self.plugin.config_revision}),
            patch.object(
                self.main,
                "Service",
                lambda settings, path, send, **kwargs: self.real_service(settings, path, send, **kwargs),
            ),
        ):
            response = await self.plugin.page_save_config()
        self.assertEqual(response.status_code, 200, response.body)
        self.assertEqual(self.plugin.config_error, "")
        self.assertEqual(self.plugin.config["groups"][0]["server_ids"], ["server1", "server2"])
        entries = self.plugin.service.store.entries("g1")
        self.assertEqual([(entry["id"], entry["name"], entry["owner"]) for entry in entries], [(1, "Steve", "member")])

    async def test_unknown_compact_binding_is_rejected_without_rebinding_saved_data(self):
        raw = json.loads(json.dumps(dict(self.plugin.config)))
        raw["groups"][0]["server_ids"] = ["server1,missing_server"]
        original = self.plugin.service
        with self.bound_request({"config": raw, "revision": self.plugin.config_revision}):
            response = await self.plugin.page_save_config()
        self.assertEqual(response.status_code, 400)
        self.assertIn("missing_server", response.body.decode())
        self.assertIs(self.plugin.service, original)
        self.assertEqual(self.plugin.config["groups"][0]["server_ids"], ["server1"])
        self.assertEqual([row["server"] for row in original.store.db.execute("SELECT * FROM bindings")], ["server1"])
