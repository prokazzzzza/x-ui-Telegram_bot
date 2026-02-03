from __future__ import annotations

import argparse
import asyncio
import importlib
import os
import sys
from types import ModuleType


def _ensure_minimal_env() -> None:
    os.environ.setdefault("BOT_TOKEN", "0:SMOKE_TEST_TOKEN")
    os.environ.setdefault("ADMIN_ID", "0")
    os.environ.setdefault("HOST_IP", "127.0.0.1")
    os.environ.setdefault("BOT_NAME", "smoke")
    os.environ.setdefault("BOT_DB_PATH", "/tmp/x-ui-bot_smoke.db")
    os.environ.setdefault("BOT_LOG_FILE", "/tmp/x-ui-bot_smoke.log")


def _import_bot_module() -> ModuleType:
    candidates = ("bot", "bot.bot")
    last_error: Exception | None = None
    for name in candidates:
        try:
            module = importlib.import_module(name)
        except Exception as ex:
            last_error = ex
            continue

        if getattr(module, "register_handlers", None) is not None and getattr(module, "main", None) is not None:
            return module

    if last_error is not None:
        raise last_error
    raise RuntimeError("Не удалось импортировать модуль бота")


def smoke_check() -> None:
    _ensure_minimal_env()

    bot_module = _import_bot_module()

    from telegram.ext import ApplicationBuilder

    token = str(getattr(bot_module, "TOKEN", os.environ["BOT_TOKEN"]))
    app = ApplicationBuilder().token(token).build()

    register_handlers = getattr(bot_module, "register_handlers", None)
    if register_handlers is None:
        raise RuntimeError("bot.register_handlers не найден")
    register_handlers(app)

    container = getattr(bot_module, "build_container", None)
    prices_provider = getattr(bot_module, "get_prices", None)
    if container is not None and prices_provider is not None:
        container(prices_provider=prices_provider)


def run(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args(argv)

    if args.smoke:
        smoke_check()
        print("SMOKE OK")
        return

    bot_module = _import_bot_module()
    main = getattr(bot_module, "main", None)
    if main is None:
        raise RuntimeError("bot.main не найден")
    asyncio.run(main())


if __name__ == "__main__":
    run(sys.argv[1:])
