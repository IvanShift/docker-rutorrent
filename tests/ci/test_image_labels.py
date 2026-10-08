"""Pin the OCI and source-identity labels of the published runtime image.

Earlier images got licenses, url, title and version from docker/metadata-action;
the Dockerfile now owns every label. A renamed, dropped or empty label changes
what registries and scanners show, so the whole set is frozen here.
"""

import re
import shlex
import unittest
from pathlib import Path

from php_runtime_contract import docker_instructions

ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_URL = "https://github.com/IvanShift/docker-rutorrent"
EXPECTED = {
    "org.opencontainers.image.title": "ruTorrent on Alpine",
    "org.opencontainers.image.description": "rTorrent + ruTorrent built from source on Alpine",
    # The packaged software's exact version; a moving tag name is not one.
    "org.opencontainers.image.version": "${RUTORRENT_COMMIT_SHA}",
    "org.opencontainers.image.revision": "${VCS_REF}",
    "org.opencontainers.image.created": "${BUILD_DATE}",
    "org.opencontainers.image.source": REPOSITORY_URL,
    "org.opencontainers.image.url": REPOSITORY_URL,
    "org.opencontainers.image.licenses": "MIT",
    "io.github.ivanshift.rtorrent.revision": "${RTORRENT_VERSION}",
    "io.github.ivanshift.rtorrent.version": "${RTORRENT_BRANCH}",
    "io.github.ivanshift.libtorrent.revision": "${LIBTORRENT_VERSION}",
    "io.github.ivanshift.libtorrent.version": "${LIBTORRENT_BRANCH}",
    "io.github.ivanshift.rutorrent.revision": "${RUTORRENT_COMMIT_SHA}",
    "io.github.ivanshift.geolite.version": "${GEOIP2_DB_VERSION}",
    "io.github.ivanshift.geolite.sha256": "${GEOIP2_DB_SHA256}",
    "maintainer": "IvanShift",
}


def runtime_label_inputs():
    """Return the runtime stage's labels and the ARGs declared before them."""
    runtime = False
    declared, labels = set(), None
    for instruction in docker_instructions((ROOT / "Dockerfile").read_text(encoding="utf-8")):
        keyword, _, rest = instruction.partition(" ")
        keyword = keyword.upper()
        if keyword == "FROM":
            runtime = re.search(r"\sAS\s+runtime\s*$", instruction, re.I) is not None
        elif runtime and keyword == "ARG":
            declared.add(rest.split("=", 1)[0].strip())
        elif runtime and keyword == "LABEL":
            if labels is not None:
                raise AssertionError("Expected one LABEL instruction in the runtime stage")
            labels = dict(word.split("=", 1) for word in shlex.split(rest))
            labels["__declared__"] = frozenset(declared)
    if labels is None:
        raise AssertionError("Runtime stage has no LABEL instruction")
    return labels.pop("__declared__"), labels


class ImageLabelTests(unittest.TestCase):
    def test_runtime_labels_are_complete_and_named_as_published(self):
        _, labels = runtime_label_inputs()
        self.assertEqual(labels, EXPECTED)

    def test_every_label_argument_is_declared_before_the_label(self):
        # An undeclared build argument expands to an empty label value.
        declared, labels = runtime_label_inputs()
        for key, value in labels.items():
            for argument in re.findall(r"\$\{([A-Z0-9_]+)\}", value):
                with self.subTest(label=key):
                    self.assertIn(argument, declared)

    def test_license_label_names_the_repository_license(self):
        _, labels = runtime_label_inputs()
        first_line = (ROOT / "LICENSE").read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(first_line, f"{labels['org.opencontainers.image.licenses']} License")


if __name__ == "__main__":
    unittest.main()
