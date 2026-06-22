"""
bot.py — Main file for the Twitter → Telegram monitor bot

Usage:
    python bot.py

Dependencies: pip install -r requirements.txt
"""
import argparse
import asyncio
import logging
import signal
import sys
from datetime import timezone
from html import escape as html_escape


from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

import config
from rss_parser import Tweet, fetch_tweets
from state import add_seen_ids, load_seen_ids
from ai_client import generate_ai_reply
from twitter_client import twitter_client

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("TwitterBot")


# In-memory store for generated replies
# Key: draft_message_id (int), Value: dict {"tweet_id": str, "text": str}
REPLY_DRAFTS = {}


# ── Message formatting ────────────────────────────────────────────────────────
def format_message(tweet: Tweet) -> str:
    """Formats the Telegram notification message for a tweet."""
    dt = tweet.published.astimezone(timezone.utc)
    date_str = dt.strftime("%d.%m.%Y %H:%M UTC")

    # Truncate long tweets (keep room for link and date)
    text = tweet.text
    if len(text) > 800:
        text = text[:797] + "…"

    # Escape special chars for MarkdownV2
    def esc(s: str) -> str:
        for ch in r"\_*[]()~`>#+-=|{}.!":
            s = s.replace(ch, f"\\{ch}")
        return s

    lines = [
        f"👤 *{esc(tweet.username)}*",
        "🐦 *New tweet\\!*",
        "",
        esc(text),
        "",
        f"🔗 [Open tweet]({tweet.url})",
        f"📅 {esc(date_str)}",
    ]
    return "\n".join(lines)


def get_tweet_keyboard(tweet_id: str) -> InlineKeyboardMarkup:
    """Creates the inline keyboard panel under the tweet."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("❤️ Like", callback_data=f"like:{tweet_id}"),
            InlineKeyboardButton("🔖 Bookmark", callback_data=f"bookmark:{tweet_id}"),
            InlineKeyboardButton("💬 Reply", callback_data=f"reply_prompt:{tweet_id}"),
        ]
    ])


# ── Send notification ─────────────────────────────────────────────────────────
async def send_tweet_notification(bot: Bot, tweet: Tweet) -> bool:
    """Sends a tweet notification to Telegram with control panel buttons. Returns True on success."""
    try:
        await bot.send_message(
            chat_id=config.TELEGRAM_CHAT_ID,
            text=format_message(tweet),
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_web_page_preview=False,  # show link preview card
            reply_markup=get_tweet_keyboard(tweet.id),
        )
        logger.info("✅ Sent tweet %s to Telegram", tweet.id)
        return True
    except TelegramError as exc:
        logger.error("❌ Telegram error sending tweet %s: %s", tweet.id, exc)
        return False


# ── Monitoring loop ───────────────────────────────────────────────────────────
async def check_new_tweets(bot: Bot, seen_ids: set[str]) -> set[str]:
    """
    Fetches RSS feeds for all monitored accounts, finds new tweets, and sends them.
    Returns updated seen_ids set.
    """
    updated_seen = seen_ids.copy()

    for username in config.TWITTER_USERNAMES:
        logger.debug("Checking tweets for @%s...", username)
        tweets = fetch_tweets(
            username=username,
            instances=config.NITTER_INSTANCES,
            limit=config.INITIAL_FETCH_COUNT,
        )

        if not tweets:
            logger.debug("No tweets fetched for @%s", username)
            continue

        # Detect per-user first run
        # If seen_ids is empty, or if we haven't seen any tweets for this user yet
        user_tweet_ids = {t.id for t in tweets}
        has_seen_any = bool(user_tweet_ids & updated_seen)

        if not has_seen_any:
            # First launch / new user: send ONLY the latest (most recent) tweet
            latest_tweet = tweets[0]  # sorted newest-first in rss_parser
            logger.info("🆕 First run/new user detected for @%s. Sending latest tweet: %s", username, latest_tweet.url)
            await send_tweet_notification(bot, latest_tweet)
            
            # Mark all retrieved tweets as seen so we don't spam the chat on subsequent loops
            updated_seen = add_seen_ids(updated_seen, user_tweet_ids)
        else:
            # Regular run: check for tweets not in seen_ids
            new_tweets = [t for t in tweets if t.id not in updated_seen]

            if not new_tweets:
                continue

            # Send in chronological order (oldest first)
            new_tweets_sorted = sorted(new_tweets, key=lambda t: t.published)
            logger.info("📨 Found %d new tweet(s) for @%s, sending…", len(new_tweets_sorted), username)

            sent_ids: set[str] = set()
            for tweet in new_tweets_sorted:
                success = await send_tweet_notification(bot, tweet)
                if success:
                    sent_ids.add(tweet.id)
                # Small delay to avoid Telegram rate limits
                await asyncio.sleep(1)

            updated_seen = add_seen_ids(updated_seen, sent_ids)

    return updated_seen


# ── Telegram Callback Handlers ────────────────────────────────────────────────
def extract_tweet_text_from_message(message_text: str) -> str:
    """Extracts raw tweet text from Telegram message block."""
    parts = message_text.split("\n\n")
    if len(parts) >= 3:
        # Join middle parts in case the tweet itself contains double newlines
        return "\n\n".join(parts[1:-1]).strip()
    return message_text.strip()


async def update_keyboard_action_done(message, action_type: str):
    """Updates the Inline Keyboard marking Like, Bookmark, or Reply as completed with a checkmark."""
    reply_markup = message.reply_markup
    if not reply_markup:
        return
        
    keyboard = []
    for row in reply_markup.inline_keyboard:
        new_row = []
        for button in row:
            if action_type == "like" and (button.callback_data.startswith("like:") or (button.callback_data == "loading" and button.text.startswith("⏳ Lik"))):
                new_row.append(InlineKeyboardButton("✅", callback_data="done"))
            elif action_type == "bookmark" and (button.callback_data.startswith("bookmark:") or (button.callback_data == "loading" and button.text.startswith("⏳ Book"))):
                new_row.append(InlineKeyboardButton("✅", callback_data="done"))
            elif action_type == "reply" and (button.callback_data.startswith("reply_prompt:") or (button.callback_data == "loading" and button.text.startswith("⏳ Repl"))):
                new_row.append(InlineKeyboardButton("✅", callback_data="done"))
            else:
                new_row.append(button)
        keyboard.append(new_row)
        
    await message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))


async def update_keyboard_loading(message, action_type: str):
    """Updates the Inline Keyboard to show a loading status for the clicked action."""
    reply_markup = message.reply_markup
    if not reply_markup:
        return
        
    keyboard = []
    for row in reply_markup.inline_keyboard:
        new_row = []
        for button in row:
            if action_type == "like" and button.callback_data.startswith("like:"):
                new_row.append(InlineKeyboardButton("⏳ Liking...", callback_data="loading"))
            elif action_type == "bookmark" and button.callback_data.startswith("bookmark:"):
                new_row.append(InlineKeyboardButton("⏳ Bookmarking...", callback_data="loading"))
            elif action_type == "reply" and button.callback_data.startswith("reply_prompt:"):
                new_row.append(InlineKeyboardButton("⏳ Replying...", callback_data="loading"))
            else:
                new_row.append(button)
        keyboard.append(new_row)
        
    await message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))


async def update_keyboard_restore(message, action_type: str, tweet_id: str):
    """Restores the button to its original state if an action fails."""
    reply_markup = message.reply_markup
    if not reply_markup:
        return
        
    keyboard = []
    for row in reply_markup.inline_keyboard:
        new_row = []
        for button in row:
            if button.callback_data == "loading":
                if action_type == "like":
                    new_row.append(InlineKeyboardButton("❤️ Like", callback_data=f"like:{tweet_id}"))
                elif action_type == "bookmark":
                    new_row.append(InlineKeyboardButton("🔖 Bookmark", callback_data=f"bookmark:{tweet_id}"))
                elif action_type == "reply":
                    new_row.append(InlineKeyboardButton("💬 Reply", callback_data=f"reply_prompt:{tweet_id}"))
            else:
                new_row.append(button)
        keyboard.append(new_row)
        
    await message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))


def get_friendly_error_message(raw_error: str) -> str:
    """Translates raw Twitter/proxy errors into human-friendly English descriptions."""
    err_lower = raw_error.lower()
    if "unauthorized" in err_lower or "401" in err_lower or "403" in err_lower or "login" in err_lower or "cookie" in err_lower or "authentication" in err_lower:
        return "❌ Authentication error!\n\nCookies in your .env file are expired or invalid. Please copy new auth_token and ct0 from your browser and paste them into .env."
    elif "proxy" in err_lower or "connect" in err_lower or "timeout" in err_lower or "socks" in err_lower or "httpcore" in err_lower:
        return "❌ Proxy error!\n\nThe bot could not connect through the proxy server. Please check your proxy status or the TWITTER_PROXY setting in .env."
    elif "rate limit" in err_lower or "429" in err_lower:
        return "❌ Twitter Rate Limit!\n\nYou have performed too many actions in a short time. Please wait 15-30 minutes."
    
    # Clean up standard prefix if present
    clean_err = raw_error.replace("Failed to like tweet: ", "").replace("Failed to bookmark tweet: ", "").replace("Failed to reply to tweet: ", "")
    if len(clean_err) > 120:
        clean_err = clean_err[:117] + "..."
    return f"❌ Twitter Error:\n{clean_err}"


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles all callback queries from inline buttons (Like, Bookmark, Reply actions)."""
    query = update.callback_query
    
    data = query.data
    if not data:
        await query.answer()
        return
        
    if data == "done":
        await query.answer("Action already completed.", show_alert=False)
        return
    if data == "loading":
        await query.answer("Action is currently in progress...", show_alert=False)
        return
        
    if ":" not in data:
        await query.answer()
        return
        
    action, payload = data.split(":", 1)
    
    if action == "like":
        tweet_id = payload
        await update_keyboard_loading(query.message, "like")
        
        success, msg = await twitter_client.like_tweet(tweet_id)
        if success:
            await query.answer("Liked successfully! ❤️", show_alert=False)
            await update_keyboard_action_done(query.message, "like")
        else:
            await update_keyboard_restore(query.message, "like", tweet_id)
            await query.answer(get_friendly_error_message(msg), show_alert=True)
            
    elif action == "bookmark":
        tweet_id = payload
        await update_keyboard_loading(query.message, "bookmark")
        
        success, msg = await twitter_client.bookmark_tweet(tweet_id)
        if success:
            await query.answer("Bookmarked successfully! 🔖", show_alert=False)
            await update_keyboard_action_done(query.message, "bookmark")
        else:
            await update_keyboard_restore(query.message, "bookmark", tweet_id)
            await query.answer(get_friendly_error_message(msg), show_alert=True)
            
    elif action == "reply_prompt":
        tweet_id = payload
        await update_keyboard_loading(query.message, "reply")
        
        # 1. Send status message
        draft_msg = await query.message.reply_text("🤖 Generating AI reply draft...")
        
        # 2. Extract tweet text from the telegram message
        tweet_text = extract_tweet_text_from_message(query.message.text)
        
        # 3. Call AI client to generate reply
        reply_text = await generate_ai_reply(tweet_text)
        
        # Restore Reply button
        await update_keyboard_restore(query.message, "reply", tweet_id)
        await query.answer()
        
        if reply_text and not reply_text.startswith("❌"):
            # Save draft in memory
            REPLY_DRAFTS[draft_msg.message_id] = {
                "tweet_id": tweet_id,
                "text": reply_text
            }
            
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Send Reply", callback_data=f"send_reply:{draft_msg.message_id}"),
                    InlineKeyboardButton("🔄 Regenerate", callback_data=f"regen_reply:{draft_msg.message_id}"),
                ],
                [
                    InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_reply:{draft_msg.message_id}")
                ]
            ])
            
            await draft_msg.edit_text(
                f"📝 <b>AI Reply Draft</b>:\n\n{html_escape(reply_text)}\n\n<i>Guidelines from reply_settings.md were applied.</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )
        else:
            error_msg = reply_text or "Could not generate reply text."
            await draft_msg.edit_text(f"❌ Failed to generate reply:\n{error_msg}")
            
    elif action == "send_reply":
        draft_msg_id = int(payload)
        draft = REPLY_DRAFTS.get(draft_msg_id)
        if not draft:
            await query.answer("Draft not found or expired. Please generate a new one.", show_alert=True)
            return
            
        # Set loading state on draft buttons
        loading_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("⏳ Sending...", callback_data="loading"),
                InlineKeyboardButton("🔄 Regenerate", callback_data="loading"),
            ],
            [
                InlineKeyboardButton("❌ Cancel", callback_data="loading")
            ]
        ])
        await query.message.edit_reply_markup(reply_markup=loading_keyboard)
        
        success, msg = await twitter_client.reply_tweet(draft["tweet_id"], draft["text"])
        if success:
            await query.message.edit_text(f"✅ Reply posted successfully!\n\nText: {draft['text']}")
            REPLY_DRAFTS.pop(draft_msg_id, None)
            
            # Update original tweet message keyboard to show we replied
            parent_msg = query.message.reply_to_message
            if parent_msg:
                try:
                    await update_keyboard_action_done(parent_msg, "reply")
                except Exception as e:
                    logger.warning("Could not update parent message keyboard: %s", e)
            await query.answer("Reply posted successfully! ✅", show_alert=False)
        else:
            # Restore draft keyboard on failure
            restore_keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Send Reply", callback_data=f"send_reply:{draft_msg_id}"),
                    InlineKeyboardButton("🔄 Regenerate", callback_data=f"regen_reply:{draft_msg_id}"),
                ],
                [
                    InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_reply:{draft_msg_id}")
                ]
            ])
            await query.message.edit_reply_markup(reply_markup=restore_keyboard)
            await query.answer(get_friendly_error_message(msg), show_alert=True)
            
    elif action == "regen_reply":
        draft_msg_id = int(payload)
        draft = REPLY_DRAFTS.get(draft_msg_id)
        if not draft:
            await query.answer("Draft not found or expired. Please generate a new one.", show_alert=True)
            return
            
        # Set loading state on draft buttons
        loading_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Send Reply", callback_data="loading"),
                InlineKeyboardButton("⏳ Regenerating...", callback_data="loading"),
            ],
            [
                InlineKeyboardButton("❌ Cancel", callback_data="loading")
            ]
        ])
        await query.message.edit_reply_markup(reply_markup=loading_keyboard)
        
        parent_msg = query.message.reply_to_message
        if parent_msg and parent_msg.text:
            tweet_text = extract_tweet_text_from_message(parent_msg.text)
        else:
            # Restore draft keyboard on failure
            restore_keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Send Reply", callback_data=f"send_reply:{draft_msg_id}"),
                    InlineKeyboardButton("🔄 Regenerate", callback_data=f"regen_reply:{draft_msg_id}"),
                ],
                [
                    InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_reply:{draft_msg_id}")
                ]
            ])
            await query.message.edit_reply_markup(reply_markup=restore_keyboard)
            await query.message.reply_text("❌ Error: Could not retrieve parent tweet text. Cancel and try again.")
            await query.answer()
            return
            
        reply_text = await generate_ai_reply(tweet_text)
        if reply_text and not reply_text.startswith("❌"):
            draft["text"] = reply_text
            
            restore_keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Send Reply", callback_data=f"send_reply:{draft_msg_id}"),
                    InlineKeyboardButton("🔄 Regenerate", callback_data=f"regen_reply:{draft_msg_id}"),
                ],
                [
                    InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_reply:{draft_msg_id}")
                ]
            ])
            
            await query.message.edit_text(
                f"📝 <b>AI Reply Draft</b>:\n\n{html_escape(reply_text)}\n\n<i>Guidelines from reply_settings.md were applied.</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=restore_keyboard
            )
            await query.answer("Draft regenerated! 🔄")
        else:
            error_msg = reply_text or "Could not generate reply text."
            # Restore draft keyboard on failure
            restore_keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Send Reply", callback_data=f"send_reply:{draft_msg_id}"),
                    InlineKeyboardButton("🔄 Regenerate", callback_data=f"regen_reply:{draft_msg_id}"),
                ],
                [
                    InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_reply:{draft_msg_id}")
                ]
            ])
            await query.message.edit_reply_markup(reply_markup=restore_keyboard)
            await query.answer(get_friendly_error_message(error_msg), show_alert=True)
            
    elif action == "cancel_reply":
        draft_msg_id = int(payload)
        REPLY_DRAFTS.pop(draft_msg_id, None)
        await query.message.delete()
        await query.answer()


# ── Main loop ─────────────────────────────────────────────────────────────────
async def main() -> None:
    logger.info("=" * 60)
    logger.info("🤖 Twitter → Telegram Bot starting")
    logger.info("   Accounts : %s", ", ".join(config.TWITTER_USERNAMES))
    logger.info("   Interval : %d min", config.CHECK_INTERVAL_MINUTES)
    logger.info("=" * 60)

    # Initialize Twitter Client
    twitter_client.initialize()

    # Build Telegram Application
    application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    # Register callback query handler for inline button clicks
    application.add_handler(CallbackQueryHandler(handle_callback))

    # Verify Telegram connection
    try:
        me = await application.bot.get_me()
        logger.info("🔗 Connected to Telegram as @%s", me.username)
    except TelegramError as exc:
        logger.critical("❌ Failed to connect to Telegram: %s", exc)
        sys.exit(1)

    seen_ids = load_seen_ids()
    interval_sec = config.CHECK_INTERVAL_MINUTES * 60

    # Start the telegram application
    await application.initialize()
    await application.start()
    await application.updater.start_polling()

    # Graceful shutdown event
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()

    def _shutdown(sig_name: str) -> None:
        logger.info("⏹  Received %s, shutting down…", sig_name)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown, sig.name)
        except NotImplementedError:
            pass  # Windows does not support add_signal_handler for SIGTERM

    logger.info("🚀 Monitoring loop started")

    # Monitoring loop
    while not stop_event.is_set():
        try:
            seen_ids = await check_new_tweets(application.bot, seen_ids)
        except Exception as exc:
            logger.exception("⚠️  Unexpected error in monitoring loop: %s", exc)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_sec)
        except asyncio.TimeoutError:
            pass  # Normal timeout, continue loop

    # Clean shutdown
    logger.info("👋 Shutting down Telegram application...")
    await application.updater.stop()
    await application.stop()
    await application.shutdown()
    logger.info("👋 Bot stopped")


# ── Instant send mode ─────────────────────────────────────────────────────────
async def send_now(test_only: bool = False) -> None:
    """
    --send-now : fetches the latest tweet and sends it to Telegram (ignores seen_ids).
    --test     : same but only prints to console without sending.
    """
    username = config.TWITTER_USERNAMES[0]
    print(f"🔍 Fetching tweets for @{username}…")
    tweets = fetch_tweets(username=username, limit=config.INITIAL_FETCH_COUNT)


    if not tweets:
        print("❌ Could not fetch any tweets. Check your username and Nitter availability.")
        return

    tweet = tweets[0]  # most recent
    print(f"\n✅ Found tweet [{tweet.id}]:")
    print(f"   URL  : {tweet.url}")
    print(f"   Text : {tweet.text[:200]}")
    print(f"   Date : {tweet.published}")

    if test_only:
        print("\n[--test mode] Sending skipped.")
        return

    bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
    try:
        me = await bot.get_me()
        print(f"\n🔗 Connected to Telegram as @{me.username}")
    except TelegramError as exc:
        print(f"❌ Telegram connection error: {exc}")
        return

    success = await send_tweet_notification(bot, tweet)
    if success:
        print("\n✅ Tweet sent to Telegram!")
    else:
        print("\n❌ Failed to send. Check bot.log.")


# ── Helper: find correct TELEGRAM_CHAT_ID ─────────────────────────────────────
async def get_chat_id() -> None:
    """
    Calls getUpdates and prints all chat IDs the bot has interacted with.
    Before running: send /start to your bot in Telegram.
    """
    bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
    print("🔍 Looking up chat_id via getUpdates…")
    print("   (Make sure you sent /start to the bot in Telegram)\n")
    try:
        updates = await bot.get_updates(limit=20, timeout=5)
    except TelegramError as exc:
        print(f"❌ Error: {exc}")
        return

    if not updates:
        print("⚠️  No updates found. Send /start to the bot and try again.")
        return

    seen: set[int] = set()
    for update in updates:
        msg = update.message or update.edited_message or update.channel_post
        if msg and msg.chat.id not in seen:
            seen.add(msg.chat.id)
            chat = msg.chat
            name = chat.username or chat.title or chat.first_name or "—"
            print(f"  chat_id : {chat.id}")
            print(f"  type    : {chat.type}")
            print(f"  name    : {name}")
            print(f"  → Set TELEGRAM_CHAT_ID={chat.id} in your .env\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Twitter → Telegram Monitor Bot")
    parser.add_argument(
        "--send-now",
        action="store_true",
        help="Immediately send the latest tweet to Telegram (for testing)",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Show the latest tweet in console without sending",
    )
    parser.add_argument(
        "--get-chat-id",
        action="store_true",
        help="Show available chat IDs (send /start to the bot first)",
    )
    args = parser.parse_args()

    try:
        if args.get_chat_id:
            asyncio.run(get_chat_id())
        elif args.send_now:
            asyncio.run(send_now(test_only=False))
        elif args.test:
            asyncio.run(send_now(test_only=True))
        else:
            asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Stopped by user")
