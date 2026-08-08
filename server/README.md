# GeoSpot server deployment

The public server version runs at `/geospot/` through Apache and listens internally on `127.0.0.1:61210`.

The deployment is additive:

- application files: `/home/codexops/geospot2`
- service: `/etc/systemd/system/geospot2.service`
- Apache include: `/etc/apache2/geospot2-proxy.conf`
- one include line in the existing HTTPS vhost
- SQLite data: `/home/codexops/geospot2/data/leaderboard.sqlite3`

Every install creates a timestamped release and rollback snapshot. Running `rollback-geospot2.sh` disables the service and restores the exact Apache vhost copy while preserving scores and releases.
