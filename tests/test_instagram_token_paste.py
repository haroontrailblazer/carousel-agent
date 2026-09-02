"""Connecting an Instagram account by pasting its access token.

WHY THIS EXISTS ALONGSIDE OAUTH

The Connect button is the better door and stays the default: Instagram hands
back a token, its real lifetime, the identity and the picture in one exchange,
and nobody handles a credential. But it cannot always be opened. It needs a
Meta app (``IG_APP_ID``/``IG_APP_SECRET``), a public HTTPS address Meta has
allowlisted, and - for any account that is not a listed tester - App Review
with Advanced Access and Business Verification behind it.

Pasting a token needs none of that. A token generated in the Meta dashboard,
or in the Graph API Explorer, connects an account on a laptop with no public
URL at all. That is the entire point of this route, and the tests below hold
it to it: **the paste path must keep working when the Meta app is not
configured**, because that is the situation it was added for.

What it must NOT do is lower the bar on storage. The token is still Fernet-
encrypted or refused, still keyed on the Instagram user id so reconnecting
replaces rather than duplicates, and still never echoed back to the browser.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.services import instagram_oauth, secret_box
from app.services.instagram_accounts import Account
from web_api import routes_settings
from web_api.auth import Identity

IDENTITY = Identity(email="someone@example.com", subject="someone@example.com")

PASTED = (
    "IGAAWwgwspBZChBZAFowbTZAYeEE2b2xDOHJsdjZAqYTc4d2JWYTRNbm9MWjJtclQxQzdY"
)

DISCOVERED = {
    "ig_user_id": "17841400000000000",
    "username": "haroontrailblazer",
    "name": "Haroon",
    "profile_picture_url": "https://cdn.example.com/pic.jpg",
}


def _account(**overrides) -> Account:
    base = dict(
        id="acc-1",
        ig_user_id="17841400000000000",
        username="haroontrailblazer",
        name="Haroon",
        avatar_key="instagram/17841400000000000.png",
        auth_kind="instagram_login",
        token="the-real-secret-token",
        token_expires_at=datetime.now(timezone.utc) + timedelta(days=60),
        is_default=True,
        disabled=False,
        connected_by="someone@example.com",
        connected_at=datetime.now(timezone.utc),
        last_refreshed_at=None,
    )
    base.update(overrides)
    return Account(**base)


def _settings(**overrides):
    """Deliberately NO Meta app credentials and NO public URL.

    settings is a frozen dataclass, so the whole object is swapped rather than
    a field patched. Every test in this file runs against a console that could
    not start an OAuth flow if it wanted to - which is the case this route is
    for.
    """
    base = {
        "ig_app_id": "",
        "ig_app_secret": "",
        "public_base_url": "",
        "session_secret": "s" * 48,
        "ig_api_version": "v23.0",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _Harness:
    """The patches every happy-path test needs, in one place."""

    def __init__(
        self,
        *,
        identify=None,
        refresh=None,
        saved: AsyncMock | None = None,
        secrets_ready: bool = True,
        settings_obj=None,
    ) -> None:
        self.saved = saved or AsyncMock(return_value=_account())
        self._identify = identify or (
            lambda token, ig_user_id="", **kw: (
                dict(DISCOVERED),
                instagram_oauth.AUTH_KIND_INSTAGRAM,
            )
        )
        self._refresh = refresh or (lambda token: ("refreshed-token", 5183944))
        self._secrets_ready = secrets_ready
        self._settings = settings_obj or _settings()
        self._patches: list = []

    def __enter__(self) -> "_Harness":
        self._patches = [
            patch.object(routes_settings, "settings", self._settings),
            patch.object(
                routes_settings.secret_box, "configured", lambda: self._secrets_ready
            ),
            patch.object(routes_settings.instagram_oauth, "identify", self._identify),
            patch.object(
                routes_settings.instagram_oauth, "refresh_long_lived", self._refresh
            ),
            patch.object(
                routes_settings.instagram_oauth, "fetch_avatar", lambda url: b"pic"
            ),
            patch.object(
                routes_settings,
                "_store_avatar",
                AsyncMock(return_value="instagram/17841400000000000.png"),
            ),
            patch.object(routes_settings.instagram_accounts, "save", self.saved),
            patch.object(routes_settings.instagram_accounts, "listing", lambda: []),
        ]
        for item in self._patches:
            item.start()
        return self

    def __exit__(self, *exc) -> None:
        for item in reversed(self._patches):
            item.stop()


async def _connect(token: str = PASTED, ig_user_id: str = "") -> dict:
    return await routes_settings.instagram_connect_token(
        routes_settings.InstagramTokenRequest(token=token, ig_user_id=ig_user_id),
        identity=IDENTITY,
    )


class ConnectingTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_pasted_token_connects_the_account_it_belongs_to(self) -> None:
        with _Harness() as harness:
            response = await _connect()

        self.assertEqual(response["result"], "connected")
        self.assertEqual(response["account"]["handle"], "@haroontrailblazer")

        kwargs = harness.saved.await_args.kwargs
        self.assertEqual(kwargs["ig_user_id"], "17841400000000000")
        self.assertEqual(kwargs["username"], "haroontrailblazer")
        self.assertEqual(kwargs["auth_kind"], instagram_oauth.AUTH_KIND_INSTAGRAM)
        # Who pasted it, from the session - not from anything in the request.
        self.assertEqual(kwargs["connected_by"], "someone@example.com")

    async def test_the_response_never_carries_the_token(self) -> None:
        """The console shows a handle and a picture. Never the credential."""
        with _Harness():
            response = await _connect()
        rendered = repr(response)
        self.assertNotIn(PASTED, rendered)
        self.assertNotIn("refreshed-token", rendered)
        self.assertNotIn("the-real-secret-token", rendered)

    async def test_it_works_with_no_meta_app_and_no_public_url(self) -> None:
        """The reason this route exists. A 503 here would defeat the point."""
        with _Harness() as harness:
            response = await _connect()
        self.assertEqual(response["result"], "connected")
        self.assertFalse(response["app_configured"])
        harness.saved.assert_awaited_once()

    async def test_surrounding_whitespace_is_stripped(self) -> None:
        """Tokens arrive from a dashboard by copy-paste, newline and all."""
        seen: dict = {}

        def identify(token, ig_user_id="", **kw):
            seen["token"] = token
            seen["ig_user_id"] = ig_user_id
            return dict(DISCOVERED), instagram_oauth.AUTH_KIND_INSTAGRAM

        with _Harness(identify=identify):
            await _connect(token=f"  {PASTED}\n", ig_user_id=" 17841400000000000 ")

        self.assertEqual(seen["token"], PASTED)
        self.assertEqual(seen["ig_user_id"], "17841400000000000")

    async def test_an_empty_token_is_refused_before_meta_is_asked(self) -> None:
        called: list[str] = []

        def identify(token, ig_user_id="", **kw):
            called.append(token)
            raise AssertionError("Meta must not be asked about an empty token.")

        with _Harness(identify=identify):
            with self.assertRaises(HTTPException) as caught:
                await _connect(token="   ")

        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(caught.exception.detail["code"], "bad_token")
        self.assertEqual(called, [])

    async def test_an_id_that_is_not_a_number_is_refused_by_the_schema(self) -> None:
        """Instagram user ids are numeric; anything else is a paste error."""
        with self.assertRaises(ValidationError):
            routes_settings.InstagramTokenRequest(
                token=PASTED, ig_user_id="@haroontrailblazer"
            )


class ExpiryTests(unittest.IsolatedAsyncioTestCase):
    """A pasted token carries no issue date, so its expiry has to be earned."""

    async def test_a_refreshable_token_gets_a_known_expiry(self) -> None:
        """Refreshing turns a guess into a fact, and extends the token too.

        ``ig_refresh_token`` answers with a fresh 60-day token and the exact
        seconds remaining. That is strictly better than assuming: a token
        pasted on day 50 of its life would otherwise be recorded as having 60
        days left, and the nightly refresh job - which only looks at tokens
        expiring within a fortnight - would not touch it until long after it
        had died.
        """
        with _Harness() as harness:
            response = await _connect()

        kwargs = harness.saved.await_args.kwargs
        self.assertEqual(kwargs["token"], "refreshed-token")
        self.assertEqual(kwargs["expires_in"], 5183944)
        self.assertEqual(response["expiry"], "confirmed")

    async def test_a_token_meta_will_not_refresh_is_still_stored(self) -> None:
        """Under 24 hours old, Meta refuses to refresh. Not a failure to connect.

        The token was already proven to work by the identity lookup, so the
        only thing lost is certainty about the expiry - recorded as the 60 days
        a long-lived token gets, and reported as an assumption.
        """

        def refuses(token):
            raise instagram_oauth.OAuthError("instagram_error", "Too new to refresh.")

        with _Harness(refresh=refuses) as harness:
            response = await _connect()

        kwargs = harness.saved.await_args.kwargs
        self.assertEqual(kwargs["token"], PASTED)
        self.assertEqual(
            kwargs["expires_in"], routes_settings.ASSUMED_LIFETIME_DAYS * 86400
        )
        self.assertEqual(response["expiry"], "assumed")

    async def test_a_refresh_answering_with_no_time_left_is_not_believed(self) -> None:
        """``expires_in: 0`` would record an account that is dead on arrival."""
        with _Harness(refresh=lambda token: ("odd-token", 0)) as harness:
            response = await _connect()

        kwargs = harness.saved.await_args.kwargs
        self.assertEqual(kwargs["token"], PASTED)
        self.assertEqual(
            kwargs["expires_in"], routes_settings.ASSUMED_LIFETIME_DAYS * 86400
        )
        self.assertEqual(response["expiry"], "assumed")

    async def test_a_facebook_login_token_is_never_sent_to_the_refresh_endpoint(
        self,
    ) -> None:
        """``ig_refresh_token`` only exists for Instagram Login tokens."""
        calls: list[str] = []

        def refresh(token):
            calls.append(token)
            return "should-not-happen", 5183944

        def identify(token, ig_user_id="", **kw):
            return dict(DISCOVERED), instagram_oauth.AUTH_KIND_FACEBOOK

        with _Harness(identify=identify, refresh=refresh) as harness:
            response = await _connect(ig_user_id="17841400000000000")

        self.assertEqual(calls, [])
        kwargs = harness.saved.await_args.kwargs
        self.assertEqual(kwargs["auth_kind"], instagram_oauth.AUTH_KIND_FACEBOOK)
        self.assertEqual(kwargs["token"], PASTED)
        self.assertEqual(response["expiry"], "assumed")


class RefusalTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_token_instagram_rejects_is_reported_in_metas_words(self) -> None:
        def identify(token, ig_user_id="", **kw):
            raise instagram_oauth.OAuthError(
                "instagram_error", "Instagram refused: Invalid OAuth access token."
            )

        with _Harness(identify=identify) as harness:
            with self.assertRaises(HTTPException) as caught:
                await _connect()

        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(caught.exception.detail["code"], "instagram_error")
        self.assertIn("Invalid OAuth access token", caught.exception.detail["message"])
        harness.saved.assert_not_awaited()

    async def test_an_id_that_disagrees_saves_nothing(self) -> None:
        def identify(token, ig_user_id="", **kw):
            raise instagram_oauth.OAuthError(
                "id_mismatch", "That token belongs to 17841400000000000."
            )

        with _Harness(identify=identify) as harness:
            with self.assertRaises(HTTPException) as caught:
                await _connect(ig_user_id="17841499999999999")

        self.assertEqual(caught.exception.detail["code"], "id_mismatch")
        harness.saved.assert_not_awaited()

    async def test_without_a_secrets_key_nothing_is_sent_to_meta_at_all(self) -> None:
        """Refuse first: there is no point identifying a token we cannot store.

        And storing it in the clear is not on offer - that is the whole reason
        these tokens live encrypted in the database rather than in .env.
        """
        called: list[str] = []

        def identify(token, ig_user_id="", **kw):
            called.append(token)
            return dict(DISCOVERED), instagram_oauth.AUTH_KIND_INSTAGRAM

        with _Harness(identify=identify, secrets_ready=False) as harness:
            with self.assertRaises(HTTPException) as caught:
                await _connect()

        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(caught.exception.detail["code"], "secrets_unconfigured")
        self.assertEqual(called, [])
        harness.saved.assert_not_awaited()

    async def test_a_storage_layer_refusal_becomes_a_503_not_a_crash(self) -> None:
        saved = AsyncMock(side_effect=secret_box.SecretsNotConfigured("no key"))
        with _Harness(saved=saved):
            with self.assertRaises(HTTPException) as caught:
                await _connect()
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(caught.exception.detail["code"], "secrets_unconfigured")


if __name__ == "__main__":
    unittest.main()
