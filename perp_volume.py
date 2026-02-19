#!/usr/bin/env python3
"""
Perpetual USDT Derivatives Volume Tracker
==========================================
Fetches daily or date-range trading volumes for USDT perpetual contracts
from Bybit and Binance.

Usage:
    # Today's 24h snapshot for all pairs
    python perp_volume.py

    # Top 20 pairs by volume
    python perp_volume.py --top 20

    # Specific symbols
    python perp_volume.py --symbols BTCUSDT ETHUSDT SOLUSDT

    # Historical daily volumes for a date range
    python perp_volume.py --from 2026-02-10 --to 2026-02-18

    # Specific exchange only
    python perp_volume.py --exchange bybit
    python perp_volume.py --exchange binance

    # Export to CSV
    python perp_volume.py --csv volumes.csv

    # Combined
    python perp_volume.py --symbols BTCUSDT ETHUSDT --from 2026-02-01 --to 2026-02-15 --csv output.csv
"""

import argparse
import csv
import sys
import time
from datetime import datetime, timedelta
from typing import Optional

try:
    import requests
except ImportError:
    print("❌ Missing dependency: requests")
    print("   Install it with: pip install requests")
    sys.exit(1)

try:
    from tabulate import tabulate
    HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False


# ─── API Configuration ────────────────────────────────────────────────────────

BYBIT_BASE = "https://api.bybit.com"
BINANCE_BASE = "https://fapi.binance.com"

BYBIT_TICKERS  = f"{BYBIT_BASE}/v5/market/tickers"
BYBIT_KLINE    = f"{BYBIT_BASE}/v5/market/kline"
BINANCE_TICKER = f"{BINANCE_BASE}/fapi/v1/ticker/24hr"
BINANCE_KLINE  = f"{BINANCE_BASE}/fapi/v1/klines"


# ─── Utility Helpers ──────────────────────────────────────────────────────────

def fmt_vol(v: float) -> str:
    """Format volume into human-readable form."""
    if v >= 1_000_000_000:
        return f"${v / 1_000_000_000:.2f}B"
    elif v >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    elif v >= 1_000:
        return f"${v / 1_000:.2f}K"
    return f"${v:.2f}"


def date_to_ms(date_str: str) -> int:
    """Convert YYYY-MM-DD to milliseconds timestamp."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return int(dt.timestamp() * 1000)


def ms_to_date(ms: int) -> str:
    """Convert milliseconds timestamp to YYYY-MM-DD."""
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d")


def print_table(headers: list, rows: list):
    """Print a formatted table."""
    if HAS_TABULATE:
        print(tabulate(rows, headers=headers, tablefmt="fancy_grid", stralign="right"))
    else:
        # Fallback: manual column formatting
        col_widths = [max(len(str(h)), *(len(str(r[i])) for r in rows)) for i, h in enumerate(headers)]
        sep = "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"
        header_str = "|" + "|".join(f" {h:>{w}} " for h, w in zip(headers, col_widths)) + "|"
        print(sep)
        print(header_str)
        print(sep)
        for row in rows:
            row_str = "|" + "|".join(f" {str(v):>{w}} " for v, w in zip(row, col_widths)) + "|"
            print(row_str)
        print(sep)


# ─── Bybit API ────────────────────────────────────────────────────────────────

def bybit_get_24h_volumes(symbols: Optional[list] = None) -> list:
    """Fetch 24h volumes for USDT perpetuals from Bybit."""
    results = []
    try:
        resp = requests.get(BYBIT_TICKERS, params={"category": "linear"}, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if data.get("retCode") != 0:
            print(f"  ⚠️  Bybit API error: {data.get('retMsg', 'Unknown')}")
            return results

        for item in data["result"]["list"]:
            symbol = item["symbol"]
            # Only USDT perpetuals
            if not symbol.endswith("USDT"):
                continue
            if symbols and symbol not in symbols:
                continue

            turnover = float(item.get("turnover24h", 0))  # Quote volume (USDT)
            volume = float(item.get("volume24h", 0))        # Base volume
            last_price = float(item.get("lastPrice", 0))

            results.append({
                "exchange": "Bybit",
                "symbol": symbol,
                "volume_usdt": turnover,
                "volume_base": volume,
                "last_price": last_price,
            })
    except requests.RequestException as e:
        print(f"  ❌ Bybit API request failed: {e}")
    return results


def bybit_get_daily_kline_volume(symbol: str, start_ms: int, end_ms: int) -> list:
    """Fetch daily kline volumes from Bybit for a symbol over a date range."""
    results = []
    try:
        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": "D",  # Daily
            "start": start_ms,
            "end": end_ms,
            "limit": 200,
        }
        resp = requests.get(BYBIT_KLINE, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if data.get("retCode") != 0:
            return results

        for candle in data["result"]["list"]:
            # Bybit kline: [startTime, open, high, low, close, volume, turnover]
            ts = int(candle[0])
            turnover = float(candle[6])  # USDT turnover
            volume = float(candle[5])    # Base volume

            results.append({
                "exchange": "Bybit",
                "symbol": symbol,
                "date": ms_to_date(ts),
                "volume_usdt": turnover,
                "volume_base": volume,
            })
    except requests.RequestException as e:
        print(f"  ❌ Bybit kline request failed for {symbol}: {e}")
    return results


# ─── Binance API ──────────────────────────────────────────────────────────────

def binance_get_24h_volumes(symbols: Optional[list] = None) -> list:
    """Fetch 24h volumes for USDT perpetuals from Binance."""
    results = []
    try:
        resp = requests.get(BINANCE_TICKER, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        for item in data:
            symbol = item["symbol"]
            if not symbol.endswith("USDT"):
                continue
            if symbols and symbol not in symbols:
                continue

            quote_vol = float(item.get("quoteVolume", 0))  # USDT volume
            base_vol = float(item.get("volume", 0))         # Base volume
            last_price = float(item.get("lastPrice", 0))

            results.append({
                "exchange": "Binance",
                "symbol": symbol,
                "volume_usdt": quote_vol,
                "volume_base": base_vol,
                "last_price": last_price,
            })
    except requests.RequestException as e:
        print(f"  ❌ Binance API request failed: {e}")
    return results


def binance_get_daily_kline_volume(symbol: str, start_ms: int, end_ms: int) -> list:
    """Fetch daily kline volumes from Binance for a symbol over a date range."""
    results = []
    try:
        params = {
            "symbol": symbol,
            "interval": "1d",
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": 500,
        }
        resp = requests.get(BINANCE_KLINE, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        for candle in data:
            # Binance kline: [openTime, open, high, low, close, volume, closeTime,
            #                  quoteVolume, trades, takerBuyBase, takerBuyQuote, ignore]
            ts = int(candle[0])
            quote_vol = float(candle[7])  # Quote asset volume (USDT)
            base_vol = float(candle[5])   # Base asset volume

            results.append({
                "exchange": "Binance",
                "symbol": symbol,
                "date": ms_to_date(ts),
                "volume_usdt": quote_vol,
                "volume_base": base_vol,
            })
    except requests.RequestException as e:
        print(f"  ❌ Binance kline request failed for {symbol}: {e}")
    return results


# ─── Main Modes ───────────────────────────────────────────────────────────────

def mode_24h_snapshot(args):
    """Show current 24h rolling volumes."""
    print("\n🔄 Fetching 24h perpetual USDT volumes...\n")

    all_results = []

    if args.exchange in ("all", "bybit"):
        print("  📡 Querying Bybit...")
        all_results.extend(bybit_get_24h_volumes(args.symbols))
    if args.exchange in ("all", "binance"):
        print("  📡 Querying Binance...")
        all_results.extend(binance_get_24h_volumes(args.symbols))

    if not all_results:
        print("\n  ⚠️  No data retrieved. Check your network or symbol filters.")
        return

    # Sort by USDT volume descending
    all_results.sort(key=lambda x: x["volume_usdt"], reverse=True)

    # Apply top-N filter
    if args.top:
        all_results = all_results[:args.top]

    # Display
    headers = ["#", "Exchange", "Symbol", "24h Volume (USDT)", "24h Volume (Base)", "Last Price"]
    rows = []
    total_volume = 0
    for i, r in enumerate(all_results, 1):
        rows.append([
            i,
            r["exchange"],
            r["symbol"],
            fmt_vol(r["volume_usdt"]),
            f"{r['volume_base']:,.2f}",
            f"${r['last_price']:,.4f}",
        ])
        total_volume += r["volume_usdt"]

    print()
    print_table(headers, rows)
    print(f"\n  📊 Total Volume (shown): {fmt_vol(total_volume)}")
    print(f"  📋 Pairs shown: {len(all_results)}")

    # Aggregate by exchange
    exchange_totals = {}
    for r in all_results:
        exchange_totals[r["exchange"]] = exchange_totals.get(r["exchange"], 0) + r["volume_usdt"]
    print("\n  🏦 Volume by Exchange:")
    for ex, vol in sorted(exchange_totals.items(), key=lambda x: x[1], reverse=True):
        print(f"     {ex}: {fmt_vol(vol)}")

    # Export CSV if requested
    if args.csv:
        export_csv(args.csv, headers[1:], [[r[1], r[2], r[3], r[4], r[5]] for r in rows])
        print(f"\n  💾 Exported to {args.csv}")


def mode_date_range(args):
    """Show daily volumes over a date range."""
    start_ms = date_to_ms(args.date_from)
    end_ms = date_to_ms(args.date_to) + 86400000 - 1  # End of day

    symbols = args.symbols or ["BTCUSDT", "ETHUSDT"]

    print(f"\n📅 Fetching daily volumes from {args.date_from} to {args.date_to}")
    print(f"   Symbols: {', '.join(symbols)}\n")

    all_results = []

    for sym in symbols:
        if args.exchange in ("all", "bybit"):
            print(f"  📡 Bybit → {sym}...")
            all_results.extend(bybit_get_daily_kline_volume(sym, start_ms, end_ms))
            time.sleep(0.2)  # Be polite to API
        if args.exchange in ("all", "binance"):
            print(f"  📡 Binance → {sym}...")
            all_results.extend(binance_get_daily_kline_volume(sym, start_ms, end_ms))
            time.sleep(0.2)

    if not all_results:
        print("\n  ⚠️  No data retrieved. The symbol may not exist on the exchange.")
        return

    # Sort by date, then exchange, then symbol
    all_results.sort(key=lambda x: (x["date"], x["exchange"], x["symbol"]))

    # Display
    headers = ["Date", "Exchange", "Symbol", "Daily Volume (USDT)", "Daily Volume (Base)"]
    rows = []
    total_volume = 0
    for r in all_results:
        rows.append([
            r["date"],
            r["exchange"],
            r["symbol"],
            fmt_vol(r["volume_usdt"]),
            f"{r['volume_base']:,.2f}",
        ])
        total_volume += r["volume_usdt"]

    print()
    print_table(headers, rows)
    print(f"\n  📊 Total Volume (range): {fmt_vol(total_volume)}")

    # Daily totals
    daily_totals = {}
    for r in all_results:
        daily_totals[r["date"]] = daily_totals.get(r["date"], 0) + r["volume_usdt"]

    print("\n  📆 Daily Totals:")
    for date, vol in sorted(daily_totals.items()):
        print(f"     {date}: {fmt_vol(vol)}")

    # Exchange totals
    exchange_totals = {}
    for r in all_results:
        exchange_totals[r["exchange"]] = exchange_totals.get(r["exchange"], 0) + r["volume_usdt"]
    print("\n  🏦 Volume by Exchange:")
    for ex, vol in sorted(exchange_totals.items(), key=lambda x: x[1], reverse=True):
        print(f"     {ex}: {fmt_vol(vol)}")

    # Export CSV
    if args.csv:
        export_csv(args.csv, headers, rows)
        print(f"\n  💾 Exported to {args.csv}")


def export_csv(filepath: str, headers: list, rows: list):
    """Export results to a CSV file."""
    with open(filepath, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="📊 Perpetual USDT Derivatives Volume Tracker — Bybit & Binance",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                                    # 24h snapshot, all pairs
  %(prog)s --top 20                           # Top 20 by volume
  %(prog)s --symbols BTCUSDT ETHUSDT          # Specific pairs
  %(prog)s --from 2026-02-10 --to 2026-02-18  # Date range (defaults to BTC & ETH)
  %(prog)s --from 2026-02-10 --to 2026-02-18 --symbols SOLUSDT
  %(prog)s --exchange bybit --top 10          # Bybit only, top 10
  %(prog)s --csv output.csv                   # Export to CSV
        """,
    )
    parser.add_argument(
        "--symbols", nargs="+", metavar="SYM",
        help="Filter to specific symbols (e.g. BTCUSDT ETHUSDT). For date-range mode, defaults to BTCUSDT ETHUSDT.",
    )
    parser.add_argument(
        "--exchange", choices=["all", "bybit", "binance"], default="all",
        help="Which exchange to query (default: all)",
    )
    parser.add_argument(
        "--top", type=int, metavar="N",
        help="Show only top N pairs by volume (24h mode only)",
    )
    parser.add_argument(
        "--from", dest="date_from", metavar="YYYY-MM-DD",
        help="Start date for historical range (enables date-range mode)",
    )
    parser.add_argument(
        "--to", dest="date_to", metavar="YYYY-MM-DD",
        help="End date for historical range (defaults to today if --from is set)",
    )
    parser.add_argument(
        "--csv", metavar="FILE",
        help="Export results to a CSV file",
    )

    args = parser.parse_args()

    # Normalize symbols to uppercase
    if args.symbols:
        args.symbols = [s.upper() for s in args.symbols]

    # Determine mode
    if args.date_from:
        # Date-range mode
        if not args.date_to:
            args.date_to = datetime.now().strftime("%Y-%m-%d")
        # Validate dates
        try:
            start = datetime.strptime(args.date_from, "%Y-%m-%d")
            end = datetime.strptime(args.date_to, "%Y-%m-%d")
            if start > end:
                print("❌ --from date must be before --to date")
                sys.exit(1)
            if (end - start).days > 365:
                print("❌ Date range cannot exceed 365 days")
                sys.exit(1)
        except ValueError:
            print("❌ Invalid date format. Use YYYY-MM-DD.")
            sys.exit(1)

        mode_date_range(args)
    else:
        # 24h snapshot mode
        mode_24h_snapshot(args)

    print()


if __name__ == "__main__":
    main()
