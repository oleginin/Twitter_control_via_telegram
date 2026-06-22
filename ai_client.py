"""
ai_client.py — Interacts with Google Gemini API to generate replies based on settings in reply_settings.md
"""
import logging
import asyncio
from pathlib import Path
import requests

import config

logger = logging.getLogger("TwitterBot.AI")

SETTINGS_FILE = Path(__file__).parent / "reply_settings.md"


def _read_settings() -> str:
    """Reads reply settings dynamically from reply_settings.md."""
    if not SETTINGS_FILE.exists():
        logger.warning("reply_settings.md not found at %s. Using default instructions.", SETTINGS_FILE)
        return "Keep replies concise, friendly, and engaging."
    try:
        return SETTINGS_FILE.read_text(encoding="utf-8")
    except Exception as exc:
        logger.error("Failed to read reply_settings.md: %s", exc)
        return "Keep replies concise, friendly, and engaging."


def _call_gemini_api(prompt: str) -> str:
    """Synchronous network call to Gemini API with retry and fallback."""
    if not config.GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY is not configured in .env!")
        return "❌ GEMINI_API_KEY is missing. Please set it in your .env file."

    # List of models to try in order
    models = ["gemini-2.5-flash", "gemini-1.5-flash"]
    
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": prompt
                    }
                ]
            }
        ]
    }

    last_error = None
    import time

    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={config.GEMINI_API_KEY}"
        
        # Try up to 3 times per model
        max_retries = 3
        for attempt in range(max_retries):
            try:
                logger.info("Calling Gemini API with model %s (attempt %d/%d)...", model, attempt + 1, max_retries)
                response = requests.post(url, json=payload, headers=headers, timeout=15)
                
                # Check for transient errors to trigger retry
                if response.status_code in (503, 429, 500):
                    response.raise_for_status()
                    
                response.raise_for_status()
                data = response.json()
                
                # Parse the response text
                candidates = data.get("candidates", [])
                if candidates:
                    content = candidates[0].get("content", {})
                    parts = content.get("parts", [])
                    if parts:
                        return parts[0].get("text", "").strip()
                
                logger.error("Invalid response structure from Gemini API: %s", data)
                return "❌ Error: Invalid response structure from Gemini API."
                
            except requests.RequestException as exc:
                last_error = exc
                status_code = getattr(exc.response, 'status_code', None) if exc.response else None
                logger.warning(
                    "Gemini API request failed for %s (status: %s): %s.", 
                    model, status_code, exc
                )
                if attempt < max_retries - 1:
                    sleep_time = 2 ** attempt
                    logger.info("Retrying in %d seconds...", sleep_time)
                    time.sleep(sleep_time)
                else:
                    logger.warning("Max retries reached for model %s.", model)
                    
            except Exception as exc:
                last_error = exc
                logger.exception("Unexpected error in Gemini API call: %s", exc)
                break

    return f"❌ Gemini API Error (Tried models {models}): {last_error}"


async def generate_ai_reply(tweet_text: str) -> str:
    """
    Generates an AI reply draft for a given tweet text.
    Reads reply_settings.md guidelines on every invocation and runs the HTTP call in a thread pool.
    """
    settings = _read_settings()
    
    prompt = (
        "You are a helpful AI assistant that writes engaging replies to tweets on Twitter/X.\n\n"
        "Here are the instructions and guidelines you MUST strictly follow:\n"
        "-------------------------------------\n"
        f"{settings}\n"
        "-------------------------------------\n\n"
        "Here is the tweet you need to reply to:\n"
        f"\"{tweet_text}\"\n\n"
        "Generate ONLY the reply text. Do NOT include quotes, explanations, prefixes, or any extra text. "
        "Output the raw reply ready to be posted."
    )

    logger.info("Generating reply draft for tweet: %s...", tweet_text[:60].replace("\n", " "))
    
    # Run the synchronous API call in an executor thread to avoid blocking the asyncio event loop
    reply = await asyncio.to_thread(_call_gemini_api, prompt)
    
    # Clean up double quotes if the model wrapped the entire output in quotes
    if reply.startswith('"') and reply.endswith('"') and len(reply) > 1:
        reply = reply[1:-1].strip()
        
    return reply
