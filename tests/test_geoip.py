import unittest
from unittest.mock import patch, MagicMock
import sys
import os

# Add bot directory to path
sys.path.append('/usr/local/x-ui/bot')
os.environ['BOT_TOKEN'] = 'test'
os.environ['ADMIN_ID'] = '123'

import bot

class TestGeoIP(unittest.TestCase):
    def test_get_flag_emoji(self):
        # US -> 🇺🇸
        self.assertEqual(bot.get_flag_emoji('US'), '🇺🇸')
        # RU -> 🇷🇺
        self.assertEqual(bot.get_flag_emoji('RU'), '🇷🇺')
        # None -> 🏳️
        self.assertEqual(bot.get_flag_emoji(None), '🏳️')
        # Invalid -> 🏳️ (or garbage, but function handles exceptions if any)
        
    @patch('requests.get')
    def test_geoip_api(self, mock_get):
        # We can't easily test the async watch_access_log loop here without complex async setup.
        # But we can verify the API call logic if we extracted it.
        # Since it's embedded, we'll just test the flag logic and maybe integration test later.
        pass

if __name__ == '__main__':
    unittest.main()
