"""Index committed top-level assistant text once, not stream/tool noise."""
SCHEMA='''CREATE TABLE IF NOT EXISTS reply_index(session_id TEXT NOT NULL,message_id TEXT NOT NULL,seq INTEGER NOT NULL,PRIMARY KEY(session_id,message_id));
CREATE INDEX IF NOT EXISTS reply_cursor ON reply_index(session_id,seq);'''
def record(db,sid,kind,data,seq):
    if kind!='claude' or data.get('type')!='assistant' or data.get('parent_tool_use_id'):return
    message=data.get('message') or {};content=message.get('content') or []
    if not any(isinstance(c,dict) and c.get('type')=='text' and str(c.get('text') or '').strip() for c in content):return
    mid=message.get('id') or data.get('uuid') or str(seq)
    db.execute('INSERT OR IGNORE INTO reply_index VALUES (?,?,?)',(sid,mid,seq))
def initialize(db):
    existed=db.execute("SELECT 1 FROM sqlite_master WHERE name='reply_index'").fetchone()
    db.executescript(SCHEMA)
    if not existed:
        import json
        rows=db.execute("SELECT seq,session_id,type,data FROM events WHERE type='claude' AND json_extract(data,'$.type')='assistant' ORDER BY seq").fetchall()
        for seq,sid,kind,raw in rows:record(db,sid,kind,json.loads(raw),seq)
def heads(db,ids):
    result={}
    for sid in ids:
        head=db.execute('SELECT COALESCE(MAX(seq),0),COUNT(*) FROM reply_index WHERE session_id=?',(sid,)).fetchone()
        row=db.execute('SELECT status FROM sessions WHERE id=?',(sid,)).fetchone()
        if row:result[sid]={'seq':head[0],'count':head[1],'status':row[0]}
    return result
