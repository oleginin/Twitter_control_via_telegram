# Twitter/X → Telegram Monitor & Control Bot

An advanced, asynchronous Python bot that monitors specified Twitter/X accounts via Nitter RSS feeds and sends new posts to Telegram. Includes a Telegram inline control panel (**Like, Bookmark, Reply**) to interact with X.com using cookie authentication, proxy support, and Google Gemini AI.

---

## Key Features

1. **Multi-Profile Monitoring** — Tracks multiple Twitter/X accounts configured via comma-separated list.
2. **Interactive Inline Keyboard** — Control panel under each notification message:
   - ❤️ **Like** (Favorites the post on X)
   - 🔖 **Bookmark** (Bookmarks the post on X)
   - 💬 **Reply** (Drafts an AI-generated reply using Gemini, allowing you to edit/regenerate/cancel before posting)
3. **Google Gemini AI Integration** — Generates tweet replies using Google Gemini 2.5 Flash. Styling rules are dynamically read from `reply_settings.md` (no restart needed).
4. **First-Run Flood Protection** — When a new user is added or on first launch, the bot sends only the single latest tweet to prevent chat flooding.
5. **No Paid X API Needed** — Utilizes browser session cookies (`auth_token` and `ct0`) and standard HTTP/S proxy configuration to interact with X.com.
6. **Automatic Patching** — Includes a `patch_twikit.py` script to patch local library issues (KeyError urls, JS transaction extraction) during deployment.

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
| `TWITTER_USERNAMES` | Comma-separated list of accounts to monitor (e.g. `0leshkoo,elonmusk`) |
| `TWITTER_COOKIE_AUTH_TOKEN` | Your account session `auth_token` cookie from x.com |
| `TWITTER_COOKIE_CT0` | Your account session `ct0` cookie from x.com |
| `TWITTER_PROXY` | Proxy URL for X.com actions (e.g., `http://user:pass@ip:port`) |
| `GEMINI_API_KEY` | Free API key from [Google AI Studio](https://aistudio.google.com/) |
| `TELEGRAM_BOT_TOKEN` | Token from [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | Telegram chat or channel ID to send updates |
| `CHECK_INTERVAL_MINUTES` | Frequency of checks (default: `5` minutes) |

> **To obtain X cookies**: Log into x.com in your browser, press F12, go to Application (Chrome) or Storage (Firefox) -> Cookies -> `https://x.com` and copy the values for `auth_token` and `ct0`.

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
