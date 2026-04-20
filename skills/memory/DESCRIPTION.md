# memory — Accumulated Knowledge via Cognee

Skills in this category use [Cognee](https://github.com/topoteretes/cognee)'s 3-store memory architecture (SQLite + LanceDB + Kuzu, all embedded) to accumulate **structured knowledge** about tickers, narratives, and source quality over time.

**Principle:** Don't ingest raw posts — way too expensive. Ingest only **high-signal outputs**: daily briefs, trader-reasoning evaluations, and significant alerts. That's ~2-5 cognify() calls per day, keeping costs predictable.

## Why Cognee over just SQLite

- Raw SQLite can't answer *"has this $NVDA partnership rumor come up before?"* — that's a semantic query over history.
- Flat-file notes can't traverse *"what tickers moved when semiconductor sentiment shifted bullish last quarter"* — that's a multi-hop graph query.
- Cognee gives us: vector similarity (semantics) + graph traversal (relationships) + provenance tracking (relational) from a single API call.

## Skills

- `remember` — Ingest a text blob (typically a daily brief or trader-reasoning JSON) into Cognee. Runs `cognee.add()` then `cognee.cognify()`.
- `recall` — Query accumulated knowledge. Used by `trader-reasoning` (from Sage) to enrich current evaluation with historical context.

## Activation status

**Disabled by default.** Cognee requires an OpenAI API key for embeddings + extraction. To activate:

1. Add `OPENAI_API_KEY` as a Railway service variable (expected cost: $1-3/mo at our scale)
2. Send `/restart` to the bot (or manually restart via Railway admin)
3. Verify with: *"Use the remember skill to store a test fact. Then recall it."*

When `OPENAI_API_KEY` is missing, both skills return a helpful "Cognee not configured" message instead of erroring.

## Data location

- `/data/cognee/` (persisted on Railway volume)
  - SQLite relational store
  - LanceDB vector store
  - Kuzu graph store

All embedded, no external services, no Docker.

## Typical ingestion cadence

- Daily brief → remembered at 4:30 PM ET (after `daily-brief` skill runs)
- Trader-reasoning JSON → remembered whenever a ticker crosses composite_score ≥ 0.7 (HIGH priority)
- Nothing else gets remembered automatically

## Typical recall use

- `trader-reasoning` invokes `recall` for the target ticker before composing its evaluation: "what have we learned about $NVDA over the last 30 days?"
- User asks *"what did we say about $SMCI last week?"* — direct invocation

See `shared/cognee_setup.py` for the Python wrapper. See the plan at `~/.claude/plans/ok-for-now-plan-frolicking-bear.md` for the broader architecture.
