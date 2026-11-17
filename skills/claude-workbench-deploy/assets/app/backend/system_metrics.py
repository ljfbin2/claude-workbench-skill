"""Read-only Linux host metrics with a tiny shared two-second sampler."""
import copy,os,threading,time
from datetime import datetime,timezone
_LOCK=threading.Lock();STOP=threading.Event();_thread=None;_snapshot=None;_last=None

def cpu_percent(previous,current):
 if previous is None:return None
 delta=[a-b for a,b in zip(current[:8],previous[:8])]
 total=sum(delta)
 if len(delta)!=8 or total<=0 or any(x<0 for x in delta):return None
 return round((total-delta[3]-delta[4])*100/total,1)

def memory_usage(values):
 total=values['MemTotal'];available=values['MemAvailable']
 if not 0<=available<=total or total<=0:raise ValueError('Invalid memory counters')
 return dict(state='known',total_bytes=total,used_bytes=total-available,available_bytes=available,percent=round((total-available)*100/total,1))

def disk_usage(path='/'):
 s=os.statvfs(path);total=s.f_blocks*s.f_frsize;used=(s.f_blocks-s.f_bfree)*s.f_frsize;available=s.f_bavail*s.f_frsize
 if total<=0 or not 0<=used<=total or available<0:raise ValueError('Invalid filesystem counters')
 return dict(state='known',path=path,total_bytes=total,used_bytes=used,available_bytes=available,percent=round(used*100/total,1))

def sample():
 global _last,_snapshot
 now=datetime.now(timezone.utc).isoformat();value=dict(sampled_at=now,sample_interval_seconds=2)
 try:
  with open('/proc/stat') as f:current=[int(x) for x in f.readline().split()[1:9]]
  if len(current)!=8:raise ValueError('Invalid CPU counters')
  percent=cpu_percent(_last,current);_last=current
  value['cpu']=dict(state='known' if percent is not None else 'sampling',percent=percent,cores=os.cpu_count())
 except (OSError,ValueError):value['cpu']=dict(state='unavailable',percent=None)
 try:
  with open('/proc/meminfo') as f:mem={parts[0].rstrip(':'):int(parts[1])*1024 for line in f if len(parts:=line.split())>=2}
  value['memory']=memory_usage(mem)
 except (OSError,ValueError,KeyError):value['memory']=dict(state='unavailable',percent=None)
 try:value['disk']=disk_usage('/')
 except (OSError,ValueError):value['disk']=dict(state='unavailable',percent=None)
 with _LOCK:_snapshot=(time.monotonic(),value)

def _run():
 while not STOP.is_set():sample();STOP.wait(2)

def start():
 global _thread,_last,_snapshot
 if _thread and _thread.is_alive():return
 STOP.clear();_last=None;_snapshot=None;_thread=threading.Thread(target=_run,name='host-metrics',daemon=True);_thread.start()

def stop():
 STOP.set()
 if _thread and _thread is not threading.current_thread():_thread.join(timeout=.5)

def snapshot():
 with _LOCK:
  if _snapshot is None or time.monotonic()-_snapshot[0]>10:
   return dict(sampled_at=None,cpu=dict(state='unavailable',percent=None),memory=dict(state='unavailable',percent=None),disk=dict(state='unavailable',percent=None))
  return copy.deepcopy(_snapshot[1])
