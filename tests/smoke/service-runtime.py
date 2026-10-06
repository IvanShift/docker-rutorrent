import atexit, json, pathlib, subprocess, time, os, sys, uuid

repo = pathlib.Path(__file__).resolve().parents[2]
image = sys.argv[1]
name = 'smoke-services-runtime-' + str(os.getpid())
owner = str(uuid.uuid4())
image_id = subprocess.check_output(['docker','image','inspect',image,'--format','{{.Id}}'],text=True).strip()
image_version = subprocess.check_output(['docker','image','inspect',image,'--format','{{index .Config.Labels "io.github.ivanshift.rtorrent.version"}}'],text=True).strip()
if image_version.lstrip('v') != '0.16.25' or os.environ.get('SMOKE_EXPECTED_RTORRENT_VERSION',image_version).lstrip('v') != '0.16.25':
    print('ok - service runtime gate # SKIP requires explicit reviewed .25 version')
    sys.exit(0)
owned_id = ''

def docker(*args, check=True):
    result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=65)
    if check and result.returncode:
        raise RuntimeError(f'docker {args[:3]} status={result.returncode}: {result.stderr}{result.stdout}')
    return result.stdout.strip()

def rpc(*args):
    return docker('exec', '--user', '991:991', name, 'php85', '/tests/service-rpc.php', *args)

def wait(predicate, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if predicate(): return
        except RuntimeError:
            pass
        time.sleep(.25)
    raise RuntimeError('focused fixture deadline exceeded')

def health():
    return subprocess.run(['docker', 'exec', name, 'rutorrent-healthcheck'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0

def state():
    return json.loads(docker('exec', name, 'cat', '/config/nginx/.service-health.json'))

args=['run','--pull=never','-d','--name',name,'--label','smoke.services-runtime.owner='+owner,'--stop-timeout','60',
    '-e','RUTORRENT_WATCHDOG_INTERVAL_SECONDS=1','-e','RUTORRENT_WATCHDOG_START_SECONDS=0','-e','RUTORRENT_WATCHDOG_FAILURES=2',
    '--mount',f'type=bind,src={repo}/tests/smoke/service-runtime-rpc.php,dst=/tests/service-rpc.php,readonly',
    '--mount',f'type=bind,src={repo}/tests/smoke/fixtures/service-runtime/tracker.php,dst=/tests/service-tracker.php,readonly']
if len(sys.argv)>2:
    args += ['--mount',f'type=bind,src={pathlib.Path(sys.argv[2]).resolve()},dst=/candidate,readonly','--entrypoint','/bin/sh',image_id,'-c',
        'cp -r /candidate/. /; exec /usr/local/bin/startup /usr/local/libexec/service-runtime supervise /etc/s6.d']
else:args += [image_id]
owned_id=docker(*args)
def cleanup():
    try:
        info=json.loads(docker('inspect',name))[0]
        if info['Id']!=owned_id or info['Config']['Labels'].get('smoke.services-runtime.owner')!=owner:return
        if info['State']['Running']:docker('stop','--time','60',owned_id)
        docker('rm','-v',owned_id)
    except (RuntimeError, json.JSONDecodeError):pass
atexit.register(cleanup)
wait(health)
wait(lambda: 'plugin schedules registered' in docker('logs',name))
pid = rpc('system.pid')
hash_value = docker('exec', '--user', '991:991', name, 'php85', '-r', '$i="d6:lengthi1e4:name15:service-fixture12:piece lengthi16384e6:pieces20:".sha1("x",true)."e";file_put_contents("/data/service-fixture.torrent","d4:info".$i."e");file_put_contents("/data/downloads/service-fixture","x");echo strtoupper(sha1($i));')
rpc('load.normal', '', '/data/service-fixture.torrent')
rpc('d.custom1.set', hash_value, 'service-remaining-label')
print('ok - initial .25 runtime and label', flush=True)
original_health=docker('exec',name,'cat','/etc/nginx/healthcheck.php')
docker('exec',name,'php85','-r','file_put_contents("/etc/nginx/healthcheck.php",$argv[1]);',"<?php echo 'wrong';")
# This image deliberately caches PHP source for 60s; restart only FPM to make
# the substituted fixture bytes the actual HTTP response under observation.
docker('exec',name,'s6-svc','-r','/etc/s6.d/php85')
try:
    wait(lambda: docker('exec',name,'curl','--noproxy','*','-fsS','--max-time','2','http://127.0.0.1:8080/healthz')=='wrong')
    wait(lambda: state()['classification']=='http-unavailable')
    assert rpc('system.pid')==pid
    print('ok - HTTP200 with wrong body is visibly unhealthy without daemon reset',flush=True)
finally:
    docker('exec',name,'php85','-r','file_put_contents("/etc/nginx/healthcheck.php",$argv[1]);',original_health)
    docker('exec',name,'s6-svc','-r','/etc/s6.d/php85')
wait(health)

# A real blocking daemon command remains alive while SCGI probes time out.
blocked = subprocess.Popen(['docker','exec','--user','991:991',name,'php85','/tests/service-rpc.php','execute.capture','','/bin/sleep','12'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
wait(lambda: state()['classification'] == 'scgi-unresponsive-or-busy', seconds=10)
assert docker('inspect','-f','{{.State.Running}}',name) == 'true'
out, err = blocked.communicate(timeout=20)
assert blocked.returncode == 0, (out, err)
wait(health)
assert rpc('system.pid') == pid
assert rpc('d.custom1',hash_value) == 'service-remaining-label'
print('ok - actual 12-second synchronous daemon work is diagnosed and never killed', flush=True)

docker('exec',name,'kill','-STOP',pid)
wait(health, seconds=18)
assert rpc('system.pid') == pid
assert 'recovery=continued' in docker('logs',name)
print('ok - positively stopped daemon resumes through pidfd and preserves identity', flush=True)

# A second watchdog never probes or publishes over the first instance.
duplicate = docker('exec',name,'php85','/usr/local/libexec/service-watchdog.php')
assert duplicate == 'service-watchdog: monitor-already-running'
print('ok - duplicate watchdog excludes concurrent monitoring', flush=True)

before = docker('logs',name).count('plugin schedules registered')
for _ in range(2):
    helper = docker('exec',name,'s6-svstat','-o','pid','/etc/s6.d/logrotate')
    assert helper.isdigit() and int(helper) > 1, 'only a live owned helper PID may be signaled'
    docker('exec',name,'kill','-KILL',helper)
    def helper_restarted():
        current = docker('exec',name,'s6-svstat','-o','pid','/etc/s6.d/logrotate')
        return current.isdigit() and int(current) > 1 and current != helper
    wait(helper_restarted)
assert rpc('system.pid') == pid
assert docker('logs',name).count('plugin schedules registered') == before
print('ok - repeated logrotate failures restart only helper without schedule rearm', flush=True)

docker('exec',name,'s6-svc','-r','/etc/s6.d/rtorrent')
wait(health, seconds=30)
wait(lambda: rpc('system.pid') != pid, seconds=30)
wait(lambda: docker('logs',name).count('plugin schedules registered') == before + 1, seconds=20)
assert rpc('d.custom1',hash_value) == 'service-remaining-label'
pid = rpc('system.pid')
print('ok - s6 daemon restart saves label, preserves container and initializes schedules once', flush=True)

docker('exec','--user','991:991',name,'/usr/local/libexec/service-runtime','reopen','nginx')
docker('exec','--user','991:991',name,'/usr/local/libexec/service-runtime','reopen','php85')
print('ok - real nginx/FPM master log reopen through pidfd', flush=True)

docker('exec',name,'sh','-c','umask 027; printf "fixture:hash\\n" > /config/nginx/passwd/rutorrent_passwd.tmp.fixture; chmod 0640 /config/nginx/passwd/rutorrent_passwd.tmp.fixture')
docker('exec',name,'/usr/local/libexec/service-runtime','publish-auth','rutorrent_passwd.tmp.fixture')
assert docker('exec',name,'cat','/config/nginx/passwd/rutorrent_passwd') == 'fixture:hash'
assert docker('exec',name,'stat','-c','%u:%a:%h','/config/nginx/passwd/rutorrent_passwd') == '0:640:1'
print('ok - durable root password publication retains expected owner/mode/link contract', flush=True)

docker('exec','-d','--user','991:991',name,'php85','-S','127.0.0.1:19091','/tests/service-tracker.php')
tracker_hash=docker('exec','--user','991:991',name,'php85','-r','$i="d6:lengthi1e4:name15:service-tracker12:piece lengthi16384e6:pieces20:".sha1("x",true)."e";file_put_contents("/data/service-tracker.torrent","d8:announce".strlen($u="http://127.0.0.1:19091/announce").":".$u."4:info".$i."e");file_put_contents("/data/downloads/service-tracker","x");echo strtoupper(sha1($i));')
rpc('load.start','','/data/service-tracker.torrent')
def tracker_started():
    body=docker('exec',name,'cat','/data/services-announce-events.jsonl',check=False)
    return any(json.loads(line).get('event')=='started' for line in body.splitlines() if line)
wait(tracker_started)
docker('stop',name)
assert docker('inspect','-f','{{.State.ExitCode}}',name)=='0'
docker('start',name)
wait(health)
events=[json.loads(line) for line in docker('exec',name,'cat','/data/services-announce-events.jsonl').splitlines() if line]
assert any(event.get('event')=='stopped' and event.get('info_hash_hex')==tracker_hash.lower() for event in events)
assert rpc('d.custom1',hash_value)=='service-remaining-label'
pid=rpc('system.pid')
print('ok - normal bounded shutdown sends actual loopback stopped announce and preserves session',flush=True)

docker('exec',name,'kill','-KILL',pid)
wait(lambda: docker('inspect','-f','{{.State.Running}}',name) == 'false', seconds=30)
assert docker('inspect','-f','{{.State.ExitCode}}',name) == '1'
print('ok - unexpected primary daemon SIGKILL still exits whole stack1', flush=True)
print('ok - service runtime gate completed on exact image '+image_id,flush=True)
