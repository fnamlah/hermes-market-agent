---
name: market-data
description: Pull real-time price, volume, and intraday OHLC for monitored tickers. Uses yfinance (free, end-of-day + delayed intraday) by default; falls back to Polygon.io if POLYGON_API_KEY is set. Writes to market_quotes in /data/hermes-market.db.
version: 0.1.0
author: Faisal Alnamlah
license: MIT
platforms: [linux]
prerequisites:
  commands: [python3, sqlite3, jq, curl]
  python_packages: [yfinance, requests]
metadata:
  hermes:
    tags: [market-data, stocks, price, volume, yfinance, polygon]
    profile: scout
---

# market-data — Stock price/volume snapshots

Pulls current price + intraday volume + 20-day average volume for each monitored ticker, writes one row per ticker per cycle to `market_quotes`.

This skill provides the **market-side** signal that combines with social signals (from apify-x, apify-reddit) in Sage's composite score. Unusual volume + social attention spike is a stronger signal than either alone.

## When to use this skill

- Every cycle (15 min during market hours, 60 min after-hours)
- Quick one-off lookup when the user asks "what's $NVDA doing"
- Before an alert fires — Herald should re-check current price to include in the message

## Required environment

- **Default (yfinance):** no env vars needed, free tier, ~15-min delayed
- **Upgrade (Polygon):** set `POLYGON_API_KEY` as Railway service variable — real-time, cleaner data

## Execution (yfinance path, default)

Use Python with the `yfinance` library. Install via the container's `pip install yfinance` (see Dockerfile).

```python
import yfinance as yf
import sqlite3, json
from datetime import datetime, timezone

conn = sqlite3.connect('/data/hermes-market.db')
tickers_json = conn.execute("SELECT value FROM config WHERE key='monitored_tickers'").fetchone()[0]
tickers = json.loads(tickers_json)

now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%fZ')
batch = yf.Tickers(' '.join(tickers))

for t in tickers:
    try:
        info = batch.tickers[t].fast_info
        hist = batch.tickers[t].history(period='1d', interval='1m', prepost=True).tail(1)
        if hist.empty:
            continue
        row = hist.iloc[0]
        conn.execute("""
            INSERT OR IGNORE INTO market_quotes(
                ticker, snapshot_at_utc, price, volume,
                open, high, low, close, prev_close, change_pct,
                avg_volume_20d, volume_ratio, source, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            t, now, float(info.last_price), int(row['Volume']),
            float(row['Open']), float(row['High']), float(row['Low']), float(row['Close']),
            float(info.previous_close),
            (float(info.last_price) - float(info.previous_close)) / float(info.previous_close) * 100,
            int(info.ten_day_average_volume * 2) if info.ten_day_average_volume else None,
            None,  # volume_ratio computed by Sage using baseline
            'yfinance',
            json.dumps({'ticker': t, 'source': 'yfinance'})
        ))
    except Exception as e:
        print(f'market-data error for {t}: {e}')

conn.commit()
conn.close()
```

## Execution (Polygon path, if POLYGON_API_KEY set)

```
GET https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,TSLA,...&apiKey=$POLYGON_API_KEY
```

Response gives `day`, `prevDay`, `min`, `lastTrade`, `lastQuote` per ticker. Use `day.v` for volume, `lastTrade.p` for price, `prevDay.c` for prev close, `min.v` / `prevDay.v` for volume ratio.

Preferred over yfinance when the user upgrades — more reliable for intraday snapshots and always fresh.

## Signals to emit (Sage will read these)

Row in `market_quotes` includes:
- `price` — most recent trade
- `volume` — cumulative day volume
- `avg_volume_20d` — 20-day average (for spike detection)
- `volume_ratio` — current / avg (filled by Sage during scoring, leave NULL here)
- `change_pct` — vs previous close
- `is_premarket` / `is_afterhours` — flags for session-aware scoring

## Market hours detection

Use `pytz.timezone('America/New_York')` and check:
- Market hours: 09:30–16:00 ET Mon–Fri
- Pre-market: 04:00–09:30 ET
- After-hours: 16:00–20:00 ET
- Skip fetching on weekends and US holidays (hardcoded list)

The cron scheduler should handle cadence (don't pull every minute on weekends), but this skill should still write pre/after-hours rows when invoked during those windows.

## Failure modes to handle

- yfinance occasionally returns empty — retry once with `period='2d'`
- Polygon 403 → expired API key, alert operator via Telegram
- Ticker doesn't exist (e.g., delisted) → log and remove from monitored list (manual review)
- Partial batch success → commit what you have, report count

## What NOT to do

- Do not compute technical indicators here (RSI, MACD, etc.) — that's analysis, not collection. Scout stores raw data; Sage derives indicators.
- Do not make individual HTTP calls per ticker when batch fetch works (yfinance `Tickers()` batches under the hood)
- Do not store partial rows — if a fetch fails mid-ticker, skip that ticker and retry next cycle
