#!/bin/bash
#
# Put Apache in front of the dealer on its public plain-HTTP port.
#
# Why bother, when the dealer can listen on that port itself? Because then
# the port is only up when the dealer is. Restart the service and every
# client fetching the list gets a connection refused for a second or two,
# and a crash takes it out until someone notices.
#
# With Apache in front, /servers is served straight off the file the dealer
# republishes every time it rebuilds the list. That answers whether the
# dealer is running or not, needs no work from it, and is the same bytes.
#
# The dealer has to move off the public port for this: set
#
#   DOGDORM_DEALER_BIND_HOST=127.0.0.1
#   DOGDORM_DEALER_PORT=8002
#   DOGDORM_SERVERS_FILE=/opt/dogdorm/servers.json
#
# in the service environment, and restart it, BEFORE running this. Apache
# cannot bind a port something else is holding, and on Debian a failed bind
# stops the whole server -- every other site with it.
#
# Usage:
#
#   ./install_http_front.sh [public_port] [dealer] [server_name]
#
# e.g. ./install_http_front.sh 8000 127.0.0.1:8002 ovh1.p2pd.net

set -e

# apache2ctl and the a2* helpers live in sbin, which is not on a normal
# user's PATH on Debian.
PATH="$PATH:/usr/sbin:/sbin"

if [ "$EUID" -eq 0 ]; then
    echo "Error: Do not run this script as root."
    echo "Please run as a normal user (it will sudo where it needs to)."
    exit 1
fi

PUBLIC_PORT="${1:-8000}"
BACKEND="${2:-127.0.0.1:8002}"
SERVER_NAME="${3:-$(hostname -f 2>/dev/null || hostname)}"
SERVERS_FILE="${SERVERS_FILE-/opt/dogdorm/servers.json}"

LISTEN_CONF="/etc/apache2/conf-available/dogdorm-listen-$PUBLIC_PORT.conf"
SITE_NAME="dogdorm-http-$PUBLIC_PORT"
SITE_FILE="/etc/apache2/sites-available/$SITE_NAME.conf"

if ! command -v apache2ctl >/dev/null 2>&1; then
    echo "Error: Apache is not installed on this machine."
    exit 1
fi

# Refuse to run while something else holds the port, because Apache failing
# to bind at reload takes down every site on the box, not just this one.
if ss -lnt "sport = :$PUBLIC_PORT" 2>/dev/null | grep -q LISTEN; then
    echo "Error: something is already listening on port $PUBLIC_PORT:"
    ss -lntp "sport = :$PUBLIC_PORT" 2>/dev/null | tail -n +2
    echo
    echo "Move the dealer off it first (see the notes at the top of this file)."
    exit 1
fi

echo "Enabling the modules the vhost needs..."
sudo a2enmod -q proxy proxy_http deflate

echo "Listening on $PUBLIC_PORT..."
sudo tee "$LISTEN_CONF" > /dev/null <<EOF
# Added by dogdorm's install_http_front.sh.
Listen $PUBLIC_PORT
EOF
sudo a2enconf -q "dogdorm-listen-$PUBLIC_PORT"

SERVE_LIST=""
if [ -n "$SERVERS_FILE" ]; then
    SERVERS_DIR=$(dirname "$SERVERS_FILE")
    SERVERS_NAME=$(basename "$SERVERS_FILE")
    SERVE_LIST=$(cat <<EOF

    # Straight off disk, so it answers even when the dealer is not running.
    # ProxyPass has to be told to leave the path alone or it wins over Alias.
    ProxyPass /servers !
    Alias /servers $SERVERS_FILE
    <Directory "$SERVERS_DIR">
        <Files "$SERVERS_NAME">
            Require all granted
        </Files>
    </Directory>
EOF
)
fi

echo "Creating vhost on $PUBLIC_PORT for $SERVER_NAME..."
sudo tee "$SITE_FILE" > /dev/null <<EOF
# Added by dogdorm's install_http_front.sh. Public plain-HTTP front for the
# dealer, which listens on $BACKEND.
<VirtualHost *:$PUBLIC_PORT>
    ServerName $SERVER_NAME

    # Only the read-only routes are reachable. The dealer decides whether a
    # caller is local by looking at the client address, and behind a proxy
    # that is whatever the proxy says it is -- so the routes that must never
    # be public are refused here, before they reach it.
    <Location "/">
        Require all denied
    </Location>
    <LocationMatch "^/(servers|legacy)?/?\$">
        Require all granted
    </LocationMatch>
$SERVE_LIST
    ProxyPreserveHost Off
    ProxyPass / http://$BACKEND/
    ProxyPassReverse / http://$BACKEND/

    # The list gzips to about a twentieth of its size, and Debian's
    # deflate.conf does not cover application/json.
    <IfModule mod_deflate.c>
        AddOutputFilterByType DEFLATE application/json
    </IfModule>

    ErrorLog \${APACHE_LOG_DIR}/dogdorm-$PUBLIC_PORT-error.log
    CustomLog \${APACHE_LOG_DIR}/dogdorm-$PUBLIC_PORT-access.log combined
</VirtualHost>
EOF
sudo a2ensite -q "$SITE_NAME"

echo "Checking the config..."
if ! sudo apache2ctl configtest; then
    echo "Config test failed -- backing the site out and leaving Apache alone."
    sudo a2dissite -q "$SITE_NAME"
    exit 1
fi

echo "Reloading Apache..."
sudo systemctl reload apache2

echo "Done. Try: curl http://$SERVER_NAME:$PUBLIC_PORT/servers"
echo "Remove with: sudo a2dissite $SITE_NAME && sudo a2disconf dogdorm-listen-$PUBLIC_PORT && sudo systemctl reload apache2"
