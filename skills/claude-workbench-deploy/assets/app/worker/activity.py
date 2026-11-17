"""Bounded, incremental task telemetry from committed events; never drives execution."""
import datetime as dt
import math


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 10**12 else None


def tokens(raw):
    if not isinstance(raw, dict):
        return None
    out = {key: number(raw.get(key)) for key in ('input_tokens', 'output_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')}
    return out if any(value is not None for value in out.values()) else None


def initial(claude_id):
    return dict(claude_id=claude_id, cursor=0, phase='starting', last_event_at=None,
                last_output_at=None, last_progress_at=None, last_kind=None, last_activity_heartbeat=False, tools={}, tasks={},
                response_usage=None, response_id=None, result_usage=None, context=None, context_reason=None, capacities={}, canonical_capacities={}, model=None,
                last_success_at=None, turn_started_at=None, latest_input_at=None, error=False, error_reason=None)


def context(state, usage, stamp):
    if not usage or any(usage.get(key) is None for key in ('input_tokens','cache_read_input_tokens','cache_creation_input_tokens')):
        state['context'] = None
        state['context_reason'] = 'missing_input_or_cache_usage'
        return
    state['context_reason'] = None
    # Cached input is part of this request context; aggregated result usage is not.
    keys = ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens', 'output_tokens')
    count = sum(usage.get(key) or 0 for key in keys)
    state['context'] = dict(tokens=count, model=state['model'], observed_at=stamp)


def response_sample(state, message, stamp, starting=False):
    model = message.get('model') or state['model']
    identifier = message.get('id')
    same = bool(identifier and identifier == state['response_id'] and model == state['model'])
    previous = state['response_usage'] if same else None
    if starting or not same:
        state['result_usage'] = None
        state['response_usage'] = None
        state['context'] = None
        state['context_reason'] = 'awaiting_request_usage'
    state['model'] = model
    state['response_id'] = identifier
    usage = tokens(message.get('usage'))
    if usage:
        if previous:
            usage = {key: usage.get(key) if usage.get(key) is not None else value for key,value in previous['values'].items()}
        state['response_usage'] = dict(values=usage, observed_at=stamp)
        context(state, usage, stamp)


def consume(state, kind, message, stamp):
    if kind == 'user':
        state.update(latest_input_at=stamp, result_usage=None, error=False, error_reason=None)
        if state['phase']=='finishing':
            state.update(phase='starting',last_event_at=None,last_output_at=None,last_progress_at=None,last_kind=None)
        return
    if kind == 'error':
        from worker.service import diagnostic
        state['error'] = True
        state['error_reason'] = diagnostic([message.get('message','')])
        return
    if kind == 'status':
        if message.get('status') == 'running':
            state.update(turn_started_at=stamp,phase='starting',last_event_at=None,last_output_at=None,last_progress_at=None,last_kind=None)
        return
    if kind != 'claude' or message.get('session_id') != state['claude_id']:
        return
    typ = message.get('type')
    state['last_event_at'] = stamp
    state['last_kind'] = typ
    state['last_activity_heartbeat'] = typ=='tool_progress' and message.get('heartbeat') is True
    parent = message.get('parent_tool_use_id')
    known_main_progress = (typ=='tool_progress' and message.get('tool_use_id') in state['tools'] and parent==message.get('tool_use_id'))
    if parent is not None and not known_main_progress:
        return
    if typ == 'assistant':
        msg = message.get('message') or {}
        response_sample(state, msg, stamp)
        for block in msg.get('content') or []:
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'text' and block.get('text'):
                state.update(last_output_at=stamp, phase='responding')
            elif block.get('type') == 'tool_use' and isinstance(block.get('id'), str):
                state['tools'][block['id']] = dict(name=str(block.get('name') or 'tool')[:60], started_at=stamp, elapsed_seconds=0)
                state['phase'] = 'executing'
                state['last_progress_at'] = stamp
            elif block.get('type') == 'thinking':
                state['phase'] = 'thinking'
    elif typ == 'stream_event':
        event = message.get('event') or {}
        delta = event.get('delta') or {}
        if event.get('type') == 'message_start':
            msg = event.get('message') or {}
            response_sample(state, msg, stamp, starting=True)
        elif delta.get('type') == 'text_delta' and delta.get('text'):
            state.update(last_output_at=stamp, phase='responding')
        elif delta.get('type') == 'thinking_delta':
            state['phase'] = 'thinking'
        elif delta.get('type') == 'input_json_delta':
            state['phase'] = 'preparing_tool'
        elif event.get('type') == 'message_stop' and not state['tools'] and not state['tasks']:
            state['phase'] = 'finishing'
        elif event.get('type') == 'message_delta':
            usage = tokens(event.get('usage'))
            if usage and state['response_usage']:
                state['response_usage']['values'].update({k:v for k,v in usage.items() if v is not None})
                state['response_usage']['observed_at'] = stamp
                context(state, state['response_usage']['values'], stamp)
    elif typ == 'tool_progress':
        key = message.get('tool_use_id')
        if isinstance(key, str):
            tool = state['tools'].setdefault(key, dict(name=str(message.get('tool_name') or 'tool')[:60], started_at=stamp, elapsed_seconds=0))
            elapsed = number(message.get('elapsed_time_seconds'))
            if elapsed is not None:
                tool['elapsed_seconds'] = elapsed
            state.update(last_progress_at=stamp, phase='executing')
    elif typ == 'user':
        blocks = (message.get('message') or {}).get('content') or []
        if isinstance(blocks, list):
            for block in blocks:
                if isinstance(block, dict) and block.get('type') == 'tool_result':
                    state['tools'].pop(block.get('tool_use_id'), None)
                    state.update(last_progress_at=stamp, phase='requesting')
    elif typ == 'system':
        sub = message.get('subtype')
        if sub == 'status' and message.get('status') in ('requesting', 'compacting'):
            state['phase'] = message['status']
        elif sub == 'thinking_tokens':
            state['phase'] = 'thinking'
        elif sub == 'task_started' and isinstance(message.get('task_id'), str):
            state['tasks'][message['task_id']] = message.get('tool_use_id')
            state['phase'] = 'executing'
        elif sub == 'task_notification' and message.get('status') in ('completed', 'failed', 'stopped'):
            tool_id = state['tasks'].pop(message.get('task_id'), None)
            state['tools'].pop(tool_id or message.get('tool_use_id'), None)
            state.update(last_progress_at=stamp, phase='requesting')
    elif typ == 'result':
        state['result_usage'] = dict(values=tokens(message.get('usage')), observed_at=stamp)
        models = message.get('modelUsage')
        if isinstance(models, dict):
            state['capacities'] = {}
            canonical = {}
            for model, details in models.items():
                if not isinstance(details, dict):
                    continue
                capacity = number(details.get('contextWindow'))
                if capacity and capacity <= 10**8:
                    state['capacities'][model] = capacity
                    name = details.get('canonicalModel')
                    if isinstance(name, str):
                        canonical.setdefault(name, set()).add(capacity)
            state['canonical_capacities'] = {name: sorted(values) for name, values in canonical.items()}
        if message.get('subtype') in (None, 'success') and not message.get('is_error'):
            state['last_success_at'] = stamp
            state.update(error=False,error_reason=None)
        else:
            from worker.service import diagnostic
            state['error'] = True
            state['error_reason'] = diagnostic([message.get('result','')] + [str(value) for value in (message.get('errors') or [])[:3]]) or 'Claude returned an unsuccessful result'
        state.update(phase='finishing', tools={}, tasks={})


def snapshot(worker, sid, status, updated_at):
    # Only committed journal rows enter this cache; failed DB transactions cannot invent activity.
    with worker.mutex:
        cache = getattr(worker, '_activity_cache', None)
        if cache is None:
            cache = worker._activity_cache = {}
        with worker.db() as db:
            row = db.execute('SELECT claude_id FROM sessions WHERE id=?', (sid,)).fetchone()
            claude_id = row[0] if row else None
            state = cache.get(sid)
            if state is None or state['claude_id'] != claude_id:
                state = initial(claude_id)
                first = db.execute("SELECT seq FROM events WHERE session_id=? AND type='user' ORDER BY seq DESC LIMIT 1", (sid,)).fetchone()
                state['cursor'] = first[0]-1 if first else 0
                # Recover prior capacity and context without replaying the full chat history.
                import json
                prior = db.execute("SELECT data,created_at FROM events WHERE session_id=? AND type='claude' AND seq<=? AND json_extract(data,'$.type') IN ('result','assistant') ORDER BY seq DESC LIMIT 64", (sid, state['cursor'])).fetchall()
                for raw, stamp in reversed(prior):
                    consume(state, 'claude', json.loads(raw), stamp)
                state['tools'] = {};state['tasks'] = {};state['result_usage'] = None
                cache[sid] = state
                if len(cache)>128:
                    cache.pop(next(key for key in cache if key != sid), None)
            rows = db.execute('SELECT seq,type,data,created_at FROM events WHERE session_id=? AND seq>? ORDER BY seq LIMIT 5001', (sid, state['cursor'])).fetchall()
        import json
        for seq, kind, raw, stamp in rows[:5000]:
            consume(state, kind, json.loads(raw), stamp)
            state['cursor'] = seq
        child = worker.children.get(sid, {})
        proc = child.get('process')
        alive = proc.poll() is None if proc else None
        phase = state['phase']
        if state['tools'] or state['tasks']:
            phase = 'executing'
        if child.get('idle') and child.get('background'):
            phase = 'background'
        if status not in ('running', 'stopping'):
            phase = status
        elif status == 'stopping':
            phase = 'stopping'
        elif proc is not None and not alive:
            phase = 'finishing'
        context_value = dict(state['context']) if state['context'] else None
        if context_value:
            capacity = state['capacities'].get(context_value['model'])
            choices = state['canonical_capacities'].get(context_value['model'], [])
            source = 'exact_model' if capacity else 'canonical_model' if len(choices)==1 else None
            if capacity is None and len(choices)==1:
                capacity = choices[0]
            context_value.update(capacity=capacity, percent=round(context_value['tokens']/capacity*100, 1) if capacity else None, estimated=True,
                                 window_source=source, window_reason='ambiguous_canonical' if not source and len(choices)>1 else None)
        checked_at = dt.datetime.now(dt.timezone.utc).isoformat()
        tool = next(reversed(state['tools'].values()), None)
        return dict(phase=phase, process_alive=alive, checked_at=checked_at,
                    started_at=state['turn_started_at'] or (updated_at if status in ('running', 'stopping') else state['latest_input_at']),
                    last_event_at=state['last_event_at'], last_output_at=state['last_output_at'],
                    last_progress_at=state['last_progress_at'], last_kind=state['last_kind'], last_activity_heartbeat=state['last_activity_heartbeat'], tool=dict(tool) if tool else None,
                    last_success_at=state['last_success_at'], usage=state['result_usage'] or state['response_usage'],
                    usage_kind='turn' if state['result_usage'] else 'response', context=context_value, context_reason=state['context_reason'],
                    catching_up=len(rows)>5000, has_error=state['error'], error_reason=state['error_reason'])
