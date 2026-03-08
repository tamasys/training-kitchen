from flask import Flask, jsonify, Response, request
from flask_cors import CORS
import subprocess, os, json, sys, threading, time, urllib.request

app = Flask(__name__)
CORS(app)

# ── Storage path ─────────────────────────────────────────────────────────────
# Defaults to /workspace (RunPod Network Volume standard).
# Override with STORAGE_DIR env var for non-standard mount points (/mnt, etc.)
STORAGE_DIR = os.environ.get('STORAGE_DIR', '/workspace').rstrip('/')
print(f"[coordinator] Storage directory: {STORAGE_DIR}", flush=True)

# ── Platform detection ────────────────────────────────────────────────────────
# Set PLATFORM=runpod in the RunPod template environment to enable RunPod-specific
# features (auto-stop, public URLs via pod ID).  Leave unset for local development.
PLATFORM      = os.environ.get('PLATFORM', '').lower().strip()   # 'runpod' | ''
RUNPOD_POD_ID = os.environ.get('RUNPOD_POD_ID', '')

def _build_platform_info():
    """Return a dict describing the current platform and reachable tool URLs."""
    if PLATFORM == 'runpod' and RUNPOD_POD_ID:
        def rp_url(port):
            return f"https://{RUNPOD_POD_ID}-{port}.proxy.runpod.net"
        return {
            "platform": "runpod",
            "pod_id":   RUNPOD_POD_ID,
            "features": {"autostop": True},
            "urls": {
                "dashboard": rp_url(80),
                "files":     rp_url(8080),
                "vlm":       rp_url(5002),
                "trainer":   rp_url(8676),
                "llm_api":   rp_url(5001),
                "coordinator": rp_url(80),   # /api/ proxied through port-80 nginx
            },
        }
    # Local / unknown — assume localhost
    return {
        "platform": PLATFORM or "local",
        "pod_id":   None,
        "features": {"autostop": False},
        "urls": {
            "dashboard":   "http://localhost",
            "files":       "http://localhost:8080",
            "vlm":         "http://localhost:5002",
            "trainer":     "http://localhost:8676",
            "llm_api":     "http://localhost:5001",
            "coordinator": "http://localhost",
        },
    }

print(f"[coordinator] Platform: {PLATFORM or 'local'}", flush=True)


# ── Download tracking ────────────────────────────────────────────────────────
# id -> {"proc": Popen, "log": path, "done": bool}
_downloads = {}
_lock = threading.Lock()

LOG_DIR = "/tmp/dl_logs"
os.makedirs(LOG_DIR, exist_ok=True)


def get_registry():
    """Load models.json and expand {storage} placeholder with STORAGE_DIR."""
    with open('/app/models.json', 'r') as f:
        raw = json.load(f)
    for entry in raw.values():
        for key in ('path',):
            if key in entry:
                entry[key] = entry[key].replace('{storage}', STORAGE_DIR)
    return raw


def _stream_output(proc, log_path, model_id, final=True):
    """Read subprocess output line-by-line, write to log file AND stdout (docker logs).

    If final=False, the __STATUS__ sentinel is suppressed so the SSE stream
    stays open for a subsequent download (e.g. mmproj sidecar).
    """
    prefix = f"[download:{model_id}]"
    with open(log_path, 'a', buffering=1) as lf:
        for line in iter(proc.stdout.readline, ''):
            msg = f"{prefix} {line.rstrip()}"
            print(msg, flush=True)
            lf.write(line)
        proc.wait()
    rc = proc.returncode
    status = "complete" if rc == 0 else f"failed (exit {rc})"
    print(f"{prefix} Download {status}.", flush=True)
    if final:
        with open(log_path, 'a') as lf:
            lf.write(f"\n__STATUS__{status}\n")
        with _lock:
            if model_id in _downloads:
                _downloads[model_id]["done"] = True


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

    The Ostris UI (port 8675) exposes a simple /api/queue endpoint that returns
    a list of jobs.  An empty list, or all jobs in a terminal state
    (completed / failed / cancelled), counts as idle.
    We fall back to True (idle) if the endpoint is not reachable.
    """
    try:
        req = urllib.request.urlopen("http://localhost:8675/api/queue", timeout=4)
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


# ── Engine (llama_cpp.server) ─────────────────────────────────────────────────────
_engine_proc  = None   # subprocess.Popen or None
_engine_lock  = threading.Lock()
LLAMA_CONFIG  = '/tmp/llama_config.json'

def _get_engine_status():
    """Return (status_str, loaded_model_name | None)."""
    with _engine_lock:
        proc = _engine_proc
    if proc is None:
        return 'STOPPED', None
    rc = proc.poll()
    if rc is None:
        return 'RUNNING', getattr(proc, '_model_name', None)
    if rc == 0:
        return 'STOPPED', None
    return 'ERROR', None


@app.route('/api/platform')
def platform_info():
    """Return the current platform and tool URLs.
    The frontend uses this instead of URL-sniffing so the server is the
    single source of truth about the deployment environment.
    """
    return jsonify(_build_platform_info())


@app.route('/api/coordinator/<path:subpath>', methods=['GET', 'POST', 'PUT', 'DELETE'])
def coordinator_alias(subpath):
    """Mirror of /api/<subpath>.
    Nginx on the VLM (port 5002) and AI Toolkit (port 8676) server blocks
    forward /api/ to those tools' own backends. This /api/coordinator/ prefix
    lets the autostop-guard reach the coordinator from any page without
    knowing the absolute coordinator URL.
    """
    import urllib.parse
    from flask import stream_with_context
    # Re-dispatch internally by reconstructing the target URL and call the view
    target = f"/api/{subpath}"
    with app.test_request_context(
        target,
        method=request.method,
        data=request.get_data(),
        content_type=request.content_type,
        headers=request.headers,
    ):
        rv = app.dispatch_request()
    return rv


@app.route('/api/status')
def status():
    reg = get_registry()
    data = {"models": {}, "engine": False, "engine_status": "STOPPED"}
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

    eng_status, eng_model = _get_engine_status()
    data["engine_status"] = eng_status
    data["engine"]        = eng_status == "RUNNING"
    data["engine_model"]  = eng_model
    return jsonify(data)


@app.route('/api/engine/<action>', methods=['GET', 'POST'])
def engine_ctl(action):
    global _engine_proc
    if action not in ('start', 'stop'):
        return jsonify({"error": "invalid action"}), 400

    if action == 'stop':
        with _engine_lock:
            proc = _engine_proc
            _engine_proc = None
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            print("[engine] stopped.", flush=True)
        return jsonify({"status": "ok"})

    # ── START ──────────────────────────────────────────────────────────────────
    with _engine_lock:
        if _engine_proc and _engine_proc.poll() is None:
            return jsonify({"status": "already_running"})

        # Collect installed models (exclude mmproj sidecars)
        reg = get_registry()
        model_cfgs = []
        for mid, entry in reg.items():
            path = entry['path']
            if not os.path.exists(path):
                continue
            cfg = {
                "model":          path,
                "model_alias":    mid,
                "n_gpu_layers":   -1,
                "n_ctx":          4096,
                "chat_format":    "llava-1-5",
            }
            # Attach mmproj if one exists alongside the model
            mmproj_name = entry.get('mmproj')
            if mmproj_name:
                mmproj_path = os.path.join(os.path.dirname(path), mmproj_name)
                if os.path.exists(mmproj_path):
                    cfg["clip_model_path"] = mmproj_path
            model_cfgs.append(cfg)
    
        if not model_cfgs:
            return jsonify({"status": "error",
                            "detail": "No models installed. Download a model first."})
    
        # Write the llama_cpp config file
        config = {
            "host":   "0.0.0.0",
            "port":   5001,
            "models": model_cfgs,
        }
        with open(LLAMA_CONFIG, 'w') as f:
            json.dump(config, f, indent=2)
        print(f"[engine] Starting with {len(model_cfgs)} model(s): "
              f"{[m['model_alias'] for m in model_cfgs]}", flush=True)
    
        proc = subprocess.Popen(
            ['python3', '-m', 'llama_cpp.server', '--config_file', LLAMA_CONFIG],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        proc._model_name = model_cfgs[0]['model_alias'] if model_cfgs else None

        _engine_proc = proc

    # Stream llama_cpp logs to docker logs in background
    def _tail():
        for line in iter(proc.stdout.readline, ''):
            print(f"[llama] {line.rstrip()}", flush=True)
        proc.wait()
        print(f"[engine] process exited (rc={proc.returncode}).", flush=True)
    threading.Thread(target=_tail, daemon=True).start()

    return jsonify({"status": "ok", "models": [m['model_alias'] for m in model_cfgs]})


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

        repo     = entry['repo']
        filename = entry.get('file')
        mmproj   = entry.get('mmproj')   # optional vision projector sidecar
        dest_dir = os.path.dirname(entry['path'])
        os.makedirs(dest_dir, exist_ok=True)
    
        log_path = os.path.join(LOG_DIR, f"{id}.log")
        open(log_path, 'w').close()
        _downloads[id] = {"proc": None, "log": log_path, "done": False}

    def build_cmd(file=None):
        base = ["huggingface-cli", "download", repo]
        if file:
            base.append(file)
        return base + ["--local-dir", dest_dir, "--local-dir-use-symlinks=False"]

    def run_downloads():
        """Download main file, then mmproj sidecar (if any), sequentially."""
        # ── Main file ──────────────────────────────────────────────────────
        label = f"'{filename}'" if filename else f"repo '{repo}'"
        print(f"[download:{id}] Downloading {label} into {dest_dir}", flush=True)

        proc = subprocess.Popen(
            build_cmd(filename),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        with _lock:
            _downloads[id]["proc"] = proc

        _stream_output(proc, log_path, id, final=not mmproj)

        if proc.returncode != 0 or not mmproj:
            return

        # ── mmproj sidecar ─────────────────────────────────────────────────
        print(f"[download:{id}] Downloading mmproj '{mmproj}' into {dest_dir}", flush=True)
        with open(log_path, 'a') as lf:
            lf.write(f"\n[Fetching vision projector: {mmproj}]\n")

        proc2 = subprocess.Popen(
            build_cmd(mmproj),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        with _lock:
            _downloads[id]["proc"] = proc2

        _stream_output(proc2, log_path, id, final=True)

    with _lock:
        _downloads[id] = {"proc": None, "log": log_path, "done": False}

    threading.Thread(target=run_downloads, daemon=True).start()
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


@app.route('/api/autostop/ping', methods=['POST'])
def autostop_ping():
    """Reset the inactivity timer without changing any other settings.
    Called by the guard overlay when the user clicks 'I'm Still Here'."""
    with _autostop_lock:
        if _autostop["enabled"] and not _autostop["stopping"]:
            _autostop["last_active_at"] = None
            print("[autostop] Timer reset by user ping.", flush=True)
    return jsonify({"status": "ok"})


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