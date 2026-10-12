#!/usr/bin/env bash
# Read-only health of the R0 and U2 recorders on the OCI capture VM.
#
#   sudo -u trade-capture /opt/trade-capture/current/deploy/oci/trade-capture-health.sh
#
# Prints unit state, the recorder lock on each root, the age of the newest
# finalized manifest, disk runway above the floor (from the bytes actually
# written for the previous UTC day) and clock-sync state. Exit 0 when every
# check passes, 1 otherwise. Writes nothing; evidence comes from manifests.
set -u

ENV_FILE=/etc/trade-capture/capture.env
APP=/opt/trade-capture/current
CLI="$APP/scripts/capture_bybit_public.py"
ARCHIVE_MOUNT=/srv/trade-capture
# Hourly segments finalize a manifest per symbol every hour; allow one missed
# hand-off plus slack before calling a root stale (an operations alert, not evidence).
MAX_MANIFEST_AGE_SECONDS=4500

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
    lock=$(/usr/bin/python3 -I -B "$CLI" --root "$root" status --recent 1 | head -n 1)
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
if command -v chronyc >/dev/null 2>&1; then
    chronyc -n tracking 2>/dev/null | grep -E '^(Reference ID|System time|Last offset|Leap status)' \
        | sed 's/^/chrony         /'
fi

if [ "$fail" -eq 0 ]; then echo "verdict        OK"; else echo "verdict        ATTENTION"; fi
exit "$fail"
