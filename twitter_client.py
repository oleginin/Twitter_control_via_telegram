"""
twitter_client.py — Wraps twikit client to perform X (Twitter) write actions using cookie-based authentication and proxies.
"""
import json
import logging
from pathlib import Path
from typing import Tuple, Optional

from twikit import Client

import config

logger = logging.getLogger("TwitterBot.TwitterClient")

COOKIES_FILE = Path(__file__).parent / "cookies_twikit.json"


class TwitterClientWrapper:
    def __init__(self):
        self.client: Optional[Client] = None
        self.is_authenticated = False

    def initialize(self) -> Tuple[bool, str]:
        """
        Initializes the Twikit client and loads cookies from environment variables or cookies_twikit.json.
        """
        auth_token = config.TWITTER_COOKIE_AUTH_TOKEN
        ct0 = config.TWITTER_COOKIE_CT0
        proxy = config.TWITTER_PROXY or None

        # Determine if we can build cookies from .env
        if auth_token and ct0:
            try:
                cookies_data = {
                    "auth_token": auth_token,
                    "ct0": ct0
                }
                COOKIES_FILE.write_text(json.dumps(cookies_data, indent=2), encoding="utf-8")
                logger.info("Generated cookies_twikit.json from .env variables.")
            except Exception as exc:
                logger.error("Failed to write cookies_twikit.json: %s", exc)

        if not COOKIES_FILE.exists():
            msg = "No Twitter authentication cookies found. Please set TWITTER_COOKIE_AUTH_TOKEN and TWITTER_COOKIE_CT0 in your .env."
            logger.warning(msg)
            return False, msg

        try:
            logger.info("Initializing X (Twitter) client (Proxy: %s)...", proxy)
            # Create the twikit Client
            self.client = Client(
                language="en-US",
                proxy=proxy,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
            
            # Load cookies from file (synchronous method in twikit)
            self.client.load_cookies(str(COOKIES_FILE))
            
            self.is_authenticated = True
            logger.info("✅ X (Twitter) client successfully initialized.")
            return True, "Success"
            
        except Exception as exc:
            self.is_authenticated = False
            msg = f"Failed to initialize X Client with cookies: {exc}"
            logger.error(msg)
            return False, msg

    async def like_tweet(self, tweet_id: str) -> Tuple[bool, str]:
        """Likes (favorites) a tweet by ID. Returns (success, message)."""
        if not self.is_authenticated or self.client is None:
            # Try to re-initialize in case .env was updated
            success, msg = self.initialize()
            if not success:
                return False, f"Not authenticated: {msg}"

        try:
            logger.info("Liking tweet %s...", tweet_id)
            await self.client.favorite_tweet(tweet_id)
            logger.info("✅ Successfully liked tweet %s", tweet_id)
            return True, "Liked"
        except Exception as exc:
            logger.warning("Failed to like tweet %s: %s. Resetting transaction and retrying...", tweet_id, exc)
            try:
                self.client.client_transaction.home_page_response = None
                await self.client.favorite_tweet(tweet_id)
                logger.info("✅ Successfully liked tweet %s after transaction reset", tweet_id)
                return True, "Liked"
            except Exception as retry_exc:
                msg = f"Failed to like tweet: {retry_exc}"
                logger.error(msg)
                return False, msg

    async def bookmark_tweet(self, tweet_id: str) -> Tuple[bool, str]:
        """Bookmarks a tweet by ID. Returns (success, message)."""
        if not self.is_authenticated or self.client is None:
            success, msg = self.initialize()
            if not success:
                return False, f"Not authenticated: {msg}"

        try:
            logger.info("Bookmarking tweet %s...", tweet_id)
            await self.client.bookmark_tweet(tweet_id)
            logger.info("✅ Successfully bookmarked tweet %s", tweet_id)
            return True, "Bookmarked"
        except Exception as exc:
            logger.warning("Failed to bookmark tweet %s: %s. Resetting transaction and retrying...", tweet_id, exc)
            try:
                self.client.client_transaction.home_page_response = None
                await self.client.bookmark_tweet(tweet_id)
                logger.info("✅ Successfully bookmarked tweet %s after transaction reset", tweet_id)
                return True, "Bookmarked"
            except Exception as retry_exc:
                msg = f"Failed to bookmark tweet: {retry_exc}"
                logger.error(msg)
                return False, msg

    async def reply_tweet(self, tweet_id: str, text: str) -> Tuple[bool, str]:
        """Replies to a tweet with comment text. Returns (success, message)."""
        if not self.is_authenticated or self.client is None:
            success, msg = self.initialize()
            if not success:
                return False, f"Not authenticated: {msg}"

        try:
            logger.info("Replying to tweet %s: %s...", tweet_id, text[:50])
            # create_tweet uses reply_to for replies
            await self.client.create_tweet(text=text, reply_to=tweet_id)
            logger.info("✅ Successfully replied to tweet %s", tweet_id)
            return True, "Replied"
        except Exception as exc:
            logger.warning("Failed to reply to tweet %s: %s. Resetting transaction and retrying...", tweet_id, exc)
            try:
                self.client.client_transaction.home_page_response = None
                await self.client.create_tweet(text=text, reply_to=tweet_id)
                logger.info("✅ Successfully replied to tweet %s after transaction reset", tweet_id)
                return True, "Replied"
            except Exception as retry_exc:
                msg = f"Failed to reply to tweet: {retry_exc}"
                logger.error(msg)
                return False, msg


# Singleton instance
twitter_client = TwitterClientWrapper()
