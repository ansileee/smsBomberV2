from __future__ import annotations

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.keyboards.menus import mainMenuKeyboard, backToMainKeyboard, aiChatKeyboard
from bot.services.database import db
from bot.services.ai_service import askAi, buildUserPrompt
from bot.utils import PM, b, i, c, hEsc, isAdmin

router = Router()

# per-user AI chat history stored in memory (session only — intentional)
_chatHistory: dict = {}

AI_WELCOME = (
    f"{b('drazeX — OSINT and API Intelligence Bot')}\n\n"
    "I can help you with:\n"
    "  - How OTP and SMS APIs work\n"
    "  - Building and understanding API configs\n"
    "  - How the smsBomber platform works\n"
    "  - HTTP concepts: headers, status codes, rate limiting\n"
    "  - Indian telecom networks and number formats\n\n"
    f"{b('Example questions to try')}\n"
    f"{c('What is a rate limit and how does the bot handle it?')}\n"
    f"{c('How do I format a phone number with country code in a JSON body?')}\n"
    f"{c('What does a confirmed OTP mean in the dashboard?')}\n"
    f"{c('Explain what workers do in the test engine')}\n"
    f"{c('What is the difference between json and data in an API config?')}\n\n"
    f"{i('Type your question below. Type anything to start.')}"
)

HELP_TEXT = (
    f"{b('Help')}\n\n"
    f"{b('Start Test')}\n"
    "Pick a target number, set duration, set workers, then launch.\n\n"
    f"{b('Settings')}\n"
    "Set default workers and proxy preference.\n\n"
    f"{b('Dashboard')}\n"
    "Updates live every 2s. Shows confirmed OTPs, 2xx responses, errors, and per-API breakdown.\n\n"
    f"{b('AI Chat')}\n"
    "Ask drazeX anything about OTP APIs and how this platform works.\n\n"
    f"{b('Confirmed OTPs')} = 2xx response and body contains success keywords.\n"
    f"{b('2xx Total')} = all successful HTTP responses.\n\n"
    f"{i('Daily limit resets at midnight IST.')}"
)


def mainMenuText(userId: int) -> str:
    from bot.services.api_manager import apiManager
    total   = len(apiManager.getMergedConfigs())
    skipped = len(db.getSkippedApiNames())
    active  = total - skipped
    u       = db.getUser(userId)
    if u:
        _, testsToday, dailyLimit = db.canRunTest(userId)
        status = f"Tests today: {testsToday}/{dailyLimit}"
        banned = "  [BANNED]" if u["isBanned"] else ""
    else:
        testsToday, dailyLimit = 0, 0
        status = "New user"
        banned = ""
    skipStr = f"  {c(str(skipped))} skipped" if skipped else ""
    return (
        f"{b('smsBomber')}\n\n"
        f"APIs loaded   {c(str(active))} active{skipStr}\n"
        f"{c(status)}{banned}\n\n"
        f"{i('by @drazeforce')}"
    )


@router.message(CommandStart())
@router.message(Command("menu"))
async def cmdStart(message: Message, state: FSMContext) -> None:
    await state.clear()
    userId = message.from_user.id
    u = db.getUser(userId)
    if u and u["isBanned"]:
        await message.answer("Your account has been restricted.")
        return
    _, testsToday, dailyLimit = db.canRunTest(userId) if u else (False, 0, 0)
    await message.answer(
        mainMenuText(userId),
        reply_markup=mainMenuKeyboard(testsToday, dailyLimit),
        parse_mode=PM
    )


@router.callback_query(F.data == "nav:main_menu")
async def cbMainMenu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    userId = callback.from_user.id
    u = db.getUser(userId)
    _, testsToday, dailyLimit = db.canRunTest(userId) if u else (False, 0, 0)
    await callback.message.edit_text(
        mainMenuText(userId),
        reply_markup=mainMenuKeyboard(testsToday, dailyLimit),
        parse_mode=PM
    )
    await callback.answer()


@router.callback_query(F.data == "menu:help")
async def cbHelp(callback: CallbackQuery) -> None:
    userId = callback.from_user.id
    u      = db.getUser(userId)
    _, testsToday, dailyLimit = db.canRunTest(userId) if u else (False, 0, 0)
    text   = HELP_TEXT + f"\n\n{c(f'Your usage: {testsToday}/{dailyLimit} today')}"
    await callback.message.edit_text(text, reply_markup=backToMainKeyboard(), parse_mode=PM)
    await callback.answer()


# ---------------------------------------------------------------------------
# AI Chat
# ---------------------------------------------------------------------------

class AiChat(StatesGroup):
    chatting = State()


@router.callback_query(F.data == "menu:ai_chat")
async def cbAiChat(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AiChat.chatting)
    await callback.message.edit_text(AI_WELCOME, reply_markup=aiChatKeyboard(), parse_mode=PM)
    await callback.answer()


@router.callback_query(F.data == "ai:clear")
async def cbAiClear(callback: CallbackQuery, state: FSMContext) -> None:
    userId = callback.from_user.id
    _chatHistory.pop(userId, None)
    await state.set_state(AiChat.chatting)
    await callback.message.edit_text(AI_WELCOME, reply_markup=aiChatKeyboard(), parse_mode=PM)
    await callback.answer("Chat cleared.")


@router.message(F.text, AiChat.chatting)
async def handleAiMessage(message: Message, state: FSMContext) -> None:
    userId  = message.from_user.id
    userMsg = (message.text or "").strip()
    if not userMsg:
        return

    history = _chatHistory.get(userId, [])
    prompt  = buildUserPrompt(history, userMsg)

    thinking = await message.answer(f"{i('drazeX is thinking...')}", parse_mode=PM)
    reply    = await askAi(prompt)

    if not reply:
        await thinking.edit_text(
            f"{b('drazeX AI — Temporarily Unavailable')}\n\n"
            f"Our AI systems are currently under maintenance.\n"
            f"Please try again in a few minutes.\n\n"
            f"{i('For urgent help, contact @drazeforce.')}",
            reply_markup=aiChatKeyboard(),
            parse_mode=PM
        )
        return

    # update history
    history.append({"user": userMsg, "bot": reply})
    _chatHistory[userId] = history[-10:]  # keep last 10 turns

    await thinking.edit_text(
        f"{b('drazeX')}\n\n{hEsc(reply)}\n\n{i('Reply to continue the conversation.')}",
        reply_markup=aiChatKeyboard(),
        parse_mode=PM
    )