"""Per-session cgroup v2 lifetime; no global task-count policy."""
import json
import os
import re
import signal
import sys
import time
from pathlib import Path


def base():
    configured = os.environ.get('WEB_AI_TASK_CGROUP')
    if not configured:
        if os.environ.get('WEB_AI_WORKER_UNIT'):
            raise RuntimeError('Independent task cgroups are not configured')
        return None
    path = Path(configured)
    actual = next(line[3:] for line in Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0::'))
    expected = Path('/sys/fs/cgroup') / actual.lstrip('/')
    if path != expected or not path.is_dir() or not os.access(path / 'cgroup.procs', os.W_OK):
        raise RuntimeError('Independent task cgroup is unavailable')
    return path


def allocate(worker, sid, identity):
    import uuid
    root = base()
    if root is None:
        return None
    name = 'task-' + uuid.uuid4().hex
    path = root / name
    path.mkdir(mode=0o700)
    with worker.db() as db:
        db.execute('INSERT OR REPLACE INTO execution_scopes VALUES (?,?,?)',
                   (sid, name, json.dumps(identity, sort_keys=True)))
    return path


def stored(worker, sid):
    root = base()
    if root is None:
        return None
    with worker.db() as db:
        row = db.execute('SELECT scope FROM execution_scopes WHERE session_id=?', (sid,)).fetchone()
    if not row or not re.fullmatch(r'task-[0-9a-f]{32}', row[0]):
        return None
    return root / row[0]


def released(path):
    if path is None:
        return False
    try:
        values = dict(line.split() for line in (path / 'cgroup.events').read_text().splitlines())
        return values.get('populated') == '0'
    except FileNotFoundError:
        return not path.exists()
    except (OSError, ValueError):
        return False


def kill(path):
    if path is None or not path.exists():
        return
    (path / 'cgroup.kill').write_text('1')


def cleanup(path):
    if path is None:
        return
    if not released(path):
        kill(path)
        until = time.monotonic() + 5
        while not released(path) and time.monotonic() < until:
            time.sleep(.05)
    if released(path) and path.exists():
        for child in sorted(path.rglob('*'), reverse=True):
            if child.is_dir():
                child.rmdir()
        path.rmdir()


def command(path, args):
    return [sys.executable, str(Path(__file__).with_name('task_runner.py')), str(path), *args] if path else args


def is_released(worker, sid, pgid, identity):
    path = stored(worker, sid)
    if path is not None:
        return released(path)
    from worker.receipts import group_released
    return not os.environ.get('WEB_AI_WORKER_UNIT') and group_released(pgid)
