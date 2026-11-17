"""Monotonic, owner-checked read markers shared by browsers."""
SCHEMA='''CREATE TABLE IF NOT EXISTS read_markers(user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,session_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,read_seq INTEGER NOT NULL,PRIMARY KEY(user_id,session_id));'''
async def enrich(app,user,data):
    heads=data.get('attention',{})
    with app.db() as db:
        for sid,head in heads.items():db.execute('INSERT OR IGNORE INTO read_markers VALUES (?,?,?)',(user['id'],sid,head['seq']))
        cursors={sid:db.execute('SELECT read_seq FROM read_markers WHERE user_id=? AND session_id=?',(user['id'],sid)).fetchone()[0] for sid in heads}
    if cursors:
        result=await app.worker('POST','/reply-cursors',json={'cursors':cursors})
        data['attention']=result['attention']
    return data
async def mark(app,user,sid,seq):
    result=await app.worker('POST','/reply-cursors',json={'cursors':{sid:0}})
    head=result['attention'].get(sid,{}).get('seq',0);seq=min(head,seq)
    with app.db() as db:
        db.execute('INSERT INTO read_markers VALUES (?,?,?) ON CONFLICT(user_id,session_id) DO UPDATE SET read_seq=MAX(read_seq,excluded.read_seq)',(user['id'],sid,seq))
        value=db.execute('SELECT read_seq FROM read_markers WHERE user_id=? AND session_id=?',(user['id'],sid)).fetchone()[0]
    return {'read_seq':value}
