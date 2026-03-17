from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def mainMenuKeyboard(testsToday: int = 0, dailyLimit: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    badge = f"Start Test  [{testsToday}/{dailyLimit}]" if dailyLimit else "Start Test"
    builder.button(text=badge,       callback_data="menu:start_test")
    builder.button(text="My History", callback_data="menu:history")
    builder.button(text="AI Chat",    callback_data="menu:ai_chat")
    builder.button(text="Settings",   callback_data="menu:config")
    builder.button(text="Help",       callback_data="menu:help")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def durationKeyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="30s",    callback_data="dur:30")
    builder.button(text="1 min",  callback_data="dur:60")
    builder.button(text="5 min",  callback_data="dur:300")
    builder.button(text="10 min", callback_data="dur:600")
    builder.button(text="Custom duration", callback_data="dur:custom")
    builder.button(text="Back",   callback_data="nav:main_menu")
    builder.adjust(4, 1, 1)
    return builder.as_markup()


def workersKeyboard(hasDuration: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="2 workers",  callback_data="wrk:2")
    builder.button(text="4 workers",  callback_data="wrk:4")
    builder.button(text="8 workers",  callback_data="wrk:8")
    builder.button(text="16 workers", callback_data="wrk:16")
    builder.button(text="Custom workers", callback_data="wrk:custom")
    builder.button(text="Back",           callback_data="nav:wizard_duration")
    builder.adjust(4, 1, 1)
    return builder.as_markup()


def proxyKeyboard(hasProxies: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="No proxy",  callback_data="proxy:none")
    if hasProxies:
        builder.button(text="Use proxy", callback_data="proxy:file")
    builder.button(text="Back", callback_data="nav:wizard_workers")
    builder.adjust(2 if hasProxies else 1, 1)
    return builder.as_markup()


def confirmKeyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Launch", callback_data="confirm:start")
    builder.button(text="Edit",   callback_data="confirm:edit")
    builder.button(text="Cancel", callback_data="confirm:cancel")
    builder.adjust(2, 1)
    return builder.as_markup()


def runningKeyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Stop Test", callback_data="test:stop")
    builder.adjust(1)
    return builder.as_markup()


def finishedKeyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Repeat Test", callback_data="test:repeat")
    builder.button(text="New Test",    callback_data="menu:start_test")
    builder.button(text="Main Menu",   callback_data="nav:main_menu")
    builder.adjust(2, 1)
    return builder.as_markup()


def configKeyboard(defaultWorkers: int, proxyEnabled: bool) -> InlineKeyboardMarkup:
    proxyLabel = "Proxy default: ON" if proxyEnabled else "Proxy default: OFF"
    builder = InlineKeyboardBuilder()
    builder.button(text=f"Default workers: {defaultWorkers}", callback_data="cfg:workers")
    builder.button(text=proxyLabel,                           callback_data="cfg:toggle_proxy")
    builder.button(text="Main Menu",                          callback_data="nav:main_menu")
    builder.adjust(1)
    return builder.as_markup()


def configWorkersKeyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for w in [2, 4, 8, 16]:
        builder.button(text=str(w), callback_data=f"cfg:set_workers:{w}")
    builder.button(text="Back", callback_data="cfg:back")
    builder.adjust(4, 1)
    return builder.as_markup()


def backToMainKeyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Main Menu", callback_data="nav:main_menu")
    builder.adjust(1)
    return builder.as_markup()


def aiChatKeyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Clear Chat", callback_data="ai:clear")
    builder.button(text="Main Menu",  callback_data="nav:main_menu")
    builder.adjust(2)
    return builder.as_markup()