"""Select feed-supplied thumbnail URLs without fetching article pages."""
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

_IMAGE = re.compile(r"\.(?:avif|gif|jpe?g|png|webp)(?:$|[?#])", re.I)

def safe_image_url(value, base=""):
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        url = urljoin(base, value.strip())
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username:
            return ""
        return url
    except ValueError:
        return ""

class _Images(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []
    def handle_starttag(self, tag, attrs):
        if tag != "img":
            return
        values = dict(attrs)
        if values.get("width") in ("0", "1") or values.get("height") in ("0", "1"):
            return
        self.urls.append(values.get("src", ""))

def feed_thumbnail(entry):
    base = str(entry.get("link") or "")
    candidates = list(entry.get("media_thumbnail") or [])
    candidates += [m for m in list(entry.get("media_content") or []) + list(entry.get("enclosures") or [])
                   if str(m.get("type", "")).startswith("image/") or m.get("medium") == "image"
                   or _IMAGE.search(str(m.get("url") or m.get("href") or ""))]
    for media in candidates:
        url = safe_image_url(media.get("url") or media.get("href"), base)
        if url:
            return url
    parser = _Images()
    for content in [entry.get("summary", ""), *[c.get("value", "") for c in entry.get("content") or []]]:
        parser.feed(str(content))
    for candidate in parser.urls:
        url = safe_image_url(candidate, base)
        if url and not re.search(r"pixel|beacon|tracking|spacer", url, re.I):
            return url
    return ""

def news_thumbnail(payload):
    explicit = safe_image_url(payload.get("thumbnail_url"))
    if explicit:
        return explicit
    for media in payload.get("media_urls") or []:
        if isinstance(media, str) and _IMAGE.search(media):
            url = safe_image_url(media)
            if url:
                return url
    return ""
