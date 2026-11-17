"""Allowlisted diagnostic metadata; never collects prompts, files or raw logs."""
import datetime as dt
import re

PHASES = frozenset(('idle','queued','starting','running','stopping','completed','failed','interrupted','unknown','requesting','thinking','responding','preparing_tool','executing','compacting','background','finishing','requires_action','answering','approval_unknown'))
STATES = frozenset(('pending','answering','unknown','writing','written','delivered','completed','failed','interrupted','queued','rejected','accepted','received'))
def stamp(value):
    if not isinstance(value,str) or len(value)>40:return None
    try:
        parsed=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
        return value if parsed.tzinfo else None
    except ValueError:return None

def clean(value):
    text=str(value or '')[:1200]
    text=re.sub(r'(?is)-----BEGIN .*?-----.*?(?:-----END .*?-----|$)','[redacted]',text)
    text=re.sub(r'(?i)\b(?:authorization|proxy-authorization|cookie|set-cookie|password|passwd|token|api[_ -]?key|secret|credential)\s*[:=].*','[redacted]',text)
    text=re.sub(r'(?i)\b(?:bearer|basic)\s+\S+','[redacted]',text)
    text=re.sub(r'(?:sk-[\w-]+|gh[pousr]_[\w]+|github_pat_[\w]+|eyJ[\w.-]+)','[redacted]',text)
    text=re.sub(r'https?://\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?:/[^\s,;]+)+|[A-Za-z]:[\\/][^\s]+','[redacted]',text)
    text=re.sub(r'\b[0-9a-fA-F_-]{24,}\b','[redacted]',text)
    return re.sub(r'[\x00-\x1f\x7f]',' ',text)[:600]

def session_view(raw):
    a=raw.get('activity') or {}
    state=lambda v:v if v in PHASES else 'unknown'
    receipts={}
    for item in (raw.get('deliveries') or [])[:20]:
        value=item.get('state');value=value if value in STATES else 'unknown'
        receipts[value]=receipts.get(value,0)+1
    return dict(status=state(raw.get('status')),phase=state(raw.get('display_status') or a.get('phase')),
        process_alive=a.get('process_alive') if type(a.get('process_alive')) is bool else None,
        revision=raw.get('revision') if type(raw.get('revision')) is int else None,
        generation=raw.get('generation') if re.fullmatch(r'[0-9a-f]{32}',str(raw.get('generation',''))) else None,
        observed_at=stamp(a.get('checked_at')),started_at=stamp(a.get('started_at')),
        last_text_at=stamp(a.get('last_output_at')),last_tool_at=stamp(a.get('last_progress_at')),
        last_event_at=stamp(a.get('last_event_at')),last_success_at=stamp(a.get('last_success_at')),
        pending_messages=len(raw.get('queue') or []),pending_approvals=len(raw.get('approvals') or []),
        receipts=receipts,last_error=clean(a.get('error_reason')),
        resources=resource_view(raw.get('resources') or {}),
        catching_up=a.get('catching_up') is True)

def bundle(service,raw=None,available=True):
    allowed={k:v for k,v in service.items() if k in ('revision','process_count','active_sessions','unknown_sessions','pending_messages') and type(v) is int and v>=0}
    if re.fullmatch(r'[0-9a-f]{32}',str(service.get('generation',''))):allowed['generation']=service['generation']
    return dict(format_version=1,app_release='2026-11-15.batch4',created_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        executor_available=available,executor=allowed,session=session_view(raw) if raw is not None else None,
        scope='Metadata only; no prompts, tool bodies, project files, credentials or browser history.')

def resource_view(raw):
    result={k:v for k,v in raw.items() if k in ('available','finished','last_sample_unavailable') and type(v) is bool}
    for k in ('sampled_at','cpu_total_seconds','cpu_percent','memory_bytes','observed_peak_bytes','processes','oom_kill_count'):
        v=raw.get(k)
        if type(v) in (int,float) and v>=0:result[k]=v
    if raw.get('memory_kind') in ('rss_sum','cgroup'):result['memory_kind']=raw['memory_kind']
    result['peak_kind']='observed samples'
    return result
