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
import math
import datetime
from datetime import timezone
from html import escape as html_escape
from pathlib import Path

from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, CommandHandler, MessageHandler, filters

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

# Silence noisy HTTP requests logs (like getUpdates) from httpx
logging.getLogger("httpx").setLevel(logging.WARNING)


# ── Usernames File Management ──────────────────────────────────────────────────
USERNAMES_FILE = Path(__file__).parent / "twitter_usernames.txt"

def load_usernames() -> list[str]:
    """Loads usernames from the text file. Falls back to config.TWITTER_USERNAMES if file is empty or missing."""
    if not USERNAMES_FILE.exists():
        # Initialize file with default list from config/env
        default_users = getattr(config, "TWITTER_USERNAMES", [])
        try:
            USERNAMES_FILE.write_text("\n".join(default_users), encoding="utf-8")
            logger.info("Created usernames file with %d default users: %s", len(default_users), USERNAMES_FILE)
        except Exception as e:
            logger.error("Failed to create %s: %s", USERNAMES_FILE, e)
        return default_users

    try:
        content = USERNAMES_FILE.read_text(encoding="utf-8")
        usernames = []
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                username = line.lstrip("@").strip()
                if username:
                    usernames.append(username)
        
        # If file is empty, fallback to config
        if not usernames:
            default_users = getattr(config, "TWITTER_USERNAMES", [])
            return default_users
            
        return usernames
    except Exception as e:
        logger.error("Failed to read usernames from %s: %s", USERNAMES_FILE, e)
        return getattr(config, "TWITTER_USERNAMES", [])

def save_usernames(usernames: list[str]) -> bool:
    """Saves the list of usernames to the text file."""
    try:
        USERNAMES_FILE.write_text("\n".join(usernames), encoding="utf-8")
        logger.info("Saved %d usernames to %s", len(usernames), USERNAMES_FILE)
        return True
    except Exception as e:
        logger.error("Failed to save usernames to %s: %s", USERNAMES_FILE, e)
        return False


# ── Batch Management ─────────────────────────────────────────────────────────
class UserBatchManager:
    def __init__(self, num_batches: int = 2):
        self.num_batches = num_batches
        self.current_index = 0

    def get_next_batch(self, usernames: list[str]) -> list[str]:
        if not usernames:
            return []
        
        # If index is out of bounds or at the start of a cycle
        if self.current_index >= len(usernames):
            self.current_index = 0
            
        n = len(usernames)
        effective_batches = min(self.num_batches, n) if n > 0 else 1
        batch_size = math.ceil(n / effective_batches)
        
        start = self.current_index
        end = start + batch_size
        
        batch = usernames[start:end]
        self.current_index = end
        
        # If we reached or exceeded the end of list, reset index for next call
        if self.current_index >= len(usernames):
            self.current_index = 0
            
        return batch

    def calculate_sleep_interval(self, total_usernames: int, check_interval_minutes: float) -> float:
        """
        Calculates the sleep interval between batches (in seconds)
        so that all accounts are checked once every check_interval_minutes.
        """
        if total_usernames <= 0:
            return check_interval_minutes * 60
            
        effective_batches = min(self.num_batches, total_usernames)
        return (check_interval_minutes * 60) / effective_batches

batch_manager = UserBatchManager(num_batches=config.NUM_BATCHES)


# ── Daily Cleanup Task ────────────────────────────────────────────────────────
async def daily_cleanup_task() -> None:
    """Background task to delete/truncate log files once a day (when the date changes)."""
    last_cleanup_date = datetime.date.today()
    while True:
        # Check every 30 minutes
        await asyncio.sleep(1800)
        
        current_date = datetime.date.today()
        if current_date != last_cleanup_date:
            logger.info("🧹 Starting daily log cleanup (date changed from %s to %s)...", last_cleanup_date, current_date)
            try:
                for handler in logging.getLogger().handlers + logger.handlers:
                    if isinstance(handler, logging.FileHandler):
                        handler.close()
                
                log_dir = Path(__file__).parent
                for file in log_dir.glob("*.log"):
                    try:
                        file.write_text("", encoding="utf-8")
                        logger.info("Truncated log file: %s", file.name)
                    except Exception as e:
                        logger.error("Failed to truncate %s: %s", file.name, e)
                
                for handler in logging.getLogger().handlers + logger.handlers:
                    if isinstance(handler, logging.FileHandler):
                        handler.stream = handler._open()
                
                last_cleanup_date = current_date
                logger.info("✅ Daily log cleanup completed successfully")
            except Exception as exc:
                logger.error("❌ Error during daily log cleanup: %s", exc)


# In-memory store for generated replies
# Key: draft_message_id (int), Value: dict {"tweet_id": str, "text": str}
REPLY_DRAFTS = {}


# ── Message formatting ────────────────────────────────────────────────────────
def format_message(tweet: Tweet) -> str:
    """Formats a tweet for Telegram using HTML."""
    from html import escape
    date_str = tweet.published.strftime("%d.%m.%Y %H:%M UTC")
    text = tweet.text
    if len(text) > 800:
        text = text[:797] + "…"
    lines = [
        f"👤 <b>{escape(tweet.username)}</b>",
        "────────────────",
        escape(text),
        "────────────────",
        f"🔗 <a href='{tweet.url}'>Open tweet</a>",
        f"📅 <i>{escape(date_str)}</i>",
    ]
    return "\n".join(lines)


def get_tweet_keyboard(tweet_id: str) -> InlineKeyboardMarkup:
    """Creates the inline keyboard panel under the tweet."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("❤️ Like", callback_data=f"like:{tweet_id}"),
            InlineKeyboardButton("🔖 Bookmark", callback_data=f"bookmark:{tweet_id}"),
        ],
        [
            InlineKeyboardButton("💬 Reply", callback_data=f"reply_prompt:{tweet_id}"),
        ]
    ])


# ── User Settings UI ──────────────────────────────────────────────────────────
async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    usernames = load_usernames()
    text = (
        "📊 <b>TwitterBot Dashboard</b>\n\n"
        f"👥 Tracked accounts: {len(usernames)}\n"
        f"🤖 AI Provider: {config.AI_PROVIDER.upper()}\n"
        f"🔄 Interval: {config.CHECK_INTERVAL_MINUTES} min\n\n"
        "Select an action:"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Manage Users", callback_data="manage_users")],
    ])
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)




# ── Send notification ─────────────────────────────────────────────────────────
async def send_tweet_notification(bot: Bot, tweet: Tweet) -> bool:
    """Sends a tweet notification to Telegram with control panel buttons. Returns True on success."""
    try:
        await bot.send_message(
            chat_id=config.TELEGRAM_CHAT_ID,
            text=format_message(tweet),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=False,  # show link preview card
            reply_markup=get_tweet_keyboard(tweet.id),
        )
        logger.info("✅ Sent tweet %s to Telegram", tweet.id)
        return True
    except TelegramError as exc:
        logger.error("❌ Telegram error sending tweet %s: %s", tweet.id, exc)
        return False


# ── Monitoring loop ───────────────────────────────────────────────────────────
async def check_new_tweets(bot: Bot, seen_ids: set[str], usernames: list[str]) -> set[str]:
    """
    Fetches RSS feeds for all monitored accounts in the batch, finds new tweets, and sends them.
    Returns updated seen_ids set.
    """
    updated_seen = seen_ids.copy()

    for username in usernames:
        logger.debug("Checking tweets for @%s...", username)
        tweets = fetch_tweets(
            username=username,
            instances=config.NITTER_INSTANCES,
            limit=max(20, config.INITIAL_FETCH_COUNT),
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
    parts = message_text.split("────────────────\n")
    if len(parts) >= 3:
        return parts[1].strip()
    parts = message_text.split("\n\n")
    if len(parts) >= 3:
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


# ── Interactive Account Management ───────────────────────────────────────────

def process_add_usernames(raw_input: str) -> tuple[list[str], list[str]]:
    """Helper to process a raw string of comma-separated usernames, saving new ones."""
    usernames = load_usernames()
    added = []
    skipped = []
    
    # Split by comma or whitespace/newlines
    parts = raw_input.replace("\n", ",").split(",")
    for part in parts:
        u = part.strip().lstrip("@").strip()
        if u:
            if u not in usernames:
                usernames.append(u)
                added.append(u)
            else:
                skipped.append(u)
                
    if added:
        save_usernames(usernames)
        
    return added, skipped


async def show_users_menu(message, edit: bool = False) -> None:
    """Shows the main user management menu."""
    usernames = load_usernames()
    
    # Format list
    if usernames:
        users_list_str = "\n".join(f"{i+1}. 👤 <b>@{html_escape(u)}</b>" for i, u in enumerate(usernames))
    else:
        users_list_str = "<i>No accounts are currently being monitored.</i>"
        
    n = len(usernames)
    effective_batches = min(config.NUM_BATCHES, n) if n > 0 else 1
    batch_size = math.ceil(n / effective_batches)
    
    text = (
        "👥 <b>Monitored Twitter Accounts</b>\n"
        f"Total accounts: {n}\n\n"
        f"{users_list_str}\n\n"
        f"ℹ️ <i>Checks are staggered. The list is split into <b>{config.NUM_BATCHES}</b> parts. "
        f"A batch of ~<b>{batch_size}</b> account(s) is checked every cycle.</i>"
    )
    
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Add Account", callback_data="manage:add_prompt"),
            InlineKeyboardButton("🗑️ Delete Account", callback_data="manage:delete_list")
        ],
        [
            InlineKeyboardButton("🔄 Refresh List", callback_data="manage:list")
        ]
    ])
    
    try:
        if edit:
            await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        else:
            await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except Exception as e:
        logger.warning("Failed to show/edit users menu: %s", e)


async def show_delete_menu(message) -> None:
    """Shows the delete account selection menu."""
    usernames = load_usernames()
    if not usernames:
        text = "⚠️ <b>No accounts to delete!</b>"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back", callback_data="manage:list")]
        ])
        await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        return

    text = "🗑️ <b>Select an account to remove from monitoring:</b>"
    
    # Build a grid of delete buttons
    keyboard_buttons = []
    # 2 buttons per row
    row = []
    for u in usernames:
        row.append(InlineKeyboardButton(f"❌ @{u}", callback_data=f"manage:delete_confirm:{u}"))
        if len(row) == 2:
            keyboard_buttons.append(row)
            row = []
    if row:
        keyboard_buttons.append(row)
        
    # Add back button
    keyboard_buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="manage:list")])
    
    await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard_buttons))


async def handle_manage_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, payload: str) -> None:
    """Handles callback interactions for account management."""
    query = update.callback_query
    
    # Verify owner
    if str(query.message.chat_id) != str(config.TELEGRAM_CHAT_ID):
        await query.answer("⚠️ Unauthorized access.", show_alert=True)
        return

    if payload == "list":
        await show_users_menu(query.message, edit=True)
        await query.answer()
        
    elif payload == "add_prompt":
        context.user_data['awaiting_usernames'] = True
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data="manage:list")]
        ])
        await query.message.edit_text(
            "✍️ <b>Add Twitter Account(s)</b>\n\n"
            "Please send the Twitter username(s) you want to monitor.\n"
            "You can specify multiple accounts separated by commas (e.g. <code>elonmusk, jack</code>).\n\n"
            "<i>State: awaiting username input...</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
        await query.answer()
        
    elif payload == "delete_list":
        await show_delete_menu(query.message)
        await query.answer()
        
    elif payload.startswith("delete_confirm:"):
        user_to_delete = payload.split(":", 1)[1]
        usernames = load_usernames()
        if user_to_delete in usernames:
            usernames.remove(user_to_delete)
            save_usernames(usernames)
            await query.answer(f"✅ Removed @{user_to_delete}", show_alert=False)
            await show_delete_menu(query.message)
        else:
            await query.answer(f"❌ User @{user_to_delete} not found", show_alert=True)
            await show_users_menu(query.message, edit=True)


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Command /users - displays the interactive management menu."""
    if str(update.effective_chat.id) != str(config.TELEGRAM_CHAT_ID):
        return
    await show_users_menu(update.message, edit=False)


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Command /add <username1>, <username2> - adds new account(s)."""
    if str(update.effective_chat.id) != str(config.TELEGRAM_CHAT_ID):
        return
        
    if not context.args:
        await update.message.reply_text(
            "⚠️ <b>Usage:</b> <code>/add username1, username2</code>",
            parse_mode=ParseMode.HTML
        )
        return
        
    raw_input = " ".join(context.args)
    added, skipped = process_add_usernames(raw_input)
    
    msg = f"<b>Account configuration update:</b>\n\n"
    if added:
        msg += f"✅ Added accounts: {', '.join('<b>@'+a+'</b>' for a in added)}\n"
    if skipped:
        msg += f"⚠️ Skipped (already exist): {', '.join('<b>@'+s+'</b>' for s in skipped)}\n"
        
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    await show_users_menu(update.message, edit=False)


async def cmd_del(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Command /del <username> - removes an account."""
    if str(update.effective_chat.id) != str(config.TELEGRAM_CHAT_ID):
        return
        
    if not context.args:
        await update.message.reply_text(
            "⚠️ <b>Usage:</b> <code>/del username</code>",
            parse_mode=ParseMode.HTML
        )
        return
        
    user_to_delete = context.args[0].strip().lstrip("@").strip()
    usernames = load_usernames()
    
    if user_to_delete in usernames:
        usernames.remove(user_to_delete)
        save_usernames(usernames)
        await update.message.reply_text(f"✅ Removed <b>@{html_escape(user_to_delete)}</b> from monitoring.", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(f"❌ Account <b>@{html_escape(user_to_delete)}</b> is not being monitored.", parse_mode=ParseMode.HTML)
        
    await show_users_menu(update.message, edit=False)


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Processes text messages from the owner, checking if we are waiting for usernames."""
    if str(update.effective_chat.id) != str(config.TELEGRAM_CHAT_ID):
        return
        
    if context.user_data.get('awaiting_usernames'):
        context.user_data['awaiting_usernames'] = False
        
        raw_input = update.message.text
        added, skipped = process_add_usernames(raw_input)
        
        msg = f"<b>Account configuration update:</b>\n\n"
        if added:
            msg += f"✅ Added accounts: {', '.join('<b>@'+a+'</b>' for a in added)}\n"
        if skipped:
            msg += f"⚠️ Skipped (already exist): {', '.join('<b>@'+s+'</b>' for s in skipped)}\n"
        if not added and not skipped:
            msg += "❌ No valid usernames found in your message."
            
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
        await show_users_menu(update.message, edit=False)


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
        
    if data == "manage_users":
        await query.answer()
        await show_users_menu(query.message)
        return

    if ":" not in data:
        await query.answer()
        return
        
    action, payload = data.split(":", 1)
    
    if action == "manage":
        await handle_manage_callback(update, context, payload)
        return
        
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
        tone = "base"
        tweet_id = payload
        await query.answer()
        await update_keyboard_loading(query.message, "reply")
        
        draft_msg = await query.message.reply_text("🤖 Generating AI reply draft...")
        tweet_text = extract_tweet_text_from_message(query.message.text)
        reply_text = await generate_ai_reply(tweet_text, tone)
        
        # Restore Reply button
        await update_keyboard_restore(query.message, "reply", tweet_id)
        
        if reply_text and not reply_text.startswith("❌"):
            # Save draft in memory
            REPLY_DRAFTS[draft_msg.message_id] = {
                "tweet_id": tweet_id,
                "text": reply_text,
                "tweet_text": tweet_text
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
        await query.answer()
        
        tweet_text = draft.get("tweet_text")
        if not tweet_text:
            # Fallback to reply_to_message in case of older drafts or memory reset
            parent_msg = query.message.reply_to_message
            if parent_msg and parent_msg.text:
                tweet_text = extract_tweet_text_from_message(parent_msg.text)
                draft["tweet_text"] = tweet_text
                
        if not tweet_text:
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
            await query.message.reply_text(f"❌ Failed to regenerate draft: {get_friendly_error_message(error_msg)}")
            
    elif action == "cancel_reply":
        draft_msg_id = int(payload)
        REPLY_DRAFTS.pop(draft_msg_id, None)
        await query.message.delete()
        await query.answer()


# ── Main loop ─────────────────────────────────────────────────────────────────
async def main() -> None:
    # Load initial usernames
    initial_usernames = load_usernames()

    logger.info("=" * 60)
    logger.info("🤖 Twitter → Telegram Bot starting")
    logger.info("   Accounts : %s", ", ".join(initial_usernames))
    logger.info("   Interval : %d min (total cycle for all accounts)", config.CHECK_INTERVAL_MINUTES)
    logger.info("   Batches  : %d parts", config.NUM_BATCHES)
    logger.info("=" * 60)

    # Initialize Twitter Client
    twitter_client.initialize()

    # Build Telegram Application
    application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    # Register command and message handlers for account management
    application.add_handler(CommandHandler("users", cmd_users))
    application.add_handler(CommandHandler("menu", cmd_menu))
    application.add_handler(CommandHandler("add", cmd_add))
    application.add_handler(CommandHandler("del", cmd_del))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

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

    # Start background daily cleanup task
    cleanup_task = asyncio.create_task(daily_cleanup_task())

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
        # Reload usernames list dynamically
        usernames = load_usernames()
        
        if not usernames:
            logger.warning("⚠️ No usernames configured for monitoring. Sleeping for %d min...", config.CHECK_INTERVAL_MINUTES)
            interval_sec = config.CHECK_INTERVAL_MINUTES * 60
        else:
            # Get next batch of usernames
            batch = batch_manager.get_next_batch(usernames)
            logger.info("📡 Checking batch of %d user(s): %s", len(batch), ", ".join(batch))
            
            try:
                seen_ids = await check_new_tweets(application.bot, seen_ids, batch)
            except Exception as exc:
                logger.exception("⚠️ Unexpected error in monitoring loop: %s", exc)
                
            # Calculate sleep interval based on current list size
            interval_sec = batch_manager.calculate_sleep_interval(len(usernames), config.CHECK_INTERVAL_MINUTES)
            logger.info("⏳ Sleeping for %.1f minutes (%.1f seconds) until next batch check...", interval_sec / 60, interval_sec)

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
    usernames = load_usernames()
    username = usernames[0] if usernames else "unknown"
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
