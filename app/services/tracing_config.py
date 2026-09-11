"""Optional workspace tracing, configured in the console rather than .env."""
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from app.services import db, secret_box

CONFIG_KEY = "langfuse"
DEFAULT_BASE_URL = "https://cloud.langfuse.com"


@dataclass(frozen=True)
class TracingSettings:
    enabled: bool = False
    base_url: str = DEFAULT_BASE_URL
    public_key: str = field(default="", repr=False)
    secret_key: str = field(default="", repr=False)
    key_error: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.public_key and self.secret_key and not self.key_error)


def validate_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    try:
        url = urlsplit(value)
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or url.query or url.fragment or "\\" in value or any(c.isspace() for c in value)):
            raise ValueError
        _ = url.port
    except ValueError:
        raise ValueError("Enter the HTTPS base URL of your Langfuse server, without credentials or query parameters.") from None
    return value


def _decode(stored: dict) -> TracingSettings:
    public = stored.get("public_key_encrypted", "")
    secret = stored.get("secret_key_encrypted", "")
    pk, sk = secret_box.decrypt(public), secret_box.decrypt(secret)
    return TracingSettings(
        enabled=bool(stored.get("enabled")), base_url=stored.get("base_url") or DEFAULT_BASE_URL,
        public_key=pk, secret_key=sk, key_error=bool((public and not pk) or (secret and not sk)),
    )


async def load() -> TracingSettings:
    return _decode(await db.get_config(CONFIG_KEY, {}) or {})


async def load_for_run() -> TracingSettings:
    # Optional telemetry must never block a carousel or reuse stale credentials.
    try:
        return await load()
    except Exception:
        return TracingSettings()


async def save(config: TracingSettings, *, replace_credentials: bool = True) -> TracingSettings:
    public = secret_box.encrypt(config.public_key) if replace_credentials else ""
    secret = secret_box.encrypt(config.secret_key) if replace_credentials else ""
    def merge(previous):
        updated = dict(previous or {}) | {"enabled": config.enabled, "base_url": config.base_url}
        if replace_credentials:
            updated.update(public_key_encrypted=public, secret_key_encrypted=secret)
        return updated
    return _decode(await db.update_config(CONFIG_KEY, merge))


async def clear() -> TracingSettings:
    def remove(previous):
        return dict(previous or {}) | {"enabled": False, "public_key_encrypted": "", "secret_key_encrypted": ""}
    return _decode(await db.update_config(CONFIG_KEY, remove))


async def verify(config: TracingSettings) -> None:
    """Check credentials without exporting prompts or following a redirect."""
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
            response = await client.get(config.base_url + "/api/public/projects", auth=(config.public_key, config.secret_key))
        if response.status_code in (401, 403):
            raise ValueError("Langfuse rejected these keys. Check the project keys and server region.")
        if response.status_code != 200 or not isinstance(response.json().get("data"), list):
            raise RuntimeError
    except ValueError as exc:
        if str(exc).startswith("Langfuse rejected"):
            raise
        raise ValueError("Could not verify this Langfuse server. Check its URL and try again.") from None
    except Exception:
        raise ValueError("Could not reach Langfuse. Check the server URL and try again.") from None


def public_status(config: TracingSettings) -> dict:
    return {
        "enabled": config.enabled, "base_url": config.base_url,
        "key_configured": config.configured, "key_error": config.key_error,
        "secrets_ready": secret_box.configured(),
    }
