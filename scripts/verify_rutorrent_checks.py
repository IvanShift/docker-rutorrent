#!/usr/bin/env python3
"""Require successful GitHub Actions checks for exactly the pinned ruTorrent SHA.

With --repo, fetch every check-runs page using GITHUB_TOKEN and bounded retries.
Without --repo, read a complete API response from stdin for offline verification.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

# New source commits declare two stable aggregate checks in the fork workflows.
REQUIRED_CHECKS = {"rutorrent-image-tests-v1", "rutorrent-image-lint-v1"}
# This already-published source predates aggregates. Do not grant this fallback
# to arbitrary commits: every later pin must have the versioned producer gates.
LEGACY_PIN_SHA = "d108dddd286cd5eca9173b91b2cdee64adb27e6a"
LEGACY_REQUIRED_CHECKS = {
    "php (7.4)",
    "php (8.1)",
    "jest",
    "php-syntax (7.4)",
    "php-syntax (8.1)",
    "phpstan",
}
GITHUB_ACTIONS_APP_ID = 15368


def validate_page(response):
    if not isinstance(response, dict):
        raise ValueError("Invalid check-runs response: expected an object")
    runs, count = response.get("check_runs"), response.get("total_count")
    if not isinstance(runs, list) or type(count) is not int or count < 0:
        raise ValueError(
            "Invalid check-runs response: missing check_runs or total_count"
        )
    return runs, count


def verify(response, sha):
    required = LEGACY_REQUIRED_CHECKS if sha == LEGACY_PIN_SHA else REQUIRED_CHECKS
    runs, count = validate_page(response)
    if count != len(runs):
        raise ValueError(
            f"Incomplete check-runs response: expected {count}, received {len(runs)}"
        )
    if not runs:
        raise ValueError(f"No check runs for ruTorrent {sha}")
    seen, unsuccessful, ids = set(), set(), set()
    for run in runs:
        if not isinstance(run, dict) or not isinstance(run.get("name"), str):
            raise ValueError("Invalid check-runs response: malformed check run")
        run_id = run.get("id")
        if type(run_id) is not int or run_id <= 0 or run_id in ids:
            raise ValueError("Invalid check-runs response: missing or duplicate run ID")
        ids.add(run_id)
        name = run["name"]
        if name not in required:
            continue
        seen.add(name)
        app = run.get("app")
        if (
            not isinstance(app, dict)
            or app.get("id") != GITHUB_ACTIONS_APP_ID
            or app.get("slug") != "github-actions"
        ):
            raise ValueError(
                f"Required check was not produced by GitHub Actions: {name}"
            )
        if (
            run.get("head_sha") != sha
            or run.get("status") != "completed"
            or run.get("conclusion") != "success"
        ):
            unsuccessful.add(name)
    missing = required - seen
    if missing:
        raise ValueError(
            "Missing required ruTorrent checks: " + ", ".join(sorted(missing))
        )
    if unsuccessful:
        raise ValueError(
            "Required ruTorrent checks did not succeed: "
            + ", ".join(sorted(unsuccessful))
        )


def request_json(url, token, opener=urllib.request.urlopen, sleep=time.sleep):
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
        "User-Agent": "docker-rutorrent-check-gate",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(4):
        try:
            with opener(
                urllib.request.Request(url, headers=headers), timeout=30
            ) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            error.close()
            if error.code not in {429, 500, 502, 503, 504} or attempt == 3:
                raise ValueError(
                    f"GitHub check-runs request failed: HTTP {error.code}"
                ) from error
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt == 3:
                reason = (
                    error.reason
                    if isinstance(error, urllib.error.URLError)
                    else "timeout"
                )
                raise ValueError(
                    f"GitHub check-runs request failed: {reason}"
                ) from error
        sleep(2**attempt)
    raise AssertionError("unreachable")


def fetch(repo, sha, token, requester=request_json):
    collected, expected, ids = [], None, set()
    # Reconstruct API page URLs; never forward the token to a Link URL.
    for page in range(1, 101):
        url = f"https://api.github.com/repos/{repo}/commits/{sha}/check-runs?filter=latest&per_page=100&app_id={GITHUB_ACTIONS_APP_ID}&page={page}"
        runs, count = validate_page(requester(url, token))
        if expected is not None and count != expected:
            raise ValueError("Check-runs changed during pagination; retry the workflow")
        expected = count
        if len(runs) > 100 or len(collected) + len(runs) > count:
            raise ValueError("Invalid check-runs pagination count")
        for run in runs:
            run_id = run.get("id") if isinstance(run, dict) else None
            if type(run_id) is not int or run_id <= 0 or run_id in ids:
                raise ValueError(
                    "Invalid check-runs pagination: duplicate or missing run ID"
                )
            ids.add(run_id)
        collected.extend(runs)
        if len(collected) == count:
            return {"total_count": count, "check_runs": collected}
        if not runs:
            raise ValueError(
                f"Incomplete check-runs response: expected {count}, "
                f"received {len(collected)}"
            )
    raise ValueError("Check-runs response exceeded 100 pages")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sha")
    parser.add_argument(
        "--repo", help="GitHub owner/repository from the Dockerfile source default"
    )
    args = parser.parse_args()
    if re.fullmatch(r"[0-9a-f]{40}", args.sha) is None:
        parser.exit(1, "Expected a full 40-hex ruTorrent SHA\n")
    if (
        args.repo
        and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo) is None
    ):
        parser.exit(1, "Expected GitHub owner/repository\n")
    try:
        response = (
            fetch(args.repo, args.sha, os.environ.get("GITHUB_TOKEN"))
            if args.repo
            else json.load(sys.stdin)
        )
        verify(response, args.sha)
    except (ValueError, UnicodeDecodeError) as error:
        print(
            f"Invalid check-runs JSON: {error}"
            if isinstance(error, json.JSONDecodeError)
            else str(error),
            file=sys.stderr,
        )
        return 1
    summary = (
        "six required checks"
        if args.sha == LEGACY_PIN_SHA
        else "two release aggregates"
    )
    print(f"ruTorrent {args.sha}: {summary} succeeded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
