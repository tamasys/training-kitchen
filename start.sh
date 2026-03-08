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

# Patch bug in vlm-caption/caption_openai.py where it passes an user style
# response instead of an assistant style response.
if [ -f /app/vlm-caption/caption_openai.py ]; then
    sed -i -E 's/"content": \[\{"type": "text", "text": (response_text|prompt)\}\]/"content": \1/g' /app/vlm-caption/caption_openai.py
fi

# Inject stop tokens to prevent JoyCaption from hallucinating dialog continuations
if [ -f /app/vlm-caption/caption_openai.py ]; then
    sed -i -E 's/stream=True,/stream=True, stop=["\\nUSER:", "USER:", "  -  USER", "ASSISTANT:", "\\nASSISTANT"],/g' /app/vlm-caption/caption_openai.py
fi

# Disable hardcoded debug logging that causes race conditions
if [ -f /app/vlm-caption/caption_openai.py ]; then
    sed -i 's/save_debug_task = asyncio.create_task(write_debug_messages(messages, i))/save_debug_task = asyncio.sleep(0)/g' /app/vlm-caption/caption_openai.py
fi

# Enable .json debug output
# if [ -f /app/vlm-caption/file_utils/file_access.py ]; then
#     sed -i 's/as f_cap:/as f_cap, aiofiles.open(debug_path, "w", encoding="utf-8") as f_log:/' /app/vlm-caption/file_utils/file_access.py
#     sed -i 's/await asyncio.gather(f_cap.write(caption_text))/await asyncio.gather(f_cap.write(caption_text), f_log.write(debug_info))/' /app/vlm-caption/file_utils/file_access.py
#     sed -i 's/#debug_path/debug_path/g' /app/vlm-caption/file_utils/file_access.py
#     sed -i 's/\.log"/.json"/g' /app/vlm-caption/file_utils/file_access.py
# fi

# Start all services immediately (toolkits are baked in; updater runs in background)
exec /usr/bin/supervisord -c /app/supervisord.conf