#!/bin/bash
set -e

echo "Starting InstantReports..."

# Single worker: the APScheduler runner is created in the app's startup event,
# so every extra worker would run its own copy and fire each schedule once per
# worker (duplicate reports/emails). Scale the scheduler separately if needed.
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
