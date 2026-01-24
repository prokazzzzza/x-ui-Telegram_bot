import sys
import os
import pytest
import asyncio
from unittest.mock import MagicMock, patch

# Add bot directory to path
sys.path.append('/usr/local/x-ui/bot')

# Mock environment variables to ensure bot imports correctly
os.environ['BOT_TOKEN'] = '123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11'
os.environ['ADMIN_ID'] = '123456789'

import bot

def test_post_init_logic():
    """
    Test that post_init runs correctly and schedules background tasks.
    This simulates the startup phase of the bot.
    """
    async def run_test():
        # Mock the Application object
        app_mock = MagicMock()
        
        # Mock bot methods which are awaited
        app_mock.bot.set_my_commands.return_value = asyncio.Future()
        app_mock.bot.set_my_commands.return_value.set_result(True)
        
        app_mock.bot.set_my_description.return_value = asyncio.Future()
        app_mock.bot.set_my_description.return_value.set_result(True)
        
        app_mock.bot.set_my_short_description.return_value = asyncio.Future()
        app_mock.bot.set_my_short_description.return_value.set_result(True)
        
        # Mock asyncio.create_task to verify it's called
        with patch('asyncio.create_task') as mock_create_task:
            await bot.post_init(app_mock)
            
            # Verify that watch_access_log task was scheduled
            assert mock_create_task.called
            
    asyncio.run(run_test())

def test_critical_handlers_exist():
    """
    Ensure critical handler functions are defined.
    """
    handlers = [
        'start',
        'admin_panel',
        'admin_edit_limit_ip',
        'admin_ip_history',
        'watch_access_log'
    ]
    
    for h in handlers:
        assert hasattr(bot, h), f"Handler {h} is missing from bot module"

def test_watch_access_log_structure():
    """
    Verify watch_access_log is an async function.
    """
    import inspect
    assert inspect.iscoroutinefunction(bot.watch_access_log)
