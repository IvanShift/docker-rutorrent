#!/usr/bin/env python3
"""Plan old run-specific staging tag cleanup offline; never contact or write a registry."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re

LEGACY_CANDIDATE = re.compile(r"candidate-[1-9][0-9]*-[1-9][0-9]*-(?:default|filebot)-(?:amd64|arm64)\Z")


def cleanup_plan(snapshot, *, now, older_than_days=14):
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("results"), list):
        raise ValueError("Expected Docker Hub tag listing")
    rows = snapshot["results"]
    if snapshot.get("next") is not None or type(snapshot.get("count")) is not int or snapshot["count"] != len(rows):
        raise ValueError("Incomplete tag snapshot; collect every page before cleanup planning")
    if now.tzinfo is None or type(older_than_days) is not int or not 1 <= older_than_days <= 365:
        raise ValueError("Expected timezone-aware time and retention from 1 to 365 days")
    names = set()
    delete = []
    skipped = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            raise ValueError("Invalid Docker Hub tag row")
        name = row["name"]
        if name in names:
            raise ValueError("Duplicate tag in snapshot")
        names.add(name)
        if not LEGACY_CANDIDATE.fullmatch(name):
            continue
        # The live Hub capture supplies last_updated. Unknown age is retained.
        try:
            updated = datetime.fromisoformat(row["last_updated"].replace("Z", "+00:00"))
            if updated.tzinfo is None:
                raise ValueError("Timestamp lacks timezone")
        except (KeyError, AttributeError, TypeError, ValueError):
            skipped.append({"name": name, "reason": "unproven-age"})
            continue
        if updated <= now - timedelta(days=older_than_days):
            delete.append(name)
    return {"mode": "dry-run-only", "older_than_days": older_than_days,
            "candidate_tags": sorted(delete), "skipped": skipped,
            "retained_count": len(rows) - len(delete)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--older-than-days", type=int, default=14)
    args = parser.parse_args()
    try:
        result = cleanup_plan(json.loads(args.snapshot.read_text()),
                              now=datetime.now(timezone.utc), older_than_days=args.older_than_days)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Candidate cleanup planning refused: {error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
