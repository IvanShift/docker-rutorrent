#!/usr/bin/env python3
"""Old image metadata must never start any new native service gate."""
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
print(os.environ['GATE_IMAGE_VERSION'] if any('.Labels' in arg for arg in sys.argv) else 'sha256:metadata-only-fixture')
''')
    docker.chmod(0o755)
    entries = ['tests/smoke/service-runtime.sh','tests/smoke/schedule-errors.sh',
        'tests/smoke/service-runtime.py','tests/smoke/service-runtime-tty.py']
    for version in ['v0.16.24','v0.9.8']:
        for entry in entries:
            trace = work/'trace'
            trace.write_text('')
            env = dict(os.environ,PATH=str(work)+os.pathsep+os.environ['PATH'],
                GATE_TRACE=str(trace),GATE_IMAGE_VERSION=version,SMOKE_EXPECTED_RTORRENT_VERSION='0.16.25')
            runner = 'sh' if entry.endswith('.sh') else 'python3'
            result = subprocess.run([runner,str(repo/entry),'metadata-only-fixture'],env=env,capture_output=True,text=True,timeout=5)
            assert result.returncode==0 and 'SKIP' in result.stdout,(entry,version,result.stdout,result.stderr)
            calls = [json.loads(line) for line in trace.read_text().splitlines()]
            assert calls and all(call[:2]==['image','inspect'] for call in calls), 'old-native gate invoked Docker beyond metadata'
    print('ok - four service gates skip .24/.98 using metadata only, even with inherited .25 expectation')
