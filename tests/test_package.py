"""Boundary regressions. Run on the deployment server; no provider calls."""
import hashlib,importlib.util,json,socket,tempfile,unittest
from unittest.mock import patch
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];SKILL=ROOT/'skills/claude-workbench-deploy'
def module(name):
 spec=importlib.util.spec_from_file_location(name,SKILL/'scripts'/(name+'.py'));obj=importlib.util.module_from_spec(spec);spec.loader.exec_module(obj);return obj
D=module('deploy');P=module('privacy_scan')
class PackageTests(unittest.TestCase):
 def test_asset_manifests_exact(self):
  for kind in ('app','browser'):
   base=SKILL/'assets'/kind;manifest=json.loads((SKILL/'assets'/(kind+'.sha256.json')).read_text())
   self.assertEqual(set(manifest),{str(p.relative_to(base)) for p in base.rglob('*') if p.is_file()})
   for name,digest in manifest.items():self.assertEqual(hashlib.sha256((base/name).read_bytes()).hexdigest(),digest)
 def test_traversal_and_shared_roots_rejected(self):
  for value in ('/','/etc','/opt','relative','/opt/../etc/x'):
   with self.assertRaises(ValueError):D.safe_path(value)
 def test_symlink_ancestor_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'real').mkdir();(root/'alias').symlink_to(root/'real',target_is_directory=True)
   with self.assertRaises(ValueError):D.safe_path(str(root/'alias'/'new'))
 def test_occupied_port_rejected(self):
  with socket.socket() as s:
   s.bind(('127.0.0.1',0));s.listen();self.assertFalse(D.port_free(s.getsockname()[1]))
 def test_private_files_and_token_canaries_blocked(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'account.env').write_text('not-a-real-secret')
   (root/'source.py').write_text('token='+'sk'+'-ant-'+'A'*24)
   (root/'redirect').symlink_to(root/'source.py')
   count,issues=P.scan(root);rules={rule for name,rule in issues}
   self.assertTrue({'blocked-file','provider-token','symlink'}.issubset(rules))
 def test_tampered_or_traversing_manifest_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp)
   for kind in ('app','browser'):
    base=root/'assets'/kind;base.mkdir(parents=True);(base/'entry.py').write_text('pass')
    (root/'assets'/(kind+'.sha256.json')).write_text(json.dumps({'entry.py':hashlib.sha256(b'pass').hexdigest()}))
   with patch.object(D,'SKILL',root):
    self.assertEqual(D.validated_assets()['app']['entry.py'],b'pass')
    (root/'assets/app/entry.py').write_text('changed')
    with self.assertRaises(ValueError):D.validated_assets()
    (root/'assets/app.sha256.json').write_text(json.dumps({'../app/entry.py':hashlib.sha256(b'changed').hexdigest()}))
    with self.assertRaises(ValueError):D.validated_assets()
 def test_clean_package(self):self.assertEqual(P.scan(ROOT)[1],[])
if __name__=='__main__':unittest.main()
