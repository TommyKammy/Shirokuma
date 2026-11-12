'use strict';

// Result saving uses only explicit Workbench and browser adapters.
function createResultExportController({host,authority,http,view,browser}){
let dbExportView='',dbExportRevision=0;
const dbExportTypes={csv:'text/csv; charset=utf-8',json:'application/json; charset=utf-8',tsv:'text/tab-separated-values; charset=utf-8'};
const dbExportHelp='Save all received rows in original order; display filter and sort do not change the saved range. CSV/TSV: unquoted \\N is NULL; all non-NULL values are quoted. JSON encodes every integer cell, including nested values, as a decimal string.';
function dbExportScope(){
 const w=host.current(),stamp=JSON.stringify([host.activeTab,host.workspacePane,host.worksheetId,w&&host.input(w),authority.role,authority.maximumRole,authority.principal,authority.epoch,authority.authorizationVersion,authority.roleVersion(w?.selected_role)]);
 if(stamp!==dbExportView){dbExportView=stamp;++dbExportRevision;}
 return JSON.stringify([stamp,dbExportRevision]);
}
function dbExportReady(run){
 const w=host.current();
 return host.activeTab==='warehouse'&&host.workspacePane==='worksheet'&&!!run&&host.live(run)&&host.worksheetId===run.origin.worksheet_id&&run.authorized&&!run.rowsPurged&&!run.restored&&!run.exportUnavailable&&!!run.id&&!!run.receivedPages?.length&&run.origin.principal===authority.principal&&authority.roleWithinHeader(run.origin.role)&&w?.selected_role===run.origin.role&&JSON.stringify(host.context(w))===JSON.stringify(run.origin.context)&&!host.reason(w);
}
function dbExportControls(){
 dbExportScope();const run=host.run(),ready=dbExportReady(run);
 for(const format of Object.keys(dbExportTypes)){
  const button=view.$('export-'+format);button.disabled=!ready||!!run?.saveBusy;button.dataset.databaseUnavailable=ready?'false':'true';
  button.setAttribute('aria-busy',String(!!run?.saveBusy));button.setAttribute('aria-describedby','result-action-state');
 }
 view.$('copy-tsv').disabled=true;
 if(run?.saveBusy)view.ui(view.$('result-action-state'),'Saving received rows…');
 else if(run?.saveNotice){
  const {key,values}=run.saveNotice;
  view.ui(view.$('result-action-state'),key,values?()=>({...values,state:view.t(values.state)}):{});
 }
 else if(ready)view.ui(view.$('result-action-state'),'Received {rows} rows · {state} · partial: {partial} · truncated: {truncated}.',{rows:run.rows.length,state:view.t(run.state),partial:String(run.state!=='FINISHED'||!!run.truncated),truncated:String(!!run.truncated)});
 else view.ui(view.$('result-action-state'),'Result saving unavailable. Received page receipts or cached results are unavailable; SQL is not rerun.');
}
function dbExportCapture(run){
 return JSON.stringify([dbExportScope(),run.token,run.id,run.origin.request_id,run.origin.saved_worksheet_id||run.origin.worksheet_id,run.origin.context,run.origin.role,run.origin.principal,run.receivedPages,run.rows.length,run.state,!!run.truncated]);
}
function dbExportReply(data,format,run){
 const keys=['format','filename','content_type','content_base64','query_id','request_id','worksheet_id','context','returned_rows','saved_rows','state','partial','truncated'];
 const contextKeys=['database_id','schema_id','warehouse_id','sql_catalog','sql_schema','selected_role','binding_revision','generation_id','grant_revision'];
 if(!http.exact(data,keys)||data.format!==format||data.filename!=='query-result-'+run.id+'.'+format||!/^query-result-[A-Za-z0-9_-]+\.(csv|json|tsv)$/.test(data.filename)||data.content_type!==dbExportTypes[format]||data.query_id!==run.id||data.request_id!==run.origin.request_id||data.worksheet_id!==(run.origin.saved_worksheet_id||run.origin.worksheet_id)||!http.exact(data.context,contextKeys)||contextKeys.some(k=>data.context[k]!==run.origin.context[k])||data.returned_rows!==run.rows.length||data.saved_rows!==run.rows.length||!['QUEUED','RUNNING','FINISHED','FAILED','CANCELED','UNKNOWN'].includes(data.state)||typeof data.partial!=='boolean'||(run.state!=='FINISHED'||data.state!=='FINISHED'||!!run.truncated)&&!data.partial||data.truncated!==!!run.truncated||typeof data.content_base64!=='string'||data.content_base64.length>Math.ceil(16777216/3)*4||data.content_base64.length%4!==0||!/^[A-Za-z0-9+/]*={0,2}$/.test(data.content_base64))throw Error('INVALID_RESULT_EXPORT');
 const binary=browser.atob(data.content_base64);if(binary.length>16777216)throw Error('INVALID_RESULT_EXPORT');return Uint8Array.from(binary,c=>c.charCodeAt(0));
}
async function dbExportResults(format){
 const run=host.run();if(!dbExportTypes[format]||!dbExportReady(run)||run.saveBusy)return;
 const capture=dbExportCapture(run),token={};run.saveBusy=token;delete run.saveNotice;dbExportControls();
 const current=()=>run.saveBusy===token&&dbExportReady(run)&&capture===dbExportCapture(run);
 const authorize=async()=>{
  const revision=dbExportRevision;
  let items;try{items=await authority.readInventory(run.origin.role,current);}catch(error){if(run.saveBusy===token&&host.live(run)&&revision===dbExportRevision&&host.activeTab==='warehouse'&&host.workspacePane==='worksheet'&&host.worksheetId===run.origin.worksheet_id&&['DB_ACCESS_REVOKED','AUTHENTICATION_REQUIRED','STALE_BINDING','STALE_CONTEXT','CONTEXT_MISMATCH','UNKNOWN_CONTEXT'].includes(error.code))authority.invalidateAccess(error,run.origin.role,run.origin.context.database_id);throw error;}
  if(!current())return false;
  authority.checkAuthorization(authority.receipt(items));authority.applyInventory(items,run.origin.role);
  if(!authority.runAllowed(run,items)){authority.hideRows(r=>r===run);return false;}
  if(!current())return false;
  // Inspect the primary binding and schema without advancing query pages.
  const response=await http.fetch('/api/v1/queries/'+encodeURIComponent(run.id),{headers:{'X-Shirokuma-Role':run.origin.role}});
  if(!current())return false;
  if([401,403].includes(response.status))throw Object.assign(Error('DB_ACCESS_REVOKED'),{code:response.status===401?'AUTHENTICATION_REQUIRED':'DB_ACCESS_REVOKED'});
  const detail=await response.json();if(!current())return false;
  if(!response.ok)throw http.error(detail,response.status);
  const keys=['database_id','schema_id','warehouse_id','sql_catalog','sql_schema','selected_role','binding_revision','generation_id','grant_revision'];
  if(detail?.id!==run.id||detail.request_id!==run.origin.request_id||detail.worksheet_id!==(run.origin.saved_worksheet_id||run.origin.worksheet_id)||!http.exact(detail.context,keys)||keys.some(key=>detail.context[key]!==run.origin.context[key])||!['QUEUED','RUNNING','FINISHED','FAILED','CANCELED','UNKNOWN'].includes(detail.state)||detail.execution_closure)throw Object.assign(Error('CONTEXT_MISMATCH'),{code:'CONTEXT_MISMATCH'});
  return current();
 };
 try{
  if(!await authorize())return;
  const response=await http.fetch('/api/v1/queries/'+encodeURIComponent(run.id)+'/results/export',{method:'POST',headers:{'X-Shirokuma-Role':run.origin.role,'Content-Type':'application/json'},body:JSON.stringify({format,request_id:run.origin.request_id,worksheet_id:run.origin.saved_worksheet_id||run.origin.worksheet_id,received_pages:[...run.receivedPages]})});
  if(!current())return;
  const data=await response.json();if(!current())return;
  if(!response.ok)throw http.error(data,response.status);
  const bytes=dbExportReply(data,format,run);
  if(!await authorize()||!current())return;
  const url=browser.URL.createObjectURL(new browser.Blob([bytes],{type:data.content_type})),link=browser.document.createElement('a');
  try{link.href=url;link.download=data.filename;browser.document.body.append(link);if(current())link.click();}finally{link.remove();browser.URL.revokeObjectURL(url);}
  if(current())run.saveNotice={key:'Saved {rows} rows · {state} · partial: {partial} · truncated: {truncated}.',values:{rows:data.saved_rows,state:data.state,partial:String(data.partial),truncated:String(data.truncated)}};
 }catch(error){
  if(current()){
   if(!authority.invalidateAccess(error,run.origin.role,run.origin.context.database_id)){
    run.saveNotice={key:error.code==='RESULT_EXPORT_UNAVAILABLE'?'Result saving unavailable. Received page receipts or cached results are unavailable; SQL is not rerun.':'Result saving unavailable. Refresh context and check access; SQL is not rerun.'};
    if(error.code==='RESULT_EXPORT_UNAVAILABLE')run.exportUnavailable=true;
   }
  }
 }finally{if(run.saveBusy===token){delete run.saveBusy;if(host.worksheetId===run.origin.worksheet_id)dbExportControls();}}
}

function clearRun(run){delete run.exportUnavailable;delete run.saveBusy;delete run.saveNotice;}
function markUnavailable(run){run.exportUnavailable=true;}
function invalidateBoundary(){++dbExportRevision;}
return Object.freeze({types:dbExportTypes,help:dbExportHelp,scope:dbExportScope,ready:dbExportReady,controls:dbExportControls,capture:dbExportCapture,reply:dbExportReply,save:dbExportResults,clearRun,markUnavailable,invalidateBoundary});
}
