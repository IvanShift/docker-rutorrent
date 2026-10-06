#!/usr/bin/env python3
"""Refuse promotion of mutable image tags from a superseded master commit."""

import argparse
import os
import re

from verify_rutorrent_checks import request_json


def verify_revision(expected, response):
    if re.fullmatch(r"[0-9a-f]{40}", expected) is None:
        raise ValueError("Expected a full publication revision")
    if not isinstance(response, dict) or response.get("ref") != "refs/heads/master":
        raise ValueError("Cannot establish the current master revision")
    object_ = response.get("object")
    if not isinstance(object_, dict) or object_.get("type") != "commit":
        raise ValueError("Current master must refer to a commit")
    if object_.get("sha") != expected:
        raise ValueError("Publication refused: this candidate is no longer master")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("revision")
    parser.add_argument("--repo", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo) is None:
        parser.exit(1, "Expected a GitHub owner/repository\n")
    try:
        response = request_json(
            f"https://api.github.com/repos/{args.repo}/git/ref/heads/master",
            os.environ.get("GITHUB_TOKEN"),
        )
        verify_revision(args.revision, response)
    except ValueError as error:
        parser.exit(1, f"{error}\n")
    print(f"Publication revision is current master: {args.revision}")


if __name__ == "__main__":
    main()
