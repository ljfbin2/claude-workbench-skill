"""Read-only, descriptor-scoped project browsing. No shell or write operations."""
import os,stat,subprocess,time,select,signal
from pathlib import Path
from worker.files import _session,open_project,FileIPCError
FLAGS=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC
LIMIT=256*1024

def directory(worker,sid,raw=''):
    base=_session(worker,sid)
    if not isinstance(raw,str) or '\x00' in raw or '\\' in raw:raise FileIPCError('Invalid project path',403)
    parts=raw.split('/') if raw else []
    if any(p in ('','.','..') for p in parts) or raw.startswith('/'):raise FileIPCError('Path is outside project',403)
    walk_flags=os.O_PATH|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC
    fd=os.open('/',walk_flags)
    try:
        for part in [*base.parts[1:],*parts]:
            child=os.open(part,walk_flags,dir_fd=fd);os.close(fd);fd=child
        readable=os.open('.',FLAGS,dir_fd=fd);os.close(fd);return readable
    except OSError:
        os.close(fd);raise FileIPCError('Project directory unavailable',404)

def tree(worker,sid,raw=''):
    fd=directory(worker,sid,raw)
    try:
        entries=[];truncated=False
        with os.scandir(fd) as scan:
            for entry in scan:
                if len(entries)>=1000:truncated=True;break
                try:info=entry.stat(follow_symlinks=False)
                except OSError:continue
                kind='directory' if stat.S_ISDIR(info.st_mode) else 'file' if stat.S_ISREG(info.st_mode) else 'blocked'
                entries.append(dict(name=entry.name,path=(raw+'/' if raw else '')+entry.name,kind=kind,size=info.st_size if kind=='file' else None))
        entries.sort(key=lambda e:(e['kind']!='directory',e['name'].casefold()))
        return dict(path=raw,entries=entries,truncated=truncated)
    finally:os.close(fd)

def text(worker,sid,raw):
    fd,length=open_project(worker,sid,raw)
    try:
        if length>1024*1024:raise FileIPCError('Text preview is limited to 1 MiB; download the original file',413)
        data=b''
        while len(data)<=1024*1024:
            block=os.read(fd,min(65536,1024*1024+1-len(data)))
            if not block:break
            data+=block
        if len(data)>1024*1024:raise FileIPCError('File changed; refresh the preview',409)
        if b'\0' in data:raise FileIPCError('Binary file; download or preview as an image',415)
        try:value=data.decode('utf-8-sig')
        except UnicodeDecodeError:raise FileIPCError('Only UTF-8 text preview is supported; download the original',415)
        return dict(text=value,size=len(data))
    finally:os.close(fd)

def git_read(worker,sid,command):
    root=directory(worker,sid)
    proc=None;git=None
    try:
        try:git=os.open('.git',FLAGS,dir_fd=root)
        except OSError:raise FileIPCError('Diff requires an independent Git repository at this work directory',409)
        args=['/usr/bin/git','--git-dir=/proc/self/fd/'+str(git),'--work-tree=/proc/self/fd/'+str(root),'--no-optional-locks','-c','core.fsmonitor=false','-c','core.hooksPath=/dev/null','-c','core.attributesFile=/dev/null','--no-pager',*command]
        env={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8','GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_OPTIONAL_LOCKS':'0','GIT_NO_LAZY_FETCH':'1','GIT_TERMINAL_PROMPT':'0'}
        proc=subprocess.Popen(args,cwd='/proc/self/fd/'+str(root),pass_fds=(root,git),env=env,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,start_new_session=True)
        chunks=[];size=0;deadline=time.monotonic()+4
        while True:
            left=deadline-time.monotonic()
            if left<=0:raise FileIPCError('Diff timed out; narrow the selected file',408)
            ready,_,_=select.select([proc.stdout],[],[],min(left,.2))
            if not ready:continue
            block=os.read(proc.stdout.fileno(),min(65536,LIMIT+1-size))
            if not block:break
            chunks.append(block);size+=len(block)
            if size>LIMIT:raise FileIPCError('Diff exceeds 256 KiB; inspect it with project tools',413)
        proc.wait(timeout=max(.1,deadline-time.monotonic()))
        if proc.returncode:raise FileIPCError('Git comparison unavailable; repository may have no HEAD commit',409)
        return b''.join(chunks)
    except subprocess.TimeoutExpired:raise FileIPCError('Diff timed out',408)
    finally:
        if proc is not None:
            if proc.poll() is None:
                try:os.killpg(proc.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                proc.wait()
            proc.stdout.close()
        if git is not None:os.close(git)
        os.close(root)

def diff(worker,sid,raw):
    if not isinstance(raw,str) or not raw or raw.startswith('/') or '\\' in raw or any(p in ('','.','..') for p in raw.split('/')):raise FileIPCError('Invalid project path',403)
    parent,name=raw.rsplit('/',1) if '/' in raw else ('',raw)
    fd=directory(worker,sid,parent)
    try:
        try:
            info=os.stat(name,dir_fd=fd,follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):raise FileIPCError('Only regular file diffs are available',403)
        except FileNotFoundError:pass
    finally:os.close(fd)
    data=git_read(worker,sid,['diff','--no-ext-diff','--no-textconv','--no-color','HEAD','--',raw])
    return dict(text=data.decode('utf-8',errors='replace'),basis='HEAD',includes_untracked=False)

def changes(worker,sid,raw=''):
    data=git_read(worker,sid,['status','--porcelain=v1','-z','--untracked-files=all','--ignore-submodules=all'])
    parts=data.split(b'\0');items=[];i=0
    while i<len(parts):
        entry=parts[i];i+=1
        if not entry:continue
        code=entry[:2].decode('ascii','replace');path=entry[3:].decode('utf-8','replace');old=None
        if 'R' in code or 'C' in code:
            if i<len(parts):old=parts[i].decode('utf-8','replace');i+=1
        kind='untracked' if code=='??' else 'renamed' if 'R' in code else 'deleted' if 'D' in code else 'added' if 'A' in code else 'conflict' if 'U' in code or code in ('AA','DD') else 'modified'
        items.append(dict(path=path,previous_path=old,status=kind,code=code))
        if len(items)>1000:raise FileIPCError('More than 1000 changed files; inspect with project tools',413)
    return dict(entries=items,basis='HEAD/index/worktree',scope='Current shared project state; not attributed to a Claude turn')
