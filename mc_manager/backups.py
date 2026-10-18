"""Bounded plugin-data archives and SQLite online backup/restore."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import re
import secrets
import sqlite3
import tempfile
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from .config import MCError, Settings, player_name
from .message_catalog import text as message_text

MAX_ARCHIVE = 32 * 1024 * 1024
MAX_DATABASE = 128 * 1024 * 1024
MEMBERS = {"database.sqlite3", "config.json", "backup.json"}
NAME = re.compile(r"^mc-backup-\d{8}-\d{6}-[0-9a-f]{12}\.zip$")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_database(path: Path) -> dict[str, int]:
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA trusted_schema=OFF")
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise MCError(message_text("error_backups_001"))
        objects = connection.execute("SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        allowed = {
            "scopes",
            "bindings",
            "server_history",
            "entries",
            "logs",
            "sessions",
            "group_sessions",
            "sync_versions",
            "dirty",
            "jobs",
            "outbox",
            "requests",
            "alerts",
        }
        if any(kind in {"view", "trigger"} or (kind == "table" and name not in allowed) for kind, name in objects):
            raise MCError(message_text("error_backups_002"))
        required = {
            "scopes": {"id", "platform", "group_id", "next_id"},
            "bindings": {"server", "scope"},
            "entries": {"scope", "id", "name", "uuid", "owner", "created"},
            "logs": {"id", "scope", "time", "action"},
        }
        for table, columns in required.items():
            actual = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            if not columns <= actual:
                raise MCError(message_text("error_backups_011", table=table))
        scopes = {row[0]: row[1] for row in connection.execute("SELECT id,next_id FROM scopes")}
        if any(not isinstance(value, int) or not 1 <= value <= 2**63 - 1 for value in scopes.values()):
            raise MCError(message_text("error_backups_003"))
        for scope, index, name, identifier, owner in connection.execute("SELECT scope,id,name,uuid,owner FROM entries"):
            player_name(name)
            uuid.UUID(identifier)
            if (
                scope not in scopes
                or not isinstance(index, int)
                or index < 1
                or not isinstance(owner, str)
                or not isinstance(scopes[scope], int)
                or scopes[scope] <= index
            ):
                raise MCError(message_text("error_backups_012"))
        for column in ("id", "lower(name)", "lower(uuid)"):
            if connection.execute(
                f"SELECT 1 FROM entries GROUP BY scope,{column} HAVING COUNT(*)>1 LIMIT 1"
            ).fetchone():
                raise MCError(message_text("error_backups_013"))
        return {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("scopes", "entries", "logs")
        }


class BackupManager:
    def __init__(self, data_dir: Path, keep: int = 7):
        data_dir = data_dir.resolve()
        self.directory = data_dir / "backups"
        if self.directory.is_symlink():
            raise MCError(message_text("error_backups_004"))
        self.directory.mkdir(parents=True, exist_ok=True)
        self.database = data_dir / "mc.sqlite3"
        self.keep = keep
        self.pending: dict[str, tuple[Path, float, str]] = {}
        self.lock = asyncio.Lock()

    def path(self, name: str) -> Path:
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise MCError(message_text("error_backups_005"))
        path = self.directory / name
        if path.is_symlink() or path.resolve().parent != self.directory.resolve():
            raise MCError(message_text("error_backups_006"))
        return path

    def list(self) -> list[dict]:
        result = []
        for path in self.directory.glob("mc-backup-*.zip"):
            if not NAME.fullmatch(path.name) or path.is_symlink():
                continue
            try:
                with ZipFile(path) as archive:
                    if archive.getinfo("backup.json").file_size > 16384:
                        continue
                    manifest = json.loads(archive.read("backup.json"))
                datetime.fromisoformat(manifest["created"])
                result.append(
                    {
                        "name": path.name,
                        "created": manifest["created"],
                        "label": manifest.get("label", ""),
                        "bytes": path.stat().st_size,
                    }
                )
            except (OSError, ValueError, KeyError, TypeError, AttributeError, BadZipFile):
                continue
        result.sort(key=lambda item: datetime.fromisoformat(item["created"]).timestamp(), reverse=True)
        return result

    def _create(self, config: dict, label: str) -> dict:
        name = datetime.now(timezone.utc).strftime("mc-backup-%Y%m%d-%H%M%S-") + secrets.token_hex(6) + ".zip"
        with tempfile.TemporaryDirectory(prefix=".backup-", dir=self.directory) as temporary:
            database = Path(temporary) / "database.sqlite3"
            with (
                closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)) as source,
                closing(sqlite3.connect(database)) as destination,
            ):
                source.backup(destination, pages=128, sleep=0.01)
            if database.stat().st_size > MAX_DATABASE:
                raise MCError(message_text("error_backups_014"))
            counts = validate_database(database)
            db_data = database.read_bytes()
            config_data = json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8")
            if len(config_data) > 1024 * 1024:
                raise MCError(message_text("error_backups_015"))
            manifest = {
                "format": 1,
                "plugin": "astrbot_plugin_mc_manager",
                "created": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                "label": label,
                "counts": counts,
                "sha256": {"database.sqlite3": digest(db_data), "config.json": digest(config_data)},
            }
            staged = Path(temporary) / "archive.zip"
            with ZipFile(staged, "w", ZIP_DEFLATED) as archive:
                archive.writestr("database.sqlite3", db_data)
                archive.writestr("config.json", config_data)
                archive.writestr("backup.json", json.dumps(manifest, ensure_ascii=False))
            if staged.stat().st_size > MAX_ARCHIVE:
                raise MCError(message_text("error_backups_016"))
            os.chmod(staged, 0o600)
            os.replace(staged, self.path(name))
        for old in self.list()[self.keep :]:
            self.path(old["name"]).unlink(missing_ok=True)
        return {"name": name, **manifest, "bytes": self.path(name).stat().st_size}

    async def create(self, config: dict, label: str = "manual") -> dict:
        async with self.lock:
            task = asyncio.create_task(asyncio.to_thread(self._create, config, label))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise

    def download(self, name: str) -> dict:
        path = self.path(name)
        if not path.is_file() or path.stat().st_size > MAX_ARCHIVE:
            raise MCError(message_text("error_backups_007"))
        data = path.read_bytes()
        return {"name": name, "sha256": digest(data), "content_base64": base64.b64encode(data).decode("ascii")}

    def inspect(self, data: bytes) -> tuple[dict, bytes, dict]:
        if len(data) > MAX_ARCHIVE:
            raise MCError(message_text("error_backups_008"))
        try:
            with ZipFile(io.BytesIO(data)) as archive:
                if len(archive.namelist()) != 3 or set(archive.namelist()) != MEMBERS:
                    raise MCError(message_text("error_backups_021"))
                sizes = {info.filename: info.file_size for info in archive.infolist()}
                if (
                    sizes["database.sqlite3"] > MAX_DATABASE
                    or sizes["config.json"] > 1024 * 1024
                    or sizes["backup.json"] > 16384
                ):
                    raise MCError(message_text("error_backups_022"))
                manifest = json.loads(archive.read("backup.json"))
                if (
                    not isinstance(manifest, dict)
                    or type(manifest.get("format")) is not int
                    or manifest.get("format") != 1
                    or manifest.get("plugin") != "astrbot_plugin_mc_manager"
                    or not isinstance(manifest.get("sha256"), dict)
                ):
                    raise MCError(message_text("error_backups_023"))
                raw_config = archive.read("config.json")
                database = archive.read("database.sqlite3")
            for name, contents in (("database.sqlite3", database), ("config.json", raw_config)):
                if manifest["sha256"].get(name) != digest(contents):
                    raise MCError(message_text("error_backups_024"))
            config = json.loads(raw_config)
            Settings(config)
            with tempfile.TemporaryDirectory(prefix=".inspect-", dir=self.directory) as temporary:
                path = Path(temporary) / "check.sqlite3"
                path.write_bytes(database)
                counts = validate_database(path)
            return config, database, counts
        except MCError:
            raise
        except (OSError, ValueError, KeyError, TypeError, AttributeError, BadZipFile, sqlite3.DatabaseError):
            raise MCError(message_text("error_backups_017")) from None

    def preview(self, *, name: str = "", data: str = "") -> dict:
        for token, (path, expires, _) in list(self.pending.items()):
            if expires < time.time():
                path.unlink(missing_ok=True)
                del self.pending[token]
        if len(self.pending) >= 5:
            raise MCError(message_text("error_backups_009"))
        if data:
            if not isinstance(data, str) or len(data) > MAX_ARCHIVE * 4 // 3 + 8:
                raise MCError(message_text("error_backups_018"))
            try:
                contents = base64.b64decode(data, validate=True)
            except (ValueError, TypeError):
                raise MCError(message_text("error_backups_025")) from None
        else:
            contents = base64.b64decode(self.download(name)["content_base64"])
        config, _, counts = self.inspect(contents)
        token = secrets.token_urlsafe(24)
        path = self.directory / (".restore-" + secrets.token_hex(12) + ".zip")
        with path.open("xb") as output:
            output.write(contents)
        os.chmod(path, 0o600)
        self.pending[token] = (path, time.time() + 600, digest(contents))
        return {
            "token": token,
            "counts": counts,
            "groups": [
                {"id": group.get("id"), "group_id": group.get("group_id")} for group in config.get("groups", [])
            ],
            "servers": [server.get("id") for server in config.get("servers", [])],
            "expires_in": 600,
            "note": "恢复覆盖插件配置与数据库；恢复前自动备份当前数据，旧待发广播将清空。Minecraft 世界文件不在备份范围。",
        }

    def consume(self, token: str) -> tuple[dict, bytes]:
        item = self.pending.pop(token, None)
        if not item:
            raise MCError(message_text("error_backups_010"))
        path, expires, expected = item
        try:
            if expires < time.time():
                raise MCError(message_text("error_backups_019"))
            data = path.read_bytes()
            if digest(data) != expected:
                raise MCError(message_text("error_backups_020"))
            config, database, _ = self.inspect(data)
            return config, database
        finally:
            path.unlink(missing_ok=True)

    def restore_database(self, database: bytes, sanitize: bool = True):
        with tempfile.TemporaryDirectory(prefix=".restore-db-", dir=self.directory) as temporary:
            path = Path(temporary) / "source.sqlite3"
            path.write_bytes(database)
            validate_database(path)
            with closing(sqlite3.connect(path)) as source, closing(sqlite3.connect(self.database)) as destination:
                source.backup(destination)
                if sanitize:
                    destination.execute("DELETE FROM outbox")
                    destination.execute(
                        "UPDATE jobs SET status='restore_interrupted',detail='恢复备份后旧重启任务不再继续' WHERE status='running'"
                    )
                destination.commit()

    def close(self):
        for path, _, _ in self.pending.values():
            path.unlink(missing_ok=True)
        self.pending.clear()
