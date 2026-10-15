from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import Actor, Group, MCError, Settings


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "mc.sqlite3"
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS scopes(id TEXT PRIMARY KEY, platform TEXT, group_id TEXT,
            next_id INTEGER NOT NULL DEFAULT 1, initialized INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS bindings(server TEXT PRIMARY KEY, scope TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS server_history(server TEXT PRIMARY KEY, scope TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS entries(scope TEXT, id INTEGER, name TEXT COLLATE NOCASE,
            uuid TEXT, owner TEXT, created TEXT, PRIMARY KEY(scope,id),
            UNIQUE(scope,name), UNIQUE(scope,uuid));
        CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT,
            time TEXT, platform TEXT, group_id TEXT, user TEXT, user_name TEXT,
            action TEXT, player TEXT, detail TEXT);
        CREATE TABLE IF NOT EXISTS sessions(scope TEXT PRIMARY KEY, origin TEXT);
        CREATE TABLE IF NOT EXISTS dirty(server TEXT PRIMARY KEY, error TEXT DEFAULT '待同步');
        CREATE TABLE IF NOT EXISTS jobs(server TEXT PRIMARY KEY, scope TEXT, origin TEXT,
            started TEXT, status TEXT, detail TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY AUTOINCREMENT,
            origin TEXT, text TEXT, created TEXT, attempts INTEGER DEFAULT 0, scope TEXT DEFAULT '');
        """)

        with self.db:
            columns = {row[1] for row in self.db.execute("PRAGMA table_info(scopes)")}
            if "active" not in columns:
                self.db.execute("ALTER TABLE scopes ADD COLUMN active INTEGER NOT NULL DEFAULT 0")
                self.db.execute("UPDATE scopes SET active=1 WHERE id IN (SELECT scope FROM bindings)")
            columns = {row[1] for row in self.db.execute("PRAGMA table_info(outbox)")}
            if "scope" not in columns:
                self.db.execute("ALTER TABLE outbox ADD COLUMN scope TEXT DEFAULT ''")
            self.db.execute("INSERT OR IGNORE INTO server_history SELECT server,scope FROM bindings")
            for row in self.db.execute(
                "SELECT scope,origin FROM sessions UNION SELECT scope,origin FROM jobs"
            ).fetchall():
                if row["origin"]:
                    self.db.execute(
                        "UPDATE outbox SET scope=? WHERE origin=? AND (scope='' OR scope IS NULL)",
                        (row["scope"], row["origin"]),
                    )

    @contextmanager
    def transaction(self):
        with self.db:
            yield

    def _reset_scope_delivery(self, scope: str):
        origins = [
            row[0]
            for row in self.db.execute(
                "SELECT origin FROM sessions WHERE scope=? UNION SELECT origin FROM jobs WHERE scope=?", (scope, scope)
            )
            if row[0]
        ]
        self.db.execute("DELETE FROM outbox WHERE scope=?", (scope,))
        for origin in origins:
            self.db.execute("DELETE FROM outbox WHERE origin=? AND (scope='' OR scope IS NULL)", (origin,))
        self.db.execute("DELETE FROM sessions WHERE scope=?", (scope,))
        self.db.execute(
            "UPDATE jobs SET status='configuration_changed',detail='群或服务器绑定已更改，旧广播已取消' WHERE scope=? AND status='running'",
            (scope,),
        )

    def validate_scopes(self, settings: Settings, reserve: bool = True, actor: Actor | None = None):
        # Settings validates duplicate identities and server membership. Historical
        # rows are archives; only the bindings table represents current ownership.
        if not reserve:
            return
        with self.transaction():
            previous = {row["id"]: dict(row) for row in self.db.execute("SELECT * FROM scopes")}
            old_bindings = {row["server"]: row["scope"] for row in self.db.execute("SELECT * FROM bindings")}
            history = {row["server"]: row["scope"] for row in self.db.execute("SELECT * FROM server_history")}
            for scope, row in previous.items():
                if scope not in settings.groups:
                    self._reset_scope_delivery(scope)
                    self.db.execute("UPDATE scopes SET active=0 WHERE id=?", (scope,))
                    if row["active"] or scope in old_bindings.values():
                        group = Group(scope, row["platform"], row["group_id"], ())
                        who = Actor(
                            group.platform,
                            group.group_id,
                            actor.user if actor else "system",
                            actor.name if actor else "配置同步",
                            True,
                        )
                        self.audit(group, who, "unbind_group", "", "移除群绑定；服务器已释放，历史白名单和日志保留")
            self.db.execute("DELETE FROM bindings")
            for group in settings.groups.values():
                old = previous.get(group.id)
                changed = bool(old and (old["platform"], old["group_id"]) != (group.platform, group.group_id))
                who = Actor(
                    group.platform,
                    group.group_id,
                    actor.user if actor else "system",
                    actor.name if actor else "配置同步",
                    True,
                )
                if old is None:
                    self.db.execute(
                        "INSERT INTO scopes(id,platform,group_id,active) VALUES(?,?,?,1)",
                        (group.id, group.platform, group.group_id),
                    )
                    if actor:
                        self.audit(group, who, "bind_group", "", "新增群绑定：" + ",".join(group.servers))
                else:
                    self.db.execute(
                        "UPDATE scopes SET platform=?,group_id=?,active=1 WHERE id=?",
                        (group.platform, group.group_id, group.id),
                    )
                    if changed:
                        self._reset_scope_delivery(group.id)
                        detail = f"群号/平台 {old['platform']}:{old['group_id']} → {group.platform}:{group.group_id}；白名单和编号保留，广播会话重置"
                        if old["platform"] != group.platform:
                            self.db.execute("UPDATE entries SET owner='' WHERE scope=?", (group.id,))
                            detail += "；跨平台条目归属清空，由管理员管理"
                        self.audit(group, who, "change_group", "", detail)
                    elif not old["active"] and actor:
                        self.audit(group, who, "bind_group", "", "恢复已归档配置组，保留该组原有白名单")
                transferred = [sid for sid in group.servers if sid in history and history[sid] != group.id]
                if transferred:
                    # Never seed a new scope from files containing the former
                    # group's members. Its own DB entries are authoritative.
                    self.db.execute("UPDATE scopes SET initialized=1 WHERE id=?", (group.id,))
                for sid in group.servers:
                    prior = history.get(sid)
                    self.db.execute("INSERT INTO bindings VALUES(?,?)", (sid, group.id))
                    self.db.execute(
                        "INSERT INTO server_history VALUES(?,?) ON CONFLICT(server) DO UPDATE SET scope=excluded.scope",
                        (sid, group.id),
                    )
                    if changed or (
                        old_bindings.get(sid) != group.id
                        and (old is not None or actor is not None or prior is not None)
                    ):
                        self.mark_dirty([sid])
                    if prior is not None and prior != group.id:
                        self.audit(
                            group,
                            who,
                            "move_server",
                            sid,
                            f"服务器从配置组 {prior} 转给 {group.id}，使用新组白名单，不自动导入旧组成员",
                        )
            self.db.execute("DELETE FROM dirty WHERE server NOT IN (SELECT server FROM bindings)")

    def initialized(self, scope: str) -> bool:
        return bool(self.db.execute("SELECT initialized FROM scopes WHERE id=?", (scope,)).fetchone()[0])

    def entries(self, scope: str) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM entries WHERE scope=? ORDER BY id", (scope,))]

    def find(self, scope: str, target: str) -> dict:
        if target.isdecimal():
            row = self.db.execute("SELECT * FROM entries WHERE scope=? AND id=?", (scope, int(target))).fetchone()
        else:
            row = self.db.execute("SELECT * FROM entries WHERE scope=? AND name=?", (scope, target)).fetchone()
        if not row:
            raise MCError("未找到该白名单条目；编号按群固定，删除后不会复用。")
        return dict(row)

    def insert(self, scope: str, name: str, uuid: str, owner: str) -> int:
        identifier = self.db.execute("SELECT next_id FROM scopes WHERE id=?", (scope,)).fetchone()[0]
        self.db.execute("INSERT INTO entries VALUES(?,?,?,?,?,?)", (scope, identifier, name, uuid, owner, now()))
        self.db.execute("UPDATE scopes SET next_id=next_id+1 WHERE id=?", (scope,))
        return identifier

    def audit(self, group, actor, action: str, player: str = "", detail: str = ""):
        self.db.execute(
            "INSERT INTO logs(scope,time,platform,group_id,user,user_name,action,player,detail) VALUES(?,?,?,?,?,?,?,?,?)",
            (group.id, now(), actor.platform, actor.group, actor.user, actor.name, action, player, detail),
        )

    def mark_dirty(self, servers):
        for sid in servers:
            self.db.execute(
                "INSERT INTO dirty(server,error) VALUES(?,?) ON CONFLICT(server) DO UPDATE SET error=excluded.error",
                (sid, "待同步"),
            )

    def sync_result(self, sid: str, error: str):
        with self.transaction():
            if error:
                self.db.execute(
                    "INSERT INTO dirty VALUES(?,?) ON CONFLICT(server) DO UPDATE SET error=excluded.error", (sid, error)
                )
            else:
                self.db.execute("DELETE FROM dirty WHERE server=?", (sid,))

    def dirty(self) -> dict[str, str]:
        return {r[0]: r[1] for r in self.db.execute("SELECT * FROM dirty")}

    def remember(self, scope: str, origin: str):
        if origin:
            with self.transaction():
                self.db.execute(
                    "INSERT INTO sessions VALUES(?,?) ON CONFLICT(scope) DO UPDATE SET origin=excluded.origin",
                    (scope, origin),
                )

    def origin(self, scope: str) -> str:
        row = self.db.execute("SELECT origin FROM sessions WHERE scope=?", (scope,)).fetchone()
        return row[0] if row else ""

    def logs(self, scope: str, page: int, size: int, user: str = "") -> tuple[list[dict], int]:
        clause = "scope=?" + (" AND user=?" if user else "")
        args = (scope, user) if user else (scope,)
        count = self.db.execute("SELECT COUNT(*) FROM logs WHERE " + clause, args).fetchone()[0]
        rows = self.db.execute(
            "SELECT * FROM logs WHERE " + clause + " ORDER BY id DESC LIMIT ? OFFSET ?",
            (*args, size, (page - 1) * size),
        )
        return [dict(r) for r in rows], count

    def job(self, sid: str, scope: str, origin: str):
        with self.transaction():
            self.db.execute(
                "INSERT INTO jobs VALUES(?,?,?,?,?,?) ON CONFLICT(server) DO UPDATE SET scope=excluded.scope,origin=excluded.origin,started=excluded.started,status=excluded.status,detail=excluded.detail",
                (sid, scope, origin, now(), "running", ""),
            )

    def finish_job(self, sid: str, status: str, detail: str, origin: str, text: str):
        with self.transaction():
            self.db.execute("UPDATE jobs SET status=?,detail=? WHERE server=?", (status, detail, sid))
            if origin:
                row = self.db.execute("SELECT scope FROM jobs WHERE server=?", (sid,)).fetchone()
                scope = row[0] if row else ""
                self.db.execute(
                    "INSERT INTO outbox(origin,text,created,scope) VALUES(?,?,?,?)", (origin, text, now(), scope)
                )

    def jobs(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM jobs ORDER BY started DESC")]

    def outbox(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM outbox ORDER BY id LIMIT 20")]

    def delivered(self, identifier: int, success: bool):
        with self.transaction():
            if success:
                self.db.execute("DELETE FROM outbox WHERE id=?", (identifier,))
            else:
                self.db.execute("UPDATE outbox SET attempts=attempts+1 WHERE id=?", (identifier,))

    def close(self):
        self.db.close()
