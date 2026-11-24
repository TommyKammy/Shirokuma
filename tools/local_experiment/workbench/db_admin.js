'use strict';
function dbAdminAllowed(){return databaseMode&&dbAdmin.platform&&dbMaximumRole==='admin'&&currentRole==='admin';}
const dbAdminStateLabels={READY:'Available',REGISTERED:'Registered',PREPARING:'Preparing',RETIRING:'Retiring',RETIRED:'Retired',FAILED:'Failed',UNKNOWN:'Unknown'};
function dbAdminStateLabel(state){return t(dbAdminStateLabels[String(state||'').toUpperCase()]||'Unknown');}
function dbAdminStateHeading(){return language==='ja'?t('Database state'):'State';}
function dbAdminRevisionHeading(){return language==='ja'?t('Database revision number'):'Revision';}
function dbAdminTeam(value){
 const team=String(value??'').trim();
 if(![...team].length||[...team].length>64||/[\u0000-\u001f\u007f-\u009f]/u.test(team))throw Error(t('Team must be 1 to 64 characters without control characters.'));
 return team;
}
function dbAdminPinStorageKey(){return 'shirokuma.db-pins.v1.'+String(workbenchSession?.username||'local');}
function dbAdminLoadPins(){
 const key=dbAdminPinStorageKey();
 if(dbAdmin.pinKey!==key){
  dbAdmin.pinKey=key;dbAdmin.pins=new Set();
  try{const saved=JSON.parse(localStorage.getItem(key)||'[]');if(Array.isArray(saved))dbAdmin.pins=new Set(saved.filter(id=>typeof id==='string'&&id));}catch{/* Storage is optional; pins still work in memory. */}
 }
 return dbAdmin.pins;
}
function dbAdminSavePins(){
 try{localStorage.setItem(dbAdmin.pinKey||dbAdminPinStorageKey(),JSON.stringify([...dbAdmin.pins]));}catch{/* Storage is optional. */}
}
function dbAdminTogglePin(id){
 if(!id)return;
 dbAdminLoadPins();
 if(dbAdmin.pins.has(id))dbAdmin.pins.delete(id);else dbAdmin.pins.add(id);
 dbAdminSavePins();dbAdminRenderList();dbAdminRenderDetailPin();
}
function dbAdminUpdatedValue(d){
 const value=d?.updated_at??d?.updatedAt;
 return typeof value==='string'&&Number.isFinite(Date.parse(value))?value:null;
}
function dbAdminUpdatedLabel(d){const value=dbAdminUpdatedValue(d);return value?fmtJST(value):'—';}
function dbAdminName(d){return d?.display_name||d?.sql_catalog||'';}
function dbAdminFilterCount(){
 return Number(Boolean(dbAdmin.listState))+Number(Boolean(dbAdmin.listTeam))+Number(dbAdmin.listPinned)+Number(dbAdmin.listSort!=='name-asc');
}
function dbAdminCreationRecords(){
 const records=new Map(),add=p=>{const key=p?.creation_id||p?.database_id;if(key&&!records.has(key))records.set(key,p);};
 if(dbCreation.process?.creation_id)add(dbCreation.process);
 for(const p of dbCreation.list||[])add(p);
 return [...records.values()].filter(p=>p&&typeof p==='object');
}
function dbAdminCreationOpen(p){
 const lifecycle=String(p?.lifecycle||'').toUpperCase();
 if(['READY','RETIRING','RETIRED'].includes(lifecycle)&&!['CREATING','VERIFYING','UNKNOWN','BLOCKED','STOPPED','RECONCILE_REQUIRED'].includes(String(p?.progress_code||'').toUpperCase()))return false;
 return ['CREATING','VERIFYING','APPLYING','PREPARATION_PENDING','PLAN_REVIEW_REQUIRED','FULL_REVIEW_REQUIRED','REPLAN_REQUIRED','RECONCILE_REQUIRED','UNKNOWN','BLOCKED','STOPPED'].includes(String(p?.progress_code||'').toUpperCase())||!lifecycle;
}
function dbAdminCreationNext(p){
 const progress=String(p?.progress_code||'').toUpperCase();
 if(['UNKNOWN','RECONCILE_REQUIRED','BLOCKED','STOPPED'].includes(progress)||p?.blocker)return t('Reconcile the creation request');
 if(['CREATING','VERIFYING','APPLYING','PREPARATION_PENDING'].includes(progress))return t('Check creation progress');
 if(['PLAN_REVIEW_REQUIRED','FULL_REVIEW_REQUIRED','REPLAN_REQUIRED'].includes(progress))return t('Review the next approval');
 return t('Check creation status');
}
function dbAdminCreationHref(p){
 if(p?.creation_id&&dbCreation.environment_id)return '#databases/create?creation='+encodeURIComponent(p.creation_id)+'&creation_environment='+encodeURIComponent(dbCreation.environment_id);
 return '#databases/'+encodeURIComponent(p?.database_id||p?.creation_id||'');
}
function dbAdminSetRoute(hash){
 if(!globalThis.location||!globalThis.history?.replaceState)return;
 const url=new URL(globalThis.location.href);url.hash=hash;
 if(url.href!==globalThis.location.href)globalThis.history.replaceState(null,'',url);
}
function dbAdminRoute(){
 const page=workbenchRoute().page||'';
 if(page==='databases/create'||page==='databases/create-review')return {view:'create'};
 const retire=page.match(/^databases\/(retire|confirm)~([A-Za-z0-9_-]{1,128})$/);
 if(retire)return {view:'retire',id:retire[2],confirm:retire[1]==='confirm'};
 const panel=page.match(/^databases\/(access|metadata|operation)~([A-Za-z0-9_-]{1,128})$/);
 if(panel)return {view:'detail',id:panel[2],panel:panel[1]};
 const detail=page.match(/^databases\/([A-Za-z0-9_-]{1,128})$/);
 if(detail)return {view:'detail',id:detail[1],panel:'info'};
 if(page==='databases'||page==='db-admin')return {view:'list'};
 return null;
}
function dbAdminShowView(view){
 const changed=dbAdmin.view!==view;
 dbAdmin.view=view;
 for(const [id,shown] of [['db-admin-list-view',view==='list'],['db-admin-detail-view',view==='detail'],['db-admin-create-view',view==='create'],['db-admin-retire-view',view==='retire']]){
  const node=$(id);if(node)node.hidden=!shown;
 }
 if(view==='list')ui($('db-admin-title'),'Database Management');
 if(changed&&typeof workbenchScheduleScrollTop==='function')workbenchScheduleScrollTop();
}
function dbAdminLanguageChanged(){
 if(typeof dbAdmin==='undefined'||!dbAdmin.installed)return;
 dbAdminRenderList();dbAdminRenderSelection();
 if(dbAdmin.view==='detail'&&dbAdmin.database)dbAdminRenderDetail();
 if(dbAdmin.view==='create')dbCreationRender();
 if(dbAdmin.view==='retire')dbAdminRenderRetirePage();
 const snapshot=$('db-admin-retire-snapshot');
 if(dbAdmin.retireData&&snapshot&&!snapshot.hidden)dbAdminRetireRender(dbAdmin.retireData,!dbAdmin.retireData.operation,dbAdmin.retireUsers);
}
function dbAdminGate(){
 const allowed=dbAdminAllowed();$('tab-db-admin').hidden=!allowed;workbenchSyncMobileNav();
 if(!allowed||dbAdmin.retireReview&&dbAdmin.retireReview.scope!==dbAdminScope())dbAdminRetireInvalidate();
 if(!allowed){$('db-admin').hidden=true;if(typeof activeTab!=='undefined'&&activeTab==='db-admin')activate($('tab-warehouse'));}
 return allowed;
}
function dbAdminClear(){
 dbPlatformSync();
 dbCreationClear();
 dbAdminClearReadiness();
 dbAdminRetireInvalidate();dbAdmin.retirement=null;dbAdmin.retireRefresh=null;
 ++dbAdmin.epoch;++dbAdmin.selection;++dbAdmin.request;dbAdmin.database='';dbAdmin.databases=[];dbAdmin.drafts.clear();dbAdmin.requests.clear();dbAdmin.installed=false;dbAdmin.rendered=false;dbAdmin.panel='info';dbAdmin.view='list';dbAdmin.grants=[];dbAdmin.listQuery='';dbAdmin.listState='';dbAdmin.listTeam='';dbAdmin.listSort='name-asc';dbAdmin.listPinned=false;dbAdmin.listPage=1;dbAdmin.filtersOpen=false;dbAdmin.retireData=null;dbAdmin.retireUsers=[];dbAdmin.retireStep='impact';dbAdmin.retireAuto='';dbAdmin.missing='';dbAdmin.pendingCreation=null;dbAdmin.legacyCreation=null;
 $('db-admin-content').replaceChildren();message('db-admin-error','');dbAdminGate();dbUsageSync();dbCapacitySync();dbLifecycleHistorySync();
}
function dbAdminSession(session){
 const allowed=session.authenticated===true&&session.platform_admin===true&&session.maximum_role==='admin'&&session.data_mode==='database'&&JSON.stringify([session.username,session.authenticated])===dbPrincipal;
 if(dbAdmin.platform!==allowed){dbRuntimePurge();if(dbAudit.installed)dbAuditPurge();dbAdmin.platform=allowed;dbAdminClear();dbObservationsSync();}
 if(dbAudit.installed)dbAuditValidate();
 dbBaselineSync();dbFoundationBaselineSync();dbFoundationControllersSync();dbRecentFailuresSync();dbTelemetryReadinessSync();dbPodBaselineSync();dbControllerBaselineSync();dbManifestAdvisorySync();
 dbAISettingsSync();
 dbAdminGate();
}
function dbAdminScope(){return JSON.stringify([dbIdentity(),dbAuthorizationVersion,dbRoleVersion('admin'),dbInventoryVersion,dbMaximumRole,dbAdmin.platform,dbAdmin.epoch,dbAdmin.selection,dbAdmin.database]);}
function dbAdminOwner(){return JSON.stringify([dbPrincipal,currentRole,dbAdmin.epoch,dbAdmin.selection,dbAdmin.database]);}
function dbAdminStale(){return Object.assign(Error(t('Request context changed; response ignored.')),{stale:true});}
async function dbAdminRequest(path,{method='GET',payload,key,scope=dbAdminScope(),retirement=false,beforeSend}={}){
 const current=()=>scope===dbAdminScope()&&dbAdminAllowed();
 if(!current())throw dbAdminStale();
 // Every read, inspection and replay revalidates current platform authority.
 let auth,session;
 try{auth=await fetch('/api/session');session=await auth.json();}catch(error){if(current()){dbAdmin.platform=false;dbAdminClear();}throw error;}
 if(!current())throw dbAdminStale();
 dbAdminSession(auth.ok?session:{});
 if(!current())throw dbAdminStale();
 const headers={'X-Shirokuma-Role':'admin'};
 if(method!=='GET'){if(!key)throw Error('Idempotency-Key required');headers['Content-Type']='application/json';headers['Idempotency-Key']=key;}
 beforeSend?.();
 const response=await fetch(path,{method,headers,...(method==='GET'?{}:{body:payload})});
 if(!current())throw dbAdminStale();
 if([401,403].includes(response.status)){dbAdmin.platform=false;dbAdminClear();throw dbAdminStale();}
 let data;try{data=await response.json();}catch(error){if(!current())throw dbAdminStale();throw error;}
 if(!current())throw dbAdminStale();
 if(!response.ok||data?.error){
  const detail=retirement&&typeof data?.code==='string'?[data.code,data.message,data.details&&JSON.stringify(data.details)].filter(Boolean).join(': '):typeof data?.error==='string'?data.error:JSON.stringify(data?.error||response.status);
  throw Object.assign(Error(detail),{status:response.status});
 }
 return data;
}
function dbAdminNode(tag,id,parent,label){const n=document.createElement(tag);if(id)n.id=id;if(label)ui(n,label);if(parent)parent.append(n);return n;}
function dbAdminField(parent,id,label,tag='input'){
 const wrap=dbAdminNode('div','',parent),l=dbAdminNode('label','',wrap,label);l.htmlFor=id;return dbAdminNode(tag,id,wrap);
}
function dbAdminButton(parent,id,label,task){const b=dbAdminNode('button',id,parent,label);b.type='button';b.onclick=task;return b;}
function dbAdminInteger(node,min=1){const raw=node.value;if(!/^\d+$/.test(raw)||!Number.isSafeInteger(Number(raw))||Number(raw)<min)throw Error(t('Choose an exact integer revision.'));return Number(raw);}
function dbAdminDisplay(name,description){
 if(typeof name!=='string'||![...name].length||[...name].length>128||!name.trim()||/[\u0000-\u001f\u007f-\u009f]/u.test(name)||description!==undefined&&(typeof description!=='string'||[...description].length>2048||/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/u.test(description)))throw Error(t('Invalid display metadata.'));
}
function dbAdminResponse(kind,data,op){
 if(!data||typeof data!=='object')return false;
 if(kind==='retire')return dbAdminRetireOperation(data,op.databaseID,op.operationID);
 if(kind==='display')return typeof data.operation_id==='string'&&data.state==='APPLIED'&&Number.isSafeInteger(data.revision)&&data.revision>0&&data.resource_type===op.resourceType&&data.resource_id===op.resourceID;
 if(kind==='apply')return data.requires_operator===true&&typeof data.id==='string'&&data.state==='REQUESTED';
 if(kind==='register')return typeof data.operation_id==='string'&&typeof data.database_id==='string'&&data.state==='REGISTERED';
 if(kind==='grant')return typeof data.id==='string'&&['AWAITING_APPLY','APPLIED','SUPERSEDED'].includes(data.state);
 return typeof (data.id||data.operation_id)==='string'&&typeof data.state==='string';
}
function dbAdminText(value){return typeof value==='string'&&value.trim()!=='';}
function dbAdminRevision(value){return Number.isSafeInteger(value)&&value>0;}
function dbAdminWarehousePlan(data){
 return data?.action==='WAREHOUSE_CONFIG_APPLY'&&data.warehouse_id==='LOCAL'&&
  ['bootstrap','full'].includes(data.phase)&&dbAdminText(data.id)&&dbAdminText(data.generation_id);
}
function dbAdminReusablePlan(op){
 const data=op?.result;
 if(!dbAdminWarehousePlan(data)||data.state!=='AWAITING_APPLY'||!dbAdminRevision(data.revision)||!dbAdminText(data.delivery_digest))return false;
 const payload=JSON.parse(op.payload);
 return data.generation_id===payload.generation_id&&data.phase===payload.phase&&data.predecessor_id===payload.predecessor_id;
}
function dbAdminClearReadiness(){
 ++dbAdmin.readinessRead;
 $('db-admin-readiness')?.replaceChildren();
}
async function dbAdminCheckReadiness(){
 if(!dbAdminGate()||activeTab!=='db-admin')return;
 dbAdminClearReadiness();
 const result=$('db-admin-readiness'),id=$('db-admin-operation-id').value,target=dbAdmin.database;
 const selected=dbAdmin.databases.find(row=>row.id===target);
 const scope=dbAdminScope(),read=dbAdmin.readinessRead;
 const current=()=>read===dbAdmin.readinessRead&&scope===dbAdminScope()&&dbAdminAllowed()&&activeTab==='db-admin'&&
  target===dbAdmin.database&&target===$('db-admin-select').value&&id===$('db-admin-operation-id').value;
 const show=(label,values)=>ui(dbAdminNode('p','',result),label,values);
 if(!selected||!dbAdminText(id)){show('Select the target database and enter the operation ID.');return;}
 show('Checking database readiness…');
 try{
  const path='/api/v1/operations/'+encodeURIComponent(id),data=await dbAdminRequest(path,{scope});
  if(!current())return;
  result.replaceChildren();
  const databases=data?.manifest?.spec?.databases;
  const planned=databases&&Object.hasOwn(databases,target)?databases[target]:null;
  const revision=planned?.binding?.desired_revision;
  if(!dbAdminWarehousePlan(data)||data.id!==id||!dbAdminText(data.state)||
     planned?.sql_catalog!==selected.sql_catalog||!dbAdminRevision(revision)){
   show('Operation does not match the selected database and LOCAL plan.');return;
  }
  show('Phase {phase} · Operation {state} · Generation {generation}',{phase:data.phase,state:data.state,generation:data.generation_id});
  const handoff=Array.isArray(data.handoffs)&&data.handoffs.find(h=>h?.operation_id===id&&h.route==='/api/v1/operations/'+id+'/apply'&&
   h.requires_operator===true&&h.state==='REQUESTED'&&dbAdminText(h.id)&&dbAdminText(data.delivery_digest)&&
   h.payload?.delivery_digest===data.delivery_digest&&dbAdminRevision(h.payload.expected_revision)&&h.payload.expected_revision<=data.revision);
  if(handoff)show('Operator apply handoff recorded as REQUESTED ({id}).',{id:handoff.id});
  if(handoff&&data.state!=='APPLIED'&&data.state!=='PREPARED'){
   show('Review the operation status, then check readiness after the full apply is verified.');return;
  }
  if(data.state==='AWAITING_APPLY'){
   show('Review the operation revision and delivery digest, then request operator apply.');return;
  }
  if(data.phase==='bootstrap'&&data.state==='PREPARED'){
   show('Use this bootstrap operation ID as the predecessor when creating the full plan.');return;
  }
  const verify=Array.isArray(data.steps)?data.steps.filter(step=>step?.step==='verify'):[];
  const needsPredecessor=['REGISTERED','PREPARING'].includes(selected.state.toUpperCase());
  if(data.phase!=='full'||data.state!=='APPLIED'||needsPredecessor&&(!dbAdminText(data.predecessor_id)||data.predecessor_id===id)||
     verify.length!==1||verify[0].state!=='SUCCEEDED'||!dbAdminText(verify[0].evidence_ref)){
   show('Full apply verification is required to confirm readiness.');return;
  }
  const inventory=await dbAdminRequest('/api/v1/admin/databases',{scope});
  if(!current())return;
  const rows=Array.isArray(inventory?.databases)?inventory.databases.filter(row=>row?.id===target):[];
  const row=rows[0],binding=row?.binding;
  if(rows.length!==1||row.sql_catalog!==planned.sql_catalog||typeof row.state!=='string'||row.state.toUpperCase()!=='READY'||
     binding?.database_id!==target||binding.desired_revision!==revision||binding.applied_revision!==revision||
     binding.applied_generation_id!==data.generation_id){
   show('Current database readiness could not be confirmed for this generation.');return;
  }
  show('Database {id} is READY.',{id:target});
 }catch(error){if(!error.stale&&current()){result.replaceChildren();show('Could not check database readiness. Try again.');}}
}
function dbAdminUnresolved(){return [...dbAdmin.requests.values()].some(op=>op.pending||op.uncertain);}
function dbAdminMutationControls(id){
 const op=dbAdmin.requests.get(id);
 if(id==='db-admin-register')$('db-admin-use-registration').hidden=!dbAdminText(op?.result?.database_id)||op.pending;
 if(id==='db-admin-plan')$('db-admin-use-plan').hidden=!dbAdminReusablePlan(op)||op.pending;
 $(id+'-save').disabled=!!op&&(op.pending||op.uncertain)||id==='db-admin-retire'&&!dbAdminRetireConfirmed()||id==='db-admin-retire-reconcile'&&!dbAdminRetireCanReconcile();$(id+'-save').setAttribute('aria-busy',String(!!op?.pending));$(id+'-retry').disabled=!op?.uncertain||!!op?.pending;
 if(id==='db-admin-grant'&&$('db-admin-grant-revoke'))$('db-admin-grant-revoke').disabled=!!op&&(op.pending||op.uncertain);
 if($('db-admin-select'))$('db-admin-select').disabled=dbAdminUnresolved();
 if($('db-admin-selection-help'))$('db-admin-selection-help').hidden=!dbAdminUnresolved();
}
async function dbAdminMutate(id,build,replay=false){
 if(!dbAdminGate())return;
 let op=dbAdmin.requests.get(id);
 try{
  if(op?.pending)return;
  if(replay){if(!op?.uncertain||op.owner!==dbAdminOwner())return;}
  else{
   if(op?.uncertain)throw Error(t('A saved uncertain request must be reconciled before creating another request.'));
   const request=build();op={...request,payload:JSON.stringify(request.payload),key:crypto.randomUUID(),owner:dbAdminOwner(),uncertain:false,pending:false};dbAdmin.requests.set(id,op);
  }
  const scope=dbAdminScope();
  if(['register','plan','apply','retire'].includes(op.kind))dbAdminClearReadiness();
  if(op.kind==='retire')dbAdminRetireInvalidate();
  const read=dbAdmin.retireRead;
  const reviewedRetirement=op.kind==='retire'&&!replay;
  let sent=!reviewedRetirement;
  const beforeSend=reviewedRetirement?()=>{
   if(read!==dbAdmin.retireRead||activeTab!=='db-admin')throw dbAdminStale();
   sent=true;
  }:undefined;
  op.pending=true;dbAdminMutationControls(id);ui($(id+'-state'),'Request in progress.');
  try{
   const data=await dbAdminRequest(op.path,{method:op.method||'POST',payload:op.payload,key:op.key,scope,retirement:op.kind==='retire',beforeSend});
   if(scope!==dbAdminScope()||dbAdmin.requests.get(id)!==op)return;
   if(!dbAdminResponse(op.kind,data,op))throw Error(t('Outcome uncertain. Inspect or retry the saved request with the same key.'));
   op.uncertain=false;op.result=data;
   $(id+'-result').textContent=JSON.stringify(data,null,2);
   if(op.kind==='retire'){dbAdminRetireInvalidate();dbAdminRetireRender(data);}
   ui($(id+'-state'),op.kind==='apply'?'Operator handoff recorded. Check database readiness for current status.':op.kind==='register'?'Registered. Select the registered database, then prepare an operator plan.':op.kind==='display'?'Display metadata saved.':op.kind==='grant'&&data.state==='AWAITING_APPLY'?'Pending backend verification':data.state);
  }catch(error){
   if(!sent){
    if(dbAdmin.requests.get(id)===op&&dbAdminAllowed())ui($(id+'-state'),id==='db-admin-retire'?'Load a matching READY preview before starting retirement.':'Inspect the current retirement for the selected database before reconciling.');
    return;
   }
   if(error.stale||scope!==dbAdminScope()||dbAdmin.requests.get(id)!==op){op.uncertain=true;return;}
   // A rejection after an ambiguous attempt cannot disprove earlier acceptance.
   op.uncertain=op.uncertain||![400,404,409,422].includes(error.status);
   ui($(id+'-state'),op.uncertain?'Outcome uncertain. Inspect or retry the saved request with the same key.':'Request rejected; draft retained. Refresh and review revisions explicitly.');
   $(id+'-result').textContent=error.message;
  }finally{op.pending=false;if(dbAdmin.requests.get(id)===op&&dbAdminAllowed()){
   if(sent&&scope!==dbAdminScope()){op.uncertain=true;ui($(id+'-state'),'Outcome uncertain. Inspect or retry the saved request with the same key.');}
   dbAdminMutationControls(id);
  }}
 }catch(error){if(!error.fieldShown)message('db-admin-error',error.message);}
}
function dbAdminActions(parent,id,label,build){
 const actions=dbAdminNode('div','',parent);actions.className='form-actions';
 dbAdminButton(actions,id+'-save',label,()=>dbAdminMutate(id,build));
 const retry=dbAdminButton(actions,id+'-retry','Retry frozen request',()=>dbAdminMutate(id,build,true));retry.disabled=true;
 dbAdminNode('p',id+'-state',parent).setAttribute('role','status');dbAdminNode('pre',id+'-result',parent);
}
function dbAdminInstall(){
 if(dbAdmin.installed)return;dbAdmin.installed=true;dbAdmin.pinKey='';dbAdminLoadPins();
 ui($('tab-db-admin-label'),'Database Management');
 const root=$('db-admin-content');
 const listView=dbAdminNode('section','db-admin-list-view',root);listView.className='db-list-view';
 const listHead=dbAdminNode('div','',listView);listHead.className='pageheading db-heading';
 const listTitle=$('db-admin-title');if(listTitle.tagName!=='H1')listTitle.tagName='H1';listTitle.hidden=false;listTitle.tabIndex=-1;listHead.append(listTitle);ui(listTitle,'Database Management');
 const listActions=dbAdminNode('div','',listHead);listActions.className='db-actions';
 const create=dbAdminNode('a','db-admin-creation-entry',listActions,'Create database');create.className='primary';create.href='#databases/create';
 create.onclick=event=>{event=event||{};if(!dbAdminAllowed())return;if(event.metaKey||event.ctrlKey||event.shiftKey||event.altKey||(event.button!==undefined&&event.button!==0))return;event.preventDefault?.();dbAdminStartCreate();};
 dbAdminNode('p','db-admin-create-reason',listView,'Database creation requires current platform admin, maximum admin and the selected admin role.').className='help';
 $('db-admin-create-reason').hidden=true;
 const requestHistory=dbAdminNode('details','db-request-history',listView);requestHistory.className='section';requestHistory.open=false;
 dbAdminNode('summary','',requestHistory,'Creation requests and samples');
 dbAdminNode('ul','db-admin-request-history-list',requestHistory);
 const refresh=dbAdminNode('button','db-admin-refresh',requestHistory,'Refresh current revisions');refresh.type='button';refresh.className='small';refresh.onclick=dbAdminRefresh;
 const filters=dbAdminNode('form','db-admin-list-filters',listView);filters.className='db-filters';filters.setAttribute('role','search');filters.setAttribute('data-open','false');filters.setAttribute('aria-label',t('Search databases'));
 const queryLabel=dbAdminNode('label','db-admin-list-query-label',filters);queryLabel.className='db-search';queryLabel.htmlFor='db-admin-list-query';
 ui(dbAdminNode('span','db-admin-list-query-text',queryLabel),'Search databases');
 const query=dbAdminNode('input','db-admin-list-query',queryLabel);query.className='field';query.type='search';query.autocomplete='off';query.placeholder=t('Search DB, display name or project');
 query.oninput=()=>{dbAdmin.listQuery=query.value;dbAdmin.listPage=1;dbAdminRenderList();};
 const toggle=dbAdminNode('button','db-admin-filter-toggle',filters,'Database filters');toggle.type='button';toggle.className='db-filter-toggle';toggle.setAttribute('aria-expanded','false');toggle.setAttribute('aria-controls','db-admin-filter-controls');
 toggle.onclick=()=>{dbAdmin.filtersOpen=!dbAdmin.filtersOpen;dbAdminSyncFilterOpen();};
 const controls=dbAdminNode('div','db-admin-filter-controls',filters);controls.className='db-filter-controls';
 const stateLabel=dbAdminNode('label','db-admin-list-state-label',controls);stateLabel.htmlFor='db-admin-list-state';
 ui(dbAdminNode('span','db-admin-list-state-text',stateLabel),'Database state');
 const state=dbAdminNode('select','db-admin-list-state',stateLabel);state.className='field';
 state.onchange=()=>{dbAdmin.listState=state.value;dbAdmin.listPage=1;dbAdminRenderList();};
 const teamLabel=dbAdminNode('label','db-admin-list-team-label',controls);teamLabel.htmlFor='db-admin-list-team';
 ui(dbAdminNode('span','db-admin-list-team-text',teamLabel),'Team');
 const team=dbAdminNode('select','db-admin-list-team',teamLabel);team.className='field';
 team.onchange=()=>{dbAdmin.listTeam=team.value;dbAdmin.listPage=1;dbAdminRenderList();};
 const sortLabel=dbAdminNode('label','db-admin-list-sort-label',controls);sortLabel.htmlFor='db-admin-list-sort';
 ui(dbAdminNode('span','db-admin-list-sort-text',sortLabel),'Sort');
 const sort=dbAdminNode('select','db-admin-list-sort',sortLabel);sort.className='field';
 for(const [value,label] of [['name-asc',t('DB name A–Z')],['name-desc',t('DB name Z–A')]])sort.add(new Option(label,value));
 sort.onchange=()=>{dbAdmin.listSort=sort.value;dbAdmin.listPage=1;dbAdminRenderList();};
 const pinnedLabel=dbAdminNode('label','db-admin-list-pinned-label',controls);pinnedLabel.className='db-pinned';
 const pinned=dbAdminNode('input','db-admin-list-pinned',pinnedLabel);pinned.type='checkbox';
 dbAdminNode('span','db-admin-list-pinned-text',pinnedLabel,'Pinned only');
 pinned.onchange=()=>{dbAdmin.listPinned=pinned.checked;dbAdmin.listPage=1;dbAdminRenderList();};
 const reset=dbAdminNode('button','db-admin-list-reset',controls,'Reset filters');reset.type='reset';
 filters.onreset=event=>{event?.preventDefault?.();dbAdmin.listQuery='';dbAdmin.listState='';dbAdmin.listTeam='';dbAdmin.listPinned=false;dbAdmin.listSort='name-asc';dbAdmin.listPage=1;query.value='';state.value='';team.value='';sort.value='name-asc';pinned.checked=false;dbAdminRenderList();query.focus?.();};
 const paging=dbAdminNode('div','db-admin-paging',listView);paging.className='db-paging';paging.setAttribute('aria-label',t('Database pagination'));
 dbAdminNode('span','db-admin-range',paging).setAttribute('role','status');$('db-admin-range').setAttribute('aria-live','polite');$('db-admin-range').setAttribute('aria-atomic','true');
 for(const [page,label,aria] of [['first','First','First page'],['prev','Previous','Previous page'],['next','Next','Next page'],['last','Last','Last page']]){
  const button=dbAdminNode('button','db-admin-page-'+page,paging,t(label));button.type='button';button.dataset.dbPage=page;button.setAttribute('aria-label',t(aria));button.dataset.dbLabel=label;button.dataset.dbAria=aria;
  button.onclick=()=>dbAdminPage(page);
 }
 const updatedNote=dbAdminNode('p','db-admin-list-updated-note',listView,'Last updated: shown as “—” because the server does not provide an update time');updatedNote.className='help';updatedNote.hidden=true;
 const region=dbAdminNode('div','db-admin-list',listView);region.className='tablewrap';region.tabIndex=0;
 region.setAttribute('role','region');region.setAttribute('aria-labelledby','db-admin-list-caption');
 const listTable=dbAdminNode('table','',region);listTable.className='db-table';
 const caption=dbAdminNode('caption','db-admin-list-caption',listTable,'Database list');
 const listHeadRow=dbAdminNode('thead','',listTable),listHeadTr=dbAdminNode('tr','',listHeadRow);
 for(const [index,label] of ['DB name','Database state','Team','Last updated','Pin'].entries()){
  const th=dbAdminNode('th','db-admin-list-head-'+index,listHeadTr);th.scope='col';th.textContent=label==='Database state'?dbAdminStateHeading():t(label);
 }
 dbAdminNode('tbody','db-admin-list-rows',listTable);
 const empty=dbAdminNode('p','db-admin-list-empty',listView,'No databases match these filters. Change the search term or conditions.');empty.className='empty';empty.hidden=true;

 const detailView=dbAdminNode('section','db-admin-detail-view',root);detailView.hidden=true;
 const detail=dbAdminNode('div','db-admin-detail',detailView);
 const back=dbAdminNode('a','db-admin-back',detail,'← Back to list');back.className='backlink';back.href='#databases';
 const detailHead=dbAdminNode('div','',detail);detailHead.className='pageheading db-heading';
 const detailTitleLine=dbAdminNode('div','db-admin-detail-title-line',detailHead);detailTitleLine.className='db-title-line';
 const detailTitle=dbAdminNode('h1','db-admin-detail-title',detailTitleLine);detailTitle.className='db-title';detailTitle.tabIndex=-1;
 const detailState=dbAdminNode('div','db-admin-detail-state',detailTitleLine);detailState.setAttribute('role','status');
 const detailPin=dbAdminNode('div','db-admin-detail-pin',detailHead);detailPin.className='db-actions';
 const infoPanel=dbAdminNode('div','db-admin-info-panel',detail);infoPanel.className='db-info';
 dbAdminNode('p','db-admin-summary',infoPanel).className='db-context';$('db-admin-summary').hidden=true;
 dbAdminNode('p','db-admin-current-target',infoPanel,'Choose a database to manage.').setAttribute('role','status');
 dbAdminNode('p','db-admin-target-guidance',infoPanel).className='help';
 const subnav=dbAdminNode('nav','db-admin-subnav',infoPanel);subnav.className='db-actions';subnav.setAttribute('aria-label','Database management');
 const accessLink=dbAdminNode('a','db-admin-access-open',subnav,'Database access rights');
 const operationLink=dbAdminNode('a','db-admin-operation-open',subnav,'Operation history and apply plan');
 const metadataLink=dbAdminNode('a','db-admin-metadata-open',subnav,'Database and schema display information');
 const actions=dbAdminNode('div','db-admin-detail-actions',infoPanel);actions.className='db-actions';
 const useSQL=dbAdminButton(actions,'db-admin-use-sql','Use in SQL',dbAdminUseSQL);useSQL.className='primary';
 const retireLink=dbAdminNode('a','db-admin-retire-open',actions,'Review retirement impact');
 const info=dbAdminNode('dl','db-admin-info',infoPanel);info.className='kv';
 const operationPanel=dbAdminNode('section','db-admin-operation-panel',detail);operationPanel.className='db-flow';operationPanel.hidden=true;
 const operationBack=dbAdminNode('a','db-admin-operation-back',operationPanel,'← Back to database details');operationBack.className='backlink';operationBack.href='#databases';
 dbAdminNode('h2','',operationPanel,'Operation history and apply plan');
 const management=dbAdminNode('details','db-admin-management',operationPanel);dbAdminNode('summary','',management,'Manage');
 const select=dbAdminField(management,'db-admin-select','Management selection','select');
 select.onchange=()=>{if(dbAdminSelect(select.value))return dbAdminLoadGrants();};
 select.setAttribute('aria-describedby','db-admin-selection-help');
 const selectionHelp=dbAdminNode('p','db-admin-selection-help',management);selectionHelp.className='help';selectionHelp.hidden=true;ui(selectionHelp,'Resolve pending or uncertain requests before changing the management database.');
 const registration=dbAdminNode('details','',management);dbAdminNode('summary','',registration,'Register a database manually');
 dbAdminNode('p','',registration,'Registration creates registry intent and four fixed schemas. Backend verification is pending.');
 const catalog=dbAdminField(registration,'db-admin-register-name','SQL catalog name');
 dbAdminActions(registration,'db-admin-register','Register database',()=>{
  if(!/^[a-z][a-z0-9_]{0,30}$/.test(catalog.value)||['system','information_schema','tpch','tpcds','iceberg'].includes(catalog.value))throw Error(t('Invalid SQL catalog name'));
  return {kind:'register',path:'/api/v1/databases',payload:{sql_catalog:catalog.value,profile_id:'local-polaris-1.8.0'}};
 });
 const useRegistration=dbAdminButton(registration,'db-admin-use-registration','Use registered database ID',async()=>{
  const registration=dbAdmin.requests.get('db-admin-register'),id=registration?.result?.database_id,scope=dbAdminScope();
  if(!dbAdminAllowed()||activeTab!=='db-admin'||!dbAdminText(id)||dbAdminUnresolved())return;
  const refreshed=await dbAdminRefresh();
  if(scope!==dbAdminScope()||!dbAdminAllowed()||activeTab!=='db-admin'||dbAdmin.requests.get('db-admin-register')!==registration)return;
  const sqlCatalog=JSON.parse(registration.payload).sql_catalog;
  if(!refreshed||!dbAdmin.databases.some(row=>row.id===id&&row.sql_catalog===sqlCatalog)){
   ui($('db-admin-register-state'),'Could not select the registered database. Refresh current revisions and select its ID.');return;
  }
  select.value=id;const grants=select.onchange();
  if(dbAdminAllowed()&&activeTab==='db-admin'&&dbAdmin.database===id){plan.open=true;generation.focus();}
  return grants;
 });
 useRegistration.hidden=true;
 const plan=dbAdminNode('details','',management);dbAdminNode('summary','',plan,'Prepare an operator plan');
 dbAdminNode('p','',plan,'Use operator-prepared material to create a bootstrap or full plan. The operator supplies the public generation ID.');
 const generation=dbAdminField(plan,'db-admin-generation','Generation ID'),phase=dbAdminField(plan,'db-admin-phase','Phase','select'),predecessor=dbAdminField(plan,'db-admin-predecessor','Predecessor operation ID');
 for(const value of ['bootstrap','full']){const o=new Option(value,value);ui(o,value);phase.add(o);}phase.value='bootstrap';
 dbAdminActions(plan,'db-admin-plan','Create plan',()=>({kind:'plan',path:'/api/v1/warehouses/LOCAL/plans',payload:{generation_id:generation.value,phase:phase.value,predecessor_id:predecessor.value||null}}));
 const operation=dbAdminNode('details','',management);dbAdminNode('summary','',operation,'Request apply or verify readiness');
 const operationID=dbAdminField(operation,'db-admin-operation-id','Operation ID');
 operationID.oninput=dbAdminClearReadiness;
 const inspection=dbAdminNode('details','',operation);dbAdminNode('summary','',inspection,'Operation details');
 dbAdminButton(inspection,'db-admin-inspect','Inspect operation',async()=>{
  const scope=dbAdminScope(),request=++dbAdmin.request,id=operationID.value;
  try{const data=await dbAdminRequest('/api/v1/operations/'+encodeURIComponent(id),{scope});if(scope===dbAdminScope()&&request===dbAdmin.request&&id===operationID.value)$('db-admin-inspection').textContent=JSON.stringify(data,null,2);}catch(error){if(!error.stale&&scope===dbAdminScope()&&request===dbAdmin.request&&id===operationID.value)message('db-admin-error',error.message);}
 });
 dbAdminNode('pre','db-admin-inspection',inspection);
 dbAdminNode('p','',operation,'Apply records a public handoff (requires_operator). A private operator must execute and verify it; this does not make the DB READY.');
 const expected=dbAdminField(operation,'db-admin-apply-revision','Expected revision'),digest=dbAdminField(operation,'db-admin-delivery','Delivery digest');
 dbAdminActions(operation,'db-admin-apply','Request operator apply',()=>({kind:'apply',path:'/api/v1/operations/'+encodeURIComponent(operationID.value)+'/apply',payload:{expected_revision:dbAdminInteger(expected),delivery_digest:digest.value}}));
 dbAdminNode('p','',operation,'Select the target database and enter the operation ID.').className='help';
 dbAdminButton(operation,'db-admin-check-readiness','Check database readiness',dbAdminCheckReadiness);
 dbAdminNode('div','db-admin-readiness',operation).setAttribute('role','status');
 const usePlan=dbAdminButton(plan,'db-admin-use-plan','Use plan for operator apply',()=>{
  const op=dbAdmin.requests.get('db-admin-plan');
  if(!dbAdminAllowed()||dbAdminUnresolved()||!dbAdminReusablePlan(op))return;
  operationID.value=op.result.id;expected.value=String(op.result.revision);digest.value=op.result.delivery_digest;
  dbAdminClearReadiness();operation.open=true;operationID.focus();
 });
 usePlan.hidden=true;
 const metadataPanel=dbAdminNode('section','db-admin-metadata-panel',detail);metadataPanel.hidden=true;metadataPanel.className='db-flow';metadataPanel.setAttribute('aria-labelledby','db-admin-metadata-heading');
 const metadataBack=dbAdminNode('a','db-admin-metadata-back',metadataPanel,'← Back to database details');metadataBack.className='backlink';metadataBack.href='#databases';
 const metadataHeading=dbAdminNode('h2','db-admin-metadata-heading',metadataPanel,'Database and schema display information');
 const metadataDetails=dbAdminNode('details','',metadataPanel);metadataDetails.open=true;dbAdminNode('summary','',metadataDetails,'Display information');
 dbAdminNode('p','',metadataDetails,'Safe binding metadata');dbAdminNode('pre','db-admin-binding',metadataDetails);
 dbAdminNode('div','db-admin-metadata',metadataDetails);dbAdminNode('h3','',metadataDetails,'Schema display metadata');dbAdminNode('div','db-admin-schemas',metadataDetails);
 const accessPanel=dbAdminNode('section','db-admin-access-panel',detail);accessPanel.hidden=true;accessPanel.className='db-flow';accessPanel.setAttribute('aria-labelledby','db-admin-access-heading');
 const accessBack=dbAdminNode('a','db-admin-access-back',accessPanel,'← Back to database details');accessBack.className='backlink';accessBack.href='#databases';
 const accessHeading=dbAdminNode('h2','db-admin-access-heading',accessPanel,'Database access rights');
 const grants=dbAdminNode('details','',accessPanel);grants.open=true;dbAdminNode('summary','',grants,'Desired grants');dbAdminNode('p','',grants,'Higher privileges take effect after backend verification. Restrictions and revocation can take effect immediately.');
 dbAdminNode('div','db-admin-grants',grants);
 const principal=dbAdminField(grants,'db-admin-grant-principal','Principal ID'),role=dbAdminField(grants,'db-admin-grant-role','Desired role','select'),revision=dbAdminField(grants,'db-admin-grant-revision','Expected revision');revision.value='0';
 for(const value of ['bi_viewer','data_engineer','admin']){const option=new Option(value,value);if(value==='admin')ui(option,'Admin');role.add(option);}role.value='bi_viewer';
 dbAdminNode('p','db-admin-grant-admin-help',grants,'Admin targets must already have a global admin ceiling.').className='help';
 const build=revoke=>({kind:'grant',path:'/api/v1/databases/'+encodeURIComponent(dbAdmin.database)+'/grants',payload:{principal_id:principal.value,maximum_role:revoke?'revoked':role.value,expected_revision:dbAdminInteger(revision,0)}});
 dbAdminActions(grants,'db-admin-grant','Save desired grant',()=>build(false));
 const revoke=dbAdminButton(grants,'db-admin-grant-revoke','Revoke grant',()=>dbAdminMutate('db-admin-grant',()=>build(true)));revoke.className='danger';
 const createView=dbAdminNode('section','db-admin-create-view',root);createView.hidden=true;dbCreationInstall(createView);
 const retireView=dbAdminNode('section','db-admin-retire-view',root);retireView.hidden=true;dbAdminRetireInstall(retireView);
}

function dbAdminMetadata(parent,resource,type){
 const id='db-admin-'+type+'-'+resource.id;
 let draft=dbAdmin.drafts.get(id);
 if(!draft){draft={revision:resource.revision};dbAdmin.drafts.set(id,draft);
  const box=dbAdminNode('section',id,parent);box.className='panel-body db-p1-row';
  dbAdminNode('h3','',box).textContent=resource.sql_catalog||resource.sql_name;
  if(type==='schema')ui(dbAdminNode('p','',box),'{name} · {purpose}',{name:resource.sql_name,purpose:resource.purpose});
  const name=dbAdminField(box,id+'-name','Display name');name.value=resource.display_name;
  const description=type==='database'?dbAdminField(box,id+'-description','Description','textarea'):null;if(description)description.value=resource.description;
  const team=type==='database'?dbAdminField(box,id+'-team','Team'):null;
  const teamError=type==='database'?dbAdminNode('p',id+'-team-error',box):null;
  if(teamError){teamError.className='help';teamError.setAttribute('role','alert');teamError.hidden=true;}
  if(team){team.value=resource.team||'';team.oninput=()=>{team.setAttribute('aria-invalid','false');if(teamError){teamError.textContent='';teamError.hidden=true;}};}
  const expected=dbAdminField(box,id+'-revision','Expected revision');expected.value=String(resource.revision);expected.inputMode='numeric';
  dbAdminNode('p',id+'-current',box);
  dbAdminButton(box,id+'-use','Use current revision',()=>{expected.value=String(draft.revision);});
  dbAdminActions(box,id,'Save display metadata',()=>{
   dbAdminDisplay(name.value,description?.value);
   let nextTeam=null;
   if(team){
    const showTeamError=message=>{teamError.textContent=message;teamError.hidden=false;team.setAttribute('aria-invalid','true');team.focus();throw Object.assign(Error(message),{fieldShown:true});};
    if(resource.team&&!team.value.trim())showTeamError(t('Team must be 1 to 64 characters without control characters.'));
    if(team.value.trim()){try{nextTeam=dbAdminTeam(team.value);}catch(error){showTeamError(error.message);}}
   }
   return {kind:'display',resourceType:type,resourceID:resource.id,method:'PATCH',path:'/api/v1/'+(type==='database'?'databases':'schemas')+'/'+encodeURIComponent(resource.id)+'/display',payload:{display_name:name.value,...(description?{description:description.value}:{}),...(nextTeam?{team:nextTeam}:{}),expected_revision:dbAdminInteger(expected)}};
  });
 }
 draft.revision=resource.revision;ui($(id+'-current'),'Current revision: {revision}',{revision:resource.revision});
}
function dbAdminRenderDetail(){
 if(!dbAdmin.installed)dbAdminInstall();
 const d=dbAdmin.databases.find(d=>d.id===dbAdmin.database);
 const target=$('db-admin-current-target');
 if(d){target.textContent='';target.hidden=true;}else{ui(target,'Choose a database to manage.');target.hidden=false;}
 ui($('db-admin-target-guidance'),d&&['REGISTERED','PREPARING'].includes(d.state.toUpperCase())?'Use the operator plan to request apply, then verify database readiness.':'');
 if(!d)return;
 dbAdmin.rendered=true;
 const state=String(d.state||'').toUpperCase(),ready=state==='READY';
 $('db-admin-detail-title').textContent=dbAdminName(d);
 $('db-admin-summary').textContent=[d.display_name,d.sql_catalog].filter(Boolean).join(' · ');
 const stateNode=$('db-admin-detail-state');stateNode.replaceChildren();
 const status=dbAdminNode('span','',stateNode,dbAdminStateLabel(d.state));status.className=('status '+dbAdminStatusClass(d.state)).trim();
 const info=$('db-admin-info');info.replaceChildren();
 for(const [label,value] of [['Display name',d.display_name||'—'],['Description',d.description||'—'],['Team',d.team||t('Not set')],['Database state',dbAdminStateLabel(d.state)],['Database revision number',String(d.revision)]]){
  const term=dbAdminNode('dt','',info);term.textContent=label==='Database revision number'?dbAdminRevisionHeading():label==='Database state'?dbAdminStateHeading():t(label);
  dbAdminNode('dd','',info).textContent=value;
 }
 dbAdminRenderDetailPin();
 $('db-admin-use-sql').hidden=!ready;$('db-admin-use-sql').disabled=!ready;
 const idPath=encodeURIComponent(d.id);
 $('db-admin-access-open').href='#databases/access~'+idPath;
 $('db-admin-operation-open').href='#databases/operation~'+idPath;
 $('db-admin-metadata-open').href='#databases/metadata~'+idPath;
 $('db-admin-metadata-back').href='#databases/'+idPath;
 $('db-admin-access-back').href='#databases/'+idPath;
 $('db-admin-operation-back').href='#databases/'+idPath;
 const retire=$('db-admin-retire-open');if(retire){retire.href='#databases/retire~'+idPath;retire.hidden=!ready;}
 const safe=['revision','polaris_realm','polaris_catalog','desired_revision','applied_revision','state'];
 $('db-admin-binding').textContent=JSON.stringify(Object.fromEntries(safe.filter(k=>Object.hasOwn(d.binding,k)).map(k=>[k,d.binding[k]])),null,2);
 if(dbAdmin.metadataID!==d.id){
  $('db-admin-metadata').replaceChildren();$('db-admin-schemas').replaceChildren();dbAdmin.metadataID=d.id;
 }
 dbAdminMetadata($('db-admin-metadata'),d,'database');for(const schema of d.schemas)dbAdminMetadata($('db-admin-schemas'),schema,'schema');
 const panel=dbAdmin.panel||'info';
 $('db-admin-info-panel').hidden=panel!=='info';
 $('db-admin-access-panel').hidden=panel!=='access';
 $('db-admin-metadata-panel').hidden=panel!=='metadata';
 $('db-admin-operation-panel').hidden=panel!=='operation';
}

async function dbAdminLoadGrants(){
 if(!dbAdmin.database)return;const scope=dbAdminScope(),request=++dbAdmin.request;
 try{const data=await dbAdminRequest('/api/v1/databases/'+encodeURIComponent(dbAdmin.database)+'/grants',{scope});if(scope!==dbAdminScope()||request!==dbAdmin.request)return;
  dbAdmin.grants=Array.isArray(data.grants)?data.grants:[];
  table($('db-admin-grants'),['Principal ID','Desired role','Effective role','Desired revision','Applied revision','Effective epoch',dbAdminStateHeading()],data.grants.map(g=>[g.principal_id,g.maximum_role==='revoked'?t('Revoked'):dbRoleDisplayLabel(g.maximum_role),g.effective_role==='revoked'?t('Revoked'):dbRoleDisplayLabel(g.effective_role),g.revision,g.applied_revision??'—',g.grant_revision,t(g.maximum_role==='revoked'?'Revoked':g.applied_revision===g.revision&&g.maximum_role===g.effective_role?'Effective':'Pending backend verification')]));
 }catch(error){if(!error.stale&&scope===dbAdminScope()&&request===dbAdmin.request)message('db-admin-error',error.message);}
}
async function dbAdminRefresh(){
 if(!dbAdminGate())return;dbAdminInstall();dbAdminClearReadiness();dbAdminRetireInvalidate();const scope=dbAdminScope(),request=++dbAdmin.request;
 const refresh={};dbAdmin.retireRefresh=refresh;
 try{const data=await dbAdminRequest('/api/v1/admin/databases',{scope});if(scope!==dbAdminScope()||request!==dbAdmin.request)return;
  if(!Array.isArray(data?.databases)||data.databases.some(d=>!d||typeof d.id!=='string'||typeof d.sql_catalog!=='string'||typeof d.display_name!=='string'||typeof d.description!=='string'||d.team!=null&&typeof d.team!=='string'||typeof d.state!=='string'||!Number.isSafeInteger(d.revision)||d.revision<1||!Array.isArray(d.schemas)||d.schemas.length!==4||new Set(d.schemas.map(s=>s?.sql_name)).size!==4||!d.binding||typeof d.binding!=='object'||d.schemas.some(s=>!s||typeof s.id!=='string'||!['raw','staging','marts','sandbox'].includes(s.sql_name)||typeof s.display_name!=='string'||typeof s.purpose!=='string'||!Number.isSafeInteger(s.revision)||s.revision<1)))throw Error(t('Read model mismatch: expected databases with fixed schemas and safe binding metadata.'));
  dbAdmin.databases=data.databases;
  if(dbAdmin.database&&!data.databases.some(d=>d.id===dbAdmin.database))dbAdminSelect('');
  dbAdminRenderSelection();
  dbAdminRenderList();dbAdminApplyRoute();
  message('db-admin-error','');await dbAdminLoadGrants();return true;
 }catch(error){if(!error.stale&&scope===dbAdminScope()&&request===dbAdmin.request)message('db-admin-error',error.message);}
 finally{if(dbAdmin.retireRefresh===refresh)dbAdmin.retireRefresh=null;}
}
const dbAdminPageSize=25;
function dbAdminListRows(){
 const query=dbAdmin.listQuery.trim().toLowerCase(),state=dbAdmin.listState,team=dbAdmin.listTeam;
 return dbAdmin.databases.filter(d=>{
  const name=[d.sql_catalog,d.display_name,d.project,d.description,d.id].filter(Boolean).join(' ').toLowerCase();
  return (!query||name.includes(query))&&(!state||String(d.state||'').toUpperCase()===state)&&
   (!team||(team==='__unset__'?!d.team:(d.team||'')===team))&&(!dbAdmin.listPinned||dbAdmin.pins.has(d.id));
 }).sort((a,b)=>{
  if(dbAdmin.listSort.startsWith('updated')){
   const av=dbAdminUpdatedValue(a),bv=dbAdminUpdatedValue(b);
   if(av||bv){const compared=String(av||'').localeCompare(String(bv||''));return dbAdmin.listSort==='updated-asc'?compared:-compared;}
  }
  const compared=dbAdminName(a).localeCompare(dbAdminName(b),'ja');
  return (dbAdmin.listSort==='name-desc'?-compared:compared)||String(a.id).localeCompare(String(b.id),'en');
 });
}
function dbAdminStatusClass(state){
 const value=String(state||'').toUpperCase();
 if(value==='READY')return 'success';
 if(value==='FAILED')return 'error';
 if(['REGISTERED','PREPARING','RETIRING','UNKNOWN','CREATING','VERIFYING','APPLYING','PREPARATION_PENDING','PLAN_REVIEW_REQUIRED','FULL_REVIEW_REQUIRED','REPLAN_REQUIRED','RECONCILE_REQUIRED','BLOCKED','STOPPED'].includes(value))return 'warning';
 return '';
}
function dbAdminRenderSelection(){
 const select=$('db-admin-select');if(!select)return;
 const placeholder=new Option(t('Choose database'),'');ui(placeholder,'Choose database');select.replaceChildren(placeholder);
 for(const d of dbAdmin.databases)select.add(new Option([d.display_name,d.sql_catalog,dbAdminStateLabel(d.state)].filter(Boolean).join(' · '),d.id));
 select.value=dbAdmin.database;
}
function dbAdminSyncFilterOpen(){
 const filters=$('db-admin-list-filters'),toggle=$('db-admin-filter-toggle');if(!filters||!toggle)return;
 filters.dataset.open=String(dbAdmin.filtersOpen);
 toggle.setAttribute('aria-expanded',String(dbAdmin.filtersOpen));
 const count=dbAdminFilterCount();
 toggle.textContent=count?t('Database filters ({count})',{count}):t('Database filters');
}
function dbAdminPage(direction){
 const rows=dbAdminListRows(),pages=Math.max(1,Math.ceil(rows.length/dbAdminPageSize));
 dbAdmin.listPage=direction==='first'?1:direction==='last'?pages:direction==='prev'?Math.max(1,dbAdmin.listPage-1):Math.min(pages,dbAdmin.listPage+1);
 dbAdminRenderList();
}
function dbAdminPinButton(d,prefix='db-admin-pin-'){
 const pinned=dbAdmin.pins.has(d.id);
 const button=dbAdminNode('button',prefix+d.id,'',pinned?'★':'☆');button.type='button';button.className='db-pin';
 button.setAttribute('aria-pressed',String(pinned));button.setAttribute('aria-label',t(pinned?'Unpin {name}':'Pin {name}',{name:dbAdminName(d)}));
 button.onclick=event=>{event?.stopPropagation?.();dbAdminTogglePin(d.id);};
 return button;
}
function dbAdminRenderDetailPin(){
 const host=$('db-admin-detail-pin');if(!host)return;
 const d=dbAdmin.databases.find(row=>row.id===dbAdmin.database);host.replaceChildren();
 if(d)host.append(dbAdminPinButton(d,'db-admin-detail-pin-'));
}
function dbAdminSyncOptions(select,options){
 if(!select)return;
 const same=select.options.length===options.length&&options.every(([value,label],index)=>select.options[index]?.value===value&&select.options[index]?.textContent===label);
 if(!same){select.replaceChildren();for(const [value,label] of options){const option=new Option(label,value);option.value=value;option.textContent=label;select.add(option);}}
 if(!options.some(([value])=>value===select.value))select.value=options[0]?.[0]??'';
}
function dbAdminRenderRequestHistory(){
 const list=$('db-admin-request-history-list');if(!list)return;
 const records=dbAdminCreationRecords().filter(dbAdminCreationOpen);
 list.replaceChildren();
 if(!records.length){const empty=dbAdminNode('li','',list);ui(empty,'No in-progress or stopped creation requests.');return;}
 for(const p of records){
  const item=dbAdminNode('li','',list),link=dbAdminNode('a','',item,dbAdminName(p)||p.sql_catalog||'');
  link.href=dbAdminCreationHref(p);
  const status=dbAdminNode('span','',item,dbCreationLogicalLabel(String(p.progress_code||p.lifecycle||'BLOCKED').toUpperCase()));status.className=('status '+dbAdminStatusClass(p.progress_code||p.lifecycle)).trim();
  ui(dbAdminNode('span','',item),'Next: {next}',{next:dbAdminCreationNext(p)});
 }
}
function dbAdminDim(node,dim){if(node?.style)node.style.opacity=dim?'0.5':'';}
function dbAdminRenderList(){
 const region=$('db-admin-list');if(!region)return;
 dbAdminLoadPins();
 const states=[...new Set(dbAdmin.databases.map(d=>String(d.state||'').toUpperCase()).filter(Boolean))].sort();
 const teams=[...new Set(dbAdmin.databases.map(d=>d.team).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'ja'));
 if(!['name-asc','name-desc'].includes(dbAdmin.listSort))dbAdmin.listSort='name-asc';
 const stateFilter=$('db-admin-list-state'),teamFilter=$('db-admin-list-team'),sortFilter=$('db-admin-list-sort');
 dbAdminSyncOptions(stateFilter,[['',t('All states')],...states.map(value=>[value,dbAdminStateLabel(value)])]);
 dbAdminSyncOptions(teamFilter,[['',t('All teams')],...teams.map(value=>[value,value]),['__unset__',t('Not set')]]);
 dbAdminSyncOptions(sortFilter,[['name-asc',t('DB name A–Z')],['name-desc',t('DB name Z–A')]]);
 for(const [id,key] of [['db-admin-list-state-text','Database state'],['db-admin-list-team-text','Team'],['db-admin-list-sort-text','Sort'],['db-admin-list-pinned-text','Pinned only'],['db-admin-list-query-text','Search databases']]){const label=$(id);if(label)label.textContent=t(key);}
 const resetButton=$('db-admin-list-reset');if(resetButton)resetButton.textContent=t('Reset filters');
 if(!states.includes(dbAdmin.listState))dbAdmin.listState='';
 if(dbAdmin.listTeam!=='__unset__'&&!teams.includes(dbAdmin.listTeam))dbAdmin.listTeam='';
 if(stateFilter)stateFilter.value=dbAdmin.listState;
 if(teamFilter)teamFilter.value=dbAdmin.listTeam;
 if(sortFilter)sortFilter.value=dbAdmin.listSort;
 const hasUpdated=dbAdmin.databases.some(d=>dbAdminUpdatedValue(d));
 for(const [index,label] of ['DB name','Database state','Team','Last updated','Pin'].entries()){
  const th=$('db-admin-list-head-'+index);if(!th)continue;
  th.textContent=label==='Database state'?dbAdminStateHeading():t(label);
  if(label==='Last updated'){
   if(hasUpdated)th.removeAttribute('title');
   else th.title=t('Last updated unavailable from the server');
  }
 }
 const updatedNote=$('db-admin-list-updated-note');if(updatedNote){updatedNote.hidden=hasUpdated;updatedNote.textContent=hasUpdated?'':t('Last updated: shown as “—” because the server does not provide an update time');}
 const pinnedFilter=$('db-admin-list-pinned');if(pinnedFilter)pinnedFilter.checked=dbAdmin.listPinned;
 const queryFilter=$('db-admin-list-query');if(queryFilter)queryFilter.placeholder=t('Search DB, display name or project');
 const paging=$('db-admin-paging');if(paging)paging.setAttribute('aria-label',t('Database pagination'));
 const rows=dbAdminListRows(),pages=Math.max(1,Math.ceil(rows.length/dbAdminPageSize));
 dbAdmin.listPage=Math.max(1,Math.min(dbAdmin.listPage,pages));
 const start=(dbAdmin.listPage-1)*dbAdminPageSize,visible=rows.slice(start,start+dbAdminPageSize);
 const body=$('db-admin-list-rows');body.replaceChildren();
 const creations=dbAdminCreationRecords();
 for(const d of visible){
  const tr=dbAdminNode('tr','',body);tr.dataset.dbRow=d.id;
  tr.onclick=event=>{if(event?.target?.closest?.('a,button,input,select,textarea'))return;dbAdminSelect(d.id);};
  const nameCell=dbAdminNode('th','',tr);nameCell.scope='row';
  const primary=dbAdminName(d),link=dbAdminNode('a','db-admin-link-'+d.id,nameCell,primary);link.href='#databases/'+encodeURIComponent(d.id);
  const detail=[...new Set([d.project,d.sql_catalog!==primary?d.sql_catalog:'',d.description!==primary?d.description:''].filter(Boolean))].join(' · ');if(detail)dbAdminNode('small','',nameCell,detail);
  const creation=creations.find(p=>p.database_id===d.id&&dbAdminCreationOpen(p));
  if(creation){const next=dbAdminNode('small','',nameCell);const nextLink=dbAdminNode('a','',next,t('Next: {next}',{next:dbAdminCreationNext(creation)}));nextLink.href=dbAdminCreationHref(creation);}
  const stateCell=dbAdminNode('td','',tr);stateCell.dataset.label=dbAdminStateHeading();
  const status=dbAdminNode('span','',stateCell,dbAdminStateLabel(d.state));status.className=('status '+dbAdminStatusClass(d.state)).trim();
  const teamCell=dbAdminNode('td','',tr,d.team||t('Not set'));teamCell.dataset.label=t('Team');
  const updatedCell=dbAdminNode('td','',tr);updatedCell.dataset.label=t('Last updated');
  const updated=dbAdminUpdatedValue(d);
  if(updated){const time=dbAdminNode('time','',updatedCell,dbAdminUpdatedLabel(d));time.setAttribute('datetime',updated);}
  else{
   updatedCell.textContent='—';
   updatedCell.title=t('Last updated unavailable from the server');
   updatedCell.setAttribute('aria-label',t('Last updated unavailable from the server'));
  }
  const pinCell=dbAdminNode('td','',tr);pinCell.dataset.label=t('Pin');pinCell.append(dbAdminPinButton(d));
 }
 region.rows=visible.map(d=>[dbAdminName(d),dbAdminStateLabel(d.state),d.team||t('Not set'),dbAdminUpdatedLabel(d)]);
 region.columns=['DB name','State','Team','Last updated','Pin'];
 const rangeStart=rows.length?start+1:0,rangeEnd=Math.min(start+dbAdminPageSize,rows.length);
 const all=rows.length!==dbAdmin.databases.length?t(' (of {total})',{total:dbAdmin.databases.length}):'';
 const range=$('db-admin-range');if(range)range.textContent=t('{start}–{end} / {total} items · Page {page} / {pages}',{start:rangeStart,end:rangeEnd,total:rows.length+all,page:dbAdmin.listPage,pages});
 const atEdge=dbAdmin.listPage===1,lastEdge=dbAdmin.listPage===pages;
 for(const [name,edge] of [['first',atEdge],['prev',atEdge],['next',lastEdge],['last',lastEdge]]){
  const button=$('db-admin-page-'+name);if(!button)continue;
  button.textContent=t(button.dataset.dbLabel||name);button.setAttribute('aria-label',t(button.dataset.dbAria||name));
  button.setAttribute('aria-disabled',String(edge));dbAdminDim(button,edge);
 }
 const empty=$('db-admin-list-empty');if(empty)empty.hidden=rows.length!==0;
 dbAdminSyncFilterOpen();dbAdminRenderRequestHistory();
 const reason=$('db-admin-create-reason'),entry=$('db-admin-creation-entry');
 if(reason)reason.hidden=dbAdminAllowed();
 if(entry){entry.setAttribute('aria-disabled',String(!dbAdminAllowed()));dbAdminDim(entry,!dbAdminAllowed());}
}
function dbAdminStartCreate(){
 dbAdminShowView('create');dbAdminSetRoute('databases/create');
 if(dbCreation.pending){$('db-creation-title')?.focus();return;}
 dbCreationStop();++dbCreation.read;dbCreation.process=null;dbCreation.review=null;dbCreation.stage=0;
 dbCreation.capability=null;dbCreation.error='';dbCreationResetHistory();dbCreationURL(null);
 dbCreationSave();dbCreationRender();$('db-creation-title')?.focus();void dbCreationLoad();
}
function dbAdminApplyRoute(){
 const route=dbAdminRoute();
 if(dbAdmin.legacyCreation?.id&&dbCreation.process?.creation_id===dbAdmin.legacyCreation.id&&route?.view==='list'){
  const id=dbAdmin.legacyCreation.id,params=workbenchRoute().params;dbAdmin.legacyCreation=null;
  params.set('creation',id);params.set('creation_environment',dbCreation.environment_id||'');
  dbAdminSetRoute('databases/create?'+params.toString());
  dbAdminShowView('create');return;
 }
 if(route?.view==='create'){dbAdmin.legacyCreation=null;dbAdminShowView('create');return;}
 if(route?.view==='retire'){
  if(dbAdmin.databases.some(d=>d.id===route.id)){
   if(dbAdmin.database!==route.id&&!dbAdminSelect(route.id,{navigate:false})){dbAdminShowView('list');dbAdminRenderList();return;}
   dbAdmin.retireStep=route.confirm?'confirm':'impact';
   dbAdminShowView('retire');dbAdminRenderRetirePage();
   const key=route.id+':'+dbAdmin.retireStep;
   if(dbAdmin.retireAuto!==key){
    dbAdmin.retireAuto=key;
    if(!dbAdmin.retireData||dbAdmin.retireData.database?.id!==route.id)void dbAdminRetireRead(true);
   }
   return;
  }
  dbAdminShowView('list');dbAdminRenderList();return;
 }
 if(route?.view==='detail'){
  if(dbAdmin.databases.some(d=>d.id===route.id)){
   if(dbAdmin.database!==route.id&&!dbAdminSelect(route.id,{navigate:false})){dbAdminShowView('list');dbAdminRenderList();return;}
   dbAdmin.panel=route.panel||'info';dbAdminShowView('detail');dbAdminRenderDetail();return;
  }
  const creation=dbCreation.list.find(p=>p.creation_id===route.id||p.database_id===route.id)||(dbCreation.process?.creation_id===route.id?dbCreation.process:null);
  if(creation){dbAdminShowView('create');if(dbCreation.process?.creation_id!==route.id)void dbCreationLoad(false,route.id);return;}
  dbAdmin.missing=route.id;dbAdminShowView('list');dbAdminRenderList();return;
 }
 if(!route&&dbCreation.process?.creation_id&&!dbAdmin.database){dbAdminShowView('create');return;}
 if(!route&&dbAdmin.database&&dbAdmin.databases.some(d=>d.id===dbAdmin.database)){
  dbAdminShowView('detail');dbAdminRenderDetail();return;
 }
 dbAdminShowView('list');dbAdminRenderList();
}
function dbAdminOpen(){if(dbAdminGate()){dbAdminInstall();if(typeof workbenchScheduleScrollTop==='function')workbenchScheduleScrollTop();return Promise.all([dbAdminRefresh(),dbCreationLoad()]).then(()=>dbAdminApplyRoute());}}
function dbAdminSelect(id,{navigate=true}={}){
 if(!id){
  if(navigate)dbAdminSetRoute('databases');
  dbAdmin.database='';
  dbAdminShowView('list');dbAdminRenderList();return true;
 }
 if(id===dbAdmin.database){
  if(navigate)dbAdminSetRoute('databases/'+encodeURIComponent(id));
  dbAdmin.panel=dbAdminRoute()?.panel||dbAdmin.panel||'info';dbAdminShowView('detail');dbAdminRenderDetail();return true;
 }
 if(dbAdminUnresolved()){
  $('db-admin-select').value=dbAdmin.database;
  message('db-admin-error',t('Resolve pending or uncertain requests before changing the management database.'));
  return false;
 }
 if(navigate)dbAdminSetRoute('databases/'+encodeURIComponent(id));
 dbAdminClearReadiness();
 dbAdminRetireInvalidate();++dbAdmin.selection;++dbAdmin.request;dbAdmin.retirement=null;dbAdmin.database=id;dbAdmin.panel=dbAdminRoute()?.panel||'info';dbAdmin.drafts.clear();dbAdmin.requests.clear();dbAdmin.rendered=false;dbAdmin.metadataID=null;dbAdmin.grants=[];
 $('db-admin-metadata').replaceChildren();$('db-admin-schemas').replaceChildren();$('db-admin-grants').replaceChildren();
 $('db-admin-inspection').textContent='';$('db-admin-select').value=id;
 for(const action of ['register','plan','apply']){const key='db-admin-'+action;$(key+'-state').textContent='';$(key+'-result').textContent='';dbAdminMutationControls(key);}
 dbAdminShowView('detail');dbAdminRenderDetail();
 return true;
}
async function dbAdminUseSQL(){
 const scope=dbAdminScope(),id=dbAdmin.database;if(!id)return;
 try{
  const items=await dbReadInventory(currentRole);if(scope!==dbAdminScope()||!dbInventoryCurrent(items))return;
  dbApplyInventory(items,currentRole);
  const d=items.find(d=>d.id===id&&d.state==='READY'&&d.capabilities?.query&&d.allowed_roles?.includes(currentRole));
  if(!d)throw Object.assign(Error(t('READY; SQL use unavailable for the selected role.')),{code:'READY; SQL use unavailable for the selected role.'});
  const result=await dbAdminRequest('/api/v1/databases/'+encodeURIComponent(id)+'/schemas',{scope});
  if(scope!==dbAdminScope()||!dbInventoryCurrent(items)||dbAdmin.database!==id)return;
  const permitted=result.items?.filter(s=>s.database_id===id&&s.capabilities?.query&&s.allowed_modes?.includes('read-only'))||[];
  const schema=permitted.find(s=>s.sql_name==='raw')||permitted[0];
  if(!schema)throw Object.assign(Error(t('READY; SQL use unavailable for the selected role.')),{code:'READY; SQL use unavailable for the selected role.'});
  dbSave();const number=Math.max(0,...worksheets.map(w=>Number(/^Worksheet (\d+)$/.exec(w.name)?.[1]||0)))+1;
  const w={id:crypto.randomUUID(),name:'Worksheet '+number,sql:'',database_id:id,schema_id:schema.id,binding_revision:d.binding_revision,generation_id:d.generation_id,warehouse:'LOCAL',mode:'read-only',max_rows:100,selected_role:currentRole};
  worksheets.push(w);worksheetId=w.id;$('sql').value='';dbPersist();const refresh=renderWorksheets(),identity=dbTabIdentity(),request=dbRefreshRequest,input=JSON.stringify(dbInput(w));dbRenderRun();activate($('tab-warehouse'));await refresh;
  if(identity===dbTabIdentity()&&request===dbRefreshRequest&&w===dbCurrent()&&input===JSON.stringify(dbInput(w))&&activeTab==='warehouse'&&dbWorkspacePane==='worksheet')$('sql').focus();
 }catch(error){if(scope===dbAdminScope())message('db-admin-error',error.message);}
}

function dbAdminRetireDatabase(d,id){
 const expected=dbAdmin.databases.find(row=>row.id===id);
 return d&&typeof d==='object'&&Object.keys(d).sort().join(',')==='id,revision,sql_catalog,state'&&d.id===id&&typeof d.sql_catalog==='string'&&!!d.sql_catalog&&(!expected||d.sql_catalog===expected.sql_catalog)&&typeof d.state==='string'&&!!d.state&&Number.isSafeInteger(d.revision)&&d.revision>0;
}
function dbAdminRetireDependencies(rows,id,blocker=false){
 const base=['kind','id','state','database_id',...(blocker?['code']:[])];
 const links=['job_id','stage_id','mart_id','model_id','operation_id','run_id','scan_id','schedule_id','subject_id','subject_kind','target_id','target_kind','parent_id'];
 return Array.isArray(rows)&&rows.every(row=>{
  if(row&&row.kind==='resource_ref')return !blocker&&Object.keys(row).sort().join(',')==='database_id,id,incarnation,kind,object_id,schema_id,state'&&[...base,'schema_id','object_id'].every(k=>typeof row[k]==='string'&&!!row[k])&&row.database_id===id&&row.state==='RETAINED'&&row.id===row.object_id&&Number.isSafeInteger(row.incarnation)&&row.incarnation>0;
  return row&&typeof row==='object'&&base.every(k=>typeof row[k]==='string'&&!!row[k])&&row.database_id===id&&Object.entries(row).every(([k,v])=>typeof v==='string'&&!!v&&(base.includes(k)||links.includes(k)));
 });
}
function dbAdminRetireOperation(data,id=dbAdmin.database,operationID){
 return data&&Object.keys(data).sort().join(',')==='action,actor,blockers,database,database_id,dependencies,id,revision,state,stopped_schedule_ids'&&data.action==='DATABASE_RETIRE'&&typeof data.id==='string'&&!!data.id&&(!operationID||data.id===operationID)&&typeof data.actor==='string'&&!!data.actor&&data.database_id===id&&Number.isSafeInteger(data.revision)&&data.revision>0&&['RETIRING','RETIRED'].includes(data.state)&&dbAdminRetireDatabase(data.database,id)&&data.database.state===data.state.toLowerCase()&&dbAdminRetireDependencies(data.dependencies,id)&&dbAdminRetireDependencies(data.blockers,id,true)&&Array.isArray(data.stopped_schedule_ids)&&data.stopped_schedule_ids.every(v=>typeof v==='string'&&!!v)&&new Set(data.stopped_schedule_ids).size===data.stopped_schedule_ids.length;
}
function dbAdminRetirePreview(data){
 if(!(data&&Object.keys(data).sort().join(',')==='blockers,database,dependencies,operation,stopped_schedule_ids'&&dbAdminRetireDatabase(data.database,dbAdmin.database)&&dbAdminRetireDependencies(data.dependencies,dbAdmin.database)&&dbAdminRetireDependencies(data.blockers,dbAdmin.database,true)&&Array.isArray(data.stopped_schedule_ids)))return false;
 const op=data.operation;
 if(op===null)return data.stopped_schedule_ids.length===0;
 return dbAdminRetireOperation(op)&&Object.keys(data.database).every(key=>op.database[key]===data.database[key])&&data.stopped_schedule_ids.length===op.stopped_schedule_ids.length&&data.stopped_schedule_ids.every((v,i)=>v===op.stopped_schedule_ids[i]);
}
function dbAdminRetireInvalidate(){
 ++dbAdmin.retireRead;dbAdmin.retireReview=null;dbAdmin.retireReceiptScope=null;dbAdmin.retireData=null;dbAdmin.retireUsers=[];dbAdmin.retireAuto='';
 const confirm=$('db-admin-retire-confirm'),save=$('db-admin-retire-save');
 if(confirm?.parentElement){confirm.checked=false;confirm.parentElement.hidden=true;}
 if(save)save.disabled=true;
 if($('db-admin-retire-reconcile-save'))$('db-admin-retire-reconcile-save').disabled=true;
 if($('db-admin-retire-snapshot')&&!dbAdmin.retirement)$('db-admin-retire-snapshot').hidden=true;
 if($('db-admin-retire-impact-summary')){$('db-admin-retire-impact-summary').hidden=true;$('db-admin-retire-impact-summary').textContent='';}
 if($('db-admin-retire-users'))$('db-admin-retire-users').replaceChildren();
}
function dbAdminRetireMatches(database){
 const selected=dbAdmin.databases.find(row=>row.id===dbAdmin.database);
 return selected?.id===database.id&&selected.sql_catalog===database.sql_catalog&&
  selected.state.toLowerCase()==='ready'&&database.state.toLowerCase()==='ready'&&selected.revision===database.revision;
}
function dbAdminRetireConfirmed(){
 const review=dbAdmin.retireReview;
 return dbAdminAllowed()&&activeTab==='db-admin'&&!dbAdmin.retireRefresh&&!dbAdmin.retirement&&
  !!review&&review.scope===dbAdminScope()&&dbAdminRetireMatches(review.database)&&$('db-admin-retire-confirm').checked;
}
function dbAdminRetireCanReconcile(){
 const op=dbAdmin.retirement;
 return dbAdminAllowed()&&activeTab==='db-admin'&&op?.state==='RETIRING'&&dbAdmin.retireReceiptScope===dbAdminScope()&&
  op.database_id===dbAdmin.database&&op.id===$('db-admin-retire-operation').value&&String(op.revision)===$('db-admin-retire-op-revision').value;
}
function dbAdminKindLabel(kind){
 return t({run:'Job run',schedule:'Schedule',schedule_tick:'Schedule tick',stage_scan:'Stage scan',mart_run:'Published Mart run',resource_ref:'Retained data',job:'Job',stage:'Stage',mart:'Published Mart',model:'Transformation model'}[String(kind||'')]||'Related work');
}
function dbAdminWorkStateLabel(state){
 const value=String(state||'').toUpperCase();
 return t({ENABLED:'Enabled',SCHEDULED:'Scheduled',STOPPED:'Stopped',RUNNING:'Running',UNKNOWN:'Outcome unknown',SUCCEEDED:'Completed',FAILED:'Failed',PENDING:'Pending',RETAINED:'Retained'}[value]||'Unknown');
}
function dbAdminBlockerLabel(code){
 return t({RUN_UNKNOWN:'Run outcome unknown',SCAN_UNKNOWN:'Scan outcome unknown',MART_RUN_UNKNOWN:'Published Mart run unknown',RESOURCE_RETAINED:'Retained data awaiting review'}[String(code||'')]||'Review the result');
}
function dbAdminRetireVisibleTable(node,columns,rows,blocker=false){
 node.replaceChildren();node.rows=rows;node.columns=columns;
 if(!rows.length){const empty=dbAdminNode('p','',node,'None');empty.className='empty';return;}
 const groups=new Map();
 for(const row of rows){
  const key=[row.kind||'',row.state||'',blocker?row.code||'':'',row.target_kind||''].join('\u0000');
  groups.set(key,(groups.get(key)||0)+1);
 }
 const element=dbAdminNode('table','',node),head=dbAdminNode('thead','',element),headRow=dbAdminNode('tr','',head);
 for(const label of blocker?['Kind','State','Reason','Count']:['Kind','State','Target','Count']){const th=dbAdminNode('th','',headRow,label);th.scope='col';}
 const body=dbAdminNode('tbody','',element);
 for(const [key,count] of groups){
  const [kind,state,code,target]=key.split('\u0000'),tr=dbAdminNode('tr','',body);
  for(const value of blocker?[dbAdminKindLabel(kind),dbAdminWorkStateLabel(state),dbAdminBlockerLabel(code),String(count)]:[dbAdminKindLabel(kind),dbAdminWorkStateLabel(state),target?dbAdminKindLabel(target):'—',String(count)])dbAdminNode('td','',tr,value);
 }
}
function dbAdminRetireRender(data,preview=false,users=[]){
 const op=preview?data.operation:data;
 dbAdmin.retireData=data;dbAdmin.retireUsers=users;
 if(op)dbAdmin.retirement=op;
 const d=data.database,selected=dbAdmin.databases.find(row=>row.id===d.id);
 $('db-admin-retire-snapshot').hidden=false;
 const identity=[...new Set([selected?.display_name,d.sql_catalog].filter(Boolean))].join(' · ')||d.sql_catalog;
 ui($('db-admin-retire-summary'),op?t('{database} · Database revision {revision} · {operation} · Operation revision {opRevision}',{database:identity,revision:d.revision,operation:dbAdminStateLabel(op.state),opRevision:op.revision}):t('{database} · Database revision {revision}',{database:identity,revision:d.revision}));
 const impact=$('db-admin-retire-impact'),closure=$('db-admin-retire-closure');
 $('db-admin-retire-scope').hidden=!!op;
 if(op){
  ui(impact,'Saved stopped schedules {schedules} · Blockers in receipt {blockers}',{schedules:op.stopped_schedule_ids.length,blockers:data.blockers.length});
  ui(closure,op.state==='RETIRING'
   ?'Retiring · New work is blocked. UNKNOWN keeps retirement open until closure is verified.'
   :'Retired · Retirement is complete. New work remains blocked.');
 }else{
  ui($('db-admin-retire-scope'),'Observed {time} · Scope: selected database · Role: {role}',{time:fmtJST(new Date().toISOString()),role:dbRoleDisplayLabel('admin')});
  ui(impact,'This snapshot can change. Starting retirement requires the reviewed database revision.');
 }
 const summary=$('db-admin-retire-impact-summary');
 if(summary){
  if(preview&&!op){
   ui(summary,'{name} will be retired. {count} users will lose access.',{name:selected?.display_name||d.sql_catalog,count:users.length});
   summary.hidden=false;
  }else{summary.hidden=true;summary.textContent='';}
 }
 if($('db-admin-retire-users'))table($('db-admin-retire-users'),['Principal ID','Role'],users.map(user=>[user.principal_id,dbRoleDisplayLabel(user.role)]));
 closure.hidden=!op;
 $('db-admin-retire-planned-section').hidden=!!op;
 $('db-admin-retire-stopped-section').hidden=!op;
 const schedules=data.dependencies.filter(row=>row.kind==='schedule'&&['ENABLED','SCHEDULED'].includes(row.state));
 const plannedNode=$('db-admin-retire-planned');dbAdminRetireVisibleTable(plannedNode,['ID',dbAdminStateHeading(),'Target kind','Target ID'],schedules);
 plannedNode.rows=schedules.map(row=>[row.id,t(row.state),row.target_kind??'',row.target_id??'']);
 for(const [name,rows] of [['dependencies',data.dependencies],['blockers',data.blockers]]){
  const node=$('db-admin-retire-'+name);dbAdminRetireVisibleTable(node,['Kind','ID',dbAdminStateHeading(),'Database ID',...(name==='blockers'?['Code']:[]),'Identity links'],rows,name==='blockers');
  node.rows=rows.map(row=>[row.kind,row.id,row.state,row.database_id,...(name==='blockers'?[row.code]:[]),JSON.stringify(Object.fromEntries(Object.entries(row).filter(([k])=>!['kind','id','state','database_id','code'].includes(k))))]);
 }
 const stopped=op?.stopped_schedule_ids||[];
 $('db-admin-retire-stopped').textContent='';
 const stoppedCount=$('db-admin-retire-stopped-count');if(stoppedCount)ui(stoppedCount,'Stopped schedules: {count}',{count:stopped.length});
 if(op){
  dbAdmin.retireReceiptScope=dbAdminScope();
  $('db-admin-retire-operation').value=op.id;
  $('db-admin-retire-op-revision').value=String(op.revision);
  dbAdminMutationControls('db-admin-retire-reconcile');
 }
 dbAdminRenderRetirePage();
}
async function dbAdminRetireRead(preview){
 if(!dbAdminAllowed()||activeTab!=='db-admin')return;
 dbAdminRetireInvalidate();
 const scope=dbAdminScope(),request=dbAdmin.retireRead,id=$('db-admin-retire-operation').value;
 try{
  if(!preview&&!/^[A-Za-z0-9_-]{1,128}$/.test(id))throw Error(t('Invalid retirement response.'));
  const path=preview?'/api/v1/admin/databases/'+encodeURIComponent(dbAdmin.database)+'/retire':'/api/v1/operations/'+encodeURIComponent(id);
  const data=await dbAdminRequest(path,{scope,retirement:true});
  if(scope!==dbAdminScope()||request!==dbAdmin.retireRead||activeTab!=='db-admin'||!preview&&id!==$('db-admin-retire-operation').value)return;
  if(!(preview?dbAdminRetirePreview(data):dbAdminRetireOperation(data,dbAdmin.database,id)))throw Error(t('Invalid retirement response.'));
  let users=[];
  if(preview){
   const access=await dbAdminRequest('/api/v1/databases/'+encodeURIComponent(dbAdmin.database)+'/grants',{scope});
   if(scope!==dbAdminScope()||request!==dbAdmin.retireRead||activeTab!=='db-admin')return;
   if(!Array.isArray(access?.grants))throw Error(t('Invalid retirement response.'));
   users=access.grants.filter(g=>g&&typeof g.principal_id==='string'&&g.maximum_role!=='revoked'&&g.effective_role!=='revoked')
    .map(g=>({principal_id:g.principal_id,role:g.effective_role||g.maximum_role}));
  }
  if(preview&&!data.operation){
   if(dbAdmin.retireRefresh||dbAdmin.retirement||!dbAdminRetireMatches(data.database))throw Error(t('The preview no longer matches the selected READY database. Refresh current revisions and preview again.'));
   if([...dbAdmin.requests.values()].some(op=>op.kind==='retire'&&(op.pending||op.uncertain)))return;
   dbAdmin.retireReview={scope,database:data.database,users};
   $('db-admin-retire-confirm').parentElement.hidden=false;
  }
  dbAdminRetireRender(data,preview,users);message('db-admin-error','');
 }catch(error){if(!error.stale&&scope===dbAdminScope()&&request===dbAdmin.retireRead)message('db-admin-error',error.message);}
}
function dbAdminRetireInstall(parent){
 const box=dbAdminNode('section','db-admin-retirement',parent);box.className='db-flow';
 box.setAttribute('aria-labelledby','db-admin-retire-heading');
 const back=dbAdminNode('a','db-admin-retire-back',box,'← Back to database details');back.className='backlink';back.href='#databases';
 dbAdminNode('h1','db-admin-retire-heading',box,'Retirement impact').tabIndex=-1;
 const steps=dbAdminNode('ol','db-admin-retire-steps',box);steps.className='db-steps';
 const stepImpact=dbAdminNode('li','',steps,'Review impact'),stepConfirm=dbAdminNode('li','',steps,'Review target and start');
 const facts=dbAdminNode('dl','',box);facts.className='kv';
 for(const [label,id] of [['Target database','db-admin-retire-target'],['Current state','db-admin-retire-target-state'],['Team','db-admin-retire-target-team']]){
  dbAdminNode('dt','',facts,label);const dd=dbAdminNode('dd',id,facts);if(id.endsWith('state'))dd.setAttribute('role','status');
 }
 const snapshot=dbAdminNode('div','db-admin-retire-snapshot',box);snapshot.hidden=true;
 dbAdminNode('p','db-admin-retire-summary',snapshot).setAttribute('role','status');
 dbAdminNode('p','db-admin-retire-scope',snapshot).className='small muted';
 dbAdminNode('p','db-admin-retire-impact',snapshot);
 dbAdminNode('p','db-admin-retire-closure',snapshot).hidden=true;
 const impactPanel=dbAdminNode('div','db-admin-retire-impact-panel',box);
 const planned=dbAdminNode('section','db-admin-retire-planned-section',impactPanel);
 const userSection=dbAdminNode('section','',impactPanel);
 const dependencySection=dbAdminNode('section','',impactPanel);
 const blockerSection=dbAdminNode('section','',impactPanel);
 const retentionSection=dbAdminNode('section','',impactPanel);
 const stoppedSection=dbAdminNode('div','db-admin-retire-stopped-section',impactPanel);
 const addTable=(parent,id,label,href)=>{
  const heading=dbAdminNode('h2',id+'-heading',parent,label);
  const region=dbAdminNode('div',id,parent);region.className='retirement-table';region.tabIndex=0;
  region.setAttribute('role','region');region.setAttribute('aria-labelledby',heading.id);return region;
 };
 addTable(planned,'db-admin-retire-planned','Schedules to stop');
 addTable(dependencySection,'db-admin-retire-dependencies','Dependencies');
 addTable(blockerSection,'db-admin-retire-blockers','Work blocking the start');
 addTable(userSection,'db-admin-retire-users','Users losing access');
 dbAdminNode('h2','',retentionSection,'Data retention');
 dbAdminNode('p','db-admin-retire-retention',retentionSection,'This operation retains the DB ID, SQL name, catalog, tables, object storage, PVCs and credentials. Physical removal is separate.');
 dbAdminNode('h2','',stoppedSection,'Stopped schedules');
 dbAdminNode('p','db-admin-retire-stopped-count',stoppedSection);
 const stopped=dbAdminNode('pre','db-admin-retire-stopped',stoppedSection);stopped.hidden=true;
 const impactActions=dbAdminNode('div','db-admin-retire-impact-actions',impactPanel);impactActions.className='db-actions';
 const cancel=dbAdminNode('a','db-admin-retire-cancel',impactActions,'Cancel');cancel.href='#databases';
 const next=dbAdminNode('a','db-admin-retire-next',impactActions,'Continue to target confirmation');next.className='primary';
 next.onclick=event=>{
  const data=dbAdmin.retireData;if(!data||data.blockers?.length||!dbAdmin.retireReview){event?.preventDefault?.();return;}
  event?.preventDefault?.();dbAdmin.retireStep='confirm';dbAdminSetRoute('databases/confirm~'+encodeURIComponent(dbAdmin.database));dbAdminShowView('retire');dbAdminRenderRetirePage();
 };
 const confirmPanel=dbAdminNode('div','db-admin-retire-confirm-panel',box);confirmPanel.hidden=true;
 const confirmIntro=dbAdminNode('p','',confirmPanel,'Review the impact and the target database before starting retirement.');confirmIntro.className='impact';
 const review=dbAdminNode('label','',confirmPanel);review.className='retirement-review';review.hidden=true;
 const confirm=dbAdminNode('input','db-admin-retire-confirm',review);confirm.type='checkbox';
 dbAdminNode('span','',review,'I reviewed the impact and agree to start retirement for this database.');
 confirm.onchange=()=>dbAdminMutationControls('db-admin-retire');
 const impactSummary=dbAdminNode('p','db-admin-retire-impact-summary',confirmPanel);impactSummary.className='impact';impactSummary.setAttribute('role','status');impactSummary.hidden=true;
 const confirmBack=dbAdminNode('a','db-admin-retire-confirm-back',confirmPanel,'Review impact');confirmBack.href='#databases/retire~'+encodeURIComponent(dbAdmin.database);
 dbAdminActions(confirmPanel,'db-admin-retire','Retire database',()=>buildRetirement(false));
 $('db-admin-retire-save').className='primary danger';$('db-admin-retire-save').disabled=true;
 const operationPanel=dbAdminNode('details','db-admin-retire-operation-panel',box);operationPanel.open=false;
 dbAdminNode('summary','',operationPanel,'利用終了の操作記録');
 dbAdminButton(operationPanel,'db-admin-retire-preview','Review impact',()=>dbAdminRetireRead(true));
 dbAdminNode('p','',operationPanel,'Review the impact for a READY target before starting retirement. Reconciliation checks in-flight and unresolved work.').className='help';
 const operation=dbAdminField(operationPanel,'db-admin-retire-operation','Operation ID (for reconciliation, optional)'),expected=dbAdminField(operationPanel,'db-admin-retire-op-revision','Operation revision');expected.inputMode='numeric';
 expected.readOnly=true;
 operation.oninput=()=>dbAdminMutationControls('db-admin-retire-reconcile');
 dbAdminButton(operationPanel,'db-admin-retire-inspect','Inspect operation',()=>dbAdminRetireRead(false));
 dbAdminActions(operationPanel,'db-admin-retire-reconcile','Reconcile operation',()=>buildRetirement(true));
 $('db-admin-retire-reconcile-save').disabled=true;
 function buildRetirement(reconcile){
  if([...dbAdmin.requests].some(([key,op])=>key!==(reconcile?'db-admin-retire-reconcile':'db-admin-retire')&&op.kind==='retire'&&(op.pending||op.uncertain)))throw Error(t('A saved uncertain request must be reconciled before creating another request.'));
  if(reconcile&&!dbAdminRetireCanReconcile())throw Error(t('Inspect the current retirement for the selected database before reconciling.'));
  if(!reconcile&&!dbAdminRetireConfirmed()){dbAdminRetireInvalidate();throw Error(t('Load a matching READY preview before starting retirement.'));}
  return {kind:'retire',databaseID:dbAdmin.database,...(reconcile?{operationID:operation.value}:{}),path:reconcile?'/api/v1/operations/'+encodeURIComponent(operation.value)+'/reconcile':'/api/v1/admin/databases/'+encodeURIComponent(dbAdmin.database)+'/retire',payload:{expected_revision:reconcile?dbAdminInteger(expected):dbAdmin.retireReview.database.revision}};
 }
 dbAdmin.retireControls={stepImpact,stepConfirm,impactPanel,confirmPanel,impactActions,next,back};
}
function dbAdminRenderRetirePage(){
 const controls=dbAdmin.retireControls;if(!controls)return;
 const confirm=dbAdmin.retireStep==='confirm',d=dbAdmin.databases.find(row=>row.id===dbAdmin.database);
 controls.impactPanel.hidden=false;controls.confirmPanel.hidden=!confirm;if(controls.impactActions)controls.impactActions.hidden=confirm;
 if(confirm){controls.stepImpact.removeAttribute?.('aria-current');controls.stepConfirm.setAttribute('aria-current','step');}
 else{controls.stepImpact.setAttribute('aria-current','step');controls.stepConfirm.removeAttribute?.('aria-current');}
 const heading=$('db-admin-retire-heading');if(heading)heading.textContent=t(confirm?'Retirement confirmation':'Retirement impact');
 const back=$('db-admin-retire-back');if(back)back.href='#databases/'+encodeURIComponent(dbAdmin.database);
 const cancel=$('db-admin-retire-cancel');if(cancel)cancel.href='#databases/'+encodeURIComponent(dbAdmin.database);
 const confirmBack=$('db-admin-retire-confirm-back');if(confirmBack)confirmBack.href='#databases/retire~'+encodeURIComponent(dbAdmin.database);
 if(d){
  const target=$('db-admin-retire-target');if(target)target.textContent=[...new Set([dbAdminName(d),d.sql_catalog].filter(Boolean))].join(' · ');
  const team=$('db-admin-retire-target-team');if(team)team.textContent=d.team||t('Not set');
  const state=$('db-admin-retire-target-state');if(state){state.replaceChildren();const status=dbAdminNode('span','',state,dbAdminStateLabel(d.state));status.className=('status '+dbAdminStatusClass(d.state)).trim();}
 }
 const next=controls.next,data=dbAdmin.retireData,blocked=!data||!!data.blockers?.length||!dbAdmin.retireReview;
 if(next){next.href='#databases/confirm~'+encodeURIComponent(dbAdmin.database);next.setAttribute('aria-disabled',String(blocked));dbAdminDim(next,blocked);}
}
