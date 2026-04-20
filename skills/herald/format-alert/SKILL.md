---
name: format-alert
description: Compose a concise market alert message for a single ticker crossing the composite_score threshold. Reads from ticker_signals + recent processed_posts + latest market_quotes and produces alert text that Hermes's Telegram gateway can deliver. Does NOT send — just formats.
version: 0.1.0
author: Faisal Alnamlah
license: MIT
platforms: [linux]
prerequisites:
  commands: [sqlite3]
metadata:
  hermes:
    tags: [alerts, formatting, herald, market]
    profile: herald
---

# format-alert — Compose alert message for a ticker

Given a ticker, pull its current signal, latest market snapshot, and top 3 posts, and produce a formatted alert. Priority (high vs medium) determines message length and urgency.

## When to use this skill

- After `cooldown-check` approves a ticker for alerting
- On-demand when the user asks "what would the alert for $NVDA look like"
- Never run this on low-score tickers — wastes tokens and produces confusing output

## Input

A ticker symbol plus optional override of priority. Example: `NVDA`, `NVDA high`.

## What to gather

1. Most recent `ticker_signals` row for the ticker:
   ```sql
   SELECT composite_score, phase, ratio_vs_baseline, sentiment_avg,
          mention_count, unique_authors, attention_score, credibility_score,
          cross_platform_score, market_score, false_positive_penalty
   FROM ticker_signals
   WHERE ticker = ?
   ORDER BY cycle_at_utc DESC
   LIMIT 1;
   ```

2. Top 3 posts by engagement in the last 30 min:
   ```sql
   SELECT rp.source, rp.text, rp.url, pp.signal_type, pp.catalyst_type
   FROM processed_posts pp
   JOIN raw_posts rp ON rp.id = pp.raw_post_id
   WHERE pp.ticker = ?
     AND rp.created_at_utc > datetime('now', '-30 minutes')
   ORDER BY (rp.engagement_likes + rp.engagement_reposts * 2 + rp.engagement_comments * 3) DESC
   LIMIT 3;
   ```

3. Latest market snapshot:
   ```sql
   SELECT price, change_pct, volume, avg_volume_20d
   FROM market_quotes
   WHERE ticker = ?
   ORDER BY snapshot_at_utc DESC
   LIMIT 1;
   ```

## Priority determination

- composite_score ≥ 0.7 → **HIGH**
- 0.5 ≤ composite_score < 0.7 → **MEDIUM**
- < 0.5 → should have been filtered out already; return `"error: score too low to alert"`

## Message format

### HIGH priority (single alert, immediate)

```
🚨 ALERT: ${TICKER} | Score {SCORE} | HIGH PRIORITY

📊 Signal: {phase} — mentions {ratio}x baseline
💬 {unique_authors} voices, sentiment {sentiment_avg_pretty}
📰 Catalyst: {inferred_catalyst_or_"none cited"}
💹 Price: {price} ({change_pct_signed}%), volume {vol_ratio}x avg

🔗 Top discussion:
• {source1}: "{text1_truncated_80}"
• {source2}: "{text2_truncated_80}"

⚠️ Risk: {fp_penalty_warning_or_"organic spread"}
```

### MEDIUM priority (shorter, hourly digest)

```
📊 ${TICKER} | Score {SCORE} | {phase}
{ratio}x mentions, {unique_authors} voices, {sentiment_direction} sentiment
Price {price} ({change_pct_signed}%) | Vol {vol_ratio}x
Top: "{text1_truncated_60}"
```

### Formatting rules

- **Emojis:** keep them purposeful. Don't overload.
- **Score:** show to 2 decimals, e.g. `0.78`
- **Change %:** prefix with `+` for positive, `-` for negative (never unsigned)
- **Ratio:** `{N}x` not `{N*100}%`
- **Sentiment:** map sentiment_avg to English — `>0.5` = "strongly bullish", `0.15..0.5` = "bullish", `-0.15..0.15` = "mixed", `<-0.5` = "strongly bearish"
- **Text truncation:** cut at the nearest space, append `…` if truncated
- **Newlines:** use `\n` (not literal newlines in generated SQL/JSON); Telegram renders them
- **No HTML/Markdown in messages** — plain text only. Telegram can render Markdown but it's picky about escaping, plain is safer.

### Catalyst inference

If the top posts have `catalyst_type` values, use the most common non-`none` one. Map:
- `earnings` → "earnings-related"
- `product` → "product/partnership announcement"
- `regulatory` → "regulatory development"
- `insider` → "insider activity reports"
- `macro` → "macro catalyst"
- `none` → "no specific catalyst cited"

### FP penalty warning

If `false_positive_penalty > 0.3`, append a warning line:
- `0.3-0.5` → "⚠️ Meme-heavy; sentiment may be unreliable."
- `0.5-0.7` → "⚠️ Meme-dominated. Treat as attention signal only."
- `>0.7` → "🚨 Likely spam/coordinated. Avoid acting on this alone."

## Execution

Produce the alert text as plain output. Do NOT wrap in JSON, do NOT add commentary before/after. The cron's `deliver` field will route this text to the Telegram home channel as the message body.

For multi-ticker alerts in a single cron run, emit each alert on its own with a `---` separator between them.

## What NOT to do

- Do not call `trader-reasoning` from inside this skill. That's Sage's job. If the user wants deep reasoning, they can ask explicitly.
- Do not include a "buy/sell" recommendation. This is a signal alert, not trading advice.
- Do not include stop-loss, price targets, or position sizing. Never.
- Do not include the raw ticker_signals or market_quotes JSON. Keep the message human-readable.
- Do not write to `alerts_log` — that's `cooldown-check`'s table. format-alert is pure composition.
