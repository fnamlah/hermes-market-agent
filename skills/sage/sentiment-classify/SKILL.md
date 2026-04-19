---
name: sentiment-classify
description: Batch-classify unprocessed raw_posts for sentiment, signal type, catalyst, and spam probability. Reads from raw_posts, writes to processed_posts with ticker, sentiment, sentiment_intensity, confidence, signal_type, catalyst_type, credibility_estimate, and spam_score.
version: 0.1.0
author: Faisal Alnamlah
license: MIT
platforms: [linux]
prerequisites:
  commands: [sqlite3, python3]
metadata:
  hermes:
    tags: [analysis, sentiment, llm, nlp, market]
    profile: sage
---

# sentiment-classify — Batch sentiment + signal classification

Pulls unprocessed posts from `raw_posts` (posts with no corresponding row in `processed_posts`), runs structured classification on each, and writes the results.

This skill does the heavy LLM work. Keep batches small (20 posts max per call) so the model can produce reliable structured output without truncation.

## When to use this skill

- As the first step of a Sage analysis cycle (every 15 min during market hours)
- One-off when the user asks about sentiment for a specific ticker and the posts aren't classified yet
- After a manual Scout run, to catch up before composite-score

## What to extract per post

For each post, produce a JSON object with these fields (exact types matter for the SQL insert):

```json
{
  "ticker": "NVDA",
  "sentiment": "bullish",
  "sentiment_intensity": 0.85,
  "confidence": 0.9,
  "signal_type": "catalyst",
  "catalyst_type": "earnings",
  "is_forward_looking": true,
  "credibility_estimate": "medium",
  "spam_score": 0.1,
  "is_meme": false
}
```

Constraints:
- `sentiment` ∈ `{"bullish", "bearish", "neutral", "mixed"}`
- `sentiment_intensity`, `confidence`, `spam_score` ∈ [0.0, 1.0]
- `signal_type` ∈ `{"catalyst", "rumor", "meme", "hype", "analysis", "spam", "news_reaction"}`
- `catalyst_type` ∈ `{"earnings", "product", "regulatory", "insider", "macro", "none"}`
- `credibility_estimate` ∈ `{"low", "medium", "high"}`
- `is_forward_looking`, `is_meme` are booleans (stored as 1/0 in SQL)

If a post mentions multiple tickers, produce one record per ticker-post pair.

## Classification rubric

**Bullish / bearish / neutral / mixed:** obvious overall direction. "Mixed" means the post contains both sides substantively. Single viral post ≠ "mixed" just because someone disagrees in comments.

**signal_type:**
- `catalyst` = specific confirmed event (earnings beat, FDA approval, buyback)
- `rumor` = unverified claim about future event
- `meme` = image/hype content, low analytical depth, WSB-style "tendies"/"diamond hands"
- `hype` = promotional, "to the moon", "guaranteed 10x", often pump-adjacent
- `analysis` = detailed DD with data and reasoning
- `spam` = coordinated promotion, cashtag piggybacking, new account
- `news_reaction` = commenting on already-public news

**credibility_estimate:** based on author signals (follower count, account age, subreddit quality) + post quality (specific vs vague, numbers vs adjectives). DD posts on r/investing with >100 comments → high. Anonymous new account WSB meme → low.

**spam_score:** apply WSB sarcasm discount — "this is the way" / "apes together strong" = 0.0 (not spam, just WSB culture). But "guaranteed 1000x NOW" from new account = 0.9.

**is_meme:** true if post is primarily a meme image, reaction GIF, or contains ≥50% meme language. Meme posts still count for attention but with reduced sentiment confidence.

## Execution flow

1. Query unprocessed posts:
   ```sql
   SELECT rp.id, rp.source, rp.author, rp.text, rp.created_at_utc, rp.subreddit,
          rp.author_followers, rp.engagement_likes
   FROM raw_posts rp
   LEFT JOIN processed_posts pp ON pp.raw_post_id = rp.id
   WHERE pp.id IS NULL
     AND rp.ingested_at_utc > datetime('now', '-2 hours')
   ORDER BY rp.ingested_at_utc
   LIMIT 100;
   ```
   Limit to 100 at a time; if there are more, run multiple cycles.

2. Chunk into batches of 20 posts (the LLM can reliably handle that many in one structured output call).

3. For each batch, produce a JSON array of classification objects — one object per (post, ticker) pair. Posts with zero tickers are skipped (not inserted).

4. Write results:
   ```sql
   INSERT INTO processed_posts (
       raw_post_id, ticker, sentiment, sentiment_intensity, confidence,
       signal_type, catalyst_type, is_forward_looking, credibility_estimate,
       spam_score, is_meme
   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
   ```
   Use parameterized queries (never string-interpolate ticker or text).

5. Report: `sentiment-classify: <N> posts classified, <M> records written (avg <K> tickers per post)`.

## Cost guardrails

- Batch of 20 posts ≈ ~2K input + 1K output tokens with Claude Haiku = ~$0.007 per batch
- 500 posts/cycle × 96 cycles/day = ~$5/day at full throttle; expect $1-2/day realistic
- If OpenRouter credit gets low, switch to `openai/gpt-4o-mini` (cheaper, fine for this task)

## Failure modes to handle

- Malformed LLM JSON → retry once with "respond in strict JSON only"; on second failure, skip that batch and log
- Post has no ticker mention → skip (don't write an empty row)
- Ticker is a common English word (`A`, `ALL`, `BE`) → require cashtag or strong context; otherwise skip
- Duplicate classification (same raw_post_id + ticker) → UNIQUE constraint... actually the schema doesn't enforce this, so use INSERT OR IGNORE with a manual check: `WHERE NOT EXISTS (SELECT 1 FROM processed_posts WHERE raw_post_id=? AND ticker=?)`

## What NOT to do

- Do not call external APIs. Sentiment classification uses the Hermes runtime's own LLM.
- Do not UPDATE existing processed_posts — if classification improves, re-run on new data, don't rewrite history.
- Do not compute composite scores here — that's composite-score's job.
- Do not skip posts with low confidence — record them with low confidence values. Filtering happens in scoring.
