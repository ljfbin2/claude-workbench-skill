"""Summarise only explicit Claude plan-limit observations; never query a model."""
import datetime as dt
import json
import math
import re
import sqlite3
import stat
from pathlib import Path
import threading
import time

WINDOWS = ('five_hour', 'seven_day', 'fable')
MAX_EVENT_BYTES = 32768
RESULT_LIMIT = 64
CACHE_SECONDS = 5
_CACHE_LOCK = threading.Lock()
_CACHE = {}
_GENERATION_LOCK = threading.Lock()
_GENERATION = {}
MIN_EPOCH = 946684800
MAX_EPOCH = 4102444800


def _number(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except (OverflowError, ValueError):
        return None
    return value if math.isfinite(value) and low <= value <= high else None


def _epoch(value):
    if isinstance(value, str):
        try:
            parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                return None
            value = parsed.timestamp()
        except (ValueError, OverflowError, OSError):
            return None
    number = _number(value, MIN_EPOCH, MAX_EPOCH)
    return int(number) if number is not None else None


def _observed(value, now_epoch):
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            return None
        timestamp = parsed.timestamp()
        if not (MIN_EPOCH <= timestamp <= now_epoch + 60):
            return None
        return parsed.astimezone(dt.timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def _empty():
    return {'used_percent': None, 'remaining_percent': None, 'resets_at': None,
            'updated_at': None, 'state': 'unknown'}


def _window(used, reset, observed, now_epoch):
    reset = _epoch(reset)
    observed = _observed(observed, now_epoch)
    used = _number(used, 0, 100)
    if reset is None or observed is None or used is None:
        return None
    if reset <= now_epoch:
        return {'used_percent': None, 'remaining_percent': None, 'resets_at': reset,
                'updated_at': observed, 'state': 'expired'}
    return {'used_percent': used, 'remaining_percent': max(0.0, 100.0 - used),
            'resets_at': reset, 'updated_at': observed, 'state': 'known'}


def _unscoped(scope):
    if scope is None:
        return True
    if not isinstance(scope, dict) or set(scope) - {'model', 'surface'}:
        return False
    return scope.get('model') is None and scope.get('surface') is None


def _is_fable(scope):
    if not isinstance(scope, dict) or set(scope) - {'model', 'surface'}:
        return False
    # A surface-specific or version-specific scope is not provably the entire
    # cross-surface Fable allowance shown by this application.
    if scope.get('surface') is not None:
        return False
    model = scope.get('model')
    if not isinstance(model, dict) or set(model) - {'display_name'}:
        return False
    label = model.get('display_name')
    return isinstance(label, str) and bool(re.fullmatch(
        r'(?:claude[ -]+)?fable', label.strip(), re.I))


def _rate_fraction(info, item, name):
    fraction = _number(item.get('utilization'), 0, 1)
    kind = info.get('rateLimitType', info.get('rate_limit_type'))
    statuses = [item.get('status')]
    if kind == name and item is not info:
        statuses.append(info.get('status'))
        root_fraction = _number(info.get('utilization'), 0, 1)
        if info.get('status') == 'rejected' and root_fraction is not None and root_fraction < 1:
            return None
    for status in statuses:
        if status is not None and status not in ('allowed', 'allowed_warning', 'rejected'):
            return None
        if status == 'rejected' and fraction is not None and fraction < 1:
            return None
    return fraction


def _candidates(message):
    if not isinstance(message, dict):
        return []
    out = []
    report = message.get('usage_report')
    rate = report.get('rate_limits') if isinstance(report, dict) else None
    limits = rate.get('limits') if isinstance(rate, dict) else None
    if isinstance(limits, list):
        for item in limits[:64]:
            if not isinstance(item, dict):
                continue
            kind = item.get('kind')
            scope = item.get('scope')
            name = ('five_hour' if kind == 'session' and _unscoped(scope)
                    else 'seven_day' if kind == 'weekly_all' and _unscoped(scope)
                    else 'fable' if kind == 'weekly_scoped' and _is_fable(scope) else None)
            if name:
                out.append((name, item.get('percent'), item.get('resets_at')))
    if message.get('type') != 'rate_limit_event':
        return out
    info = message.get('rate_limit_info')
    if not isinstance(info, dict):
        return out
    windows = info.get('unifiedWindows')
    if isinstance(windows, dict):
        for name in ('five_hour', 'seven_day'):
            item = windows.get(name)
            if not isinstance(item, dict):
                continue
            fraction = _rate_fraction(info, item, name)
            out.append((name, fraction * 100 if fraction is not None else None, item.get('resetsAt')))
    kind = info.get('rateLimitType', info.get('rate_limit_type'))
    if kind in ('five_hour', 'seven_day'):
        fraction = _rate_fraction(info, info, kind)
        out.append((kind, fraction * 100 if fraction is not None else None,
                    info.get('resetsAt', info.get('resets_at'))))
    return out


def summarise(records, now_epoch=None, not_before_epoch=None):
    """records are newest-first pairs (JSON event, UTC time); no raw values escape."""
    if now_epoch is None:
        now_epoch = dt.datetime.now(dt.timezone.utc).timestamp()
    result = {name: _empty() for name in WINDOWS}
    found = set()
    for raw, observed in records:
        if not_before_epoch is not None:
            safe_observed = _observed(observed, now_epoch)
            if safe_observed is None:
                continue
            if dt.datetime.fromisoformat(safe_observed).timestamp() < not_before_epoch:
                continue
        try:
            message = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            continue
        current = {}
        for name, used, reset in _candidates(message):
            if name in found:
                continue
            value = _window(used, reset, observed, now_epoch)
            if name not in current or (current[name] is None and value is not None):
                current[name] = value
        for name, value in current.items():
            if value is None:
                value = _empty()
                value['updated_at'] = _observed(observed, now_epoch)
            result[name] = value
            found.add(name)
        # A usage_report is a complete authoritative snapshot, unlike a
        # partial rate_limit_event. Missing/null rows must not resurrect older
        # data from a different billing window or an earlier login.
        if isinstance(message, dict) and 'usage_report' in message:
            for name in WINDOWS:
                if name not in found:
                    value = _empty()
                    value['updated_at'] = _observed(observed, now_epoch)
                    result[name] = value
                    found.add(name)
        if len(found) == len(WINDOWS):
            break
    return {'source': 'claude_events', 'windows': result}


def _current(result):
    now_epoch = time.time()
    windows = {}
    for name, original in result['windows'].items():
        item = dict(original)
        if item['state'] == 'known' and item['resets_at'] <= now_epoch:
            item.update(state='expired', used_percent=None, remaining_percent=None)
        windows[name] = item
    return {'source': 'claude_events', 'windows': windows}


def _credential_state(worker):
    # Metadata only: never open, read or expose credentials. Unknown login
    # storage is not enough evidence to attribute older events to this account.
    home = worker.environment.get('HOME')
    if not home:
        return None
    try:
        info = (Path(home) / '.claude' / '.credentials.json').lstat()
        if not stat.S_ISREG(info.st_mode):
            return None
        marker = (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size)
        return marker, (max(info.st_mtime_ns, info.st_ctime_ns) + 999_999_999) // 1_000_000_000
    except OSError:
        return None


def _generation(key):
    with _GENERATION_LOCK:
        return _GENERATION.get(key, 0)


def invalidate(worker):
    # Tiny independent lock; never waits on quota I/O or task scheduling.
    key = str(worker.db_path)
    with _GENERATION_LOCK:
        _GENERATION[key] = _GENERATION.get(key, 0) + 1


def snapshot(worker):
    # Read-only WAL connection with an upper CPU-time bound. The lock applies
    # only to quota snapshots, never to task scheduling or SQLite writes.
    key = str(worker.db_path)
    with _CACHE_LOCK:
        identity = _credential_state(worker)
        if identity is None:
            _CACHE.pop(key, None)
            return {'source': 'claude_events', 'windows': {n: _empty() for n in WINDOWS}}
        generation = _generation(key)
        cached = _CACHE.get(key)
        if cached is not None and cached[2] == identity[0] and cached[3] == generation and time.monotonic() < cached[0]:
            if _credential_state(worker) == identity and _generation(key) == generation:
                return _current(cached[1])
            _CACHE.pop(key, None)
            return {'source': 'claude_events', 'windows': {n: _empty() for n in WINDOWS}}
        db = None
        try:
            uri = Path(worker.db_path).absolute().as_uri() + '?mode=ro'
            db = sqlite3.connect(uri, uri=True, timeout=0.25)
            db.execute('PRAGMA query_only=ON')
            deadline = time.monotonic() + 0.5
            db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            rows = db.execute("""
                SELECT data,created_at FROM events
                WHERE type='claude' AND length(data) <= ? AND
                      CASE WHEN json_valid(data) THEN
                        json_extract(data, '$.type') = 'rate_limit_event'
                        OR json_type(data, '$.usage_report') IS NOT NULL
                      ELSE 0 END
                ORDER BY seq DESC LIMIT ?
            """, (MAX_EVENT_BYTES, RESULT_LIMIT)).fetchall()
            result = summarise(rows, not_before_epoch=identity[1])
            if _credential_state(worker) != identity or _generation(key) != generation:
                result = {'source': 'claude_events', 'windows': {n: _empty() for n in WINDOWS}}
        except (sqlite3.Error, OSError):
            # No raw exception details; absence or contention is not zero usage.
            result = {'source': 'claude_events', 'windows': {n: _empty() for n in WINDOWS}}
        finally:
            if db is not None:
                db.close()
        _CACHE[key] = (time.monotonic() + CACHE_SECONDS, result, identity[0], generation)
        return _current(result)
