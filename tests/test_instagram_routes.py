"""Account management stays available; new connections use per-account tokens."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.services import instagram_oauth
from app.services.instagram_accounts import Account
from web_api import routes_settings
from web_api.auth import Identity

IDENTITY = Identity(email="someone@example.com", subject="someone@example.com")
SECRET = "session-secret-long-enough-for-hs256-padding-aaaa"


def _account(account_id: str = "acc-1", username: str = "acme") -> Account:
    return Account(
        id=account_id,
        ig_user_id="1784140000",
        username=username,
        name="Acme",
        avatar_key="",
        auth_kind="instagram_login",
        token="a-real-secret-token",
        token_expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        is_default=True,
        disabled=False,
        connected_by="someone@example.com",
        connected_at=datetime.now(timezone.utc),
        last_refreshed_at=None,
    )


def _settings(**overrides):
    base = {
        "ig_app_id": "app-123",
        "ig_app_secret": "app-secret",
        "public_base_url": "https://console.example.com",
        "session_secret": SECRET,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class StatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_status_never_carries_a_token(self) -> None:
        with patch.object(
            routes_settings.instagram_accounts, "listing", lambda: [_account().public()]
        ), patch.object(routes_settings, "settings", _settings()):
            status = await routes_settings.instagram_status(_identity=IDENTITY)

        rendered = repr(status)
        self.assertNotIn("a-real-secret-token", rendered)
        self.assertEqual(status["accounts"][0]["username"], "acme")
        self.assertTrue(status["app_configured"])

    async def test_status_says_when_the_meta_app_is_not_configured(self) -> None:
        with patch.object(routes_settings.instagram_accounts, "listing", lambda: []), \
             patch.object(routes_settings, "settings", _settings(ig_app_id="")):
            status = await routes_settings.instagram_status(_identity=IDENTITY)
        self.assertFalse(status["app_configured"])


class TokenOnlyRoutesTests(unittest.TestCase):
    def test_oauth_cannot_connect_accounts(self):
        paths = {route.path for route in routes_settings.router.routes}
        self.assertNotIn("/settings/instagram/authorize", paths)
        self.assertNotIn("/settings/instagram/callback", paths)
        self.assertIn("/settings/instagram/token", paths)


class ManagementTests(unittest.IsolatedAsyncioTestCase):
    async def test_setting_a_default_moves_the_flag(self) -> None:
        set_default = AsyncMock()
        with patch.object(
            routes_settings.instagram_accounts, "set_default", set_default
        ), patch.object(
            routes_settings.instagram_accounts, "listing", lambda: []
        ), patch.object(routes_settings, "settings", _settings()):
            await routes_settings.instagram_set_default(
                routes_settings.AccountRef(account_id="acc-2"), identity=IDENTITY
            )
        set_default.assert_awaited_once_with("acc-2")

    async def test_disconnecting_forgets_the_account(self) -> None:
        delete = AsyncMock()
        with patch.object(routes_settings.instagram_accounts, "delete", delete), \
             patch.object(routes_settings.instagram_accounts, "listing", lambda: []), \
             patch.object(routes_settings, "settings", _settings()):
            await routes_settings.instagram_disconnect("acc-3", identity=IDENTITY)
        delete.assert_awaited_once_with("acc-3")


if __name__ == "__main__":
    unittest.main()
