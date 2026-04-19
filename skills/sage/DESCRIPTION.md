# sage — Market Intelligence Analysis

Skills in this category handle **analysis** for the Hermes Market Agent. They read Scout's raw observations (`raw_posts`, `market_quotes`) and produce structured signals (`processed_posts`, `ticker_signals`) for Herald (reporting) to act on.

**Principle:** Sage never talks to external APIs. It only reads from and writes to the local SQLite DB. All LLM-based reasoning uses the Hermes runtime's own model. All math is deterministic Python.

## Skills

- `sentiment-classify` — Batch-classify unprocessed raw_posts for sentiment, signal type, catalyst, spam probability. Writes to processed_posts.
- `composite-score` — Aggregate mentions per ticker per cycle, compute attention/momentum/novelty/cross-platform components, output composite score. Writes to ticker_signals. Subsumes spike detection and cross-platform confirmation (they're inputs to the score, not separate outputs).
- `trader-reasoning` — Structured per-ticker evaluation mirroring V1's 12-field trader prompt. Invoked on-demand by Herald for score ≥ 0.5.

## Shared dependencies

- `/data/hermes-market.db` (populated by scout skills)
- `python3` + standard library (no extra pip packages needed beyond P1)
- `sqlite3` CLI for simple queries

## Typical cron pattern

```json
{
  "name": "sage-analysis-cycle",
  "schedule": "*/15 9-16 * * 1-5",
  "prompt": "Run one analysis cycle: call sentiment-classify on unprocessed posts from the last 30 min, then composite-score to refresh ticker_signals. Report any tickers where composite_score crossed 0.5.",
  "skills": ["sentiment-classify", "composite-score"],
  "deliver": "local"
}
```

Trigger Scout first (P1 cron), Sage second — or chain them by having one cron job that loads all Scout + Sage skills.

See the plan at `~/.claude/plans/ok-for-now-plan-frolicking-bear.md` for the full architecture (Scout + Sage + Herald + Cognee).
