"""Host checks of the shipped watchdog loop with scripted probe results.

A copy of service-watchdog.php runs with its absolute helper paths pointed at
fakes and a virtual clock: PHP 8 lets a function removed by disable_functions
be redefined, so the auto-prepended fixture owns sleep(), time() and hrtime(),
reports the lock as root-owned and ends the loop after the last scripted step.
"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from host_php import requires_host_php

ROOT = Path(__file__).resolve().parents[2]
WATCHDOG = ROOT / "rootfs/usr/local/libexec/service-watchdog.php"
EPOCH = 1800000000
HEALTHY, HTTP, SCGI, STOPPED = (True, True, "S"), (True, False, "S"), (False, True, "S"), (False, False, "T")

# Each step is one observation: [SCGI ready, HTTP ok, /proc state of PID 4242]
# and an optional wall-clock offset that stays in force from that step on.
PREPEND = r"""<?php
$GLOBALS['fixture'] = ['dir' => getenv('WATCHDOG_FIXTURE'), 'step' => 0, 'elapsed' => 0, 'wall' => 0,
    'steps' => json_decode(getenv('WATCHDOG_STEPS'), true, 8, JSON_THROW_ON_ERROR)];
function fixture_observe(): void
{
    $fixture = $GLOBALS['fixture'];
    [$scgi, $http, $state] = $step = $fixture['steps'][$fixture['step']];
    if (isset($step[3])) $GLOBALS['fixture']['wall'] = $step[3];
    file_put_contents($fixture['dir'] . '/scgi', $scgi ? '0' : '124');
    file_put_contents($fixture['dir'] . '/http', $http ? "ok\n" : '');
    file_put_contents($fixture['dir'] . '/proc/4242/stat', "4242 (rtorrent main) $state 1 1 1 0 -1\n");
}
function sleep(int $seconds): int
{
    $GLOBALS['fixture']['elapsed'] += $seconds;
    if (++$GLOBALS['fixture']['step'] === count($GLOBALS['fixture']['steps'])) exit(0);
    fixture_observe();
    return 0;
}
function time(): int
{
    return EPOCH + $GLOBALS['fixture']['elapsed'] + $GLOBALS['fixture']['wall'];
}
function hrtime(bool $as_number = false): int
{
    return $GLOBALS['fixture']['elapsed'] * 1000000000;
}
// The monitor lock is created by this unprivileged test user.
function fstat($stream): array|false
{
    $stat = stat(stream_get_meta_data($stream)['uri']);
    if ($stat !== false) $stat['uid'] = $stat[4] = 0;
    return $stat;
}
fixture_observe();
""".replace("EPOCH", str(EPOCH))

FAKES = {
    "timeout": 'exit "$(cat "$fixture/scgi")"\n',
    "curl": '[ -s "$fixture/http" ] || exit 7\ncat "$fixture/http"\n',
    "su-exec": 'shift\nexec "$@"\n',
    "s6-svstat": "echo 4242\n",
    "service-runtime": (
        'case "$1" in\n'
        "  prepare-health) printf '%s\\n.service-health.json.tmp.\\n' \"$fixture/state\" ;;\n"
        '  publish-health) cat "$fixture/state/$2" >> "$fixture/published" ;;\n'
        "  resume-rtorrent) printf '%s\\n' \"$2\" >> \"$fixture/resumed\" ;;\n"
        "  *) exit 70 ;;\n"
        "esac\n"),
}
REWRITES = {
    "/usr/local/libexec/service-runtime": "service-runtime", "/usr/bin/curl": "curl",
    "/usr/bin/timeout": "timeout", "/sbin/su-exec": "su-exec", "/usr/bin/s6-svstat": "s6-svstat",
}


@requires_host_php("the watchdog loop", extensions=("ctype", "json"))
class ServiceWatchdogLoopTests(unittest.TestCase):
    def watch(self, steps, threshold=3, interval=15):
        """Run the loop over STEPS; return (published states, stdout lines, resumed PIDs)."""
        with tempfile.TemporaryDirectory(prefix="watchdog-") as scratch:
            fixture = Path(scratch)
            (fixture / "state").mkdir()
            (fixture / "proc/4242").mkdir(parents=True)
            source = WATCHDOG.read_text()
            for path, fake in REWRITES.items():
                self.assertIn(path, source, "the watchdog no longer calls " + path)
                source = source.replace(path, str(fixture / fake))
                (fixture / fake).write_text(f"#!/bin/sh\nfixture={scratch}\n" + FAKES[fake])
                (fixture / fake).chmod(0o755)
            self.assertIn('"/proc/$pid/stat"', source)
            (fixture / "watchdog.php").write_text(source.replace('"/proc/', f'"{scratch}/proc/'))
            (fixture / "prepend.php").write_text(PREPEND)
            result = subprocess.run(
                ["php", "-d", "disable_functions=sleep,time,hrtime,fstat",
                 "-d", "auto_prepend_file=" + str(fixture / "prepend.php"), str(fixture / "watchdog.php")],
                capture_output=True, text=True, timeout=60,
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "WATCHDOG_FIXTURE": scratch, "WATCHDOG_STEPS": json.dumps(steps),
                     "RUTORRENT_WATCHDOG_START_SECONDS": "0", "RUTORRENT_WATCHDOG_FAILURES": str(threshold),
                     "RUTORRENT_WATCHDOG_INTERVAL_SECONDS": str(interval)})
            self.assertEqual((result.returncode, result.stderr), (0, ""))
            published = (fixture / "published").read_text().splitlines()
            resumed = (fixture / "resumed").read_text().split() if (fixture / "resumed").exists() else []
            return [json.loads(line) for line in published], result.stdout.splitlines(), resumed

    def test_threshold_crossing_is_published_once_for_every_unhealthy_class(self):
        for step, name in ((HTTP, "http-unavailable"), (SCGI, "scgi-unresponsive-or-busy"), (STOPPED, "daemon-stopped")):
            with self.subTest(name):
                published, lines, _ = self.watch([HEALTHY] + [step] * 5)
                self.assertEqual([(state["classification"], state["consecutive_failures"]) for state in published],
                                 [("healthy", 0), (name, 1), (name, 3)])
                self.assertEqual([line for line in lines if "classification=" in line],
                                 [f"service-watchdog: classification={c} failures={n} pid=4242"
                                  for c, n in (("healthy", 0), (name, 1), (name, 3))])

    def test_only_a_positively_stopped_daemon_is_resumed(self):
        for step, recovery, resumed in ((HTTP, "deferred", []), (SCGI, "deferred", []),
                                        (STOPPED, "pidfd-sigcont-only", ["4242"])):
            with self.subTest(step):
                published, lines, actual = self.watch([step] * 5)
                self.assertEqual(actual, resumed)
                self.assertEqual({state["automatic_recovery"] for state in published}, {recovery})
                self.assertEqual([line for line in lines if "recovery=" in line],
                                 [f"service-watchdog: recovery=continued pid={pid}" for pid in resumed])

    def test_state_is_a_snapshot_of_the_publishing_observation(self):
        # Between a change, the threshold crossing and the five-minute refresh
        # the file keeps its observation although the live count grows.
        published, _, _ = self.watch([HEALTHY] + [HTTP] * 8, interval=100)
        self.assertEqual([(state["observed_at"] - EPOCH, state["consecutive_failures"]) for state in published],
                         [(0, 0), (100, 1), (300, 3), (600, 6)])
        published, _, _ = self.watch([HEALTHY] * 7, interval=100)
        self.assertEqual([state["observed_at"] - EPOCH for state in published], [0, 300, 600])

    def test_wall_clock_step_back_delays_neither_refresh_nor_repeated_resume(self):
        back = -3600
        published, _, _ = self.watch([HEALTHY] + [HEALTHY + (back,)] + [HEALTHY] * 5, interval=100)
        self.assertEqual([state["observed_at"] - EPOCH for state in published], [0, 300 + back, 600 + back])
        _, _, resumed = self.watch([STOPPED] + [STOPPED + (back,)] + [STOPPED] * 5, threshold=1, interval=100)
        self.assertEqual(resumed, ["4242"] * 3)


if __name__ == "__main__":
    unittest.main()
