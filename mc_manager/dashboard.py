"""Authenticated dashboard operations and exclusive, rollback-protected restore."""

from __future__ import annotations

import asyncio
import copy
import csv
import io
import json
from zipfile import ZipFile

from astrbot.api.web import error_response, json_response, request

from .backups import BackupManager
from .config import Actor, Group, MCError, Settings, positive
from .operations import path_health, utc_now
from .store import Store


def export_rows(rows: list[dict], kind: str, filename: str) -> dict:
    if kind == "json":
        return {
            "name": filename + ".json",
            "content": json.dumps(rows, ensure_ascii=False, indent=2),
            "mime": "application/json;charset=utf-8",
        }
    if kind != "csv":
        raise MCError("导出格式应为 json 或 csv。")
    output = io.StringIO(newline="")
    columns = list(rows[0]) if rows else []
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        safe = {}
        for key, value in row.items():
            text = str(value if value is not None else "")
            safe[key] = "'" + text if text[:1] in {"=", "+", "-", "@", "\t", "\r", "\n"} else text
        writer.writerow(safe)
    return {"name": filename + ".csv", "content": "\ufeff" + output.getvalue(), "mime": "text/csv;charset=utf-8"}


class DashboardOperationsMixin:
    def _backup_manager(self, service):
        if service is not None:
            return service.backups
        if getattr(self, "_repair_backups", None) is None:
            self._repair_backups = BackupManager(self.data_dir)
        return self._repair_backups

    async def _backup_file_operation(self, manager, function, *args, **kwargs):
        async with manager.lock:
            task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise

    async def page_operations(self):
        try:
            self._page_admin()
            if request.method.upper() == "GET":
                payload = dict(request.query)
                action = str(payload.get("kind", "diagnostics"))
            else:
                payload = await request.json(default={})
                if not isinstance(payload, dict):
                    raise MCError("操作请求必须是 JSON 对象。")
                action = str(payload.get("action", ""))
            if action == "restore_apply":
                return json_response(await self._restore_backup(payload))
            async with self.operation(allow_repair=True) as service:
                if action == "diagnostics" and service is None:
                    import tempfile
                    from pathlib import Path

                    storage = {
                        "plugin_data": await asyncio.to_thread(path_health, self.data_dir),
                        "temporary": await asyncio.to_thread(path_health, Path(tempfile.gettempdir())),
                    }
                    return json_response(
                        {
                            "checked": utc_now(),
                            "workers": {
                                "initialization": {
                                    "state": "retrying",
                                    "last_success": "",
                                    "last_error": self.config_error or "服务未启动",
                                }
                            },
                            "storage": storage,
                            "database_bytes": {},
                            "servers": [],
                            "queue": {},
                        }
                    )
                manager = (
                    self._backup_manager(service)
                    if action in {"backups", "backup_download", "backup_create", "restore_preview"}
                    else None
                )
                if action == "backups":
                    return json_response(
                        {
                            "backups": await self._backup_file_operation(manager, manager.list),
                            "revision": self.config_revision,
                        }
                    )
                if action == "backup_download":
                    return json_response(
                        await self._backup_file_operation(manager, manager.download, str(payload.get("name", "")))
                    )
                if action == "restore_preview":
                    preview = await self._backup_file_operation(
                        manager, manager.preview, name=str(payload.get("name", "")), data=payload.get("data", "")
                    )
                    return json_response({**preview, "revision": self.config_revision})
                if service is None:
                    raise MCError("请先修正配置，或从已有备份恢复。")
                if action == "backup_create":
                    result = await manager.create(copy.deepcopy(dict(self.config)))
                    return json_response(result)
                if action == "cleanup_preview":
                    return json_response(service.cleanup_preview())
                if action == "cleanup_apply":
                    count = service.cleanup_apply(str(payload.get("token", "")))
                    return json_response({"count": count})
                if action == "diagnostics":
                    group = (
                        self._group(str(payload["group"]))
                        if payload.get("group")
                        else Group("dashboard", "", "", tuple(self.settings.servers))
                    )
                    return json_response(await service.diagnostics(group))
                if action in {"notifications", "notification_retry"}:
                    scope = str(payload.get("group", ""))
                    if scope:
                        self._group(scope)
                    if action == "notification_retry":
                        service.store.retry_notification(positive(payload.get("id"), "通知编号", 1, 2**63 - 1), scope)
                        service.notification_wake.set()
                    return json_response({"notifications": service.store.notifications(scope)})
                group = self._group(str(payload.get("group", "")))
                actor = Actor(
                    group.platform, group.group_ids[0], "dashboard:" + request.username, request.username, True, ""
                )
                if action == "requests":
                    return json_response({"requests": service.store.pending_requests(group.id)})
                if action in {"approve", "reject"}:
                    text = await service.review_request(
                        actor,
                        group,
                        positive(payload.get("id"), "申请编号", 1, 2**63 - 1),
                        action == "approve",
                        str(payload.get("detail", "管理员拒绝"))[:500],
                    )
                    return json_response({"message": text})
                if action == "bulk_preview":
                    return json_response(
                        await service.bulk_preview(
                            actor, group, payload.get("data", ""), str(payload.get("format", "json"))
                        )
                    )
                if action == "bulk_apply":
                    return json_response(await service.bulk_apply(actor, group, str(payload.get("token", ""))))
                if action in {"bulk_remove", "bulk_transfer"}:
                    if payload.get("confirmed") is not True:
                        raise MCError("批量修改需要明确确认。")
                    return json_response(
                        await service.bulk_change(
                            actor,
                            group,
                            payload.get("ids"),
                            "remove" if action == "bulk_remove" else "transfer",
                            payload.get("owner", ""),
                        )
                    )
                if action == "entries_export":
                    result = service.entries_page(group, 1, 10000, str(payload.get("search", "")))
                    export = export_rows(
                        result["entries"], str(payload.get("format", "json")), "mc-whitelist-" + group.id
                    )
                    return json_response({**export, "total": result["total"], "truncated": result["total"] > 10000})
                if action == "logs_export":
                    filters = {
                        key: str(payload.get(key, ""))[:128]
                        for key in ("user", "player", "action_filter", "start", "end")
                    }
                    filters["action"] = filters.pop("action_filter")
                    rows, total = service.store.filtered_logs(group.id, 1, 10000, **filters)
                    export = export_rows(rows, str(payload.get("format", "csv")), "mc-logs-" + group.id)
                    return json_response({**export, "total": total, "truncated": total > 10000})
                raise MCError("不支持的运维操作。")
        except (MCError, ValueError, TypeError, KeyError) as exc:
            return error_response(str(exc))
        except Exception:
            self.logger.exception("MC 运维操作失败")
            return error_response("运维操作失败，请查看后台任务状态与 AstrBot 日志。")

    async def _restore_backup(self, payload: dict) -> dict:
        if payload.get("confirmed") is not True:
            raise MCError("恢复会覆盖配置和数据库，需要明确确认。")
        async with self.guard:
            await self.operations_idle.wait()
            if payload.get("revision") != self.config_revision:
                raise MCError("配置已变化，请重新预览备份。")
            current = self.service
            if current is not None and current.restart_tasks:
                raise MCError("服务器正在重启，请等待监控结束再恢复。")
            manager = self._backup_manager(current)
            config, database = manager.consume(str(payload.get("token", "")))
            settings = Settings(config)
            previous_config = copy.deepcopy(dict(self.config))
            previous_settings = self.settings
            previous_error = self.config_error
            if current is not None:
                await current.pause()
            candidate = None
            try:
                if not manager.database.exists():
                    Store(self.data_dir).close()
                safety = await manager.create(previous_config, "before-restore")
            except BaseException:
                if current is not None:
                    current.resume()
                raise
            rollback_data = manager.path(safety["name"]).read_bytes()
            try:
                if current is not None:
                    await current.close()
                self.service = None
                manager.restore_database(database)
                self.config.clear()
                self.config.update(config)
                await self._persist_config()
                candidate = self._new_service(settings)
                with candidate.store.transaction():
                    candidate.store.mark_dirty([sid for sid, groups in settings.server_groups.items() if groups])
                    for group in settings.groups.values():
                        actor = Actor(
                            group.platform, group.group_ids[0], "dashboard:" + request.username, request.username, True
                        )
                        candidate.store.audit(
                            group, actor, "restore", "", "从插件数据备份恢复；安全备份=" + safety["name"]
                        )
                self._install_service(settings, candidate)
                await candidate.start()
            except BaseException:
                if candidate is not None:
                    await candidate.close()
                if current is not None:
                    await current.close()
                with ZipFile(io.BytesIO(rollback_data)) as archive:
                    manager.restore_database(archive.read("database.sqlite3"), sanitize=False)
                self.config.clear()
                self.config.update(previous_config)
                await self._persist_config()
                if previous_error:
                    self._install_service(previous_settings, None, previous_error)
                else:
                    restored = self._new_service(previous_settings)
                    self._install_service(previous_settings, restored)
                    await restored.start()
                raise
            return {
                "message": "插件数据与配置已恢复，白名单将重新同步。",
                "safety_backup": safety["name"],
                "revision": self.config_revision,
            }
