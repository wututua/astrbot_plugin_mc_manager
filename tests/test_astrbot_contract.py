"""Exercise every AstrBot surface used by the plugin against release source."""

import ast
import copy
import logging
import os
import tempfile
import types
import unittest
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASE = Path(os.environ.get("MC_TEST_ASTRBOT_RELEASE", ROOT / ".research/release/astrbot"))
AVAILABLE = (RELEASE / "core/star/context.py").is_file()


def compile_class(relative, name, methods, namespace):
    source = RELEASE / relative
    tree = ast.parse(source.read_text(encoding="utf-8"))
    original = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name)
    selected = [
        copy.deepcopy(node)
        for node in original.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in methods
    ]
    if {node.name for node in selected} != methods:
        raise AssertionError("A required AstrBot method is missing")
    module = ast.Module(
        body=[
            ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
            ast.ClassDef(name=name, bases=[], keywords=[], body=selected, decorator_list=[]),
        ],
        type_ignores=[],
    )
    namespace.setdefault("__name__", __name__)
    exec(compile(ast.fix_missing_locations(module), str(source), "exec"), namespace)
    return namespace[name]


def session_namespace():
    namespace = {"dataclass": dataclass, "field": field, "__name__": __name__}
    exec(
        compile(
            (RELEASE / "core/platform/message_type.py").read_text(encoding="utf-8"), "message-type-release", "exec"
        ),
        namespace,
    )
    tree = ast.parse((RELEASE / "core/platform/message_session.py").read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    exec(compile(tree, "message-session-release", "exec"), namespace)
    return namespace


@unittest.skipUnless(AVAILABLE, "requires an AstrBot release source")
class AstrBotContractTests(unittest.IsolatedAsyncioTestCase):
    def test_all_plugin_public_imports_are_exported(self):
        main = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        for statement in main.body:
            if not isinstance(statement, ast.ImportFrom) or not (statement.module or "").startswith("astrbot."):
                continue
            relative = statement.module.removeprefix("astrbot.").replace(".", "/")
            module = RELEASE / (relative + ".py")
            if not module.is_file():
                module = RELEASE / relative / "__init__.py"
            tree = ast.parse(module.read_text(encoding="utf-8"))
            names = set()
            for node in tree.body:
                if isinstance(node, ast.ImportFrom):
                    names.update(alias.asname or alias.name for alias in node.names)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.add(node.name)
                elif isinstance(node, ast.Assign):
                    names.update(t.id for t in node.targets if isinstance(t, ast.Name))
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    names.add(node.target.id)
            for symbol in statement.names:
                with self.subTest(module=statement.module, symbol=symbol.name):
                    self.assertTrue(symbol.name in names or (module.parent / symbol.name / "__init__.py").is_file())

    def test_star_accepts_config_and_provides_dedicated_logger(self):
        logger = logging.getLogger("astrbot.plugin.astrbot_plugin_mc_manager")
        namespace = {
            "star_map": {},
            "logging": logging,
            "logger": logging.getLogger("contracts"),
            "LogManager": types.SimpleNamespace(get_plugin_logger=lambda name: logger),
        }
        star = compile_class("core/star/base.py", "Star", {"__init__"}, namespace)
        namespace["star_map"][star.__module__] = types.SimpleNamespace(name="astrbot_plugin_mc_manager")
        context = object()
        config = {"setting": "value"}
        instance = star(context, config)
        self.assertIs(instance.context, context)
        self.assertIs(instance.logger, logger)

    def test_event_identity_admin_and_origin_match_group_permissions(self):
        namespace = session_namespace()
        event_type = compile_class(
            "core/platform/astr_message_event.py",
            "AstrMessageEvent",
            {"get_platform_id", "get_group_id", "get_sender_id", "get_sender_name", "is_admin", "unified_msg_origin"},
            namespace,
        )
        event = event_type()
        event.platform_meta = types.SimpleNamespace(id="qq-bot")
        event.message_obj = types.SimpleNamespace(
            group_id="100", sender=types.SimpleNamespace(user_id="123", nickname="群成员")
        )
        event.role = "admin"
        event.session = namespace["MessageSession"].from_str("qq-bot:GroupMessage:100")
        self.assertEqual(
            (event.get_platform_id(), event.get_group_id(), event.get_sender_id(), event.get_sender_name()),
            ("qq-bot", "100", "123", "群成员"),
        )
        self.assertTrue(event.is_admin())
        self.assertEqual(event.unified_msg_origin, "qq-bot:GroupMessage:100")
        event.role = "member"
        self.assertFalse(event.is_admin())

    def test_context_get_config_uses_effective_session_admin_list(self):
        context_type = compile_class("core/star/context.py", "Context", {"get_config"}, {})
        context = context_type()
        context._config = {"admins_id": ["default-admin"]}
        requested = []
        session_config = {"admins_id": ["session-admin"]}

        def get_conf(origin):
            requested.append(origin)
            return session_config

        context.astrbot_config_mgr = types.SimpleNamespace(get_conf=get_conf)
        self.assertIs(context.get_config(), context._config)
        self.assertIs(context.get_config("qq-bot:GroupMessage:100"), session_config)
        self.assertEqual(requested, ["qq-bot:GroupMessage:100"])

    def test_web_api_registration_replaces_only_matching_method_and_route(self):
        context_type = compile_class("core/star/context.py", "Context", {"register_web_api"}, {})
        context = context_type()
        context.registered_web_apis = []

        async def original():
            pass

        async def replacement():
            pass

        context.register_web_api("/mc/config", original, ["GET"], "first")
        context.register_web_api("/mc/config", replacement, ["GET"], "replacement")
        context.register_web_api("/mc/config", original, ["POST"], "save")
        self.assertEqual(len(context.registered_web_apis), 2)
        self.assertIs(context.registered_web_apis[0][1], replacement)
        self.assertEqual(context.registered_web_apis[1][2], ["POST"])

    async def test_context_sends_group_broadcast_to_matching_platform_instance(self):
        namespace = session_namespace()
        namespace["logger"] = logging.getLogger("contracts")
        context_type = compile_class("core/star/context.py", "Context", {"send_message"}, namespace)
        received = []

        async def send(session, chain):
            received.append((str(session), chain))

        platform = types.SimpleNamespace(
            meta=lambda: types.SimpleNamespace(id="qq-bot", name="aiocqhttp"), send_by_session=send
        )
        context = context_type()
        context.platform_manager = types.SimpleNamespace(platform_insts=[platform])
        context.get_config = lambda **kwargs: {}
        chain = object()
        self.assertTrue(await context.send_message("qq-bot:GroupMessage:100", chain))
        self.assertEqual(received, [("qq-bot:GroupMessage:100", chain)])
        self.assertFalse(await context.send_message("other-bot:GroupMessage:100", chain))

    def test_plugin_data_dir_keeps_existing_database_location(self):
        with tempfile.TemporaryDirectory() as temp:
            namespace = {
                "os": os,
                "Path": Path,
                "get_astrbot_data_path": lambda: temp,
                "ensure_dir": lambda path: Path(path).mkdir(parents=True, exist_ok=True),
            }
            tools = compile_class("core/star/star_tools.py", "StarTools", {"get_data_dir"}, namespace)
            path = tools.get_data_dir("astrbot_plugin_mc_manager")
            self.assertEqual(path, (Path(temp) / "plugin_data" / "astrbot_plugin_mc_manager").resolve())
            self.assertTrue(path.is_dir())

    def test_platform_manager_lists_actual_instances(self):
        manager_type = compile_class("core/platform/manager.py", "PlatformManager", {"get_insts"}, {})
        instance = manager_type()
        instances = [object(), object()]
        instance.platform_insts = instances
        self.assertIs(instance.get_insts(), instances)

    def test_custom_filter_and_lifecycle_contracts_exist(self):
        for filename, class_name, methods in [
            ("core/star/base.py", "Star", {"initialize", "terminate"}),
            ("core/star/filter/custom_filter.py", "CustomFilter", {"__init__", "filter"}),
        ]:
            tree = ast.parse((RELEASE / filename).read_text(encoding="utf-8"))
            cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
            actual = {node.name for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
            self.assertTrue(methods <= actual)
        exports = (RELEASE / "api/event/filter/__init__.py").read_text(encoding="utf-8")
        self.assertIn("register_custom_filter as custom_filter", exports)

    def test_native_sdk_exports_requested_bridge_operations(self):
        sdk = (RELEASE / "dashboard/plugin_page_bridge.js").read_text(encoding="utf-8")
        self.assertTrue("window.AstrBotPluginView" in sdk or "window.AstrBotPluginPage" in sdk)
        for signature in ["ready()", "apiGet(endpoint, params)", "apiPost(endpoint, body)"]:
            self.assertIn(signature, sdk)

    def test_config_persistence_and_web_request_helpers_are_public(self):
        tree = ast.parse((RELEASE / "core/config/astrbot_config.py").read_text(encoding="utf-8"))
        config = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "AstrBotConfig")
        save = next(node for node in config.body if isinstance(node, ast.FunctionDef) and node.name == "save_config")
        self.assertEqual([arg.arg for arg in save.args.args[:1]], ["self"])
        self.assertTrue(len(save.args.args) - len(save.args.defaults) <= 1)
        async_save = next(
            node for node in config.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "save_config_async"
        )
        self.assertTrue(len(async_save.args.args) - len(async_save.args.defaults) <= 1)
        source = (RELEASE / "api/web.py").read_text(encoding="utf-8")
        self.assertIn("async def json(", source)
        self.assertIn("def json_response(", source)
        self.assertIn("def error_response(", source)
        self.assertIn("def username(", source)
