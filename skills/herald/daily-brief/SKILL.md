---
name: daily-brief
description: Generate an end-of-day social market brief summarizing today's top tickers, emerging and fading narratives, suppressed false signals, and tickers to watch tomorrow. Delivered via Hermes's messaging gateway at 4:30 PM ET.
version: 0.1.0
author: Faisal Alnamlah
license: MIT
platforms: [linux]
prerequisites:
  commands: [sqlite3]
metadata:
  hermes:
    tags: [reports, daily-brief, herald, market]
    profile: herald
---

# daily-brief — End-of-day social market summary

Runs once per trading day (4:30 PM ET via cron) after the market close and after-hours reactions settle. Produces a scannable 500-800 word report of today's notable tickers, narrative shifts, and tomorrow's watchlist.

## When to use this skill

- 4:30 PM ET weekdays via cron (automated)
- On-demand when the user asks for "today's recap"
- For backfill when a scheduled run misses (rare)

## Data to gather

All queries scope to `cycle_at_utc > date('now', '-1 day') AT START_OF_DAY_ET`. For simplicity use UTC `datetime('now', '-14 hours')` which covers the full US market day.

### Top 10 tickers by peak composite score today
```sql
SELECT ticker,
       MAX(composite_score) AS peak_score,
       (SELECT phase FROM ticker_signals ts2
        WHERE ts2.ticker = ts.ticker
          AND ts2.composite_score = MAX(ts.composite_score)
        LIMIT 1) AS peak_phase,
       SUM(mention_count) AS total_mentions,
       AVG(sentiment_avg) AS avg_sentiment,
       MAX(ratio_vs_baseline) AS peak_ratio
FROM ticker_signals ts
WHERE cycle_at_utc > datetime('now', '-14 hours')
GROUP BY ticker
ORDER BY peak_score DESC
LIMIT 10;
```

### Emerging narratives (first appeared today, hit score > 0.4)
```sql
SELECT ticker, MIN(cycle_at_utc) AS first_seen, MAX(composite_score) AS peak
FROM ticker_signals
WHERE ticker NOT IN (
    SELECT DISTINCT ticker FROM ticker_signals
    WHERE cycle_at_utc < datetime('now', '-14 hours')
)
GROUP BY ticker
HAVING peak > 0.4
ORDER BY peak DESC
LIMIT 5;
```

### Fading narratives (peaked early, now below 0.3)
```sql
WITH today AS (
  SELECT ticker,
         MAX(composite_score) AS peak_score,
         (SELECT composite_score FROM ticker_signals ts3
          WHERE ts3.ticker = ts.ticker
          ORDER BY cycle_at_utc DESC LIMIT 1) AS current_score
  FROM ticker_signals ts
  WHERE cycle_at_utc > datetime('now', '-14 hours')
  GROUP BY ticker
)
SELECT ticker, peak_score, current_score
FROM today
WHERE peak_score > 0.5 AND current_score < 0.3
ORDER BY peak_score DESC
LIMIT 5;
```

### Suppressed false signals (spam/meme dominated, peaked but penalty high)
```sql
SELECT ticker, MAX(composite_score) AS peak_before_penalty,
       AVG(false_positive_penalty) AS avg_fp
FROM ticker_signals
WHERE cycle_at_utc > datetime('now', '-14 hours')
  AND false_positive_penalty > 0.5
GROUP BY ticker
HAVING peak_before_penalty > 0.4
ORDER BY avg_fp DESC
LIMIT 5;
```

### Alert recap
```sql
SELECT COUNT(*) AS alerts_sent,
       SUM(CASE priority WHEN 'high' THEN 1 ELSE 0 END) AS high,
       SUM(CASE priority WHEN 'medium' THEN 1 ELSE 0 END) AS med
FROM alerts_log
WHERE sent_at_utc > datetime('now', '-14 hours');
```

## Message format

```
📊 Social Market Brief — {Weekday} {Month} {Day}

═══ TOP 10 TICKERS ═══
1. ${TKR1} {peak_score} — {phase}, {mentions}M mentions ({sentiment})
2. ${TKR2} {peak_score} — {phase}, ...
...

📈 EMERGING (new today)
• ${NEW1}: peaked at {score}, first seen {time_ago}
• ${NEW2}: ...
(or "No new narratives today")

📉 FADING (peaked early, now quiet)
• ${FADE1}: {peak} → {current}
(or "No fading narratives today")

🚫 FALSE SIGNALS SUPPRESSED
• ${SPAM1}: hit {score} but {fp}% spam/meme-dominated
(or "No false signals today")

🔔 ALERTS SENT TODAY: {N} total ({high} high, {med} medium)

👀 TOMORROW
Watch: {top 3 tickers with phase != 'fading'}
Reason: {one-liner per ticker}

—
Market hours analysis window: 09:30 ET to current.
{N_posts_analyzed} posts classified across {N_tickers_scored} tickers.
```

## Tone and style

- **Terse.** Every line earns its place. No filler.
- **Actionable.** "Watch tomorrow" names specific things to look for.
- **Honest about quiet days.** "Nothing emerged today" is a valid summary when volume is low.
- **No hype.** Describe what happened; don't editorialize. No "big moves!" or "wild session!"
- **Plain text.** Telegram renders newlines and emojis; don't use Markdown headers.

## Length target

500-800 words total. Trim aggressively if the day was quiet.

## Quiet-day fallback

If total posts_analyzed < 500 or total tickers_scored < 5, produce a shorter 150-word note:

```
📊 Social Market Brief — {date}

Quiet session. Limited social signal today ({N_posts} posts across {N_tickers} tickers).

{Top 3 tickers if any crossed 0.3, else "No tickers crossed the watch threshold."}

{Alert count if any, else "No alerts sent."}

Tomorrow: resume normal cycle.
```

## Failure modes to handle

- No ticker_signals today (e.g., Scout was down) → emit "📊 No analysis data for today — check Scout status." and exit
- No alerts_log rows → "🔔 No alerts sent today." (common on quiet days, not an error)
- Database lock or SQL error → emit "📊 Brief generation failed — {short error}. Manual review required." (should be rare)

## What NOT to do

- Do not include tomorrow's **price predictions**. This is a social-signal recap, not forecasting.
- Do not list every ticker that was scored — top 10 only. Noise buries the signal.
- Do not repeat the same ticker in both EMERGING and FADING — if it's emerging, it's not fading.
- Do not include raw SQL or JSON in the output.
- Do not write to any table. Read-only skill.
