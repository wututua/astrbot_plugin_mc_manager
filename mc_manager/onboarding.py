"""Bounded, read-only server discovery and short-lived credential-safe setup plans."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import os
import re
import secrets
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import MCError, Settings, group_ids, positive
from .message_catalog import text as message_text

MAX_FILES = 64
MAX_ENTRIES = 2048
MAX_BYTES = 256 * 1024
MAX_PLANS = 5
PLAN_TTL = 600
SKIP = {
    "world",
    "world_nether",
    "world_the_end",
    "plugins",
    "mods",
    "libraries",
    "logs",
    "backups",
    "versions",
    "crash-reports",
}
SPACE = " \t\f"


def _unescape(value):
    result = []
    index = 0
    while index < len(value):
        char = value[index]
        index += 1
        if char != "\\":
            result.append(char)
            continue
        if index == len(value):
            break
        char = value[index]
        index += 1
        if char == "u":
            digits = value[index : index + 4]
            if len(digits) != 4 or not re.fullmatch(r"[0-9a-fA-F]{4}", digits):
                raise ValueError("invalid unicode escape")
            result.append(chr(int(digits, 16)))
            index += 4
        else:
            result.append({"t": "\t", "r": "\r", "n": "\n", "f": "\f"}.get(char, char))
    return "".join(result).encode("utf-16-le", "surrogatepass").decode("utf-16-le")


def parse_properties(data: bytes) -> dict[str, str]:
    """Java Properties separators, escaped keys/values and continuation lines."""
    try:
        source = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        source = data.decode("latin-1")
    result = {}
    logical = ""
    continuing = False
    lines = re.split(r"\r\n|\r|\n", source)
    for natural in [*lines, ""]:
        line = natural.lstrip(SPACE)
        if not continuing and (not line or line.startswith(("#", "!"))):
            continue
        logical += line
        slashes = len(logical) - len(logical.rstrip("\\"))
        if slashes % 2:
            logical = logical[:-1]
            continuing = True
            continue
        continuing = False
        index = 0
        escaped = False
        while index < len(logical):
            char = logical[index]
            if not escaped and (char in "=:" or char in SPACE):
                break
            escaped = not escaped if char == "\\" else False
            index += 1
        key = _unescape(logical[:index])
        rest = logical[index:].lstrip(SPACE)
        if rest.startswith(("=", ":")):
            rest = rest[1:].lstrip(SPACE)
        result[key] = _unescape(rest)
        logical = ""
    return result


def _linked(path):
    return path.is_symlink() or path.is_junction()


def _read_properties(root, path):
    try:
        if root.resolve() != root or _linked(path) or not path.resolve().is_relative_to(root):
            raise MCError(message_text("error_setup_path"))
        cursor = path.parent
        while cursor != root:
            if _linked(cursor):
                raise MCError(message_text("error_setup_path"))
            cursor = cursor.parent
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BYTES:
            raise MCError(message_text("error_setup_properties"))
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino):
                raise MCError(message_text("error_setup_properties"))
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
                raise MCError(message_text("error_setup_properties"))
            data = stream.read(MAX_BYTES + 1)
            after = os.fstat(stream.fileno())
        if len(data) > MAX_BYTES or (info.st_mtime_ns, info.st_size) != (after.st_mtime_ns, after.st_size):
            raise MCError(message_text("error_setup_properties"))
        return data, (info.st_dev, info.st_ino, info.st_mtime_ns, hashlib.sha256(data).hexdigest())
    except OSError:
        raise MCError(message_text("error_setup_properties")) from None


@dataclass
class DiscoveredServer:
    key: str
    path: Path
    fingerprint: tuple | None
    config: dict = field(repr=False)
    issues: list[str] = field(default_factory=list)
    ready: bool = False
    configured: bool = False
    whitelist_enabled: bool = False

    def public(self):
        return {
            "key": self.key,
            "name": self.config.get("name", self.path.parent.name),
            "directory": self.config.get("directory", self.key),
            "rcon_port": self.config.get("rcon_port", 25575),
            "rcon_host": self.config.get("rcon_host", "127.0.0.1"),
            "online_mode": self.config.get("online_mode", False),
            "ready": self.ready,
            "configured": self.configured,
            "whitelist_enabled": self.whitelist_enabled,
            "issues": self.issues,
        }


@dataclass
class Discovery:
    root: Path
    records: list[DiscoveredServer] = field(repr=False)
    single_server: bool = False
    warnings: list[str] = field(default_factory=list)


def discover_servers(raw_root, config) -> Discovery:
    if not isinstance(raw_root, str) or not raw_root.strip() or len(raw_root) > 4096:
        raise MCError(message_text("error_setup_root"))
    try:
        original = Path(raw_root.strip()).expanduser()
        if _linked(original) or not original.is_dir():
            raise MCError(message_text("error_setup_root"))
        original = original.resolve()
        single = (original / "server.properties").exists()
        root = original.parent if single else original
        if single and root == original:
            raise MCError(message_text("error_setup_root"))
        paths = []
        warnings = []
        pending = [(original, 0)]
        visited = 0
        while pending:
            directory, depth = pending.pop()
            properties = directory / "server.properties"
            if properties.exists() or properties.is_symlink():
                paths.append(properties)
                if len(paths) > MAX_FILES:
                    raise MCError(message_text("error_setup_limit"))
                continue
            if depth >= 2:
                continue
            try:
                with os.scandir(directory) as entries:
                    children = []
                    for entry in entries:
                        visited += 1
                        if visited > MAX_ENTRIES:
                            raise MCError(message_text("error_setup_limit"))
                        if entry.name.startswith(".") or entry.name.casefold() in SKIP:
                            continue
                        path = Path(entry.path)
                        if entry.is_dir(follow_symlinks=False) and not _linked(path):
                            children.append(path)
            except PermissionError:
                if directory == original:
                    raise
                if len(warnings) < 16:
                    warnings.append("无法读取子目录：" + str(directory.relative_to(original))[:120])
                elif len(warnings) == 16:
                    warnings.append("还有其他子目录无法读取，请检查文件权限")
                continue
            pending.extend((child, depth + 1) for child in sorted(children, reverse=True))
    except (OSError, RuntimeError, ValueError):
        raise MCError(message_text("error_setup_root")) from None
    existing = {}
    old_root = str(config.get("server_root", "")).strip()
    if old_root and isinstance(config.get("servers", []), list):
        for item in config.get("servers", []):
            if isinstance(item, dict):
                existing[(Path(old_root).expanduser() / str(item.get("directory", item.get("id", "")))).resolve()] = (
                    item
                )
    records = []
    for path in sorted(paths):
        relative = path.parent.relative_to(root).as_posix()
        saved = existing.get(path.parent)
        record = DiscoveredServer(relative, path, None, {"name": path.parent.name, "directory": relative})
        try:
            data, record.fingerprint = _read_properties(root, path)
            props = parse_properties(data)
            record.whitelist_enabled = props.get("white-list", "false").lower() == "true"
            if saved is not None:
                record.config = copy.deepcopy(saved)
                record.configured = record.ready = True
            else:
                enabled = props.get("enable-rcon", "false").lower() == "true"
                password = props.get("rcon.password", "")
                port = props.get("rcon.port", "25575")
                mode = props.get("online-mode", "true").lower()
                valid_port = bool(re.fullmatch(r"[0-9]{1,5}", port)) and 1 <= int(port) <= 65535
                valid_password = (
                    bool(password)
                    and password != "__KEEP__"
                    and len(password) <= 4096
                    and not any(c in password for c in "\r\n\x00")
                )
                valid_mode = mode in {"true", "false"}
                record.config.update(
                    rcon_host="127.0.0.1",
                    rcon_port=int(port) if valid_port else None,
                    rcon_password=password if valid_password else "",
                    online_mode=mode == "true",
                    tps_command="spark tps",
                    tps_auto_detect=True,
                    tps_output="template",
                )
                record.ready = enabled and valid_port and valid_password and valid_mode
                if not enabled:
                    record.issues.append("enable-rcon 未开启")
                if not valid_port:
                    record.issues.append("rcon.port 不是有效端口")
                if not valid_password:
                    record.issues.append("rcon.password 未填写或格式无效")
                if not valid_mode:
                    record.issues.append("online-mode 必须为 true 或 false")
                if "online-mode" not in props:
                    record.issues.append("未设置 online-mode，已按正版模式预选，请核对")
            if not record.whitelist_enabled:
                record.issues.append("white-list 未开启，游戏不会按白名单限制入服")
        except (MCError, ValueError, UnicodeError):
            record.config = {"name": path.parent.name, "directory": relative}
            record.issues = ["server.properties 无法读取或格式无效，请使用手动配置"]
            record.ready = False
        records.append(record)
    endpoints = {}
    for record in records:
        if record.ready:
            try:
                port = positive(record.config.get("rcon_port", 25575), "RCON 端口", 1, 65535)
            except MCError:
                record.ready = False
                record.issues.append("已保存的 RCON 端口需要修正，请使用手动配置")
                continue
            if not isinstance(record.config.get("online_mode", False), bool):
                record.ready = False
                record.issues.append("已保存的 UUID 模式需要修正，请使用手动配置")
                continue
            endpoints.setdefault((str(record.config.get("rcon_host", "127.0.0.1")).casefold(), port), []).append(record)
    for duplicates in endpoints.values():
        if len(duplicates) > 1:
            for record in duplicates:
                record.ready = False
                record.issues.append("RCON 地址和端口与其他扫描结果重复，请先修改端口或手动添加")
    return Discovery(root, records, single, warnings)


@dataclass
class SetupPlan:
    discovery: Discovery = field(repr=False)
    owner: str
    revision: str
    created: float


class SetupPlans:
    def __init__(self):
        self._plans: dict[str, SetupPlan] = {}
        self._timers = {}
        self._loop = None

    def expire(self, token):
        self._plans.pop(token, None)
        handle = self._timers.pop(token, None)
        if handle is not None:
            try:
                current_loop = asyncio.get_running_loop()
            except RuntimeError:
                current_loop = None
            if self._loop is not None and current_loop is not self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(handle.cancel)
            else:
                handle.cancel()

    def clear(self):
        for token in list(self._plans):
            self.expire(token)

    def issue(self, discovery, owner, revision):
        now = time.monotonic()
        for key, value in list(self._plans.items()):
            if now - value.created >= PLAN_TTL:
                self.expire(key)
        while len(self._plans) >= MAX_PLANS:
            self.expire(next(iter(self._plans)))
        token = secrets.token_urlsafe(24)
        self._plans[token] = SetupPlan(discovery, owner, revision, now)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            self._loop = loop
            self._timers[token] = loop.call_later(PLAN_TTL, self.expire, token)
        return {
            "token": token,
            "revision": revision,
            "server_root": str(discovery.root),
            "single_server": discovery.single_server,
            "servers": [row.public() for row in discovery.records],
            "warnings": discovery.warnings,
        }

    def prepare(self, payload, owner, revision, config, bindings):
        token = payload.get("token")
        plan = self._plans.get(token) if isinstance(token, str) else None
        if (
            plan is None
            or plan.owner != owner
            or plan.revision != revision
            or time.monotonic() - plan.created >= PLAN_TTL
        ):
            if plan is not None and plan.owner == owner:
                self.expire(token)
            raise MCError(message_text("error_setup_expired"))
        selected = payload.get("selected")
        if (
            not isinstance(selected, list)
            or not 1 <= len(selected) <= MAX_FILES
            or any(not isinstance(key, str) for key in selected)
            or len(set(selected)) != len(selected)
        ):
            raise MCError(message_text("error_setup_selection"))
        choices = {row.key: row for row in plan.discovery.records}
        if any(key not in choices or not choices[key].ready for key in selected):
            raise MCError(message_text("error_setup_selection"))
        raw = copy.deepcopy(config)
        if any(
            not isinstance(raw.get(key, []), list) or any(not isinstance(item, dict) for item in raw.get(key, []))
            for key in ("servers", "groups")
        ):
            raise MCError(message_text("error_setup_existing"))
        root = plan.discovery.root
        if raw.get("servers") and Path(str(raw.get("server_root", ""))).expanduser().resolve() != root:
            raise MCError(message_text("error_setup_different_root"))
        platform = payload.get("platform_id")
        if (
            not isinstance(platform, str)
            or not platform.strip()
            or len(platform) > 512
            or any(ord(c) < 32 for c in platform)
        ):
            raise MCError(message_text("error_setup_platform"))
        group_input = payload.get("group_id")
        if isinstance(group_input, str) and len(group_input) > 4096:
            raise MCError(message_text("error_setup_group"))
        if isinstance(group_input, list) and len(group_input) > 64:
            raise MCError(message_text("error_setup_group"))
        identities = group_ids(group_input)
        if not identities or len(identities) > 64 or any(len(identity) > 128 for identity in identities):
            raise MCError(message_text("error_setup_group"))
        host = payload.get("rcon_host", "127.0.0.1")
        if not isinstance(host, str) or not host.strip() or len(host) > 255 or any(ord(c) < 32 for c in host):
            raise MCError(message_text("error_setup_host"))
        raw["server_root"] = str(root)
        servers = raw.setdefault("servers", [])
        groups = raw.setdefault("groups", [])
        reserved_servers = (
            {str(item.get("id")) for item in servers}
            | set(bindings.get("reserved_servers", []))
            | {str(item.get("server")) for item in bindings.get("servers", [])}
        )
        reserved_groups = {str(item.get("id")) for item in groups} | {
            str(item.get("id")) for item in bindings.get("groups", [])
        }

        def unused(prefix, reserved):
            index = 1
            while prefix + str(index) in reserved:
                index += 1
            value = prefix + str(index)
            reserved.add(value)
            return value

        identifiers = []
        added = 0
        for key in selected:
            record = choices[key]
            _, fingerprint = _read_properties(root, record.path)
            if fingerprint != record.fingerprint:
                raise MCError(message_text("error_setup_changed"))
            if record.configured:
                current = next((item for item in servers if item.get("id") == record.config.get("id")), None)
                if current is None:
                    raise MCError(message_text("error_setup_expired"))
                identifier = str(current["id"])
            else:
                item = copy.deepcopy(record.config)
                identifier = item["id"] = unused("server", reserved_servers)
                item["__template_key"] = "server"
                item["rcon_host"] = host.strip()
                servers.append(item)
                added += 1
            identifiers.append(identifier)
        overlaps = [
            group
            for group in groups
            if str(group.get("platform_id", "")).strip() == platform.strip()
            and set(group_ids(group.get("group_id"))) & set(identities)
        ]
        if len(overlaps) > 1:
            raise MCError(message_text("error_setup_group_overlap"))
        if overlaps:
            group = overlaps[0]
            if set(group_ids(group["group_id"])) != set(identities):
                raise MCError(message_text("error_setup_shared_group", groups=",".join(group_ids(group["group_id"]))))
            group["group_id"] = ",".join(identities)
            previous = group.get("server_ids", [])
            if isinstance(previous, str):
                previous = [part.strip() for part in re.split(r"[,，\r\n]+", previous) if part.strip()]
            group["server_ids"] = list(dict.fromkeys([*previous, *identifiers]))
        else:
            group = {
                "__template_key": "group",
                "id": unused("group", reserved_groups),
                "platform_id": platform.strip(),
                "group_id": ",".join(identities),
                "server_ids": identifiers,
            }
            groups.append(group)
        if token not in self._plans or time.monotonic() - plan.created >= PLAN_TTL:
            self.expire(token)
            raise MCError(message_text("error_setup_expired"))
        Settings(raw)
        return raw, {
            "added": added,
            "servers": identifiers,
            "group_id": group["id"],
            "group_ids": group_ids(group["group_id"]),
        }
