'use strict';
const $ = selector => document.querySelector(selector);
const state = {user:null, csrf:'', sessions:[], session:null, providers:[], events:new Map(), attachments:[], oldest:false, loading:false, cursor:0, epoch:0, status:'idle', queue:[], directory:null, poll:null, uploading:0, attachmentDrafts:new Map(), switching:false, statusVerified:false, statusRequest:0, confirming:false, queuePaused:false, sending:new Set(), stopRequests:new Set(), eventsDirty:false, pollRunningEpoch:null};
const pathId = id => encodeURIComponent(id);
const deliveryStatus=document.createElement('p');deliveryStatus.id='delivery-status';deliveryStatus.className='help';deliveryStatus.setAttribute('role','status');deliveryStatus.hidden=true;$('#composer-taskline').prepend(deliveryStatus);
let lastActivityStatus=null,lastActivityReceived=0,activityConnectionProblem=false,providerSnapshot=null,activityServerOffset=0;
const activityLabels={approval_unknown:'\u5ba1\u6279\u56de\u4f20\u5f85\u6838\u9a8c',answering:'\u6b63\u5728\u56de\u4f20\u786e\u8ba4',requires_action:'\u7b49\u5f85\u4f60\u786e\u8ba4',starting:'\u6b63\u5728\u51c6\u5907',requesting:'\u7b49\u5f85\u6a21\u578b',thinking:'\u6a21\u578b\u601d\u8003\u4e2d',responding:'\u6b63\u5728\u8f93\u51fa',preparing_tool:'\u51c6\u5907\u6267\u884c\u5de5\u5177',executing:'\u6267\u884c\u5de5\u5177',compacting:'\u538b\u7f29\u4e0a\u4e0b\u6587',background:'\u540e\u53f0\u4efb\u52a1\u8fd0\u884c\u4e2d',finishing:'\u6b63\u5728\u6536\u5c3e',completed:'\u5df2\u5b8c\u6210',failed:'\u6267\u884c\u5931\u8d25',interrupted:'\u5df2\u4e2d\u65ad',unknown:'\u6267\u884c\u72b6\u6001\u672a\u77e5',idle:'\u672a\u5f00\u59cb',queued:'\u6392\u961f\u4e2d',stopping:'\u6b63\u5728\u505c\u6b62'};
function activityAge(stamp){const time=Date.parse(stamp);return Number.isFinite(time)?Math.max(0,(Date.now()+activityServerOffset-time)/1000):null;}
function activityDuration(seconds){seconds=Math.max(0,Math.floor(seconds||0));return seconds<60?seconds+'\u79d2':seconds<3600?Math.floor(seconds/60)+'\u5206'+seconds%60+'\u79d2':Math.floor(seconds/3600)+'\u5c0f\u65f6'+Math.floor(seconds%3600/60)+'\u5206';}
function activityClock(stamp){return Number.isFinite(Date.parse(stamp))?new Date(stamp).toLocaleTimeString('zh-CN',{hour12:false}):'\u672a\u63d0\u4f9b';}
function renderProviderState(){
 const dot=$('#provider-status .status-dot'),text=$('#provider-status span:last-child');
 if(!dot||!text)return;
 const a=lastActivityStatus?.activity,linked=lastActivityStatus&&performance.now()-lastActivityReceived<10000&&!activityConnectionProblem;
 if(activityConnectionProblem||lastActivityStatus&&!linked){dot.className='status-dot bad';text.textContent='\u6267\u884c\u5668\u8fde\u63a5\u5f85\u786e\u8ba4';}
 else if(linked){dot.className='status-dot ok';text.textContent='\u6267\u884c\u5668\u5df2\u8fde\u63a5'+(a?.process_alive===true?' \u00b7 Claude \u6267\u884c\u4e2d':a?.last_success_at?' \u00b7 \u6700\u8fd1\u6210\u529f '+activityClock(a.last_success_at):'');}
 else if(providerSnapshot){const known=providerSnapshot.auth_status_known===true,ok=providerSnapshot.configured,stale=providerSnapshot.auth_status_stale===true;dot.className='status-dot '+(known&&ok&&!stale?'ok':known&&!ok?'bad':'');text.textContent=known?(ok?'Claude \u5df2\u767b\u5f55':'Claude \u672a\u767b\u5f55')+(stale?'\uff08\u4e0a\u6b21\u6838\u9a8c\uff09':''):'Claude \u767b\u5f55\u72b6\u6001\u672a\u6838\u9a8c';}
 text.parentElement.title='\u6267\u884c\u5668\u8fde\u63a5\u4e0e\u5b98\u65b9\u767b\u5f55\u5206\u522b\u6838\u9a8c\u3002\n\u767b\u5f55\u6838\u9a8c\uff1a'+activityClock(providerSnapshot?.auth_status_checked_at)+(providerSnapshot?.auth_status_stale?'\uff08\u7f13\u5b58\uff09':'')+'\n\u6700\u8fd1\u6210\u529f\uff1a'+activityClock(a?.last_success_at)+'\n\u6700\u8fd1\u72b6\u6001\u540c\u6b65\uff1a'+activityClock(a?.checked_at);
}
function renderActivityUsage(){
 const a=lastActivityStatus?.activity;if(!a)return;
 const parts=[],u=a.usage?.values;
 if(u){const counts=[];for(const [key,label] of [['input_tokens','\u975e\u7f13\u5b58\u8f93\u5165'],['cache_read_input_tokens','\u7f13\u5b58\u8bfb\u53d6'],['cache_creation_input_tokens','\u7f13\u5b58\u5199\u5165'],['output_tokens','\u8f93\u51fa']])if(Number.isFinite(u[key]))counts.push(label+' '+u[key].toLocaleString('zh-CN'));if(counts.length)parts.push((a.usage_kind==='turn'?(lastActivityStatus.status==='completed'?'\u672c\u8f6e\u4e3b\u52a9\u624b':'\u6700\u8fd1\u5b8c\u6210\u8f6e\u6b21\uff08\u4e3b\u52a9\u624b\uff09'):'\u6700\u8fd1\u54cd\u5e94')+'\uff1a'+counts.join(' \u00b7 '));}
 const c=a.context;
 if(c&&Number.isFinite(c.tokens)){let label='\u6700\u8fd1\u8bf7\u6c42\u4e0a\u4e0b\u6587\uff08\u4f30\u7b97\uff09\uff1a'+c.tokens.toLocaleString('zh-CN')+' tokens';if(Number.isFinite(c.percent)&&c.percent<=100)label+=' / '+c.capacity.toLocaleString('zh-CN')+' \u2248 '+c.percent+'%';else label+='\uff08'+(c.capacity?'\u6570\u636e\u8d85\u51fa\u7a97\u53e3\uff0c\u767e\u5206\u6bd4\u5f85\u6838\u9a8c':c.window_reason==='ambiguous_canonical'?'\u6a21\u578b\u7a97\u53e3\u5339\u914d\u4e0d\u660e\u786e':'\u5b98\u65b9\u672a\u63d0\u4f9b\u5339\u914d\u7684\u7a97\u53e3')+'\uff09';parts.push(label);}
 else parts.push(a.context_reason==='missing_input_or_cache_usage'?'\u4e0a\u4e0b\u6587\uff1a\u5b98\u65b9\u672a\u5b8c\u6574\u63d0\u4f9b\u8f93\u5165\u6216\u7f13\u5b58\u7528\u91cf':'\u4e0a\u4e0b\u6587\uff1a\u7b49\u5f85\u5b98\u65b9\u8bf7\u6c42\u7528\u91cf');
 $('#usage').textContent=parts.join('\n');$('#usage').title='\u7528\u91cf\u4e0a\u62a5\uff1a'+activityClock(a.usage?.observed_at)+'\n\u4e0a\u4e0b\u6587\u6837\u672c\uff1a'+activityClock(c?.observed_at)+'\n\u4e0a\u4e0b\u6587\u4f30\u7b97\u542b\u672c\u6b21\u8bf7\u6c42\u7684\u975e\u7f13\u5b58\u8f93\u5165\u3001\u7f13\u5b58\u8f93\u5165\u548c\u5df2\u4e0a\u62a5\u8f93\u51fa\uff0c\u4e0d\u662f\u5b9e\u65f6\u5185\u90e8\u5360\u7528\u3002\u672c\u8f6e\u4e3b\u52a9\u624b\u7528\u91cf\u4e0d\u542b\u5b50\u4efb\u52a1\u7d2f\u8ba1\u3002';
}
function renderActivityView(){
 const data=lastActivityStatus;if(!data){renderApprovals(null);deliveryStatus.hidden=true;syncTaskline();renderProviderState();return;}
 const a=data.activity||{},running=['running','starting','stopping'].includes(data.status),linked=performance.now()-lastActivityReceived<10000&&!activityConnectionProblem;
 const approvalState=data.approvals?.[0]?.state;
 let phase=data.display_status||(approvalState?(approvalState==='unknown'?'approval_unknown':approvalState==='answering'?'answering':'requires_action'):a.phase||data.status),label=activityLabels[phase]||'\u72b6\u6001\u5f85\u6838\u9a8c';
 if(phase==='executing'&&a.tool?.name)label=(a.tool.name==='Bash'?'\u6267\u884c\u547d\u4ee4':'\u4f7f\u7528\u5de5\u5177')+' \u00b7 '+a.tool.name;
 const taskLabel=label;
 const silence=activityAge(a.last_event_at||a.started_at),warn=running&&linked&&!a.catching_up&&silence!==null&&silence>=90;
 if(a.catching_up)label='\u6b63\u5728\u8865\u8bfb\u72b6\u6001';
 if(!linked)label=taskLabel+'\uff08\u4e0a\u6b21\u72b6\u6001\uff09';else if(warn)label+=' \u00b7 \u957f\u65f6\u95f4\u65e0\u65b0\u6d3b\u52a8';
 if(data.status==='queued'&&data.queue_paused)label='\u961f\u5217\u5df2\u6682\u505c';
 $('#task-status').textContent=label;$('#task-status').title='\u6267\u884c\u8fdb\u7a0b\uff1a'+(a.process_alive===true?(linked?'\u5b58\u6d3b':'\u4e0a\u6b21\u68c0\u67e5\u65f6\u5b58\u6d3b'):a.process_alive===false?'\u5df2\u9000\u51fa':'\u5f53\u524d\u65e0\u53ef\u786e\u8ba4\u7684\u6267\u884c\u8fdb\u7a0b')+'\n\u6d3b\u52a8\u6765\u6e90\uff1a'+(a.last_kind||'\u672a\u6536\u5230');
 const details=[];
 if(data.queue?.length)details.push(data.queue.length+' \u6761\u6d88\u606f\u7b49\u5f85\u9001\u8fbe');
 if(running){details.push(linked?label:taskLabel+'\uff08\u4e0a\u6b21\u72b6\u6001\uff09');if(a.started_at)details.push((linked?'\u5df2\u8fd0\u884c ':'\u4e0a\u6b21\u8bb0\u5f55\u5df2\u8fd0\u884c ')+activityDuration(linked?activityAge(a.started_at):(Date.parse(a.checked_at)-Date.parse(a.started_at))/1000));if(silence!==null)details.push('\u6700\u8fd1\u6d3b\u52a8 '+activityDuration(silence)+'\u524d'+(a.last_kind==='tool_progress'?(a.last_activity_heartbeat?'\uff08\u547d\u4ee4\u5fc3\u8df3\uff09':'\uff08\u547d\u4ee4\u8fdb\u5ea6\uff09'):''));const output=activityAge(a.last_output_at);details.push(output===null?'\u5c1a\u65e0\u6587\u5b57\u8f93\u51fa':'\u6700\u8fd1\u6587\u5b57 '+activityDuration(output)+'\u524d');if(warn)details.push((a.process_alive===true?'\u8fdb\u7a0b\u4ecd\u5b58\u6d3b':a.process_alive===false?'\u8fdb\u7a0b\u5df2\u9000\u51fa':'\u6267\u884c\u8fdb\u7a0b\u672a\u786e\u8ba4')+'\uff0c\u5f85\u6838\u67e5\uff1b\u4e0d\u4f1a\u81ea\u52a8\u91cd\u53d1');}
 else if(data.status==='completed'){}
 else if(data.status==='interrupted')details.push('\u4e0a\u4e00\u6761\u5df2\u4e2d\u65ad\uff0c\u672a\u81ea\u52a8\u91cd\u53d1');
 else if(data.status==='failed')details.push('\u6267\u884c\u5931\u8d25\uff1a'+(a.error_reason||'\u5b98\u65b9\u672a\u63d0\u4f9b\u5177\u4f53\u539f\u56e0'));
 else if(data.status==='unknown')details.push('\u6267\u884c\u7ed3\u679c\u5f85\u6838\u9a8c\uff0c\u4e0d\u4f1a\u81ea\u52a8\u91cd\u53d1');
 else if(data.status==='queued')details.push(label);
 if(!details.length){const latest=data.deliveries?.[0];if(latest?.state==='writing')details.push('\u7b49\u5f85 Claude \u63a5\u6536\u786e\u8ba4');}
 
 deliveryStatus.textContent=compactDelivery(data,label,details);deliveryStatus.hidden=!details.length;deliveryStatus.dataset.warning=String(warn||!linked||approvalState==='unknown');
 deliveryStatus.title=details.join(' \u00b7 ')+'\n'+'\u6700\u8fd1\u6d3b\u52a8\uff1a'+activityClock(a.last_event_at)+'\n\u6700\u8fd1\u9519\u8bef\uff1a'+(a.error_reason||'\u65e0')+'\n\u547d\u4ee4\u6700\u8fd1\u4e0a\u62a5\u65f6\u957f\uff1a'+(a.tool?activityDuration(a.tool.elapsed_seconds):'\u65e0')+'\n\u6700\u8fd1\u6587\u5b57\uff1a'+activityClock(a.last_output_at)+'\n\u6700\u8fd1\u72b6\u6001\u540c\u6b65\uff1a'+activityClock(a.checked_at);
 renderApprovals(data);syncTaskline();renderActivityUsage();renderProviderState();
}
function renderDelivery(data){
 lastActivityStatus=data;lastActivityReceived=data?performance.now():0;const serverTime=Date.parse(data?.activity?.checked_at);activityServerOffset=Number.isFinite(serverTime)?serverTime-Date.now():0;activityConnectionProblem=false;
 if(!data){deliveryStatus.textContent='';deliveryStatus.hidden=true;renderProviderState();return;}
 if(state.noticeKind==='delivery'&&!data.queue?.length)notice('');
 renderActivityView();
}
setInterval(()=>{if(!document.hidden)renderActivityView();},1000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden){renderActivityView();if(currentId()&&state.pollRunningEpoch!==state.epoch)startPolling(state.epoch,true);}});

let connectionFailureSerial=0;
function notice(message,kind='other') { state.noticeKind=message?kind:null; const node=$('#notice'); node.textContent=message||''; node.hidden=!message; }
function connectionNotice(message) { connectionFailureSerial++; activityConnectionProblem=true;renderActivityView();notice(message,'connection'); }
function humanTime(value) { if (!value) return ''; const date=new Date(typeof value==='number'?value*1000:value); return Number.isNaN(date.valueOf())?'':date.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}); }
function currentId() { return state.session && state.session.id; }
function draftKey(id) { return 'cc-workbench-draft:' + state.user.username + ':' + id; }
function saveDraft() { if(currentId()) localStorage.setItem(draftKey(currentId()), $('#prompt').value); }
function csrfHeaders() { return {'X-CSRF-Token': state.csrf}; }
async function api(url, options={}) {
  const {timeoutMs=15000,...requestOptions}=options;
  const controller=new AbortController(),external=requestOptions.signal;
  const cancel=()=>controller.abort();if(external){if(external.aborted)cancel();else external.addEventListener('abort',cancel,{once:true});}
  const opts={credentials:'same-origin',...requestOptions,signal:controller.signal,headers:{...(options.headers||{})}};
  if (opts.method && !['GET','HEAD'].includes(opts.method.toUpperCase())) opts.headers['X-CSRF-Token']=state.csrf;
  if (opts.body && !(opts.body instanceof FormData) && typeof opts.body!=='string') { opts.headers['Content-Type']='application/json'; opts.body=JSON.stringify(opts.body); }
  const timer=setTimeout(cancel,timeoutMs);let response;
  try {response=await fetch(url,opts);}catch(e){if(e.name==='AbortError')throw new Error(external?.aborted?'\u8bf7\u6c42\u5df2\u53d6\u6d88':'\u8bf7\u6c42\u8d85\u65f6');throw e;}
  finally{if(!response){clearTimeout(timer);external?.removeEventListener('abort',cancel);}}
  if(response.status===401) { clearTimeout(timer);external?.removeEventListener('abort',cancel);await historyCache.clear();sendOutbox.clear(); window.location.assign('/login'); throw new Error('登录已失效'); }
  let data={}; try {data=await response.json();}catch(e){if(controller.signal.aborted)throw new Error('\u8bf7\u6c42\u8d85\u65f6');throw new Error('\u670d\u52a1\u5668\u8fd4\u56de\u4e86\u65e0\u6548\u6570\u636e');}finally{clearTimeout(timer);external?.removeEventListener('abort',cancel);}
  if(!response.ok){const error=new Error(typeof data.detail==='string'?data.detail:'请求失败（'+response.status+'）');error.status=response.status;throw error;}
  return data;
}
function setTheme(theme) { if(!['light','dark','warm'].includes(theme)) theme='light'; document.documentElement.dataset.theme=theme; $('#theme').value=theme; localStorage.setItem('cc-workbench-theme',theme); }
$('#theme').onchange=event=>setTheme(event.target.value);
setTheme(localStorage.getItem('cc-workbench-theme')||'light');
function sidebar(open) { $('#sidebar').classList.toggle('open',open); $('#backdrop').hidden=!open; }
$('#open-sidebar').onclick=()=>sidebar(true); $('#close-sidebar').onclick=()=>sidebar(false); $('#backdrop').onclick=()=>sidebar(false);
document.querySelectorAll('[data-close]').forEach(button=>button.onclick=()=>document.getElementById(button.dataset.close).close());
$('#logout').onclick=async()=>{try{await api('/api/auth/logout',{method:'POST'}); await historyCache.clear();sendOutbox.clear();attachmentManager.clear(); location.assign('/login');}catch(e){notice(e.message)}};
let providerRequest=null,providerVersion=0,providerLoadedAt=-Infinity,providerAttemptAt=-Infinity,providerRefreshError=false;
function loadProviders({maxAge=0}={}) {
 if(providerRequest)return providerRequest;
 const now=performance.now(),hasList=state.providers.some(p=>providerModels(p).length>0);
 if(maxAge&&hasList&&now-providerLoadedAt<maxAge&&!providerRefreshError)return Promise.resolve(true);
 if(maxAge&&(providerRefreshError||!hasList)&&now-providerAttemptAt<5000)return Promise.resolve(!providerRefreshError);
 providerAttemptAt=now;
 providerRequest=(async()=>{
  try{
   const result=await api('/api/providers');
   if(!Array.isArray(result.providers))throw new Error('\u6a21\u578b\u5217\u8868\u54cd\u5e94\u65e0\u6548');
   state.providers=result.providers;providerLoadedAt=performance.now();providerRefreshError=false;providerVersion++;
   providerSnapshot=state.providers.find(p=>p.id==='claude');renderProviderState();
   if(!$('#new-dialog').open)prepareNewDialog();
   updateConversationMeta();return true;
  }catch(e){
   providerRefreshError=true;
   // Failed discovery cannot erase a previously obtained candidate list.
   if(state.providers.length){
    state.providers=state.providers.map(p=>({...p,model_list_stale:true,auth_status_stale:true,model_list_error:e.message}));providerVersion++;
    providerSnapshot=state.providers.find(p=>p.id==='claude');
   }else if(!$('#new-dialog').open)prepareNewDialog();
   renderProviderState();prepareModelDialog();return false;
  }
 })().finally(()=>{providerRequest=null;});
 return providerRequest;
}
const effortNames={low:'低',medium:'中等',high:'高',xhigh:'超高',max:'最高'};
function providerFor(id) { return state.providers.find(item=>item.id===id); }
function modelValue(item) { return typeof item==='string'?item:(item?.value||item?.id||''); }
function modelLabel(item) { return typeof item==='string'?item:(item?.displayName||item?.name||modelValue(item)); }
function providerModels(provider) { return provider?.model_list_available===true&&Array.isArray(provider.models)?provider.models:[]; }
function selectedModel(provider,value) { return providerModels(provider).find(item=>modelValue(item)===(value||'default')); }
function appendMissing(select,value,label) {
 if(value && ![...select.options].some(option=>option.value===value)) {
  const option=new Option(label+'（当前列表不可用）',value);option.dataset.missing='1';select.add(option);
 }
}
function populateEfforts(provider,modelSelect,effortSelect,saved) {
 const item=selectedModel(provider,modelSelect.value);
 const levels=item&&Array.isArray(item.supportedEffortLevels)?item.supportedEffortLevels:[];
 effortSelect.replaceChildren(new Option(levels.length?'使用助手默认值':'使用助手默认值（支持的强度未知）',''));
 for(const level of levels) if(typeof level==='string'&&level&&!([...effortSelect.options].some(option=>option.value===level))) effortSelect.add(new Option(effortNames[level]||level,level));
 appendMissing(effortSelect,saved,saved);
 effortSelect.value=saved||'';
 effortSelect.disabled=effortSelect.options.length===1;
}
function populateModels(provider,modelSelect,effortSelect,savedModel,savedEffort) {
 const models=providerModels(provider);
 modelSelect.replaceChildren(new Option(models.length?'使用助手默认值':'默认模型（列表暂不可用）',''));
 for(const item of models) {
  const value=modelValue(item);if(!value||value==='default'||[...modelSelect.options].some(option=>option.value===value))continue;
  const disabled=item?.selectable===false;
  const label=disabled?modelLabel(item)+'（不可用：'+(item.unavailableReason||'当前版本不支持')+'）':modelLabel(item);
  const option=new Option(label,value);option.disabled=disabled;if(disabled)option.dataset.missing='1';if(item?.description)option.title=item.description;modelSelect.add(option);
 }
 appendMissing(modelSelect,savedModel,savedModel);
 modelSelect.value=savedModel||'';
 modelSelect.disabled=modelSelect.options.length===1;
 populateEfforts(provider,modelSelect,effortSelect,savedEffort);
}
function modelListNote(provider) {
 if(!provider?.model_list_available) {const reason=typeof provider?.model_list_error==='string'?provider.model_list_error.slice(0,160):'';return '暂时无法获取模型列表'+(reason?'：'+reason:'')+'；可使用助手默认值。';}
 const detail='候选项的实际使用取决于账号权限及额度。';
 if(provider.model_list_stale===true) {
  const time=humanTime(provider.model_list_updated_at);
  return '当前暂时无法刷新模型列表，显示'+(time?time+'获取的缓存':'上次获取的缓存')+'。'+detail;
 }
 if(provider.model_list_stale!==false) return '模型列表更新时间未知。'+detail;
 return '列表为助手提供的候选项；'+detail;
}
function updateProviderOptions() {
 const p=providerFor($('#provider').value);
 populateModels(p,$('#model'),$('#effort'),null,null);
 $('#new-model-note').textContent=modelListNote(p);
 const permission=$('#permission'); permission.replaceChildren();
 const names={plan:'只规划',dontAsk:'自动拒绝审批请求',acceptEdits:'自动接受文件编辑',bypassPermissions:'跳过权限检查（高权限）'};
 if(p&&Array.isArray(p.permission_modes)) for(const mode of p.permission_modes) permission.add(new Option(names[mode]||mode,mode));
 if([...permission.options].some(x=>x.value==='dontAsk')) permission.value='dontAsk';
 updatePermissionNote();newCollaborationOptions();
}
$('#model').onchange=()=>populateEfforts(providerFor($('#provider').value),$('#model'),$('#effort'),null);
$('#provider').onchange=updateProviderOptions;
function updatePermissionNote() {
 const mode=$('#permission').value;
 const descriptions={plan:'只规划；需要审批的操作会被拒绝。',dontAsk:'需要审批的操作会被拒绝，可能导致任务无法完成。',acceptEdits:'允许自动接受文件编辑；其他需要审批的操作可能被拒绝。',bypassPermissions:'高权限：跳过权限检查。Claude 和其执行的命令可能访问工作目录以外该系统用户有权访问的文件；工作目录不等于系统沙箱。'};
 $('#permission-note').textContent=descriptions[mode]||'助手未提供可用的权限模式。';
}
$('#permission').onchange=updatePermissionNote;

let sessionsLoadSerial=0,archivedView=false;
let selectionTarget=null;
let sessionMenu=null;
let sessionMenuTrigger=null;
function closeSessionMenu(restoreFocus=false) {
 const trigger=sessionMenuTrigger;
 if(sessionMenu)sessionMenu.remove();
 sessionMenu=null;sessionMenuTrigger=null;
 if(trigger){trigger.setAttribute('aria-expanded','false');if(restoreFocus&&trigger.isConnected)trigger.focus();}
}
function openSessionMenu(trigger,session) {
 if(sessionMenuTrigger===trigger){closeSessionMenu(true);return;}
 closeSessionMenu();
 const menu=document.createElement('div');
 menu.className='session-action-menu';
 menu.setAttribute('role','menu');
 menu.setAttribute('aria-label','\u4f1a\u8bdd\u64cd\u4f5c');
 const actions=[
  [session.pinned_at?'\u53d6\u6d88\u7f6e\u9876':'\u7f6e\u9876',()=>toggleSessionPin(session)],
  ...(state.user?.is_admin?[["\u534f\u4f5c\u8bbe\u7f6e",()=>openCollaboration(session)]]:[]),
  [session.archived_at?'\u6062\u590d\u4f1a\u8bdd':'\u5f52\u6863',()=>archiveSession(session)],
  ['\u91cd\u547d\u540d',()=>renameSession(session.id,session.title)],
  ['\u5220\u9664',()=>deleteSession(session.id,session.title)]
 ];
 for(const [label,run] of actions){
  const item=document.createElement('button');item.type='button';
  item.className='session-action-item';item.setAttribute('role','menuitem');
  item.textContent=label;
  if(label==='\u5220\u9664')item.classList.add('danger');
  item.onclick=event=>{event.stopPropagation();closeSessionMenu();run();};
  menu.append(item);
 }
 document.querySelector('.shell').append(menu);sessionMenu=menu;sessionMenuTrigger=trigger;
 trigger.setAttribute('aria-expanded','true');
 const rect=trigger.getBoundingClientRect(),box=menu.getBoundingClientRect();
 const left=Math.max(8,Math.min(rect.right-box.width,window.innerWidth-box.width-8));
 const below=rect.bottom+4;
 const top=below+box.height<=window.innerHeight-8?below:Math.max(8,rect.top-box.height-4);
 menu.style.left=left+'px';menu.style.top=top+'px';
 menu.querySelector('button').focus();
}
document.addEventListener('pointerdown',event=>{
 if(sessionMenu&&!sessionMenu.contains(event.target)&&
    !sessionMenuTrigger?.contains(event.target))closeSessionMenu();
});
document.addEventListener('keydown',event=>{
 if(!sessionMenu)return;
 if(event.key==='Escape'){event.preventDefault();closeSessionMenu(true);return;}
 if(event.key==='Tab'){closeSessionMenu();return;}
 if(event.key==='ArrowDown'||event.key==='ArrowUp'){
  event.preventDefault();const items=[...sessionMenu.querySelectorAll('button')];
  const i=items.indexOf(document.activeElement);
  items[(i+(event.key==='ArrowDown'?1:-1)+items.length)%items.length].focus();
 }
});
window.addEventListener('resize',()=>closeSessionMenu());
document.addEventListener('scroll',event=>{
 if(sessionMenu&&event.target instanceof Element&&event.target.contains(sessionMenuTrigger))closeSessionMenu();
},true);
async function loadSessions() {
 loadProjectPreferences();
 const request=++sessionsLoadSerial;
 try {
  const query=$('#search').value.trim();
  const result=await api('/api/sessions?archived='+archivedView+'&q='+encodeURIComponent(query));
  if(request!==sessionsLoadSerial)return;
  state.sessions=result.sessions||[];
  const list=$('#session-list');closeSessionMenu();list.replaceChildren();
  for(const s of state.sessions) {
   const row=document.createElement('div');row.className='session-row';
   const button=document.createElement('button');
   button.dataset.sessionId=s.id;button.className='session'+(currentId()===s.id?' active':'');button.type='button';
   const bullet=document.createElement('span');bullet.className='bullet';
   const body=document.createElement('span');body.className='body';
   const title=document.createElement('strong');title.textContent=s.title;
   if(s.pinned_at){
    const pin=document.createElement('span');pin.className='session-pin';
    pin.setAttribute('aria-label','\u5df2\u7f6e\u9876');pin.title='\u5df2\u7f6e\u9876';
    pin.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 3 6 0-1 6 4 4v2H6v-2l4-4-1-6zm3 12v6"/></svg>';
    title.prepend(pin);
   }
   const meta=document.createElement('small');
   meta.textContent=(s.provider==='claude'?'Claude Code':s.provider)+' \u00b7 '+humanTime(s.updated_at);
   body.append(title,meta);button.append(bullet,body);
   const runtime=document.createElement('span');runtime.className='session-runtime';runtime.hidden=true;runtime.setAttribute('role','img');button.append(runtime);button.onclick=()=>selectSession(s.id);
   const more=document.createElement('button');more.type='button';
   more.className='session-menu-trigger';more.textContent='\u22ef';
   more.setAttribute('aria-label','\u4f1a\u8bdd\u64cd\u4f5c\uff1a'+s.title);
   more.setAttribute('aria-haspopup','menu');more.setAttribute('aria-expanded','false');
   more.onclick=event=>{event.stopPropagation();openSessionMenu(more,s);};
   row.append(button,more);list.append(row);
  }
  groupSessionRows(list,state.sessions);
  if(!state.sessions.length){
   const p=document.createElement('p');p.className='help';
   p.textContent=query?'\u6ca1\u6709\u627e\u5230\u5339\u914d\u7684\u4f1a\u8bdd':'\u8fd8\u6ca1\u6709\u4f1a\u8bdd';
   list.append(p);
  }
  paintAttention();refreshSessionRuntime();
  if(result.search_complete===false)notice('\u90e8\u5206\u5386\u53f2\u6682\u65f6\u65e0\u6cd5\u641c\u7d22\uff0c\u8bf7\u68c0\u67e5\u6267\u884c\u670d\u52a1\u3002');
 }catch(e){if(request===sessionsLoadSerial)notice(e.message)}
}
let searchTimer; $('#search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(loadSessions,250)}; $('#refresh-sessions').onclick=loadSessions;
function getSequences() { return [...state.events.keys()].sort((a,b)=>a-b); }
const historySync=document.createElement('small');historySync.id='history-sync';historySync.hidden=true;historySync.setAttribute('role','status');$('#conversation-meta').after(historySync);
function setHistorySync(text){historySync.textContent=text||'';historySync.hidden=!text;}
let fillTimer=null;
// Follow the latest message until the reader deliberately scrolls upward.
let bottomFrame=null,bottomLastTop=0,bottomLastHeight=0,touchStartY=null;
state.followBottom=true;
function stopFollowingBottom(){
 state.followBottom=false;
 if(bottomFrame!==null){cancelAnimationFrame(bottomFrame);bottomFrame=null;}
}
function resetBottomFollow(){
 if(bottomFrame!==null){cancelAnimationFrame(bottomFrame);bottomFrame=null;}
 state.followBottom=true;bottomLastTop=0;bottomLastHeight=0;touchStartY=null;
}
function pinConversationBottom(){
 if(!state.followBottom||!currentId())return;
 const view=$('#conversation');
 view.scrollTop=view.scrollHeight;
 bottomLastTop=view.scrollTop;bottomLastHeight=view.scrollHeight;
}
function scheduleBottomPin(){
 if(bottomFrame!==null||!state.followBottom||!currentId())return;
 const epoch=state.epoch;
 bottomFrame=requestAnimationFrame(()=>{bottomFrame=null;if(epoch===state.epoch)pinConversationBottom();});
}
const conversationScroll=$('#conversation');
conversationScroll.addEventListener('scroll',()=>{
 const top=conversationScroll.scrollTop,height=conversationScroll.scrollHeight;
 if(state.followBottom&&height===bottomLastHeight&&top<bottomLastTop-1)stopFollowingBottom();
 if(height-top-conversationScroll.clientHeight<=2)state.followBottom=true;
 bottomLastTop=top;bottomLastHeight=height;
},{passive:true});
conversationScroll.addEventListener('wheel',event=>{if(event.deltaY<0)stopFollowingBottom();},{passive:true});
conversationScroll.addEventListener('touchstart',event=>{touchStartY=event.touches[0]?.clientY??null;},{passive:true});
conversationScroll.addEventListener('touchmove',event=>{if(touchStartY!==null&&(event.touches[0]?.clientY??touchStartY)>touchStartY+3)stopFollowingBottom();},{passive:true});
conversationScroll.addEventListener('pointerdown',event=>{
 const rect=conversationScroll.getBoundingClientRect();
 if(event.target===conversationScroll&&event.clientX>=rect.right-20)stopFollowingBottom();
},{passive:true});
document.addEventListener('keydown',event=>{
 const target=event.target;
 if(!(target instanceof Element)||target.closest('input,textarea,select,[contenteditable="true"]'))return;
 if(target!==document.body&&target!==document.documentElement&&!conversationScroll.contains(target))return;
 if(['ArrowUp','PageUp','Home'].includes(event.key)||(event.key===' '&&event.shiftKey))stopFollowingBottom();
});
conversationScroll.addEventListener('load',scheduleBottomPin,true);
const bottomResizeObserver=new ResizeObserver(scheduleBottomPin);
bottomResizeObserver.observe(conversationScroll);bottomResizeObserver.observe($('#message-list'));

function rememberSession(){
 const id=currentId();if(!id||state.switching)return;
 historyCache.put(id,{session:{...state.session},events:state.events,cursor:state.cursor,before:state.historyBefore,oldest:state.oldest,queue:state.queue,nodes:[...$('#message-list').childNodes],scrollTop:$('#conversation').scrollTop,usage:$('#usage').textContent,renderLimit:state.renderLimit});
}
async function historyPage(id,before,epoch,signal){
 const page=await api('/api/sessions/'+pathId(id)+'/history?limit=100'+(before?'&before='+before:''),{signal});
 if(epoch!==state.epoch)return null;
 if(page.format_version!==1)throw Error('历史记录版本无效');
 return page;
}
function showSession(session){
 state.session=session;state.switching=false;selectionTarget=null;
 $('#conversation-title').textContent=session.title;updateConversationMeta();
 $('#rename').hidden=false;$('#delete').hidden=false;$('#session-model').hidden=!state.user.is_admin;
 $('#empty-state').hidden=true;$('#messages').hidden=false;$('#prompt').disabled=!!session.archived_at;
 $('#prompt').value=localStorage.getItem(draftKey(session.id))||'';
 state.attachments=state.attachmentDrafts.get(session.id)||[];
 const restored=sendOutbox.entries(session.id).find(e=>e.restoredDraft&&e.prompt===$('#prompt').value);
 if(restored&&!state.attachments.length&&restored.attachments.length){
  state.attachments=restored.attachments.map(a=>({...a,progress:100}));
  state.attachmentDrafts.set(session.id,state.attachments);
 }
 renderAttachments();
 $('#attach').disabled=!state.user.is_admin||!!session.archived_at;setStatus('unknown');sidebar(false);
 for(const button of document.querySelectorAll('.session[data-session-id]'))button.classList.toggle('active',button.dataset.sessionId===session.id);
 bindProjectPanel(session);
 historyCache.active(session.id);
}
async function selectSession(id){
 if(!projectDialog.hidden){resetProjectPanel();$('#project-feedback').textContent='\u6b63\u5728\u5207\u6362\u4f1a\u8bdd';}
 invalidateModelDialog();
 saveDraft();rememberSession();clearTimeout(state.poll);clearTimeout(fillTimer);state.loadController?.abort();
 const controller=new AbortController();state.loadController=controller;
 const cached=historyCache.get(id);state.epoch++;resetBottomFollow();const epoch=state.epoch;selectionTarget=id;state.switching=true;state.session=null;
 state.statusRequest++;state.statusVerified=false;state.status='unknown';state.queuePaused=false;state.queue=[];state.loading=false;
 state.events=new Map();state.eventsDirty=false;state.cursor=0;state.historyBefore=null;state.oldest=false;state.renderLimit=8;
 renderDelivery(null);notice('');$('#confirm-interrupted').hidden=true;
 $('#prompt').value='';$('#prompt').disabled=true;$('#send').disabled=true;$('#attach').disabled=true;
 $('#rename').hidden=true;$('#delete').hidden=true;$('#session-model').hidden=true;
 const list=$('#message-list');
 if(cached){
  state.events=cached.events;state.cursor=cached.cursor;state.historyBefore=cached.before;state.oldest=cached.oldest;state.queue=cached.queue||[];
  showSession(cached.session);$('#usage').textContent=cached.usage||'上下文与用量：未知';
  if(cached.nodes){state.renderLimit=cached.renderLimit||Infinity;list.replaceChildren(...cached.nodes);$('#older').hidden=!state.oldest;}
  else{list.replaceChildren();renderEvents(true);}
  setHistorySync('缓存记录  正在同步');
  scheduleBottomPin();
 }else{
  list.replaceChildren();$('#messages').hidden=false;$('#empty-state').hidden=true;$('#older').hidden=true;$('#usage').textContent='上下文与用量：未知';
  $('#conversation-title').textContent=state.sessions.find(s=>s.id===id)?.title||'正在加载对话';setHistorySync('正在读取聊天记录');
 }
 try{
  const [result,page]=await Promise.all([api('/api/sessions/'+pathId(id),{signal:controller.signal}),cached?Promise.resolve(null):historyPage(id,null,epoch,controller.signal)]);
  if(epoch!==state.epoch)return;
  showSession(result.session);resizePrompt();
  if(!cached){
   state.events=new Map((page.events||[]).map(e=>[e.seq,e]));state.cursor=page.cursor;state.historyBefore=page.before;state.oldest=!!page.has_more;
   renderEvents(true);setHistorySync('');
  }
  await refreshStatus(epoch);if(epoch!==state.epoch)return;startPolling(epoch,true);rememberSession();
 }catch(e){
  if(epoch!==state.epoch)return;
  if(e.status===404||e.status===403){historyCache.remove(id);clearDeletedSession(id);setHistorySync('');notice('此对话已删除或无权访问。');return;}
  selectionTarget=null;state.switching=false;
  setHistorySync(cached?'缓存记录  同步失败':'历史记录加载失败');connectionNotice(e.message);
  if(currentId())startPolling(epoch,true);
 }
}
const observationState=new Map(),retiredGenerations=new Set();let observationGeneration='';
function acceptGeneration(generation){
 if(typeof generation!=='string'||!generation||retiredGenerations.has(generation))return false;
 if(observationGeneration&&observationGeneration!==generation){retiredGenerations.add(observationGeneration);observationState.clear();for(const entry of sessionRuntime.values())entry.received=-Infinity;}
 observationGeneration=generation;return true;
}
function acceptSessionSnapshot(id,data){
 if(!Number.isSafeInteger(data.revision)||!acceptGeneration(data.generation))return false;
 const old=observationState.get(id);
 if(old&&old.generation===data.generation&&old.revision>data.revision)return false;
 observationState.set(id,{generation:data.generation,revision:data.revision});
 sessionRuntime.set(id,{data,received:performance.now()});paintSessionRuntime();
 if(currentId()===id){
  const previous=JSON.stringify([state.status,state.queuePaused,state.queue]);
  state.statusVerified=true;state.queuePaused=data.queue_paused===true;state.queue=Array.isArray(data.queue)?data.queue:[];
  setStatus(data.status);renderDelivery(data);
  if(state.eventsDirty||previous!==JSON.stringify([state.status,state.queuePaused,state.queue]))renderEvents(false);
 }
 return true;
}
async function refreshStatus(epoch) {
 const id=currentId();if(!id)return;
 const request=++state.statusRequest;
 try {
  const data=await api('/api/sessions/'+pathId(currentId())+'/status',{timeoutMs:5000});
  if(!['idle','queued','running','starting','stopping','completed','failed','interrupted','unknown'].includes(data.status))throw new Error('\u6267\u884c\u670d\u52a1\u8fd4\u56de\u65e0\u6548\u72b6\u6001');
  if(epoch===state.epoch && request===state.statusRequest){if(acceptSessionSnapshot(id,data)){probeLocal(id);return true;}return false;}
 }catch(e){if(epoch===state.epoch && request===state.statusRequest){state.statusVerified=false;setStatus('unknown');if(state.eventsDirty)renderEvents(false);connectionNotice('\u72b6\u6001\u83b7\u53d6\u5931\u8d25\uff1a'+e.message);}} return false;
}
function setStatus(value) {
 state.status=value||'unknown'; let names={idle:'\u672a\u5f00\u59cb',queued:'\u6392\u961f\u4e2d',running:'\u8fd0\u884c\u4e2d',starting:'\u542f\u52a8\u4e2d',stopping:'\u505c\u6b62\u4e2d',completed:'\u5df2\u5b8c\u6210',failed:'\u5931\u8d25',interrupted:'\u5df2\u4e2d\u65ad',unknown:'\u72b6\u6001\u672a\u77e5'};
 const label=$('#task-status'); label.textContent=state.status==='queued'&&state.queuePaused?'\u961f\u5217\u5df2\u6682\u505c':names[state.status]||'\u72b6\u6001\u672a\u77e5'; label.className='status-label '+state.status;
 const running=['running','starting','stopping'].includes(state.status); $('#stop').hidden=!running; $('#stop').disabled=state.status==='stopping'||state.stopRequests.has(currentId()); $('#stop').setAttribute('aria-busy',state.stopRequests.has(currentId())?'true':'false');
 $('#confirm-interrupted').hidden=!(currentId() && state.statusVerified && state.status==='unknown' && state.user.is_admin);
 $('#confirm-interrupted').disabled=state.confirming;
 $('#send').setAttribute('aria-busy',state.sending.has(currentId())?'true':'false');
 $('#send').disabled=state.sending.has(currentId())||state.switching||!currentId()||!state.statusVerified||state.status==='stopping'||state.status==='unknown'||attachmentManager.busy(currentId())||!state.user.is_admin||!!state.session?.archived_at||!($('#prompt').value.trim()||state.attachments.some(a=>a.id));
 syncTaskline();
 $('#prompt').placeholder=state.session?.archived_at?'\u5f52\u6863\u4f1a\u8bdd\uff0c\u6062\u590d\u540e\u53ef\u7ee7\u7eed':!state.user.is_admin?'\u5f53\u524d\u4ec5\u7ba1\u7406\u5458\u53ef\u4f7f\u7528\u5df2\u914d\u7f6e\u7684 Claude \u8d26\u6237':state.status==='queued'&&state.queuePaused?'\u961f\u5217\u5df2\u6682\u505c\uff1b\u65b0\u6d88\u606f\u5c06\u6392\u5728\u540e\u9762':state.status==='queued'?'\u672c\u4f1a\u8bdd\u6d88\u606f\u6392\u961f\u4e2d\uff1b\u65b0\u6d88\u606f\u5c06\u52a0\u5165\u961f\u5217':running?'\u8865\u5145\u8bf4\u660e\uff0c\u6309 Enter \u53d1\u9001':state.queue.length?'\u961f\u5217\u5df2\u6682\u505c\uff1b\u65b0\u6d88\u606f\u5c06\u6392\u5728\u540e\u9762':'\u8f93\u5165\u4efb\u52a1\uff0c\u652f\u6301\u7c98\u8d34\u6216\u62d6\u5165\u6587\u4ef6';
}
$('#confirm-interrupted').onclick=async()=>{
 const id=currentId(), epoch=state.epoch;
 if(!id||state.confirming||!state.statusVerified||state.status!=='unknown')return;
 if(!confirm('\u4efb\u52a1\u53ef\u80fd\u5df2\u7ecf\u6267\u884c\u90e8\u5206\u64cd\u4f5c\u5e76\u4fee\u6539\u6587\u4ef6\u3002\u786e\u8ba4\u540e\u4ec5\u6807\u8bb0\u4e2d\u65ad\u5e76\u89e3\u9664\u963b\u585e\uff0c\u4e0d\u4f1a\u81ea\u52a8\u91cd\u53d1\uff1b\u5982\u9700\u7ee7\u7eed\uff0c\u8bf7\u68c0\u67e5\u6587\u4ef6\u540e\u624b\u52a8\u518d\u6b21\u53d1\u9001\u3002\u786e\u5b9a\u5904\u7406\u5417\uff1f'))return;
 state.confirming=true;setStatus(state.status);
 try{
  const result=await api('/api/sessions/'+pathId(id)+'/confirm-interrupted',{method:'POST',body:{confirm:true}});
  if(result.status!=='interrupted')throw new Error('\u6267\u884c\u670d\u52a1\u672a\u786e\u8ba4\u4e2d\u65ad');
  if(epoch===state.epoch){state.statusVerified=false;setStatus('unknown');await refreshStatus(epoch);notice('\u5df2\u6807\u8bb0\u4e2d\u65ad\u3002\u8bf7\u68c0\u67e5\u5df2\u6267\u884c\u7684\u64cd\u4f5c\uff0c\u9700\u8981\u65f6\u518d\u6b21\u53d1\u9001\u3002');}
 }catch(e){if(epoch===state.epoch)notice('\u65e0\u6cd5\u786e\u8ba4\u4e2d\u65ad\uff1a'+e.message);}
 finally{state.confirming=false;if(epoch===state.epoch)setStatus(state.status);}
};
function pollDelay() {return ['running','starting','stopping'].includes(state.status)||(state.status==='queued'&&!state.queuePaused)?300:1500;}
function startPolling(epoch,immediate=false) {
 clearTimeout(state.poll);
 async function tick() {
  if(epoch!==state.epoch||!currentId())return;
  state.pollRunningEpoch=epoch;
  try {
   const failureSerial=connectionFailureSerial;
   const id=currentId(),startCursor=state.cursor; let more=true; let changed=false;
   while(more) { const previousCursor=state.cursor;const data=await api('/api/sessions/'+pathId(id)+'/updates?after='+state.cursor+'&limit=2000',{timeoutMs:8000}); if(epoch!==state.epoch)return;
    for(const e of data.events||[]) {chatProjection.add(state.events,e);state.cursor=Math.max(state.cursor,e.seq);changed=true;state.eventsDirty=true;if(e.type==='claude'&&e.data?.type==='rate_limit_event')scheduleQuotaRefresh();} if(data.format_version!==1||!Number.isSafeInteger(data.cursor)||data.cursor<state.cursor||data.has_more&&data.cursor<=previousCursor)throw Error('Invalid update cursor');state.cursor=data.cursor;more=!!data.has_more;
   }
   const statusOk=await refreshStatus(epoch);
   if(statusOk&&epoch===state.epoch&&id===currentId()){setHistorySync('');if(changed||state.cursor!==startCursor)rememberSession();if(failureSerial===connectionFailureSerial&&state.noticeKind==='connection')notice('');}
  }catch(e){if(epoch===state.epoch){state.statusVerified=false;setStatus('unknown');connectionNotice('连接中断，正在重试：'+e.message);}}
  finally{if(state.pollRunningEpoch===epoch)state.pollRunningEpoch=null;}
  if(epoch===state.epoch)state.poll=setTimeout(tick,pollDelay());
 }
 state.poll=setTimeout(tick,immediate?0:pollDelay());
}
$('#older').onclick=async()=>{
 if(state.loading||!currentId())return;stopFollowingBottom();state.loading=true; const id=currentId(),epoch=state.epoch; const seq=getSequences();
 try {if(!state.historyBefore)return; const view=$('#conversation'); const height=view.scrollHeight;
   const page=await historyPage(id,state.historyBefore,epoch); if(epoch!==state.epoch)return;
   for(const e of page.events||[]) chatProjection.add(state.events,e);state.oldest=!!page.has_more;state.historyBefore=page.before;state.renderLimit=Infinity;renderEvents(false,true);view.scrollTop+=view.scrollHeight-height;
 }catch(e){notice(e.message)}finally{state.loading=false;}
};
function safeMarkdown(container, text) {
 let html=''; try {html=marked.parse(text||'',{gfm:true,breaks:false});}catch{container.textContent=text||'';return;}
 container.innerHTML=DOMPurify.sanitize(html,{USE_PROFILES:{html:true},FORBID_TAGS:['style','form','iframe','object','embed','video','audio']});
 for(const image of container.querySelectorAll('img')) {
  const raw=image.getAttribute('src')||'';
  if(raw.startsWith('/api/files/')) { image.loading='lazy'; continue; }
  if(raw && !/^(?:[a-z]+:|\/\/|#)/i.test(raw) && currentId()) {const full=raw.startsWith('/')?raw:state.session.cwd.replace(/\/$/,'')+'/'+raw;image.src='/api/project-file?session_id='+encodeURIComponent(currentId())+'&path='+encodeURIComponent(full);image.loading='lazy';continue;}
  const alternate=document.createElement('span'); alternate.textContent=image.alt?'[图片：'+image.alt+']':'[图片链接]'; image.replaceWith(alternate);
 }
 for(const a of container.querySelectorAll('a')) {
  const href=a.getAttribute('href')||'';
  if(href.startsWith('/api/files/')) {a.target='_blank';a.rel='noopener noreferrer';continue;}
  if(href && !/^(?:[a-z]+:|\/\/|#)/i.test(href)&&currentId()) {const full=href.startsWith('/')?href:state.session.cwd.replace(/\/$/,'')+'/'+href;a.href='/api/project-file?session_id='+encodeURIComponent(currentId())+'&path='+encodeURIComponent(full);a.target='_blank';a.rel='noopener noreferrer';continue;}
  try {const u=new URL(href,location.origin);if(!['http:','https:','mailto:'].includes(u.protocol))throw Error(); if(u.origin===location.origin&&!href.startsWith('#')){a.removeAttribute('href');continue;} a.target='_blank';a.rel='noopener noreferrer';}catch{a.removeAttribute('href');}
 }
 for(const pre of container.querySelectorAll('pre')) { const code=pre.querySelector('code'); if(!code)continue;const copy=document.createElement('button');copy.className='code-copy';copy.type='button';copy.textContent='复制';copy.onclick=()=>copyText(code.textContent,copy);pre.append(copy); }
}
async function copyText(text,button) {try{await navigator.clipboard.writeText(text);button.textContent='已复制';setTimeout(()=>button.textContent='复制',1500);}catch{notice('复制失败，请手动选择内容。')}}
function addFile(container,file) {
 if(!file||typeof file!=='object'||!file.id)return;
 const a=document.createElement('a');a.className='file-link';a.href='/api/files/'+encodeURIComponent(file.id);a.target='_blank';a.rel='noopener noreferrer';a.textContent=file.name||'附件';container.append(a);
 if(attachmentManager.raster(file)) {const img=document.createElement('img');img.className='file-preview';img.src=a.href;img.alt=file.name||'图片附件';img.loading='lazy';const preview=document.createElement('button');preview.type='button';preview.className='sent-image-preview';preview.setAttribute('aria-label','\u9884\u89c8 '+(file.name||'\u56fe\u7247'));preview.onclick=()=>attachmentManager.open(file);preview.append(img);container.append(preview);}
}
function lazyTool(details,tool){
 if(!tool.historyRef&&!tool.result?.historyRef)return;
 const id=currentId();let loaded=false,busy=false;
 details.addEventListener('toggle',async()=>{
  if(!details.open||loaded||busy)return;busy=true;
  const nodes=details.querySelectorAll('pre');
  try{
   await Promise.all([[tool.historyRef,nodes[0]],[tool.result?.historyRef,nodes[1]]].map(async([ref,node])=>{
    if(!ref||!node)return;
    node.textContent='正在加载详情';
    const value=await api('/api/sessions/'+pathId(id)+'/history-detail?seq='+ref.seq+'&block='+ref.block);
    node.textContent=value.text;
   }));loaded=true;
  }catch(e){for(const node of nodes)node.textContent='详情加载失败：'+e.message+'（收起后可重试）';}
  finally{busy=false;}
 });
}

function messageNode(item) {
 const article=document.createElement('article');article.className='message '+item.role;
 const header=document.createElement('div');header.className='message-head';
 const avatar=document.createElement('span');avatar.className='avatar';
 if(item.role==='user')avatar.textContent='U';
 else{const logo=document.createElement('img');logo.src='/static/claude-code-v1.svg';logo.width=26;logo.height=26;logo.alt='';logo.setAttribute('aria-hidden','true');avatar.classList.add('assistant-logo');avatar.append(logo);}
 const who=document.createElement('strong');who.textContent=item.role==='user'?'你':'Claude Code';const time=document.createElement('time');time.textContent=humanTime(item.time);
 header.append(avatar,who,time);
 if((item.text||'').trim()) {const copy=document.createElement('button');copy.type='button';copy.className='text-button';copy.textContent='\u590d\u5236';copy.onclick=()=>copyText(item.text||'',copy);header.append(copy);}
 const body=document.createElement('div');body.className='message-body'; if(item.local)body.textContent=item.text||'';else safeMarkdown(body,item.text||'');
 if(item.attachments?.length){const files=document.createElement('div');files.className='file-list';for(const f of item.attachments)addFile(files,f);body.append(files);}

 let toolTarget=body;
 if(item.toolGroup) {
  const group=document.createElement('details');group.className='tool-group';group.dataset.groupKeys=JSON.stringify(item.groupKeys||[]);
  const summary=document.createElement('summary');
  const counts=new Map();let failures=0;
  const labels={WebSearch:'\u641c\u7d22\u7f51\u9875',ToolSearch:'\u641c\u7d22\u5de5\u5177',Read:'\u8bfb\u53d6\u6587\u4ef6',Bash:'\u6267\u884c\u547d\u4ee4',Edit:'\u4fee\u6539\u6587\u4ef6',Write:'\u5199\u5165\u6587\u4ef6'};
  for(const tool of item.tools){
   if(tool.error||tool.result?.error)failures++;
   const prefix='\u5de5\u5177\uff1a';
   if(!tool.name?.startsWith(prefix))continue;
   const name=tool.name.slice(prefix.length);const label=labels[name]||name;
   counts.set(label,(counts.get(label)||0)+1);
  }
  const parts=[...counts].map(([name,count])=>name+' '+count+' \u6b21');
  if(failures)parts.push(failures+' \u9879\u5931\u8d25');
  summary.textContent='\u6267\u884c\u8fc7\u7a0b'+(parts.length?' \u00b7 '+parts.join(' \u00b7 '):'');
  group.append(summary);body.append(group);toolTarget=group;
 }
 for(const tool of item.tools||[]) {
  const details=document.createElement('details');if(tool.renderKey)details.dataset.toolKey=tool.renderKey;const summary=document.createElement('summary');summary.textContent=tool.name||'\u6267\u884c\u8fc7\u7a0b';
  const pre=document.createElement('pre');pre.className='tool-output';pre.textContent=tool.text;details.append(summary,pre);
  if(tool.result) {
   const resultLabel=document.createElement('div');resultLabel.textContent=tool.result.error?'\u5de5\u5177\u7ed3\u679c\uff08\u5931\u8d25\uff09':'\u5de5\u5177\u7ed3\u679c';
   const result=document.createElement('pre');result.className='tool-output';result.textContent=tool.result.text;details.append(resultLabel,result);
  }
  lazyTool(details,tool);toolTarget.append(details);
 }
 if(item.queued){
  const controls=document.createElement('div');controls.className='queue-actions';
  const cancel=document.createElement('button');cancel.type='button';cancel.className='text-button';cancel.textContent='取消排队';
  cancel.onclick=async()=>{try{await api('/api/sessions/'+pathId(currentId())+'/queue/'+pathId(item.request_id),{method:'DELETE'});const local=sendOutbox.get(currentId(),item.request_id);if(local)dropLocal(local);await refreshStatus(state.epoch);}catch(e){notice(e.message)}};controls.append(cancel);
  if(!['running','starting','stopping','unknown'].includes(state.status) && (state.status!=='queued'||state.queuePaused)) {const resume=document.createElement('button');resume.type='button';resume.className='text-button';resume.textContent='继续队列';resume.onclick=async()=>{try{const r=await api('/api/sessions/'+pathId(currentId())+'/resume-queue',{method:'POST'});if(r.blocked)notice('队列暂停：'+r.blocked);await refreshStatus(state.epoch)}catch(e){notice(e.message)}};controls.append(resume);}
  body.append(controls);
 }
 if(item.local){
  article.classList.add('local-message');
  const entry=sendOutbox.get(currentId(),item.request_id);if(entry)body.append(localReceiptNode(entry));
 }
 article.append(header,body);return article;
}
function renderEvents(initial=false,keepPosition=false) {
 reconcileLocalEvents();
 const view=$('#conversation');
 const sessionKey=currentId()||'';
 const projection=chatProjection.project(state.events,sessionKey);
 const items=projection.items.slice();
 const authoritative=new Set(items.map(item=>item.key));
 for(const entry of sendOutbox.entries(sessionKey)){
  if(!authoritative.has(localRequestKey(sessionKey,entry.request_id)))items.push(localItem(entry));
 }
 const grouped=[];let group=null;
 for(const item of items) {
  if(item.boundary){group=null;continue;}
  const pure=!item.queued&&item.role==='assistant'&&!(item.text||'').trim()&&!(item.attachments||[]).length&&(item.tools||[]).length;
  if(!pure){group=null;grouped.push(item);continue;}
  if(!group){group={key:item.key,role:'assistant',text:'',time:item.time,toolGroup:true,tools:[],groupKeys:[]};grouped.push(group);}
  group.groupKeys.push(...(item.memberKeys||[item.key]));
  item.tools.forEach((tool,index)=>group.tools.push({...tool,renderKey:tool.renderKey||item.key+':tool:'+index}));
 }
 const total=grouped.length;const visible=grouped.slice(-Math.max(1,state.renderLimit||8));
 const target=$('#message-list'),old=new Map(),openGroups=new Set(),openTools=new Set();
 for(const node of target.children) {
  old.set(node.dataset.renderKey,node);
  if(node.__hasTools){
   for(const detail of node.querySelectorAll('details.tool-group'))if(detail.open){try{for(const key of JSON.parse(detail.dataset.groupKeys||'[]'))openGroups.add(key);}catch{}}
   for(const detail of node.querySelectorAll('details[data-tool-key]'))if(detail.open)openTools.add(detail.dataset.toolKey);
  }
 }
 let cursor=target.firstChild;
 for(const item of visible) {
  if(!item.queued&&!(item.text||'').trim()&&!(item.attachments||[]).length&&!(item.tools||[]).length)continue;
  const key=item.key,signature=JSON.stringify([item,item.queued?state.status:null,item.queued?state.queuePaused:null]);
  let node=old.get(key);
  if(!node||node.__renderSignature!==signature) {
   const previous=node,open=previous?[...previous.querySelectorAll('details')].map(detail=>detail.open):[];
   node=messageNode(item);node.dataset.renderKey=key;node.__renderSignature=signature;node.__hasTools=!!item.tools?.length;
   if(item.toolGroup){
    const group=node.querySelector('details.tool-group');
    if(group&&(item.groupKeys||[]).some(key=>openGroups.has(key)))group.open=true;
    for(const detail of node.querySelectorAll('details[data-tool-key]'))if(openTools.has(detail.dataset.toolKey))detail.open=true;
   }else [...node.querySelectorAll('details')].forEach((detail,index)=>{if(open[index])detail.open=true;});
   if(previous){if(cursor===previous)cursor=node;previous.replaceWith(node);}
  }
  if(node!==cursor)target.insertBefore(node,cursor);
  cursor=node.nextSibling;old.delete(key);
 }
 for(const node of old.values())node.remove();
 state.eventsDirty=false;$('#older').hidden=!state.oldest;
 renderActivityUsage();
 if(!keepPosition&&state.followBottom){pinConversationBottom();scheduleBottomPin();}
 rememberSession();clearTimeout(fillTimer);
 if((state.renderLimit||8)<total){const epoch=state.epoch;fillTimer=setTimeout(()=>{if(epoch!==state.epoch)return;const pinned=state.followBottom,top=view.scrollTop,height=view.scrollHeight;state.renderLimit=(state.renderLimit||8)+8;renderEvents(false,true);view.scrollTop=pinned?view.scrollHeight:top+view.scrollHeight-height;},32);}
}
$('#prompt').addEventListener('input',()=>{saveDraft();resizePrompt();setStatus(state.status)});
function resizePrompt(){const p=$('#prompt');p.style.height='auto';p.style.height=Math.min(p.scrollHeight,180)+'px';}
$('#prompt').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('#composer').requestSubmit();}});
function pendingMap(key) {
 let data; try {data=JSON.parse(localStorage.getItem(key)||'{}');}catch{data={};}
 if(!data || typeof data!=='object' || Array.isArray(data)) data={};
 if(typeof data.signature==='string' && typeof data.request_id==='string') return {[data.signature]:data.request_id};
 return data;
}
function pendingId(key, signature) {
 const data=pendingMap(key);
 if(!data[signature]) {
  const bytes=new Uint8Array(16); crypto.getRandomValues(bytes);
  data[signature]=Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');
  localStorage.setItem(key,JSON.stringify(data));
 }
 return data[signature];
}
function clearPending(key, signature, requestId) {
 const data=pendingMap(key);
 if(data[signature]!==requestId)return;
 delete data[signature];
 if(Object.keys(data).length) localStorage.setItem(key,JSON.stringify(data)); else localStorage.removeItem(key);
}
async function sendTurn(id, body) {
 return await api('/api/sessions/'+pathId(id)+'/turns',{method:'POST',body});
}
$('#composer').onsubmit=e=>{
 e.preventDefault();const id=currentId();if(!id||$('#send').disabled)return;
 const value=$('#prompt').value,files=state.attachments.filter(a=>a.id);
 if(!value.trim()&&!files.length)return;
 const signature=JSON.stringify([value,files.map(a=>a.id)]);
 const pendingKey='cc-workbench-pending:'+state.user.username+':'+id;
 let requestId;
 try{requestId=pendingId(pendingKey,signature);}catch(error){notice('\u6d4f\u89c8\u5668\u65e0\u6cd5\u4fdd\u5b58\u53d1\u9001\u7f16\u53f7\uff0c\u672c\u6b21\u672a\u63d0\u4ea4\u3002\u8bf7\u5141\u8bb8\u672c\u5730\u5b58\u50a8\u6216\u91ca\u653e\u7a7a\u95f4\u540e\u91cd\u8bd5\u3002');return;}
 const existing=sendOutbox.get(id,requestId);
 if(existing){retryLocal(existing);return;}
 if(sendOutbox.entries(id).length>=50){notice('\u5f85\u6838\u5bf9\u6d88\u606f\u5df2\u8fbe50\u6761\uff0c\u8bf7\u5148\u6838\u5bf9\u53d1\u9001\u7ed3\u679c');return;}
 const entry=sendOutbox.add({session_id:id,request_id:requestId,prompt:value,
  attachments:files.map(a=>({id:a.id,name:a.name,content_type:a.content_type})),
  created_at:Date.now(),phase:'sending',error:'',signature,pendingKey,inflight:true});
 state.sending.add(id);sendFlights.set(id,requestId);$('#send').disabled=true;
 appendImmediate(entry);$('#prompt').value='';
 const remaining=state.attachments.filter(a=>!files.includes(a));
 state.attachments=remaining;state.attachmentDrafts.set(id,remaining);
 resetBottomFollow();scheduleBottomPin();
 setTimeout(()=>{
  submitLocal(entry);
  if(localVisible(entry)){resizePrompt();renderAttachments();try{saveDraft();}catch{}}
 },0);
};
$('#stop').onclick=async()=>{
 const id=currentId(),epoch=state.epoch;if(!id||state.stopRequests.has(id))return;
 state.stopRequests.add(id);setStatus(state.status);notice('\u6b63\u5728\u8bf7\u6c42\u505c\u6b62\u2026');
 try{await api('/api/sessions/'+pathId(id)+'/stop',{method:'POST'});if(epoch===state.epoch)await refreshStatus(epoch);}
 catch(e){if(epoch===state.epoch)notice('\u505c\u6b62\u5931\u8d25\uff1a'+e.message);}
 finally{state.stopRequests.delete(id);if(currentId()===id&&!state.switching){if($('#notice').textContent==='\u6b63\u5728\u8bf7\u6c42\u505c\u6b62\u2026')notice('');setStatus(state.status);}}
};
function updateConversationMeta() {
 if(!state.session)return;
 const s=state.session,p=providerFor(s.provider),item=selectedModel(p,s.model||'');
 const model=s.model?(item?modelLabel(item):s.model):'默认模型';
 const effort=s.reasoning_effort?(effortNames[s.reasoning_effort]||s.reasoning_effort):'默认强度';
 if(s.collaboration_enabled){
  const coder=effortNames[s.coder_effort]||s.coder_effort||effortNames.high;
  const reviewer=effortNames[s.reviewer_effort]||s.reviewer_effort||effortNames.high;
  $('#conversation-meta').textContent='Claude Code \u00b7 '+s.cwd+' \u00b7 Opus 5.5 '+coder+' \u00b7 Fable 5.2 \u5ba1\u6838 '+reviewer;
  $('#session-model').textContent='Coder + Reviewer \u00b7 \u534f\u4f5c';return;
 }
 $('#conversation-meta').textContent='Claude Code · '+s.cwd+' · '+model+' · '+effort;
 $('#session-model').textContent=model+' \u00b7 '+effort;prepareModelDialog();
}
function missingSelection(select) { return select.selectedOptions[0]?.dataset.missing==='1'; }
function refreshModelNote(provider) {
 const m=$('#session-model-select'),e=$('#session-effort-select'),unavailable=missingSelection(m)||missingSelection(e);
 const invalid=unavailable?'当前保存的选项不在可用列表中；请选择有效选项或恢复默认值。':'';
 const unknown=provider?.model_list_available?'支持的强度未知时使用默认值。':'可以保留原设置或恢复默认值。';
 $('#model-note').textContent=invalid+modelListNote(provider)+unknown;
 $('#save-model').disabled=unavailable;
}
let modelEditor=null,preparedModelKey='',preparedModelProvider=null;
function modelEditorActive(editor){return !!editor&&modelEditor===editor&&editor.id===currentId()&&editor.epoch===state.epoch&&$('#model-dialog').open;}
function prepareModelDialog(force=false){
 if(!state.session||!state.user?.is_admin||$('#model-dialog').open)return false;
 const s=state.session,key=JSON.stringify([s.id,s.model||'',s.reasoning_effort||'',providerVersion]);
 if(!force&&key===preparedModelKey)return true;
 const p=providerFor(s.provider);
 populateModels(p,$('#session-model-select'),$('#session-effort-select'),s.model||null,s.reasoning_effort||null);
 preparedModelKey=key;preparedModelProvider=p;refreshModelNote(p);return true;
}
function invalidateModelDialog(){
 modelEditor=null;preparedModelKey='';
 if($('#model-dialog').open)$('#model-dialog').close();
}
function modelDialogNote(editor){
 refreshModelNote(editor.provider);
 const note=$('#model-note');
 if(providerRequest&&!providerModels(editor.provider).length)note.textContent+='\n\u6b63\u5728\u52a0\u8f7d\u771f\u5b9e\u6a21\u578b\u5217\u8868\uff0c\u8bf7\u7a0d\u5019\u3002';
 else if(providerRequest)note.textContent+='\n\u6b63\u5728\u540e\u53f0\u5237\u65b0\uff0c\u5df2\u6709\u5019\u9009\u53ef\u7ee7\u7eed\u9009\u62e9\u3002';
 if(editor.deferred)note.textContent+='\n\u5217\u8868\u5df2\u66f4\u65b0\uff1b\u4fdd\u7559\u672c\u6b21\u9009\u62e9\uff0c\u4e0b\u6b21\u6253\u5f00\u4f7f\u7528\u65b0\u5217\u8868\u3002';
 if(editor.refreshFailed)note.textContent+='\n\u66f4\u65b0\u5931\u8d25\uff0c\u4fdd\u7559\u5df2\u53d6\u5f97\u7684\u5019\u9009\u548c\u5f53\u524d\u9009\u62e9\u3002';
 $('#save-model').disabled=editor.saving||missingSelection($('#session-model-select'))||missingSelection($('#session-effort-select'));
}
async function refreshModelDialog(editor){
 if(!modelEditorActive(editor))return;
 const request=loadProviders({maxAge:60000});modelDialogNote(editor);
 const ok=await request;if(!modelEditorActive(editor))return;
 editor.refreshFailed=!ok;
 if(editor.revision!==providerVersion){
  if(!editor.dirty&&!editor.saving){
   const m=$('#session-model-select').value,e=$('#session-effort-select').value;
   editor.provider=providerFor(state.session.provider);editor.revision=providerVersion;
   populateModels(editor.provider,$('#session-model-select'),$('#session-effort-select'),m||null,e||null);
  }else editor.deferred=true;
 }
 modelDialogNote(editor);
}
$('#session-model-select').onchange=()=>{
 if(!modelEditor||!modelEditorActive(modelEditor))return;
 modelEditor.dirty=true;
 populateEfforts(modelEditor.provider,$('#session-model-select'),$('#session-effort-select'),null);
 modelDialogNote(modelEditor);
};
$('#session-effort-select').onchange=()=>{if(modelEditorActive(modelEditor)){modelEditor.dirty=true;modelDialogNote(modelEditor);}};
$('#model-dialog').addEventListener('close',()=>{
 if($('#model-dialog').open)return;
 modelEditor=null;preparedModelKey='';prepareModelDialog();
});
$('#session-model').onclick=()=>{
 if(state.session?.collaboration_enabled){openCollaboration(state.session);return;}
 const id=currentId(),dialog=$('#model-dialog');
 if(!id||state.switching||!state.user?.is_admin||$('#session-model').disabled||dialog.open)return;
 if(modelEditor?.dirty)preparedModelKey='';
 prepareModelDialog();
 const editor={id,epoch:state.epoch,provider:preparedModelProvider,revision:providerVersion,dirty:false,saving:false};
 modelEditor=editor;$('#model-error').hidden=true;
 $('#save-model').disabled=missingSelection($('#session-model-select'))||missingSelection($('#session-effort-select'));dialog.showModal();
 setTimeout(()=>refreshModelDialog(editor),0);
};
$('#model-form').onsubmit=async event=>{
 event.preventDefault();const editor=modelEditor;
 if(!editor||!modelEditorActive(editor)||editor.saving||missingSelection($('#session-model-select'))||missingSelection($('#session-effort-select')))return;
 const error=$('#model-error');error.hidden=true;editor.saving=true;modelDialogNote(editor);
 try{
  const result=await api('/api/sessions/'+pathId(editor.id),{method:'PATCH',body:{model:$('#session-model-select').value||null,reasoning_effort:$('#session-effort-select').value||null}});
  if(!modelEditorActive(editor))return;
  state.session=result.session;updateConversationMeta();rememberSession();$('#model-dialog').close();notice('\u6a21\u578b\u8bbe\u7f6e\u5df2\u4fdd\u5b58\uff0c\u5bf9\u4e4b\u540e\u65b0\u53d1\u9001\u7684\u6d88\u606f\u751f\u6548\u3002');
 }catch(e){if(modelEditorActive(editor)){error.textContent=e.message;error.hidden=false;}}
 finally{editor.saving=false;if(modelEditorActive(editor))modelDialogNote(editor);}
};

async function renameSession(id,oldTitle) {
 if(!id)return;
 const title=prompt('\u8f93\u5165\u65b0\u7684\u4f1a\u8bdd\u540d\u79f0',oldTitle);
 if(title===null||!title.trim())return;
 try{
  const r=await api('/api/sessions/'+pathId(id),{method:'PATCH',body:{title:title.trim()}});
  if(currentId()===id&&r.session){
   state.session.title=r.session.title;
   $('#conversation-title').textContent=r.session.title;
  }
  await loadSessions();
 }catch(e){notice(e.message)}
}
function clearDeletedSession(id) {
 if(projectSession?.id===id)closeProjectPanel(false);
 if(currentId()===id)invalidateModelDialog();
 for(const entry of sendOutbox.entries(id))sendOutbox.remove(id,entry.request_id);
 historyCache.remove(id);clearTimeout(fillTimer);
 if(currentId()!==id&&selectionTarget!==id)return;
 selectionTarget=null;state.epoch++;stopFollowingBottom();clearTimeout(state.poll);state.statusRequest++;
 state.session=null;state.switching=false;state.events=new Map();setHistorySync('');state.eventsDirty=false;
 state.attachments=[];state.cursor=0;state.oldest=false;state.loading=false;
 state.queue=[];state.queuePaused=false;state.statusVerified=false;
 $('#empty-state').hidden=false;$('#messages').hidden=true;
 $('#conversation-title').textContent='\u5f00\u59cb\u4e00\u6bb5\u5de5\u4f5c';
 $('#conversation-meta').textContent='\u9009\u62e9\u6216\u521b\u5efa\u4f1a\u8bdd';
 $('#prompt').value='';$('#prompt').disabled=true;$('#send').disabled=true;
 $('#attach').disabled=true;$('#rename').hidden=true;$('#delete').hidden=true;
 $('#session-model').hidden=true;$('#confirm-interrupted').hidden=true;
 $('#usage').textContent='\u4e0a\u4e0b\u6587\u4e0e\u7528\u91cf\uff1a\u672a\u77e5';
 setStatus('idle');renderAttachments();
}
async function deleteSession(id,title) {
 if(!id)return;
 const msg='\u786e\u5b9a\u5220\u9664\u4f1a\u8bdd\u201c'+title+'\u201d\uff1f\n\n'
  +'\u5c06\u5220\u9664\u5de5\u4f5c\u53f0\u4e2d\u7684\u5386\u53f2\u3001\u6392\u961f\u6d88\u606f\u53ca\u8be5\u4f1a\u8bdd\u7684\u4e0a\u4f20\u6682\u5b58\u526f\u672c\uff0c\u65e0\u6cd5\u64a4\u9500\u3002\n'
  +'Claude \u81ea\u8eab\u7684\u5386\u53f2\u8bb0\u5f55\u53ef\u80fd\u4ecd\u4fdd\u7559\u3002';
 if(!confirm(msg))return;
 try{
  const deleted=await api('/api/sessions/'+pathId(id),{method:'DELETE'});
  attachmentManager.clearSession(id);clearDeletedSession(id);
  if(deleted.provider_history_retained)alert('\u4f1a\u8bdd\u5df2\u4ece\u5de5\u4f5c\u53f0\u79fb\u9664\uff1bClaude \u81ea\u8eab\u7684\u5386\u53f2\u8bb0\u5f55\u4ecd\u4f1a\u4fdd\u7559\u3002');
  await loadSessions();
 }catch(e){notice(e.message==='The task is running or in an unknown state'?'\u4f1a\u8bdd\u4efb\u52a1\u8fd0\u884c\u4e2d\u6216\u72b6\u6001\u672a\u77e5\uff0c\u6682\u4e0d\u80fd\u5220\u9664':e.message)}
}
$('#rename').onclick=()=>{if(state.session)renameSession(state.session.id,state.session.title);};
$('#delete').onclick=()=>{if(state.session)deleteSession(state.session.id,state.session.title);};
$('#settings-open').onclick=()=>{$('#user-feedback').hidden=true;$('#settings-dialog').showModal();};
$('#user-form').onsubmit=async e=>{e.preventDefault();const form=e.target,feedback=$('#user-feedback'),data=new FormData(form);try{await api('/api/users',{method:'POST',body:{username:data.get('username'),password:data.get('password')}});form.reset();feedback.textContent='用户已创建。当前 Claude 账户仅供管理员使用。';feedback.className='help';feedback.hidden=false;}catch(error){feedback.textContent=error.message;feedback.className='error';feedback.hidden=false;}};
const attachmentManager=new AttachmentManager({state,changed:()=>setStatus(state.status),notice});
function renderAttachments(){attachmentManager.render();}
function uploadFiles(files){attachmentManager.add(files);}
$('#attach').onclick=()=>$('#files').click();$('#files').onchange=e=>{uploadFiles(e.target.files);e.target.value='';};
$('#prompt').addEventListener('paste',e=>{const files=[...(e.clipboardData?.items||[])].filter(x=>x.kind==='file').map(x=>x.getAsFile()).filter(Boolean);if(files.length)uploadFiles(files);});
const workspace=$('.workspace');workspace.addEventListener('dragover',e=>{if([...e.dataTransfer.types].includes('Files')){e.preventDefault();workspace.classList.add('drag-over');}});workspace.addEventListener('dragleave',()=>workspace.classList.remove('drag-over'));workspace.addEventListener('drop',e=>{e.preventDefault();workspace.classList.remove('drag-over');if(e.dataTransfer.files.length)uploadFiles(e.dataTransfer.files);});


/* The page reads saved usage; only an explicit dialog action starts an official query. */
const quotaNames={five_hour:'5\u5c0f\u65f6',seven_day:'\u672c\u5468',fable:'Fable'};
let quotaRequest=null,quotaSnapshot=null,quotaFetchError=false,quotaSoon=null,quotaResetTimer=null,quotaPendingRefresh=false;
let quotaPollTimer=null,quotaModalSerial=0,quotaAppliedSerial=0,quotaRequestSerial=0;
function quotaPercent(n) {
 if(n>0&&n<0.1)return '<0.1%';
 return (Math.floor(n*10)/10).toLocaleString('zh-CN',{maximumFractionDigits:1})+'%';
}
function quotaTime(value) {
 const date=new Date(typeof value==='number'?value*1000:value);
 if(Number.isNaN(date.valueOf()))return '';
 return date.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
}
function scheduleQuotaExpiry() {
 clearTimeout(quotaResetTimer);const now=Date.now()/1000;
 const values=Object.values(quotaSnapshot?.windows||{}).filter(item=>item?.state==='known'&&Number.isFinite(item.resets_at)&&item.resets_at>now).map(item=>item.resets_at);
 if(values.length)quotaResetTimer=setTimeout(renderQuota,Math.min(2147483647,Math.max(0,(Math.min(...values)-now)*1000+50)));
}
function renderQuota() {
 if(!state.user?.is_admin)return;
 for(const row of document.querySelectorAll('[data-quota-key]')) {
  const key=row.dataset.quotaKey,item=quotaSnapshot?.windows?.[key],trigger=row.querySelector('.quota-trigger'),progress=row.querySelector('.quota-progress'),value=row.querySelector('.quota-value'),detail=row.querySelector('.quota-detail');
  const now=Date.now()/1000,used=item?.used_percent,remaining=item?.remaining_percent,reset=item?.resets_at,stamp=item?.updated_at,time=stamp?quotaTime(stamp):'';
  const resetOk=reset==null||(Number.isFinite(reset)&&reset>now);
  const valid=item?.state==='known'&&Number.isFinite(used)&&used>=0&&used<=100&&Number.isFinite(remaining)&&remaining>=0&&remaining<=100&&Math.abs(100-used-remaining)<0.11&&!!time&&resetOk;
  if(!valid) {
   row.dataset.known='false';progress.hidden=true;progress.removeAttribute('value');progress.removeAttribute('data-level');value.textContent=item?.state==='expired'||(!resetOk&&reset!=null)?'\u5df2\u91cd\u7f6e\uff0c\u5f85\u66f4\u65b0':quotaFetchError?'\u8bfb\u53d6\u5931\u8d25':quotaSnapshot?.refresh_status==='pending'?'\u67e5\u8be2\u4e2d':'\u6682\u65e0\u6570\u636e';
   const reason=quotaSnapshot?.refresh_status==='busy'&&quotaSnapshot.refresh_reason?quotaSnapshot.refresh_reason:quotaSnapshot?.refresh_status==='pending'?'正在查询该项额度':quotaFetchError?'\u6682\u65f6\u65e0\u6cd5\u8bfb\u53d6\u989d\u5ea6':item?.state==='expired'||(!resetOk&&reset!=null)?'\u5df2\u8fc7\u91cd\u7f6e\u65f6\u95f4\uff0c\u7b49\u5f85\u66f4\u65b0':'\u5b98\u65b9\u6682\u672a\u63d0\u4f9b\u53ef\u786e\u8ba4\u7684\u989d\u5ea6';
   if(detail)detail.textContent=reason+(time?'\n'+(item?.timestamp_kind==='queried'?'\u6700\u8fd1\u67e5\u8be2\uff1a':'\u4e0a\u6b21\u4e0a\u62a5\uff1a')+time:'');
   if(trigger){trigger.setAttribute('aria-label',quotaNames[key]+' \u989d\u5ea6\u672a\u77e5\uff0c\u70b9\u51fb\u67e5\u770b\u8be6\u60c5');trigger.title=reason;}
   continue;
  }
  row.dataset.known='true';progress.hidden=false;progress.value=remaining;
  progress.dataset.level=remaining===0?'empty':remaining<20?'low':remaining<50?'warn':'good';
  const number=quotaPercent(remaining);value.textContent=(item?.stale?'上次 ':'')+(remaining===0?'\u5df2\u7528\u5b8c':number+'\u53ef\u7528');
  progress.setAttribute('aria-label',quotaNames[key]+'\u53ef\u7528\u989d\u5ea6 '+number);
  const timeText=(item?.stale?'数据超过5分钟，点击查询更新。\n':'')+(item?.timestamp_kind==='queried'?'\u6700\u8fd1\u67e5\u8be2\uff1a':'\u4e0a\u6b21\u4e0a\u62a5\uff1a')+time;
  if(trigger){trigger.setAttribute('aria-label',quotaNames[key]+' '+value.textContent+'\uff0c\u70b9\u51fb\u67e5\u770b\u8be6\u60c5');trigger.title=timeText;}
  if(detail)detail.textContent='\u5df2\u4f7f\u7528 '+quotaPercent(used)+' \u00b7 \u91cd\u7f6e\uff1a'+(reset!=null?quotaTime(reset):'\u672a\u77e5')+'\n'+timeText;
 }
 scheduleQuotaExpiry();
}
function applyQuota(data,serial) {
 if(serial<quotaAppliedSerial)return;
 quotaAppliedSerial=serial;quotaSnapshot=data;quotaFetchError=false;renderQuota();
}
function refreshQuota() {
 if(!state.user?.is_admin)return Promise.resolve(null);
 if(quotaRequest)return quotaRequest;
 const serial=++quotaRequestSerial;
 quotaRequest=(async()=>{
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),5000);
  try{const data=await api('/api/quota',{signal:controller.signal});applyQuota(data,serial);return data;}
  catch(e){if(serial>=quotaAppliedSerial){quotaFetchError=true;renderQuota();}return null;}
  finally{clearTimeout(timeout);}
 })().finally(()=>{quotaRequest=null;if(quotaPendingRefresh){quotaPendingRefresh=false;refreshQuota();}});
 return quotaRequest;
}
function scheduleQuotaRefresh() {
 if(!state.user?.is_admin)return;
 clearTimeout(quotaSoon);quotaSoon=setTimeout(()=>{if(quotaRequest)quotaPendingRefresh=true;else refreshQuota();},400);
}
function quotaStatusText(data) {
 const stateText=$('#quota-refresh-state'),status=data?.refresh_status;
 if(status==='pending'){stateText.textContent='\u6b63\u5728\u67e5\u8be2\u5b98\u65b9\u989d\u5ea6\u2026';return;}
 if(status==='success'){stateText.textContent='\u67e5\u8be2\u5b8c\u6210\uff1b\u5b98\u65b9\u6570\u636e\u53ef\u80fd\u6709\u7f13\u5b58\u5ef6\u8fdf\u3002';return;}
 if(status==='busy'){stateText.textContent='\u4e0a\u6b21\u67e5\u8be2\u672a\u6267\u884c\uff1a'+(data?.refresh_reason||'\u4efb\u52a1\u8fd0\u884c\u6216\u6392\u961f\u4e2d\uff0c\u6682\u65f6\u65e0\u6cd5\u67e5\u8be2\u3002')+'\u3002\u4efb\u52a1\u5b8c\u6210\u540e\u53ef\u70b9\u51fb\u91cd\u8bd5\u3002';return;}
 if(status==='failed'){stateText.textContent=data?.refresh_reason||'\u67e5\u8be2\u5931\u8d25\uff0c\u663e\u793a\u5df2\u4fdd\u5b58\u7684\u989d\u5ea6\u3002';return;}
 stateText.textContent='\u663e\u793a\u6700\u8fd1\u4fdd\u5b58\u7684\u989d\u5ea6\uff1b\u5b98\u65b9\u6570\u636e\u53ef\u80fd\u6709\u5ef6\u8fdf\u3002';
}
function quotaStatus(data){quotaStatusText(data);if(data?.refresh_warning)$('#quota-refresh-state').textContent+=' '+data.refresh_warning;}
async function requestQuotaDetails() {
 const dialog=$('#quota-dialog');if(!dialog.open||!state.user?.is_admin)return;
 const cycle=++quotaModalSerial;clearTimeout(quotaPollTimer);quotaPollTimer=null;
 $('#quota-refresh-state').textContent='\u6b63\u5728\u67e5\u8be2\u5b98\u65b9\u989d\u5ea6\u2026';
 $('#quota-retry').disabled=true;
 if(quotaRequest){await quotaRequest;if(!dialog.open||cycle!==quotaModalSerial)return;}
 const serial=++quotaRequestSerial;
 try{
  const data=await api('/api/quota/refresh',{method:'POST'});applyQuota(data,serial);
  if(!dialog.open||cycle!==quotaModalSerial)return;
  quotaStatus(data);
  const refreshId=data?.refresh_id;
  if(data?.refresh_status==='pending'&&refreshId!=null){
   let attempts=0;
   const poll=async()=>{
    quotaPollTimer=null;
    if(!dialog.open||cycle!==quotaModalSerial)return;
    if(quotaRequest){await quotaRequest;if(!dialog.open||cycle!==quotaModalSerial)return;}
    const result=await refreshQuota();
    if(!dialog.open||cycle!==quotaModalSerial)return;
    if(!result){$('#quota-refresh-state').textContent='\u8bfb\u53d6\u67e5\u8be2\u7ed3\u679c\u5931\u8d25\uff0c\u8bf7\u91cd\u8bd5\u3002';$('#quota-retry').disabled=false;return;}
    if(result.refresh_id!==refreshId){$('#quota-refresh-state').textContent='\u67e5\u8be2\u72b6\u6001\u5df2\u6539\u53d8\uff0c\u8bf7\u91cd\u8bd5\u3002';$('#quota-retry').disabled=false;return;}
    quotaStatus(result);
    if(result.refresh_status==='pending'&&++attempts<20){quotaPollTimer=setTimeout(poll,1000);}
    else {if(result.refresh_status==='pending')$('#quota-refresh-state').textContent='\u67e5\u8be2\u4ecd\u672a\u5b8c\u6210\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5\u3002';$('#quota-retry').disabled=false;}
   };
   quotaPollTimer=setTimeout(poll,1000);return;
  }
  if(data?.refresh_status==='pending')$('#quota-refresh-state').textContent='\u67e5\u8be2\u72b6\u6001\u672a\u77e5\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5\u3002';
 }catch(e){if(dialog.open&&cycle===quotaModalSerial)$('#quota-refresh-state').textContent='\u67e5\u8be2\u5931\u8d25\uff1a'+e.message;}
 finally{if(dialog.open&&cycle===quotaModalSerial&&quotaPollTimer===null)$('#quota-retry').disabled=false;}
}
function initQuota() {
 const meter=$('#quota-meter'),dialog=$('#quota-dialog');meter.hidden=!state.user?.is_admin;if(meter.hidden)return;
 $('#quota-open').onclick=()=>{dialog.showModal();requestQuotaDetails();};
 $('#quota-retry').onclick=()=>requestQuotaDetails();
 dialog.addEventListener('close',()=>{quotaModalSerial++;clearTimeout(quotaPollTimer);quotaPollTimer=null;$('#quota-retry').disabled=false;});
 document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')renderQuota();});
 refreshQuota();
}

let collaborationEditor=null,collaborationCycle=0;
const pinRequests=new Set();
function roleCandidate(provider,role){
 const target=provider?.collaboration_models?.[role];if(!target)return undefined;
 return providerModels(provider).find(item=>item?.selectable===true&&
  (item.resolvedModel||modelValue(item))===target);
}
function fillRoleEffort(prefix,role,value,provider){
 const select=$('#'+prefix+'-'+role),item=roleCandidate(provider,role);
 const levels=Array.isArray(item?.supportedEffortLevels)?item.supportedEffortLevels:[];
 select.replaceChildren();
 for(const level of levels)select.add(new Option(effortNames[level]||level,level));
 if(!levels.includes(value)){
  const option=new Option((effortNames[value]||value)+'\uff08\u5f53\u524d\u4e0d\u53ef\u7528\uff09',value);
  option.dataset.missing='1';select.add(option);
 }
 select.value=value;select.disabled=!levels.length;
}
function collaborationFields(prefix,provider,values){
 fillRoleEffort(prefix,'reviewer',values.reviewer_effort||'high',provider);
 fillRoleEffort(prefix,'coder',values.coder_effort||'high',provider);
}
function collaborationInvalid(prefix){
 return ['reviewer','coder'].some(role=>missingSelection($('#'+prefix+'-'+role)));
}
function collaborationVisibility(prefix){
 const enabled=$('#'+prefix+'-enabled').checked;
 $('#'+prefix+'-roles').hidden=!enabled;
 if(prefix==='new-collaboration'){
  $('#model').disabled=enabled||$('#model').options.length===1;
  $('#effort').disabled=enabled||$('#effort').options.length===1;
 }else $('#collaboration-save').disabled=!!collaborationEditor?.saving||
   enabled&&collaborationInvalid(prefix)||!!collaborationEditor?.loading||!!collaborationEditor?.failed;
}
function newCollaborationOptions(){
 const prefix='new-collaboration',provider=providerFor($('#provider').value);
 const values={reviewer_effort:$('#'+prefix+'-reviewer').value||'high',
  coder_effort:$('#'+prefix+'-coder').value||'high'};
 collaborationFields(prefix,provider,values);collaborationVisibility(prefix);
}
$('#new-collaboration-enabled').onchange=()=>collaborationVisibility('new-collaboration');
function collaborationActive(editor){
 return !!editor&&editor===collaborationEditor&&editor.cycle===collaborationCycle&&$('#collaboration-dialog').open;
}
function collaborationNote(editor){
 const enabled=$('#collaboration-enabled').checked;
 const unavailable=enabled&&collaborationInvalid('collaboration');
 $('#collaboration-note').textContent=(unavailable?
  '\u65e0\u6cd5\u786e\u8ba4\u6240\u9700\u6a21\u578b\u6216\u5f3a\u5ea6\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5\u3002':
  '\u4ec5\u5f71\u54cd\u4e4b\u540e\u65b0\u53d1\u9001\u7684\u6d88\u606f\uff1b\u8fd0\u884c\u4e2d\u548c\u5df2\u6392\u961f\u7684\u4efb\u52a1\u4fdd\u6301\u539f\u8bbe\u7f6e\u3002')+
  (editor.loading?'\n\u6b63\u5728\u8bfb\u53d6\u4f1a\u8bdd\u8bbe\u7f6e\u2026':'')+
  (editor.refreshFailed?'\n\u5217\u8868\u66f4\u65b0\u5931\u8d25\uff0c\u4fdd\u7559\u5df2\u83b7\u53d6\u7684\u5019\u9009\u3002':'');
 collaborationVisibility('collaboration');
}
async function openCollaboration(session){
 if(!state.user?.is_admin)return;
 const dialog=$('#collaboration-dialog');
 if(dialog.open)dialog.close();
 const editor={id:session.id,cycle:++collaborationCycle,dirty:false,saving:false,
  provider:providerFor(session.provider||'claude'),loading:true};
 collaborationEditor=editor;
 $('#collaboration-session-title').textContent=session.title;
 $('#collaboration-error').hidden=true;
 $('#collaboration-enabled').checked=!!session.collaboration_enabled;
 collaborationFields('collaboration',editor.provider,session);collaborationNote(editor);
 dialog.showModal();
 // Open from the cached session immediately; revalidate ownership/settings in the background.
 try{
  const result=await api('/api/sessions/'+pathId(editor.id));
  if(!collaborationActive(editor))return;
  editor.loading=false;
  if(!editor.dirty){
   $('#collaboration-enabled').checked=!!result.session.collaboration_enabled;
   collaborationFields('collaboration',editor.provider,result.session);
  }
  collaborationNote(editor);
 }catch(error){
  if(!collaborationActive(editor))return;
  editor.loading=false;editor.failed=true;
  $('#collaboration-error').textContent=error.message;$('#collaboration-error').hidden=false;
  $('#collaboration-save').disabled=true;return;
 }
 const ok=await loadProviders({maxAge:60000});
 if(!collaborationActive(editor))return;
 editor.refreshFailed=!ok;
 if(!editor.dirty&&!editor.saving){
  const values={reviewer_effort:$('#collaboration-reviewer').value,coder_effort:$('#collaboration-coder').value};
  editor.provider=providerFor('claude');collaborationFields('collaboration',editor.provider,values);
 }
 collaborationNote(editor);
}
for(const id of ['collaboration-enabled','collaboration-reviewer','collaboration-coder']){
 $('#'+id).onchange=()=>{
  const editor=collaborationEditor;if(!collaborationActive(editor)||editor.failed)return;
  editor.dirty=true;collaborationNote(editor);
 };
}
$('#collaboration-dialog').addEventListener('close',()=>{if($('#collaboration-dialog').open)return;collaborationCycle++;collaborationEditor=null;});
$('#collaboration-form').onsubmit=async event=>{
 event.preventDefault();const editor=collaborationEditor;
 if(!collaborationActive(editor)||editor.loading||editor.failed||editor.saving)return;
 const enabled=$('#collaboration-enabled').checked;
 if(enabled&&collaborationInvalid('collaboration'))return;
 const body={collaboration_enabled:enabled,reviewer_effort:$('#collaboration-reviewer').value,
  coder_effort:$('#collaboration-coder').value};
 editor.saving=true;collaborationNote(editor);$('#collaboration-error').hidden=true;
 try{
  const result=await api('/api/sessions/'+pathId(editor.id),{method:'PATCH',body});
  if(!collaborationActive(editor))return;
  if(currentId()===editor.id){
   state.session=result.session;updateConversationMeta();rememberSession();
  }
  $('#collaboration-dialog').close();await loadSessions();
  notice('\u4f1a\u8bdd\u534f\u4f5c\u8bbe\u7f6e\u5df2\u4fdd\u5b58\u3002');
 }catch(error){
  if(collaborationActive(editor)){
   $('#collaboration-error').textContent=error.message;$('#collaboration-error').hidden=false;
  }
 }finally{editor.saving=false;if(collaborationActive(editor))collaborationNote(editor);}
};
async function toggleSessionPin(session){
 if(pinRequests.has(session.id))return;
 pinRequests.add(session.id);
 try{
  const result=await api('/api/sessions/'+pathId(session.id),
   {method:'PATCH',body:{pinned:!session.pinned_at}});
  if(currentId()===session.id)state.session.pinned_at=result.session.pinned_at;
  await loadSessions();
 }catch(error){notice(error.message);}
 finally{pinRequests.delete(session.id);}
}

const sendFlights=new Map();
function releaseSend(entry){
 if(sendFlights.get(entry.session_id)!==entry.request_id)return;
 sendFlights.delete(entry.session_id);state.sending.delete(entry.session_id);
 if(currentId()===entry.session_id&&!state.switching)setStatus(state.status);
}
function localRequestKey(sid,rid){return sid+':request:'+rid;}
function localLabel(entry){
 if(entry.phase==='failed')return '\u53d1\u9001\u5931\u8d25';
 if(entry.phase==='uncertain')return '\u63a5\u6536\u72b6\u6001\u5f85\u6838\u5bf9';
 if(entry.phase==='queued')return '\u670d\u52a1\u5668\u5df2\u63a5\u6536\uff0c\u7b49\u5f85\u6267\u884c';
 if(entry.phase==='accepted')return '\u670d\u52a1\u5668\u5df2\u63a5\u6536';
 return '\u6b63\u5728\u53d1\u9001';
}
function clearRestoredDraft(entry){
 if(!entry.restoredDraft||!localVisible(entry))return;
 if(JSON.stringify([$('#prompt').value,state.attachments.filter(a=>a.id).map(a=>a.id)])!==entry.signature)return;
 $('#prompt').value='';
 state.attachments=state.attachments.filter(a=>!entry.attachments.some(file=>file.id===a.id));
 state.attachmentDrafts.set(entry.session_id,state.attachments);resizePrompt();renderAttachments();
 try{saveDraft();}catch{}
 entry.restoredDraft=false;
}
function dropLocal(entry){
 clearRestoredDraft(entry);
 entry.confirmed=true;sendOutbox.remove(entry.session_id,entry.request_id);
 try{clearPending(entry.pendingKey,entry.signature,entry.request_id);}catch{}
 releaseSend(entry);
}
function reconcileLocalEvents(){
 const sid=currentId();if(!sid)return;
 for(const event of state.events.values()){
  if(event.type!=='user'||typeof event.data?.request_id!=='string')continue;
  const entry=sendOutbox.get(sid,event.data.request_id);if(entry)dropLocal(entry);
 }
 for(const entry of sendOutbox.entries(sid)){
  if(Number.isSafeInteger(entry.event_seq)&&entry.event_seq<=state.cursor)dropLocal(entry);
 }
}
function localItem(entry){
 return {key:localRequestKey(entry.session_id,entry.request_id),role:'user',text:entry.prompt,
  attachments:entry.attachments,time:Math.floor(entry.created_at/1000),tools:[],local:true,
  request_id:entry.request_id,local_phase:entry.phase,local_error:entry.error||'',
  local_can_retry:!entry.inflight&&!state.sending.has(entry.session_id)&&state.user?.is_admin&&state.statusVerified&&!['unknown','stopping'].includes(state.status)};
}
function localReceiptNode(entry){
 const row=document.createElement('div');row.className='send-receipt';row.dataset.phase=entry.phase;
 const label=document.createElement('span');label.textContent=localLabel(entry);row.append(label);
 if(entry.error){const detail=document.createElement('span');detail.className='send-error-detail';detail.textContent=entry.error;row.append(detail);}
 if(['failed','uncertain'].includes(entry.phase)){
  const retry=document.createElement('button');retry.type='button';retry.className='text-button';
  retry.textContent='\u91cd\u8bd5';retry.disabled=entry.inflight||state.sending.has(entry.session_id)||!state.user?.is_admin||!state.statusVerified||['unknown','stopping'].includes(state.status);
  retry.onclick=()=>retryLocal(entry);row.append(retry);
 }
 if(entry.phase==='failed'){
  const remove=document.createElement('button');remove.type='button';remove.className='text-button';
  remove.textContent='\u79fb\u9664\u8bb0\u5f55';remove.disabled=entry.inflight;
  remove.title='\u4ec5\u79fb\u9664\u6b64\u6761\u672a\u63d0\u4ea4\u6d88\u606f\uff0c\u4fdd\u7559\u8f93\u5165\u5185\u5bb9';
  remove.onclick=()=>{
   sendOutbox.remove(entry.session_id,entry.request_id);
   try{clearPending(entry.pendingKey,entry.signature,entry.request_id);}catch{}
   if(localVisible(entry))renderEvents(false,true);
  };row.append(remove);
 }
 return row;
}
function appendImmediate(entry){
 const article=document.createElement('article');article.className='message user local-message';
 article.dataset.renderKey=localRequestKey(entry.session_id,entry.request_id);
 const header=document.createElement('div');header.className='message-head';
 const who=document.createElement('strong');who.textContent='\u4f60';header.append(who);
 const body=document.createElement('div');body.className='message-body';
 body.textContent=entry.prompt||entry.attachments.map(a=>a.name).join(', ');
 const receipt=document.createElement('div');receipt.className='send-receipt';receipt.textContent='\u6b63\u5728\u53d1\u9001';
 article.append(header,body,receipt);$('#message-list').append(article);
}
function rememberLocal(entry){
 if(sendOutbox.get(entry.session_id,entry.request_id)!==entry)return;
 try{sendOutbox.save(entry);}catch{if(currentId()===entry.session_id)notice('\u672c\u5730\u72b6\u6001\u4fdd\u5b58\u5931\u8d25\uff0c\u8bf7\u52ff\u5173\u95ed\u6b64\u9875');}
}
function localVisible(entry){return currentId()===entry.session_id&&!state.switching;}
async function probeLocal(id,force=false){
 const epoch=state.epoch;
 const pending=sendOutbox.entries(id).filter(e=>!e.inflight&&!e.checking&&!state.queue.some(q=>q.request_id===e.request_id)&&(force||Date.now()-(e.checked_at||0)>5000)).slice(0,5);
 await Promise.allSettled(pending.map(async entry=>{
  entry.checking=true;entry.checked_at=Date.now();
  try{
   const receipt=await api('/api/sessions/'+pathId(id)+'/requests/'+pathId(entry.request_id),{timeoutMs:5000});
   if(sendOutbox.get(id,entry.request_id)!==entry)return;
   if(receipt.request_id!==entry.request_id||typeof receipt.accepted!=='boolean')throw Error('Invalid receipt');
   if(receipt.accepted){
    clearRestoredDraft(entry);
    if(entry.phase==='queued'&&!receipt.queued&&!receipt.delivery_state&&!receipt.event_seq){dropLocal(entry);return;}
    entry.phase=receipt.queued?'queued':'accepted';entry.error='';entry.event_seq=receipt.event_seq;
    if(localVisible(entry)&&Number.isSafeInteger(entry.event_seq)&&entry.event_seq<=state.cursor){dropLocal(entry);}
    else rememberLocal(entry);
   }else if(entry.phase!=='failed'){
    entry.phase='uncertain';entry.error='\u6682\u672a\u67e5\u5230\u63a5\u6536\u8bb0\u5f55\uff0c\u53ef\u4f7f\u7528\u539f\u7f16\u53f7\u91cd\u8bd5';rememberLocal(entry);
   }
  }catch{}
  finally{entry.checking=false;}
 }));
 if(epoch===state.epoch&&currentId()===id&&pending.length)renderEvents(false,true);
}
function restoreUnsentDraft(entry){
 if(!localVisible(entry)||$('#prompt').value||state.attachments.length)return;
 entry.restoredDraft=true;
 $('#prompt').value=entry.prompt;
 if(!state.attachments.length&&entry.attachments.length){
  state.attachments=entry.attachments.map(a=>({...a,progress:100}));
  state.attachmentDrafts.set(entry.session_id,state.attachments);renderAttachments();
 }
 try{saveDraft();}catch{}
 resizePrompt();
}
async function submitLocal(entry){
 let submitted=false;
 try{
  // Persist the complete immutable body before any network write; a failure cannot send a task.
  sendOutbox.save(entry);
  submitted=true;
  const result=await sendTurn(entry.session_id,{prompt:entry.prompt,
   attachments:entry.attachments.map(a=>a.id),request_id:entry.request_id});
  if(sendOutbox.get(entry.session_id,entry.request_id)===entry){
   entry.phase=result.queued?'queued':'accepted';entry.error='';rememberLocal(entry);
  }
  clearRestoredDraft(entry);
  try{clearPending(entry.pendingKey,entry.signature,entry.request_id);}catch{}
  releaseSend(entry);
  if(localVisible(entry)){
   if(typeof result.session_title==='string'){state.session.title=result.session_title;$('#conversation-title').textContent=result.session_title;}
   renderEvents(false);refreshStatus(state.epoch);
   if(state.pollRunningEpoch!==state.epoch)startPolling(state.epoch,true);
  }
  loadSessions();
 }catch(error){
  if(sendOutbox.get(entry.session_id,entry.request_id)===entry&&!entry.confirmed){
   const definitive=!submitted||[400,403,404,413,422].includes(error.status);
   entry.phase=definitive?'failed':'uncertain';
   entry.error=!submitted?'\u6d4f\u89c8\u5668\u672a\u80fd\u4fdd\u5b58\u5185\u5bb9\uff0c\u672c\u6b21\u672a\u63d0\u4ea4':error.message;
   restoreUnsentDraft(entry);
   if(submitted)rememberLocal(entry);
   if(localVisible(entry))renderEvents(false);
  }
 }finally{
  entry.inflight=false;releaseSend(entry);
  if(localVisible(entry)){renderEvents(false,true);probeLocal(entry.session_id);}
 }
}
function retryLocal(entry){
 if(entry.inflight||state.sending.has(entry.session_id)||!state.user?.is_admin||!localVisible(entry)||!state.statusVerified||['unknown','stopping'].includes(state.status))return;
 if(JSON.stringify([$('#prompt').value,state.attachments.filter(a=>a.id).map(a=>a.id)])===entry.signature){
  $('#prompt').value='';
  state.attachments=state.attachments.filter(a=>!entry.attachments.some(file=>file.id===a.id));
  state.attachmentDrafts.set(entry.session_id,state.attachments);resizePrompt();renderAttachments();
  try{saveDraft();}catch{}
 }
 entry.inflight=true;entry.phase='sending';entry.error='';state.sending.add(entry.session_id);
 sendFlights.set(entry.session_id,entry.request_id);setStatus(state.status);renderEvents(false,true);
 setTimeout(()=>submitLocal(entry),0);
}


function syncTaskline(){
 $('#composer-taskline').hidden=deliveryStatus.hidden&&$('#stop').hidden;
}
function compactDelivery(data,label,details){
 if(!details.length)return '';
 if(['running','starting','stopping'].includes(data.status)){
  const since=data.activity?.started_at;
  const linked=!activityConnectionProblem&&performance.now()-lastActivityReceived<10000;
  const elapsed=linked?activityAge(since):(Date.parse(data.activity?.checked_at)-Date.parse(since))/1000;
  const a=data.activity||{};
  const age=stamp=>stamp?(linked?activityAge(stamp):Math.max(0,(Date.parse(a.checked_at)-Date.parse(stamp))/1000)):null;
  const text=age(a.last_output_at),tool=age(a.last_progress_at);
  return label+(since?' \u00b7 \u5df2\u8fd0\u884c '+activityDuration(elapsed):'')+
   ' \u00b7 \u6587\u672c '+(text===null?'\u5c1a\u65e0\u8f93\u51fa':activityDuration(text)+'\u524d')+
   ' \u00b7 \u5de5\u5177 '+(tool===null?'\u5c1a\u65e0\u6d3b\u52a8':activityDuration(tool)+'\u524d');
 }
 return details.join(' \u00b7 ');
}

let newEditor=null,newCycle=0,newPreparedVersion=-1;
let defaultsTimer=null,defaultsCycle=0;
const projectDefaultsCache=new Map();
const newConfigIds=new Set(['provider','model','effort','permission','new-collaboration-enabled',
 'new-collaboration-reviewer','new-collaboration-coder']);
function newActive(editor){return !!editor&&newEditor===editor&&editor.cycle===newCycle&&$('#new-dialog').open;}
function fillNewProviderSelect(saved){
 const select=$('#provider');select.replaceChildren();
 for(const p of state.providers){
  const option=new Option(p.name+(p.configured?'':'\uff08\u672a\u914d\u7f6e\uff09'),p.id);
  option.disabled=!p.configured;select.add(option);
 }
 if(saved){appendMissing(select,saved,saved);select.value=saved;}
 else{const first=[...select.options].find(o=>!o.disabled);select.value=first?.value||'';}
}
function applyNewFields(editor){
 const touched=editor?.touched||new Set();
 const model=touched.has('model')?$('#model').value:editor?.defaults?.model||null;
 const effort=touched.has('effort')?$('#effort').value:editor?.defaults?.reasoning_effort||null;
 const permission=touched.has('permission')?$('#permission').value:'dontAsk';
 const reviewer=touched.has('new-collaboration-reviewer')?$('#new-collaboration-reviewer').value:editor?.defaults?.reviewer_effort||'high';
 const coder=touched.has('new-collaboration-coder')?$('#new-collaboration-coder').value:editor?.defaults?.coder_effort||'high';
 if(!touched.has('new-collaboration-enabled'))$('#new-collaboration-enabled').checked=!!editor?.defaults?.collaboration_enabled;
 const saved=$('#provider').value;fillNewProviderSelect(saved);
 const provider=providerFor($('#provider').value);if(editor)editor.provider=provider;
 populateModels(provider,$('#model'),$('#effort'),model,effort);
 const names={plan:'\u53ea\u89c4\u5212',dontAsk:'\u81ea\u52a8\u62d2\u7edd\u5ba1\u6279\u8bf7\u6c42',
  acceptEdits:'\u81ea\u52a8\u63a5\u53d7\u6587\u4ef6\u7f16\u8f91',bypassPermissions:'\u8df3\u8fc7\u6743\u9650\u68c0\u67e5\uff08\u9ad8\u6743\u9650\uff09'};
 $('#permission').replaceChildren();
 for(const mode of provider?.permission_modes||[])$('#permission').add(new Option(names[mode]||mode,mode));
 if(permission&&[...$('#permission').options].some(o=>o.value===permission))$('#permission').value=permission;
 else if(touched.has('permission'))appendMissing($('#permission'),permission,permission);
 collaborationFields('new-collaboration',provider,{reviewer_effort:reviewer,coder_effort:coder});
 collaborationVisibility('new-collaboration');updatePermissionNote();
 updateNewNote(editor);updateNewGate(editor);
}
function updateNewGate(editor){
 const provider=editor?.provider||providerFor($('#provider').value);
 $('#create').disabled=!!editor?.saving||!state.user?.is_admin||!provider?.configured||missingSelection($('#provider'))||!$('#permission').value||
  missingSelection($('#permission'))||missingSelection($('#model'))||missingSelection($('#effort'))||
  $('#new-collaboration-enabled').checked&&collaborationInvalid('new-collaboration');
}
function updateNewNote(editor){
 const provider=editor?.provider||providerFor($('#provider').value);
 $('#new-model-note').textContent=modelListNote(provider)+(editor?.refreshing?
  '\n\u6b63\u5728\u540e\u53f0\u66f4\u65b0\u5217\u8868\uff0c\u53ef\u4ee5\u5148\u586b\u5199\u5de5\u4f5c\u76ee\u5f55\u3002':'')+
  (editor?.refreshFailed?'\n\u5217\u8868\u66f4\u65b0\u5931\u8d25\uff0c\u4fdd\u7559\u5df2\u83b7\u53d6\u7684\u9009\u9879\u3002':'');
}
function prepareNewDialog(){
 if($('#new-dialog').open)return;
 $('#new-error').hidden=true;$('#cwd').value='';updateDirectoryPreview();
 $('#new-collaboration-enabled').checked=false;
 applyNewFields(null);newPreparedVersion=providerVersion;
}
async function refreshNewDialog(editor){
 if(!newActive(editor))return;
 editor.refreshing=true;updateNewNote(editor);
 const ok=await loadProviders({maxAge:60000});if(!newActive(editor))return;
 editor.refreshing=false;editor.refreshFailed=!ok;
 if(!editor.saving&&(!editor.touched.size||!editor.provider?.configured||!$('#permission').value))applyNewFields(editor);
 else{updateNewNote(editor);updateNewGate(editor);}
}
function openNew(){
 const dialog=$('#new-dialog');if(dialog.open)return;
 if(newPreparedVersion!==providerVersion)prepareNewDialog();
 const editor={cycle:++newCycle,touched:new Set(),saving:false,provider:providerFor($('#provider').value)};
 newEditor=editor;dialog.showModal();
 setTimeout(()=>refreshNewDialog(editor),0);
}
$('#new-session').onclick=openNew;$('#empty-new').onclick=openNew;
for(const type of ['input','change'])$('#new-form').addEventListener(type,event=>{
 if(newActive(newEditor)&&newConfigIds.has(event.target.id))newEditor.touched.add(event.target.id);
},true);
$('#model').onchange=()=>{
 populateEfforts(newActive(newEditor)?newEditor.provider:providerFor($('#provider').value),$('#model'),$('#effort'),null);
 updateNewGate(newEditor);
};
$('#provider').onchange=()=>{
 if(newActive(newEditor))for(const id of ['model','effort','permission'])newEditor.touched.delete(id);
 applyNewFields(newEditor);
};
for(const id of ['effort','permission','new-collaboration-reviewer','new-collaboration-coder']){
 $('#'+id).addEventListener('change',()=>updateNewGate(newEditor));
}
$('#new-collaboration-enabled').onchange=()=>{collaborationVisibility('new-collaboration');updateNewGate(newEditor);};
$('#new-dialog').addEventListener('close',()=>{
 if($('#new-dialog').open)return;
 newCycle++;newEditor=null;
 if($('#directory-dialog').open)$('#directory-dialog').close();
 prepareNewDialog();
});
$('#new-form').onsubmit=async event=>{
 event.preventDefault();const editor=newEditor;if(!newActive(editor)||editor.saving||$('#create').disabled)return;
 const error=$('#new-error'),mode=$('#permission').value;error.hidden=true;
 if(mode==='bypassPermissions'&&!confirm('\u9ad8\u6743\u9650\u5c06\u8df3\u8fc7\u6743\u9650\u68c0\u67e5\uff0c\u53ef\u80fd\u8bbf\u95ee\u5de5\u4f5c\u76ee\u5f55\u4ee5\u5916\u7684\u6587\u4ef6\u3002\u786e\u8ba4\u7ee7\u7eed\uff1f'))return;
 const body={title:'\u65b0\u4f1a\u8bdd',cwd:$('#cwd').value.trim(),provider:$('#provider').value,
  model:$('#model').value||null,reasoning_effort:$('#effort').value||null,permission_mode:mode,
  collaboration_enabled:$('#new-collaboration-enabled').checked,
  reviewer_effort:$('#new-collaboration-reviewer').value||'high',coder_effort:$('#new-collaboration-coder').value||'high'};
 editor.saving=true;updateNewGate(editor);
 try{
  const result=await api('/api/sessions',{method:'POST',body});
  archivedView=false;$('#history-view').value='active';loadSessions();
  if(newActive(editor)){$('#new-dialog').close();selectSession(result.session.id);}
 }catch(e){if(newActive(editor)){error.textContent=e.message;error.hidden=false;}}
 finally{editor.saving=false;if(newActive(editor))updateNewGate(editor);}
};
let directoryCycle=0,directoryRequest=0,directoryOwner=null,directorySaving=false,workingRoots=[];
function directoryActive(cycle){return cycle===directoryCycle&&$('#directory-dialog').open;}
function directoryButtons(){
 $('#parent-dir').disabled=directorySaving||!state.directory;
 $('#new-folder').disabled=directorySaving||!state.directory||!state.user?.is_admin;
 $('#choose-dir').disabled=directorySaving||!state.directory;
 $('#folder-create').disabled=directorySaving;
 $('#folder-name').disabled=directorySaving;
}
async function browse(path,autoRoot=false){
 if(directorySaving)return;
 const cycle=directoryCycle,request=++directoryRequest;
 state.directory=null;directoryButtons();$('#folder-form').hidden=true;
 $('#directory-error').hidden=true;$('#directory-path').textContent='\u6b63\u5728\u8bfb\u53d6\u76ee\u5f55\u2026';
 $('#directory-list').replaceChildren();
 try{
  const result=await api('/api/directories'+(path?'?path='+encodeURIComponent(path):''));
  if(!directoryActive(cycle)||request!==directoryRequest)return;
  workingRoots=Array.isArray(result.roots)?result.roots:workingRoots;
  if(autoRoot&&!result.path&&result.directories?.length===1)return browse(result.directories[0].path);
  state.directory=result.path;$('#directory-path').textContent=result.path||'\u6388\u6743\u76ee\u5f55';
  const list=$('#directory-list');
  for(const entry of result.directories||[]){
   const button=document.createElement('button');button.className='directory-entry';button.type='button';
   button.textContent=entry.name;button.onclick=()=>browse(entry.path);list.append(button);
  }
  if(!result.directories?.length){const text=document.createElement('p');text.className='help';text.textContent='\u6682\u65e0\u5b50\u6587\u4ef6\u5939\uff0c\u53ef\u70b9\u51fb\u201c\u65b0\u5efa\u6587\u4ef6\u5939\u201d';list.append(text);}
  directoryButtons();updateDirectoryPreview();
 }catch(e){
  if(directoryActive(cycle)&&request===directoryRequest){$('#directory-error').textContent=e.message;$('#directory-error').hidden=false;directoryButtons();}
 }
}
function updateDirectoryPreview(){
 scheduleProjectDefaults();
 const value=$('#cwd').value.trim();
 $('#cwd-preview').textContent=value?(value.startsWith('/')?value:(workingRoots[0]?workingRoots[0]+'/'+value:'\u5c06\u5728\u6388\u6743\u6839\u76ee\u5f55\u521b\u5efa\uff1a'+value)):
  '\u8f93\u5165\u9879\u76ee\u540d\u53ef\u81ea\u52a8\u521b\u5efa\uff1b\u5b8c\u6574\u8def\u5f84\u9700\u5148\u5728\u6d4f\u89c8\u7a97\u53e3\u521b\u5efa\u6216\u9009\u62e9\u3002';
}
$('#cwd').addEventListener('input',updateDirectoryPreview);
$('#browse').onclick=()=>{
 directoryCycle++;directoryOwner=newEditor;directorySaving=false;
 state.directory=null;$('#folder-form').hidden=true;directoryButtons();
 $('#directory-dialog').showModal();browse(null,true);
};
$('#directory-dialog').addEventListener('close',()=>{
 if($('#directory-dialog').open)return;
 directoryCycle++;directoryRequest++;directorySaving=false;state.directory=null;
});
$('#parent-dir').onclick=()=>{
 if(!state.directory||directorySaving)return;
 if(workingRoots.includes(state.directory))return browse(null);
 const path=state.directory.replace(/\/+$/,'');browse(path.slice(0,path.lastIndexOf('/'))||null);
};
function chooseDirectory(path){
 if(!newActive(directoryOwner))return;
 $('#cwd').value=path;updateDirectoryPreview();$('#directory-dialog').close();$('#cwd').focus();
}
$('#choose-dir').onclick=()=>{if(state.directory&&!directorySaving)chooseDirectory(state.directory);};
$('#new-folder').onclick=()=>{
 if(!state.directory||directorySaving)return;
 $('#folder-name').value='';$('#folder-error').hidden=true;$('#folder-form').hidden=false;
 $('#folder-parent').textContent='\u521b\u5efa\u4f4d\u7f6e\uff1a'+state.directory;$('#folder-name').focus();
};
$('#folder-cancel').onclick=()=>{if(!directorySaving)$('#folder-form').hidden=true;};
$('#folder-form').onsubmit=async event=>{
 event.preventDefault();if(!state.directory||directorySaving)return;
 const cycle=directoryCycle,parent=state.directory,name=$('#folder-name').value.trim();
 const error=$('#folder-error');error.hidden=true;
 if(!name){error.textContent='\u8bf7\u8f93\u5165\u6587\u4ef6\u5939\u540d\u79f0';error.hidden=false;return;}
 directorySaving=true;directoryButtons();
 try{
  const result=await api('/api/directories',{method:'POST',body:{parent,name}});
  if(directoryActive(cycle)&&newActive(directoryOwner))chooseDirectory(result.path);
 }catch(e){
  if(directoryActive(cycle)){
   error.textContent=e.status?e.message:'\u521b\u5efa\u7ed3\u679c\u672a\u786e\u8ba4\uff0c\u8bf7\u5173\u95ed\u6b64\u8f93\u5165\u533a\u5e76\u5237\u65b0\u76ee\u5f55\u6838\u5bf9\uff0c\u907f\u514d\u91cd\u590d\u521b\u5efa';error.hidden=false;
  }
 }finally{if(directoryActive(cycle)){directorySaving=false;directoryButtons();}}
};
prepareNewDialog();

const sessionRuntime=new Map();let runtimeBusy=false;
function runtimeTitle(data,stale){
 const a=data.activity||{},phase=['requires_action','answering','approval_unknown'].includes(data.status)?data.status:a.phase||data.status;
 const parts=[(activityLabels[phase]||'\u72b6\u6001\u672a\u77e5')+(stale?'\uff08\u4e0a\u6b21\u72b6\u6001\uff09':'')];
 if(a.last_output_at)parts.push('\u6700\u8fd1\u6587\u672c\uff1a'+activityClock(a.last_output_at));
 if(a.last_progress_at)parts.push('\u6700\u8fd1\u5de5\u5177\u6d3b\u52a8\uff1a'+activityClock(a.last_progress_at));
 if(a.checked_at)parts.push('\u72b6\u6001\u6838\u5bf9\uff1a'+activityClock(a.checked_at));
 return parts.join('\n');
}
function paintSessionRuntime(){
 for(const button of document.querySelectorAll('#session-list .session')){
  const marker=button.querySelector('.session-runtime'),entry=sessionRuntime.get(button.dataset.sessionId);
  if(!marker)continue;
  const status=entry?.data.display_status||entry?.data.status,visible=['running','stopping','unknown'].includes(entry?.data.status)||['requires_action','answering','approval_unknown'].includes(status);
  marker.hidden=!visible;if(!visible)continue;
  const stale=performance.now()-entry.received>8000;
  const running=entry?.data.status==='running'&&!['requires_action','answering','approval_unknown'].includes(status)&&!stale;
  marker.className='session-runtime'+(running?' spinning':'')+(stale?' stale':'')+(['unknown','approval_unknown'].includes(status)?' uncertain':'')+(status==='requires_action'?' needs-answer':'');
  marker.title=runtimeTitle(entry.data,stale);marker.setAttribute('aria-label',marker.title);
 }
}
async function refreshSessionRuntime(){
 if(runtimeBusy||document.hidden||!state.user)return;
 runtimeBusy=true;const account=state.user.username;
 try{
  const data=await api('/api/session-statuses'+(currentId()?'?focus='+pathId(currentId()):''),{timeoutMs:5000});
  if(state.user?.username!==account)return;
  if(!acceptGeneration(data.generation))return;
  attentionSnapshot=data.attention||{};paintAttention();markVisibleRead();
  for(const [sid,value] of Object.entries(data.sessions||{}))acceptSessionSnapshot(sid,value);
  for(const [sid,entry] of sessionRuntime)if(!Object.hasOwn(data.sessions||{},sid)&&entry.data.generation===data.generation&&entry.data.revision<=data.revision)sessionRuntime.delete(sid);
 }catch{}finally{runtimeBusy=false;paintSessionRuntime();}
}
setInterval(()=>{if(!document.hidden){paintSessionRuntime();refreshSessionRuntime();}},2000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshSessionRuntime();});
const approvalHost=document.createElement('div');approvalHost.id='approval-panel';approvalHost.hidden=true;
$('#composer-taskline').before(approvalHost);let approvalKey='',approvalFlight=false;
function renderApprovals(data){
 const pending=data?.approvals?.[0],sid=currentId();
 const key=pending?sid+'/'+pending.request_id+'/'+pending.state:'';
 if(key===approvalKey)return;
 approvalKey=key;approvalFlight=false;approvalHost.replaceChildren();approvalHost.hidden=!pending;
 if(!pending)return;
 const form=document.createElement('form'),title=document.createElement('strong');
 title.textContent=pending.state==='unknown'?'\u5ba1\u6279\u56de\u4f20\u7ed3\u679c\u5f85\u6838\u9a8c':pending.state==='answering'?'\u6b63\u5728\u56de\u4f20\u4f60\u7684\u786e\u8ba4':pending.tool_name==='AskUserQuestion'?'Claude \u9700\u8981\u4f60\u7684\u56de\u7b54':'Claude \u8bf7\u6c42\u6267\u884c '+pending.tool_name;
 form.append(title);const fields=[];
 if(pending.state!=='pending'){
  const note=document.createElement('p');note.setAttribute('role','status');
  note.textContent=pending.state==='unknown'?'\u5ba1\u6279\u56de\u4f20\u7ed3\u679c\u5f85\u6838\u9a8c\uff0c\u4e0d\u4f1a\u81ea\u52a8\u91cd\u53d1\u3002\u53ef\u67e5\u770b\u6267\u884c\u72b6\u6001\u6216\u505c\u6b62\u5f53\u524d\u4efb\u52a1\u3002':'\u6b63\u5728\u56de\u4f20\u5ba1\u6279\uff0c\u8bf7\u52ff\u91cd\u590d\u63d0\u4ea4';
  form.append(note);approvalHost.append(form);return;
 }

 if(pending.tool_name==='AskUserQuestion'){
  for(const [index,q] of (pending.input.questions||[]).entries()){
   const group=document.createElement('fieldset'),legend=document.createElement('legend');legend.textContent=q.question;group.append(legend);
   for(const option of q.options||[]){
    const label=document.createElement('label'),input=document.createElement('input'),text=document.createElement('span');
    input.type=q.multiSelect?'checkbox':'radio';input.name='official-q-'+index;input.value=option.label;
    text.textContent=option.label+(option.description?'\uff1a'+option.description:'');label.append(input,text);group.append(label);
   }
   const custom=document.createElement('label'),caption=document.createElement('span'),answer=document.createElement('input');
   caption.textContent='\u8865\u5145\u6216\u81ea\u5b9a\u4e49\u56de\u7b54';answer.type='text';answer.maxLength=4000;
   custom.append(caption,answer);group.append(custom);form.append(group);fields.push({q,group,answer});
  }
 }else{
  const detail=document.createElement('pre');detail.textContent=JSON.stringify(pending.input,null,2);form.append(detail);
 }
 const actions=document.createElement('div'),allow=document.createElement('button'),deny=document.createElement('button'),feedback=document.createElement('p');
 actions.className='approval-actions';allow.type='submit';allow.textContent=pending.tool_name==='AskUserQuestion'?'\u63d0\u4ea4\u56de\u7b54':'\u5141\u8bb8\u672c\u6b21';deny.type='button';deny.textContent='\u62d2\u7edd';feedback.setAttribute('role','status');
 actions.append(allow,deny);form.append(actions,feedback);approvalHost.append(form);
 async function answer(accepted){
  if(approvalFlight||key!==approvalKey)return;
  const answers={};
  if(accepted)for(const {q,group,answer} of fields){
   const values=[...group.querySelectorAll('input:checked')].map(e=>e.value);
   if(answer.value.trim())values.push(answer.value.trim());
   if(!values.length){feedback.textContent='\u8bf7\u56de\u7b54\u6bcf\u4e00\u4e2a\u95ee\u9898';return;}
   answers[q.question]=values.join(', ');
  }
  approvalFlight=true;allow.disabled=deny.disabled=true;feedback.textContent='\u6b63\u5728\u9001\u56de Claude';
  try{
   await api('/api/sessions/'+pathId(sid)+'/approvals/'+encodeURIComponent(pending.request_id),{method:'POST',body:{allow:accepted,...(fields.length?{answers}:{})}});
   if(key===approvalKey){feedback.textContent='\u5df2\u63d0\u4ea4\uff0c\u7b49\u5f85 Claude \u7ee7\u7eed';refreshStatus(state.epoch);}
  }catch(e){if(key===approvalKey){feedback.textContent=e.message;if(e.status&&e.status!==503){approvalFlight=false;allow.disabled=deny.disabled=false;}}}
 }
 form.onsubmit=e=>{e.preventDefault();answer(true);};deny.onclick=()=>answer(false);
}

'use strict';
let diagnosticCycle=0,diagnosticData=null;
const diagnosticDialog=$('#diagnostics-dialog');
async function loadDiagnostics(){
 const cycle=++diagnosticCycle,sid=currentId();$('#diagnostics-notice').textContent='\u6b63\u5728\u8bfb\u53d6\u8fd0\u884c\u72b6\u6001';$('#diagnostics-download').disabled=true;
 try{
  const data=await api('/api/diagnostics'+(sid?'?session_id='+pathId(sid):''),{timeoutMs:7000});
  if(!diagnosticDialog.open||cycle!==diagnosticCycle)return;
  diagnosticData=data;const s=data.session,receiptNames={pending:'\u7b49\u5f85',writing:'\u5199\u5165\u4e2d',written:'\u5df2\u5199\u5165',delivered:'\u5df2\u9001\u8fbe',completed:'\u5df2\u5b8c\u6210',failed:'\u5931\u8d25',interrupted:'\u5df2\u4e2d\u65ad',unknown:'\u5f85\u6838\u9a8c',queued:'\u6392\u961f',received:'\u5df2\u63a5\u6536',accepted:'\u5df2\u63a5\u53d7',rejected:'\u5df2\u62d2\u7edd'},values=$('#diagnostics-values');values.replaceChildren();
  const yes=v=>v===true?'\u662f':v===false?'\u5426':'\u5f85\u6838\u9a8c';
  const lines=[['\u6267\u884c\u5668\u5728\u7ebf',yes(data.executor_available)],['\u6b63\u5728\u6267\u884c',String(data.executor?.active_sessions??'\u672a\u77e5')],['\u5f85\u6838\u9a8c\u4f1a\u8bdd',String(data.executor?.unknown_sessions??'\u672a\u77e5')]];
  if(s)lines.push(['\u5f53\u524d\u9636\u6bb5',activityLabels[s.phase]||s.phase],['Claude \u8fdb\u7a0b\u5b58\u6d3b',yes(s.process_alive)],['\u6700\u8fd1\u6587\u5b57',activityClock(s.last_text_at)],['\u6700\u8fd1\u5de5\u5177',activityClock(s.last_tool_at)],['\u6700\u8fd1\u72b6\u6001\u6838\u5bf9',activityClock(s.observed_at)],['\u7b49\u5f85\u786e\u8ba4',String(s.pending_approvals)],['\u5f85\u9001\u8fbe\u6d88\u606f',String(s.pending_messages)],['\u6700\u8fd1\u6d88\u606f\u56de\u6267',Object.entries(s.receipts||{}).map(([k,v])=>(receiptNames[k]||k)+' '+v).join(' / ')||'\u65e0'],['\u6700\u8fd1\u9519\u8bef',s.last_error||'\u65e0']);
  if(s?.resources){const r=s.resources,mb=n=>(n/1024/1024).toFixed(1)+' MiB';lines.push(['\u4f1a\u8bdd\u8d44\u6e90',r.available?(r.finished?'\u4e0a\u6b21\u6267\u884c\u91c7\u6837':'\u8fdb\u7a0b\u8d44\u6e90\u91c7\u6837\uff0c\u542b\u5b50\u8fdb\u7a0b'):'\u6682\u65e0\u53ef\u7528\u91c7\u6837']);if(r.available)lines.push(['CPU',r.cpu_percent==null?'\u7b49\u5f85\u4e0b\u6b21\u91c7\u6837':r.cpu_percent+'%\uff08\u5355\u6838=100%\uff09'],['\u5185\u5b58',mb(r.memory_bytes)+(r.memory_kind==='rss_sum'?'\uff08RSS\u5408\u8ba1\uff0c\u5171\u4eab\u9875\u53ef\u80fd\u91cd\u590d\uff09':'' )],['\u89c2\u6d4b\u5cf0\u503c',mb(r.observed_peak_bytes)+'\uff08\u6bcf5\u79d2\u91c7\u6837\uff09'],['\u8d44\u6e90\u91c7\u6837',new Date(r.sampled_at*1000).toLocaleTimeString('zh-CN')]);} for(const [label,value] of lines){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=value;values.append(dt,dd);}
  $('#diagnostics-notice').textContent=(data.executor_available?'\u5df2\u6838\u5bf9\u670d\u52a1\u72b6\u6001':'\u6267\u884c\u5668\u6682\u65f6\u65e0\u6cd5\u8fde\u63a5')+' \u00b7 '+activityClock(data.created_at);$('#diagnostics-download').disabled=false;
 }catch(e){if(cycle===diagnosticCycle&&diagnosticDialog.open)$('#diagnostics-notice').textContent=e.message;}
}
$('#diagnostics-open').onclick=()=>{diagnosticDialog.showModal();$('#diagnostics-values').replaceChildren();diagnosticData=null;loadDiagnostics();};
$('#diagnostics-refresh').onclick=loadDiagnostics;
diagnosticDialog.addEventListener('close',()=>{diagnosticCycle++;diagnosticData=null;});
$('#diagnostics-download').onclick=()=>{
 if(!diagnosticData)return;const url=URL.createObjectURL(new Blob([JSON.stringify(diagnosticData,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='workbench-diagnostics.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
};

'use strict';

const projectPreferenceConfirmed=new Map();
const projectPreferences=new Map(),projectPreferenceFlights=new Map(),projectPreferenceEdits=new Map();
let preferencesLoad=null,preferencesLoadedAt=0;
function projectDisplayName(path){return projectPreferences.get(path)?.name||path.split('/').filter(Boolean).at(-1)||path||'\u9879\u76ee';}
async function loadProjectPreferences(){
 if(preferencesLoad)return preferencesLoad;
 if(Date.now()-preferencesLoadedAt<15000)return;
 const account=state.user?.username;if(!account)return;
 preferencesLoad=(async()=>{try{const data=await api('/api/project-preferences');if(state.user?.username!==account)return;for(const [path,value] of Object.entries(data.projects||{})){if(!projectPreferenceFlights.has(path)&&value.revision>=(projectPreferences.get(path)?.revision||0)){projectPreferences.set(path,value);projectPreferenceConfirmed.set(path,value);}}preferencesLoadedAt=Date.now();if($('#session-list .session-row'))groupSessionRows($('#session-list'),state.sessions);}catch(e){notice('\u9879\u76ee\u663e\u793a\u8bbe\u7f6e\u8bfb\u53d6\u5931\u8d25\uff1a'+e.message);}finally{preferencesLoad=null;}})();return preferencesLoad;
}
function saveProjectPreference(session,patch){
 const path=session.cwd,before={name:'',collapsed:false,revision:0,...projectPreferences.get(path)},serial=(projectPreferenceEdits.get(path)||0)+1;
 if(!projectPreferenceConfirmed.has(path))projectPreferenceConfirmed.set(path,before);
 projectPreferenceEdits.set(path,serial);projectPreferences.set(path,{...before,...patch});
 const previous=projectPreferenceFlights.get(path)||Promise.resolve();
 const flight=previous.catch(()=>{}).then(async()=>{try{const data=await api('/api/sessions/'+pathId(session.id)+'/project-preferences',{method:'PATCH',body:patch});projectPreferenceConfirmed.set(path,data.project);if(projectPreferenceEdits.get(path)===serial)projectPreferences.set(path,data.project);else projectPreferences.get(path).revision=Math.max(projectPreferences.get(path).revision||0,data.project.revision);}
 catch(e){if(projectPreferenceEdits.get(path)===serial){projectPreferences.set(path,projectPreferenceConfirmed.get(path)||before);groupSessionRows($('#session-list'),state.sessions);}notice('\u9879\u76ee\u663e\u793a\u8bbe\u7f6e\u4fdd\u5b58\u5931\u8d25\uff1a'+e.message);throw e;}
 finally{if(projectPreferenceFlights.get(path)===flight)projectPreferenceFlights.delete(path);}});
 projectPreferenceFlights.set(path,flight);return flight;
}
function groupSessionRows(list,sessions){
 const groups=new Map(),byId=new Map(sessions.map(s=>[s.id,s]));
 for(const row of [...list.querySelectorAll('.session-row')]){
  const session=byId.get(row.querySelector('.session')?.dataset.sessionId);if(!session)continue;
  const path=session.cwd||'',label=projectDisplayName(path);let group=groups.get(path);
  if(!group){
   group=document.createElement('details');group.className='project-group';group.dataset.projectPath=path;group.open=!projectPreferences.get(path)?.collapsed;
   const summary=document.createElement('summary'),chevron=document.createElement('span'),name=document.createElement('span');
   chevron.className='project-chevron';chevron.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 6 6-6 6"/></svg>';name.className='project-name';name.textContent=label;name.title=label+'\n'+path;summary.append(chevron,name);
   const rename=document.createElement('button');rename.type='button';rename.className='project-rename-button';rename.title='\u4fee\u6539\u9879\u76ee\u663e\u793a\u540d\u79f0';rename.setAttribute('aria-label',label+' \u4fee\u6539\u9879\u76ee\u540d\u79f0');rename.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 5 4 4M4 20l4-.8L20 7l-4-4L4.8 16 4 20z"/></svg>';rename.onclick=e=>{e.preventDefault();e.stopPropagation();openProjectRename(session);};summary.append(rename);
   if(state.user?.is_admin){const defaults=document.createElement('button');defaults.type='button';defaults.className='project-defaults-button';defaults.textContent='\u9ed8\u8ba4\u914d\u7f6e';defaults.setAttribute('aria-label',label+' \u9ed8\u8ba4\u914d\u7f6e');defaults.onclick=e=>{e.preventDefault();e.stopPropagation();openProjectDefaults(session);};summary.append(defaults);}
   group.append(summary);groups.set(path,group);
   group.addEventListener('toggle',()=>{if(!group.isConnected||group.open===!projectPreferences.get(path)?.collapsed)return;saveProjectPreference(session,{collapsed:!group.open}).catch(()=>{});});
  }
  group.append(row);
 }
 list.replaceChildren(...groups.values());paintAttention();
}
const projectRenameDialog=document.createElement('dialog');projectRenameDialog.id='project-rename-dialog';projectRenameDialog.setAttribute('aria-labelledby','project-rename-title');
projectRenameDialog.innerHTML='<form id="project-rename-form"><div class="dialog-heading"><h2 id="project-rename-title">&#39033;&#30446;&#26174;&#31034;&#21517;&#31216;</h2><button type="button" class="icon-button" id="project-rename-close" aria-label="&#20851;&#38381;">&#215;</button></div><p id="project-rename-path" class="help break"></p><label for="project-rename-input">&#26174;&#31034;&#21517;&#31216;</label><input id="project-rename-input" maxlength="100" autocomplete="off"><p class="help">&#20165;&#26356;&#25913;&#20391;&#26639;&#26174;&#31034;&#65292;&#19981;&#37325;&#21629;&#21517;&#26381;&#21153;&#22120;&#25991;&#20214;&#22841;&#12290;&#30041;&#31354;&#24674;&#22797;&#30446;&#24405;&#21517;&#12290;</p><p id="project-rename-error" class="error" role="alert" hidden></p><div class="dialog-actions"><button type="button" id="project-rename-cancel" class="secondary">&#21462;&#28040;</button><button type="submit" id="project-rename-save" class="primary">&#20445;&#23384;</button></div></form>';
document.body.append(projectRenameDialog);let projectRenameSession=null,projectRenameEditor=null;
function openProjectRename(session){projectRenameEditor={session};projectRenameSession=session;$('#project-rename-path').textContent=session.cwd;$('#project-rename-input').value=projectPreferences.get(session.cwd)?.name||'';$('#project-rename-input').placeholder=projectDisplayName(session.cwd);$('#project-rename-error').hidden=true;$('#project-rename-save').disabled=false;projectRenameDialog.showModal();}
$('#project-rename-close').onclick=$('#project-rename-cancel').onclick=()=>projectRenameDialog.close();
$('#project-rename-form').onsubmit=async e=>{e.preventDefault();const editor=projectRenameEditor,session=editor.session,name=$('#project-rename-input').value.trim();$('#project-rename-save').disabled=true;try{await saveProjectPreference(session,{name});if(projectRenameEditor===editor){projectRenameDialog.close();groupSessionRows($('#session-list'),state.sessions);}}catch(error){if(projectRenameEditor===editor){$('#project-rename-error').textContent=error.message;$('#project-rename-error').hidden=false;}}finally{if(projectRenameEditor===editor)$('#project-rename-save').disabled=false;}};
projectRenameDialog.addEventListener('close',()=>{if(projectRenameDialog.open)return;projectRenameSession=null;projectRenameEditor=null;});
$('#history-view').onchange=()=>{archivedView=$('#history-view').value==='archived';loadSessions();};
async function archiveSession(s){
 try{
  const data=await api('/api/sessions/'+pathId(s.id)+'/archive',{method:'POST',body:{archived:!s.archived_at}});
  historyCache.remove(s.id);
  if(currentId()===s.id){state.session=data.session;$('#prompt').disabled=!!data.session.archived_at;$('#attach').disabled=!!data.session.archived_at||!state.user.is_admin;setStatus(state.status);}
  if(!data.session.archived_at){archivedView=false;$('#history-view').value='active';}
  await loadSessions();notice(data.session.archived_at?'\u5df2\u5f52\u6863\uff0c\u5386\u53f2\u4e0e\u9879\u76ee\u6587\u4ef6\u4fdd\u7559':'\u5df2\u6062\u590d\u4f1a\u8bdd');
 }catch(e){notice(e.message);}
}
let projectCycle=0,projectRequest=0,projectSession=null,projectPath='',projectFile='';
const projectDialog=$('#project-dialog');
const splitWorkspace=document.createElement('div');splitWorkspace.className='workbench-split';
const chatPane=document.createElement('div');chatPane.className='chat-pane';chatPane.append($('#conversation'),$('.composer-wrap'));
splitWorkspace.append(chatPane,projectDialog);$('.workspace').append(splitWorkspace);
$('#project-open').setAttribute('aria-controls','project-dialog');$('#project-open').setAttribute('aria-expanded','false');
function resetProjectPanel(){if($('#project-changes'))$('#project-changes').disabled=true;$('#project-parent').disabled=true;$('#project-root').textContent='';projectCycle++;projectRequest++;projectSession=null;projectFile='';projectPath='';$('#project-content').textContent='';$('#project-selected').textContent='';$('#project-entries').replaceChildren();$('#project-image').hidden=true;$('#project-image').removeAttribute('src');$('#project-download').hidden=true;$('#project-diff').hidden=true;}
function bindProjectPanel(session){if(projectDialog.hidden||projectSession?.id===session.id)return;resetProjectPanel();projectSession={...session};if($('#project-changes'))$('#project-changes').disabled=false;$('#project-root').textContent=session.cwd;browseProject();}
function closeProjectPanel(focus=true){resetProjectPanel();projectDialog.hidden=true;splitWorkspace.classList.remove('has-project');$('#project-open').setAttribute('aria-expanded','false');$('#project-open').textContent='\u6587\u4ef6';if(focus)$('#project-open').focus({preventScroll:true});scheduleBottomPin();}
$('#project-close').onclick=()=>closeProjectPanel();
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!e.defaultPrevented&&!projectDialog.hidden&&!document.querySelector('dialog[open]')){e.preventDefault();closeProjectPanel();}});

function projectError(error){const code=error.status;return code===403?'\u8def\u5f84\u4e0d\u5728\u6388\u6743\u8303\u56f4\uff0c\u6216\u5305\u542b\u7b26\u53f7\u94fe\u63a5':code===404?'\u6587\u4ef6\u6216\u76ee\u5f55\u5df2\u4e0d\u53ef\u7528':code===413?'\u5185\u5bb9\u8d85\u51fa\u9884\u89c8\u4e0a\u9650\uff0c\u8bf7\u4e0b\u8f7d\u539f\u6587\u4ef6':code===415?'\u6682\u4e0d\u652f\u6301\u6b64\u7f16\u7801\u6216\u6587\u4ef6\u7c7b\u578b\uff0c\u8bf7\u4e0b\u8f7d\u539f\u6587\u4ef6':code===409?'\u6b64\u76ee\u5f55\u9700\u6709\u72ec\u7acb Git \u4ed3\u5e93\u548c HEAD \u63d0\u4ea4\uff0c\u4e14\u6587\u4ef6\u672a\u5728\u67e5\u770b\u65f6\u53d8\u5316':code===408?'\u5bf9\u6bd4\u8d85\u65f6\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5':error.message;}
function projectURL(kind,path){return '/api/sessions/'+pathId(projectSession.id)+'/project-view?kind='+kind+'&path='+encodeURIComponent(path);}
function currentProject(cycle,request){return !projectDialog.hidden&&projectSession?.id===currentId()&&cycle===projectCycle&&request===projectRequest;}
async function browseProject(path=''){
 const cycle=projectCycle,request=++projectRequest;$('#project-feedback').textContent='\u6b63\u5728\u8bfb\u53d6\u76ee\u5f55';
 try{
  const data=await api(projectURL('tree',path));if(!currentProject(cycle,request))return;
  projectPath=data.path;$('#project-parent').disabled=!projectPath;const list=$('#project-entries');list.replaceChildren();
  for(const entry of data.entries||[]){const b=document.createElement('button');b.type='button';b.className='project-entry';b.textContent=entry.name+(entry.kind==='directory'?'/':'');b.disabled=entry.kind==='blocked';b.title=entry.kind==='blocked'?'\u7b26\u53f7\u94fe\u63a5\u6216\u7279\u6b8a\u6587\u4ef6\u4e0d\u53ef\u6253\u5f00':entry.path;b.onclick=()=>entry.kind==='directory'?browseProject(entry.path):readProject(entry);list.append(b);}
  $('#project-feedback').textContent=(projectPath||'/')+(data.truncated?' \u00b7 \u4ec5\u663e\u793a\u524d 1000 \u9879':'');
 }catch(e){if(currentProject(cycle,request))$('#project-feedback').textContent=projectError(e);}
}
async function readProject(entry,diff=false){
 const cycle=projectCycle,request=++projectRequest;projectFile=entry.path;
 $('#project-selected').textContent=entry.path;$('#project-content').textContent='';$('#project-image').hidden=true;$('#project-image').removeAttribute('src');
 const full=projectSession.cwd.replace(/\/$/,'')+'/'+entry.path,url='/api/project-file?session_id='+pathId(projectSession.id)+'&path='+encodeURIComponent(full);
 $('#project-download').href=url;$('#project-download').download=entry.path.split('/').at(-1);$('#project-download').hidden=false;$('#project-diff').hidden=false;
 if(!diff&&Number.isFinite(entry.size)&&entry.size>20*1024*1024&&/\.(?:png|jpe?g|gif|webp)$/i.test(entry.path)){$('#project-feedback').textContent='\u56fe\u7247\u8d85\u8fc7 20 MiB\uff0c\u8bf7\u4e0b\u8f7d\u67e5\u770b';return;}
 if(!diff&&/\.(?:png|jpe?g|gif|webp)$/i.test(entry.path)){$('#project-image').src=url;$('#project-image').hidden=false;$('#project-feedback').textContent='\u56fe\u7247\u9884\u89c8';return;}
 $('#project-feedback').textContent='\u6b63\u5728\u8bfb\u53d6\u5185\u5bb9';
 try{const data=await api(projectURL(diff?'diff':'text',entry.path),{timeoutMs:6000});if(!currentProject(cycle,request))return;$('#project-content').textContent=data.text||(diff?'\u4e0e HEAD \u65e0\u5dee\u5f02\uff0c\u6216\u6587\u4ef6\u5c1a\u672a\u8ddf\u8e2a':'\u7a7a\u6587\u4ef6');$('#project-feedback').textContent=diff?'\u4e0e HEAD \u7684\u5dee\u5f02\uff08\u4e0d\u542b\u672a\u8ddf\u8e2a\u6587\u4ef6\uff09':'\u53ea\u8bfb\u539f\u6587';}
 catch(e){if(currentProject(cycle,request))$('#project-feedback').textContent=projectError(e);}
}
$('#project-open').onclick=()=>{
 if(!state.session){notice('\u8bf7\u5148\u9009\u62e9\u4f1a\u8bdd');return;}
 if(!projectDialog.hidden){closeProjectPanel();return;}
 projectDialog.hidden=false;splitWorkspace.classList.add('has-project');$('#project-open').setAttribute('aria-expanded','true');$('#project-open').textContent='\u6536\u8d77\u6587\u4ef6';bindProjectPanel(state.session);projectDialog.focus({preventScroll:true});scheduleBottomPin();
};
$('#project-parent').onclick=()=>browseProject(projectPath.split('/').slice(0,-1).join('/'));
$('#project-diff').onclick=()=>{if(projectFile)readProject({path:projectFile},true);};



function scheduleProjectDefaults(){
 clearTimeout(defaultsTimer);const editor=newEditor;if(!newActive(editor))return;
 const raw=$('#cwd').value.trim();if(!raw.startsWith('/'))return;
 const cycle=++defaultsCycle;
 const apply=values=>{if(cycle!==defaultsCycle||!newActive(editor)||editor.saving||$('#cwd').value.trim()!==raw)return;editor.defaults=values||{};applyNewFields(editor);};
 if(projectDefaultsCache.has(raw))apply(projectDefaultsCache.get(raw));
 defaultsTimer=setTimeout(async()=>{try{const r=await api('/api/project-defaults?cwd='+encodeURIComponent(raw));projectDefaultsCache.set(raw,r.defaults);apply(r.defaults);}catch{/* A new path may not exist yet; creating a project remains explicit. */}},180);
}
let defaultsEditor=null;
const defaultsDialog=document.createElement('dialog');defaultsDialog.id='project-defaults-dialog';defaultsDialog.setAttribute('aria-labelledby','project-defaults-title');
defaultsDialog.innerHTML='<form id="project-defaults-form"><div class="dialog-heading"><div><h2 id="project-defaults-title">&#39033;&#30446;&#40664;&#35748;&#37197;&#32622;</h2><p id="project-defaults-path" class="break"></p></div><button type="button" class="icon-button" id="project-defaults-close" aria-label="&#20851;&#38381;">&#215;</button></div><label for="defaults-model">&#27169;&#22411;</label><select id="defaults-model"></select><label for="defaults-effort">&#25512;&#29702;&#24378;&#24230;</label><select id="defaults-effort"></select><label class="check-row"><input id="defaults-enabled" type="checkbox">&#36328;&#27169;&#22411;&#23457;&#26680;&#19982;&#32534;&#31243;</label><label for="defaults-reviewer">&#23457;&#26680;&#24378;&#24230;</label><select id="defaults-reviewer"></select><label for="defaults-coder">&#32534;&#31243;&#24378;&#24230;</label><select id="defaults-coder"></select><p class="help">&#20165;&#29992;&#20110;&#35813;&#39033;&#30446;&#30340;&#26032;&#20250;&#35805;&#65292;&#19981;&#25913;&#21464;&#24050;&#26377;&#20250;&#35805;&#21644;&#26435;&#38480;&#12290;</p><p id="defaults-feedback" role="status"></p><div class="dialog-actions"><button class="primary" id="defaults-save" type="submit">&#20445;&#23384;</button></div></form>';
document.body.append(defaultsDialog);
$('#project-defaults-close').onclick=()=>defaultsDialog.close();
function fillProjectDefaults(values){const p=providerFor('claude');populateModels(p,$('#defaults-model'),$('#defaults-effort'),values.model||null,values.reasoning_effort||null);$('#defaults-enabled').checked=!!values.collaboration_enabled;for(const role of ['reviewer','coder']){const select=$('#defaults-'+role);select.replaceChildren();for(const effort of roleCandidate(p,role)?.supportedEffortLevels||[]){if(![...select.options].some(o=>o.value===effort))select.add(new Option(({low:'\u4f4e',medium:'\u4e2d\u7b49',high:'\u9ad8',xhigh:'\u8d85\u9ad8',max:'\u6700\u9ad8'})[effort]||effort,effort));}const saved=values[role+'_effort']||'high';if(![...select.options].some(o=>o.value===saved))appendMissing(select,saved,saved);select.value=saved;}}
async function openProjectDefaults(session){defaultsEditor={session,dirty:false,saving:false};const editor=defaultsEditor;$('#project-defaults-path').textContent=session.cwd;$('#defaults-feedback').textContent='\u6b63\u5728\u8bfb\u53d6';fillProjectDefaults(projectDefaultsCache.get(session.cwd)||session);defaultsDialog.showModal();$('#defaults-save').disabled=true;try{const [r]=await Promise.all([api('/api/project-defaults?cwd='+encodeURIComponent(session.cwd)),loadProviders({maxAge:60000})]);if(defaultsEditor!==editor||!defaultsDialog.open)return;projectDefaultsCache.set(session.cwd,r.defaults);if(!editor.dirty)fillProjectDefaults(r.defaults||{});$('#defaults-feedback').textContent='';$('#defaults-save').disabled=false;}catch(e){if(defaultsEditor===editor)$('#defaults-feedback').textContent=e.message;}}
$('#project-defaults-form').onchange=()=>{if(defaultsEditor)defaultsEditor.dirty=true;};
$('#defaults-model').addEventListener('change',()=>{const p=providerFor('claude');populateEfforts(p,$('#defaults-model'),$('#defaults-effort'),null);});
$('#project-defaults-form').onsubmit=async e=>{e.preventDefault();const editor=defaultsEditor;if(!editor||editor.saving)return;editor.saving=true;$('#defaults-save').disabled=true;const body={model:$('#defaults-model').value||null,reasoning_effort:$('#defaults-effort').value||null,collaboration_enabled:$('#defaults-enabled').checked,reviewer_effort:$('#defaults-reviewer').value||'high',coder_effort:$('#defaults-coder').value||'high'};try{const r=await api('/api/sessions/'+pathId(editor.session.id)+'/project-defaults',{method:'PUT',body});projectDefaultsCache.set(editor.session.cwd,r.defaults);if(defaultsEditor===editor){defaultsDialog.close();notice('\u5df2\u4fdd\u5b58\u9879\u76ee\u9ed8\u8ba4\u914d\u7f6e');}}catch(error){if(defaultsEditor===editor)$('#defaults-feedback').textContent=error.message;}finally{editor.saving=false;if(defaultsEditor===editor)$('#defaults-save').disabled=false;}};
defaultsDialog.addEventListener('close',()=>{defaultsEditor=null;});

let attentionSnapshot={},readBusy=false;
const attentionFilter=document.createElement('select');attentionFilter.id='attention-filter';attentionFilter.setAttribute('aria-label','\u7b5b\u9009\u4f1a\u8bdd\u72b6\u6001');
for(const [v,t] of [['all','\u5168\u90e8\u72b6\u6001'],['unread','\u672a\u8bfb\u56de\u590d'],['confirm','\u9700\u8981\u786e\u8ba4'],['error','\u5931\u8d25\u6216\u5f85\u6838\u9a8c']])attentionFilter.add(new Option(t,v));
$('.history-view').append(attentionFilter);attentionFilter.onchange=paintAttention;
function paintAttention(){for(const button of document.querySelectorAll('#session-list .session')){const sid=button.dataset.sessionId,head=attentionSnapshot[sid],run=sessionRuntime.get(sid)?.data,phase=run?.display_status;const confirm=['requires_action','answering','approval_unknown'].includes(phase),error=['failed','unknown','interrupted'].includes(run?.status||head?.status),unread=head?.unread||0;let badge=button.querySelector('.session-attention');if(!badge){badge=document.createElement('span');badge.className='session-attention';button.querySelector('.body').append(badge);}badge.textContent=confirm?'\u5f85\u786e\u8ba4':error?'\u5f85\u68c0\u67e5':unread?unread+' \u672a\u8bfb':'';badge.dataset.kind=confirm?'confirm':error?'error':'unread';badge.hidden=!badge.textContent;button.closest('.session-row').hidden=attentionFilter.value==='confirm'?!confirm:attentionFilter.value==='error'?!error:attentionFilter.value==='unread'?!unread:false;}
 for(const group of document.querySelectorAll('.project-group'))group.hidden=![...group.querySelectorAll('.session-row')].some(r=>!r.hidden);}
async function markVisibleRead(){const sid=currentId(),head=attentionSnapshot[sid];if(readBusy||document.hidden||!document.hasFocus()||!state.followBottom||state.switching||!head?.unread||!state.events.has(head.seq))return;readBusy=true;try{await api('/api/sessions/'+pathId(sid)+'/read',{method:'POST',body:{seq:head.seq}});if(attentionSnapshot[sid]?.seq===head.seq){attentionSnapshot[sid].unread=0;paintAttention();}}catch{}finally{readBusy=false;}}
window.addEventListener('focus',markVisibleRead);conversationScroll.addEventListener('scroll',()=>{if(state.followBottom)markVisibleRead();},{passive:true});
const changesButton=document.createElement('button');changesButton.id='project-changes';changesButton.type='button';changesButton.className='text-button';changesButton.textContent='\u5f53\u524d\u6539\u52a8';$('#project-parent').after(changesButton);
changesButton.onclick=async()=>{const cycle=projectCycle,request=++projectRequest;$('#project-feedback').textContent='\u6b63\u5728\u8bfb\u53d6\u6539\u52a8';try{const data=await api(projectURL('changes',''),{timeoutMs:6000});if(!currentProject(cycle,request))return;const list=$('#project-entries');list.replaceChildren();for(const entry of data.entries){const b=document.createElement('button');b.type='button';b.className='project-entry';const labels={added:'\u65b0\u589e',modified:'\u4fee\u6539',deleted:'\u5220\u9664',renamed:'\u91cd\u547d\u540d',untracked:'\u672a\u8ddf\u8e2a',conflict:'\u51b2\u7a81'};b.textContent=(labels[entry.status]||entry.status)+' \u00b7 '+entry.path;b.title=entry.previous_path?entry.previous_path+' \u2192 '+entry.path:entry.path;b.onclick=()=>readProject(entry,entry.status!=='untracked');list.append(b);}$('#project-feedback').textContent=(data.entries.length?data.entries.length+' \u4e2a\u6539\u52a8\u6587\u4ef6':'\u6ca1\u6709\u6539\u52a8')+' \u00b7 \u5f53\u524d\u5171\u4eab\u9879\u76ee\u72b6\u6001\uff0c\u4e0d\u4ee3\u8868\u672c\u6b21\u5bf9\u8bdd\u4fee\u6539';}catch(e){if(currentProject(cycle,request))$('#project-feedback').textContent=projectError(e);}};
(async()=>{try{const me=await api('/api/auth/me');state.user=me.user;state.csrf=me.csrf_token;sendOutbox.init(state.user);attachmentManager.init();if(newActive(newEditor))updateNewGate(newEditor);initSystemMetrics(state.user);$('#project-open').hidden=!state.user.is_admin;$('#diagnostics-open').hidden=!state.user.is_admin;initQuota();$('#current-user').textContent=state.user.username;$('#settings-open').hidden=!state.user.is_admin;const ready=historyCache.init(state.user);await Promise.all([ready,loadSessions(),loadProjectPreferences()]);loadProviders();const last=historyCache.last();if(last&&state.sessions.some(s=>s.id===last))selectSession(last);if(!state.user.is_admin)notice('当前仅管理员可使用已配置的 Claude 账户；普通用户尚未配置独立凭据。');}catch(e){notice(e.message)}})();
