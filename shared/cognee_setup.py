"""Cognee integration wrapper for the Hermes Market Agent.

Provides three CLI entry points used by the `memory` skills:

    python cognee_setup.py status
    python cognee_setup.py remember <tag> < input_text.md
    python cognee_setup.py recall "<query>" [--limit N]

Design goals:
- Guarded activation — if OPENAI_API_KEY is not set, every command reports
  a clear "not configured" message and exits 3. Skills surface this to the
  user without cryptic Python tracebacks.
- Embedded-only storage at /data/cognee — no network storage, no Docker.
- Idempotent initialization — safe to call multiple times.

Cost notes:
- cognify() runs LLM extraction on every ingest. Cost scales with input size.
- Default model is gpt-4o-mini; configurable via COGNEE_LLM_MODEL env var.
- Expected: ~2-5 ingests/day at our scale = under $3/mo.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

COGNEE_DATA_DIR = Path("/data/cognee")
COGNEE_DATA_DIR.mkdir(parents=True, exist_ok=True)


def _is_configured() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


def _not_configured_message() -> str:
    return (
        "Cognee is not configured. To enable:\n"
        "  1. Add OPENAI_API_KEY as a Railway service variable\n"
        "  2. Restart the service\n"
        "See skills/memory/DESCRIPTION.md for cost estimates."
    )


def _configure_cognee() -> None:
    """Set Cognee data paths. Must be called before any cognee API use."""
    import cognee
    from cognee.infrastructure.files.storage import storage_path
    os.environ.setdefault("COGNEE_DATA_PATH", str(COGNEE_DATA_DIR))
    os.environ.setdefault("COGNEE_SYSTEM_PATH", str(COGNEE_DATA_DIR / "system"))
    # Default to gpt-4o-mini for extraction (cheap, structured)
    os.environ.setdefault("LLM_MODEL", os.environ.get("COGNEE_LLM_MODEL", "gpt-4o-mini"))


async def _remember_impl(tag: str, text: str) -> str:
    _configure_cognee()
    import cognee
    await cognee.add(text, dataset_name=tag)
    await cognee.cognify(datasets=[tag])
    return f"remembered: tag={tag}, bytes={len(text)}"


async def _recall_impl(query: str, limit: int) -> str:
    _configure_cognee()
    import cognee
    from cognee.api.v1.search import SearchType
    results = await cognee.search(
        query_type=SearchType.GRAPH_COMPLETION,
        query_text=query,
    )
    if not results:
        return "(no relevant memory found)"
    truncated = results[:limit] if isinstance(results, list) else [results]
    lines = []
    for i, r in enumerate(truncated, 1):
        lines.append(f"{i}. {str(r)[:500]}")
    return "\n".join(lines)


def cmd_status() -> int:
    if not _is_configured():
        print(_not_configured_message())
        return 3
    try:
        import cognee  # noqa: F401
    except ImportError as e:
        print(f"cognee package not installed: {e}")
        return 4
    print(f"Cognee configured. Data dir: {COGNEE_DATA_DIR}")
    return 0


def cmd_remember(tag: str) -> int:
    if not _is_configured():
        print(_not_configured_message())
        return 3
    text = sys.stdin.read()
    if not text.strip():
        print("error: no input text on stdin")
        return 2
    try:
        result = asyncio.run(_remember_impl(tag, text))
        print(result)
        return 0
    except Exception as e:
        print(f"remember failed: {e}")
        return 1


def cmd_recall(query: str, limit: int) -> int:
    if not _is_configured():
        print(_not_configured_message())
        return 3
    try:
        result = asyncio.run(_recall_impl(query, limit))
        print(result)
        return 0
    except Exception as e:
        print(f"recall failed: {e}")
        return 1


def main() -> int:
    p = argparse.ArgumentParser(description="Cognee wrapper for Hermes Market Agent")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Check whether Cognee is configured")

    r = sub.add_parser("remember", help="Ingest text (from stdin) into Cognee")
    r.add_argument("tag", help="Dataset tag (e.g. 'daily-brief-2026-04-20', 'trader-nvda')")

    q = sub.add_parser("recall", help="Query accumulated memory")
    q.add_argument("query", help="Natural-language question")
    q.add_argument("--limit", type=int, default=5)

    args = p.parse_args()

    if args.cmd == "status":
        return cmd_status()
    if args.cmd == "remember":
        return cmd_remember(args.tag)
    if args.cmd == "recall":
        return cmd_recall(args.query, args.limit)
    return 99


if __name__ == "__main__":
    sys.exit(main())
