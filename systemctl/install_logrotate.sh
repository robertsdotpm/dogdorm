#!/bin/bash
#
# Cap the size of dogdorm's logs.
#
# The dealer writes one log and every worker writes another, so a hundred
# workers means a hundred growing files. Nothing rotated them: on the P2PD
# monitor that folder reached 14 GB.
#
# Rotation is copytruncate because the workers open their log once at startup
# and never reopen it. Renaming the file under them would leave every worker
# writing to the rotated copy forever, and the live log would stay empty.
#
# logrotate's own cron entry runs daily, which is far too coarse when a
# hundred processes are writing, so this also installs an hourly timer. Both
# share logrotate's default state file, so they cannot double-rotate.
#
# Usage:
#
#   ./install_logrotate.sh [max_size] [keep]
#
# e.g. ./install_logrotate.sh 5M 1

set -e

# logrotate lives in sbin, which is not on a normal user's PATH on Debian.
PATH="$PATH:/usr/sbin:/sbin"

if [ "$EUID" -eq 0 ]; then
    echo "Error: Do not run this script as root."
    echo "Please run as a normal user (it will sudo where it needs to)."
    exit 1
fi

MAX_SIZE="${1:-5M}"
KEEP="${2:-1}"
LOG_DIR=/opt/dogdorm
CONF=/etc/logrotate.d/dogdorm

if ! command -v logrotate >/dev/null 2>&1; then
    echo "Error: logrotate is not installed (apt install logrotate)."
    exit 1
fi

# Rotate as whoever owns the logs, which is whoever the service runs as.
OWNER=$(stat -c '%U %G' "$LOG_DIR" 2>/dev/null || echo "")
if [ -z "$OWNER" ]; then
    echo "Error: $LOG_DIR does not exist. Install the service first."
    exit 1
fi

echo "Writing $CONF (max $MAX_SIZE per file, keeping $KEEP, owned by $OWNER)..."
sudo tee "$CONF" > /dev/null <<EOF
# Added by dogdorm's install_logrotate.sh.
$LOG_DIR/*.log {
    su $OWNER
    size $MAX_SIZE
    rotate $KEEP
    compress
    missingok
    notifempty
    copytruncate
}
EOF

echo "Installing an hourly timer so the cap is checked more than once a day..."
sudo tee /etc/systemd/system/dogdorm-logrotate.service > /dev/null <<EOF
[Unit]
Description=Cap dogdorm's logs

[Service]
Type=oneshot
ExecStart=/usr/sbin/logrotate $CONF
EOF

sudo tee /etc/systemd/system/dogdorm-logrotate.timer > /dev/null <<EOF
[Unit]
Description=Cap dogdorm's logs hourly

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now dogdorm-logrotate.timer

echo "Checking the config parses..."
sudo logrotate -d "$CONF" > /dev/null

echo "Done. Rotate now with: sudo logrotate -v $CONF"
echo "Next run: $(systemctl list-timers dogdorm-logrotate.timer --no-pager 2>/dev/null | sed -n 2p)"
