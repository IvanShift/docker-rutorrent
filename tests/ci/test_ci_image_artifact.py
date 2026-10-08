"""Behavior tests for exact archive, manifest and config publication identity."""

import copy
import gzip
import hashlib
import io
import json
import random
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from ci_image_artifact import (  # noqa: E402 - import repository helper after path setup
    record,
    verify_import,
    verify_record,
)

REVISION = "a" * 40


def archive_fixture(
    path,
    arch="amd64",
    revision=REVISION,
    corrupt_layer=False,
    duplicate_index=False,
    payload=b"fixture data",
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
        gzip.compress(payload), "application/vnd.oci.image.layer.v1.tar+gzip"
    )
    config = descriptor(
        {
            "os": "linux",
            "architecture": arch,
            "rootfs": {
                "type": "layers",
                "diff_ids": ["sha256:" + hashlib.sha256(payload).hexdigest()],
            },
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


def imported_fixture(
    oci,
    layout="classic",
    config_override=None,
    layers=None,
    tags=None,
    duplicate=False,
    layer_override=None,
):
    """Docker save compatibility manifests for both daemon image stores."""
    with tarfile.open(oci) as archive:
        index = json.load(archive.extractfile("index.json"))
        manifest = json.load(
            archive.extractfile(
                "blobs/sha256/" + index["manifests"][0]["digest"].split(":")[1]
            )
        )
        config_digest = manifest["config"]["digest"].split(":")[1]
        config_bytes = archive.extractfile("blobs/sha256/" + config_digest).read()
        layer = gzip.decompress(
            archive.extractfile(
                "blobs/sha256/" + manifest["layers"][0]["digest"].split(":")[1]
            ).read()
        )
    if layer_override is not None:
        layer = layer_override
    if layout == "classic":
        config_path, layer_path = config_digest + ".json", "layer/layer.tar"
    else:
        config_path = "blobs/sha256/" + config_digest
        layer = gzip.compress(layer)
        layer_path = "blobs/sha256/" + hashlib.sha256(layer).hexdigest()
    blobs = {
        config_path: config_bytes if config_override is None else config_override,
        layer_path: layer,
        "manifest.json": json.dumps(
            [
                {
                    "Config": config_path,
                    "RepoTags": ["ivanshift/rutorrent:validation-default-amd64"]
                    if tags is None
                    else tags,
                    "Layers": [layer_path] if layers is None else layers,
                }
            ]
        ).encode(),
    }
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, data in blobs.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
        if duplicate:
            member = tarfile.TarInfo("manifest.json")
            member.size = len(blobs["manifest.json"])
            archive.addfile(member, io.BytesIO(blobs["manifest.json"]))
    output.seek(0)
    return output


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

    def test_import_checks_exact_config_and_rootfs_for_classic_and_containerd_layouts(
        self,
    ):
        archive_fixture(self.archive)
        metadata = record(self.archive, "default", "amd64", REVISION)
        for layout in ("classic", "containerd"):
            with self.subTest(layout=layout):
                self.assertEqual(
                    verify_import(
                        imported_fixture(self.archive, layout),
                        metadata,
                        "ivanshift/rutorrent:validation-default-amd64",
                    ),
                    metadata,
                )

    def test_large_import_layers_are_checked_by_streaming_for_both_stores(self):
        archive_fixture(
            self.archive, payload=random.Random(1).randbytes(2 * 1024 * 1024 + 1)
        )
        metadata = record(self.archive, "default", "amd64", REVISION)
        for layout in ("classic", "containerd"):
            with self.subTest(layout=layout):
                verify_import(
                    imported_fixture(self.archive, layout),
                    metadata,
                    "ivanshift/rutorrent:validation-default-amd64",
                )

    def test_import_rejects_altered_layer_bytes_with_unchanged_config_and_manifest(
        self,
    ):
        archive_fixture(self.archive)
        metadata = record(self.archive, "default", "amd64", REVISION)
        for layout in ("classic", "containerd"):
            with (
                self.subTest(layout=layout),
                self.assertRaisesRegex(ValueError, "layers differ"),
            ):
                verify_import(
                    imported_fixture(self.archive, layout, layer_override=b"altered"),
                    metadata,
                    "ivanshift/rutorrent:validation-default-amd64",
                )

    def test_import_cannot_substitute_config_even_when_rootfs_is_unchanged(self):
        archive_fixture(self.archive)
        metadata = record(self.archive, "default", "amd64", REVISION)
        with self.assertRaisesRegex(ValueError, "config differs"):
            verify_import(
                imported_fixture(self.archive, config_override=b"substituted"),
                metadata,
                "ivanshift/rutorrent:validation-default-amd64",
            )

    def test_import_checks_manifest_tag_and_ordered_layers(self):
        archive_fixture(self.archive)
        metadata = record(self.archive, "default", "amd64", REVISION)
        for changes, error in (
            ({"tags": ["foreign:tag"]}, "candidate tag"),
            ({"layers": []}, "layers differ"),
            ({"layers": ["missing/layer.tar"]}, "Missing regular"),
            ({"layers": ["layer/layer.tar", "layer/layer.tar"]}, "layers differ"),
        ):
            with (
                self.subTest(changes=changes),
                self.assertRaisesRegex(ValueError, error),
            ):
                verify_import(
                    imported_fixture(self.archive, **changes),
                    metadata,
                    "ivanshift/rutorrent:validation-default-amd64",
                )

    def test_duplicate_import_manifest_is_rejected(self):
        archive_fixture(self.archive)
        metadata = record(self.archive, "default", "amd64", REVISION)
        with self.assertRaisesRegex(ValueError, "Duplicate imported"):
            verify_import(
                imported_fixture(self.archive, duplicate=True),
                metadata,
                "ivanshift/rutorrent:validation-default-amd64",
            )


if __name__ == "__main__":
    unittest.main()
