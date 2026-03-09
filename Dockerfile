# --- STAGE 1: BUILDER ---
# Use the 'devel' image to get nvcc and headers needed for compilation
FROM nvidia/cuda:12.2.2-devel-ubuntu22.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y python3-pip cmake git build-essential

# Force llama-cpp-python to compile from source with CUDA support
# This ensures we get the latest Qwen 3 VL architecture support
ENV CMAKE_ARGS="-DGGML_CUDA=on"
ENV FORCE_CMAKE=1

RUN pip3 install --no-cache-dir wheel
RUN pip3 wheel "llama-cpp-python[server]" --wheel-dir=/app/wheels

# --- STAGE 2: FINAL RUNNER ---
FROM nvidia/cuda:12.2.2-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive

# Make libcuda.so.1 (injected by the Docker GPU runtime) visible to the dynamic linker.
# /usr/local/nvidia/lib64 is where nvidia-container-toolkit bind-mounts the driver libs.
# /usr/local/cuda/lib64 covers any CUDA runtime libs baked into the base image.
ENV LD_LIBRARY_PATH=/usr/local/nvidia/lib64:/usr/local/cuda/lib64:${LD_LIBRARY_PATH}

# Runtime dependencies + Node.js 18
RUN apt-get update && apt-get install -y \
    python3-pip git curl nginx supervisor \
    libgl1-mesa-glx libglib2.0-0 jq \
    && curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Copy the pre-compiled wheel from the builder stage
COPY --from=builder /app/wheels /tmp/wheels

# Install standard dependencies + our custom-built llama-cpp-python
RUN pip3 install --no-cache-dir \
    huggingface_hub flask flask-cors \
    /tmp/wheels/*.whl && \
    rm -rf /tmp/wheels

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
    pip3 install -q --no-cache-dir -r /app/vlm-caption/requirements.txt && \
    rm -f /app/vlm-caption/caption.yaml   # never bake in a stale working config

WORKDIR /app
COPY . .
RUN chmod +x start.sh scripts/updater.sh && \
    rm -f /app/vlm-caption/caption.yaml

# Install nginx dashboard config and remove default site
RUN rm -f /etc/nginx/sites-enabled/default && \
    ln -s /app/nginx.conf /etc/nginx/sites-enabled/training-kitchen

# Ports: 80 (Dashboard), 5005 (Coordinator API), 8080 (Files), 5001 (LLM API), 5002 (VLM UI), 8676 (AI Toolkit UI via nginx)
EXPOSE 80 5005 8080 5001 5002 8676
ENTRYPOINT ["/app/start.sh"]