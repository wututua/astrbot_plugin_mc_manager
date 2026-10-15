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
            raise MCError("该正版玩家不存在。")
        data = json.loads(body)
        canonical = player_name(data["name"])
        if canonical.casefold() != name.casefold():
            raise MCError("正版 UUID 查询返回了不同的玩家名。")
        return canonical, str(uuid.UUID(data["id"]))
    except urllib.error.HTTPError as exc:
        if exc.code in (204, 404):
            raise MCError("该正版玩家不存在。") from None
        raise MCError(f"正版 UUID 查询失败（HTTP {exc.code}），请稍后重试。") from None
    except (OSError, ValueError, KeyError):
        raise MCError("正版 UUID 查询失败，请检查联网后重试。") from None


def whitelist_path(server: Server) -> Path:
    directory = server.directory
    if directory.resolve() != directory:
        raise MCError("服务器目录在配置后变为链接，已拒绝访问。")
    if not directory.is_dir():
        raise MCError(f"服务器目录不存在：{server.id}")
    path = directory / "whitelist.json"
    if path.is_symlink() or path.resolve().parent != directory.resolve():
        raise MCError("whitelist.json 不能是指向其他位置的链接。")
    return path


def read_whitelist(server: Server) -> list[dict]:
    path = whitelist_path(server)
    if not path.exists():
        return []
    try:
        if path.stat().st_size > 8 * 1024 * 1024:
            raise MCError("白名单文件超过 8 MB。")
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
                raise MCError("白名单存在重复玩家名或 UUID，请先人工检查。")
            names.add(name.casefold())
            uuids.add(identifier)
            result.append({"name": name, "uuid": identifier})
        return result
    except (ValueError, TypeError, KeyError, AttributeError, OSError):
        raise MCError(f"服务器 {server.id} 的白名单损坏或无法读取，已拒绝覆盖。") from None


def write_whitelist(server: Server, entries: list[dict]) -> bool:
    path = whitelist_path(server)
    existing = read_whitelist(server)
    desired = [{"uuid": row["uuid"], "name": row["name"]} for row in entries]
    if sorted(existing, key=lambda r: r["uuid"]) == sorted(desired, key=lambda r: r["uuid"]) and path.exists():
        return False
    backups = server.directory / ".mcwl-backups"
    if backups.is_symlink():
        raise MCError("备份目录不能是链接。")
    if path.exists():
        backups.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        shutil.copy2(path, backups / f"whitelist-{stamp}.json")
        for old in sorted(backups.glob("whitelist-*.json"))[:-10]:
            old.unlink()
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
