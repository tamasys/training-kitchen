#!/bin/bash
set -e
echo "--- Initializing Training Kitchen ---"

# STORAGE_DIR can be overridden for non-standard RunPod volume mount paths.
# RunPod Network Volume is /workspace by default; some templates use /mnt or /runpod-volume.
export STORAGE_DIR="${STORAGE_DIR:-/workspace}"
echo "Storage directory: $STORAGE_DIR"

mkdir -p "$STORAGE_DIR/models" "$STORAGE_DIR/outputs" "$STORAGE_DIR/config" "$STORAGE_DIR/images"

# Generate vlm-caption's init.yaml from our template, substituting the real storage path.
# On a fresh container (no saved caption.yaml), the app copies this to caption.yaml
# automatically on first run. User-saved settings in caption.yaml are left untouched
# so they survive container restarts.
sed "s|__STORAGE_DIR__|$STORAGE_DIR|g" \
    /app/config/vlm-caption-init.yaml \
    > /app/vlm-caption/init.yaml
echo "[vlm-caption] init.yaml written with base_directory: $STORAGE_DIR/images"

# Robust Python-based patching for VLM backends
cat << 'EOF' > /tmp/patch_vlm.py
import os
import re

caption_py = "/app/vlm-caption/caption_openai.py"
if os.path.exists(caption_py):
    with open(caption_py, "r") as f:
        src = f.read()
    
    # Fix bug where user response strings were not unwrapped properly
    src = re.sub(r'"content": \[\{"type": "text", "text": (response_text|prompt)\}\]', r'"content": \1', src)
    
    # Inject stop tokens to prevent JoyCaption from hallucinating dialog continuations
    if 'stop=["\\nUSER:"' not in src:
        src = src.replace('stream=True,', 'stream=True, stop=["\\nUSER:", "USER:", "  -  USER", "ASSISTANT:", "\\nASSISTANT"],')
        
    # Disable hardcoded debug logging that causes race conditions
    src = src.replace('save_debug_task = asyncio.create_task(write_debug_messages(messages, i))', 'save_debug_task = asyncio.sleep(0)')
    
    with open(caption_py, "w") as f:
        f.write(src)
EOF
python3 /tmp/patch_vlm.py

# Start all services immediately (toolkits are baked in; updater runs in background)
exec /usr/bin/supervisord -c /app/supervisord.conf