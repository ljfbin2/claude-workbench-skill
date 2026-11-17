#!/usr/bin/env python3
"""Package hygiene scan. Reports file/rule names, never matching private text."""
import argparse,hashlib,ipaddress,json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
BLOCKED={'.env','.venv','node_modules','__pycache__','.ssh','chrome-profile','uploads','backups'}
SUFFIXES={'.pyc','.pyo','.pem','.key','.db','.sqlite','.sqlite3','.log','.har','.zip','.tar','.gz'}
TEXT={'.py','.js','.mjs','.css','.html','.svg','.md','.yaml','.yml','.json','.txt'}
RULES={
 'private-key':r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
 'provider-token':r'(?:sk-ant-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})',
 'windows-personal-path':r'[A-Za-z]:[\\/](?:Users|Desktop|Documents)[\\/]',
}
def scan(root):
 findings=[];count=0
 for path in sorted(root.rglob('*')):
  rel=path.relative_to(root)
  if '.git' in rel.parts:continue
  if path.is_symlink():findings.append((str(rel),'symlink'));continue
  if not path.is_file():continue
  count+=1
  if any(x in BLOCKED for x in rel.parts) or path.suffix.lower() in SUFFIXES or path.name.endswith('.env'):findings.append((str(rel),'blocked-file'));continue
  if path.suffix.lower() not in TEXT and path.name not in ('.gitignore','LICENSE','purify-LICENSE'):findings.append((str(rel),'unexpected-file'));continue
  try:text=path.read_text()
  except UnicodeError:findings.append((str(rel),'binary'));continue
  for name,pattern in RULES.items():
   if re.search(pattern,text):findings.append((str(rel),name))
  # Vendor attribution is preserved. Documentation and test fixture loopbacks are safe.
  if '/vendor/' not in '/'+str(rel) and not rel.name.startswith(('LICENSE','THIRD_PARTY')):
   for candidate in re.findall(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])',text):
    try:address=ipaddress.ip_address(candidate)
    except ValueError:continue
    if address.is_global:findings.append((str(rel),'public-ip'));break
 return count,findings
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=ROOT);a=p.parse_args();n,issues=scan(a.root)
 print(json.dumps({'files':n,'passed':not issues,'findings':[{'path':x,'rule':y} for x,y in issues]},indent=2));sys.exit(bool(issues))
