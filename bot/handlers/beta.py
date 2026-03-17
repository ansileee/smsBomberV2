"""
Beta Tester panel — admin-only area for experimental features.
Currently includes:
  1. cURL to JSON converter (AI-powered via external free APIs)
  2. Admin AI Chat
Note: AI features require working AI backends in config.py AI_BACKENDS.
      If all backends are down, a maintenance message is shown.
"""
from __future__ import annotations

import json

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.services.api_manager import apiManager
from bot.utils import PM, b, i, c, hEsc as esc, isAdmin

router = Router()

_adminChatHistory: dict = {}


class BetaStates(StatesGroup):
    waitingCurl  = State()
    curlConfirm  = State()
    adminAiChat  = State()


def betaMenuKeyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="cURL to API (AI)", callback_data="beta:curl")
    builder.button(text="Admin AI Chat",    callback_data="beta:ai_chat")
    builder.button(text="Back",             callback_data="adm:menu")
    builder.adjust(2, 1)
    return builder.as_markup()


def curlResultKeyboard(hasJson: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if hasJson:
        builder.button(text="Save to Bot", callback_data="beta:curl_save")
        builder.button(text="Demo Test",   callback_data="beta:curl_demotest")
    builder.button(text="Try Another", callback_data="beta:curl")
    builder.button(text="Beta Menu",   callback_data="beta:menu")
    builder.adjust(2, 2) if hasJson else builder.adjust(1, 1)
    return builder.as_markup()


def adminAiKeyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Clear Chat", callback_data="beta:ai_clear")
    builder.button(text="Beta Menu",  callback_data="beta:menu")
    builder.adjust(2)
    return builder.as_markup()


async def _callAi(prompt: str):
    """Try AI backends. Returns response string or None."""
    from bot.config import AI_BACKENDS
    from urllib.parse import quote
    import aiohttp
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession() as session:
        for urlTemplate in AI_BACKENDS:
            url = urlTemplate.replace("{prompt}", quote(prompt))
            try:
                async with session.get(url, timeout=timeout) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        if isinstance(data, dict):
                            for key in ("response", "reply", "result", "answer", "text", "output", "message"):
                                if key in data and data[key]:
                                    return str(data[key]).strip()
                            for v in data.values():
                                if isinstance(v, str) and v.strip():
                                    return v.strip()
                        if isinstance(data, str) and data.strip():
                            return data.strip()
            except Exception:
                continue
    return None


CURL_SYSTEM = """You are an expert API config converter for the smsBomber platform.
Convert any cURL command into our exact JSON config format.

OUTPUT RULES:
1. Output ONLY valid JSON. No markdown, no backticks, no explanation.
2. Required fields: name, method, url
3. Optional: headers, json, data, params, cookies
4. Strip: cookie, :authority, :method, :path, :scheme, sec-ch-ua, sec-ch-ua-mobile, sec-ch-ua-platform, sec-fetch-dest, sec-fetch-mode, sec-fetch-site, sec-gpc, content-length, accept-encoding, connection, host
5. Phone detection: replace 10-digit Indian number with {phone}. If prefixed 91 use "91{phone}", +91 use "+91{phone}", integer use 91{phone} (no quotes)
6. Use "json" for application/json, "data" for form-encoded, "params" for query strings
7. Name after domain

Example output:
{"name":"Example","method":"POST","url":"https://api.example.com/otp","headers":{"content-type":"application/json"},"json":{"phone":"{phone}"}}"""

ADMIN_AI_SYSTEM = """You are an AI assistant for the smsBomber OTP testing platform admin.
Answer questions about: API configs, cURL conversion, platform internals, HTTP concepts, rate limiting, proxy setup.
Creator: @drazeforce
Be concise and technical. No emojis."""


@router.callback_query(F.data == "beta:menu")
async def cbBetaMenu(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        f"{b('Beta Tester')}\n\n"
        f"Experimental features for admin use.\n\n"
        f"{b('cURL to API')}\n"
        f"Paste a raw cURL command — AI converts it to our JSON config format.\n\n"
        f"{b('Admin AI Chat')}\n"
        f"Ask the AI about APIs, platform internals, HTTP concepts.",
        reply_markup=betaMenuKeyboard(),
        parse_mode=PM
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# cURL converter
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "beta:curl")
async def cbCurl(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    await state.set_state(BetaStates.waitingCurl)
    await state.update_data(pendingCurlCfg=None)
    builder = InlineKeyboardBuilder()
    builder.button(text="Cancel", callback_data="beta:menu")
    await callback.message.edit_text(
        f"{b('cURL to API Converter')}\n\n"
        f"Paste a raw cURL command.\n"
        f"The AI will parse it and output a ready-to-save JSON config.\n\n"
        f"{i('Paste the cURL command now.')}",
        reply_markup=builder.as_markup(),
        parse_mode=PM
    )
    await callback.answer()


@router.message(StateFilter(BetaStates.waitingCurl))
async def handleCurl(message: Message, state: FSMContext) -> None:
    if not isAdmin(message.from_user.id):
        return
    curl = (message.text or "").strip()
    if not curl.startswith("curl"):
        await message.answer(
            f"{b('Invalid')}\n\nInput must start with {c('curl')}.", parse_mode=PM
        )
        return

    thinking = await message.answer(f"{i('AI is converting your cURL...')}", parse_mode=PM)
    prompt   = f"{CURL_SYSTEM}\n\nConvert this cURL:\n{curl}\n\nJSON output:"
    result   = await _callAi(prompt)

    if not result:
        await thinking.edit_text(
            f"{b('AI Unavailable')}\n\n"
            f"All AI backends are currently unreachable.\n"
            f"Please try again later or paste the JSON config manually using Add API.",
            reply_markup=curlResultKeyboard(False),
            parse_mode=PM
        )
        return

    raw = result.strip()
    if raw.startswith("```"):
        lines = [l for l in raw.splitlines() if not l.startswith("```")]
        raw   = "\n".join(lines).strip()

    ok, cfg, error = apiManager.validateApiJson(raw)
    if not ok:
        await thinking.edit_text(
            f"{b('Parse Error')}\n\n"
            f"AI returned a response but it failed validation:\n{c(error)}\n\n"
            f"AI output:\n<pre>{esc(raw[:400])}</pre>\n\n"
            f"Try again or paste the JSON manually.",
            reply_markup=curlResultKeyboard(False),
            parse_mode=PM
        )
        return

    await state.update_data(pendingCurlCfg=cfg, pendingCurlJson=json.dumps(cfg))
    await state.set_state(BetaStates.curlConfirm)

    lines = [f"{b('Converted Successfully')}\n"]
    lines.append(f"Name    {c(esc(cfg['name']))}")
    lines.append(f"Method  {c(cfg['method'])}")
    lines.append(f"URL     {c(esc(cfg['url']))}")
    if cfg.get("headers"):
        lines.append(f"Headers {c(str(len(cfg['headers'])))} fields")
    if cfg.get("json"):
        lines.append(f"Body    JSON  {c(str(len(cfg['json'])))} fields")
    elif cfg.get("data"):
        lines.append(f"Body    Form  {c(str(len(cfg['data'])))} fields")
    if cfg.get("params"):
        lines.append(f"Params  {c(str(len(cfg['params'])))} fields")
    lines.append(f"\n<pre>{esc(json.dumps(cfg, indent=2))}</pre>")
    lines.append(f"\n{i('Save it or run a Demo Test first.')}")

    await thinking.edit_text("\n".join(lines), reply_markup=curlResultKeyboard(True), parse_mode=PM)


@router.callback_query(F.data == "beta:curl_save", StateFilter(BetaStates.curlConfirm))
async def cbCurlSave(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    data    = await state.get_data()
    cfg     = data.get("pendingCurlCfg")
    if not cfg:
        await callback.answer("Session expired.", show_alert=True)
        await state.clear()
        return
    from bot.services.database import db
    db.addCustomApi(name=cfg["name"], method=cfg["method"], url=cfg["url"], configJson=json.dumps(cfg))
    await state.clear()
    builder = InlineKeyboardBuilder()
    builder.button(text="API Manager", callback_data="aapi:menu")
    builder.button(text="Beta Menu",   callback_data="beta:menu")
    builder.adjust(2)
    await callback.message.edit_text(
        f"{b('Saved.')}  {esc(cfg['name'])} added to API list.",
        reply_markup=builder.as_markup(), parse_mode=PM
    )
    await callback.answer("Saved.")


@router.callback_query(F.data == "beta:curl_demotest", StateFilter(BetaStates.curlConfirm))
async def cbCurlDemoTest(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    data = await state.get_data()
    cfg  = data.get("pendingCurlCfg")
    if not cfg:
        await callback.answer("Session expired.", show_alert=True)
        return
    from bot.handlers.admin_apis import randomPhone
    from bot.services.tester_runner import testSingleApi
    phone   = randomPhone()
    waiting = await callback.message.edit_text(
        f"{b('Demo Test')}\n\nFiring with random number {c(phone)}...", parse_mode=PM
    )
    await callback.answer()
    result = await testSingleApi(cfg, phone)
    if not result["ok"]:
        builder = InlineKeyboardBuilder()
        builder.button(text="Back", callback_data="beta:menu")
        await waiting.edit_text(
            f"{b('Test Failed')}\n\nAPI    {esc(cfg['name'])}\nPhone  {c(phone)}\nError  {c(esc(result['error']))}",
            reply_markup=builder.as_markup(), parse_mode=PM
        )
        return
    status  = result["status"]
    latency = result["latencyMs"]
    snippet = esc((result.get("snippet") or "(empty)")[:120])
    if status == 429:   lbl = "RATE LIMITED"
    elif status < 300:  lbl = "OK"
    elif status < 500:  lbl = "CLIENT ERR"
    else:               lbl = "SERVER ERR"
    builder = InlineKeyboardBuilder()
    builder.button(text="Save to Bot", callback_data="beta:curl_save")
    builder.button(text="Beta Menu",   callback_data="beta:menu")
    builder.adjust(2)
    await waiting.edit_text(
        f"{b('Test Result')}\n\n"
        f"API      {esc(cfg['name'])}\nPhone    {c(phone)}\n"
        f"Status   {c(f'{lbl} {status}')}\nLatency  {c(f'{latency}ms')}\n\n"
        f"{i('Response')}\n{c(snippet)}\n\n{i('Looks good? Save it.')}",
        reply_markup=builder.as_markup(), parse_mode=PM
    )


# ---------------------------------------------------------------------------
# Admin AI Chat
# ---------------------------------------------------------------------------

ADMIN_AI_WELCOME = (
    f"{b('Admin AI Chat')}\n\n"
    f"Ask anything about:\n"
    f"  - API config format and placeholders\n"
    f"  - cURL parsing and conversion rules\n"
    f"  - Platform internals, rate limiting, proxy setup\n"
    f"  - HTTP request debugging\n\n"
    f"{b('Example questions')}\n"
    f"{c('How does the round-robin API queue work?')}\n"
    f"{c('What headers should I strip from a cURL?')}\n"
    f"{c('How do I add a SOCKS5 proxy?')}\n\n"
    f"{i('Type your question below.')}"
)


@router.callback_query(F.data == "beta:ai_chat")
async def cbAdminAiChat(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    await state.set_state(BetaStates.adminAiChat)
    await callback.message.edit_text(ADMIN_AI_WELCOME, reply_markup=adminAiKeyboard(), parse_mode=PM)
    await callback.answer()


@router.callback_query(F.data == "beta:ai_clear")
async def cbAdminAiClear(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    _adminChatHistory.pop(callback.from_user.id, None)
    await state.set_state(BetaStates.adminAiChat)
    await callback.message.edit_text(ADMIN_AI_WELCOME, reply_markup=adminAiKeyboard(), parse_mode=PM)
    await callback.answer("Chat cleared.")


@router.message(F.text, BetaStates.adminAiChat)
async def handleAdminAiMessage(message: Message, state: FSMContext) -> None:
    if not isAdmin(message.from_user.id):
        return
    userId  = message.from_user.id
    userMsg = (message.text or "").strip()
    if not userMsg:
        return
    history = _adminChatHistory.get(userId, [])
    parts   = [ADMIN_AI_SYSTEM, ""]
    for turn in history[-6:]:
        parts.append(f"User: {turn['user']}")
        parts.append(f"AI: {turn['bot']}")
    parts.append(f"User: {userMsg}")
    parts.append("AI:")
    prompt   = "\n".join(parts)
    thinking = await message.answer(f"{i('AI is thinking...')}", parse_mode=PM)
    reply    = await _callAi(prompt)
    if not reply:
        await thinking.edit_text(
            f"{b('AI Unavailable')}\n\nAll backends unreachable. Try again shortly.",
            reply_markup=adminAiKeyboard(), parse_mode=PM
        )
        return
    history.append({"user": userMsg, "bot": reply})
    _adminChatHistory[userId] = history[-10:]
    await thinking.edit_text(
        f"{b('AI')}\n\n{esc(reply)}\n\n{i('Reply to continue.')}",
        reply_markup=adminAiKeyboard(), parse_mode=PM
    )