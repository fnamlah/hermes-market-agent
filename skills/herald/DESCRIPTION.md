# herald — Market Intelligence Alert Delivery

Skills in this category handle **alerts and reports** for the Hermes Market Agent. They read ticker_signals produced by Sage and produce message output that Hermes's messaging gateway delivers to the user.

**Principle:** Herald does not send messages directly. When invoked from a cron job with `deliver: "telegram"` (or whatever home-channel the user set), Hermes automatically routes the skill's output to the messaging gateway. Herald's job is to **compose the right text** and **suppress noise** (cooldowns, rate limits, dedup) — Hermes handles transport.

## Skills

- `format-alert` — Compose a concise alert message for a single ticker crossing the priority threshold. Reads from ticker_signals + processed_posts + market_quotes.
- `cooldown-check` — Enforce cooldown rules and global rate limits. Checks alerts_log before emitting, writes a row when an alert is emitted.
- `daily-brief` — End-of-day market-close summary (top 10 tickers by daily score, emerging narratives, fading narratives, suppressed false signals).

## Threshold logic (from V1, implemented inside the skills)

| Composite score | Action |
|---|---|
| ≥ 0.7 | **High priority** — immediate alert |
| 0.5 – 0.7 | **Medium priority** — hourly digest |
| 0.3 – 0.5 | Watchlist (no alert; dashboard only) |
| < 0.3 | Suppress |

With cooldowns:
- High-priority: 1h per ticker (overridable if score jumps by ≥ 0.2)
- Medium-priority: 2h per ticker
- Watchlist: 4h before re-evaluation
- Global rate limit: **8 alerts/day** across all tickers

## Typical cron patterns

**Market-hours alert pass** (every 15 min, 09:30-16:00 ET):
```json
{
  "name": "herald-alert-pass",
  "schedule": "*/15 13-20 * * 1-5",
  "prompt": "Check ticker_signals from the last 20 minutes for any ticker with composite_score >= 0.5. For each, call cooldown-check to verify we can emit. If allowed, call format-alert to compose the message. Respond with the alert text, or 'no alerts this cycle' if nothing qualifies.",
  "skills": ["cooldown-check", "format-alert"],
  "deliver": "telegram"
}
```

**Daily brief** (4:30 PM ET, weekdays):
```json
{
  "name": "herald-daily-brief",
  "schedule": "30 20 * * 1-5",
  "prompt": "Generate today's social market brief using the daily-brief skill. Cover top 10 tickers by composite_score, emerging and fading narratives, false signals suppressed, and tickers to watch tomorrow.",
  "skills": ["daily-brief"],
  "deliver": "telegram"
}
```

A reference template with all cron jobs lives at `/app/shared/default-cron-jobs.json`.

See the plan at `~/.claude/plans/ok-for-now-plan-frolicking-bear.md` for the full architecture.
