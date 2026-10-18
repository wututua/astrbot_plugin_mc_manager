"""Optional group/game bridge. Log readers never replay pre-existing history."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from stat import S_ISREG

from .config import MCError
from .message_catalog import text as message_text
from .text import safe_text

COLORS = dict(
    zip(
        "0123456789abcdef",
        (
            "black",
            "dark_blue",
            "dark_green",
            "dark_aqua",
            "dark_red",
            "dark_purple",
            "gold",
            "gray",
            "dark_gray",
            "blue",
            "green",
            "aqua",
            "red",
            "light_purple",
            "yellow",
            "white",
        ),
    )
)
STYLES = dict(zip("klmno", ("obfuscated", "bold", "strikethrough", "underlined", "italic")))
# Only trusted Minecraft log envelopes are considered. RCON, plugins and stack
# traces cannot turn arbitrary lines into player activity.
ENVELOPE = re.compile(
    r"^\[[^\]\r\n]+\] \[(?:Server thread|Async Chat Thread[^\]\r\n]*)/INFO\]"
    r"(?: \[[^\]\r\n]+\])?:\s*(.*)$"
)
CHAT = re.compile(r"^(?:\[Not Secure\] )?<([A-Za-z0-9_]{3,16})> (.+)$")
ACTIVITY = re.compile(r"^([A-Za-z0-9_]{3,16}) (joined|left) the game$")
DEATH = re.compile(
    r"^([A-Za-z0-9_]{3,16}) (?:"
    r"was (?:slain|shot|pummeled|pummelled|pricked|poked|stabbed|impaled|skewered|stung|roasted|"
    r"obliterated|killed|blown up|blown from|burnt to a crisp|fireballed|squished|squashed|struck by lightning|doomed to fall)|"
    r"fell (?:from|off|out of|into)|hit the ground too hard|went up in flames|burned to death|"
    r"tried to swim in lava|discovered the floor was lava|walked into (?:fire|a cactus|(?:the )?danger zone)|"
    r"suffocated in a wall|drowned|blew up|didn't want to live|starved to death|withered away|"
    r"froze to death|experienced kinetic energy|went off with a bang|left the confines of this world|died"
    r")(?:\b|$)"
)


def game_text(value, limit=2000):
    """Remove controls but retain legacy section-sign formatting for tellraw."""
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", str(value))
    return "".join(" " if char in "\n\r\t" else char for char in text if ord(char) >= 32 or char in "\n\r\t")[:limit]


def legacy_components(text):
    """Convert legacy color/style codes to data-only Java text components."""
    components, buffer, state = [], [], {}

    def flush():
        if buffer:
            components.append({"text": "".join(buffer), **state})
            buffer.clear()

    index = 0
    while index < len(text):
        if text[index] == "§" and index + 1 < len(text):
            code = text[index + 1].lower()
            hex_code = re.match(r"§x((?:§[0-9a-f]){6})", text[index:], re.I) if code == "x" else None
            if hex_code or code in COLORS or code in STYLES or code == "r":
                flush()
                if hex_code or code in COLORS or code == "r":
                    state = {"color": "white", **dict.fromkeys(STYLES.values(), False)}
                    if hex_code:
                        state["color"] = "#" + hex_code[1].replace("§", "").lower()
                    elif code in COLORS:
                        state["color"] = COLORS[code]
                else:
                    state[STYLES[code]] = True
                index += len(hex_code[0]) if hex_code else 2
                continue
        buffer.append(text[index])
        index += 1
    flush()
    return components or [{"text": ""}]


def tellraw_command(text):
    text = game_text(text, 4000)

    def encode(value):
        return "tellraw @a " + json.dumps(
            {"text": "", "extra": legacy_components(value)}, ensure_ascii=False, separators=(",", ":")
        )

    command = encode(text)
    # Minecraft RCON accepts a 4096-byte packet, including the 10-byte framing.
    if len(command.encode("utf-8")) <= 4080:
        return command
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if len(encode(text[:middle] + "…").encode("utf-8")) <= 4080:
            low = middle
        else:
            high = middle - 1
    return encode(text[:low] + "…")


@dataclass(frozen=True)
class GameEvent:
    kind: str
    player: str
    message: str


def parse_game_event(line):
    envelope = ENVELOPE.fullmatch(safe_text(line, 8192).strip())
    if not envelope:
        return None
    content = envelope[1]
    chat = CHAT.fullmatch(content)
    if chat:
        return GameEvent("chat", chat[1], chat[2])
    activity = ACTIVITY.fullmatch(content)
    if activity:
        return GameEvent("join" if activity[2] == "joined" else "leave", activity[1], content)
    death = DEATH.match(content)
    if death:
        return GameEvent("death", death[1], content)
    return None


class LogTail:
    """Bounded binary tail, supporting UTF-8 chunks, rotation and copytruncate."""

    def __init__(self, directory: Path, relative: str, encoding="utf-8"):
        self.directory = directory.resolve()
        self.relative = relative
        self.encoding = encoding
        self.initialized = False
        self.identity = None
        self.offset = 0
        self.anchor = b""
        self.partial = b""
        self.discard_line = False
        self.status = "等待日志"

    def read(self):
        target = (self.directory / self.relative).resolve()
        root = self.directory
        if not target.is_relative_to(root) or target == root:
            raise MCError(message_text("error_bridge_001"))
        try:
            if not S_ISREG(target.stat().st_mode):
                raise MCError(message_text("error_bridge_002"))
            stream = target.open("rb")
        except FileNotFoundError:
            self.initialized = True
            self.identity = None
            self.status = "等待日志文件"
            return []
        with stream:
            stat = os.fstat(stream.fileno())
            identity = (stat.st_dev, stat.st_ino)
            if not self.initialized:
                self.offset = stat.st_size
                self.identity = identity
                self.initialized = True
                # An incomplete historical line must not be replayed later.
                if self.offset:
                    stream.seek(self.offset - 1)
                    self.discard_line = stream.read(1) != b"\n"
            else:
                changed = identity != self.identity or stat.st_size < self.offset
                if not changed and self.anchor:
                    stream.seek(self.offset - len(self.anchor))
                    changed = stream.read(len(self.anchor)) != self.anchor
                if changed:
                    self.offset, self.anchor, self.partial = 0, b"", b""
                    self.discard_line = False
                self.identity = identity
            stream.seek(self.offset)
            chunk = stream.read(65536)
            self.offset += len(chunk)
            stream.seek(max(0, self.offset - 128))
            self.anchor = stream.read(min(128, self.offset))
        self.status = "读取正常"
        parts = (self.partial + chunk).split(b"\n")
        self.partial = parts.pop()
        lines = []
        for part in parts:
            if self.discard_line:
                self.discard_line = False
                continue
            if len(part) <= 8192:
                lines.append(part.decode(self.encoding, errors="replace").rstrip("\r"))
        if len(self.partial) > 8192:
            self.partial = b""
            self.discard_line = True
        return lines


class BridgeMixin:
    def _init_bridge(self):
        self.bridge_poll_task = None
        self.bridge_delivery_task = None
        self.bridge_queue = asyncio.Queue(maxsize=200)
        self.bridge_tails = {}
        self.bridge_cooldowns = {}
        self.bridge_seen = {}
        self.bridge_stats = {"queued": 0, "sent": 0, "failed": 0, "dropped": 0, "last_error": ""}
        if self.settings.bridge_enabled:
            for sid, server in self.settings.servers.items():
                if self.bridge_groups(sid):
                    self.bridge_tails[sid] = LogTail(
                        server.directory, server.bridge_log_path, server.bridge_log_encoding
                    )

    def bridge_groups(self, sid):
        return [
            group
            for group in self.settings.server_groups.get(sid, ())
            if group.bridge_enabled and group.bridge_to_group
        ]

    def _bridge_enqueue(self, direction, target, text):
        try:
            self.bridge_queue.put_nowait((direction, target, text, time.monotonic()))
            self.bridge_stats["queued"] += 1
            return True
        except asyncio.QueueFull:
            self.bridge_stats["dropped"] += 1
            return False

    def relay_group(self, actor, text, message_id=""):
        if not self.settings.bridge_enabled or self.closed:
            return 0
        try:
            group = self.settings.group_for(actor)
        except MCError:
            return 0
        if not group.bridge_enabled or not actor.user or not actor.origin:
            return 0
        # Record actual adapter sessions even when the user hasn't used /mc yet.
        self.store.remember(group.id, actor.origin, actor.group)
        if not group.bridge_to_game:
            return 0
        text = str(text).strip()
        if group.bridge_prefix:
            if not text.startswith(group.bridge_prefix):
                return 0
            text = text[len(group.bridge_prefix) :].lstrip()
        if not text or text.startswith(("/", "!", "！")):
            return 0
        first = text.split(maxsplit=1)[0].casefold()
        if first in {"mc", *(prefix.casefold() for prefix in self.settings.prefixes)}:
            return 0
        now = time.monotonic()
        self.bridge_seen = {key: expiry for key, expiry in self.bridge_seen.items() if expiry > now}
        identity = (actor.platform, actor.group, str(message_id))
        if message_id and identity in self.bridge_seen:
            return 0
        cooldown_key = (actor.platform, actor.group, actor.user)
        if now - self.bridge_cooldowns.get(cooldown_key, float("-inf")) < self.settings.bridge_cooldown_seconds:
            return 0
        if cooldown_key not in self.bridge_cooldowns and len(self.bridge_cooldowns) >= 10000:
            self.bridge_cooldowns.pop(next(iter(self.bridge_cooldowns)))
        self.bridge_cooldowns[cooldown_key] = now
        if message_id:
            if len(self.bridge_seen) >= 10000:
                self.bridge_seen.pop(next(iter(self.bridge_seen)))
            self.bridge_seen[identity] = now + 60
        values = dict.fromkeys(self.settings.template_fields, "")
        values.update(
            group=game_text(actor.group, 80),
            user=game_text(actor.user, 80),
            nickname=game_text(actor.name or actor.user, 80),
            message=game_text(text, self.settings.bridge_max_chars),
            event="chat",
        )
        count = 0
        for sid in group.servers:
            if sid in self.restart_tasks:
                continue
            server = self.settings.servers[sid]
            values.update(server=game_text(server.name, 80), server_id=sid)
            rendered = self.settings.messages["bridge_to_game"].format_map(values)
            if not rendered.strip():
                continue
            if self._bridge_enqueue("game", sid, tellraw_command(rendered)):
                count += 1
        return count

    async def bridge_poll(self):
        if not self.settings.bridge_enabled:
            return
        for sid, tail in self.bridge_tails.items():
            task = asyncio.create_task(asyncio.to_thread(tail.read))
            try:
                lines = await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
            except (OSError, MCError) as exc:
                tail.status = "读取失败：" + safe_text(exc, 300)
                self.bridge_stats["last_error"] = tail.status
                continue
            for line in lines:
                event = parse_game_event(line)
                if event is None:
                    continue
                recipients = {}
                server = self.settings.servers[sid]
                for group in self.bridge_groups(sid):
                    if not getattr(group, "bridge_" + event.kind):
                        continue
                    for group_id, origin in self.broadcast_origins(group).items():
                        if origin not in recipients:
                            recipients[origin] = self.settings.render(
                                "bridge_" + event.kind,
                                server=server.name,
                                server_id=sid,
                                group=group_id,
                                player=event.player,
                                message=event.message[: self.settings.bridge_max_chars],
                                event=event.kind,
                            )
                for origin, text in recipients.items():
                    if text.strip():
                        self._bridge_enqueue("group", origin, text)

    async def bridge_flush(self):
        if not self.settings.bridge_enabled:
            return
        for _ in range(50):
            try:
                direction, target, text, queued = self.bridge_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            try:
                if time.monotonic() - queued > 30:
                    self.bridge_stats["dropped"] += 1
                    continue
                if direction == "game":
                    if target in self.restart_tasks:
                        self.bridge_stats["dropped"] += 1
                        continue
                    server = self.settings.servers[target]
                    async with self.server_locks[target]:
                        async with self.query_slots:
                            async with self.rcon_factory(server, self.settings.rcon_timeout) as client:
                                await client.command(text)
                else:
                    async with asyncio.timeout(max(5, self.settings.rcon_timeout)):
                        if not await self.send(target, text):
                            raise MCError(message_text("error_bridge_003"))
                self.bridge_stats["sent"] += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.bridge_stats["failed"] += 1
                self.bridge_stats["last_error"] = safe_text(exc, 300)
                self.logger.debug("MC 消息互通投递失败：%s", self.bridge_stats["last_error"])
            finally:
                self.bridge_queue.task_done()

    async def _bridge_poll_loop(self):
        await self._worker("bridge_events", self.bridge_poll, lambda: self.settings.bridge_poll_seconds)

    async def _bridge_delivery_loop(self):
        await self._worker("bridge_delivery", self.bridge_flush, lambda: 0.1)

    def bridge_snapshot(self):
        return {
            "enabled": self.settings.bridge_enabled,
            "pending": self.bridge_queue.qsize(),
            **self.bridge_stats,
            "logs": {sid: tail.status for sid, tail in self.bridge_tails.items()},
        }
