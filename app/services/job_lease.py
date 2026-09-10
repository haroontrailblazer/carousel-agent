"""Renewable, owner-checked job leases for independent HTTPS requests."""
import asyncio
from contextlib import asynccontextmanager
import logging
import uuid

from app.services import db

logger = logging.getLogger(__name__)


@asynccontextmanager
async def job_lease(name: str):
    client = await db.get_pool()
    owner = uuid.uuid4().hex
    acquired = await client.rpc('carousel_job_lease', {'job':name,'owner':owner,'action':'acquire'})
    if not acquired:
        yield False
        return
    parent = asyncio.current_task()
    async def renew():
        while True:
            await asyncio.sleep(30)
            try:
                okay = await client.rpc('carousel_job_lease', {'job':name,'owner':owner,'action':'renew'})
            except Exception:
                okay = False
            if not okay:
                logger.error('Lost scheduled job lease: %s', name)
                parent.cancel()
                return
    heartbeat = asyncio.create_task(renew())
    try:
        yield True
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        try:
            await client.rpc('carousel_job_lease', {'job':name,'owner':owner,'action':'release'})
        except Exception:
            logger.warning('Could not release job lease %s; it will expire.', name)
