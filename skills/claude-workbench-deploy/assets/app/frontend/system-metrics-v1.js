'use strict';
let metricsEnabled=false,metricsTimer=null,metricsController=null;
function initSystemMetrics(user){
 metricsEnabled=!!user?.is_admin;document.querySelector('#server-metrics').hidden=!metricsEnabled;
 if(metricsEnabled)refreshSystemMetrics();
}
function metricPercent(item){return item?.state==='known'&&Number.isFinite(item.percent)&&item.percent>=0&&item.percent<=100?item.percent.toFixed(1).replace(/\.0$/,'')+'%':'\u2014';}
function metricBytes(value){return Number.isFinite(value)&&value>=0?(value/1073741824).toFixed(1)+' GiB':'未知';}
function showSystemMetrics(value,error=false){
 const names={cpu:'CPU',memory:'内存',disk:'磁盘'};
 for(const [key,name] of Object.entries(names))document.querySelector('#metric-'+key).textContent=metricPercent(value?.[key]);
 const body=document.querySelector('#server-metrics-details');body.replaceChildren();
 const add=text=>{const line=document.createElement('div');line.textContent=text;body.append(line);};
 if(error){add('暂时无法读取服务器资源，下次自动重试。');return;}
 if(['cpu','memory','disk'].some(k=>value?.[k]?.state==='unavailable'))add('\u90e8\u5206\u6307\u6807\u6682\u4e0d\u53ef\u7528\u3002');
 add('CPU、内存为服务器整机使用率；磁盘为系统盘 / 的容量占用率。');
 add('CPU：'+metricPercent(value?.cpu)+(value?.cpu?.cores?'  '+value.cpu.cores+' 核':'')+(value?.cpu?.state==='unavailable'?'\uff08\u6682\u4e0d\u53ef\u7528\uff09':value?.cpu?.state==='sampling'?'（正在采样）':''));
 add('内存：'+metricBytes(value?.memory?.used_bytes)+' / '+metricBytes(value?.memory?.total_bytes));
 add('磁盘：'+metricBytes(value?.disk?.used_bytes)+' / '+metricBytes(value?.disk?.total_bytes));
 add('磁盘可用：'+metricBytes(value?.disk?.available_bytes));
 if(value?.sampled_at){const date=new Date(value.sampled_at);if(!Number.isNaN(date.valueOf()))add('采样：'+date.toLocaleTimeString('zh-CN',{hour12:false})+'  每5秒刷新');}
}
async function refreshSystemMetrics(){
 clearTimeout(metricsTimer);metricsTimer=null;
 if(!metricsEnabled||document.hidden||metricsController)return;
 const controller=new AbortController();metricsController=controller;const timeout=setTimeout(()=>controller.abort(),4000);
 try{const value=await api('/api/system-metrics',{signal:controller.signal});if(!document.hidden)showSystemMetrics(value);}
 catch(e){if(!document.hidden){if(e.status===403){metricsEnabled=false;document.querySelector('#server-metrics').hidden=true;}else showSystemMetrics(null,true);}}
 finally{clearTimeout(timeout);metricsController=null;if(metricsEnabled&&!document.hidden)metricsTimer=setTimeout(refreshSystemMetrics,5000);}
}
document.addEventListener('visibilitychange',()=>{
 clearTimeout(metricsTimer);metricsTimer=null;
 if(document.hidden)metricsController?.abort();else if(metricsEnabled)refreshSystemMetrics();
});
window.addEventListener('pagehide',()=>{clearTimeout(metricsTimer);metricsController?.abort();});

window.addEventListener('pageshow',()=>{if(metricsEnabled&&!document.hidden)refreshSystemMetrics();});
