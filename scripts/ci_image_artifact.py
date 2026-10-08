#!/usr/bin/env python3
"""Record and verify a single-platform OCI candidate before smoke and publication.

The archive is retained byte for byte. Docker imports its image for testing;
publication uses skopeo --preserve-digests on the same verified archive.
"""

import argparse
import gzip
import hashlib
import io
import json
import re
import sys
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


def verify_import(stream, metadata, image):
    """Verify docker-save bytes without depending on daemon image-ID semantics.

    Classic and containerd stores may name images by config or manifest digest.
    Both export a compatibility manifest selecting the config and ordered layers.
    The retained OCI manifest stays the publication identity; its exact config
    binds the imported uncompressed rootfs even if Docker recompresses layers.
    """
    members = {}
    with tarfile.open(fileobj=stream, mode="r|*") as archive:
        for member in archive:
            name = member.name.removeprefix("./")
            if name in members:
                raise ValueError(f"Duplicate imported archive member: {name}")
            if member.islnk():
                members[name] = member.linkname.removeprefix("./")
            elif member.isfile():
                with archive.extractfile(member) as contents:
                    if member.size <= 2 * 1024 * 1024:
                        data = contents.read()
                        raw_digest = hashlib.sha256(data).hexdigest()
                        # Small layer blobs can also be gzip-compressed.
                        if data.startswith(b"\x1f\x8b"):
                            with gzip.GzipFile(
                                fileobj=io.BytesIO(data)
                            ) as uncompressed:
                                diff_digest = hashlib.file_digest(
                                    uncompressed, "sha256"
                                ).hexdigest()
                        else:
                            diff_digest = raw_digest
                    else:
                        data = None
                        with io.BufferedReader(contents) as buffered:
                            if buffered.peek(2).startswith(b"\x1f\x8b"):
                                with gzip.GzipFile(fileobj=buffered) as uncompressed:
                                    diff_digest = hashlib.file_digest(
                                        uncompressed, "sha256"
                                    ).hexdigest()
                                raw_digest = None
                            else:
                                raw_digest = hashlib.file_digest(
                                    buffered, "sha256"
                                ).hexdigest()
                                diff_digest = raw_digest
                members[name] = (raw_digest, diff_digest, data)
            else:
                members[name] = None

    def regular(name):
        seen = set()
        while isinstance(members.get(name), str):
            if name in seen:
                raise ValueError("Cyclic imported archive link")
            seen.add(name)
            name = members[name]
        member = members.get(name)
        if not isinstance(member, tuple):
            raise ValueError(f"Missing regular imported archive member: {name}")
        return member

    manifest_bytes = regular("manifest.json")[2]
    if manifest_bytes is None:
        raise ValueError("Imported image manifest is too large")
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest, list) or len(manifest) != 1:
        raise ValueError("Expected exactly one imported image manifest")
    manifest = manifest[0]
    if image not in manifest.get("RepoTags", []):
        raise ValueError("Imported image manifest does not select the candidate tag")
    config_digest, _, config_bytes = regular(manifest["Config"])
    if "sha256:" + str(config_digest) != metadata["config_digest"]:
        raise ValueError(
            "Imported image config differs from the retained OCI candidate"
        )
    if config_bytes is None:
        raise ValueError("Imported image config is too large")
    config = json.loads(config_bytes)
    rootfs = config.get("rootfs", {})
    diff_ids = rootfs.get("diff_ids")
    layers = manifest.get("Layers")
    if (
        rootfs.get("type") != "layers"
        or not isinstance(diff_ids, list)
        or not isinstance(layers, list)
    ):
        raise ValueError("Imported image lacks an ordered rootfs manifest")
    actual_layers = ["sha256:" + regular(name)[1] for name in layers]
    if actual_layers != diff_ids or any(
        not DIGEST.fullmatch(value) for value in diff_ids
    ):
        raise ValueError("Imported image layers differ from the exact config rootfs")
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("record", "verify", "verify-import"))
    parser.add_argument("archive", type=Path)
    parser.add_argument("metadata", type=Path)
    parser.add_argument("flavor", choices=("default", "filebot"))
    parser.add_argument("arch", choices=("amd64", "arm64"))
    parser.add_argument("revision")
    parser.add_argument("--image", help="Exact tag selected by docker image save")
    args = parser.parse_args()
    if args.mode == "verify-import" and not args.image:
        parser.error("verify-import requires --image")
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
            if args.mode == "verify-import":
                result = verify_import(sys.stdin.buffer, result, args.image)
    except (ValueError, KeyError, OSError, EOFError, tarfile.TarError) as error:
        parser.exit(1, f"Invalid CI image artifact: {error}\n")
    print(result["config_digest"])


if __name__ == "__main__":
    main()
