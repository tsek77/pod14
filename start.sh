#!/usr/bin/env bash
# start.sh — Апп эхлүүлэх
source /workspace/app/.venv/bin/activate
cd /workspace/app
GRADIO_SERVER_NAME=0.0.0.0 GRADIO_SERVER_PORT=7860 \
DOTENV_PATH=/workspace/.env \
python main.py
