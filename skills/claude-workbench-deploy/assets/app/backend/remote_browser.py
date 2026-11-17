"""Admin-only RFB relay to a private Unix socket; no input logging."""
import asyncio,json,time,os
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import APIRouter,Depends,HTTPException,WebSocket,WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel,Field
router=APIRouter();RUNTIME=Path(os.environ.get('WEB_AI_BROWSER_RUNTIME','/run/claude-workbench-browser'))

def admin(request):
 from backend.app import current_user,require_project_admin
 user=current_user(request);require_project_admin(user);return user

async def control(command='status',url=None):
 writer=None
 try:
  reader,writer=await asyncio.wait_for(asyncio.open_unix_connection(str(RUNTIME/'control.sock')),3)
  writer.write(json.dumps({'command':command,'url':url}).encode()+b'\n');await writer.drain()
  line=await asyncio.wait_for(reader.readline(),3)
  if len(line)>4096:raise ValueError('Invalid response')
  value=json.loads(line)
  if not isinstance(value,dict) or not value.get('available'):raise ValueError('Browser unavailable')
  return value
 except (OSError,ValueError,asyncio.TimeoutError):
  raise HTTPException(503,'\u670d\u52a1\u5668\u6d4f\u89c8\u5668\u6682\u4e0d\u53ef\u7528')
 finally:
  if writer:writer.close();await writer.wait_closed()

# Request type is explicit: it is also accepted by existing auth/CSRF checks.
from fastapi import Request
@router.get('/server-browser')
def page(request:Request):
 admin(request)
 return FileResponse(Path(__file__).resolve().parents[1]/'frontend/server-browser.html',headers={'Cache-Control':'no-store'})

@router.get('/api/server-browser/status')
async def status(request:Request):
 admin(request);return await control()

class Destination(BaseModel):
 target:str=Field(max_length=32)

@router.post('/api/server-browser/open')
async def navigate(body:Destination,request:Request):
 admin(request)
 urls={'claude':'https://claude.ai/','billing':'https://claude.ai/settings/billing'}
 if body.target not in urls:raise HTTPException(400,'Invalid destination')
 return await control('open',urls[body.target])

def socket_admin(ws):
 from backend.app import db,token_hash
 token=ws.cookies.get('web_ai_session','')
 with db() as conn:
  row=conn.execute('SELECT users.is_admin FROM users JOIN auth_sessions ON users.id=auth_sessions.user_id WHERE auth_sessions.token_hash=? AND auth_sessions.expires_at>?',(token_hash(token),int(time.time()))).fetchone() if token else None
 return bool(row and row['is_admin'])

@router.websocket('/api/server-browser/socket')
async def relay(ws:WebSocket):
 from backend.app import settings
 expected=('https' if settings.secure_cookie else 'http')+'://'+ws.headers.get('host','')
 if ws.headers.get('origin')!=expected or not await asyncio.to_thread(socket_admin,ws):
  await ws.close(code=1008);return
 reader=writer=None;tasks=[]
 try:
  reader,writer=await asyncio.wait_for(asyncio.open_unix_connection(str(RUNTIME/'desktop.sock')),3)
  await ws.accept()
  async def receive():
   while True:
    packet=await ws.receive_bytes()
    if len(packet)>2*1024*1024:raise ValueError('Frame too large')
    writer.write(packet);await writer.drain()
  async def send():
   while True:
    packet=await reader.read(65536)
    if not packet:return
    await ws.send_bytes(packet)
  async def expiry():
   while True:
    await asyncio.sleep(2)
    if not await asyncio.to_thread(socket_admin,ws):return
  tasks=[asyncio.create_task(f()) for f in [receive,send,expiry]]
  done,pending=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
  for task in done:
   if not task.cancelled():task.result()
 except (OSError,ValueError,RuntimeError,asyncio.TimeoutError,WebSocketDisconnect):
  pass
 finally:
  for task in tasks:task.cancel()
  if tasks:await asyncio.gather(*tasks,return_exceptions=True)
  if writer:writer.close();await writer.wait_closed()
  try:await ws.close()
  except RuntimeError:pass

class AuthCode(BaseModel):
 id:str=Field(pattern=r"^[0-9a-f]{32}$")
 code:str=Field(min_length=1,max_length=4096)

class AuthID(BaseModel):
 id:str=Field(pattern=r"^[0-9a-f]{32}$")


def safe_auth(value):
 return {'id':value.get('id'),'state':value.get('state','idle'),'has_url':bool(value.get('url')),'reason':value.get('reason')}

@router.get('/api/server-browser/account')
async def account_status(request:Request):
 admin(request)
 from backend.app import worker
 return safe_auth(await worker('GET','/account/login'))

@router.post('/api/server-browser/account/start')
async def account_start(request:Request):
 admin(request);await control()
 from backend.app import worker
 result=await worker('POST','/account/login')
 if result.get('url'):await control('open',result['url'])
 return safe_auth(result)

@router.post('/api/server-browser/account/open')
async def account_open(body:AuthID,request:Request):
 admin(request)
 from backend.app import worker
 result=await worker('GET','/account/login')
 if result.get('id')!=body.id or not result.get('url'):raise HTTPException(409,'Authorization URL unavailable')
 await control('open',result['url']);return safe_auth(result)

@router.post('/api/server-browser/account/code')
async def account_code(body:AuthCode,request:Request):
 admin(request)
 if len(body.code)>4096 or len(body.id)>64:raise HTTPException(400,'Invalid authorization input')
 from backend.app import worker
 return safe_auth(await worker('POST','/account/code',json=body.model_dump()))

@router.post('/api/server-browser/account/cancel')
async def account_cancel(body:AuthID,request:Request):
 admin(request)
 from backend.app import worker
 return safe_auth(await worker('POST','/account/cancel',json=body.model_dump()))
