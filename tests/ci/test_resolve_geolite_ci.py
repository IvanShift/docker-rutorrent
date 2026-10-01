"""Current release refresh must download exactly the checksummed Country database."""

import copy
import hashlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from resolve_geolite_ci import (  # noqa: E402 - import repository helper after path setup
    download_sha256,
    release_asset,
    resolve,
)

DATA = b"fixture mmdb data"
DIGEST = hashlib.sha256(DATA).hexdigest()
URL = "https://github.com/P3TERX/GeoLite.mmdb/releases/download/2026.10.01/GeoLite2-Country.mmdb"
RELEASE = {
    "draft": False,
    "prerelease": False,
    "tag_name": "2026.10.01",
    "assets": [
        {
            "name": "GeoLite2-Country.mmdb",
            "digest": f"sha256:{DIGEST}",
            "size": len(DATA),
            "browser_download_url": URL,
        }
    ],
}


class GeoLiteRefreshTests(unittest.TestCase):
    def test_one_version_and_verified_checksum_are_returned(self):
        requester, downloader = (
            mock.Mock(return_value=RELEASE),
            mock.Mock(return_value=DIGEST),
        )
        self.assertEqual(
            resolve("token", requester, downloader),
            {"geolite_version": "2026.10.01", "geolite_sha256": DIGEST},
        )
        downloader.assert_called_once_with(URL, len(DATA))

    def test_missing_digest_malformed_release_or_unexpected_url_is_rejected(self):
        variants = []
        for key, value in (
            ("digest", None),
            ("size", 0),
            ("browser_download_url", "https://example.invalid/database"),
        ):
            data = copy.deepcopy(RELEASE)
            data["assets"][0][key] = value
            variants.append(data)
        data = copy.deepcopy(RELEASE)
        data["prerelease"] = True
        variants.append(data)
        for data in variants:
            with self.subTest(data=data), self.assertRaises(ValueError):
                release_asset(data)

    def test_download_must_match_release_checksum(self):
        with self.assertRaisesRegex(ValueError, "SHA256 differs"):
            resolve(
                None, mock.Mock(return_value=RELEASE), mock.Mock(return_value="0" * 64)
            )

    def test_asset_download_never_forwards_api_credentials(self):
        opener = mock.Mock(return_value=io.BytesIO(DATA))
        self.assertEqual(download_sha256(URL, len(DATA), opener), DIGEST)
        self.assertIsNone(opener.call_args.args[0].get_header("Authorization"))
        self.assertEqual(opener.call_args.kwargs["timeout"], 30)

    def test_truncated_or_oversized_asset_is_rejected(self):
        for size in (len(DATA) - 1, len(DATA) + 1):
            with self.subTest(size=size), self.assertRaises(ValueError):
                download_sha256(URL, size, mock.Mock(return_value=io.BytesIO(DATA)))


if __name__ == "__main__":
    unittest.main()
