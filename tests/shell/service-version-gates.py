#!/usr/bin/env python3
"""Version gates admit their reviewed targets and refuse others before startup."""
import json
import os
import pathlib
import subprocess
import tempfile

repo = pathlib.Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory(prefix='service-version-gates-') as temporary:
    work = pathlib.Path(temporary)
    docker = work/'docker'
    docker.write_text('''#!/usr/bin/env python3
import json,os,sys
with open(os.environ['GATE_TRACE'],'a') as output:output.write(json.dumps(sys.argv[1:])+'\\n')
if sys.argv[1:3] != ['image','inspect']:sys.exit(97)
if os.environ.get('GATE_INSPECT_FAIL'):sys.exit(42)
print(os.environ['GATE_IMAGE_VERSION'] if any('.Labels' in arg for arg in sys.argv) else 'sha256:metadata-only-fixture')
''')
    docker.chmod(0o755)

    def run_gate(entry, version, **extra):
        trace = work/'trace'
        trace.write_text('')
        env = dict(os.environ, PATH=str(work)+os.pathsep+os.environ['PATH'],
            TMPDIR=str(work), GATE_TRACE=str(trace), GATE_IMAGE_VERSION=version,
            SMOKE_EXPECTED_RTORRENT_VERSION='0.16.26', GATE_INSPECT_FAIL='')
        env.update(extra)
        runner = 'sh' if entry.endswith('.sh') else 'python3'
        result = subprocess.run([runner,str(repo/entry),'metadata-only-fixture'],env=env,capture_output=True,text=True,timeout=5)
        calls = [json.loads(line) for line in trace.read_text().splitlines()]
        return result, calls

    entries = ['tests/smoke/service-runtime.sh','tests/smoke/schedule-errors.sh',
        'tests/smoke/service-runtime.py','tests/smoke/service-runtime-tty.py']
    for version in ['v0.16.24','v0.16.25','v0.9.8']:
        for entry in entries:
            result, calls = run_gate(entry, version)
            assert result.returncode==0 and 'SKIP' in result.stdout,(entry,version,result.stdout,result.stderr)
            assert calls and all(call[:2]==['image','inspect'] for call in calls), 'old-native gate invoked Docker beyond metadata'
    print('ok - four service gates skip .24/.25/.98 using metadata only, even with inherited .26 expectation')

    # An outdated allowlist must not silently skip the public URL checks on the
    # new image. Stop at the first run request: no Docker daemon is contacted.
    public = 'tests/smoke/public-url.sh'
    for version, target in [('v0.16.26', 'native'), ('0.16.26', 'native'),
                            ('v0.9.8', 'legacy'), ('0.9.8', 'legacy'),
                            ('v0.16.25', 'skip'), ('v0.16.24', 'skip'), ('', 'skip')]:
        result, calls = run_gate(public, version)
        starts = [call for call in calls if call[:1] == ['run']]
        detail = (version, result.returncode, result.stdout, result.stderr, calls)
        if target == 'native':
            assert result.returncode == 97 and 'SKIP' not in result.stdout, detail
            assert len(starts) == 1 and '-d' in starts[0], detail
        elif target == 'legacy':
            # A legacy image reaches the application-contract probe; this fake
            # lacks that contract, so the existing historical-image skip applies.
            assert result.returncode == 0 and 'no new application contract' in result.stdout, detail
            assert len(starts) == 1 and '--rm' in starts[0] and '-d' not in starts[0], detail
        else:
            assert result.returncode == 0 and 'SKIP' in result.stdout, detail
            assert calls and all(call[:2] == ['image', 'inspect'] for call in calls), detail
        assert not [path for path in work.iterdir() if path.is_dir()], 'public URL gate left its workspace behind'
    print('ok - public URL gate admits .26/.98 and skips old or missing versions before startup')

    for entry in [*entries, public]:
        result, calls = run_gate(entry, 'v0.16.26', GATE_INSPECT_FAIL='1')
        assert result.returncode != 0 and 'SKIP' not in result.stdout, (entry, result.stdout, result.stderr)
        assert calls and all(call[:2] == ['image', 'inspect'] for call in calls), (entry, calls)
    print('ok - unavailable image metadata fails every gate before startup')
