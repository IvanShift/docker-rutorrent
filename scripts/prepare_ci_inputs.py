#!/usr/bin/env python3
"""Read source defaults and one timestamp for the CI run without overriding pins."""

import argparse
import datetime
import os
import re
import shlex
from pathlib import Path


def dockerfile_defaults(text):
    """Parse global ARG defaults, retaining '=' and expanding preceding ARGs."""
    defaults = {}
    for line in text.splitlines():
        if re.match(r"\s*FROM\s", line, re.I):
            break
        match = re.match(r"\s*ARG\s+(.+?)\s*$", line, re.I)
        if not match:
            continue
        words = shlex.split(match[1], comments=True)
        if len(words) != 1:
            raise ValueError(f"Unsupported ARG default: {line}")
        name, separator, value = words[0].partition("=")
        if not separator:
            continue
        if name in defaults:
            raise ValueError(f"Duplicate ARG default: {name}")

        def expand(match):
            key = match[1] or match[2]
            if key not in defaults:
                raise ValueError(f"Undefined ARG reference: {key}")
            return defaults[key]

        value = re.sub(
            r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)", expand, value
        )
        defaults[name] = value
    return defaults


def prepare(text, now):
    defaults = dockerfile_defaults(text)
    label = defaults.get("RTORRENT_BRANCH", "")
    if re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", label) is None:
        raise ValueError("Invalid RTORRENT_BRANCH default")
    if re.fullmatch(r"[0-9a-f]{40}", defaults.get("RTORRENT_VERSION", "")) is None:
        raise ValueError("RTORRENT_VERSION must pin a full commit SHA")
    repo = defaults.get("RUTORRENT_REPO", "")
    match = re.fullmatch(
        r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?", repo
    )
    if not match:
        raise ValueError(
            "The ruTorrent check gate requires a GitHub HTTPS repository URL"
        )
    ref = defaults.get("RUTORRENT_REF", "")
    sha = defaults.get("RUTORRENT_COMMIT_SHA", "") or ref
    if re.fullmatch(r"[0-9a-f]{40}", sha) is None:
        raise ValueError(
            "RUTORRENT_REF or RUTORRENT_COMMIT_SHA must pin a full commit SHA"
        )
    if re.fullmatch(r"[0-9a-f]{40}", ref) and ref != sha:
        raise ValueError("RUTORRENT_REF disagrees with RUTORRENT_COMMIT_SHA")
    return {
        "rtorrent_label": label[1:],
        "rutorrent_repo_slug": match[1],
        "rutorrent_commit": sha,
        "build_date": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tag_date": now.strftime("%Y.%m.%d"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dockerfile", type=Path)
    parser.add_argument("--output", type=Path, default=os.environ.get("GITHUB_OUTPUT"))
    args = parser.parse_args()
    try:
        values = prepare(
            args.dockerfile.read_text(encoding="utf-8"),
            datetime.datetime.now(datetime.timezone.utc),
        )
    except ValueError as error:
        parser.exit(1, f"Invalid immutable CI inputs: {error}\n")
    result = "".join(f"{key}={value}\n" for key, value in values.items())
    if args.output:
        with args.output.open("a", encoding="utf-8") as output:
            output.write(result)
    else:
        print(result, end="")


if __name__ == "__main__":
    main()
