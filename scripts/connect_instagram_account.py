"""Connect an Instagram account from a token you already hold.

    python -m scripts.connect_instagram_account <access-token> [--days 60]

WHY THIS EXISTS

There are two better doors, and both need a browser. Profile -> Instagram ->
Connect runs the OAuth flow, which hands back a token, its real lifetime, the
identity and the picture in one exchange. Failing that - no Meta app, no public
URL, no Advanced Access - the same page has a "Paste an access token" form,
which asks Instagram who the token belongs to and then tries
``ig_refresh_token`` so the expiry it records is a fact rather than a guess.

This script is what is left when there is no browser at all: a headless
deployment, a container being seeded, a terminal on the far end of an SSH
session.

It deliberately writes through ``instagram_accounts.save`` rather than touching
the table, so the token is Fernet-encrypted exactly as the OAuth path encrypts
it, the first account still becomes the default, and reconnecting the same
``ig_user_id`` replaces its token instead of creating a duplicate row.

WHAT IT CANNOT KNOW

A token pasted in has no discoverable issue date, and Meta exposes no
introspection endpoint, so the expiry recorded here is an ASSUMPTION: 60 days,
which is what a long-lived token gets. If the token was already old, the
recorded expiry is optimistic and the refresh job may find it already lapsed -
which it reports rather than hides. Pass ``--days`` when you know better.

The console form does not have that problem, because it refreshes first. Prefer
it whenever a browser is available.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import avatar_store, instagram_accounts, instagram_oauth, secret_box  # noqa: E402

#: What Meta issues a long-lived Instagram token. Not discoverable from the
#: token itself - see the module docstring.
ASSUMED_LIFETIME_DAYS = 60


async def _store_avatar(ig_user_id: str, url: str) -> str:
    """Download the profile picture into the media bucket; return its key.

    Best effort, exactly as the OAuth callback treats it: the picture becomes
    the favicon on every slide's brand rail, but failing to fetch it must not
    fail the connection - the rail falls back to a generated monogram.
    """
    if not url:
        return ""
    payload = await asyncio.to_thread(instagram_oauth.fetch_avatar, url)
    if not payload:
        print("  ! profile picture could not be fetched; the rail will use a monogram")
        return ""
    from io import BytesIO

    from PIL import Image

    try:
        with Image.open(BytesIO(payload)) as src:
            buf = BytesIO()
            src.convert("RGBA").save(buf, format="PNG")
        return await avatar_store.save_at(
            f"instagram/{ig_user_id}.png", buf.getvalue(), "image/png"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  ! profile picture could not be stored ({exc}); using a monogram")
        return ""


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("token", help="a long-lived Instagram access token")
    parser.add_argument(
        "--days",
        type=int,
        default=ASSUMED_LIFETIME_DAYS,
        help=f"assumed remaining lifetime (default {ASSUMED_LIFETIME_DAYS})",
    )
    parser.add_argument(
        "--connected-by", default="script", help="recorded in the audit trail"
    )
    args = parser.parse_args()

    if not secret_box.configured():
        print("SECRETS_KEY is not set - refusing to store a token unencrypted.")
        return 2

    print("Asking Instagram who this token belongs to...")
    try:
        who = await asyncio.to_thread(instagram_oauth.fetch_identity, args.token)
    except instagram_oauth.OAuthError as exc:
        print(f"Instagram refused the token: {exc.message}")
        return 1

    print(f"  @{who['username']}  ({who['name'] or 'no display name'})")
    print(f"  ig_user_id: {who['ig_user_id']}")

    avatar_key = await _store_avatar(who["ig_user_id"], who["profile_picture_url"])
    if avatar_key:
        print(f"  profile picture stored at {avatar_key}")

    await instagram_accounts.load()
    account = await instagram_accounts.save(
        ig_user_id=who["ig_user_id"],
        username=who["username"],
        name=who["name"],
        token=args.token,
        expires_in=args.days * 24 * 3600,
        connected_by=args.connected_by,
        avatar_key=avatar_key,
    )

    print()
    print(f"Connected {account.handle}")
    print(f"  id       : {account.id}")
    print(f"  default  : {account.is_default}")
    print(f"  expires  : {account.token_expires_at} (ASSUMED {args.days} days)")
    print(f"  usable   : {account.usable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
