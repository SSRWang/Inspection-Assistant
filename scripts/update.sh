#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ "$EUID" -ne 0 ]; then
    echo "Please run as root"
    exit 1
fi

cd "$PROJECT_DIR"
echo "Pulling latest source..."
git pull

echo "Re-running installer..."
bash "$SCRIPT_DIR/install.sh"

echo "Restarting service..."
systemctl restart gpu-node-inspector

systemctl status gpu-node-inspector --no-pager
