"""Connection tests never save drafts or run user-provided mutable commands."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from mc_manager.config import MCError, Server
from mc_manager.connection import probe_connection


class ConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.server = Server("s1", "Test", self.root, "127.0.0.1", 25575, "secret", players_command="stop")
        self.commands = []
        self.response = "There are 2 of a max of 20 players online: Steve, Alex"
        owner = self

        class Client:
            def __init__(self, *_):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                pass

            async def command(self, command):
                owner.commands.append(command)
                if isinstance(owner.response, Exception):
                    raise owner.response
                return owner.response

        self.client = Client

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_fixed_list_command_parses_players_and_never_writes_data(self):
        result = await probe_connection(self.server, 1, self.client)
        self.assertEqual(self.commands, ["list"])
        self.assertEqual((result["players_online"], result["players_max"]), ("2", "20"))
        self.assertNotIn("secret", json.dumps(result))
        self.assertEqual(list(self.root.iterdir()), [])

    async def test_no_game_players_is_a_successful_connection(self):
        self.response = "There are 0 of a max of 20 players online:"
        result = await probe_connection(self.server, 1, self.client)
        self.assertEqual(result["players_online"], "0")
        self.assertEqual(result["players_names"], "无")

    async def test_connection_errors_are_actionable(self):
        for failure in (OSError("refused"), TimeoutError(), EOFError("closed")):
            self.response = failure
            with self.subTest(error=type(failure)), self.assertRaisesRegex(MCError, "RCON"):
                await probe_connection(self.server, 1, self.client)

    async def test_invalid_or_empty_reply_does_not_claim_query_success(self):
        for response in ("", "Unknown command", "You do not have permission to use this command"):
            self.response = response
            with self.subTest(response=response), self.assertRaisesRegex(MCError, "无有效回复"):
                await probe_connection(self.server, 1, self.client)

    async def test_arbitrary_player_command_is_not_used_by_the_probe(self):
        await probe_connection(replace(self.server, players_command="op Someone"), 1, self.client)
        self.assertEqual(self.commands, ["list"])
