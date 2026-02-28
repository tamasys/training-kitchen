# --- FINAL RUNNER ---
FROM nvidia/cuda:12.2.2-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive

# Minimal runtime dependencies
RUN apt-get update && apt-get install -y \
    python3-pip git curl nginx supervisor \
    libgl1-mesa-glx libglib2.0-0 jq \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies including llama-cpp-python pre-built wheel for CUDA 12.2
RUN pip3 install --no-cache-dir \
    huggingface_hub flask flask-cors \
    "llama-cpp-python[server]" \
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu122

# Install FileBrowser Quantum
RUN curl -fsSL https://raw.githubusercontent.com/filebrowser/get/master/get.sh | bash

WORKDIR /app
COPY . .
RUN chmod +x start.sh

# Ports: 80 (Dashboard), 8080 (Files), 5001 (LLM API), 5002 (VLM UI), 7860 (Ostris GUI)
EXPOSE 80 8080 5001 5002 7860
ENTRYPOINT ["/app/start.sh"]