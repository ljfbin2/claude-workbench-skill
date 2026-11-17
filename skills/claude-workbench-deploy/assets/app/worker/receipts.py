"""Match explicit CLI receipts; verify local process-group release conservatively."""
import os
from pathlib import Path

def accepted_ids(message,session_id):
 if message.get('session_id')!=session_id or message.get('parent_tool_use_id') is not None:return []
 kind=message.get('type');ids=[]
 if kind=='user' and message.get('isReplay') is True:ids=[message.get('uuid')]
 elif kind=='command_lifecycle' and message.get('state') in ('queued','started'):ids=[message.get('command_uuid')]
 elif kind in ('assistant','stream_event','result') or (kind=='system' and (message.get('subtype') in ('init','thinking_tokens') or message.get('subtype')=='status' and message.get('status') in ('requesting','compacting'))):
  many=message.get('user_message_uuids')
  ids=many if isinstance(many,list) else []
  ids=ids[:64]+[message.get('user_message_uuid')]
 return list(dict.fromkeys(v for v in ids if isinstance(v,str) and len(v)==36))

def group_released(pgid):
 if pgid is None:return True
 if os.name!='posix':return False
 try:os.killpg(pgid,0)
 except ProcessLookupError:return True
 except (PermissionError,OSError):return False
 # Reaped leaders can leave zombie group members; zombies cannot execute.
 try:
  for entry in Path('/proc').iterdir():
   if not entry.name.isdigit():continue
   try:fields=(entry/'stat').read_text().rsplit(') ',1)[1].split()
   except FileNotFoundError:continue
   if int(fields[2])==pgid and fields[0] not in ('Z','X'):return False
 except (OSError,ValueError,IndexError):return False
 return True


def execution_released(pgid,identity):
 if not group_released(pgid):return False
 if identity is None:
  # Non-service test harnesses can prove only their own process group.
  return not os.environ.get('WEB_AI_WORKER_UNIT')
 from worker.recovery import provenance
 if provenance()!=identity:return False
 try:
  group=next(v[3:] for v in Path('/proc/self/cgroup').read_text().splitlines() if v.startswith('0::'))
  if not group.startswith('/') or '..' in Path(group).parts:return False
  members=Path('/sys/fs/cgroup')/group.lstrip('/')/'cgroup.procs'
  for raw in members.read_text().split():
   pid=int(raw)
   if pid==os.getpid():continue
   try:state=(Path('/proc')/raw/'stat').read_text().rsplit(') ',1)[1].split()[0]
   except FileNotFoundError:continue
   if state not in ('Z','X'):return False
  return True
 except (OSError,ValueError,IndexError,StopIteration):return False
