from __future__ import annotations

from aiogram import Router, F
from aiogram.types import CallbackQuery

from bot.keyboards.menus import configKeyboard, configWorkersKeyboard, backToMainKeyboard
from bot.config import DEFAULT_WORKERS
from bot.utils import PM, b, i, c

router = Router()

cfg = {
    "defaultWorkers": DEFAULT_WORKERS,
    "proxyEnabled":   False,
}


def getCfg() -> dict:
    return cfg


@router.callback_query(F.data == "menu:config")
async def cbConfig(callback: CallbackQuery) -> None:
    await showConfig(callback)


async def showConfig(callback: CallbackQuery) -> None:
    proxy = "Enabled" if cfg["proxyEnabled"] else "Disabled"
    text  = (
        f"{b('Settings')}\n\n"
        f"Default workers   {c(str(cfg['defaultWorkers']))}\n"
        f"Proxy by default  {c(proxy)}"
    )
    try:
        await callback.message.edit_text(
            text,
            reply_markup=configKeyboard(cfg["defaultWorkers"], cfg["proxyEnabled"]),
            parse_mode=PM
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "cfg:workers")
async def cbCfgWorkers(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        f"{b('Default Workers')}\n\nSelect the default number of concurrent workers.",
        reply_markup=configWorkersKeyboard(),
        parse_mode=PM
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cfg:set_workers:"))
async def cbCfgSetWorkers(callback: CallbackQuery) -> None:
    workers = int(callback.data.split(":")[2])
    cfg["defaultWorkers"] = workers
    await callback.answer(f"Default workers set to {workers}.")
    await showConfig(callback)


@router.callback_query(F.data == "cfg:toggle_proxy")
async def cbCfgToggleProxy(callback: CallbackQuery) -> None:
    cfg["proxyEnabled"] = not cfg["proxyEnabled"]
    status = "enabled" if cfg["proxyEnabled"] else "disabled"
    await callback.answer(f"Proxy default {status}.")
    await showConfig(callback)


@router.callback_query(F.data == "cfg:back")
async def cbCfgBack(callback: CallbackQuery) -> None:
    await showConfig(callback)