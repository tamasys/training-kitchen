# --- STAGE 1: The Builder ---
FROM nvidia/cuda:12.2.2-devel-ubuntu22.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y python3-pip git cmake ninja-build build-essential
RUN pip3 install scikit-build-core[pyproject] setuptools

# CUDA Build Flags
ENV FORCE_CMAKE=1
ENV CMAKE_ARGS="-DGGML_CUDA=on -DCMAKE_LIBRARY_PATH=/usr/local/cuda/lib64/stubs"
ENV TORCH_CUDA_ARCH_LIST="7.0;7.5;8.0;8.6;8.9;9.0+PTX"

# Compile llama-cpp-python once
RUN pip3 wheel --no-cache-dir --wheel-dir=/root/wheels llama-cpp-python[server] --no-build-isolation

# --- STAGE 2: The Final Runner ---
FROM nvidia/cuda:12.2.2-runtime-ubuntu22.04

# Minimal runtime dependencies
RUN apt-get update && apt-get install -y \
    python3-pip git curl nginx supervisor \
    libgl1-mesa-glx libglib2.0-0 jq \
    && rm -rf /var/lib/apt/lists/*

# Copy and install the pre-compiled wheel from Stage 1
COPY --from=builder /root/wheels /root/wheels
RUN pip3 install --no-cache-dir /root/wheels/*.whl

# Install other Python tools
RUN pip3 install --no-cache-dir huggingface_hub flask flask-cors

# Install FileBrowser Quantum
RUN curl -fsSL https://raw.githubusercontent.com/filebrowser/get/master/get.sh | bash

WORKDIR /app
COPY . .
RUN chmod +x start.sh scripts/*.sh

# Ports: 80 (Dashboard), 8080 (Files), 5001 (LLM API), 5002 (VLM UI), 7860 (Ostris GUI)
EXPOSE 80 8080 5001 5002 7860
ENTRYPOINT ["/app/start.sh"]