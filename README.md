# Maxi_VPN_bot

**Maxi_VPN_bot** is a comprehensive Telegram bot designed to manage VPN subscriptions powered by [X-UI](https://github.com/MHSanaei/3x-ui) (VLESS + Reality). It provides a seamless user experience for purchasing, managing, and connecting to a secure VPN service using Telegram Stars for payments.

## 🚀 Features

### User Features
-   **Automated Purchases**: Buy subscriptions (1 Month, 3 Months, 1 Year) instantly using **Telegram Stars**.
-   **Subscription Management**:
    -   View current subscription status (expiry date, traffic usage).
    -   Extend existing subscriptions.
    -   Receive expiry warnings (24h before).
-   **Connection Keys**: One-click retrieval of VLESS+Reality connection strings (subscription URL).
-   **Cross-Platform Support**: Detailed setup instructions for:
    -   📱 Android (v2RayTun)
    -   🍎 iOS (V2Box)
    -   💻 PC (AmneziaVPN / Hiddify)
-   **Referral System**: Invite users and earn bonuses/extensions.
-   **Promo Codes**: Redeem codes for free trial days.
-   **Free Trial**: 3-day free trial for new users.
-   **Multi-language**: Support for Russian 🇷🇺 and English 🇬🇧.

### Admin Panel
Accessible only to the administrator, offering full control over the service:
-   **📊 Statistics**: View server load (CPU, RAM, Disk).
-   **💰 Price Management**: Adjust prices for all subscription tiers dynamically.
-   **📜 Sales Log**: View the last 20 transactions (User, Plan, Amount, Time).
-   **🎁 Promo Codes**: Generate new promo codes with custom duration and usage limits.
-   **📢 Broadcast**: Send messages to all bot users.
-   **👥 User Management**:
    -   Search users by ID.
    -   Reset trial status.
    -   Delete users from the database.
    -   Rebind users.
-   **🖥 Server Status**: Real-time monitoring of system resources.

## 🛠 Installation & Setup

### Prerequisites
-   Linux Server (Ubuntu/Debian recommended)
-   Python 3.9+
-   X-UI (MHSanaei fork) installed
-   Telegram Bot Token

### Configuration
The bot is configured via `bot.py`. Key variables:
-   `TOKEN`: Your Telegram Bot Token.
-   `ADMIN_ID`: Telegram ID of the admin.
-   `DB_PATH`: Path to X-UI database.
-   `BOT_DB_PATH`: Path to the bot's internal database.

### Running the Bot
The bot runs as a systemd service:
```bash
systemctl start x-ui-bot
systemctl enable x-ui-bot
```

## 📂 Project Structure
-   `bot.py`: Main application logic.
-   `bot_data.db`: SQLite database for user preferences, transactions, and promos.
-   `/etc/x-ui/x-ui.db`: External connection to X-UI database for client management.

## 📝 Recent Updates
-   **Sales Log**: Added a dedicated menu to view recent sales history directly in the admin panel.
-   **Admin Notifications**: Real-time notifications to the admin for every new purchase.
-   **Stability**: Improved error handling and keyboard navigation.

## 📜 License
Private / Proprietary
