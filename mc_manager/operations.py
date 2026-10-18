"""Operational features shared by chat commands and the authenticated dashboard."""

from __future__ import annotations

import asyncio
import hashlib
import json
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
from .files import read_whitelist
from .message_catalog import text as message_text
from .monitoring import QueryInfo, QueryResult, finish_query, list_result, parse_players
from .performance import parse_performance, performance_values
from .text import safe_text


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
        self.tps_capabilities = {}
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
                    state="retrying",
                    last_error=safe_text(self.settings.localize(exc) or type(exc).__name__, 500),
                    failures=failures,
                )
                self.logger.exception("MC %s 后台任务失败，%s 秒后重试", name, delay)
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
        server = self.settings.servers[sid]
        log_wait = server.spark_log_wait_seconds if kind == "tps" and server.spark_log_fallback else 0
        deadline = self.settings.query_timeout_seconds or max(5, 2 * self.settings.rcon_timeout + 2 + log_wait)
        try:
            async with asyncio.timeout(deadline):
                result = await self._query_rcon(sid, kind)
        except TimeoutError:
            text = message_text("query_timeout", seconds=f"{deadline:g}")
            result = QueryResult(text, QueryInfo(error=text))
            if kind == "status":
                self.last_status[sid] = "查询超时，状态未确认"
        result = finish_query(result, started)
        health = self.server_health.setdefault(sid, {})
        for selected in ("status", "players") if kind in {"status", "players"} else (kind,):
            health[selected + "_checked"] = utc_now()
            health[selected + "_latency_ms"] = round((time.monotonic() - started) * 1000, 1)
            if isinstance(result, QueryResult):
                health[selected + "_source"] = result.info.source
                health[selected + "_command"] = result.info.command
                health[selected + "_error"] = result.info.error
        if kind in {"status", "players"} and isinstance(result, QueryResult):
            health.update(
                parse_players(result.info.raw if not result.info.error else "", self.settings.tps_missing_value)
            )
        return result

    async def cached_query(self, sid: str, kind: str, fresh=False) -> str:
        # Status and players use the same configured list command. Share the
        # transport result while retaining each caller's presentation.
        key = (sid, "players" if kind == "status" else kind)
        cached = self.query_cache.get(key)
        if not fresh and cached and cached[0] > time.monotonic():
            result = cached[1].cached_copy() if isinstance(cached[1], QueryResult) else cached[1]
            return list_result(result, kind)
        task = self.query_inflight.get(key)
        if task is None or task.done():

            async def run():
                try:
                    value = await self.query(sid, kind)
                    ttl = self.settings.query_cache_seconds
                    if isinstance(value, QueryResult):
                        if value.info.error:
                            ttl = min(ttl, 1)
                    elif value.startswith("离线或 RCON"):
                        ttl = min(ttl, 1)
                    if ttl:
                        self.query_cache[key] = (time.monotonic() + ttl, value)
                    return value
                finally:
                    if self.query_inflight.get(key) is asyncio.current_task():
                        self.query_inflight.pop(key, None)

            task = asyncio.create_task(run(), name=f"mc-query-{sid}-{kind}")
            self.query_inflight[key] = task
        return list_result(await asyncio.shield(task), kind)

    def invalidate_queries(self, sid: str):
        self.tps_capabilities.pop(sid, None)
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
                raise MCError(message_text("error_operations_011", value1=math.ceil(interval - (now - previous))))
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
            report = await self.cached_query(sid, "tps")
            parsed = parse_performance(
                report, report.info.command if isinstance(report, QueryResult) else server.tps_command
            )
            details["performance"] = {
                "status": ("部分" if parsed.partial else "已解析") if parsed.recognized else "未解析",
                "format": parsed.format,
                "source": report.info.source if isinstance(report, QueryResult) else "RCON",
                "command": report.info.command if isinstance(report, QueryResult) else server.tps_command,
                "error": report.info.error if isinstance(report, QueryResult) else "",
            }
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
            "bridge": self.bridge_snapshot(),
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
                    key,
                    server=self.settings.servers[sid].name,
                    server_id=sid,
                    alert_type=message_text(
                        {
                            "RCON 不可用": "alert_type_rcon",
                            "磁盘空间不足": "alert_type_disk",
                            "TPS 过低": "alert_type_tps",
                        }.get(kind, "alert_type_other"),
                        alert_type=kind,
                    ),
                    alert_detail=detail,
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
                self.evaluate_alert(
                    sid,
                    "RCON 不可用",
                    "离线" in status,
                    message_text("alert_status_offline") if "离线" in status else message_text("alert_status_online"),
                )
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
                    message_text("alert_disk_detail", free_mb=free_mb),
                )
            result = await self.cached_query(sid, "tps")
            if isinstance(result, QueryResult) and result.info.error:
                continue
            parsed = parse_performance(result, server.tps_command, self.settings.tps_missing_value)
            values = performance_values(parsed, result, self.settings.tps_missing_value)
            if "1m" in parsed.valid_windows:
                raw_value = next(rate for window, rate in parsed.rates if window == "1m")
                detail_key = "alert_tps_detail"
            elif "perf_tps" in parsed.values or parsed.valid_windows:
                raw_value = values["perf_tps"]
                detail_key = "alert_tps_current_detail"
            else:
                # A numeric missing-value label must never become a sample.
                # Target tick rates are configuration, not measured TPS.
                continue
            try:
                value = float(raw_value.replace("*", "").replace(",", "."))
            except (TypeError, ValueError, KeyError):
                continue
            self.evaluate_alert(
                sid,
                "TPS 过低",
                value < self.settings.alert_tps_threshold,
                message_text(detail_key, perf_tps=value),
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
            raise MCError(message_text("error_operations_001"))
        return self.store.prune_logs(item[1])

    async def execute(self, actor: Actor, action: str, args: list[str]) -> str:
        if self.closed:
            raise MCError(message_text("error_unloading"))
        group = self.settings.group_for(actor)
        if not isinstance(actor.user, str) or not actor.user.strip():
            raise MCError(message_text("error_operations_002"))
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
            raise MCError(message_text("error_operations_003"))
        self.store.remember(group.id, actor.origin, actor.group)
        if action == "diagnose":
            self.require_admin(actor, group)
            report = await self.diagnostics(group)
            servers = report["servers"]
            if args:
                allowed = self.targets(group, args[0])
                servers = [row for row in servers if row["id"] in allowed]
            lines = [
                self.settings.render(
                    "diagnostic_server",
                    server=row["name"],
                    server_id=row["id"],
                    status=row["rcon"],
                    writable=message_text("boolean_unknown")
                    if row["directory"]["writable"] is None
                    else message_text("boolean_true")
                    if row["directory"]["writable"]
                    else message_text("boolean_false"),
                    readable=message_text("boolean_true")
                    if row["whitelist"]["readable"]
                    else message_text("boolean_false"),
                )
                for row in servers
            ]
            lines.extend(
                self.settings.render(
                    "diagnostic_worker",
                    worker=name,
                    worker_state=message_text("worker_state_" + value["state"])
                    if "worker_state_" + value["state"] in self.settings.messages
                    else value["state"],
                    error=value["last_error"] or message_text("no_error"),
                )
                for name, value in report["workers"].items()
            )
            return self.settings.render("diagnostics", result="\n".join(lines))
        if action == "backup":
            if actor.admin is not True:
                raise MCError(message_text("error_operations_012"))
            result = await self.backups.create(self.settings.raw)
            with self.store.transaction():
                self.store.audit(group, actor, "backup", "", result["name"])
            return self.settings.render("backup_created", result=result["name"])
        if action == "cancel":
            targets = self.targets(group, args[0])
            for sid in targets:
                if not self.settings.can_restart(actor, group, sid):
                    raise MCError(message_text("error_operations_016"))
                job = next((row for row in self.store.jobs() if row["server"] == sid), None)
                if sid not in self.restart_tasks or not job or job["phase"] not in {"queued", "countdown"}:
                    raise MCError(message_text("error_operations_017"))
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
                        self.settings.render("restart_cancelled_detail"),
                        "",
                        self.settings.render("restart_cancelled", server=self.settings.servers[sid].name),
                        recipients=self.server_recipients(sid),
                    )
                    self.notification_wake.set()
                self.cancel_requested.discard(sid)
                with self.store.transaction():
                    self.store.audit(
                        group, actor, "restart_cancelled", sid, self.settings.render("audit_restart_cancelled_1")
                    )
            return "\n".join(
                self.settings.render("restart_cancelled", server=self.settings.servers[sid].name) for sid in targets
            )
        if action == "requests":
            rows = self.store.pending_requests(group.id, "" if self.settings.can_manage(actor, group) else actor.user)
            lines = [
                self.settings.render(
                    "request_entry",
                    id=row["id"],
                    request_kind=message_text("request_kind_" + row["kind"]),
                    player=row["player"],
                    user=row["user"],
                    time=row["created"],
                )
                for row in rows
            ]
            return self.settings.render("requests", entries="\n".join(lines) or self.settings.render("empty_requests"))
        if action in {"approve", "reject"}:
            return await self.review_request(
                actor,
                group,
                positive(args[0], "申请编号", 1, 2**63 - 1),
                action == "approve",
                args[1] if len(args) > 1 else self.settings.render("default_rejection"),
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
                or self.settings.render("empty_mine"),
            )

    async def submit_request(self, group, actor, kind, name, identifier, entry=None):
        with self.store.transaction():
            self.store.validate_player(group, name, identifier, exclude_id=entry["id"] if entry else None)
            request_id = self.store.create_request(group, actor, kind, name, identifier, entry)
            self.store.audit(
                group, actor, "request_" + kind, name, self.settings.render("audit_event_1", id=request_id)
            )
        return self.settings.render("request_created", id=request_id, player=name)

    async def review_request(self, actor, group, request_id: int, approve: bool, detail: str = "") -> str:
        self.require_admin(actor, group)
        async with self.locks[group.id]:
            await self.ensure_group(group)
            row = self.store.db.execute(
                "SELECT * FROM requests WHERE id=? AND scope=? AND status='pending'", (request_id, group.id)
            ).fetchone()
            if not row:
                raise MCError(message_text("error_operations_013"))
            row = dict(row)
            with self.store.transaction():
                if approve:
                    original = json.loads(row["expected"]) if row["expected"] else None
                    self.store.validate_player(group, row["player"], row["uuid"], exclude_id=row["target"])
                    if row["kind"] == "add":
                        owned = [entry for entry in self.store.entries(group.id) if entry["owner"] == row["user"]]
                        if len(owned) >= group.max_entries:
                            raise MCError(message_text("error_operations_022"))
                        try:
                            self.store.insert(group.id, row["player"], row["uuid"], row["user"])
                        except sqlite3.IntegrityError:
                            raise MCError(message_text("error_operations_025")) from None
                    else:
                        current = self.store.find(group.id, str(row["target"]))
                        if (
                            not original
                            or any(current[key] != original[key] for key in ("name", "uuid", "owner"))
                            or current["owner"] != row["user"]
                        ):
                            raise MCError(message_text("error_operations_023"))
                        try:
                            self.store.db.execute(
                                "UPDATE entries SET name=?,uuid=? WHERE scope=? AND id=? AND owner=?",
                                (row["player"], row["uuid"], group.id, row["target"], row["user"]),
                            )
                        except sqlite3.IntegrityError:
                            raise MCError(message_text("error_operations_026")) from None
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
                    self.settings.render("audit_event_2", id=request_id, owner=row["user"], detail=detail[:500]),
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
            raise MCError(message_text("error_operations_004"))
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
                raise MCError(message_text("error_operations_018"))
        except (ValueError, TypeError):
            raise MCError(message_text("error_operations_014")) from None
        if not isinstance(rows, list) or not 1 <= len(rows) <= 500 or any(not isinstance(row, dict) for row in rows):
            raise MCError(message_text("error_operations_005"))
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
                        raise MCError(message_text("error_operations_019"))
                    if name.casefold() in seen:
                        raise MCError(message_text("error_operations_020"))
                    seen.add(name.casefold())
                    canonical, identifier = await self.resolve_player(group, name)
                    if row.get("uuid") and str(row["uuid"]) != identifier:
                        raise MCError(message_text("error_operations_021"))
                    old = existing.get(canonical.casefold())
                    if old:
                        if old["uuid"] != identifier:
                            raise MCError(message_text("error_operations_024"))
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
            raise MCError(message_text("error_operations_006"))
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
            raise MCError(message_text("error_operations_007"))
        if preview["errors"]:
            raise MCError(message_text("error_operations_008"))
        async with self.locks[group.id]:
            if self.entries_fingerprint(group) != preview["fingerprint"]:
                raise MCError(message_text("error_operations_015"))
            with self.store.transaction():
                for entry in preview["entries"]:
                    self.store.validate_player(group, entry["name"], entry["uuid"])
                    self.store.insert(group.id, entry["name"], entry["uuid"], entry["owner"])
                    self.store.audit(
                        group,
                        actor,
                        "bulk_add",
                        entry["name"],
                        self.settings.render("audit_bulk_add", owner=entry["owner"]),
                    )
                self.store.mark_dirty(group.servers)
            return {"count": len(preview["entries"]), "sync": await self.sync_group(group)}

    async def bulk_change(self, actor, group, identifiers: list[int], action: str, owner: str = "") -> dict:
        self.require_admin(actor, group)
        if not isinstance(identifiers, list) or not 1 <= len(identifiers) <= 500:
            raise MCError(message_text("error_operations_009"))
        ids = list(dict.fromkeys(positive(value, "编号", 1, 2**63 - 1) for value in identifiers))
        if (
            action not in {"remove", "transfer"}
            or not isinstance(owner, str)
            or len(owner) > 128
            or any(ord(char) < 32 for char in owner)
        ):
            raise MCError(message_text("error_operations_010"))
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
                        self.settings.render(
                            "audit_event_3",
                            id=entry["id"],
                            old_owner=entry["owner"],
                            new_owner=owner if action == "transfer" else message_text("owner_removed"),
                        ),
                    )
                if action == "remove":
                    self.store.mark_dirty(group.servers)
            return {"count": len(entries), "sync": await self.sync_group(group) if action == "remove" else "归属已更新"}
