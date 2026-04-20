---
name: apify-reddit
description: Scrape Reddit for US stock market discussion using Apify Reddit Scraper. Monitors r/wallstreetbets, r/stocks, r/investing, r/options. Writes rows to the raw_posts table in /data/hermes-market.db.
version: 0.1.0
author: Faisal Alnamlah
license: MIT
platforms: [linux]
prerequisites:
  env: [APIFY_TOKEN]
  commands: [curl, jq, sqlite3]
metadata:
  hermes:
    tags: [scraping, reddit, apify, market-data]
    profile: scout
---

# apify-reddit — Reddit scraping via Apify

Scrapes Reddit for US stock discussion. Writes each unique post to `/data/hermes-market.db` in the `raw_posts` table with `source='reddit'`. Includes the subreddit name so Sage can weight by community quality later.

## When to use this skill

- Scheduled market-hours cycle (every 15 min for Tier 1 subs, every 30 min for Tier 2)
- DD post discovery (deep-dive research posts often lead X by hours)
- Cross-platform confirmation (same ticker trending on Reddit + X = stronger signal)

## Required environment

- `APIFY_TOKEN` — set as a Railway service variable

## How to invoke Apify

The **Apify Reddit Scraper Lite** (free — pay-per-result only, no monthly rental). Actor: `trudax~reddit-scraper-lite`. Synchronous run endpoint:

```
POST https://api.apify.com/v2/acts/trudax~reddit-scraper-lite/run-sync-get-dataset-items?token=$APIFY_TOKEN
Content-Type: application/json

{
  "startUrls": [
    {"url": "https://www.reddit.com/r/wallstreetbets/new/"},
    {"url": "https://www.reddit.com/r/stocks/new/"},
    {"url": "https://www.reddit.com/r/investing/new/"},
    {"url": "https://www.reddit.com/r/options/new/"}
  ],
  "sort": "new",
  "maxItems": 30,
  "maxPostCount": 10,
  "maxComments": 0,
  "skipComments": true,
  "skipUserPosts": true,
  "skipCommunity": true,
  "proxy": {"useApifyProxy": true, "apifyProxyGroups": ["RESIDENTIAL"]}
}
```

**Cost note:** `maxItems: 30` × $0.004/post × 24 cycles/day = ~$2.9/day worst-case. The paid `trudax/reddit-scraper` parent actor went rental-only; this lite variant is the drop-in replacement with the same input schema. Do not raise maxItems past 50 without checking spend on the Apify dashboard.

For cycles that target Tier 1 subs only, skip `searches` and use `startUrls` — cheaper and faster.

## Read monitored subs from config

```bash
TIER1_SUBS=$(sqlite3 /data/hermes-market.db "SELECT value FROM config WHERE key='monitored_subreddits_tier1';" | jq -r '.[]')
START_URLS=$(echo "$TIER1_SUBS" | jq -R '"https://www.reddit.com/r/" + . + "/new/"' | jq -s '[.[] | {url: .}]')
```

## Writing to SQLite

Each Apify response item has: `id`, `title`, `body`, `url`, `createdAt`, `username`, `upVotes`, `numberOfComments`, `parsedCommunityName`. Concatenate `title + "\n\n" + body` into the `text` column.

```bash
sqlite3 /data/hermes-market.db <<SQL
INSERT OR IGNORE INTO raw_posts(
    source, external_id, author, text, url, created_at_utc,
    engagement_likes, engagement_comments, subreddit, raw_json
) VALUES (
    'reddit', :id, :username, :text, :url, :created_at,
    :upvotes, :num_comments, :sub, :raw_json
);
SQL
```

The UNIQUE constraint on `(source, external_id)` handles dedup across re-runs.

## Minimum viable execution

1. Read Tier 1 subs from config
2. Build `startUrls` from those subs
3. Call Apify sync endpoint, `maxItems: 200`
4. Stream response, INSERT OR IGNORE each post
5. Report: `apify-reddit: <N> new posts across <M> subreddits`

## Content-type weighting (for Sage to use later)

Annotate the signal quality of each subreddit implicitly by ordering in config:

- Tier 1 (continuous): WSB, stocks, investing, options → highest signal density
- Tier 2 (every 30 min): StockMarket, Daytrading, pennystocks → emerging signals
- Tier 3 (2-3x daily): sector-specific → narrative discovery

Scout doesn't need to interpret this — it just scrapes. Tier weighting lives in Sage's scoring (P2).

## Cost guardrails

- Apify Reddit scraper is **~$0.025 per 1K items** — cheaper than X.
- Full Tier 1 sweep should cost well under $1/day at 15-min cadence.
- Avoid `maxComments: N > 0` unless you really need comment analysis — it multiplies cost linearly.

## Failure modes to handle

- Rate-limited (429) → backoff 60s, single retry
- Apify actor returns error (e.g., subreddit removed) → log, continue with other subs
- Empty dataset on one sub is normal (quiet hours)
- Missing `APIFY_TOKEN` → hard fail

## What NOT to do

- Do not run with `maxComments > 0` on every cycle — too expensive
- Do not crosspost-dedup here; that's Sage's job (in processed_posts)
- Do not UPDATE existing rows — append only
