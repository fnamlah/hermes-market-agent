---
name: recall
description: Query Cognee's accumulated memory with a natural-language question. Returns semantically similar + graph-traversed results. Used by trader-reasoning for historical context, or directly by the user asking "what do we know about X".
version: 0.1.0
author: Faisal Alnamlah
license: MIT
platforms: [linux]
prerequisites:
  commands: [python3]
  files: [/app/shared/cognee_setup.py]
  env: [OPENAI_API_KEY]
metadata:
  hermes:
    tags: [memory, cognee, recall, retrieval]
    profile: memory
---

# recall — Query Cognee memory

Asks Cognee to retrieve relevant accumulated knowledge for a question. Uses `SearchType.GRAPH_COMPLETION` — enters via semantic similarity (vector), traverses the knowledge graph to find related context, returns reasoned results.

## When to use this skill

- **From `trader-reasoning`** — when evaluating a ticker, call `recall "historical narrative and signals for $TICKER"` to surface past insights
- **User questions** — "what did we say about $SMCI last week", "what was the bull thesis on AI infrastructure in March"
- **Pre-alert context** — before Herald emits a high-priority alert, recall prior events on the ticker (detect recycled narratives)

## Activation check

Run `python3 /app/shared/cognee_setup.py status` first. If "not configured", tell the user that memory is disabled and explain how to enable (same message as `remember` skill).

## How to invoke

```bash
python3 /app/shared/cognee_setup.py recall "historical narratives on NVDA AI partnerships" --limit 5
```

### Output format

Numbered list of up to `--limit` results, each truncated to 500 chars:

```
1. On 2026-04-18, $NVDA crossed score 0.82 on rumored AWS partnership...
2. Trader assessment 2026-04-15 flagged $NVDA as "likely_coordinated" when...
3. ...
```

If no results, output: `(no relevant memory found)` — this is normal for tickers we haven't accumulated much on yet.

### Exit codes

- `0` — query ran (results may be empty)
- `1` — search failed — transient
- `3` — not configured
- `4` — package missing

## Query design guidance

- **Be specific.** "NVDA partnership rumors" beats "tell me about NVDA".
- **Include timeframes when relevant.** "Bullish NVDA sentiment in April 2026" narrows the graph.
- **Avoid yes/no questions.** Cognee isn't a boolean oracle; it surfaces evidence. Ask "what do we know about X" not "did X happen".

## Chaining with other skills

Example: `trader-reasoning` invokes recall as a preprocessing step:

1. User asks for trader eval on $NVDA
2. trader-reasoning calls `recall "recent trader assessments and catalysts for NVDA"` to build historical context
3. trader-reasoning incorporates recalled context into the 12-field JSON output under `monitor_next` or in a "prior context" preamble

## Cost guardrails

- Each recall call = one embedding query + graph traversal + (optional) LLM summarization
- Cost: ~$0.001 per query with gpt-4o-mini
- No rate limit needed — queries are cheap; ingestion is the expensive side

## Failure modes to handle

- `OPENAI_API_KEY` missing → exit 3 → surface to user
- Empty graph (fresh install) → `(no relevant memory found)` is correct, not an error
- Query too vague → Cognee may return overly broad results; tell the agent to retry with a more specific question if first attempt is too generic

## What NOT to do

- Do not use recall as a search replacement for SQL queries on ticker_signals — that's wasteful. SQL for fresh data, recall for historical/narrative knowledge.
- Do not paraphrase recall output without attribution. When chaining with trader-reasoning, preserve the tag/date from the source memory so the user can trace provenance.
- Do not spam recall in tight loops — it's cheap but not free, and repeated calls within seconds usually mean the agent is confused about what question to ask.
