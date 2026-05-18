#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════
# setup.sh — RunPod Pod дээр нэг удаа ажиллуулах суулгалт
# Хэрэглэх: bash /workspace/app/setup.sh
# ══════════════════════════════════════════════════════════════════
set -euo pipefail

APP_DIR="/workspace/app"
VENV="$APP_DIR/.venv"

echo "══════════════════════════════════════════════"
echo "  Podcast Generator — RunPod Setup"
echo "══════════════════════════════════════════════"

# 1. System packages
echo ""
echo "[1/5] System packages суулгаж байна…"
apt-get update -qq
apt-get install -y --no-install-recommends \
    ffmpeg \
    python3-venv \
    python3-pip \
    git \
    nano \
    > /dev/null
echo "  ✅ ffmpeg, python3-venv, git суулгагдлаа"

# 2. Virtual environment
echo ""
echo "[2/5] Python virtual environment үүсгэж байна…"
python3 -m venv "$VENV"
source "$VENV/bin/activate"
pip install --upgrade pip --quiet
echo "  ✅ venv: $VENV"

# 3. Python packages
echo ""
echo "[3/5] Python dependencies суулгаж байна…"
pip install -r "$APP_DIR/requirements.txt" --quiet
echo "  ✅ Бүх dependency суулгагдлаа"

# 4. .env файл шалгах
echo ""
echo "[4/5] .env файл шалгаж байна…"
if [ ! -f "/workspace/.env" ]; then
    cp "$APP_DIR/.env.example" "/workspace/.env"
    echo "  ⚠  /workspace/.env үүсгэгдлэн — API key-үүдийг оруулна уу"
    echo "     nano /workspace/.env"
else
    echo "  ✅ /workspace/.env байна"
fi

# 5. Хавтасуудыг үүсгэх
echo ""
echo "[5/5] Хавтасуудыг үүсгэж байна…"
mkdir -p /workspace/{output,images,audio,subtitles,tmp}
echo "  ✅ /workspace/{output,images,audio,subtitles,tmp}"

echo ""
echo "══════════════════════════════════════════════"
echo "  ✅ Setup дууслаа!"
echo ""
echo "  Дараагийн алхмууд:"
echo "  1. API key-үүдийг оруулах:"
echo "     nano /workspace/.env"
echo ""
echo "  2. Апп эхлүүлэх:"
echo "     source $VENV/bin/activate"
echo "     cd $APP_DIR && python main.py"
echo ""
echo "  3. Браузераас нэвтрэх:"
echo "     RunPod → Connect → Port 7860"
echo "══════════════════════════════════════════════"
