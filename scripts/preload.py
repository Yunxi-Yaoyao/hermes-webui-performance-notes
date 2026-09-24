import pathlib,re,json
START='<!-- WEBUI_CHAT_PRELOAD_BEGIN -->'
END='<!-- WEBUI_CHAT_PRELOAD_END -->'
def generate(root):
 root=pathlib.Path(root)
 html=(root/'index.html').read_text()
 mains=re.findall(r'<script[^>]+type="module"[^>]+src="(/assets/js/index-[^"/]+\.js)"',html)
 if len(mains)!=1:raise ValueError('ambiguous/unknown main module layout')
 src=(root/mains[0].lstrip('/')).read_text()
 deps=re.findall(r'm\.f=\[(.*?)\]',src)
 chats=set(re.findall(r'import\("\./(ChatView-[^"/]+\.js)"\),__vite__mapDeps\(\[([0-9,]+)\]\)',src))
 if len(deps)!=1 or len(chats)!=1:raise ValueError('ambiguous/unknown route dependency layout')
 items=json.loads('['+deps[0]+']');indices=[int(x) for x in next(iter(chats))[1].split(',')]
 if not all(0<=i<len(items) for i in indices):raise ValueError('invalid dependency index')
 assets=[items[i] for i in indices]
 for glob in ['assets/js/en-*.js','assets/js/zh-*.js','assets/js/MarkdownRenderer-*.js','assets/css/MarkdownRenderer-*.css']:
  matches=[x for x in root.glob(glob) if re.fullmatch(re.escape(glob.split('/')[-1].split('*')[0])+r'[A-Za-z0-9_-]{8}'+re.escape(glob.split('*')[1]),x.name)]
  if len(matches)!=1:raise ValueError('ambiguous/missing '+glob)
  assets.append(matches[0].relative_to(root).as_posix())
 assets=list(dict.fromkeys(assets))
 if len(assets)>100:raise ValueError('unexpected dependency count')
 for rel in assets:
  if not re.fullmatch(r'assets/(js|css)/[A-Za-z0-9_.-]+\.(js|css)',rel) or not (root/rel).is_file():raise ValueError('unsafe/missing asset '+rel)
 js="""(()=>{if(!/^#\\/hermes\\/(?:chat|session(?:\\/|$))/.test(location.hash))return;
const assets=%s;
for(const path of assets){const l=document.createElement('link');l.crossOrigin='anonymous';l.href='/'+path;if(path.endsWith('.js')){l.rel='modulepreload'}else{l.rel='preload';l.as='style'}document.head.appendChild(l)}
})();"""%json.dumps(assets,separators=(',',':'))
 return START+'\n<script>'+js+'</script>\n'+END,assets

if __name__ == '__main__':
 import argparse,sys
 parser=argparse.ArgumentParser(description='Print static chat preload hints. Does not modify the package.')
 parser.add_argument('--client-root',type=pathlib.Path,required=True)
 args=parser.parse_args()
 version=json.loads((args.client_root.parent.parent/'package.json').read_text()).get('version')
 if version!='0.7.22':parser.error('Only 0.7.22 dependency layout was tested; inspect newer versions first.')
 text,assets=generate(args.client_root)
 print(text)
 print(f'Validated {len(assets)} static references; review before use.',file=sys.stderr)
