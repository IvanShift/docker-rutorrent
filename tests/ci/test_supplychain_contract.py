"""Primary manifest captures and bounded, digest-preserving publication wiring."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(os.environ.get("SUPPLYCHAIN_TEST_ROOT", Path(__file__).resolve().parents[2]))
FIXTURES = Path(__file__).parent / "fixtures/registry"


class SupplyChainContract(unittest.TestCase):
    def test_every_alpine_stage_and_frontend_use_verified_multiarch_index(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        captures = json.loads((FIXTURES / "provenance.json").read_text())["registries"]
        for capture in captures:
            filename = capture["repository"].replace("/", "-") + "-index.json"
            raw = (FIXTURES / filename).read_bytes()
            digest = "sha256:" + hashlib.sha256(raw).hexdigest()
            self.assertEqual(digest, capture["index_digest"])
            platforms = {m["platform"].get("architecture") for m in json.loads(raw)["manifests"]
                         if m["platform"].get("os") == "linux"}
            self.assertTrue({"amd64", "arm64"} <= platforms)
            if capture["repository"] == "library/alpine":
                self.assertIn("ARG ALPINE_DIGEST=" + digest, dockerfile)
                stages = re.findall(r"(?m)^FROM\s+.*alpine:.*$", dockerfile)
                self.assertGreaterEqual(len(stages), 6)
                for stage in stages:
                    self.assertIn("alpine:${ALPINE_VERSION}@${ALPINE_DIGEST}", stage)
            else:
                self.assertEqual(dockerfile.splitlines()[0],
                                 "# syntax=docker/dockerfile:" + capture["tag"] + "@" + digest)

    def test_filebot_download_has_finite_retry_connection_and_transfer_budgets(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        line = next(line for line in dockerfile.splitlines() if "get.filebot.net/filebot/" in line)
        args = shlex.split(line[line.index("curl "):].removesuffix("\\").strip())
        for flag, maximum in (("--retry", 3), ("--retry-max-time", 600),
                              ("--connect-timeout", 15), ("--max-time", 180)):
            self.assertIn(flag, args)
            value = int(args[args.index(flag) + 1])
            self.assertGreater(value, 0)
            self.assertLessEqual(value, maximum)
        self.assertTrue(re.search(r'"\$\{FILEBOT_SHA256\}" /filebot/filebot\.tar\.xz \| sha256sum -c -', dockerfile), 'FileBot checksum gate must remain present')

    def test_all_docker_workflow_actions_are_immutable(self):
        workflow = (ROOT / ".github/workflows/build.yml").read_text()
        refs = re.findall(r"(?m)^\s+uses:\s+([^\s#]+)", workflow)
        self.assertTrue(refs)
        for ref in refs:
            self.assertRegex(ref, r"\A[^@]+@[0-9a-f]{40}\Z")

    def test_repeated_publish_reuses_only_four_serialized_staging_tags(self):
        workflow = (ROOT / ".github/workflows/build.yml").read_text()
        publish = workflow.split("\n  publish:\n", 1)[1]
        self.assertRegex(publish, r"concurrency:\s+group: publish-rutorrent\s+cancel-in-progress: false")
        self.assertIn('"$IMAGE_NAME@$amd64" "$IMAGE_NAME@$arm64"', publish)
        self.assertIn('$TAG_DATE-run$RUN_ID-attempt$RUN_ATTEMPT', publish)
        upload = publish.split("      - name: Upload candidate manifests without changing their digests\n", 1)[1]
        body = upload.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0]
        body = textwrap.dedent(body)
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            tools = base / "bin"
            tools.mkdir()
            # Only the transfer utility is substituted. The shipped shell reads
            # actual candidate metadata and compares its uploaded digest.
            skopeo = tools / "skopeo"
            skopeo.write_text('#!/usr/bin/env python3\n'
                              'import json,os,pathlib,sys\n'
                              'args=sys.argv[1:]\n'
                              'assert "--preserve-digests" in args\n'
                              'out=pathlib.Path(args[args.index("--digestfile")+1])\n'
                              'expected=json.loads((out.parent/"metadata.json").read_text())["manifest_digest"]\n'
                              'out.write_text(expected+"\\n")\n'
                              'with open(os.environ["TAG_TRACE"],"a") as f: f.write(args[-1]+"\\n")\n')
            skopeo.chmod(0o755)
            for flavor in ("default", "filebot"):
                for arch in ("amd64", "arm64"):
                    candidate = base / "candidates" / f"candidate-{flavor}-{arch}"
                    candidate.mkdir(parents=True)
                    (candidate / "metadata.json").write_text(json.dumps({"manifest_digest": "sha256:" + "a" * 64}))
            trace = base / "tags"
            env = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ["PATH"],
                       RUNNER_TEMP=str(base), IMAGE_NAME="example.invalid/rutorrent", TAG_TRACE=str(trace))
            for run in ("111", "222"):
                result = subprocess.run(["bash", "-c", body], env=dict(env, RUN_ID=run, RUN_ATTEMPT="1"),
                                        text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
            tags = trace.read_text().splitlines()
            expected = {f"docker://example.invalid/rutorrent:candidate-{f}-{a}"
                        for f in ("default", "filebot") for a in ("amd64", "arm64")}
            self.assertEqual(len(tags), 8)
            self.assertEqual(set(tags), expected)

    def test_docker_helpers_are_installed_without_changing_the_application_abi(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertIn("AS docker-helpers", dockerfile)
        self.assertIn("source=rootfs/usr/local/libexec,target=/sources,readonly", dockerfile)
        self.assertIn("source=scripts/build-docker-helpers.sh", dockerfile)
        self.assertIn("COPY --from=docker-helpers /helpers/ /", dockerfile)
        self.assertIn('CMD ["/usr/local/libexec/service-runtime", "supervise", "/etc/s6.d"]', dockerfile)
        self.assertNotIn("45000/udp", next(line for line in dockerfile.splitlines() if line.startswith("EXPOSE ")))


if __name__ == "__main__":
    unittest.main()
