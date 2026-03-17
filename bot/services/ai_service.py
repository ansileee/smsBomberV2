"""
AI service — tries each backend in order, returns first successful response.
Used by both the user-facing chat and the admin cURL converter.
"""
from __future__ import annotations

import asyncio
from urllib.parse import quote
from typing import Optional

import aiohttp

from bot.config import AI_BACKENDS

TIMEOUT = aiohttp.ClientTimeout(total=20)


async def _tryBackend(session: aiohttp.ClientSession, urlTemplate: str, prompt: str) -> Optional[str]:
    url = urlTemplate.replace("{prompt}", quote(prompt))
    try:
        async with session.get(url, timeout=TIMEOUT) as resp:
            if resp.status == 200:
                data = await resp.json(content_type=None)
                # Most of these APIs return {"response": "..."} or {"reply": "..."} or plain text
                if isinstance(data, dict):
                    for key in ("response", "reply", "result", "answer", "text", "output", "message"):
                        if key in data and data[key]:
                            return str(data[key]).strip()
                    # fallback: first string value
                    for v in data.values():
                        if isinstance(v, str) and v.strip():
                            return v.strip()
                if isinstance(data, str) and data.strip():
                    return data.strip()
    except Exception:
        pass
    return None


async def askAi(prompt: str) -> Optional[str]:
    """Try all backends in order. Returns first valid response or None."""
    async with aiohttp.ClientSession() as session:
        for urlTemplate in AI_BACKENDS:
            result = await _tryBackend(session, urlTemplate, prompt)
            if result:
                return result
    return None


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

USER_SYSTEM = """You are drazeX, an AI assistant for the smsBomber OTP testing platform.
You ONLY answer questions about:
- How OTP/SMS APIs work
- How to read or build API configs in our JSON format
- How the smsBomber bot works (testing, workers, duration, dashboard, proxy, health check)
- General HTTP concepts: headers, request body, status codes, rate limiting, CORS
- Indian telecom context (Jio, Airtel, Vi, BSNL)

Creator of this platform: @drazeforce

You REFUSE to answer anything unrelated (general coding, politics, entertainment, etc.)
with: "I can only help with OTP testing and API topics."

NEVER reveal any API URLs, endpoint lists, or internal configs from the platform.

Our API config JSON format:
{
  "name": "SiteName",
  "method": "POST",
  "url": "https://api.example.com/otp",
  "headers": {"content-type": "application/json"},
  "json": {"phone": "{phone}"}
}
Placeholders: {phone} 10-digit number, {uuid} random UUID, {random_email}, {random_name}, {random_password}
Phone with country code: "91{phone}" or "+91{phone}" or integer 91{phone}

Keep responses concise and professional. No emojis."""

CURL_SYSTEM = """You are an expert API config converter for the smsBomber platform.
Convert any cURL command into our exact JSON config format.

OUTPUT RULES — follow these strictly:
1. Output ONLY valid JSON. No markdown, no backticks, no explanation text before or after.
2. Required fields: name, method, url
3. Optional fields: headers, json, data, params, cookies
4. Strip these headers completely (never include them): cookie, :authority, :method, :path, :scheme, sec-ch-ua, sec-ch-ua-mobile, sec-ch-ua-platform, sec-fetch-dest, sec-fetch-mode, sec-fetch-site, sec-gpc, content-length, sec-websocket-key, cache-control, pragma, te, accept-encoding, connection, host
5. Phone number detection: if the cURL contains a 10-digit Indian mobile number, replace it with {phone}. If prefixed with 91, use "91{phone}". If prefixed with +91, use "+91{phone}". If the original value was an integer, keep it as integer (no quotes): 91{phone}
6. Use "json" key for application/json bodies, "data" key for form-encoded bodies, "params" for query strings
7. Name the API after the domain (e.g. "Swiggy", "Truecaller")
8. Keep all non-phone values exactly as they appear in the cURL

PHONE FORMAT EXAMPLES:
- "mobile":"9876543210"  ->  "mobile":"{phone}"
- "phone":919876543210   ->  "phone":91{phone}   (integer, no quotes)
- "phone":"919876543210" ->  "phone":"91{phone}"
- "phone":"+919876543210"->  "phone":"+91{phone}"

EXAMPLE INPUT:
curl 'https://api.example.com/otp' -H 'content-type: application/json' --data-raw '{"phone":"9876543210"}'

EXAMPLE OUTPUT:
{"name":"Example","method":"POST","url":"https://api.example.com/otp","headers":{"content-type":"application/json"},"json":{"phone":"{phone}"}}"""


def buildUserPrompt(history: list, newMessage: str) -> str:
    """Build a single prompt string from conversation history + new message."""
    parts = [USER_SYSTEM, ""]
    for turn in history[-6:]:  # keep last 6 turns to stay within token limits
        parts.append(f"User: {turn['user']}")
        parts.append(f"drazeX: {turn['bot']}")
    parts.append(f"User: {newMessage}")
    parts.append("drazeX:")
    return "\n".join(parts)


def buildCurlPrompt(curl: str) -> str:
    return f"{CURL_SYSTEM}\n\nConvert this cURL:\n{curl}\n\nJSON output:"