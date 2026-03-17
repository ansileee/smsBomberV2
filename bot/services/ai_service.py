"""
AI service using Google Gemini API (free tier).
Models tried in order: gemini-1.5-flash -> gemini-1.5-flash-8b -> gemini-pro
"""
from __future__ import annotations

import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=30)

GEMINI_MODELS = [
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
    "gemini-1.0-pro",
    "gemini-pro",
]

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


async def askGemini(prompt: str, systemPrompt: str = "") -> Optional[str]:
    """Try Gemini models in order. Returns text or None."""
    from bot.config import GEMINI_API_KEY
    if not GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY not set")
        return None

    payload: dict = {
        "contents": [
            {"role": "user", "parts": [{"text": prompt}]}
        ],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 1024,
        },
    }
    if systemPrompt:
        payload["system_instruction"] = {
            "parts": [{"text": systemPrompt}]
        }

    async with aiohttp.ClientSession() as session:
        for model in GEMINI_MODELS:
            url = f"{BASE_URL.format(model=model)}?key={GEMINI_API_KEY}"
            try:
                async with session.post(url, json=payload, timeout=TIMEOUT) as resp:
                    raw = await resp.json(content_type=None)
                    logger.info(f"Gemini {model}: status={resp.status}")

                    if resp.status == 200:
                        # Extract text from response
                        candidates = raw.get("candidates", [])
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts", [])
                            if parts:
                                text = parts[0].get("text", "").strip()
                                if text:
                                    return text
                        # If candidates empty, might be blocked
                        blocked = raw.get("promptFeedback", {}).get("blockReason", "")
                        if blocked:
                            logger.warning(f"Gemini blocked: {blocked}")
                            return "[Response blocked by safety filter]"
                        logger.warning(f"Gemini {model} empty response: {raw}")
                        continue

                    elif resp.status in (400, 404):
                        # Model not found or bad request — try next model
                        err = raw.get("error", {}).get("message", "")
                        logger.warning(f"Gemini {model} {resp.status}: {err}")
                        continue

                    elif resp.status == 429:
                        logger.warning(f"Gemini {model} rate limited")
                        return None

                    else:
                        err = raw.get("error", {}).get("message", "")
                        logger.warning(f"Gemini {model} error {resp.status}: {err}")
                        continue

            except Exception as ex:
                logger.warning(f"Gemini {model} exception: {ex}")
                continue

    return None


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

ADMIN_AI_SYSTEM = """You are an expert AI assistant for the smsBomber OTP testing platform, built by @drazeforce.

You help the admin with:
- API config format and placeholders ({phone}, {uuid}, {random_email}, etc.)
- Parsing and converting cURL commands to our JSON config format
- Platform internals: round-robin engine, rate limiting, dead API detection, proxy setup
- HTTP concepts: headers, status codes, CORS, request bodies
- Debugging API configs

Our JSON config format:
{
  "name": "SiteName",
  "method": "POST",
  "url": "https://api.example.com/otp",
  "headers": {"content-type": "application/json"},
  "json": {"phone": "{phone}"}
}

Phone placeholders:
- Plain 10-digit: {phone}
- With 91 prefix string: "91{phone}"
- With +91 prefix: "+91{phone}"
- Integer type: 91{phone} (no quotes in JSON)

Body keys: "json" for application/json, "data" for form-encoded, "params" for query strings.
Strip headers: cookie, sec-*, content-length, accept-encoding, connection, host.

Be concise, technical, and direct. No emojis."""


def buildAdminPrompt(history: list, newMessage: str) -> str:
    parts = []
    for turn in history[-8:]:
        parts.append(f"User: {turn['user']}\nAssistant: {turn['bot']}")
    parts.append(f"User: {newMessage}")
    return "\n\n".join(parts)