"""Move self into the allocated session cgroup before exec; no threaded preexec."""
import os
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
root = Path(os.environ.pop('WEB_AI_TASK_CGROUP'))
if path.parent != root or not re.fullmatch(r'task-[0-9a-f]{32}', path.name):
    raise RuntimeError('Invalid task cgroup')
(path / 'cgroup.procs').write_text(str(os.getpid()))
os.execvpe(sys.argv[2], sys.argv[2:], os.environ)
