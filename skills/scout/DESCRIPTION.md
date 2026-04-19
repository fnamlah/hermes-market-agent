# scout — Market Intelligence Data Ingestion

Skills in this category handle **data collection** for the Hermes Market Agent. They scrape social platforms and pull market data, writing raw observations to `/data/hermes-market.db` for Sage (analysis) and Herald (reporting) to consume.

**Principle:** Scout is append-only. It never interprets, never deletes. It writes clean rows into the raw data lake.

## Skills

- `apify-x` — X/Twitter cashtag + keyword scraping via Apify
- `apify-reddit` — Reddit subreddit scraping via Apify (WSB, stocks, investing, options)
- `market-data` — Stock price, volume, OHLC via yfinance (free) or Polygon.io (paid)

## Shared dependencies

- `APIFY_TOKEN` env var (for apify-* skills)
- `POLYGON_API_KEY` env var (optional, for market-data upgrade)
- `/data/hermes-market.db` SQLite database (initialized on container start from `shared/schema.sql`)
- `sqlite3`, `jq`, `curl`, `python3` CLIs

## Typical cron pattern

```json
{
  "name": "scout-cycle-market-hours",
  "schedule": "*/15 9-16 * * 1-5",
  "prompt": "Run one ingestion cycle: call apify-x, apify-reddit, then market-data. Report counts.",
  "skills": ["apify-x", "apify-reddit", "market-data"],
  "deliver": "local"
}
```

See the plan at `~/.claude/plans/ok-for-now-plan-frolicking-bear.md` for the full architecture (Scout + Sage + Herald + Cognee).
