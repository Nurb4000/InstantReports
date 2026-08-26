#!/bin/bash
set -e

case "${MODE}" in
  designer)
    echo "Starting InstantReports in Designer mode..."
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
    ;;
  runner)
    echo "Starting InstantReports in Runner mode..."
    exec python -m app.runner
    ;;
  *)
    echo "Invalid MODE: ${MODE}. Must be 'designer' or 'runner'."
    exit 1
    ;;
esac
