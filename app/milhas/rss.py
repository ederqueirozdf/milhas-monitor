import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import feedparser
import httpx

UA = "Mozilla/5.0 (X11; Linux x86_64) milhas-monitor/1.0"


@dataclass
class Post:
    url: str
    titulo: str
    resumo: str
    publicado: datetime | None


def _texto(fragmento: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", fragmento or "")).strip()


def buscar(url: str) -> list[Post]:
    resp = httpx.get(url, headers={"User-Agent": UA}, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)
    posts = []
    for e in feed.entries:
        link = e.get("link")
        if not link:
            continue
        quando = e.get("published_parsed") or e.get("updated_parsed")
        posts.append(
            Post(
                url=link,
                titulo=_texto(e.get("title", "")),
                resumo=_texto(e.get("summary", ""))[:1000],
                publicado=datetime(*quando[:6], tzinfo=timezone.utc) if quando else None,
            )
        )
    return posts
