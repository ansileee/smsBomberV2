"""
Beta Tester panel — admin-only.
  1. cURL to API converter (pure Python parser)
  2. Admin AI Chat (Google Gemini)
"""
from __future__ import annotations

import json

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.services.curl_parser import parseCurl
from bot.services.ai_service import askGemini, buildAdminPrompt, ADMIN_AI_SYSTEM
from bot.services.api_manager import apiManager
from bot.utils import PM, b, i, c, hEsc as esc, isAdmin

router = Router()

_adminChatHistory: dict = {}


class BetaStates(StatesGroup):
    waitingCurl = State()
    curlConfirm = State()
    adminAiChat = State()


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------

def betaMenuKeyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="cURL to API",   callback_data="beta:curl")
    builder.button(text="Admin AI Chat", callback_data="beta:ai_chat")
    builder.button(text="Back",          callback_data="adm:menu")
    builder.adjust(2, 1)
    return builder.as_markup()


def curlResultKeyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Save to Bot", callback_data="beta:curl_save")
    builder.button(text="Demo Test",   callback_data="beta:curl_demotest")
    builder.button(text="Edit JSON",   callback_data="beta:curl_edit")
    builder.button(text="Try Another", callback_data="beta:curl")
    builder.adjust(2, 2)
    return builder.as_markup()


def curlFailKeyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Try Again", callback_data="beta:curl")
    builder.button(text="Beta Menu", callback_data="beta:menu")
    builder.adjust(2)
    return builder.as_markup()


def adminAiKeyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Clear Chat", callback_data="beta:ai_clear")
    builder.button(text="Beta Menu",  callback_data="beta:menu")
    builder.adjust(2)
    return builder.as_markup()


ADMIN_AI_WELCOME = (
    f"{b('Admin AI Chat')}  {i('powered by Gemini')}\n\n"
    f"Ask anything about:\n"
    f"  - API config format and placeholders\n"
    f"  - cURL parsing and conversion rules\n"
    f"  - Platform internals, rate limiting, proxy setup\n"
    f"  - HTTP request debugging\n\n"
    f"{b('Example questions')}\n"
    f"{c('How does the round-robin API queue work?')}\n"
    f"{c('What headers should I strip from a cURL?')}\n"
    f"{c('How do I add a SOCKS5 proxy?')}\n"
    f"{c('Why is an API returning 429?')}\n\n"
    f"{i('Type your question below.')}"
)


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

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
        f"Paste any raw cURL — converted instantly to our JSON config format.\n"
        f"Handles JSON, form data, query params, cookies, Windows CMD format.\n\n"
        f"{b('Admin AI Chat')}\n"
        f"Gemini-powered assistant for API configs, cURL help, platform internals.",
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
    await state.update_data(pendingCurlCfg=None, editMode=False)
    builder = InlineKeyboardBuilder()
    builder.button(text="Cancel", callback_data="beta:menu")
    await callback.message.edit_text(
        f"{b('cURL to API Converter')}\n\n"
        f"Paste a raw cURL command.\n\n"
        f"Supported formats:\n"
        f"  - Linux/Mac bash style\n"
        f"  - Windows CMD style (with {c('^')} escapes)\n\n"
        f"Handles: JSON body, form data, query params, cookies.\n"
        f"Phone formats detected: {c('{phone}')}, {c('91{phone}')}, {c('+91{phone}')}\n\n"
        f"{i('Paste the cURL command now.')}",
        reply_markup=builder.as_markup(),
        parse_mode=PM
    )
    await callback.answer()


@router.message(StateFilter(BetaStates.waitingCurl))
async def handleCurl(message: Message, state: FSMContext) -> None:
    if not isAdmin(message.from_user.id):
        return
    text     = (message.text or "").strip()
    data     = await state.get_data()
    editMode = data.get("editMode", False)

    # If edit mode — accept raw JSON directly
    if editMode:
        ok, cfg, error = apiManager.validateApiJson(text)
        if not ok:
            await message.answer(
                f"{b('Invalid JSON')}\n\n{c(error)}\n\nFix and paste again.",
                parse_mode=PM
            )
            return
        await state.update_data(pendingCurlCfg=cfg, pendingCurlJson=json.dumps(cfg), editMode=False)
        await state.set_state(BetaStates.curlConfirm)
        await _showCurlResult(message, cfg, is_edit=False)
        return

    # Normal mode — expect cURL
    if not text.lower().startswith("curl"):
        await message.answer(
            f"{b('Invalid')}\n\nInput must start with {c('curl')}.\nPaste the cURL command directly.",
            parse_mode=PM
        )
        return

    ok, cfg, error = parseCurl(text)

    if not ok:
        await message.answer(
            f"{b('Parse Failed')}\n\n{c(error)}\n\n"
            f"Make sure you paste the full cURL including the URL.\n"
            f"You can also add configs manually via {b('API Manager')} → Add API.",
            reply_markup=curlFailKeyboard(),
            parse_mode=PM
        )
        return

    await state.update_data(pendingCurlCfg=cfg, pendingCurlJson=json.dumps(cfg), editMode=False)
    await state.set_state(BetaStates.curlConfirm)
    await _showCurlResult(message, cfg, is_edit=False)


async def _showCurlResult(msgOrCb, cfg: dict, is_edit: bool = True) -> None:
    lines = [f"{b('Converted Successfully')}\n"]
    lines.append(f"Name    {c(esc(cfg['name']))}")
    lines.append(f"Method  {c(cfg['method'])}")
    lines.append(f"URL     {c(esc(cfg['url'][:80]))}")
    if cfg.get("headers"):
        lines.append(f"Headers {c(str(len(cfg['headers'])))} fields")
    if cfg.get("json"):
        lines.append(f"Body    JSON  {c(str(len(cfg['json'])))} fields")
    elif cfg.get("data"):
        lines.append(f"Body    Form  {c(str(len(cfg['data'])))} fields")
    if cfg.get("params"):
        lines.append(f"Params  {c(str(len(cfg['params'])))} fields")
    if cfg.get("cookies"):
        lines.append(f"Cookies {c(str(len(cfg['cookies'])))} fields")
    lines.append(f"\n{b('JSON')}\n<pre>{esc(json.dumps(cfg, indent=2))}</pre>")
    lines.append(f"\n{i('Save, Demo Test, or Edit if needed.')}")
    text = "\n".join(lines)
    if is_edit:
        await msgOrCb.message.edit_text(text, reply_markup=curlResultKeyboard(), parse_mode=PM)
    else:
        await msgOrCb.answer(text, reply_markup=curlResultKeyboard(), parse_mode=PM)


@router.callback_query(F.data == "beta:curl_save", StateFilter(BetaStates.curlConfirm))
async def cbCurlSave(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    data = await state.get_data()
    cfg  = data.get("pendingCurlCfg")
    if not cfg:
        await callback.answer("Session expired.", show_alert=True)
        await state.clear()
        return
    from bot.services.database import db
    db.addCustomApi(name=cfg["name"], method=cfg["method"], url=cfg["url"], configJson=json.dumps(cfg))
    await state.clear()
    builder = InlineKeyboardBuilder()
    builder.button(text="API Manager",  callback_data="aapi:menu")
    builder.button(text="Convert More", callback_data="beta:curl")
    builder.button(text="Beta Menu",    callback_data="beta:menu")
    builder.adjust(2, 1)
    await callback.message.edit_text(
        f"{b('Saved.')}  {esc(cfg['name'])} added to the API list.",
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
        builder.button(text="Save Anyway", callback_data="beta:curl_save")
        builder.button(text="Edit JSON",   callback_data="beta:curl_edit")
        builder.button(text="Back",        callback_data="beta:curl_showresult")
        builder.adjust(2, 1)
        await waiting.edit_text(
            f"{b('Test Failed')}\n\n"
            f"API    {esc(cfg['name'])}\n"
            f"Phone  {c(phone)}\n"
            f"Error  {c(esc(result['error']))}\n\n"
            f"{i('API may need auth tokens or be rate-limited. You can still save it.')}",
            reply_markup=builder.as_markup(), parse_mode=PM
        )
        return
    status  = result["status"]
    latency = result["latencyMs"]
    snippet = esc((result.get("snippet") or "(empty)")[:200])
    if status == 429:   lbl = "RATE LIMITED"
    elif status < 300:  lbl = "OK"
    elif status < 500:  lbl = "CLIENT ERR"
    else:               lbl = "SERVER ERR"
    builder = InlineKeyboardBuilder()
    builder.button(text="Save to Bot",  callback_data="beta:curl_save")
    builder.button(text="Test Again",   callback_data="beta:curl_demotest")
    builder.button(text="Edit JSON",    callback_data="beta:curl_edit")
    builder.button(text="Beta Menu",    callback_data="beta:menu")
    builder.adjust(2, 2)
    await waiting.edit_text(
        f"{b('Test Result')}\n\n"
        f"API      {esc(cfg['name'])}\n"
        f"Phone    {c(phone)}\n"
        f"Status   {c(f'{lbl} {status}')}\n"
        f"Latency  {c(f'{latency}ms')}\n\n"
        f"{i('Response snippet')}\n{c(snippet)}",
        reply_markup=builder.as_markup(), parse_mode=PM
    )


@router.callback_query(F.data == "beta:curl_edit", StateFilter(BetaStates.curlConfirm))
async def cbCurlEdit(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    data = await state.get_data()
    cfg  = data.get("pendingCurlCfg")
    if not cfg:
        await callback.answer("Session expired.", show_alert=True)
        return
    await state.update_data(editMode=True)
    await state.set_state(BetaStates.waitingCurl)
    builder = InlineKeyboardBuilder()
    builder.button(text="Cancel", callback_data="beta:curl_showresult")
    await callback.message.edit_text(
        f"{b('Edit JSON')}\n\n"
        f"Current:\n<pre>{esc(json.dumps(cfg, indent=2))}</pre>\n\n"
        f"Paste the corrected JSON below.",
        reply_markup=builder.as_markup(), parse_mode=PM
    )
    await callback.answer()


@router.callback_query(F.data == "beta:curl_showresult")
async def cbCurlShowResult(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    data = await state.get_data()
    cfg  = data.get("pendingCurlCfg")
    if cfg:
        await state.set_state(BetaStates.curlConfirm)
        await _showCurlResult(callback, cfg, is_edit=True)
    else:
        await cbCurl(callback, state)
    await callback.answer()


# ---------------------------------------------------------------------------
# Admin AI Chat — Gemini
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "beta:ai_chat")
async def cbAdminAiChat(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    from bot.config import GEMINI_API_KEY
    if not GEMINI_API_KEY:
        await callback.answer("GEMINI_API_KEY not set in environment.", show_alert=True)
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

    history  = _adminChatHistory.get(userId, [])
    prompt   = buildAdminPrompt(history, userMsg)
    thinking = await message.answer(f"{i('Gemini is thinking...')}", parse_mode=PM)
    reply    = await askGemini(prompt, systemPrompt=ADMIN_AI_SYSTEM)

    if not reply:
        await thinking.edit_text(
            f"{b('AI Unavailable')}\n\n"
            f"Gemini could not be reached. Check that {c('GEMINI_API_KEY')} is set correctly in Railway.",
            reply_markup=adminAiKeyboard(),
            parse_mode=PM
        )
        return

    history.append({"user": userMsg, "bot": reply})
    _adminChatHistory[userId] = history[-12:]
    await thinking.edit_text(
        f"{b('Gemini')}\n\n{esc(reply)}\n\n{i('Reply to continue.')}",
        reply_markup=adminAiKeyboard(),
        parse_mode=PM
    )