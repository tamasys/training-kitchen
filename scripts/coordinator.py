from flask import Flask, jsonify
from flask_cors import CORS
import subprocess, os, json

app = Flask(__name__)
CORS(app)

def get_registry():
    with open('/app/models.json', 'r') as f: return json.load(f)

@app.route('/api/status')
def status():
    reg = get_registry()
    data = {"models": {}, "engine": False}
    for k, v in reg.items():
        data["models"][k] = {"installed": os.path.exists(v['path']), "name": v['name'], "desc": v['desc']}
    
    # Check if LLM engine is running via supervisor
    res = subprocess.run(['supervisorctl', 'status', 'llama_router'], capture_output=True, text=True)
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
    repo = entry['repo']
    path = os.path.dirname(entry['path'])
    os.makedirs(path, exist_ok=True)
    subprocess.Popen([f"huggingface-cli download {repo} --local-dir {path} --local-dir-use-symlinks=False"], shell=True)
    return jsonify({"status": "started"})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5005)