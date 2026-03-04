from flask import Flask, jsonify, Response, request
from flask_cors import CORS
import subprocess, os, json, sys, threading, time, urllib.request

app = Flask(__name__)
CORS(app)

# ── Download tracking ────────────────────────────────────────────────────────
# id -> {"proc": Popen, "log": path, "done": bool}
_downloads = {}
_lock = threading.Lock()

LOG_DIR = "/tmp/dl_logs"
os.makedirs(LOG_DIR, exist_ok=True)


def get_registry():
    with open('/app/models.json', 'r') as f:
        return json.load(f)


def _stream_output(proc, log_path, model_id):
    """Read subprocess output line-by-line, write to log file AND stdout (docker logs)."""
    prefix = f"[download:{model_id}]"
    with open(log_path, 'w', buffering=1) as lf:
        for line in iter(proc.stdout.readline, ''):
            msg = f"{prefix} {line.rstrip()}"
            print(msg, flush=True)
            lf.write(line)
        proc.wait()
    with _lock:
        if model_id in _downloads:
            _downloads[model_id]["done"] = True
    rc = proc.returncode
    status = "complete" if rc == 0 else f"failed (exit {rc})"
    print(f"{prefix} Download {status}.", flush=True)
    with open(log_path, 'a') as lf:
        lf.write(f"\n__STATUS__{status}\n")


# ── Auto-stop state ──────────────────────────────────────────────────────────
_autostop = {
    "enabled":        False,
    "minutes":        15,        # inactivity window
    "last_active_at": None,      # epoch seconds; None = timer not yet started
    "stopping":       False,     # True once the GraphQL call has been fired
}
_autostop_lock = threading.Lock()


def _is_vlm_idle():
    """Return True when the VLM captioner reports no job in progress."""
    try:
        req = urllib.request.urlopen("http://localhost:5002/api/status", timeout=4)
        data = json.loads(req.read())
        return not data.get("captioning_in_progress", True)
    except Exception:
        return True  # if unreachable, treat as idle


def _is_trainer_idle():
    """Return True when Ostris AI Toolkit has no active training job.

    The Ostris UI (port 8675) exposes a simple /queue endpoint that returns
    a list of jobs.  An empty list, or all jobs in a terminal state
    (completed / failed / cancelled), counts as idle.
    We fall back to True (idle) if the endpoint is not reachable.
    """
    try:
        req = urllib.request.urlopen("http://localhost:8675/queue", timeout=4)
        data = json.loads(req.read())
        # data is expected to be a list of job dicts with a "status" field
        if isinstance(data, list):
            active = [j for j in data if j.get("status") not in
                      ("completed", "failed", "cancelled", "error")]
            return len(active) == 0
        return True
    except Exception:
        return True


def _stop_pod_via_runpodctl():
    """Stop this pod using the runpodctl CLI.

    runpodctl is pre-installed on every RunPod pod and pre-authenticated
    with a pod-scoped API key — no manual secret configuration needed.
    """
    pod_id = os.environ.get("RUNPOD_POD_ID", "")
    if not pod_id:
        print("[autostop] ERROR: RUNPOD_POD_ID env var not set.", flush=True)
        return False

    print(f"[autostop] Running: runpodctl pod stop {pod_id}", flush=True)
    result = subprocess.run(
        ["runpodctl", "pod", "stop", pod_id],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print(f"[autostop] Pod stop succeeded: {result.stdout.strip()}", flush=True)
        return True
    else:
        print(f"[autostop] Pod stop failed (exit {result.returncode}): {result.stderr.strip()}", flush=True)
        return False


def _autostop_watcher():
    """Background thread: every 30 s check idleness and stop the pod if overdue."""
    print("[autostop] Watcher thread started.", flush=True)
    while True:
        time.sleep(30)
        with _autostop_lock:
            if not _autostop["enabled"] or _autostop["stopping"]:
                continue
            minutes = _autostop["minutes"]

        vlm_idle     = _is_vlm_idle()
        trainer_idle = _is_trainer_idle()
        all_idle     = vlm_idle and trainer_idle

        with _autostop_lock:
            if not _autostop["enabled"] or _autostop["stopping"]:
                continue

            if all_idle:
                if _autostop["last_active_at"] is None:
                    _autostop["last_active_at"] = time.time()
                    print("[autostop] Everything idle – inactivity timer started.", flush=True)
                else:
                    elapsed = (time.time() - _autostop["last_active_at"]) / 60
                    print(f"[autostop] Idle for {elapsed:.1f}/{minutes} min "
                          f"(VLM={'idle' if vlm_idle else 'busy'}, "
                          f"Trainer={'idle' if trainer_idle else 'busy'})", flush=True)
                    if elapsed >= minutes:
                        _autostop["stopping"] = True
                        print(f"[autostop] Inactivity limit reached – stopping pod.", flush=True)
                        threading.Thread(target=_stop_pod_via_runpodctl, daemon=True).start()
            else:
                if _autostop["last_active_at"] is not None:
                    print("[autostop] Activity detected – resetting inactivity timer.", flush=True)
                _autostop["last_active_at"] = None


threading.Thread(target=_autostop_watcher, daemon=True).start()


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route('/api/status')
def status():
    reg = get_registry()
    data = {"models": {}, "engine": False}
    for k, v in reg.items():
        installed = os.path.exists(v['path'])
        with _lock:
            dl_info = _downloads.get(k)
        downloading = dl_info is not None and not dl_info.get("done", False)
        data["models"][k] = {
            "installed":   installed,
            "name":        v['name'],
            "desc":        v['desc'],
            "downloading": downloading,
        }

    res = subprocess.run(['supervisorctl', 'status', 'llama_router'],
                         capture_output=True, text=True)
    data["engine"] = "RUNNING" in res.stdout
    return jsonify(data)


@app.route('/api/engine/<action>')
def engine_ctl(action):
    if action not in ('start', 'stop'):
        return jsonify({"error": "invalid action"}), 400
    subprocess.run(['supervisorctl', action, 'llama_router'])
    return jsonify({"status": "ok"})


@app.route('/api/download/<id>')
def dl(id):
    registry = get_registry()
    if id not in registry:
        return jsonify({"error": "unknown model id"}), 404

    entry = registry[id]

    if os.path.exists(entry['path']):
        return jsonify({"status": "already_installed"})

    with _lock:
        if id in _downloads and not _downloads[id].get("done", False):
            return jsonify({"status": "already_downloading"})

    repo = entry['repo']
    path = os.path.dirname(entry['path'])
    os.makedirs(path, exist_ok=True)

    log_path = os.path.join(LOG_DIR, f"{id}.log")
    open(log_path, 'w').close()

    print(f"[download:{id}] Starting download of '{repo}' into {path}", flush=True)

    proc = subprocess.Popen(
        ["huggingface-cli", "download", repo,
         "--local-dir", path,
         "--local-dir-use-symlinks=False"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    with _lock:
        _downloads[id] = {"proc": proc, "log": log_path, "done": False}

    t = threading.Thread(target=_stream_output, args=(proc, log_path, id), daemon=True)
    t.start()

    return jsonify({"status": "started"})


@app.route('/api/download/progress/<id>')
def dl_progress(id):
    """SSE endpoint – streams log lines to the browser as they arrive."""
    registry = get_registry()
    if id not in registry:
        return jsonify({"error": "unknown model id"}), 404

    log_path = os.path.join(LOG_DIR, f"{id}.log")

    def generate():
        for _ in range(50):
            if os.path.exists(log_path):
                break
            time.sleep(0.1)

        with open(log_path, 'r') as f:
            while True:
                line = f.readline()
                if line:
                    payload = line.rstrip().replace('\n', ' ')
                    yield f"data: {payload}\n\n"
                    if line.startswith("__STATUS__"):
                        break
                else:
                    with _lock:
                        info = _downloads.get(id)
                    if info and info.get("done", False):
                        break
                    time.sleep(0.25)

        yield "data: __DONE__\n\n"

    return Response(generate(), mimetype='text/event-stream',
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})


@app.route('/api/autostop', methods=['GET'])
def autostop_get():
    with _autostop_lock:
        s = dict(_autostop)
    # compute seconds remaining in the idle window
    remaining = None
    if s["enabled"] and s["last_active_at"] is not None and not s["stopping"]:
        elapsed   = time.time() - s["last_active_at"]
        remaining = max(0, s["minutes"] * 60 - elapsed)
    return jsonify({
        "enabled":         s["enabled"],
        "minutes":         s["minutes"],
        "stopping":        s["stopping"],
        "timer_running":   s["last_active_at"] is not None,
        "seconds_remaining": remaining,
        "vlm_idle":        _is_vlm_idle(),
        "trainer_idle":    _is_trainer_idle(),
    })


@app.route('/api/autostop', methods=['POST'])
def autostop_set():
    body = request.get_json(silent=True) or {}
    with _autostop_lock:
        if "enabled" in body:
            _autostop["enabled"] = bool(body["enabled"])
            # Reset timer whenever the toggle changes
            _autostop["last_active_at"] = None
            _autostop["stopping"] = False
            print(f"[autostop] {'Enabled' if _autostop['enabled'] else 'Disabled'} "
                  f"(window={_autostop['minutes']} min).", flush=True)
        if "minutes" in body:
            try:
                mins = int(body["minutes"])
                if mins > 0:
                    _autostop["minutes"] = mins
                    _autostop["last_active_at"] = None   # reset timer on change
            except (ValueError, TypeError):
                pass
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5005, threaded=True)