#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="/opt/gpu-node-inspector"
CONFIG_DIR="/etc/gpu-node-inspector"
LOG_DIR="/var/log/gpu-node-inspector"
USER="inspector"

if [ "$EUID" -ne 0 ]; then
    echo "Please run as root"
    exit 1
fi

# Python version check
PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
if [[ $(echo -e "3.9\n$PYTHON_VERSION" | sort -V | head -n1) != "3.9" ]]; then
    echo "Python >= 3.9 is required, found $PYTHON_VERSION"
    exit 1
fi

# Create user
if ! id "$USER" &>/dev/null; then
    useradd --system --no-create-home --home-dir "$INSTALL_DIR" "$USER"
fi

# Install directory
mkdir -p "$INSTALL_DIR"
cp -r inspector main.py requirements.txt pyproject.toml templates "$INSTALL_DIR/"
mkdir -p "$INSTALL_DIR"/data "$INSTALL_DIR"/logs
chown -R "$USER:$USER" "$INSTALL_DIR"

# Config directory
mkdir -p "$CONFIG_DIR"
if [ ! -f "$CONFIG_DIR/config.yaml" ]; then
    cp config.example.yaml "$CONFIG_DIR/config.yaml"
    echo "Please edit $CONFIG_DIR/config.yaml"
fi
if [ ! -f "$CONFIG_DIR/env" ]; then
    touch "$CONFIG_DIR/env"
    chmod 600 "$CONFIG_DIR/env"
    chown "$USER:$USER" "$CONFIG_DIR/env"
    echo "Please edit $CONFIG_DIR/env with secrets"
fi
chmod 600 "$CONFIG_DIR/config.yaml"

# Log directory
mkdir -p "$LOG_DIR"
chown "$USER:$USER" "$LOG_DIR"

# Virtualenv
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

# Systemd service
cat > /etc/systemd/system/gpu-node-inspector.service <<EOF
[Unit]
Description=GPU Node Inspector
After=network.target

[Service]
Type=simple
User=$USER
Group=$USER
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$CONFIG_DIR/env
ExecStart=$INSTALL_DIR/venv/bin/python -m main
Restart=always
RestartSec=5
StandardOutput=append:$LOG_DIR/inspector.log
StandardError=append:$LOG_DIR/inspector.log

[Install]
WantedBy=multi-user.target
EOF

# Logrotate
cp scripts/logrotate.conf /etc/logrotate.d/gpu-node-inspector

# SSH key permissions hint
echo "Ensure your SSH private key files are chmod 600 and owned by $USER"

systemctl daemon-reload
systemctl enable gpu-node-inspector.service
echo "Run: systemctl start gpu-node-inspector"
