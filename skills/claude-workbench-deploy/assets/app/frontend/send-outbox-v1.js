'use strict';
// Local pending display never authorizes or automatically replays a server task.
const sendOutbox=(()=>{
 const prefix='cc-workbench-outbox-v1:';let owner='',records=new Map();
 const key=e=>prefix+owner+':'+e.session_id+':'+e.request_id;
 function init(user){
  owner=user.username;records=new Map();
  try{
   for(const k of Object.keys(localStorage).filter(k=>k.startsWith(prefix))){
    if(!k.startsWith(prefix+owner+':')){localStorage.removeItem(k);continue;}
    try{
     const e=JSON.parse(localStorage.getItem(k));
     if(!e||e.owner!==owner||!/^[a-f0-9]{32}$/.test(e.request_id)||!e.session_id||
       typeof e.prompt!=='string'||!Array.isArray(e.attachments))continue;
     if(e.phase==='sending')e.phase='uncertain';
     e.inflight=false;records.set(e.session_id+':'+e.request_id,e);
    }catch{}
   }
  }catch{}
 }
 function entries(sid){return [...records.values()].filter(e=>e.session_id===sid).sort((a,b)=>a.created_at-b.created_at);}
 function get(sid,rid){return records.get(sid+':'+rid);}
 function add(e){e.owner=owner;records.set(e.session_id+':'+e.request_id,e);return e;}
 function save(e){
  if(!owner||e.owner!==owner)throw Error('\u767b\u5f55\u72b6\u6001\u5df2\u6539\u53d8');
  const {session_id,request_id,prompt,attachments,created_at,phase,error,signature,pendingKey,restoredDraft}=e;
  localStorage.setItem(key(e),JSON.stringify({owner,session_id,request_id,prompt,attachments,created_at,
   phase,error,signature,pendingKey,restoredDraft}));
 }
 function remove(sid,rid){
  const e=get(sid,rid);if(e){try{localStorage.removeItem(key(e));}catch{}records.delete(sid+':'+rid);}
 }
 function clear(){
  try{for(const k of Object.keys(localStorage))if(k.startsWith(prefix))localStorage.removeItem(k);}catch{}
  owner='';records=new Map();
 }
 return {init,entries,get,add,save,remove,clear};
})();
