---
name: apify-x
description: Scrape X (Twitter) for US stock market signals using Apify Tweet Scraper V2. Use for cashtag searches ($AAPL, $TSLA), keyword discovery, or monitoring accounts. Writes rows to the raw_posts table in /data/hermes-market.db.
version: 0.1.0
author: Faisal Alnamlah
license: MIT
platforms: [linux]
prerequisites:
  env: [APIFY_TOKEN]
  commands: [curl, jq, sqlite3]
metadata:
  hermes:
    tags: [scraping, x, twitter, apify, market-data]
    profile: scout
---

# apify-x — X/Twitter scraping via Apify

Scrapes X for US stock market discussion. Writes each unique post to `/data/hermes-market.db` in the `raw_posts` table with `source='x'`. Deduplication is handled by the SQL UNIQUE constraint on `(source, external_id)`.

## When to use this skill

- Scheduled market-hours cycle (every 15 min) to pull recent cashtag mentions
- One-off scrape of a specific ticker when the user asks about social chatter
- Monitoring a set of financial journalist accounts for breaking news

## Required environment

- `APIFY_TOKEN` — set as a Railway service variable

## How to invoke Apify

The **Apify Tweet Scraper V2** actor ID is `apidojo~tweet-scraper`. Synchronous run endpoint:

```
POST https://api.apify.com/v2/acts/apidojo~tweet-scraper/run-sync-get-dataset-items?token=$APIFY_TOKEN
Content-Type: application/json

{
  "searchTerms": ["$AAPL", "$TSLA", ...],
  "tweetLanguage": "en",
  "maxItems": 200,
  "sort": "Latest"
}
```

**Read monitored tickers from the database** (don't hardcode):

```bash
TICKERS=$(sqlite3 /data/hermes-market.db "SELECT value FROM config WHERE key='monitored_tickers';" | jq -r '.[]' | sed 's/^/$/' | jq -R . | jq -s .)
```

## Writing to SQLite

One row per unique post. The response is a JSON array — each item has `id`, `text`, `url`, `createdAt`, `author.userName`, `author.followers`, `likeCount`, `retweetCount`, `replyCount`.

Insert with `INSERT OR IGNORE` (the UNIQUE constraint handles dedup):

```bash
sqlite3 /data/hermes-market.db <<SQL
INSERT OR IGNORE INTO raw_posts(
    source, external_id, author, author_followers, text, url,
    created_at_utc, engagement_likes, engagement_reposts, engagement_comments, raw_json
) VALUES (
    'x', :id, :author, :followers, :text, :url,
    :created_at, :likes, :retweets, :replies, :raw_json
);
SQL
```

Use a heredoc with one INSERT per post, OR write the response to a temp file and stream with `jq -c '.[]' | while read row; do ... done`.

## Minimum viable execution

For a scheduled cycle, the agent should:

1. Read monitored tickers from config
2. Build `searchTerms` as `$TICKER` for each
3. Call Apify sync endpoint with `maxItems: 200`, `sort: "Latest"`
4. Stream the JSON response, INSERT OR IGNORE each post
5. Report: `apify-x: <N> new posts inserted across <M> tickers`

## Cost guardrails

- Apify charges **$0.15 per 1K tweets**. `maxItems: 200` × 20 tickers × 96 cycles/day = ~$58/day worst case. Cap with `maxItems: 100` or batch multiple tickers per run.
- Use `tweetsDesired` to limit per-ticker results.
- Apify has a free tier (~$5/month credit). After that, monitor spend in the Apify console.

## Failure modes to handle

- `429 Too Many Requests` → backoff 30s, retry once
- Empty dataset → fine, just log and move on (markets closed, no chatter)
- Malformed JSON → log the raw response, skip the cycle, alert operator via Telegram
- Missing `APIFY_TOKEN` → hard fail with a clear error, do not silently skip

## What NOT to do

- Do not delete or UPDATE existing rows in `raw_posts`. Append-only.
- Do not fetch older data (`sort: "Top"`, historical dates) unless the user explicitly asks — that multiplies cost.
- Do not log the full tweet text at info level — spammy.
