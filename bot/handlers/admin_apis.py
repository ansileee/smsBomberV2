from __future__ import annotations

import json
import asyncio
import random
import string
from typing import Optional, List

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.services.database import db
from bot.services.api_manager import apiManager
from bot.services.tester_runner import testSingleApi
from bot.utils import PM, b, i, c, hEsc as esc, isAdmin

router = Router()

APIS_PER_PAGE      = 8
HC_PER_PAGE        = 8
HEALTH_CONCURRENCY = 10

_healthCheckCache: dict = {}


class ApiAdminStates(StatesGroup):
    waitingApiJson          = State()
    waitingConfirm          = State()
    waitingConfirmTestPhone = State()
    waitingEditJson         = State()
    waitingEditConfirm      = State()
    waitingRename           = State()
    waitingTestPhone        = State()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def randomPhone() -> str:
    prefixes = [
        "6360","6361","6362","7000","7001","7002","7200","7201","7300","7400",
        "7500","7600","7700","7800","7900","8000","8001","8002","8003","8050",
        "8080","8100","8104","8105","8800","8801","8900","8901","9000","9001",
        "9002","9003","9004","9005","9006","9007","9008","9009","9010","9011",
        "9100","9101","9102","9200","9201","9300","9400","9500","9600","9700",
        "9800","9810","9820","9830","9840","9850","9860","9870","9880","9890",
        "9900","9910","9920","9930","9940","9950","9960","9970","9980","9990",
    ]
    prefix    = random.choice(prefixes)
    remaining = 10 - len(prefix)
    return prefix + "".join(random.choices(string.digits, k=remaining))


def getMergedTagged() -> List[dict]:
    """Merge base + custom APIs with _dbId and _isOverride tags."""
    from apis import API_CONFIGS as BASE  # type: ignore
    customApis = db.getAllCustomApis()
    dbByName: dict = {}
    dbByUrl:  dict = {}
    for row in customApis:
        try:
            cfg = json.loads(row["configJson"])
            dbByName[cfg.get("name", "").lower()] = row
            dbByUrl[cfg.get("url", "")]           = row
        except Exception:
            pass

    result    = []
    seenDbIds = set()
    for base in BASE:
        row = dbByName.get(base["name"].lower()) or dbByUrl.get(base["url"])
        if row:
            try:
                cfg = json.loads(row["configJson"])
                cfg["_dbId"]       = row["id"]
                cfg["_isOverride"] = True
                result.append(cfg)
                seenDbIds.add(row["id"])
            except Exception:
                pass
        else:
            entry = dict(base)
            entry["_dbId"]       = None
            entry["_isOverride"] = False
            result.append(entry)

    for row in customApis:
        if row["id"] not in seenDbIds:
            try:
                cfg = json.loads(row["configJson"])
                cfg["_dbId"]       = row["id"]
                cfg["_isOverride"] = False
                result.append(cfg)
            except Exception:
                pass
    return result


def cleanCfg(api: dict) -> dict:
    return {k: v for k, v in api.items() if not k.startswith("_")}


def formatDetail(cfg: dict) -> str:
    lines = [f"{b('API Detail')}\n"]
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
    if cfg.get("cookies"):
        lines.append(f"Cookies {c(str(len(cfg['cookies'])))} fields")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------

def apiManagerMenuKeyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Add API",      callback_data="aapi:add")
    builder.button(text="List All",     callback_data="aapi:list:0")
    builder.button(text="Browse",       callback_data="aapi:browse")
    builder.button(text="Health Check", callback_data="aapi:health")
    builder.button(text="Back",         callback_data="adm:menu")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def browseMenuKeyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Recently Added", callback_data="aapi:browse:recent")
    builder.button(text="All APIs (A-Z)", callback_data="aapi:browse:az")
    builder.button(text="Dead APIs",      callback_data="aapi:browse:dead")
    builder.button(text="Skipped APIs",   callback_data="aapi:browse:skipped")
    builder.button(text="Back",           callback_data="aapi:menu")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def apiListKeyboard(page: int, totalPages: int, pageApis: list, pageStart: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for n, api in enumerate(pageApis):
        dbId  = api.get("_dbId")
        label = api["name"]
        if api.get("_isOverride"):
            label += " [edited]"
        elif not dbId:
            label += " [base]"
        cb = f"aapi:ddb:{dbId}" if dbId else f"aapi:didx:{pageStart + n}"
        builder.button(text=label, callback_data=cb)
    if page > 0:
        builder.button(text="Prev", callback_data=f"aapi:list:{page - 1}")
    if page < totalPages - 1:
        builder.button(text="Next", callback_data=f"aapi:list:{page + 1}")
    builder.button(text="Back", callback_data="aapi:menu")
    builder.adjust(1)
    return builder.as_markup()


def apiDetailKeyboard(dbId: Optional[int], globalIdx: Optional[int] = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if dbId:
        builder.button(text="Rename",    callback_data=f"aapi:rename:{dbId}")
        builder.button(text="Edit JSON", callback_data=f"aapi:edit:{dbId}")
        builder.button(text="Delete",    callback_data=f"aapi:delete:{dbId}")
        builder.button(text="Test",      callback_data=f"aapi:testone:{dbId}")
        builder.button(text="Back",      callback_data="aapi:list:0")
        builder.adjust(3, 1, 1)
    else:
        builder.button(text="Edit (copy to bot)", callback_data=f"aapi:copyidx:{globalIdx}")
        builder.button(text="Test",               callback_data=f"aapi:testoneidx:{globalIdx}")
        builder.button(text="Back",               callback_data="aapi:list:0")
        builder.adjust(1, 1, 1)
    return builder.as_markup()


def backToApiMenuKeyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="API Manager", callback_data="aapi:menu")
    builder.adjust(1)
    return builder.as_markup()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "aapi:menu")
async def cbApiMenu(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    await state.clear()
    allApis   = getMergedTagged()
    custom    = sum(1 for a in allApis if a.get("_dbId") and not a.get("_isOverride"))
    overrides = sum(1 for a in allApis if a.get("_isOverride"))
    base      = len(allApis) - custom - overrides
    skipped   = len(db.getSkippedApiNames())
    await callback.message.edit_text(
        f"{b('API Manager')}\n\n"
        f"Total    {c(str(len(allApis)))}\n"
        f"Base     {c(str(base))}  Edited  {c(str(overrides))}  Custom  {c(str(custom))}\n"
        f"Skipped  {c(str(skipped))}",
        reply_markup=apiManagerMenuKeyboard(),
        parse_mode=PM
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Browse
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "aapi:browse")
async def cbBrowse(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(
        f"{b('Browse APIs')}\n\n{i('Choose a view.')}",
        reply_markup=browseMenuKeyboard(),
        parse_mode=PM
    )


@router.callback_query(F.data.startswith("aapi:browse:"))
async def cbBrowseView(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    view    = callback.data.split(":")[2]
    allApis = getMergedTagged()
    cache   = _healthCheckCache.get(f"hc_{callback.from_user.id}")
    builder = InlineKeyboardBuilder()

    if view == "recent":
        customApis = db.getAllCustomApis()
        recent     = sorted(customApis, key=lambda x: x["id"], reverse=True)[:20]
        if not recent:
            await callback.answer("No custom APIs added yet.", show_alert=True)
            return
        lines = [f"{b('Recently Added')}  {c(str(len(recent)) + ' APIs')}\n"]
        for n, row in enumerate(recent, 1):
            cfg = json.loads(row["configJson"])
            lines.append(f"{n}. {esc(cfg['name'])}  {c(cfg['method'])}")
            builder.button(text=cfg["name"], callback_data=f"aapi:ddb:{row['id']}")
        builder.button(text="Back", callback_data="aapi:browse")
        builder.adjust(1)
        await callback.message.edit_text("\n".join(lines), reply_markup=builder.as_markup(), parse_mode=PM)

    elif view == "az":
        sortedApis = sorted(allApis, key=lambda x: x["name"].lower())
        lines = [f"{b('All APIs')}  {c(str(len(sortedApis)) + ' total')}\n"]
        shown = sortedApis[:20]
        for n, api in enumerate(shown, 1):
            tag  = " [custom]" if api.get("_dbId") and not api.get("_isOverride") else (" [edited]" if api.get("_isOverride") else " [base]")
            lines.append(f"{n}. {esc(api['name'])}{tag}")
            dbId = api.get("_dbId")
            cb   = f"aapi:ddb:{dbId}" if dbId else f"aapi:didx:{sortedApis.index(api)}"
            builder.button(text=api["name"], callback_data=cb)
        if len(sortedApis) > 20:
            lines.append(f"\n{i(f'Showing first 20 of {len(sortedApis)}. Use List All for full paginated view.')}")
        builder.button(text="Back", callback_data="aapi:browse")
        builder.adjust(1)
        await callback.message.edit_text("\n".join(lines), reply_markup=builder.as_markup(), parse_mode=PM)

    elif view == "dead":
        if not cache or not cache.get("dead"):
            bk = InlineKeyboardBuilder()
            bk.button(text="Run Health Check", callback_data="aapi:health")
            bk.button(text="Back", callback_data="aapi:browse")
            bk.adjust(1)
            await callback.message.edit_text(
                f"{b('Dead APIs')}\n\n{i('No health check data yet. Run Health Check first.')}",
                reply_markup=bk.as_markup(), parse_mode=PM
            )
            await callback.answer()
            return
        dead  = cache["dead"]
        lines = [f"{b('Dead APIs')}  {c(str(len(dead)) + ' failed')}\n"]
        for n, r in enumerate(dead, 1):
            err = (r["result"].get("error") or "timeout")[:40]
            lines.append(f"{n}. {esc(r['name'])}  {i(err)}")
            builder.button(text=r["name"], callback_data=f"aapi:hcresult:dead:{n - 1}")
        if dead:
            builder.button(text="Skip All Dead", callback_data="aapi:skipall_dead")
        builder.button(text="Back", callback_data="aapi:browse")
        builder.adjust(1)
        await callback.message.edit_text("\n".join(lines), reply_markup=builder.as_markup(), parse_mode=PM)

    elif view == "skipped":
        skippedNames = db.getSkippedApiNames()
        if not skippedNames:
            bk = InlineKeyboardBuilder()
            bk.button(text="Back", callback_data="aapi:browse")
            bk.adjust(1)
            await callback.message.edit_text(
                f"{b('Skipped APIs')}\n\n{i('No APIs are currently skipped.')}",
                reply_markup=bk.as_markup(), parse_mode=PM
            )
            await callback.answer()
            return
        lines = [f"{b('Skipped APIs')}  {c(str(len(skippedNames)) + ' skipped')}\n"]
        for n, name in enumerate(sorted(skippedNames), 1):
            lines.append(f"{n}. {esc(name)}")
            api = next((a for a in allApis if a["name"] == name and a.get("_dbId")), None)
            if api:
                builder.button(text=f"Enable: {name}", callback_data=f"aapi:unskip:{api['_dbId']}")
        builder.button(text="Back", callback_data="aapi:browse")
        builder.adjust(1)
    await callback.answer()
    await callback.message.edit_text("\n".join(lines), reply_markup=builder.as_markup(), parse_mode=PM)



@router.callback_query(F.data == "aapi:skipall_dead")
async def cbSkipAllDead(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    cache = _healthCheckCache.get(f"hc_{callback.from_user.id}")
    if not cache or not cache.get("dead"):
        await callback.answer("No dead API data. Run health check first.", show_alert=True)
        return
    dead  = cache["dead"]
    count = 0
    for r in dead:
        db.skipApi(r["name"])
        count += 1
    await callback.answer(f"Skipped {count} dead APIs.")
    builder = InlineKeyboardBuilder()
    builder.button(text="View Skipped", callback_data="aapi:browse:skipped")
    builder.button(text="API Manager",  callback_data="aapi:menu")
    builder.adjust(1)
    await callback.message.edit_text(
        f"{b('Auto-skip complete')}\n\n"
        f"Skipped {c(str(count))} dead APIs.\n"
        f"They will be excluded from future tests until you re-enable them.",
        reply_markup=builder.as_markup(),
        parse_mode=PM
    )


@router.callback_query(F.data.startswith("aapi:unskip:"))
async def cbUnskipFromBrowse(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    dbId = int(callback.data.split(":")[2])
    row  = db.getCustomApi(dbId)
    if row:
        db.unskipApi(row["name"])
        await callback.answer(f"Enabled: {row['name']}")
    callback.data = "aapi:browse:skipped"
    await cbBrowseView(callback)


# ---------------------------------------------------------------------------
# List all APIs (paginated)
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("aapi:list:"))
async def cbListApis(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    page       = int(callback.data.split(":")[2])
    allApis    = getMergedTagged()
    total      = len(allApis)
    totalPages = max(1, -(-total // APIS_PER_PAGE))
    start      = page * APIS_PER_PAGE
    pageApis   = allApis[start:start + APIS_PER_PAGE]
    lines = [f"{b('APIs')}  {c(f'{total} total  page {page+1}/{totalPages}')}\n"]
    for n, api in enumerate(pageApis, start=start + 1):
        tag = " [edited]" if api.get("_isOverride") else (" [base]" if not api.get("_dbId") else " [custom]")
        url = api["url"][:48] + "..." if len(api["url"]) > 48 else api["url"]
        lines.append(f"{n}. {esc(api['name'])}  {api['method']}{tag}\n   {esc(url)}")
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=apiListKeyboard(page, totalPages, pageApis, start),
        parse_mode=PM
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Detail screens
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("aapi:ddb:"))
async def cbDetailDb(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    dbId = int(callback.data.split(":")[2])
    row  = db.getCustomApi(dbId)
    if not row:
        await callback.answer("API not found.", show_alert=True)
        return
    cfg      = json.loads(row["configJson"])
    skipped  = db.isApiSkipped(cfg["name"])
    skipStr  = f"\n{i('Currently skipped — will not be used in tests.')}" if skipped else ""
    await callback.answer()
    await callback.message.edit_text(
        formatDetail(cfg) + skipStr,
        reply_markup=apiDetailKeyboard(dbId=dbId),
        parse_mode=PM
    )


@router.callback_query(F.data.startswith("aapi:didx:"))
async def cbDetailIdx(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    idx     = int(callback.data.split(":")[2])
    allApis = getMergedTagged()
    if idx >= len(allApis):
        await callback.answer("API not found.", show_alert=True)
        return
    api = allApis[idx]
    await callback.message.edit_text(
        formatDetail(api),
        reply_markup=apiDetailKeyboard(dbId=None, globalIdx=idx),
        parse_mode=PM
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Copy base API to DB for editing
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("aapi:copyidx:"))
async def cbCopyBase(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    idx     = int(callback.data.split(":")[2])
    allApis = getMergedTagged()
    if idx >= len(allApis):
        await callback.answer("API not found.", show_alert=True)
        return
    api = allApis[idx]
    if api.get("_dbId"):
        await callback.answer("Already in bot DB.", show_alert=True)
        return
    cfg  = cleanCfg(api)
    dbId = db.addCustomApi(name=cfg["name"], method=cfg["method"], url=cfg["url"], configJson=json.dumps(cfg))
    apiManager.invalidateCache()
    await state.set_state(ApiAdminStates.waitingEditJson)
    await state.update_data(editApiId=dbId)
    await callback.message.edit_text(
        f"{b('Copied to bot.')} Paste updated JSON to edit {esc(cfg['name'])}.\n\n"
        f"Current:\n<pre>{esc(json.dumps(cfg, indent=2))}</pre>",
        parse_mode=PM
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Rename
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("aapi:rename:"))
async def cbRename(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    dbId = int(callback.data.split(":")[2])
    row  = db.getCustomApi(dbId)
    if not row:
        await callback.answer("Not found.", show_alert=True)
        return
    await state.set_state(ApiAdminStates.waitingRename)
    await state.update_data(renameApiId=dbId)
    await callback.answer()
    await callback.message.edit_text(
        f"{b('Rename')}  {c(esc(row['name']))}\n\nType the new name.",
        parse_mode=PM
    )


@router.message(StateFilter(ApiAdminStates.waitingRename))
async def handleRename(message: Message, state: FSMContext) -> None:
    if not isAdmin(message.from_user.id):
        return
    newName = (message.text or "").strip()
    if not newName or len(newName) > 64:
        await message.answer("Name must be 1-64 characters.")
        return
    data = await state.get_data()
    dbId = data["renameApiId"]
    row  = db.getCustomApi(dbId)
    if not row:
        await message.answer("API no longer exists.")
        await state.clear()
        return
    cfg         = json.loads(row["configJson"])
    cfg["name"] = newName
    db.updateCustomApi(dbId, name=newName, method=cfg["method"], url=cfg["url"], configJson=json.dumps(cfg))
    apiManager.invalidateCache()
    await state.clear()
    await message.answer(
        f"Renamed to: {c(esc(newName))}",
        reply_markup=apiDetailKeyboard(dbId=dbId),
        parse_mode=PM
    )


# ---------------------------------------------------------------------------
# Edit JSON
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("aapi:edit:"))
async def cbEditApi(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    dbId = int(callback.data.split(":")[2])
    row  = db.getCustomApi(dbId)
    if not row:
        await callback.answer("Not found.", show_alert=True)
        return
    await state.set_state(ApiAdminStates.waitingEditJson)
    await state.update_data(editApiId=dbId)
    cfg = json.loads(row["configJson"])
    await callback.answer()
    await callback.message.edit_text(
        f"{b('Edit')}  {c(esc(cfg['name']))}\n\nPaste updated JSON.\n\n"
        f"Current:\n<pre>{esc(json.dumps(cfg, indent=2))}</pre>",
        parse_mode=PM
    )


@router.message(StateFilter(ApiAdminStates.waitingEditJson))
async def handleEditJson(message: Message, state: FSMContext) -> None:
    if not isAdmin(message.from_user.id):
        return
    raw      = (message.text or "").strip()
    ok, cfg, error = apiManager.validateApiJson(raw)
    if not ok:
        await message.answer(f"Invalid JSON.\n\n{error}\n\nFix and paste again or /start to cancel.")
        return
    data = await state.get_data()
    dbId = data.get("editApiId")
    await state.update_data(editApiJson=json.dumps(cfg), editApiConfig=cfg)
    await state.set_state(ApiAdminStates.waitingEditConfirm)
    builder = InlineKeyboardBuilder()
    builder.button(text="Save",   callback_data="aapi:confirm_edit")
    builder.button(text="Cancel", callback_data=f"aapi:ddb:{dbId}")
    builder.adjust(2)
    await message.answer(f"{formatDetail(cfg)}\n\nSave?", reply_markup=builder.as_markup(), parse_mode=PM)


@router.callback_query(F.data == "aapi:confirm_edit", StateFilter(ApiAdminStates.waitingEditConfirm))
async def cbConfirmEdit(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    data    = await state.get_data()
    dbId    = data.get("editApiId")
    cfg     = data.get("editApiConfig")
    cfgJson = data.get("editApiJson")
    if not all([dbId, cfg, cfgJson]):
        await callback.answer("Session expired.", show_alert=True)
        await state.clear()
        return
    db.updateCustomApi(dbId, name=cfg["name"], method=cfg["method"], url=cfg["url"], configJson=cfgJson)
    apiManager.invalidateCache()
    await state.clear()
    await callback.message.edit_text(
        f"{b('Saved.')}  {esc(cfg['name'])} ({cfg['method']}) updated.",
        reply_markup=apiDetailKeyboard(dbId=dbId),
        parse_mode=PM
    )
    await callback.answer("Saved.")


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("aapi:delete:"))
async def cbDeleteApi(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    dbId = int(callback.data.split(":")[2])
    row  = db.getCustomApi(dbId)
    if not row:
        await callback.answer("Not found.", show_alert=True)
        return
    db.deleteCustomApi(dbId)
    apiManager.invalidateCache()
    await callback.answer(f"Deleted: {row['name']}")
    await callback.message.edit_text(
        f"{b('Deleted.')} API removed.", reply_markup=backToApiMenuKeyboard(), parse_mode=PM
    )


# ---------------------------------------------------------------------------
# Add new API
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "aapi:add")
async def cbAddApi(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    await state.set_state(ApiAdminStates.waitingApiJson)
    example = '{"name":"MyApp","method":"POST","url":"https://api.example.com/otp","headers":{"content-type":"application/json"},"json":{"phone":"{phone}"}}'
    await callback.message.edit_text(
        f"{b('Add API')}\n\nPaste full JSON config.\n"
        f"Required: name, method, url\n"
        f"Optional: headers, json, data, params, cookies\n\n"
        f"Example:\n<pre>{esc(example)}</pre>",
        parse_mode=PM
    )
    await callback.answer()


@router.message(StateFilter(ApiAdminStates.waitingApiJson))
async def handleApiJson(message: Message, state: FSMContext) -> None:
    if not isAdmin(message.from_user.id):
        return
    raw      = (message.text or "").strip()
    ok, cfg, error = apiManager.validateApiJson(raw)
    if not ok:
        await message.answer(f"Invalid.\n\n{error}\n\nFix and paste again.")
        return
    await state.update_data(pendingApiJson=json.dumps(cfg), pendingApiConfig=cfg)
    await state.set_state(ApiAdminStates.waitingConfirm)
    builder = InlineKeyboardBuilder()
    builder.button(text="Save",      callback_data="aapi:confirm_save")
    builder.button(text="Demo Test", callback_data="aapi:confirm_demotest")
    builder.button(text="Test",      callback_data="aapi:confirm_test")
    builder.button(text="Cancel",    callback_data="aapi:menu")
    builder.adjust(2, 2)
    await message.answer(
        f"{formatDetail(cfg)}\n\n"
        f"{i('Save  |  Demo Test — random number  |  Test — your number')}",
        reply_markup=builder.as_markup(),
        parse_mode=PM
    )


@router.callback_query(F.data == "aapi:confirm_save", StateFilter(ApiAdminStates.waitingConfirm))
async def cbConfirmSave(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    data    = await state.get_data()
    cfg     = data.get("pendingApiConfig")
    cfgJson = data.get("pendingApiJson")
    if not cfg or not cfgJson:
        await callback.answer("Session expired.", show_alert=True)
        await state.clear()
        return
    db.addCustomApi(name=cfg["name"], method=cfg["method"], url=cfg["url"], configJson=cfgJson)
    apiManager.invalidateCache()
    await state.clear()
    total   = len(getMergedTagged())
    builder = InlineKeyboardBuilder()
    builder.button(text="Demo Test",   callback_data="aapi:postsave_demotest")
    builder.button(text="Test",        callback_data="aapi:postsave_test")
    builder.button(text="API Manager", callback_data="aapi:menu")
    builder.adjust(2, 1)
    await callback.message.edit_text(
        f"{b('Saved.')}  {esc(cfg['name'])} added.  Total APIs: {c(str(total))}\n\n{i('Test it now or go back.')}",
        reply_markup=builder.as_markup(),
        parse_mode=PM
    )
    await callback.answer("Saved.")


# ---------------------------------------------------------------------------
# Demo Test and manual test (add flow)
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "aapi:confirm_demotest", StateFilter(ApiAdminStates.waitingConfirm))
async def cbConfirmDemoTest(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    data = await state.get_data()
    cfg  = data.get("pendingApiConfig")
    if not cfg:
        await callback.answer("Session expired.", show_alert=True)
        return
    phone   = randomPhone()
    waiting = await callback.message.edit_text(
        f"{b('Demo Test')}\n\nFiring one request with random number {c(phone)}...", parse_mode=PM
    )
    await callback.answer()
    result = await testSingleApi(cleanCfg(cfg), phone)
    await _showTestResult(waiting, cfg, phone, result, backCb="aapi:confirm_back")


@router.callback_query(F.data == "aapi:confirm_test", StateFilter(ApiAdminStates.waitingConfirm))
async def cbConfirmTest(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    await state.set_state(ApiAdminStates.waitingConfirmTestPhone)
    builder = InlineKeyboardBuilder()
    builder.button(text="Back", callback_data="aapi:confirm_back")
    await callback.answer()
    await callback.message.edit_text(
        f"{b('Test API')}\n\nEnter a 10-digit number to test with.",
        reply_markup=builder.as_markup(), parse_mode=PM
    )


@router.callback_query(F.data == "aapi:confirm_back")
async def cbConfirmBack(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    await state.set_state(ApiAdminStates.waitingConfirm)
    data = await state.get_data()
    cfg  = data.get("pendingApiConfig")
    if not cfg:
        await callback.answer("Session expired.", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    builder.button(text="Save",      callback_data="aapi:confirm_save")
    builder.button(text="Demo Test", callback_data="aapi:confirm_demotest")
    builder.button(text="Test",      callback_data="aapi:confirm_test")
    builder.button(text="Cancel",    callback_data="aapi:menu")
    builder.adjust(2, 2)
    await callback.answer()
    await callback.message.edit_text(
        f"{formatDetail(cfg)}\n\n{i('Save  |  Demo Test  |  Test — your number')}",
        reply_markup=builder.as_markup(), parse_mode=PM
    )


@router.message(StateFilter(ApiAdminStates.waitingConfirmTestPhone))
async def handleConfirmTestPhone(message: Message, state: FSMContext) -> None:
    if not isAdmin(message.from_user.id):
        return
    phone = (message.text or "").strip()
    if not phone.isdigit() or len(phone) != 10:
        await message.answer("Enter exactly 10 digits.")
        return
    data = await state.get_data()
    cfg  = data.get("pendingApiConfig")
    if not cfg:
        await message.answer("Session expired.")
        await state.clear()
        return
    await state.set_state(ApiAdminStates.waitingConfirm)
    waiting = await message.answer(
        f"{b('Testing')}  {esc(cfg['name'])} with {c(phone)}...", parse_mode=PM
    )
    result = await testSingleApi(cleanCfg(cfg), phone)
    await _showTestResult(waiting, cfg, phone, result, backCb="aapi:confirm_back")


# ---------------------------------------------------------------------------
# Post-save test
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "aapi:postsave_demotest")
async def cbPostSaveDemoTest(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    customApis = db.getAllCustomApis()
    if not customApis:
        await callback.answer("No API found.", show_alert=True)
        return
    row   = sorted(customApis, key=lambda x: x["id"], reverse=True)[0]
    cfg   = json.loads(row["configJson"])
    phone = randomPhone()
    waiting = await callback.message.edit_text(
        f"{b('Demo Test')}\n\nFiring one request with random number {c(phone)}...", parse_mode=PM
    )
    await callback.answer()
    result = await testSingleApi(cleanCfg(cfg), phone)
    await _showTestResult(waiting, cfg, phone, result, backCb="aapi:menu")


@router.callback_query(F.data == "aapi:postsave_test")
async def cbPostSaveTest(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    customApis = db.getAllCustomApis()
    if not customApis:
        await callback.answer("No API found.", show_alert=True)
        return
    row = sorted(customApis, key=lambda x: x["id"], reverse=True)[0]
    await state.set_state(ApiAdminStates.waitingTestPhone)
    await state.update_data(testApiDbId=row["id"], testApiIdx=None)
    cfg = json.loads(row["configJson"])
    builder = InlineKeyboardBuilder()
    builder.button(text="Back", callback_data="aapi:menu")
    await callback.message.edit_text(
        f"{b('Test')}  {esc(cfg['name'])}\n{c(cfg['method'])}  {esc(cfg['url'])}\n\nEnter a 10-digit phone number.",
        reply_markup=builder.as_markup(), parse_mode=PM
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Shared test result display
# ---------------------------------------------------------------------------

async def _showTestResult(message, cfg: dict, phone: str, result: dict, backCb: str) -> None:
    builder = InlineKeyboardBuilder()
    builder.button(text="Demo Test Again", callback_data=f"aapi:quickdemo:{cfg['name'][:20]}")
    builder.button(text="Back",            callback_data=backCb)
    builder.adjust(1)
    if not result["ok"]:
        await message.edit_text(
            f"{b('Test Failed')}\n\n"
            f"API    {esc(cfg['name'])}\n"
            f"Phone  {c(phone)}\n"
            f"Error  {c(esc(result['error']))}",
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
    await message.edit_text(
        f"{b('Test Result')}\n\n"
        f"API      {esc(cfg['name'])}\n"
        f"Phone    {c(phone)}\n"
        f"Status   {c(f'{lbl} {status}')}\n"
        f"Latency  {c(f'{latency}ms')}\n\n"
        f"{i('Response')}\n{c(snippet)}",
        reply_markup=builder.as_markup(), parse_mode=PM
    )


@router.callback_query(F.data.startswith("aapi:quickdemo:"))
async def cbQuickDemo(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    apiName = callback.data.split(":", 2)[2]
    allApis = getMergedTagged()
    api     = next((a for a in allApis if a["name"][:20] == apiName), None)
    if not api:
        await callback.answer("API not found.", show_alert=True)
        return
    phone   = randomPhone()
    waiting = await callback.message.edit_text(
        f"{b('Demo Test')}\n\nFiring one request with random number {c(phone)}...", parse_mode=PM
    )
    await callback.answer()
    result = await testSingleApi(cleanCfg(api), phone)
    await _showTestResult(waiting, cleanCfg(api), phone, result, backCb="aapi:menu")


# ---------------------------------------------------------------------------
# Test single API (from list)
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("aapi:testone:"))
async def cbTestOne(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    dbId    = int(callback.data.split(":")[2])
    allApis = getMergedTagged()
    api     = next((a for a in allApis if a.get("_dbId") == dbId), None)
    if not api:
        await callback.answer("API not found.", show_alert=True)
        return
    await state.set_state(ApiAdminStates.waitingTestPhone)
    await state.update_data(testApiDbId=dbId, testApiIdx=None)
    await callback.answer()
    await callback.message.edit_text(
        f"{b('Test')}  {esc(api['name'])}\n{c(api['method'])}  {esc(api['url'])}\n\nEnter a 10-digit phone number.",
        parse_mode=PM
    )


@router.callback_query(F.data.startswith("aapi:testoneidx:"))
async def cbTestOneIdx(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    idx     = int(callback.data.split(":")[2])
    allApis = getMergedTagged()
    if idx >= len(allApis):
        await callback.answer("API not found.", show_alert=True)
        return
    api = allApis[idx]
    await state.set_state(ApiAdminStates.waitingTestPhone)
    await state.update_data(testApiDbId=None, testApiIdx=idx)
    await callback.answer()
    await callback.message.edit_text(
        f"{b('Test')}  {esc(api['name'])}\n{c(api['method'])}  {esc(api['url'])}\n\nEnter a 10-digit phone number.",
        parse_mode=PM
    )


@router.message(StateFilter(ApiAdminStates.waitingTestPhone))
async def handleTestPhone(message: Message, state: FSMContext) -> None:
    if not isAdmin(message.from_user.id):
        return
    phone = (message.text or "").strip()
    if not phone.isdigit() or len(phone) != 10:
        await message.answer("Enter exactly 10 digits.")
        return
    data    = await state.get_data()
    dbId    = data.get("testApiDbId")
    idx     = data.get("testApiIdx")
    allApis = getMergedTagged()
    if dbId is not None:
        api = next((a for a in allApis if a.get("_dbId") == dbId), None)
    elif idx is not None:
        api = allApis[idx] if idx < len(allApis) else None
    else:
        api = None
    if not api:
        await message.answer("API no longer available.")
        await state.clear()
        return
    await state.clear()
    cfg     = cleanCfg(api)
    waiting = await message.answer(f"Testing {esc(api['name'])}...", parse_mode=PM)
    result  = await testSingleApi(cfg, phone)
    if not result["ok"]:
        await waiting.edit_text(
            f"{b('Test Failed')}\n\nAPI    {esc(api['name'])}\nError  {esc(result['error'])}",
            reply_markup=backToApiMenuKeyboard(), parse_mode=PM
        )
        return
    status  = result["status"]
    latency = result["latencyMs"]
    snippet = esc((result.get("snippet") or "(empty)")[:100])
    if status == 429:   lbl = "RATE LIMITED"
    elif status < 300:  lbl = "OK"
    elif status < 500:  lbl = "CLIENT ERR"
    else:               lbl = "SERVER ERR"
    await waiting.edit_text(
        f"{b('Test Result')}\n\n"
        f"API      {esc(api['name'])}\n"
        f"Status   {c(f'{lbl} {status}')}\n"
        f"Latency  {c(f'{latency}ms')}\n\n"
        f"{i('Response')}\n{c(snippet)}",
        reply_markup=backToApiMenuKeyboard(), parse_mode=PM
    )


# ---------------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "aapi:health")
async def cbHealthCheck(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    allApis = getMergedTagged()
    if not allApis:
        await callback.answer("No APIs loaded.", show_alert=True)
        return
    phone   = randomPhone()
    total   = len(allApis)
    waiting = await callback.message.edit_text(
        f"{b('Health Check')}\n\n{c(f'Testing {total} APIs...')}\n{i('Please wait.')}",
        parse_mode=PM
    )
    await callback.answer()

    semaphore = asyncio.Semaphore(HEALTH_CONCURRENCY)

    async def checkOne(api: dict) -> dict:
        async with semaphore:
            cfg    = cleanCfg(api)
            result = await testSingleApi(cfg, phone)
            return {"name": api["name"], "method": api["method"], "result": result}

    results = await asyncio.gather(*[checkOne(a) for a in allApis])

    okList   = [r for r in results if r["result"].get("ok") and 0 < (r["result"].get("status") or 0) < 300]
    rlList   = [r for r in results if r["result"].get("status") == 429]
    errList  = [r for r in results if r["result"].get("ok") and r["result"].get("status", 0) >= 400 and r["result"].get("status") != 429]
    deadList = [r for r in results if not r["result"].get("ok") or r["result"].get("status") is None]

    # Rate limit bypass detection — APIs that always 429 on first request
    alwaysRl = [r for r in rlList]
    rlNote   = ""
    if alwaysRl:
        rlNote = f"\n{i(f'{len(alwaysRl)} API(s) returned 429 immediately — consider skipping them.')}"

    cacheKey = f"hc_{callback.from_user.id}"
    _healthCheckCache[cacheKey] = {
        "phone": phone, "ok": okList, "rl": rlList, "err": errList, "dead": deadList
    }

    builder = InlineKeyboardBuilder()
    if okList:   builder.button(text=f"OK  ({len(okList)})",           callback_data="aapi:hccat:ok:0")
    if deadList: builder.button(text=f"Dead  ({len(deadList)})",       callback_data="aapi:hccat:dead:0")
    if rlList:   builder.button(text=f"Rate Limited  ({len(rlList)})", callback_data="aapi:hccat:rl:0")
    if errList:  builder.button(text=f"Errors  ({len(errList)})",      callback_data="aapi:hccat:err:0")
    if deadList: builder.button(text="Skip All Dead",                  callback_data="aapi:skipall_dead")
    builder.button(text="Run Again", callback_data="aapi:health")
    builder.button(text="Back",      callback_data="aapi:menu")
    builder.adjust(2, 2, 1, 2)

    await waiting.edit_text(
        f"{b('Health Check')}\n"
        f"{c(f'Phone: {phone}')}\n\n"
        f"OK            {c(str(len(okList)))}\n"
        f"Dead          {c(str(len(deadList)))}\n"
        f"Rate limited  {c(str(len(rlList)))}\n"
        f"Errors        {c(str(len(errList)))}"
        f"{rlNote}\n\n"
        f"{i('Tap a category to browse.')}",
        reply_markup=builder.as_markup(),
        parse_mode=PM
    )


@router.callback_query(F.data == "aapi:health_summary")
async def cbHealthSummary(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    cache = _healthCheckCache.get(f"hc_{callback.from_user.id}")
    if not cache:
        await callback.answer("Results expired. Run health check again.", show_alert=True)
        return
    okList   = cache["ok"]
    deadList = cache["dead"]
    rlList   = cache["rl"]
    errList  = cache["err"]
    phone    = cache["phone"]
    builder  = InlineKeyboardBuilder()
    if okList:   builder.button(text=f"OK  ({len(okList)})",           callback_data="aapi:hccat:ok:0")
    if deadList: builder.button(text=f"Dead  ({len(deadList)})",       callback_data="aapi:hccat:dead:0")
    if rlList:   builder.button(text=f"Rate Limited  ({len(rlList)})", callback_data="aapi:hccat:rl:0")
    if errList:  builder.button(text=f"Errors  ({len(errList)})",      callback_data="aapi:hccat:err:0")
    if deadList: builder.button(text="Skip All Dead",                  callback_data="aapi:skipall_dead")
    builder.button(text="Run Again", callback_data="aapi:health")
    builder.button(text="Back",      callback_data="aapi:menu")
    builder.adjust(2, 2, 1, 2)
    await callback.message.edit_text(
        f"{b('Health Check')}\n"
        f"{c(f'Phone: {phone}')}\n\n"
        f"OK            {c(str(len(okList)))}\n"
        f"Dead          {c(str(len(deadList)))}\n"
        f"Rate limited  {c(str(len(rlList)))}\n"
        f"Errors        {c(str(len(errList)))}\n\n"
        f"{i('Tap a category to browse.')}",
        reply_markup=builder.as_markup(),
        parse_mode=PM
    )
    await callback.answer()


@router.callback_query(F.data.startswith("aapi:hccat:"))
async def cbHcCategory(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    parts      = callback.data.split(":")
    cat        = parts[2]
    page       = int(parts[3])
    cache      = _healthCheckCache.get(f"hc_{callback.from_user.id}")
    if not cache:
        await callback.answer("Results expired.", show_alert=True)
        return
    catMap     = {"ok": cache["ok"], "dead": cache["dead"], "rl": cache["rl"], "err": cache["err"]}
    catLabel   = {"ok": "OK", "dead": "Dead", "rl": "Rate Limited", "err": "Errors"}
    entries    = catMap.get(cat, [])
    total      = len(entries)
    totalPages = max(1, -(-total // HC_PER_PAGE))
    start      = page * HC_PER_PAGE
    builder    = InlineKeyboardBuilder()
    for n, r in enumerate(entries[start:start + HC_PER_PAGE]):
        builder.button(text=f"{r['name']} ({r['method']})", callback_data=f"aapi:hcresult:{cat}:{start + n}")
    if page > 0:
        builder.button(text="Prev", callback_data=f"aapi:hccat:{cat}:{page - 1}")
    if page < totalPages - 1:
        builder.button(text="Next", callback_data=f"aapi:hccat:{cat}:{page + 1}")
    builder.button(text="Back", callback_data="aapi:health_summary")
    builder.adjust(1)
    await callback.answer()
    await callback.message.edit_text(
        f"{b(catLabel[cat] + ' APIs')}  {c(str(total) + ' total')}\n\n{i('Tap an API to see its result.')}",
        reply_markup=builder.as_markup(), parse_mode=PM
    )


@router.callback_query(F.data.startswith("aapi:hcresult:"))
async def cbHcResult(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    parts   = callback.data.split(":")
    cat     = parts[2]
    idx     = int(parts[3])
    cache   = _healthCheckCache.get(f"hc_{callback.from_user.id}")
    if not cache:
        await callback.answer("Results expired.", show_alert=True)
        return
    catMap  = {"ok": cache["ok"], "dead": cache["dead"], "rl": cache["rl"], "err": cache["err"]}
    entries = catMap.get(cat, [])
    if idx >= len(entries):
        await callback.answer("Not found.", show_alert=True)
        return
    r      = entries[idx]
    res    = r["result"]
    name   = r["name"]
    method = r["method"]
    page   = idx // HC_PER_PAGE
    isSkipped = db.isApiSkipped(name)
    if not res["ok"] or res.get("status") is None:
        err  = esc((res.get("error") or "timeout")[:80])
        text = f"{b(esc(name))}  {c(method)}\n\nStatus  {c('DEAD')}\nError   {c(err)}"
    else:
        status  = res["status"]
        latency = res.get("latencyMs", 0)
        snippet = esc((res.get("snippet") or "(empty)")[:100])
        if status == 429:   lbl = "RATE LIMITED"
        elif status < 300:  lbl = "OK"
        elif status < 500:  lbl = "CLIENT ERR"
        else:               lbl = "SERVER ERR"
        text = (
            f"{b(esc(name))}  {c(method)}\n\n"
            f"Status   {c(f'{lbl} {status}')}\n"
            f"Latency  {c(f'{latency}ms')}\n\n"
            f"{i('Response')}\n{c(snippet)}"
        )
    skipLabel = "Enable" if isSkipped else "Skip next time"
    builder   = InlineKeyboardBuilder()
    if cat in ("dead", "err"):
        builder.button(text=skipLabel,    callback_data=f"aapi:hcskip:{cat}:{idx}")
        builder.button(text="Delete API", callback_data=f"aapi:hcdelete:{cat}:{idx}")
        builder.button(text="Back",       callback_data=f"aapi:hccat:{cat}:{page}")
        builder.adjust(2, 1)
    else:
        builder.button(text=skipLabel, callback_data=f"aapi:hcskip:{cat}:{idx}")
        builder.button(text="Back",    callback_data=f"aapi:hccat:{cat}:{page}")
        builder.adjust(1)
    await callback.answer()
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode=PM)


@router.callback_query(F.data.startswith("aapi:hcskip:"))
async def cbHcSkip(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    parts     = callback.data.split(":")
    cat       = parts[2]
    idx       = int(parts[3])
    cache     = _healthCheckCache.get(f"hc_{callback.from_user.id}")
    if not cache:
        await callback.answer("Results expired.", show_alert=True)
        return
    entries   = {"ok": cache["ok"], "dead": cache["dead"], "rl": cache["rl"], "err": cache["err"]}.get(cat, [])
    if idx >= len(entries):
        await callback.answer("Not found.", show_alert=True)
        return
    name      = entries[idx]["name"]
    isSkipped = db.isApiSkipped(name)
    if isSkipped:
        db.unskipApi(name)
        await callback.answer(f"Enabled: {name}")
    else:
        db.skipApi(name)
        await callback.answer(f"Will skip: {name}")
    await cbHcResult(callback)


@router.callback_query(F.data.startswith("aapi:hcdelete:"))
async def cbHcDelete(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    parts   = callback.data.split(":")
    cat     = parts[2]
    idx     = int(parts[3])
    cache   = _healthCheckCache.get(f"hc_{callback.from_user.id}")
    if not cache:
        await callback.answer("Results expired.", show_alert=True)
        return
    entries = {"ok": cache["ok"], "dead": cache["dead"], "rl": cache["rl"], "err": cache["err"]}.get(cat, [])
    if idx >= len(entries):
        await callback.answer("Not found.", show_alert=True)
        return
    name    = entries[idx]["name"]
    allApis = getMergedTagged()
    api     = next((a for a in allApis if a["name"] == name and a.get("_dbId")), None)
    if not api:
        await callback.answer("Base APIs cannot be deleted — use Skip instead.", show_alert=True)
        return
    db.deleteCustomApi(api["_dbId"])
    apiManager.invalidateCache()
    entries.pop(idx)
    await callback.answer(f"Deleted: {name}")
    page = idx // HC_PER_PAGE
    await callback.message.edit_text(
        f"{b('Deleted')}  {c(esc(name))}\n\n{i('API removed.')}",
        reply_markup=InlineKeyboardBuilder().button(
            text="Back", callback_data=f"aapi:hccat:{cat}:{page}"
        ).adjust(1).as_markup(),
        parse_mode=PM
    )