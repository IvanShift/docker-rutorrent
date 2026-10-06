#!/usr/bin/env python3
"""Download one current GeoLite2 Country release, requiring its API SHA256."""

import argparse
import hashlib
import http.client
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from verify_rutorrent_checks import request_json

MAX_SIZE = 20 * 1024 * 1024
RELEASE_API = "https://api.github.com/repos/P3TERX/GeoLite.mmdb/releases/latest"


def release_asset(release):
    if (
        not isinstance(release, dict)
        or release.get("draft") is not False
        or release.get("prerelease") is not False
    ):
        raise ValueError("Expected a published stable GeoLite2 release")
    version = release.get("tag_name", "")
    if re.fullmatch(r"[0-9]{4}\.[0-9]{2}\.[0-9]{2}", version) is None:
        raise ValueError("Invalid GeoLite2 release tag")
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise ValueError("Missing GeoLite2 release assets")
    matches = [
        asset
        for asset in assets
        if isinstance(asset, dict) and asset.get("name") == "GeoLite2-Country.mmdb"
    ]
    if len(matches) != 1:
        raise ValueError("Expected one GeoLite2 Country release asset")
    asset = matches[0]
    digest = asset.get("digest", "")
    if (
        not isinstance(digest, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
    ):
        raise ValueError("GeoLite2 Country asset must publish a SHA256 digest")
    size = asset.get("size")
    if type(size) is not int or not 0 < size <= MAX_SIZE:
        raise ValueError(
            "GeoLite2 Country asset size is outside the bounded download limit"
        )
    url = f"https://github.com/P3TERX/GeoLite.mmdb/releases/download/{version}/GeoLite2-Country.mmdb"
    if asset.get("browser_download_url") != url:
        raise ValueError("Unexpected GeoLite2 Country asset URL")
    return version, digest.removeprefix("sha256:"), size, url


def download_sha256(
    url, expected_size, opener=urllib.request.urlopen, sleep=time.sleep
):
    for attempt in range(4):
        try:
            # Release assets use a separate request without the API token.
            with opener(
                urllib.request.Request(
                    url, headers={"User-Agent": "docker-rutorrent-geolite-refresh"}
                ),
                timeout=30,
            ) as response:
                digest, size = hashlib.sha256(), 0
                while data := response.read(1024 * 1024):
                    size += len(data)
                    if size > expected_size:
                        raise ValueError(
                            "GeoLite2 Country download exceeded the declared asset size"
                        )
                    digest.update(data)
                if size != expected_size:
                    raise ValueError("GeoLite2 Country download was truncated")
                return digest.hexdigest()
        except urllib.error.HTTPError as error:
            error.close()
            if error.code not in {429, 500, 502, 503, 504} or attempt == 3:
                raise ValueError(
                    f"GeoLite2 download failed: HTTP {error.code}"
                ) from error
        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            http.client.HTTPException,
        ) as error:
            if attempt == 3:
                raise ValueError(
                    "GeoLite2 download failed after four attempts"
                ) from error
        sleep(2**attempt)
    raise AssertionError("unreachable")


def resolve(token, requester=request_json, downloader=download_sha256):
    version, digest, size, url = release_asset(requester(RELEASE_API, token))
    if downloader(url, size) != digest:
        raise ValueError(
            "Downloaded GeoLite2 Country SHA256 differs from release metadata"
        )
    return {"geolite_version": version, "geolite_sha256": digest}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=os.environ.get("GITHUB_OUTPUT"))
    args = parser.parse_args()
    try:
        values = resolve(os.environ.get("GITHUB_TOKEN"))
    except ValueError as error:
        parser.exit(1, f"GeoLite2 refresh refused: {error}\n")
    text = "".join(f"{key}={value}\n" for key, value in values.items())
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as output:
            output.write(
                f"GeoLite2 Country release: `{values['geolite_version']}`\n\n"
                f"SHA256: `{values['geolite_sha256']}`\n"
            )
    if args.output:
        with args.output.open("a", encoding="utf-8") as output:
            output.write(text)
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
