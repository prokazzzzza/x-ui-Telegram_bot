import asyncio
import os
from dotenv import load_dotenv
from telegram import Bot

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

async def main():
    bot = Bot(token=TOKEN)
    target_id = 1347093069
    print(f"Testing get_chat for {target_id}...")
    try:
        chat = await bot.get_chat(target_id)
        print(f"Success!")
        print(f"Username: {chat.username}")
        print(f"First Name: {chat.first_name}")
        print(f"Last Name: {chat.last_name}")
        print(f"Type: {chat.type}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
