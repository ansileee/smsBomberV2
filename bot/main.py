import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import BOT_TOKEN
from bot.handlers import start, test_flow, dashboard, admin, admin_apis, admin_proxy, beta
from bot.middleware.auth import AuthMiddleware
from bot.services.scheduler import midnightResetLoop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def main() -> None:
    bot = Bot(token=BOT_TOKEN)
    dp  = Dispatcher(storage=MemoryStorage())

    dp.update.middleware(AuthMiddleware())

    dp.include_router(start.router)
    dp.include_router(test_flow.router)
    dp.include_router(dashboard.router)
    dp.include_router(admin.router)
    dp.include_router(admin_apis.router)
    dp.include_router(admin_proxy.router)
    dp.include_router(beta.router)

    logger.info("Bot starting...")
    try:
        asyncio.get_event_loop().create_task(midnightResetLoop())
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        await bot.session.close()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    asyncio.run(main())