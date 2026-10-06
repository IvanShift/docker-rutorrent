"""Freeze the release wiring that transfers exactly the tested image bytes."""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from verify_publish_revision import verify_revision  # noqa: E402


class WorkflowContractTests(unittest.TestCase):
    def setUp(self):
        self.workflow = (ROOT / ".github/workflows/build.yml").read_text()

    def job(self, name):
        match = re.search(
            rf"(?ms)^  {name}:\n.*?(?=^  [a-z_]+:\n|\Z)", self.workflow
        )
        self.assertIsNotNone(match, f"Required workflow job: {name}")
        return match[0]

    def test_publication_requires_native_and_legacy_validation(self):
        publish = self.job("publish")
        self.assertRegex(publish, r"needs:\s*\[prepare, validate, legacy\]")
        self.assertIn("success()", publish)
        self.assertIn("github.ref == 'refs/heads/master'", publish)

    def test_build_inputs_preserve_source_pins_and_refresh_runtime_packages(self):
        for name in ("validate", "legacy"):
            job = self.job(name)
            self.assertIn("no-cache-filters: runtime", job)
            self.assertNotRegex(job, r"(?m)^\s+RUTORRENT_(REF|COMMIT_SHA)=")

    def test_exact_tested_archive_is_uploaded_without_a_publish_rebuild(self):
        validate, publish = self.job("validate"), self.job("publish")
        self.assertIn("outputs: type=oci,dest=", validate)
        self.assertIn("ci_image_artifact.py record", validate)
        self.assertIn("actions/upload-artifact@", validate)
        self.assertIn("ci_image_artifact.py verify", publish)
        self.assertIn("copy --preserve-digests --retry-times 3", publish)
        self.assertNotIn("docker/build-push-action@", publish)
        self.assertNotRegex(publish, r"docker (?:build |buildx build )")
        self.assertLess(publish.index("ci_image_artifact.py verify"), publish.index("Login to Docker Hub"))

    def test_revision_guard_precedes_mutable_tag_promotion(self):
        publish = self.job("publish")
        self.assertIn("group: publish-rutorrent", publish)
        self.assertIn("cancel-in-progress: false", publish)
        self.assertLess(publish.index("verify_publish_revision.py"), publish.index("docker buildx imagetools create"))
        self.assertIn('REVISION: ${{ github.sha }}', publish)
        self.assertIn('REPOSITORY: ${{ github.repository }}', publish)

    def test_regression_smokes_are_required_by_workflow(self):
        validate = self.job("validate")
        for script in ("startup-regressions", "erasedata-fpm-rename", "retrackers-metadata"):
            self.assertIn(f'tests/smoke/{script}.sh "$CANDIDATE_IMAGE"', validate)
        self.assertIn("tests/smoke/retrackers-metadata.sh rutorrent-ci:legacy-098 legacy", self.job("legacy"))

    def test_geolite_identity_is_retained_in_final_image(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        for field in ("version", "sha256"):
            self.assertIn(f'io.github.ivanshift.geolite.{field}="${{GEOIP2_DB_{field.upper()}}}"', dockerfile)


class PublicationRevisionTests(unittest.TestCase):
    def test_only_current_master_can_promote_tags(self):
        current = "a" * 40
        response = {"ref": "refs/heads/master", "object": {"type": "commit", "sha": current}}
        verify_revision(current, response)
        with self.assertRaisesRegex(ValueError, "no longer master"):
            verify_revision("b" * 40, response)

    def test_unproven_branch_or_revision_is_refused(self):
        for response in ({}, {"ref": "refs/heads/other"}, {"ref": "refs/heads/master", "object": {"type": "tag", "sha": "a" * 40}}):
            with self.subTest(response=response), self.assertRaises(ValueError):
                verify_revision("a" * 40, response)


if __name__ == "__main__":
    unittest.main()
