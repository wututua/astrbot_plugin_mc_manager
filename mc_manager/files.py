from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

from .config import MCError, Server, player_name
from .message_catalog import text as message_text


class WhitelistReadError(MCError):
    """Content/read errors which an explicit overwrite policy may recover."""


def offline_uuid(name: str) -> str:
    digest = hashlib.md5(("OfflinePlayer:" + name).encode("utf-8")).digest()
    return str(uuid.UUID(bytes=digest, version=3))


def lookup_uuid(name: str) -> tuple[str, str]:
    player_name(name)
    url = "https://api.mojang.com/users/profiles/minecraft/" + name
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "AstrBot-MC-Manager/1.0"})
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read(8192)
        if not body:
            raise MCError(message_text("error_files_004"))
        data = json.loads(body)
        canonical = player_name(data["name"])
        if canonical.casefold() != name.casefold():
            raise MCError(message_text("error_files_005"))
        return canonical, str(uuid.UUID(data["id"]))
    except urllib.error.HTTPError as exc:
        if exc.code in (204, 404):
            raise MCError(message_text("error_files_004")) from None
        raise MCError(message_text("error_files_006", code=exc.code)) from None
    except (OSError, ValueError, KeyError):
        raise MCError(message_text("error_files_007")) from None


def whitelist_path(server: Server) -> Path:
    directory = server.directory
    if directory.resolve() != directory:
        raise MCError(message_text("error_files_001"))
    if not directory.is_dir():
        raise MCError(message_text("error_files_002", id=server.id))
    path = directory / "whitelist.json"
    if path.is_symlink() or path.resolve().parent != directory.resolve():
        raise MCError(message_text("error_files_003"))
    return path


def read_whitelist(server: Server) -> list[dict]:
    path = whitelist_path(server)
    if not path.exists():
        return []
    try:
        if path.stat().st_size > 8 * 1024 * 1024:
            raise MCError(message_text("error_files_008"))
        entries = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(entries, list):
            raise ValueError
        names = set()
        uuids = set()
        result = []
        for row in entries:
            name = player_name(row["name"])
            identifier = str(uuid.UUID(row["uuid"]))
            if name.casefold() in names or identifier in uuids:
                raise MCError(message_text("error_files_010"))
            names.add(name.casefold())
            uuids.add(identifier)
            result.append({"name": name, "uuid": identifier})
        return result
    except MCError as exc:
        raise WhitelistReadError(exc) from None
    except (ValueError, TypeError, KeyError, AttributeError, OSError):
        raise WhitelistReadError(message_text("error_files_009", id=server.id)) from None


def read_initial_whitelist(server: Server) -> list[dict]:
    try:
        return read_whitelist(server)
    except WhitelistReadError:
        if not server.whitelist_overwrite:
            raise
        # Still require the original file to be backed up before disk_write.
        # Explicit manual imports and diagnostics continue to report the error.
        return []


def write_whitelist(server: Server, entries: list[dict]) -> bool:
    path = whitelist_path(server)
    try:
        existing = read_whitelist(server)
    except WhitelistReadError:
        if not server.whitelist_overwrite:
            raise
        existing = None
    desired = [{"uuid": row["uuid"], "name": row["name"]} for row in entries]
    if (
        existing is not None
        and sorted(existing, key=lambda r: r["uuid"]) == sorted(desired, key=lambda r: r["uuid"])
        and path.exists()
    ):
        return False
    backups = server.directory / ".mcwl-backups"
    if backups.is_symlink():
        raise MCError(message_text("error_backups_004"))
    if path.exists():
        try:
            backups.mkdir(exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            shutil.copy2(path, backups / f"whitelist-{stamp}.json")
            for old in sorted(backups.glob("whitelist-*.json"))[:-10]:
                old.unlink()
        except OSError as exc:
            raise MCError(message_text("error_files_011", exc=exc)) from None
    fd, temporary = tempfile.mkstemp(prefix=".mcwl-", suffix=".tmp", dir=server.directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
            json.dump(desired, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        if path.exists():
            shutil.copymode(path, temporary)
            if hasattr(os, "chown"):
                stat = path.stat()
                try:
                    os.chown(temporary, stat.st_uid, stat.st_gid)
                except PermissionError:
                    pass
        else:
            os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True
