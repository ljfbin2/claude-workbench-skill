"""Read-only presentation of the append-only event log; raw events remain intact."""
import json


def real_message(kind, data):
    return kind == 'user' or (kind == 'claude' and data.get('type') == 'assistant'
        and any(b.get('type') == 'text' and str(b.get('text', '')).strip()
                for b in data.get('message', {}).get('content', []) if isinstance(b, dict)))


def compact(rows):
    result = []
    partial = None

    def flush():
        nonlocal partial
        if partial:
            result.append(partial)
            partial = None

    for seq, kind, raw, stamp in rows:
        data = json.loads(raw)
        event = dict(seq=seq, type=kind, data=data, created_at=stamp)
        if kind in ('user', 'error'):
            flush()
            result.append(event)
        elif kind == 'claude':
            typ = data.get('type')
            if typ == 'stream_event':
                e = data.get('event', {})
                delta = e.get('delta', {})
                if e.get('type') == 'content_block_delta' and delta.get('type') == 'text_delta':
                    text = delta.get('text') or ''
                    if partial:
                        partial['data']['event']['delta']['text'] += text
                    elif text:
                        partial = event
            elif typ == 'assistant':
                blocks = data.get('message', {}).get('content') or []
                if any(b.get('type') == 'text' and b.get('text') for b in blocks):
                    partial = None  # The final text replaces its streamed deltas.
                else:
                    flush()
                content = []
                for index, block in enumerate(blocks):
                    if block.get('type') == 'text':
                        content.append(block)
                    elif block.get('type') == 'tool_use':
                        content.append(dict(type='tool_use', id=block.get('id', ''),
                            name=block.get('name', ''), _history_ref=dict(seq=seq, block=index)))
                event['data'] = dict(type=typ, message=dict(content=content,
                    usage=data.get('message', {}).get('usage')))
                result.append(event)
            elif typ == 'user':
                content = []
                blocks = data.get('message', {}).get('content') or []
                if isinstance(blocks, list):
                    for index, block in enumerate(blocks):
                        if isinstance(block, dict) and block.get('type') == 'tool_result':
                            content.append(dict(type='tool_result', tool_use_id=block.get('tool_use_id'),
                                is_error=block.get('is_error') is True, content='',
                                _history_ref=dict(seq=seq, block=index)))
                if content:
                    flush()
                    event['data'] = dict(type=typ, message=dict(content=content))
                    result.append(event)
            elif typ == 'rate_limit_event':
                result.append(event)
            elif typ == 'result':
                flush()
                event['data'] = dict(type=typ, usage=data.get('usage'))
                result.append(event)
    flush()
    return result


def page(worker, sid, before=None, limit=100):
    limit = max(1, min(int(limit), 100))
    cursor = min(int(before or 9223372036854775807), 9223372036854775807)
    rows = []
    count = 0
    with worker.db() as db:
        db.execute('BEGIN')
        high = db.execute('SELECT COALESCE(MAX(seq),0) FROM events WHERE session_id=?', (sid,)).fetchone()[0]
        cursor = min(cursor, high + 1)
        while count < limit:
            batch = db.execute('SELECT seq,type,data,created_at FROM events WHERE session_id=? AND seq<? ORDER BY seq DESC LIMIT 500', (sid, cursor)).fetchall()
            if not batch:
                break
            for row in batch:
                rows.append(row)
                count += bool(real_message(row[1], json.loads(row[2])))
                if count == limit:
                    break
            cursor = rows[-1][0]
            if len(batch) < 500:
                break
        lower = rows[-1][0] if rows else cursor
        more = db.execute('SELECT 1 FROM events WHERE session_id=? AND seq<? LIMIT 1', (sid, lower)).fetchone() is not None
    return dict(events=compact(reversed(rows)), has_more=more, before=lower,
                cursor=high, real_messages=count, format_version=1)


def detail(worker, sid, seq, block):
    if seq < 1 or block < 0:
        raise ValueError('Invalid detail reference')
    with worker.db() as db:
        row = db.execute('SELECT type,data FROM events WHERE session_id=? AND seq=?', (sid, seq)).fetchone()
    if not row or row[0] != 'claude':
        return None
    data = json.loads(row[1])
    blocks = data.get('message', {}).get('content') or []
    if not isinstance(blocks, list) or block >= len(blocks):
        return None
    item = blocks[block]
    if data.get('type') == 'assistant' and item.get('type') == 'tool_use':
        text = json.dumps(item.get('input', {}), ensure_ascii=False, indent=2)
    elif data.get('type') == 'user' and item.get('type') == 'tool_result':
        value = item.get('content', '')
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    else:
        return None
    return dict(text=text)


def updates(worker, sid, after=0, limit=2000):
    limit = max(1, min(int(limit), 2000))
    with worker.db() as db:
        rows = db.execute('SELECT seq,type,data,created_at FROM events WHERE session_id=? AND seq>? ORDER BY seq LIMIT ?', (sid, after, limit + 1)).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    return dict(events=compact(rows), has_more=more, cursor=rows[-1][0] if rows else after,
                format_version=1)
