import shutil
"""On-demand official usage, with passive reads and a single background probe."""
import datetime as dt
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import tempfile
import threading
import time
from worker import quota, usage_report, quota_store

NODE = os.environ.get('WEB_AI_QUOTA_NODE') or shutil.which('node') or '/unavailable/node'
PROBE = str(Path(__file__).with_name('quota_probe.mjs'))
PROBE_TIMEOUT = 15
MIN_INTERVAL = 10
OFFICIAL_CACHE_MARGIN = 65
_LOCK = threading.Lock()
_CACHE = {}

def _unknown():
    return {name: quota._empty() for name in quota.WINDOWS}

def _current(window):
    value = dict(window)
    if value.get('state') == 'known' and value.get('resets_at') is not None and value['resets_at'] <= time.time():
        value.update(state='expired', used_percent=None, remaining_percent=None)
    return value

def _observed_epoch(item):
    stamp = item.get('updated_at')
    if not isinstance(stamp, str):
        return 0
    try:
        value = dt.datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        return value.timestamp() if value.tzinfo else 0
    except (ValueError, OverflowError, OSError):
        return 0

def _entry(key, identity):
    entry = _CACHE.get(key)
    if entry is None or entry['identity'] != identity:
        entry = {'identity': identity, 'status': 'idle', 'reason': None,
                 'windows': quota_store.load(key,identity), 'id': 0, 'last_started': None, 'warning': None}
        _CACHE[key] = entry
    return entry

def snapshot(worker):
    before = quota._credential_state(worker)
    base = quota.snapshot(worker)
    identity = quota._credential_state(worker)
    key = str(worker.db_path)
    if identity is None or identity != before:
        with _LOCK:
            _CACHE.pop(key, None)
        base['windows'] = _unknown()
        base.update(refresh_status='idle', refresh_pending=False, refresh_reason=None, refresh_id=0)
        return base
    with _LOCK:
        entry = _entry(key, identity)
        cached = {k: dict(v) for k,v in (entry['windows'] or {}).items()}
        status, reason, ident = entry['status'], entry['reason'], entry['id'];warning=entry.get('warning')
    for name in quota.WINDOWS:
        if name not in cached:
            continue
        official = _current(cached[name])
        event = base['windows'][name]
        # Query time is not source time: upstream may return an older cache.
        # An event observed within that uncertainty window takes precedence.
        if official.get('state') == 'known':
            if event.get('state') != 'known' or _observed_epoch(event) < _observed_epoch(official) - OFFICIAL_CACHE_MARGIN:
                base['windows'][name] = official
        elif event.get('state') != 'known':
            base['windows'][name] = official
    if quota._credential_state(worker) != identity:
        with _LOCK:
            _CACHE.pop(key, None)
        base['windows'] = _unknown()
        status, reason, ident = 'idle', None, 0;warning=None
    for item in base['windows'].values():
        item['stale']=item.get('state')=='known' and time.time()-_observed_epoch(item)>300
    base.update(refresh_status=status, refresh_pending=status == 'pending', refresh_reason=reason, refresh_id=ident, refresh_warning=warning)
    return base

def _set_status(key, identity, status, reason):
    with _LOCK:
        entry = _entry(key, identity)
        # Do not overwrite a request that has meanwhile started.
        if entry['status'] != 'pending':
            entry['id'] += 1
            entry['status'], entry['reason'] = status, reason

def _refresh(worker, key, identity, request_id):
    result, reason = None, '\u5b98\u65b9\u7528\u91cf\u67e5\u8be2\u5931\u8d25\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5'
    try:
        env_keys = ('HOME', 'PATH', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME', 'LANG', 'LC_ALL', 'TZ')
        env = {k: worker.environment[k] for k in env_keys if k in worker.environment}
        for key in ('WEB_AI_JS_SDK_DIR', 'WEB_AI_QUOTA_SDK_VERSION', 'WEB_AI_QUOTA_CLI_VERSION'):
            if os.environ.get(key):
                env[key] = os.environ[key]
        try:
            with tempfile.TemporaryDirectory(prefix='quota-probe-', dir=worker.db_path.parent) as cwd:
                proc = subprocess.Popen([NODE, PROBE, worker.claude], cwd=cwd, env=env,
                                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, start_new_session=True)
                try:
                    out, _ = proc.communicate(timeout=PROBE_TIMEOUT)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    try:
                        proc.communicate(timeout=1)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        if proc.stdout:
                            proc.stdout.close()
                    reason = '\u5b98\u65b9\u7528\u91cf\u67e5\u8be2\u8d85\u65f6\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5'
                    raise
                if proc.returncode or len(out) > 65536:
                    raise ValueError('quota probe failed')
                payload = json.loads(out)
                rate = payload.get('rate_limits') if isinstance(payload, dict) else None
                if not (isinstance(payload, dict) and payload.get('rate_limits_available') is True and
                        isinstance(rate, dict) and isinstance(rate.get('limits'), list) and len(rate['limits']) <= 64):
                    raise ValueError('usage report unavailable')
                observed = dt.datetime.now(dt.timezone.utc).isoformat()
                result = usage_report.normalise(payload, observed)['windows']
                if not any(window.get('state') == 'known' for window in result.values()):
                    raise ValueError('no valid official limits')
                for window in result.values():
                    window['timestamp_kind'] = 'queried'
        except (OSError, ValueError, TypeError, subprocess.SubprocessError):
            result = None
        current_identity = quota._credential_state(worker)
        with _LOCK:
            entry = _CACHE.get(key)
            if entry is not None and entry['identity'] == identity and entry['id'] == request_id:
                if current_identity != identity:
                    _CACHE.pop(key, None)
                elif result is None:
                    entry['status'], entry['reason'] = 'failed', reason
                else:
                    partial = any(v.get('state') != 'known' for v in result.values())
                    merged = {k: dict(v) for k,v in (entry['windows'] or {}).items()}
                    for name, window in result.items():
                        if window.get('state') == 'known' or name not in merged:
                            merged[name] = window
                    entry['windows'], entry['status'], entry['reason'] = merged, 'success', None
                    try:
                        quota_store.save(key,identity,merged);entry['warning']='\u90e8\u5206\u7a97\u53e3\u672a\u8fd4\u56de\u65b0\u8bfb\u6570\uff0c\u4fdd\u7559\u4e0a\u6b21\u6570\u636e' if partial else None
                    except OSError:
                        entry['warning']='\u65e0\u6cd5\u4fdd\u5b58\u989d\u5ea6\u7f13\u5b58\uff0c\u672c\u6b21\u67e5\u8be2\u7ed3\u679c\u4ecd\u6709\u6548'
    finally:
        with worker.mutex:
            worker._quota_probe_active = False
            worker._capability_probe_active = False
        worker._model_lock.release()
        # A task accepted while this quota request held the execution lock
        # remains durable in the queue until the probe has exited.
        worker._dispatch_global()

def refresh(worker):
    key = str(worker.db_path)
    identity = quota._credential_state(worker)
    if identity is None:
        result = snapshot(worker)
        result.update(refresh_status='failed', refresh_pending=False,
                      refresh_reason='\u5f53\u524d\u8d26\u53f7\u4e0d\u53ef\u7528', refresh_id=0)
        return result
    if not worker.claude:
        _set_status(key, identity, 'failed', '\u5f53\u524d\u8d26\u53f7\u4e0d\u53ef\u7528')
        return snapshot(worker)
    with _LOCK:
        entry = _entry(key, identity)
        pending = entry['status'] == 'pending'
        limited = entry['last_started'] is not None and time.monotonic() - entry['last_started'] < MIN_INTERVAL
    if pending:
        return snapshot(worker)
    if limited:
        _set_status(key, identity, 'busy', '\u67e5\u8be2\u8fc7\u4e8e\u9891\u7e41\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5')
        return snapshot(worker)
    if not worker._model_lock.acquire(blocking=False):
        if not getattr(worker, '_quota_probe_active', False):
            _set_status(key, identity, 'busy', '\u6267\u884c\u670d\u52a1\u6b63\u5fd9\uff0c\u8bf7\u7a0d\u540e\u67e5\u8be2')
        return snapshot(worker)
    reserved = False
    started = False
    try:
        with worker.mutex:
            try:
                with worker.db() as db:
                    active = db.execute("SELECT 1 FROM sessions WHERE status IN ('running','stopping','unknown') LIMIT 1").fetchone()
                    queued = db.execute('SELECT 1 FROM pending_turns LIMIT 1').fetchone()
            except sqlite3.Error:
                _set_status(key, identity, 'failed', '\u65e0\u6cd5\u8bfb\u53d6\u6267\u884c\u72b6\u6001')
                return snapshot(worker)
            if active or queued or worker._capability_probe_active:
                _set_status(key, identity, 'busy', '\u4efb\u52a1\u8fd0\u884c\u3001\u6392\u961f\u6216\u72b6\u6001\u672a\u77e5\uff0c\u6682\u4e0d\u80fd\u67e5\u8be2')
                return snapshot(worker)
            with _LOCK:
                entry = _entry(key, identity)
                entry['id'] += 1
                request_id = entry['id']
                entry['status'], entry['reason'] = 'pending', None
                entry['last_started'] = time.monotonic()
            worker._capability_probe_active = True
            worker._quota_probe_active = True
            reserved = True
        try:
            threading.Thread(target=_refresh, args=(worker, key, identity, request_id), daemon=True).start()
        except RuntimeError:
            with _LOCK:
                entry = _CACHE.get(key)
                if entry is not None and entry['identity'] == identity and entry['id'] == request_id:
                    entry['status'], entry['reason'] = 'failed', '\u67e5\u8be2\u65e0\u6cd5\u542f\u52a8'
            return snapshot(worker)
        started = True
        return snapshot(worker)
    finally:
        if not started:
            if reserved:
                with worker.mutex:
                    worker._quota_probe_active = False
                    worker._capability_probe_active = False
            worker._model_lock.release()
            if reserved:
                worker._dispatch_global()
