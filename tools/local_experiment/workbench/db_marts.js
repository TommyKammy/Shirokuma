'use strict';

// Managed Marts owns an independent admin/MARTS context and explicit model version.
const dbMarts={installed:false,started:false,epoch:0,request:0,pending:false,invalid:true,marts:[],models:[],history:[],mart:null,model:null,selection:'',run:null,save:null};
Object.assign(i18n.ja,{
 'Managed Marts':'公開済み MARTS','Refresh Marts':'マート一覧を更新','Refresh MARTS':'MARTS を更新','Saved Marts':'保存済みマート','Load Mart':'マートを読み込む','Load current revision':'現在のリビジョンを読み込む','New Mart':'新しいマート','Save Mart':'マートを保存',
 'Version':'バージョン','Load model version':'モデルのバージョンを読み込む','Watch sources':'監視するソース','All model sources':'すべてのモデルソースを監視','Manual only':'手動のみ（監視なし）','Check changes':'変更を確認','Check only when changed':'変更がある場合のみ更新','Full refresh':'全件更新','Mart history':'マートの履歴','Refresh history':'履歴を更新',
 'Model and quality details':'モデルと品質条件の詳細','Mart definition details':'マート定義の詳細','Draft Mart target':'マート下書きの対象','Mart target':'マートの対象','Mart recovery required':'マートの復旧が必要です。','Not accepted':'未受付',
 'Manual-only Marts allow unconditional refresh. Check and conditional refresh require watches (WATCH_REQUIRED).':'手動のみのマートは無条件に更新できます。変更確認と条件付き更新には監視ソースが必要です（WATCH_REQUIRED）。',
 'Full refresh replaces the saved target using this model version and its quality checks.':'全件更新は、このモデルの版と品質条件を使って保存済みの対象を置き換えます。',
 'Discard this draft and load the current server revision?':'この下書きを破棄して現在のサーバーリビジョンを読み込みますか？',
 'Mart access unavailable. Refresh an authorized context.':'マートにアクセスできません。許可された接続先を更新してください。',
 'Mart saved.':'マートを保存しました。','Mart saved. Load Mart to restore its explicit model version.':'マートを保存しました。マートを読み込んで、保存したモデルの版を復元してください。',
 'Load an explicit model version and choose its MARTS target.':'モデルの版を明示して読み込み、MARTS の対象を選択してください。',
 'Observe the original save before changing its inputs.':'入力を変更する前に、元の保存操作を照会してください。',
 'Mart request failed. Draft retained. Load current revision after a conflict.':'マートの要求に失敗しました。下書きは保持しています。競合時は現在のリビジョンを読み込んでください。',
 'WATCH_REQUIRED — check requires watched sources.':'WATCH_REQUIRED — 変更確認には監視ソースが必要です。',
 'WATCH_REQUIRED — request not accepted; check requires watched sources.':'WATCH_REQUIRED — 要求は受け付けられませんでした。変更確認には監視ソースが必要です。',
 'UNKNOWN — Observe uses the original context and key.':'UNKNOWN — 照会には元のコンテキストとキーを使用します。',
 'Publication status':'公開状態','Changed':'変更あり','Unchanged':'変更なし','Target snapshot':'対象スナップショット','Rows':'行数'
});
for(const key of Object.keys(i18n.ja))i18n.en[key]=key;
const dbMartsFields=['name','model-id','version','watch'];
const dbMartsPath=id=>'/api/v1/marts'+(id?'/'+encodeURIComponent(id):'');
const dbMartsOwner=()=>JSON.parse(dbPrincipal||'[]')[0];
function dbMartsAllowed(context=dbMarts.context,schemas=dbMarts.schemas){
 const p=dbMarts;if(!context||p.principal!==dbPrincipal||context.selected_role!=='admin'||context.sql_schema?.toLowerCase()!=='marts'||!dbRoleWithinHeader('admin'))return false;
 const d=dbRoleItems('admin').find(d=>d.id===context.database_id),s=(schemas||[]).find(s=>s.id===context.schema_id);
 return !!(d&&s&&s.sql_name?.toLowerCase()==='marts'&&d.state==='READY'&&d.allowed_roles?.includes('admin')&&d.binding_revision===context.binding_revision&&d.generation_id===context.generation_id&&(d.grant_revision??null)===context.grant_revision&&s.capabilities?.write===true);
}
function dbMartsValidMart(m,c=dbMarts.context,id){return !!m&&!!c&&typeof m.id==='string'&&!!m.id&&(!id||m.id===id)&&m.database_id===c.database_id&&m.owner===dbMartsOwner()&&Number.isInteger(m.revision)&&m.revision>0&&m.definition?.target?.schema_id===c.schema_id&&['database_id','schema_id','warehouse_id','sql_catalog','sql_schema','selected_role'].every(k=>m.context?.[k]===c[k]);}
function dbMartsValidRun(r,c,id,runId){return !!r&&typeof r.id==='string'&&!!r.id&&(!runId||r.id===runId)&&r.mart_id===id&&r.owner===dbMartsOwner()&&dataopsSameContext(r.context,c)&&Number.isInteger(r.mart_revision)&&r.mart_revision>0&&['check','refresh'].includes(r.kind)&&typeof r.status==='string'&&!!r.status;}
function dbMartsSelected(){const p=dbMarts;return !p.invalid&&dbMartsAllowed()&&dbMartsValidMart(p.mart,p.context,p.selection)&&p.mart.id===p.selection;}
function dbMartsSelect(){const p=dbMarts;++p.request;p.selection=$('db-marts-list').value;p.mart=null;p.model=null;p.history=[];p.conflict=false;for(const k of dbMartsFields)$('db-marts-'+k).value='';if(p.run)p.run.details=null;dbMartsRender();}
function dbMartsAssociated(r){const p=dbMarts;return !!r&&r.principal===dbPrincipal&&dbMartsSelected()&&p.mart.id===r.mart_id&&dataopsSameContext(p.context,r.context)&&dbMartsAllowed(r.context);}
function dbMartsClear(){const p=dbMarts;++p.epoch;++p.request;p.mart=null;p.model=null;p.selection='';p.history=[];p.marts=[];p.models=[];p.conflict=false;p.context=null;p.invalid=true;for(const k of dbMartsFields)$('db-marts-'+k).value='';$('db-marts-models').value='';$('db-marts-check-changes').checked=false;if(p.run)p.run.details=null;dbMartsRender();}
function dbMartsPurge(){const p=dbMarts;if(!p.installed)return;dbMartsClear();p.save=null;if(p.run){p.run.payload=null;p.run.details=null;p.run.purged=true;}if(p.principal!==dbPrincipal)p.run=null;ui(p.status,'Mart access unavailable. Refresh an authorized context.');dbMartsRender();}
function dbMartsValidate(){const p=dbMarts;if(!p.installed)return;if(!p.invalid&&!dbMartsAllowed())dbMartsPurge();if(p.run?.principal!==dbPrincipal)p.run=null;dbMartsRender();}
function dbMartsRejection(data,status){
 if(status!==409||!data||typeof data!=='object'||Array.isArray(data))return false;
 const code=data.error?.code||data.code,codes=['MART_REVISION_CONFLICT','WATCH_REQUIRED','MODEL_VERSION_CHANGED','MART_BUSY','MART_RECONCILIATION_REQUIRED'];
 if(!codes.includes(code)||data.error!==undefined&&(!data.error||typeof data.error!=='object'||Array.isArray(data.error)))return false;
 return [data,...(data.error?[data.error]:[])].every((node,i)=>Object.keys(node).every(k=>['code','message','request_id','retryable','details',...(i===0?['error']:[])].includes(k))&&(!Object.hasOwn(node,'code')||node.code===code)&&(!node.details||typeof node.details==='object'&&!Array.isArray(node.details)&&Object.keys(node.details).length===0));
}
async function dbMartsRequest(path,context,body,key){
 const headers={'X-Shirokuma-Role':'admin'};if(key)headers['X-Idempotency-Key']=key;
 const response=await fetch(body===undefined?path+(path.includes('?')?'&':'?')+'context='+encodeURIComponent(JSON.stringify(context)):path,body===undefined?{headers}:{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify({...body,context})});
 const data=await response.json();if(!response.ok)throw Object.assign(dbError(data,response.status),{martRejected:dbMartsRejection(data,response.status)});if(!dataopsSameContext(data.context,context))throw Error('context');return data;
}
function dbMartsGuard(){const p=dbMarts,epoch=p.epoch,request=++p.request,principal=dbPrincipal,context=p.context,id=p.mart?.id,selection=p.selection;return ()=>epoch===p.epoch&&request===p.request&&principal===dbPrincipal&&dataopsSameContext(context,p.context)&&id===p.mart?.id&&selection===p.selection&&dbMartsAllowed();}
function dbMartsError(error){if(dataopsAuthError(error))dbMartsPurge();else{if(['MART_REVISION_CONFLICT','REVISION_CONFLICT'].includes(error.code))dbMarts.conflict=true;ui(dbMarts.status,error.code==='WATCH_REQUIRED'?'WATCH_REQUIRED — check requires watched sources.':'Mart request failed. Draft retained. Load current revision after a conflict.');}}
async function dbMartsRefreshAuthorization(){const p=dbMarts;if(!p.started)return;const epoch=p.epoch,principal=dbPrincipal;try{const items=await dbReadInventory('admin');if(epoch!==p.epoch||principal!==dbPrincipal||!dbInventoryCurrent(items))return;dbApplyInventory(items,'admin');if(p.context){const reply=await dbAPI('/api/v1/databases/'+encodeURIComponent(p.context.database_id)+'/schemas','admin');if(epoch!==p.epoch||principal!==dbPrincipal)return;p.schemas=reply.items||[];}dbMartsValidate();}catch(error){if(epoch===p.epoch&&principal===dbPrincipal&&error.code!=='AUTH_READ_STALE')dbMartsPurge();}}
async function dbMartsRefresh(){
 const p=dbMarts,epoch=++p.epoch,request=++p.request,principal=dbPrincipal,old=p.context;
 const current=()=>epoch===p.epoch&&request===p.request&&principal===dbPrincipal;
 try{const items=await dbReadInventory('admin');if(!current()||!dbInventoryCurrent(items))return;dbApplyInventory(items,'admin');p.databases=items;p.principal=principal;
 const reply=p.database?await dbAPI('/api/v1/databases/'+encodeURIComponent(p.database)+'/schemas','admin'):{items:[]};if(!current())return;p.schemas=reply.items||[];
 if(!p.database||!p.schema){p.invalid=true;dbMartsRender();return;}
 const context=dataopsFreeze(dataopsContext(p));if(old&&!dataopsSameContext(old,context)){dbMartsPurge();return;}p.context=context;p.invalid=!dbMartsAllowed();if(p.invalid){dbMartsPurge();return;}
 const result=await dbMartsRequest(dbMartsPath(),context);if(!current()||!dbMartsAllowed())return;if(!Array.isArray(result.items)||result.items.some(m=>!dbMartsValidMart(m,context)))throw Error('marts');p.marts=result.items;
 const models=await dbMartsRequest('/api/v1/wap/models',context);if(!current()||!dbMartsAllowed())return;p.models=(models.items||[]).filter(m=>m.database_id===context.database_id);dbMartsRender();
 }catch(error){if(current())dbMartsError(error);}
}
function dbMartsWatches(model){return [...new Map(Object.values(model.sources||{}).map(r=>[dataopsRefKey(r),r])).entries()].sort(([a],[b])=>a.localeCompare(b)).map(([,r])=>r);}
function dbMartsDefinition(){
 const p=dbMarts,m=p.model;if(!m||m.database_id!==p.context.database_id||m.model_id!==$('db-marts-model-id').value||String(m.version)!==$('db-marts-version').value)throw Error('load model');
 const name=$('db-marts-name').value.trim();if(!/^[a-z][a-z0-9_-]{0,47}$/.test(name))throw Error('name');
 const target=p.mart?.current_target_ref||m.current_target_ref||m.target;if(target?.database_id!==p.context.database_id||target.schema_id!==p.context.schema_id)throw Error('target');
 return {name,model_id:m.model_id,version:m.version,source_refs:$('db-marts-watch').value==='all'?dbMartsWatches(m):[],target:dataopsClone(target)};
}
function dbMartsMatches(){try{const d=dbMarts.mart.definition,normalize=v=>({...v,source_refs:v.source_refs.map(r=>dataopsRefKey(r)).sort(),target:dataopsRefKey(v.target)+(v.target.physical_name||'')});return dbMartsSelected()&&!dbMarts.conflict&&dataopsSameContext(normalize(dbMartsDefinition()),normalize({...d,target:dbMarts.mart.current_target_ref||d.target}));}catch{return false;}}
async function dbMartsLoadModel(){
 const p=dbMarts;if(p.invalid||p.pending||!dbMartsAllowed())return;const id=$('db-marts-model-id').value,version=Number($('db-marts-version').value);if(!id||!Number.isSafeInteger(version)||version<1)return;
 const current=dbMartsGuard();p.pending=true;dbMartsRender();try{const reply=await dbMartsRequest('/api/v1/wap/models?model_id='+encodeURIComponent(id)+'&version='+version,p.context);if(!current())return;const m=reply.model||reply.items?.[0];if(!m||m.database_id!==p.context.database_id||m.model_id!==id||m.version!==version)throw Error('model');p.model=dataopsFreeze(dataopsClone(m));}catch(error){if(current())dbMartsError(error);}finally{p.pending=false;dbMartsRender();}
}
async function dbMartsLoad(){
 const p=dbMarts;if(p.invalid||p.pending||!p.selection||!dbMartsAllowed())return;if(p.mart&&!dbMartsMatches()&&!confirm(t('Discard this draft and load the current server revision?')))return;
 const id=p.selection,current=dbMartsGuard();p.pending=true;dbMartsRender();try{const reply=await dbMartsRequest(dbMartsPath(id),p.context);if(!current())return;if(!dbMartsValidMart(reply.mart,p.context,id))throw Error('mart');const d=reply.mart.definition;
 const models=await dbMartsRequest('/api/v1/wap/models?model_id='+encodeURIComponent(d.model_id)+'&version='+d.version,p.context);if(!current())return;const m=models.model||models.items?.[0];if(!m||m.database_id!==p.context.database_id||m.model_id!==d.model_id||m.version!==d.version)throw Error('model');
 p.mart=reply.mart;p.model=dataopsFreeze(dataopsClone(m));p.history=[];p.save=null;p.conflict=false;$('db-marts-name').value=d.name;$('db-marts-model-id').value=d.model_id;$('db-marts-version').value=String(d.version);$('db-marts-watch').value=d.source_refs.length?'all':'manual';ui(p.status,'Ready');
 }catch(error){if(current())dbMartsError(error);}finally{p.pending=false;dbMartsRender();}
}
async function dbMartsSave(){
 const p=dbMarts;if(p.invalid||p.pending||p.conflict||!dbMartsAllowed()||(p.mart&&!dbMartsSelected()))return;
 if(p.save){await dbMartsRecoverSave();return;}
 try{const definition=dbMartsDefinition(),body={expected_revision:p.mart?.revision||0,definition},signature=JSON.stringify(body);if(p.save&&p.save.signature!==signature){ui(p.status,'Observe the original save before changing its inputs.');return;}if(!p.save)p.save={signature,principal:dbPrincipal,mart_id:p.mart?.id||null,body:dataopsFreeze(dataopsClone(body)),context:dataopsFreeze(dataopsClone(p.context)),key:crypto.randomUUID(),path:dbMartsPath(p.mart?.id)};
 const s=p.save,current=dbMartsGuard();p.pending=true;dbMartsRender();try{const reply=await dbMartsRequest(s.path,s.context,s.body,s.key);if(!current())return;if(!dbMartsValidMart(reply.mart,s.context,p.mart?.id)||reply.mart.revision!==s.body.expected_revision+1)throw Error('mart');p.mart=reply.mart;p.selection=reply.mart.id;p.save=null;p.marts=p.marts.filter(m=>m.id!==p.mart.id).concat(p.mart);ui(p.status,'Mart saved.');}catch(error){if(current()){if(error.status===409)p.save=null;dbMartsError(error);}}finally{p.pending=false;dbMartsRender();}
 }catch{ui(p.status,'Load an explicit model version and choose its MARTS target.');}
}
function dbMartsCanRecoverSave(){const p=dbMarts,s=p.save;return !!s&&s.principal===dbPrincipal&&!p.invalid&&dbMartsAllowed()&&dataopsSameContext(s.context,p.context)&&(s.mart_id?dbMartsSelected()&&p.selection===s.mart_id:!p.mart&&!p.selection);}
async function dbMartsRecoverSave(){
 const p=dbMarts,s=p.save;if(p.pending||!dbMartsCanRecoverSave())return;const current=dbMartsGuard();p.pending=true;dbMartsRender();try{const reply=await dbMartsRequest(s.path,s.context,s.body,s.key);if(!current()||p.save!==s)return;if(!dbMartsValidMart(reply.mart,s.context,s.mart_id)||reply.mart.revision!==s.body.expected_revision+1)throw Error('mart');p.mart=reply.mart;p.selection=reply.mart.id;p.save=null;p.marts=p.marts.filter(m=>m.id!==p.mart.id).concat(p.mart);ui(p.status,'Mart saved. Load Mart to restore its explicit model version.');}catch(error){if(current()){if(error.status===409)p.save=null;dbMartsError(error);}}finally{p.pending=false;dbMartsRender();}
}
const dbMartsTerminal=r=>!!r&&['succeeded','failed','skipped'].includes(r.status)&&r.details?.reconciliation_required!==true;
function dbMartsHeld(){const p=dbMarts;return dbMartsSelected()&&(p.mart.reconciliation_required===true||!!p.mart.blocked_run_id||dbMartsAssociated(p.run)&&!p.run.rejection&&!dbMartsTerminal(p.run));}
async function dbMartsSubmit(kind){
 const p=dbMarts;if(p.pending||!dbMartsMatches()||dbMartsHeld())return;
 const context=dataopsFreeze(dataopsClone(p.context)),body={context,expected_revision:p.mart.revision};if(kind==='refresh')body.check_changes=$('db-marts-check-changes').checked===true;
 p.run={id:null,mart_id:p.mart.id,mart_revision:p.mart.revision,principal:dbPrincipal,context,key:crypto.randomUUID(),payload:dataopsFreeze(body),path:dbMartsPath(p.mart.id)+(kind==='check'?'/checks':'/refreshes'),kind,status:'unknown',details:null,sequence:0};await dbMartsObserve();
}
async function dbMartsObserve(){
 if(dbMarts.save){await dbMartsRecoverSave();return;}
 const p=dbMarts,r=p.run;if(p.pending||!dbMartsAssociated(r)||r.purged||(!r.id&&!r.payload))return;
 const current=dbMartsGuard(),sequence=++r.sequence;p.pending=true;dbMartsRender();try{
 const reply=await dbMartsRequest(r.id?dbMartsPath(r.mart_id)+'/runs/'+encodeURIComponent(r.id):r.path,r.context,r.id?undefined:r.payload,r.id?undefined:r.key),run=reply.run;
 if(!dbMartsValidRun(run,r.context,r.mart_id,r.id)||run.mart_revision!==r.mart_revision||run.kind!==r.kind)throw Error('run');
 // A late acknowledgement may preserve a safe handle, never protected content.
 if(p.run!==r||sequence!==r.sequence||r.principal!==dbPrincipal)return;r.id=run.id;r.payload=null;r.path=null;
 if(!current()||!dbMartsAssociated(r)||r.purged)return;r.status=run.status;r.details=run;ui(p.status,run.status);
 }catch(error){if(p.run===r&&current()){dbMartsError(error);if(!r.id&&error.martRejected){r.rejection=error.code;r.status='rejected';r.payload=null;r.path=null;r.details=null;if(error.code!=='WATCH_REQUIRED')p.conflict=true;ui(p.status,error.code==='WATCH_REQUIRED'?'WATCH_REQUIRED — request not accepted; check requires watched sources.':error.code+' — request not accepted. Load current revision; Observe blocked runs from this Mart history.');}else if(!r.id&&!dataopsAuthError(error))ui(p.status,'UNKNOWN — Observe uses the original context and key.');}}
 finally{p.pending=false;dbMartsRender();}
}
async function dbMartsHistory(){
 const p=dbMarts;if(p.pending||!dbMartsSelected())return;const id=p.mart.id,current=dbMartsGuard();p.pending=true;dbMartsRender();try{const reply=await dbMartsRequest(dbMartsPath(id)+'/runs',p.context);if(!current()||!dbMartsSelected())return;if(!Array.isArray(reply.items)||reply.items.some(r=>!dbMartsValidRun(r,p.context,id)))throw Error('runs');p.history=reply.items;}catch(error){if(current())dbMartsError(error);}finally{p.pending=false;dbMartsRender();}
}
function dbMartsRender(){
 dbScheduleSync('mart');
 const p=dbMarts;if(!p.installed)return;const allowed=!p.invalid&&dbMartsAllowed(),selected=dbMartsSelected();
 const martsDatabases=dbReadyDatabases(p.role);
 dataopsOptions($('db-marts-role'),dbRoleWithinHeader('admin')?[['admin',dbRoleDisplayNames.admin]]:[],p.role,'Execution role');dataopsOptions($('db-marts-database'),martsDatabases.map(d=>[d.id,dbDatabaseLabel(d)]),martsDatabases.some(d=>d.id===p.database)?p.database:'','Choose database');dataopsOptions($('db-marts-schema'),(p.schemas||[]).filter(s=>s.sql_name?.toLowerCase()==='marts').map(s=>[s.id,dbSchemaLabel(s)]),p.schema,'Choose schema');
 dataopsOptions($('db-marts-list'),(allowed?p.marts:[]).map(m=>[m.id,m.definition.name+' · v'+m.revision]),p.selection,'Saved Marts');dataopsOptions($('db-marts-models'),(allowed?p.models:[]).map(m=>[JSON.stringify([m.model_id,m.version]),m.model_id+' · v'+m.version]),$('db-marts-models').value,'Saved models');
 for(const k of [...dbMartsFields,'models','load-model','save'])$('db-marts-'+k).disabled=p.pending||!allowed;
 $('db-marts-save').disabled=p.pending||!allowed||p.conflict||!!(p.mart&&!selected);
 for(const k of ['check','refresh-run'])$('db-marts-'+k).disabled=p.pending||!dbMartsMatches()||dbMartsHeld();
 $('db-marts-observe').disabled=p.pending||(p.save?!dbMartsCanRecoverSave():!dbMartsAssociated(p.run)||p.run?.purged||(!p.run?.id&&!p.run?.payload));$('db-marts-history-refresh').disabled=p.pending||!selected;$('db-marts-check-changes').disabled=p.pending||!allowed;
 $('db-marts-model-detail').textContent=allowed&&p.model?JSON.stringify({model_id:p.model.model_id,version:p.model.version,sources:p.model.sources,target:p.mart?.current_target_ref||p.model.current_target_ref||p.model.target,min_rows:p.model.min_rows,not_null:p.model.not_null},null,2):'';
 $('db-marts-definition-detail').textContent=selected?JSON.stringify({id:p.mart.id,owner:p.mart.owner,revision:p.mart.revision,current_target_ref:p.mart.current_target_ref,last_success_at:p.mart.last_success_at,last_success_baseline:p.mart.last_success_baseline,reconciliation_required:p.mart.reconciliation_required,blocked_run_id:p.mart.blocked_run_id},null,2):'';
 const target=selected?(p.mart.current_target_ref||p.mart.definition.target):(p.model?.current_target_ref||p.model?.target),targetSchema=p.schemas.find(s=>s.id===target?.schema_id)?.sql_name;
 const targetName=target?.physical_name||p.mart?.definition?.target?.physical_name||p.model?.target?.physical_name,version=p.model?.version||p.mart?.definition?.version;
 $('db-marts-target-summary').textContent=allowed&&target?[t(selected?'Mart target':'Draft Mart target'),targetSchema&&targetName?[p.context.sql_catalog,targetSchema,targetName].join('.'):t('Unavailable'),p.mart?.definition?.name||$('db-marts-name').value,version?'v'+version:'',p.conflict?'REVISION_CONFLICT':'',dbMartsHeld()?t('Mart recovery required'):''].filter(Boolean).join(' · '):'';
 const identity=JSON.stringify([dbPrincipal,p.context,p.mart?.id,p.mart?.revision,p.model?.model_id,p.model?.version]);
 for(const id of ['model-detail','definition-detail']){const disclosure=$('db-marts-'+id).parentElement;if(disclosure.dataset.identity!==identity){disclosure.open=false;disclosure.dataset.identity=identity;}}
 const detail=$('db-marts-run-detail'),r=p.run;dbBearReset(detail,JSON.stringify([identity,r?.id||r?.key]));if(r){dataopsNode('p','',detail).textContent=t('Status')+': '+t(r.rejection?'Not accepted':r.status||'UNKNOWN');dataopsNode('p','',dbBearDetails(detail,'request','Request details')).textContent='Run ID: '+(r.id||(r.rejection?'not accepted':'UNKNOWN'))+' · Key: '+r.key;if(dbMartsAssociated(r)&&!r.purged){if(r.rejection)dataopsNode('p','',detail).textContent='Not accepted: '+r.rejection;if(r.details){
  const d=r.details;dataopsNode('p','',detail).textContent=t('Status')+': '+d.status+' · '+t('Committed')+': '+String(d.committed??'UNKNOWN');
  for(const [label,value] of [['Cleanup error',d.cleanup_error],['Error',d.error||d.error_code||d.errors],['Publication status',d.result?.publication_state],['Quality',d.result?.audit?.passed??d.result?.passed],['Rows',d.result?.audit?.count??d.result?.build?.count],['Changed',d.result?.changed],['Unchanged',d.result?.unchanged],['Publication',d.result?.publication?.id],['Target snapshot',d.result?.publication?.target?.snapshot]])if(value!==undefined&&value!==null)dataopsNode('p','',detail).textContent=t(label)+': '+(typeof value==='object'?JSON.stringify(value):String(value));
  dataopsNode('pre','',dbBearDetails(detail,'result','Raw run result')).textContent=JSON.stringify(d,null,2);
 }}}
 const history=$('db-marts-history'),focusedRun=history.dataset.identity===identity?document.activeElement?.dataset.martRun:null;history.dataset.identity=identity;history.replaceChildren();if(selected)for(const run of p.history){const row=dataopsNode('div','',history);dataopsNode('p','',row).textContent=[run.id,run.kind,run.status,'v'+run.mart_revision].join(' · ');const button=dataopsButton(row,'','Observe run',()=>{if(p.pending||!dbMartsSelected()||!dbMartsValidRun(run,p.context,p.mart.id)||(dbMartsAssociated(p.run)&&!p.run.rejection&&!dbMartsTerminal(p.run)&&p.run.id!==run.id))return;p.run={id:run.id,mart_id:run.mart_id,mart_revision:run.mart_revision,kind:run.kind,status:run.status,context:dataopsFreeze(dataopsClone(run.context)),principal:dbPrincipal,key:'',sequence:0,details:null};return dbMartsObserve();});button.dataset.martRun=run.id;if(focusedRun===run.id)button.focus();}
 const openMart=$('transformation-open-published');
 if(openMart){
  const href=allowed&&targetSchema&&targetName?dbExplorePath({sql_catalog:p.context?.sql_catalog||''},{sql_name:targetSchema},{physical_name:targetName}):'';
  openMart.hidden=!href;openMart.href=href?'#explore/'+href:'#transformation';
 }
 const publicationSummary=$('transformation-publication-summary');
 if(publicationSummary){
  const name=selected?p.mart.definition.name:p.model?.model_id;
  ui(publicationSummary,name?'Current publication: {name} · v{version} · last run {time}':'No published model selected.',{name:name||'',version:version||'',time:p.mart?.last_success_at?fmtJST(p.mart.last_success_at):t('Not run yet')});
 }
}
function dbMartsOpen(){
 if(dbMarts.installed&&!dbMarts.started){dbMarts.started=true;return dbMartsRefresh();}
}
function dbMartsInstall(){
 Object.assign(i18n.ja,{'Model and quality details':'モデルと品質条件の詳細','Mart definition details':'Mart 定義の詳細','Raw run result':'実行結果の JSON','Mart target':'Mart の対象','Full refresh replaces the saved target using this model version and its quality checks.':'全件更新は、このモデルの版と品質条件を使って保存済み対象を置き換えます。','Mart recovery required':'Mart の復旧が必要です。','Committed':'コミット済み','Cleanup error':'クリーンアップエラー','Error':'エラー','Changed':'変更あり','Unchanged':'変更なし'});
 const p=dbMarts;if(p.installed)return;p.installed=true;p.principal=dbPrincipal;p.role='admin';p.database=dbCurrent()?.database_id||'';p.schema='';p.databases=dbRoleItems('admin');p.schemas=[];
 const root=dataopsNode('section','db-marts-panel',workbenchAtlasHost('transformation-marts'));root.className='db-marts-panel atlas-section';const body=dataopsNode('div','',root);body.className='atlas-section-body';dataopsNode('h2','db-marts-heading',body,'Published MARTS').hidden=true;
 const selectors=dataopsNode('div','',body);selectors.className='toolbar transformation-context';for(const [key,label] of [['role','Execution role'],['database','Database'],['schema','Schema']]){const n=dataopsField(selectors,'db-marts-'+key,label,'select');n.onchange=()=>{const value=n.value;dbMartsClear();p[key]=value;if(key==='database')p.schema='';return dbMartsRefresh();};}dataopsButton(selectors,'db-marts-refresh','Refresh MARTS',()=>{p.started=true;return dbMartsRefresh();});
 const update=workflowDetails(body,'Update review and save');update.open=true;
 const saved=dataopsNode('div','',update);saved.className='toolbar transformation-saved';dataopsField(saved,'db-marts-list','Saved Marts','select').onchange=dbMartsSelect;dataopsButton(saved,'db-marts-load','Load Mart',dbMartsLoad);dataopsButton(saved,'db-marts-reload','Load current revision',dbMartsLoad);dataopsButton(saved,'db-marts-new','New Mart',()=>{if(p.pending||p.invalid||p.save)return;++p.request;p.mart=null;p.model=null;p.selection='';p.history=[];p.conflict=false;for(const k of dbMartsFields)$('db-marts-'+k).value='';$('db-marts-watch').value='all';dbMartsRender();});
 const form=dataopsNode('form','db-marts-form',update);form.onsubmit=e=>e.preventDefault();const fields=dataopsNode('div','',form);fields.className='job-fields';dataopsField(fields,'db-marts-name','Mart name');dataopsField(fields,'db-marts-models','Saved models','select').onchange=()=>{++p.request;const value=$('db-marts-models').value;if(value){const [id,version]=JSON.parse(value);$('db-marts-model-id').value=id;$('db-marts-version').value=String(version);}p.model=null;dbMartsRender();};
 dataopsField(fields,'db-marts-model-id','Model ID');const version=dataopsField(fields,'db-marts-version','Version');version.type='number';version.min='1';for(const k of ['name','model-id','version'])$('db-marts-'+k).oninput=()=>{++p.request;if(k!=='name')p.model=null;dbMartsRender();};dataopsButton(form,'db-marts-load-model','Load model version',dbMartsLoadModel);dataopsField(form,'db-marts-watch','Watch sources','select',[['all','All model sources'],['manual','Manual only']]).onchange=()=>{++p.request;dbMartsRender();};
 dataopsNode('p','',form,'Manual-only Marts allow unconditional refresh. Check and conditional refresh require watches (WATCH_REQUIRED).').className='help';dataopsNode('pre','db-marts-model-detail',workflowDetails(form,'Model and quality details'));dataopsButton(form,'db-marts-save','Save Mart',dbMartsSave).className='primary';dataopsNode('pre','db-marts-definition-detail',workflowDetails(update,'Mart definition details'));
 dataopsNode('p','db-marts-target-summary',update);dataopsNode('p','',update,'Full refresh replaces the saved target using this model version and its quality checks.').className='help';
 const actions=dataopsNode('div','',update);actions.className='toolbar transformation-actions';dataopsButton(actions,'db-marts-check','Check changes',()=>dbMartsSubmit('check'));const checkbox=dataopsField(actions,'db-marts-check-changes','Check only when changed');checkbox.type='checkbox';checkbox.checked=false;dataopsButton(actions,'db-marts-refresh-run','Full refresh',()=>dbMartsSubmit('refresh'));dataopsButton(actions,'db-marts-observe','Observe run',dbMartsObserve);
 p.status=dataopsNode('p','db-marts-status',body);p.status.className='status-line';p.status.setAttribute('role','status');p.status.setAttribute('aria-live','polite');dataopsNode('div','db-marts-run-detail',body);dataopsNode('h3','db-marts-history-heading',body,'Mart history');dataopsButton(body,'db-marts-history-refresh','Refresh history',dbMartsHistory);dataopsNode('div','db-marts-history',body);
 dbMartsRender();
}
