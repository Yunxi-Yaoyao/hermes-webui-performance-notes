import unittest,tempfile,pathlib,json
import sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'scripts'))
from preload import generate
class Tests(unittest.TestCase):
 def build(self,p):
  (p/'assets/js').mkdir(parents=True);(p/'assets/css').mkdir()
  (p/'index.html').write_text('<head><script type="module" src="/assets/js/index-X.js"></script></head>')
  deps=['assets/js/ChatView-AAAAAAAA.js','assets/css/ChatView-AAAAAAAA.css']
  src='const __vite__mapDeps=(i,m,d=(m.f||(m.f='+json.dumps(deps)+'))) => i;'
  src+='component:()=>et(()=>import("./ChatView-AAAAAAAA.js"),__vite__mapDeps([0,1]))'
  (p/'assets/js/index-X.js').write_text(src)
  for rel in deps+['assets/js/en-AAAAAAAA.js','assets/js/zh-AAAAAAAA.js','assets/js/MarkdownRenderer-AAAAAAAA.js','assets/css/MarkdownRenderer-AAAAAAAA.css']:(p/rel).write_text('')
 def test_discovery(self):
  with tempfile.TemporaryDirectory() as t:
   p=pathlib.Path(t);self.build(p);text,assets=generate(p)
   self.assertIn('modulepreload',text);self.assertIn('ChatView-AAAAAAAA.css',text);self.assertEqual(len(assets),6)
   self.assertIn('location.hash',text);self.assertNotIn('fetch(',text)
 def test_missing_asset_refuses(self):
  with tempfile.TemporaryDirectory() as t:
   p=pathlib.Path(t);self.build(p);(p/'assets/css/ChatView-AAAAAAAA.css').unlink()
   with self.assertRaises(ValueError):generate(p)
 def test_unknown_layout_refuses(self):
  with tempfile.TemporaryDirectory() as t:
   p=pathlib.Path(t);self.build(p);(p/'assets/js/index-X.js').write_text('changed architecture')
   with self.assertRaises(ValueError):generate(p)
 def test_ambiguous_entry_refuses(self):
  with tempfile.TemporaryDirectory() as t:
   p=pathlib.Path(t);self.build(p)
   html=p/'index.html';html.write_text(html.read_text()*2)
   with self.assertRaises(ValueError):generate(p)
 def test_conflicting_chat_dependencies_refuse(self):
  with tempfile.TemporaryDirectory() as t:
   p=pathlib.Path(t);self.build(p);f=p/'assets/js/index-X.js'
   f.write_text(f.read_text()+'component:()=>et(()=>import("./ChatView-AAAAAAAA.js"),__vite__mapDeps([1]))')
   with self.assertRaises(ValueError):generate(p)
 def test_repeated_identical_route_is_allowed(self):
  with tempfile.TemporaryDirectory() as t:
   p=pathlib.Path(t);self.build(p);f=p/'assets/js/index-X.js'
   f.write_text(f.read_text()+'component:()=>et(()=>import("./ChatView-AAAAAAAA.js"),__vite__mapDeps([0,1]))')
   self.assertEqual(len(generate(p)[1]),6)
if __name__=='__main__':unittest.main()
