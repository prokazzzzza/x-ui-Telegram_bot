import sys
import os
import pytest
from unittest.mock import MagicMock, ANY

# Add bot directory to path
sys.path.append('/usr/local/x-ui/bot')

# Mock environment variables
os.environ['BOT_TOKEN'] = '123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11'
os.environ['ADMIN_ID'] = '123456789'

import bot

def test_handler_registration():
    """
    Verify that all critical handlers, including new ones, are correctly registered.
    This requires refactoring the main block of bot.py to use a register_handlers function.
    """
    # Check if register_handlers exists (it should be added by the developer)
    if not hasattr(bot, 'register_handlers'):
        pytest.fail("bot.register_handlers function is missing. Please refactor bot.py to allow testing handler registration.")

    app_mock = MagicMock()
    bot.register_handlers(app_mock)
    
    # Helper to check if a handler with specific pattern was registered
    def has_handler_with_pattern(pattern_str):
        # app_mock.add_handler is called multiple times.
        # call_args_list contains all calls.
        # Each call is (args, kwargs). args[0] is the handler object.
        for call in app_mock.add_handler.call_args_list:
            handler = call[0][0]
            # Check if it's a CallbackQueryHandler (or similar) and has 'pattern'
            if hasattr(handler, 'pattern') and handler.pattern:
                # handler.pattern is a regex object or string depending on implementation.
                # In python-telegram-bot, it's usually compiled regex or string.
                # We'll check the string representation
                if pattern_str in str(handler.pattern):
                    return True
        return False

    # Check for existing handlers
    assert has_handler_with_pattern('admin_panel'), "admin_panel handler not registered"
    assert has_handler_with_pattern('admin_edit_limit_ip'), "admin_edit_limit_ip handler not registered"
    
    # Check for NEW functionality
    assert has_handler_with_pattern('admin_ip_history'), "admin_ip_history handler not registered! Button will not work."

