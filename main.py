from __future__ import annotations

import asyncio
import copy
import json
import secrets
from pathlib import Path

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools
from astrbot.api.web import error_response, json_response, request

from .mc_manager.config import Actor, MCError, Settings, positive
from .mc_manager.service import Service
from .mc_manager.spark import DEFAULT_TPS_TEMPLATE, variables_table
from .mc_manager.store import Store

PLUGIN_NAME = "astrbot_plugin_mc_manager"


class MCCommandFilter(filter.CustomFilter):
    settings: Settings | None = None

    def filter(self, event: AstrMessageEvent, cfg: AstrBotConfig) -> bool:
        if self.settings is None:
            return False
        prefixes = {"mc", *(prefix.casefold() for prefix in self.settings.prefixes)}
        original = getattr(event.message_obj, "message_str", "")
        candidates = [event.get_message_str()]
        if isinstance(original, str) and event.is_at_or_wake_command:
            candidates.insert(0, original)
        words = []
        for candidate in candidates:
            parts = candidate.strip().split(maxsplit=1)
            if parts and parts[0].removeprefix("/").casefold() in prefixes:
                words = parts
                break
        if not words:
            return False
        text = words[1] if len(words) > 1 else ""
        event.set_extra("mc_manager_arguments", text)
        # This filter runs before the native CommandFilter. Normalize aliases
        # into the one registered command, and wake only an exact command token.
        event.message_str = "mc" + (" " + text if text else "")
        event.is_at_or_wake_command = True
        return True


class MCManagerPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context, config)
        self.config = config
        self.config_error = ""
        try:
            self.settings = Settings(dict(config))
        except MCError as exc:
            self.config_error = str(exc)
            self.settings = self._recovery_settings()
        self.service: Service | None = None
        self.guard = asyncio.Lock()
        self.config_revision = secrets.token_urlsafe(18)
        self.config_schema = json.loads((Path(__file__).parent / "_conf_schema.json").read_text(encoding="utf-8"))
        self.data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        MCCommandFilter.settings = self.settings

    def _recovery_settings(self) -> Settings:
        commands = self.config.get("commands", {})
        prefix = commands.get("prefix", "mc") if isinstance(commands, dict) else "mc"
        try:
            return Settings({"commands": {"prefix": prefix}})
        except MCError:
            return Settings({})

    def _register_web_apis(self):
        for route, handler, methods, desc in [
            ("snapshot", self.page_snapshot, ["GET"], "服务器与群绑定状态"),
            ("entries", self.page_entries, ["GET"], "白名单列表"),
            ("logs", self.page_logs, ["GET"], "审计日志"),
            ("action", self.page_action, ["POST"], "管理操作"),
            ("config", self.page_config, ["GET"], "读取配置（密码脱敏）"),
            ("config", self.page_save_config, ["POST"], "保存配置"),
        ]:
            self.context.register_web_api(f"/{PLUGIN_NAME}/{route}", handler, methods, desc)

    async def initialize(self):
        async with self.guard:
            # Configuration repair must stay reachable even when a partially
            # filled native template fails validation.
            self._register_web_apis()
            if self.service is None and not self.config_error:
                try:
                    self.service = Service(self.settings, self.data_dir, self._send)
                    await self.service.start()
                except MCError as exc:
                    if self.service is not None:
                        await self.service.close()
                        self.service = None
                    self.config_error = str(exc)
                    self.settings = self._recovery_settings()
                    MCCommandFilter.settings = self.settings
        if self.config_error:
            self.logger.warning("MC 配置待修正，管理页面可用于修复：%s", self.config_error)
        else:
            self.logger.info("MC 管理插件已加载；群与服务器配置在插件配置页或管理面板中填写。")

    async def _send(self, origin: str, text: str) -> bool:
        try:
            chain = MessageChain().message(text)
            return bool(await self.context.send_message(origin, chain))
        except Exception:
            self.logger.exception("MC 重启广播发送失败，将在后续同步周期重试")
            return False

    def _service(self) -> Service:
        if self.config_error:
            raise MCError("配置待修正：" + self.config_error + " 请打开管理面板修正后保存。")
        if self.service is None or self.service.closed:
            raise MCError("插件尚未初始化或已卸载。")
        return self.service

    def _verified_chat_admin(self, event: AstrMessageEvent, user: str) -> bool:
        # event.role is mutable shared state. Confirm elevated chat privileges
        # against the effective session configuration, never a truthy flag alone.
        if not user.strip() or event.is_admin() is not True:
            return False
        if event.get_extra("_api_key_allow_admin_role", default=None) is False:
            return False
        try:
            configured = self.context.get_config(event.unified_msg_origin).get("admins_id", [])
        except Exception:
            self.logger.warning("无法核对 AstrBot 管理员配置，本次不授予全局管理权限。")
            return False
        return isinstance(configured, list) and any(isinstance(value, str) and value == user for value in configured)

    @filter.command("mc")
    @filter.custom_filter(MCCommandFilter)
    async def mc(self, event: AstrMessageEvent):
        """Minecraft 管理：/mc [帮助/添加/查看/移除/玩家/tps/重启]，主指令别名可配置。"""
        text = event.get_extra("mc_manager_arguments", default=None)
        if text is None:
            words = event.get_message_str().strip().split(maxsplit=1)
            text = words[1] if len(words) > 1 else ""
        event.stop_event()
        try:
            user = str(event.get_sender_id() or "")
            actor = Actor(
                str(event.get_platform_id() or ""),
                str(event.get_group_id() or ""),
                user,
                event.get_sender_name(),
                self._verified_chat_admin(event, user),
                event.unified_msg_origin,
            )
            async with self.guard:
                action, args = self.settings.route(text)
                result = await self._service().execute(actor, action, args)
        except MCError as exc:
            result = self.settings.render("error", error=exc)
        except Exception:
            self.logger.exception("MC 指令处理失败")
            result = self.settings.render("error", error="内部错误，请管理员检查 AstrBot 日志和同步状态。")
        yield event.plain_result(result)

    def _page_admin(self):
        # Dashboard's plugin-extension routes require a logged-in dashboard
        # administrator. Require its public API identity as defense in depth.
        if not request.username:
            raise MCError("请先登录 AstrBot 管理后台。")

    def _group(self, group_id: str):
        group = self.settings.groups.get(group_id)
        if not group:
            raise MCError("请选择一个已配置的群。")
        return group

    async def page_snapshot(self):
        try:
            self._page_admin()
            async with self.guard:
                if self.config_error:
                    return json_response(
                        {
                            "groups": [],
                            "servers": [],
                            "jobs": [],
                            "notifications_pending": 0,
                            "config_error": self.config_error,
                            "configuration_required": True,
                        }
                    )
                service = self._service()
                if request.query.get("probe", "") == "1":
                    await service.probe_servers()
                return json_response({**service.snapshot(), "config_error": "", "configuration_required": False})
        except MCError as exc:
            return error_response(str(exc))

    async def page_entries(self):
        try:
            self._page_admin()
            async with self.guard:
                group = self._group(request.query.get("group", ""))
                service = self._service()
                async with service.locks[group.id]:
                    await service.ensure_group(group)
                    return json_response({"entries": service.store.entries(group.id)})
        except MCError as exc:
            return error_response(str(exc))

    async def page_logs(self):
        try:
            self._page_admin()
            async with self.guard:
                group = self._group(request.query.get("group", ""))
                page = positive(request.query.get("page", "1"), "页码")
                rows, total = self._service().store.logs(group.id, page, 20, request.query.get("user", ""))
                return json_response({"logs": rows, "total": total, "page": page})
        except MCError as exc:
            return error_response(str(exc))

    async def page_action(self):
        try:
            self._page_admin()
            payload = await request.json(default={})
            if not isinstance(payload, dict):
                raise MCError("请求必须是 JSON 对象。")
            action = payload.get("action")
            if action not in {"add", "remove", "modify", "sync", "import", "restart", "players", "tps", "status"}:
                raise MCError("不支持该操作。")
            args = payload.get("args", [])
            if not isinstance(args, list) or len(args) > 2 or any(not isinstance(a, str) or len(a) > 128 for a in args):
                raise MCError("参数格式无效。")
            async with self.guard:
                group = self._group(str(payload.get("group", "")))
                # The dashboard operator is distinct from every chat user.
                actor = Actor(
                    group.platform, group.group_ids[0], "dashboard:" + request.username, request.username, True, ""
                )
                result = await self._service().execute(actor, action, args)
                return json_response({"message": result})
        except MCError as exc:
            return error_response(str(exc))
        except Exception:
            self.logger.exception("MC 面板操作失败")
            return error_response("操作失败，请查看 AstrBot 日志和服务器状态。")

    def _platform_options(self) -> list[dict[str, str]]:
        manager = getattr(self.context, "platform_manager", None)
        platforms = manager.get_insts() if manager is not None else []
        options = []
        for platform in platforms:
            meta = platform.meta()
            options.append(
                {
                    "id": str(meta.id),
                    "name": str(meta.name),
                    "display_name": str(getattr(meta, "adapter_display_name", None) or meta.name),
                }
            )
        return options

    def _config_bindings(self) -> dict:
        store = self.service.store if self.service is not None else Store(self.data_dir)
        try:
            return {
                "groups": [dict(row) for row in store.db.execute("SELECT id,platform,group_id,active FROM scopes")],
                "servers": [dict(row) for row in store.db.execute("SELECT server,scope FROM bindings")],
            }
        finally:
            if self.service is None:
                store.close()

    @staticmethod
    def _redact_passwords(value):
        if isinstance(value, dict):
            return {
                key: ("__KEEP__" if item else "") if key == "rcon_password" else MCManagerPlugin._redact_passwords(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [MCManagerPlugin._redact_passwords(item) for item in value]
        return value

    async def page_config(self):
        try:
            self._page_admin()
            async with self.guard:
                payload = self._redact_passwords(copy.deepcopy(dict(self.config)))
                return json_response(
                    {
                        "config": payload,
                        "data_directory": str(self.data_dir),
                        "tps_default_template": DEFAULT_TPS_TEMPLATE,
                        "tps_variables": variables_table(),
                        "schema": self.config_schema,
                        "platforms": self._platform_options(),
                        "revision": self.config_revision,
                        "bindings": self._config_bindings(),
                        "config_error": self.config_error,
                    }
                )
        except MCError as exc:
            return error_response(str(exc))

    async def _persist_config(self):
        task = asyncio.create_task(self.config.save_config_async())
        try:
            committed = await asyncio.shield(task)
        except asyncio.CancelledError:
            # Finish the framework's disk write before rollback or unloading.
            await task
            raise
        if committed is False:
            raise MCError("配置保存被更新的配置覆盖，请重新载入后再试。")

    async def page_save_config(self):
        try:
            self._page_admin()
            payload = await request.json(default={})
            if not isinstance(payload, dict) or not isinstance(payload.get("config"), dict):
                raise MCError("请求需要 config 对象。")
            raw = payload["config"]
            async with self.guard:
                current = self.service
                if "revision" in payload and payload["revision"] != self.config_revision:
                    raise MCError("配置已被其他页面修改，请先重新载入配置后再保存。")
                if current is not None and current.restart_tasks:
                    raise MCError("有服务器正在重启，等待重启监控结束后再保存配置。")
                Settings(raw)
                previous_servers = self.config.get("servers", [])
                old_servers = (
                    {str(item["id"]): item for item in previous_servers if isinstance(item, dict) and "id" in item}
                    if isinstance(previous_servers, list)
                    else {}
                )
                for server in raw.get("servers", []):
                    if server.get("rcon_password") == "__KEEP__":
                        sid = server.get("id")
                        if sid not in old_servers:
                            raise MCError("新服务器需要填写密码或密码环境变量。")
                        server["rcon_password"] = old_servers[sid].get("rcon_password", "")
                histories = {row["id"]: row for row in self._config_bindings()["groups"]}
                for group in raw.get("groups", []):
                    old = histories.get(group["id"])
                    if old and (old["platform"], old["group_id"]) != (group["platform_id"], group["group_id"]):
                        group["broadcast_origin"] = ""
                settings = Settings(raw)
                candidate = Service(settings, self.data_dir, self._send, reserve_scopes=False)
                old_config = copy.deepcopy(dict(self.config))
                paused = False
                try:
                    self.config.clear()
                    self.config.update(raw)
                    await self._persist_config()
                    if current is not None:
                        paused = True
                        await current.pause()
                    operator = Actor("", "", "dashboard:" + request.username, request.username, True)
                    candidate.store.validate_scopes(settings, actor=operator)
                except BaseException:
                    self.config.clear()
                    self.config.update(old_config)
                    candidate.store.close()
                    if paused and current is not None:
                        current.resume()
                    try:
                        await self._persist_config()
                    except Exception:
                        self.logger.exception("MC 配置回滚保存失败，当前实例仍使用原配置")
                    raise
                if current is not None:
                    await current.close()
                self.settings = settings
                self.config_error = ""
                MCCommandFilter.settings = settings
                self.service = candidate
                self.config_revision = secrets.token_urlsafe(18)
                await candidate.start()
            return json_response({"message": "配置已保存并立即生效。"})
        except (MCError, TypeError, ValueError, KeyError) as exc:
            return error_response(str(exc))
        except Exception:
            self.logger.exception("MC 配置保存失败")
            return error_response("配置保存失败，请查看 AstrBot 日志。")

    async def terminate(self):
        async with self.guard:
            if self.service:
                await self.service.close()
                self.service = None
            if MCCommandFilter.settings is self.settings:
                MCCommandFilter.settings = None
            # Remove only bound handlers owned by this plugin instance.
            self.context.registered_web_apis[:] = [
                route for route in self.context.registered_web_apis if getattr(route[1], "__self__", None) is not self
            ]
