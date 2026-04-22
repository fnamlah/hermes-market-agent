---
name: cooldown-check
description: Enforce per-ticker cooldowns and global daily rate limit on alerts. Checks alerts_log before emitting, writes a row when an alert is approved for emission. Must be called BEFORE format-alert for any ticker.
version: 0.1.0
author: Faisal Alnamlah
license: MIT
platforms: [linux]
prerequisites:
  commands: [sqlite3]
metadata:
  hermes:
    tags: [alerts, cooldown, rate-limit, herald]
    profile: herald
---

# cooldown-check — Alert gating

Pure gating logic. Given a ticker and its current composite_score, decide whether an alert should emit this cycle. Returns `"emit"` or `"suppress:<reason>"`. If emit, inserts a row into `alerts_log` so subsequent cycles see the cooldown.

This skill is the **first** step of the alert pipeline. Call it before `format-alert` — skip format-alert if this returns suppress.

## When to use this skill

- Once per ticker per alert cycle, before formatting
- As a read-only check (dry-run) when the user asks "why didn't $NVDA alert"

## Input

A ticker + current composite_score + priority tier (`high`, `medium`, `watchlist`, or auto-compute from score).

## Rules

### Rate limit (global)
Max **8 alerts per day**. Check:
```sql
SELECT COUNT(*) FROM alerts_log
WHERE sent_at_utc > datetime('now', '-24 hours');
```
If ≥ 8 → suppress with reason `"daily_cap_reached (8/day)"`.

### Priority-specific cooldown (per-ticker)

| Priority | Cooldown | Override condition |
|---|---|---|
| High (score ≥ 0.7) | 1 hour | score jumped by ≥ 0.2 since last alert |
| Medium (0.5 ≤ score < 0.7) | 2 hours | score jumped by ≥ 0.2 |
| Watchlist (0.3 ≤ score < 0.5) | 4 hours | score jumped by ≥ 0.3 |
| Below 0.3 | always suppress | — |

Cooldown check:
```sql
SELECT priority, signal_score, sent_at_utc
FROM alerts_log
WHERE ticker = ?
ORDER BY sent_at_utc DESC
LIMIT 1;
```

If the last alert for this ticker is within the cooldown window:
- Compute score delta: `current_score - last_alert.signal_score`
- If delta ≥ the priority's override threshold → allow through (this is an **escalation** — note it in the output message via format-alert)
- Otherwise → suppress with reason `"cooldown_active (last alert {N}m ago, delta +{D})"`

### Hard filters (always suppress)

Even if cooldown is clear:
- `false_positive_penalty >= 0.7` → `"likely_spam_or_coordinated"`
- `unique_authors < 5` → `"insufficient_breadth"`
- **`cycle_at_utc` older than 20 minutes** → `"stale_signal (last cycle Nm ago)"` — prevents firing alerts on ancient ticker_signals rows when Sage hasn't written a fresh cycle (e.g., no new classified posts in the last window). Critical: without this guard, Herald re-alerts on the same stale row every cooldown expiration, which is a false-positive factory.

Pull these from the ticker_signals row for the current cycle.

```sql
-- Check staleness
SELECT ticker, composite_score, cycle_at_utc,
       (julianday('now') - julianday(cycle_at_utc)) * 24 * 60 AS minutes_old
FROM ticker_signals
WHERE ticker = ?
ORDER BY cycle_at_utc DESC
LIMIT 1;
```

If `minutes_old > 20` → suppress.

## Execution flow

```
1. Read most recent ticker_signals row for the ticker.
2. Apply hard filters. If suppressed, return reason.
3. Apply rate limit. If suppressed, return reason.
4. Look up last alert in alerts_log for this ticker.
5. Apply priority-specific cooldown. If suppressed, return reason.
6. Otherwise — INSERT a row into alerts_log and return "emit:<priority>".
```

### SQL for logging an approved alert

```sql
INSERT INTO alerts_log (ticker, priority, signal_score, channel)
VALUES (?, ?, ?, 'telegram');
```

Note: `message_body` is filled in by the caller AFTER format-alert runs — or stays NULL if the system crashes between cooldown-check and format-alert (not ideal but acceptable). For perfect atomicity, format first then INSERT inside cooldown-check. Simpler to do it in this order and accept the rare inconsistency.

## Output format

Return one of:
- `emit:high` — high-priority alert approved, insert row, proceed to format-alert
- `emit:medium` — medium-priority alert approved
- `emit:watchlist` — watchlist addition (no formatting, just log)
- `suppress:<reason>` — don't emit

## Examples

```
$ cooldown-check NVDA 0.78
emit:high

$ cooldown-check NVDA 0.72
suppress:cooldown_active (last alert 15m ago, delta -0.06)

$ cooldown-check MEME 0.55
suppress:likely_spam_or_coordinated

$ cooldown-check ANY 0.80
suppress:daily_cap_reached (8/day)
```

## What NOT to do

- Do not call format-alert from inside this skill — cooldown-check is pure gating, not message composition.
- Do not write `message_body` here (that's filled by the alert pipeline after format-alert).
- Do not override the daily cap "just this once" — the cap is there to prevent alert fatigue. If the user explicitly asks for more than 8/day, raise it in config, don't bypass here.
- Do not use "cooldown reset" as a fallback for high-score signals — the escalation override handles that.
