"""Durable upload receipts. Stable IDs are scoped to an owner and conversation."""
import asyncio,hashlib,re,time,uuid
from pathlib import Path
from fastapi import HTTPException
LOCKS=[asyncio.Lock() for _ in range(64)]
SCHEMA='''CREATE TABLE IF NOT EXISTS upload_receipts (
 user_id TEXT NOT NULL REFERENCES users(id), request_id TEXT NOT NULL,
 session_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
 file_id TEXT NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
 digest TEXT NOT NULL, PRIMARY KEY(user_id,request_id));'''
async def receive(app,request,file,user):
    rid=request.headers.get('x-upload-id');sid=request.headers.get('x-session-id')
    app.require_project_admin(user)
    if rid or sid:
        if not rid or not re.fullmatch(r'[0-9a-f-]{32,36}',rid) or not sid:raise HTTPException(400,'Invalid upload identity')
        session=app.conversation(sid,user)
        if session.get('archived_at'):raise HTTPException(409,'Conversation is archived')
    name=Path((file.filename or 'file').replace('\\','/')).name[:200] or 'file'
    extension=Path(name).suffix.lower()
    if len(extension)>16 or not extension[1:].isalnum():extension=''
    content_type=file.content_type or 'application/octet-stream'
    fid=uuid.uuid4().hex;path=app.settings.uploads/(fid+extension);size=0;digest=hashlib.sha256();keep=False
    lock=LOCKS[int(hashlib.sha256((user['id']+str(rid or fid)).encode()).hexdigest(),16)%len(LOCKS)]
    try:
        async with lock:
            with path.open('xb') as target:
                while chunk:=await file.read(1024*1024):
                    size+=len(chunk)
                    if size>512*1024*1024:raise HTTPException(413,'File exceeds 512 MiB')
                    digest.update(chunk);target.write(chunk)
            with app.db() as db:
                previous=db.execute('SELECT r.*,u.name,u.size,u.content_type,u.path FROM upload_receipts r JOIN uploads u ON u.id=r.file_id WHERE r.user_id=? AND r.request_id=?',(user['id'],rid)).fetchone() if rid else None
                if previous:
                    if (previous['session_id'],previous['name'],previous['size'],previous['content_type'],previous['digest'])!=(sid,name,size,content_type,digest.hexdigest()):raise HTTPException(409,'Upload ID has different content; add as a new attachment')
                    if not Path(previous['path']).is_file():raise HTTPException(409,'Original uploaded file is unavailable; add as a new attachment')
                    return dict(id=previous['file_id'],name=name,size=size,content_type=content_type)
                db.execute('INSERT INTO uploads VALUES (?,?,?,?,?,?,?)',(fid,user['id'],name,str(path),size,content_type,int(time.time())))
                if rid:db.execute('INSERT INTO upload_receipts VALUES (?,?,?,?,?)',(user['id'],rid,sid,fid,digest.hexdigest()))
            keep=True
            return dict(id=fid,name=name,size=size,content_type=content_type)
    finally:
        if not keep:path.unlink(missing_ok=True)
        await file.close()
