#!/usr/bin/env python3
"""Lightweight tracked-file publication guard; not a complete secret scanner."""
import ipaddress,json,pathlib,re,subprocess,sys
ROOT=pathlib.Path(__file__).resolve().parents[1]
def main():
 names=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0')
 issues=[];seen=0
 for name in filter(None,names):
  p=ROOT/name;seen+=1
  if p.suffix.lower() in {'.png','.jpg','.jpeg','.webp','.db','.sqlite','.har','.pcap','.tgz','.zip','.pem','.key'}:issues.append((name,'forbidden artifact type'));continue
  text=p.read_text(encoding='utf-8')
  if p.stat().st_size>200_000:issues.append((name,'unexpected large file'))
  patterns={
   'private filesystem':r'/(?:root|home|opt/hermes-workstation)/',
   'private session ID':r'\bmuf[a-z0-9]{8,}\b',
   'credential-like token':r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|glpat-[A-Za-z0-9_-]{15,}|sk-[A-Za-z0-9]{20,})',
   'embedded URL credential':r'https?://[^\s/]+:[^\s/]+@',
  }
  for label,pattern in patterns.items():
   if re.search(pattern,text):issues.append((name,label))
  for addr in re.findall(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])',text):
   try:ip=ipaddress.ip_address(addr)
   except ValueError:continue
   allowed=ip.is_loopback or any(ip in ipaddress.ip_network(n) for n in ['192.0.2.0/24','198.51.100.0/24','203.0.113.0/24']) or str(ip) in ['0.0.0.0','255.255.255.255']
   if name in {'scripts/discovery_guard.py','tests/test_discovery_guard.py','scripts/check_publication.py'} and str(ip) in {'224.0.0.0','240.0.0.0','198.50.0.0','198.51.0.0','255.255.255.252'}:allowed=True  # reviewed rejection-test network constants, not hosts
   if not allowed:issues.append((name,'non-documentation IP address'))
  if p.suffix=='.json':json.loads(text)
 if not seen:issues.append(('.', 'no tracked files: stage intended publication first'))
 if issues:
  for name,reason in issues:print(name+': '+reason)
  return 1
 print(f'Publication guard passed: {seen} tracked text files; manual review still required.')
 return 0
if __name__=='__main__':sys.exit(main())
