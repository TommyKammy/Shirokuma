'use strict';
function dbCreationAvailable(){return dbCreation.capability?.available===true&&(!dbCreationLogical()||dbCreation.capability?.warehouse_state==='ACTIVE');}
function dbCreationFormMode(){
 const cap=dbCreation.capability;
 if(!cap||!dbCreationEnvironment(cap))return null;
 return cap.creation_mode==='logical_v1'?'logical_v1':!Object.hasOwn(cap,'creation_mode')?'legacy':null;
}
function dbCreationLogicalLabel(progress){return t(({CREATING:'Creating database',VERIFYING:'Verifying creation',UNKNOWN:'Creation outcome unknown',READY:'Ready',BLOCKED:'Creation blocked',STOPPED:'Creation stopped'})[progress]||'Creation blocked');}
function dbCreationLogical(){return (dbCreation.process?.creation_id?dbCreation.process:dbCreation.capability)?.creation_mode==='logical_v1';}
function dbCreationPollDelay(){
 const p=dbCreation.process;
 return dbCreationLogical()&&['CREATING','VERIFYING'].includes(p?.progress_code)&&p?.blocker!=='PRINCIPAL_STOPPED'&&!['READY','RETIRING','RETIRED'].includes((p?.lifecycle||'').toUpperCase())?1000:5000;
}
function dbCreationObservation(){
 const node=$('db-creation-observation');if(!node)return;
 node.hidden=!dbCreation.readFailed||(!dbCreation.process?.creation_id&&(!dbCreation.pending||dbCreation.pending.busy));
 node.textContent=node.hidden?'':t('Status check failed. Displaying the last known state, not a new result.');
}
function dbCreationJourney(){
 const root=$('db-creation-journey');root.replaceChildren();
 if(dbCreationLogical()){
  if(dbCreation.process?.creation_id)return;
  const list=dbAdminNode('ol','',root);list.className='db-steps';list.setAttribute('aria-label',t('Create database'));
  const current=dbCreation.stage===2?2:0;
  for(const [index,label] of ['Name and team','Initial access','Review and create'].entries()){
   const item=dbAdminNode('li','',list,label);if(index===current)item.setAttribute('aria-current','step');
  }
  return;
 }
 if(!dbCreation.process?.creation_id&&!dbCreationFormMode()||dbCreation.process?.creation_id&&!dbCreation.process.progress_code)return;
 const p=dbCreation.process,plans=p?.plans||[],foundation=plans.filter(v=>v.phase==='bootstrap'&&v.purpose==='APPLY').at(-1);
 const phase=plans.some(v=>v.phase==='full'&&v.purpose==='APPLY')||foundation?.state==='PREPARED';
 const terminal=['RETIRING','RETIRED'].includes((p?.lifecycle||'').toUpperCase());
 const current=p?(p.lifecycle?.toUpperCase()==='READY'?5:phase?4:3):dbCreation.stage;
 const list=dbAdminNode('ol','',root);list.className='db-steps';list.setAttribute('aria-label',t('Create database'));
 for(const [index,label] of ['Name','Initial access','Review request','Foundation setup (approval 1/2)','SQL connection setup (approval 2/2)','Ready'].entries()){
  const item=dbAdminNode('li','',list,label);if(index===current&&!terminal)item.setAttribute('aria-current','step');
 }
}
function dbCreationPhase(parent,plan){
 const section=dbAdminNode('div','',parent);
 const p=dbCreation.process,plans=p?.plans||[],bootstrap=plans.filter(v=>v.phase==='bootstrap'&&v.purpose==='APPLY').at(-1);
 const current=plan.phase===(plans.some(v=>v.phase==='full'&&v.purpose==='APPLY')||bootstrap?.state==='PREPARED'?'full':'bootstrap')&&!['READY','RETIRING','RETIRED'].includes((p?.lifecycle||'').toUpperCase());
 const running=current&&['APPLYING','VERIFYING'].includes(p?.progress_code),attention=current&&['STOPPED','BLOCKED','REPLAN_REQUIRED','RECONCILE_REQUIRED'].includes(p?.progress_code);
 const state=running?'Running':attention?'Needs attention':['PREPARED','APPLIED','SUCCEEDED'].includes(plan.state)?'Completed':plan.state==='AWAITING_APPLY'?'Awaiting approval':['PREPARING','DRAINING','APPLYING','VERIFYING'].includes(plan.state)?'Running':'Needs attention';
 dbAdminNode('h4','',section).textContent=t(plan.phase==='bootstrap'?'Foundation setup (approval 1/2)':'SQL connection setup (approval 2/2)')+' · '+t(state);
 dbAdminNode('p','',section,plan.phase==='bootstrap'?'Foundation setup creates connection information and prepares shared storage and catalog services.':'SQL connection setup propagates the SQL service configuration and verifies the complete connection.');
 const labels={publish_snapshot:'Publish reviewed configuration',apply_secrets:'Issue connection information',reconcile_storage:'Prepare shared storage',reconcile_polaris:'Prepare catalog access',reconcile_trino:'Propagate SQL connection configuration',verify:'Verify access',apply:'Apply reviewed configuration'};
 const list=dbAdminNode('ul','',section);
 for(const name of plan.public_summary?.fixed_steps||plan.steps?.map(s=>s.step)||[]){
  const step=plan.steps?.find(s=>s.step===name),deferred=plan.phase==='bootstrap'&&name==='reconcile_trino';
  const state=deferred?'Deferred to SQL connection setup (approval 2/2)':running&&step?.state==='UNKNOWN'?'Earlier result unknown; current setup or verification is running.':attention&&step?.state!=='SUCCEEDED'?'Needs attention':step?.state==='SUCCEEDED'?'Completed':step?.state==='RUNNING'?'Running':step?.state==='PENDING'?(plan.state==='AWAITING_APPLY'?'Awaiting approval':'Not started'):'Needs attention';
  dbAdminNode('li','',list).textContent=t(labels[name]||'Additional server-reviewed step')+' · '+t(state);
 }
}
function dbCreationNextAction(parent,p,bootstrap,full,owner,stopped,terminal,ready){
 const actions=p.next_actions||[],progress=p.progress_code;let next;
 if(terminal)next='This database is retiring or retired. Creation history is available below.';
 else if(!owner)next='Only the original requester can change this creation.';
 else if(ready)next=p.access_source==='none'?'READY; SQL use unavailable for the selected role.':'The database is ready. Open a new SQL worksheet to use it.';
 else if(dbCreation.pending)next=dbCreation.pending.busy?'Request in progress.':'Request outcome uncertain. Retry only the saved request.';
 else if(!dbCreationStagedDetailCurrent())next='Refresh creation status before taking a staged action.';
 else if((stopped||p.blocker||progress==='BLOCKED')&&actions.includes('reconcile'))next='Reconcile the saved creation to check its outcome. No creation work is resent.';
 else if(['STOPPED','BLOCKED'].includes(progress))next='Creation is stopped or blocked. Review the status and available actions below.';
 else if(['APPLYING','VERIFYING'].includes(progress))next='Setup or verification is running. Status is checked automatically while this screen is visible.';
 else if(progress==='REPLAN_REQUIRED')next=actions.includes('replan')?'Prepare a revised plan, then review it again before approval.':'A revised plan is required, but preparing it is currently unavailable. Review the status and available actions.';
 else if(stopped||p.blocker)next='Creation is stopped or blocked. Review the status and available actions below.';
 else if(progress==='RECONCILE_REQUIRED')next=actions.includes('recover-verify')?'The outcome is unknown. Review the recovery plan before approving limited verification.':actions.includes('reconcile')?'The outcome is unknown. Reconcile the current status before continuing.':'The outcome is unknown. Review the status and available actions below.';
 else if(['PLAN_REVIEW_REQUIRED','FULL_REVIEW_REQUIRED'].includes(progress)){
  if(full?.state==='AWAITING_APPLY'&&actions.includes('approve'))next='Review the SQL connection plan, then explicitly approve step 2/2.';
  else if(bootstrap?.state==='PREPARED'&&!full&&actions.includes('plans'))next='Foundation setup is complete. Prepare the SQL connection plan for approval 2/2; the database is not ready yet.';
  else if(bootstrap?.state==='AWAITING_APPLY'&&actions.includes('approve'))next='Review the foundation plan, then explicitly approve step 1/2.';
  else if(actions.includes('replan'))next='Prepare a revised plan, then review it again before approval.';
 }
 if(!next&&['PREPARATION_PENDING','PLAN_REVIEW_REQUIRED'].includes(progress)&&!bootstrap&&actions.includes('plans'))next='Prepare the foundation plan, then review it for approval 1/2.';
 else if(!next&&progress==='PREPARATION_PENDING')next='Creation preparation is pending. Status is checked automatically while this screen is visible.';
 dbAdminNode('p','db-creation-next-action',parent,next||'Creation needs review. Use only the actions currently available below.');
}
function dbCreationOrigin(){return globalThis.location?.origin||'local';}
function dbCreationScope(){return JSON.stringify([dbCreationOrigin(),dbPrincipal,currentRole,dbMaximumRole,dbAdmin.platform,dbCreation.environment_id,dbCreation.epoch]);}
function dbCreationBinding(){return {origin:dbCreationOrigin(),principal:dbPrincipal,role:currentRole,environment_id:dbCreation.environment_id};}
function dbCreationBindingMatches(binding){const current=dbCreationBinding();return !!current.environment_id&&binding&&Object.keys(current).every(k=>binding[k]===current[k]);}
function dbCreationStorageKey(){return dbCreation.environment_id?'shirokuma.creation.v2:'+JSON.stringify([dbCreationOrigin(),dbPrincipal,currentRole,dbCreation.environment_id]):'';}
function dbCreationEnvironment(cap){return cap?.configured===true&&typeof cap.environment_id==='string'&&cap.environment_id.trim()?cap.environment_id:null;}
function dbCreationStop(){if(dbCreation.timer!==null)clearTimeout(dbCreation.timer);dbCreation.timer=null;dbCreationObservation();}
function dbCreationSuspend(){
 dbCreationStop();++dbCreation.epoch;++dbCreation.read;dbCreation.review=null;
 if(!dbCreationLogical())dbCreation.checkedID=null;
 if(dbCreation.pending){dbCreation.pending.busy=false;dbCreationSave();}
 // Keep the in-memory draft and receipt. Reopening revalidates authority with GET.
 dbCreation.scope=dbCreationScope();
}
function dbCreationClear(){
 if(dbCreation.pending){dbCreation.pending.busy=false;dbCreationSave();}
 dbCreationStop();++dbCreation.epoch;++dbCreation.read;dbCreation.scope='';dbCreation.checkedID=null;dbCreation.readFailed=false;dbCreation.process=null;dbCreation.pending=null;dbCreation.review=null;dbCreation.error='';dbCreation.capability=null;
 dbCreation.draft={sql_catalog:'',display_name:'',description:'',team:'',initial_grants:[]};dbCreation.stage=0;dbCreation.principals=[];dbCreation.list=[];dbCreation.cursor=null;dbCreation.storageKey='';dbCreation.environment_id=null;dbCreation.environmentNotice=false;dbCreation.legacyNotice=false;
 dbCreationResetHistory();
}
function dbCreationSave(){
 const key=dbCreation.storageKey||dbCreationStorageKey();if(!key)return;
 const saved=JSON.stringify({binding:dbCreation.pending?.binding||dbCreationBinding(),pending:dbCreation.pending,creation_id:dbCreation.process?.creation_id||null});
 dbCreationSaved.set(key,saved);
 try{localStorage.setItem(key,saved);}
 catch{dbCreation.storageNotice=true;}
}
function dbCreationReadSaved(key){
 if(dbCreationSaved.has(key))return dbCreationSaved.get(key);
 try{return localStorage.getItem(key);}catch{dbCreation.storageNotice=true;return null;}
}
function dbCreationRestore(){
 const key=dbCreationStorageKey();if(!key)return;
 if(dbCreation.storageKey&&dbCreation.storageKey!==key){const cap=dbCreation.capability;dbCreationClear();dbCreation.environment_id=dbCreationEnvironment(cap);dbCreation.capability=cap;}
 const scope=dbCreationScope();if(dbCreation.scope===scope&&dbCreation.storageKey===key)return;dbCreation.scope=scope;
 dbCreation.storageKey=key;
 try{const saved=JSON.parse(dbCreationReadSaved(key)||'null');
  const legacyKey='shirokuma.creation.v1:'+JSON.stringify([dbCreationOrigin(),dbPrincipal,currentRole]);
  dbCreation.legacyNotice=!!JSON.parse(dbCreationReadSaved(legacyKey)||'null')?.pending;
  if(dbCreationBindingMatches(saved?.binding)&&dbCreationBindingMatches(saved?.pending?.binding)&&typeof saved.pending.path==='string'&&typeof saved.pending.body==='string'&&typeof saved.pending.key==='string'&&
   (saved.pending.path===dbCreationRoute||/^\/api\/v1\/admin\/database-creations\/[A-Za-z0-9_-]+\/(plans|replan|reconcile|withdraw|resume|phases\/(bootstrap|full)\/(approve|recover-verify))$/.test(saved.pending.path))){
   JSON.parse(saved.pending.body);dbCreation.pending={...saved.pending,busy:false};
  }
  if(dbCreationBindingMatches(saved?.binding)&&/^[A-Za-z0-9_-]{1,128}$/.test(saved?.creation_id||''))dbCreation.process={creation_id:saved.creation_id};
 }catch{dbCreation.storageNotice=true;}
 const selection=dbCreationURL(),pending=dbAdmin.pendingCreation;dbAdmin.pendingCreation=null;
 const chosen=selection.id?selection:pending||{};
 const id=chosen.environment===dbCreation.environment_id?chosen.id:null;
 if(/^[A-Za-z0-9_-]{1,128}$/.test(id||'')){dbCreation.process={creation_id:id};dbAdmin.legacyCreation={id};}
 else dbAdmin.legacyCreation=null;
}
function dbCreationAdoptCapability(cap){
 const environment=dbCreationEnvironment(cap);
 if(environment!==dbCreation.environment_id){const previous=!!dbCreation.environment_id;dbCreationClear();dbCreation.environment_id=environment;dbCreation.environmentNotice=previous;}
 dbCreation.capability=cap;return !!environment;
}
async function dbCreationCheckEnvironment(binding,scope){
 const cap=await dbCreationRequest(dbCreationRoute+'/capabilities',{},scope);
 if(!dbCreationEnvironment(cap)||cap.environment_id!==binding?.environment_id||!dbCreationBindingMatches(binding)){
  dbCreationAdoptCapability(cap);dbCreation.environmentNotice=true;dbCreationRender();
  throw Object.assign(Error('Environment identity unavailable or changed. Saved requests remain bound to their original environment.'),{environment:true});
 }
 dbCreation.capability=cap;
}
async function dbCreationRequest(path,options={},scope=dbCreationScope(),beforeSend,binding){
 const valid=()=>dbAdminAllowed()&&scope===dbCreationScope();
 if(!valid())throw dbAdminStale();
 const auth=await fetch('/api/session',{cache:'no-store'}),session=await auth.json();
 if(!valid())throw dbAdminStale();dbAdminSession(auth.ok?session:{});
 if(!valid())throw dbAdminStale();
 if(options.method==='POST'){await dbCreationCheckEnvironment(binding,scope);if(!valid())throw dbAdminStale();}
 if(beforeSend&&!beforeSend())throw Object.assign(Error('Creation changed. Review current status before acting.'),{code:'Creation changed. Review current status before acting.',notSent:true});
 const response=await fetch(path,{cache:'no-store',...options,headers:{'X-Shirokuma-Role':'admin',...options.headers,...(options.method==='POST'?{'X-Shirokuma-Environment':binding.environment_id}:{})}});
 if(!valid())throw dbAdminStale();const data=await response.json();if(!valid())throw dbAdminStale();
 if(options.method==='POST'){await dbCreationCheckEnvironment(binding,scope);if(!valid())throw dbAdminStale();}
 if(!response.ok||data?.error){
  const code=data?.error||data?.code||String(response.status);
  if(response.status===401||response.status===403&&['ADMIN_REQUIRED','UNAUTHENTICATED','AUTHENTICATION_REQUIRED'].includes(code)){dbAdmin.platform=false;dbAdminClear();throw dbAdminStale();}
  const rejection=[400,409].includes(response.status)&&data&&Object.keys(data).sort().join(',')==='error,message'&&typeof data.message==='string'&&
   ['INVALID_REQUEST','INVALID_NAME','INVALID_INITIAL_GRANTS','REVISION_CONFLICT','STALE_PLAN','RECONCILIATION_REQUIRED','CREATION_BUSY','UNAPPLIED_CHANGES'].includes(data.error);
  throw Object.assign(Error(code),{code,status:response.status,rejection});
 }
 if(path===dbCreationRoute+'/capabilities')dbRuntimeConfirmProfile(data,'capability');
 return data;
}
function dbCreationNotice(error,readOnly=false){
 if(error.stale)return;
 dbCreation.error=error.code==='ENVIRONMENT_MISMATCH'?'Server environment does not match the saved request. Its original outcome is unknown; the saved request is retained.':error.code||'Connection unavailable. Last known status retained; refresh to retry.';
 if(readOnly&&dbCreation.process?.creation_id&&!dbCreationLogical()){
  const wasReviewFocused=['db-creation-confirm','db-creation-approve'].includes(document.activeElement?.id);
  dbCreation.review=null;dbCreationRender();
  if(wasReviewFocused)$('db-creation-refresh')?.focus();
  return;
 }
 if(error.code==='PRINCIPAL_STOPPED'&&dbCreation.process)dbCreation.process={...dbCreation.process,blocker:'PRINCIPAL_STOPPED',progress_code:'STOPPED',next_actions:[]};
 if(readOnly&&error.code!=='PRINCIPAL_STOPPED'){$('db-creation-status').textContent=t(dbCreation.error);dbCreationObservation();}
 else dbCreationRender();
}
function dbCreationSchedule(){
 dbCreationStop();if(!dbAdminAllowed()||activeTab!=='db-admin'||document.hidden||dbCreation.pending?.busy||!dbCreation.process?.creation_id){dbCreationObservation();return;}
 dbCreation.timer=setTimeout(()=>{dbCreation.timer=null;void dbCreationLoad();},dbCreationPollDelay());dbCreation.timer?.unref?.();dbCreationObservation();
}
async function dbCreationLoad(discover=false,reopenID=''){
 if(reopenID&&(dbCreation.pending?.busy||dbCreation.scope!==dbCreationScope()||!dbCreation.list.some(p=>p.creation_id===reopenID)))return;
 if(!dbAdminAllowed())return;dbCreationStop();let scope=dbCreationScope(),read=++dbCreation.read;
 if(discover)dbCreationInvalidateStagedDetail();
 const previousMode=JSON.stringify([dbCreation.capability?.creation_mode,dbCreation.capability?.available,dbCreation.capability?.blocker,dbCreation.capability?.warehouse_state]),previousPrincipals=JSON.stringify(dbCreation.principals);
 try{
  const cap=await dbCreationRequest(dbCreationRoute+'/capabilities',{},scope);if(read!==dbCreation.read)return;
  if(reopenID&&dbCreationEnvironment(cap)!==dbCreation.environment_id){
   dbCreationAdoptCapability(cap);dbCreation.environmentNotice=true;dbCreationRender();return;
  }
  if(!dbCreationAdoptCapability(cap)){dbCreation.environmentNotice=true;dbCreationRender();return;}
  dbCreationRestore();scope=dbCreationScope();read=++dbCreation.read;
  const previous=JSON.stringify(dbCreation.process),previousList=JSON.stringify([dbCreation.list,dbCreation.cursor]),hadError=!!dbCreation.error,id=reopenID||dbCreation.process?.creation_id,hadCurrentDetail=dbCreationStagedDetailCurrent();
  const data=await dbCreationRequest(dbCreationRoute+(id&&!discover?'/'+encodeURIComponent(id):''),{},scope);
  if(scope!==dbCreationScope()||read!==dbCreation.read)return;
  if(!id&&dbCreationLogical()){
   const people=await dbCreationRequest(dbCreationRoute+'/principals',{},scope);
   if(scope!==dbCreationScope()||read!==dbCreation.read)return;dbCreation.principals=people.principals||[];
  }
  await dbCreationCheckEnvironment(dbCreationBinding(),scope);if(scope!==dbCreationScope()||read!==dbCreation.read)return;
  if(id&&!discover&&(reopenID||!dbCreationLogical())&&data.creation_id!==id)throw Error('Invalid creation detail');
  dbCreation.error='';dbCreation.readFailed=false;dbCreation.checkedID=!discover&&id&&data.creation_id===id?id:null;
  if(data.creation_id){dbCreation.process=data;if(reopenID||dbCreation.review?.token!==dbCreationActionToken())dbCreation.review=null;dbCreationRememberURL(data.creation_id);}
  else{dbCreation.list=data.creations||[];dbCreation.cursor=data.cursor||null;}
  const scopedRefresh=dbCreation.renderedScope!==scope,active=document.activeElement;
  const controls=()=>[...$('db-creation-form').querySelectorAll('button,input,select,textarea'),...$('db-creation-process').querySelectorAll('button,input,select,textarea')];
  const focus=scopedRefresh&&controls().includes(active)?{id:active.id,start:active.selectionStart,end:active.selectionEnd}:null;
  const stagedFreshnessChanged=!dbCreationLogical()&&hadCurrentDetail!==dbCreationStagedDetailCurrent();
  if(reopenID||(data.creation_id?JSON.stringify(dbCreation.process)!==previous:JSON.stringify([dbCreation.list,dbCreation.cursor])!==previousList)||stagedFreshnessChanged||scopedRefresh||dbCreation.renderedPending!==dbCreation.pending||previousMode!==JSON.stringify([dbCreation.capability?.creation_mode,dbCreation.capability?.available,dbCreation.capability?.blocker,dbCreation.capability?.warehouse_state])||previousPrincipals!==JSON.stringify(dbCreation.principals)){
   dbCreationRender();
   if(focus){const target=controls().find(node=>node.id&&node.id===focus.id&&!node.disabled)||$('db-creation-heading');target?.focus();
    if(target&&['INPUT','TEXTAREA'].includes(target.tagName)&&typeof focus.start==='number')target.setSelectionRange(focus.start,focus.end);}
   if(reopenID&&active?.id==='db-creation-history-open')$('db-creation-heading')?.focus();
  }
  if(hadError)$('db-creation-status').textContent=dbCreation.storageNotice?t('Browser storage unavailable; request is retained only in this session.') : '';
 }catch(error){if(scope===dbCreationScope()&&read===dbCreation.read){
  if(reopenID&&!error.stale){
   const status=$('db-creation-status');status.setAttribute('role','alert');status.textContent=t(error.code||'Connection unavailable. Last known status retained; refresh to retry.');
  }else if(!reopenID){dbCreation.readFailed=true;dbCreationNotice(error,true);}
 }}
 finally{if(scope===dbCreationScope()&&read===dbCreation.read)dbCreationSchedule();}
}
function dbCreationRememberURL(id){
 dbCreationSave();dbCreationURL(id);
}
function dbCreationURL(id){
 if(!globalThis.location)return {};
 const url=new URL(globalThis.location.href),hash=url.hash.slice(1),split=hash.indexOf('?');
 const page=split>=0?hash.slice(0,split):hash.includes('=')?'':hash;
 const createPage=page==='databases/create'||page==='databases/create-review';
 const databasePage=page==='databases'||page.startsWith('databases/');
 // Saved creation state must not replace a database list/detail/retirement route.
 if(databasePage&&!createPage)return {id:null,environment:null};
 const params=new URLSearchParams(split>=0?hash.slice(split+1):page?'':hash);
 const source=params.has('creation')||params.has('creation_environment')?params:url.searchParams;
 const selection={id:source.get('creation'),environment:source.get('creation_environment')};
 // Only the explicit creation screens own the URL. Legacy #db-admin?creation=… activation
 // is promoted by dbAdminApplyRoute once, not by every state save/poll.
 if(arguments.length&&createPage&&globalThis.history?.replaceState){
  url.searchParams.delete('creation');url.searchParams.delete('creation_environment');
  params.delete('creation');params.delete('creation_environment');
  if(id){params.set('creation',id);params.set('creation_environment',dbCreation.environment_id||'');}
  const state=params.toString();
  url.hash=page+(state?'?'+state:'');
  globalThis.history.replaceState(null,'',url);
 }
 return selection;
}
function dbCreationCanonical(value){
 if(Array.isArray(value))return value.map(dbCreationCanonical);
 if(value&&typeof value==='object')return Object.fromEntries(Object.keys(value).sort().map(k=>[k,dbCreationCanonical(value[k])]));return value;
}
function dbCreationActionToken(process=dbCreation.process){return JSON.stringify([dbCreationScope(),dbCreationCanonical(process)]);}
function dbCreationStagedDetailCurrent(){
 const id=dbCreation.process?.creation_id;
 return !!id&&!dbCreation.readFailed&&dbCreation.checkedID===id;
}
function dbCreationInvalidateStagedDetail(){
 if(!dbCreation.process?.creation_id||dbCreationLogical())return;
 dbCreation.checkedID=null;dbCreation.review=null;dbCreationRender();
}
function dbCreationCanMutate(suffix,payload){
 if(!dbCreation.environment_id)return false;
 if(!suffix)return !dbCreation.process&&dbCreation.stage===2&&dbCreationAvailable();
 const p=dbCreation.process;if(!p||p.requester_id!==JSON.parse(dbPrincipal)[0]||p.revision!==payload?.expected_revision)return false;
 const prefix='/'+encodeURIComponent(p.creation_id)+'/';if(!suffix.startsWith(prefix))return false;
 const tail=suffix.slice(prefix.length),parts=tail.split('/'),action=parts[0]==='phases'?parts[2]:tail;
 if(!(p.next_actions||[]).includes(action))return false;
 if(dbCreationLogical())return p.progress_code!=='STOPPED'&&p.blocker!=='PRINCIPAL_STOPPED'&&
  (p.progress_code!=='UNKNOWN'||action==='reconcile')&&
  (action==='reconcile'||action==='withdraw'||action==='resume'&&p.progress_code==='BLOCKED');
 if(!dbCreationStagedDetailCurrent()||action==='resume')return false;
 if((p.progress_code==='STOPPED'||p.blocker==='PRINCIPAL_STOPPED')&&!['reconcile','withdraw'].includes(action))return false;
 const plans=p.plans||[],bootstrap=plans.filter(v=>v.phase==='bootstrap'&&v.purpose==='APPLY').at(-1);
 if(['plans','replan','approve'].includes(action)&&plans.some(v=>v.state==='UNKNOWN'))return false;
 if(action==='plans')return payload.phase==='bootstrap'?!bootstrap:payload.phase==='full'&&bootstrap?.state==='PREPARED'&&!plans.some(v=>v.phase==='full'&&v.purpose==='APPLY');
 if(action==='replan')return payload.phase===(bootstrap?.state==='PREPARED'?'full':'bootstrap');
 if(action==='approve'||action==='recover-verify'){
  const review=dbCreation.review,plan=review?.plan,recovery=action==='recover-verify';
  return !!review&&review.token===dbCreationActionToken()&&review.revision===p.revision&&parts[1]===plan.phase&&
   plan.purpose===(recovery?'VERIFY_RECOVERY':'APPLY')&&plan.state===(recovery?'UNKNOWN':'AWAITING_APPLY')&&(!recovery||plan.phase==='full')&&
   plans.filter(v=>v.phase===plan.phase&&v.purpose===plan.purpose).at(-1)?.plan_id===plan.plan_id&&
   plans.some(v=>JSON.stringify(v)===review.fingerprint)&&payload.plan_id===plan.plan_id&&payload.operation_id===plan.operation_id&&
   payload.expected_operation_revision===plan.operation_revision&&payload.delivery_digest===plan.delivery_digest&&(!recovery||payload.recovery_digest===plan.recovery_digest);
 }
 return ['replan','reconcile','withdraw'].includes(action);
}
async function dbCreationMutate(suffix,payload,retry=false){
 if(!dbAdminAllowed()||dbCreation.pending?.busy)return;
 const scope=dbCreationScope();
 if(!retry){
  if(dbCreation.pending)return;
  if(!dbCreationCanMutate(suffix,payload)){dbCreationNotice({code:'Creation changed. Review current status before acting.'});return;}
  if(!dbCreation.storageKey)dbCreation.storageKey=dbCreationStorageKey();
  dbCreation.pending={path:dbCreationRoute+suffix,body:JSON.stringify(dbCreationCanonical(payload)),key:crypto.randomUUID(),binding:dbCreationBinding(),busy:false};
  dbCreationSave(); // Freeze before the first network request, including session preflight.
 }
 const request=dbCreation.pending;if(!request)return;dbCreationStop();++dbCreation.read;const token=dbCreationActionToken();request.busy=true;dbCreationSave();dbCreationRender();
 try{
  const data=await dbCreationRequest(request.path,{method:'POST',body:request.body,headers:{'Content-Type':'application/json','Idempotency-Key':request.key}},scope,
   retry?null:()=>token===dbCreationActionToken()&&dbCreationCanMutate(suffix,payload),request.binding);
  if(scope!==dbCreationScope())return;
  if(typeof data.creation_id!=='string'||!/^[A-Za-z0-9_-]{1,128}$/.test(data.creation_id)||typeof data.request_id!=='string'||!data.request_id||!Number.isSafeInteger(data.accepted_revision)||data.accepted_revision<1||suffix&&!request.path.startsWith(dbCreationRoute+'/'+data.creation_id+'/'))throw Error('Invalid creation acknowledgment');
  const action=request.path===dbCreationRoute?'CREATE':request.path.endsWith('/resume')?'RESUME':null;
  if(action&&(('action' in data&&data.action!==action)||('phase' in data&&data.phase!==null)))throw Error('Invalid creation acknowledgment');
  dbCreation.pending=null;dbCreation.review=null;dbCreation.process={...(dbCreation.process||{}),creation_id:data.creation_id};
  if(!dbCreationLogical())dbCreation.checkedID=null;
  dbCreation.error='';dbCreationRememberURL(data.creation_id);dbCreationRender();await dbCreationLoad();
 }catch(error){if(scope===dbCreationScope()){
  if(error.rejection||error.notSent){
   dbCreation.pending=null;dbCreation.review=null;
   if(dbCreation.process?.creation_id&&!dbCreationLogical())dbCreation.checkedID=null;
   dbCreationSave();
  }
  dbCreationNotice(error);
 }}
 finally{if(scope===dbCreationScope()){if(dbCreation.pending===request){request.busy=false;dbCreationSave();dbCreationRender();}dbCreationSchedule();}}
}
function dbCreationInstall(root){
 const box=dbAdminNode('section','db-creation',root);box.className='panel-body db-flow';box.style&&(box.style.overflowWrap='anywhere');
 box.setAttribute('aria-labelledby','db-creation-title');
 const back=dbAdminNode('a','db-creation-list-back',box,'← Back to list');back.className='backlink';back.href='#databases';
 dbAdminNode('h1','db-creation-title',box,'Create database').tabIndex=-1;
 const status=dbAdminNode('p','db-creation-status',box);status.setAttribute('role','status');
 dbAdminNode('div','db-creation-journey',box);
 dbAdminNode('div','db-creation-process',box);
 dbAdminNode('div','db-creation-complete-actions',box);
 const shell=dbAdminNode('form','db-creation-form-shell',box);shell.className='db-creation-form-shell';shell.addEventListener?.('submit',event=>event.preventDefault?.());
 const fieldset=dbAdminNode('fieldset','',shell);dbAdminNode('legend','',fieldset,'Creation details');
 dbAdminNode('div','db-creation-form',fieldset);
 dbAdminNode('p','db-creation-observation',box);
 const history=dbAdminNode('details','db-creation-history',box);
 history.open=false;
 dbAdminNode('summary','',history,'Creation history');
 const controls=dbAdminNode('div','',history);controls.className='toolbar';
 const picker=dbAdminField(controls,'db-creation-history-picker','Saved creation','select');
 picker.onchange=dbCreationRenderHistory;
 dbAdminButton(controls,'db-creation-history-open','Open creation status',()=>{
  if(picker===$('db-creation-history-picker')&&/^[A-Za-z0-9_-]{1,128}$/.test(picker.value))return dbCreationLoad(false,picker.value);
 });
 dbAdminButton(controls,'db-creation-discover','Rediscover creations',()=>dbCreationLoad(true));
 dbCreationRender();
}
function dbCreationResetHistory(){
 const history=$('db-creation-history');if(!history)return;
 history.open=false;$('db-creation-history-picker').value='';
}
function dbCreationRenderHistory(){
 const picker=$('db-creation-history-picker'),value=picker.value;
 const records=dbCreation.scope===dbCreationScope()?dbCreation.list:[];
 const options=[['',t(records.length?'Choose a creation':'No creation records')]];
 for(const p of records){
  const lifecycle=(p.lifecycle||'').toUpperCase();
  const status=lifecycle==='READY'?t('Ready'):['RETIRING','RETIRED'].includes(lifecycle)||p.creation_mode!=='logical_v1'
   ?t(lifecycle||'Unavailable'):dbCreationLogicalLabel(p.progress_code);
  options.push([p.creation_id,p.sql_catalog+' · '+status]);
 }
 if(picker.options.length!==options.length||options.some(([id,label],i)=>picker.options[i].value!==id||picker.options[i].textContent!==label)){
  picker.replaceChildren();
  for(const [id,label] of options)picker.add(new Option(label,id));
 }
 const choice=options.some(([id])=>id===value)?value:'';
 if(picker.value!==choice)picker.value=choice;
 $('db-creation-history-open').disabled=!picker.value||!!dbCreation.pending?.busy;
}
function dbCreationRefreshButton(parent){
 return dbAdminButton(parent,'db-creation-refresh','Refresh creation status',()=>{
  dbCreationInvalidateStagedDetail();dbCreation.capability=null;return dbCreationLoad();
 });
}
function dbCreationStage(stage){dbCreation.stage=stage;dbCreationRender();$('db-creation-heading')?.focus();}
function dbCreationButton(parent,id,label,task,disabled=false){const scope=dbCreationScope(),b=dbAdminButton(parent,id,label,()=>{if(dbAdminAllowed()&&scope===dbCreationScope())return task();});b.disabled=disabled||!!dbCreation.pending;return b;}
function dbCreationRender(){
 if(!$('db-creation-form')||!dbAdminAllowed())return;
 dbCreation.renderedScope=dbCreationScope();dbCreation.renderedPending=dbCreation.pending;dbCreationJourney();dbCreationObservation();
 dbCreationRenderHistory();
 const parent=$('db-creation-form');parent.replaceChildren();
 const status=$('db-creation-status');status.textContent='';status.setAttribute('role',dbCreation.error?'alert':'status');
 if(dbCreation.storageNotice)status.textContent=t('Browser storage unavailable; request is retained only in this session.');
 if(dbCreation.environmentNotice)dbAdminNode('p','',parent,'Environment identity unavailable or changed. Saved requests remain bound to their original environment.');
 if(dbCreation.legacyNotice)dbAdminNode('p','',parent,'An older saved request has no environment binding and cannot be replayed.');
 if(dbCreation.error)status.textContent+=(status.textContent?'\n':'')+t(dbCreation.error);
 if(dbCreation.pending){
  dbAdminNode('p','',parent,dbCreation.pending.busy?'Request in progress.':'Request outcome uncertain. Retry only the saved request.');
  const request=dbCreation.pending,retry=dbAdminButton(parent,'db-creation-retry','Retry frozen request',()=>{if(request===dbCreation.pending)return dbCreationMutate('',null,true);});retry.disabled=request.busy;
 }
 const selected=!!dbCreation.process?.creation_id;
 if(selected){
  dbCreationRenderProcess();dbCreationRenderComplete();
  const actions=dbAdminNode('div','',$('db-creation-process'));actions.className='toolbar';
  dbCreationRefreshButton(actions);
  dbCreationButton(actions,'db-creation-new','New creation',()=>{
   if(dbCreation.pending)return;
   dbCreationStop();++dbCreation.read;dbCreation.process=null;dbCreation.review=null;dbCreation.stage=0;
   dbCreation.capability=null;dbCreation.error='';
   dbCreationResetHistory();
   dbCreationURL(null);
   dbCreationSave();dbCreationRender();$('db-creation-title').focus();return dbCreationLoad();
  });
  return;
 }
 $('db-creation-process').replaceChildren();$('db-creation-complete-actions')?.replaceChildren();
 if(!dbCreationFormMode()){
  const loading=dbAdminNode('p','db-creation-loading',parent,dbCreation.capability||dbCreation.error?'The database creation service is unavailable. Try refreshing its status.':'Loading…');loading.setAttribute('role','status');
  dbCreationRefreshButton(parent);
  return;
 }
 const logical=dbCreationLogical();
 const d=dbCreation.draft;
 if(dbCreation.stage===0){
  const fields=[['sql_catalog','SQL catalog name (required)','input'],['team','Team (required)','input'],['display_name','Display name (optional)','input'],['description','Description (optional)','textarea']];
  for(const [key,label,tag] of fields){
   const field=dbAdminField(parent,'db-creation-'+key,label,tag);field.value=d[key];field.oninput=()=>{d[key]=field.value;field.setAttribute('aria-invalid','false');};
   if(key==='team'){field.maxLength=64;field.required=true;field.autocomplete='off';field.setAttribute('list','db-creation-team-options');}
   const help=dbAdminNode('p','db-creation-'+key+'-help',field.parentElement,{sql_catalog:'Required. Example: analytics. Start with a lowercase letter, then use lowercase letters, digits or underscores (up to 31 characters).',team:'Required. 1 to 64 characters. Existing teams are suggestions.',display_name:'Optional. If empty, the SQL catalog name is used.',description:'Optional. Add a short description of this database.'}[key]);
   help.className='help';field.setAttribute('aria-describedby',help.id);
  }
  const teams=[...new Set(dbAdmin.databases.map(row=>row.team).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'ja'));
  const list=dbAdminNode('datalist','db-creation-team-options',parent);
  for(const value of teams)list.append(new Option(value,value));
  const basics=()=>{
   let field=$('db-creation-sql_catalog');
   if(!/^[a-z][a-z0-9_]{0,30}$/.test(d.sql_catalog)||['system','information_schema','tpch','tpcds','iceberg'].includes(d.sql_catalog)){const error=Error(t('Invalid SQL catalog name'));error.field=field;throw error;}
   field=$('db-creation-team');
   try{d.team=dbAdminTeam(d.team);}catch(error){error.field=field;throw error;}
   field=$('db-creation-display_name');
   try{dbAdminDisplay(d.display_name||d.sql_catalog);}catch(error){error.field=field;throw error;}
   field=$('db-creation-description');
   try{dbAdminDisplay(d.display_name||d.sql_catalog,d.description);}catch(error){error.field=field;throw error;}
  };
  if(!logical)dbCreationButton(parent,'db-creation-next','Continue to access',async()=>{
   const scope=dbCreationScope();
   try{basics();
    const people=await dbCreationRequest(dbCreationRoute+'/principals',{},scope);if(scope!==dbCreationScope())return;
    dbCreation.principals=people.principals||[];dbCreationStage(1);
   }catch(error){if(scope===dbCreationScope()&&!error.stale){status.setAttribute('role','alert');status.textContent=error.message;const field=error.field||$('db-creation-sql_catalog');field.setAttribute('aria-invalid','true');field.focus();}}
  });
 }
 if(dbCreation.stage===1||logical&&dbCreation.stage===0){
  if(logical)dbAdminNode('h4','',parent,'Initial access');
  const chooser=dbAdminField(parent,'db-creation-principal','Choose principal','select');chooser.add(new Option(t('Choose principal'),''));
  for(const p of dbCreation.principals)if(!d.initial_grants.some(g=>g.principal_id===p.principal_id))chooser.add(new Option(p.display_name,p.principal_id));
  const roles=dbAdminField(parent,'db-creation-role','Desired role','select');
  chooser.onchange=()=>{roles.replaceChildren();const p=dbCreation.principals.find(p=>p.principal_id===chooser.value),all=['bi_viewer','data_engineer','admin'];for(const role of all.slice(0,all.indexOf(p?.maximum_role)+1))roles.add(new Option(role,role));};
  dbCreationButton(parent,'db-creation-add','Add access',()=>{const p=dbCreation.principals.find(p=>p.principal_id===chooser.value),all=['bi_viewer','data_engineer','admin'];
   if(!p||!roles.value||all.indexOf(roles.value)>all.indexOf(p.maximum_role)||d.initial_grants.some(g=>g.principal_id===p.principal_id))return;
   d.initial_grants.push({principal_id:p.principal_id,maximum_role:roles.value});dbCreationRender();});
  for(const grant of d.initial_grants){const row=dbAdminNode('div','',parent);dbAdminNode('span','',row).textContent=grant.principal_id+' · '+grant.maximum_role;
   dbCreationButton(row,'','Remove access',()=>{d.initial_grants=d.initial_grants.filter(g=>g!==grant);dbCreationRender();});}
  if(!d.initial_grants.length)dbAdminNode('p','',parent,'No initial grants.');
  if(!logical){dbCreationButton(parent,'db-creation-back','Back to name',()=>dbCreationStage(0));dbCreationButton(parent,'db-creation-next','Review intent',()=>dbCreationStage(2));}
 }
 if(logical&&dbCreation.stage===0){
  dbCreationButton(parent,'db-creation-next','Review creation',()=>{
   try{
    let field=$('db-creation-sql_catalog');
    if(!/^[a-z][a-z0-9_]{0,30}$/.test(d.sql_catalog)||['system','information_schema','tpch','tpcds','iceberg'].includes(d.sql_catalog)){const error=Error(t('Invalid SQL catalog name'));error.field=field;throw error;}
    field=$('db-creation-team');
    try{d.team=dbAdminTeam(d.team);}catch(error){error.field=field;throw error;}
    field=$('db-creation-display_name');
    try{dbAdminDisplay(d.display_name||d.sql_catalog);}catch(error){error.field=field;throw error;}
    field=$('db-creation-description');
    try{dbAdminDisplay(d.display_name||d.sql_catalog,d.description);}catch(error){error.field=field;throw error;}
    const ranks=['bi_viewer','data_engineer','admin'],seen=new Set();
    for(const grant of d.initial_grants){const principal=dbCreation.principals.find(p=>p.principal_id===grant.principal_id);field=$('db-creation-principal');
     if(!principal||seen.has(grant.principal_id)||!ranks.includes(grant.maximum_role)||ranks.indexOf(grant.maximum_role)>ranks.indexOf(principal.maximum_role)){const error=Error(t('Invalid initial access'));error.field=field;throw error;}
     seen.add(grant.principal_id);
    }
    dbCreationStage(2);
   }catch(error){const field=error.field||$('db-creation-sql_catalog');status.setAttribute('role','alert');status.textContent=error.message;field.setAttribute('aria-invalid','true');field.focus();}
  });
 }
 if(dbCreation.stage===2){
  dbCreationSummary(parent,d);
  dbCreationButton(parent,'db-creation-back',logical?'Back to name and team':'Back to access',()=>dbCreationStage(logical?0:1));
  dbCreationButton(parent,'db-creation-submit',logical?'Create database':'Submit creation intent',()=>dbCreationMutate('',{...d,team:dbAdminTeam(d.team),display_name:d.display_name||d.sql_catalog,initial_grants:[...d.initial_grants].sort((a,b)=>a.principal_id.localeCompare(b.principal_id))}),!dbCreationAvailable());
 }
 if(logical){const [label,guidance]=dbWarehousePresentation(dbCreation.capability?.warehouse_state);const observation=dbAdminNode('p','db-creation-warehouse-state',parent);observation.setAttribute('role','status');observation.textContent=t(label)+': '+t(guidance);}
 const primary=$(dbCreation.stage===2?'db-creation-submit':'db-creation-next');if(primary)primary.className='primary';
 if(dbCreation.capability?.blocker){if(logical)dbCreationLogicalBlocker(parent,dbCreation.capability.blocker);else dbAdminNode('p','',parent).textContent=dbCreation.capability.blocker;}
}
function dbCreationSummary(parent,p){
 dbAdminNode('p','',parent).textContent=[p.sql_catalog,p.team&&t('Team')+': '+p.team,p.display_name,p.description].filter(Boolean).join('\n');
 for(const g of p.initial_grants||[])dbAdminNode('p','',parent).textContent=g.principal_id+' · '+(g.maximum_role||g.desired_role)+(g.effective_role?' · effective: '+g.effective_role:'');
 if(!p.initial_grants?.length)dbAdminNode('p','',parent,'No initial grants.');
 dbAdminNode('p','',parent,'Fixed schemas: raw, marts, sandbox, staging.');
 const lifecycle=(p.lifecycle||'').toUpperCase();
 if(dbCreationLogical()&&!['READY','RETIRING','RETIRED'].includes(lifecycle))return;
 dbAdminNode('p','',parent,lifecycle==='READY'?(p.access_source==='none'?'Verification is complete. SQL use is unavailable for the selected role.':'Verification is complete. The database is ready to use.'):['RETIRING','RETIRED'].includes(lifecycle)?'Creation history is shown for this retiring or retired database.':'Foundation setup and SQL connection setup require separate plan review and approval. Creation will place all of LOCAL in maintenance until full verification completes.');
}
function dbCreationLogicalBlocker(parent,blocker){
 const labels={RECONCILIATION_REQUIRED:'Reconcile the saved creation to check its outcome. No creation work is resent.',CREATION_FAILED:'Creation failed. Ask an administrator to review the status and available recovery actions.',WAREHOUSE_START_REQUIRED:'Wake shared LOCAL in Compute before creating a database or running SQL.',FOUNDATION_NOT_READY:'Ask an administrator to prepare the database service.',OPERATOR_UNAVAILABLE:'The database creation service is unavailable. Try refreshing its status.',PRINCIPAL_STOPPED:'Creation has stopped and cannot be resumed.'};
 dbAdminNode('p','',parent,labels[blocker]||'Creation is blocked. Resolve the cause, then resume only when available.');
}
function dbCreationRenderLogical(box,p){
 const lifecycle=(p.lifecycle||'').toUpperCase(),ready=lifecycle==='READY',terminal=['RETIRING','RETIRED'].includes(lifecycle),historicalReady=p.progress_code==='READY'&&!ready,stopped=p.progress_code==='STOPPED'||p.blocker==='PRINCIPAL_STOPPED';
 const status=dbAdminNode('p','db-creation-progress',box,terminal?(lifecycle==='RETIRING'?'Retiring database':'Retired database'):historicalReady?(lifecycle==='RECONCILING'?'Reconciling database':'Creation blocked'):dbCreationLogicalLabel(p.progress_code));status.setAttribute('role','status');
 status.className='status-line '+(stopped?'tone-warn':ready?'tone-ok':['CREATING','VERIFYING'].includes(p.progress_code)?'tone-run':'tone-warn');
 const area=dbAdminNode('section','db-creation-actions',box),owner=p.requester_id===JSON.parse(dbPrincipal)[0],path='/'+encodeURIComponent(p.creation_id),revision={expected_revision:p.revision};
 dbAdminNode('p','db-creation-next-action',area,terminal?'This database is retiring or retired. Creation history is available below.':stopped?'Creation has stopped and cannot be resumed.':!ready&&!owner?'Only the original requester can change this creation.':historicalReady?'Creation needs review. Use only the actions currently available below.':p.progress_code==='UNKNOWN'?(dbCreationCanMutate(path+'/reconcile',revision)?'Reconcile the saved creation to check its outcome. No creation work is resent.':'The outcome is unknown. Review the status and available actions below.'):p.progress_code==='BLOCKED'?'Creation is blocked. Resolve the cause, then resume only when available.':ready?(p.access_source==='none'?'READY; SQL use unavailable for the selected role.':'The database is ready. Open a new SQL worksheet to use it.'):'Creation and access verification proceed automatically. Status updates while this screen is visible.');
 if(p.blocker)dbCreationLogicalBlocker(box,p.blocker);
 if(!owner)dbAdminNode('p','',area,'Only the original requester can change this creation.');
 if(owner&&!stopped&&!ready&&!terminal)for(const [action,label] of [['reconcile','Reconcile creation'],['resume','Resume creation'],['withdraw','Stop creation']]){
  if(!dbCreationCanMutate(path+'/'+action,revision))continue;
  dbCreationButton(area,'db-creation-'+action,label,()=>{if(action==='withdraw'&&!confirm(t('Stop creation')))return;return dbCreationMutate(path+'/'+action,revision);});
 }
 dbCreationSummary(box,p);
 if(ready&&!stopped&&p.progress_code==='READY'&&p.access_source!=='none')dbCreationButton(area,'db-creation-sql','Open new SQL',()=>dbCreationSQL());
}
function dbCreationRenderComplete(){
 const host=$('db-creation-complete-actions');if(!host)return;host.replaceChildren();
 const p=dbCreation.process,lifecycle=String(p?.lifecycle||'').toUpperCase();
 if(!p?.creation_id||!['READY','RETIRING','RETIRED'].includes(lifecycle))return;
 const actions=dbAdminNode('div','',host);actions.className='db-actions';
 const list=dbAdminNode('a','db-creation-complete-list',actions,'Back to list');list.href='#databases';
 if(p.database_id){
  const detail=dbAdminNode('a','db-creation-complete-detail',actions,'Open the created database');detail.className='primary';detail.href='#databases/'+encodeURIComponent(p.database_id);
  detail.onclick=async event=>{
   event?.preventDefault?.();
   await dbAdminRefresh();
   if(dbAdmin.databases.some(d=>d.id===p.database_id))dbAdminSelect(p.database_id);
   else if(globalThis.location)globalThis.location.hash='databases/'+encodeURIComponent(p.database_id);
  };
 }
}
function dbCreationRenderProcess(){
 const box=$('db-creation-process');box.replaceChildren();const p=dbCreation.process;if(!p)return;
 const title=dbAdminNode('h3','db-creation-heading',box);title.textContent=p.sql_catalog||t('Creation preparation pending');title.tabIndex=-1;
 if(!p.progress_code){dbAdminNode('p','',box,'Request in progress.');return;}
 if(dbCreationLogical()){dbCreationRenderLogical(box,p);return;}
 const labels={PREPARATION_PENDING:'Creation preparation pending',PLAN_REVIEW_REQUIRED:'Plan review required',FULL_REVIEW_REQUIRED:'Full plan review required',APPLYING:'Applying creation',VERIFYING:'Verifying creation',READY:'READY',REPLAN_REQUIRED:'Revised plan required',RECONCILE_REQUIRED:'Reconciliation required',STOPPED:'Creation stopped',BLOCKED:'Creation blocked'};
 const currentState=(p.lifecycle||'').toUpperCase(),ready=currentState==='READY',terminalState=['RETIRING','RETIRED'].includes(currentState);
 const progress=dbAdminNode('p','db-creation-progress',box,terminalState||p.progress_code==='READY'&&!ready?currentState||'Creation blocked':labels[p.progress_code]||(ready?'READY':'Creation preparation pending'));
 progress.setAttribute('role','status');
 progress.className='status-line '+(ready?'tone-ok':['APPLYING','VERIFYING'].includes(p.progress_code)?'tone-run':'tone-warn');
 const actionArea=dbAdminNode('section','db-creation-actions',box);
 if(p.blocker)dbAdminNode('p','',box).textContent=p.blocker;
 const plans=p.plans||[],bootstrap=plans.filter(v=>v.phase==='bootstrap'&&v.purpose==='APPLY').at(-1),full=plans.filter(v=>v.phase==='full'&&v.purpose==='APPLY').at(-1);
 if(!ready&&!terminalState&&p.progress_code!=='READY'&&(bootstrap&&['PREPARING','DRAINING','APPLYING','PREPARED'].includes(bootstrap.state)||full||['APPLYING','VERIFYING','FULL_REVIEW_REQUIRED'].includes(p.progress_code)))dbAdminNode('p','',box,'Warehouse maintenance; waiting for full approval or verification.');
 if(p.access_source==='platform_admin')dbAdminNode('p','',box,'Platform admin access is independent of desired and effective grants.');
 const owner=p.requester_id===JSON.parse(dbPrincipal)[0],actions=p.next_actions||[],stopped=p.progress_code==='STOPPED'||p.blocker==='PRINCIPAL_STOPPED';
 dbCreationNextAction(actionArea,p,bootstrap,full,owner,stopped,terminalState,ready);
 const path='/'+encodeURIComponent(p.creation_id),revision={expected_revision:p.revision};
 const token=dbCreationActionToken(p);
 const current=()=>dbCreationStagedDetailCurrent()&&token===dbCreationActionToken();
 const stagedButton=(id,label,task)=>{
  const button=dbCreationButton(actionArea,id,label,()=>{if($(id)===button&&current()&&!dbCreation.pending)return task();},!current());
  return button;
 };
 if(!owner)dbAdminNode('p','',box,'Only the original requester can change this creation.');
 if(owner&&!stopped){
  if(actions.includes('plans')){
   if(!bootstrap)stagedButton('db-creation-bootstrap','Prepare foundation plan',()=>dbCreationMutate(path+'/plans',{...revision,phase:'bootstrap'}));
   else if(bootstrap.state==='PREPARED'&&!full)stagedButton('db-creation-full','Prepare SQL connection plan',()=>dbCreationMutate(path+'/plans',{...revision,phase:'full'}));
  }
  if(actions.includes('replan')){
   stagedButton('db-creation-replan','Prepare revised plan',()=>dbCreationMutate(path+'/replan',{...revision,phase:bootstrap?.state==='PREPARED'?'full':'bootstrap'}));
   dbAdminNode('p','',actionArea,'A revised plan requires a new review and approval.');
  }
 }
 if(owner&&actions.includes('reconcile')){
  stagedButton('db-creation-reconcile','Reconcile creation',()=>dbCreationMutate(path+'/reconcile',revision));
  dbAdminNode('p','',actionArea,'Reconcile checks the saved state. Limited recovery verification requires a separate review and approval.');
 }
 if(owner&&actions.includes('withdraw'))stagedButton('db-creation-withdraw','Withdraw approval',()=>{
  if(!confirm(t('Withdraw approval for this creation? Future dispatch stops. The LOCAL reservation and effects already applied are retained.'))||!current())return;
  return dbCreationMutate(path+'/withdraw',revision);
 });
 for(const plan of plans){
  const detail=dbAdminNode('details','',box);
  const version=Number.isSafeInteger(plan.sequence)&&plan.sequence>0?t('Plan version {version}',{version:plan.sequence}):'';
  dbAdminNode('summary','',detail).textContent=[plan.phase,plan.purpose,plan.state,version].filter(Boolean).join(' · ');
  dbAdminNode('p','',detail).textContent=plan.public_summary?.impact||plan.public_summary?.code||(plan.purpose==='VERIFY_RECOVERY'?'VERIFY_RECOVERY_LIMITED':plan.phase==='bootstrap'?'BOOTSTRAP_MAINTENANCE':'FULL_RUNTIME_HANDOFF');
  for(const step of plan.steps||[])dbAdminNode('p','',detail).textContent=step.step+' · '+step.state;
  for(const step of plan.public_summary?.fixed_steps||[])if(!plan.steps?.some(s=>s.step===step))dbAdminNode('p','',detail).textContent=step;
  const recovery=plan.purpose==='VERIFY_RECOVERY',latest=plans.filter(v=>v.phase===plan.phase&&v.purpose===plan.purpose).at(-1)===plan;
  if(owner&&!stopped&&latest&&(recovery?actions.includes('recover-verify')&&plan.phase==='full':actions.includes('approve')&&plan.state==='AWAITING_APPLY'))
   stagedButton('db-creation-review-'+plan.phase+(recovery?'-recovery':''),recovery?'Review recovery plan':'Review plan',()=>{
    dbCreation.review={fingerprint:JSON.stringify(plan),revision:p.revision,plan,token};dbCreationRender();$('db-creation-confirm').focus();
   });
 }
 const activeRecovery=p.progress_code==='VERIFYING'&&full?.state==='UNKNOWN'?plans.filter(v=>v.phase==='full'&&v.purpose==='VERIFY_RECOVERY').at(-1):null;
 for(const phase of [bootstrap,activeRecovery||full])if(phase)dbCreationPhase(actionArea,phase);
 if(activeRecovery)dbAdminNode('p','',actionArea,'Recovery verification is limited to the server-reviewed fixed steps.');
 dbCreationSummary(box,p);
 const review=dbCreation.review;
 if(current()&&review&&review.token===token&&review.revision===p.revision&&plans.some(plan=>JSON.stringify(plan)===review.fingerprint)&&actions.includes(review.plan.purpose==='VERIFY_RECOVERY'?'recover-verify':'approve')&&owner&&!stopped){
  const plan=review.plan,recovery=plan.purpose==='VERIFY_RECOVERY',section=dbAdminNode('section','',actionArea);
  dbAdminNode('h4','',section).textContent=t(plan.phase==='bootstrap'?'Foundation setup (approval 1/2)':'SQL connection setup (approval 2/2)')+' · '+t(recovery?'Review recovery plan':'Review plan');dbCreationSummary(section,p);
  dbAdminNode('p','',section,dbCreationGuidance);
  if(Number.isSafeInteger(plan.sequence)&&plan.sequence>0)ui(dbAdminNode('p','',section),'Plan version {version}',{version:plan.sequence});
  if(Number.isSafeInteger(plan.operation_revision)&&plan.operation_revision>0)ui(dbAdminNode('p','',section),'Operation revision {revision}',{revision:plan.operation_revision});
  dbCreationPhase(section,plan);
  if(recovery)dbAdminNode('p','',section,'Recovery verification is limited to the server-reviewed fixed steps.');
  const checkbox=dbAdminField(section,'db-creation-confirm',recovery?'I approve the limited verification in this recovery plan.':'I approve this phase and its warehouse maintenance impact.');checkbox.type='checkbox';
  const reviewed=()=>current()&&review===dbCreation.review&&$('db-creation-confirm')===checkbox&&!dbCreation.pending;
  const approve=dbCreationButton(section,'db-creation-approve',recovery?'Approve recovery verification':'Approve reviewed plan',()=>{
   if(!checkbox.checked||!reviewed()||!confirm(t(recovery?'Confirm limited recovery verification for this reviewed plan?':'Confirm approval of this reviewed phase?'))||!reviewed())return;
   const body={...revision,plan_id:plan.plan_id,operation_id:plan.operation_id,expected_operation_revision:plan.operation_revision,delivery_digest:plan.delivery_digest};
   if(recovery)body.recovery_digest=plan.recovery_digest;
   return dbCreationMutate(path+'/phases/'+plan.phase+(recovery?'/recover-verify':'/approve'),body);
  },true);checkbox.onchange=()=>{approve.disabled=!checkbox.checked||!reviewed();};
 }
 if(p.lifecycle==='READY'){
  if(p.access_source==='none')dbAdminNode('p','',box,'READY; SQL use unavailable for the selected role.');
  else dbCreationButton(actionArea,'db-creation-sql','Open new SQL',()=>dbCreationSQL());
 }
}
async function dbCreationSQL(){
 const scope=dbCreationScope(),id=dbCreation.process?.database_id;if(!id)return;
 try{
  const items=await dbReadInventory(currentRole);if(scope!==dbCreationScope()||!dbInventoryCurrent(items))return;
  dbApplyInventory(items,currentRole);
  const d=items.find(d=>d.id===id&&d.state==='READY'&&d.capabilities?.query&&d.allowed_roles?.includes(currentRole));
  if(!d)throw Object.assign(Error('Unavailable'),{code:'READY; SQL use unavailable for the selected role.'});
  const result=await dbCreationRequest('/api/v1/databases/'+encodeURIComponent(id)+'/schemas',{},scope);
  if(scope!==dbCreationScope()||!dbInventoryCurrent(items)||dbCreation.process?.database_id!==id)return;
  const permitted=result.items?.filter(s=>s.database_id===id&&s.capabilities?.query&&s.allowed_modes?.includes('read-only'))||[];
  const schema=permitted.find(s=>s.sql_name==='raw')||permitted[0];
  if(!schema)throw Object.assign(Error('Unavailable'),{code:'READY; SQL use unavailable for the selected role.'});
  dbSave();const number=Math.max(0,...worksheets.map(w=>Number(/^Worksheet (\d+)$/.exec(w.name)?.[1]||0)))+1;
  const w={id:crypto.randomUUID(),name:'Worksheet '+number,sql:'',database_id:id,schema_id:schema.id,binding_revision:d.binding_revision,generation_id:d.generation_id,warehouse:'LOCAL',mode:'read-only',max_rows:100,selected_role:currentRole};
  worksheets.push(w);worksheetId=w.id;$('sql').value='';dbPersist();const refresh=renderWorksheets(),identity=dbTabIdentity(),request=dbRefreshRequest,input=JSON.stringify(dbInput(w));dbRenderRun();activate($('tab-warehouse'));await refresh;
  if(identity===dbTabIdentity()&&request===dbRefreshRequest&&w===dbCurrent()&&input===JSON.stringify(dbInput(w))&&activeTab==='warehouse'&&dbWorkspacePane==='worksheet')$('sql').focus();
 }catch(error){if(scope===dbCreationScope())dbCreationNotice(error);}
}
if(typeof document.addEventListener==='function')document.addEventListener('visibilitychange',()=>{dbCreationSuspend();if(!document.hidden&&dbAdminAllowed()&&activeTab==='db-admin')void dbCreationLoad();});
