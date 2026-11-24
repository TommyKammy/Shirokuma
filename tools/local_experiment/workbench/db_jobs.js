'use strict';
// Native disclosures retain keyboard focus only within the same authorized Job/run.
const dbJobsDisclosures=new WeakMap();
function dbJobsResetDetails(node,identity){
 const old=dbJobsDisclosures.get(node),entries=old?.identity===identity?old:new Map();entries.identity=identity;
 for(const entry of entries.values())entry.focused=document.activeElement===entry.summary;
 for(const [key,entry] of entries){if(!entry.used){entries.delete(key);continue;}entry.used=false;entry.details.replaceChildren(entry.summary);}
 dbJobsDisclosures.set(node,entries);node.replaceChildren();
}
function dbJobsDetails(node,key,label){
 const entries=dbJobsDisclosures.get(node);let entry=entries.get(key);
 if(!entry){const details=document.createElement('details'),summary=document.createElement('summary');details.className='workflow-details';details.open=false;entry={details,summary};entries.set(key,entry);}
 entry.used=true;ui(entry.summary,label);entry.details.replaceChildren(entry.summary);node.append(entry.details);
 if(entry.focused){entry.summary.focus();entry.focused=false;}return entry.details;
}
const dbJobsStatus=state=>state==='canceled'?t('canceled'):jobStatus(state);
const dbJobsStatusKey=state=>state==='canceled'?'canceled':jobStatuses[state]||'Unknown outcome';
Object.assign(i18n.ja,{
 'Current Job':'現在のジョブ','No Job loaded':'読み込み済みジョブなし','Next action':'次の操作',
 'Job run metadata':'ジョブ実行のメタデータ','Step diagnostics':'ステップの診断情報','Run diagnostics':'実行の診断情報',
 'Job definition and action help':'ジョブ定義と操作のヘルプ','Parameter and input help':'パラメーターと入力のヘルプ',
 'Wait for the current Job request to finish.':'現在のジョブ要求が完了するまでお待ちください。',
 'Load a saved Job or fill in and save a new Job.':'保存済みジョブを読み込むか、新しいジョブを入力して保存してください。',
 'Review the saved Job and inputs, then run.':'保存済みジョブと入力を確認して実行してください。',
 'Observe the saved run to check its status.':'保存済みの実行を照会して状態を確認してください。',
 'Inspect the failed step before starting another run.':'失敗したステップを確認してから、次の実行を準備してください。',
 'Review the run result. Check the saved Job and inputs before running again.':'実行結果を確認できます。再実行する場合は保存済みジョブと入力を確認してください。',
 'Failure code':'失敗コード','Error code':'エラーコード','Rows ingested':'取り込み行数','Committed rows':'反映した行数','Partially completed':'一部のみ完了','Model count':'モデル数','Triggered':'アラート検知'
});
for(const key of Object.keys(i18n.ja))i18n.en[key]=key;
const dbJobsFields=['name','description','parameters','steps','arguments'];
const dbJobsTerminal=r=>r&&['succeeded','failed','canceled'].includes(r.status);
function dbJobsSuspend(){clearTimeout(dbJobs.pollTimer);dbJobs.pollTimer=null;}
function dbJobsPollingAllowed(){
 const p=dbJobs,r=p.run;
 return databaseMode&&p.started&&activeTab==='jobs'&&!document.hidden&&!p.pending&&!p.tracking&&!!r?.id&&['queued','running'].includes(r.status)&&!r.purged&&p.pollFailed!==r&&r.principal===dbPrincipal&&dbJobsAllowed(r.context,r.schemas);
}
function dbJobsSyncPolling(){
 if(!dbJobsPollingAllowed()){dbJobsSuspend();return;}
 if(dbJobs.pollTimer!=null)return;
 const run=dbJobs.run;
 const timer=setTimeout(()=>{if(dbJobs.pollTimer!==timer)return;dbJobs.pollTimer=null;if(dbJobs.run===run&&dbJobsPollingAllowed())void dbJobsObserve('observe',true);},2000);
 dbJobs.pollTimer=timer;
}

const dbJobsScope=c=>JSON.stringify([dbPrincipal,c]);
const dbJobsDraftKey=()=>dbJobsScope(dbJobs.context)+'/'+(dbJobs.job?.id||'new');
function dbJobsAllowed(context=dbJobs.context,schemas=dbJobs.schemas){
 if(!context||dbJobs.principal!==dbPrincipal||context.selected_role==='bi_viewer'||!dbRoleWithinHeader(context.selected_role))return false;
 const d=dbRoleItems(context.selected_role).find(d=>d.id===context.database_id),s=(schemas||[]).find(s=>s.id===context.schema_id);
 return !!(d&&d.state==='READY'&&d.allowed_roles?.includes(context.selected_role)&&d.binding_revision===context.binding_revision&&d.generation_id===context.generation_id&&(d.grant_revision??null)===context.grant_revision&&s?.capabilities?.job_sql===true);
}
function dbJobsOwn(job=dbJobs.job){return !job||(job.owner===JSON.parse(dbPrincipal)[0]&&job.selected_role===dbJobs.role);}
function dbJobsMatches(job=dbJobs.job,values=Object.fromEntries(dbJobsFields.map(k=>[k,$('db-jobs-'+k).value]))){
 if(!job)return false;
 const canonical=value=>JSON.stringify(value,(_,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v);
 try{return canonical({name:values.name,description:values.description,parameters:JSON.parse(values.parameters),steps:JSON.parse(values.steps)})===canonical({name:job.definition.name,description:job.definition.description||'',parameters:job.definition.parameters||{},steps:job.definition.steps});}catch{return false;}
}
function dbJobsCapture(){
 const p=dbJobs;if(!p.installed||p.invalid||!p.context)return;
 p.drafts.set(dbJobsDraftKey(),{context:p.context,schemas:dataopsClone(p.schemas),job:p.job?dataopsClone(p.job):null,values:Object.fromEntries(dbJobsFields.map(k=>[k,$('db-jobs-'+k).value])),save:p.save});
}
function dbJobsEditor(job=null){
 const p=dbJobs;p.job=job;p.selection=job?.id||'';p.save=null;p.view=p.editOpen?'list':'detail';
 const saved=p.drafts.get(dbJobsDraftKey());
 if(saved){p.job=saved.job;p.save=saved.save;}
 const definition=job?.definition;
 const values=saved?.values||{name:definition?.name||'',description:definition?.description||'',parameters:JSON.stringify(definition?.parameters||{},null,2),steps:JSON.stringify(definition?.steps||[{name:'check',action:'sql',sql:'SELECT 1',mode:'read-only'}],null,2),arguments:'{}'};
 for(const k of dbJobsFields)$('db-jobs-'+k).value=values[k];
 for(const row of p.files){row.name.value='';row.file.value='';}p.files=[];$('db-jobs-files').replaceChildren();
 dbJobsRender();
}
function dbJobsFocusEditDetails(){
 const details=$('db-jobs-edit-details');if(!details||details.hidden)return;
 details.scrollIntoView?.({block:'start'});
 details.children?.[0]?.focus?.({preventScroll:true});
}
function dbJobsNew(){
 const p=dbJobs;if(p.pending||p.invalid)return;
 dbJobsCapture();++p.request;p.editOpen=true;p.view='list';dbJobsEditor();dbJobsRender();
 if(typeof workbenchShowDetail==='function'){try{workbenchShowDetail('jobs','new');}catch{}}
 dbJobsFocusEditDetails();
}
async function dbJobsEdit(){
 const p=dbJobs,id=$('db-jobs-list').value;if(!id||p.pending||p.invalid)return;
 p.editOpen=true;p.view='list';await dbJobsLoad(false,id);
 if(p.job?.id!==id){p.editOpen=false;return;}
 p.view='list';p.editOpen=true;dbJobsRender();
 if(typeof workbenchShowDetail==='function'){try{workbenchShowDetail('jobs',id+'~edit');}catch{}}
 dbJobsFocusEditDetails();
}
function dbJobsPurge(){
 const p=dbJobs;if(!p.installed)return;dbJobsSuspend();++p.epoch;++p.request;p.scheduleRefreshing=false;p.invalid=true;p.jobs=[];p.history=[];p.job=null;p.save=null;p.drafts.clear();p.view='list';p.editOpen=false;
 for(const k of dbJobsFields)$('db-jobs-'+k).value='';
 for(const row of p.files){row.name.value='';row.file.value='';}p.files=[];$('db-jobs-files').replaceChildren();
 if(p.run){p.run.payload=null;p.run.details=null;p.run.schemas=[];p.run.purged=true;}
 if(p.principal!==dbPrincipal)p.run=null;
 ui(p.status,'Job access unavailable. Refresh an authorized context.');dbJobsRender();
}
function dbJobsValidate(){
 if(!dbJobs.installed)return;
 for(const [key,draft] of dbJobs.drafts)if(!dbJobsAllowed(draft.context,draft.schemas))dbJobs.drafts.delete(key);
 if(!dbJobs.invalid&&!dbJobsAllowed())dbJobsPurge();
 // A selection change cannot transfer authorization to an accepted run.
 const r=dbJobs.run;if(r&&!r.purged&&(!dbJobsAllowed(r.context,r.schemas)||r.principal!==dbPrincipal)){r.payload=null;r.details=null;r.schemas=[];r.purged=true;}
 dbJobsRender();
}
async function dbJobsRefreshAuthorization(){
 const p=dbJobs;if(!p.started)return;
 const epoch=p.epoch,principal=dbPrincipal,role=p.role,context=p.context,request=++p.authorizationRequest;
 const current=()=>epoch===p.epoch&&principal===dbPrincipal&&request===p.authorizationRequest;
 try{
  const items=await dbReadInventory(role);if(!current()||!dbInventoryCurrent(items))return;dbApplyInventory(items,role);
  const result=p.database?await dbAPI('/api/v1/databases/'+encodeURIComponent(p.database)+'/schemas',role):{items:[]};
  if(!current()||!dbInventoryCurrent(items))return;
  p.schemas=result.items||[];
  if(context&&!dbJobsAllowed(context))dbJobsPurge();
 const r=p.run;
  if(r&&!r.purged){
   let runSchemas=p.schemas;
   if(r.context.database_id!==p.database||r.context.selected_role!==role){
    const items=await dbReadInventory(r.context.selected_role);if(!current()||!dbInventoryCurrent(items))return;dbApplyInventory(items,r.context.selected_role);
    const result=await dbAPI('/api/v1/databases/'+encodeURIComponent(r.context.database_id)+'/schemas',r.context.selected_role);
    if(!current()||!dbInventoryCurrent(items))return;runSchemas=result.items||[];
   }
   r.schemas=runSchemas;if(!dbJobsAllowed(r.context,r.schemas)){r.payload=null;r.details=null;r.schemas=[];r.purged=true;}
  }
  // Detached drafts remain private only while their original grants and schemas do.
  for(const [key,draft] of p.drafts){
   let draftSchemas=p.schemas;
   if(draft.context.database_id!==p.database||draft.context.selected_role!==role){
    const items=await dbReadInventory(draft.context.selected_role);if(!current()||!dbInventoryCurrent(items))return;dbApplyInventory(items,draft.context.selected_role);
    const result=await dbAPI('/api/v1/databases/'+encodeURIComponent(draft.context.database_id)+'/schemas',draft.context.selected_role);
    if(!current()||!dbInventoryCurrent(items))return;draftSchemas=result.items||[];
   }
   draft.schemas=draftSchemas;if(!dbJobsAllowed(draft.context,draft.schemas))p.drafts.delete(key);
  }
  dbJobsValidate();
 }catch(error){if(error.code!=='AUTH_READ_STALE'&&current())dbJobsPurge();}
}
async function dbJobsRefresh(){
 const p=dbJobs;dbJobsCapture();const epoch=++p.epoch,request=++p.request,principal=dbPrincipal,role=p.role;
 const current=()=>epoch===p.epoch&&request===p.request&&principal===dbPrincipal;
 p.scheduleRefreshing=true;p.invalid=true;ui(p.status,'Loading…');dbJobsRender();
 try{
  const items=await dbReadInventory(role);if(!current()||!dbInventoryCurrent(items))return;dbApplyInventory(items,role);p.databases=items;
  const allowedDatabases=items.filter(d=>d.state==='READY'&&d.allowed_roles?.includes(role));
  const workspaceDatabase=dbCurrent()?.database_id||(typeof dbExplorer!=='undefined'?dbExplorer.database_id:'')||'';
  if(!allowedDatabases.some(d=>d.id===p.database)){
   const preferred=allowedDatabases.find(d=>d.id===workspaceDatabase);
   p.database=preferred?.id||'';
   if(!preferred)p.schema='';
  }
  const database=p.database;
  const result=database?await dbAPI('/api/v1/databases/'+encodeURIComponent(database)+'/schemas',role):{items:[]};
  if(!current()||!dbInventoryCurrent(items))return;p.schemas=result.items||[];p.principal=principal;
  if(database&&p.schema&&!p.schemas.some(s=>s.id===p.schema)){
   const workspaceSchema=dbCurrent()?.schema_id||(typeof dbExplorer!=='undefined'?dbExplorer.schema_id:'')||'';
   p.schema=p.schemas.some(s=>s.id===workspaceSchema)?workspaceSchema:'';
  }
  if(!database||!p.schema){p.context=null;p.invalid=true;ui(p.status,'');dbJobsRender();return;}
  p.context=dataopsFreeze(dataopsContext(p));p.invalid=!dbJobsAllowed();
  if(p.invalid){dbJobsPurge();return;}
  const reply=await dataopsRequest('/api/v1/jobs',p.context);if(!current()||!dbJobsAllowed())return;
  p.jobs=reply.jobs||[];p.history=[];
  const keep=p.job&&p.jobs.find(j=>j.id===p.job.id)||null;
  const draft=p.drafts.get(dbJobsDraftKey());
  if(p.view==='detail'||keep||draft)dbJobsEditor(keep);
  else{p.job=null;p.selection='';p.save=null;p.view='list';}
  ui(p.status,'Ready');dbJobsRender();
 }catch(error){if(current()){dbJobsPurge();ui(p.status,'Job access unavailable. Refresh an authorized context.');}}
 finally{if(current()){p.scheduleRefreshing=false;dbJobsRender();if(p.view==='list')void dbJobsListHistory();}}
}
async function dbJobsListHistory(){
 const p=dbJobs;if(p.invalid||!dbJobsAllowed())return;const epoch=p.epoch,request=++p.request,context=p.context;
 try{const reply=await dataopsRequest('/api/v1/jobs/runs'+(p.job?'?job_id='+encodeURIComponent(p.job.id):''),context);
  if(epoch!==p.epoch||request!==p.request||!dbJobsAllowed())return;p.history=reply.runs||[];dbJobsRender();
 }catch(error){if(epoch===p.epoch){if(dataopsAuthError(error))dbJobsPurge();else{ui(p.status,'Job request failed. Draft retained.');dbJobsRender();}}}
}
async function dbJobsLoad(currentRevision=false,requestedId=null){
 const p=dbJobs,id=requestedId||$('db-jobs-list').value;if(!id||p.pending||p.invalid)return;dbJobsCapture();const epoch=p.epoch,request=++p.request;
 const current=()=>epoch===p.epoch&&request===p.request&&(requestedId||$('db-jobs-list').value)===id;
 try{
  const listed=p.jobs.find(j=>j.id===id),context=dataopsFreeze(dataopsContext(p,listed?.context?.schema_id||p.schema));
  if(!dbJobsAllowed(context))throw Error('context');
  const key=dbJobsScope(context)+'/'+id,draft=p.drafts.get(key);
  if(currentRevision&&draft&&(!dbJobsMatches(draft.job,draft.values)||draft.values.arguments.trim()!=='{}')&&!confirm(t('Discard this Job draft and load the current server revision?')))return;
  const reply=await dataopsRequest('/api/v1/jobs/'+encodeURIComponent(id),context);
  if(!current()||!dbJobsAllowed(context))return;
  if(reply.job?.id!==id)throw Error('receipt');
  if(currentRevision){p.drafts.delete(key);p.jobs=p.jobs.map(job=>job.id===id?reply.job:job);}
  p.context=context;p.schema=context.schema_id;ui(p.status,'Ready');dbJobsEditor(reply.job);await dbJobsListHistory();
   if(typeof workbenchShowDetail==='function'){try{workbenchShowDetail('jobs',reply.job.id,{push:false});}catch{}}
 }catch(error){if(current()){if(dataopsAuthError(error))dbJobsPurge();else{ui(p.status,'Job request failed. Draft retained.');dbJobsRender();}}}
}
function dbJobsDefinition(){
 const p=dbJobs,definition={name:$('db-jobs-name').value,description:$('db-jobs-description').value,parameters:JSON.parse($('db-jobs-parameters').value),steps:JSON.parse($('db-jobs-steps').value)};
 if(!/^[a-z][a-z0-9_-]{0,63}$/.test(definition.name)||!definition.parameters||Array.isArray(definition.parameters)||typeof definition.parameters!=='object'||!Array.isArray(definition.steps)||definition.steps.length<1||definition.steps.length>50)throw Error('inputs');
 const names=new Set();for(const s of definition.steps){if(!s.name||names.has(s.name)||!['sql','alert','ingest','wap_refresh','table_export'].includes(s.action))throw Error('inputs');names.add(s.name);
  if(!['sql','alert'].includes(s.action)&&(p.role!=='admin'||!p.schemas.find(s=>s.id===p.schema)?.capabilities?.job_admin))throw Error('inputs');}
 return definition;
}
async function dbJobsSave(){
 const p=dbJobs;if(p.pending||p.invalid||!dbJobsAllowed()||!dbJobsOwn())return;
 let payload;try{payload={context:p.context,expected_revision:p.job?.revision||0,definition:dbJobsDefinition()};if(p.job)payload.job_id=p.job.id;}catch{ui(p.status,'Job inputs are invalid. Check name, JSON, revision and named files.');return;}
 const signature=JSON.stringify(payload);if(p.save?.signature!==signature)p.save={signature,key:crypto.randomUUID(),payload:dataopsFreeze(dataopsClone(payload))};
 const record=p.save,epoch=p.epoch;p.pending=true;dbJobsCapture();dbJobsRender();
 try{const reply=await dataopsRequest('/api/v1/jobs',record.payload.context,record.payload,record.key);
  if(epoch!==p.epoch||!dbJobsAllowed())return;
  const j=reply.job;if(!j||j.revision!==record.payload.expected_revision+1||j.selected_role!==p.role||j.owner!==JSON.parse(dbPrincipal)[0]||(p.job&&j.id!==p.job.id))throw Error('receipt');
  p.drafts.delete(dbJobsDraftKey());p.job=j;p.selection=j.id;p.view='detail';p.editOpen=false;p.save=null;$('db-jobs-list').value=j.id;dbJobsCapture();p.jobs=p.jobs.filter(job=>job.id!==j.id).concat(j);ui(p.status,'Job saved.');
   if(typeof workbenchShowDetail==='function'){try{workbenchShowDetail('jobs',j.id,{push:false});}catch{}}
 }catch(error){if(epoch===p.epoch){if(dataopsAuthError(error))dbJobsPurge();else ui(p.status,'Job request failed. Draft retained.');}}
 finally{p.pending=false;dbJobsRender();}
}
async function dbJobsRun(){
 const p=dbJobs;if(p.pending||p.tracking||p.invalid||!dbJobsAllowed()||!p.job||!dbJobsOwn()||(p.run&&!dbJobsTerminal(p.run)))return;
 if(!dbJobsMatches()){ui(p.status,'Save or load the definition before running.');dbJobsRender();return;}
 p.pending=true;const epoch=p.epoch;dbJobsRender();
 try{
  const argumentsValue=JSON.parse($('db-jobs-arguments').value);if(!argumentsValue||Array.isArray(argumentsValue)||typeof argumentsValue!=='object')throw Error('inputs');
  const inputs={};for(const row of p.files){const name=row.name.value,file=row.file.files?.[0];if(!name||!file||Object.hasOwn(inputs,name))throw Error('inputs');
   const bytes=new Uint8Array(await file.arrayBuffer());let raw='';for(const byte of bytes)raw+=String.fromCharCode(byte);inputs[name]={filename:file.name,content_base64:btoa(raw)};}
  if(epoch!==p.epoch||p.invalid||!dbJobsAllowed()||!dbJobsMatches())return;
  const context=dataopsFreeze(dataopsClone(p.context)),payload={context,expected_revision:p.job.revision,arguments:argumentsValue};if(Object.keys(inputs).length)payload.inputs=inputs;
  p.run={context,job_id:p.job.id,revision:p.job.revision,key:crypto.randomUUID(),payload:dataopsFreeze(payload),schemas:dataopsClone(p.schemas),principal:dbPrincipal,status:'unknown',details:null,id:null,sequence:0,purged:false};
  await dbJobsSendRun(p.run,'submit');
 }catch{if(epoch===p.epoch)ui(p.status,'Job inputs are invalid. Check name, JSON, revision and named files.');}
 finally{p.pending=false;dbJobsRender();}
}
function dbJobsCanRetry(r){return !!r&&!r.id&&!!r.payload&&!!r.key;}
async function dbJobsSendRun(r,action){
 const submission=action==='submit'||action==='retry';
 if(submission?!dbJobsCanRetry(r):!r.id||!['observe','cancel','reconcile'].includes(action))return;
 const p=dbJobs,sequence=++r.sequence,epoch=p.epoch;
 const freshSubmission=action==='submit'&&sequence===1;
 try{
  const path=submission?'/api/v1/jobs/'+encodeURIComponent(r.job_id)+'/runs'
   :'/api/v1/jobs/runs/'+encodeURIComponent(r.id)+(action==='observe'?'':'/'+action);
  const reply=await dataopsRequest(path,r.context,submission?r.payload:action==='observe'?undefined:{},submission?r.key:undefined);
  if(sequence!==r.sequence||p.run!==r)return;
  const run=reply.run;
  if(!run?.id||(r.id&&run.id!==r.id)||!['queued','running','succeeded','failed','canceled','unknown'].includes(run.status))throw Error('receipt');
  if(action==='cancel'){
   r.status=run.status;
   if(epoch===p.epoch)ui(p.status,r.status);
   return true;
  }
  if(run.job_id!==r.job_id||run.revision!==r.revision||run.owner!==JSON.parse(r.principal)[0]||run.selected_role!==r.context.selected_role||!dataopsSameContext(run.context,r.context))throw Error('receipt');
  r.id=run.id;r.status=run.status;
  // Late acceptance may supply only the original cancellation handle after loss.
  r.details=!r.purged&&epoch===p.epoch&&r.principal===dbPrincipal&&dbJobsAllowed(r.context,r.schemas)?run:null;
  r.payload=null;
  if(epoch===p.epoch)ui(p.status,r.status==='unknown'?'The saved run is UNKNOWN. Reconciliation checks backend evidence.':r.status);
  return true;
 }catch(error){if(sequence!==r.sequence)return;
  if(freshSubmission&&p.run===r&&!r.id&&!r.details&&!r.purged&&epoch===p.epoch&&r.principal===dbPrincipal&&error.status===400&&error.code==='JOB_INPUT_REJECTED'&&error.details?.admission==='NOT_ATTEMPTED'&&error.jobRejectionOnly&&!error.observation){
   r.payload=null;p.run=null;ui(p.status,'Job inputs rejected before admission. Correct the inputs and explicitly run again.');return;
  }
  if(!r.id)r.status='unknown';if(dataopsAuthError(error))dbJobsPurge();
  else if(epoch===p.epoch)ui(p.status,submission?'Job acceptance is unconfirmed.'
   :action==='observe'?'Run status check failed. Showing the last known status. Retry the read-only check.'
   :'Run action failed. Check the saved run status before trying again.');}
}
async function dbJobsObserve(action='observe',automatic=false){
 const p=dbJobs,r=p.run;if(!r||p.pending||p.tracking||r.principal!==dbPrincipal)return;
 if(automatic&&action!=='observe'||!['observe','retry','cancel','reconcile'].includes(action))return;
 if(action==='retry'?!dbJobsCanRetry(r):!r.id)return;
 if(action==='cancel'?dbJobsTerminal(r):r.purged||!dbJobsAllowed(r.context,r.schemas))return;
 if(action==='reconcile'&&r.status!=='unknown')return;
 dbJobsSuspend();p.tracking=true;p.polling=automatic;dbJobsRender();try{const observed=await dbJobsSendRun(r,action);if(action==='observe'&&!observed)p.pollFailed=r;else if(observed)p.pollFailed=null;}finally{p.tracking=false;p.polling=false;dbJobsRender();}
}
function dbJobsRender(){
 const p=dbJobs;if(!p.installed)return;dbJobsSyncPolling();const allowed=!p.invalid&&dbJobsAllowed(),waiting=p.pending||p.scheduleRefreshing||(p.tracking&&!p.polling),own=dbJobsOwn();
 dbScheduleSync('job');
 if($('db-jobs-list-panel'))$('db-jobs-list-panel').hidden=p.view!=='list';
 if($('db-jobs-saved-panel'))$('db-jobs-saved-panel').hidden=!allowed;
 if($('db-jobs-edit-details'))$('db-jobs-edit-details').hidden=!allowed||p.view!=='list';
 if($('db-jobs-detail'))$('db-jobs-detail').hidden=p.view!=='detail';
 if($('db-schedule-job-panel'))$('db-schedule-job-panel').hidden=p.view!=='list'||!p.editOpen;
 if($('db-jobs-back'))$('db-jobs-back').hidden=p.view!=='detail';
 if($('db-jobs-new'))$('db-jobs-new').className=p.view==='list'?'primary':'';
 const editDetails=$('db-jobs-edit-details');if(editDetails)editDetails.open=!!p.editOpen;
 const note=$('db-jobs-heading-note');if(note)ui(note,'Latest 100 runs');
 dbJobsRenderList();
 const run=dbJobsActiveRun();
 const title=$('db-jobs-detail-title'),detailStatus=$('db-jobs-detail-status'),eyebrow=$('db-jobs-eyebrow');
 if(title)title.textContent=p.job?.definition.name||t(p.view==='detail'?'New job':'No job selected.');
 if(eyebrow){
  if(p.job){const start=run?.started_at||run?.created_at;const identity=start?fmtJST(start):t('Revision {revision}',{revision:p.job.revision});ui(eyebrow,'Run detail / {identity}',{identity});}
  else ui(eyebrow,'Job definition / New job');
 }
 if(detailStatus){detailStatus.hidden=!p.job;if(p.job){detailStatus.className='status '+(run?dbJobsStatusClass(run.status):'');ui(detailStatus,run?dbJobsStatusKey(run.status):'Revision {revision}',{revision:p.job.revision});}}
 dbJobsRenderFacts(run);dbJobsRenderOutput(run);dbJobsRenderCode();dbJobsRenderSteps();
 const stepNote=$('db-jobs-step-note');if(stepNote){if(run)stepNote.textContent=t('State')+': '+t(dbJobsStatusKey(run.status));else ui(stepNote,'Definition only; run steps appear after a run.');}
 const roles=['data_engineer','admin'].filter(dbRoleWithinHeader),databases=dbReadyDatabases(p.role),schemas=p.schemas||[];
 dataopsOptions($('db-jobs-role'),roles.map(r=>[r,dbRoleDisplayLabel(r)]),roles.includes(p.role)?p.role:'','Execution role');
 dataopsOptions($('db-jobs-database'),p.scheduleRefreshing?[]:databases.map(d=>[d.id,d.display_name+' · '+d.sql_catalog]),databases.some(d=>d.id===p.database)?p.database:'',p.scheduleRefreshing?'Loading…':'Choose database');
 dataopsOptions($('db-jobs-schema'),p.scheduleRefreshing?[]:schemas.map(s=>[s.id,s.display_name+' · '+s.sql_name]),schemas.some(s=>s.id===p.schema)?p.schema:'',p.scheduleRefreshing?'Loading…':'Choose schema');
 dataopsOptions($('db-jobs-list'),(allowed?p.jobs:[]).map(j=>[j.id,j.definition?.name+' · v'+j.revision]),allowed&&p.jobs.some(j=>j.id===p.selection)?p.selection:'','Saved jobs');
 for(const n of p.form.querySelectorAll('input,select,textarea,button'))n.disabled=waiting||!allowed||!own;
 for(const k of ['role','database','schema','refresh','list','load','reload','new'])$('db-jobs-'+k).disabled=waiting;
 $('db-jobs-save').disabled=waiting||!allowed||!own;
 $('db-jobs-run').disabled=waiting||!allowed||!own||!p.job||!dbJobsMatches()||!!(p.run&&!dbJobsTerminal(p.run));
 const dirty=allowed&&own&&p.job&&!dbJobsMatches(),status=p.status.textContent,hint='Save or load the definition before running.';
 if(dirty&&['Ready','Job saved.',hint].map(key=>t(key)).includes(status))ui(p.status,hint);
 else if(!dirty&&status===t(hint))ui(p.status,'Ready');
 $('db-jobs-owner').textContent=p.job?[t('Owner')+': '+dbJobsSafeName(p.job.owner),t('Saved role')+': '+dbRoleDisplayLabel(p.job.selected_role),t('Revision')+': '+p.job.revision].join(' · '):'';
 if(allowed&&!own)ui(p.status,'Job definition belongs to another owner or saved role.');
 const r=p.run,sameActor=r?.principal===dbPrincipal;
 $('db-jobs-observe').disabled=waiting||p.tracking||!r||r.purged||!sameActor||!dbJobsAllowed(r.context,r.schemas)||!r.id;
 $('db-jobs-retry').hidden=p.pending||!dbJobsCanRetry(r)||r.purged||!sameActor;
 $('db-jobs-retry').disabled=waiting||p.tracking||$('db-jobs-retry').hidden||!dbJobsAllowed(r.context,r.schemas);
 const retryHelp=$('db-jobs-retry-help');if(retryHelp)retryHelp.hidden=$('db-jobs-retry').hidden;
 $('db-jobs-cancel').disabled=waiting||p.tracking||!r?.id||!sameActor||dbJobsTerminal(r);
 $('db-jobs-reconcile').disabled=waiting||p.tracking||!r?.id||!sameActor||r.purged||r.status!=='unknown'||!dbJobsAllowed(r.context,r.schemas);
 const authorizedRun=r&&!r.purged&&sameActor&&dbJobsAllowed(r.context,r.schemas);
 const identity=JSON.stringify([dbPrincipal,currentRole,p.role,p.database,p.schema,allowed?p.context:null,allowed?p.job?.id:null,authorizedRun?[r.context,r.job_id,r.id||r.key]:null]);
 for(const id of ['db-jobs-definition-help','db-jobs-input-help']){const node=$(id);if(node.dataset.identity!==identity){node.open=false;node.dataset.identity=identity;}}
 const line=(label,value,parent)=>{const row=dataopsNode('p','',parent);dataopsNode('strong','',row,label);dataopsNode('span','',row).textContent=': '+String(value??'');};
 $('db-jobs-current').textContent=t('Current job')+': '+(allowed&&p.job?p.job.definition.name+' · '+t('Revision')+' '+p.job.revision:t('No job selected.'));
 $('db-jobs-context').textContent=allowed?[p.context.sql_catalog,p.context.sql_schema,dbRoleDisplayLabel(p.context.selected_role)].join(' / '):'';
 let next=p.scheduleRefreshing?'Loading…':waiting?'Wait for the current Job request to finish.':!allowed?'Job access unavailable. Refresh an authorized context.':!own?'Job definition belongs to another owner or saved role.':!p.job?'Load a saved Job or fill in and save a new Job.':!dbJobsMatches()?'Save or load the definition before running.':'Review the saved Job and inputs, then run.';
 if(!waiting&&r&&!dbJobsTerminal(r))next=r.purged?'Run details cleared after access changed. Original cancellation handle retained.':r.id?r.status==='unknown'?'The saved run is UNKNOWN. Reconciliation checks backend evidence.':'Observe the saved run to check its status.':'Job acceptance is unconfirmed.';
 else if(!waiting&&authorizedRun&&dbJobsTerminal(r)&&allowed&&own&&p.job&&dbJobsMatches())next=r.status==='failed'?'Inspect the failed step before starting another run.':'Review the run result. Check the saved Job and inputs before running again.';
 $('db-jobs-next').textContent=t('Next action')+': '+t(next);
 // Localize handler status tokens without changing request state or failure notices.
 if(p.status.textContent===status&&['queued','running','succeeded','failed','canceled','unknown'].includes(status))ui(p.status,status==='canceled'?'canceled':jobStatuses[status]||'Unknown outcome');
 const neutralStatus=!p.status.textContent||p.status.textContent===t('Ready');
 p.status.hidden=p.view==='detail'||neutralStatus;
 const runStatus=$('db-jobs-run-status');
 if(runStatus){runStatus.hidden=p.view!=='detail'||neutralStatus;runStatus.className=p.status.className;runStatus.textContent=p.status.textContent;}
 const area=$('db-jobs-run-detail');dbJobsResetDetails(area,identity);
 const steps=(run,parent,key)=>{for(const [index,step] of (run.steps||[]).entries()){
  line('Step',step.name+' · '+t(typeof jobActionLabels!=='undefined'?jobActionLabels[step.action]||step.action:step.action==='table_export'?'Table export':step.action)+' · '+dbJobsStatus(step.status),parent);
  const facts=Object.entries(step.summary||{}).filter(([k,v])=>/^(code|error|error_code|count|.*_count|rows|.*_rows|triggered|status)$/.test(k)&&['string','number','boolean'].includes(typeof v));
  const labels={code:'Failure code',error:'Error',error_code:'Error code',row_count:'Rows',rows:'Rows',count:'Count',rows_ingested:'Rows ingested',committed_rows:'Committed rows',model_count:'Model count',triggered:'Triggered',status:'Status'};
  for(const [key,value] of facts)line(labels[key]||key,value,parent);
  if(step.summary?.partial===true)line('Result',t('Partially completed'),parent);
  const extra=dbJobsDetails(parent,key+':step:'+index,'Step diagnostics');
  line('Operation IDs',(step.operation_ids||[]).join(', '),extra);line('Query IDs',(step.queries||[]).map(q=>q.id+' · '+t(q.state)).join(', '),extra);line('Summary',JSON.stringify(step.summary??{}),extra);
 }};
 if(r){line('Status',dbJobsStatus(r.status),area);
  if(r.purged)dataopsNode('p','',area,'Run details cleared after access changed. Original cancellation handle retained.');
  else if(authorizedRun){
   line('Job',r.details?.job_name||(p.job?.id===r.job_id?p.job.definition.name:t('Saved Jobs')),area);line('Execution context',[r.context.sql_catalog,r.context.sql_schema,dbRoleDisplayLabel(r.context.selected_role),t('Revision')+' '+r.revision].join(' / '),area);
   if(r.id)dataopsNode('p','',area,'Checking status only reads the saved run. It does not run the Job.').className='help';
   else if(!$('db-jobs-retry').hidden)dataopsNode('p','db-jobs-retry-help',area,'Retrying sends the original inputs and key and may start the Job if it was not accepted.').className='help';
   if(r.status==='unknown'&&r.id)dataopsNode('p','',area,'The saved run is UNKNOWN. Reconciliation checks backend evidence.');
   const extra=dbJobsDetails(area,'metadata','Job run metadata');line('Run ID',r.id||'unknown',extra);line('Job',r.job_id,extra);line('Execution context',JSON.stringify(r.context),extra);line('Owner',r.details?.owner||'',extra);
   if(r.details)steps(r.details,area,'current');
  }
 }
 const history=$('db-jobs-history');dbJobsResetDetails(history,identity);if(allowed)for(const run of p.history){
  const row=dataopsNode('div','',history);line('Job',run.job_name,row);line('Status',dbJobsStatus(run.status),row);line('Started',run.created_at?fmtJST(run.created_at):'—',row);line('Revision',run.revision,row);
  dataopsButton(row,'','Observe run',()=>{
   if(p.pending||p.tracking||p.invalid||!dbJobsAllowed()||(p.run&&!dbJobsTerminal(p.run)&&p.run.id!==run.id))return;
   if(p.run?.id===run.id&&!p.run.purged&&dbJobsAllowed(p.run.context,p.run.schemas))return dbJobsObserve();
   p.run={id:run.id,details:run,job_id:run.job_id,revision:run.revision,context:dataopsFreeze(dataopsClone(run.context)),schemas:dataopsClone(p.schemas),principal:dbPrincipal,status:run.status,sequence:0,purged:false};
   return dbJobsObserve();
  });
  dbJobsDisclosures.set(row,dbJobsDisclosures.get(history));const extra=dbJobsDetails(row,'run:'+run.id,'Run diagnostics');
  line('Run ID',run.id,extra);line('Execution context',JSON.stringify(run.context),extra);
  dbJobsDisclosures.set(extra,dbJobsDisclosures.get(history));steps(run,extra,'history:'+run.id);
 }
}
function dbJobsLabeledField(parent,id,labelKey,tag='select'){
 const label=document.createElement('label');label.className='atlas-field-label';label.htmlFor=id;
 const span=document.createElement('span');span.className='atlas-label';ui(span,labelKey);
 const node=document.createElement(tag);node.id=id;node.className='field';
 label.append(span,node);parent.append(label);return node;
}
function dbJobsStatusClass(status){return status==='succeeded'?'success':['failed','unknown'].includes(status)?'failed':'warning';}
function dbJobsSafeName(value){const text=String(value??'').trim();return text&&!dbLooksLikeOpaqueId(text)?text:'—';}
function dbJobsRunEnvelope(run){
 return {id:run.id,details:run,job_id:run.job_id,revision:run.revision,context:dataopsFreeze(dataopsClone(run.context)),schemas:dataopsClone(dbJobs.schemas),principal:dbPrincipal,status:run.status,sequence:0,purged:false};
}
function dbJobsActiveRun(job=dbJobs.job){
 if(!job)return null;
 const p=dbJobs;
 if(p.run&&p.run.job_id===job.id&&!p.run.purged)return p.run.details||p.run;
 return (p.history||[]).find(run=>run.job_id===job.id)||null;
}
// Ingest receipts name their target only by ResourceRef; look the name up once
// through the authorized object detail, then repaint.
const dbJobsTargetNames=new Map();
function dbJobsTargetName(ref,role){
 const key=[role,ref.object_id,ref.incarnation].join(':');
 if(dbJobsTargetNames.has(key))return dbJobsTargetNames.get(key);
 dbJobsTargetNames.set(key,null);
 void dbAPI('/api/v1/objects/'+encodeURIComponent(ref.object_id)+'?detail=columns',role).then(reply=>{
  const object=reply?.object,current=object?.ref||reply?.ref;
  if(current?.object_id!==ref.object_id||current?.incarnation!==ref.incarnation||typeof object.physical_name!=='string')return;
  dbJobsTargetNames.set(key,object.physical_name);
  if(dbJobs.installed)dbJobsRender();
 }).catch(()=>{});
 return null;
}
function dbJobsRefOutput(ref,run){
 if(!ref||typeof ref!=='object'||typeof ref.object_id!=='string'||!Number.isInteger(ref.incarnation))return null;
 const context=run?.context||{};
 const database=ref.database_id===context.database_id?context.sql_catalog:dbDatabases.find(item=>item.id===ref.database_id)?.sql_catalog;
 const schema=ref.schema_id===context.schema_id?context.sql_schema:(dbJobs.schemas||[]).find(item=>item.id===ref.schema_id)?.sql_name;
 if(!database||!schema)return null;
 const name=dbJobsTargetName(ref,run?.selected_role||dbJobs.role);
 if(!name)return null;
 return {name:[database,schema,name].join('.'),href:'#explore/'+dbExplorePath({sql_catalog:database},{sql_name:schema},{physical_name:name})};
}
function dbJobsRunOutput(run){
 for(const step of run?.steps||[]){
  const summary=step?.summary;if(!summary||typeof summary!=='object')continue;
  const fromRef=dbJobsRefOutput(summary.target,run);if(fromRef)return fromRef;
  for(const key of ['target_name','table','destination_name','target']){
   const value=summary[key];
   if(typeof value!=='string'||!value.trim()||value.split('.').length<3)continue;
   const parts=value.split('.');if(parts.some(part=>dbLooksLikeOpaqueId(part)))continue;
   return {name:value,href:'#explore/'+parts.map(encodeURIComponent).join('.')};
  }
 }
 return null;
}
function dbJobsRunOutputRows(run){
 for(const step of run?.steps||[]){
  const summary=step?.summary;if(!summary||typeof summary!=='object')continue;
  for(const key of ['committed_rows','row_count','rows','count'])if(Number.isFinite(summary[key]))return summary[key];
 }
 return null;
}
function dbJobsRunDuration(run){
 const start=run?.started_at||run?.created_at,end=run?.finished_at;
 if(!start||!end)return '—';
 const milliseconds=Date.parse(end)-Date.parse(start);
 if(!Number.isFinite(milliseconds)||milliseconds<0)return '—';
 return t('{seconds} s',{seconds:(milliseconds/1000).toFixed(1)});
}
function dbJobsStepFacts(step){
 const labels={code:'Failure code',error:'Error',error_code:'Error code',count:'Count',row_count:'Rows',rows:'Rows',committed_rows:'Committed rows',rows_ingested:'Rows ingested',model_count:'Model count',triggered:'Triggered',status:'Status'};
 const parts=[];
 for(const [key,value] of Object.entries(step?.summary||{}))if(/^(code|error|error_code|count|.*_count|rows|.*_rows|triggered|status)$/.test(key)&&['string','number','boolean'].includes(typeof value))parts.push(t(labels[key]||'Status')+': '+String(value));
 if(step?.summary?.partial===true)parts.push(t('Partially completed'));
 return parts.join(' · ');
}
function dbJobsRunIsHistorical(run,job=dbJobs.job){
 return !!(run&&job&&Number.isInteger(run.revision)&&run.revision!==job.revision);
}
function dbJobsRenderFacts(run){
 const host=$('db-jobs-facts');if(!host)return;
 host.replaceChildren();
 const add=(key,value)=>{const dt=document.createElement('dt'),dd=document.createElement('dd');ui(dt,key);dd.textContent=value==null||value===''?'—':String(value);host.append(dt,dd);};
 const job=dbJobs.job;
 if(!job){add('Job','—');return;}
 const start=run?.started_at||run?.created_at;
 add('Started',start?fmtJST(start):'—');
 add('Duration',dbJobsRunDuration(run));
 add('Executor',dbJobsSafeName(run?.owner||job.owner));
 add('Execution role',dbRoleDisplayLabel(run?.selected_role||job.selected_role));
 add('Revision','v'+(Number.isInteger(run?.revision)?run.revision:job.revision));
 const context=run?.context||job.context;
 if(context&&(context.sql_catalog||context.sql_schema))add('Execution context',[context.sql_catalog,context.sql_schema].filter(Boolean).join(' / '));
}
function dbJobsRenderOutput(run){
 const name=$('db-jobs-output-name'),subtitle=$('db-jobs-output-context'),facts=$('db-jobs-output-facts'),link=$('db-jobs-output-link');
 if(!name||!subtitle||!facts||!link)return;
 const output=dbJobsRunOutput(run),rows=dbJobsRunOutputRows(run),job=dbJobs.job,context=run?.context||job?.context;
 name.textContent=output?output.name:'—';
 if(output)subtitle.textContent=[context?.sql_catalog,context?.sql_schema].filter(Boolean).join(' / ')||'—';
 else ui(subtitle,'No output table.');
 facts.replaceChildren();
 const add=(key,value)=>{const dt=document.createElement('dt'),dd=document.createElement('dd');ui(dt,key);dd.textContent=value==null?'—':String(value);facts.append(dt,dd);};
 add('State',run?t(dbJobsStatusKey(run.status)):'—');
 add('Rows written',rows);
 add('Database',context?.sql_catalog||'—');
 add('Execution role',dbRoleDisplayLabel(run?.selected_role||job?.selected_role));
 link.hidden=!output;
 if(output){link.href=output.href;ui(link,'View output table');}
}
function dbJobsRenderCode(){
 const node=$('db-jobs-code'),note=$('db-jobs-code-note');if(!node)return;
 let steps;
 try{steps=JSON.parse($('db-jobs-steps')?.value||'[]');}catch{node.textContent=t('Step JSON is invalid. Fix it before saving.');return;}
 if(!Array.isArray(steps)||!steps.length){node.textContent=t('No definition loaded.');return;}
 const sql=steps.filter(step=>['sql','alert'].includes(step?.action)&&typeof step.sql==='string').map(step=>'-- '+(step.name||'')+'\n'+step.sql).join('\n\n');
 node.textContent=sql||JSON.stringify(steps,null,2);
 if(!note)return;
 const run=dbJobsActiveRun();
 if(dbJobsRunIsHistorical(run))ui(note,'Current definition, revision {revision}; this run used revision {run}',{revision:dbJobs.job.revision,run:run.revision});
 else if(dbJobs.job)ui(note,'Revision {revision}',{revision:dbJobs.job.revision});else ui(note,'Definition');
}
function dbJobsContextReason(allowed){
 const p=dbJobs;
 if(p.scheduleRefreshing)return 'Refreshing the Jobs context…';
 if(!dbRoleWithinHeader(p.role))return 'This role cannot use Jobs.';
 const databases=dbReadyDatabases(p.role);
 if(!databases.length)return 'No available database. Check database states in Database Management.';
 if(!p.database)return 'Choose a database to load jobs.';
 if(!databases.some(d=>d.id===p.database))return 'Choose an available database.';
 if(!p.schemas?.length)return 'No available schema in this database.';
 if(!p.schema)return 'Choose a schema to load jobs.';
 if(!p.schemas.some(s=>s.id===p.schema))return 'Choose an available schema.';
 if(!allowed)return 'Job access unavailable. Refresh an authorized context.';
 return '';
}
function dbJobsInstall(){
 const p=dbJobs;if(p.installed)return;p.installed=true;
 if(typeof document.addEventListener==='function')document.addEventListener('visibilitychange',()=>{dbJobsSuspend();if(!document.hidden)dbJobsSyncPolling();});
 p.principal=dbPrincipal;p.role=dbCurrent()?.selected_role||currentRole;p.database=dbCurrent()?.database_id||(typeof dbExplorer!=='undefined'?dbExplorer.database_id:'')||'';p.schema=dbCurrent()?.schema_id||(typeof dbExplorer!=='undefined'?dbExplorer.schema_id:'')||'';p.schemas=[];p.databases=dbRoleItems(p.role);p.files=[];p.view='list';
 for(const child of $('jobs').children)child.hidden=true;
 const legacyError=$('jobs-error');if(legacyError){legacyError.textContent='';legacyError.hidden=true;}
 const root=dataopsNode('section','db-jobs-panel',$('jobs'));root.className='jobs-page';
 const listView=dataopsNode('div','db-jobs-list-panel',root);
 const heading=dataopsNode('div','db-jobs-pageheading',listView);heading.className='pageheading';
 const headingText=dataopsNode('div','',heading);
 const h1=dataopsNode('h1','db-jobs-title',headingText,'Jobs');h1.tabIndex=-1;
 dataopsNode('span','db-jobs-heading-note',heading,'Latest 100 runs').className='tiny muted';
 const selectors=dataopsNode('div','db-jobs-context-fields',listView);selectors.className='atlas-fields';
 for(const [key,label] of [['role','Execution role'],['database','Database'],['schema','Schema']]){
  const node=dbJobsLabeledField(selectors,'db-jobs-'+key,label,'select');node.onchange=()=>{dbJobsCapture();p[key]=node.value;p.job=null;p.selection='';p.save=null;p.history=[];p.jobs=[];p.context=null;p.invalid=true;for(const k of dbJobsFields)$('db-jobs-'+k).value='';if(key==='database')p.schema='';return dbJobsRefresh();};
 }
 dataopsButton(selectors,'db-jobs-refresh','Refresh Jobs',dbJobsRefresh);
 p.status=dataopsNode('p','db-jobs-status',listView);p.status.className='status-line';p.status.setAttribute('role','status');p.status.setAttribute('aria-live','polite');
 const recent=dataopsNode('section','db-jobs-recent',listView);recent.className='panel';
 const recentHead=dataopsNode('div','',recent);recentHead.className='panelhead';
 dataopsNode('h2','',recentHead,'Recent runs');
 dataopsNode('span','db-jobs-recent-note',recentHead,'Latest 100 runs').className='tiny muted';
 const recentRows=dataopsNode('div','db-jobs-recent-runs',recent);recentRows.className='tablewrap';recentRows.tabIndex=0;recentRows.setAttribute('role','region');recentRows.setAttribute('aria-labelledby','db-jobs-recent-caption');
 const hint=dataopsNode('p','db-jobs-hint',listView,'Open the run detail to inspect steps and output data.');hint.className='sqlhint';
 const savedPanel=dataopsNode('section','db-jobs-saved-panel',listView);savedPanel.className='panel';
 const savedHead=dataopsNode('div','',savedPanel);savedHead.className='panelhead';
 dataopsNode('h2','',savedHead,'Saved jobs');
 const listSummary=dataopsNode('span','db-jobs-list-summary',savedHead);listSummary.className='tiny muted';
 dataopsButton(savedHead,'db-jobs-new','New job',()=>dbJobsNew());
 const savedBody=dataopsNode('div','',savedPanel);savedBody.className='panelbody';
 const listRows=dataopsNode('div','db-jobs-list-rows',savedBody);listRows.className='objectrows';
 dataopsNode('p','db-jobs-registration-help',savedBody,'Use New job in the saved jobs panel to create the first job.').className='help';
 const savedDetails=dataopsNode('details','db-jobs-saved-details',savedBody);savedDetails.className='workflow-details';savedDetails.open=false;
 dataopsNode('summary','',savedDetails,'Saved job selector');
 const saved=dataopsNode('div','',savedDetails);saved.className='toolbar jobs-saved-toolbar';
 const savedSelect=dbJobsLabeledField(saved,'db-jobs-list','Saved jobs','select');savedSelect.onchange=()=>{p.selection=$('db-jobs-list').value;};
 dataopsButton(saved,'db-jobs-load','Load Job',()=>{p.editOpen=false;return dbJobsLoad();});
 dataopsButton(saved,'db-jobs-reload','Load current revision',()=>{p.editOpen=false;return dbJobsLoad(true);});
 dataopsButton(saved,'db-jobs-edit','Edit job',()=>dbJobsEdit());
 const editDetails=dataopsNode('details','db-jobs-edit-details',listView);editDetails.className='jobs-edit-details';editDetails.open=false;
 dataopsNode('summary','',editDetails,'Job registration and edit');

 const detailView=dataopsNode('div','db-jobs-detail',root);
 const back=dataopsNode('a','db-jobs-back',detailView,'← Job list');back.className='backlink';back.href='#jobs';back.hidden=true;back.onclick=event=>{event?.preventDefault?.();dbJobsShowList();};
 const detailPage=dataopsNode('div','',detailView);detailPage.className='pageheading';
 const detailPageText=dataopsNode('div','',detailPage);
 dataopsNode('div','db-jobs-eyebrow',detailPageText,'').className='eyebrow';
 const detailTitle=dataopsNode('h1','db-jobs-detail-title',detailPageText,'No job selected.');detailTitle.tabIndex=-1;
 const detailStatus=dataopsNode('span','db-jobs-detail-status',detailPage);detailStatus.className='status';detailStatus.hidden=true;
 const grid=dataopsNode('div','',detailView);grid.className='jobdetailgrid';
 const left=dataopsNode('div','',grid),right=dataopsNode('div','',grid);
 const stepsView=dataopsNode('section','db-jobs-steps-view',left);stepsView.className='panel';stepsView.setAttribute('aria-labelledby','db-jobs-steps-title');
 const stepsHead=dataopsNode('div','',stepsView);stepsHead.className='panelhead';
 dataopsNode('h2','db-jobs-steps-title',stepsHead,'Processing steps');
 dataopsNode('span','db-jobs-step-note',stepsHead,'Definition only; run steps appear after a run.').className='tiny muted';
 const stepsBody=dataopsNode('div','',stepsView);stepsBody.className='panelbody';
 dataopsNode('div','db-jobs-steps-list',stepsBody);
 const codePanel=dataopsNode('section','db-jobs-code-panel',left);codePanel.className='panel';
 const codeHead=dataopsNode('div','',codePanel);codeHead.className='panelhead';
 dataopsNode('h2','',codeHead,'Processing content');
 dataopsNode('span','db-jobs-code-note',codeHead,'Definition').className='tiny muted';
 dataopsNode('pre','db-jobs-code',codePanel,'No definition loaded.').className='codeblock';
 const runPanel=dataopsNode('section','db-jobs-run-panel',left);runPanel.className='panel';
 const runHead=dataopsNode('div','',runPanel);runHead.className='panelhead';dataopsNode('h2','',runHead,'Run operations');
 const runBody=dataopsNode('div','',runPanel);runBody.className='panelbody';
 dataopsNode('p','db-jobs-current',runBody);dataopsNode('p','db-jobs-context',runBody).className='help';
 const runStatus=dataopsNode('p','db-jobs-run-status',runBody);runStatus.className='status-line';runStatus.setAttribute('role','status');runStatus.setAttribute('aria-live','polite');
 dataopsNode('p','db-jobs-next',runBody).setAttribute('role','status');
 const controls=dataopsNode('div','',runBody);controls.className='toolbar jobs-control-toolbar';for(const [id,label,action] of [['observe','Observe run','observe'],['retry','Retry original Job request','retry'],['cancel','Cancel run','cancel'],['reconcile','Reconcile unknown run','reconcile']])dataopsButton(controls,'db-jobs-'+id,label,()=>dbJobsObserve(action));
 dataopsNode('p','db-jobs-retry-help',runBody,'Retrying sends the original inputs and key and may start the Job if it was not accepted.').className='help';
 $('db-jobs-retry').setAttribute('aria-describedby','db-jobs-retry-help');
 dataopsNode('div','db-jobs-run-detail',runBody).setAttribute('aria-live','polite');
 const target=dataopsNode('section','db-jobs-output',right);target.className='target';
 dataopsNode('div','db-jobs-output-eyebrow',target,'Output data').className='eyebrow';
 dataopsNode('h2','db-jobs-output-name',target,'—');
 dataopsNode('p','db-jobs-output-context',target,'No output table.').className='subtitle';
 dataopsNode('dl','db-jobs-output-facts',target).className='kv';
 const outputLink=dataopsNode('a','db-jobs-output-link',target,'View output table');outputLink.className='primary full';outputLink.hidden=true;
 const factsPanel=dataopsNode('section','db-jobs-facts-panel',right);factsPanel.className='panel';
 const factsHead=dataopsNode('div','',factsPanel);factsHead.className='panelhead';dataopsNode('h2','',factsHead,'Run facts');
 const factsBody=dataopsNode('div','',factsPanel);factsBody.className='panelbody';dataopsNode('dl','db-jobs-facts',factsBody).className='kv';

 const editorPanel=dataopsNode('section','db-jobs-editor-panel',editDetails);editorPanel.className='panel';
 const editorHead=dataopsNode('div','',editorPanel);editorHead.className='panelhead';
 dataopsNode('h2','',editorHead,'Job registration');
 const editorState=dataopsNode('span','job-editor-state',editorHead,'New definition');editorState.className='badge';editorState.setAttribute('role','status');
 const editorBody=dataopsNode('div','',editorPanel);editorBody.className='panelbody';
 p.form=dataopsNode('form','db-jobs-form',editorBody);p.form.onsubmit=e=>e.preventDefault();
 dataopsNode('p','db-jobs-owner',p.form).className='help';const fields=dataopsNode('div','',p.form);fields.className='atlas-fields';
 for(const [key,label,tag] of [['name','Job name','input'],['description','Job description','input'],['parameters','Parameters JSON','textarea'],['steps','Ordered steps JSON','textarea'],['arguments','Arguments JSON','textarea']]){
  const node=dbJobsLabeledField(fields,'db-jobs-'+key,label,tag);if(tag==='textarea'){node.rows=key==='steps'?12:5;node.spellcheck=false;node.parentElement.className='atlas-field-label job-json';}node.oninput=()=>{dbJobsCapture();dbJobsRender();};
 }
 const definitionHelp=dataopsNode('details','db-jobs-definition-help',p.form);definitionHelp.className='workflow-details';definitionHelp.open=false;dataopsNode('summary','',definitionHelp,'Job definition and action help');
 dataopsNode('p','',definitionHelp,'SQL and alert are available to engineers. Ingest, WAP refresh and table export require admin. Steps run in array order.').className='help';
 dataopsNode('p','',definitionHelp,'SQL: sql and mode. Alert: read-only Boolean sql. Ingest: input, mode and target or destination. WAP refresh: model_id and version. Table export: source and format (csv/jsonl).').className='help';
 const inputHelp=dataopsNode('details','db-jobs-input-help',p.form);inputHelp.className='workflow-details';inputHelp.open=false;dataopsNode('summary','',inputHelp,'Parameter and input help');
 dataopsNode('p','',inputHelp,'Provide parameter definitions and typed arguments as JSON. Files use the input name referenced by an ingest step.').className='help';
 dataopsNode('div','db-jobs-files',p.form);dataopsButton(p.form,'db-jobs-add-input','Add named input',()=>{if(p.pending||p.invalid)return;const files=$('db-jobs-files');const row=dataopsNode('div','',files);row.className='toolbar jobs-input-row';const id=crypto.randomUUID(),name=dataopsField(row,'db-job-input-name-'+id,'Input name'),file=dataopsField(row,'db-job-input-file-'+id,'Input file');file.type='file';const record={name,file,row};p.files.push(record);dataopsButton(row,'','Remove',()=>{if(p.pending)return;file.value='';name.value='';p.files=p.files.filter(r=>r!==record);files.replaceChildren(...p.files.map(r=>r.row));});});
 const actions=dataopsNode('div','',p.form);actions.className='toolbar jobs-actions';dataopsButton(actions,'db-jobs-save','Save Job',dbJobsSave).className='primary';dataopsButton(actions,'db-jobs-run','Run Job',dbJobsRun).className='run';
 $('db-jobs-parameters').value='{}';
 $('db-jobs-steps').value=JSON.stringify([{name:'check',action:'sql',sql:'SELECT 1',mode:'read-only'}],null,2);
 $('db-jobs-arguments').value='{}';
 const scheduleSlot=dataopsNode('div','db-schedule-job-slot',editDetails);
 const historyPanel=dataopsNode('section','db-jobs-history-panel',editDetails);historyPanel.className='panel';
 const historyHead=dataopsNode('div','',historyPanel);historyHead.className='panelhead';
 dataopsNode('h2','db-jobs-history-title',historyHead,'Run history (latest 100)');
 dataopsButton(historyHead,'db-jobs-history-refresh','Refresh history',dbJobsListHistory);
 const historyBody=dataopsNode('div','',historyPanel);historyBody.className='panelbody';
 dataopsNode('p','db-jobs-history-summary',historyBody).className='help';dataopsNode('div','db-jobs-history',historyBody);
 detailView.append(back,detailPage,grid);
 root.append(listView,detailView);
 dbJobsRender();
 if(activeTab==='jobs')void dbJobsOpen();
}
function dbJobsRenderList(){
 const p=dbJobs,host=$('db-jobs-list-rows');if(!host)return;
 host.replaceChildren();
 dbJobsRenderRecentRuns();
 const allowed=!p.invalid&&dbJobsAllowed(),summary=$('db-jobs-list-summary'),jobs=allowed?p.jobs:[];
 if(summary)ui(summary,'{count} saved jobs',{count:jobs.length});
 if(!allowed)return;
 if(!jobs.length){const empty=document.createElement('p');empty.className='empty';ui(empty,'No saved jobs yet. Choose New job to create the first one.');host.append(empty);return;}
 for(const job of jobs){
  const row=document.createElement('a');row.className='objectrow';row.href='#jobs/'+encodeURIComponent(job.id);
  row.onclick=event=>{event?.preventDefault?.();void dbJobsSelect(job.id);};
  const icon=document.createElement('span');icon.className='objecticon';icon.setAttribute('aria-hidden','true');icon.textContent='◷';
  const body=document.createElement('div'),strong=document.createElement('strong'),small=document.createElement('small');
  strong.textContent=job.definition.name;
  const count=(job.definition.steps||[]).length,run=dbJobsActiveRun(job);
  small.textContent=[dbJobsSafeName(job.owner),t('Revision')+' '+job.revision,t(count===1?'1 step':'{count} steps',{count})].join(' · ');
  body.append(strong,small);
  const end=document.createElement('span');end.className='rowend';
  if(run){const status=document.createElement('span');status.className='status '+dbJobsStatusClass(run.status);ui(status,dbJobsStatusKey(run.status));end.append(status);}
  else end.textContent=dbRoleDisplayLabel(job.selected_role);
  row.append(icon,body,end);host.append(row);
 }
}
function dbJobsRenderRecentRuns(){
 const p=dbJobs,host=$('db-jobs-recent-runs');if(!host)return;
 host.replaceChildren();
 const allowed=!p.invalid&&dbJobsAllowed(),runs=allowed?(p.history||[]).slice(0,100):[];
 const table=document.createElement('table');
 const caption=document.createElement('caption');caption.id='db-jobs-recent-caption';ui(caption,'Run history · {count} runs',{count:runs.length});table.append(caption);
 const head=document.createElement('thead'),headRow=document.createElement('tr');
 for(const key of ['Job name','Status','Started at (JST)','Duration','Output']){const th=document.createElement('th');th.scope='col';ui(th,key);headRow.append(th);}head.append(headRow);
 const body=document.createElement('tbody');
 if(!runs.length){
  const row=document.createElement('tr'),cell=document.createElement('td');cell.colSpan=5;
  const empty=document.createElement('p');empty.className='empty';
  const guidance=(p.status?.textContent||p.scheduleRefreshing)?'—':dbJobsContextReason(false)||'Run history is unavailable for this context.';
  ui(empty,allowed?'No runs yet.':guidance);cell.append(empty);row.append(cell);body.append(row);
 }else for(const run of runs){
  const row=document.createElement('tr');
  const nameCell=document.createElement('td'),link=document.createElement('a');
  link.href=run.id?'#jobs/run/'+encodeURIComponent(run.id):(run.job_id?'#jobs/'+encodeURIComponent(run.job_id):'#jobs');link.textContent=dbJobsSafeName(run.job_name||run.job_id);
  if(run.id)link.onclick=event=>{event?.preventDefault?.();return dbJobsSelectRun(run);};
  else if(run.job_id)link.onclick=event=>{event?.preventDefault?.();return dbJobsSelect(run.job_id);};
  nameCell.append(link);
  const statusCell=document.createElement('td'),status=document.createElement('span');status.className='status '+dbJobsStatusClass(run.status);ui(status,dbJobsStatusKey(run.status));statusCell.append(status);
  const startedCell=document.createElement('td'),started=run.started_at||run.created_at,time=document.createElement('time');
  if(started){time.datetime=started;time.textContent=fmtJST(started);}else time.textContent='—';
  startedCell.append(time);
  const durationCell=document.createElement('td');durationCell.textContent=dbJobsRunDuration(run);
  const outputCell=document.createElement('td'),output=dbJobsRunOutput(run);
  if(output){const outputLink=document.createElement('a');outputLink.href=output.href;outputLink.textContent=output.name;outputCell.append(outputLink);}else outputCell.textContent='—';
  row.append(nameCell,statusCell,startedCell,durationCell,outputCell);body.append(row);
 }
 table.append(head,body);host.append(table);
}
function dbJobsRenderSteps(){
 const list=$('db-jobs-steps-list');if(!list)return;
 list.replaceChildren();
 let steps;
 try{steps=JSON.parse($('db-jobs-steps').value||'[]');}catch{const note=document.createElement('p');note.className='callout warn';ui(note,'Step JSON is invalid. Fix it before saving.');list.append(note);return;}
 if(!Array.isArray(steps)||!steps.length){const empty=document.createElement('p');empty.className='empty';ui(empty,'No steps yet.');list.append(empty);return;}
 const run=dbJobsActiveRun();
 // A run of another revision executed other steps; show what it recorded.
 const historical=dbJobsRunIsHistorical(run);
 if(historical)steps=(run.steps||[]).map(item=>({name:item.name,action:item.action}));
 if(!steps.length){const empty=document.createElement('p');empty.className='empty';ui(empty,'No steps yet.');list.append(empty);return;}
 steps.forEach((step,index)=>{
  const row=document.createElement('div');row.className='step';
  const executed=historical?run.steps[index]:(run?.steps||[]).find(item=>item.name===step.name)||null;
  const glyph=document.createElement('span');glyph.className='step-icon';glyph.setAttribute('aria-hidden','true');
  const status=executed?.status||'';
  if(status==='succeeded')glyph.className+=' success';
  else if(status==='failed'||status==='unknown')glyph.className+=' failed';
  else glyph.className+=' muted';
  glyph.textContent=status==='succeeded'?'✓':status==='failed'||status==='unknown'?'✕':'·';
  const body=document.createElement('div'),title=document.createElement('h3'),meta=document.createElement('p');
  title.textContent=step.name||t('Step {number}',{number:index+1});
  const actionLabel=typeof jobActionLabels!=='undefined'?jobActionLabels[step.action]:null;
  meta.textContent=[t(actionLabel||(step.action==='table_export'?'Table export':step.action)),step.mode?t(step.mode):'',step.schema,step.table||step.model_id||'',dbJobsStepFacts(executed)].filter(Boolean).join(' · ');
  body.append(title,meta);
  const badge=document.createElement('span');badge.className='status '+(executed?dbJobsStatusClass(executed.status):'');if(executed)ui(badge,dbJobsStatusKey(executed.status));else ui(badge,'No status');
  row.append(glyph,body,badge);list.append(row);
 });
}
function dbJobsSelect(id){
 if(!id)return;
 const p=dbJobs;p.view='detail';p.editOpen=false;p.selection=id;$('db-jobs-list').value=id;
 if(typeof workbenchShowDetail==='function'){try{workbenchShowDetail('jobs',id);}catch{}}
 return dbJobsLoad(false,id).then(()=>{if(p.job?.id===id){const title=$('db-jobs-detail-title');title?.scrollIntoView?.({block:'start'});title?.focus?.({preventScroll:true});}});
}
async function dbJobsSelectRun(run){
 if(!run?.id||!run.job_id)return;
 const p=dbJobs;p.view='detail';p.editOpen=false;p.selection=run.job_id;$('db-jobs-list').value=run.job_id;
 if(typeof workbenchShowDetail==='function'){try{workbenchShowDetail('jobs','run/'+run.id);}catch{}}
 await dbJobsLoad(false,run.job_id);
 if(p.job?.id!==run.job_id)return;
 p.run=dbJobsRunEnvelope(run);dbJobsRender();
 if(typeof workbenchShowDetail==='function'){try{workbenchShowDetail('jobs','run/'+run.id,{push:false});}catch{}}
 const title=$('db-jobs-detail-title');title?.scrollIntoView?.({block:'start'});title?.focus?.({preventScroll:true});
}
function dbJobsShowList({route=true}={}){
 const p=dbJobs;if(!p.installed)return;
 dbJobsCapture();if(route||p.job||p.selection||p.pending)++p.request;p.view='list';p.editOpen=false;p.job=null;p.selection='';p.save=null;
 $('db-jobs-list').value='';
 if(route&&typeof workbenchClearDetail==='function')workbenchClearDetail('jobs');
 dbJobsRender();
 if(route)$('db-jobs-title')?.focus?.();
 if(dbJobsAllowed())void dbJobsListHistory();
}
async function dbJobsRestorePage(page){
 const p=dbJobs;if(!p.installed)return;
 const id=typeof workbenchDetailId==='function'?workbenchDetailId(page):'';
 if(!id)return dbJobsShowList({route:false});
 if(!p.started)await dbJobsOpen();
 if(p.invalid||!dbJobsAllowed())return dbJobsShowList({route:false});
 if(id==='new'){p.editOpen=true;p.view='list';dbJobsEditor();dbJobsRender();return;}
 if(id.startsWith('run/')){
  const runId=id.slice(4);
  try{
   const reply=await dataopsRequest('/api/v1/jobs/runs/'+encodeURIComponent(runId),p.context);
   if(!reply.run||reply.run.id!==runId)throw Error('receipt');
   p.view='detail';p.editOpen=false;p.selection=reply.run.job_id;
   await dbJobsLoad(false,reply.run.job_id);
   if(p.job?.id!==reply.run.job_id)throw Error('job');
   p.run=dbJobsRunEnvelope(reply.run);dbJobsRender();
   if(typeof workbenchShowDetail==='function'){try{workbenchShowDetail('jobs','run/'+runId,{push:false});}catch{}}
  }catch{dbJobsShowList({route:false});}
  return;
 }
 const edit=id.endsWith('~edit'),jobId=edit?id.slice(0,-5):id;
 await dbJobsLoad(false,jobId);
 if(p.job?.id!==jobId)return dbJobsShowList({route:false});
 if(edit){p.view='list';p.editOpen=true;dbJobsRender();if(typeof workbenchShowDetail==='function'){try{workbenchShowDetail('jobs',jobId+'~edit',{push:false});}catch{}}}
}
function dbJobsOpenAlerts(){
 const p=dbJobs;if(!p.installed)return;
 if(!p.job){
  $('db-jobs-title')?.focus?.();
  if(p.status)ui(p.status,'Select a job to see its alerts.');
  return;
 }
 p.view='list';p.editOpen=true;dbJobsRender();
 const button=$('db-schedule-job-alerts-refresh');
 if(button){button.scrollIntoView?.({block:'start'});button.focus?.();}
 else $('db-jobs-title')?.focus?.();
}
function dbJobsLanguageChanged(){
 if(typeof dbJobs==='undefined'||!dbJobs.installed||typeof activeTab==='undefined'||activeTab!=='jobs')return;
 dbJobsRender();
}
function dbJobsOpen(){if(!dbJobs.installed)return;if(!dbJobs.started){dbJobs.started=true;return dbJobsRefresh();}dbJobsSyncPolling();}
