---
name: trader-reasoning
description: Produce a structured senior-trader evaluation for a single ticker — source credibility, novelty, breadth, speed, catalyst, likely impact, direction, manipulation risk, signal phase, action, what to monitor next. Runs on-demand when composite_score >= 0.5.
version: 0.1.0
author: Faisal Alnamlah
license: MIT
platforms: [linux]
prerequisites:
  commands: [sqlite3]
metadata:
  hermes:
    tags: [analysis, reasoning, llm, trader, sage]
    profile: sage
---

# trader-reasoning — Per-ticker trader evaluation

Structured evaluation for a single ticker at a single point in time. Pulls the top posts + latest market snapshot + current ticker_signals row, then runs a 12-field trader assessment. The output is designed to be consumed by Herald (in P3) when composing alerts.

This is **not** a cycle-level skill. It's invoked **per ticker**, when the user or Herald wants a deep assessment. Running it on every cycle would be expensive and low-value.

## When to use this skill

- When a ticker crosses `composite_score >= 0.5` (Herald triggers this before formatting an alert)
- When the user asks "what's going on with $NVDA"
- As a one-shot exploratory tool — before deciding to add a ticker to the monitored list

## Input

A ticker symbol. Example: `NVDA`, `SMCI`, `PLTR`.

## What to gather before reasoning

1. Current cycle's `ticker_signals` row:
   ```sql
   SELECT composite_score, mention_count, unique_authors, sentiment_avg,
          ratio_vs_baseline, phase, attention_score, credibility_score,
          momentum_score, novelty_score, market_score, false_positive_penalty
   FROM ticker_signals
   WHERE ticker = ?
   ORDER BY cycle_at_utc DESC
   LIMIT 1;
   ```

2. Top 15 posts by engagement in the current 30-min window:
   ```sql
   SELECT rp.source, rp.author, rp.text, rp.engagement_likes, rp.engagement_reposts,
          pp.sentiment, pp.signal_type, pp.catalyst_type, pp.credibility_estimate
   FROM processed_posts pp
   JOIN raw_posts rp ON rp.id = pp.raw_post_id
   WHERE pp.ticker = ?
     AND rp.created_at_utc > datetime('now', '-30 minutes')
   ORDER BY (rp.engagement_likes + rp.engagement_reposts * 2 + rp.engagement_comments * 3) DESC
   LIMIT 15;
   ```

3. Latest market snapshot:
   ```sql
   SELECT price, volume, change_pct, avg_volume_20d, prev_close, snapshot_at_utc
   FROM market_quotes
   WHERE ticker = ?
   ORDER BY snapshot_at_utc DESC
   LIMIT 1;
   ```

## The 12-field assessment

Produce a JSON object with exactly these fields:

```json
{
  "ticker": "NVDA",
  "assessed_at_utc": "2026-04-20T15:23:00Z",
  "source_credibility": "medium",
  "novelty": "new",
  "breadth": "moderate",
  "speed": "fast",
  "catalyst": {"type": "rumor", "summary": "AI chip partnership with major cloud provider"},
  "likely_impact": "momentum",
  "direction": "bullish",
  "manipulation_risk": "organic",
  "signal_phase": "accelerating",
  "cross_platform": "cross_confirmed",
  "action": "watchlist",
  "monitor_next": "Watch for official press release or denial; check pre-market volume"
}
```

Constraints:
- `source_credibility` ∈ `{"low", "medium", "high"}`
- `novelty` ∈ `{"new", "recycled", "escalation"}`
- `breadth` ∈ `{"narrow", "moderate", "broad"}`
- `speed` ∈ `{"slow", "moderate", "fast", "viral"}`
- `catalyst.type` ∈ `{"none", "rumor", "confirmed_event"}`; `catalyst.summary` is 1-line English
- `likely_impact` ∈ `{"noise", "volatility_only", "momentum", "significant"}`
- `direction` ∈ `{"bullish", "bearish", "mixed"}`
- `manipulation_risk` ∈ `{"organic", "suspicious", "likely_coordinated"}`
- `signal_phase` ∈ `{"emerging", "accelerating", "peaking", "fading"}`
- `cross_platform` ∈ `{"single_platform", "cross_confirmed"}`
- `action` ∈ `{"monitor", "watchlist", "potential_opportunity", "avoid"}`
- `monitor_next` is 1-2 sentences — what specifically would confirm or invalidate the signal

## Reasoning rubric (condensed from V1)

**source_credibility:** weight the top 3 voices. One credible journalist + amplification = medium. All anonymous low-karma = low.

**novelty:** is this thesis new today? Cross-check `first_seen_hours_ago` from ticker_signals.

**breadth:** count unique authors, not posts. 5+ independent voices = moderate. 15+ = broad.

**speed:** ratio_vs_baseline > 5x = fast. > 10x = viral.

**catalyst:** look in post text for specific events (earnings date, FDA decision, buyback, partnership rumor). If none found, set `"none"`.

**likely_impact:** match score to action. `significant` = score > 0.7 and market move corroborates. `noise` = score < 0.3 or high FP penalty.

**direction:** use sentiment_avg from ticker_signals. Bias toward `mixed` if sentiment is within ±0.15.

**manipulation_risk:** high spam_avg, low author diversity, repeated text → `likely_coordinated`. Use `suspicious` when unsure, reserve `likely_coordinated` for clear cases.

**action:**
- `avoid` if manipulation_risk = likely_coordinated OR composite_score < 0.3
- `monitor` if score 0.3-0.5
- `watchlist` if 0.5-0.7
- `potential_opportunity` if > 0.7 AND (cross_platform = cross_confirmed OR market corroborates)

## Output format

Print the JSON to stdout (one object, no surrounding text). Herald will parse it for alert composition. The JSON must be parseable — no comments, no trailing commas.

In conversational contexts (user asked directly), also print a 2-3 sentence English summary *after* the JSON, separated by a blank line.

## Cost guardrails

- One invocation ≈ 3-5K input tokens (posts + signals) + 500 output = ~$0.015 with Claude Haiku
- Don't invoke for every ticker every cycle — only when score ≥ 0.5 or the user asks

## Failure modes to handle

- No posts in last 30 min → report "insufficient fresh data", return with `action: "monitor"` and explain
- No market_quotes row → proceed with social-only reasoning, note in `monitor_next`
- Ticker not in ticker_signals → means no cycle has scored it yet, invoke composite-score first

## What NOT to do

- Do not write to `alerts_log` — that's Herald. Trader-reasoning output is an input to Herald's alert composition, not the alert itself.
- Do not recommend position sizes, stop losses, or price targets — this is a signal quality assessment, not trading advice.
- Do not speculate about catalysts not mentioned in the source posts. If no specific event is cited, catalyst is `"none"`.
- Do not run this on every ticker every cycle. Triggered invocation only.
