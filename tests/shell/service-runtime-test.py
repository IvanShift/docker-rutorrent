#!/usr/bin/env python3
"""Compile the Docker helper and exercise real signals and a deterministic PID-reuse model."""
import os
import pathlib
import subprocess
import sys
import tempfile
import time

repo = pathlib.Path(__file__).resolve().parents[2] if 'tests/shell' in str(pathlib.Path(__file__).resolve()) else pathlib.Path('/home/dev/Documents/my_projects/docker-rutorrent')
source = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else repo / 'rootfs/usr/local/libexec/service-runtime.c'
mutant = len(sys.argv) > 2 and sys.argv[2] == 'numeric-signal-mutant'

with tempfile.TemporaryDirectory(prefix='service-runtime-test-') as temporary:
    work = pathlib.Path(temporary)
    candidate = source.read_text()
    if mutant:
        previous = 'int status = pidfd_signal(fd, SIGUSR1);'
        assert candidate.count(previous) == 1
        candidate = candidate.replace(previous, 'int status = kill((pid_t)number, SIGUSR1);')
    (work/'helper.c').write_text(candidate)
    subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror','-DSERVICE_RUNTIME_TEST_HOOK','-o',str(work/'helper'),str(work/'helper.c')],check=True)
    children = []
    def child(name):
        marker = work/name
        script = 'import signal,sys;signal.signal(signal.SIGUSR1,lambda *_:open(sys.argv[1],"w").write("usr1"));print("ready",flush=True);signal.pause();signal.pause()'
        process = subprocess.Popen([sys.executable,'-c',script,str(marker)],stdout=subprocess.PIPE,text=True)
        children.append(process)
        assert process.stdout.readline().strip() == 'ready'
        return process, marker
    def environment(pid):
        (work/'pid').write_text(str(pid)+'\n')
        return dict(os.environ, SERVICE_RUNTIME_TEST_PIDFILE=str(work/'pid'),
            SERVICE_RUNTIME_TEST_EXECUTABLE=sys.executable,SERVICE_RUNTIME_TEST_PREFIX=sys.executable)
    def wait(path):
        deadline = time.monotonic()+5
        while time.monotonic()<deadline:
            if path.exists(): return
            time.sleep(.005)
        raise AssertionError('fixture rendezvous exceeded deadline')
    try:
        original, marker = child('ordinary-signal')
        env = environment(original.pid)
        subprocess.run([str(work/'helper'),'reopen','nginx'],env=env,check=True)
        wait(marker)
        print('ok - real identity-bound USR1 signal',flush=True)
        marker.unlink()
        env['SERVICE_RUNTIME_TEST_PREFIX']='not-the-master '
        result=subprocess.run([str(work/'helper'),'reopen','nginx'],env=env,capture_output=True,text=True)
        assert result.returncode==1 and 'identity-mismatch' in result.stderr and not marker.exists()
        env['SERVICE_RUNTIME_TEST_PREFIX']=sys.executable
        for invalid in ['1\n','0\n','+123\n',' 123\n','123extra\n']:
            (work/'pid').write_text(invalid)
            assert subprocess.run([str(work/'helper'),'reopen','nginx'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==1
        print('ok - mismatched master and noncanonical PID refuse signaling',flush=True)
        original.terminate(); original.wait(timeout=5)
        original, marker = child('original-race')
        victim, victim_marker = child('replacement-victim')
        env=environment(original.pid)
        ready=work/'ready';resume=work/'resume';replacement=work/'replacement'
        env.update(SERVICE_RUNTIME_TEST_READY=str(ready),SERVICE_RUNTIME_TEST_RESUME=str(resume),SERVICE_RUNTIME_TEST_REPLACEMENT_FILE=str(replacement))
        helper=subprocess.Popen([str(work/'helper'),'reopen','nginx'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        wait(ready)
        original.terminate();original.wait(timeout=5)
        replacement.write_text(str(victim.pid)+'\n')
        resume.touch()
        output,error=helper.communicate(timeout=5)
        time.sleep(.05)
        assert helper.returncode==1 and not victim_marker.exists(), 'PID replacement victim received signal after original exit'
        assert victim.poll() is None
        print('ok - original exit and numeric PID replacement cannot redirect pidfd signal',flush=True)
    finally:
        for process in children:
            if process.poll() is None: process.terminate()
            process.wait(timeout=5)
