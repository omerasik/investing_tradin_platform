"""Module 1B disposable PostgreSQL demo acceptance runner.

This script executes the Module 1B acceptance test suite against a local or CI
disposable PostgreSQL instance.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def main() -> int:
    dsn = os.environ.get("POSTGRES_TEST_DSN")
    if not dsn:
        print("FAIL: POSTGRES_TEST_DSN environment variable is not configured.", file=sys.stderr)
        return 1

    parsed = urlparse(dsn)
    if parsed.hostname not in LOCAL_HOSTS:
        print(f"FAIL: Module 1B acceptance requires a local or CI disposable PostgreSQL DSN (found host: {parsed.hostname}).", file=sys.stderr)
        return 1

    suite = unittest.defaultTestLoader.loadTestsFromName("tests.test_module1b_demo_acceptance")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    if not result.wasSuccessful():
        print("\nModule 1B Demo Acceptance: FAILED", file=sys.stderr)
        return 1

    print("\n==================================================")
    print("Module 1B Demo Acceptance Summary:")
    print("  Module 1B demo seed:       PASS")
    print("  Idempotent replay:         PASS")
    print("  Cross-domain coherence:    PASS")
    print("  PIT:                       PASS")
    print("  No UUID config:            PASS")
    print("  Real provider activation:  NO")
    print("  Live trading:              DISABLED")
    print("==================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
