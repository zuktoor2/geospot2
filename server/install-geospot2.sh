#!/usr/bin/env bash
set -euo pipefail

BASE=/home/codexops/geospot2
SOURCE="$BASE/source"
STAMP="$(date +%Y%m%d-%H%M%S)"
RELEASE="$BASE/releases/$STAMP"
ROLLBACK="$BASE/rollback/$STAMP"
VHOST=/etc/apache2/sites-available/wecreativeforge-ssl.conf
SERVICE=/etc/systemd/system/geospot2.service
PROXY=/etc/apache2/geospot2-proxy.conf

test "$(id -u)" -eq 0 || { echo "Run this installer with sudo."; exit 1; }
test -f "$SOURCE/index.html" || { echo "Missing $SOURCE/index.html"; exit 1; }
test -f "$SOURCE/server/app.py" || { echo "Missing server application."; exit 1; }
test -f "$VHOST" || { echo "Missing expected Apache vhost: $VHOST"; exit 1; }

install -d -o codexops -g codexops "$BASE/data" "$BASE/releases" "$BASE/rollback" "$RELEASE/public" "$ROLLBACK"
cp -a "$VHOST" "$ROLLBACK/wecreativeforge-ssl.conf"
test ! -e "$SERVICE" || cp -a "$SERVICE" "$ROLLBACK/geospot2.service"
test ! -e "$PROXY" || cp -a "$PROXY" "$ROLLBACK/geospot2-proxy.conf"

find "$SOURCE" -maxdepth 1 -type f ! -name '*.md' -exec cp -a {} "$RELEASE/public/" \;
test ! -d "$SOURCE/vendor" || cp -a "$SOURCE/vendor" "$RELEASE/public/vendor"
chown -R codexops:codexops "$RELEASE"
ln -sfn "$RELEASE" "$BASE/current"
chown -h codexops:codexops "$BASE/current"

if [[ ! -x "$BASE/venv/bin/python" ]]; then
    sudo -u codexops python3 -m venv "$BASE/venv"
fi
sudo -u codexops "$BASE/venv/bin/pip" install --disable-pip-version-check -r "$SOURCE/server/requirements.txt"

install -m 0644 "$SOURCE/server/geospot2.service" "$SERVICE"
install -m 0644 "$SOURCE/server/geospot2-proxy.conf" "$PROXY"
if ! grep -qF 'IncludeOptional /etc/apache2/geospot2-proxy.conf' "$VHOST"; then
    sed -i '/<\/VirtualHost>/i\    IncludeOptional /etc/apache2/geospot2-proxy.conf' "$VHOST"
fi

ln -sfn "$ROLLBACK" "$BASE/rollback/latest"
chown -h codexops:codexops "$BASE/rollback/latest"
apache2ctl configtest
systemctl daemon-reload
systemctl enable --now geospot2.service
systemctl reload apache2

curl --fail --silent http://127.0.0.1:61210/api/health
echo
echo "GeoSpot installed. Rollback snapshot: $ROLLBACK"

