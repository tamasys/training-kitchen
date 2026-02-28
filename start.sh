#!/bin/bash
set -e
echo "--- Initializing Training Kitchen ---"

# Ensure persistence
mkdir -p /workspace/models /workspace/outputs /workspace/config

# Clone/Update Toolkits
if [ ! -d "/app/ai-toolkit/.git" ]; then
    git clone https://github.com/ostris/ai-toolkit.git /app/ai-toolkit
fi
cd /app/ai-toolkit && git pull && pip3 install -q -r requirements.txt

if [ ! -d "/app/vlm-caption/.git" ]; then
    git clone https://github.com/victorchall/vlm-caption.git /app/vlm-caption
fi
cd /app/vlm-caption && git pull && pip3 install -q -r requirements.txt

# Start the process manager
exec /usr/bin/supervisord -c /app/supervisord.conf