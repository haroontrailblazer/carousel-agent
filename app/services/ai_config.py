"""Workspace AI settings. Secrets stay encrypted at rest and out of sessions."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import cached_property

from app.services import db, secret_box

CONFIG_KEY = "ai"
DEFAULT_MODELS = {
    "planner_model": "openai/gpt-5.6-sol",
    "utility_model": "openai/gpt-5.4-mini",
    "phrasing_model": "openai/gpt-5.6-sol",
    "image_model": "gpt-image-2",
}
MODEL_FIELDS = tuple(DEFAULT_MODELS)


class AISettingsNotConfigured(RuntimeError):
    """The workspace needs an OpenAI credential saved in the settings UI."""


@dataclass(frozen=True)
class AISettings:
    api_key: str = field(repr=False)
    planner_model: str
    utility_model: str
    phrasing_model: str
    image_model: str
    key_source: str
    key_error: bool = False

    def require_key(self):
        if self.key_error:
            raise AISettingsNotConfigured("The saved OpenAI key cannot be decrypted. Save it again in Profile & settings > AI & models.")
        if not self.api_key:
            raise AISettingsNotConfigured("Save your OpenAI API key in Profile & settings > AI & models before starting a run.")

    @cached_property
    def client(self):
        from openai import OpenAI

        self.require_key()
        return OpenAI(api_key=self.api_key, max_retries=0)

    def close(self):
        client = self.__dict__.pop("client", None)
        if client is not None:
            client.close()


_cache: AISettings | None = None
_bound: ContextVar[AISettings | None] = ContextVar("ai_settings", default=None)


def _decode(stored: dict) -> AISettings:
    encrypted = stored.get("api_key_encrypted", "")
    key = secret_box.decrypt(encrypted) if encrypted else ""
    return AISettings(
        api_key=key,
        key_source="saved" if encrypted else "unset",
        key_error=bool(encrypted and not key),
        **{name: stored.get(name) or DEFAULT_MODELS[name] for name in MODEL_FIELDS},
    )


def current() -> AISettings:
    return _bound.get() or _cache or _decode({})


@contextmanager
def bind(config: AISettings):
    """Keep an invocation and its worker threads on one immutable configuration."""
    token = _bound.set(config)
    try:
        yield
    finally:
        _bound.reset(token)


async def load() -> AISettings:
    """Refresh from shared storage. A failed read must not silently change keys."""
    global _cache
    stored = await db.get_config(CONFIG_KEY, {})
    _cache = _decode(stored or {})
    return _cache


async def save(*, models: dict[str, str], api_key: str = "") -> AISettings:
    global _cache
    encrypted = secret_box.encrypt(api_key) if api_key else ""

    def merge(previous):
        updated = dict(previous or {})
        updated.update({name: models[name] for name in MODEL_FIELDS})
        if encrypted:
            updated["api_key_encrypted"] = encrypted
        return updated

    stored = await db.update_config(CONFIG_KEY, merge)
    _cache = _decode(stored)
    return _cache


def public_status(config: AISettings) -> dict:
    return {
        **{name: getattr(config, name) for name in MODEL_FIELDS},
        "key_configured": bool(config.api_key),
        "key_source": config.key_source,
        "key_error": config.key_error,
        "secrets_ready": secret_box.configured(),
    }
