"""One generated result, multiple independently encrypted Telegram destinations."""

import asyncio
import copy
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cryptography.fernet import Fernet

from app.services import db, secret_box, telegram_config as config
from app.tools import telegram_tools as tg
from web_api import routes_settings as routes
from web_api.auth import Identity


def bot(bot_id):
    return {"bot_id": str(bot_id), "bot_token": f"{bot_id}:private-token-{bot_id}",
            "chat_id": str(1000 + bot_id), "bot_username": f"bot{bot_id}"}


class StorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stored = {}
        self.lock = asyncio.Lock()
        self.key = patch.object(secret_box, "settings", SimpleNamespace(secrets_key=Fernet.generate_key().decode()))
        self.key.start()
        self.addCleanup(self.key.stop)
        self.cache = patch.object(config, "_cache", None)
        self.cache.start()
        self.addCleanup(self.cache.stop)

        async def update(key, transform):
            async with self.lock:
                await asyncio.sleep(0)
                self.stored = transform(copy.deepcopy(self.stored))
                return copy.deepcopy(self.stored)

        self.update = patch.object(db, "update_config", update)
        self.update.start()
        self.addCleanup(self.update.stop)

    async def save(self, id, token=None):
        return await config.save(bot_id=str(id), bot_token=token or bot(id)["bot_token"],
                                 chat_id=bot(id)["chat_id"], bot_username=f"bot{id}")

    async def test_connecting_two_bots_preserves_both_and_encrypts_separately(self):
        await asyncio.gather(self.save(1), self.save(2))
        self.assertEqual({b["bot_id"] for b in config.all_credentials()}, {"1", "2"})
        self.assertNotIn("private-token", repr(self.stored))
        for id, row in self.stored["bots"].items():
            self.assertEqual(secret_box.decrypt(row["bot_token_enc"]), bot(int(id))["bot_token"])

    async def test_reconnecting_one_bot_updates_only_it(self):
        await self.save(1)
        await self.save(2)
        await self.save(1, "1:replacement-token")
        credentials = {b["bot_id"]: b for b in config.all_credentials()}
        self.assertEqual(len(credentials), 2)
        self.assertEqual(credentials["1"]["bot_token"], "1:replacement-token")
        self.assertEqual(credentials["2"]["bot_token"], bot(2)["bot_token"])

    async def test_disconnect_removes_only_the_selected_bot(self):
        await self.save(1)
        await self.save(2)
        await config.clear("1")
        self.assertEqual([b["bot_id"] for b in config.all_credentials()], ["2"])

    async def test_existing_single_bot_is_preserved_when_adding_another(self):
        self.stored = {"bot_token_enc": secret_box.encrypt(bot(1)["bot_token"]),
                       "chat_id": bot(1)["chat_id"], "bot_username": "bot1"}
        with patch.object(db, "get_config", AsyncMock(return_value=self.stored)):
            await config.load()
        self.assertEqual(config.credentials()["bot_id"], "1")
        await self.save(2)
        self.assertEqual(set(self.stored["bots"]), {"1", "2"})

    async def test_legacy_disconnect_all_still_works(self):
        await self.save(1)
        await self.save(2)
        await config.clear()
        self.assertEqual(config.all_credentials(), [])
        self.assertFalse(config.configured())

    async def test_failed_database_write_does_not_change_live_destinations(self):
        await self.save(1)
        with patch.object(db, "update_config", AsyncMock(side_effect=RuntimeError("offline"))):
            with self.assertRaises(RuntimeError):
                await self.save(2)
        self.assertEqual([b["bot_id"] for b in config.all_credentials()], ["1"])

    async def test_status_lists_every_bot_without_plaintext_tokens(self):
        await self.save(1)
        await self.save(2)
        status = routes._status()
        self.assertEqual(len(status["bots"]), 2)
        for id in (1, 2):
            self.assertNotIn(bot(id)["bot_token"], repr(status))


class BroadcastTests(unittest.TestCase):
    def setUp(self):
        self.bots = [bot(1), bot(2), bot(3)]
        self.p = patch.object(config, "all_credentials", return_value=self.bots)
        self.p.start()
        self.addCleanup(self.p.stop)

    def test_one_review_call_broadcasts_identical_content_to_all_bots(self):
        payload = {"caption": "same caption", "preview_paths": ["same.png"]}
        with patch.object(tg, "_send_review_message", return_value={"message_id": "7"}) as send:
            result = tg.send_review_message("run", payload, 2)
        self.assertEqual(send.call_count, 3)
        self.assertEqual(result["bots_sent"], 3)
        self.assertEqual({c.kwargs["creds"]["bot_id"] for c in send.call_args_list}, {"1", "2", "3"})
        for call in send.call_args_list:
            self.assertEqual(call.args, ("run", payload, 2))

    def test_finished_files_are_replicated_without_regeneration(self):
        paths = ["cover.mp4", "body.png", "cta.png"]
        with patch.object(tg, "_send_completed_carousel", return_value={"files_sent": 3}) as send:
            result = tg.send_completed_carousel("run", "Title", "Caption", paths)
        self.assertEqual(send.call_count, 3)
        self.assertNotIn("status", result)  # caller sets terminal status=delivered
        for call in send.call_args_list:
            self.assertIs(call.args[3], paths)

    def test_publication_is_confirmed_to_every_bot(self):
        with patch.object(tg, "_send_confirmation_message", return_value={"message_id": "9"}) as send:
            tg.send_confirmation_message("run", "https://instagram.com/p/same")
        self.assertEqual(send.call_count, 3)

    def test_one_broken_bot_does_not_prevent_attempting_the_others(self):
        def send(*args, creds):
            if creds["bot_id"] == "2":
                raise RuntimeError("URL containing " + creds["bot_token"])
            return {"message_id": "sent"}
        with patch.object(tg, "_send_confirmation_message", side_effect=send) as sender:
            with self.assertRaises(tg.TelegramBroadcastError) as caught:
                tg.send_confirmation_message("run", "https://instagram.com/p/same")
        self.assertEqual(sender.call_count, 3)
        self.assertEqual([d["status"] for d in caught.exception.deliveries], ["sent", "error", "sent"])
        self.assertNotIn(bot(2)["bot_token"], str(caught.exception))

    def test_bot_connections_are_used_together_with_their_own_chat(self):
        seen = []
        guard = threading.Lock()
        def request(client, method, *, data=None, files=None):
            with guard:
                seen.append((str(client.base_url), data["chat_id"], data["text"]))
            return {"message_id": 1}
        with patch.object(tg, "_request", side_effect=request):
            tg.send_confirmation_message("run", "https://instagram.com/p/same")
        self.assertEqual(len(seen), 3)
        for creds in self.bots:
            self.assertTrue(any(creds["bot_token"] in url and chat == creds["chat_id"] for url, chat, _ in seen))
        self.assertEqual(len({text for _, _, text in seen}), 1)

    def test_no_bots_is_a_configuration_error_without_any_send(self):
        with patch.object(config, "all_credentials", return_value=[]), \
             patch.object(tg, "_send_confirmation_message") as send:
            with self.assertRaises(RuntimeError):
                tg.send_confirmation_message("run", "link")
            send.assert_not_called()


class RouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_route_targets_one_bot(self):
        with patch.object(config, "clear", AsyncMock()) as clear, \
             patch.object(routes, "_status", return_value={}):
            await routes.telegram_disconnect_bot("2", Identity(email="a@b.co", subject="a"))
        clear.assert_awaited_once_with("2")
