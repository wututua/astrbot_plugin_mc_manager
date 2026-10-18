from __future__ import annotations

import asyncio
import json
import math
import sqlite3
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .bridge import BridgeMixin
from .config import Actor, Group, MCError, Settings, player_name, positive, safe_text
from .files import (
    lookup_uuid,
    offline_uuid,
    read_initial_whitelist,
    read_whitelist,
    write_whitelist,
)
from .message_catalog import join_text
from .message_catalog import text as message_text
from .monitoring import QueryInfo, QueryResult, command_error, parse_players, query_values
from .operations import OperationsMixin
from .performance import parse_performance, performance_values
from .rcon import Rcon
from .spark import parse_spark_tps
from .spark_log import SPARK_COMMAND, SparkLogCapture, file_call
from .store import Store


class Service(BridgeMixin, OperationsMixin):
    def __init__(
        self,
        settings: Settings,
        data_dir: Path,
        send: Callable[[str, str], Awaitable[bool]],
        rcon_factory=Rcon,
        reserve_scopes: bool = True,
        *,
        logger,
    ):
        self.settings = settings
        # AstrBot Star owns the logger; services never create logging handlers.
        self.logger = logger
        self.store = Store(data_dir)
        try:
            self.store.validate_scopes(settings, reserve=reserve_scopes)
        except BaseException:
            self.store.close()
            raise
        self.send = send
        self.rcon_factory = rcon_factory
        self.locks = {gid: asyncio.Lock() for gid in settings.groups}
        self.server_locks = {sid: asyncio.Lock() for sid in settings.servers}
        self.restart_tasks: dict[str, asyncio.Task] = {}
        self.background = None
        self.delivery_lock = asyncio.Lock()
        self.initialization_lock = asyncio.Lock()
        self.last_status: dict[str, str] = {}
        self.closed = False
        try:
            self._init_operations(data_dir)
            self._init_bridge()
        except BaseException:
            self.store.close()
            raise

    async def start(self):
        # A plugin reload loses the old TCP connection. Report an interrupted
        # monitor truthfully instead of claiming the process restarted.
        for job in self.store.jobs():
            if job["status"] == "running":
                server = self.settings.servers.get(job["server"])
                name = server.name if server else job["server"]
                error = message_text("restart_interrupted")
                text = self.settings.render("restart_failed", server=name, error=error)
                self.store.finish_job(
                    job["server"],
                    "interrupted",
                    error,
                    job["origin"],
                    text,
                    recipients=self.server_recipients(job["server"], job["scope"], job["origin"]),
                )
        self.resume()

    def resume(self):
        if self.closed:
            return
        for sid in self.settings.servers:
            self.last_status.setdefault(sid, "探测中")
        workers = (
            [
                ("bridge_poll_task", self._bridge_poll_loop, "mc-bridge-events"),
                ("bridge_delivery_task", self._bridge_delivery_loop, "mc-bridge-delivery"),
            ]
            if self.settings.bridge_enabled and any(g.bridge_enabled for g in self.settings.groups.values())
            else []
        )
        for attribute, coroutine, name in (
            ("background", self._loop, "mc-probe-sync"),
            ("delivery_task", self._delivery_loop, "mc-notifications"),
            ("maintenance_task", self._maintenance_loop, "mc-maintenance"),
            *workers,
        ):
            task = getattr(self, attribute)
            if task is None or task.done():
                setattr(self, attribute, asyncio.create_task(coroutine(), name=name))

    async def pause(self):
        tasks = [
            task
            for task in (
                self.background,
                self.delivery_task,
                self.maintenance_task,
                self.bridge_poll_task,
                self.bridge_delivery_task,
            )
            if task is not None
        ] + list(self.query_inflight.values())
        for task in tasks:
            task.cancel()
        completion = asyncio.gather(*tasks, return_exceptions=True)
        try:
            await asyncio.shield(completion)
        except asyncio.CancelledError:
            await completion
            raise
        finally:
            self.background = self.delivery_task = self.maintenance_task = None
            self.bridge_poll_task = self.bridge_delivery_task = None
            self.query_inflight = {key: task for key, task in self.query_inflight.items() if not task.done()}
        for lock in self.locks.values():
            async with lock:
                pass

    def broadcast_origins(self, group: Group) -> dict[str, str]:
        recorded = self.store.origins(group.id)
        result = {identity: recorded[identity] for identity in group.group_ids if recorded.get(identity)}
        parts = group.origin.split(":", 2)
        if len(parts) == 3 and parts[0] == group.platform and parts[1] == "GroupMessage":
            for identity in group.group_ids:
                if parts[2] == identity or parts[2].endswith("_" + identity):
                    result.setdefault(identity, group.origin)
        return result

    def broadcast_origin(self, group: Group, actor: Actor) -> str:
        if actor.origin:
            return actor.origin
        origins = self.broadcast_origins(group)
        return origins.get(actor.group) or next(iter(origins.values()), "")

    def server_recipients(self, sid: str, scope: str = "", origin: str = "") -> list[tuple[str, str]]:
        result, sent = [], set()
        for group in self.settings.server_groups.get(sid, ()):
            targets = list(self.broadcast_origins(group).values())
            if group.id == scope and origin:
                targets.append(origin)
            for target in targets:
                if target not in sent:
                    result.append((group.id, target))
                    sent.add(target)
        return result

    async def probe_servers(self):
        # Probe every configured endpoint before whitelist work. A broken file
        # or an unbound server must not prevent status discovery at startup.
        await asyncio.gather(*(self.cached_query(sid, "status", fresh=True) for sid in self.settings.servers))

    async def _loop(self):
        await self._worker("sync", self._sync_cycle, lambda: self.settings.sync_interval)

    async def _sync_cycle(self):
        await self.probe_servers()
        for group in self.settings.groups.values():
            try:
                async with self.locks[group.id]:
                    await self.ensure_group(group)
                    await self.sync_group(group)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                for sid in group.servers:
                    self.store.sync_result(sid, safe_text(self.settings.localize(exc), 500))

    async def ensure_group(self, group: Group):
        # No group/server locks are acquired here. Every related initial import
        # completes before any shared server is overwritten by a partial union.
        async with self.initialization_lock:
            for related in self.settings.related_groups(group):
                if not self.store.initialized(related.id):
                    await self.import_group(related, initial=True)

    async def import_group(self, group: Group, initial: bool = False) -> int:
        # Validate every source before importing. Only initial import may skip
        # malformed content on a server that explicitly allows overwriting it.
        # Path guards and the backup-before-write rule remain mandatory.
        servers = self.store.initial_servers(group) if initial else list(group.servers)
        reader = read_initial_whitelist if initial else read_whitelist
        snapshots = await asyncio.gather(*(asyncio.to_thread(reader, self.settings.servers[s]) for s in servers))
        existing = self.store.entries(group.id)
        by_name = {r["name"].casefold(): r for r in existing}
        by_uuid = {r["uuid"]: r for r in existing}
        additions = []
        for sid, rows in zip(servers, snapshots):
            foreign = {
                (entry["name"].casefold(), entry["uuid"])
                for entry in self.store.server_entries(sid)
                if entry["scope"] != group.id
            }
            for row in rows:
                if (row["name"].casefold(), row["uuid"]) in foreign:
                    continue
                named = by_name.get(row["name"].casefold())
                identified = by_uuid.get(row["uuid"])
                if named and named["uuid"] != row["uuid"]:
                    raise MCError(message_text("error_service_007", value1=row["name"]))
                if identified and identified["name"].casefold() != row["name"].casefold():
                    raise MCError(message_text("error_service_008"))
                if not named:
                    additions.append(row)
                    by_name[row["name"].casefold()] = row
                    by_uuid[row["uuid"]] = row
        with self.store.transaction():
            for row in additions:
                self.store.validate_player(group, row["name"], row["uuid"])
                self.store.insert(group.id, row["name"], row["uuid"], "")
            self.store.db.execute("UPDATE scopes SET initialized=1 WHERE id=?", (group.id,))
            self.store.mark_dirty(group.servers)
            if initial:
                actor = Actor(group.platform, group.group_id, "system", "初始导入", True)
                self.store.audit(
                    group,
                    actor,
                    "initial_import",
                    "",
                    self.settings.render("audit_initial_import_1", count=len(additions)),
                )
        return len(additions)

    async def _query_command(self, client, server, command, kind):
        capture = None
        log_error = ""
        use_log = kind == "tps" and server.spark_log_fallback and SPARK_COMMAND.fullmatch(command.strip())
        if use_log:
            capture = SparkLogCapture(server)
            try:
                # Bookmark before dispatch; no history or previous snapshot.
                await file_call(capture.mark)
            except (OSError, MCError) as exc:
                log_error = (
                    exc if isinstance(exc, MCError) else message_text("error_unknown", error=safe_text(exc, 400))
                )
                capture = None
        # Network/auth errors propagate; old log output cannot mask them.
        response = await client.command(command)
        info = QueryInfo(source="RCON", command=command, rcon_online=True)
        if use_log and not command_error(response) and parse_spark_tps(response).status == "未解析":
            if capture is not None:
                try:
                    report = await capture.wait(server.spark_log_wait_seconds)
                    return QueryResult(
                        report,
                        QueryInfo(
                            source=message_text("source_log"),
                            command=command,
                            rcon_online=True,
                            warning=message_text("query_log_acquired"),
                        ),
                    )
                except (OSError, MCError) as exc:
                    log_error = (
                        exc if isinstance(exc, MCError) else message_text("error_unknown", error=safe_text(exc, 400))
                    )
            if log_error:
                info = QueryInfo(
                    source="RCON",
                    command=command,
                    rcon_online=True,
                    warning=message_text("query_log_warning", error=log_error),
                )
        return QueryResult(response, info)

    async def _query_rcon(self, sid: str, kind: str) -> str:
        server = self.settings.servers[sid]
        if sid in self.restart_tasks:
            return QueryResult(message_text("status_restarting"), QueryInfo(error=message_text("status_restarting")))
        probe_failed = False
        command = server.tps_command if kind == "tps" else server.players_command
        try:
            async with self.server_locks[sid]:
                async with self.query_slots:
                    async with self.rcon_factory(server, self.settings.rcon_timeout) as client:
                        if kind == "tps" and server.tps_auto_detect:
                            cached = self.tps_capabilities.get(sid)
                            order = [command, "tps", "forge tps", "tick query"]
                            if cached and cached[0] > time.monotonic() and cached[2] == server.tps_command:
                                order.insert(0, cached[1])
                            failures = []
                            for command in dict.fromkeys(order):
                                response = await self._query_command(client, server, command, kind)
                                if response.strip() and not command_error(response):
                                    self.tps_capabilities[sid] = (
                                        time.monotonic() + self.settings.tps_capability_cache_seconds,
                                        command,
                                        server.tps_command,
                                    )
                                    break
                                failures.append(
                                    message_text(
                                        "query_command_failure",
                                        query_command=command,
                                        error=safe_text(response, 1000).strip() or message_text("query_empty_short"),
                                    )
                                )
                            else:
                                self.tps_capabilities.pop(sid, None)
                                probe_failed = True
                                response = QueryResult(
                                    message_text("query_native_failure", entries=join_text("\n", failures)),
                                    QueryInfo(source="RCON", command=command, rcon_online=True),
                                )
                        else:
                            response = await self._query_command(client, server, command, kind)
            result = safe_text(response, None) if kind == "tps" else safe_text(response, 6000).strip()
            if isinstance(response, QueryResult) and hasattr(response.message, "localize"):
                result = response.message
            error = ""
            if not result.strip():
                result = message_text("query_empty")
                error = result
                if kind == "tps" and SPARK_COMMAND.fullmatch(command.strip()):
                    result = message_text("query_spark_empty", detail="")
                    if response.info.warning:
                        result += "\n" + response.info.warning
            elif command_error(result) or probe_failed:
                error = message_text("query_unsupported")
                if kind != "tps":
                    result = message_text("query_unsupported_result", error=error, result=result)
            # Reachability and command support are separate: an empty/invalid
            # list response is not a successful readiness check.
            self.last_status[sid] = "RCON 可达，查询指令异常" if error and kind in {"status", "players"} else "在线"
            if kind == "tps" and not parse_performance(result, command).recognized:
                error = error or message_text("query_unparsed")
            raw = result if kind in {"players", "status"} else ""
            warning = response.info.warning
            if kind in {"status", "players"} and not error and parse_players(raw)["players_online"] == "暂无数据":
                warning = message_text("query_players_unparsed")
            info = QueryInfo(
                source=response.info.source, command=command, error=error, rcon_online=True, raw=raw, warning=warning
            )
            return QueryResult(message_text("status_online") if kind == "status" and not error else result, info)
        except (OSError, TimeoutError, EOFError, MCError) as exc:
            error = (
                exc.message
                if isinstance(exc, MCError) and isinstance(exc.message, str) and hasattr(exc.message, "localize")
                else message_text("error_unknown", error=safe_text(str(exc) or type(exc).__name__, 300))
            )
            self.last_status[sid] = "离线或 RCON 不可用"
            self.query_cache.pop((sid, "players"), None)
            return QueryResult(
                message_text("query_offline", error=error),
                QueryInfo(source="RCON", command=command, error=error, rcon_online=False),
            )

    def format_query(self, sid, kind, result):
        raw = result.info.raw if isinstance(result, QueryResult) else str(result)
        info = result.info if isinstance(result, QueryResult) else QueryInfo()
        if kind == "status":
            status = result
        elif info.rcon_online is False:
            status = message_text("status_offline")
        elif info.error:
            status = message_text("status_invalid") if info.rcon_online else message_text("status_unknown")
        else:
            status = message_text("status_online") if info.rcon_online else message_text("unverified")
        return self.settings.render(
            kind,
            server=self.settings.servers[sid].name,
            server_id=sid,
            result=result,
            status=status,
            **parse_players(raw, self.settings.tps_missing_value),
            **query_values(result, self.settings.tps_missing_value),
        )

    def format_tps(self, sid: str, result: str) -> str:
        server = self.settings.servers[sid]
        if server.tps_output == "raw":
            return self.settings.render("raw_tps", result=result, server=server.name, server_id=sid, limit=None)
        command = result.info.command if isinstance(result, QueryResult) and result.info.command else server.tps_command
        metrics = parse_spark_tps(result, self.settings.tps_missing_value)
        report = parse_performance(result, command, self.settings.tps_missing_value)
        values = {
            **performance_values(report, result, self.settings.tps_missing_value),
            "server": server.name,
            "server_id": sid,
            "tps_command": command,
            "result": safe_text(result, None).strip() if report.recognized else result,
            "tps_raw": safe_text(result, None).strip() if report.recognized else result,
            "tps_parse_status": message_text(
                {"完整": "parse_full", "部分": "parse_partial", "未解析": "parse_failed"}.get(
                    metrics.status, "parse_complete"
                )
            )
            if report.format == "spark"
            else (message_text("parse_partial") if report.partial else message_text("parse_complete"))
            if report.recognized
            else message_text("parse_failed"),
            "tps_missing_fields": ", ".join(
                name
                for name in metrics.missing
                if name not in report.values or report.values[name] == self.settings.tps_missing_value
            ),
            "tps_warning": "",
        }
        if not report.recognized:
            return self.settings.render("tps_error", limit=None, **values)
        if metrics.missing:
            values["tps_warning"] = self.settings.render("tps_warning", **values)
        text = self.settings.render("tps", limit=None, **values)
        if (
            report.partial
            and "{tps_raw}" not in self.settings.messages["tps"]
            and "{result}" not in self.settings.messages["tps"]
        ):
            text = self.settings.render(
                "raw_tps_append", result=text, tps_raw=result, server=server.name, server_id=sid, limit=None
            )
        return text

    async def disk_write(self, server, entries):
        task = asyncio.create_task(asyncio.to_thread(write_whitelist, server, entries))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Atomic file replacement must finish before a new plugin instance
            # can begin writing to the same directory.
            await task
            raise

    async def sync_group(self, group: Group, force: bool = False, allow_restart: str = "") -> str:
        await self.ensure_group(group)
        messages = []
        for sid in group.servers:
            server = self.settings.servers[sid]
            if sid in self.restart_tasks and sid != allow_restart:
                self.store.sync_result(sid, self.settings.render("sync_restart_reason"))
                messages.append(self.settings.render("sync_restarting", server=server.name, server_id=sid))
                continue
            disk_ready = False
            try:
                # Hold only one server lock, then release it before moving to
                # another server. Never acquire another group's lock while here.
                async with self.server_locks[sid]:
                    entries = self.store.server_entries(sid)
                    revision = self.store.revision(sid)
                    changed = await self.disk_write(server, entries)
                    disk_ready = True
                    needs_reload = changed or sid in self.store.dirty() or force
                    if needs_reload:
                        async with self.rcon_factory(server, self.settings.rcon_timeout) as client:
                            response = safe_text(await client.command("whitelist reload"))
                            if any(
                                x in response.casefold()
                                for x in ("unknown", "incorrect", "未知", "failed", "exception")
                            ):
                                raise MCError(message_text("error_service_011", response=response))
                    self.store.sync_result(sid, "", revision=revision)
                messages.append(self.settings.render("sync_success", server=server.name, server_id=sid))
            except Exception as exc:
                reason = safe_text(self.settings.localize(exc) or type(exc).__name__, 300)
                self.store.sync_result(sid, reason)
                detail = (
                    self.settings.render("sync_disk_ready") if disk_ready else self.settings.render("sync_disk_failed")
                )
                messages.append(
                    self.settings.render("sync_pending", server=server.name, server_id=sid, error=reason, detail=detail)
                )
        return "\n".join(messages)

    def require_admin(self, actor: Actor, group: Group, permission: str = "manage"):
        allowed = (
            self.settings.can_manage(actor, group)
            if permission == "manage"
            else (self.settings.is_admin(actor, group) or actor.user in (group.restart_admins or ()))
        )
        if not allowed:
            raise MCError(message_text("error_admin_required"))

    def targets(self, group: Group, selector: str = "") -> list[str]:
        if selector in ("", "all", "全部"):
            return list(group.servers)
        matches = [s for s in group.servers if selector in (s, self.settings.servers[s].name)]
        if len(matches) != 1:
            raise MCError(message_text("error_server_target"))
        return matches

    def allowed_entry(self, actor: Actor, group: Group, target: str) -> dict:
        row = self.store.find(group.id, target)
        if (not row["owner"] or row["owner"] != actor.user) and not self.settings.can_manage(actor, group):
            raise MCError(message_text("error_entry_owner"))
        return row

    def paginate(self, page: int, total: int) -> int:
        pages = max(1, math.ceil(total / self.settings.page_size))
        if page > pages:
            raise MCError(message_text("error_service_001", pages=pages))
        return pages

    async def resolve_player(self, group: Group, name: str) -> tuple[str, str]:
        name = player_name(name)
        if self.settings.servers[group.servers[0]].online_mode:
            cached = self.uuid_cache.get(name.casefold())
            if cached and cached[0] > time.monotonic():
                return cached[1]
            result = await asyncio.to_thread(lookup_uuid, name)
            if len(self.uuid_cache) >= 4096:
                self.uuid_cache.clear()
            self.uuid_cache[name.casefold()] = (time.monotonic() + 3600, result)
            return result
        return name, offline_uuid(name)

    async def _execute_core(self, actor: Actor, action: str, args: list[str]) -> str:
        if self.closed:
            raise MCError(message_text("error_unloading"))
        group = self.settings.group_for(actor)
        if not isinstance(actor.user, str) or not actor.user.strip():
            raise MCError(message_text("error_service_002"))
        self.store.remember(group.id, actor.origin, actor.group)
        admin_actions = {"logs", "restart", "sync", "import"}
        if action in admin_actions:
            self.require_admin(actor, group, "restart" if action == "restart" else "manage")
        counts = {
            "help": (0, 1),
            "add": (1, 1),
            "remove": (0, 1),
            "modify": (1, 2),
            "list": (0, 1),
            "players": (0, 1),
            "tps": (0, 1),
            "status": (0, 1),
            "servers": (0, 0),
            "restart": (0, 1),
            "logs": (0, 2),
            "sync": (0, 0),
            "import": (0, 0),
        }
        if action not in counts or not counts[action][0] <= len(args) <= counts[action][1]:
            if action in {"add", "modify", "help"}:
                return self.settings.render("usage_" + action)
            raise MCError(message_text("error_command_arguments"))
        if action == "help":
            if not args:
                return self.settings.render("help")
            if args[0].casefold() in {"all", "全部", "完整", "admin", "管理员"}:
                return self.settings.render("help_full")
            return self.settings.render("usage_help")
        if action == "servers":
            entries = "\n".join(
                self.settings.render("server_entry", server_id=sid, server=self.settings.servers[sid].name)
                for sid in group.servers
            )
            return self.settings.render("servers", entries=entries)
        if action in {"players", "tps", "status"}:
            targets = self.targets(group, args[0] if args else "")
            values = await asyncio.gather(*(self.cached_query(sid, action) for sid in targets))
            return "\n".join(
                self.format_tps(s, v) if action == "tps" else self.format_query(s, action, v)
                for s, v in zip(targets, values)
            )
        if action == "restart":
            if not args and len(group.servers) != 1:
                return self.settings.render(
                    "choose_restart_server",
                    entries="\n".join(
                        self.settings.render("server_entry", server_id=sid, server=self.settings.servers[sid].name)
                        for sid in group.servers
                    ),
                )
            targets = self.targets(group, args[0] if args else group.servers[0])
            if any(not self.settings.can_restart(actor, group, sid) for sid in targets):
                raise MCError(message_text("error_service_004"))
            if any(s in self.restart_tasks for s in targets):
                raise MCError(message_text("error_restart_busy"))
            origin = self.broadcast_origin(group, actor)
            if not origin:
                raise MCError(message_text("error_service_005"))
            async with self.locks[group.id]:
                await self.ensure_group(group)
            if any(s in self.restart_tasks for s in targets):
                raise MCError(message_text("error_restart_busy"))
            with self.store.transaction():
                self.store.audit(
                    group,
                    actor,
                    "restart_requested",
                    ",".join(targets),
                    self.settings.render("audit_restart_requested_1"),
                )
            for sid in targets:
                self.store.job(sid, group.id, origin)
                self.restart_tasks[sid] = asyncio.create_task(
                    self._restart(group, sid, origin), name=f"mc-restart-{sid}"
                )
            return self.settings.render(
                "restart_started", servers="、".join(self.settings.servers[s].name for s in targets)
            )
        async with self.locks[group.id]:
            if action == "logs":
                page = positive(args[0], "页码") if args else 1
                rows, total = self.store.logs(group.id, page, self.settings.page_size, args[1] if len(args) > 1 else "")
                pages = self.paginate(page, total)
                lines = []
                try:
                    timezone = ZoneInfo("Asia/Shanghai")
                except ZoneInfoNotFoundError:
                    from datetime import timedelta
                    from datetime import timezone as fixed_timezone

                    timezone = fixed_timezone(timedelta(hours=8))
                for row in rows:
                    stamp = datetime.fromisoformat(row["time"]).astimezone(timezone).strftime("%Y-%m-%d %H:%M:%S")
                    lines.append(
                        self.settings.render(
                            "log_entry",
                            time=stamp,
                            action=message_text("action_" + row["action"])
                            if "action_" + row["action"] in self.settings.messages
                            else row["action"],
                            group=row["group_id"],
                            user=row["user"],
                            player=row["player"],
                            detail=self.settings.render("log_detail", detail=row["detail"]),
                        )
                    )
                return self.settings.render(
                    "logs",
                    page=page,
                    pages=pages,
                    total=total,
                    entries="\n".join(lines) or self.settings.render("empty_logs"),
                )
            await self.ensure_group(group)
            if (action == "remove" and not args) or (action == "modify" and len(args) == 1):
                owned = [row for row in self.store.entries(group.id) if row["owner"] == actor.user]
                if not owned:
                    return self.settings.render("own_entry_missing")
                if len(owned) != 1:
                    return self.settings.render(
                        "choose_own_entry",
                        entries="\n".join(
                            self.settings.render("entry", id=row["id"], player=row["name"], owner=row["owner"])
                            for row in owned
                        ),
                    )
                # Infer only this authenticated user's sole owned row; the
                # normal permission checks and conditional SQL still run.
                args = [str(owned[0]["id"]), *args]
            if action == "list":
                page = positive(args[0], "页码") if args else 1
                rows = self.store.entries(group.id)
                pages = self.paginate(page, len(rows))
                selected = rows[(page - 1) * self.settings.page_size : page * self.settings.page_size]
                lines = [
                    self.settings.render(
                        "entry",
                        id=r["id"],
                        player=r["name"],
                        owner=r["owner"] or message_text("owner_imported_managed"),
                    )
                    for r in selected
                ]
                return self.settings.render(
                    "list",
                    page=page,
                    pages=pages,
                    total=len(rows),
                    entries="\n".join(lines) or self.settings.render("empty_whitelist"),
                )
            if action == "import":
                count = await self.import_group(group)
                with self.store.transaction():
                    self.store.audit(group, actor, "import", "", self.settings.render("audit_import_1", count=count))
                result = await self.sync_group(group, force=True)
                return self.settings.render("import", count=count, sync=result)
            if action == "sync":
                result = await self.sync_group(group, force=True)
                with self.store.transaction():
                    self.store.audit(group, actor, "sync", "", result)
                return self.settings.render("sync", sync=result)
            values = {}
            if action == "add":
                owned = [r for r in self.store.entries(group.id) if r["owner"] == actor.user]
                if len(owned) >= group.max_entries and not self.settings.can_manage(actor, group):
                    raise MCError(message_text("error_service_009", max_entries=group.max_entries))
                name, identifier = await self.resolve_player(group, args[0])
                if group.approval_required and not self.settings.can_manage(actor, group):
                    return await self.submit_request(group, actor, "add", name, identifier)
                try:
                    with self.store.transaction():
                        self.store.validate_player(group, name, identifier)
                        index = self.store.insert(group.id, name, identifier, actor.user)
                        self.store.audit(
                            group,
                            actor,
                            "add",
                            name,
                            self.settings.render(
                                "audit_add_1", id=index, uuid=identifier, server_ids=",".join(group.servers)
                            ),
                        )
                        self.store.mark_dirty(group.servers)
                except sqlite3.IntegrityError:
                    raise MCError(message_text("error_service_010")) from None
                values = dict(id=index, player=name, user=actor.user)
            elif action in {"remove", "modify"}:
                row = self.allowed_entry(actor, group, args[0])
                values = dict(id=row["id"], player=row["name"])
                if action == "remove":
                    with self.store.transaction():
                        deleted = self.store.db.execute(
                            "DELETE FROM entries WHERE scope=? AND id=? AND (owner=? OR ?=1)",
                            (group.id, row["id"], actor.user, int(self.settings.can_manage(actor, group))),
                        )
                        if deleted.rowcount != 1:
                            raise MCError(message_text("error_service_012"))
                        self.store.audit(
                            group,
                            actor,
                            "remove",
                            row["name"],
                            self.settings.render(
                                "audit_remove_1", id=row["id"], owner=row["owner"] or message_text("owner_imported")
                            ),
                        )
                        self.store.mark_dirty(group.servers)
                else:
                    name, identifier = await self.resolve_player(group, args[1])
                    if group.approval_required and not self.settings.can_manage(actor, group):
                        return await self.submit_request(group, actor, "modify", name, identifier, row)
                    try:
                        with self.store.transaction():
                            self.store.validate_player(group, name, identifier, exclude_id=row["id"])
                            updated = self.store.db.execute(
                                "UPDATE entries SET name=?,uuid=? WHERE scope=? AND id=? AND (owner=? OR ?=1)",
                                (
                                    name,
                                    identifier,
                                    group.id,
                                    row["id"],
                                    actor.user,
                                    int(self.settings.can_manage(actor, group)),
                                ),
                            )
                            if updated.rowcount != 1:
                                raise MCError(message_text("error_service_012"))
                            self.store.audit(
                                group,
                                actor,
                                "remove",
                                row["name"],
                                self.settings.render(
                                    "audit_remove_2", id=row["id"], owner=row["owner"] or message_text("owner_imported")
                                ),
                            )
                            self.store.audit(
                                group,
                                actor,
                                "add",
                                name,
                                self.settings.render(
                                    "audit_add_2", id=row["id"], owner=row["owner"] or message_text("owner_imported")
                                ),
                            )
                            self.store.mark_dirty(group.servers)
                    except sqlite3.IntegrityError:
                        raise MCError(message_text("error_operations_026")) from None
                    values.update(old_player=row["name"], player=name)
            result = await self.sync_group(group)
            template = {"add": "added", "remove": "removed", "modify": "modified"}[action]
            return self.settings.render(template, **values, sync=result)

    async def readiness(self, client, server):
        result = safe_text(await client.command(server.players_command)).strip()
        if not result or any(x in result.casefold() for x in ("unknown", "incorrect", "未知", "不完整")):
            raise MCError(message_text("error_service_003"))
        return result

    async def _restart(self, group: Group, sid: str, origin: str):
        server = self.settings.servers[sid]
        start = time.monotonic()
        deadline = start + self.settings.restart_timeout
        status = "failed"
        detail = ""
        interrupted = False
        system = Actor(group.platform, group.group_id, "system", "重启监控", True)
        try:
            countdown = self.settings.restart_countdown
            if countdown:
                self.store.job_phase(sid, "countdown", f"重启倒计时 {countdown} 秒")
                for remaining in range(countdown, 0, -1):
                    self.store.job_phase(sid, "countdown", f"{remaining} 秒后发送 stop")
                    if remaining == countdown or remaining in {60, 30, 10, 5, 4, 3, 2, 1}:
                        announcement = self.settings.render("restart_countdown", server=server.name, seconds=remaining)
                        with self.store.transaction():
                            self.store.enqueue(self.server_recipients(sid, group.id, origin), announcement)
                            self.notification_wake.set()
                        if self.settings.game_broadcast:
                            try:
                                async with self.server_locks[sid]:
                                    async with self.rcon_factory(server, self.settings.rcon_timeout) as client:
                                        game_text = self.settings.render(
                                            "game_restart_countdown",
                                            result=announcement,
                                            server=server.name,
                                            server_id=sid,
                                            seconds=remaining,
                                        )
                                        if game_text.strip():
                                            await client.command(
                                                "tellraw @a " + json.dumps({"text": game_text}, ensure_ascii=False)
                                            )
                            except Exception:
                                pass
                    await asyncio.sleep(1)
            deadline = time.monotonic() + self.settings.restart_timeout
            self.store.job_phase(sid, "stopping", "准备发送 stop")
            self.invalidate_queries(sid)
            async with self.server_locks[sid]:
                async with self.rcon_factory(server, self.settings.rcon_timeout) as client:
                    await self.readiness(client, server)
                    await client.stop()
                    self.store.job_phase(sid, "waiting_stop", "已发送 stop，等待旧连接断开")
                    await client.wait_disconnect(max(0.01, deadline - time.monotonic()))
            self.store.job_phase(sid, "waiting_start", "旧连接已断开，等待新 RCON")
            consecutive = 0
            last_error = message_text("restart_waiting")
            while time.monotonic() < deadline:
                await asyncio.sleep(min(self.settings.poll_interval, max(0, deadline - time.monotonic())))
                try:
                    async with asyncio.timeout(max(0.01, deadline - time.monotonic())):
                        async with self.server_locks[sid]:
                            async with self.rcon_factory(server, self.settings.rcon_timeout) as probe:
                                await self.readiness(probe, server)
                    consecutive += 1
                    self.store.job_phase(sid, "checking", f"启动就绪检查 {consecutive}/2")
                    if consecutive >= 2:
                        status = "success"
                        break
                except Exception as exc:
                    consecutive = 0
                    last_error = safe_text(self.settings.localize(exc) or type(exc).__name__, 200)
            if status != "success":
                raise MCError(message_text("error_service_006", last_error=last_error))
            self.store.job_phase(sid, "syncing", "服务器已就绪，正在同步白名单")
            async with self.locks[group.id]:
                await self.ensure_group(group)
                await self.sync_group(group, force=True, allow_restart=sid)
            if sid in self.store.dirty():
                detail = self.settings.render("restart_pending_sync", error=self.store.dirty()[sid])
            if self.settings.game_broadcast:
                try:
                    game_text = self.settings.render("game_restart_success", server=server.name).replace("\n", " ")
                    if game_text:
                        command = "tellraw @a " + json.dumps({"text": game_text}, ensure_ascii=False)
                        async with self.server_locks[sid]:
                            async with self.rcon_factory(server, self.settings.rcon_timeout) as client:
                                await client.command(command)
                except Exception as exc:
                    detail += self.settings.render(
                        "restart_game_failed", error=safe_text(self.settings.localize(exc), 200)
                    )
            seconds = round(time.monotonic() - start, 1)
            text = self.settings.render("restart_success", server=server.name, seconds=seconds, detail=detail)
            self.last_status[sid] = "在线"
        except asyncio.CancelledError:
            if sid not in self.cancel_requested:
                interrupted = True
                raise
            status = "cancelled"
            detail = self.settings.render("restart_cancelled_detail")
            text = self.settings.render("restart_cancelled", server=server.name)
        except Exception as exc:
            status = "failed"
            detail = safe_text(self.settings.localize(exc) or type(exc).__name__, 500)
            if isinstance(exc, TimeoutError):
                detail = self.settings.render("restart_timeout")
            text = self.settings.render("restart_failed", server=server.name, error=detail)
        finally:
            self.invalidate_queries(sid)
            if interrupted and self.restart_tasks.get(sid) is asyncio.current_task():
                self.restart_tasks.pop(sid, None)
        try:
            while True:
                try:
                    with self.store.transaction():
                        self.store.finish_job(
                            sid, status, detail, origin, text, recipients=self.server_recipients(sid, group.id, origin)
                        )
                        self.store.audit(
                            group,
                            system,
                            "restart_" + status,
                            sid,
                            detail or self.settings.render("restart_ready_detail"),
                        )
                    break
                except Exception as exc:
                    self.worker_health["restart:" + sid] = {
                        "state": "retrying",
                        "last_success": "",
                        "last_error": safe_text(exc, 500),
                        "failures": 1,
                    }
                    await asyncio.sleep(self.settings.background_retry_seconds)
            if "restart:" + sid in self.worker_health:
                self.worker_health["restart:" + sid].update(
                    state="waiting", last_success=datetime.now().astimezone().isoformat(timespec="seconds"), failures=0
                )
        finally:
            if self.restart_tasks.get(sid) is asyncio.current_task():
                self.restart_tasks.pop(sid, None)
        self.notification_wake.set()
        if status != "cancelled":
            try:
                await self.deliver()
            except Exception as exc:
                self.worker_health["notifications"] = {
                    "state": "retrying",
                    "last_success": "",
                    "last_error": safe_text(exc, 500),
                    "failures": 1,
                }

    async def deliver(self):
        async with self.delivery_lock:
            slots = asyncio.Semaphore(4)

            async def send_one(item):
                if not item["text"].strip():
                    self.store.delivered(item["id"], True)
                    return
                if item.get("scope", "") not in self.settings.groups:
                    self.store.delivered(item["id"], True)
                    return
                try:
                    expired = (
                        time.time() - datetime.fromisoformat(item["created"]).timestamp()
                        >= self.settings.notification_max_age_hours * 3600
                    )
                except (ValueError, TypeError):
                    expired = False
                error = "通知已过期" if expired else "适配器返回失败或会话不可用"
                success = False
                if not expired:
                    try:
                        async with slots, asyncio.timeout(15):
                            success = await self.send(item["origin"], item["text"])
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        error = safe_text(str(exc) or type(exc).__name__, 500)
                self.store.delivered(
                    item["id"],
                    bool(success),
                    error=error,
                    retry_seconds=self.settings.notification_retry_seconds,
                    max_attempts=1 if expired else self.settings.notification_max_attempts,
                    max_age_hours=self.settings.notification_max_age_hours,
                )

            results = await asyncio.gather(*(send_one(item) for item in self.store.outbox()), return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    raise result

    def snapshot(self) -> dict:
        dirty = self.store.dirty()
        return {
            "groups": [
                {
                    "id": g.id,
                    "platform_id": g.platform,
                    "group_id": g.group_id,
                    "group_ids": list(g.group_ids),
                    "broadcast_group_ids": list(self.broadcast_origins(g)),
                    "server_ids": list(g.servers),
                    "entries": len(self.store.entries(g.id)),
                    "broadcast_ready": bool(self.broadcast_origin(g, Actor("", "", ""))),
                }
                for g in self.settings.groups.values()
            ],
            "servers": [
                {
                    "id": s.id,
                    "name": s.name,
                    "status": "重启中" if s.id in self.restart_tasks else self.last_status.get(s.id, "尚未探测"),
                    "pending": dirty.get(s.id, ""),
                    "directory": str(s.directory),
                    **self.server_health.get(s.id, {}),
                }
                for s in self.settings.servers.values()
            ],
            "jobs": self.store.jobs(),
            "notifications_pending": self.store.db.execute(
                "SELECT COUNT(*) FROM outbox WHERE state='pending'"
            ).fetchone()[0],
            "notifications_failed": self.store.db.execute(
                "SELECT COUNT(*) FROM outbox WHERE state='failed'"
            ).fetchone()[0],
            "workers": self.worker_health,
        }

    async def close(self):
        self.closed = True
        tasks = (
            [
                task
                for task in (
                    self.background,
                    self.delivery_task,
                    self.maintenance_task,
                    self.bridge_poll_task,
                    self.bridge_delivery_task,
                )
                if task is not None
            ]
            + list(self.restart_tasks.values())
            + list(self.query_inflight.values())
        )
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # Wait for any command that already owns a group lock, so the database
        # is never closed during an in-flight whitelist transaction.
        for lock in self.locks.values():
            async with lock:
                pass
        self.store.close()
