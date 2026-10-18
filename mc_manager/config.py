from __future__ import annotations

import os
import re
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .message_catalog import EXTRA_MESSAGES
from .message_catalog import text as message_text
from .monitoring import MONITOR_FIELDS
from .performance import PERFORMANCE_FIELDS
from .spark import DEFAULT_TPS_ERROR, DEFAULT_TPS_TEMPLATE, DEFAULT_TPS_WARNING, SPARK_FIELDS
from .text import safe_text


class MCError(Exception):
    """An actionable error with canonical text and an optional message template."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message.message if isinstance(message, MCError) else message

    def localize(self, settings, depth=0):
        if hasattr(self.message, "localize"):
            return self.message.localize(settings, depth + 1)
        return settings.render("error_unknown", error=str(self), _depth=depth + 1, limit=None)


ALIASES = {
    "diagnose": "诊断,diagnose",
    "backup": "备份,backup",
    "cancel": "取消重启,cancel",
    "requests": "申请,requests",
    "approve": "通过,approve",
    "reject": "拒绝,reject",
    "mine": "我的,mine",
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
    "bridge_to_game": "§b[群 {group}] §f{nickname}§7: §r{message}",
    "bridge_chat": "[{server}] <{player}> {message}",
    "bridge_join": "[{server}] {player} 加入了游戏",
    "bridge_leave": "[{server}] {player} 离开了游戏",
    "bridge_death": "[{server}] {message}",
    "diagnostics": "MC 诊断\n{result}",
    "requests": "待审核申请\n{entries}",
    "backup_created": "已创建插件数据备份：{result}\n请在管理网页下载，备份包含配置凭据。",
    "restart_countdown": "⏳ {server} 将在 {seconds} 秒后重启，请及时保存进度。",
    "restart_cancelled": "已取消 {server} 的重启倒计时。",
    "request_created": "白名单申请 #{id} 已提交：{player}，等待管理员审核。",
    "request_approved": "已通过申请 #{id}：{player}\n{sync}",
    "request_rejected": "已拒绝申请 #{id}：{detail}",
    "alert": "⚠️ [{server}] {alert_type}：{alert_detail}",
    "alert_recovery": "✅ [{server}] {alert_type} 已恢复：{alert_detail}",
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
MESSAGES["help"] += (
    "\n{prefix} {mine}：查看自己的条目\n{prefix} {diagnose} [服务器]：诊断（管理员）\n{prefix} {requests}：查看待审核申请\n{prefix} {approve} <申请编号>：通过申请（管理员）\n{prefix} {reject} <申请编号> [原因]：拒绝申请（管理员）\n{prefix} {cancel} <服务器>：取消倒计时（重启管理员）\n{prefix} {backup}：创建插件备份（全局管理员）"
)
MESSAGES["help_full"] = MESSAGES["help"]
MESSAGES["help"] = (
    "Minecraft 管理菜单\n"
    "{prefix} {add} <玩家名>：添加白名单\n"
    "{prefix} {mine}：查看自己的白名单\n"
    "{prefix} {remove} [编号或玩家名]：移除自己的白名单\n"
    "{prefix} {modify} [编号或原名] <新玩家名>：修改自己的白名单\n"
    "{prefix} {players} [服务器]：在线玩家\n"
    "{prefix} {tps} [服务器]：服务器性能\n"
    "{prefix} {help} all：全部指令"
)
MESSAGES.update(EXTRA_MESSAGES)
FIELDS = set(
    "prefix add list remove modify players tps status servers restart logs sync import help id player user old_player page pages total entries owner result server error time action group detail count seconds".split()
)
FIELDS.update(
    token for template in EXTRA_MESSAGES.values() for _, token, _, _ in string.Formatter().parse(template) if token
)
FIELDS.update(SPARK_FIELDS)
FIELDS.update(MONITOR_FIELDS)
FIELDS.update(PERFORMANCE_FIELDS)
FIELDS.update(ALIASES)
FIELDS.update({"alert_type", "alert_detail", "nickname", "message", "server_id", "event"})
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
        raise MCError(message_text("error_config_004", label=label, minimum=minimum, maximum=maximum)) from None


def boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise MCError(message_text("error_config_001", label=label))
    return value


def player_name(value: str) -> str:
    if not NAME.fullmatch(value):
        raise MCError(message_text("error_config_002"))
    return value


def aliases(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return [x.strip() for x in str(value).replace("，", ",").split(",") if x.strip()]


def server_ids(value: Any) -> tuple[str, ...]:
    """Normalize only server references; commas inside account IDs remain literal."""
    if value is None:
        return ()
    values = value if isinstance(value, list) else [value]
    result = []
    for item in values:
        if not isinstance(item, (str, int)) or isinstance(item, bool):
            raise MCError(message_text("error_config_005"))
        for token in re.split(r"[,，\r\n]+", str(item)):
            token = token.strip()
            if not token:
                continue
            if not ID.fullmatch(token):
                raise MCError(message_text("error_config_010"))
            result.append(token)
    return tuple(result)


def group_ids(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (str, int, list)) or isinstance(value, bool):
        raise MCError(message_text("error_config_003"))
    values = value if isinstance(value, list) else [value]
    result = []
    for item in values:
        if not isinstance(item, (str, int)) or isinstance(item, bool):
            raise MCError(message_text("error_config_006"))
        for token in re.split(r"[,，\r\n]+", str(item)):
            token = token.strip()
            if token and (any(c.isspace() or ord(c) < 32 for c in token)):
                raise MCError(message_text("error_config_011"))
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
    restart_admins: tuple[str, ...] = ()
    bridge_log_path: str = "logs/latest.log"
    bridge_log_encoding: str = "utf-8"
    whitelist_overwrite: bool = False
    tps_auto_detect: bool = False
    spark_log_fallback: bool = True
    spark_log_path: str = "logs/latest.log"
    spark_log_encoding: str = "utf-8"
    spark_log_wait_seconds: int = 3
    tps_output: str = "template"


@dataclass(frozen=True)
class Group:
    id: str
    platform: str
    group_id: str
    servers: tuple[str, ...]
    admins: tuple[str, ...] = ()
    max_entries: int = 1
    origin: str = ""
    whitelist_admins: tuple[str, ...] = ()
    restart_admins: tuple[str, ...] | None = None
    approval_required: bool = False
    bridge_enabled: bool = False
    bridge_to_game: bool = True
    bridge_to_group: bool = True
    bridge_chat: bool = True
    bridge_join: bool = True
    bridge_leave: bool = True
    bridge_death: bool = True
    bridge_prefix: str = ""

    @property
    def group_ids(self) -> tuple[str, ...]:
        return group_ids(self.group_id)


def migrate_server_config(server):
    if not isinstance(server, dict):
        return server
    result = {key: value for key, value in server.items() if key not in {"tps_source", "spark_file", "tps_mode"}}
    # A missing command used to depend on the selector. Materialize that exact
    # default before dropping it so upgrades preserve the effective command.
    result.setdefault("tps_command", "tps" if server.get("tps_mode") == "native" else "spark tps")
    return result


def without_collector_config(raw: dict) -> dict:
    """Migrate retired fields while preserving effective commands and other data."""
    cleaned = dict(raw)
    cleaned.pop("spark_file_max_age", None)
    if isinstance(cleaned.get("servers"), list):
        cleaned["servers"] = [migrate_server_config(server) for server in cleaned["servers"]]
    return cleaned


class Settings:
    def __init__(self, raw: dict):
        if not isinstance(raw, dict):
            raise MCError(message_text("error_config_007"))
        raw = without_collector_config(raw)
        for key in ("servers", "groups"):
            items = raw.get(key, [])
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise MCError(message_text("error_config_012", value1=key))
        for key in ("commands", "messages"):
            if not isinstance(raw.get(key, {}), dict):
                raise MCError(message_text("error_config_013", value1=key))
        self.raw = raw
        self.bridge_enabled = boolean(raw.get("bridge_enabled", False), "消息互通总开关")
        self.bridge_poll_seconds = positive(raw.get("bridge_poll_seconds", 1), "消息日志读取间隔（秒）", 1, 60)
        self.bridge_max_chars = positive(raw.get("bridge_max_chars", 500), "互通消息最大字符数", 32, 2000)
        self.bridge_cooldown_seconds = positive(raw.get("bridge_cooldown_seconds", 2), "群消息转发冷却（秒）", 0, 300)
        self.notification_max_attempts = positive(raw.get("notification_max_attempts", 5), "广播最大重试次数", 1, 100)
        self.notification_retry_seconds = positive(
            raw.get("notification_retry_seconds", 30), "广播重试基础间隔（秒）", 0, 3600
        )
        self.notification_max_age_hours = positive(
            raw.get("notification_max_age_hours", 24), "广播有效期（小时）", 1, 720
        )
        self.background_retry_seconds = positive(
            raw.get("background_retry_seconds", 5), "后台异常重试间隔（秒）", 1, 300
        )
        self.disk_warning_mb = positive(raw.get("disk_warning_mb", 512), "磁盘空间告警阈值（MB，0 关闭）", 0, 1048576)
        self.backup_interval_hours = positive(
            raw.get("backup_interval_hours", 0), "定时备份间隔（小时，0 关闭）", 0, 8760
        )
        self.backup_keep = positive(raw.get("backup_keep", 7), "备份保留数量", 1, 100)
        self.logs_retention_days = positive(raw.get("logs_retention_days", 0), "日志保留天数（0 不限制）", 0, 3650)
        self.logs_max_entries = positive(raw.get("logs_max_entries", 0), "日志最多保留条数（0 不限制）", 0, 10000000)
        self.alert_consecutive = positive(raw.get("alert_consecutive", 3), "告警连续异常次数", 1, 100)
        self.alert_cooldown = positive(raw.get("alert_cooldown", 900), "告警重复通知间隔（秒）", 10, 86400)
        self.alert_tps_threshold = positive(raw.get("alert_tps_threshold", 15), "TPS 告警阈值", 1, 20)
        self.query_cache_seconds = positive(raw.get("query_cache_seconds", 3), "查询缓存时间（秒，0 关闭）", 0, 60)
        self.query_user_cooldown = positive(raw.get("query_user_cooldown", 0), "用户查询冷却（秒，0 关闭）", 0, 300)
        self.query_group_cooldown = positive(raw.get("query_group_cooldown", 0), "群查询冷却（秒，0 关闭）", 0, 300)
        self.query_timeout_seconds = positive(raw.get("query_timeout_seconds", 0), "查询总超时（秒，0 自动）", 0, 300)
        self.tps_capability_cache_seconds = positive(
            raw.get("tps_capability_cache_seconds", 600), "TPS 指令能力缓存（秒）", 0, 86400
        )
        self.query_concurrency = positive(raw.get("query_concurrency", 8), "RCON 查询并发上限", 1, 64)
        self.restart_countdown = positive(raw.get("restart_countdown", 0), "重启前倒计时（秒，0 立即重启）", 0, 3600)
        self.alerts_enabled = boolean(raw.get("alerts_enabled", False), "主动告警")
        self.approval_required = boolean(raw.get("approval_required", False), "白名单申请审核")
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
            raise MCError(message_text("error_config_008"))
        self.commands = dict(ALIASES)
        self.commands.update(raw.get("commands", {}))
        self.prefixes = aliases(self.commands.get("prefix", "mc"))
        self.lookup: dict[str, str] = {}
        if not self.prefixes:
            raise MCError(message_text("error_config_009"))
        for prefix in self.prefixes:
            if prefix.startswith("/") or any(x.isspace() for x in prefix):
                raise MCError(message_text("error_config_014"))
        new_actions = {"diagnose", "backup", "cancel", "requests", "approve", "reject", "mine"}
        reserved = {
            value.casefold()
            for key in ALIASES
            if key not in new_actions or self.commands[key] != ALIASES[key]
            for value in aliases(self.commands[key])
        }
        for action in ALIASES:
            values = aliases(self.commands[action])
            if action in new_actions and self.commands[action] == ALIASES[action]:
                values = [value for value in values if value.casefold() not in reserved]
                if not values:
                    fallback = "mc_" + action
                    while fallback.casefold() in reserved:
                        fallback += "_"
                    values = [fallback]
                self.commands[action] = ",".join(values)
            if not values:
                raise MCError(message_text("error_config_015", action=action))
            for value in values:
                key = value.casefold()
                if any(x.isspace() for x in value) or key in self.lookup:
                    raise MCError(message_text("error_config_036", value=value))
                self.lookup[key] = action
        self.template_fields = frozenset(FIELDS)
        self.messages = dict(MESSAGES)
        self.messages.update(raw.get("messages", {}))
        for key, value in self.messages.items():
            if not isinstance(value, str) or len(value) > 16000:
                raise MCError(message_text("error_config_016", value1=key))
            try:
                for _, token, spec, conversion in string.Formatter().parse(value):
                    if token is not None and (token not in FIELDS or spec or conversion):
                        raise ValueError(token)
            except ValueError:
                raise MCError(message_text("error_config_037", value1=key)) from None
        self.servers: dict[str, Server] = {}
        occupied: set[Path] = set()
        endpoints: set[tuple[str, int]] = set()
        for item in raw.get("servers", []):
            sid = str(item.get("id", ""))
            if not ID.fullmatch(sid) or sid in self.servers:
                raise MCError(message_text("error_config_017"))
            if self.root is None:
                raise MCError(message_text("error_config_018"))
            path = (self.root / str(item.get("directory", sid))).resolve()
            if not path.is_relative_to(self.root) or path == self.root or path in occupied:
                raise MCError(message_text("error_config_019", sid=sid))
            occupied.add(path)
            host = str(item.get("rcon_host", "127.0.0.1")).strip()
            port = positive(item.get("rcon_port", 25575), "RCON 端口", 1, 65535)
            endpoint = (host.casefold(), port)
            if endpoint in endpoints:
                raise MCError(message_text("error_config_020"))
            endpoints.add(endpoint)
            env = str(item.get("rcon_password_env", "")).strip()
            password = os.environ.get(env, "") if env else str(item.get("rcon_password", ""))
            output = item.get("tps_output", "template")
            if not isinstance(output, str) or output not in {"template", "raw"}:
                raise MCError(message_text("error_config_022", sid=sid))
            item["tps_output"] = output
            commands = [str(item.get("tps_command", "spark tps")), str(item.get("players_command", "list"))]
            if not host or any(not c or any(x in c for x in "\n\r\x00") or len(c) > 512 for c in commands):
                raise MCError(message_text("error_config_023", sid=sid))
            owners = tuple(aliases(item.get("restart_admin_ids", [])))
            if any(
                ":" not in value or not all(value.rsplit(":", 1)) or any(ord(char) < 32 for char in value)
                for value in owners
            ):
                raise MCError(message_text("error_config_024"))
            log_path = item.get("bridge_log_path", "logs/latest.log")
            if (
                not isinstance(log_path, str)
                or not log_path.strip()
                or any(ord(char) < 32 for char in log_path)
                or Path(log_path).is_absolute()
                or not (path / log_path).resolve().is_relative_to(path)
                or (path / log_path).resolve() == path
            ):
                raise MCError(message_text("error_config_025"))
            encoding = item.get("bridge_log_encoding", "utf-8")
            if encoding not in ("utf-8", "gb18030"):
                raise MCError(message_text("error_config_026"))
            spark_log_path = item.get("spark_log_path", "logs/latest.log")
            if (
                not isinstance(spark_log_path, str)
                or not spark_log_path.strip()
                or any(ord(char) < 32 for char in spark_log_path)
                or Path(spark_log_path).is_absolute()
                or not (path / spark_log_path).resolve().is_relative_to(path)
                or (path / spark_log_path).resolve() == path
            ):
                raise MCError(message_text("error_config_027"))
            spark_log_encoding = item.get("spark_log_encoding", "utf-8")
            if spark_log_encoding not in ("utf-8", "gb18030"):
                raise MCError(message_text("error_config_028"))
            self.servers[sid] = Server(
                sid,
                str(item.get("name", sid)),
                path,
                host,
                port,
                password,
                boolean(item.get("online_mode", False), "UUID 模式"),
                *commands,
                owners,
                log_path,
                encoding,
                boolean(item.get("whitelist_overwrite", False), "允许直接覆盖白名单文件"),
                boolean(item.get("tps_auto_detect", False), "TPS 指令自动探测"),
                boolean(item.get("spark_log_fallback", True), "spark 新增日志回读"),
                spark_log_path,
                spark_log_encoding,
                positive(item.get("spark_log_wait_seconds", 3), "spark 日志等待秒数", 1, 15),
                output,
            )
        self.groups: dict[str, Group] = {}
        bindings: set[tuple[str, str]] = set()
        for group_index, item in enumerate(raw.get("groups", []), start=1):
            gid = str(item.get("id") or "")
            platform = str(item.get("platform_id") or "").strip()
            identities = group_ids(item.get("group_id"))
            group_id = ",".join(identities)
            servers = server_ids(item.get("server_ids", []))
            missing = []
            if not gid.strip():
                missing.append("群配置唯一 ID（id）")
            if not platform:
                missing.append("平台实例 ID（platform_id，填 /sid 的 Bot ID）")
            if not group_id:
                missing.append("群号（group_id）")
            if missing:
                raise MCError(message_text("error_config_029", group_index=group_index, value2="、".join(missing)))
            if not ID.fullmatch(gid):
                raise MCError(message_text("error_config_030", group_index=group_index))
            if gid in self.groups:
                raise MCError(message_text("error_config_031", group_index=group_index, gid=gid))
            if any((platform, identity) in bindings for identity in identities):
                raise MCError(message_text("error_config_032"))
            if not servers or len(set(servers)) != len(servers):
                raise MCError(message_text("error_config_033", gid=gid))
            for sid in servers:
                if sid not in self.servers:
                    raise MCError(message_text("error_config_038", gid=gid, sid=sid))
            if len({self.servers[s].online_mode for s in servers}) > 1:
                raise MCError(message_text("error_config_034", gid=gid))
            bindings.update((platform, identity) for identity in identities)
            bridge_prefix = item.get("bridge_prefix", "")
            if (
                not isinstance(bridge_prefix, str)
                or len(bridge_prefix) > 32
                or any(char.isspace() or ord(char) < 32 for char in bridge_prefix)
            ):
                raise MCError(message_text("error_config_035"))
            self.groups[gid] = Group(
                gid,
                platform,
                group_id,
                servers,
                tuple(aliases(item.get("admin_ids", []))),
                positive(item.get("max_entries_per_user", 1), "每人白名单上限", 1, 100),
                str(item.get("broadcast_origin", "")),
                tuple(aliases(item.get("whitelist_admin_ids", []))),
                tuple(aliases(item["restart_admin_ids"])) if "restart_admin_ids" in item else None,
                self.approval_required or boolean(item.get("approval_required", False), "本组申请审核"),
                *(
                    boolean(item.get("bridge_" + key, key != "enabled"), "本组消息互通 " + key)
                    for key in ("enabled", "to_game", "to_group", "chat", "join", "leave", "death")
                ),
                bridge_prefix,
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
            raise MCError(message_text("error_group_only"))
        for group in self.groups.values():
            if group.platform == actor.platform and actor.group in group.group_ids:
                return group
        raise MCError(message_text("error_group_unbound"))

    def is_admin(self, actor: Actor, group: Group) -> bool:
        return (
            isinstance(actor.user, str)
            and bool(actor.user.strip())
            and actor.platform == group.platform
            and actor.group in group.group_ids
            and (actor.admin is True or actor.user in group.admins)
        )

    def can_manage(self, actor: Actor, group: Group) -> bool:
        return self.is_admin(actor, group) or (
            actor.platform == group.platform and actor.group in group.group_ids and actor.user in group.whitelist_admins
        )

    def can_restart(self, actor: Actor, group: Group, sid: str) -> bool:
        if (
            not isinstance(actor.user, str)
            or not actor.user.strip()
            or actor.platform != group.platform
            or actor.group not in group.group_ids
            or sid not in group.servers
        ):
            return False
        if actor.admin is True:
            return True
        allowed = self.is_admin(actor, group) or actor.user in (group.restart_admins or ())
        owners = self.servers[sid].restart_admins
        return allowed and (not owners or f"{actor.platform}:{actor.user}" in owners)

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
            raise MCError(message_text("error_unknown_command"))
        return action, words[1:]

    def localize(self, value, depth=0):
        # Values are resolved once; braces inside player names/server replies
        # stay literal. Templates cannot call other templates or access objects.
        if depth > 32:
            return str(value)
        if hasattr(value, "localize"):
            return value.localize(self, depth)
        return str(value)

    def render(self, key: str, *, limit: int | None = 16000, _depth=0, **values: Any) -> str:
        context = {k: "" for k in FIELDS}
        context.update({k: aliases(self.commands[k])[0] for k in ALIASES})
        context["prefix"] = "/" + self.prefixes[0]
        context.update({k: safe_text(self.localize(v, _depth), limit) for k, v in values.items()})
        return safe_text(self.messages[key].format_map(context), limit)


MESSAGE_CATEGORIES = {
    "common": "常用回复",
    "restart": "重启与广播",
    "approval": "申请审核",
    "query": "状态与查询",
    "performance": "TPS 摘要与标签",
    "bridge": "消息互通",
    "errors": "权限与操作错误",
    "validation": "配置与文件错误",
    "audit": "日志与诊断",
    "labels": "通用文字",
}


def message_category(key):
    if key.startswith("bridge_"):
        return "bridge"
    if key.startswith(("game_restart", "restart_")):
        return "restart"
    if key.startswith(("request_", "empty_requests", "default_rejection")) or key == "requests":
        return "approval"
    if key.startswith(("perf_", "parse_")):
        return "performance"
    if key.startswith(("query_", "status_", "source_", "players_none", "raw_tps")) or key in {
        "tps",
        "tps_warning",
        "tps_error",
        "players",
        "status",
    }:
        return "query"
    if key.startswith(("error_config_", "error_files_", "error_spark_log_", "error_backups_", "error_dashboard_")):
        return "validation"
    if key.startswith("error_") or key == "internal_error":
        return "errors"
    if key.startswith(("audit_", "action_", "diagnostic_", "worker_state_")) or key in {
        "diagnostics",
        "log_detail",
        "log_entry",
        "logs",
    }:
        return "audit"
    if key.startswith(("boolean_", "owner_")) or key in {"yes", "no", "unverified", "no_error"}:
        return "labels"
    return "common"


MESSAGE_FIELD_DESCRIPTIONS = {
    "groups": "现有共享配置中的完整群号列表",
    "prefix": "主指令，包含 /",
    "scope_id": "配置组 ID",
    "old_platform": "原平台实例 ID",
    "old_group": "原群号",
    "platform": "当前平台实例 ID",
    "backup": "备份文件名称",
    "percentile": "回复中标注的百分位名称",
    "server": "服务器显示名称",
    "server_id": "服务器配置 ID",
    "server_ids": "目标服务器 ID 列表",
    "uuid": "玩家 UUID",
    "old_owner": "原所属用户 ID",
    "new_owner": "新所属用户 ID 或移除标记",
    "player": "MC 玩家名",
    "old_player": "修改前的玩家名",
    "user": "操作用户 ID",
    "owner": "条目所属用户",
    "id": "白名单或申请固定编号",
    "group": "群号 / 群 ID",
    "page": "当前页码",
    "pages": "总页数",
    "total": "总条数",
    "entries": "按行模板生成的明细",
    "result": "本次完整结果或原始回复",
    "sync": "按同步行模板生成的结果",
    "error": "失败原因，可由对应错误模板生成",
    "detail": "附加说明或操作详情",
    "seconds": "耗时、等待或倒计时秒数",
    "time": "操作或申请时间",
    "count": "条目数量",
    "action": "日志操作类型",
    "servers": "目标服务器名称列表",
    "request_kind": "申请类型（可修改 request_kind_*）",
    "worker": "后台任务名称",
    "worker_state": "后台任务状态（可修改 worker_state_*）",
    "writable": "目录写入检查结果（可修改 boolean_*）",
    "readable": "白名单读取检查结果（可修改 boolean_*）",
    "windows": "本次实际提供的统计窗口",
    "window": "统计窗口",
    "labels": "统计标签",
    "values": "与窗口/标签对应的数值",
    "scope": "CPU 指标范围",
    "dimension": "核心返回的维度名称",
    "label": "当前校验/统计项目名称",
    "sid": "涉及的服务器 ID",
    "name": "当前校验的名称",
    "code": "HTTP 状态码",
    "relative": "日志相对路径",
    "max_entries": "每人白名单数量上限",
    "free_mb": "剩余磁盘空间，MB",
    "config_error": "配置问题详情",
    "last_error": "最近一次启动检查错误",
    "response": "服务端原始回复",
    "table": "备份数据库表名",
    "exc": "底层异常详情",
    "value": "本次校验的值",
    "nickname": "群昵称",
    "message": "聊天/事件原文",
    "event": "互通事件类型",
    "alert_type": "告警类型",
    "alert_detail": "告警明细",
    "token": "当前校验的变量名",
    "minimum": "最小允许值",
    "maximum": "最大允许值",
}


def message_templates():
    """Single catalog for the editor, docs and default-configuration checks."""
    result = []
    for key, default in MESSAGES.items():
        fields = list(dict.fromkeys(token for _, token, _, _ in string.Formatter().parse(default) if token))
        result.append(
            {
                "key": key,
                "category": message_category(key),
                "category_label": MESSAGE_CATEGORIES[message_category(key)],
                "default": default,
                "variables": [
                    {
                        "name": name,
                        "description": MESSAGE_FIELD_DESCRIPTIONS.get(
                            name,
                            "当前模板中对应位置的动态数据"
                            if name.startswith("value")
                            else "性能/查询变量，详见 TPS 变量文档"
                            if name in SPARK_FIELDS | MONITOR_FIELDS | PERFORMANCE_FIELDS
                            else "当前上下文中的数据",
                        ),
                    }
                    for name in fields
                ],
            }
        )
        if key in {"help", "help_full"} or key.startswith(("usage_", "choose_")):
            for variable in result[-1]["variables"]:
                if variable["name"] in ALIASES:
                    variable["description"] = "对应子指令配置中的第一个别名"
    return result
