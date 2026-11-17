"""Official CLI login only. Never expose or retain CLI output or credentials."""
import json,os,re,signal,subprocess,tempfile,threading,time,uuid,select
from urllib.parse import urlsplit
_LOCK=threading.Lock()

def status(worker):
 with _LOCK:
  item=getattr(worker,'_account_login',None)
  return {k:item[k] for k in ('id','state','url','reason') if k in item} if item else {'state':'idle'}

def start(worker):
 with _LOCK:
  item=getattr(worker,'_account_login',None)
  if item and item['state'] in ('starting','waiting','submitting','cancelling'):return status_unlocked(item)
 if not worker._model_lock.acquire(blocking=False):raise ValueError('Assistant is busy; try again when idle')
 reserved=False
 try:
  with worker.mutex,worker.db() as db:
   if worker._capability_probe_active or db.execute("SELECT 1 FROM sessions WHERE status IN ('running','stopping','unknown') LIMIT 1").fetchone() or db.execute('SELECT 1 FROM pending_turns LIMIT 1').fetchone():
    raise ValueError('Assistant is busy; try again when idle')
   worker._capability_probe_active=True;worker._account_login_active=True;reserved=True
  item={'id':uuid.uuid4().hex,'state':'starting','url':None,'process':None,'cancel':False,'ready':threading.Event(),'reason':None}
  with _LOCK:worker._account_login=item
  threading.Thread(target=run,args=(worker,item),daemon=True).start()
 except BaseException:
  with _LOCK:
   if getattr(worker,'_account_login',None):worker._account_login['state']='failed'
  if reserved:
   with worker.mutex:worker._capability_probe_active=False;worker._account_login_active=False
  worker._model_lock.release();raise
 item['ready'].wait(3)
 return status(worker)

def status_unlocked(item):return {k:item.get(k) for k in ('id','state','url','reason')}

def send_code(worker,ident,code):
 if not isinstance(code,str) or not 1<=len(code.strip())<=4096 or '\n' in code or '\r' in code:raise ValueError('Invalid authorization code')
 with _LOCK:
  item=getattr(worker,'_account_login',None)
  if not item or item['id']!=ident or item['state']!='waiting' or not item.get('process'):raise ValueError('No matching active authorization')
  proc=item['process'];item['state']='submitting'
 try:
  fd=proc.stdin.fileno();os.set_blocking(fd,False);data=(code.strip()+'\n').encode();deadline=time.monotonic()+3
  while data:
   if time.monotonic()>deadline:raise OSError('Authorization input timeout')
   if not select.select([], [fd], [], .2)[1]:continue
   try:n=os.write(fd,data)
   except BlockingIOError:continue
   if n<=0:raise OSError('Authorization input closed')
   data=data[n:]
 except (OSError,ValueError):
  cancel(worker,ident)
  raise ValueError('Authorization input failed; restart authorization')

 return status(worker)

def cancel(worker,ident):
 with _LOCK:
  item=getattr(worker,'_account_login',None)
  if not item or item['id']!=ident:raise ValueError('No matching active authorization')
  if item['state'] not in ('starting','waiting','submitting','cancelling'):return status_unlocked(item)
  item['cancel']=True;item['state']='cancelling';proc=item.get('process')
 if proc and proc.poll() is None:
  try:os.killpg(proc.pid,signal.SIGTERM)
  except ProcessLookupError:pass
  def kill_later():
   if proc.poll() is None:
    try:os.killpg(proc.pid,signal.SIGKILL)
    except ProcessLookupError:pass
  timer=threading.Timer(2,kill_later);timer.daemon=True;timer.start()
 return status(worker)

def run(worker,item):
 proc=None;timer=None;outcome='failed'
 try:
  env={k:v for k,v in worker.environment.items() if k in ('HOME','PATH','XDG_CONFIG_HOME','XDG_CACHE_HOME','XDG_DATA_HOME','LANG','LC_ALL','TZ')}
  env['BROWSER']='/usr/bin/true'
  with tempfile.TemporaryDirectory(prefix='account-login-',dir=worker.db_path.parent) as cwd:
   proc=subprocess.Popen([worker.claude,'auth','login','--claudeai'],env=env,cwd=cwd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True)
   with _LOCK:item['process']=proc;cancelled=item['cancel']
   if cancelled:
    try:os.killpg(proc.pid,signal.SIGTERM)
    except ProcessLookupError:pass
   def expire():
    if proc.poll() is None:
     try:os.killpg(proc.pid,signal.SIGKILL)
     except ProcessLookupError:pass
   timer=threading.Timer(480,expire);timer.daemon=True;timer.start()
   buffer=''
   while True:
    data=os.read(proc.stdout.fileno(),4096)
    if not data:break
    buffer=(buffer+data.decode('utf-8',errors='replace'))[-32768:]
    clean=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',buffer)
    match=re.search(r'https://(?:claude\.com|claude\.ai)/[^\s\x1b]+',clean)
    if match:
     url=match.group(0)
     if len(url)<16000 and urlsplit(url).hostname in ('claude.com','claude.ai'):
      with _LOCK:
       if item['state']=='starting':item['url']=url;item['state']='waiting'
      item['ready'].set()
   code=proc.wait(timeout=3)
   item['reason']=None if code==0 else 'Official authorization did not finish (exit '+str(code)+')'
   if code==0 and not item['cancel']:
    r=subprocess.run([worker.claude,'auth','status','--json'],env=env,cwd=cwd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=5)
    if r.returncode==0 and json.loads(r.stdout).get('loggedIn') is True:outcome='success'
 except (OSError,ValueError,subprocess.SubprocessError):item['reason']='Official authorization could not complete; retry the login'
 finally:
  if timer:timer.cancel()
  if proc and proc.poll() is None:
   try:os.killpg(proc.pid,signal.SIGKILL)
   except ProcessLookupError:pass
   try:proc.wait(timeout=3)
   except subprocess.TimeoutExpired:pass
  if proc:
   for pipe in (proc.stdin,proc.stdout):
    if pipe:
     try:pipe.close()
     except OSError:pass
  with _LOCK:
   item['state']='cancelled' if item['cancel'] else outcome;item['url']=None;item['process']=None;item['ready'].set()
  with worker.mutex:
   worker._capability_probe_active=False;worker._account_login_active=False;worker._auth_cache=None;worker._model_cache_until=0
  worker._model_lock.release();worker._dispatch_global()
