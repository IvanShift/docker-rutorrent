"""A stalled Git fetch is bounded, retried finitely, and still SHA-checked."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(os.environ.get("SUPPLYCHAIN_TEST_ROOT", Path(__file__).resolve().parents[2]))


class FetchBounds(unittest.TestCase):
    def attempt(self, failures):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            binpath = base / "bin"
            binpath.mkdir()
            (binpath / "git").write_text('#!/usr/bin/env python3\n'
                                        'import json,os,pathlib,sys\n'
                                        'a=sys.argv[1:]; base=pathlib.Path(os.environ["TRACE_BASE"])\n'
                                        'if "init" in a: pathlib.Path(a[-1]).mkdir()\n'
                                        'if "fetch" in a:\n'
                                        ' p=base/"attempts"; n=int(p.read_text())+1 if p.exists() else 1; p.write_text(str(n))\n'
                                        ' with open(base/"git-trace","a") as f: f.write(json.dumps(a)+"\\n")\n'
                                        ' sys.exit(7 if n<=int(os.environ["FAILURES"]) else 0)\n'
                                        'if "rev-parse" in a: print("a"*40)\n')
            (binpath / "timeout").write_text('#!/usr/bin/env python3\n'
                                            'import json,os,pathlib,sys\n'
                                            'with open(pathlib.Path(os.environ["TRACE_BASE"])/"timeout-trace","a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")\n'
                                            'os.execvp(sys.argv[2],sys.argv[2:])\n')
            (binpath / "sleep").write_text('#!/bin/sh\nexit 0\n')
            for program in binpath.iterdir():
                program.chmod(0o755)
            env = dict(os.environ, PATH=str(binpath) + os.pathsep + os.environ["PATH"],
                       TRACE_BASE=str(base), FAILURES=str(failures))
            result = subprocess.run(["sh", str(ROOT / "scripts/fetch-build-source.sh"),
                                     "https://example.invalid/source.git", "master", "a" * 40, str(base / "source")],
                                    text=True, capture_output=True, env=env, timeout=10)
            attempts = int((base / "attempts").read_text())
            traces = [json.loads(line) for line in (base / "git-trace").read_text().splitlines()]
            bounds = ([json.loads(line) for line in (base / "timeout-trace").read_text().splitlines()]
                      if (base / "timeout-trace").exists() else [])
            revision = (base / "source/.source-revision").read_text().strip() if (base / "source/.source-revision").exists() else None
            return result, attempts, traces, bounds, revision

    def test_two_transient_failures_retry_then_preserve_the_expected_commit(self):
        result, attempts, traces, bounds, revision = self.attempt(2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(attempts, 3)
        self.assertEqual(revision, "a" * 40)
        self.assertEqual(len(bounds), 3)
        for bound in bounds:
            self.assertEqual(bound[0], "180")
        for trace in traces:
            self.assertIn("http.lowSpeedLimit=1000", trace)
            self.assertIn("http.lowSpeedTime=30", trace)

    def test_persistent_transfer_failure_stops_after_three_attempts(self):
        result, attempts, traces, bounds, revision = self.attempt(99)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(attempts, 3)
        self.assertEqual(len(bounds), 3)
        self.assertIsNone(revision)
        self.assertIn("source-fetch-failed attempt=3 status=7", result.stderr)


if __name__ == "__main__":
    unittest.main()
