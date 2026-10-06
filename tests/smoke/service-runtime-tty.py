import fcntl, json, os, pathlib, pty, select, signal, subprocess, sys, termios, time, uuid

candidate = len(sys.argv)>2
name = 'smoke-services-tty-' + str(os.getpid())
repo = pathlib.Path(__file__).resolve().parents[2]
root = pathlib.Path(sys.argv[2]).resolve() if candidate else None
owner = str(uuid.uuid4())
image = sys.argv[1]
image_version = subprocess.check_output(['docker','image','inspect',image,'--format','{{index .Config.Labels "io.github.ivanshift.rtorrent.version"}}'],text=True).strip()
if image_version.lstrip('v') != '0.16.25' or os.environ.get('SMOKE_EXPECTED_RTORRENT_VERSION',image_version).lstrip('v') != '0.16.25':
    print('ok - service PTY gate # SKIP requires explicit reviewed .25 version')
    sys.exit(0)
image = subprocess.check_output(['docker','image','inspect',image,'--format','{{.Id}}'],text=True).strip()
owned_id = ''

def docker(*args, check=True):
    r = subprocess.run(['docker',*args],capture_output=True,text=True,timeout=65)
    if check and r.returncode: raise RuntimeError(r.stdout+r.stderr)
    return r.stdout.strip()

def health():
    return subprocess.run(['docker','exec',name,'rutorrent-healthcheck'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode == 0

def rpc(*args):
    return docker('exec','--user','991:991',name,'php85','/tests/service-rpc.php',*args)

master, slave = pty.openpty()
def tty_child():
    os.setsid()
    fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

args=['docker','run','--pull=never','--name',name,'--label','smoke.services-tty.owner='+owner,'--stop-timeout','60','-it',
    '--mount',f'type=bind,src={repo}/tests/smoke/service-runtime-rpc.php,dst=/tests/service-rpc.php,readonly']
if candidate:
    args += ['--mount',f'type=bind,src={root},dst=/candidate,readonly','--entrypoint','/bin/sh',image,'-c',
        'cp -r /candidate/. /; exec /usr/local/bin/startup /usr/local/libexec/service-runtime supervise /etc/s6.d']
else: args += [image]
process=subprocess.Popen(args,stdin=slave,stdout=slave,stderr=slave,preexec_fn=tty_child,close_fds=True)
os.close(slave)
transcript=bytearray()
def pump():
    ready,_,_=select.select([master],[],[],.1)
    if ready:
        try: transcript.extend(os.read(master,65536))
        except OSError: pass

try:
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        pump()
        if not owned_id:
            result = subprocess.run(['docker','inspect',name],capture_output=True,text=True)
            if result.returncode == 0:
                initial = json.loads(result.stdout)[0]
                if initial['Config']['Labels'].get('smoke.services-tty.owner') == owner and initial['Image'] == image:
                    owned_id = initial['Id']
        if health(): break
        if process.poll() is not None: raise RuntimeError('TTY fixture exited before readiness')
    else: raise RuntimeError('TTY readiness deadline')
    info=json.loads(docker('inspect',name))[0]
    owned_id=info['Id']
    hash_value=docker('exec','--user','991:991',name,'php85','-r','$i="d6:lengthi1e4:name15:service-fixture12:piece lengthi16384e6:pieces20:".sha1("x",true)."e";file_put_contents("/data/service-fixture.torrent","d4:info".$i."e");file_put_contents("/data/downloads/service-fixture","x");echo strtoupper(sha1($i));')
    rpc('load.normal','','/data/service-fixture.torrent')
    rpc('d.custom1.set',hash_value,'tty-remaining-label')
    before=time.monotonic()
    os.write(master,b'\x03')
    deadline=before+30
    while time.monotonic()<deadline:
        pump()
        if docker('inspect','-f','{{.State.Running}}',name)=='false': break
    else: raise RuntimeError('terminal Ctrl-C did not finish')
    elapsed=time.monotonic()-before
    status=docker('inspect','-f','{{.State.ExitCode}}',name)
    print(f'TTY Ctrl-C candidate={candidate} exit={status} elapsed={elapsed:.3f}s',flush=True)
    assert status=='0', 'interactive shutdown must exit cleanly'
    process.wait(timeout=5)
    docker('start',name)
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        if health(): break
        time.sleep(.2)
    else: raise RuntimeError('TTY restart admission/readiness failed')
    assert rpc('d.custom1',hash_value)=='tty-remaining-label', 'terminal shutdown lost session label'
    print('ok - real PTY Ctrl-C saves session and restart restores exact label',flush=True)
finally:
    try:
        info=json.loads(docker('inspect',name))[0]
        if info['Id']==owned_id and info['Config']['Labels'].get('smoke.services-tty.owner')==owner:
            if info['State']['Running']:docker('stop','--time','60',owned_id)
            docker('rm','-v',owned_id)
    except (RuntimeError,json.JSONDecodeError):pass
    os.close(master)
    if process.poll() is None:
        process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired: process.kill();process.wait(timeout=5)
