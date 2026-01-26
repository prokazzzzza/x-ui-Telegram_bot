import sys
import inspect

# Add bot directory to path
sys.path.append('/usr/local/x-ui/bot')

def test_handler_functions_exist():
    """
    Test that critical handler functions are defined in the bot module.
    This ensures we don't accidentally rename or delete them without updating registration.
    """
    import bot
    
    handlers = [
        'start',
        'admin_panel',
        'admin_stats',
        'admin_user_detail',
        'admin_edit_limit_ip', # The one we just fixed
        'admin_poll_menu',
        'handle_poll_vote',
        'admin_ip_history'
    ]
    
    for handler_name in handlers:
        assert hasattr(bot, handler_name), f"Handler function {handler_name} not found in bot module"
        assert inspect.iscoroutinefunction(getattr(bot, handler_name)), f"{handler_name} should be an async function"
