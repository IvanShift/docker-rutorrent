"""Primary manifest captures and bounded, digest-preserving publication wiring."""
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest

from php_runtime_contract import docker_instructions
from workflow_yaml import load

ROOT = Path(os.environ.get("SUPPLYCHAIN_TEST_ROOT", Path(__file__).resolve().parents[2]))
FIXTURES = Path(__file__).parent / "fixtures/registry"


def dockerfile_stages(text):
    """Return {stage: (base, instructions)} in file order; base may be another stage."""
    stages, current = {}, None
    for instruction in docker_instructions(text):
        keyword, _, rest = instruction.partition(" ")
        if keyword.upper() == "FROM":
            words = [word for word in rest.split() if not word.startswith("--")]
            current = []
            stages[words[-1] if len(words) == 3 else None] = (words[0], current)
        elif current is not None:
            current.append(instruction)
    return stages


def stage_inputs(stages, name):
    """Every stage that NAME is built from: its base chain and COPY/mount sources."""
    seen, pending = set(), [name]
    while pending:
        stage = pending.pop()
        if stage in seen or stage not in stages:
            continue
        seen.add(stage)
        base, instructions = stages[stage]
        pending.append(base)
        for instruction in instructions:
            pending += re.findall(r"--from=([\w.-]+)", instruction) + re.findall(r"[,=]from=([\w.-]+)", instruction)
    return seen


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

    def test_build_context_allowlist_is_exactly_the_dockerfile_inputs(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        sources = set()
        for instruction in docker_instructions(dockerfile):
            for mount in re.findall(r"--mount=(\S+)", instruction):
                options = dict(item.partition("=")[::2] for item in mount.split(","))
                if options["type"] == "bind" and "from" not in options:
                    sources.add(options["source"])
            words = instruction.split()
            if words[0].upper() == "COPY" and not any(word.startswith("--from=") for word in words):
                sources.update(word for word in words[1:-1] if not word.startswith("--"))
        patterns = [line.strip() for line in (ROOT / ".dockerignore").read_text().splitlines()
                    if line.strip() and not line.lstrip().startswith("#")]
        self.assertEqual(patterns[0], "*", "the context must be an allowlist")
        allowed = [pattern[1:].rstrip("/") for pattern in patterns if pattern.startswith("!")]
        leftovers = [pattern for pattern in patterns[1:] if not pattern.startswith("!")]

        def within(path, entry):
            return path == entry or path.startswith(entry + "/")

        for source in sorted(sources):
            with self.subTest(source=source):
                self.assertTrue(any(within(source, entry) for entry in allowed), "build input outside the context")
                self.assertTrue((ROOT / source).exists())
                self.assertFalse(any(fnmatch.fnmatch(source, pattern) for pattern in leftovers))
        for entry in allowed:
            with self.subTest(entry=entry):
                self.assertTrue(any(within(source, entry) or within(entry, source) for source in sources),
                                "context entry that no Dockerfile step reads")
        for pattern in leftovers:
            self.assertTrue(pattern.startswith("**/"), "only leftovers inside allowed directories may be excluded")

    def test_independent_inputs_have_their_own_cache_and_runtime_only_copies_them(self):
        stages = dockerfile_stages((ROOT / "Dockerfile").read_text())
        # An rTorrent pin change must not rebuild the small native tools.
        for tool in ("mktorrent-build", "dumptorrent-build", "unrar-build"):
            with self.subTest(tool=tool):
                inputs = stage_inputs(stages, tool)
                self.assertTrue({"src", "builder"}.isdisjoint(inputs), inputs)
                self.assertNotIn("RTORRENT", " ".join(" ".join(stages[name][1]) for name in inputs))
        self.assertTrue({"builder", "mktorrent-build", "dumptorrent-build", "unrar-build"}
                        <= stage_inputs(stages, "native-dist"))
        runtime = stages["runtime"][1]
        position = {instruction: index for index, instruction in enumerate(runtime)}
        packages = next(index for index, instruction in enumerate(runtime) if "apk upgrade" in instruction)
        self.assertLess(packages, position["COPY --from=native-dist / /"])
        # The FileBot archive is fetched once on the build platform, not in runtime.
        self.assertFalse(any("get.filebot.net" in instruction for instruction in runtime))
        self.assertIn("COPY --from=filebot-payload /payload/ /", position)
        self.assertEqual(stages["filebot-payload"][0], "fetch-base")
        self.assertIn("--platform=${BUILDPLATFORM}", next(line for line in (ROOT / "Dockerfile").read_text().splitlines()
                                                         if line.endswith(" AS fetch-base")))
        # The checkout cleanup happens before runtime: deleted bytes never enter a layer.
        self.assertIn("COPY --from=rutorrent-app /export/ /", position)
        self.assertFalse(any("rutorrent-source" in instruction for instruction in runtime))
        lint = next(instruction for instruction in runtime if "php85 -l" in instruction)
        self.assertIn("xargs -0 -n100 php85 -l", lint)

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
