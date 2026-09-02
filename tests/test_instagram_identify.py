"""Identifying the account behind a token somebody pasted.

The OAuth flow never has to ask this question: Instagram hands back a token
AND the identity in the same exchange. A pasted token arrives naked, so the
account has to be discovered from the token itself - and the answer decides
which Graph host every later publish call must use, which is not something
that can be guessed after the fact. Sending an Instagram Login token to
``graph.facebook.com`` fails at publish time with a permissions error that
names nothing useful.

Two kinds of token reach this door:

* one minted by Instagram Login (``IGAA...``), which answers ``/me`` on
  ``graph.instagram.com`` and needs no id pasted alongside it;
* one minted through Facebook Login (``EAA...``), which does NOT answer that
  ``/me`` - the node has to be addressed by its Instagram user id on
  ``graph.facebook.com``.

So the pasted id is not decoration. It is the only thing that makes the
second kind connectable, and a guard against connecting the wrong account
with the first.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services import instagram_accounts
from app.services import instagram_oauth as oauth

INSTAGRAM_IDENTITY = {
    "user_id": "17841400000000000",
    "username": "haroontrailblazer",
    "name": "Haroon",
    "profile_picture_url": "https://cdn.example.com/pic.jpg",
}


def _refuses(message: str = "Invalid OAuth access token."):
    """A Meta error body, in the shape the graph endpoints use."""
    return {"error": {"message": message, "type": "OAuthException", "code": 190}}


class AuthKindsMatchTheStoredHostsTests(unittest.TestCase):
    """The strings this module returns are what the account table stores.

    ``instagram_accounts.GRAPH_HOSTS`` maps an auth kind to the host publishes
    are sent to. A kind that is not a key there silently falls back to the
    Instagram host, which is exactly the misroute this whole distinction
    exists to prevent - so the two vocabularies are pinned to each other here
    rather than left to agree by habit.
    """

    def test_both_kinds_are_known_to_the_account_store(self) -> None:
        self.assertIn(oauth.AUTH_KIND_INSTAGRAM, instagram_accounts.GRAPH_HOSTS)
        self.assertIn(oauth.AUTH_KIND_FACEBOOK, instagram_accounts.GRAPH_HOSTS)

    def test_the_instagram_kind_is_the_default_the_store_assumes(self) -> None:
        self.assertEqual(
            oauth.AUTH_KIND_INSTAGRAM, instagram_accounts.DEFAULT_AUTH_KIND
        )


class FetchIdentityByIdTests(unittest.TestCase):
    """Addressing the account node directly, on the Facebook host."""

    def test_asks_the_facebook_host_for_that_node(self) -> None:
        seen: dict = {}

        def fake_get(url: str, params: dict, timeout) -> dict:
            seen["url"] = url
            seen["params"] = params
            return {
                "id": "17841400000000000",
                "username": "haroontrailblazer",
                "name": "Haroon",
                "profile_picture_url": "https://cdn.example.com/pic.jpg",
            }

        with patch.object(oauth, "_get_json", fake_get):
            identity = oauth.fetch_identity_by_id(
                "17841400000000000", "EAA-token", api_version="v23.0"
            )

        self.assertEqual(
            seen["url"], "https://graph.facebook.com/v23.0/17841400000000000"
        )
        self.assertEqual(seen["params"]["access_token"], "EAA-token")
        self.assertEqual(identity["ig_user_id"], "17841400000000000")
        self.assertEqual(identity["username"], "haroontrailblazer")
        self.assertEqual(
            identity["profile_picture_url"], "https://cdn.example.com/pic.jpg"
        )

    def test_a_node_with_no_username_is_not_an_instagram_account(self) -> None:
        """A Facebook Page id answers this call too, with no username."""
        with patch.object(oauth, "_get_json", lambda url, params, timeout: {"id": "5"}):
            with self.assertRaises(oauth.OAuthError) as caught:
                oauth.fetch_identity_by_id("5", "EAA-token")
        self.assertEqual(caught.exception.code, "no_identity")

    def test_metas_refusal_is_passed_through_in_words(self) -> None:
        with patch.object(oauth, "_get_json", lambda url, params, timeout: _refuses()):
            with self.assertRaises(oauth.OAuthError) as caught:
                oauth.fetch_identity_by_id("17841400000000000", "EAA-token")
        self.assertIn("Invalid OAuth access token", caught.exception.message)


class IdentifyTests(unittest.TestCase):
    """Which host a pasted token speaks, decided by asking rather than guessing."""

    def test_an_instagram_login_token_needs_no_id(self) -> None:
        with patch.object(
            oauth, "_get_json", lambda url, params, timeout: dict(INSTAGRAM_IDENTITY)
        ):
            identity, kind = oauth.identify("IGAA-token")

        self.assertEqual(kind, oauth.AUTH_KIND_INSTAGRAM)
        self.assertEqual(identity["ig_user_id"], "17841400000000000")
        self.assertEqual(identity["username"], "haroontrailblazer")

    def test_a_matching_pasted_id_is_accepted(self) -> None:
        with patch.object(
            oauth, "_get_json", lambda url, params, timeout: dict(INSTAGRAM_IDENTITY)
        ):
            _, kind = oauth.identify("IGAA-token", "17841400000000000")
        self.assertEqual(kind, oauth.AUTH_KIND_INSTAGRAM)

    def test_a_pasted_id_that_disagrees_is_refused(self) -> None:
        """The guard that stops the wrong account being connected.

        A console with several Instagram accounts open in one browser hands
        out whichever token the dashboard was last showing. Someone pasting an
        id alongside it has said which account they MEANT; when Instagram
        names a different one, that is a mistake to report, not a preference
        to override.
        """
        with patch.object(
            oauth, "_get_json", lambda url, params, timeout: dict(INSTAGRAM_IDENTITY)
        ):
            with self.assertRaises(oauth.OAuthError) as caught:
                oauth.identify("IGAA-token", "17841499999999999")

        self.assertEqual(caught.exception.code, "id_mismatch")
        # The message has to name both, or it cannot be acted on.
        self.assertIn("17841400000000000", caught.exception.message)
        self.assertIn("17841499999999999", caught.exception.message)

    def test_a_mismatch_never_falls_through_to_the_facebook_host(self) -> None:
        """A wrong id must not become a second attempt that succeeds."""
        calls: list[str] = []

        def fake_get(url: str, params: dict, timeout) -> dict:
            calls.append(url)
            return dict(INSTAGRAM_IDENTITY)

        with patch.object(oauth, "_get_json", fake_get):
            with self.assertRaises(oauth.OAuthError):
                oauth.identify("IGAA-token", "17841499999999999")

        self.assertEqual(len(calls), 1)
        self.assertNotIn("graph.facebook.com", calls[0])

    def test_a_facebook_login_token_is_identified_by_the_pasted_id(self) -> None:
        def fake_get(url: str, params: dict, timeout) -> dict:
            if "graph.instagram.com" in url:
                return _refuses("This endpoint requires an Instagram Login token.")
            return {
                "id": "17841400000000000",
                "username": "haroontrailblazer",
                "name": "Haroon",
                "profile_picture_url": "",
            }

        with patch.object(oauth, "_get_json", fake_get):
            identity, kind = oauth.identify("EAA-token", "17841400000000000")

        self.assertEqual(kind, oauth.AUTH_KIND_FACEBOOK)
        self.assertEqual(identity["ig_user_id"], "17841400000000000")
        self.assertEqual(identity["username"], "haroontrailblazer")

    def test_without_an_id_there_is_no_second_attempt_to_make(self) -> None:
        """The first refusal is the whole answer, and it is Meta's own words."""
        calls: list[str] = []

        def fake_get(url: str, params: dict, timeout) -> dict:
            calls.append(url)
            return _refuses("Invalid OAuth access token.")

        with patch.object(oauth, "_get_json", fake_get):
            with self.assertRaises(oauth.OAuthError) as caught:
                oauth.identify("nonsense")

        self.assertEqual(len(calls), 1)
        self.assertIn("Invalid OAuth access token", caught.exception.message)

    def test_when_both_hosts_refuse_the_error_says_the_id_was_tried(self) -> None:
        with patch.object(oauth, "_get_json", lambda url, params, timeout: _refuses()):
            with self.assertRaises(oauth.OAuthError) as caught:
                oauth.identify("nonsense", "17841400000000000")
        self.assertIn("17841400000000000", caught.exception.message)


if __name__ == "__main__":
    unittest.main()
