#!/bin/sh

# Exit immediately if a command exits with a non-zero status.
set -e

echo "Starting entrypoint script..."

echo "Running financial_tracker.py to generate/update data..."
python financial_tracker.py

echo "Starting FastAPI application (main.py) with Uvicorn..."
# Uvicorn will serve the FastAPI app (app_fastapi from main.py),
# which in turn mounts the Dash app.
uvicorn main:app_fastapi --host 0.0.0.0 --port 8050
