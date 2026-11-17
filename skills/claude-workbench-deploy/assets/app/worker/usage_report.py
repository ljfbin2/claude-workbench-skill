"""Validate a pinned Claude Code official usage report without model calls."""
import datetime as dt
from worker import quota, quota_store

def normalise(payload, observed, now_epoch=None):
    now_epoch = dt.datetime.now(dt.timezone.utc).timestamp() if now_epoch is None else now_epoch
    result = {'source': 'claude_usage', 'windows': {n: quota._empty() for n in quota.WINDOWS}}
    if not isinstance(payload, dict) or payload.get('rate_limits_available') is not True:
        return result
    rate = payload.get('rate_limits')
    limits = rate.get('limits') if isinstance(rate, dict) else None
    scoped = rate.get('model_scoped') if isinstance(rate, dict) else None
    if not isinstance(limits, list) or len(limits) > 64:
        return result
    for kind, name in (('session', 'five_hour'), ('weekly_all', 'seven_day')):
        rows = [row for row in limits if isinstance(row, dict) and row.get('kind') == kind]
        if len(rows) == 1 and quota._unscoped(rows[0].get('scope')):
            value = quota_store.window(rows[0].get('percent'), rows[0].get('resets_at'), observed, now_epoch)
            if value is not None:
                result['windows'][name] = value
    if not isinstance(scoped, list) or len(scoped) > 64:
        return result
    rows = [row for row in limits if isinstance(row, dict) and row.get('kind') == 'weekly_scoped'
            and quota._is_fable(row.get('scope'))]
    projected = [row for row in scoped if isinstance(row, dict)
                 and quota._is_fable({'model': {'display_name': row.get('display_name')}})]
    if len(rows) != 1 or len(projected) != 1:
        return result
    row, projection = rows[0], projected[0]
    used = quota._number(row.get('percent'), 0, 100)
    used_projection = quota._number(projection.get('utilization'), 0, 100)
    reset, reset_projection = quota._epoch(row.get('resets_at')), quota._epoch(projection.get('resets_at'))
    if used is None or used_projection is None or used != used_projection or reset != reset_projection or (row.get('resets_at') is not None and reset is None) or (projection.get('resets_at') is not None and reset_projection is None):
        return result
    value = quota_store.window(used, reset, observed, now_epoch)
    if value is not None:
        result['windows']['fable'] = value
    return result
