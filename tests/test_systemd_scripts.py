import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform == 'win32', reason='Linux service scripts')
def test_scripts_use_existing_systemd_service(tmp_path):
    root = Path(__file__).resolve().parents[1]
    for name in ('start.sh', 'stop.sh', 'status.sh'):
        shutil.copy(root / name, tmp_path / name)
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/app.json').write_text(json.dumps({'port': 12345}))
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    commands = {
        'systemctl': '#!/bin/sh\nprintf "%s\\n" "$*" >> "$EVENTS"\nexit 0\n',
        'curl': '#!/bin/sh\nexit 0\n',
        'sudo': '#!/bin/sh\nshift\nexec "$@"\n',
    }
    for name, text in commands.items():
        path = bin_dir / name
        path.write_text(text)
        path.chmod(0o755)
    env = {**os.environ, 'PATH': str(bin_dir) + os.pathsep + os.environ['PATH'], 'PYTHON': sys.executable, 'EVENTS': str(tmp_path / 'events'), 'BDPAN_SYSTEMD_UNIT': 'test-pan.service'}
    for name in ('start.sh', 'status.sh', 'stop.sh'):
        result = subprocess.run(['bash', str(tmp_path / name)], env=env, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
    events = (tmp_path / 'events').read_text()
    assert 'start test-pan.service' in events
    assert 'stop test-pan.service' in events
    assert 'is-active --quiet test-pan.service' in events
