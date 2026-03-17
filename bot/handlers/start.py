from __future__ import annotations

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery

from bot.keyboards.menus import mainMenuKeyboard, backToMainKeyboard
from bot.services.database import db
from bot.utils import PM, b, i, c, hEsc, isAdmin

router = Router()

HELP_TEXT = (
    f"{b('Help')}\n\n"
    f"{b('Start Test')}\n"
    "Pick a target number, set duration, set workers, then launch.\n\n"
    f"{b('Settings')}\n"
    "Set default workers and proxy preference.\n\n"
    f"{b('Dashboard')}\n"
    "Updates live every 2s. Shows confirmed OTPs, 2xx responses, errors, and per-API breakdown.\n\n"
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