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

# Start all services immediately (toolkits are baked in; updater runs in background)
exec /usr/bin/supervisord -c /app/supervisord.conf