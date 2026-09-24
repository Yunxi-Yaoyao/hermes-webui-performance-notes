const vm=require('node:vm'), fs=require('node:fs'), assert=require('node:assert/strict');
const file=process.argv[2];
const source=fs.existsSync(file)?fs.readFileSync(file,'utf8'):'/* baseline: native fetch */';
function env(){let calls=0, pending=[];const win={location:{href:'https://site.test/#/hermes/chat',origin:'https://site.test'}};win.fetch=async(...args)=>{calls++;return new Promise((resolve,reject)=>pending.push({resolve,reject,args}));};vm.runInNewContext(source,{window:win,URL,Headers,Request,Response,Promise,Map,JSON});return {win,count:()=>calls,releaseOne:(index)=>{const x=pending.splice(index,1)[0];x.resolve(new Response('{"ok":true}',{status:200}));},release:(status=200)=>pending.splice(0).forEach(x=>x.resolve(new Response('{"ok":true}',{status,headers:{'content-type':'application/json'}}))),reject:()=>pending.splice(0).forEach(x=>x.reject(new Error('offline')))};}
(async()=>{
 let e=env();let a=e.win.fetch('/api/hermes/profiles',{headers:{Authorization:'Bearer a'}}),b=e.win.fetch('/api/hermes/profiles',{headers:{Authorization:'Bearer a'}});assert.equal(e.count(),1,'same in-flight GET must coalesce');e.release();const [ra,rb]=await Promise.all([a,b]);assert.notEqual(ra,rb);assert.deepEqual(await ra.json(),{ok:true});assert.deepEqual(await rb.json(),{ok:true});console.log('PASS dedup and separate readable bodies');
 let later=e.win.fetch('/api/hermes/profiles',{headers:{Authorization:'Bearer a'}});assert.equal(e.count(),2,'completed responses must not be cached');e.release();await later;console.log('PASS no settled-response cache');
 for(const variants of [
  [{headers:{Authorization:'a'}},{headers:{Authorization:'b'}}],
  [{headers:{'X-Hermes-Profile':'default'}},{headers:{'X-Hermes-Profile':'novel'}}],
  [{method:'POST'},{method:'POST'}],
  [{signal:new AbortController().signal},{signal:new AbortController().signal}],
  [{cache:'reload'},{cache:'default'}]
 ]){e=env();a=e.win.fetch('/api/hermes/profiles',variants[0]);b=e.win.fetch('/api/hermes/profiles',variants[1]);assert.equal(e.count(),2,'distinct/security/abort requests must remain separate');e.release();await Promise.all([a,b]);}console.log('PASS auth/profile/options/POST/signal isolation');
 for(const url of ['https://other.test/api/hermes/profiles','/api/studio/sessions','/api/auth/me/other']){e=env();a=e.win.fetch(url);b=e.win.fetch(url);assert.equal(e.count(),2,'nonallowlisted requests untouched');e.release();await Promise.all([a,b]);}console.log('PASS exact allowlist and same origin');
 for(const status of [401,403,500]){e=env();a=e.win.fetch('/api/auth/me');b=e.win.fetch('/api/auth/me');e.release(status);const res=await Promise.all([a,b]);assert.equal(res[0].status,status);assert.equal(res[1].status,status);assert.equal(res[0].ok,false);}console.log('PASS HTTP error status preservation');
 e=env();a=e.win.fetch('/api/auth/me').catch(x=>x.message);e.reject();assert.equal(await a,'offline');b=e.win.fetch('/api/auth/me');assert.equal(e.count(),2,'failed request may retry');e.release();await b;console.log('PASS failure eviction');
 e=env();a=e.win.fetch('/api/auth/me');let m=e.win.fetch('/api/auth/logout',{method:'POST'});b=e.win.fetch('/api/auth/me');assert.equal(e.count(),3,'mutations invalidate pending joins');e.release();await Promise.all([a,b,m]);console.log('PASS mutation invalidation');
 e=env();let write=e.win.fetch('/api/hermes/config',{method:'POST'});let during=e.win.fetch('/api/hermes/config');e.releaseOne(0);await write;let after=e.win.fetch('/api/hermes/config');assert.equal(e.count(),3,'GET after completed mutation must not join earlier GET');e.release();await Promise.all([during,after]);console.log('PASS mutation completion race');
})().catch(e=>{console.error(e);process.exitCode=1});
