"""Private remote browser desktop. No keyboard, clipboard or URL logging."""
import json,os,secrets,signal,socketserver,subprocess,threading,time
from pathlib import Path
from urllib.parse import urlsplit
HOME=Path(os.environ['HOME']);RUN=Path(os.environ.get('WEB_AI_BROWSER_RUNTIME','/run/claude-workbench-browser'))
PROFILE=HOME/'chrome-profile';DISPLAY=os.environ.get('WEB_AI_BROWSER_DISPLAY',':98');XAUTH=RUN/'Xauthority';children=[];lock=threading.Lock()
ENV={**os.environ,'DISPLAY':DISPLAY,'XAUTHORITY':str(XAUTH)}
BASE=[os.environ.get('WEB_AI_BROWSER_BIN','/usr/bin/google-chrome-stable'),'--user-data-dir='+str(PROFILE),'--no-first-run','--no-default-browser-check','--disable-session-crashed-bubble']
def launch(argv):
 p=subprocess.Popen(argv,env=ENV,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);children.append(p);return p

def open_url(url):
 parsed=urlsplit(url)
 if parsed.scheme!='https' or parsed.hostname not in {'claude.ai','claude.com','platform.claude.com','www.claude.ai'} or parsed.username or parsed.password or len(url)>16000:
  raise ValueError('Unsupported destination')
 launch(BASE+['--new-tab',url])

class Control(socketserver.StreamRequestHandler):
 def handle(self):
  self.request.settimeout(3)
  try:
   raw=self.rfile.readline(17000)
   if len(raw)>16500:raise ValueError('Request too large')
   req=json.loads(raw)
   if req.get('command')=='open':
    with lock:open_url(req['url'])
   elif req.get('command')!='status':raise ValueError('Invalid command')
   value={'available':vnc.poll() is None and chrome.poll() is None,'width':1366,'height':900}
  except (ValueError,KeyError,OSError):value={'available':False,'error':'Browser request failed'}
  self.wfile.write(json.dumps(value).encode()+b'\n')

if __name__=='__main__':
 os.umask(0o077);PROFILE.mkdir(mode=0o700,parents=True,exist_ok=True)
 RUN.mkdir(mode=0o770,parents=True,exist_ok=True)
 XAUTH.touch(mode=0o600);subprocess.run(['/usr/bin/xauth','-f',str(XAUTH),'add',DISPLAY,'.',secrets.token_hex(16)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 sock=RUN/'desktop.sock';control=RUN/'control.sock'
 for p in [sock,control]:
  if p.exists():p.unlink()
 vnc=launch(['/usr/bin/Xtigervnc',DISPLAY,'-geometry','1366x900','-depth','24','-auth',str(XAUTH),'-nolisten','tcp','-rfbport','-1','-rfbunixpath',str(sock),'-rfbunixmode','432','-SecurityTypes','None','-desktop','Server browser'])
 for _ in range(100):
  if sock.exists():break
  if vnc.poll() is not None:raise RuntimeError('Desktop did not start')
  time.sleep(.05)
 if not sock.exists():raise RuntimeError('Desktop socket unavailable')
 os.chmod(sock,0o660)
 launch(['/usr/bin/openbox','--sm-disable'])
 launch(['/usr/bin/vncconfig','-nowin'])
 chrome=launch(BASE+['--new-window','https://claude.ai/'])
 server=socketserver.ThreadingUnixStreamServer(str(control),Control);os.chmod(control,0o660)
 threading.Thread(target=server.serve_forever,daemon=True).start()
 def stop(*args):
  for p in reversed(children):
   if p.poll() is None:p.terminate()
  raise SystemExit(0)
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 while True:
  if vnc.poll() is not None:raise RuntimeError('Desktop exited')
  with lock:
   children[:]=[p for p in children if p.poll() is None]
   if chrome.poll() is not None:chrome=launch(BASE+['--new-window','https://claude.ai/'])
  time.sleep(2)
