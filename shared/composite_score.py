"""Compute composite ticker signals from processed_posts + market_quotes.

Runs one analysis cycle:
  1. For each ticker with fresh activity in the last N minutes, aggregate
     mentions, unique authors, sentiment, velocity.
  2. Compute baseline (24h rolling) and ratio.
  3. Compute each component score (attention, credibility, momentum,
     confidence, cross_platform, novelty, market_score, FP penalty).
  4. Apply the V1+market composite formula.
  5. Insert one row into ticker_signals.

Invoked by the `composite-score` skill. Idempotent within a cycle (dedup by
(ticker, cycle_at_utc)) but writes a new row every cycle so time-series is
preserved.

CLI:
    python compute_composite_score.py [--window-minutes 15] [--verbose]

Exit codes:
    0 success
    1 DB error / missing tables
    2 no new activity in window
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

DB_PATH = Path("/data/hermes-market.db")

# V1 weights, rescaled so social components sum to 0.80 and market gets 0.20
WEIGHTS = {
    "attention": 0.20,
    "credibility": 0.12,
    "momentum": 0.16,
    "confidence": 0.12,
    "cross_platform": 0.08,
    "novelty": 0.12,
    "market": 0.20,
}

# Safety caps so a single component can't dominate
MIN_UNIQUE_AUTHORS_FOR_FULL_CONFIDENCE = 10
UNIQUE_AUTHOR_CAP = 30


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%fZ")


def _tickers_with_fresh_activity(conn: sqlite3.Connection, window: timedelta) -> List[str]:
    cutoff = _iso(_utc_now() - window)
    rows = conn.execute(
        """
        SELECT DISTINCT pp.ticker
        FROM processed_posts pp
        JOIN raw_posts rp ON rp.id = pp.raw_post_id
        WHERE rp.created_at_utc > ?
        """,
        (cutoff,),
    ).fetchall()
    return [r[0] for r in rows]


def _aggregate_window(
    conn: sqlite3.Connection, ticker: str, window: timedelta
) -> Dict[str, Any]:
    """Return current-window stats for a ticker."""
    cutoff = _iso(_utc_now() - window)
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS mention_count,
            COUNT(DISTINCT rp.author) AS unique_authors,
            AVG(CASE pp.sentiment
                  WHEN 'bullish' THEN pp.sentiment_intensity
                  WHEN 'bearish' THEN -pp.sentiment_intensity
                  ELSE 0 END) AS sentiment_avg,
            AVG(pp.confidence) AS confidence_avg,
            AVG(CASE WHEN pp.credibility_estimate='high' THEN 1.0
                     WHEN pp.credibility_estimate='medium' THEN 0.5
                     ELSE 0.15 END) AS credibility_avg,
            AVG(pp.spam_score) AS spam_avg,
            AVG(CASE WHEN pp.is_meme=1 THEN 1.0 ELSE 0.0 END) AS meme_ratio,
            SUM(CASE WHEN rp.source='x' THEN 1 ELSE 0 END) AS x_count,
            SUM(CASE WHEN rp.source='reddit' THEN 1 ELSE 0 END) AS reddit_count,
            COUNT(DISTINCT CASE WHEN rp.source='x' THEN rp.author END) AS x_authors,
            COUNT(DISTINCT CASE WHEN rp.source='reddit' THEN rp.author END) AS reddit_authors
        FROM processed_posts pp
        JOIN raw_posts rp ON rp.id = pp.raw_post_id
        WHERE pp.ticker = ?
          AND rp.created_at_utc > ?
        """,
        (ticker, cutoff),
    ).fetchone()
    if not row:
        return {}
    keys = [
        "mention_count", "unique_authors", "sentiment_avg", "confidence_avg",
        "credibility_avg", "spam_avg", "meme_ratio",
        "x_count", "reddit_count", "x_authors", "reddit_authors",
    ]
    return {k: (v if v is not None else 0) for k, v in zip(keys, row)}


def _baseline_24h(conn: sqlite3.Connection, ticker: str, window_minutes: int) -> float:
    """Rolling 24h average mentions per N-minute window."""
    cutoff = _iso(_utc_now() - timedelta(hours=24))
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM processed_posts pp
        JOIN raw_posts rp ON rp.id = pp.raw_post_id
        WHERE pp.ticker = ?
          AND rp.created_at_utc > ?
        """,
        (ticker, cutoff),
    ).fetchone()
    total = row[0] if row else 0
    # Convert to per-window average. 24h = 1440 min.
    return max(total * (window_minutes / 1440.0), 0.5)


def _previous_velocity(conn: sqlite3.Connection, ticker: str, window_minutes: int) -> float:
    """Velocity in the prior N-minute window (for momentum delta)."""
    cutoff_end = _iso(_utc_now() - timedelta(minutes=window_minutes))
    cutoff_start = _iso(_utc_now() - timedelta(minutes=2 * window_minutes))
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM processed_posts pp
        JOIN raw_posts rp ON rp.id = pp.raw_post_id
        WHERE pp.ticker = ?
          AND rp.created_at_utc > ?
          AND rp.created_at_utc <= ?
        """,
        (ticker, cutoff_start, cutoff_end),
    ).fetchone()
    return float(row[0] if row else 0)


def _market_row(conn: sqlite3.Connection, ticker: str) -> Dict[str, Any]:
    """Most recent market_quotes row for ticker, or empty dict."""
    row = conn.execute(
        """
        SELECT price, volume, change_pct, avg_volume_20d, prev_close
        FROM market_quotes
        WHERE ticker = ?
        ORDER BY snapshot_at_utc DESC
        LIMIT 1
        """,
        (ticker,),
    ).fetchone()
    if not row:
        return {}
    return dict(zip(["price", "volume", "change_pct", "avg_volume_20d", "prev_close"], row))


def _first_seen_hours_ago(conn: sqlite3.Connection, ticker: str) -> float:
    """Hours since the first processed_posts row for this ticker."""
    row = conn.execute(
        "SELECT MIN(processed_at_utc) FROM processed_posts WHERE ticker = ?",
        (ticker,),
    ).fetchone()
    if not row or not row[0]:
        return 0.0
    first = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
    return (_utc_now() - first).total_seconds() / 3600.0


# ── Component scores ────────────────────────────────────────────────────────

def _attention_score(window: Dict[str, Any], baseline: float) -> float:
    """log2(velocity/baseline) normalized to [0, 1]. 16x baseline = 1.0."""
    velocity = window.get("mention_count", 0)
    if baseline <= 0 or velocity <= 0:
        return 0.0
    ratio = velocity / baseline
    if ratio <= 1.0:
        return 0.0
    return min(1.0, math.log2(ratio) / 4.0)  # 16x = log2(16) = 4


def _credibility_score(window: Dict[str, Any]) -> float:
    return float(window.get("credibility_avg", 0.0))


def _momentum_score(window: Dict[str, Any], prev_velocity: float) -> float:
    """Positive = accelerating, negative = fading. Normalized to [0, 1]."""
    current = window.get("mention_count", 0)
    if current + prev_velocity <= 0:
        return 0.5
    delta = (current - prev_velocity) / max(current + prev_velocity, 1)
    # Map [-1, 1] to [0, 1]
    return max(0.0, min(1.0, 0.5 + delta * 0.5))


def _confidence_score(window: Dict[str, Any]) -> float:
    authors = window.get("unique_authors", 0)
    author_factor = min(1.0, authors / UNIQUE_AUTHOR_CAP)
    llm_conf = window.get("confidence_avg", 0.0)
    return author_factor * llm_conf


def _cross_platform_score(window: Dict[str, Any]) -> float:
    x_a = window.get("x_authors", 0)
    r_a = window.get("reddit_authors", 0)
    if x_a == 0 and r_a == 0:
        return 0.0
    if x_a == 0 or r_a == 0:
        return 0.3  # single-platform baseline
    # Both platforms, boost by minimum (prevents single-platform dominance)
    return min(1.0, 0.5 + 0.5 * min(x_a, r_a) / 10.0)


def _novelty_score(hours_since_first: float) -> float:
    """1.0 for first 2h, decays to 0.2 at 24h."""
    if hours_since_first <= 2:
        return 1.0
    if hours_since_first >= 24:
        return 0.2
    return max(0.2, 1.0 - (hours_since_first - 2) / 22.0 * 0.8)


def _market_score(market: Dict[str, Any]) -> float:
    """Volume ratio + price move — unusual volume + move = high score."""
    vol = market.get("volume") or 0
    avg_vol = market.get("avg_volume_20d") or 0
    change_pct = abs(market.get("change_pct") or 0.0)
    if avg_vol <= 0:
        vol_score = 0.0
    else:
        ratio = vol / avg_vol
        vol_score = min(1.0, max(0.0, (ratio - 1.0) / 3.0))  # 4x vol = 1.0
    move_score = min(1.0, change_pct / 5.0)  # 5% move = 1.0
    return max(vol_score, move_score)  # whichever is stronger


def _fp_penalty(window: Dict[str, Any]) -> float:
    spam = window.get("spam_avg", 0.0) or 0.0
    meme = window.get("meme_ratio", 0.0) or 0.0
    return min(0.8, max(spam, meme * 0.5))


def _phase(window: Dict[str, Any], prev_velocity: float, novelty: float) -> str:
    current = window.get("mention_count", 0)
    if novelty >= 0.9:
        return "emerging"
    if current > prev_velocity * 1.3:
        return "accelerating"
    if prev_velocity > current * 1.3:
        return "fading"
    return "peaking"


# ── Main ────────────────────────────────────────────────────────────────────

def compute_cycle(window_minutes: int = 15, verbose: bool = False) -> int:
    if not DB_PATH.exists():
        print(f"ERROR: DB missing at {DB_PATH}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")

    window = timedelta(minutes=window_minutes)
    tickers = _tickers_with_fresh_activity(conn, window)
    if not tickers:
        if verbose:
            print("no fresh activity in window")
        conn.close()
        return 2

    cycle_at = _iso(_utc_now())
    written = 0

    for ticker in tickers:
        agg = _aggregate_window(conn, ticker, window)
        if not agg or agg["mention_count"] == 0:
            continue
        baseline = _baseline_24h(conn, ticker, window_minutes)
        prev_velocity = _previous_velocity(conn, ticker, window_minutes)
        market = _market_row(conn, ticker)
        novelty = _novelty_score(_first_seen_hours_ago(conn, ticker))

        components = {
            "attention": _attention_score(agg, baseline),
            "credibility": _credibility_score(agg),
            "momentum": _momentum_score(agg, prev_velocity),
            "confidence": _confidence_score(agg),
            "cross_platform": _cross_platform_score(agg),
            "novelty": novelty,
            "market": _market_score(market),
        }
        fp = _fp_penalty(agg)
        composite = sum(WEIGHTS[k] * v for k, v in components.items()) * (1.0 - fp)

        conn.execute(
            """
            INSERT INTO ticker_signals (
                ticker, cycle_at_utc, mention_count, unique_authors,
                velocity, sentiment_avg, baseline_24h, ratio_vs_baseline,
                attention_score, credibility_score, momentum_score,
                confidence_score, cross_platform_score, novelty_score,
                market_score, false_positive_penalty, composite_score, phase
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticker, cycle_at,
                agg["mention_count"], agg["unique_authors"],
                agg["mention_count"] / max(window_minutes, 1),
                agg["sentiment_avg"],
                baseline,
                agg["mention_count"] / baseline if baseline > 0 else None,
                components["attention"], components["credibility"],
                components["momentum"], components["confidence"],
                components["cross_platform"], components["novelty"],
                components["market"], fp, composite,
                _phase(agg, prev_velocity, novelty),
            ),
        )
        written += 1
        if verbose:
            print(json.dumps({
                "ticker": ticker, "composite": round(composite, 3),
                **{k: round(v, 3) for k, v in components.items()},
                "fp_penalty": round(fp, 3),
            }))

    conn.commit()
    conn.close()
    print(f"composite-score: {written} tickers scored this cycle")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--window-minutes", type=int, default=15)
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    return compute_cycle(window_minutes=args.window_minutes, verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
