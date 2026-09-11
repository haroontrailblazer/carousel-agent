"""Bounded, process-local snapshots validated against a database revision.

Every read checks the revision; this is not a time-based stale-data cache.
Entries never cross database pools or session scopes. Concurrent readers of
one snapshot share the fetch, while different sessions can load in parallel.
"""
import asyncio
from collections import OrderedDict
from copy import deepcopy
import json
from app import tenancy
from weakref import WeakValueDictionary


class RevisionCache:
    def __init__(self, max_bytes=16 * 1024 * 1024, max_entries=64):
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self.entries = OrderedDict()
        self.locks = WeakValueDictionary()
        self.bytes = 0

    async def read(self, key, revision, fetch):
        key = (tenancy.current(), key)
        lock = self.locks.setdefault(key, asyncio.Lock())
        async with lock:
            stamp = await revision()
            previous = self.entries.get(key)
            if previous and previous[0] == stamp:
                self.entries.move_to_end(key)
                return deepcopy(previous[1])
            value = await fetch()
            size = len(json.dumps(value, ensure_ascii=False).encode('utf-8'))
            if previous:
                self.bytes -= self.entries.pop(key)[2]
            if size <= self.max_bytes:
                self.entries[key] = (stamp, deepcopy(value), size)
                self.bytes += size
                while self.bytes > self.max_bytes or len(self.entries) > self.max_entries:
                    self.bytes -= self.entries.popitem(last=False)[1][2]
            return value
