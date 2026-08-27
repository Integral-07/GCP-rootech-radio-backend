import os
import re
from datetime import datetime, timedelta, timezone

import feedparser
import functions_framework

DEFAULT_FEED_URLS = os.environ.get("FEED_URLS", "").split(",")
MAX_ARTICLES_PER_FEED = int(os.environ.get("MAX_ARTICLES_PER_FEED", "5"))
DAYS_LOOKBACK = int(os.environ.get("DAYS_LOOKBACK", "7"))


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def collect_articles(feed_urls: list) -> list:
    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS_LOOKBACK)
    articles = []

    for feed_url in feed_urls:
        feed_url = feed_url.strip()
        if not feed_url:
            continue

        parsed = feedparser.parse(feed_url)

        count = 0
        for entry in parsed.entries:
            if count >= MAX_ARTICLES_PER_FEED:
                break

            published = getattr(entry, "published_parsed", None)
            if published:
                published_dt = datetime(*published[:6], tzinfo=timezone.utc)
                if published_dt < cutoff:
                    continue

            title = getattr(entry, "title", "").strip()
            summary = strip_html(getattr(entry, "summary", ""))[:500]
            link = getattr(entry, "link", "").strip()

            if not title:
                continue

            articles.append({"title": title, "summary": summary, "url": link})
            count += 1

    return articles


@functions_framework.http
def collect_rss(request):
    request_json = request.get_json(silent=True) or {}

    topic = request_json.get("topic", "")
    feed_urls = request_json.get("feed_urls") or DEFAULT_FEED_URLS

    articles = collect_articles(feed_urls)

    return {"topic": topic, "count": len(articles), "articles": articles}
