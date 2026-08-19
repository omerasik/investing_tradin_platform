"""Run mypy over the complete package and reject any increase in known type debt."""

from __future__ import annotations

import re
import subprocess
import sys
from collections import Counter

BASELINE = {
    "agent_research.py": 13,
    "api.py": 4,
    "corporate_actions.py": 6,
    "cross_engine.py": 3,
    "event_backtest.py": 4,
    "feature_platform.py": 1,
    "fundamentals.py": 1,
    "investment_providers.py": 2,
    "investments.py": 7,
    "macro_data.py": 1,
    "paper_runtime.py": 4,
    "policy_registry.py": 4,
    "postgres_runtime.py": 5,
    "pretrade_assessment.py": 3,
    "quant_validation.py": 46,
    "research.py": 9,
    "return_history.py": 1,
    "strategy_scorecard.py": 3,
}
ERROR = re.compile(r"^src[\\/]trade_platform[\\/]([^:]+):\d+: error:")


def main() -> int:
    completed = subprocess.run(
        [sys.executable, "-m", "mypy", "--follow-imports=skip", "src/trade_platform"],
        check=False,
        capture_output=True,
        text=True,
    )
    output = completed.stdout + completed.stderr
    observed = Counter(
        match.group(1)
        for line in output.splitlines()
        if (match := ERROR.match(line)) is not None
    )
    regressions = {
        name: count
        for name, count in sorted(observed.items())
        if count > BASELINE.get(name, 0)
    }
    total = sum(observed.values())
    if regressions or total > sum(BASELINE.values()):
        print(output)
        print(f"mypy baseline regression: total={total}, files={regressions}")
        return 1
    print(f"mypy complete-package baseline: {total}/{sum(BASELINE.values())} errors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
