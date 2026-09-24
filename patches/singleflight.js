/* WebUI 0.7.22: only coalesce identical in-flight metadata GETs. */
(()=>{
 'use strict';
 const original=window.fetch.bind(window), pending=new Map(); let writing=0;
 const allow=new Set(['/api/hermes/profiles','/api/auth/me','/api/hermes/config']);
 window.fetch=function(input,init={}){
  const method=String(init.method||(input instanceof Request?input.method:'GET')).toUpperCase();
  if(method!=='GET'){
   pending.clear();writing++;
   const done=()=>{writing--;pending.clear()};
   let result;try{result=original(input,init)}catch(e){done();throw e;}
   return result.then(response=>{done();return response},error=>{done();throw error});
  }
  if(writing)return original(input,init);
  if(typeof input!=='string'||init.signal||init.body||Object.keys(init).some(k=>!['method','headers','credentials','cache','mode','redirect','referrer','referrerPolicy','integrity','keepalive'].includes(k)))return original(input,init);
  let url;try{url=new URL(input,window.location.href)}catch{return original(input,init)}
  if(url.origin!==window.location.origin||!allow.has(url.pathname))return original(input,init);
  const headers=[...new Headers(init.headers).entries()].sort((a,b)=>a[0].localeCompare(b[0]));
  const options=['credentials','cache','mode','redirect','referrer','referrerPolicy','integrity','keepalive'].map(k=>[k,init[k]??null]);
  const key=JSON.stringify([url.href,headers,options]);
  let p=pending.get(key);
  if(!p){
   p=original(input,init).then(async response=>{await response.clone().arrayBuffer();return response;});
   pending.set(key,p);
   const clear=()=>{if(pending.get(key)===p)pending.delete(key)};
   p.then(clear,clear);
  }
  return p.then(response=>response.clone());
 };
})();
