"""Exercise operational routes with AstrBot's official Web API and real SQLite backups."""

import asyncio
import json
import unittest
from unittest.mock import patch

import test_plugin as adapter_fixture


@unittest.skipUnless(adapter_fixture.HAS_WEB, "requires AstrBot public Web API and FastAPI")
class DashboardOperationsTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = adapter_fixture.PluginAdapterTests.asyncSetUp
    asyncTearDown = adapter_fixture.PluginAdapterTests.asyncTearDown
    real_service = adapter_fixture.PluginAdapterTests.real_service

    def bound_request(self, payload=None, query="", username="admin", method="POST"):
        from starlette.requests import Request

        async def receive():
            return {"type": "http.request", "body": json.dumps(payload or {}).encode(), "more_body": False}

        raw = Request(
            {"type": "http", "method": method, "path": "/", "headers": [], "query_string": query.encode()}, receive
        )
        return self.web.bind_request_context(self.web.PluginRequest(raw, username=username))

    async def operation(self, payload, username="admin"):
        with self.bound_request(payload, username=username):
            response = await self.plugin.page_operations()
        result = json.loads(response.body)
        return response.status_code, result.get("data", result), result.get("message", "")

    async def add(self, name="Steve"):
        with self.bound_request({"group": "g1", "action": "add", "args": [name]}):
            response = await self.plugin.page_action()
        self.assertEqual(response.status_code, 200, response.body)

    async def test_operational_routes_require_dashboard_auth(self):
        for payload in (
            {"action": "backups"},
            {"action": "diagnostics"},
            {"action": "backup_create"},
            {"action": "restore_apply", "confirmed": True},
        ):
            code, _, _ = await self.operation(payload, username=None)
            self.assertEqual(code, 400)

    async def test_backup_restore_preserves_config_ids_owner_and_rolls_back_extra_entry(self):
        await self.add()
        code, backup, _ = await self.operation({"action": "backup_create"})
        self.assertEqual(code, 200)
        await self.add("Alex")
        code, preview, _ = await self.operation({"action": "restore_preview", "name": backup["name"]})
        self.assertEqual(code, 200)
        self.assertEqual(preview["counts"]["entries"], 1)
        with patch.object(
            self.main,
            "Service",
            lambda settings, path, send, **kwargs: self.real_service(settings, path, send, **kwargs),
        ):
            code, result, message = await self.operation(
                {
                    "action": "restore_apply",
                    "token": preview["token"],
                    "revision": preview["revision"],
                    "confirmed": True,
                }
            )
        self.assertEqual(code, 200, message)
        self.assertIn("before-restore", [row["label"] for row in self.plugin.service.backups.list()])
        entries = self.plugin.service.store.entries("g1")
        self.assertEqual([(row["id"], row["name"], row["owner"]) for row in entries], [(1, "Steve", "dashboard:admin")])
        self.assertEqual(self.plugin.config["servers"][0]["rcon_password"], "private-secret")
        self.assertIn("safety_backup", result)

    async def test_restore_rejects_missing_confirmation_stale_revision_and_running_restart(self):
        _, backup, _ = await self.operation({"action": "backup_create"})
        _, preview, _ = await self.operation({"action": "restore_preview", "name": backup["name"]})
        payload = {"action": "restore_apply", "token": preview["token"], "revision": preview["revision"]}
        self.assertEqual((await self.operation(payload))[0], 400)
        self.assertEqual((await self.operation({**payload, "confirmed": True, "revision": "stale"}))[0], 400)
        task = asyncio.create_task(asyncio.Event().wait())
        self.plugin.service.restart_tasks["server1"] = task
        self.assertEqual((await self.operation({**payload, "confirmed": True}))[0], 400)
        self.plugin.service.restart_tasks.pop("server1")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def test_restore_save_failure_restores_previous_database_and_configuration(self):
        await self.add()
        _, backup, _ = await self.operation({"action": "backup_create"})
        await self.add("Alex")
        _, preview, _ = await self.operation({"action": "restore_preview", "name": backup["name"]})
        original = self.plugin._persist_config
        calls = 0

        async def fail_once():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("test disk write failed")
            await original()

        with (
            patch.object(self.plugin, "_persist_config", side_effect=fail_once),
            patch.object(
                self.main,
                "Service",
                lambda settings, path, send, **kwargs: self.real_service(settings, path, send, **kwargs),
            ),
        ):
            code, _, _ = await self.operation(
                {
                    "action": "restore_apply",
                    "token": preview["token"],
                    "revision": preview["revision"],
                    "confirmed": True,
                }
            )
        self.assertEqual(code, 400)
        self.assertEqual([row["name"] for row in self.plugin.service.store.entries("g1")], ["Steve", "Alex"])
        self.assertEqual(self.plugin.config["servers"][0]["rcon_password"], "private-secret")
        self.assertFalse(self.plugin.service.closed)

    async def test_bulk_routes_preview_confirmation_export_and_cross_scope(self):
        code, preview, _ = await self.operation(
            {"action": "bulk_preview", "group": "g1", "format": "text", "data": "Steve\nAlex"}
        )
        self.assertEqual(code, 200)
        code, result, _ = await self.operation({"action": "bulk_apply", "group": "g1", "token": preview["token"]})
        self.assertEqual(code, 200)
        self.assertEqual(result["count"], 2)
        code, exported, _ = await self.operation({"action": "entries_export", "group": "g1", "format": "json"})
        self.assertEqual(code, 200)
        self.assertEqual(len(json.loads(exported["content"])), 2)
        self.assertEqual((await self.operation({"action": "bulk_remove", "group": "g1", "ids": [1]}))[0], 400)
        self.assertEqual(
            (await self.operation({"action": "bulk_remove", "group": "unknown", "ids": [1], "confirmed": True}))[0], 400
        )
        self.assertEqual(
            (
                await self.operation(
                    {"action": "bulk_transfer", "group": "g1", "ids": [1], "owner": "new", "confirmed": True}
                )
            )[0],
            200,
        )

    async def test_slow_operations_do_not_hold_global_lock_and_unload_waits_for_leases(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow():
            async with self.plugin.operation():
                entered.set()
                await release.wait()

        task = asyncio.create_task(slow())
        await entered.wait()
        async with asyncio.timeout(1):
            with self.bound_request(query=""):
                response = await self.plugin.page_snapshot()
        self.assertEqual(response.status_code, 200)
        close = asyncio.create_task(self.plugin.terminate())
        await asyncio.sleep(0)
        self.assertFalse(close.done())
        release.set()
        await task
        await close
        self.assertIsNone(self.plugin.service)

    async def test_csv_export_escapes_spreadsheet_formula(self):
        from mc_manager.dashboard import export_rows

        result = export_rows([{"user": "=SUM(1,2)", "player": "Steve"}], "csv", "test")
        self.assertIn("'=SUM", result["content"])
        self.assertEqual(
            json.loads(export_rows([{"user": "=SUM(1,2)"}], "json", "test")["content"])[0]["user"], "=SUM(1,2)"
        )

    async def test_get_diagnostics_and_backup_routes_use_official_request_query(self):
        for query in (
            "kind=diagnostics&group=g1",
            "kind=backups",
            "kind=notifications&group=g1",
            "kind=requests&group=g1",
        ):
            with self.bound_request(query=query, method="GET"):
                response = await self.plugin.page_operations()
            self.assertEqual(response.status_code, 200, response.body)
        with self.bound_request(query="kind=diagnostics", method="GET", username=None):
            self.assertEqual((await self.plugin.page_operations()).status_code, 400)

    async def test_corrupt_database_keeps_configuration_and_storage_diagnostics_reachable(self):
        config = dict(self.plugin.config)
        await self.plugin.terminate()
        (self.root / "data/mc.sqlite3").write_bytes(b"not a sqlite database")
        self.plugin = self.main.MCManagerPlugin(self.context, self.Config(config))
        with patch.object(
            self.main,
            "Service",
            lambda settings, path, send, **kwargs: self.real_service(settings, path, send, **kwargs),
        ):
            await self.plugin.initialize()
        self.assertTrue(self.plugin.config_error)
        with self.bound_request():
            response = await self.plugin.page_config()
        self.assertEqual(response.status_code, 200, response.body)
        code, data, _ = await self.operation({"action": "diagnostics"})
        self.assertEqual(code, 200)
        self.assertIn("plugin_data", data["storage"])
        self.assertIn("initialization", data["workers"])
