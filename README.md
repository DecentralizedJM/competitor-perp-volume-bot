# 📊 Perpetual Volume Fetcher Bot

A Telegram bot to instantly query Bybit & Binance USDT perpetual futures volumes. All day boundaries and timestamps are aligned to **India Standard Time (IST)**.

## 🚀 Features

- **Aggregate Volume**: Get the total 24h volume across all USDT perpetual pairs for both Bybit and Binance.
- **Historical Queries**: Query volume for a specific date or a date range (up to 30 days for aggregate).
- **Per-Token Volume**: Check the volume of any specific token (e.g., `/ETH`, `/BTC`, `/SOL`).
- **IST Day Boundaries**: All daily volumes are calculated from 00:00 to 23:59 IST.
- **Accurate Historical Data**: Using hourly klines to ensure IST day parity for per-token historical lookups.
- **Live Data**: Today's data is fetched using real-time 24h rolling tickers.
- **Async & Fast**: Built with `aiohttp` for concurrent API requests.

## 🛠 Commands

| Command | Example | Description |
|---------|---------|-------------|
| `/volume` | `/volume` | Today's total 24h USDT perp volume (IST) |
| `/volume ddmmyy` | `/volume 150226` | Total volume for 15 Feb 2026 |
| `/volume ddmmyy-ddmmyy` | `/volume 100226-180226` | Daily breakdown for a range |
| `/<TOKEN>` | `/ETH` | Today's 24h volume for ETHUSDT |
| `/<TOKEN> ddmmyy` | `/BTC 150226` | BTC volume on a specific date |
| `/<TOKEN> ddmmyy-ddmmyy` | `/SOL 100226-180226` | SOL daily range (IST) |

## 📦 Installation & Setup

### 1. Requirements
- Python 3.9+
- A Telegram Bot Token from [@BotFather](https://t.me/BotFather)

### 2. Clone the Repository
```bash
git clone https://github.com/DecentralizedJM/competitor-perp-volume-bot.git
cd competitor-perp-volume-bot
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configuration
Create a `.env` file in the root directory:
```bash
cp .env.example .env
```
Edit `.env` and add your bot token:
```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
```

### 5. Run the Bot
```bash
python3 volume_bot.py
```

## 🚂 Deploy to Railway

1. **Fork/Clone** this repository to your GitHub.
2. Log in to [Railway](https://railway.app/).
3. Create a **New Project** → **Deploy from GitHub repo**.
4. Select your `competitor-perp-volume-bot` repository.
5. In the Railway dashboard for your service:
   - Go to **Variables**.
   - Add `TELEGRAM_BOT_TOKEN` with your bot token.
   - (Optional) Verify that the **Start Command** is detected as `worker: python3 volume_bot.py` or manually set it in **Settings**.
6. Deployment will start automatically. The bot runs as a background worker.

## 📊 CLI Tool
Also included is a standalone CLI script `perp_volume.py` for local usage without Telegram.

```bash
python3 perp_volume.py --top 20
python3 perp_volume.py --from 2026-02-10 --to 2026-02-18 --symbols BTCUSDT ETHUSDT
```

## ⚖️ License
MIT
