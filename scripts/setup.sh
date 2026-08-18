#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m pip install -r requirements.txt
if command -v fc-match >/dev/null 2>&1 && ! fc-match "Noto Sans CJK SC" | grep -qi "Noto"; then
  if [[ "$(id -u)" == "0" ]] && command -v apt-get >/dev/null 2>&1; then
    echo "Installing Noto CJK fonts for code visuals..."
    apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk && rm -rf /var/lib/apt/lists/*
  else
    echo "NOTICE: 未检测到 Noto CJK 中文字体。代码配图会自动停用以避免方块字。"
    echo "Ubuntu/Debian 可执行: sudo apt-get install fonts-noto-cjk"
  fi
fi
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example"
fi
echo "Setup complete. Add API keys to .env, then run: ./scripts/run.sh"
