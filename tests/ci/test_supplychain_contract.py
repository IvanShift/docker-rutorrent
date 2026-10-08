"""Primary manifest captures and bounded, digest-preserving publication wiring."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest

from workflow_yaml import load

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
        # Parsed, so a "- uses:" step or a job-level reusable workflow is seen too.
        workflows = sorted(path for path in (ROOT / ".github/workflows").iterdir() if path.suffix in (".yml", ".yaml"))
        self.assertTrue(workflows)
        for path in workflows:
            text = path.read_text(encoding="utf-8")
            jobs = load(text)["jobs"].values()
            refs = [job["uses"] for job in jobs if "uses" in job]
            refs += [step["uses"] for job in jobs for step in job.get("steps") or [] if "uses" in step]
            self.assertEqual(len(refs), len(re.findall(r"(?m)^[ -]*uses:", text)), path.name)
            for ref in refs:
                with self.subTest(workflow=path.name, ref=ref):
                    self.assertRegex(ref, r"\A[^@./][^@]*@[0-9a-f]{40}\Z")

    def test_repeated_publish_reuses_only_four_staging_tags_after_the_revision_check(self):
        steps = {step.get("name"): step for step in load((ROOT / ".github/workflows/build.yml").read_text())["jobs"]["publish"]["steps"]}
        promote = steps["Promote the tested platform digests to release tags"]["run"]
        self.assertIn('"$IMAGE_NAME@$amd64" "$IMAGE_NAME@$arm64"', promote)
        self.assertIn('$TAG_DATE-run$RUN_ID-attempt$RUN_ATTEMPT', promote)
        body = steps["Upload candidate manifests without changing their digests"]["run"]
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            tools = base / "bin"
            tools.mkdir()
            # Only the transfer utility and the GitHub revision lookup are
            # substituted. The shipped shell reads actual candidate metadata
            # and compares its uploaded digest.
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
            verifier = base / "scripts/verify_publish_revision.py"
            verifier.parent.mkdir()
            verifier.write_text('import os,sys\n'
                                'with open(os.environ["TAG_TRACE"],"a") as f: f.write("verify "+" ".join(sys.argv[1:])+"\\n")\n'
                                'sys.exit(int(os.environ["VERIFY_STATUS"]))\n')
            script = base / "upload.sh"
            script.write_text(body)
            for flavor in ("default", "filebot"):
                for arch in ("amd64", "arm64"):
                    candidate = base / "candidates" / f"candidate-{flavor}-{arch}"
                    candidate.mkdir(parents=True)
                    (candidate / "metadata.json").write_text(json.dumps({"manifest_digest": "sha256:" + "a" * 64}))
            trace = base / "tags"
            env = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ["PATH"], RUNNER_TEMP=str(base),
                       IMAGE_NAME="example.invalid/rutorrent", TAG_TRACE=str(trace), REVISION="c" * 40,
                       REPOSITORY="example/rutorrent")
            verify = f"verify {'c' * 40} --repo example/rutorrent"
            expected = {f"docker://example.invalid/rutorrent:candidate-{f}-{a}"
                        for f in ("default", "filebot") for a in ("amd64", "arm64")}
            for run in ("111", "222"):
                trace.write_text("")
                # GitHub runs a step's script with bash -e.
                result = subprocess.run(["bash", "-e", str(script)], cwd=base, text=True, capture_output=True, timeout=10,
                                        env=dict(env, RUN_ID=run, RUN_ATTEMPT="1", VERIFY_STATUS="0"))
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = trace.read_text().splitlines()
                self.assertEqual(calls[0], verify)
                self.assertEqual(len(calls[1:]), 4)
                self.assertEqual(set(calls[1:]), expected)
            # A superseded commit is refused before it can overwrite any staging slot.
            trace.write_text("")
            result = subprocess.run(["bash", "-e", str(script)], cwd=base, text=True, capture_output=True, timeout=10,
                                    env=dict(env, RUN_ID="333", RUN_ATTEMPT="1", VERIFY_STATUS="1"))
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(trace.read_text().splitlines(), [verify])

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
