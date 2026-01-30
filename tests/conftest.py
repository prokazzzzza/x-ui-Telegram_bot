import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_bot_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bot_dir = "/usr/local/x-ui/bot"
    if bot_dir not in sys.path:
        sys.path.append(bot_dir)

    import bot

    log_path = tmp_path / "bot_test.log"
    monkeypatch.setattr(bot, "LOG_FILE", str(log_path))
