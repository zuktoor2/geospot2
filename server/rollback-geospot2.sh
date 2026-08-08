#!/usr/bin/env bash
set -euo pipefail

BASE=/home/codexops/geospot2
ROLLBACK="${1:-$BASE/rollback/latest}"
VHOST=/etc/apache2/sites-available/wecreativeforge-ssl.conf

test "$(id -u)" -eq 0 || { echo "Run this rollback with sudo."; exit 1; }
ROLLBACK="$(readlink -f "$ROLLBACK")"
test -f "$ROLLBACK/wecreativeforge-ssl.conf" || { echo "Rollback snapshot not found."; exit 1; }

systemctl disable --now geospot2.service 2>/dev/null || true
cp -a "$ROLLBACK/wecreativeforge-ssl.conf" "$VHOST"
if [[ -f "$ROLLBACK/geospot2.service" ]]; then
    cp -a "$ROLLBACK/geospot2.service" /etc/systemd/system/geospot2.service
else
    rm -f /etc/systemd/system/geospot2.service
fi
if [[ -f "$ROLLBACK/geospot2-proxy.conf" ]]; then
    cp -a "$ROLLBACK/geospot2-proxy.conf" /etc/apache2/geospot2-proxy.conf
else
    rm -f /etc/apache2/geospot2-proxy.conf
fi
apache2ctl configtest
systemctl daemon-reload
systemctl reload apache2

echo "GeoSpot route and service rolled back. Game data and releases were preserved in $BASE."

