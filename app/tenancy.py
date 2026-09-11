"""Trusted account scope, inherited by async tasks and asyncio.to_thread."""
from collections.abc import MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID

_owner = ContextVar("workspace_owner", default="")


def current() -> str:
    return _owner.get()


def require() -> str:
    owner = current()
    if not owner:
        raise PermissionError("A signed-in workspace is required")
    return owner


@contextmanager
def bind(owner: str):
    owner = str(UUID(str(owner)))
    token = _owner.set(owner)
    try:
        yield owner
    finally:
        _owner.reset(token)


class ScopedDict(MutableMapping):
    """Existing synchronous caches, partitioned by the verified account UUID."""
    def __init__(self):
        self.scopes = {}

    def _data(self):
        return self.scopes.setdefault(current(), {})

    def __getitem__(self, key): return self._data()[key]
    def __setitem__(self, key, value): self._data()[key] = value
    def __delitem__(self, key): del self._data()[key]
    def __iter__(self): return iter(self._data())
    def __len__(self): return len(self._data())
    def clear(self): self._data().clear()


async def owners():
    """Service-only scheduler inventory; never exposed as a user API."""
    from app.services import db
    client = await db.get_pool()
    return await client.system_rpc("carousel_workspace_owners")
