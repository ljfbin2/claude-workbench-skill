"""Persist only sanitized official windows, bound to the exact credential metadata."""
import hashlib,json,os,stat,tempfile,time
from pathlib import Path
from worker import quota

def marker(identity):return hashlib.sha256(json.dumps(identity,separators=(',',':')).encode()).hexdigest()
def path(key):return Path(key).with_name(Path(key).name+'.quota-v2.json')
def window(used,reset,observed,now_epoch=None):
 now_epoch=time.time() if now_epoch is None else now_epoch
 if reset is not None:return quota._window(used,reset,observed,now_epoch)
 used=quota._number(used,0,100);observed=quota._observed(observed,now_epoch)
 if used is None or observed is None:return None
 return dict(state='known',used_percent=used,remaining_percent=100-used,resets_at=None,updated_at=observed)

def load(key,identity):
 try:
  p=path(key);info=p.lstat()
  if not stat.S_ISREG(info.st_mode) or info.st_size>16384:return None
  fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
  with os.fdopen(fd) as f:value=json.load(f)
  if value.get('version')!=1 or value.get('identity')!=marker(identity):return None
  out={}
  for name in quota.WINDOWS:
   v=value.get('windows',{}).get(name,{})
   if v.get('state') not in ('known','expired'):out[name]=quota._empty();continue
   normal=window(v.get('used_percent'),v.get('resets_at'),v.get('updated_at'))
   if normal is None:normal=quota._empty()
   normal['timestamp_kind']='queried';out[name]=normal
  return out
 except (OSError,ValueError,TypeError,AttributeError):return None

def save(key,identity,windows):
 p=path(key);fd,tmp=tempfile.mkstemp(prefix='.quota-',dir=p.parent)
 try:
  os.fchmod(fd,0o600)
  with os.fdopen(fd,'w') as f:
   json.dump(dict(version=1,identity=marker(identity),windows=windows),f,separators=(',',':'));f.flush();os.fsync(f.fileno())
  os.replace(tmp,p)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)
