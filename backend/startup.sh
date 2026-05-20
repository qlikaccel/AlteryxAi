#!/usr/bin/env bash
# startup.sh — use this as the Run Command in DigitalOcean / Render / local Docker.
#
# Why timeout-keep-alive=75?
#   DigitalOcean App Platform's load balancer has a 30-second hard timeout on
#   HTTP connections.  Setting keep-alive to 75 s tells uvicorn to hold the
#   TCP socket open longer than the LB window so that long-running poll
#   responses (e.g. background publish-job status checks) are never silently
#   dropped before the response bytes leave the process.
#
# Why workers=2?
#   Two workers let one handle a heavy iterative-macro analysis thread while
#   the other stays available for lightweight poll requests, preventing the
#   504s seen when a single worker is blocked by CPU-intensive work.
#
# DigitalOcean App Settings → Run Command:
#   bash backend/startup.sh
#
set -e

PORT="${PORT:-8080}"

exec uvicorn main:app \
  --host 0.0.0.0 \
  --port "$PORT" \
  --workers "${UVICORN_WORKERS:-2}" \
  --timeout-keep-alive 75
