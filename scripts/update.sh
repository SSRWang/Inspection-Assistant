#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ "$EUID" -ne 0 ]; then
    echo "Please run as root"
    exit 1
fi

cd "$PROJECT_DIR"
echo "Pulling latest source from Gitee..."
# Prefer Gitee (fast from China-based servers); fall back to GitHub origin.
if git remote get-url gitee >/dev/null 2>&1; then
    git pull gitee main || git pull origin main
else
    git pull origin main
fi

echo "Re-running installer..."
bash "$SCRIPT_DIR/install.sh"

echo "Restarting service..."
systemctl restart gpu-node-inspector

systemctl status gpu-node-inspector --no-pager
