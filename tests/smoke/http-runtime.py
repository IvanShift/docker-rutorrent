#!/usr/bin/env python3
"""Real nginx/FPM/WebUI admission, framing, upload and watchdog regression gate.

Usage: python3 tests/smoke/http-runtime.py IMAGE [ROOTFS_OVERLAY]
The optional overlay is intermediate local evidence; final acceptance omits it.
Requires google-chrome (or CHROME_BIN) and Node with the WebSocket global.
"""
import argparse
import atexit
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('image')
parser.add_argument('overlay', nargs='?')
parser.add_argument('--scope', choices=['all', 'nginx', 'watchdog'], default='all')
options = parser.parse_args()
repo = pathlib.Path(__file__).resolve().parents[2]
image = options.image
owner = str(uuid.uuid4())
name = 'smoke-http-runtime-' + owner[:12]
network = name + '-net'
owned_id = ''
network_id = ''
chrome = None
work = tempfile.TemporaryDirectory(prefix='http-runtime-')
workspace = pathlib.Path(work.name)
errors = []


def docker(*args, check=True):
    result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=65)
    if check and result.returncode:
        raise RuntimeError(f'docker {args[:3]} status={result.returncode}: {result.stderr}{result.stdout}')
    return (result.stdout + (result.stderr if args and args[0] == 'logs' else '')).strip()


def cleanup():
    if chrome is not None and chrome.poll() is None:
        chrome.terminate()
        chrome.wait(timeout=10)
    if owned_id:
        try:
            info = json.loads(docker('inspect', name))[0]
            if info['Id'] == owned_id and info['Config']['Labels'].get('smoke.http-runtime.owner') == owner:
                docker('rm', '-f', '-v', owned_id)
                print(json.dumps({'removed_container': owned_id, 'removed_volumes': [mount['Name'] for mount in info['Mounts'] if mount['Type'] == 'volume']}), flush=True)
        except (RuntimeError, json.JSONDecodeError):
            pass
    if network_id:
        try:
            info = json.loads(docker('network', 'inspect', network))[0]
            if info['Id'] == network_id and info['Labels'].get('smoke.http-runtime.owner') == owner:
                docker('network', 'rm', network_id)
                print(json.dumps({'removed_network': network_id}), flush=True)
        except (RuntimeError, json.JSONDecodeError):
            pass
    work.cleanup()


atexit.register(cleanup)
image_id = docker('image', 'inspect', image, '--format', '{{.Id}}')
network_id = docker('network', 'create', '--internal', '--label', 'smoke.http-runtime.owner=' + owner, network)
args = ['run', '--pull=never', '-d', '--name', name, '--label', 'smoke.http-runtime.owner=' + owner,
        '--network', network, '--stop-timeout', '60',
        '-e', 'HTTP_AUTH=false', '-e', 'ALLOW_UNAUTHENTICATED_HTTP=true',
        '-e', 'RUTORRENT_WATCHDOG_INTERVAL_SECONDS=1', '-e', 'RUTORRENT_WATCHDOG_START_SECONDS=0',
        '-e', 'RUTORRENT_WATCHDOG_FAILURES=2',
        '--mount', f'type=bind,src={repo}/tests/smoke,dst=/tests/smoke,readonly']
if options.overlay:
    args += ['--mount', f'type=bind,src={pathlib.Path(options.overlay).resolve()},dst=/candidate,readonly',
             '--entrypoint', '/bin/sh', image_id, '-c',
             'cp -r /candidate/. /; exec /usr/local/bin/startup /usr/local/libexec/service-runtime supervise /etc/s6.d']
else:
    args += [image_id]
owned_id = docker(*args)
volume_names = [mount['Name'] for mount in json.loads(docker('inspect', name))[0]['Mounts'] if mount['Type'] == 'volume']
print(json.dumps({'image': image_id, 'container': owned_id, 'network': network_id, 'owner': owner, 'volumes': volume_names,
                  'overlay': options.overlay}), flush=True)


def wait(predicate, seconds=40):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except (RuntimeError, json.JSONDecodeError, OSError):
            pass
        time.sleep(.25)
    return False


def check(condition, message):
    print(('ok - ' if condition else 'not ok - ') + message, flush=True)
    if not condition:
        errors.append(message)


assert wait(lambda: docker('exec', name, 'curl', '--noproxy', '*', '-fsS', '--max-time', '2', 'http://127.0.0.1:8080/healthz', check=False) == 'ok'), (docker('logs', name), docker('exec', name, 'curl', '--noproxy', '*', '-sS', '-D', '-', '--max-time', '2', 'http://127.0.0.1:8080/healthz', check=False), docker('exec', name, 'cat', '/tmp/nginx-rutorrent-error.log', check=False))
address = docker('inspect', name, '--format', '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')
base = 'http://' + address + ':8080'


def request(path, *options):
    return docker('exec', name, 'curl', '--noproxy', '*', '-sS', '--max-time', '5', '-D', '-',
                  *options, 'http://127.0.0.1:8080' + path)


check('421' in request('/', '-H', 'Host: refused.example'), 'foreign Host receives 421')
check('HTTP_ALLOWED_HOSTS' in docker('exec', name, 'tail', '-n', '5', '/tmp/nginx-rutorrent-access.log'),
      'Host refusal names HTTP_ALLOWED_HOSTS in nginx log')
docker('exec', name, 'mkdir', '-p', '/rutorrent/app/plugins/http-runtime')
docker('exec', name, 'php85', '-r', 'file_put_contents($argv[1], $argv[2]);',
       '/rutorrent/app/plugins/http-runtime/deny.php', "<?php header('X-Frame-Options: DENY'); echo 'deny';")
for path, expected in [('/', 'SAMEORIGIN'), ('/php/getsettings.php', 'SAMEORIGIN'),
                       ('/plugins/http-runtime/deny.php', 'DENY'), ('/php/http-runtime-missing.php', 'SAMEORIGIN')]:
    headers = request(path).split('\n\n', 1)[0].lower()
    check(headers.count('x-frame-options:') == 1 and 'x-frame-options: ' + expected.lower() in headers,
          f'one frame header {expected} at {path}')

if options.scope == 'nginx':
    print(json.dumps({'failed': errors, 'image': image_id}), flush=True)
    sys.exit(bool(errors))

state_path = '/run/rutorrent-watchdog/.service-health.json'
check(wait(lambda: json.loads(docker('exec', name, 'cat', state_path))['classification'] == 'healthy', 8),
      'watchdog works with HTTP_AUTH=false and absent /config/nginx')
# A service-owned legacy nginx directory must not affect the root monitor.
docker('exec', name, 'sh', '-eu', '-c', 'mkdir -p /config/nginx; chown 991:991 /config/nginx; s6-svc -r /etc/s6.d/service-watchdog')
check(wait(lambda: json.loads(docker('exec', name, 'cat', state_path))['classification'] == 'healthy', 8),
      'watchdog ignores service-owned /config/nginx')

pid = docker('exec', name, 's6-svstat', '-o', 'pid', '/etc/s6.d/rtorrent')
assert pid.isdigit() and int(pid) > 1
try:
    docker('exec', name, 'kill', '-STOP', pid)
    recovered = wait(lambda: 'recovery=continued pid=' + pid in docker('logs', name), 14)
    check(recovered and docker('exec', name, 's6-svstat', '-o', 'pid', '/etc/s6.d/rtorrent') == pid,
          'unauthenticated-mode watchdog resumes only its positively stopped real daemon')
finally:
    docker('exec', name, 'kill', '-CONT', pid, check=False)

if options.scope == 'all':
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        browser_port = probe.getsockname()[1]
    chrome_binary = os.environ.get('CHROME_BIN') or shutil.which('google-chrome') or shutil.which('chromium')
    assert chrome_binary, 'CHROME_BIN or a local headless Chrome is required'
    chrome_log = (workspace / 'chrome.log').open('w')
    chrome = subprocess.Popen([chrome_binary, '--headless=new', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
                               '--no-proxy-server', '--no-first-run', '--disable-background-networking', '--remote-debugging-address=127.0.0.1',
                               f'--remote-debugging-port={browser_port}', '--user-data-dir=' + str(workspace / 'browser'),
                               'about:blank'], stdout=chrome_log, stderr=chrome_log)
    import urllib.request
    assert wait(lambda: urllib.request.urlopen(f'http://127.0.0.1:{browser_port}/json/version', timeout=1).status == 200, 10)
    result = subprocess.run(['node', str(repo / 'tests/smoke/http-runtime-ui.mjs'), str(browser_port), base, owner[:8]],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    browser = json.loads(result.stdout)
    print(json.dumps(browser, ensure_ascii=False), flush=True)
    check(browser['loadSummary']['ready'], 'real WebUI initialized')
    for field, phrase, count in [('good', 'successfully', 1), ('bad', 'not a valid torrent', 1),
                                 ('batch', 'successfully', 32), ('longBatch', 'not a valid torrent', 100)]:
        outcome = browser[field]['after']
        text = '\n'.join(outcome['logTail'])
        check(outcome['iframeDocument'].startswith('readable:') and text.lower().count(phrase) == count,
              f'real addtorrent {field}: {count} visible named results in readable iframe')
    actual = [docker('exec', '--user', '991:991', name, 'php85', '/tests/smoke/service-rpc.php', 'd.name', item['hash']) == item['name']
              for item in browser['expectedTorrents'] if '-batch-' in item['name']]
    check(len(actual) == 32 and all(actual),
          'all 32 Cyrillic-form uploads reached the real daemon')
    check(not any('Refused to display' in line for line in browser['console']), 'browser accepts same-origin result frames')
    check(not any('502 ' in line or '414 ' in line for line in browser['addResponses']), 'all upload result responses avoid 502 and 414')

# Stop during an actual blocked SCGI probe. Its child must not inherit the flock
# and permanently suppress the replacement monitor as 'already running'.
monitor = docker('exec', name, 's6-svstat', '-o', 'pid', '/etc/s6.d/service-watchdog')
if monitor.isdigit() and int(monitor) > 1:
    docker('exec', '-d', '--user', '991:991', name, 'php85', '/tests/smoke/service-runtime-rpc.php',
           'execute.capture', '', '/bin/sleep', '8')
    assert wait(lambda: docker('exec', name, 'pgrep', '-P', monitor, '-f', '/usr/local/libexec/rtorrent-ready.php', check=False), 5), 'blocked watchdog probe not observed'
# Publication failure must disable this monitor visibly without a fatal restart loop.
docker('exec', name, 's6-svc', '-d', '/etc/s6.d/service-watchdog')
assert wait(lambda: docker('exec', name, 's6-svstat', '-o', 'up', '/etc/s6.d/service-watchdog') == 'false', 5)
docker('exec', name, 'sh', '-eu', '-c', 'mkdir -p /run/rutorrent-watchdog; chmod 700 /run/rutorrent-watchdog; rm -f /run/rutorrent-watchdog/.service-health.json; '
       'printf sentinel > /tmp/http-runtime-do-not-touch; ln -s /tmp/http-runtime-do-not-touch /run/rutorrent-watchdog/.service-health.json; '
       's6-svc -u /etc/s6.d/service-watchdog')
wait(lambda: docker('exec', name, 's6-svstat', '-o', 'up', '/etc/s6.d/service-watchdog') == 'false', 8)
time.sleep(1)
log = docker('logs', name)
print('\n'.join(line for line in log.splitlines() if 'service-watchdog:' in line), flush=True)
check(log.count('service-watchdog: unavailable reason=state-publication-refused') == 1
      and 'PHP Fatal' not in log
      and docker('exec', name, 's6-svstat', '-o', 'up', '/etc/s6.d/service-watchdog') == 'false',
      'one classified publication refusal stops monitor without fatal loop')
check(docker('exec', name, 'cat', '/tmp/http-runtime-do-not-touch') == 'sentinel',
      'refused status publication preserves the symlink target')
docker('exec', name, 'sh', '-eu', '-c', 'mv /run/rutorrent-watchdog /run/rutorrent-watchdog.safe; '
       'mkdir /run/rutorrent-watchdog; chown 991:991 /run/rutorrent-watchdog; '
       's6-svc -u /etc/s6.d/service-watchdog')
time.sleep(3)
log = docker('logs', name)
print('\n'.join(line for line in log.splitlines() if 'service-watchdog:' in line), flush=True)
check(log.count('service-watchdog: unavailable reason=state-directory-unavailable') == 1
      and 'PHP Fatal' not in log
      and docker('exec', name, 's6-svstat', '-o', 'up', '/etc/s6.d/service-watchdog') == 'false',
      'unsafe monitor directory produces one classified refusal without restart loop')
print(json.dumps({'failed': errors, 'image': image_id}, ensure_ascii=False), flush=True)
sys.exit(bool(errors))
