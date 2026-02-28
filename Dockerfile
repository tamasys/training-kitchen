# --- FINAL RUNNER ---
FROM nvidia/cuda:12.2.2-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive

# Runtime dependencies + Node.js 18 (required for ai-toolkit UI)
RUN apt-get update && apt-get install -y \
    python3-pip git curl nginx supervisor \
    libgl1-mesa-glx libglib2.0-0 jq \
    && curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies including llama-cpp-python pre-built wheel for CUDA 12.2
RUN pip3 install --no-cache-dir \
    huggingface_hub flask flask-cors \
    "llama-cpp-python[server]" \
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu122

# Install FileBrowser Quantum (gtsteffaniak/filebrowser) - update version pin as needed
RUN curl -fsSL https://github.com/gtsteffaniak/filebrowser/releases/download/v1.2.1-stable/linux-amd64-filebrowser \
    -o /usr/local/bin/filebrowser && \
    chmod +x /usr/local/bin/filebrowser

# Bake in toolkits at build time for fast container startup.
# The updater.sh script will git pull and conditionally reinstall on each boot.
RUN git clone --depth=1 https://github.com/ostris/ai-toolkit.git /app/ai-toolkit && \
    pip3 install -q --no-cache-dir -r /app/ai-toolkit/requirements.txt && \
    cd /app/ai-toolkit/ui && npm install

RUN git clone --depth=1 https://github.com/victorchall/vlm-caption.git /app/vlm-caption && \
    pip3 install -q --no-cache-dir -r /app/vlm-caption/requirements.txt

# flux_train_ui.py used an old Gradio API - removed, using the real Node.js UI instead

# Copy app files (separate layer so upstream repo changes don't bust this cache)
WORKDIR /app
COPY . .
RUN chmod +x start.sh scripts/updater.sh

# Install nginx dashboard config and remove default site
RUN rm -f /etc/nginx/sites-enabled/default && \
    ln -s /app/nginx.conf /etc/nginx/sites-enabled/training-kitchen

# Ports: 80 (Dashboard), 5005 (Coordinator API), 8080 (Files), 5001 (LLM API), 5002 (VLM UI), 8675 (AI Toolkit UI)
EXPOSE 80 5005 8080 5001 5002 8675
ENTRYPOINT ["/app/start.sh"]