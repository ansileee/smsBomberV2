from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional, List

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import ADMIN_ID
from bot.services.database import db, IST
from bot.utils import PM, b, i, c, hEsc as esc, isAdmin

router = Router()

USERS_PER_PAGE     = 8
BLACKLIST_PER_PAGE = 10


class AdminStates(StatesGroup):
    waitingSetLimit        = State()
    waitingBroadcastMsg    = State()  # collecting messages until "end"
    waitingGlobalLimit     = State()
    waitingBlacklistPhone  = State()
    waitingBlacklistReason = State()
    waitingBulkBlacklist   = State()
    waitingBanConfirm      = State()
    waitingDmMessage       = State()


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------

def adminMenuKeyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Users",         callback_data="adm:users:0")
    builder.button(text="Stats",         callback_data="adm:stats")
    builder.button(text="API Manager",   callback_data="aapi:menu")
    builder.button(text="Proxy Manager", callback_data="aprx:menu")
    builder.button(text="Beta Tester",   callback_data="beta:menu")
    builder.button(text="Reset All",     callback_data="adm:reset_all")
    builder.button(text="Global Limit",  callback_data="adm:global_limit")
    builder.button(text="Broadcast",     callback_data="adm:broadcast")
    builder.button(text="Blacklist",     callback_data="adm:blacklist:0")
    builder.adjust(2, 2, 1, 2, 2)
    return builder.as_markup()


def usersListKeyboard(page: int, totalPages: int, users: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for u in users:
        name   = u["firstName"] or "Unknown"
        total  = u.get("testsTotal", u["testsToday"])
        status = "BANNED" if u["isBanned"] else f"{total} tests"
        builder.button(text=f"{name}  -  {status}", callback_data=f"adm:user:{u['userId']}")
    if page > 0:
        builder.button(text="Prev", callback_data=f"adm:users:{page - 1}")
    if page < totalPages - 1:
        builder.button(text="Next", callback_data=f"adm:users:{page + 1}")
    builder.button(text="Back", callback_data="adm:menu")
    builder.adjust(1)
    return builder.as_markup()


def userActionKeyboard(userId: int, isBanned: bool) -> InlineKeyboardMarkup:
    builder  = InlineKeyboardBuilder()
    banLabel = "Unban" if isBanned else "Ban"
    builder.button(text=banLabel,      callback_data=f"adm:ban_prompt:{userId}")
    builder.button(text="Set Limit",   callback_data=f"adm:set_limit:{userId}")
    builder.button(text="Reset Today", callback_data=f"adm:reset_user:{userId}")
    builder.button(text="History",     callback_data=f"adm:history:{userId}")
    builder.button(text="Send DM",     callback_data=f"adm:dm:{userId}")
    builder.button(text="Back",        callback_data="adm:users:0")
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup()


def confirmBanKeyboard(userId: int, isBanned: bool) -> InlineKeyboardMarkup:
    action = "unban" if isBanned else "ban"
    builder = InlineKeyboardBuilder()
    builder.button(text=f"Yes, {action}", callback_data=f"adm:toggle_ban:{userId}")
    builder.button(text="Cancel",          callback_data=f"adm:user:{userId}")
    builder.adjust(2)
    return builder.as_markup()


def confirmResetAllKeyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Yes, reset all", callback_data="adm:confirm_reset_all")
    builder.button(text="Cancel",         callback_data="adm:menu")
    builder.adjust(2)
    return builder.as_markup()


def backToAdminKeyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Admin Menu", callback_data="adm:menu")
    builder.adjust(1)
    return builder.as_markup()


def blacklistKeyboard(page: int, totalPages: int, entries: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    start   = page * BLACKLIST_PER_PAGE
    for e in entries[start:start + BLACKLIST_PER_PAGE]:
        builder.button(text=f"Remove: {e['phone']}", callback_data=f"adm:bl_remove:{e['phone']}")
    builder.button(text="Add Number",   callback_data="adm:bl_add")
    builder.button(text="Bulk Add",     callback_data="adm:bl_bulk")
    if page > 0:
        builder.button(text="Prev", callback_data=f"adm:blacklist:{page - 1}")
    if page < totalPages - 1:
        builder.button(text="Next", callback_data=f"adm:blacklist:{page + 1}")
    builder.button(text="Back", callback_data="adm:menu")
    builder.adjust(1)
    return builder.as_markup()


def formatUserDetail(u: dict) -> str:
    name = u["firstName"] or "Unknown"
    if u.get("lastName"):
        name += f" {u['lastName']}"
    username  = f"@{u['username']}" if u.get("username") else "no username"
    status    = "BANNED" if u["isBanned"] else "Active"
    joined    = datetime.fromtimestamp(u["joinedAt"], tz=IST).strftime("%d %b %Y")
    todayStr  = f"{u['testsToday']} / {u['dailyLimit']} today"
    total     = u.get("testsTotal", u["testsToday"])
    return (
        f"{b(esc(name))}  {c(esc(username))}\n\n"
        f"ID        {c(str(u['userId']))}\n"
        f"Status    {c(status)}\n"
        f"Today     {c(todayStr)}\n"
        f"All time  {c(str(total))} tests\n"
        f"Joined    {c(joined)}"
    )


# ---------------------------------------------------------------------------
# /admin
# ---------------------------------------------------------------------------

@router.message(Command("admin"))
async def cmdAdmin(message: Message, state: FSMContext) -> None:
    if not isAdmin(message.from_user.id):
        await message.answer("Unknown command.")
        return
    await state.clear()
    from bot.services.api_manager import apiManager
    total   = len(apiManager.getMergedConfigs())
    skipped = len(db.getSkippedApiNames())
    await message.answer(
        f"{b('Admin Panel')}\n\n"
        f"APIs     {c(str(total - skipped))} active  {c(str(skipped))} skipped\n"
        f"Users    {c(str(db.getUserCount()))}",
        reply_markup=adminMenuKeyboard(),
        parse_mode=PM
    )


@router.callback_query(F.data == "adm:menu")
async def cbAdminMenu(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    await state.clear()
    from bot.services.api_manager import apiManager
    total   = len(apiManager.getMergedConfigs())
    skipped = len(db.getSkippedApiNames())
    await callback.message.edit_text(
        f"{b('Admin Panel')}\n\n"
        f"APIs     {c(str(total - skipped))} active  {c(str(skipped))} skipped\n"
        f"Users    {c(str(db.getUserCount()))}",
        reply_markup=adminMenuKeyboard(),
        parse_mode=PM
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "adm:stats")
async def cbAdminStats(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    users           = db.getAllUsers(offset=0, limit=9999)
    banned          = sum(1 for u in users if u["isBanned"])
    activeToday     = sum(1 for u in users if u["testsToday"] > 0)
    totalTestsToday = sum(u["testsToday"] for u in users)
    totalTestsEver  = sum(u.get("testsTotal", 0) for u in users)
    from bot.services.api_manager import apiManager
    apiTotal = len(apiManager.getMergedConfigs())
    skipped  = len(db.getSkippedApiNames())
    await callback.message.edit_text(
        f"{b('Bot Stats')}\n\n"
        f"Users total    {c(str(len(users)))}\n"
        f"Banned         {c(str(banned))}\n"
        f"Active today   {c(str(activeToday))}\n"
        f"Tests today    {c(str(totalTestsToday))}\n"
        f"Tests all time {c(str(totalTestsEver))}\n\n"
        f"APIs active    {c(str(apiTotal - skipped))}\n"
        f"APIs skipped   {c(str(skipped))}",
        reply_markup=backToAdminKeyboard(),
        parse_mode=PM
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Users list
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("adm:users:"))
async def cbUsersList(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    page       = int(callback.data.split(":")[2])
    total      = db.getUserCount()
    totalPages = max(1, -(-total // USERS_PER_PAGE))
    users      = db.getAllUsers(offset=page * USERS_PER_PAGE, limit=USERS_PER_PAGE)
    if not users:
        await callback.message.edit_text("No users registered yet.", reply_markup=backToAdminKeyboard())
        await callback.answer()
        return
    await callback.message.edit_text(
        f"{b('Users')}  {c(f'{total} total  page {page+1}/{totalPages}')}\n\n{i('Tap a user to manage them.')}",
        reply_markup=usersListKeyboard(page, totalPages, users),
        parse_mode=PM
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:user:"))
async def cbUserDetail(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    userId = int(callback.data.split(":")[2])
    u      = db.getUser(userId)
    if not u:
        await callback.answer("User not found.", show_alert=True)
        return
    await callback.message.edit_text(
        formatUserDetail(u),
        reply_markup=userActionKeyboard(userId, bool(u["isBanned"])),
        parse_mode=PM
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Ban with confirmation
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("adm:ban_prompt:"))
async def cbBanPrompt(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    userId = int(callback.data.split(":")[2])
    u      = db.getUser(userId)
    if not u:
        await callback.answer("User not found.", show_alert=True)
        return
    action = "unban" if u["isBanned"] else "ban"
    name   = esc(u["firstName"] or str(userId))
    await callback.message.edit_text(
        f"{b('Confirm')}\n\nAre you sure you want to {action} {name}?",
        reply_markup=confirmBanKeyboard(userId, bool(u["isBanned"])),
        parse_mode=PM
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:toggle_ban:"))
async def cbToggleBan(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    userId = int(callback.data.split(":")[2])
    u      = db.getUser(userId)
    if not u:
        await callback.answer("User not found.", show_alert=True)
        return
    if u["isBanned"]:
        db.unbanUser(userId)
        await callback.answer("User unbanned.")
    else:
        db.banUser(userId)
        await callback.answer("User banned.")
    u = db.getUser(userId)
    await callback.message.edit_text(
        formatUserDetail(u),
        reply_markup=userActionKeyboard(userId, bool(u["isBanned"])),
        parse_mode=PM
    )


# ---------------------------------------------------------------------------
# Set limit
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("adm:set_limit:"))
async def cbSetLimit(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    userId = int(callback.data.split(":")[2])
    await state.set_state(AdminStates.waitingSetLimit)
    await state.update_data(targetUserId=userId)
    await callback.message.edit_text(
        f"{b('Set Daily Limit')}\n\nEnter new limit for user {c(str(userId))}.\nNumber between {c('0')} and {c('999')}.",
        parse_mode=PM
    )
    await callback.answer()


@router.message(AdminStates.waitingSetLimit)
async def handleSetLimit(message: Message, state: FSMContext) -> None:
    if not isAdmin(message.from_user.id):
        return
    try:
        limit = int((message.text or "").strip())
        if not 0 <= limit <= 999:
            raise ValueError
    except ValueError:
        await message.answer("Enter a number between 0 and 999.")
        return
    data         = await state.get_data()
    targetUserId = data["targetUserId"]
    db.setDailyLimit(targetUserId, limit)
    await state.clear()
    u = db.getUser(targetUserId)
    await message.answer(
        formatUserDetail(u),
        reply_markup=userActionKeyboard(targetUserId, bool(u["isBanned"])),
        parse_mode=PM
    )


# ---------------------------------------------------------------------------
# Reset user
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("adm:reset_user:"))
async def cbResetUser(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    userId = int(callback.data.split(":")[2])
    db.resetUserTests(userId)
    await callback.answer("Daily count reset.")
    u = db.getUser(userId)
    await callback.message.edit_text(
        formatUserDetail(u),
        reply_markup=userActionKeyboard(userId, bool(u["isBanned"])),
        parse_mode=PM
    )


# ---------------------------------------------------------------------------
# Reset all
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "adm:reset_all")
async def cbResetAll(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    await callback.message.edit_text(
        f"{b('Reset All Limits')}\n\nThis will reset today's test count for every user. Are you sure?",
        reply_markup=confirmResetAllKeyboard(),
        parse_mode=PM
    )
    await callback.answer()


@router.callback_query(F.data == "adm:confirm_reset_all")
async def cbConfirmResetAll(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    db.resetAllTests()
    await callback.message.edit_text("Done. All daily counts reset.", reply_markup=backToAdminKeyboard())
    await callback.answer("All limits reset.")


# ---------------------------------------------------------------------------
# Global limit
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "adm:global_limit")
async def cbGlobalLimit(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    await state.set_state(AdminStates.waitingGlobalLimit)
    await callback.message.edit_text(
        f"{b('Set Global Daily Limit')}\n\nEnter a number to update the daily limit for every user.",
        parse_mode=PM
    )
    await callback.answer()


@router.message(AdminStates.waitingGlobalLimit)
async def handleGlobalLimit(message: Message, state: FSMContext) -> None:
    if not isAdmin(message.from_user.id):
        return
    try:
        limit = int((message.text or "").strip())
        if not 0 <= limit <= 999:
            raise ValueError
    except ValueError:
        await message.answer("Enter a number between 0 and 999.")
        return
    db.setGlobalDailyLimit(limit)
    await state.clear()
    await message.answer(
        f"Global limit updated. All users now have a daily limit of {c(str(limit))}.",
        reply_markup=backToAdminKeyboard(),
        parse_mode=PM
    )


# ---------------------------------------------------------------------------
# User history
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("adm:history:"))
async def cbUserHistory(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    userId  = int(callback.data.split(":")[2])
    history = db.getUserHistory(userId, limit=10)
    if not history:
        await callback.answer("No test history for this user.", show_alert=True)
        return
    from bot.utils import formatDuration
    lines = [f"{b('Test History')}  {c(f'last {len(history)}')}\n"]
    for h in history:
        dt = datetime.fromtimestamp(h["startedAt"], tz=IST).strftime("%d %b %H:%M")
        lines.append(
            f"{c(dt)}  {h['phone']}  {formatDuration(h['duration'])}  "
            f"OTP {h['otpHits']}  REQ {h['totalReqs']}"
        )
    builder = InlineKeyboardBuilder()
    builder.button(text="Back", callback_data=f"adm:user:{userId}")
    await callback.message.edit_text(
        "\n".join(lines), reply_markup=builder.as_markup(), parse_mode=PM
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Send DM to user
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("adm:dm:"))
async def cbDmUser(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    userId = int(callback.data.split(":")[2])
    u      = db.getUser(userId)
    if not u:
        await callback.answer("User not found.", show_alert=True)
        return
    await state.set_state(AdminStates.waitingDmMessage)
    await state.update_data(dmTargetUserId=userId)
    builder = InlineKeyboardBuilder()
    builder.button(text="Cancel", callback_data=f"adm:user:{userId}")
    name = esc(u["firstName"] or str(userId))
    await callback.message.edit_text(
        f"{b('Send DM')}\n\nType the message to send to {name}.\nSupports HTML formatting.",
        reply_markup=builder.as_markup(),
        parse_mode=PM
    )
    await callback.answer()


@router.message(AdminStates.waitingDmMessage)
async def handleDmMessage(message: Message, state: FSMContext) -> None:
    if not isAdmin(message.from_user.id):
        return
    data         = await state.get_data()
    targetUserId = data.get("dmTargetUserId")
    await state.clear()
    if not targetUserId:
        return
    try:
        await message.bot.send_message(targetUserId, message.text or "", parse_mode=PM)
        u = db.getUser(targetUserId)
        await message.answer(
            f"Message sent to {esc(u['firstName'] if u else str(targetUserId))}.",
            reply_markup=userActionKeyboard(targetUserId, bool(u["isBanned"]) if u else False),
            parse_mode=PM
        )
    except Exception as ex:
        await message.answer(
            f"Failed to deliver message.\n{c(str(ex)[:80])}",
            reply_markup=backToAdminKeyboard(),
            parse_mode=PM
        )


# ---------------------------------------------------------------------------
# Broadcast — collect any media/text until user sends "end"
# ---------------------------------------------------------------------------

# In-memory broadcast queue per admin session
_broadcastQueue: dict = {}  # adminId -> List[Message]


@router.callback_query(F.data == "adm:broadcast")
async def cbBroadcast(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    adminId = callback.from_user.id
    _broadcastQueue[adminId] = []
    await state.set_state(AdminStates.waitingBroadcastMsg)
    builder = InlineKeyboardBuilder()
    builder.button(text="Cancel", callback_data="adm:broadcast_cancel")
    await callback.message.edit_text(
        f"{b('Broadcast Message')}\n\n"
        f"Send any content you want to broadcast:\n"
        f"  text, images, videos, documents, stickers, voice, etc.\n\n"
        f"You can send {b('multiple messages')}. Each one will be forwarded separately.\n\n"
        f"When done, send the word  {c('end')}  to confirm and broadcast.",
        reply_markup=builder.as_markup(),
        parse_mode=PM
    )
    await callback.answer()


@router.callback_query(F.data == "adm:broadcast_cancel")
async def cbBroadcastCancel(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    _broadcastQueue.pop(callback.from_user.id, None)
    await state.clear()
    await cbAdminMenu(callback, state)


@router.message(AdminStates.waitingBroadcastMsg)
async def handleBroadcastMsg(message: Message, state: FSMContext) -> None:
    if not isAdmin(message.from_user.id):
        return
    adminId = message.from_user.id

    # Check if user typed "end" (text message only)
    if message.text and message.text.strip().lower() == "end":
        queue = _broadcastQueue.pop(adminId, [])
        if not queue:
            await message.answer("Nothing to broadcast. Cancelled.")
            await state.clear()
            return
        await state.clear()
        users   = db.getAllUsers(offset=0, limit=99999)
        total   = len(users)
        sent    = 0
        failed  = 0
        status  = await message.answer(
            f"Broadcasting {len(queue)} message(s) to {total} users..."
        )
        for u in users:
            try:
                for msg in queue:
                    await msg.copy_to(u["userId"])
                sent += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05)  # avoid flood limits
        await status.edit_text(
            f"{b('Broadcast Complete')}\n\n"
            f"Messages  {c(str(len(queue)))}\n"
            f"Sent to   {c(str(sent))} users\n"
            f"Failed    {c(str(failed))}",
            reply_markup=backToAdminKeyboard(),
            parse_mode=PM
        )
        return

    # Collect message into queue
    if adminId not in _broadcastQueue:
        _broadcastQueue[adminId] = []
    _broadcastQueue[adminId].append(message)
    count = len(_broadcastQueue[adminId])
    await message.answer(
        f"{c(str(count))} message(s) queued. Send more or type {c('end')} to broadcast."
    )


# ---------------------------------------------------------------------------
# Phone Blacklist
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("adm:blacklist:"))
async def cbBlacklist(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    await state.clear()
    page       = int(callback.data.split(":")[2])
    entries    = db.getAllBlacklisted()
    totalPages = max(1, -(-len(entries) // BLACKLIST_PER_PAGE))
    if not entries:
        builder = InlineKeyboardBuilder()
        builder.button(text="Add Number", callback_data="adm:bl_add")
        builder.button(text="Bulk Add",   callback_data="adm:bl_bulk")
        builder.button(text="Back",       callback_data="adm:menu")
        builder.adjust(2, 1)
        await callback.message.edit_text(
            f"{b('Phone Blacklist')}\n\nNo numbers blacklisted yet.",
            reply_markup=builder.as_markup(),
            parse_mode=PM
        )
        await callback.answer()
        return
    lines = [f"{b('Phone Blacklist')}  {c(str(len(entries)) + ' numbers')}\n"]
    start = page * BLACKLIST_PER_PAGE
    for e in entries[start:start + BLACKLIST_PER_PAGE]:
        reason = f"  - {e['reason']}" if e.get("reason") else ""
        dt     = datetime.fromtimestamp(e["addedAt"], tz=IST).strftime("%d %b %Y")
        lines.append(f"{e['phone']}{reason}  ({dt})")
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=blacklistKeyboard(page, totalPages, entries),
        parse_mode=PM
    )
    await callback.answer()


@router.callback_query(F.data == "adm:bl_add")
async def cbBlAdd(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    await state.set_state(AdminStates.waitingBlacklistPhone)
    await callback.message.edit_text(
        f"{b('Add to Blacklist')}\n\nEnter the 10-digit phone number to permanently block.",
        parse_mode=PM
    )
    await callback.answer()


@router.message(AdminStates.waitingBlacklistPhone)
async def handleBlPhone(message: Message, state: FSMContext) -> None:
    if not isAdmin(message.from_user.id):
        return
    phone = (message.text or "").strip()
    if not phone.isdigit() or len(phone) != 10:
        await message.answer("Enter exactly 10 digits.")
        return
    await state.update_data(blPhone=phone)
    await state.set_state(AdminStates.waitingBlacklistReason)
    await message.answer(
        f"Number: {c(phone)}\n\nEnter a reason (optional) or send  -  to skip.",
        parse_mode=PM
    )


@router.message(AdminStates.waitingBlacklistReason)
async def handleBlReason(message: Message, state: FSMContext) -> None:
    if not isAdmin(message.from_user.id):
        return
    reason = (message.text or "").strip()
    if reason == "-":
        reason = ""
    data  = await state.get_data()
    phone = data["blPhone"]
    db.blacklistPhone(phone, reason)
    await state.clear()
    builder = InlineKeyboardBuilder()
    builder.button(text="View Blacklist", callback_data="adm:blacklist:0")
    builder.button(text="Admin Menu",     callback_data="adm:menu")
    builder.adjust(1)
    reasonNote = f"\nReason: {reason}" if reason else ""
    await message.answer(
        f"Blacklisted.\n\n{c(phone)} permanently blocked.{reasonNote}",
        reply_markup=builder.as_markup(),
        parse_mode=PM
    )


@router.callback_query(F.data == "adm:bl_bulk")
async def cbBlBulk(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    await state.set_state(AdminStates.waitingBulkBlacklist)
    builder = InlineKeyboardBuilder()
    builder.button(text="Cancel", callback_data="adm:blacklist:0")
    await callback.message.edit_text(
        f"{b('Bulk Blacklist')}\n\n"
        f"Paste multiple 10-digit numbers, one per line.\n"
        f"Optional: add a reason after a space on each line.\n\n"
        f"Example:\n{c('9876543210 spam')}\n{c('9123456789')}\n{c('9000000001 test account')}",
        reply_markup=builder.as_markup(),
        parse_mode=PM
    )
    await callback.answer()


@router.message(AdminStates.waitingBulkBlacklist)
async def handleBulkBlacklist(message: Message, state: FSMContext) -> None:
    if not isAdmin(message.from_user.id):
        return
    lines  = (message.text or "").strip().splitlines()
    added  = 0
    failed = []
    for line in lines:
        parts  = line.strip().split(None, 1)
        phone  = parts[0] if parts else ""
        reason = parts[1] if len(parts) > 1 else ""
        if phone.isdigit() and len(phone) == 10:
            db.blacklistPhone(phone, reason)
            added += 1
        elif phone:
            failed.append(phone[:15])
    await state.clear()
    failNote = f"\nSkipped {len(failed)}: {', '.join(failed[:5])}" if failed else ""
    builder  = InlineKeyboardBuilder()
    builder.button(text="View Blacklist", callback_data="adm:blacklist:0")
    builder.button(text="Admin Menu",     callback_data="adm:menu")
    builder.adjust(1)
    await message.answer(
        f"Bulk blacklist done.\n\nAdded  {c(str(added))} numbers.{failNote}",
        reply_markup=builder.as_markup(),
        parse_mode=PM
    )


@router.callback_query(F.data.startswith("adm:bl_remove:"))
async def cbBlRemove(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    phone = callback.data.split(":", 2)[2]
    db.unblacklistPhone(phone)
    await callback.answer(f"Removed: {phone}")
    entries    = db.getAllBlacklisted()
    totalPages = max(1, -(-len(entries) // BLACKLIST_PER_PAGE))
    if not entries:
        builder = InlineKeyboardBuilder()
        builder.button(text="Add Number", callback_data="adm:bl_add")
        builder.button(text="Bulk Add",   callback_data="adm:bl_bulk")
        builder.button(text="Back",       callback_data="adm:menu")
        builder.adjust(2, 1)
        await callback.message.edit_text(
            f"{b('Phone Blacklist')}\n\nBlacklist is now empty.",
            reply_markup=builder.as_markup(),
            parse_mode=PM
        )
        return
    lines = [f"{b('Phone Blacklist')}  {c(str(len(entries)) + ' numbers')}\n"]
    for e in entries[:BLACKLIST_PER_PAGE]:
        reason = f"  - {e['reason']}" if e.get("reason") else ""
        dt     = datetime.fromtimestamp(e["addedAt"], tz=IST).strftime("%d %b %Y")
        lines.append(f"{e['phone']}{reason}  ({dt})")
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=blacklistKeyboard(0, totalPages, entries),
        parse_mode=PM
    )