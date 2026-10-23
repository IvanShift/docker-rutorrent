"""Host-only behavior of the shared smoke shell helpers in tests/smoke/lib.sh.

Docker, the clock and sleep are shell functions here, so every case is
deterministic and none of them starts a container.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "tests/smoke/lib.sh"
BOOTSTRAP = ROOT / "tests/smoke/bootstrap-lib.sh"
LEGACY = ROOT / "tests/smoke/legacy-098.sh"

FAKES = r"""
. "$LIB"
date() { cat "$WORK/clock"; }
sleep() { echo $(( $(cat "$WORK/clock") + 1 )) > "$WORK/clock"; echo sleep >> "$WORK/calls"; }
docker() {
  printf '%s\n' "$*" >> "$WORK/calls"
  case "$1" in
    exec)
      attempts=$(grep -c '^exec ' "$WORK/calls")
      [ "$SCENARIO" = ready-third ] && [ "$attempts" -ge 3 ]
    ;;
    inspect)
      case "$SCENARIO" in
        stopped) echo false ;;
        inspect-error) return 1 ;;
        owned|rm-fails) echo 'owned-id owner-1' ;;
        foreign-owner) echo 'owned-id owner-2' ;;
        foreign-id) echo 'other-id owner-1' ;;
        # Containers of this run: the ID is the name; Labels answers the owner.
        run-owned|run-foreign)
          case "$*" in
            *Labels*)
              if [ "$SCENARIO" = run-owned ]; then case "$*" in *"{{.Id}}"*) printf '%s %s\n' "$2" "$smoke_owner" ;; *) printf '%s\n' "$smoke_owner" ;; esac
              else printf '%s %s\n' "$2" another-run; fi
            ;;
            *) printf '%s\n' "$4" ;;
          esac
        ;;
        *) echo true ;;
      esac
    ;;
    image)
      [ "$SCENARIO" != image-error ] || return 1
      printf '%s\n' "${IMAGE_VERSION-}"
    ;;
    ps) [ "$SCENARIO" != run-owned ] || printf '%s\n' run-id-1 run-id-2 ;;
    logs) echo known-diagnostic ;;
    rm) [ "$SCENARIO" != rm-fails ] ;;
  esac
}
"""
LABEL = "io.ivanshift.smoke.owner"
# The reviewed native gate exists twice: smoke_require_reviewed_native in lib.sh
# and require_reviewed_native in smoke_harness.py (test_smoke_harness.py). Both
# run this one table: (image label, SMOKE_EXPECTED_RTORRENT_VERSION or None when
# unset, gate runs).
REVIEWED_NATIVE_CASES = (
    ("v0.16.26", None, True), ("0.16.26", "v0.16.26", True),
    # Empty means unset, as in the other shell harnesses: the label decides.
    ("v0.16.26", "", True), ("v0.16.25", "", False),
    ("v0.16.25", None, False), ("v0.16.24", None, False), ("v0.9.8", None, False), ("", None, False),
    # An inherited reviewed expectation never runs the gate on an older image.
    ("v0.16.25", "0.16.26", False), ("v0.9.8", "v0.16.26", False),
    ("v0.16.26", "0.9.8", False), ("v0.16.26", "v0.16.25", False),
)
REVIEWED_NATIVE_SKIP = "ok - fixture gate # SKIP requires explicit reviewed .26 version\n"


class SmokeLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="smoke-lib-")
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        (self.work / "clock").write_text("0\n")
        (self.work / "calls").write_text("")

    def run_sh(self, script, scenario="", *args, **variables):
        env = {"PATH": "/usr/bin:/bin", "LIB": str(LIB), "WORK": str(self.work), "SCENARIO": scenario, **variables}
        return subprocess.run(["sh", "-c", FAKES + script, "sh", *args],
                              capture_output=True, text=True, env=env, timeout=30)

    def calls(self):
        return (self.work / "calls").read_text().splitlines()

    def test_fail_diagnoses_only_a_container_owned_by_this_run(self):
        for scenario, diagnostics in (("inspect-error", False), ("run-foreign", False), ("run-owned", True)):
            with self.subTest(scenario=scenario):
                (self.work / "calls").write_text("")
                result = self.run_sh('container=named-container; fail early-refusal', scenario)
                self.assertEqual(result.returncode, 1)
                self.assertIn("not ok - early-refusal", result.stderr)
                self.assertEqual("known-diagnostic" in result.stderr, diagnostics)
                self.assertEqual(any(call.startswith("logs ") for call in self.calls()), diagnostics)
                self.assertIn(f'inspect --format {{{{index .Config.Labels "{LABEL}"}}}} named-container', self.calls())

    def test_libexec_overlay_mounts_scripts_one_file_at_a_time(self):
        source = self.work / "libexec"
        (source / "nested").mkdir(parents=True)
        for name in ("rtorrent-ready.php", "service-finish", "native-actor-paths.c", "nested/inner.php"):
            (source / name).write_text("fixture\n")
        result = self.run_sh('smoke_run_with_libexec "$1" --rm --name probe image cmd', "", str(source))
        self.assertEqual(result.returncode, 0, result.stderr)
        argv = self.calls()[0].split(" ")
        self.assertEqual(argv[:2], ["run", "--pull=never"])
        self.assertEqual(argv[-5:], ["--rm", "--name", "probe", "image", "cmd"])
        mounts = {argv[index + 1] for index, word in enumerate(argv) if word == "--mount"}
        self.assertEqual(mounts, {
            f"type=bind,src={source}/{name},dst=/usr/local/libexec/{name},readonly"
            for name in ("rtorrent-ready.php", "service-finish")
        })

    def test_empty_libexec_overlay_keeps_the_image_libexec(self):
        result = self.run_sh('smoke_run_with_libexec "" --rm image cmd')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(self.calls()[0], rf"\Arun --pull=never --label {LABEL}=sh-[0-9]+-[0-9]+ --rm image cmd\Z")

    def test_every_smoke_container_carries_this_runs_owner_label(self):
        result = self.run_sh('smoke_run -d image; smoke_create --rm image; printf "%s\\n" "$smoke_owner"')
        self.assertEqual(result.returncode, 0, result.stderr)
        token = result.stdout.strip()
        self.assertRegex(token, r"\Ash-[0-9]+-[0-9]+\Z")
        self.assertEqual(self.calls(), [f"run --pull=never --label {LABEL}={token} -d image",
                                        f"create --pull=never --label {LABEL}={token} --rm image"])

    def test_run_cleanup_removes_every_container_of_this_run_only(self):
        for mode, removal in (("", ["rm -f -v run-id-1", "rm -f -v run-id-2"]),
                              ("stop", ["stop --timeout 60 run-id-1", "rm -v run-id-1",
                                        "stop --timeout 60 run-id-2", "rm -v run-id-2"])):
            with self.subTest(mode=mode or "default"):
                (self.work / "calls").write_text("")
                result = self.run_sh(f'smoke_remove_run_containers {mode}; printf "%s\\n" "$smoke_owner"', "run-owned")
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = self.calls()
                self.assertEqual(calls[0], f"ps -aq --no-trunc --filter label={LABEL}={result.stdout.strip()}")
                self.assertEqual([call for call in calls if call.split()[0] in ("rm", "stop")], removal)
        (self.work / "calls").write_text("")
        result = self.run_sh("smoke_remove_container named-container", "run-foreign")
        self.assertEqual(result.returncode, 1)
        self.assertIn("refusing to remove container named-container: not the one this run created", result.stderr)
        self.assertFalse([call for call in self.calls() if call.split()[0] in ("rm", "stop")])
        (self.work / "calls").write_text("")
        result = self.run_sh("smoke_remove_container named-container", "run-owned")
        self.assertEqual((result.returncode, [c for c in self.calls() if c.startswith("rm")]),
                         (0, ["rm -f -v named-container"]))

    def test_reviewed_native_gate_skips_other_versions_from_metadata_only(self):
        script = 'smoke_require_reviewed_native fixture-image "fixture gate"; echo continued'
        inspect = 'image inspect fixture-image --format {{index .Config.Labels "io.github.ivanshift.rtorrent.version"}}'
        for label, expected, runs in REVIEWED_NATIVE_CASES:
            with self.subTest(label=label, expected=expected):
                (self.work / "calls").write_text("")
                variables = {"IMAGE_VERSION": label}
                if expected is not None:
                    variables["SMOKE_EXPECTED_RTORRENT_VERSION"] = expected
                result = self.run_sh(script, "", **variables)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "continued\n" if runs else REVIEWED_NATIVE_SKIP)
                self.assertEqual(self.calls(), [inspect])
        (self.work / "calls").write_text("")
        result = self.run_sh(script, "image-error", IMAGE_VERSION="v0.16.26")
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertIn("not ok - cannot read the rTorrent version label of fixture-image", result.stderr)
        self.assertEqual(self.calls(), [inspect])

    def test_missing_libexec_overlay_is_refused_before_docker(self):
        result = self.run_sh('smoke_run_with_libexec "$1" --rm image cmd', "", str(self.work / "absent"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("libexec overlay is not a directory", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_scgi_wait_retries_the_real_request_until_the_daemon_answers(self):
        result = self.run_sh("wait_for_scgi fake 10", "ready-third")
        self.assertEqual(result.returncode, 0, result.stderr)
        requests = [call for call in self.calls() if call.startswith("exec ")]
        self.assertEqual(len(requests), 3)
        for request in requests:
            # The runtime PHP version is pinned elsewhere (php_runtime_contract).
            self.assertRegex(request, r"\Aexec --user 991:991 fake php[0-9]+ /tests/smoke/service-rpc\.php system\.pid\Z")
        self.assertEqual(self.calls().count("sleep"), 2)

    def test_scgi_wait_stops_at_once_when_the_container_is_gone(self):
        for scenario in ("stopped", "inspect-error"):
            with self.subTest(scenario=scenario):
                (self.work / "calls").write_text("")
                result = self.run_sh("wait_for_scgi fake 10", scenario)
                self.assertEqual(result.returncode, 1)
                self.assertIn("container is not running", result.stderr)
                self.assertIn("known-diagnostic", result.stderr)
                self.assertNotIn("sleep", self.calls())

    def test_scgi_wait_is_bounded_by_its_deadline(self):
        result = self.run_sh("wait_for_scgi fake 10", "unready")
        self.assertEqual(result.returncode, 1)
        self.assertIn("SCGI readiness timed out after 10s", result.stderr)
        self.assertEqual((self.work / "clock").read_text().strip(), "10")

    def test_owned_cleanup_removes_only_the_exact_labelled_container(self):
        cases = {
            ("owned", "stop"): (0, ["stop --timeout 60 owned-id", "rm -v owned-id"]),
            ("owned", "force"): (0, ["rm -f -v owned-id"]),
            ("foreign-owner", "force"): (1, []),
            ("foreign-id", "stop"): (1, []),
            ("inspect-error", "force"): (0, []),
            ("rm-fails", "force"): (1, ["rm -f -v owned-id"]),
            ("rm-fails", "stop"): (1, ["stop --timeout 60 owned-id", "rm -v owned-id"]),
            ("owned", "sideways"): (64, []),
        }
        for (scenario, mode), (status, expected) in cases.items():
            with self.subTest(scenario=scenario, mode=mode):
                (self.work / "calls").write_text("")
                result = self.run_sh(f"smoke_remove_owned name owned-id {LABEL} owner-1 {mode}", scenario)
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertEqual([call for call in self.calls() if not call.startswith("inspect ")], expected)
                if scenario.startswith("foreign"):
                    self.assertIn("refusing to remove container name: not the one this run created", result.stderr)
        (self.work / "calls").write_text("")
        result = self.run_sh(f"smoke_remove_owned name '' {LABEL} owner-1", "owned")
        self.assertEqual((result.returncode, self.calls()), (0, []))
        # One owner-label scheme: another label key is refused before Docker.
        result = self.run_sh("smoke_remove_owned name owned-id smoke.other.owner owner-1", "owned")
        self.assertEqual((result.returncode, self.calls()), (64, []))
        self.assertIn(f"unknown smoke owner label smoke.other.owner; use {LABEL}", result.stderr)

    def test_rootfs_overlay_refuses_read_only_per_file_binds(self):
        both = {"ROOTFS_SOURCE": "/candidate", "LIBEXEC_SOURCE": "/libexec"}
        result = self.run_sh("smoke_rootfs_overlay_exclusive STARTUP_SOURCE LIBEXEC_SOURCE", **both)
        self.assertEqual(result.returncode, 64)
        self.assertIn("ROOTFS_SOURCE cannot be combined with LIBEXEC_SOURCE", result.stderr)
        for alone in ({"ROOTFS_SOURCE": "/candidate"}, {"LIBEXEC_SOURCE": "/libexec"}, {}):
            with self.subTest(variables=alone):
                self.assertEqual(self.run_sh("smoke_rootfs_overlay_exclusive LIBEXEC_SOURCE", **alone).returncode, 0)
        # Sourcing the bootstrap launcher refuses before any case can start.
        script = f'. "{BOOTSTRAP}"; echo sourced'
        refused = self.run_sh(script, **{"ROOTFS_SOURCE": "/candidate", "RTORRENT_RC_SOURCE": "/rc"})
        self.assertEqual((refused.returncode, refused.stdout), (64, ""))
        self.assertIn("ROOTFS_SOURCE cannot be combined with RTORRENT_RC_SOURCE", refused.stderr)
        self.assertEqual(self.run_sh(script, ROOTFS_SOURCE="/candidate").stdout, "sourced\n")

    def test_legacy_smoke_refuses_rootfs_with_bind_overlays_before_docker(self):
        fake = self.work / "bin"
        fake.mkdir()
        (fake / "docker").write_text(f'#!/bin/sh\necho "$*" >> "{self.work}/calls"\nexit 99\n')
        (fake / "docker").chmod(0o755)
        env = {"PATH": f"{fake}:/usr/bin:/bin", "ROOTFS_SOURCE": "/candidate", "HEALTHCHECK_SOURCE": "/healthcheck"}
        result = subprocess.run(["sh", str(LEGACY), "fixture-image"], capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("ROOTFS_SOURCE cannot be combined with HEALTHCHECK_SOURCE", result.stderr)
        self.assertEqual(self.calls(), [])

    def libexec_overlay(self):
        source = self.work / "libexec"
        source.mkdir()
        for name in ("rtorrent-ready.php", "service-finish", "native-actor-paths.c"):
            (source / name).write_text("fixture\n")
        return source

    def assert_libexec_mounted_per_file(self, call, source):
        argv = call.split(" ")
        mounts = {argv[index + 1] for index, word in enumerate(argv) if word == "--mount"}
        # A directory mount would hide the image's compiled helpers (R3-DK-2-02).
        self.assertFalse([mount for mount in mounts if ",dst=/usr/local/libexec," in mount], mounts)
        self.assertLessEqual({f"type=bind,src={source}/{name},dst=/usr/local/libexec/{name},readonly"
                              for name in ("rtorrent-ready.php", "service-finish")}, mounts)

    def test_bootstrap_cases_overlay_libexec_one_file_at_a_time(self):
        source = self.libexec_overlay()
        script = f'smoke_dir=/tests-smoke; . "{BOOTSTRAP}"; smoke_startup_case image "true"'
        result = self.run_sh(script, LIBEXEC_SOURCE=str(source))
        self.assertEqual(result.returncode, 0, result.stderr)
        runs = [call for call in self.calls() if call.startswith("run ")]
        self.assertEqual(len(runs), 1)
        self.assert_libexec_mounted_per_file(runs[0], source)

    def test_legacy_daemon_overlays_libexec_one_file_at_a_time(self):
        source = self.libexec_overlay()
        fake = self.work / "bin"
        fake.mkdir()
        # The version probe answers 0.9.8; the detached daemon start then fails,
        # which ends the script before any readiness wait.
        (fake / "docker").write_text(
            '#!/bin/sh\nprintf "%s\\n" "$*" >> "$WORK/calls"\n'
            'case " $* " in\n'
            '  *" --entrypoint rtorrent "*) echo "Rakshasa\'s BitTorrent client version 0.9.8." ;;\n'
            '  " run "*" -d "*) exit 1 ;;\n'
            'esac\n')
        (fake / "docker").chmod(0o755)
        env = {"PATH": f"{fake}:/usr/bin:/bin", "WORK": str(self.work), "LIBEXEC_SOURCE": str(source)}
        result = subprocess.run(["sh", str(LEGACY), "fixture-image"], capture_output=True, text=True, env=env, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        starts = [call for call in self.calls() if call.startswith("run ") and " -d " in f" {call} "]
        self.assertEqual(len(starts), 1, self.calls())
        self.assert_libexec_mounted_per_file(starts[0], source)


if __name__ == "__main__":
    unittest.main()
