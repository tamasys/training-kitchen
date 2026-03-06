# Training Kitchen

Easily deploy tools for captioning images and training AI image models in the cloud.

## Features

- **Central dashboard**: Easy access to all tools with live status monitoring.
- **Easy model selection**: One-click setup of selected vision models for automated dataset tagging, or upload your own.
- **Simple image captioning**: Straightforward custom front-end for VLM Image Captioner.
- **Ostris AI-Toolkit**: Full web interface for LoRA training.
- **FileBrowser Quantum**: File management for your cloud volumes.

## Workflow

1. **Prepare**: Upload images via FileBrowser (8080).
2. **Caption**: Download a vision model via the Dashboard, click **"START ENGINE"**, then launch the VLM Tool (5002).
3. **Clear memory**: Click **"STOP ENGINE"** on the Dashboard to clear VRAM.
4. **Train**: Launch Ostris (8675), load your captioned images, and start your training job.
