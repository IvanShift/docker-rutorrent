#!/usr/bin/env python3
"""Fail closed unless the required ruTorrent check runs passed for one SHA.

Read the GitHub check-runs API response from stdin. The workflow fetches it
without a token because the ruTorrent repository is public.
"""

import json
import re
import sys


REQUIRED_CHECKS = {
    "php (7.4)",
    "php (8.1)",
    "jest",
    "php-syntax (7.4)",
    "php-syntax (8.1)",
    "phpstan",
}


def fail(message):
    print(message, file=sys.stderr)
    return 1


def main():
    if len(sys.argv) != 2 or re.fullmatch(r"[0-9a-f]{40}", sys.argv[1]) is None:
        return fail("Usage: verify_rutorrent_checks.py FULL_40_HEX_SHA < check-runs.json")
    sha = sys.argv[1]

    try:
        response = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        return fail(f"Invalid check-runs JSON: {error}")

    if not isinstance(response, dict):
        return fail("Invalid check-runs response: expected an object")
    runs = response.get("check_runs")
    count = response.get("total_count")
    if not isinstance(runs, list) or type(count) is not int or count < 0:
        return fail("Invalid check-runs response: missing check_runs or total_count")
    if count != len(runs):
        return fail(f"Incomplete check-runs response: expected {count}, received {len(runs)}")
    if not runs:
        return fail(f"No check runs for ruTorrent {sha}")

    seen = set()
    unsuccessful = set()
    for run in runs:
        if not isinstance(run, dict) or not isinstance(run.get("name"), str):
            return fail("Invalid check-runs response: malformed check run")
        name = run["name"]
        if name not in REQUIRED_CHECKS:
            continue
        seen.add(name)
        if (
            run.get("head_sha") != sha
            or run.get("status") != "completed"
            or run.get("conclusion") != "success"
        ):
            unsuccessful.add(name)

    missing = REQUIRED_CHECKS - seen
    if missing:
        return fail("Missing required ruTorrent checks: " + ", ".join(sorted(missing)))
    if unsuccessful:
        return fail("Required ruTorrent checks did not succeed: " + ", ".join(sorted(unsuccessful)))

    print(f"ruTorrent {sha}: six required checks succeeded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
