"""
rss_parser.py — Parses the Nitter RSS feed with fallback across multiple instances
"""
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Optional

import feedparser
import requests

from config import NITTER_INSTANCES, TWITTER_USERNAME

logger = logging.getLogger(__name__)

# HTTP request timeout in seconds
REQUEST_TIMEOUT = 10


@dataclass
class Tweet:
    """Represents a single tweet."""
    id: str
    url: str            # link to twitter.com
    text: str           # tweet text (HTML stripped)
    published: datetime
    is_retweet: bool
    is_reply: bool
    is_quote: bool
    username: str       # twitter profile username


def _nitter_rss_url(instance: str, username: str) -> str:
    return f"{instance.rstrip('/')}/{username}/rss"


def _twitter_url(nitter_link: str, username: str) -> str:
    """Converts a Nitter link to a clean twitter.com URL (strips ?s=20 and other params)."""
    # Extract only the numeric status ID — build a clean URL without query params
    match = re.search(r"/status/(\d+)", nitter_link)
    if match:
        tweet_id = match.group(1)
        return f"https://twitter.com/{username}/status/{tweet_id}"
    # Fallback: strip query string manually
    return re.sub(r"\?.*$", "", nitter_link)


def _extract_tweet_id(link: str) -> Optional[str]:
    match = re.search(r"/status/(\d+)", link)
    return match.group(1) if match else None


def _clean_html(html_text: str) -> str:
    """Strips HTML tags and decodes common entities."""
    text = re.sub(r"<[^>]+>", "", html_text)
    text = (
        text.replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .replace("&#39;", "'")
            .replace("&nbsp;", " ")
            .replace("&#x27;", "'")
    )
    return text.strip()


def _parse_entry(entry: feedparser.FeedParserDict, username: str) -> Optional[Tweet]:
    """Converts a feedparser entry into a Tweet object."""
    link = getattr(entry, "link", "")
    tweet_id = _extract_tweet_id(link)
    if not tweet_id:
        return None

    # Text: prefer summary, fall back to title
    raw_text = getattr(entry, "summary", "") or getattr(entry, "title", "")
    
    # Check if it's a quote tweet before trimming
    is_quote = bool(re.search(r'(<a[^>]*>\[quote\]</a>|\[quote\]|<div[^>]*class=["\']quote["\'][^>]*>)', raw_text, flags=re.IGNORECASE))
    
    # Nitter appends quote tweets at the end, usually marked by [quote] or a <div class="quote">.
    # We trim the text here so the bot only sees the user's own words, ignoring the quoted text.
    raw_text = re.split(r'(<a[^>]*>\[quote\]</a>|\[quote\]|<div[^>]*class=["\']quote["\'][^>]*>)', raw_text, flags=re.IGNORECASE)[0]
    
    text = _clean_html(raw_text)

    # Published time
    try:
        published = parsedate_to_datetime(entry.published)
    except Exception:
        published = datetime.utcnow()

    title = getattr(entry, "title", "").strip()
    author = getattr(entry, "author", "").strip()
    
    # Nitter indicates retweets by setting the author to the original tweeter
    # or by prefixing the title with "RT by @"
    is_retweet = (
        title.startswith("RT by @") or 
        text.startswith("RT @") or 
        text.startswith("RT by @") or
        (author and author.lower() != f"@{username.lower()}")
    )
    is_reply = text.startswith("@")

    return Tweet(
        id=tweet_id,
        url=_twitter_url(link, username),
        text=text,
        published=published,
        is_retweet=is_retweet,
        is_reply=is_reply,
        is_quote=is_quote,
        username=username,
    )


def fetch_tweets(
    username: str = TWITTER_USERNAME,
    instances: list[str] = NITTER_INSTANCES,
    limit: int = 20,
    skip_retweets: bool = True,
    skip_replies: bool = True,
    skip_quotes: bool = False,
) -> list[Tweet]:
    """
    Fetches the latest tweets via RSS.
    Tries each Nitter instance in order until one responds successfully.

    Returns a list of Tweet objects sorted newest-first.
    Returns [] on failure.
    """
    for instance in instances:
        url = _nitter_rss_url(instance, username)
        try:
            logger.debug("Requesting %s", url)
            resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={
                "User-Agent": "Mozilla/5.0 (compatible; TwitterMonitorBot/1.0)"
            })
            resp.raise_for_status()

            feed = feedparser.parse(resp.text)
            if feed.bozo and not feed.entries:
                logger.warning("Nitter (%s): malformed RSS, trying next instance", instance)
                continue

            tweets: list[Tweet] = []
            for entry in feed.entries:
                tweet = _parse_entry(entry, username)
                if tweet is None:
                    continue
                if skip_retweets and tweet.is_retweet:
                    continue
                if skip_replies and tweet.is_reply:
                    continue
                if skip_quotes and tweet.is_quote:
                    continue
                tweets.append(tweet)
                if len(tweets) >= limit:
                    break

            logger.info("✅ Fetched %d tweet(s) from %s", len(tweets), instance)
            return tweets

        except requests.RequestException as exc:
            logger.warning("Nitter (%s) unavailable: %s", instance, exc)
            continue
        except Exception as exc:
            logger.exception("Unexpected error parsing %s: %s", instance, exc)
            continue

    logger.error("❌ All Nitter instances failed to respond!")
    return []
