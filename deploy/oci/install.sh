#!/usr/bin/env bash
# Install (or upgrade to) one capture release on the OCI VM. Run as root from
# the extracted release directory:
#
#   sudo /opt/trade-capture/releases/<commit>/deploy/oci/install.sh
#
# Idempotent. It never starts, stops or restarts a recorder and never edits an
# existing /etc/trade-capture/capture.env: starting capture, choosing the roots
# and restarting for an upgrade are explicit operator steps (README.md).
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }
RELEASE_DIR=$(cd "$(dirname "$0")/../.." && pwd -P)
case "$RELEASE_DIR" in
    /opt/trade-capture/releases/*) ;;
    *) echo "install from /opt/trade-capture/releases/<commit>, not $RELEASE_DIR" >&2; exit 1 ;;
esac
[ -f "$RELEASE_DIR/scripts/capture_bybit_public.py" ] || { echo "not a capture release" >&2; exit 1; }
/usr/bin/python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
    || { echo "python3 >= 3.11 required (pyproject requires-python)" >&2; exit 1; }
mountpoint -q /srv/trade-capture \
    || { echo "/srv/trade-capture must be the mounted archive block volume" >&2; exit 1; }

id trade-capture >/dev/null 2>&1 || useradd --system --user-group --no-create-home \
    --home-dir /nonexistent --shell /usr/sbin/nologin trade-capture
install -d -o trade-capture -g trade-capture -m 0750 \
    /srv/trade-capture /srv/trade-capture/validation /srv/trade-capture/archive
install -d -o root -g trade-capture -m 0750 /etc/trade-capture
[ -f /etc/trade-capture/capture.env ] || install -o root -g trade-capture -m 0640 \
    "$RELEASE_DIR/deploy/oci/capture.env.example" /etc/trade-capture/capture.env

# The release is code only (standard library Python): root-owned, read-only to the service.
chown -R root:root "$RELEASE_DIR"
chmod -R u+rwX,go+rX,go-w "$RELEASE_DIR"
chmod 0755 "$RELEASE_DIR/deploy/oci/trade-capture-health.sh" "$RELEASE_DIR/deploy/oci/install.sh"
ln -sfn "$RELEASE_DIR" /opt/trade-capture/current.new
mv -T /opt/trade-capture/current.new /opt/trade-capture/current

for unit in trade-capture-r0.service trade-capture-u2.service \
            trade-capture-health.service trade-capture-health.timer; do
    install -m 0644 "$RELEASE_DIR/deploy/oci/systemd/$unit" "/etc/systemd/system/$unit"
done
install -D -m 0644 "$RELEASE_DIR/deploy/oci/journald-trade-capture.conf" \
    /etc/systemd/journald.conf.d/trade-capture.conf
if [ -d /etc/chrony/sources.d ]; then
    install -m 0644 "$RELEASE_DIR/deploy/oci/chrony-oci.sources" /etc/chrony/sources.d/oci.sources
    systemctl enable chrony-wait.service >/dev/null 2>&1 || echo "note: chrony-wait.service not found"
else
    echo "note: chrony not installed; time-sync.target will not wait for a synced clock" >&2
fi
systemctl daemon-reload
systemctl restart systemd-journald

echo "installed release $RELEASE_DIR -> /opt/trade-capture/current"
echo "recorders NOT started/restarted. Next (README.md): review /etc/trade-capture/capture.env, then"
echo "  systemctl enable --now trade-capture-r0.service trade-capture-u2.service trade-capture-health.timer"
