from __future__ import annotations

import os
import re
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .spark import DEFAULT_TPS_ERROR, DEFAULT_TPS_TEMPLATE, DEFAULT_TPS_WARNING, SPARK_FIELDS
from .text import safe_text


class MCError(Exception):
    """An actionable message safe to show to users."""


ALIASES = {
    "help": "帮助,help",
    "add": "添加,add",
    "list": "查看,list",
    "remove": "移除,删除,remove",
    "modify": "修改,edit",
    "players": "玩家,players",
    "tps": "tps",
    "status": "状态,status",
    "servers": "服务器,servers",
    "restart": "重启,restart",
    "logs": "日志,logs",
    "sync": "同步,sync",
    "import": "导入,import",
}
MESSAGES = {
    "help": "Minecraft 管理菜单\n{prefix} {add} <玩家名>：添加自己的白名单\n{prefix} {list} [页码]：查看固定编号的白名单\n{prefix} {remove} <编号或玩家名>：移除自己的条目\n{prefix} {modify} <编号或玩家名> <新玩家名>：修改自己的条目\n{prefix} {players} [服务器]：查看在线玩家\n{prefix} {tps} [服务器]：查询 TPS\n{prefix} {status} [服务器]：查看状态\n{prefix} {servers}：查看本群服务器\n{prefix} {restart} <服务器或 all>：重启（管理员）\n{prefix} {logs} [页码]：操作日志（管理员）\n{prefix} {sync}：重试同步（管理员）\n{prefix} {import}：合并导入现有白名单（管理员）",
    "added": "已添加 #{id} {player}，所属用户：{user}\n{sync}",
    "removed": "已移除 #{id} {player}\n{sync}",
    "modified": "已修改 #{id} {old_player} → {player}\n{sync}",
    "list": "本群白名单（第 {page}/{pages} 页，共 {total} 条）\n{entries}",
    "entry": "#{id} {player} · 所属：{owner}",
    "players": "[{server}] 在线玩家：{result}",
    "tps": DEFAULT_TPS_TEMPLATE,
    "tps_warning": DEFAULT_TPS_WARNING,
    "tps_error": DEFAULT_TPS_ERROR,
    "status": "[{server}] {result}",
    "servers": "本群服务器：\n{entries}",
    "error": "MC 操作失败：{error}",
    "logs": "本群操作日志（第 {page}/{pages} 页，共 {total} 条）\n{entries}",
    "log_entry": "{time} | {action} | 群 {group} | 用户 {user} | {player} | {detail}",
    "sync": "{sync}",
    "import": "已导入 {count} 条未托管条目（无归属，限管理员管理）。\n{sync}",
    "restart_started": "正在重启 {servers}，将向本群广播结果。",
    "restart_success": "✅ {server} 重启成功，耗时 {seconds} 秒。{detail}",
    "restart_failed": "❌ {server} 重启失败：{error}",
    "game_restart_success": "服务器 {server} 已重启成功。",
}
FIELDS = set(
    "prefix add list remove modify players tps status servers restart logs sync import help id player user old_player page pages total entries owner result server error time action group detail count seconds".split()
)
FIELDS.update(SPARK_FIELDS)
NAME = re.compile(r"^[A-Za-z0-9_]{3,16}$")
ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def positive(value: Any, label: str, minimum: int = 1, maximum: int = 100000) -> int:
    try:
        if isinstance(value, bool):
            raise ValueError
        number = int(value)
        if str(number) != str(value).strip() or not minimum <= number <= maximum:
            raise ValueError
        return number
    except (TypeError, ValueError):
        raise MCError(f"{label} 必须是 {minimum}～{maximum} 之间的整数。") from None


def boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise MCError(f"{label} 必须使用 JSON 布尔值 true 或 false。")
    return value


def player_name(value: str) -> str:
    if not NAME.fullmatch(value):
        raise MCError("Java 版玩家名必须为 3～16 位字母、数字或下划线。")
    return value


def aliases(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return [x.strip() for x in str(value).replace("，", ",").split(",") if x.strip()]


def group_ids(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (str, int, list)) or isinstance(value, bool):
        raise MCError("群号必须是文本、数字或群号列表，多个群用逗号或换行分隔。")
    values = value if isinstance(value, list) else [value]
    result = []
    for item in values:
        if not isinstance(item, (str, int)) or isinstance(item, bool):
            raise MCError("群号列表只能包含文本或数字。")
        for token in re.split(r"[,，\r\n]+", str(item)):
            token = token.strip()
            if token and (any(c.isspace() or ord(c) < 32 for c in token)):
                raise MCError("单个群号不能包含空白或控制字符。")
            if token and token not in result:
                result.append(token)
    return tuple(result)


@dataclass(frozen=True)
class Actor:
    platform: str
    group: str
    user: str
    name: str = ""
    admin: bool = False
    origin: str = ""


@dataclass(frozen=True)
class Server:
    id: str
    name: str
    directory: Path
    host: str
    port: int
    password: str = field(repr=False)
    online_mode: bool = False
    tps_command: str = "spark tps"
    players_command: str = "list"
    tps_source: str = "auto"
    spark_file: str = ".astrbot-spark-tps.txt"
    tps_mode: str = "spark"


@dataclass(frozen=True)
class Group:
    id: str
    platform: str
    group_id: str
    servers: tuple[str, ...]
    admins: tuple[str, ...] = ()
    max_entries: int = 1
    origin: str = ""

    @property
    def group_ids(self) -> tuple[str, ...]:
        return group_ids(self.group_id)


class Settings:
    def __init__(self, raw: dict):
        if not isinstance(raw, dict):
            raise MCError("配置必须为 JSON 对象。")
        for key in ("servers", "groups"):
            items = raw.get(key, [])
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise MCError(f"{key} 必须是 JSON 对象数组。")
        for key in ("commands", "messages"):
            if not isinstance(raw.get(key, {}), dict):
                raise MCError(f"{key} 必须为 JSON 对象。")
        self.raw = raw
        root = str(raw.get("server_root", "")).strip()
        self.root = Path(root).expanduser().resolve() if root else None
        self.page_size = positive(raw.get("page_size", 10), "每页数量", 1, 100)
        self.sync_interval = positive(raw.get("sync_interval", 30), "同步间隔", 5, 3600)
        self.rcon_timeout = positive(raw.get("rcon_timeout", 5), "RCON 超时", 1, 30)
        self.restart_timeout = positive(raw.get("restart_timeout", 180), "重启超时", 5, 3600)
        self.poll_interval = positive(raw.get("poll_interval", 3), "重启检查间隔", 1, 30)
        self.game_broadcast = boolean(raw.get("game_broadcast", True), "游戏内广播")
        self.tps_missing_value = raw.get("tps_missing_value", "暂无数据")
        if (
            not isinstance(self.tps_missing_value, str)
            or not 1 <= len(self.tps_missing_value) <= 32
            or any(c in self.tps_missing_value for c in "\r\n\x00")
        ):
            raise MCError("TPS 缺失值文字必须为 1～32 字符的单行文本。")
        self.spark_file_max_age = positive(raw.get("spark_file_max_age", 10), "spark 文件最大有效秒数", 3, 300)
        self.commands = dict(ALIASES)
        self.commands.update(raw.get("commands", {}))
        self.prefixes = aliases(self.commands.get("prefix", "mc"))
        self.lookup: dict[str, str] = {}
        if not self.prefixes:
            raise MCError("至少配置一个主指令。")
        for prefix in self.prefixes:
            if prefix.startswith("/") or any(x.isspace() for x in prefix):
                raise MCError("主指令不能包含 / 或空白。")
        for action in ALIASES:
            values = aliases(self.commands[action])
            if not values:
                raise MCError(f"指令 {action} 至少需要一个别名。")
            for value in values:
                key = value.casefold()
                if any(x.isspace() for x in value) or key in self.lookup:
                    raise MCError(f"子指令别名重复或包含空白：{value}")
                self.lookup[key] = action
        self.messages = dict(MESSAGES)
        self.messages.update(raw.get("messages", {}))
        for key, value in self.messages.items():
            if not isinstance(value, str) or len(value) > 16000:
                raise MCError(f"消息模板 {key} 必须为不超过 16000 字符的文本。")
            try:
                for _, token, spec, conversion in string.Formatter().parse(value):
                    if token is not None and (token not in FIELDS or spec or conversion):
                        raise ValueError(token)
            except ValueError:
                raise MCError(f"消息模板 {key} 使用了无效的占位符或花括号。") from None
        self.servers: dict[str, Server] = {}
        occupied: set[Path] = set()
        endpoints: set[tuple[str, int]] = set()
        for item in raw.get("servers", []):
            sid = str(item.get("id", ""))
            if not ID.fullmatch(sid) or sid in self.servers:
                raise MCError("服务器 ID 必须唯一，且只含字母、数字、下划线或横线。")
            if self.root is None:
                raise MCError("请先设置 server_root（服务器共同父目录）。")
            path = (self.root / str(item.get("directory", sid))).resolve()
            if not path.is_relative_to(self.root) or path == self.root or path in occupied:
                raise MCError(f"服务器 {sid} 的目录必须在 server_root 内，且不能重复。")
            occupied.add(path)
            host = str(item.get("rcon_host", "127.0.0.1")).strip()
            port = positive(item.get("rcon_port", 25575), "RCON 端口", 1, 65535)
            endpoint = (host.casefold(), port)
            if endpoint in endpoints:
                raise MCError("不同服务器不能使用相同的 RCON 地址和端口。")
            endpoints.add(endpoint)
            env = str(item.get("rcon_password_env", "")).strip()
            password = os.environ.get(env, "") if env else str(item.get("rcon_password", ""))
            mode = item.get("tps_mode", "spark")
            if not isinstance(mode, str) or mode not in {"spark", "native"}:
                raise MCError(f"服务器 {sid} 的 TPS 查询方式必须为 spark 或 native。")
            default_command = "spark tps" if mode == "spark" else "tps"
            commands = [str(item.get("tps_command", default_command)), str(item.get("players_command", "list"))]
            if not host or any(not c or any(x in c for x in "\n\r\x00") or len(c) > 512 for c in commands):
                raise MCError(f"服务器 {sid} 的 RCON 地址或查询命令无效。")
            source = item.get("tps_source", "auto") if mode == "spark" else "rcon"
            file_name = (
                item.get("spark_file", ".astrbot-spark-tps.txt") if mode == "spark" else ".astrbot-spark-tps.txt"
            )
            if not isinstance(source, str) or source not in {"auto", "rcon", "file"}:
                raise MCError("spark 数据来源必须为 auto、rcon 或 file。")
            if (
                not isinstance(file_name, str)
                or not file_name
                or Path(file_name).is_absolute()
                or not (path / file_name).resolve().is_relative_to(path)
            ):
                raise MCError("spark 指标文件必须是本服目录内的相对路径。")
            self.servers[sid] = Server(
                sid,
                str(item.get("name", sid)),
                path,
                host,
                port,
                password,
                boolean(item.get("online_mode", False), "UUID 模式"),
                *commands,
                source,
                file_name,
                mode,
            )
        self.groups: dict[str, Group] = {}
        bindings: set[tuple[str, str]] = set()
        for group_index, item in enumerate(raw.get("groups", []), start=1):
            gid = str(item.get("id") or "")
            platform = str(item.get("platform_id") or "").strip()
            identities = group_ids(item.get("group_id"))
            group_id = ",".join(identities)
            servers = tuple(aliases(item.get("server_ids", [])))
            missing = []
            if not gid.strip():
                missing.append("群配置唯一 ID（id）")
            if not platform:
                missing.append("平台实例 ID（platform_id，填 /sid 的 Bot ID）")
            if not group_id:
                missing.append("群号（group_id）")
            if missing:
                raise MCError(
                    f"第 {group_index} 条群绑定缺少：{'、'.join(missing)}。请编辑该群绑定，或删除未使用的空白项。"
                )
            if not ID.fullmatch(gid):
                raise MCError(
                    f"第 {group_index} 条群绑定的配置 ID 格式无效，只能使用 1～64 位字母、数字、下划线或横线。"
                )
            if gid in self.groups:
                raise MCError(f"第 {group_index} 条群绑定的配置 ID {gid} 重复，请使用不同 ID 或删除重复项。")
            if any((platform, identity) in bindings for identity in identities):
                raise MCError("同一平台下的群不能重复绑定到多条配置；可在同一配置填写多个群号。")
            if not servers or len(set(servers)) != len(servers):
                raise MCError(f"群 {gid} 的服务器集合为空或重复。")
            for sid in servers:
                if sid not in self.servers:
                    raise MCError(f"服务器 {sid} 不存在。")
            if len({self.servers[s].online_mode for s in servers}) > 1:
                raise MCError(f"群 {gid} 的服务器必须使用相同 UUID 模式。")
            bindings.update((platform, identity) for identity in identities)
            self.groups[gid] = Group(
                gid,
                platform,
                group_id,
                servers,
                tuple(aliases(item.get("admin_ids", []))),
                positive(item.get("max_entries_per_user", 1), "每人白名单上限", 1, 100),
                str(item.get("broadcast_origin", "")),
            )

        self.server_groups = {
            sid: tuple(group for group in self.groups.values() if sid in group.servers) for sid in self.servers
        }

    def related_groups(self, group: Group) -> list[Group]:
        selected = {group.id}
        pending = [group]
        while pending:
            current = pending.pop()
            for sid in current.servers:
                for neighbor in self.server_groups[sid]:
                    if neighbor.id not in selected:
                        selected.add(neighbor.id)
                        pending.append(neighbor)
        return [candidate for candidate in self.groups.values() if candidate.id in selected]

    def group_for(self, actor: Actor) -> Group:
        if not actor.group:
            raise MCError("MC 功能仅允许在绑定的群聊中使用。")
        for group in self.groups.values():
            if group.platform == actor.platform and actor.group in group.group_ids:
                return group
        raise MCError("本群未绑定 MC 服务器，请联系管理员配置。")

    def is_admin(self, actor: Actor, group: Group) -> bool:
        return (
            isinstance(actor.user, str)
            and bool(actor.user.strip())
            and actor.platform == group.platform
            and actor.group in group.group_ids
            and (actor.admin is True or actor.user in group.admins)
        )

    def extract(self, text: str) -> str | None:
        words = text.strip().split(maxsplit=1)
        if not words:
            return None
        prefix = words[0].removeprefix("/").casefold()
        if prefix not in {x.casefold() for x in self.prefixes}:
            return None
        return words[1] if len(words) > 1 else ""

    def route(self, text: str) -> tuple[str, list[str]]:
        words = text.split()
        if not words:
            return "help", []
        action = self.lookup.get(words[0].casefold())
        if not action:
            raise MCError("未知子指令，请发送主指令查看帮助。")
        return action, words[1:]

    def render(self, key: str, **values: Any) -> str:
        context = {k: "" for k in FIELDS}
        context.update({k: aliases(self.commands[k])[0] for k in ALIASES})
        context["prefix"] = "/" + self.prefixes[0]
        context.update({k: safe_text(v) for k, v in values.items()})
        return safe_text(self.messages[key].format_map(context))
