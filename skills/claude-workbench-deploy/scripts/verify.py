#!/usr/bin/env python3
"""Explicit-target API acceptance. Creates one ordinary test user, never real AI tasks."""
import argparse,json,os,re,secrets,sys
from pathlib import Path
from urllib.parse import urlsplit
import httpx

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base-url',required=True);p.add_argument('--username',required=True);p.add_argument('--password-file',type=Path,required=True);p.add_argument('--report-file',type=Path);a=p.parse_args()
 u=urlsplit(a.base_url)
 if u.scheme not in ('http','https') or u.username or u.password or (u.scheme=='http' and u.hostname not in ('localhost','127.0.0.1','::1')):p.error('Use HTTPS or a loopback test URL')
 if a.password_file.stat().st_mode&0o077:p.error('Password file must be private')
 if a.report_file and a.report_file.resolve().is_relative_to(Path(__file__).resolve().parents[3]):p.error('Reports containing test credentials must be outside the repository')
 if a.report_file and a.report_file.exists():p.error('Use a new private report path')
 os.umask(0o077)
 password=a.password_file.read_text().strip();report={'checks':[],'real_provider_tasks':'not tested by this script'}
 def check(name,condition):
  if not condition:raise AssertionError(name)
  report['checks'].append(name)
 def login(c,user,pw):
  r=c.post('/api/auth/login',json={'username':user,'password':pw});check('login accepted',r.status_code==200)
  check('session cookie HttpOnly','httponly' in r.headers.get('set-cookie','').lower())
  c.headers['X-CSRF-Token']=r.json()['csrf_token'];return r.json()
 with httpx.Client(base_url=a.base_url,timeout=20,follow_redirects=False) as admin,httpx.Client(base_url=a.base_url,timeout=20) as ordinary:
  check('anonymous business page blocked',admin.get('/').status_code==303)
  for path in ('/api/auth/me','/api/sessions','/api/providers','/api/directories','/api/system-metrics','/api/files/missing'):
   check('anonymous blocked '+path,admin.get(path).status_code in (401,403))
  page=admin.get('/login');check('public login',page.status_code==200)
  check('project title',bool(re.search(r'<title>[^<]+</title>',page.text)))
  check('wrong password rejected',admin.post('/api/auth/login',json={'username':a.username,'password':secrets.token_urlsafe(24)}).status_code==401)
  who=login(admin,a.username,password);check('administrator role',who['user']['is_admin'])
  html=admin.get('/');check('business page available',html.status_code==200)
  resources=set(re.findall(r'(?:src|href)=[\'"](/static/[^\'"?]+)',html.text+page.text))
  check('branding favicon declared',any('.svg' in x or '.ico' in x for x in resources))
  for resource in resources:check('static '+resource,admin.get(resource).status_code==200)
  check('provider endpoint',admin.get('/api/providers').status_code==200)
  check('directories endpoint',admin.get('/api/directories').status_code==200)
  check('metrics endpoint',admin.get('/api/system-metrics').status_code==200)
  user='verify_'+secrets.token_hex(6);pw=secrets.token_urlsafe(24)
  check('missing csrf denied',ordinary.post('/api/users',json={'username':user,'password':pw}).status_code in (401,403))
  check('create ordinary test user',admin.post('/api/users',json={'username':user,'password':pw}).status_code==201)
  check('duplicate rejected',admin.post('/api/users',json={'username':user,'password':pw}).status_code==409)
  who=login(ordinary,user,pw);check('ordinary role',not who['user']['is_admin'])
  check('ordinary management denied',ordinary.post('/api/users',json={'username':'forbidden','password':pw}).status_code==403)
  check('ordinary metrics denied',ordinary.get('/api/system-metrics').status_code==403)
  check('ordinary browser denied',ordinary.get('/server-browser').status_code in (403,404))
  old=ordinary.cookies.get('web_ai_session');check('logout',ordinary.post('/api/auth/logout').status_code==200)
  ordinary.cookies.set('web_ai_session',old);check('old session invalidated',ordinary.get('/api/auth/me').status_code==401)
  # Persistence can be checked after a separately authorized gateway restart.
  report['test_user']=user
  if a.report_file:
   a.report_file.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
   if a.report_file.exists():raise ValueError('Use a new private report path')
   with os.fdopen(os.open(a.report_file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as output:json.dump({**report,'test_password':pw},output,indent=2)
  admin.post('/api/auth/logout')
 print(json.dumps({'passed':len(report['checks']),'test_user_created':True,'real_provider_tasks':'not tested'},indent=2))
if __name__=='__main__':
 try:main()
 except Exception as exc:
  print('Verification failed: '+(str(exc) if isinstance(exc,AssertionError) else type(exc).__name__),file=sys.stderr);sys.exit(1)
