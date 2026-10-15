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

from .config import Actor, Group, MCError, Settings, player_name, positive, safe_text
from .files import lookup_uuid, offline_uuid, read_spark_report, read_whitelist, write_whitelist
from .rcon import Rcon
from .spark import parse_spark_tps
from .store import Store


class Service:
    def __init__(
        self,
        settings: Settings,
        data_dir: Path,
        send: Callable[[str, str], Awaitable[bool]],
        rcon_factory=Rcon,
        reserve_scopes: bool = True,
    ):
        self.settings = settings
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
        self.last_status: dict[str, str] = {}
        self.closed = False

    async def start(self):
        # A plugin reload loses the old TCP connection. Report an interrupted
        # monitor truthfully instead of claiming the process restarted.
        for job in self.store.jobs():
            if job["status"] == "running":
                server = self.settings.servers.get(job["server"])
                name = server.name if server else job["server"]
                error = "插件监控曾中断，无法确认重启结果；请查询服务器状态。"
                text = self.settings.render("restart_failed", server=name, error=error)
                self.store.finish_job(job["server"], "interrupted", error, job["origin"], text)
        self.resume()

    def resume(self):
        if not self.closed and (self.background is None or self.background.done()):
            for sid in self.settings.servers:
                self.last_status.setdefault(sid, "探测中")
            self.background = asyncio.create_task(self._loop(), name="mc-probe-sync-and-notify")

    async def pause(self):
        task = self.background
        if task is not None:
            task.cancel()
            completion = asyncio.gather(task, return_exceptions=True)
            try:
                await asyncio.shield(completion)
            except asyncio.CancelledError:
                await completion
                self.background = None
                raise
            self.background = None
        for lock in self.locks.values():
            async with lock:
                pass

    def broadcast_origin(self, group: Group, actor: Actor) -> str:
        if actor.origin:
            return actor.origin
        recorded = self.store.origin(group.id)
        if recorded:
            return recorded
        parts = group.origin.split(":", 2)
        if (
            len(parts) == 3
            and parts[0] == group.platform
            and parts[1] == "GroupMessage"
            and (parts[2] == group.group_id or parts[2].endswith("_" + group.group_id))
        ):
            return group.origin
        return ""

    async def probe_servers(self):
        # Probe every configured endpoint before whitelist work. A broken file
        # or an unbound server must not prevent status discovery at startup.
        await asyncio.gather(*(self.query(sid, "status") for sid in self.settings.servers))

    async def _loop(self):
        while True:
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
                        self.store.sync_result(sid, safe_text(exc, 500))
            await self.deliver()
            await asyncio.sleep(self.settings.sync_interval)

    async def ensure_group(self, group: Group):
        if not self.store.initialized(group.id):
            await self.import_group(group, initial=True)

    async def import_group(self, group: Group, initial: bool = False) -> int:
        # Read all files first. One corrupt/missing directory prevents a partial
        # first import from becoming an authoritative whitelist.
        snapshots = await asyncio.gather(
            *(asyncio.to_thread(read_whitelist, self.settings.servers[s]) for s in group.servers)
        )
        existing = self.store.entries(group.id)
        by_name = {r["name"].casefold(): r for r in existing}
        by_uuid = {r["uuid"]: r for r in existing}
        additions = []
        for rows in snapshots:
            for row in rows:
                named = by_name.get(row["name"].casefold())
                identified = by_uuid.get(row["uuid"])
                if named and named["uuid"] != row["uuid"]:
                    raise MCError(f"玩家 {row['name']} 在各服使用不同 UUID，导入已停止。")
                if identified and identified["name"].casefold() != row["name"].casefold():
                    raise MCError("同一 UUID 对应多个玩家名，导入已停止。")
                if not named:
                    additions.append(row)
                    by_name[row["name"].casefold()] = row
                    by_uuid[row["uuid"]] = row
        with self.store.transaction():
            for row in additions:
                self.store.insert(group.id, row["name"], row["uuid"], "")
            self.store.db.execute("UPDATE scopes SET initialized=1 WHERE id=?", (group.id,))
            self.store.mark_dirty(group.servers)
            if initial:
                actor = Actor(group.platform, group.group_id, "system", "初始导入", True)
                self.store.audit(group, actor, "initial_import", "", f"保留合并 {len(additions)} 条原有白名单")
        return len(additions)

    async def query(self, sid: str, kind: str) -> str:
        server = self.settings.servers[sid]
        if sid in self.restart_tasks:
            return "重启监控中"
        file_error = ""
        if kind == "tps" and server.tps_source in {"auto", "file"}:
            try:
                report = await asyncio.to_thread(read_spark_report, server, self.settings.spark_file_max_age)
                if parse_spark_tps(report).status == "未解析":
                    raise MCError("本地 spark 指标尚不可用，请检查 spark 是否启用及采集器日志。")
                return safe_text(report, 6000).strip()
            except MCError as exc:
                file_error = str(exc)
                if server.tps_source == "file":
                    return file_error
        command = server.tps_command if kind == "tps" else server.players_command
        try:
            async with self.server_locks[sid]:
                async with self.rcon_factory(server, self.settings.rcon_timeout) as client:
                    result = safe_text(await client.command(command), 6000).strip()
            if not result:
                result = "服务端返回空响应"
                if kind == "tps" and "spark" in command.lower():
                    result += "；spark 异步命令可能无法通过 RCON 回复，请按 TPS 文档启用附带的 JVM 采集器。"
                    if file_error:
                        result += "\n" + file_error
            if any(
                x in result.casefold()
                for x in ("unknown command", "unknown or incomplete", "incorrect argument", "未知", "不完整")
            ):
                result = "服务端不支持该命令，请检查此服的查询命令配置：" + result
            self.last_status[sid] = "在线"
            return "在线（RCON 查询成功）" if kind == "status" else result
        except Exception as exc:
            self.last_status[sid] = "离线或 RCON 不可用"
            return "离线或 RCON 不可用：" + safe_text(str(exc) or type(exc).__name__, 300)

    def format_tps(self, sid: str, result: str) -> str:
        server = self.settings.servers[sid]
        metrics = parse_spark_tps(result, self.settings.tps_missing_value)
        values = {
            **metrics.values,
            "server": server.name,
            "server_id": sid,
            "tps_command": server.tps_command,
            "result": metrics.raw,
            "tps_raw": metrics.raw,
            "tps_parse_status": metrics.status,
            "tps_missing_fields": ", ".join(metrics.missing),
            "tps_warning": "",
        }
        if metrics.status == "未解析":
            return self.settings.render("tps_error", **values)
        if metrics.missing:
            values["tps_warning"] = self.settings.render("tps_warning", **values)
        return self.settings.render("tps", **values)

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
        entries = self.store.entries(group.id)
        messages = []
        for sid in group.servers:
            server = self.settings.servers[sid]
            if sid in self.restart_tasks and sid != allow_restart:
                self.store.sync_result(sid, "重启中，完成后自动同步")
                messages.append(f"{server.name}：重启中，待同步")
                continue
            try:
                async with self.server_locks[sid]:
                    changed = await self.disk_write(server, entries)
                    needs_reload = changed or sid in self.store.dirty() or force
                    if needs_reload:
                        async with self.rcon_factory(server, self.settings.rcon_timeout) as client:
                            response = safe_text(await client.command("whitelist reload"))
                            if any(
                                x in response.casefold()
                                for x in ("unknown", "incorrect", "未知", "failed", "exception")
                            ):
                                raise MCError("白名单重载失败：" + response)
                self.store.sync_result(sid, "")
                messages.append(f"{server.name}：已同步")
            except Exception as exc:
                reason = safe_text(str(exc) or type(exc).__name__, 300)
                self.store.sync_result(sid, reason)
                messages.append(f"{server.name}：待同步（{reason}；文件成功写入时，下次启动将生效）")
        return "\n".join(messages)

    def require_admin(self, actor: Actor, group: Group):
        if not self.settings.is_admin(actor, group):
            raise MCError("该功能仅允许 AstrBot 管理员或本群配置的管理员使用。")

    def targets(self, group: Group, selector: str = "") -> list[str]:
        if selector in ("", "all", "全部"):
            return list(group.servers)
        matches = [s for s in group.servers if selector in (s, self.settings.servers[s].name)]
        if len(matches) != 1:
            raise MCError("服务器不存在、名称不唯一或不属于本群，请使用服务器 ID。")
        return matches

    def allowed_entry(self, actor: Actor, group: Group, target: str) -> dict:
        row = self.store.find(group.id, target)
        if (not row["owner"] or row["owner"] != actor.user) and not self.settings.is_admin(actor, group):
            raise MCError("只能修改或移除自己的白名单；历史导入条目需管理员处理。")
        return row

    def paginate(self, page: int, total: int) -> int:
        pages = max(1, math.ceil(total / self.settings.page_size))
        if page > pages:
            raise MCError(f"页码超出范围，目前共 {pages} 页。")
        return pages

    async def resolve_player(self, group: Group, name: str) -> tuple[str, str]:
        name = player_name(name)
        if self.settings.servers[group.servers[0]].online_mode:
            return await asyncio.to_thread(lookup_uuid, name)
        return name, offline_uuid(name)

    async def execute(self, actor: Actor, action: str, args: list[str]) -> str:
        if self.closed:
            raise MCError("插件正在卸载。")
        group = self.settings.group_for(actor)
        if not isinstance(actor.user, str) or not actor.user.strip():
            raise MCError("无法确认发送者身份，请使用可识别账号的群聊消息重试。")
        self.store.remember(group.id, actor.origin)
        admin_actions = {"logs", "restart", "sync", "import"}
        if action in admin_actions:
            self.require_admin(actor, group)
        counts = {
            "help": (0, 0),
            "add": (1, 1),
            "remove": (1, 1),
            "modify": (2, 2),
            "list": (0, 1),
            "players": (0, 1),
            "tps": (0, 1),
            "status": (0, 1),
            "servers": (0, 0),
            "restart": (1, 1),
            "logs": (0, 2),
            "sync": (0, 0),
            "import": (0, 0),
        }
        if action not in counts or not counts[action][0] <= len(args) <= counts[action][1]:
            raise MCError("指令参数数量不正确，请发送主指令查看帮助。")
        if action == "help":
            return self.settings.render("help")
        if action == "servers":
            entries = "\n".join(f"{sid} · {self.settings.servers[sid].name}" for sid in group.servers)
            return self.settings.render("servers", entries=entries)
        if action in {"players", "tps", "status"}:
            targets = self.targets(group, args[0] if args else "")
            values = await asyncio.gather(*(self.query(sid, action) for sid in targets))
            return "\n".join(
                self.format_tps(s, v)
                if action == "tps"
                else self.settings.render(action, server=self.settings.servers[s].name, result=v)
                for s, v in zip(targets, values)
            )
        if action == "restart":
            targets = self.targets(group, args[0])
            if any(s in self.restart_tasks for s in targets):
                raise MCError("目标服务器已有重启任务，请等待当前任务结束。")
            origin = self.broadcast_origin(group, actor)
            if not origin:
                raise MCError("尚无本群广播会话；请先在群内使用 MC 指令，或配置 broadcast_origin。")
            async with self.locks[group.id]:
                await self.ensure_group(group)
            if any(s in self.restart_tasks for s in targets):
                raise MCError("目标服务器已有重启任务，请等待当前任务结束。")
            with self.store.transaction():
                self.store.audit(group, actor, "restart_requested", ",".join(targets), "发送 stop，等待启动脚本重启")
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
                            action=row["action"],
                            group=row["group_id"],
                            user=row["user"],
                            player=row["player"],
                            detail=row["detail"],
                        )
                    )
                return self.settings.render(
                    "logs", page=page, pages=pages, total=total, entries="\n".join(lines) or "暂无日志"
                )
            await self.ensure_group(group)
            if action == "list":
                page = positive(args[0], "页码") if args else 1
                rows = self.store.entries(group.id)
                pages = self.paginate(page, len(rows))
                selected = rows[(page - 1) * self.settings.page_size : page * self.settings.page_size]
                lines = [
                    self.settings.render(
                        "entry", id=r["id"], player=r["name"], owner=r["owner"] or "历史导入（管理员管理）"
                    )
                    for r in selected
                ]
                return self.settings.render(
                    "list", page=page, pages=pages, total=len(rows), entries="\n".join(lines) or "暂无白名单"
                )
            if action == "import":
                count = await self.import_group(group)
                with self.store.transaction():
                    self.store.audit(group, actor, "import", "", f"合并导入 {count} 条")
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
                if len(owned) >= group.max_entries and not self.settings.is_admin(actor, group):
                    raise MCError(f"每人最多添加 {group.max_entries} 条，请先修改或移除已有条目。")
                name, identifier = await self.resolve_player(group, args[0])
                try:
                    with self.store.transaction():
                        index = self.store.insert(group.id, name, identifier, actor.user)
                        self.store.audit(
                            group, actor, "add", name, f"#{index}; UUID={identifier}; 同步到 {','.join(group.servers)}"
                        )
                        self.store.mark_dirty(group.servers)
                except sqlite3.IntegrityError:
                    raise MCError("该玩家名或 UUID 已在本群白名单中。") from None
                values = dict(id=index, player=name, user=actor.user)
            elif action in {"remove", "modify"}:
                row = self.allowed_entry(actor, group, args[0])
                values = dict(id=row["id"], player=row["name"])
                if action == "remove":
                    with self.store.transaction():
                        deleted = self.store.db.execute(
                            "DELETE FROM entries WHERE scope=? AND id=? AND (owner=? OR ?=1)",
                            (group.id, row["id"], actor.user, int(self.settings.is_admin(actor, group))),
                        )
                        if deleted.rowcount != 1:
                            raise MCError("条目归属已变化或权限不足，请重新查看白名单。")
                        self.store.audit(
                            group, actor, "remove", row["name"], f"#{row['id']}; 原归属={row['owner'] or '历史导入'}"
                        )
                        self.store.mark_dirty(group.servers)
                else:
                    name, identifier = await self.resolve_player(group, args[1])
                    try:
                        with self.store.transaction():
                            updated = self.store.db.execute(
                                "UPDATE entries SET name=?,uuid=? WHERE scope=? AND id=? AND (owner=? OR ?=1)",
                                (name, identifier, group.id, row["id"], actor.user, int(self.settings.is_admin(actor, group))),
                            )
                            if updated.rowcount != 1:
                                raise MCError("条目归属已变化或权限不足，请重新查看白名单。")
                            self.store.audit(
                                group,
                                actor,
                                "remove",
                                row["name"],
                                f"修改 #{row['id']}，原归属={row['owner'] or '历史导入'}",
                            )
                            self.store.audit(
                                group, actor, "add", name, f"修改 #{row['id']}，归属={row['owner'] or '历史导入'}"
                            )
                            self.store.mark_dirty(group.servers)
                    except sqlite3.IntegrityError:
                        raise MCError("新的玩家名或 UUID 已存在。") from None
                    values.update(old_player=row["name"], player=name)
            result = await self.sync_group(group)
            template = {"add": "added", "remove": "removed", "modify": "modified"}[action]
            return self.settings.render(template, **values, sync=result)

    async def readiness(self, client, server):
        result = safe_text(await client.command(server.players_command)).strip()
        if not result or any(x in result.casefold() for x in ("unknown", "incorrect", "未知", "不完整")):
            raise MCError("启动就绪查询没有有效响应，请检查 players_command 配置。")
        return result

    async def _restart(self, group: Group, sid: str, origin: str):
        server = self.settings.servers[sid]
        start = time.monotonic()
        deadline = start + self.settings.restart_timeout
        status = "failed"
        detail = ""
        system = Actor(group.platform, group.group_id, "system", "重启监控", True)
        try:
            async with self.server_locks[sid]:
                async with self.rcon_factory(server, self.settings.rcon_timeout) as client:
                    await self.readiness(client, server)
                    await client.stop()
                    await client.wait_disconnect(max(0.01, deadline - time.monotonic()))
            consecutive = 0
            last_error = "旧连接已断开，新服务器尚未就绪。"
            while time.monotonic() < deadline:
                await asyncio.sleep(min(self.settings.poll_interval, max(0, deadline - time.monotonic())))
                try:
                    async with asyncio.timeout(max(0.01, deadline - time.monotonic())):
                        async with self.server_locks[sid]:
                            async with self.rcon_factory(server, self.settings.rcon_timeout) as probe:
                                await self.readiness(probe, server)
                    consecutive += 1
                    if consecutive >= 2:
                        status = "success"
                        break
                except Exception as exc:
                    consecutive = 0
                    last_error = safe_text(str(exc) or type(exc).__name__, 200)
            if status != "success":
                raise MCError("等待服务器启动超时：" + last_error)
            async with self.locks[group.id]:
                await self.ensure_group(group)
                await self.sync_group(group, force=True, allow_restart=sid)
            if sid in self.store.dirty():
                detail = " 白名单待同步：" + self.store.dirty()[sid]
            if self.settings.game_broadcast:
                try:
                    game_text = self.settings.render("game_restart_success", server=server.name).replace("\n", " ")
                    if game_text:
                        command = "tellraw @a " + json.dumps({"text": game_text}, ensure_ascii=False)
                        async with self.server_locks[sid]:
                            async with self.rcon_factory(server, self.settings.rcon_timeout) as client:
                                await client.command(command)
                except Exception as exc:
                    detail += " 游戏内广播失败：" + safe_text(exc, 200)
            seconds = round(time.monotonic() - start, 1)
            text = self.settings.render("restart_success", server=server.name, seconds=seconds, detail=detail)
            self.last_status[sid] = "在线"
        except asyncio.CancelledError:
            # Persisted 'running' jobs are reported as interrupted on reload.
            raise
        except Exception as exc:
            status = "failed"
            detail = safe_text(str(exc) or type(exc).__name__, 500)
            if isinstance(exc, TimeoutError):
                detail = "重启超时：旧连接未断开或新服务器未就绪。"
            text = self.settings.render("restart_failed", server=server.name, error=detail)
        finally:
            current = self.restart_tasks.get(sid)
            if current is asyncio.current_task():
                self.restart_tasks.pop(sid, None)
        self.store.finish_job(sid, status, detail, origin, text)
        with self.store.transaction():
            self.store.audit(group, system, "restart_" + status, sid, detail or "新 RCON 连续两次可执行查询")
        await self.deliver()

    async def deliver(self):
        async with self.delivery_lock:
            for item in self.store.outbox():
                if item.get("scope", "") not in self.settings.groups:
                    self.store.delivered(item["id"], True)
                    continue
                try:
                    async with asyncio.timeout(15):
                        success = await self.send(item["origin"], item["text"])
                except asyncio.CancelledError:
                    raise
                except Exception:
                    success = False
                self.store.delivered(item["id"], bool(success))

    def snapshot(self) -> dict:
        dirty = self.store.dirty()
        return {
            "groups": [
                {
                    "id": g.id,
                    "platform_id": g.platform,
                    "group_id": g.group_id,
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
                }
                for s in self.settings.servers.values()
            ],
            "jobs": self.store.jobs(),
            "notifications_pending": self.store.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0],
        }

    async def close(self):
        self.closed = True
        tasks = ([self.background] if self.background else []) + list(self.restart_tasks.values())
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
