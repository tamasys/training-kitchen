#!/bin/bash
set -e
echo "--- Initializing Training Kitchen ---"

# Ensure workspace directories exist on the mounted volume
mkdir -p /workspace/models /workspace/outputs /workspace/config

# Start all services immediately (toolkits are baked in; updater runs in background)
exec /usr/bin/supervisord -c /app/supervisord.conf