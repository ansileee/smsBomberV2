from __future__ import annotations

import asyncio
from typing import Dict, Optional
import random

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.keyboards.menus import (
    durationKeyboard, workersKeyboard, proxyKeyboard,
    confirmKeyboard, runningKeyboard, finishedKeyboard, mainMenuKeyboard,
)
from bot.services.tester_runner import TesterRunner, validateProxies
from bot.services.proxy_manager import proxyManager
from bot.services.database import db
from bot.config import DASHBOARD_UPDATE_INTERVAL, ADMIN_ID, PROTECTED_NUMBER
from bot.utils import PM, b, i, c, hEsc, formatDuration

router = Router()

activeRunners:   Dict[int, TesterRunner] = {}
dashboardTasks:  Dict[int, asyncio.Task] = {}
summaryShown:    Dict[int, bool]         = {}
activeRecordIds: Dict[int, int]          = {}

PROTECTED_RESPONSES = [
    "Poda kunne onn!!!",
    "Onn poyeda vadhoori",
    "Ninta pari!",
    "Ntelekk ondaakaan varalletta myre, chethi kallayum panni ninta suna!!",
]


class TestWizard(StatesGroup):
    phone          = State()
    duration       = State()
    durationCustom = State()
    workers        = State()
    workersCustom  = State()
    proxy          = State()
    proxyChecking  = State()
    confirm        = State()
    running        = State()


def parseTime(t: str) -> Optional[int]:
    t = (t or "").strip()
    try:
        if t.endswith("s"): return int(t[:-1])
        if t.endswith("m"): return int(t[:-1]) * 60
        if t.endswith("h"): return int(t[:-1]) * 3600
        return int(t)
    except (ValueError, AttributeError):
        return None


def durationText() -> str:
    return (
        f"{b('Step 1 of 2 — Duration')}\n\n"
        f"How long should the test run?\n\n"
        f"{i('Choose below or enter a custom value.')}"
    )


def workersText(duration: int) -> str:
    return (
        f"{b('Step 2 of 2 — Workers')}\n\n"
        f"Duration   {c(formatDuration(duration))}\n\n"
        f"How many concurrent workers?\n"
        f"{i('More workers = more requests per second.')}"
    )


def buildConfirmText(data: dict, proxyInfo: str = "") -> str:
    proxyLabel = proxyInfo if proxyInfo else ("Proxy" if data.get("useProxy") else "Direct")
    return (
        f"{b('Ready to Launch')}\n\n"
        f"Phone      {c(data['phone'])}\n"
        f"Duration   {c(formatDuration(data['duration']))}\n"
        f"Workers    {c(str(data['workers']))}\n"
        f"Proxy      {c(proxyLabel)}\n\n"
        f"{i('Tap Launch to start or Edit to change settings.')}"
    )


def buildDashboardText(snap: dict, phone: str, duration: int) -> str:
    elapsed  = snap["elapsed"]
    remaining = max(0, duration - int(elapsed))
    pct      = min(elapsed / duration, 1.0) if duration > 0 else 0

    rlCount   = 0
    deadCount = 0
    lines     = []

    for name, s in snap["perApi"].items():
        if s.get("status") == "ratelimited":
            rlCount += 1
        elif s.get("status") == "dead":
            deadCount += 1
        elif s.get("requests", 0) > 0:
            conf = s.get("confirmed", 0)
            req  = s["requests"]
            ms   = s["avgMs"]
            lines.append(
                f"<code>{hEsc(name[:14]):<14}</code>  "
                f"{c(str(conf))} otp  {req}req  {ms}ms"
            )

    # Sort active APIs by confirmed OTPs descending
    lines = sorted(lines, reverse=True)

    MAX_SHOWN = 8
    apiBlock  = "\n".join(lines[:MAX_SHOWN]) if lines else i("Sending requests...")
    if len(lines) > MAX_SHOWN:
        apiBlock += f"\n{i(f'and {len(lines) - MAX_SHOWN} more active APIs')}"

    barTotal  = 20
    barFilled = int(barTotal * pct)
    bar       = "█" * barFilled + "░" * (barTotal - barFilled)

    totalReqs = snap.get("totalReqs", snap.get("total", 0))
    confirmed = snap.get("confirmed", snap.get("otpSent", 0))
    responses = snap.get("responses", 0)

    # ETA display
    if remaining > 0:
        etaStr = f"ETA: {formatDuration(remaining)}"
    else:
        etaStr = "finishing..."

    statusBits = []
    if rlCount:   statusBits.append(f"RL {rlCount}")
    if deadCount: statusBits.append(f"Dead {deadCount}")
    statusStr = "  [ " + "  ".join(statusBits) + " ]" if statusBits else ""

    return (
        f"{b('Test Running')}  {c(phone)}\n"
        f"<code>{bar}</code>  {c(f'{int(pct*100)}%')}  {i(etaStr)}\n\n"
        f"Elapsed     {c(formatDuration(int(elapsed)))}\n"
        f"Requests    {c(str(totalReqs))}  {i(str(snap['rps']) + ' r/s')}\n"
        f"Confirmed   {c(str(confirmed))}\n"
        f"2xx Total   {c(str(responses))}\n"
        f"Errors      {c(str(snap['errors']))}{statusStr}\n\n"
        f"{b('Active APIs')}\n{apiBlock}"
    )


def buildSummaryText(snap: dict, phone: str) -> str:
    sortedApis = sorted(
        snap["perApi"].items(),
        key=lambda x: (x[1].get("confirmed", 0), x[1].get("responses", 0)),
        reverse=True,
    )
    topLines = []
    for name, s in sortedApis[:8]:
        if s.get("requests", 0) > 0:
            topLines.append(
                f"<code>{hEsc(name[:14]):<14}</code>  "
                f"{c(str(s.get('confirmed',0)))} otp  "
                f"{s.get('responses',0)} ok  "
                f"{s['requests']} req"
            )
    topBlock  = "\n".join(topLines) if topLines else i("No responses recorded.")
    elapsed   = int(snap["elapsed"])
    totalReqs = snap.get("totalReqs", snap.get("total", 0))
    confirmed = snap.get("confirmed", snap.get("otpSent", 0))
    responses = snap.get("responses", 0)
    return (
        f"{b('Test Complete')}\n"
        f"{c(phone)}\n\n"
        f"Duration    {c(formatDuration(elapsed))}\n"
        f"Requests    {c(str(totalReqs))}\n"
        f"Confirmed   {c(str(confirmed))}\n"
        f"2xx Total   {c(str(responses))}\n"
        f"Errors      {c(str(snap['errors']))}\n"
        f"Req/sec     {c(str(snap['rps']))}\n\n"
        f"{b('Top APIs')}\n{topBlock}"
    )


async def dashboardLoop(runner, message, phone, duration, userId, state):
    lastText = ""
    while runner.isRunning:
        await asyncio.sleep(DASHBOARD_UPDATE_INTERVAL)
        snap    = runner.stats.snapshot()
        newText = buildDashboardText(snap, phone, duration)
        if newText != lastText:
            try:
                await message.edit_text(newText, reply_markup=runningKeyboard(), parse_mode=PM)
                lastText = newText
            except Exception:
                pass

    if not summaryShown.get(userId, False):
        summaryShown[userId] = True
        snap = runner.stats.snapshot()
        _saveHistory(userId, snap)
        db.saveLastConfig(userId, runner.phone, runner.duration, runner.workers)
        summaryText = buildSummaryText(snap, phone)
        try:
            await message.edit_text(summaryText, reply_markup=finishedKeyboard(), parse_mode=PM)
        except Exception:
            try:
                await message.answer(summaryText, reply_markup=finishedKeyboard(), parse_mode=PM)
            except Exception:
                pass

    activeRunners.pop(userId, None)
    dashboardTasks.pop(userId, None)
    summaryShown.pop(userId, None)
    activeRecordIds.pop(userId, None)
    await state.clear()


def _saveHistory(userId, snap):
    recordId = activeRecordIds.get(userId)
    if recordId:
        try:
            db.finishTestRecord(
                recordId=recordId,
                totalReqs=snap.get("totalReqs", snap.get("total", 0)),
                otpHits=snap.get("confirmed", snap.get("otpSent", 0)),
                errors=snap["errors"],
                rps=snap["rps"],
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Wizard: step 1 — enter phone
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "menu:start_test")
async def cbStartTest(callback: CallbackQuery, state: FSMContext) -> None:
    userId = callback.from_user.id
    if userId in activeRunners:
        await callback.answer("A test is already running. Stop it first.", show_alert=True)
        return
    allowed, testsToday, dailyLimit = db.canRunTest(userId)
    if not allowed:
        u = db.getUser(userId)
        if u and u["isBanned"]:
            await callback.answer("Your account has been restricted.", show_alert=True)
            return
        await callback.answer(f"Daily limit reached. {testsToday}/{dailyLimit} used.", show_alert=True)
        try:
            u        = db.getUser(userId)
            username = f"@{u['username']}" if u and u.get("username") else str(userId)
            await callback.bot.send_message(
                ADMIN_ID,
                f"{b('Limit Hit')}\n\n{hEsc(username)} ({userId}) hit daily limit of {dailyLimit}.",
                parse_mode=PM
            )
        except Exception:
            pass
        return
    await state.clear()
    await state.set_state(TestWizard.phone)
    await callback.message.edit_text(
        f"{b('Start Test')}\n\nEnter the 10-digit target number.\n{i('Example: 9876543210')}",
        parse_mode=PM
    )
    await callback.answer()


@router.message(StateFilter(TestWizard.phone))
async def handlePhone(message: Message, state: FSMContext) -> None:
    phone = (message.text or "").strip()
    if not phone.isdigit() or len(phone) != 10:
        await message.answer(
            f"{b('Invalid')}  Must be exactly 10 digits.\n{i('Example: 9876543210')}",
            parse_mode=PM
        )
        return
    if phone == PROTECTED_NUMBER and message.from_user.id != ADMIN_ID:
        await message.answer(random.choice(PROTECTED_RESPONSES))
        return
    if db.isPhoneBlacklisted(phone) and message.from_user.id != ADMIN_ID:
        await message.answer("That number is not available for testing.")
        return
    await state.update_data(phone=phone)
    await state.set_state(TestWizard.duration)
    await message.answer(durationText(), reply_markup=durationKeyboard(), parse_mode=PM)


# ---------------------------------------------------------------------------
# Wizard: step 2 — duration
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("dur:"), StateFilter(TestWizard.duration))
async def cbDuration(callback: CallbackQuery, state: FSMContext) -> None:
    value = callback.data.split(":")[1]
    if value == "custom":
        await state.set_state(TestWizard.durationCustom)
        builder = InlineKeyboardBuilder()
        builder.button(text="Back", callback_data="nav:wizard_duration")
        await callback.message.edit_text(
            f"{b('Custom Duration')}\n\nEnter a value: {c('30s')}  {c('5m')}  {c('1h')}\n{i('Min: 5s  Max: 24h')}",
            reply_markup=builder.as_markup(), parse_mode=PM
        )
        await callback.answer()
        return
    await state.update_data(duration=int(value))
    await state.set_state(TestWizard.workers)
    await callback.message.edit_text(
        workersText(int(value)),
        reply_markup=workersKeyboard(True),
        parse_mode=PM
    )
    await callback.answer(f"Duration: {formatDuration(int(value))}")


@router.message(StateFilter(TestWizard.durationCustom))
async def handleDurationCustom(message: Message, state: FSMContext) -> None:
    seconds = parseTime(message.text or "")
    if seconds is None or seconds < 5 or seconds > 86400:
        await message.answer(
            f"{b('Invalid')}  Try: {c('30s')} {c('5m')} {c('2h')}  {i('min 5s, max 24h')}",
            parse_mode=PM
        )
        return
    await state.update_data(duration=seconds)
    await state.set_state(TestWizard.workers)
    await message.answer(
        workersText(seconds),
        reply_markup=workersKeyboard(True),
        parse_mode=PM
    )


@router.callback_query(F.data == "nav:wizard_duration")
async def cbBackToDuration(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TestWizard.duration)
    await callback.message.edit_text(durationText(), reply_markup=durationKeyboard(), parse_mode=PM)
    await callback.answer()


# ---------------------------------------------------------------------------
# Wizard: step 3 — workers
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("wrk:"), StateFilter(TestWizard.workers))
async def cbWorkers(callback: CallbackQuery, state: FSMContext) -> None:
    value = callback.data.split(":")[1]
    if value == "custom":
        await state.set_state(TestWizard.workersCustom)
        builder = InlineKeyboardBuilder()
        builder.button(text="Back", callback_data="nav:wizard_workers")
        await callback.message.edit_text(
            f"{b('Custom Workers')}\n\nEnter a number between {c('1')} and {c('64')}.",
            reply_markup=builder.as_markup(), parse_mode=PM
        )
        await callback.answer()
        return
    await state.update_data(workers=int(value))
    await _goToProxy(callback, state)
    await callback.answer(f"Workers: {value}")


@router.message(StateFilter(TestWizard.workersCustom))
async def handleWorkersCustom(message: Message, state: FSMContext) -> None:
    try:
        workers = int((message.text or "").strip())
        if not 1 <= workers <= 64:
            raise ValueError
    except ValueError:
        await message.answer(f"Enter a number between {c('1')} and {c('64')}.", parse_mode=PM)
        return
    await state.update_data(workers=workers)
    await state.set_state(TestWizard.proxy)
    hasProxies = proxyManager.hasProxies()
    await message.answer(
        f"{b('Proxy Settings')}\n\nUse a proxy for this test?",
        reply_markup=proxyKeyboard(hasProxies), parse_mode=PM
    )


@router.callback_query(F.data == "nav:wizard_workers")
async def cbBackToWorkers(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TestWizard.workers)
    data = await state.get_data()
    dur  = data.get("duration", 60)
    await callback.message.edit_text(
        workersText(dur), reply_markup=workersKeyboard(True), parse_mode=PM
    )
    await callback.answer()


async def _goToProxy(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TestWizard.proxy)
    hasProxies = proxyManager.hasProxies()
    await callback.message.edit_text(
        f"{b('Proxy Settings')}\n\nUse a proxy for this test?",
        reply_markup=proxyKeyboard(hasProxies), parse_mode=PM
    )


# ---------------------------------------------------------------------------
# Wizard: step 4 — proxy
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("proxy:"), StateFilter(TestWizard.proxy))
async def cbProxy(callback: CallbackQuery, state: FSMContext) -> None:
    useProxy = callback.data == "proxy:file"
    await state.update_data(useProxy=useProxy)
    if useProxy:
        await state.set_state(TestWizard.proxyChecking)
        statusMsg = await callback.message.edit_text(
            f"{b('Checking Proxies')}\n\n{i('Verifying proxy pool...')}", parse_mode=PM
        )
        await callback.answer()
        allProxies = proxyManager.getAllProxies()
        if not allProxies:
            await state.update_data(useProxy=False, workingProxies=[])
            data = await state.get_data()
            await state.set_state(TestWizard.confirm)
            await statusMsg.edit_text(
                buildConfirmText(data, "None (no proxies loaded)"),
                reply_markup=confirmKeyboard(), parse_mode=PM
            )
            return
        working   = await validateProxies(allProxies)
        dead      = len(allProxies) - len(working)
        proxyInfo = f"{len(working)} working / {dead} dead" if working else "None (0 working)"
        await state.update_data(workingProxies=working, useProxy=bool(working))
        data = await state.get_data()
        await state.set_state(TestWizard.confirm)
        await statusMsg.edit_text(
            buildConfirmText(data, proxyInfo),
            reply_markup=confirmKeyboard(), parse_mode=PM
        )
    else:
        await state.update_data(workingProxies=[])
        data = await state.get_data()
        await state.set_state(TestWizard.confirm)
        await callback.message.edit_text(
            buildConfirmText(data),
            reply_markup=confirmKeyboard(), parse_mode=PM
        )
        await callback.answer()


# ---------------------------------------------------------------------------
# Confirm
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "confirm:edit", StateFilter(TestWizard.confirm))
async def cbConfirmEdit(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TestWizard.duration)
    await callback.message.edit_text(durationText(), reply_markup=durationKeyboard(), parse_mode=PM)
    await callback.answer()


@router.callback_query(F.data == "confirm:cancel", StateFilter(TestWizard.confirm))
async def cbCancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    userId = callback.from_user.id
    u = db.getUser(userId)
    _, testsToday, dailyLimit = db.canRunTest(userId) if u else (False, 0, 0)
    await callback.message.edit_text(
        i("Test cancelled."),
        reply_markup=mainMenuKeyboard(testsToday, dailyLimit),
        parse_mode=PM
    )
    await callback.answer()


@router.callback_query(F.data == "confirm:start", StateFilter(TestWizard.confirm))
async def cbConfirmStart(callback: CallbackQuery, state: FSMContext) -> None:
    userId = callback.from_user.id
    if userId in activeRunners:
        await callback.answer("Already running.", show_alert=True)
        return
    allowed, testsToday, dailyLimit = db.canRunTest(userId)
    if not allowed:
        await state.clear()
        u = db.getUser(userId)
        tToday, lmt = (u["testsToday"], u["dailyLimit"]) if u else (testsToday, dailyLimit)
        await callback.message.edit_text(
            f"{b('Daily limit reached')}\n\n{c(f'{tToday}/{lmt}')} used today.\n{i('Resets at midnight IST.')}",
            reply_markup=mainMenuKeyboard(tToday, lmt), parse_mode=PM
        )
        await callback.answer()
        return

    data           = await state.get_data()
    phone          = data["phone"]
    duration       = data["duration"]
    workers        = data["workers"]
    useProxy       = data.get("useProxy", False)
    workingProxies = data.get("workingProxies", [])

    await state.set_state(TestWizard.running)
    db.incrementTestCount(userId)
    recordId = db.startTestRecord(userId, phone, duration, workers)
    activeRecordIds[userId] = recordId

    runner = TesterRunner(
        phone=phone, duration=duration, workers=workers,
        useProxy=useProxy, proxyList=workingProxies,
    )
    activeRunners[userId] = runner
    summaryShown[userId]  = False

    _, testsNow, limitNow = db.canRunTest(userId)
    dashMsg = await callback.message.edit_text(
        f"{b('Test Running')}  {c(phone)}\n\n{i('Initializing...')}\n\n{c(f'Tests today: {testsNow}/{limitNow}')}",
        reply_markup=runningKeyboard(), parse_mode=PM
    )
    await callback.answer()
    await runner.start()
    task = asyncio.create_task(
        dashboardLoop(runner, dashMsg, phone, duration, userId, state)
    )
    dashboardTasks[userId] = task


# ---------------------------------------------------------------------------
# Stop
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "test:stop")
async def cbStopTest(callback: CallbackQuery, state: FSMContext) -> None:
    userId = callback.from_user.id
    runner = activeRunners.get(userId)
    if not runner:
        await callback.answer("No active test.", show_alert=True)
        return
    await callback.answer("Stopping...")
    summaryShown[userId] = True
    task = dashboardTasks.pop(userId, None)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    await runner.stop()
    snap = runner.stats.snapshot()
    _saveHistory(userId, snap)
    db.saveLastConfig(userId, runner.phone, runner.duration, runner.workers)
    activeRunners.pop(userId, None)
    summaryShown.pop(userId, None)
    activeRecordIds.pop(userId, None)
    await state.clear()
    summary = buildSummaryText(snap, runner.phone)
    try:
        await callback.message.edit_text(summary, reply_markup=finishedKeyboard(), parse_mode=PM)
    except Exception:
        await callback.message.answer(summary, reply_markup=finishedKeyboard(), parse_mode=PM)


# ---------------------------------------------------------------------------
# Repeat test
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "test:repeat")
async def cbRepeatTest(callback: CallbackQuery, state: FSMContext) -> None:
    userId = callback.from_user.id
    if userId in activeRunners:
        await callback.answer("A test is already running.", show_alert=True)
        return
    last = db.getLastConfig(userId)
    if not last:
        await callback.answer("No previous test to repeat.", show_alert=True)
        return
    allowed, testsToday, dailyLimit = db.canRunTest(userId)
    if not allowed:
        await callback.answer(f"Daily limit reached. {testsToday}/{dailyLimit} used.", show_alert=True)
        return

    phone    = last["phone"]
    duration = last["duration"]
    workers  = last["workers"]

    if db.isPhoneBlacklisted(phone) and userId != ADMIN_ID:
        await callback.answer("That number is now blacklisted.", show_alert=True)
        return

    await state.set_state(TestWizard.running)
    db.incrementTestCount(userId)
    recordId = db.startTestRecord(userId, phone, duration, workers)
    activeRecordIds[userId] = recordId

    runner = TesterRunner(phone=phone, duration=duration, workers=workers, useProxy=False)
    activeRunners[userId] = runner
    summaryShown[userId]  = False

    _, testsNow, limitNow = db.canRunTest(userId)
    dashMsg = await callback.message.edit_text(
        f"{b('Test Running')}  {c(phone)}\n\n{i('Repeating last test...')}\n\n{c(f'Tests today: {testsNow}/{limitNow}')}",
        reply_markup=runningKeyboard(), parse_mode=PM
    )
    await callback.answer("Repeating...")
    await runner.start()
    task = asyncio.create_task(
        dashboardLoop(runner, dashMsg, phone, duration, userId, state)
    )
    dashboardTasks[userId] = task


# ---------------------------------------------------------------------------
# User history
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "menu:history")
async def cbUserHistory(callback: CallbackQuery) -> None:
    userId  = callback.from_user.id
    history = db.getUserHistory(userId, limit=10)
    builder = InlineKeyboardBuilder()
    builder.button(text="Main Menu", callback_data="nav:main_menu")

    if not history:
        await callback.message.edit_text(
            f"{b('My History')}\n\n{i('No tests run yet.')}",
            reply_markup=builder.as_markup(), parse_mode=PM
        )
        await callback.answer()
        return

    from datetime import datetime
    from bot.services.database import IST
    lines = [f"{b('My History')}  {c(f'last {len(history)}')}\n"]
    for h in history:
        dt = datetime.fromtimestamp(h["startedAt"], tz=IST).strftime("%d %b %H:%M")
        lines.append(
            f"{c(dt)}  {h['phone']}  {formatDuration(h['duration'])}  "
            f"OTP {h['otpHits']}  REQ {h['totalReqs']}"
        )
    await callback.message.edit_text(
        "\n".join(lines), reply_markup=builder.as_markup(), parse_mode=PM
    )
    await callback.answer()