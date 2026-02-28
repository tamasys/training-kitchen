#!/bin/bash
# Background updater - runs once after supervisord starts.
# Pulls latest code for each toolkit and restarts only if requirements changed.
echo "[updater] Checking for updates..."

update_repo() {
    local name="$1"
    local dir="$2"
    local service="$3"

    cd "$dir" || { echo "[updater] ERROR: $dir not found, skipping."; return; }

    OLD_HASH=$(git rev-parse HEAD)
    OLD_REQS=$(md5sum requirements.txt 2>/dev/null || echo "none")

    git pull -q

    NEW_HASH=$(git rev-parse HEAD)
    NEW_REQS=$(md5sum requirements.txt 2>/dev/null || echo "none")

    if [ "$OLD_HASH" = "$NEW_HASH" ]; then
        echo "[updater] $name is up to date."
        return
    fi

    echo "[updater] $name updated ($OLD_HASH -> $NEW_HASH)."

    if [ "$OLD_REQS" != "$NEW_REQS" ]; then
        echo "[updater] requirements.txt changed, reinstalling..."
        pip3 install -q -r requirements.txt
    fi

    echo "[updater] Restarting $service..."
    supervisorctl restart "$service"
    echo "[updater] $service restarted."
}

# Small delay to let supervisord fully initialise all programs first
sleep 5

update_repo "ai-toolkit"  "/app/ai-toolkit"  "ostris_gui"
update_repo "vlm-caption" "/app/vlm-caption" "vlm_ui"

echo "[updater] Done."
