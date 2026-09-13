#!/bin/bash
#
# Serve the dealer over HTTPS by putting Apache in front of it.
#
# The dealer speaks plain HTTP and only that (see dealer/__main__.py). Rather
# than teach it TLS -- which would mean handing the Python process a copy of
# a private key, and bouncing the dealer and all its workers every time the
# certificate renews -- this points an Apache vhost at it on an HTTPS port.
# Certbot already knows how to renew the certificate and reload Apache, so
# once this is installed there is nothing left to maintain.
#
# The certificate must already exist. To get one for a name that this
# machine answers on port 80 for:
#
#   sudo certbot certonly --webroot -w /var/www/html -d your.domain
#
# Usage:
#
#   ./install_https.sh <domain> [https_port] [backend]
#
# e.g. ./install_https.sh warpgate.io 8001 127.0.0.1:8000
#
# Run it once per domain you want to answer on. Domains sharing a port are
# told apart by SNI, so several can point at the same dealer.

set -e

# apache2ctl and the a2* helpers live in sbin, which is not on a normal
# user's PATH on Debian.
PATH="$PATH:/usr/sbin:/sbin"

if [ "$EUID" -eq 0 ]; then
    echo "Error: Do not run this script as root."
    echo "Please run as a normal user (it will sudo where it needs to)."
    exit 1
fi

DOMAIN="$1"
HTTPS_PORT="${2:-8001}"
BACKEND="${3:-127.0.0.1:8000}"

# The dealer republishes the finished list here every time it rebuilds it.
# Serving that file rather than proxying keeps /servers answering while the
# dealer restarts, and costs the dealer nothing. Set it to "" to proxy
# /servers like everything else.
SERVERS_FILE="${SERVERS_FILE-/opt/dogdorm/servers.json}"

if [ -z "$DOMAIN" ]; then
    echo "usage: $0 <domain> [https_port] [backend host:port]"
    exit 1
fi

CERT_DIR="/etc/letsencrypt/live/$DOMAIN"
LISTEN_CONF="/etc/apache2/conf-available/dogdorm-listen-$HTTPS_PORT.conf"
SITE_NAME="dogdorm-https-$DOMAIN"
SITE_FILE="/etc/apache2/sites-available/$SITE_NAME.conf"

if ! command -v apache2ctl >/dev/null 2>&1; then
    echo "Error: Apache is not installed on this machine."
    exit 1
fi

if ! sudo test -s "$CERT_DIR/fullchain.pem"; then
    echo "Error: no certificate at $CERT_DIR."
    echo "Get one first, e.g.:"
    echo "  sudo certbot certonly --webroot -w /var/www/html -d $DOMAIN"
    exit 1
fi

# A certificate usually covers more than the one name asked for (the www.
# form, most often). Serve every name on it, or a request for one of the
# others falls through to whichever vhost happens to be first on this port
# and gets handed the wrong certificate.
ALIASES=$(sudo openssl x509 -in "$CERT_DIR/fullchain.pem" -noout -ext subjectAltName \
    | tr ',' '\n' | sed -n 's/.*DNS://p' | tr -d ' ' | grep -vx "$DOMAIN" | tr '\n' ' ')
ALIASES="${ALIASES% }"

echo "Enabling the modules the vhost needs..."
sudo a2enmod -q ssl proxy proxy_http deflate headers

# The Listen lives in its own file keyed by port, so that installing a second
# domain on the same port doesn't try to bind it twice (which Apache treats
# as fatal).
echo "Listening on $HTTPS_PORT..."
sudo tee "$LISTEN_CONF" > /dev/null <<EOF
# Added by dogdorm's install_https.sh -- the port the dealer is served on.
<IfModule ssl_module>
    Listen $HTTPS_PORT
</IfModule>
EOF
sudo a2enconf -q "dogdorm-listen-$HTTPS_PORT"

# Serve the published list off disk when there is one, so a dealer restart
# does not take /servers with it. ProxyPass has to be told to leave that path
# alone, or it would win over the Alias.
SERVE_LIST=""
if [ -n "$SERVERS_FILE" ]; then
    SERVERS_DIR=$(dirname "$SERVERS_FILE")
    SERVERS_NAME=$(basename "$SERVERS_FILE")
    SERVE_LIST=$(cat <<EOF

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

echo "Creating vhost for $DOMAIN${ALIASES:+ (also serving: $ALIASES)}..."
sudo tee "$SITE_FILE" > /dev/null <<EOF
# Added by dogdorm's install_https.sh. HTTPS front end for the dealer, which
# is itself listening on $BACKEND over plain HTTP.
<IfModule ssl_module>
<VirtualHost *:$HTTPS_PORT>
    ServerName $DOMAIN
${ALIASES:+    ServerAlias $ALIASES}

    Include /etc/letsencrypt/options-ssl-apache.conf
    SSLCertificateFile $CERT_DIR/fullchain.pem
    SSLCertificateKeyFile $CERT_DIR/privkey.pem

    # The dealer builds no absolute URLs of its own, but FastAPI redirects
    # /servers/ to /servers, and the Location it sends back has to be
    # rewritten to point at this vhost. Leaving ProxyPreserveHost off is what
    # makes that work: the dealer then names the backend in its Location
    # header, ProxyPassReverse recognises it, and the client is sent to
    # https://<whatever name it asked for>:$HTTPS_PORT. Preserving the
    # original Host instead would have the dealer emit the right host with
    # the wrong scheme -- plain http, pointed at this TLS port.
    # Only the read-only routes are reachable from out here. The dealer's
    # own guard on /work, /insert, /complete and /alias works by looking at
    # the client address, which behind a proxy is whatever the proxy says it
    # is -- correct today only because uvicorn rewrites it from the header
    # Apache appends. That is one flag away from being wrong, so the routes
    # that must never be public are refused before they reach the dealer.
    <Location "/">
        Require all denied
    </Location>
    <LocationMatch "^/(servers|legacy)?/?$">
        Require all granted
    </LocationMatch>

$SERVE_LIST

    # The list was served by the dealer until now, and its CORS middleware
    # sent this. Serving the file directly means the web server has to, or a
    # page on another origin -- the netstats dashboard, say -- cannot read it.
    <IfModule mod_headers.c>
        Header always set Access-Control-Allow-Origin "*"
        Header always set Access-Control-Allow-Methods "GET, OPTIONS"
    </IfModule>
    ProxyPreserveHost Off
    ProxyPass / http://$BACKEND/
    ProxyPassReverse / http://$BACKEND/

    # The server list is most of a megabyte of JSON that gzips to about a
    # twentieth of that. Debian's deflate.conf lists a handful of text types
    # and application/json is not among them, so ask for it here.
    <IfModule mod_deflate.c>
        AddOutputFilterByType DEFLATE application/json
    </IfModule>

    ErrorLog \${APACHE_LOG_DIR}/dogdorm-$DOMAIN-error.log
    CustomLog \${APACHE_LOG_DIR}/dogdorm-$DOMAIN-access.log combined
</VirtualHost>
</IfModule>
EOF
sudo a2ensite -q "$SITE_NAME"

echo "Checking the config..."
sudo apache2ctl configtest

echo "Reloading Apache..."
sudo systemctl reload apache2

echo "Done. Try: curl https://$DOMAIN:$HTTPS_PORT/servers"
echo "Remove with: sudo a2dissite $SITE_NAME && sudo systemctl reload apache2"
