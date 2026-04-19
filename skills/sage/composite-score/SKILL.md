---
name: composite-score
description: Aggregate ticker activity from processed_posts + market_quotes, compute attention/credibility/momentum/confidence/cross-platform/novelty/market components and the composite score. Writes one row per ticker per cycle to ticker_signals. Subsumes spike detection and cross-platform confirmation.
version: 0.1.0
author: Faisal Alnamlah
license: MIT
platforms: [linux]
prerequisites:
  commands: [python3, sqlite3]
  files: [/app/shared/composite_score.py]
metadata:
  hermes:
    tags: [analysis, scoring, market, sage]
    profile: sage
---

# composite-score — Ticker signal scoring

Runs deterministic math to compute composite signal scores for all tickers with fresh activity. The actual computation lives in `/app/shared/composite_score.py` — this skill is mostly about **when** to invoke it and how to interpret the output.

## When to use this skill

- After `sentiment-classify` has populated `processed_posts` for the current cycle
- Every 15 min during market hours (triggered by cron)
- On demand when a user asks "what's the current score for $NVDA"

## How to invoke

```bash
python3 /app/shared/composite_score.py --window-minutes 15 --verbose
```

Flags:
- `--window-minutes N` (default 15) — size of the current activity window
- `--verbose` — print per-ticker component breakdown as JSON-lines

Exit codes:
- `0` — success, rows written
- `1` — DB missing or SQL error
- `2` — no fresh activity in window (normal outside market hours)

## What it writes

One row per ticker per cycle into `ticker_signals` with:
- `mention_count`, `unique_authors`, `velocity` — raw counts
- `sentiment_avg` — bullish/bearish/neutral normalized to [-1, 1]
- `baseline_24h` — rolling baseline for spike detection
- `ratio_vs_baseline` — current / baseline (the "spike magnitude")
- Component scores (all [0, 1]): `attention_score`, `credibility_score`, `momentum_score`, `confidence_score`, `cross_platform_score`, `novelty_score`, `market_score`
- `false_positive_penalty` (max 0.8)
- `composite_score` — weighted sum × (1 - penalty)
- `phase` ∈ `{emerging, accelerating, peaking, fading}`

## Composite formula (for reference — implemented in the script)

```
composite = (
    0.20 * attention +
    0.12 * credibility +
    0.16 * momentum +
    0.12 * confidence +
    0.08 * cross_platform +
    0.12 * novelty +
    0.20 * market
) * (1 - false_positive_penalty)
```

Weights rescaled from V1 to add a `market` component — social components sum to 0.80, market fills the remaining 0.20.

## Reading the results

After running, query the current cycle's top tickers:

```sql
SELECT ticker, composite_score, phase, ratio_vs_baseline, sentiment_avg
FROM ticker_signals
WHERE cycle_at_utc > datetime('now', '-1 minute')
ORDER BY composite_score DESC
LIMIT 10;
```

## Threshold interpretation (for Herald in P3)

- `composite_score >= 0.7` → HIGH priority alert
- `0.5 <= score < 0.7` → MEDIUM priority (hourly digest)
- `0.3 <= score < 0.5` → watchlist only (dashboard)
- `< 0.3` → suppress

## Typical report after one cycle run

```
composite-score: 6 tickers scored this cycle
```

With `--verbose`, also prints per-ticker breakdown. Summarize for the user: mention the top 3 by composite, note any that crossed 0.5 vs their previous cycle.

## Failure modes to handle

- No fresh activity in window → exit 2, report "no activity in last 15 min, skipping"
- Ticker has no market_quotes row yet → market_score = 0, that's fine (social-only signal)
- DB locked (rare with WAL mode) → retry once after 2s
- `composite_score.py` script missing → hard error, means Dockerfile drift

## What NOT to do

- Do not invoke this from raw_posts directly — run `sentiment-classify` first. Composite-score reads `processed_posts`, not `raw_posts`.
- Do not tune the weights here — weights are in the script. Change them in source and redeploy.
- Do not delete old ticker_signals rows — the time series is used by other skills (momentum needs prev cycle).
- Do not write to `alerts_log` — that's Herald's table, in P3.
