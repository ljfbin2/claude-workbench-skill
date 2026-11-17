"""Owner-scoped display metadata. Never renames or grants access to a directory."""
SCHEMA='''CREATE TABLE IF NOT EXISTS project_preferences (
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 cwd TEXT NOT NULL,name TEXT NOT NULL DEFAULT '',collapsed INTEGER NOT NULL DEFAULT 0,
 revision INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(user_id,cwd));'''
def list_for(db,user_id):
    return {row['cwd']:{'name':row['name'],'collapsed':bool(row['collapsed']),'revision':row['revision']} for row in db.execute('SELECT * FROM project_preferences WHERE user_id=?',(user_id,))}
def save(db,user_id,cwd,values):
    # Updating one field never replaces another browser's independent field.
    db.execute('''INSERT INTO project_preferences(user_id,cwd,name,collapsed,revision) VALUES (?,?,?,?,1)
    ON CONFLICT(user_id,cwd) DO UPDATE SET
    name=CASE WHEN ? THEN excluded.name ELSE project_preferences.name END,
    collapsed=CASE WHEN ? THEN excluded.collapsed ELSE project_preferences.collapsed END,
    revision=project_preferences.revision+1''',
    (user_id,cwd,values.get('name',''),int(values.get('collapsed',False)),'name' in values,'collapsed' in values))
    row=db.execute('SELECT name,collapsed,revision FROM project_preferences WHERE user_id=? AND cwd=?',(user_id,cwd)).fetchone()
    return {'name':row['name'],'collapsed':bool(row['collapsed']),'revision':row['revision']}
