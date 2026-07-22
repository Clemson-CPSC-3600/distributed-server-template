#!/usr/bin/env python3
"""GitHub Classroom per-bundle grader.

The Classroom workflow runs ``run_tests.py`` once, then invokes this program
for bundles 1, 2, and 3.  Reading the runner's status cache keeps Classroom's
score aligned with local and Gradescope specification grading.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from run_tests import BundleTestRunner  # noqa: E402


TRUST_CACHE = os.environ.get("GITHUB_ACTIONS") == "true"


def load_status():
    cache_path = ROOT / BundleTestRunner.STATUS_CACHE_RELATIVE
    if TRUST_CACHE and cache_path.exists():
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            data["bundles"] = {int(key): value for key, value in data["bundles"].items()}
            return data
        except (OSError, json.JSONDecodeError, KeyError, ValueError):
            pass

    runner = BundleTestRunner()
    _exit_code, bundles_data = runner.run_tests_standard()
    status = runner.compute_bundle_status(bundles_data)
    runner.write_status_cache(status)
    return status


def main(bundle_number):
    status = load_status()
    bundles = status["bundles"]

    print(f"Checking Bundle {bundle_number}")
    print("-" * 40)
    info = bundles.get(bundle_number, {})
    print(
        f"Bundle {bundle_number}: "
        f"{info.get('passed', 0)}/{info.get('total', 0)} tests passed"
    )
    print(f"Overall Grade: {status.get('grade', 'Not Passing')}")
    print("-" * 40)

    for required in range(1, bundle_number + 1):
        if not bundles.get(required, {}).get("complete", False):
            print(f"FAIL: Bundle {bundle_number} requires completed Bundle {required}")
            return 1

    print(f"PASS: Bundle {bundle_number}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python github_grader.py <bundle_number>")
        sys.exit(1)
    try:
        requested_bundle = int(sys.argv[1])
    except ValueError:
        print("Error: bundle number must be an integer")
        sys.exit(1)
    if requested_bundle not in (1, 2, 3):
        print("Error: bundle must be 1, 2, or 3")
        sys.exit(1)
    sys.exit(main(requested_bundle))
