#!/bin/sh

# Exit immediately if a command exits with a non-zero status.
set -e

echo "Starting entrypoint script..."

echo "Running financial_tracker.py to generate/update data..."
python financial_tracker.py

echo "Starting Dash application (app.py)..."
# To make Dash accessible from outside the container, app.py should run with host='0.0.0.0'.
# This command assumes app.py's app.run() is configured accordingly.
python app.py
