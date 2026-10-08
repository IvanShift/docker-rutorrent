import fcntl, os, pathlib, pty, select, subprocess, sys, termios, time

import smoke_harness
from smoke_harness import docker

candidate = len(sys.argv)>2
name = 'smoke-services-tty-' + str(os.getpid())
repo = pathlib.Path(__file__).resolve().parents[2]
root = pathlib.Path(sys.argv[2]).resolve() if candidate else None
owner = smoke_harness.owner_token('service-runtime-tty')
image = sys.argv[1]
smoke_harness.require_reviewed_native(image, 'service PTY gate')
image = docker('image','inspect',image,'--format','{{.Id}}')

def health():
    return smoke_harness.healthy(name)

def rpc(*args):
    return smoke_harness.rpc(name, *args)

master, slave = pty.openpty()
def tty_child():
    os.setsid()
    fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

args=smoke_harness.run_argv(owner,'--name',name,'--stop-timeout','60','-it',
    '--mount',f'type=bind,src={repo}/tests/smoke,dst=/tests/smoke,readonly')
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
        if health(): break
        if process.poll() is not None: raise RuntimeError('TTY fixture exited before readiness')
    else: raise RuntimeError('TTY readiness deadline')
    hash_value=smoke_harness.torrent_fixture(name,'service-fixture','/data/service-fixture.torrent')
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
    # By label, so a container whose start or readiness failed is removed too.
    try:
        smoke_harness.remove_run_containers(owner)
    except RuntimeError as error:
        print(f'container cleanup failed: {error}', file=sys.stderr, flush=True)
    os.close(master)
    if process.poll() is None:
        process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired: process.kill();process.wait(timeout=5)
