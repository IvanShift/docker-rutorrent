#!/usr/bin/env python3
"""Record and verify a single-platform OCI candidate before smoke and publication.

The archive is retained byte for byte. Docker imports its image for testing;
publication uses skopeo --preserve-digests on the same verified archive.
"""

import argparse
import hashlib
import json
import re
import tarfile
from pathlib import Path

DIGEST = re.compile(r"sha256:([0-9a-f]{64})\Z")


def file_sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inspect_archive(path, arch, revision):
    with tarfile.open(path, "r:*") as archive:
        members = {}
        for member in archive.getmembers():
            # Do not extract files or permit ambiguous repeated OCI member names.
            name = member.name.removeprefix("./")
            if name in members:
                raise ValueError(f"Duplicate archive member: {name}")
            members[name] = member

        def read(name):
            member = members.get(name)
            if member is None or not member.isfile():
                raise ValueError(f"Missing regular OCI archive member: {name}")
            with archive.extractfile(member) as stream:
                return stream.read()

        def blob(descriptor):
            digest = descriptor.get("digest", "")
            match = DIGEST.fullmatch(digest)
            if not match:
                raise ValueError("Invalid OCI descriptor digest")
            data = read(f"blobs/sha256/{match[1]}")
            if (
                len(data) != descriptor.get("size")
                or hashlib.sha256(data).hexdigest() != match[1]
            ):
                raise ValueError(f"OCI blob digest or size mismatch: {digest}")
            return json.loads(data)

        index = json.loads(read("index.json"))
        descriptors = index.get("manifests")
        if not isinstance(descriptors, list) or len(descriptors) != 1:
            raise ValueError("Expected exactly one image in the OCI candidate archive")
        descriptor = descriptors[0]
        manifest = blob(descriptor)
        if (
            manifest.get("schemaVersion") != 2
            or "layers" not in manifest
            or "config" not in manifest
        ):
            raise ValueError(
                "Expected a single-platform OCI image manifest "
                "(attestations disabled at export)"
            )
        config = blob(manifest["config"])
        if config.get("os") != "linux" or config.get("architecture") != arch:
            raise ValueError(
                "OCI candidate platform does not match the native smoke runner"
            )
        labels = config.get("config", {}).get("Labels", {})
        if labels.get("org.opencontainers.image.revision") != revision:
            raise ValueError(
                "OCI candidate revision does not match this workflow checkout"
            )
        for layer in manifest["layers"]:
            match = DIGEST.fullmatch(layer.get("digest", ""))
            if not match:
                raise ValueError("Invalid OCI layer digest")
            member = members.get(f"blobs/sha256/{match[1]}")
            if (
                member is None
                or not member.isfile()
                or member.size != layer.get("size")
            ):
                raise ValueError("Missing or truncated OCI image layer")
            with archive.extractfile(member) as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != match[1]:
                raise ValueError("OCI layer digest mismatch")
        return {
            "manifest_digest": descriptor["digest"],
            "config_digest": manifest["config"]["digest"],
        }


def record(path, flavor, arch, revision):
    return {
        "flavor": flavor,
        "platform": f"linux/{arch}",
        "revision": revision,
        "archive_sha256": file_sha256(path),
        **inspect_archive(path, arch, revision),
    }


def verify_record(path, metadata, flavor, arch, revision):
    actual = record(path, flavor, arch, revision)
    if metadata != actual:
        raise ValueError(
            "Candidate archive or identity differs from the smoke-tested artifact"
        )
    return actual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("record", "verify"))
    parser.add_argument("archive", type=Path)
    parser.add_argument("metadata", type=Path)
    parser.add_argument("flavor", choices=("default", "filebot"))
    parser.add_argument("arch", choices=("amd64", "arm64"))
    parser.add_argument("revision")
    args = parser.parse_args()
    try:
        if args.mode == "record":
            result = record(args.archive, args.flavor, args.arch, args.revision)
            args.metadata.write_text(
                json.dumps(result, indent=2) + "\n", encoding="utf-8"
            )
        else:
            result = verify_record(
                args.archive,
                json.loads(args.metadata.read_text(encoding="utf-8")),
                args.flavor,
                args.arch,
                args.revision,
            )
    except (ValueError, KeyError, OSError, tarfile.TarError) as error:
        parser.exit(1, f"Invalid CI image artifact: {error}\n")
    print(result["config_digest"])


if __name__ == "__main__":
    main()
