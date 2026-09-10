"""The review action follows this run's account, never another default."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.state import K_ACCOUNT_ID
from web_api import routes_runs as routes


class ReviewDeliveryTargetTests(unittest.IsolatedAsyncioTestCase):
    async def detail(self, account_id, account):
        with patch.object(routes.db, "get_run", AsyncMock(return_value={"run_id": "test", "phase": "review"})), \
             patch.object(routes, "_session_state", AsyncMock(return_value={K_ACCOUNT_ID: account_id})), \
             patch.object(routes.db, "load_pending_review", AsyncMock(return_value={})), \
             patch.object(routes, "_trace_length", AsyncMock(return_value=0)), \
             patch.object(routes.instagram_accounts, "get", return_value=account) as get_account:
            result = await routes.get_run("test")
        return result, get_account

    async def test_unconnected_run_offers_send_even_if_other_accounts_exist(self):
        result, get_account = await self.detail("", SimpleNamespace(usable=True))
        self.assertEqual(result["delivery_target"], "telegram")
        self.assertFalse(result["publish_configured"])
        get_account.assert_not_called()

    async def test_connected_run_offers_publish_for_its_own_account(self):
        result, get_account = await self.detail("account-a", SimpleNamespace(usable=True))
        self.assertEqual(result["delivery_target"], "instagram")
        self.assertTrue(result["publish_configured"])
        get_account.assert_called_once_with("account-a")

    async def test_expired_or_removed_account_cannot_publish_or_silently_send(self):
        for account in (None, SimpleNamespace(usable=False)):
            result, _ = await self.detail("account-a", account)
            self.assertEqual(result["delivery_target"], "instagram")
            self.assertFalse(result["publish_configured"])
