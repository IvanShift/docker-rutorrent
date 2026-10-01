"""Behavior tests for exact archive, manifest and config publication identity."""

import copy
import gzip
import hashlib
import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from ci_image_artifact import (  # noqa: E402 - import repository helper after path setup
    record,
    verify_record,
)

REVISION = "a" * 40


def archive_fixture(
    path, arch="amd64", revision=REVISION, corrupt_layer=False, duplicate_index=False
):
    blobs = {}

    def descriptor(value, media_type):
        data = value if isinstance(value, bytes) else json.dumps(value).encode()
        digest = hashlib.sha256(data).hexdigest()
        blobs[f"blobs/sha256/{digest}"] = data
        return {
            "mediaType": media_type,
            "size": len(data),
            "digest": f"sha256:{digest}",
        }

    layer = descriptor(
        gzip.compress(b"fixture data"), "application/vnd.oci.image.layer.v1.tar+gzip"
    )
    config = descriptor(
        {
            "os": "linux",
            "architecture": arch,
            "config": {"Labels": {"org.opencontainers.image.revision": revision}},
        },
        "application/vnd.oci.image.config.v1+json",
    )
    manifest = descriptor(
        {"schemaVersion": 2, "config": config, "layers": [layer]},
        "application/vnd.oci.image.manifest.v1+json",
    )
    index = json.dumps({"schemaVersion": 2, "manifests": [manifest]}).encode()
    blobs["index.json"] = index
    blobs["oci-layout"] = b'{"imageLayoutVersion":"1.0.0"}'
    if corrupt_layer:
        name = "blobs/sha256/" + layer["digest"].split(":")[1]
        blobs[name] = b"x" * len(blobs[name])
    with tarfile.open(path, "w") as stream:
        for name, data in blobs.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            stream.addfile(member, io.BytesIO(data))
        if duplicate_index:
            member = tarfile.TarInfo("index.json")
            member.size = len(index)
            stream.addfile(member, io.BytesIO(index))
    return manifest, config


class CandidateArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rem4-ci-artifact-")
        self.addCleanup(self.temp.cleanup)
        self.archive = Path(self.temp.name) / "candidate.tar"

    def test_saved_archive_records_exact_publication_manifest_and_test_config(self):
        manifest, config = archive_fixture(self.archive)
        metadata = record(self.archive, "default", "amd64", REVISION)
        self.assertEqual(metadata["manifest_digest"], manifest["digest"])
        self.assertEqual(metadata["config_digest"], config["digest"])
        self.assertEqual(
            verify_record(self.archive, metadata, "default", "amd64", REVISION),
            metadata,
        )

    def test_platform_revision_or_flavor_substitution_is_rejected(self):
        archive_fixture(self.archive)
        metadata = record(self.archive, "default", "amd64", REVISION)
        for flavor, arch, revision in (
            ("filebot", "amd64", REVISION),
            ("default", "arm64", REVISION),
            ("default", "amd64", "b" * 40),
        ):
            with (
                self.subTest(flavor=flavor, arch=arch, revision=revision),
                self.assertRaises(ValueError),
            ):
                verify_record(self.archive, metadata, flavor, arch, revision)

    def test_corrupt_layer_cannot_be_recorded_as_a_candidate(self):
        archive_fixture(self.archive, corrupt_layer=True)
        with self.assertRaisesRegex(ValueError, "layer digest mismatch"):
            record(self.archive, "default", "amd64", REVISION)

    def test_duplicate_archive_member_is_rejected(self):
        archive_fixture(self.archive, duplicate_index=True)
        with self.assertRaisesRegex(ValueError, "Duplicate archive member"):
            record(self.archive, "default", "amd64", REVISION)

    def test_modified_archive_is_rejected_after_smoke_recording(self):
        archive_fixture(self.archive)
        metadata = record(self.archive, "default", "amd64", REVISION)
        with self.archive.open("ab") as output:
            output.write(b"changed after smoke")
        with self.assertRaisesRegex(
            ValueError, "differs from the smoke-tested artifact"
        ):
            verify_record(self.archive, metadata, "default", "amd64", REVISION)

    def test_modified_manifest_digest_metadata_is_rejected(self):
        archive_fixture(self.archive)
        metadata = record(self.archive, "default", "amd64", REVISION)
        changed = copy.deepcopy(metadata)
        changed["manifest_digest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(
            ValueError, "differs from the smoke-tested artifact"
        ):
            verify_record(self.archive, changed, "default", "amd64", REVISION)


if __name__ == "__main__":
    unittest.main()
