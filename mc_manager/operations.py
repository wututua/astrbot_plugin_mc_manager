"""Operational features shared by chat commands and the authenticated dashboard."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import secrets
import shutil
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .backups import BackupManager
from .config import Actor, MCError, player_name, positive
from .files import read_spark_report, read_whitelist
from .spark import parse_spark_tps
from .text import safe_text

logger = logging.getLogger(__name__)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def path_health(directory: Path, writable: bool = False) -> dict:
    result = {"path": str(directory), "exists": directory.is_dir(), "writable": None, "free_bytes": None, "error": ""}
    try:
        probe = directory
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        result["free_bytes"] = shutil.disk_usage(probe).free
        if writable and result["exists"]:
            fd, name = tempfile.mkstemp(prefix=".mc-diagnostic-", dir=directory)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(b"MC diagnostic\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                result["writable"] = True
            finally:
                Path(name).unlink(missing_ok=True)
    except OSError as exc:
        result["error"] = safe_text(exc, 300)
        if writable:
            result["writable"] = False
    return result


class OperationsMixin:
    def _init_operations(self, data_dir: Path):
        self.data_dir = data_dir
        self.backups = BackupManager(data_dir, self.settings.backup_keep)
        self.worker_health = {}
        self.server_health = {}
        self.delivery_task = None
        self.notification_wake = asyncio.Event()
        self.maintenance_task = None
        self.query_slots = asyncio.Semaphore(self.settings.query_concurrency)
        self.query_cache = {}
        self.query_inflight = {}
        self.user_queries = {}
        self.group_queries = {}
        self.bulk_previews = {}
        self.cleanup_previews = {}
        self.cancel_requested = set()
        self.uuid_cache = {}
        self._last_maintenance = 0

    async def _worker(self, name, action, interval):
        failures = 0
        while True:
            health = self.worker_health.setdefault(
                name, {"state": "starting", "last_success": "", "last_error": "", "failures": 0}
            )
            health["state"] = "running"
            try:
                await action()
                failures = 0
                health.update(state="waiting", last_success=utc_now(), failures=0)
                delay = interval()
            except asyncio.CancelledError:
                health["state"] = "paused"
                raise
            except Exception as exc:
                failures += 1
                delay = min(300, self.settings.background_retry_seconds * 2 ** min(failures - 1, 6))
                health.update(
                    state="retrying", last_error=safe_text(str(exc) or type(exc).__name__, 500), failures=failures
                )
                logger.exception("MC %s 后台任务失败，%s 秒后重试", name, delay)
            if name == "notifications" and not failures:
                try:
                    async with asyncio.timeout(delay):
                        await self.notification_wake.wait()
                except TimeoutError:
                    pass
                finally:
                    self.notification_wake.clear()
            else:
                await asyncio.sleep(delay)

    async def _delivery_loop(self):
        await self._worker("notifications", self.deliver, lambda: max(0.05, min(5, self.settings.sync_interval)))

    async def _maintenance_loop(self):
        await self._worker("maintenance", self.maintenance, lambda: max(1, min(60, self.settings.sync_interval)))

    async def query(self, sid: str, kind: str) -> str:
        started = time.monotonic()
        result = await self._query_rcon(sid, kind)
        health = self.server_health.setdefault(sid, {})
        health[kind + "_checked"] = utc_now()
        health[kind + "_latency_ms"] = round((time.monotonic() - started) * 1000, 1)
        return result

    async def cached_query(self, sid: str, kind: str) -> str:
        key = (sid, kind)
        cached = self.query_cache.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        task = self.query_inflight.get(key)
        if task is None or task.done():

            async def run():
                try:
                    value = await self.query(sid, kind)
                    ttl = self.settings.query_cache_seconds
                    if value.startswith("离线或 RCON"):
                        ttl = min(ttl, 1)
                    if ttl:
                        self.query_cache[key] = (time.monotonic() + ttl, value)
                    return value
                finally:
                    if self.query_inflight.get(key) is asyncio.current_task():
                        self.query_inflight.pop(key, None)

            task = asyncio.create_task(run(), name=f"mc-query-{sid}-{kind}")
            self.query_inflight[key] = task
        return await asyncio.shield(task)

    def invalidate_queries(self, sid: str):
        for key in list(self.query_cache):
            if key[0] == sid:
                self.query_cache.pop(key, None)

    def query_limit(self, actor, group):
        now = time.monotonic()
        identity = (actor.platform, actor.user)
        chat_identity = (actor.platform, actor.group)
        for table, key, interval in (
            (self.user_queries, identity, self.settings.query_user_cooldown),
            (self.group_queries, chat_identity, self.settings.query_group_cooldown),
        ):
            previous = table.get(key)
            if interval and previous is not None and now - previous < interval:
                raise MCError(f"查询过于频繁，请等待 {math.ceil(interval - (now - previous))} 秒。")
        self.user_queries[identity] = now
        self.group_queries[chat_identity] = now
        if len(self.user_queries) > 10000:
            self.user_queries = {key: stamp for key, stamp in self.user_queries.items() if now - stamp < 600}

    async def diagnostics(self, group) -> dict:
        async def server_check(sid):
            server = self.settings.servers[sid]
            state, directory = await asyncio.gather(
                self.query(sid, "status"), asyncio.to_thread(path_health, server.directory, True)
            )
            details = {
                "id": sid,
                "name": server.name,
                "rcon": state,
                "directory": directory,
                **self.server_health.get(sid, {}),
            }
            try:
                rows = await asyncio.to_thread(read_whitelist, server)
                details["whitelist"] = {"readable": True, "entries": len(rows)}
            except MCError as exc:
                details["whitelist"] = {"readable": False, "error": str(exc)}
            if server.tps_mode == "spark":
                try:
                    report = await asyncio.to_thread(read_spark_report, server, self.settings.spark_file_max_age)
                    details["spark"] = {
                        "status": parse_spark_tps(report).status,
                        "path": str(server.directory / server.spark_file),
                    }
                except MCError as exc:
                    details["spark"] = {"status": "不可用", "error": str(exc), "source": server.tps_source}
            return details

        paths = {"plugin_data": self.data_dir, "temporary": Path(tempfile.gettempdir())}
        storage = {name: await asyncio.to_thread(path_health, path) for name, path in paths.items()}
        sizes = {}
        for suffix in ("", "-wal", "-shm"):
            file = Path(str(self.store.path) + suffix)
            sizes[file.name] = file.stat().st_size if file.exists() else 0
        return {
            "checked": utc_now(),
            "workers": self.worker_health,
            "storage": storage,
            "database_bytes": sizes,
            "servers": await asyncio.gather(*(server_check(sid) for sid in group.servers)),
            "queue": {
                row["state"]: row["amount"]
                for row in self.store.db.execute("SELECT state,COUNT(*) AS amount FROM outbox GROUP BY state")
            },
            "disk_warning_mb": self.settings.disk_warning_mb,
        }

    async def maintenance(self):
        if time.time() - self._last_maintenance >= 3600:
            identifiers = self.store.retention_ids(self.settings.logs_retention_days, self.settings.logs_max_entries)
            if identifiers:
                self.store.prune_logs(identifiers)
            self._last_maintenance = time.time()
        if self.settings.backup_interval_hours:
            async with self.backups.lock:
                existing = self.backups.list()
            previous = datetime.fromisoformat(existing[0]["created"]).timestamp() if existing else 0
            if time.time() - previous >= self.settings.backup_interval_hours * 3600:
                await self.backups.create(self.settings.raw, "scheduled")
        if self.settings.alerts_enabled:
            await self.check_alerts()

    def evaluate_alert(self, sid: str, kind: str, bad: bool, detail: str):
        row = self.store.db.execute("SELECT * FROM alerts WHERE server=? AND kind=?", (sid, kind)).fetchone()
        old = dict(row) if row else {"active": 0, "count": 0, "last_sent": 0}
        count = old["count"] + 1 if bad else 0
        active = int(bad and (old["active"] or count >= self.settings.alert_consecutive))
        send = (active and (not old["active"] or time.time() - old["last_sent"] >= self.settings.alert_cooldown)) or (
            old["active"] and not bad
        )
        recipients = self.server_recipients(sid)
        last_sent = old["last_sent"]
        with self.store.transaction():
            if send and recipients:
                key = "alert" if bad else "alert_recovery"
                text = self.settings.render(
                    key, server=self.settings.servers[sid].name, alert_type=kind, alert_detail=detail
                )
                self.store.enqueue(recipients, text)
                self.notification_wake.set()
                last_sent = time.time()
            self.store.db.execute(
                "INSERT INTO alerts(server,kind,active,count,last_sent,detail) VALUES(?,?,?,?,?,?) ON CONFLICT(server,kind) DO UPDATE SET active=excluded.active,count=excluded.count,last_sent=excluded.last_sent,detail=excluded.detail",
                (sid, kind, active, count, last_sent, detail[:500]),
            )

    async def check_alerts(self):
        data_storage = await asyncio.to_thread(path_health, self.data_dir)
        temporary_storage = await asyncio.to_thread(path_health, Path(tempfile.gettempdir()))
        for sid, server in self.settings.servers.items():
            if sid in self.restart_tasks:
                continue
            status = self.last_status.get(sid, "探测中")
            if "离线" in status or status == "在线":
                self.evaluate_alert(sid, "RCON 不可用", "离线" in status, status)
            storage = await asyncio.to_thread(path_health, server.directory)
            free = [
                item["free_bytes"]
                for item in (data_storage, temporary_storage, storage)
                if item["free_bytes"] is not None
            ]
            if free and self.settings.disk_warning_mb:
                free_mb = min(free) // (1024 * 1024)
                self.evaluate_alert(
                    sid,
                    "磁盘空间不足",
                    free_mb < self.settings.disk_warning_mb,
                    f"插件数据、临时目录或服务端所在盘剩余 {free_mb} MB",
                )
            if server.tps_mode != "spark":
                continue
            report_path = server.directory / server.spark_file
            if server.tps_source == "file" or (server.tps_source == "auto" and report_path.exists()):
                try:
                    await asyncio.to_thread(read_spark_report, server, self.settings.spark_file_max_age)
                    stale, detail = False, "采集文件已恢复更新"
                except MCError as exc:
                    stale, detail = True, str(exc)
                self.evaluate_alert(sid, "spark 采集异常", stale, detail)
            metrics = parse_spark_tps(await self.cached_query(sid, "tps"))
            if "tps_1m" not in metrics.missing:
                value = float(metrics.values["tps_1m"])
                self.evaluate_alert(
                    sid, "TPS 过低", value < self.settings.alert_tps_threshold, f"最近 1 分钟 TPS={value}"
                )

    def cleanup_preview(self) -> dict:
        identifiers = self.store.retention_ids(self.settings.logs_retention_days, self.settings.logs_max_entries)
        token = secrets.token_urlsafe(24)
        self.cleanup_previews = {token: (time.time() + 600, identifiers)}
        return {
            "token": token,
            "count": len(identifiers),
            "days": self.settings.logs_retention_days,
            "maximum": self.settings.logs_max_entries,
        }

    def cleanup_apply(self, token: str) -> int:
        item = self.cleanup_previews.pop(token, None)
        if not item or item[0] < time.time():
            raise MCError("清理预览已失效，请重新预览。")
        return self.store.prune_logs(item[1])

    async def execute(self, actor: Actor, action: str, args: list[str]) -> str:
        if self.closed:
            raise MCError("插件正在卸载。")
        group = self.settings.group_for(actor)
        if not isinstance(actor.user, str) or not actor.user.strip():
            raise MCError("无法确认发送者身份。")
        special = {
            "diagnose": (0, 1),
            "backup": (0, 0),
            "cancel": (1, 1),
            "requests": (0, 0),
            "approve": (1, 1),
            "reject": (1, 2),
            "mine": (0, 1),
        }
        if action not in special:
            if action in {"players", "tps", "status"}:
                self.query_limit(actor, group)
            return await self._execute_core(actor, action, args)
        low, high = special[action]
        if not low <= len(args) <= high:
            raise MCError("指令参数数量不正确，请查看帮助。")
        self.store.remember(group.id, actor.origin, actor.group)
        if action == "diagnose":
            self.require_admin(actor, group)
            report = await self.diagnostics(group)
            servers = report["servers"]
            if args:
                allowed = self.targets(group, args[0])
                servers = [row for row in servers if row["id"] in allowed]
            lines = [
                f"{row['name']}（{row['id']}）：{row['rcon']}；目录可写={row['directory']['writable']}；白名单可读={row['whitelist']['readable']}"
                for row in servers
            ]
            lines.extend(
                f"后台 {name}：{value['state']}；最近错误={value['last_error'] or '无'}"
                for name, value in report["workers"].items()
            )
            return self.settings.render("diagnostics", result="\n".join(lines))
        if action == "backup":
            if actor.admin is not True:
                raise MCError("插件完整备份仅允许已核实的 AstrBot 全局管理员或后台管理员创建。")
            result = await self.backups.create(self.settings.raw)
            with self.store.transaction():
                self.store.audit(group, actor, "backup", "", result["name"])
            return self.settings.render("backup_created", result=result["name"])
        if action == "cancel":
            targets = self.targets(group, args[0])
            for sid in targets:
                if not self.settings.can_restart(actor, group, sid):
                    raise MCError("没有此服务器的重启管理权限。")
                job = next((row for row in self.store.jobs() if row["server"] == sid), None)
                if sid not in self.restart_tasks or not job or job["phase"] not in {"queued", "countdown"}:
                    raise MCError("只能取消尚未发送 stop 的重启倒计时。")
            for sid in targets:
                self.cancel_requested.add(sid)
                task = self.restart_tasks[sid]
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                if sid in self.restart_tasks:
                    self.restart_tasks.pop(sid, None)
                    self.store.finish_job(
                        sid,
                        "cancelled",
                        "管理员取消倒计时",
                        "",
                        self.settings.render("restart_cancelled", server=self.settings.servers[sid].name),
                        recipients=self.server_recipients(sid),
                    )
                    self.notification_wake.set()
                self.cancel_requested.discard(sid)
                with self.store.transaction():
                    self.store.audit(group, actor, "restart_cancelled", sid, "发送 stop 前取消")
            return "\n".join(
                self.settings.render("restart_cancelled", server=self.settings.servers[sid].name) for sid in targets
            )
        if action == "requests":
            rows = self.store.pending_requests(group.id, "" if self.settings.can_manage(actor, group) else actor.user)
            lines = [
                f"#{row['id']} · {row['kind']} · {row['player']} · {row['user']} · {row['created']}" for row in rows
            ]
            return self.settings.render("requests", entries="\n".join(lines) or "暂无待审核申请")
        if action in {"approve", "reject"}:
            return await self.review_request(
                actor,
                group,
                positive(args[0], "申请编号", 1, 2**63 - 1),
                action == "approve",
                args[1] if len(args) > 1 else "管理员拒绝",
            )
        async with self.locks[group.id]:
            await self.ensure_group(group)
            rows = [row for row in self.store.entries(group.id) if row["owner"] == actor.user]
            page = positive(args[0], "页码") if args else 1
            pages = self.paginate(page, len(rows))
            selected = rows[(page - 1) * self.settings.page_size : page * self.settings.page_size]
            return self.settings.render(
                "list",
                page=page,
                pages=pages,
                total=len(rows),
                entries="\n".join(
                    self.settings.render("entry", id=row["id"], player=row["name"], owner=row["owner"])
                    for row in selected
                )
                or "暂无自己的条目",
            )

    async def submit_request(self, group, actor, kind, name, identifier, entry=None):
        with self.store.transaction():
            self.store.validate_player(group, name, identifier, exclude_id=entry["id"] if entry else None)
            request_id = self.store.create_request(group, actor, kind, name, identifier, entry)
            self.store.audit(group, actor, "request_" + kind, name, f"申请 #{request_id}")
        return self.settings.render("request_created", id=request_id, player=name)

    async def review_request(self, actor, group, request_id: int, approve: bool, detail: str = "") -> str:
        self.require_admin(actor, group)
        async with self.locks[group.id]:
            await self.ensure_group(group)
            row = self.store.db.execute(
                "SELECT * FROM requests WHERE id=? AND scope=? AND status='pending'", (request_id, group.id)
            ).fetchone()
            if not row:
                raise MCError("未找到本配置组待审核的申请。")
            row = dict(row)
            with self.store.transaction():
                if approve:
                    original = json.loads(row["expected"]) if row["expected"] else None
                    self.store.validate_player(group, row["player"], row["uuid"], exclude_id=row["target"])
                    if row["kind"] == "add":
                        owned = [entry for entry in self.store.entries(group.id) if entry["owner"] == row["user"]]
                        if len(owned) >= group.max_entries:
                            raise MCError("申请人的白名单数量已达到上限。")
                        try:
                            self.store.insert(group.id, row["player"], row["uuid"], row["user"])
                        except sqlite3.IntegrityError:
                            raise MCError("该玩家已存在，无法重复通过。") from None
                    else:
                        current = self.store.find(group.id, str(row["target"]))
                        if (
                            not original
                            or any(current[key] != original[key] for key in ("name", "uuid", "owner"))
                            or current["owner"] != row["user"]
                        ):
                            raise MCError("原条目或归属已变化，请拒绝旧申请并重新提交。")
                        try:
                            self.store.db.execute(
                                "UPDATE entries SET name=?,uuid=? WHERE scope=? AND id=? AND owner=?",
                                (row["player"], row["uuid"], group.id, row["target"], row["user"]),
                            )
                        except sqlite3.IntegrityError:
                            raise MCError("新的玩家名或 UUID 已存在。") from None
                    self.store.mark_dirty(group.servers)
                status = "approved" if approve else "rejected"
                self.store.db.execute(
                    "UPDATE requests SET status=?,reviewer=?,detail=? WHERE id=? AND scope=? AND status='pending'",
                    (status, actor.user, detail[:500], request_id, group.id),
                )
                self.store.audit(
                    group,
                    actor,
                    "request_" + status,
                    row["player"],
                    f"申请 #{request_id}；申请人={row['user']}；{detail[:500]}",
                )
            sync = await self.sync_group(group) if approve else ""
            return self.settings.render(
                "request_approved" if approve else "request_rejected",
                id=request_id,
                player=row["player"],
                detail=detail,
                sync=sync,
            )

    def entries_page(self, group, page: int = 1, size: int = 20, search: str = "") -> dict:
        value = search[:128]
        clause = (
            "scope=? AND (instr(lower(name),lower(?))>0 OR instr(uuid,?)>0 OR instr(owner,?)>0 OR CAST(id AS TEXT)=?)"
        )
        values = (group.id, value, value, value, value)
        total = self.store.db.execute("SELECT COUNT(*) FROM entries WHERE " + clause, values).fetchone()[0]
        rows = [
            dict(row)
            for row in self.store.db.execute(
                "SELECT * FROM entries WHERE " + clause + " ORDER BY id LIMIT ? OFFSET ?",
                (*values, size, (page - 1) * size),
            )
        ]
        return {"entries": rows, "total": total, "page": page, "pages": max(1, math.ceil(total / size))}

    def entries_fingerprint(self, group) -> str:
        rows = {sid: self.store.server_entries(sid) for sid in group.servers}
        return hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    async def bulk_preview(self, actor, group, data: str, format_name: str) -> dict:
        import csv
        import io

        self.require_admin(actor, group)
        if not isinstance(data, str) or len(data) > 1024 * 1024:
            raise MCError("导入文本不能超过 1 MB。")
        try:
            if format_name == "json":
                rows = json.loads(data)
                if isinstance(rows, dict):
                    rows = rows.get("entries")
            elif format_name == "csv":
                rows = list(csv.DictReader(io.StringIO(data.lstrip("\ufeff"))))
            elif format_name == "text":
                rows = [{"name": line.strip()} for line in data.splitlines() if line.strip()]
            else:
                raise MCError("导入格式应为 json、csv 或 text。")
        except (ValueError, TypeError):
            raise MCError("无法解析导入内容。") from None
        if not isinstance(rows, list) or not 1 <= len(rows) <= 500 or any(not isinstance(row, dict) for row in rows):
            raise MCError("每次导入应包含 1～500 条玩家记录。")
        additions, skipped, errors = [], [], []
        async with self.locks[group.id]:
            await self.ensure_group(group)
            existing = {row["name"].casefold(): row for row in self.store.entries(group.id)}
            seen = set()
            for index, row in enumerate(rows, 1):
                try:
                    name = player_name(str(row.get("name", "")))
                    owner = str(row.get("owner") or "")
                    if len(owner) > 128 or any(ord(char) < 32 for char in owner):
                        raise MCError("归属用户 ID 无效。")
                    if name.casefold() in seen:
                        raise MCError("导入内容存在重复玩家。")
                    seen.add(name.casefold())
                    canonical, identifier = await self.resolve_player(group, name)
                    if row.get("uuid") and str(row["uuid"]) != identifier:
                        raise MCError("导入 UUID 与当前身份模式不一致。")
                    old = existing.get(canonical.casefold())
                    if old:
                        if old["uuid"] != identifier:
                            raise MCError("已有同名条目使用不同 UUID。")
                        skipped.append(canonical)
                        continue
                    self.store.validate_player(group, canonical, identifier)
                    additions.append({"name": canonical, "uuid": identifier, "owner": owner})
                except MCError as exc:
                    errors.append({"row": index, "error": str(exc)})
            fingerprint = self.entries_fingerprint(group)
        now = time.time()
        self.bulk_previews = {key: value for key, value in self.bulk_previews.items() if value["expires"] > now}
        if len(self.bulk_previews) >= 10:
            raise MCError("待确认导入过多，请稍后重试。")
        token = secrets.token_urlsafe(24)
        self.bulk_previews[token] = {
            "scope": group.id,
            "user": actor.user,
            "expires": now + 600,
            "entries": additions,
            "errors": errors,
            "fingerprint": fingerprint,
        }
        return {"token": token, "additions": additions, "skipped": skipped, "errors": errors, "expires_in": 600}

    async def bulk_apply(self, actor, group, token: str) -> dict:
        self.require_admin(actor, group)
        preview = self.bulk_previews.pop(token, None)
        if (
            not preview
            or preview["expires"] < time.time()
            or preview["scope"] != group.id
            or preview["user"] != actor.user
        ):
            raise MCError("导入预览已失效，请重新预览。")
        if preview["errors"]:
            raise MCError("请先修正预览中的错误。")
        async with self.locks[group.id]:
            if self.entries_fingerprint(group) != preview["fingerprint"]:
                raise MCError("白名单已变化，请重新预览后导入。")
            with self.store.transaction():
                for entry in preview["entries"]:
                    self.store.validate_player(group, entry["name"], entry["uuid"])
                    self.store.insert(group.id, entry["name"], entry["uuid"], entry["owner"])
                    self.store.audit(group, actor, "bulk_add", entry["name"], "管理员批量导入；归属=" + entry["owner"])
                self.store.mark_dirty(group.servers)
            return {"count": len(preview["entries"]), "sync": await self.sync_group(group)}

    async def bulk_change(self, actor, group, identifiers: list[int], action: str, owner: str = "") -> dict:
        self.require_admin(actor, group)
        if not isinstance(identifiers, list) or not 1 <= len(identifiers) <= 500:
            raise MCError("请选择 1～500 条记录。")
        ids = list(dict.fromkeys(positive(value, "编号", 1, 2**63 - 1) for value in identifiers))
        if (
            action not in {"remove", "transfer"}
            or not isinstance(owner, str)
            or len(owner) > 128
            or any(ord(char) < 32 for char in owner)
        ):
            raise MCError("批量操作或归属无效。")
        async with self.locks[group.id]:
            entries = [self.store.find(group.id, str(identifier)) for identifier in ids]
            with self.store.transaction():
                for entry in entries:
                    if action == "remove":
                        self.store.db.execute("DELETE FROM entries WHERE scope=? AND id=?", (group.id, entry["id"]))
                    else:
                        self.store.db.execute(
                            "UPDATE entries SET owner=? WHERE scope=? AND id=?", (owner, group.id, entry["id"])
                        )
                    self.store.audit(
                        group,
                        actor,
                        "bulk_" + action,
                        entry["name"],
                        f"#{entry['id']}；原归属={entry['owner']}；新归属={owner if action == 'transfer' else '已移除'}",
                    )
                if action == "remove":
                    self.store.mark_dirty(group.servers)
            return {"count": len(entries), "sync": await self.sync_group(group) if action == "remove" else "归属已更新"}
