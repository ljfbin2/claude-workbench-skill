import RFB from '/static/vendor/novnc/core/rfb.js';
const $=s=>document.querySelector(s);let csrf='',rfb=null,account=null,pollTimer=null,remoteClipboard='',autoOpened=null;
async function api(path,options={}){
 const opt={credentials:'same-origin',...options,headers:{...(options.headers||{})}};
 if(opt.method&&opt.method!=='GET')opt.headers['X-CSRF-Token']=csrf;
 if(opt.body){opt.headers['Content-Type']='application/json';opt.body=JSON.stringify(opt.body);}
 const r=await fetch(path,opt);if(r.status===401){location.assign('/login');throw Error('登录已失效');}
 const v=await r.json();if(!r.ok)throw Error(typeof v.detail==='string'?(v.detail==='Assistant is busy; try again when idle'?'\u52a9\u624b\u6b63\u5fd9\uff0c\u8bf7\u5728\u4efb\u52a1\u7ed3\u675f\u540e\u91cd\u8bd5':v.detail):'请求失败（'+r.status+'）');return v;
}
function feedback(text=''){$('#feedback').textContent=text;}
async function connect(){
 feedback();$('#connection').textContent='正在连接服务器';
 if(rfb){rfb.disconnect();rfb=null;}$('#screen').replaceChildren();
 try{
  await api('/api/server-browser/status');
  const client=new RFB($('#screen'),(location.protocol==='https:'?'wss:':'ws:')+'//'+location.host+'/api/server-browser/socket');rfb=client;
  client.scaleViewport=true;client.resizeSession=false;client.background='#edf1f6';
  client.addEventListener('connect',()=>{if(rfb===client)$('#connection').textContent='已连接  服务器浏览器';});
  client.addEventListener('disconnect',()=>{if(rfb===client)$('#connection').textContent='连接已断开，可点击重新连接';});
  client.addEventListener('securityfailure',()=>feedback('远程连接验证失败，请重新登录工作台。'));
  client.addEventListener('clipboard',e=>{remoteClipboard=e.detail.text||'';if($('#clipboard-dialog').open)$('#clipboard-text').value=remoteClipboard;});
 }catch(e){$('#connection').textContent='连接失败';feedback(e.message);}
}
async function navigate(target){try{feedback();await api('/api/server-browser/open',{method:'POST',body:{target}});}catch(e){feedback(e.message);}}
function accountView(v){
 account=v;const active=['starting','waiting','submitting','cancelling'].includes(v.state);
 const labels={submitting:'\u6b63\u5728\u9a8c\u8bc1\u6388\u6743\u7801\u2026',cancelling:'\u6b63\u5728\u53d6\u6d88\u6388\u6743\u2026',idle:'未启动授权',starting:'正在启动官方授权',waiting:'请在服务器浏览器中完成官方授权',success:'Claude Code 登录成功',failed:'授权未完成或已过期，可重试',cancelled:'已取消本次授权'};
 $('#auth-status').textContent=labels[v.state]||'授权状态未知';$('#auth-start').disabled=active;
 $('#auth-open').hidden=!v.has_url;$('#auth-cancel').hidden=!active;$('#auth-code-form').hidden=v.state!=='waiting';
}
async function accountPoll(){
 clearTimeout(pollTimer);if(!$('#account-panel').open)return;
 try{const v=await api('/api/server-browser/account');accountView(v);if(v.has_url&&autoOpened!==v.id){await api('/api/server-browser/account/open',{method:'POST',body:{id:v.id}});autoOpened=v.id;}if($('#account-panel').open&&['starting','waiting','submitting','cancelling'].includes(v.state))pollTimer=setTimeout(accountPoll,1500);}
 catch(e){feedback(e.message);$('#auth-status').textContent='授权状态读取失败，请重新打开此面板查看。';}
}
$('#reconnect').onclick=connect;$('#open-claude').onclick=()=>navigate('claude');$('#open-billing').onclick=()=>navigate('billing');
$('#clipboard-open').onclick=()=>{$('#clipboard-text').value=remoteClipboard;$('#clipboard-dialog').showModal();};
$('#clipboard-send').onclick=()=>{if(!rfb){feedback('请先连接服务器浏览器。');return;}rfb.clipboardPasteFrom($('#clipboard-text').value);$('#clipboard-dialog').close();rfb.focus();};
$('#clipboard-dialog').addEventListener('close',()=>{$('#clipboard-text').value='';});
$('#account-panel').addEventListener('toggle',()=>{if($('#account-panel').open)accountPoll();else{clearTimeout(pollTimer);$('#auth-code').value='';}});
$('#auth-start').onclick=async()=>{if(!confirm('启动新的官方 Claude Code 授权？请确认当前没有编程任务；完成前将暂时无法发送新任务。'))return;try{feedback();$('#auth-start').disabled=true;const v=await api('/api/server-browser/account/start',{method:'POST'});accountView(v);if(v.has_url)autoOpened=v.id;accountPoll();}catch(e){feedback(e.message);$('#auth-start').disabled=false;}};
$('#auth-open').onclick=async()=>{try{await api('/api/server-browser/account/open',{method:'POST',body:{id:account.id}});}catch(e){feedback(e.message);}};
$('#auth-cancel').onclick=async()=>{try{accountView(await api('/api/server-browser/account/cancel',{method:'POST',body:{id:account.id}}));accountPoll();}catch(e){feedback(e.message);}};
$('#code-from-remote').onclick=()=>{$('#auth-code').value=remoteClipboard.trim();};
$('#auth-code-form').onsubmit=async e=>{e.preventDefault();if(account?.state!=='waiting')return;const code=$('#auth-code').value;accountView({...account,state:'submitting'});try{await api('/api/server-browser/account/code',{method:'POST',body:{id:account.id,code}});$('#auth-code').value='';remoteClipboard='';accountPoll();}catch(err){feedback(err.message);accountPoll();}};
window.addEventListener('beforeunload',()=>{clearTimeout(pollTimer);rfb?.disconnect();remoteClipboard='';});
(async()=>{try{const me=await api('/api/auth/me');if(!me.user.is_admin)throw Error('只有管理员可以使用服务器浏览器。');csrf=me.csrf_token;await connect();}catch(e){feedback(e.message);$('#connection').textContent='无法连接';}})();
