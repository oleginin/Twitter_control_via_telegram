# Twitter/X → Telegram Monitor & Control Bot

An advanced, asynchronous Python bot that monitors specified Twitter/X accounts via Nitter RSS feeds and sends new posts to Telegram. Includes a Telegram inline control panel (**Like, Bookmark, Reply**) to interact with X.com using cookie authentication, proxy support, and Google Gemini AI.

---

## Key Features

1. **Flexible Profile Management** — Tracks multiple Twitter/X accounts configured via `twitter_usernames.txt` or directly via Telegram.
2. **Interactive Telegram Control Panel** — Rich control options under each tweet notification message:
   - ❤️ **Like** (Favorites the post on X.com)
   - 🔖 **Bookmark** (Bookmarks the post on X.com)
   - 💬 **Reply** (Drafts an AI reply using Gemini, with buttons to send/regenerate/cancel)
3. **Interactive Account Administration** — Manage the list of monitored accounts directly in Telegram:
   - Command `/users` displays a beautiful inline list with **➕ Add Account** and **🗑️ Delete Account** buttons.
   - Fast commands: `/add <username>` and `/del <username>`.
4. **Staggered Parsing (Staggering)** — The accounts list is dynamically divided into `NUM_BATCHES` (default: 2) and checks are spread out evenly over the `CHECK_INTERVAL_MINUTES` to avoid X/Nitter rate limiting.
5. **Daily Log Rotation** — Automatically closes and clears/truncates `bot.log` and other log files once a day at midnight to conserve server storage space.
6. **Google Gemini AI Integration** — Generates tweet replies using Gemini 2.5 Flash with custom styling instructions loaded dynamically from `reply_settings.md` (no restarts needed).
7. **First-Run Flood Protection** — When a new user is added or on startup, the bot sends only the single latest tweet to prevent spamming the chat.
8. **No Paid X API Needed** — Uses browser session cookies (`auth_token` and `ct0`) and standard HTTP/S proxy configuration to interact with X.com.
9. **Automatic Patching** — Built-in `patch_twikit.py` script automatically fixes upstream `twikit` issues.

---

## Quick Start (Ubuntu Server / VPS Deployment)

### 1. Update and install packages
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv git
```

### 2. Clone the repository and navigate
```bash
git clone <your-repository-url> twitter_bot
cd twitter_bot
```

### 3. Set up Python virtual environment
```bash
python3 -m venv venv
source venv/bin/activate
```

### 4. Install dependencies
```bash
pip install -r requirements.txt
```

### 5. Apply library patches
Run the custom patch script to resolve upstream twikit library bugs:
```bash
python patch_twikit.py
```

### 6. Configure `.env`
Copy the template and edit it:
```bash
cp .env.example .env
nano .env
```

| Variable | Description |
|----------|-------------|
| `TWITTER_USERNAMES` | Comma-separated list of accounts (legacy fallback; preferred way is `twitter_usernames.txt` or Telegram interface) |
| `TWITTER_COOKIE_AUTH_TOKEN` | Your account session `auth_token` cookie from x.com |
| `TWITTER_COOKIE_CT0` | Your account session `ct0` cookie from x.com |
| `TWITTER_PROXY` | Proxy URL for X.com actions (e.g., `http://user:pass@ip:port`) |
| `GEMINI_API_KEY` | API key for direct Google Gemini integration (legacy) |
| `AI_PROVIDER` | AI provider type: `gemini` (default) or `openai_compatible` |
| `AI_API_KEY` | API Key for the selected provider (falls back to `GEMINI_API_KEY`) |
| `AI_API_URL` | API endpoint for OpenAI-compatible providers (default: OpenRouter) |
| `AI_MODEL` | AI Model name (e.g., `google/gemini-2.5-flash:free`, `meta-llama/llama-3-8b-instruct:free`) |
| `TELEGRAM_BOT_TOKEN` | Token from [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | Telegram chat or channel ID to send updates |
| `CHECK_INTERVAL_MINUTES` | Frequency of a full loop check across all accounts (default: `5` minutes) |
| `NUM_BATCHES` | Number of parts to split the accounts list into (default: `2`) |

> **Note**: Usernames are stored and managed in the `twitter_usernames.txt` file which is created dynamically at the first start of the bot.

---

## Run Modes

```bash
# Start standard background monitoring loop
python bot.py

# Send the latest tweet to Telegram immediately (ignores seen logs, for testing)
python bot.py --send-now

# Output the latest tweet in the console without sending (for testing)
python bot.py --test

# List available chat IDs (send /start to the bot in Telegram first)
python bot.py --get-chat-id
```

---

## Telegram Bot Commands

Authorized user (`TELEGRAM_CHAT_ID`) can use the following commands in the chat:
* `/users` — Open interactive menu to view accounts list, add new ones, or delete existing ones.
* `/add <username1, username2>` — Quickly add new Twitter account(s) to the monitor list.
* `/del <username>` — Quickly remove a Twitter account from the monitor list.

---

## Customizing AI Replies

You can change the rules for AI-generated comments in the [reply_settings.md](reply_settings.md) file.
The bot reads this file on every generation request, so **you do not need to restart the bot** for settings changes to take effect.

---

## Running Continuously with systemd (VPS)

1. Create a service file:
```bash
sudo nano /etc/systemd/system/twitter-bot.service
```

2. Paste the following configuration (replace `<your_user>` with your Ubuntu username, e.g., `ubuntu`):
```ini
[Unit]
Description=Twitter to Telegram Control Bot
After=network.target

[Service]
Type=simple
User=<your_user>
WorkingDirectory=/home/<your_user>/twitter_bot
ExecStart=/home/<your_user>/twitter_bot/venv/bin/python bot.py
Restart=always
RestartSec=10
Environment=PYTHONUTF8=1

[Install]
WantedBy=multi-user.target
```

3. Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable twitter-bot
sudo systemctl start twitter-bot
```

4. View logs in real-time:
```bash
journalctl -u twitter-bot -f -n 50
```
