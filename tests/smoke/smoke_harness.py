"""Shared Docker helpers for the Python smoke harnesses in this directory.

The harnesses run as scripts, so this module is importable from their own
directory. Nothing here talks to Docker at import time: the version gates of
service-runtime.py and service-runtime-tty.py must stay metadata-only.
"""
import json
import os
import subprocess
import sys
import time
import uuid

SERVICE_USER = '991:991'
REVIEWED_NATIVE_VERSION = '0.16.27'
# The one owner-label scheme of every smoke container and network (see lib.sh).
OWNER_LABEL = 'io.ivanshift.smoke.owner'


def owner_token(harness):
    """A unique token of this run, stored under OWNER_LABEL."""
    return f'{harness}-{uuid.uuid4()}'


def docker(*args, check=True, timeout=65):
    """Run one Docker CLI command and return its stripped output.

    `docker logs` replays the container's stderr on stderr, so logs return both
    streams. A failed command raises RuntimeError unless check is false.
    """
    result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f'docker {args[:3]} status={result.returncode}: {result.stderr}{result.stdout}')
    return (result.stdout + (result.stderr if args and args[0] == 'logs' else '')).strip()


def poll(predicate, seconds=40, interval=.25):
    """Return whether predicate() became true before the deadline.

    A failed Docker command or a partially written JSON document counts as
    "not yet"; any other exception is a harness defect and propagates.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except (RuntimeError, ValueError, OSError):
            pass
        time.sleep(interval)
    return False


def wait(predicate, seconds=20, message='focused fixture deadline exceeded'):
    """Like poll(), but a missed deadline fails the harness."""
    if not poll(predicate, seconds):
        raise RuntimeError(message)


def require_reviewed_native(image, gate):
    """Skip a gate written for the reviewed native daemon using image metadata only."""
    version = docker('image', 'inspect', image, '--format',
                     '{{index .Config.Labels "io.github.ivanshift.rtorrent.version"}}')
    expected = os.environ.get('SMOKE_EXPECTED_RTORRENT_VERSION') or version
    if version.lstrip('v') != REVIEWED_NATIVE_VERSION or expected.lstrip('v') != REVIEWED_NATIVE_VERSION:
        print(f'ok - {gate} # SKIP requires explicit reviewed .27 version', flush=True)
        sys.exit(0)


def healthy(container):
    """Ask the image's own readiness command, without Docker's health interval."""
    return subprocess.run(['docker', 'exec', container, 'rutorrent-healthcheck'],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def rpc(container, *args):
    """Call the daemon through the shared SCGI client with the 30-second deadline."""
    return docker('exec', '--user', SERVICE_USER, container, 'php85', '/tests/smoke/service-runtime-rpc.php', *args)


def torrent_fixture(container, *args):
    """Create a fixture with the shared generator; returns its uppercase info hash."""
    return docker('exec', '--user', SERVICE_USER, container, 'php85', '/tests/smoke/torrent-fixture.php', *args)


def watchdog_state(container):
    return json.loads(docker('exec', container, 'cat', '/run/rutorrent-watchdog/.service-health.json'))


def watchdog_resumes_stopped_daemon(container, seconds=18):
    """SIGSTOP the real daemon and require the watchdog to resume exactly it.

    The watchdog must log the pidfd recovery of this PID; the same process
    must stay the rTorrent service, answer SCGI and pass readiness again. The
    daemon is always continued afterwards, so a failed check cannot leave it
    stopped. The HTTP_AUTH mode of the container does not change the scenario.
    """
    pid = docker('exec', container, 's6-svstat', '-o', 'pid', '/etc/s6.d/rtorrent')
    if not (pid.isdigit() and int(pid) > 1):
        raise RuntimeError(f'no live rTorrent service process: {pid!r}')
    try:
        docker('exec', container, 'kill', '-STOP', pid)
        return (poll(lambda: f'recovery=continued pid={pid}' in docker('logs', container), seconds)
                and docker('exec', container, 's6-svstat', '-o', 'pid', '/etc/s6.d/rtorrent') == pid
                and poll(lambda: rpc(container, 'system.pid') == pid, 10)
                and poll(lambda: healthy(container), 10))
    finally:
        docker('exec', container, 'kill', '-CONT', pid, check=False)


def run_argv(owner, *args):
    """The docker run argv of one container labelled with this run's token.

    The Python harnesses start containers only through this function or
    start_container(), so the owner label is always in the starting argv.
    """
    return ['docker', 'run', '--pull=never', '--label', f'{OWNER_LABEL}={owner}', *args]


def start_container(owner, *args):
    """Start one detached labelled container (docker run -d ARGS); return its ID.

    docker run -d can create the container, print its ID and still fail to
    start it, before the caller has recorded any ID. The containers of this run
    are then removed by their label before the error propagates.
    """
    try:
        return docker(*run_argv(owner, '-d', *args)[1:])
    except BaseException as error:
        try:
            remove_run_containers(owner, stop=False)
        except RuntimeError as cleanup_error:
            raise RuntimeError(f'container start failed and its cleanup failed: {cleanup_error}') from error
        raise


def remove_run_containers(owner, stop=True):
    """Remove every container labelled with this run's token, recorded or not.

    Returns {container ID: names of its volume mounts} for what was removed.
    """
    removed = {}
    for owned_id in docker('ps', '-aq', '--no-trunc', '--filter', f'label={OWNER_LABEL}={owner}').split():
        volumes = remove_owned_container(owned_id, owned_id, owner, stop=stop)
        if volumes is not None:
            removed[owned_id] = volumes
    return removed


def remove_owned_container(container, owned_id, owner, stop=True):
    """Remove only the exact container this run created, with its anonymous volumes.

    Returns the names of all its volume mounts (docker rm -v removes only the
    anonymous ones among them), or None when the name now belongs to another
    container or the container is already gone.
    """
    if not owned_id:
        return None
    try:
        info = json.loads(docker('inspect', container))[0]
    except (RuntimeError, ValueError):
        return None
    if info['Id'] != owned_id or info['Config']['Labels'].get(OWNER_LABEL) != owner:
        return None
    if stop and info['State']['Running']:
        docker('stop', '--timeout', '60', owned_id)
    docker('rm', '-f', '-v', owned_id)
    return [mount['Name'] for mount in info['Mounts'] if mount['Type'] == 'volume']
