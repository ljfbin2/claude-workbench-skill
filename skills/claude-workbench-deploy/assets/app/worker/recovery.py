"""Fail-closed provenance for manual acknowledgement after a worker restart."""
import hashlib
import json
import os
import pwd
import re
import stat
import subprocess
from pathlib import Path


def provenance():
    """Return a trusted service identity, or None when it cannot be established."""
    try:
        unit = os.environ.get('WEB_AI_WORKER_UNIT', '')
        invocation = os.environ.get('INVOCATION_ID', '')
        if not re.fullmatch(r'[A-Za-z0-9_.@:-]+\.service', unit):
            return None
        if not re.fullmatch(r'[0-9a-f]{32}', invocation) or os.getuid() == 0:
            return None
        names = ('Id', 'MainPID', 'ControlGroup', 'InvocationID', 'KillMode',
                 'Delegate', 'NoNewPrivileges', 'SendSIGKILL', 'ProtectControlGroups',
                 'User', 'NeedDaemonReload', 'FragmentPath', 'DropInPaths', 'Restart')
        proc = subprocess.run(['/usr/bin/systemctl', 'show', unit] +
                              ['--property=' + name for name in names],
                              capture_output=True, text=True, timeout=3,
                              env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
        if proc.returncode:
            return None
        props = dict(line.split('=', 1) for line in proc.stdout.splitlines() if '=' in line)
        fixed = {'Id': unit, 'MainPID': str(os.getpid()), 'InvocationID': invocation,
                 'KillMode': 'control-group', 'Delegate': 'yes', 'NoNewPrivileges': 'yes',
                 'SendSIGKILL': 'yes', 'ProtectControlGroups': 'no',
                 'NeedDaemonReload': 'no', 'DropInPaths': '', 'Restart': 'on-failure'}
        if any(props.get(key) != value for key, value in fixed.items()):
            return None
        user = props.get('User', '')
        uid = int(user) if user.isdecimal() else pwd.getpwnam(user).pw_uid
        if uid != os.getuid() or os.geteuid() != uid:
            return None
        status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
        if int(status['CapEff'].strip(), 16) or int(status['CapPrm'].strip(), 16):
            return None
        group = props.get('ControlGroup', '')
        if not group.startswith('/') or '..' in Path(group).parts:
            return None
        groups = Path('/proc/self/cgroup').read_text().splitlines()
        if '0::' + group not in groups:
            return None
        # Only the delegated service subtree is writable; ancestors remain protected.
        mount = Path('/sys/fs/cgroup')
        current = mount / group.lstrip('/')
        if not current.is_dir() or not (mount / 'cgroup.controllers').exists():
            return None
        while True:
            procs = current / 'cgroup.procs'
            if not procs.is_file() or (current != mount / group.lstrip('/') and os.access(procs, os.W_OK)):
                return None
            if current == mount:
                break
            current = current.parent
        # Only PID1's root-owned unit, with root-owned/non-writable ancestors, is trusted.
        fragment = Path(props.get('FragmentPath', ''))
        if not fragment.is_absolute() or fragment.resolve(strict=True) != fragment:
            return None
        current = fragment
        while True:
            st = current.lstat()
            if st.st_uid != 0 or st.st_mode & 0o022 or stat.S_ISLNK(st.st_mode):
                return None
            if current == fragment and not stat.S_ISREG(st.st_mode):
                return None
            if current == Path('/'):
                break
            current = current.parent
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        if not re.fullmatch(r'[0-9a-f-]{36}', boot):
            return None
        stable = {key: props[key] for key in ('Id', 'KillMode', 'Delegate', 'NoNewPrivileges',
                  'SendSIGKILL', 'ProtectControlGroups', 'User', 'FragmentPath', 'Restart')}
        stable['fragment_sha256'] = hashlib.sha256(fragment.read_bytes()).hexdigest()
        digest = hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()
        return {'unit': unit, 'invocation': invocation, 'boot': boot, 'config': digest}
    except (OSError, ValueError, KeyError, LookupError, subprocess.TimeoutExpired):
        return None
