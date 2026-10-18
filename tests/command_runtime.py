"""Load real command decorators, registry, and waking stage from an AstrBot release."""

import ast
import copy
import enum
import inspect
import re
import types
import typing
from abc import ABCMeta, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from logger_stub import logger_stub

H = TypeVar("H")


def execute_nodes(path, namespace, names=None, method_subset=None):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if names is not None and getattr(node, "name", None) not in names:
            continue
        selected = copy.deepcopy(node)
        if isinstance(selected, ast.ClassDef):
            # A registry/waking stage needs only the selected original methods;
            # strip overload stubs and inherited runtime plumbing.
            if method_subset and selected.name in method_subset:
                selected.bases = []
                selected.decorator_list = []
                selected.body = [
                    method
                    for method in selected.body
                    if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and method.name in method_subset[selected.name]
                    and not any(isinstance(d, ast.Name) and d.id == "overload" for d in method.decorator_list)
                ]
        nodes.append(selected)
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)


def make_command_runtime(release):
    namespace = {
        "__name__": __name__,
        "inspect": inspect,
        "re": re,
        "types": types,
        "typing": typing,
        "enum": enum,
        "Enum": enum.Enum,
        "ABCMeta": ABCMeta,
        "abstractmethod": abstractmethod,
        "Callable": Callable,
        "dataclass": dataclass,
        "field": field,
        "Generic": Generic,
        "H": H,
        "HandlerFilter": object,
        "AstrBotConfig": dict,
        "AstrMessageEvent": object,
        "logger": logger_stub("astrbot-command-tests"),
        "star_map": {},
    }
    execute_nodes(
        release / "core/star/star_handler.py",
        namespace,
        {"EventType", "StarHandlerMetadata", "StarHandlerRegistry"},
        {"StarHandlerRegistry": {"__init__", "append", "get_handler_by_full_name", "get_handlers_by_event_type"}},
    )
    execute_nodes(
        release / "core/star/filter/custom_filter.py",
        namespace,
        {"CustomFilterMeta", "CustomFilter", "CustomFilterAnd", "CustomFilterOr"},
    )
    execute_nodes(release / "core/star/filter/command.py", namespace, {"GreedyStr", "unwrap_optional", "CommandFilter"})
    namespace["star_handlers_registry"] = namespace["StarHandlerRegistry"]()
    namespace["RegisteringCommandable"] = type("RegisteringCommandable", (), {})
    namespace["CommandGroupFilter"] = type("CommandGroupFilter", (), {})
    namespace["PermissionTypeFilter"] = type("PermissionTypeFilter", (), {})
    execute_nodes(
        release / "core/star/register/star_handler.py",
        namespace,
        {"get_handler_full_name", "get_handler_or_create", "register_command", "register_custom_filter"},
    )
    execute_nodes(release / "core/platform/message_type.py", namespace, {"MessageType"})
    namespace.update({"At": type("At", (), {}), "AtAll": type("AtAll", (), {}), "Reply": type("Reply", (), {})})

    class Sessions:
        @staticmethod
        async def filter_handlers_by_session(event, handlers):
            return handlers

    namespace["SessionPluginManager"] = Sessions
    execute_nodes(
        release / "core/pipeline/waking_check/stage.py",
        namespace,
        {"WakingCheckStage"},
        {"WakingCheckStage": {"process"}},
    )
    # Both release-specific native filters live on one handler; the decorators
    # use the real metadata factory and registry, not no-op test decorators.
    api_filter = types.SimpleNamespace(
        CustomFilter=namespace["CustomFilter"],
        custom_filter=namespace["register_custom_filter"],
        command=namespace["register_command"],
    )
    return types.SimpleNamespace(
        namespace=namespace,
        api_filter=api_filter,
        registry=namespace["star_handlers_registry"],
        command_filter=namespace["CommandFilter"],
    )


async def wake_event(runtime, event, wake_prefix=None):
    stage = runtime.namespace["WakingCheckStage"]()
    config = {
        "admins_id": ["admin"],
        "wake_prefix": ["/"] if wake_prefix is None else wake_prefix,
        "plugin_set": ["*"],
        "platform_settings": {"unique_session": False},
    }
    stage.ctx = types.SimpleNamespace(astrbot_config=config)
    stage.no_permission_reply = True
    stage.friend_message_needs_wake_prefix = False
    stage.ignore_bot_self_message = False
    stage.ignore_at_all = False
    stage.disable_builtin_commands = False
    stage.unique_session = False
    stage._umo_auto_name_recorder = types.SimpleNamespace(schedule=lambda _: None)
    await stage.process(event)
    return event.get_extra("activated_handlers", [])


class CommandEvent:
    def __init__(self, text, message_type, group="100", platform="bot", user="member"):
        self.message_str = text
        self.message_obj = types.SimpleNamespace(type=message_type, message_str=text)
        self.platform = platform
        self.group = group
        self.user = user
        self.role = "member"
        self.is_wake = False
        self.is_at_or_wake_command = False
        self._extras = {}
        self.stopped = False
        self.sent = []
        self.unified_msg_origin = f"{platform}:GroupMessage:{group}"
        self.plugins_name = None

    def is_private_chat(self):
        return not self.group

    def get_message_str(self):
        return self.message_str

    def get_platform_id(self):
        return self.platform

    def get_platform_name(self):
        return "aiocqhttp"

    def get_group_id(self):
        return self.group

    def get_sender_id(self):
        return self.user

    def get_sender_name(self):
        return self.user

    def get_self_id(self):
        return "bot-self"

    def get_messages(self):
        return [types.SimpleNamespace(text=self.message_str)]

    def is_admin(self):
        return self.role == "admin"

    def set_extra(self, key, value):
        self._extras[key] = value

    def get_extra(self, key=None, default=None):
        return self._extras if key is None else self._extras.get(key, default)

    def stop_event(self):
        self.stopped = True

    def plain_result(self, text):
        return text

    async def send(self, message):
        self.sent.append(message)
