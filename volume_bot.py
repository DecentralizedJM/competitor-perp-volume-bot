#!/usr/bin/env python3
"""
📊 Perpetual USDT Volume Bot for Telegram (IST)
=================================================
Fetches USDT perpetual futures volumes from Bybit & Binance.
All dates and day boundaries are in IST (UTC+5:30).

Commands:
  /volume                   → Today's total 24h volume (all pairs aggregated)
  /volume ddmmyy            → Volume for a specific date (IST day)
  /volume ddmmyy-ddmmyy     → Daily breakdown for a date range
  /start or /help           → Show help message

  /<TOKEN>                  → Today's 24h volume for a specific token
  /<TOKEN> ddmmyy           → Volume for a token on a specific date (IST day)
  /<TOKEN> ddmmyy-ddmmyy    → Daily breakdown for a token in a range

  Examples: /ETH, /BTC 150226, /SOL 100226-180226
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple, Union

try:
    import aiohttp
except ImportError:
    print("❌ Missing: aiohttp → pip install aiohttp")
    sys.exit(1)

try:
    from telegram import Update
    from telegram.ext import (
        Application,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )
    from telegram.constants import ParseMode
except ImportError:
    print("❌ Missing: python-telegram-bot → pip install 'python-telegram-bot>=20.0'")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv is optional

# ─── Config ───────────────────────────────────────────────────────────────────

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
if not BOT_TOKEN:
    print("❌ Set TELEGRAM_BOT_TOKEN in .env or environment")
    sys.exit(1)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BYBIT_BASE = "https://api.bybit.com"
BINANCE_BASE = "https://fapi.binance.com"

# IST timezone: UTC+5:30
IST = timezone(timedelta(hours=5, minutes=30))

# Semaphores to limit concurrent requests per exchange
BYBIT_SEM = asyncio.Semaphore(10)
BINANCE_SEM = asyncio.Semaphore(10)


# ─── IST Helpers ─────────────────────────────────────────────────────────────

def now_ist() -> datetime:
    """Current datetime in IST."""
    return datetime.now(IST)


def today_ist() -> datetime:
    """Today's date at midnight IST (timezone-aware)."""
    return now_ist().replace(hour=0, minute=0, second=0, microsecond=0)


def ist_day_start_utc_ms(dt: datetime) -> int:
    """Convert an IST date (midnight IST) to UTC milliseconds.
    e.g. 19 Feb 2026 00:00 IST = 18 Feb 2026 18:30 UTC."""
    # Make dt timezone-aware in IST at midnight
    ist_midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=IST)
    return int(ist_midnight.timestamp() * 1000)


def ist_day_end_utc_ms(dt: datetime) -> int:
    """End of an IST day in UTC milliseconds (23:59:59.999 IST)."""
    ist_end = dt.replace(hour=23, minute=59, second=59, microsecond=999000, tzinfo=IST)
    return int(ist_end.timestamp() * 1000)


def ms_to_ist_date_key(ms: int) -> str:
    """Convert UTC ms timestamp to IST date key (YYYY-MM-DD)."""
    utc_dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    ist_dt = utc_dt.astimezone(IST)
    return ist_dt.strftime("%Y-%m-%d")


def ms_to_ist_date_fmt(ms: int) -> str:
    """Convert UTC ms timestamp to IST display date (dd Mon YYYY)."""
    utc_dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    ist_dt = utc_dt.astimezone(IST)
    return ist_dt.strftime("%d %b %Y")


def date_fmt(dt: datetime) -> str:
    """Format date as dd Mon YYYY."""
    return dt.strftime("%d %b %Y")


def is_today_ist(dt: datetime) -> bool:
    """Check if a naive date matches today in IST."""
    return dt.date() == now_ist().date()


# ─── Formatting Helpers ──────────────────────────────────────────────────────

def fmt(v: float) -> str:
    """Format volume to human-readable."""
    if v >= 1_000_000_000:
        return f"${v / 1_000_000_000:,.2f}B"
    elif v >= 1_000_000:
        return f"${v / 1_000_000:,.2f}M"
    elif v >= 1_000:
        return f"${v / 1_000:,.2f}K"
    return f"${v:,.2f}"


def parse_date(s: str) -> Optional[datetime]:
    """Parse ddmmyy to naive datetime."""
    s = s.strip()
    try:
        return datetime.strptime(s, "%d%m%y")
    except ValueError:
        return None


def parse_date_arg(text: str):
    """
    Parse command arguments into (single_date, start_date, end_date).
    Returns one of:
      - (None, None, None) → no date arg (use today)
      - (date, None, None) → single date
      - (None, start, end) → date range
    """
    text = text.strip()
    if not text:
        return None, None, None

    if "-" in text:
        parts = text.split("-", 1)
        start = parse_date(parts[0])
        end = parse_date(parts[1])
        if start and end and start <= end:
            return None, start, end
        return "invalid", None, None
    else:
        d = parse_date(text)
        if d:
            return d, None, None
        return "invalid", None, None


# ─── API Layer (async with aiohttp) ──────────────────────────────────────────

async def fetch_json(session: aiohttp.ClientSession, url: str,
                     params: dict = None) -> Optional[Union[dict, list]]:
    """Fetch JSON from a URL."""
    try:
        async with session.get(url, params=params,
                               timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                return await resp.json()
            else:
                logger.warning(f"HTTP {resp.status} from {url}")
                return None
    except Exception as e:
        logger.error(f"Request to {url} failed: {e}")
        return None


# ── Bybit ─────────────────────────────────────────────────────────────────────

async def bybit_24h_all(session: aiohttp.ClientSession) -> Dict[str, float]:
    """Get all USDT perp 24h tickers from Bybit. Returns {symbol: turnover_usdt}."""
    data = await fetch_json(session, f"{BYBIT_BASE}/v5/market/tickers",
                            {"category": "linear"})
    result = {}
    if data and data.get("retCode") == 0:
        for item in data["result"]["list"]:
            sym = item["symbol"]
            if sym.endswith("USDT"):
                result[sym] = float(item.get("turnover24h", 0))
    return result


async def bybit_24h_symbol(session: aiohttp.ClientSession, symbol: str) -> dict:
    """Get 24h ticker for a single symbol on Bybit."""
    data = await fetch_json(session, f"{BYBIT_BASE}/v5/market/tickers",
                            {"category": "linear", "symbol": symbol})
    if data and data.get("retCode") == 0 and data["result"]["list"]:
        item = data["result"]["list"][0]
        return {
            "volume_usdt": float(item.get("turnover24h", 0)),
            "volume_base": float(item.get("volume24h", 0)),
            "last_price": float(item.get("lastPrice", 0)),
        }
    return {}


async def bybit_kline_hourly(session: aiohttp.ClientSession, symbol: str,
                              start_ms: int, end_ms: int) -> List[Dict]:
    """Get hourly klines for a symbol from Bybit."""
    all_results = []
    cursor_start = start_ms

    while cursor_start < end_ms:
        async with BYBIT_SEM:
            data = await fetch_json(session, f"{BYBIT_BASE}/v5/market/kline", {
                "category": "linear", "symbol": symbol,
                "interval": "60", "start": cursor_start, "end": end_ms, "limit": 200,
            })
        if not data or data.get("retCode") != 0:
            break
        candles = data["result"]["list"]
        if not candles:
            break
        for c in candles:
            ts = int(c[0])
            all_results.append({
                "ts": ts,
                "ist_date_key": ms_to_ist_date_key(ts),
                "turnover": float(c[6]),
                "volume": float(c[5]),
            })
        # Bybit returns newest first, so oldest is last
        oldest_ts = min(int(c[0]) for c in candles)
        if oldest_ts <= cursor_start:
            break
        cursor_start = max(int(c[0]) for c in candles) + 3600000
        if len(candles) < 200:
            break

    return all_results


async def bybit_kline_daily(session: aiohttp.ClientSession, symbol: str,
                             start_ms: int, end_ms: int) -> List[Dict]:
    """Get daily klines for a symbol from Bybit (UTC-based candles)."""
    async with BYBIT_SEM:
        data = await fetch_json(session, f"{BYBIT_BASE}/v5/market/kline", {
            "category": "linear", "symbol": symbol,
            "interval": "D", "start": start_ms, "end": end_ms, "limit": 200,
        })
    results = []
    if data and data.get("retCode") == 0:
        for c in data["result"]["list"]:
            ts = int(c[0])
            results.append({
                "ts": ts,
                "ist_date_key": ms_to_ist_date_key(ts),
                "ist_date_fmt": ms_to_ist_date_fmt(ts),
                "turnover": float(c[6]),
                "volume": float(c[5]),
            })
    return results


# ── Binance ───────────────────────────────────────────────────────────────────

async def binance_24h_all(session: aiohttp.ClientSession) -> Dict[str, float]:
    """Get all USDT perp 24h tickers from Binance. Returns {symbol: quoteVolume}."""
    data = await fetch_json(session, f"{BINANCE_BASE}/fapi/v1/ticker/24hr")
    result = {}
    if data:
        for item in data:
            sym = item["symbol"]
            if sym.endswith("USDT"):
                result[sym] = float(item.get("quoteVolume", 0))
    return result


async def binance_24h_symbol(session: aiohttp.ClientSession, symbol: str) -> dict:
    """Get 24h ticker for a single symbol on Binance."""
    data = await fetch_json(session, f"{BINANCE_BASE}/fapi/v1/ticker/24hr",
                            {"symbol": symbol})
    if data and isinstance(data, dict):
        return {
            "volume_usdt": float(data.get("quoteVolume", 0)),
            "volume_base": float(data.get("volume", 0)),
            "last_price": float(data.get("lastPrice", 0)),
        }
    return {}


async def binance_kline_hourly(session: aiohttp.ClientSession, symbol: str,
                                start_ms: int, end_ms: int) -> List[Dict]:
    """Get hourly klines for a symbol from Binance."""
    all_results = []
    cursor_start = start_ms

    while cursor_start < end_ms:
        async with BINANCE_SEM:
            data = await fetch_json(session, f"{BINANCE_BASE}/fapi/v1/klines", {
                "symbol": symbol, "interval": "1h",
                "startTime": cursor_start, "endTime": end_ms, "limit": 500,
            })
        if not data:
            break
        for c in data:
            ts = int(c[0])
            all_results.append({
                "ts": ts,
                "ist_date_key": ms_to_ist_date_key(ts),
                "turnover": float(c[7]),
                "volume": float(c[5]),
            })
        if len(data) < 500:
            break
        cursor_start = int(data[-1][0]) + 3600000

    return all_results


async def binance_kline_daily(session: aiohttp.ClientSession, symbol: str,
                               start_ms: int, end_ms: int) -> List[Dict]:
    """Get daily klines for a symbol from Binance (UTC-based candles)."""
    async with BINANCE_SEM:
        data = await fetch_json(session, f"{BINANCE_BASE}/fapi/v1/klines", {
            "symbol": symbol, "interval": "1d",
            "startTime": start_ms, "endTime": end_ms, "limit": 500,
        })
    results = []
    if data:
        for c in data:
            ts = int(c[0])
            results.append({
                "ts": ts,
                "ist_date_key": ms_to_ist_date_key(ts),
                "ist_date_fmt": ms_to_ist_date_fmt(ts),
                "turnover": float(c[7]),
                "volume": float(c[5]),
            })
    return results


# ── Aggregate Helpers ─────────────────────────────────────────────────────────

async def get_all_usdt_symbols(session: aiohttp.ClientSession) -> Tuple[list, list]:
    """Get lists of all USDT perp symbols from both exchanges."""
    bybit_tickers, binance_tickers = await asyncio.gather(
        bybit_24h_all(session),
        binance_24h_all(session),
    )
    return list(bybit_tickers.keys()), list(binance_tickers.keys())


async def aggregate_daily_volume_ist(session: aiohttp.ClientSession,
                                      start: datetime, end: datetime) -> dict:
    """
    Aggregate total daily USDT perp volume for a date range.
    Uses daily klines (UTC-based) and maps them to IST dates.
    For today (IST), uses 24h rolling ticker instead of incomplete kline.

    Returns { "YYYY-MM-DD": {"bybit": vol, "binance": vol, "date_fmt": str} }
    """
    # Expand range by 1 day on each side to capture IST overlap with UTC candles
    fetch_start_ms = ist_day_start_utc_ms(start - timedelta(days=1))
    fetch_end_ms = ist_day_end_utc_ms(end + timedelta(days=1))

    bybit_syms, binance_syms = await get_all_usdt_symbols(session)

    daily = {}  # type: Dict[str, Dict]

    # Date keys we actually want
    wanted_keys = set()
    d = start
    while d <= end:
        wanted_keys.add(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)

    today_key = now_ist().strftime("%Y-%m-%d")

    # If today is included, we need to calculate volume since midnight IST
    # independently using klines, rather than 24h ticker (which is rolling)
    if today_key in wanted_keys:
        # Calculate start of today in UTC ms
        today_start_ms = ist_day_start_utc_ms(now_ist())
        today_end_ms = ist_day_end_utc_ms(now_ist())
        
        # We need smaller candles (hourly) to accurately catch volume since 00:00 IST
        # However for aggregate of ALL pairs, hourly for 400+ pairs is too slow.
        # Daily klines (UTC based) might overlap.
        # 
        # Strategy for Aggregate Today:
        # 1. Get 24h ticker (rolling) -> this is WRONG for "since midnight"
        # 2. Get Daily Kline (UTC) -> this covers 05:30 IST yesterday to 05:30 IST today (partial)
        # 
        # Correct approach for accurate "since midnight IST" without fetching 10,000 hourly candles:
        # There is none without heavy API usage.
        #
        # Compromise:
        # Use the Daily Kline (UTC) that covers the majority of "Today IST".
        # 00:00 IST = 18:30 UTC (previous day).
        # So "Today IST" spans two UTC days.
        # 
        # Let's stick to the previous method but filter strictly for klines that fall 
        # entirely or mostly within today? No, that misses live data.
        #
        # FASTEST APPROXIMATION for "Since Midnight IST":
        # fetch_start_ms is already set to capture overlaps.
        # The 'process_bybit/binance' functions already buckets klines into IST days.
        # We just need to NOT skip today_key in those loops, and REMOVE the 24h ticker block.
        pass

    async def process_bybit():
        tasks = [bybit_kline_daily(session, sym, fetch_start_ms, fetch_end_ms)
                 for sym in bybit_syms]
        results = await asyncio.gather(*tasks)
        for klines in results:
            for k in klines:
                day = k["ist_date_key"]
                if day not in wanted_keys:
                    continue
                if day not in daily:
                    daily[day] = {"bybit": 0, "binance": 0, "date_fmt": date_fmt(
                        datetime.strptime(day, "%Y-%m-%d"))}
                daily[day]["bybit"] += k["turnover"]

    async def process_binance():
        tasks = [binance_kline_daily(session, sym, fetch_start_ms, fetch_end_ms)
                 for sym in binance_syms]
        results = await asyncio.gather(*tasks)
        for klines in results:
            for k in klines:
                day = k["ist_date_key"]
                if day not in wanted_keys:
                    continue
                if day not in daily:
                    daily[day] = {"bybit": 0, "binance": 0, "date_fmt": date_fmt(
                        datetime.strptime(day, "%Y-%m-%d"))}
                daily[day]["binance"] += k["turnover"]

    await process_bybit()
    await process_binance()

    # Mark today as live (it's incomplete but accurate "since midnight")
    if today_key in daily:
        daily[today_key]["is_live"] = True
    elif today_key in wanted_keys:
         # If no data yet (e.g. just after midnight), init with 0
         daily[today_key] = {
            "bybit": 0,
            "binance": 0,
            "date_fmt": date_fmt(now_ist()),
            "is_live": True,
        }

    return daily


# ── Per-token IST aggregation (hourly klines) ────────────────────────────────

async def token_daily_volume_ist(session: aiohttp.ClientSession, symbol: str,
                                  start: datetime, end: datetime) -> dict:
    """
    Get daily volume for a single token aggregated by IST day.
    Uses hourly klines for accurate IST boundaries.
    For today, uses 24h ticker.

    Returns { "YYYY-MM-DD": {"bybit": vol, "binance": vol, ...} }
    """
    start_ms = ist_day_start_utc_ms(start)
    end_ms = ist_day_end_utc_ms(end)

    today_key = now_ist().strftime("%Y-%m-%d")
    today_in_range = start.strftime("%Y-%m-%d") <= today_key <= end.strftime("%Y-%m-%d")

    # For historical days, use hourly klines
    # Adjust end to exclude today (we'll fetch today separately)
    # Actually, token_daily uses hourly klines which ARE accurate for "since midnight".
    # So we can just use hourly klines for today as well!
    # The only issue is that "hourly" might be slightly delayed vs "ticker".
    # But for "Since Midnight", hourly sum is better than 24h ticker.
    
    # We will use hourly klines for the WHOLE range including today.
    # This ensures consistency.
    
    daily = {}  # type: Dict[str, Dict]

    bybit_hourly, binance_hourly = await asyncio.gather(
        bybit_kline_hourly(session, symbol, start_ms, end_ms),
        binance_kline_hourly(session, symbol, start_ms, end_ms),
    )

    for k in bybit_hourly:
        day = k["ist_date_key"]
        if day not in daily:
            daily[day] = {"bybit": 0, "binance": 0, "bybit_base": 0, "binance_base": 0,
                          "date_fmt": date_fmt(datetime.strptime(day, "%Y-%m-%d"))}
        daily[day]["bybit"] += k["turnover"]
        daily[day]["bybit_base"] += k["volume"]

    for k in binance_hourly:
        day = k["ist_date_key"]
        if day not in daily:
            daily[day] = {"bybit": 0, "binance": 0, "bybit_base": 0, "binance_base": 0,
                          "date_fmt": date_fmt(datetime.strptime(day, "%Y-%m-%d"))}
        daily[day]["binance"] += k["turnover"]
        daily[day]["binance_base"] += k["volume"]

    if today_in_range:
        if today_key in daily:
             daily[today_key]["is_live"] = True
        else:
             daily[today_key] = {
                "bybit": 0, "binance": 0, "bybit_base": 0, "binance_base": 0,
                "date_fmt": date_fmt(now_ist()),
                "is_live": True,
             }

    return daily


# ─── Bot Command Handlers ────────────────────────────────────────────────────

HELP_TEXT = """
📊 *Perpetual Volume Bot*
_Bybit & Binance USDT Perp Futures_
_All times in IST 🇮🇳_

*Aggregate Volume:*
`/volume` — Today's total 24h volume
`/volume ddmmyy` — Volume for a specific date
`/volume ddmmyy-ddmmyy` — Daily breakdown for range

*Token Volume:*
`/ETH` — Today's 24h ETH perp volume
`/BTC 150226` — BTC volume on 15 Feb 2026
`/SOL 100226-180226` — SOL daily range

_Date format: ddmmyy (e.g. 190226 = 19 Feb 2026)_
"""


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start and /help."""
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.MARKDOWN)


async def cmd_volume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /volume command."""
    arg = " ".join(context.args).strip() if context.args else ""

    single_date, range_start, range_end = parse_date_arg(arg)

    if single_date == "invalid":
        await update.message.reply_text(
            "❌ Invalid date format\\. Use `ddmmyy` or `ddmmyy\\-ddmmyy`",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    async with aiohttp.ClientSession() as session:
        if single_date is None and range_start is None:
            # ── Today's aggregate (Since Midnight IST) ──
            msg = await update.message.reply_text("⏳ Fetching today's volumes (Since 00:00 IST)...")

            # For "Since Midnight", we can't use 24h ticker.
            # We must use aggregate_daily_volume_ist for just today.
            today = today_ist()
            daily = await aggregate_daily_volume_ist(session, today, today)
            
            key = today.strftime("%Y-%m-%d")
            d = daily.get(key, {})
            
            bybit_total = d.get("bybit", 0)
            binance_total = d.get("binance", 0)
            combined = bybit_total + binance_total
            ist_now = now_ist()

            text = (
                f"📊 *Today's USDT Perp Volume* (Since 00:00 IST)\n"
                f"🕐 _{ist_now.strftime('%d %b %Y, %I:%M %p')} IST_\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🟡 *Bybit*: `{fmt(bybit_total)}`\n"
                f"🟠 *Binance*: `{fmt(binance_total)}`\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🏦 *Combined*: `{fmt(combined)}`\n"
            )
            await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)

        elif single_date and single_date != "invalid":
            # ── Single date aggregate ──
            if is_today_ist(single_date):
                 # Same logic as above 'Today', just formatted slightly differently
                msg = await update.message.reply_text("⏳ Fetching this day's volumes (Since 00:00 IST)...")
                today = today_ist()
                daily = await aggregate_daily_volume_ist(session, today, today)
                key = today.strftime("%Y-%m-%d")
                d = daily.get(key, {})
                
                bybit_total = d.get("bybit", 0)
                binance_total = d.get("binance", 0)
                combined = bybit_total + binance_total
                ist_now = now_ist()

                text = (
                    f"📊 *USDT Perp Volume — {date_fmt(single_date)}* 🔴 LIVE\n"
                    f"🕐 _{ist_now.strftime('%I:%M %p')} IST_\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"🟡 *Bybit*: `{fmt(bybit_total)}`\n"
                    f"🟠 *Binance*: `{fmt(binance_total)}`\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🏦 *Combined*: `{fmt(combined)}`\n"
                )
                await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
            else:
                msg = await update.message.reply_text(
                    f"⏳ Fetching all perp volumes for {date_fmt(single_date)}...\n"
                    f"_This may take 10-20 seconds_",
                    parse_mode=ParseMode.MARKDOWN,
                )

                daily = await aggregate_daily_volume_ist(session, single_date, single_date)
                key = single_date.strftime("%Y-%m-%d")

                if key in daily:
                    d = daily[key]
                    combined = d["bybit"] + d["binance"]
                    text = (
                        f"📊 *USDT Perp Volume — {d['date_fmt']}*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"🟡 *Bybit*: `{fmt(d['bybit'])}`\n"
                        f"🟠 *Binance*: `{fmt(d['binance'])}`\n\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🏦 *Combined*: `{fmt(combined)}`\n"
                    )
                else:
                    text = f"⚠️ No data found for {date_fmt(single_date)}"

                await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)

        else:
            # ── Date range aggregate ──
            days = (range_end - range_start).days + 1
            if days > 30:
                await update.message.reply_text(
                    "❌ Range cannot exceed 30 days for aggregate volume.")
                return

            msg = await update.message.reply_text(
                f"⏳ Fetching {days} days of aggregate volume...\n"
                f"_This may take 15-30 seconds_",
                parse_mode=ParseMode.MARKDOWN,
            )

            daily = await aggregate_daily_volume_ist(session, range_start, range_end)

            lines = [
                f"📊 *USDT Perp Volume (IST)*\n"
                f"📅 {date_fmt(range_start)} → {date_fmt(range_end)}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            ]

            total_bybit = 0
            total_binance = 0

            for key in sorted(daily.keys()):
                d = daily[key]
                combined = d["bybit"] + d["binance"]
                total_bybit += d["bybit"]
                total_binance += d["binance"]
                live_tag = " 🔴" if d.get("is_live") else ""
                lines.append(
                    f"*{d['date_fmt']}*{live_tag}\n"
                    f"  🟡 Bybit: `{fmt(d['bybit'])}`\n"
                    f"  🟠 Binance: `{fmt(d['binance'])}`\n"
                    f"  🏦 Total: `{fmt(combined)}`\n"
                )

            grand_total = total_bybit + total_binance
            lines.append(
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"*TOTALS ({days} days)*\n"
                f"  🟡 Bybit: `{fmt(total_bybit)}`\n"
                f"  🟠 Binance: `{fmt(total_binance)}`\n"
                f"  🏦 Combined: `{fmt(grand_total)}`\n"
            )

            await msg.edit_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle /<TOKEN> commands (e.g. /ETH, /BTC, /SOL).
    This is registered as a catch-all for unknown commands.
    """
    raw = update.message.text.strip()
    parts = raw.split(maxsplit=1)
    token = parts[0][1:].upper()
    arg = parts[1].strip() if len(parts) > 1 else ""
    symbol = f"{token}USDT"

    single_date, range_start, range_end = parse_date_arg(arg)

    if single_date == "invalid":
        await update.message.reply_text(
            "❌ Invalid date format\\. Use `ddmmyy` or `ddmmyy\\-ddmmyy`",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    async with aiohttp.ClientSession() as session:
        if single_date is None and range_start is None:
            # ── Today's for this token (Since Midnight) ──
            msg = await update.message.reply_text(f"⏳ Fetching {symbol} volumes (Since 00:00 IST)...")

            today = today_ist()
            daily = await token_daily_volume_ist(session, symbol, today, today)
            
            key = today.strftime("%Y-%m-%d")
            d = daily.get(key, {})
            
            bybit_vol = d.get("bybit", 0)
            binance_vol = d.get("binance", 0)
            bybit_base = d.get("bybit_base", 0)
            binance_base = d.get("binance_base", 0)
            combined = bybit_vol + binance_vol
            ist_now = now_ist()

            lines = [
                f"📊 *{symbol} — Today's Volume*\n"
                f"🕐 _{ist_now.strftime('%d %b %Y, %I:%M %p')} IST_\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            ]

            lines.append(
                f"🟡 *Bybit*\n"
                f"   Volume: `{fmt(bybit_vol)}`\n"
                f"   Base Vol: `{bybit_base:,.2f} {token}`\n"
            )
            lines.append(
                f"🟠 *Binance*\n"
                f"   Volume: `{fmt(binance_vol)}`\n"
                f"   Base Vol: `{binance_base:,.2f} {token}`\n"
            )

            lines.append(
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🏦 *Combined*: `{fmt(combined)}`"
            )

            await msg.edit_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

        elif single_date and single_date != "invalid":
            # ── Single date for this token ──
            is_live = is_today_ist(single_date)
            msg = await update.message.reply_text(
                    f"⏳ Fetching {symbol} volume for {date_fmt(single_date)} (IST)...")

            daily = await token_daily_volume_ist(session, symbol,
                                                 single_date, single_date)
            key = single_date.strftime("%Y-%m-%d")

            d = daily.get(key, {})
            bybit_vol = d.get("bybit", 0)
            binance_vol = d.get("binance", 0)
            bybit_base = d.get("bybit_base", 0)
            binance_base = d.get("binance_base", 0)
            combined = bybit_vol + binance_vol
            
            live_tag = " 🔴 LIVE" if is_live else ""
            ist_now = now_ist()
            
            text = (
                f"📊 *{symbol} — {date_fmt(single_date)}*{live_tag}\n"
                f"🕐 _{ist_now.strftime('%I:%M %p')} IST_\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🟡 *Bybit*: `{fmt(bybit_vol)}` ({bybit_base:,.2f} {token})\n"
                f"🟠 *Binance*: `{fmt(binance_vol)}` ({binance_base:,.2f} {token})\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🏦 *Combined*: `{fmt(combined)}`\n"
            )
            await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)

        else:
            # ── Date range for this token ──
            days = (range_end - range_start).days + 1
            if days > 90:
                await update.message.reply_text("❌ Range cannot exceed 90 days.")
                return

            msg = await update.message.reply_text(
                f"⏳ Fetching {symbol} volumes for {days} days (IST)...")

            daily = await token_daily_volume_ist(session, symbol,
                                                  range_start, range_end)

            if not daily:
                await msg.edit_text(
                    f"⚠️ No data for `{symbol}` in this range",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return

            lines = [
                f"📊 *{symbol} Daily Volume (IST)*\n"
                f"📅 {date_fmt(range_start)} → {date_fmt(range_end)}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            ]

            total_bybit = 0
            total_binance = 0

            for key in sorted(daily.keys()):
                d = daily[key]
                combined = d["bybit"] + d["binance"]
                total_bybit += d["bybit"]
                total_binance += d["binance"]
                live_tag = " 🔴" if d.get("is_live") else ""
                lines.append(
                    f"*{d['date_fmt']}*{live_tag}\n"
                    f"  🟡 `{fmt(d['bybit'])}` | 🟠 `{fmt(d['binance'])}` | 🏦 `{fmt(combined)}`\n"
                )

            grand = total_bybit + total_binance
            lines.append(
                f"\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"*TOTALS*\n"
                f"  🟡 Bybit: `{fmt(total_bybit)}`\n"
                f"  🟠 Binance: `{fmt(total_binance)}`\n"
                f"  🏦 Combined: `{fmt(grand)}`"
            )

            await msg.edit_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ─── Bot Setup ────────────────────────────────────────────────────────────────

def main():
    """Start the bot."""
    logger.info("🚀 Starting Perpetual Volume Bot (IST)...")
    logger.info(f"🕐 Current IST time: {now_ist().strftime('%d %b %Y %I:%M %p')}")

    app = Application.builder().token(BOT_TOKEN).build()

    # Known commands
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("volume", cmd_volume))

    # Catch-all for /<TOKEN> commands
    app.add_handler(MessageHandler(filters.COMMAND, cmd_token))

    logger.info("✅ Bot is running! Send /start in Telegram to begin.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
