"""Personal newsroom sources, configured in the console."""
from contextvars import ContextVar
from urllib.parse import urlsplit, parse_qs
import ipaddress
import re
from app import tenancy
from app.services import db

DEFAULT_RSS = ["https://www.techmeme.com/feed.xml", "https://techcrunch.com/feed/", "https://www.theverge.com/rss/index.xml", "https://arstechnica.com/feed/"]
_sources = ContextVar("workspace_sources", default=None)


def validate(value):
    result = {}
    for key in ("rss_feeds", "youtube_channels"):
        entries = value.get(key, [])
        if not isinstance(entries, list) or len(entries) > 30:
            raise ValueError("Add at most 30 sources per list.")
        cleaned = []
        for entry in entries:
            if not isinstance(entry, str) or len(entry) > 2048:
                raise ValueError("Enter a valid source URL or YouTube channel ID.")
            entry = entry.strip()
            if not entry: continue
            if key == "youtube_channels" and re.fullmatch(r"UC[A-Za-z0-9_-]{22}", entry):
                cleaned.append(entry); continue
            url = urlsplit(entry)
            if url.scheme != "https" or not url.hostname or url.username or url.password:
                raise ValueError("Sources must use HTTPS URLs without embedded credentials.")
            host = url.hostname.lower().rstrip(".")
            if "\\" in entry or any(c.isspace() for c in entry) or url.port not in (None, 443):
                raise ValueError("Use a public HTTPS source on the standard port.")
            if host == "localhost" or host.endswith((".localhost", ".local", ".internal")) or "." not in host:
                raise ValueError("Use a public news source.")
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                pass
            else:
                if not address.is_global: raise ValueError("Use a public news source.")
            if key == "youtube_channels":
                channel = url.path.removeprefix("/channel/") if url.path.startswith("/channel/") else parse_qs(url.query).get("channel_id", [""])[0] if url.path == "/feeds/videos.xml" else ""
                if host not in ("youtube.com", "www.youtube.com") or not re.fullmatch(r"UC[A-Za-z0-9_-]{22}", channel):
                    raise ValueError("Use a YouTube channel ID (UC...) or youtube.com/channel/<ID> URL.")
            cleaned.append(entry)
        result[key] = list(dict.fromkeys(cleaned))
    return result


async def load():
    value = await db.get_config("news_sources", None)
    value = validate(value if value is not None else {"rss_feeds": DEFAULT_RSS, "youtube_channels": []})
    _sources.set((tenancy.current(), value))
    return value


def current():
    cached = _sources.get()
    return cached[1] if cached and cached[0] == tenancy.current() else {"rss_feeds": DEFAULT_RSS, "youtube_channels": []}


async def save(value):
    value = validate(value)
    await db.set_config("news_sources", value)
    _sources.set((tenancy.current(), value))
    return value
