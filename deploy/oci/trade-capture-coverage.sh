#!/usr/bin/env bash
# Hourly market-data coverage alert for the OCI recorders (README section 7d).
#
#   sudo systemctl start trade-capture-coverage.service && journalctl -u trade-capture-coverage -n 20
#
# For the last whole UTC hour, per symbol, the seconds no COMPLETE partition's
# proven window covers (sessions merged: a 3 s reconnect is a 3 s hole, a
# missing partition a 3600 s one), against COVERAGE_MAX_UNCOVERED_SECONDS, an
# operator alert threshold the unit sets. With a healthchecks.io-compatible URL
# in /etc/trade-capture/coverage-ping-url -- a check of its own, never the
# service-health one -- the report goes out as a success or /fail ping.
#
# It is neither service health (trade-capture-health) nor acceptance
# (universe-acceptance decides COMPLETE hours, run-based, with its own bound).
# Exit 0 = OK, 1 = ATTENTION (a symbol-hour above the threshold), 2 = the check
# itself could not run. Writes nothing.
set -u

ENV_FILE=/etc/trade-capture/capture.env
APP=/opt/trade-capture/current
PYTHON=/opt/trade-capture/python
CLI="$APP/scripts/capture_bybit_public.py"
PING_URL_FILE=/etc/trade-capture/coverage-ping-url
: "${COVERAGE_MAX_UNCOVERED_SECONDS:?set by trade-capture-coverage.service: the operator alert threshold}"

# shellcheck source=/dev/null
. "$ENV_FILE"
r0=$("$PYTHON" -I -B "$CLI" --root "$R0_ROOT" coverage-hours --scope production --hours 1 \
    --max-uncovered-seconds "$COVERAGE_MAX_UNCOVERED_SECONDS" 2>&1)
r0_status=$?
u2=$("$PYTHON" -I -B "$CLI" --root "$U2_ROOT" coverage-hours --scope universe --hours 1 \
    --max-uncovered-seconds "$COVERAGE_MAX_UNCOVERED_SECONDS" 2>&1)
u2_status=$?

if [ "$r0_status" -eq 0 ] && [ "$u2_status" -eq 0 ]; then
    status=0
elif [ "$r0_status" -le 1 ] && [ "$u2_status" -le 1 ]; then
    status=1
else
    status=2
fi
report=$(printf '%s\n%s\n' "$r0" "$u2")
printf '%s\n' "$report"
if [ -r "$PING_URL_FILE" ]; then
    if [ "$status" -eq 0 ]; then outcome=success; else outcome=fail; fi
    printf '%s\n' "$report" | "$PYTHON" -I -B "$APP/deploy/oci/capture_offsite.py" \
        ping "$outcome" --url-file "$PING_URL_FILE" --context "trade-capture-coverage $outcome" \
        | sed 's/^/alert_ping     /'
fi
exit "$status"
