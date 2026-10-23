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

from dockerfile_parser import instructions, run_scripts, stages as dockerfile_stages
from workflow_yaml import load

ROOT = Path(os.environ.get("SUPPLYCHAIN_TEST_ROOT", Path(__file__).resolve().parents[2]))
FIXTURES = Path(__file__).parent / "fixtures/registry"


def stage_inputs(stages, name):
    """Every stage that NAME is built from: its base chain and COPY/mount sources."""
    seen, pending = set(), [name]
    while pending:
        stage = pending.pop()
        if stage in seen or stage not in stages:
            continue
        seen.add(stage)
        pending.append(stages[stage].base)
        for instruction in stages[stage].instructions:
            pending += re.findall(r"--from=([\w.-]+)", instruction) + re.findall(r"[,=]from=([\w.-]+)", instruction)
    return seen


def context_sources(instruction):
    """Build-context paths one instruction reads: bind mounts and COPY sources without a stage."""
    sources = []
    for mount in re.findall(r"--mount=(\S+)", instruction):
        options = dict(item.partition("=")[::2] for item in mount.split(","))
        if options["type"] == "bind" and "from" not in options:
            sources.append(options["source"])
    words = instruction.split()
    if words[0].upper() == "COPY" and not any(word.startswith("--from=") for word in words):
        sources += [word for word in words[1:-1] if not word.startswith("--")]
    return sources


def context_files(source):
    """The files of the build context that SOURCE (a path, directory or glob) selects."""
    files = set()
    for path in ROOT.glob(source):
        candidates = path.rglob("*") if path.is_dir() else [path]
        files.update(item.relative_to(ROOT).as_posix() for item in candidates
                     if item.is_file() and "__pycache__" not in item.parts)
    return files


class SupplyChainContract(unittest.TestCase):
    def test_historical_multiarch_registry_fixtures_keep_their_identity(self):
        # Historical parser fixtures stay fixed. New registry replies are local
        # review evidence; dependency refreshes must not commit raw captures.
        captures = json.loads((FIXTURES / "provenance.json").read_text())["registries"]
        for capture in captures:
            filename = capture["repository"].replace("/", "-") + "-index.json"
            raw = (FIXTURES / filename).read_bytes()
            self.assertEqual("sha256:" + hashlib.sha256(raw).hexdigest(), capture["index_digest"])
            platforms = {m["platform"].get("architecture") for m in json.loads(raw)["manifests"]
                         if m["platform"].get("os") == "linux"}
            self.assertTrue({"amd64", "arm64"} <= platforms)

    def test_every_alpine_stage_and_frontend_use_reviewed_multiarch_index(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        headers = [stage.header for stage in dockerfile_stages(dockerfile).values()]
        # Verified against Docker Hub on 2026-10-23, including amd64 and arm64.
        self.assertIn("ARG ALPINE_VERSION=3.24.3\n", dockerfile)
        self.assertIn("ARG ALPINE_DIGEST=sha256:be2656061b536c1883c4034790e21d0a09c5aaaf51968b89368a3c0371ea2c9a\n", dockerfile)
        alpine = [header for header in headers if "alpine:" in header]
        self.assertGreaterEqual(len(alpine), 6)
        for header in alpine:
            self.assertIn("alpine:${ALPINE_VERSION}@${ALPINE_DIGEST}", header)
        self.assertEqual(dockerfile.splitlines()[0],
                         "# syntax=docker/dockerfile:1.28@sha256:bb22d9815c728170f72750f4e5b0d672e06176142e1d602c7e66c050100b7e5b")

    def test_filebot_download_has_finite_retry_connection_and_transfer_budgets(self):
        # test_build_metadata runs this RUN with a wrong checksum: the gate itself is tested there.
        script, = run_scripts(dockerfile_stages((ROOT / "Dockerfile").read_text())["filebot-payload"])
        lexer = shlex.shlex(script, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        words = list(lexer)
        start = words.index("curl")
        args = words[start:words.index("&&", start)]
        self.assertTrue(any("get.filebot.net/filebot/" in arg for arg in args))
        for flag, maximum in (("--retry", 3), ("--retry-max-time", 600),
                              ("--connect-timeout", 15), ("--max-time", 180)):
            self.assertIn(flag, args)
            value = int(args[args.index(flag) + 1])
            self.assertGreater(value, 0)
            self.assertLessEqual(value, maximum)

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
        sources = {source for instruction in instructions((ROOT / "Dockerfile").read_text())
                   for source in context_sources(instruction)}
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
                self.assertTrue(context_files(source))
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
                self.assertNotIn("RTORRENT", " ".join(" ".join(stages[name].instructions) for name in inputs))
        self.assertTrue({"builder", "mktorrent-build", "dumptorrent-build", "unrar-build"}
                        <= stage_inputs(stages, "native-dist"))
        runtime = stages["runtime"].instructions
        position = {instruction: index for index, instruction in enumerate(runtime)}
        packages = next(index for index, instruction in enumerate(runtime) if "apk upgrade" in instruction)
        self.assertLess(packages, position["COPY --from=native-dist / /"])
        # The FileBot archive is fetched once on the build platform, not in runtime.
        self.assertFalse(any("get.filebot.net" in instruction for instruction in runtime))
        self.assertIn("COPY --from=filebot-payload /payload/ /", position)
        self.assertEqual(stages["filebot-payload"].base, "fetch-base")
        self.assertIn("--platform=${BUILDPLATFORM}", stages["fetch-base"].header)
        # The checkout cleanup happens before runtime: deleted bytes never enter a layer.
        self.assertIn("COPY --from=rutorrent-app /export/ /", position)
        self.assertFalse(any("rutorrent-source" in instruction for instruction in runtime))
        lint = next(instruction for instruction in runtime if "php85 -l" in instruction)
        self.assertIn("xargs -0 -n100 php85 -l", lint)

    def test_docker_helpers_are_installed_without_changing_the_application_abi(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertIn("COPY --from=docker-helpers /helpers/ /", dockerfile_stages(dockerfile)["runtime"].instructions)
        self.assertIn('CMD ["/usr/local/libexec/service-runtime", "supervise", "/etc/s6.d"]', dockerfile)
        self.assertNotIn("45000/udp", next(line for line in dockerfile.splitlines() if line.startswith("EXPOSE ")))

    def test_docker_helper_compile_reads_only_the_sources_its_script_names(self):
        # The PHP and shell workers next to the C sources must not invalidate the
        # compile cache: the stage and its inputs read only the build script and
        # the sources that script compiles, which owns that list.
        stages = dockerfile_stages((ROOT / "Dockerfile").read_text())
        specs = re.search(r"(?m)^for spec in (.+); do$", (ROOT / "scripts/build-docker-helpers.sh").read_text())[1]
        compiled = {"rootfs/usr/local/libexec/" + spec.partition(":")[0] for spec in specs.split()}
        self.assertEqual(len(compiled), 3)
        read = set()
        for name in stage_inputs(stages, "docker-helpers"):
            for instruction in stages[name].instructions:
                for source in context_sources(instruction):
                    read |= context_files(source)
        self.assertEqual(read, compiled | {"scripts/build-docker-helpers.sh"})


if __name__ == "__main__":
    unittest.main()
