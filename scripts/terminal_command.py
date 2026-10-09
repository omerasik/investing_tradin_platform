"""Submit a research terminal command with your own operator credential.

    set TRADE_PLATFORM_OPERATOR_TOKEN=<your token>
    python scripts/terminal_command.py --api http://127.0.0.1:8000 --kind WATCHLIST_RECORD --inputs watch.json
    python scripts/terminal_command.py --api ... --kind HOLDOUT_OPEN --inputs open.json --key owner-open-1
    python scripts/terminal_command.py --api ... --list

Owner decisions (WATCHLIST_RECORD, ACCOUNT_REGISTER, ACCOUNT_POLICY_RECORD,
PREREGISTRATION_RECORD, HOLDOUT_OPEN) go to the owner endpoint, which needs the
REVIEW_RISK permission; every ``approved_by`` / ``authorized_by`` / ``opened_by``
must equal the subject your token authenticates as. Research kinds go to the
research endpoint. The token is read from the environment, never from argv.
A refused command prints the exact blocking reasons (``BLOCKED_OWNER_DECISION_OR_<n>:...``).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

OWNER_KINDS = {"WATCHLIST_RECORD", "ACCOUNT_REGISTER", "ACCOUNT_POLICY_RECORD", "PREREGISTRATION_RECORD",
               "HOLDOUT_OPEN"}


def _call(url: str, token: str, body: dict[str, object] | None = None) -> tuple[int, object]:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method="GET" if body is None else "POST",
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", required=True, help="protected API base URL, e.g. http://127.0.0.1:8000")
    parser.add_argument("--kind")
    parser.add_argument("--inputs", type=Path, help="JSON file with every input field explicit")
    parser.add_argument("--key", help="idempotency key (default: a new random key)")
    parser.add_argument("--list", action="store_true", help="print recent commands and their outcomes")
    args = parser.parse_args()
    token = os.environ.get("TRADE_PLATFORM_OPERATOR_TOKEN")
    if not token:
        raise SystemExit("TRADE_PLATFORM_OPERATOR_TOKEN is not set")
    base = args.api.rstrip("/") + "/operator-dashboard/research-terminal"
    if args.list:
        print(json.dumps(_call(f"{base}/commands?limit=50", token)[1], indent=1))
        return
    if not args.kind or not args.inputs:
        raise SystemExit("--kind and --inputs are required")
    inputs = json.loads(args.inputs.read_text(encoding="utf-8"))
    path = "owner-commands" if args.kind in OWNER_KINDS else "commands"
    status, body = _call(f"{base}/{path}", token, {"kind": args.kind, "inputs": inputs,
                                                   "idempotency_key": args.key or f"cli-{uuid.uuid4()}"})
    print(json.dumps({"http_status": status, "response": body}, indent=1))
    sys.exit(0 if 200 <= status < 300 else 1)


if __name__ == "__main__":
    main()
