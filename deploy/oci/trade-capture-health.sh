#!/usr/bin/env bash
# Read-only health of the R0 and U2 recorders on the OCI capture VM.
#
#   sudo systemctl start trade-capture-health.service && journalctl -u trade-capture-health -n 40
#
# (Run it through the unit: it carries the recorders' CPUWeight, so the
# scheduling probe describes their class, not an SSH session's.)
# Prints unit state, the recorder lock on each root, the age of the newest
# finalized manifest, disk runway above the floor (from the bytes actually
# written for the previous UTC day), clock-sync state and capture_host_probe.py
# (scheduling lateness, the recorders' own clock samples, memory headroom).
# Exit 0 when every check passes, 1 otherwise. Writes nothing.
#
# This is SERVICE health only. A running recorder is not proven coverage: the
# U2 hour facts below show, per symbol, whether one proven run reached each
# recent hour's end and every stop inside it, with no bound and no verdict.
# COMPLETE hours are decided only by 'universe-acceptance' with the operator's
# explicit hand-off bound; nothing here affects the exit code.
#
# chronyc is deliberately not run: under the unit's NoNewPrivileges SELinux
# refuses its chronyc_t domain transition (nnp_transition AVC). Sync state comes
# from timedatectl, the offset from the recorders' own RTT-bounded samples; run
# 'chronyc tracking' from an SSH session for chrony's view.
set -u

ENV_FILE=/etc/trade-capture/capture.env
APP=/opt/trade-capture/current
# Interpreter link chosen by install.sh (Python >= 3.11; the system python3 is untouched).
PYTHON=/opt/trade-capture/python
CLI="$APP/scripts/capture_bybit_public.py"
ARCHIVE_MOUNT=/srv/trade-capture
# Hourly segments finalize a manifest per symbol every hour; allow one missed
# hand-off plus slack before calling a root stale (an operations alert, not evidence).
MAX_MANIFEST_AGE_SECONDS=4500

# Dead-man's switch (README section 7c): with a healthchecks.io-compatible URL in
# this file, the snapshot is posted as a success ping (exit 0) or a /fail ping
# (exit 1); silence -- VM, timer or network down -- alerts by itself. Absent
# file: no ping, nothing else changes. A failed ping never changes the exit code.
PING_URL_FILE=/etc/trade-capture/alert-ping-url
if [ -z "${TRADE_CAPTURE_HEALTH_INNER:-}" ] && [ -r "$PING_URL_FILE" ]; then
    report=$(TRADE_CAPTURE_HEALTH_INNER=1 /usr/bin/bash "$0" 2>&1)
    status=$?
    printf '%s\n' "$report"
    if [ "$status" -eq 0 ]; then outcome=success; else outcome=fail; fi
    printf '%s\n' "$report" | "$PYTHON" -I -B "$APP/deploy/oci/capture_offsite.py" \
        ping "$outcome" --url-file "$PING_URL_FILE" --context "trade-capture-health $outcome" \
        | sed 's/^/alert_ping     /'
    exit "$status"
fi

# shellcheck source=/dev/null
. "$ENV_FILE"
fail=0
now=$(date -u +%s)
echo "health_at      $(date -u +%Y-%m-%dT%H:%M:%SZ) release=$(readlink -f "$APP")"

for unit in trade-capture-r0 trade-capture-u2; do
    active=$(systemctl show -p ActiveState --value "$unit.service")
    restarts=$(systemctl show -p NRestarts --value "$unit.service")
    last_exit=$(systemctl show -p ExecMainStatus --value "$unit.service")
    echo "unit           $unit active=$active restarts=$restarts last_exit=$last_exit"
    [ "$active" = active ] || fail=1
done

for pair in "r0:$R0_ROOT" "u2:$U2_ROOT"; do
    label=${pair%%:*}
    root=${pair#*:}
    lock=$("$PYTHON" -I -B "$CLI" --root "$root" status --recent 1 | head -n 1)
    echo "lock_$label        ${lock#* }"
    case "$lock" in *RUNNING\ pid=*) ;; *) fail=1 ;; esac
    newest=$(find "$root" -name MANIFEST.json -printf '%T@\n' 2>/dev/null | sort -n | tail -n 1)
    if [ -z "$newest" ]; then
        echo "manifest_$label    none under $root"
        fail=1
    else
        age=$(( now - ${newest%.*} ))
        echo "manifest_$label    newest ${age} s ago"
        [ "$age" -le "$MAX_MANIFEST_AGE_SECONDS" ] || fail=1
    fi
done

avail=$(df -B1 --output=avail "$ARCHIVE_MOUNT" | tail -n 1 | tr -d ' ')
floor=$(awk -v g="$MIN_FREE_GB" 'BEGIN { printf "%.0f", g * 1024 ^ 3 }')
yesterday=$(date -u -d yesterday +%F)
per_day=0
for root in "$R0_ROOT" "$U2_ROOT"; do
    bytes=$(find "$root" -type d -name "date=$yesterday" -prune -print0 2>/dev/null \
        | du -sbc --files0-from=- 2>/dev/null | tail -n 1 | cut -f 1)
    per_day=$(( per_day + ${bytes:-0} ))
done
awk -v a="$avail" -v f="$floor" -v d="$per_day" -v y="$yesterday" 'BEGIN {
    gib = 1024 ^ 3
    printf "disk_free      %.1f GiB (floor %.1f GiB, usable %.1f GiB)\n", a / gib, f / gib, (a - f) / gib
    if (d > 0) printf "runway         %.0f days at %.2f GiB written on %s (both roots)\n", (a - f) / d, d / gib, y
    else printf "runway         unknown: nothing written on %s\n", y
}'
[ "$avail" -gt "$floor" ] || fail=1

synced=$(timedatectl show -p NTPSynchronized --value 2>/dev/null)
echo "clock_synced   ${synced:-unknown}"
[ "$synced" = yes ] || fail=1

"$PYTHON" -I -B "$APP/deploy/oci/capture_host_probe.py" "$R0_ROOT" "$U2_ROOT" || fail=1

if [ "$fail" -eq 0 ]; then echo "service        OK"; else echo "service        ATTENTION"; fi
"$PYTHON" -I -B "$CLI" --root "$U2_ROOT" universe-hours --hours 2 | sed 's/^/u2_hours       /'
echo "evidence       NOT JUDGED HERE: service OK is not COMPLETE coverage (universe-acceptance decides)"
exit "$fail"
