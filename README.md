# Training Kitchen

A Docker environment for easy deployment of captioning images and training AI image models.

## Features

- **Central Dashboard**: Easy access to all tools with live status monitoring.
- **Easy LLM Selection**: One-click setup of selected vision models for automated dataset tagging, or upload your own.
- **Ostris AI-Toolkit**: Full web interface for LoRA training.
- **FileBrowser Quantum**: File management for your cloud volumes.

## Workflow

1. **Prepare**: Upload images via FileBrowser (8080).
2. **Caption**: Download a vision model via the Dashboard, then launch the VLM Tool (5002).
3. **Clear memory**: Click **"STOP ENGINE"** on the Dashboard to clear VRAM.
4. **Train**: Launch Ostris (8675) and start your training job.
