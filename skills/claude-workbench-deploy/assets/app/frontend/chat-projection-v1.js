'use strict';
// Incremental display model. The persisted journal remains authoritative.
const chatProjection=(()=>{
 let source=null,session='',last=0,size=0,items=[],partial=null,lastUsage=null,reads=0,pending=[];
 const tools=new Map();
 function reset(map,sid){source=map;session=sid;last=0;size=0;items=[];partial=null;lastUsage=null;pending=[];tools.clear();}
 function textOf(x){return typeof x.content==='string'?x.content:JSON.stringify(x.content??'',null,2);}
 function consume(event){
  reads++;last=Math.max(last,event.seq);const d=event.data||{},key=session+':event:'+event.seq;
  if(event.type==='user'){items.push({key:d.request_id?session+':request:'+d.request_id:key,role:'user',text:d.text||'',attachments:d.attachments||[],time:event.created_at,tools:[]});partial=null;return;}
  if(event.type==='error'){items.push({key,role:'assistant',text:'\u53d1\u751f\u9519\u8bef\uff1a'+(d.message||'\u672a\u77e5\u9519\u8bef'),time:event.created_at,tools:[]});partial=null;return;}
  if(event.type!=='claude')return;
  const owner=(d.parent_tool_use_id||'main')+':';
  if(d.type==='stream_event'){
   const e=d.event||{},delta=e.delta||{};
   if(e.type==='content_block_delta'&&delta.type==='text_delta'&&delta.text){
    if(!partial){partial={key,role:'assistant',text:'',tools:[],time:event.created_at};items.push(partial);}
    partial.text+=delta.text;
   }
  }else if(d.type==='assistant'){
   const blocks=Array.isArray(d.message?.content)?d.message.content:[],text=blocks.filter(x=>x.type==='text').map(x=>x.text||'').join('\n\n');
   const calls=blocks.filter(x=>['tool_use','server_tool_use'].includes(x.type));
   if(text.trim()||calls.length||partial?.text.trim()){
    if(!partial){partial={key,role:'assistant',text:'',tools:[],time:event.created_at};items.push(partial);}
    if(text)partial.text=text;
    for(const [index,x] of calls.entries()){
     const id=typeof x.id==='string'?x.id:'',toolKey=(d.session_id||'')+':'+owner+id;
     if(id&&tools.has(toolKey))continue;
     const tool={id,name:'\u5de5\u5177\uff1a'+(x.name||'\u6267\u884c'),historyRef:x._history_ref,text:x._history_ref?'\u5c55\u5f00\u540e\u52a0\u8f7d\u8be6\u60c5':JSON.stringify(x.input??{},null,2),renderKey:session+':tool:'+toolKey+':'+(id?'':event.seq+':'+index)};
     partial.tools.push(tool);if(id)tools.set(toolKey,{item:partial,tool});
    }
   }
   partial=null;if(d.message?.usage)lastUsage=d.message.usage;
  }else if(d.type==='user'&&Array.isArray(d.message?.content)){
   partial=null;
   for(const [index,x] of d.message.content.entries()){
    if(x.type!=='tool_result')continue;
    const found=tools.get((d.session_id||'')+':'+owner+x.tool_use_id);
    if(found){
     found.tool.result={text:x._history_ref?'\u5c55\u5f00\u540e\u52a0\u8f7d\u8be6\u60c5':textOf(x),historyRef:x._history_ref,error:x.is_error===true};
     const keys=found.item.memberKeys??=[found.item.key];if(!keys.includes(key))keys.push(key);
    }else items.push({key:key+':result:'+index,role:'assistant',text:'',time:event.created_at,tools:[{renderKey:key+':result:'+index,error:x.is_error===true,name:x.is_error===true?'\u5de5\u5177\u7ed3\u679c\uff08\u5931\u8d25\uff09':'\u5de5\u5177\u7ed3\u679c',historyRef:x._history_ref,text:x._history_ref?'\u5c55\u5f00\u540e\u52a0\u8f7d\u8be6\u60c5':textOf(x)}]});
   }
  }else if(d.type==='result'){if(d.usage)lastUsage=d.usage;partial=null;items.push({boundary:true});}
 }
 function project(map,sid){
  // Equal-size updates cannot occur in the append-only journal. Replaced maps
  // and prepended history deliberately rebuild from the canonical sequence.
  if(source!==map||session!==sid||map.size<size)reset(map,sid);
  if(map.size!==size){
   const added=[];let older=false;
   if(pending.length&&map.size===size+pending.length)added.push(...pending);
   else for(const [seq,event] of map)if(seq>last)added.push(event);else if(seq<=(size?last:0)&&!sourceSeen.has(seq))older=true;
   pending=[];
   if(older){reset(map,sid);sourceSeen.clear();added.length=0;added.push(...map.values());}
   added.sort((a,b)=>a.seq-b.seq);for(const event of added){consume(event);sourceSeen.add(event.seq);}size=map.size;
  }
  return {items,lastUsage};
 }
 const sourceSeen=new Set();
 return {add(map,event){if(map===source&&!map.has(event.seq)&&event.seq>last)pending.push(event);map.set(event.seq,event);},project(map,sid){if(source!==map||session!==sid||map.size<size)sourceSeen.clear();return project(map,sid);},stats(){return {eventsRead:reads,items:items.length};}};
})();
