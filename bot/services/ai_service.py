"""
AI service using Google Gemini API.
Used by beta panel admin AI chat and cURL converter fallback.
"""
from __future__ import annotations

import json
from typing import Optional

import aiohttp

from bot.config import GEMINI_API_KEY

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

TIMEOUT = aiohttp.ClientTimeout(total=30)


async def askGemini(prompt: str, systemPrompt: str = "") -> Optional[str]:
    """Send a prompt to Gemini and return the text response."""
    if not GEMINI_API_KEY:
        return None

    payload = {
        "contents": [
            {"role": "user", "parts": [{"text": prompt}]}
        ],
    }

    if systemPrompt:
        payload["system_instruction"] = {
            "parts": [{"text": systemPrompt}]
        }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{GEMINI_URL}?key={GEMINI_API_KEY}",
                json=payload,
                timeout=TIMEOUT,
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                text = (
                    data.get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "")
                    .strip()
                )
                return text if text else None
    except Exception:
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
    """Build conversation prompt from history."""
    parts = []
    for turn in history[-8:]:
        parts.append(f"User: {turn['user']}\nAssistant: {turn['bot']}")
    parts.append(f"User: {newMessage}")
    return "\n\n".join(parts)