'use strict';
// Cache is optional, scoped to a verified account, and never authorizes an action.
const historyCache=(()=>{
 let owner='',db=null,generation=0;const entries=new Map();const timers=new Map();
 function transaction(mode,fn){if(!db)return;try{fn(db.transaction('records',mode).objectStore('records'));}catch{}}
 async function init(user){
  if(typeof user.username!=='string'||!user.username)return;owner=user.username;const gen=++generation;
  try{
   db=await new Promise((resolve,reject)=>{const r=indexedDB.open('cc-workbench-history-v1',1);r.onupgradeneeded=()=>r.result.createObjectStore('records',{keyPath:'key'});r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});
   db.onversionchange=()=>{db.close();db=null;};
   const records=await new Promise((resolve,reject)=>{const r=db.transaction('records').objectStore('records').getAll();r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});
   if(gen!==generation)return;
   for(const r of records.sort((a,b)=>a.saved-b.saved)){
    if(r.owner!==owner){transaction('readwrite',s=>s.delete(r.key));continue;}
    if(r.version!==1||!Array.isArray(r.events)||!r.session?.id)continue;
    entries.set(r.session.id,{...r,events:new Map(r.events.map(e=>[e.seq,e])),nodes:null});
   }
   while(entries.size>5)remove(entries.keys().next().value);
  }catch{db=null;}
 }
 function get(id){const v=entries.get(id);if(v){entries.delete(id);entries.set(id,v);}return v;}
 function put(id,entry){
  if(!owner)return;
  entries.delete(id);entries.set(id,entry);
  while(entries.size>5)remove(entries.keys().next().value);
  clearTimeout(timers.get(id));const gen=generation;
  timers.set(id,setTimeout(()=>{
   timers.delete(id);if(gen!==generation||!entries.has(id))return;
   const persist=()=>{
    if(gen!==generation||!entries.has(id))return;
    let events=[...entry.events.values()].sort((a,b)=>a.seq-b.seq),count=0,start=0;
    for(let i=events.length-1;i>=0;i--){const e=events[i];if(e.type==='user'||e.type==='claude'&&e.data?.type==='assistant'&&e.data.message?.content?.some(b=>b.type==='text'&&(b.text||'').trim()))count++;if(count===100){start=i;break;}}
    events=events.slice(start);
    const r={key:owner+':'+id,owner,version:1,saved:Date.now(),session:entry.session,events,cursor:entry.cursor,before:start?events[0]?.seq:entry.before,oldest:entry.oldest||start>0,usage:entry.usage};
    // Bound disk use. Oversized records remain usable in the current tab.
    if(JSON.stringify(r).length>3*1024*1024){transaction('readwrite',s=>s.delete(r.key));return;}
    transaction('readwrite',s=>s.put(r));
   };
   if(window.requestIdleCallback)requestIdleCallback(persist,{timeout:2000});else setTimeout(persist,0);
  },150));
 }
 function remove(id){entries.delete(id);clearTimeout(timers.get(id));timers.delete(id);transaction('readwrite',s=>s.delete(owner+':'+id));}
 function clear(){
  generation++;owner='';entries.clear();for(const t of timers.values())clearTimeout(t);timers.clear();try{localStorage.removeItem('cc-workbench-active');}catch{}
  return new Promise(resolve=>{
   const wipe=database=>{try{const tx=database.transaction('records','readwrite');tx.objectStore('records').clear();tx.oncomplete=tx.onabort=tx.onerror=()=>resolve();}catch{resolve();}};
   if(db){wipe(db);return;}
   try{const r=indexedDB.open('cc-workbench-history-v1',1);r.onupgradeneeded=()=>r.result.createObjectStore('records',{keyPath:'key'});r.onsuccess=()=>{db=r.result;wipe(db);};r.onerror=()=>resolve();}catch{resolve();}
  });
 }

 function active(id){try{localStorage.setItem('cc-workbench-active',JSON.stringify({owner,id}));}catch{}}
 function last(){try{const r=JSON.parse(localStorage.getItem('cc-workbench-active'));return r?.owner===owner?r.id:null;}catch{return null;}}
 return {init,get,put,remove,clear,active,last};
})();
