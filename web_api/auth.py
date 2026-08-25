"""Authentication for the console, the API, and the mounted ADK dev UI.

**Why a cookie and not just a bearer token.** ``EventSource`` cannot set
headers at all, and the live run stream needs authenticating. The alternative
is a token in the query string, which writes the credential into every access
log and proxy trace along the way.

So the browser authenticates with Supabase directly, posts that token ONCE to
``/api/auth/session``, and receives our own short-lived httpOnly cookie. Every
request after that rides the cookie. A bearer token is still accepted, for
curl, CI and scripts.

**Why our own cookie rather than storing Supabase's token in one.** Supabase
access tokens last an hour and are refreshed client-side, so we would be
re-minting constantly; ours carries the role from ``app_users`` so
authorisation needs no database round trip per request; and verifying our own
HS256 token is local work rather than a JWKS fetch on the hot path.

**Why raw ASGI and not BaseHTTPMiddleware.** BaseHTTPMiddleware pulls the
response body through a memory stream, which defeats incremental flushing - the
live run stream would only appear once the run had already finished.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Literal, Optional
from urllib.parse import quote

import jwt
from starlette.responses import JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import settings
from app.services import db

logger = logging.getLogger(__name__)

#: Name of our session cookie. ``__Host-`` would be stricter but forbids a
#: Domain attribute and requires Secure, which breaks plain-HTTP localhost
#: development; the attributes set in ``session_cookie_headers`` cover the same
#: ground for this deployment shape.
COOKIE_NAME = "carousel_session"

#: Paths that must work with no credentials at all.
#:
#: ``/review-api`` is here deliberately: the Approve/Reject links are opened
#: from a Telegram message where there is nobody to log in. That is acceptable
#: because those URLs are capability URLs on an unguessable run id, the GET
#: pages change nothing, and the POST is single-use once the pending review is
#: claimed atomically.
ALWAYS_OPEN = (
    "/healthz",
    "/health",
    "/review-api",
    "/api/auth/config",
    "/api/auth/session",
)

#: Prefixes that require an identity. Everything else falls through to the
#: static SPA bundle, which must load unauthenticated - otherwise the browser
#: can never render a login screen to get a credential in the first place.
PROTECTED = ("/api/",)


#: Minimum session-secret length. PyJWT warns below 32 bytes for HS256, and a
#: short secret here is not a style issue - anyone who can guess it can mint a
#: session cookie for any allowlisted email.
MIN_SESSION_SECRET_BYTES = 32


def validate_session_secret(secret: str) -> list[str]:
    """Problems with the configured session secret, worst first.

    Returned rather than raised so startup can log every problem at once and
    still boot in development, where the console is expected to be unusable
    until configured.
    """
    problems: list[str] = []
    if not secret:
        problems.append(
            "SESSION_SECRET is not set - nobody can sign in. Generate one with "
            "`python -c \"import secrets; print(secrets.token_urlsafe(48))\"`."
        )
        return problems
    if len(secret.encode("utf-8")) < MIN_SESSION_SECRET_BYTES:
        problems.append(
            f"SESSION_SECRET is shorter than {MIN_SESSION_SECRET_BYTES} bytes; "
            "anyone who guesses it can mint a session for any allowed user."
        )
    if secret.lower() in ("changeme", "secret", "dev", "test", "password"):
        problems.append("SESSION_SECRET is a placeholder value.")
    return problems


class AuthError(Exception):
    """A credential was missing, malformed, expired or not allowed."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Identity:
    """Who is making a request."""

    email: str
    subject: str
    role: str = "reviewer"
    source: Literal["cookie", "bearer"] = "cookie"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class SupabaseJWTVerifier:
    """Verifies a Supabase access token, by JWKS or shared secret.

    Supabase signs tokens one of two ways depending on how old the project is:
    a shared HS256 secret, or asymmetric keys published at
    ``/auth/v1/.well-known/jwks.json``. Supporting only one means auth breaks
    on whichever project type was not tested, so both are handled - the
    presence of ``SUPABASE_JWT_SECRET`` selects HS256.
    """

    def __init__(
        self,
        *,
        supabase_url: str = "",
        jwt_secret: str = "",
        audience: str = "authenticated",
    ) -> None:
        self._supabase_url = (supabase_url or "").rstrip("/")
        self._jwt_secret = jwt_secret or ""
        self._audience = audience
        self._jwks_client: Any = None

    @property
    def configured(self) -> bool:
        return bool(self._jwt_secret or self._supabase_url)

    def _jwks(self) -> Any:
        if self._jwks_client is None:
            from jwt import PyJWKClient

            self._jwks_client = PyJWKClient(
                f"{self._supabase_url}/auth/v1/.well-known/jwks.json",
                cache_keys=True,
            )
        return self._jwks_client

    def verify(self, token: str) -> dict:
        """Verify and decode a Supabase access token.

        Raises:
            AuthError: for any token we will not accept, with a code the API
                can branch on rather than matching message text.
        """
        if not token:
            raise AuthError("no_token", "No access token supplied.")
        if not self.configured:
            raise AuthError(
                "auth_not_configured",
                "Set SUPABASE_URL (and SUPABASE_JWT_SECRET for a legacy "
                "project) before signing in.",
            )
        try:
            if self._jwt_secret:
                claims = jwt.decode(
                    token,
                    self._jwt_secret,
                    algorithms=["HS256"],
                    audience=self._audience,
                )
            else:
                signing_key = self._jwks().get_signing_key_from_jwt(token)
                claims = jwt.decode(
                    token,
                    signing_key.key,
                    algorithms=["RS256", "ES256"],
                    audience=self._audience,
                )
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("token_expired", "That sign-in has expired.") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthError("token_invalid", f"Invalid access token: {exc}") from exc
        except Exception as exc:  # JWKS fetch failure, etc.
            logger.warning("Supabase token verification failed: %s", exc)
            raise AuthError(
                "verification_failed",
                "Could not verify the sign-in with Supabase.",
            ) from exc

        email = str(claims.get("email") or "").strip().lower()
        subject = str(claims.get("sub") or "")
        if not email:
            raise AuthError("no_email", "That sign-in carries no email address.")
        return {"email": email, "subject": subject, "claims": claims}


def issue_session_token(identity: Identity, *, ttl_s: int, secret: str) -> str:
    """Mint our own signed session token."""
    now = int(time.time())
    return jwt.encode(
        {
            "sub": identity.subject,
            "email": identity.email,
            "role": identity.role,
            "iat": now,
            "exp": now + int(ttl_s),
        },
        secret,
        algorithm="HS256",
    )


def read_session_token(raw: str, *, secret: str) -> Optional[Identity]:
    """Decode our session token, or ``None`` if it is not usable.

    Returns ``None`` rather than raising: an expired or tampered cookie is an
    ordinary event (someone left a tab open overnight), and the caller's answer
    is the same in every case - ask them to sign in again.
    """
    if not raw or not secret:
        return None
    try:
        claims = jwt.decode(raw, secret, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None
    email = str(claims.get("email") or "")
    if not email:
        return None
    return Identity(
        email=email,
        subject=str(claims.get("sub") or ""),
        role=str(claims.get("role") or "reviewer"),
        source="cookie",
    )


def session_cookie_headers(token: str, *, ttl_s: int, secure: bool) -> list[tuple[bytes, bytes]]:
    """Set-Cookie for a new session.

    ``HttpOnly`` keeps the token out of reach of any script on the page, which
    matters because artifact previews and captions are rendered from
    model-generated content. ``SameSite=Lax`` still sends the cookie on
    top-level navigation, so following a link into the console works, while
    blocking it on cross-site POSTs.
    """
    attrs = [
        f"{COOKIE_NAME}={token}",
        "Path=/",
        "HttpOnly",
        "SameSite=Lax",
        f"Max-Age={int(ttl_s)}",
    ]
    if secure:
        attrs.append("Secure")
    return [(b"set-cookie", "; ".join(attrs).encode("latin-1"))]


def clear_cookie_headers(*, secure: bool) -> list[tuple[bytes, bytes]]:
    """Set-Cookie that removes the session."""
    attrs = [f"{COOKIE_NAME}=", "Path=/", "HttpOnly", "SameSite=Lax", "Max-Age=0"]
    if secure:
        attrs.append("Secure")
    return [(b"set-cookie", "; ".join(attrs).encode("latin-1"))]


async def authorize_email(email: str) -> Identity:
    """Check the allowlist and build the identity to put in the cookie.

    The primary gate is Supabase itself, where public signup is off and users
    are created by invite. This is the second gate: even a valid Supabase token
    from a stranger gets nothing here. A disabled user is told their access was
    revoked rather than that they do not exist - they know they had an account,
    and pretending otherwise just generates a support message.
    """
    clean = (email or "").strip().lower()
    try:
        row = await db.get_app_user(clean)
    except Exception as exc:
        logger.warning("Allowlist lookup failed for %s: %s", clean, exc)
        raise AuthError(
            "allowlist_unavailable",
            "Could not check access rights right now. Try again shortly.",
        ) from exc

    if row is None:
        raise AuthError(
            "not_allowed",
            f"{clean} is not on the access list for this console.",
        )
    if row.get("disabled"):
        raise AuthError("access_revoked", "Your access to this console was revoked.")
    return Identity(
        email=clean, subject=clean, role=str(row.get("role") or "reviewer")
    )


def _cookie_value(scope: Scope, name: str) -> str:
    """Read one cookie from a raw ASGI scope."""
    for key, value in scope.get("headers") or []:
        if key != b"cookie":
            continue
        for chunk in value.decode("latin-1").split(";"):
            k, _, v = chunk.strip().partition("=")
            if k == name:
                return v
    return ""


def _header(scope: Scope, name: bytes) -> str:
    for key, value in scope.get("headers") or []:
        if key == name:
            return value.decode("latin-1")
    return ""


def _wants_html(scope: Scope) -> bool:
    """Is this a browser navigating, rather than a script calling the API?

    A navigation should be redirected to the login screen; an XHR should get a
    401 it can handle. Sending JSON to someone who clicked a /dev bookmark is a
    dead end, and redirecting an XHR turns an auth failure into a confusing
    200-with-HTML.
    """
    if _header(scope, b"sec-fetch-mode") == "navigate":
        return True
    return "text/html" in _header(scope, b"accept")


class AuthMiddleware:
    """Raw-ASGI gate over the whole application."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        verifier: SupabaseJWTVerifier,
        secret: str,
        always_open: tuple[str, ...] = ALWAYS_OPEN,
        protected: tuple[str, ...] = PROTECTED,
        login_path: str = "/login",
        secure_cookies: bool = True,
    ) -> None:
        self.app = app
        self._verifier = verifier
        self._secret = secret
        self._always_open = always_open
        self._protected = protected
        self._login_path = login_path
        self._secure_cookies = secure_cookies

    def _is_open(self, path: str) -> bool:
        return any(path == p or path.startswith(p + "/") for p in self._always_open)

    def _is_protected(self, path: str) -> bool:
        """Does this path require an identity?

        A prefix ending in "/" matches by prefix ("/api/" covers "/api/runs").
        One without matches the path itself or anything beneath it, so "/admin"
        would cover "/admin" and "/admin/users" but not "/administrator".
        """
        for prefix in self._protected:
            if prefix.endswith("/"):
                if path.startswith(prefix):
                    return True
            elif path == prefix or path.startswith(prefix + "/"):
                return True
        return False

    async def _identify(self, scope: Scope) -> Optional[Identity]:
        cookie = _cookie_value(scope, COOKIE_NAME)
        identity = read_session_token(cookie, secret=self._secret)
        if identity is not None:
            return identity

        header = _header(scope, b"authorization")
        if header.lower().startswith("bearer "):
            token = header[7:].strip()
            try:
                verified = self._verifier.verify(token)
                identity = await authorize_email(verified["email"])
                return Identity(
                    email=identity.email,
                    subject=identity.subject,
                    role=identity.role,
                    source="bearer",
                )
            except AuthError as exc:
                logger.info("Bearer authentication refused: %s", exc.code)
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or ""
        if self._is_open(path) or not self._is_protected(path):
            await self.app(scope, receive, send)
            return

        identity = await self._identify(scope)
        if identity is not None:
            scope["carousel_identity"] = identity
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            # A websocket must be refused with a close frame; sending an HTTP
            # response into a websocket scope raises inside the server.
            await send({"type": "websocket.close", "code": 1008})
            return

        if _wants_html(scope):
            target = f"{self._login_path}?next={quote(path, safe='')}"
            response = RedirectResponse(target, status_code=302)
        else:
            response = JSONResponse(
                {"error": "unauthenticated", "code": "unauthenticated"},
                status_code=401,
            )
        await response(scope, receive, send)


def build_verifier() -> SupabaseJWTVerifier:
    """The verifier configured from settings."""
    return SupabaseJWTVerifier(
        supabase_url=settings.supabase_url,
        jwt_secret=settings.supabase_jwt_secret,
    )


__all__ = [
    "ALWAYS_OPEN",
    "COOKIE_NAME",
    "PROTECTED",
    "AuthError",
    "AuthMiddleware",
    "Identity",
    "SupabaseJWTVerifier",
    "authorize_email",
    "validate_session_secret",
    "build_verifier",
    "clear_cookie_headers",
    "issue_session_token",
    "read_session_token",
    "session_cookie_headers",
]
