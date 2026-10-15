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

PLUGIN_NAME = "astrbot_plugin_mc_manager"


class MCCommandFilter(filter.CustomFilter):
    settings: Settings | None = None

    def filter(self, event: AstrMessageEvent, cfg: AstrBotConfig) -> bool:
        return bool(self.settings and self.settings.extract(event.get_message_str()) is not None)


class MCManagerPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context, config)
        self.config = config
        self.settings = Settings(dict(config))
        self.service: Service | None = None
        self.guard = asyncio.Lock()
        self.config_revision = secrets.token_urlsafe(18)
        self.config_schema = json.loads((Path(__file__).parent / "_conf_schema.json").read_text(encoding="utf-8"))
        self.data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        MCCommandFilter.settings = self.settings

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
            if self.service is None:
                self.service = Service(self.settings, self.data_dir, self._send)
                await self.service.start()
                self._register_web_apis()
        self.logger.info("MC 管理插件已加载；群与服务器配置在插件配置页或管理面板中填写。")

    async def _send(self, origin: str, text: str) -> bool:
        try:
            chain = MessageChain().message(text)
            return bool(await self.context.send_message(origin, chain))
        except Exception:
            self.logger.exception("MC 重启广播发送失败，将在后续同步周期重试")
            return False

    def _service(self) -> Service:
        if self.service is None or self.service.closed:
            raise MCError("插件尚未初始化或已卸载。")
        return self.service

    @filter.custom_filter(MCCommandFilter)
    async def mc(self, event: AstrMessageEvent):
        text = self.settings.extract(event.get_message_str())
        if text is None:
            return
        event.stop_event()
        actor = Actor(
            str(event.get_platform_id() or ""),
            str(event.get_group_id() or ""),
            str(event.get_sender_id() or ""),
            event.get_sender_name(),
            bool(event.is_admin()),
            event.unified_msg_origin,
        )
        try:
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
                service = self._service()
                if request.query.get("probe", "") == "1":
                    await asyncio.gather(*(service.query(s, "status") for s in self.settings.servers))
                return json_response(service.snapshot())
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
                    group.platform, group.group_id, "dashboard:" + request.username, request.username, True, ""
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

    async def page_config(self):
        try:
            self._page_admin()
            async with self.guard:
                payload = copy.deepcopy(dict(self.config))
                for server in payload.get("servers", []):
                    server["rcon_password"] = "__KEEP__" if server.get("rcon_password") else ""
                return json_response(
                    {
                        "config": payload,
                        "data_directory": str(self.data_dir),
                        "tps_default_template": DEFAULT_TPS_TEMPLATE,
                        "tps_variables": variables_table(),
                        "schema": self.config_schema,
                        "platforms": self._platform_options(),
                        "revision": self.config_revision,
                        "bindings": {
                            "groups": [
                                dict(row)
                                for row in self._service().store.db.execute("SELECT id,platform,group_id FROM scopes")
                            ],
                            "servers": [
                                dict(row)
                                for row in self._service().store.db.execute("SELECT server,scope FROM bindings")
                            ],
                        },
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
                current = self._service()
                if "revision" in payload and payload["revision"] != self.config_revision:
                    raise MCError("配置已被其他页面修改，请先重新载入配置后再保存。")
                if current.restart_tasks:
                    raise MCError("有服务器正在重启，等待重启监控结束后再保存配置。")
                Settings(raw)
                old_servers = {s["id"]: s for s in self.config.get("servers", [])}
                for server in raw.get("servers", []):
                    if server.get("rcon_password") == "__KEEP__":
                        sid = server.get("id")
                        if sid not in old_servers:
                            raise MCError("新服务器需要填写密码或密码环境变量。")
                        server["rcon_password"] = old_servers[sid].get("rcon_password", "")
                settings = Settings(raw)
                candidate = Service(settings, self.data_dir, self._send, reserve_scopes=False)
                old_config = copy.deepcopy(dict(self.config))
                try:
                    self.config.clear()
                    self.config.update(raw)
                    await self._persist_config()
                    candidate.store.validate_scopes(settings)
                except BaseException:
                    self.config.clear()
                    self.config.update(old_config)
                    candidate.store.close()
                    try:
                        await self._persist_config()
                    except Exception:
                        self.logger.exception("MC 配置回滚保存失败，当前实例仍使用原配置")
                    raise
                await current.close()
                self.settings = settings
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
