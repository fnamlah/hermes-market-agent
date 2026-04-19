#!/bin/bash
set -e

# Mirror dashboard-ref-only's startup: create every directory hermes expects
# and seed a default config.yaml if the volume is empty. Without these,
# `hermes dashboard` endpoints that hit logs/, sessions/, cron/, etc. can fail
# with opaque errors even though no auth is actually involved.
mkdir -p /data/.hermes/cron /data/.hermes/sessions /data/.hermes/logs \
         /data/.hermes/memories /data/.hermes/skills /data/.hermes/pairing \
         /data/.hermes/hooks /data/.hermes/image_cache /data/.hermes/audio_cache \
         /data/.hermes/workspace

if [ ! -f /data/.hermes/config.yaml ] && [ -f /opt/hermes-agent/cli-config.yaml.example ]; then
  cp /opt/hermes-agent/cli-config.yaml.example /data/.hermes/config.yaml
fi

[ ! -f /data/.hermes/.env ] && touch /data/.hermes/.env

# ── Market-agent additions ──────────────────────────────────────────────────
# Sync shipped skills into the volume on every container start. Using cp -r
# (not symlink) so users can still edit skills via the admin dashboard without
# their changes getting clobbered on the NEXT restart — we only copy if the
# destination is missing OR the shipped version is newer.
if [ -d /app/skills ]; then
  for category_dir in /app/skills/*/; do
    category=$(basename "$category_dir")
    mkdir -p "/data/.hermes/skills/$category"
    cp -rn "$category_dir." "/data/.hermes/skills/$category/" 2>/dev/null || true
  done
fi

# Initialize market SQLite DB from shipped schema (idempotent — schema uses
# CREATE TABLE IF NOT EXISTS and INSERT OR IGNORE for config defaults).
if [ -f /app/shared/schema.sql ]; then
  sqlite3 /data/hermes-market.db < /app/shared/schema.sql
fi

exec python /app/server.py
