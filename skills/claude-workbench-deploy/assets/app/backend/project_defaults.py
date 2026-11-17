"""Owner-scoped project defaults, applied only by an explicit new-session form."""
import json,time
SCHEMA='''CREATE TABLE IF NOT EXISTS project_defaults (
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,cwd TEXT NOT NULL,
 settings TEXT NOT NULL,updated_at INTEGER NOT NULL,PRIMARY KEY(user_id,cwd));'''
KEYS=('model','reasoning_effort','collaboration_enabled','reviewer_effort','coder_effort')
def get(app,user,cwd):
    with app.db() as db:row=db.execute('SELECT settings FROM project_defaults WHERE user_id=? AND cwd=?',(user['id'],cwd)).fetchone()
    return json.loads(row[0]) if row else None
async def put(app,user,session,body):
    values=body.model_dump();values={k:values[k] for k in KEYS}
    if values['collaboration_enabled']:await app.collaboration_snapshot(True,values['reviewer_effort'],values['coder_effort'])
    else:await app.validate_model_selection(values['model'],values['reasoning_effort'])
    with app.db() as db:db.execute('INSERT INTO project_defaults VALUES (?,?,?,?) ON CONFLICT(user_id,cwd) DO UPDATE SET settings=excluded.settings,updated_at=excluded.updated_at',(user['id'],session['cwd'],json.dumps(values),int(time.time())))
    return values
