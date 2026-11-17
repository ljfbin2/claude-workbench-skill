"""Read-only per-execution cgroup CPU and sampled RSS, including child processes."""
import json,os,threading,time,sqlite3,logging
from pathlib import Path
SCHEMA='CREATE TABLE IF NOT EXISTS execution_resources(session_id TEXT PRIMARY KEY,scope TEXT NOT NULL,snapshot TEXT NOT NULL);'
def snapshot(path,previous=None):
    stamp=time.time();data={'sampled_at':stamp,'available':False,'memory_kind':'rss_sum','scope':'execution lifetime including child processes','peak_kind':'observed samples'}
    if path is None or not path.is_dir():return data
    try:
        stat=dict(line.split() for line in (path/'cpu.stat').read_text().splitlines());cpu=int(stat['usage_usec'])
        pids=set()
        for item in [path,*[p for p in path.rglob('*') if p.is_dir()]]:
            try:pids.update(int(x) for x in (item/'cgroup.procs').read_text().split())
            except FileNotFoundError:continue
        rss=0;observed=0
        for pid in pids:
            try:rss+=int((Path('/proc')/str(pid)/'statm').read_text().split()[1])*os.sysconf('SC_PAGE_SIZE');observed+=1
            except (FileNotFoundError,ProcessLookupError):continue
        memory_file=path/'memory.current'
        if memory_file.exists():memory=int(memory_file.read_text());data['memory_kind']='cgroup'
        else:memory=rss
        data.update(available=True,cpu_total_seconds=cpu/1e6,memory_bytes=memory,processes=observed,cpu_percent=None,observed_peak_bytes=max(memory,(previous or {}).get('observed_peak_bytes',0)))
        if previous and previous.get('available') and stamp>previous['sampled_at']:
            data['cpu_percent']=round(max(0,(cpu/1e6-previous['cpu_total_seconds'])/(stamp-previous['sampled_at'])*100),1)
        if (path/'memory.events').exists():
            events=dict(line.split() for line in (path/'memory.events').read_text().splitlines());data['oom_kill_count']=int(events.get('oom_kill',0))
    except (OSError,ValueError,KeyError):data['reason']='Resource sample unavailable'
    return data
class Monitor:
    def __init__(self,worker,sid,path):
        self.worker,self.sid,self.path=worker,sid,path;self.done=threading.Event();self.previous=None
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
    def sample(self,final=False):
        data=snapshot(self.path,self.previous);data['finished']=final
        if data['available']:self.previous=data
        elif self.previous:data={**self.previous,'finished':final,'last_sample_unavailable':True}
        with self.worker.db() as db:db.execute('INSERT OR REPLACE INTO execution_resources VALUES (?,?,?)',(self.sid,self.path.name if self.path else '',json.dumps(data)))
    def run(self):
        while not self.done.is_set():
            try:self.sample()
            except (OSError,ValueError,sqlite3.Error):logging.getLogger(__name__).warning('Execution resource sample unavailable')
            self.done.wait(5)
    def stop(self):
        self.done.set();self.thread.join(timeout=6)
        try:self.sample(final=True)
        except (OSError,ValueError,sqlite3.Error):logging.getLogger(__name__).warning('Execution resource sample unavailable')
def read(worker,sid):
    with worker.db() as db:row=db.execute('SELECT snapshot FROM execution_resources WHERE session_id=?',(sid,)).fetchone()
    return json.loads(row[0]) if row else {'available':False,'reason':'No execution resource sample yet'}
