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
    """Synchronous network call to Gemini API with key rotation on error."""
    if not config.GEMINI_API_KEYS:
        logger.warning("No GEMINI_API_KEY configured in .env!")
        return "❌ GEMINI_API_KEY is missing. Please set it in your .env file."

    # List of models to try in order (prioritizing user preference)
    models = ["gemini-3.1-flash-lite", "gemini-3.5-flash", "gemini-2.5-flash"]
    
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

    # Iterate over available API keys (rotation on failure)
    for key_idx, api_key in enumerate(config.GEMINI_API_KEYS):
        # Log masked key for debugging
        masked_key = f"...{api_key[-6:]}" if len(api_key) > 6 else "invalid_key"
        logger.info("Using Gemini API key %d/%d (ends in %s)", key_idx + 1, len(config.GEMINI_API_KEYS), masked_key)
        
        # For the current key, try models in order
        for model in models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            
            # Try up to 2 times for transient errors
            max_retries = 2
            for attempt in range(max_retries):
                try:
                    logger.debug("Requesting Gemini model %s (attempt %d/%d)...", model, attempt + 1, max_retries)
                    response = requests.post(url, json=payload, headers=headers, timeout=30)
                    
                    # If rate limited (429) or other API limit errors, raise error to trigger key rotation
                    if response.status_code == 429:
                        logger.warning("Rate limit (429) encountered with current API key. Triggering key rotation.")
                        response.raise_for_status()
                    elif response.status_code == 403:
                        logger.warning("Forbidden/Auth error (403) with current API key. Key might be invalid or quota exceeded. Triggering key rotation.")
                        response.raise_for_status()
                        
                    response.raise_for_status()
                    data = response.json()
                    
                    candidates = data.get("candidates", [])
                    if candidates:
                        content = candidates[0].get("content", {})
                        parts = content.get("parts", [])
                        if parts:
                            return parts[0].get("text", "").strip()
                    
                    logger.error("Invalid response structure from Gemini API: %s", data)
                    raise Exception("Invalid response structure from Gemini API.")
                    
                except requests.RequestException as exc:
                    last_error = exc
                    status_code = getattr(exc.response, 'status_code', None) if exc.response else None
                    logger.warning(
                        "Gemini API request failed for model %s with key %d (status: %s): %s", 
                        model, key_idx + 1, status_code, exc
                    )
                    
                    # If the error is an authentication/quota/rate limit issue (403, 429), immediately break
                    # out of the retry loop to rotate the key immediately
                    if status_code in (403, 429):
                        break
                        
                    if attempt < max_retries - 1:
                        sleep_time = 2 ** attempt
                        time.sleep(sleep_time)
                except Exception as exc:
                    last_error = exc
                    logger.exception("Unexpected error during Gemini API call: %s", exc)
                    break
            
            # If the current key failed with a critical quota/auth error (403/429), skip other models for this key
            status_code = getattr(getattr(last_error, 'response', None), 'status_code', None) if last_error else None
            if status_code in (403, 429):
                logger.info("Skipping remaining models for current key due to status %s. Rotating key...", status_code)
                break

    return f"❌ Gemini API Error (Tried keys: {len(config.GEMINI_API_KEYS)}): {last_error}"


def _call_openai_api(prompt: str) -> str:
    """Calls any OpenAI-compatible API (OpenRouter, Groq, DeepSeek, local Ollama, etc.)."""
    if not config.AI_API_KEY and "localhost" not in config.AI_API_URL and "127.0.0.1" not in config.AI_API_URL:
        logger.warning("AI_API_KEY is not configured in .env!")
        return "❌ AI_API_KEY is missing. Please set it in your .env file."

    url = config.AI_API_URL
    if not url.endswith("/chat/completions") and not url.endswith("/generate"):
        url = url.rstrip("/") + "/chat/completions"

    headers = {
        "Content-Type": "application/json",
    }
    if config.AI_API_KEY:
        headers["Authorization"] = f"Bearer {config.AI_API_KEY}"

    # Split models list by commas
    models = [m.strip() for m in config.AI_MODEL.split(",") if m.strip()]
    if not models:
        models = ["google/gemini-2.5-flash:free"]
        
    # Randomly shuffle models to spread rate limits
    import random
    random.shuffle(models)
    
    last_error = None
    
    for model in models:
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.7
        }

        # Try up to 2 times for each model
        max_retries = 2
        import time
        for attempt in range(max_retries):
            try:
                logger.info("Calling OpenAI-compatible API (%s) using model %s (attempt %d/%d)...", url, model, attempt + 1, max_retries)
                response = requests.post(url, json=payload, headers=headers, timeout=20)
                
                # Check for transient errors to trigger retry
                if response.status_code in (503, 429, 500):
                    response.raise_for_status()
                    
                response.raise_for_status()
                data = response.json()
                
                # Some API providers return error inside the JSON response with 200 OK
                if "error" in data:
                    err_msg = data["error"].get("message", str(data["error"]))
                    raise Exception(f"API Error returned: {err_msg}")
                
                choices = data.get("choices", [])
                if choices:
                    message = choices[0].get("message", {})
                    content = message.get("content", "")
                    if content:
                        return content.strip()
                
                logger.error("Invalid response structure from API for model %s: %s", model, data)
                raise Exception("Invalid response structure from API.")
                
            except Exception as exc:
                last_error = exc
                status_code = getattr(exc, 'response', None)
                if status_code:
                    status_code = getattr(status_code, 'status_code', None)
                logger.warning("API request failed for model %s (attempt %d/%d, status: %s): %s", model, attempt + 1, max_retries, status_code, exc)
                if attempt < max_retries - 1:
                    sleep_time = 2 ** attempt
                    time.sleep(sleep_time)
                else:
                    logger.warning("Failed all attempts for model %s. Trying next model...", model)

    return f"❌ All OpenAI-compatible models failed. Last error: {last_error}"


async def generate_ai_reply(tweet_text: str, tone: str = 'base') -> str:
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
        "-------------------------------------\n\n" + '\n        "Here is the tone you must use:\\n"\n        f"Tone: {tone}\\n\\n"\n' + 
        "Here is the tweet you need to reply to:\n"
        f"\"{tweet_text}\"\n\n"
        "Generate ONLY the reply text. Do NOT include quotes, explanations, prefixes, or any extra text. "
        "Output the raw reply ready to be posted."
    )

    logger.info("Generating reply draft for tweet: %s...", tweet_text[:60].replace("\n", " "))
    
    # Run the synchronous API call in an executor thread to avoid blocking the asyncio event loop
    try:
        if config.AI_PROVIDER == "gemini":
            reply = await asyncio.wait_for(asyncio.to_thread(_call_gemini_api, prompt), timeout=90.0)
        else:
            reply = await asyncio.wait_for(asyncio.to_thread(_call_openai_api, prompt), timeout=45.0)
    except asyncio.TimeoutError:
        logger.error("Timeout generating AI reply (took longer than 45s)")
        return "❌ Error: AI reply generation timed out (took longer than 45 seconds). Please try again or switch to a faster model/provider."
    
    # Clean up double quotes if the model wrapped the entire output in quotes
    if reply.startswith('"') and reply.endswith('"') and len(reply) > 1:
        reply = reply[1:-1].strip()
        
    return reply
