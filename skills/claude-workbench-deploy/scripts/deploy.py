#!/usr/bin/env python3
"""First-install planner/applicator. Refuses existing targets; never imports personal data."""
import importlib.util
import argparse,getpass,hashlib,json,os,pwd,re,secrets,shutil,socket,subprocess,sys,time
import urllib.request
from pathlib import Path
SKILL=Path(__file__).resolve().parents[1]
def run(*args):return subprocess.run(args,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True).stdout.strip()
def safe_path(value):
 p=Path(value)
 if not p.is_absolute() or '..' in p.parts or not re.fullmatch(r'/[A-Za-z0-9_./-]+',value) or str(p) in ('/','/etc','/opt','/var','/var/lib','/home','/usr'):raise ValueError('Use a dedicated absolute path without spaces or traversal')
 if p.is_symlink() or any(x.is_symlink() for x in p.parents):raise ValueError('Installation paths cannot use symlinks')
 return p

def validated_assets():
 result={}
 for kind in ('app','browser'):
  base=SKILL/'assets'/kind;manifest=json.loads((SKILL/'assets'/(kind+'.sha256.json')).read_text());result[kind]={}
  if not isinstance(manifest,dict) or not manifest:raise ValueError('Missing asset manifest')
  for name,digest in manifest.items():
   relative=Path(name)
   if relative.is_absolute() or '..' in relative.parts or str(relative)!=name or not re.fullmatch(r'[A-Za-z0-9_./-]+',name):raise ValueError('Unsafe asset path')
   source=base/relative
   if source.is_symlink() or any(x.is_symlink() for x in source.parents) or not source.resolve().is_relative_to(base.resolve()) or not source.is_file():raise ValueError('Unsafe asset source')
   content=source.read_bytes()
   if not isinstance(digest,str) or not re.fullmatch(r'[0-9a-f]{64}',digest) or hashlib.sha256(content).hexdigest()!=digest:raise ValueError('Source integrity check failed')
   result[kind][name]=content
 return result

def fetch_brand_asset():
 # Download for this installation only. UXWing prohibits asset redistribution.
 url='https://uxwing.com/wp-content/themes/uxwing/download/brands-and-social-media/claude-code-icon.svg'
 request=urllib.request.Request(url,headers={'User-Agent':'Claude-Workbench-Installer/1.0'})
 with urllib.request.urlopen(request,timeout=20) as response:content=response.read(65537)
 if len(content)>65536 or hashlib.sha256(content).hexdigest()!='1c2e3c9115efd771a82915391766fa2809ed18528a7b6e9d256b9329fd118fc8':raise ValueError('Upstream brand asset changed; review its source/license before installation')
 return content

def parse():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--apply',action='store_true');p.add_argument('--instance',default='claude-workbench');p.add_argument('--app-dir');p.add_argument('--data-dir');p.add_argument('--config-dir');p.add_argument('--web-port',type=int,default=18752);p.add_argument('--worker-port',type=int,default=18751);p.add_argument('--domain');p.add_argument('--admin-user',default='admin');p.add_argument('--password-file',type=Path);p.add_argument('--claude-bin',type=Path,required=True);p.add_argument('--runtime-python',type=Path,help='Use a preinstalled compatible venv instead of creating one');p.add_argument('--with-browser',action='store_true');p.add_argument('--browser-bin',type=Path,default=Path('/usr/bin/google-chrome-stable'));p.add_argument('--browser-display',default=':98');p.add_argument('--coder-model',default='');p.add_argument('--reviewer-model',default='');p.add_argument('--quota-node');p.add_argument('--quota-js-sdk');p.add_argument('--quota-sdk-version');p.add_argument('--quota-cli-version');return p.parse_args()
def port_free(port):
 with socket.socket() as s:
  try:s.bind(('127.0.0.1',port));return True
  except OSError:return False

def plan(a):
 if not re.fullmatch(r'[a-z][a-z0-9-]{2,23}',a.instance):raise ValueError('Invalid instance name')
 if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',a.admin_user):raise ValueError('Invalid administrator name')
 if not 1024<=a.web_port<=65535 or not 1024<=a.worker_port<=65535 or a.web_port==a.worker_port:raise ValueError('Select distinct unprivileged loopback ports')
 if a.domain and not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?',a.domain):raise ValueError('Invalid DNS hostname')
 app=safe_path(a.app_dir or '/opt/'+a.instance);data=safe_path(a.data_dir or '/var/lib/'+a.instance);conf=safe_path(a.config_dir or '/etc/'+a.instance)
 if any(x==y or x.is_relative_to(y) or y.is_relative_to(x) for i,x in enumerate((app,data,conf)) for y in (app,data,conf)[i+1:]):raise ValueError('App, data and configuration must be separate directories')
 for path in (app,data,conf):
  if path.exists():raise ValueError('Target already exists; inspect and repair it instead of reinitializing: '+str(path))
 if not port_free(a.web_port) or not port_free(a.worker_port):raise ValueError('Selected port is already occupied')
 if a.domain and (not port_free(80) or not port_free(443)):raise ValueError('An existing public web service owns port 80/443; prepare an explicit vhost integration, do not overwrite it')
 if not a.claude_bin.is_absolute() or not a.claude_bin.is_file() or not os.access(a.claude_bin,os.X_OK):raise ValueError('Provide an installed official Claude CLI executable')
 if a.runtime_python and (not a.runtime_python.is_absolute() or not a.runtime_python.is_file()):raise ValueError('Runtime Python is unavailable')
 validated_assets()
 for executable in [a.claude_bin,a.runtime_python]:
  if executable and (any(str(p).startswith(('/home/','/root/')) for p in (executable,executable.resolve()))):raise ValueError('Service executables must be globally readable outside protected home directories')
 if a.with_browser and (not a.browser_bin.is_file() or not re.fullmatch(r':[0-9]{1,3}',a.browser_display)):raise ValueError('Browser binary or display is invalid')
 for value in (a.coder_model,a.reviewer_model):
  if value and not re.fullmatch(r'[A-Za-z0-9_.-]{1,120}',value):raise ValueError('Role models must be actual model identifiers')
 if bool(a.coder_model)!=bool(a.reviewer_model):raise ValueError('Configure both collaboration roles, or neither')
 for suffix in ('-web','-exec','-browser','-proxy'):
  try:pwd.getpwnam(a.instance+suffix)
  except KeyError:pass
  else:raise ValueError('Service identity already exists; choose a new instance or inspect prior installation')
 for suffix in ('web','worker','browser','proxy'):
  if Path('/etc/systemd/system/'+a.instance+'-'+suffix+'.service').exists():raise ValueError('A target unit already exists')
 if not Path('/sys/fs/cgroup/cgroup.controllers').is_file() or not Path('/run/systemd/system').is_dir():raise ValueError('systemd and cgroup v2 are required')
 needed=['git','systemctl','runuser']+(['caddy'] if a.domain else [])+(['Xtigervnc','openbox','vncconfig','xauth'] if a.with_browser else [])
 if not a.runtime_python and importlib.util.find_spec('ensurepip') is None:raise ValueError('Install the matching python3-venv package before applying the fresh installation')
 missing=[name for name in needed if not shutil.which(name)]
 if missing:raise ValueError('Install official prerequisites first: '+', '.join(missing))
 return dict(instance=a.instance,app=str(app),data=str(data),config=str(conf),gateway_port=a.web_port,worker_port=a.worker_port,public_https=bool(a.domain),domain=a.domain,browser=a.with_browser,existing_service_changes='none',credential_migration='none')
def write(path,text,mode=0o600):
 path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text);path.chmod(mode)
def env(path,values):
 for value in values.values():
  if '\n' in str(value) or '\r' in str(value) or '"' in str(value):raise ValueError('Invalid environment value')
 write(path,''.join(k+'="'+str(v).replace('\\','\\\\')+'"\n' for k,v in values.items()))
def own(path,user,mode):
 u=pwd.getpwnam(user);path.mkdir(mode=mode,parents=True,exist_ok=True);path.chmod(mode);os.chown(path,u.pw_uid,u.pw_gid)
def unit(name,user,app,python,module,config,writable,worker=False,extras=''):
 text='[Unit]\nDescription=Claude web workbench '+module+'\nAfter=network.target\n\n[Service]\nType=simple\nUser='+user+'\nGroup='+user+'\nWorkingDirectory='+str(app)+'\nEnvironmentFile='+str(config)+'\nExecStart='+str(python)+' '+module+'\nRestart=on-failure\nRestartSec=3s\nKillMode=control-group\nTimeoutStopSec=8s\nUMask=0077\nNoNewPrivileges=yes\nProtectSystem=strict\nProtectHome=yes\nPrivateTmp=yes\nPrivateDevices=yes\nProtectKernelTunables=yes\nProtectKernelModules=yes\nProtectControlGroups='+('no' if worker else 'yes')+'\nDelegate='+('yes' if worker else 'no')+'\nReadWritePaths='+' '.join(map(str,writable))+'\n'+extras+'\n[Install]\nWantedBy=multi-user.target\n'
 write(Path('/etc/systemd/system')/name,text,0o644)
def apply(a,p):
 if os.geteuid()!=0:raise ValueError('--apply requires root')
 if a.password_file:
  q=a.password_file.resolve(strict=True)
  if q.is_relative_to(SKILL) or q.stat().st_mode&0o077:raise ValueError('Password file must be private and outside the skill tree')
  password=q.read_text().strip()
 else:
  password=getpass.getpass('New administrator password (12+ characters): ')
  if password!=getpass.getpass('Repeat password: '):raise ValueError('Passwords do not match')
 if len(password)<12 or any(c in password for c in '\r\n"'):raise ValueError('Use 12+ characters without line breaks or a double quote')
 bundled=validated_assets();brand_asset=fetch_brand_asset()
 app,data,conf=(Path(p[k]) for k in ('app','data','config'));app.mkdir(mode=0o755,parents=True);data.mkdir(mode=0o711,parents=True);conf.mkdir(mode=0o700,parents=True)
 # Copy only pinned regular source files, never a profile, existing database, binary or secret.
 for name,content in bundled['app'].items():
  dest=app/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(content);dest.chmod(0o644)
 (app/'frontend/claude-code-v1.svg').write_bytes(brand_asset);(app/'frontend/claude-code-v1.svg').chmod(0o644)
 browser=app/'browser';browser.mkdir();(browser/'desktop.py').write_bytes(bundled['browser']['desktop.py']);(browser/'desktop.py').chmod(0o644)
 identities=[a.instance+'-'+x for x in ('web','exec')]+([a.instance+'-browser'] if a.with_browser else [])+([a.instance+'-proxy'] if a.domain else [])
 for user in identities:run('useradd','--system','--user-group','--no-create-home','--home-dir',str(data/('home' if user.endswith('-exec') else user.rsplit('-',1)[-1])),'--shell','/usr/sbin/nologin',user)
 web=a.instance+'-web';worker=a.instance+'-exec';own(data/'gateway',web,0o700);own(data/'worker',worker,0o700);own(data/'worker/uploads',worker,0o700);own(data/'home',worker,0o700);own(data/'workspaces',worker,0o700)
 python=a.runtime_python or app/'.venv/bin/python'
 if not a.runtime_python:
  run(sys.executable,'-m','venv',str(app/'.venv'));run(str(python),'-m','pip','install','--disable-pip-version-check','-r',str(app/'requirements-lock.txt'))
 run(str(python),'-c','import fastapi,httpx,uvicorn,claude_agent_sdk')
 run('runuser','-u',worker,'--',str(a.claude_bin),'--version')
 token=secrets.token_urlsafe(48);secret=secrets.token_urlsafe(48);unit_base=a.instance+'-worker.service';runtime='/run/'+a.instance+'-browser'
 shared={'WEB_AI_WORKER_TOKEN':token,'WEB_AI_CODER_MODEL':a.coder_model,'WEB_AI_REVIEWER_MODEL':a.reviewer_model,'TZ':'UTC','LANG':'C.UTF-8'}
 gateway={**shared,'WEB_AI_DATA_DIR':str(data/'gateway'),'WEB_AI_SESSION_SECRET':secret,'WEB_AI_WORKER_URL':'http://127.0.0.1:'+str(a.worker_port),'WEB_AI_ALLOWED_ROOTS':str(data/'workspaces'),'WEB_AI_ADMIN_USERNAME':a.admin_user,'WEB_AI_ADMIN_PASSWORD':password,'WEB_AI_HTTPS':int(bool(a.domain)),'WEB_AI_BROWSER_RUNTIME':runtime}
 execution={**shared,'WEB_AI_WORKER_DB':str(data/'worker/events.sqlite3'),'WEB_AI_UPLOAD_ROOT':str(data/'worker/uploads'),'WEB_AI_WORKSPACE_ROOTS':str(data/'workspaces'),'WEB_AI_CLAUDE_HOME':str(data/'home'),'WEB_AI_CLAUDE_BIN':str(a.claude_bin),'WEB_AI_WORKER_PORT':a.worker_port,'WEB_AI_WORKER_UNIT':unit_base,'WEB_AI_TASK_CGROUP':'/sys/fs/cgroup/system.slice/'+unit_base,'HOME':str(data/'home')}
 for key,option in [('WEB_AI_QUOTA_NODE',a.quota_node),('WEB_AI_JS_SDK_DIR',a.quota_js_sdk),('WEB_AI_QUOTA_SDK_VERSION',a.quota_sdk_version),('WEB_AI_QUOTA_CLI_VERSION',a.quota_cli_version)]:
  if option:execution[key]=option
 env(conf/'web.env',gateway);env(conf/'worker.env',execution)
 unit(unit_base,worker,app,python,'-m worker',conf/'worker.env',[data/'worker',data/'home',data/'workspaces',execution['WEB_AI_TASK_CGROUP']],worker=True)
 extra=''
 if a.with_browser:
  group=a.instance+'-browser';run('usermod','-a','-G',group,web);own(data/'browser',group,0o700)
  env(conf/'browser.env',{'HOME':str(data/'browser'),'WEB_AI_BROWSER_RUNTIME':runtime,'WEB_AI_BROWSER_BIN':str(a.browser_bin),'WEB_AI_BROWSER_DISPLAY':a.browser_display,'LANG':'C.UTF-8','TZ':'UTC'})
  unit(a.instance+'-browser.service',group,app,python,str(browser/'desktop.py'),conf/'browser.env',[data/'browser',runtime],extras='RuntimeDirectory='+a.instance+'-browser\nRuntimeDirectoryMode=0750\n')
 unit(a.instance+'-web.service',web,app,python,'-m uvicorn backend.app:app --host 127.0.0.1 --port '+str(a.web_port),conf/'web.env',[data/'gateway'])
 if a.domain:
  proxy=a.instance+'-proxy';own(data/'proxy',proxy,0o700)
  write(conf/'Caddyfile',a.domain+' {\n request_body {\n  max_size 520MiB\n }\n reverse_proxy 127.0.0.1:'+str(a.web_port)+'\n}\n',0o644)
  # The proxy receives only its public config, never the gateway/worker environment.
  public=app/'Caddyfile';shutil.copyfile(conf/'Caddyfile',public);public.chmod(0o644)
  run('caddy','validate','--config',str(public),'--adapter','caddyfile')
  write(Path('/etc/systemd/system')/(a.instance+'-proxy.service'),'[Unit]\nDescription=Workbench HTTPS entry\nAfter=network.target\n[Service]\nUser='+proxy+'\nGroup='+proxy+'\nEnvironment=HOME='+str(data/'proxy')+'\nExecStart='+shutil.which('caddy')+' run --config '+str(public)+' --adapter caddyfile\nRestart=on-failure\nAmbientCapabilities=CAP_NET_BIND_SERVICE\nNoNewPrivileges=true\nProtectSystem=strict\nReadWritePaths='+str(data/'proxy')+'\n[Install]\nWantedBy=multi-user.target\n',0o644)
 run('systemctl','daemon-reload');units=[unit_base,a.instance+'-web.service']+([a.instance+'-browser.service'] if a.with_browser else [])+([a.instance+'-proxy.service'] if a.domain else [])
 run('systemctl','enable','--now',*units)
 end=time.monotonic()+25
 while time.monotonic()<end:
  try:
   import urllib.request
   with urllib.request.urlopen('http://127.0.0.1:'+str(a.web_port)+'/login',timeout=2) as r:
    if r.status==200:break
  except OSError:time.sleep(.2)
 else:raise RuntimeError('Gateway did not become ready; inspect metadata-only service logs without printing environment files')
 gateway.pop('WEB_AI_ADMIN_PASSWORD');env(conf/'web.env',gateway);run('systemctl','restart',a.instance+'-web.service')
 write(conf/'installation.json',json.dumps({**p,'units':units,'admin_user':a.admin_user,'credentials_initialized':True},indent=2))
 print(json.dumps({'installed':True,'entry':('https://'+a.domain) if a.domain else 'loopback only: use an SSH tunnel','units':units,'next':'Complete a new official Claude login under the isolated executor HOME; run verify.py'},indent=2))
if __name__=='__main__':
 try:
  args=parse();result=plan(args)
  if args.apply:apply(args,result)
  else:print(json.dumps(result,indent=2))
 except (ValueError,OSError,subprocess.CalledProcessError) as exc:
  # Never print subprocess arguments/output: they may contain private environment or passwords.
  print('Deployment stopped: '+(str(exc) if isinstance(exc,ValueError) else type(exc).__name__)+'. Preserve partial state and inspect; do not reinitialize it.',file=sys.stderr);sys.exit(1)
