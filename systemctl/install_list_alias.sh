#!/bin/bash
#
# Serve the published server list from an existing website, on its own
# port, as a plain file.
#
# The dealer publishes the list to SERVERS_FILE and install_http_front.sh /
# install_https.sh serve it on the dealer's own ports. A URL with a port in
# it is awkward, though: some networks only let 80 and 443 out, and it looks
# odd in someone else's documentation. This adds the same file to a vhost
# you already have -- https://www.example.org/servers.json -- without
# touching anything else that vhost does.
#
# The settings go in a snippet of their own and the vhost gets one Include
# line, so undoing it is deleting that line. The snippet deliberately lives
# outside conf-available: enabled globally with a2enconf it would put the
# list on every site on the machine.
#
# Usage:
#
#   ./install_list_alias.sh <vhost_file> [url_path] [servers_file]
#
# e.g. ./install_list_alias.sh /etc/apache2/sites-available/https-warpgate.conf

set -e

# apache2ctl and the a2* helpers live in sbin, which is not on a normal
# user's PATH on Debian.
PATH="$PATH:/usr/sbin:/sbin"

if [ "$EUID" -eq 0 ]; then
    echo "Error: Do not run this script as root."
    echo "Please run as a normal user (it will sudo where it needs to)."
    exit 1
fi

VHOST="$1"
URL_PATH="${2:-/servers.json}"
SERVERS_FILE="${3:-/opt/dogdorm/servers.json}"
SNIPPET_DIR=/etc/apache2/dogdorm
SNIPPET="$SNIPPET_DIR/list-alias$(echo "$URL_PATH" | tr '/.' '--').conf"

if [ -z "$VHOST" ] || ! sudo test -f "$VHOST"; then
    echo "usage: $0 <vhost_file> [url_path] [servers_file]"
    exit 1
fi

if ! sudo test -f "$SERVERS_FILE"; then
    echo "Error: $SERVERS_FILE does not exist yet -- is the dealer publishing it?"
    exit 1
fi

echo "Enabling the modules the snippet uses..."
sudo a2enmod -q alias headers deflate

SERVERS_DIR=$(dirname "$SERVERS_FILE")
SERVERS_NAME=$(basename "$SERVERS_FILE")

echo "Writing $SNIPPET..."
sudo mkdir -p "$SNIPPET_DIR"
sudo tee "$SNIPPET" > /dev/null <<EOF
# Added by dogdorm's install_list_alias.sh, and Included from inside a vhost.
# Serves the dealer's published server list at $URL_PATH.
Alias $URL_PATH $SERVERS_FILE

<Directory "$SERVERS_DIR">
    <Files "$SERVERS_NAME">
        Require all granted
    </Files>
</Directory>

<Location "$URL_PATH">
    # Public data meant to be read from other people's pages.
    <IfModule mod_headers.c>
        Header always set Access-Control-Allow-Origin "*"
        Header always set Access-Control-Allow-Methods "GET, OPTIONS"
    </IfModule>

    # Most of a megabyte of JSON that gzips to a twentieth of that; Debian's
    # deflate.conf does not cover application/json.
    <IfModule mod_deflate.c>
        AddOutputFilterByType DEFLATE application/json
    </IfModule>
</Location>
EOF

BACKUP="$VHOST.bak-$(date +%Y%m%d-%H%M%S)"
sudo cp -a "$VHOST" "$BACKUP"

if sudo grep -qF "Include $SNIPPET" "$VHOST"; then
    echo "$VHOST already includes the snippet."
else
    echo "Adding the Include to $VHOST..."
    # Just before the vhost closes, so it applies to that vhost alone.
    sudo sed -i "0,/<\/VirtualHost>/s||    # dogdorm: serve the published server list at $URL_PATH\n    Include $SNIPPET\n</VirtualHost>|" "$VHOST"
fi

echo "Checking the config..."
if ! sudo apache2ctl configtest; then
    echo "Config test failed -- putting $VHOST back and leaving Apache alone."
    sudo cp -a "$BACKUP" "$VHOST"
    exit 1
fi

echo "Reloading Apache..."
sudo systemctl reload apache2

echo "Done. The previous vhost is at $BACKUP"
echo "Remove by deleting the Include line from $VHOST and reloading Apache."
