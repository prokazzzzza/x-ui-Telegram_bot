import sys
import asyncio
import importlib
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

bot_path = "/usr/local/x-ui/bot"
if bot_path not in sys.path:
    sys.path.append(bot_path)

sys.modules['sqlite3'] = MagicMock()

bot = importlib.import_module("bot")
 
@pytest.mark.asyncio
async def test_support_full_cycle():
    """
    Test the full cycle of support:
    1. User sends support message via Main Bot.
    2. Main Bot forwards it to Admin via Support Bot.
    3. Admin replies via Support Bot.
    4. Support Bot forwards reply to User via Main Bot.
    """
    
    # --- Setup Mocks ---
    
    # Mock Context
    context = MagicMock()
    context.bot = AsyncMock() # Main Bot
    context.user_data = {}
    context.bot_data = {}
    
    # Mock Support Bot
    support_bot = AsyncMock()
    context.bot_data['support_bot'] = support_bot
    bot.ADMIN_ID = "123456789"
    bot.ADMIN_ID_INT = 123456789
    
    # Mock Main Bot in Support Bot's data (for reply handler)
    # In reality, they are separate apps, but handlers receive their own context.
    # For handle_message (Main Bot), context has support_bot.
    # For admin_bot_reply_handler (Support Bot), context has main_bot.
    
    # --- Step 1: User sends support message ---
    
    # User info
    user_id = 123456789
    username = "test_user"
    
    # Mock Update for Main Bot
    update_user = MagicMock()
    update_user.message = MagicMock()
    update_user.message.reply_text = AsyncMock() # Must be awaitable
    update_user.message.from_user.id = user_id
    update_user.message.from_user.username = username
    update_user.message.from_user.first_name = "Test"
    update_user.message.from_user.last_name = "User"
    update_user.message.text = "Help me please!"
    update_user.message.photo = [] # Ensure it is treated as text message
    update_user.message.reply_to_message = None # Not a reply
    
    # Set state to awaiting support message
    context.user_data['admin_action'] = 'awaiting_support_message'
    
    # Mock save_support_ticket to avoid DB calls
    with patch('bot.save_support_ticket') as mock_save:
        # Call handler
        await bot.handle_message(update_user, context)
        
        # Verify support ticket saved
        mock_save.assert_called_with(str(user_id), "Help me please!")

    # --- Step 2: Verify forwarding to Admin ---
    
    # Verify support_bot.send_message was called
    # The code sends 2 messages: content and hint
    assert support_bot.send_message.call_count >= 1
    
    # Check arguments of the first call (the content)
    args, kwargs = support_bot.send_message.call_args_list[0]
    
    # Admin ID should be target
    assert kwargs['chat_id'] == bot.ADMIN_ID
    
    # Message text should contain user info and text
    sent_text = kwargs['text']
    assert f"@{username}" in sent_text
    assert str(user_id) in sent_text
    assert "Help me please!" in sent_text
    assert "🆘" in sent_text # Alert emoji
    
    # --- Step 3: Admin replies ---
    
    # Admin context (Support Bot)
    admin_context = MagicMock()
    admin_context.bot = support_bot
    admin_context.bot_data = {'main_bot': context.bot} # Link Main Bot
    
    # Mock Update for Support Bot (Admin Reply)
    update_admin = MagicMock()
    update_admin.message = MagicMock() # Ensure message is mock
    update_admin.message.reply_text = AsyncMock() # Must be awaitable
    update_admin.message.chat_id = int(bot.ADMIN_ID)
    update_admin.message.text = "Here is the solution."
    
    # Mock the message Admin is replying TO (The alert from Step 2)
    # It must match the pattern `User: @name (123456789)`
    reply_to = MagicMock()
    reply_to.text = sent_text # Use the text we verified above
    reply_to.caption = None
    update_admin.message.reply_to_message = reply_to
    
    # Call handler
    await bot.admin_bot_reply_handler(update_admin, admin_context)
    
    # --- Step 4: Verify reply to User ---
    
    # Verify main_bot.send_message was called
    context.bot.send_message.assert_called_once()
    
    # Check arguments
    args, kwargs = context.bot.send_message.call_args
    
    assert kwargs['chat_id'] == str(user_id)
    assert "Here is the solution." in kwargs['text']
    assert "🔔" in kwargs['text'] # Notification emoji from support_reply_template
    
    print("\n✅ Full Support Cycle Test Passed!")

if __name__ == "__main__":
    # Allow running directly
    import asyncio
    asyncio.run(test_support_full_cycle())
