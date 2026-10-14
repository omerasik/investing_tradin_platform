#!/usr/bin/env bash
# Install (or upgrade to) one capture release on the capture VM (Oracle Linux 9
# or Ubuntu 24.04). Run as root from the extracted release directory:
#
#   sudo /opt/trade-capture/releases/<commit>/deploy/oci/install.sh
#
# Idempotent. It never starts, stops or restarts a recorder, never edits an
# existing /etc/trade-capture/capture.env, installs no package, changes no
# firewall and leaves the system python3 and journald untouched: starting
# capture, choosing the roots and restarting for an upgrade are explicit
# operator steps (README.md).
#
# Interpreter: the first of TRADE_CAPTURE_PYTHON, /usr/bin/python3.12,
# /usr/bin/python3.11, /usr/bin/python3.13, /usr/bin/python3 that is >= 3.11
# (pyproject requires-python) becomes /opt/trade-capture/python, the only
# interpreter the units and the health check use.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }
RELEASE_DIR=$(cd "$(dirname "$0")/../.." && pwd -P)
case "$RELEASE_DIR" in
    /opt/trade-capture/releases/*) ;;
    *) echo "install from /opt/trade-capture/releases/<commit>, not $RELEASE_DIR" >&2; exit 1 ;;
esac
[ -f "$RELEASE_DIR/scripts/capture_bybit_public.py" ] || { echo "not a capture release" >&2; exit 1; }

supported() { [ -x "$1" ] && "$1" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; }
PYTHON=""
for candidate in ${TRADE_CAPTURE_PYTHON:-} /usr/bin/python3.12 /usr/bin/python3.11 \
                 /usr/bin/python3.13 /usr/bin/python3; do
    if supported "$candidate"; then PYTHON=$candidate; break; fi
done
[ -n "$PYTHON" ] || {
    echo "no Python >= 3.11 found. Oracle Linux 9: dnf install python3.12 (python3 stays 3.9)" >&2
    exit 1
}
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
# Owner-provided URL files, if present, stay readable only by root and the service group.
for secret in /etc/trade-capture/backup-par-url /etc/trade-capture/alert-ping-url; do
    [ ! -e "$secret" ] || { chown root:trade-capture "$secret"; chmod 0640 "$secret"; }
done
ln -sfn "$PYTHON" /opt/trade-capture/python.new
mv -T /opt/trade-capture/python.new /opt/trade-capture/python
ln -sfn "$RELEASE_DIR" /opt/trade-capture/current.new
mv -T /opt/trade-capture/current.new /opt/trade-capture/current

# The backup timer and the alert template are installed, never enabled here: they
# do nothing until the owner adds /etc/trade-capture/backup-par-url or
# alert-ping-url (README section 7); this script never writes those files.
for unit in trade-capture-r0.service trade-capture-u2.service \
            trade-capture-health.service trade-capture-health.timer \
            trade-capture-backup.service trade-capture-backup.timer \
            trade-capture-alert@.service; do
    install -m 0644 "$RELEASE_DIR/deploy/oci/systemd/$unit" "/etc/systemd/system/$unit"
done

# Clock: the units order after time-sync.target; make that target mean "synced".
if systemctl cat chrony-wait.service >/dev/null 2>&1; then
    systemctl enable chrony-wait.service >/dev/null
    if ! grep -qs '169\.254\.169\.254' /etc/chrony.conf /etc/chrony/chrony.conf /etc/chrony/sources.d/*; then
        if [ -d /etc/chrony/sources.d ]; then
            install -m 0644 "$RELEASE_DIR/deploy/oci/chrony-oci.sources" /etc/chrony/sources.d/oci.sources
            echo "note: added the OCI NTP source; it applies after: chronyc reload sources"
        else
            echo "note: chrony has no OCI NTP source (169.254.169.254); add it by hand" >&2
        fi
    fi
elif systemctl cat systemd-time-wait-sync.service >/dev/null 2>&1; then
    systemctl enable systemd-time-wait-sync.service >/dev/null
else
    echo "note: no chrony-wait/systemd-time-wait-sync; recorders may start before the clock syncs" >&2
fi

# SELinux (Oracle Linux, enforcing): default labels for the new paths.
if command -v selinuxenabled >/dev/null 2>&1 && selinuxenabled; then
    restorecon -RF /opt/trade-capture /srv/trade-capture /etc/trade-capture \
        /etc/systemd/system/trade-capture-*
fi
[ "$(stat -fc %T /sys/fs/cgroup)" = cgroup2fs ] \
    || echo "note: not cgroup v2; CPUWeight= falls back to CPUShares semantics" >&2
systemctl daemon-reload

echo "installed release $RELEASE_DIR -> /opt/trade-capture/current (python $PYTHON)"
echo "recorders NOT started/restarted. Next (README.md): review /etc/trade-capture/capture.env, then"
echo "  systemctl enable --now trade-capture-r0.service trade-capture-u2.service trade-capture-health.timer"
