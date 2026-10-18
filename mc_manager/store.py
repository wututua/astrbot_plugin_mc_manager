from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import Actor, Group, MCError, Settings, group_ids
from .message_catalog import text as message_text
from .operations_store import OperationsStore


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store(OperationsStore):
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "mc.sqlite3"
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self._savepoint = 0
        try:
            self._initialize()
        except BaseException:
            self.db.close()
            raise

    def _initialize(self):
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS scopes(id TEXT PRIMARY KEY, platform TEXT, group_id TEXT,
            next_id INTEGER NOT NULL DEFAULT 1, initialized INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS bindings(server TEXT, scope TEXT NOT NULL, PRIMARY KEY(server,scope));
        CREATE TABLE IF NOT EXISTS server_history(server TEXT PRIMARY KEY, scope TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS entries(scope TEXT, id INTEGER, name TEXT COLLATE NOCASE,
            uuid TEXT, owner TEXT, created TEXT, PRIMARY KEY(scope,id),
            UNIQUE(scope,name), UNIQUE(scope,uuid));
        CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT,
            time TEXT, platform TEXT, group_id TEXT, user TEXT, user_name TEXT,
            action TEXT, player TEXT, detail TEXT);
        CREATE TABLE IF NOT EXISTS sessions(scope TEXT PRIMARY KEY, origin TEXT);
        CREATE TABLE IF NOT EXISTS group_sessions(scope TEXT, group_id TEXT, origin TEXT, PRIMARY KEY(scope,group_id));
        CREATE TABLE IF NOT EXISTS sync_versions(server TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS dirty(server TEXT PRIMARY KEY, error TEXT DEFAULT '待同步');
        CREATE TABLE IF NOT EXISTS jobs(server TEXT PRIMARY KEY, scope TEXT, origin TEXT,
            started TEXT, status TEXT, detail TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY AUTOINCREMENT,
            origin TEXT, text TEXT, created TEXT, attempts INTEGER DEFAULT 0, scope TEXT DEFAULT '');
        """)

        with self.db:
            self.db.execute("BEGIN")
            binding_columns = {row[1]: row[5] for row in self.db.execute("PRAGMA table_info(bindings)")}
            if not binding_columns.get("scope"):
                self.db.execute("ALTER TABLE bindings RENAME TO bindings_single_scope")
                self.db.execute("CREATE TABLE bindings(server TEXT, scope TEXT NOT NULL, PRIMARY KEY(server,scope))")
                self.db.execute("INSERT INTO bindings SELECT server,scope FROM bindings_single_scope")
                self.db.execute("DROP TABLE bindings_single_scope")
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

            for row in self.db.execute(
                "SELECT sessions.scope,scopes.group_id,sessions.origin FROM sessions JOIN scopes ON scopes.id=sessions.scope"
            ).fetchall():
                identities = group_ids(row["group_id"])
                if len(identities) == 1:
                    self.db.execute("INSERT OR IGNORE INTO group_sessions VALUES(?,?,?)", tuple(row))

        self.upgrade_operations()

    @contextmanager
    def transaction(self):
        if self.db.in_transaction:
            self._savepoint += 1
            name = f"mc_nested_{self._savepoint}"
            self.db.execute(f"SAVEPOINT {name}")
            try:
                yield
            except BaseException:
                self.db.execute(f"ROLLBACK TO {name}")
                self.db.execute(f"RELEASE {name}")
                raise
            else:
                self.db.execute(f"RELEASE {name}")
        else:
            with self.db:
                self.db.execute("BEGIN")
                yield

    def _reset_scope_delivery(self, scope: str):
        origins = [
            row[0]
            for row in self.db.execute(
                "SELECT origin FROM sessions WHERE scope=? UNION SELECT origin FROM jobs WHERE scope=? UNION SELECT origin FROM group_sessions WHERE scope=?",
                (scope, scope, scope),
            )
            if row[0]
        ]
        self.db.execute("DELETE FROM outbox WHERE scope=?", (scope,))
        for origin in origins:
            self.db.execute("DELETE FROM outbox WHERE origin=? AND (scope='' OR scope IS NULL)", (origin,))
        self.db.execute("DELETE FROM sessions WHERE scope=?", (scope,))
        self.db.execute("DELETE FROM group_sessions WHERE scope=?", (scope,))
        self.db.execute(
            "UPDATE jobs SET status='configuration_changed',detail='群或服务器绑定已更改，旧广播已取消' WHERE scope=? AND status='running'",
            (scope,),
        )

    def validate_scopes(self, settings: Settings, reserve: bool = True, actor: Actor | None = None):
        if not reserve:
            return
        with self.transaction():
            previous = {row["id"]: dict(row) for row in self.db.execute("SELECT * FROM scopes")}
            old_bindings = {(row["server"], row["scope"]) for row in self.db.execute("SELECT * FROM bindings")}
            history = {row["server"]: row["scope"] for row in self.db.execute("SELECT * FROM server_history")}
            known_servers = set(history)
            for scope, row in previous.items():
                if scope not in settings.groups:
                    self._reset_scope_delivery(scope)
                    self.db.execute(
                        "UPDATE requests SET status='cancelled',detail='群配置已移除' WHERE scope=? AND status='pending'",
                        (scope,),
                    )
                    self.db.execute("UPDATE scopes SET active=0 WHERE id=?", (scope,))
                    if row["active"] or any(bound_scope == scope for _, bound_scope in old_bindings):
                        group = Group(scope, row["platform"], row["group_id"], ())
                        who = Actor(
                            group.platform,
                            group.group_id,
                            actor.user if actor else "system",
                            actor.name if actor else "配置同步",
                            True,
                        )
                        self.audit(
                            group,
                            who,
                            "unbind_group",
                            "",
                            settings.render("audit_unbind_group"),
                        )
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
                        self.audit(
                            group,
                            who,
                            "bind_group",
                            "",
                            settings.render("audit_bind_group", server_ids=",".join(group.servers)),
                        )
                else:
                    self.db.execute(
                        "UPDATE scopes SET platform=?,group_id=?,active=1 WHERE id=?",
                        (group.platform, group.group_id, group.id),
                    )
                    if changed:
                        retained = self.origins(group.id) if old["platform"] == group.platform else {}
                        self._reset_scope_delivery(group.id)
                        for pending in self.pending_requests(group.id):
                            if old["platform"] != group.platform or pending["group_id"] not in group.group_ids:
                                self.db.execute(
                                    "UPDATE requests SET status='cancelled',detail='群或平台绑定已变化' WHERE id=?",
                                    (pending["id"],),
                                )
                        for identity, origin in retained.items():
                            if identity in group.group_ids:
                                self.remember(group.id, origin, identity)
                        detail = settings.render(
                            "audit_change_group",
                            old_platform=old["platform"],
                            old_group=old["group_id"],
                            platform=group.platform,
                            group=group.group_id,
                        )
                        if old["platform"] != group.platform:
                            self.db.execute("UPDATE entries SET owner='' WHERE scope=?", (group.id,))
                            detail += settings.render("audit_change_platform")
                        self.audit(group, who, "change_group", "", detail)
                    elif not old["active"] and actor:
                        self.audit(group, who, "bind_group", "", settings.render("audit_restore_group"))
                eligible = []
                for sid in group.servers:
                    prior = history.setdefault(sid, group.id)
                    if prior == group.id:
                        eligible.append(sid)
                    self.db.execute("INSERT INTO bindings VALUES(?,?)", (sid, group.id))
                    self.db.execute("INSERT OR IGNORE INTO server_history VALUES(?,?)", (sid, group.id))
                    if changed:
                        self.mark_dirty([sid])
                    if (sid, group.id) not in old_bindings and prior != group.id:
                        self.audit(
                            group,
                            who,
                            "move_server",
                            sid,
                            settings.render("audit_move_server", scope_id=group.id, server_id=sid),
                        )
                if not eligible:
                    self.db.execute("UPDATE scopes SET initialized=1 WHERE id=?", (group.id,))
            current_bindings = {(row["server"], row["scope"]) for row in self.db.execute("SELECT * FROM bindings")}
            for sid, scope in old_bindings ^ current_bindings:
                if sid in settings.servers and (scope in previous or actor is not None or sid in known_servers):
                    self.mark_dirty([sid])
            for sid, groups in settings.server_groups.items():
                if len(groups) > 1:
                    self.server_entries(sid)
            self.db.execute("DELETE FROM dirty WHERE server NOT IN (SELECT server FROM bindings)")

    def initial_servers(self, group: Group) -> list[str]:
        return [
            sid
            for sid in group.servers
            if self.db.execute("SELECT scope FROM server_history WHERE server=?", (sid,)).fetchone()[0] == group.id
        ]

    def server_entries(self, sid: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT e.* FROM entries e JOIN bindings b ON b.scope=e.scope WHERE b.server=? ORDER BY e.scope,e.id",
            (sid,),
        )
        by_name, by_uuid = {}, {}
        for row in rows:
            entry = dict(row)
            named, identified = by_name.get(entry["name"].casefold()), by_uuid.get(entry["uuid"])
            if (named and named["uuid"] != entry["uuid"]) or (
                identified and identified["name"].casefold() != entry["name"].casefold()
            ):
                raise MCError(message_text("error_store_001", sid=sid))
            if not named:
                by_name[entry["name"].casefold()] = entry
                by_uuid[entry["uuid"]] = entry
        return list(by_name.values())

    def validate_player(self, group: Group, name: str, identifier: str, exclude_id: int | None = None):
        for sid in group.servers:
            rows = self.db.execute(
                "SELECT e.* FROM entries e JOIN bindings b ON b.scope=e.scope WHERE b.server=?", (sid,)
            )
            for row in rows:
                if row["scope"] == group.id and row["id"] == exclude_id:
                    continue
                if (row["name"].casefold() == name.casefold() and row["uuid"] != identifier) or (
                    row["uuid"] == identifier and row["name"].casefold() != name.casefold()
                ):
                    raise MCError(message_text("error_store_002"))

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
            raise MCError(message_text("error_entry_missing"))
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
                "INSERT INTO sync_versions(server,revision) VALUES(?,1) ON CONFLICT(server) DO UPDATE SET revision=revision+1",
                (sid,),
            )
            self.db.execute(
                "INSERT INTO dirty(server,error) VALUES(?,?) ON CONFLICT(server) DO UPDATE SET error=excluded.error",
                (sid, "待同步"),
            )

    def revision(self, sid: str) -> int:
        row = self.db.execute("SELECT revision FROM sync_versions WHERE server=?", (sid,)).fetchone()
        return row[0] if row else 0

    def sync_result(self, sid: str, error: str, revision: int | None = None):
        with self.transaction():
            if error:
                self.db.execute(
                    "INSERT INTO dirty VALUES(?,?) ON CONFLICT(server) DO UPDATE SET error=excluded.error", (sid, error)
                )
            elif revision is None or self.revision(sid) == revision:
                self.db.execute("DELETE FROM dirty WHERE server=?", (sid,))

    def dirty(self) -> dict[str, str]:
        return {r[0]: r[1] for r in self.db.execute("SELECT * FROM dirty")}

    def remember(self, scope: str, origin: str, group_id: str = ""):
        if origin:
            if not group_id:
                row = self.db.execute("SELECT group_id FROM scopes WHERE id=?", (scope,)).fetchone()
                identities = group_ids(row[0]) if row else ()
                group_id = next(
                    (
                        identity
                        for identity in identities
                        if origin.endswith(":" + identity) or origin.endswith("_" + identity)
                    ),
                    identities[0] if len(identities) == 1 else "",
                )
            with self.transaction():
                self.db.execute(
                    "INSERT INTO sessions VALUES(?,?) ON CONFLICT(scope) DO UPDATE SET origin=excluded.origin",
                    (scope, origin),
                )
                if group_id:
                    self.db.execute(
                        "INSERT INTO group_sessions VALUES(?,?,?) ON CONFLICT(scope,group_id) DO UPDATE SET origin=excluded.origin",
                        (scope, group_id, origin),
                    )

    def origins(self, scope: str) -> dict[str, str]:
        return {
            row["group_id"]: row["origin"]
            for row in self.db.execute("SELECT group_id,origin FROM group_sessions WHERE scope=?", (scope,))
        }

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
                "INSERT INTO jobs(server,scope,origin,started,status,detail,phase) VALUES(?,?,?,?,?,?,'queued') ON CONFLICT(server) DO UPDATE SET scope=excluded.scope,origin=excluded.origin,started=excluded.started,status=excluded.status,detail=excluded.detail,phase='queued'",
                (sid, scope, origin, now(), "running", ""),
            )

    def finish_job(
        self,
        sid: str,
        status: str,
        detail: str,
        origin: str,
        text: str,
        recipients: list[tuple[str, str]] | None = None,
    ):
        with self.transaction():
            self.db.execute("UPDATE jobs SET status=?,detail=?,phase=? WHERE server=?", (status, detail, status, sid))
            if recipients is None:
                row = self.db.execute("SELECT scope FROM jobs WHERE server=?", (sid,)).fetchone()
                recipients = [(row[0] if row else "", origin)] if origin else []
            for scope, target in recipients:
                self.db.execute(
                    "INSERT INTO outbox(origin,text,created,scope) VALUES(?,?,?,?)", (target, text, now(), scope)
                )

    def jobs(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM jobs ORDER BY started DESC")]

    def close(self):
        self.db.close()
