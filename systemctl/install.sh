#!/bin/bash
set -e

# Prevent running as root
if [ "$EUID" -eq 0 ]; then
    echo "Error: Do not run this script as root."
    echo "Please run as a normal user."
    exit 1
fi

# Determine the user running this script
USER=$(whoami)
GROUP=$(id -gn "$USER")

# Variables
INSTALL_DIR=/opt/dogdorm
SERVICE_NAME=dogdorm
UNIT_FILE=/etc/systemd/system/$SERVICE_NAME.service

echo "Installing as user: $USER, group: $GROUP"


# Only remove if INSTALL_DIR is non-empty and starts with /opt/
if [[ -n "$INSTALL_DIR" && "$INSTALL_DIR" == /opt/* ]]; then
    echo "Removing old $INSTALL_DIR..."
    sudo rm -rf "$INSTALL_DIR"
fi

echo "Creating install directory..."
sudo mkdir -p "$INSTALL_DIR"

echo "Copying scripts..."
sudo cp start.sh stop.sh restart.sh "$INSTALL_DIR/"

echo "Setting ownership and permissions..."
sudo chown -R "$USER:$GROUP" "$INSTALL_DIR"
sudo chmod -R 755 "$INSTALL_DIR"

echo "Creating systemd unit file..."
sudo tee "$UNIT_FILE" > /dev/null <<EOF
[Unit]
Description=P2PD Server Monitor

[Service]
User=$USER
Group=$GROUP
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/start.sh
ExecStop=$INSTALL_DIR/stop.sh
ExecRestart=$INSTALL_DIR/restart.sh
Restart=no
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

echo "Reloading systemd..."
sudo systemctl daemon-reload

echo "Enabling and starting service..."
sudo systemctl enable $SERVICE_NAME
sudo systemctl start $SERVICE_NAME

echo "Done. Check status with: sudo systemctl status $SERVICE_NAME"
echo "Restart with: sudo systemctl restart $SERVICE_NAME"
echo "View logs with: sudo journalctl -u $SERVICE_NAME -f"
echo "Increase worker no with: export MONITOR_WORKER_NO=10; sudo systemctl restart $SERVICE_NAME"
