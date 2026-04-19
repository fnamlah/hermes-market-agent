-- Hermes Market Agent — SQLite schema
-- Runs on first container start (idempotent via CREATE TABLE IF NOT EXISTS).
-- Database path: /data/hermes-market.db

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

-- Raw scraped posts from X and Reddit (immutable data lake)
CREATE TABLE IF NOT EXISTS raw_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL CHECK (source IN ('x', 'reddit')),
    external_id TEXT NOT NULL,
    author TEXT,
    author_followers INTEGER,
    author_age_days INTEGER,
    text TEXT NOT NULL,
    url TEXT,
    created_at_utc TEXT NOT NULL,
    engagement_likes INTEGER DEFAULT 0,
    engagement_reposts INTEGER DEFAULT 0,
    engagement_comments INTEGER DEFAULT 0,
    engagement_awards INTEGER DEFAULT 0,
    subreddit TEXT,
    raw_json TEXT NOT NULL,
    ingested_at_utc TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_raw_posts_source_time ON raw_posts(source, created_at_utc);
CREATE INDEX IF NOT EXISTS idx_raw_posts_ingested ON raw_posts(ingested_at_utc);

-- Market data snapshots (price, volume, OHLC)
CREATE TABLE IF NOT EXISTS market_quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    snapshot_at_utc TEXT NOT NULL,
    price REAL,
    volume INTEGER,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    prev_close REAL,
    change_pct REAL,
    avg_volume_20d INTEGER,
    volume_ratio REAL,
    is_premarket INTEGER DEFAULT 0,
    is_afterhours INTEGER DEFAULT 0,
    source TEXT NOT NULL,
    raw_json TEXT,
    ingested_at_utc TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (ticker, snapshot_at_utc, source)
);
CREATE INDEX IF NOT EXISTS idx_market_quotes_ticker_time ON market_quotes(ticker, snapshot_at_utc);

-- Posts enriched with ticker + sentiment (filled by Sage in P2)
CREATE TABLE IF NOT EXISTS processed_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_post_id INTEGER NOT NULL REFERENCES raw_posts(id) ON DELETE CASCADE,
    ticker TEXT NOT NULL,
    sentiment TEXT CHECK (sentiment IN ('bullish', 'bearish', 'neutral', 'mixed')),
    sentiment_intensity REAL CHECK (sentiment_intensity BETWEEN 0 AND 1),
    confidence REAL CHECK (confidence BETWEEN 0 AND 1),
    signal_type TEXT,
    catalyst_type TEXT,
    is_forward_looking INTEGER,
    credibility_estimate TEXT CHECK (credibility_estimate IN ('low', 'medium', 'high')),
    spam_score REAL CHECK (spam_score BETWEEN 0 AND 1),
    is_meme INTEGER DEFAULT 0,
    processed_at_utc TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_processed_posts_ticker ON processed_posts(ticker, processed_at_utc);
CREATE INDEX IF NOT EXISTS idx_processed_posts_raw ON processed_posts(raw_post_id);

-- Aggregated signals per ticker per cycle (filled by Sage in P2)
CREATE TABLE IF NOT EXISTS ticker_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    cycle_at_utc TEXT NOT NULL,
    mention_count INTEGER,
    unique_authors INTEGER,
    velocity REAL,
    sentiment_avg REAL,
    baseline_24h REAL,
    ratio_vs_baseline REAL,
    attention_score REAL,
    credibility_score REAL,
    momentum_score REAL,
    confidence_score REAL,
    cross_platform_score REAL,
    novelty_score REAL,
    market_score REAL,
    false_positive_penalty REAL,
    composite_score REAL,
    phase TEXT CHECK (phase IN ('emerging', 'accelerating', 'peaking', 'fading')),
    computed_at_utc TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_ticker_signals_ticker ON ticker_signals(ticker, cycle_at_utc);
CREATE INDEX IF NOT EXISTS idx_ticker_signals_score ON ticker_signals(composite_score DESC);

-- Alerts dispatched (for cooldown + outcome tagging, filled by Herald in P3)
CREATE TABLE IF NOT EXISTS alerts_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    priority TEXT CHECK (priority IN ('high', 'medium', 'watchlist')),
    signal_score REAL,
    message_body TEXT,
    channel TEXT CHECK (channel IN ('telegram', 'whatsapp', 'email')),
    sent_at_utc TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    outcome TEXT CHECK (outcome IN ('useful', 'somewhat_useful', 'noise', 'false_positive', 'spam', NULL))
);
CREATE INDEX IF NOT EXISTS idx_alerts_ticker_time ON alerts_log(ticker, sent_at_utc);

-- Config (monitored tickers, thresholds, source weights)
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at_utc TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Seed default config (upsert pattern)
INSERT OR IGNORE INTO config(key, value) VALUES
    ('monitored_tickers', '["AAPL","TSLA","NVDA","AMD","MSFT","AMZN","META","GOOG","SPY","QQQ","IWM","XLF","XLE","ARKK","SMCI","PLTR","COIN","HOOD","RIVN","F"]'),
    ('monitored_subreddits_tier1', '["wallstreetbets","stocks","investing","options"]'),
    ('monitored_subreddits_tier2', '["StockMarket","Daytrading","pennystocks","thetagang","SecurityAnalysis"]'),
    ('cycle_minutes_market_hours', '15'),
    ('cycle_minutes_afterhours', '60'),
    ('max_alerts_per_day', '8'),
    ('threshold_high', '0.7'),
    ('threshold_medium', '0.5'),
    ('threshold_watchlist', '0.3');
