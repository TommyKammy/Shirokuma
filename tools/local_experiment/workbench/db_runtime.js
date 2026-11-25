'use strict';

// Shared LOCAL metadata is independent of worksheet selection. No metadata read wakes compute.
const dbRuntime={installed:false,scope:'',serial:0,status:null,operation:null,intent:null,pending:false,reading:false,observing:false,dirty:false,base:null,conflict:false,support:null,supportPrincipal:'',confirmation:null,known:{names:[],jobs:false,failed:false,pending:false,key:''},knownPending:false,knownPromise:null,contextPromise:null,contextCheckedKey:''};
const dbRuntimeBase='/api/v1/warehouses/LOCAL/runtime';
Object.assign(i18n.ja,{
 'Shared LOCAL compute':'共有 LOCAL コンピュート',
 'Physical observation':'物理状態の観測','Shared operation':'共有操作','Shared controls':'共有コンピュートの操作','Chill safety':'休止前の確認',
 'Auto-Chill minutes':'自動休止までの分数','0 disables Auto-Chill. Range: 0–1440 minutes.':'0 は自動休止を無効にします。設定範囲は 0〜1440 分です。',
 'Shared runtime controls and SQL auto-resume are unsupported for this profile. Use the existing operator workflow.':'このプロファイルでは共有ランタイム操作と SQL の自動復帰は未対応です。従来のオペレーター手順を利用してください。',
 'Runtime support is not confirmed. Refresh status; use the existing operator workflow until support is confirmed.':'ランタイム機能の対応は未確認です。状態を更新し、対応を確認するまでは従来のオペレーター手順を利用してください。',
 'Accepted':'受付済み','Completed':'完了','Failed':'失敗','Unknown':'状態不明','Unknown action':'不明な操作','Revalidated':'状態検証済み',
 'Revalidate state':'状態を再検証','Recheck shared LOCAL state. SQL is never resent.':'共有 LOCAL の現在状態を再検証します。SQL は再送しません。',
 'Creation requests':'DB 作成要求','Creation effects':'DB 作成の適用処理','Creation roots':'DB 作成の基盤処理',
 'Unresolved admissions (included in admitted work)':'結果不明の受付（受付済み処理の内数）',
 'Chill requires all observed categories to be clear. The backend rechecks before stopping.':'休止には観測した全カテゴリの件数が 0 である必要があります。停止前にバックエンドが再検証します。',
 'Operation {id}':'操作 {id}',
 '{guidance} All databases share this compute; storage and catalog remain.':'{guidance} 全 DB がこのコンピュートを共有します。ストレージとカタログは保持されます。',
 'All databases share this compute; Wake and Chill affect all databases while stored data and the catalog remain.':'全 DB がこのコンピュートを共有し、起動・休止は全 DB に影響しますが、保存データとカタログは保持されます。',
 'P-only read-only Platform observations are available. Legacy workload checks, policy audits and other Bear actions remain unavailable.':'P のみの読み取り専用プラットフォーム観測を利用できます。従来のワークロード検査、ポリシー監査、その他の Bear 操作は未対応です。',
 'Physical {state} · observed {time}':'物理状態 {state} · 観測 {time}',
 'Engine verified at {time}':'エンジン検証済み {time}',
 'Engine readiness is unverified. Ready replicas alone do not prove SQL readiness.':'エンジンの準備は未検証です。ready replica 数だけでは SQL の準備完了を証明しません。',
 'Shared operation: {action} · {phase}':'共有操作: {action} · {phase}',
 'No observed shared operation.':'共有操作の観測はありません。',
 'Operation observation unavailable. Last confirmed phase: {phase}. Refresh to check again.':'操作の観測を取得できません。最後に確認した状態: {phase}。更新して再確認してください。',
 'Accepted; completion is not verified.':'受付済みです。完了は未確認です。',
 'Starting shared LOCAL':'共有 LOCAL を起動中','Stopping shared LOCAL':'共有 LOCAL を停止中',
 'Runtime unavailable. Refresh to check again.':'共有状態は取得できません。更新して再確認してください。',
 'Platform authority and the selected admin header role are required.':'基盤権限とヘッダーの admin ロール選択が必要です。',
 'Stored Auto-Chill: {minutes} min · revision {revision} (0 = disabled)':'保存済み自動休止: {minutes} 分 · 版 {revision}（0 = 無効）',
 'Auto-Chill minutes (0–1440; 0 = disabled)':'自動休止までの分数（0〜1440、0 = 無効）',
 'Save Auto-Chill':'自動休止を保存','Reload / discard draft':'再読込／下書きを破棄',
 'Policy changed elsewhere. Reload / discard draft before saving.':'設定が別の場所で変更されました。保存前に再読込／下書きを破棄してください。',
 'Unsaved policy draft.':'設定の下書きは未保存です。','Check original receipt':'元の受付を確認',
 'Outcome UNKNOWN. Check the original receipt with the same key; no automatic retry.':'結果は不明です。同じキーで元の受付を明示確認してください。自動再試行は行いません。',
 'Operation FAILED. Review the shared state before a new action.':'操作は失敗しました。次の操作の前に共有状態を確認してください。',
 'Operation UNKNOWN. Use explicit Reconcile; SQL is never resent.':'操作結果は不明です。明示的に照合してください。SQL は再送しません。',
 'Operation finished; refresh the physical observation and engine proof.':'操作は完了しました。物理状態とエンジン検証の観測を更新してください。',
 'Blockers: {counts}':'停止を妨げる状態: {counts}',
 'Runtime operations':'共有操作','Admitted work':'受付済み処理','Unresolved outcomes':'未解決の結果','Database creation':'DB 作成',
 'Operation {id} · {code}':'操作 {id} · {code}',
 'Blocker observation unavailable; stopping safety is unverified.':'停止を妨げる状態は取得できません。安全な停止は未検証です。',
 'Waiting for shared LOCAL startup. Cancel affects only this SQL request.':'共有 LOCAL の起動を待っています。取消はこの SQL 要求だけに適用されます。',
 'Wake shared LOCAL in Compute before creating a database or running SQL.':'データベース作成や SQL 実行の前に、コンピュート画面で共有 LOCAL を起動してください。'
});
for(const key of Object.keys(i18n.ja))if(!i18n.en[key])i18n.en[key]=key;
Object.assign(i18n.ja,{
 "Runtime transitioning": "遷移中",
 "Conditions observed at {time}. Captured eligibility is not current permission, admission, success or health assurance. The server rechecks when an operation is requested. Use “{refresh}” for a new observation.": "条件の観測時刻: {time}。観測時点の選択条件であり、現在の権限・受付・成功・健全性を保証しません。実際の操作時にサーバーが再確認します。新しい観測は「{refresh}」を利用してください。",
 "Selectable in this observation. Rechecked when operated.": "この観測では選択できます。操作時に再確認します。",
 "This profile does not support shared runtime controls.": "このプロファイルは共有ランタイム操作に対応していません。",
 "Profile support is unconfirmed.": "プロファイルの対応は未確認です。",
 "Current platform authority, maximum admin role and selected admin role are required.": "現在の基盤管理権限・上限 admin・選択 admin が必要です。",
 "No runtime observation is available.": "ランタイムの観測がありません。",
 "This action was not advertised in the captured observation.": "この操作は観測時の利用可能な操作に含まれていません。",
 "A local request is in flight.": "ローカルの要求を送信中です。",
 "Original acceptance is unconfirmed. Check original receipt may POST the original body with the same key; it does not create a new intent.": "元の受付は未確認です。「元の受付を確認」は同じキーと元の本文で POST する場合があります。新しい要求意図は作りません。",
 "A local operation is accepted or running.": "ローカルの操作は受付済みまたは実行中です。",
 "A shared operation was accepted or running in this observation.": "この観測では共有操作が受付済みまたは実行中です。",
 "Chill is blocked by observed categories: {counts}.": "休止を妨げる観測カテゴリ: {counts}。",
 "The stored policy or its revision is unconfirmed.": "保存済み設定またはその revision は未確認です。",
 "No unsaved policy changes.": "未保存の設定変更はありません。",
 "Enter an integer from 0 to 1440; 0 disables Auto-Chill.": "0〜1440 の整数を入力してください。0 は自動休止を無効にします。",
 "No original receipt awaits confirmation.": "確認待ちの元の受付はありません。",
 "The active observation means the fixed Deployments matched expected replica counts and generation conditions. It does not confirm live SQL health.": "稼働状態の観測は、固定 Deployment が期待されるレプリカ数と世代の条件を満たしたことを示します。現在の SQL の健全性は確認していません。",
 "Chilled was observed; its cause and actual stop time are unconfirmed.": "休止状態を観測しました。原因と実際の停止時刻は未確認です。",
 "Transitioning may include starting or stopping.": "遷移中には起動中と停止中の両方が含まれます。",
 "Physical state is unconfirmed; do not assume it is stopped.": "物理状態は未確認です。停止したとは判断できません。",
 "Engine verification is historical evidence, not live health. Saved policy and “{history}” do not establish the current stop cause.": "エンジン検証は過去の証跡であり、現在の健全性を示しません。保存済み設定や「{history}」から現在の停止原因は判断できません。",
 "{action}: {condition}": "{action}: {condition}"});
Object.assign(i18n.en,{'Runtime transitioning':'Transitioning'});
for(const key of Object.keys(i18n.ja))if(!i18n.en[key])i18n.en[key]=key;
function dbRuntimeStamp(){return JSON.stringify([dbPrincipal,dbEpoch,currentRole,dbMaximumRole,dbAdmin.platform,dbAuthorizationVersion]);}
function dbRuntimePurge(render=true){
 const p=dbRuntime;++p.serial;p.scope='';p.status=null;p.operation=null;p.intent=null;p.pending=false;p.reading=false;p.observing=false;p.dirty=false;p.base=null;p.conflict=false;p.failed=false;p.confirmation=null;p.known={names:[],jobs:false,failed:false,pending:false,key:''};p.knownPromise=null;p.contextPromise=null;p.contextCheckedKey='';
 if(p.installed){$('runtime-minutes').value='';if(render)dbRuntimeRender();}
}
function dbRuntimeSync(){
 const p=dbRuntime;if(p.scope!==dbRuntimeStamp()){dbRuntimePurge();p.scope=dbRuntimeStamp();}dbRuntimeRender();
}
// Profile support is public metadata, independent of P-only receipts and current observation availability.
function dbRuntimeSupport(){return dbRuntime.supportPrincipal===dbPrincipal?dbRuntime.support:null;}
function dbRuntimeConfirmProfile(data,source){
 let support=null;
 if(data?.creation_mode==='logical_v1')support='logical';
 else if(data&&!Object.hasOwn(data,'creation_mode')&&(
  source==='status'&&data.data_mode==='database'&&['healthy','degraded'].includes(data.status)||
  source==='capability'&&data.warehouse_id==='LOCAL'&&typeof data.environment_id==='string'&&!!data.environment_id&&typeof data.configured==='boolean'&&typeof data.available==='boolean'))support='unsupported';
 if(support){dbRuntime.support=support;dbRuntime.supportPrincipal=dbPrincipal;}
 if(dbRuntime.installed)dbRuntimeRender();
}
function dbRuntimeDefaultGuidance(){
 return dbRuntimeSupport()==='logical'?'Wake shared LOCAL, then run authorized SQL. Database creation also requires a platform operator to Wake LOCAL in Compute.':dbRuntimeSupport()==='unsupported'?'Shared runtime controls and SQL auto-resume are unsupported for this profile. Use the existing operator workflow.':'Runtime support is not confirmed. Refresh status; use the existing operator workflow until support is confirmed.';
}
function dbRuntimeAllowed(action){return dbRuntimeSupport()==='logical'&&dbAdminAllowed()&&Array.isArray(dbRuntime.status?.available_actions)&&dbRuntime.status.available_actions.includes(action);}
function dbRuntimeLocked(){return dbRuntime.pending||!!dbRuntime.intent||['ACCEPTED','RUNNING'].includes(dbRuntime.operation?.phase);}
function dbRuntimeBlockers(){
 const counts=dbRuntime.status?.blockers,record=(value,keys)=>!!value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).length===keys.length&&keys.every(key=>Object.hasOwn(value,key));
 if(!record(counts,['runtime_operations','admissions','unknown','creation'])||!record(counts.creation,['requests','effects','roots']))return null;
 const items=[['Runtime operations',counts.runtime_operations],['Admitted work',counts.admissions],['Unresolved admissions (included in admitted work)',counts.unknown],['Creation requests',counts.creation.requests],['Creation effects',counts.creation.effects],['Creation roots',counts.creation.roots]];
 return items.every(([,value])=>Number.isSafeInteger(value)&&value>=0)&&counts.unknown<=counts.admissions?items:null;
}
function dbRuntimeCanChill(){const counts=dbRuntimeBlockers();return !!counts&&counts.every(([,value])=>value===0);}
// UX-A6b: the server only returns category counts. Names are added only from
// this tab's own worksheets and from job runs this context can actually read.
function dbRuntimeBlockerTotal(){
 const counts=dbRuntimeBlockers();if(!counts)return null;
 const values=Object.fromEntries(counts);return values['Runtime operations']+values['Admitted work']+values['Creation requests']+values['Creation effects']+values['Creation roots'];
}
function dbRuntimeWorksheetProcesses(){
 if(typeof worksheets==='undefined'||typeof dbRuns==='undefined'||!dbRuns?.get)return [];
 return worksheets.filter(w=>{const run=dbRuns.get(w.id);return !!run&&['QUEUED','RUNNING','UNKNOWN'].includes(run.state);}).map(w=>({kind:'worksheet',name:w.name}));
}
function dbRuntimeKnownProcesses(){
 const seen=new Set(),items=[];
 for(const item of [...(dbRuntime.known.names||[]),...dbRuntimeWorksheetProcesses()]){const key=item.kind+'\u0000'+item.name;if(!seen.has(key)){seen.add(key);items.push(item);}}
 return items;
}
function dbRuntimeProcessLabel(item){
 return t(item.kind==='job'?'job {name}':'worksheet {name}',{name:item.name});
}
function dbRuntimeContextKey(){
 const w=typeof dbCurrent==='function'?dbCurrent():null;
 return JSON.stringify([dbPrincipal,currentRole,w?.id||'',w?.database_id||'',w?.schema_id||'',w?.selected_role||'']);
}
function dbRuntimeEnsureJobContext(){
 const p=dbRuntime;if(p.contextPromise)return p.contextPromise;
 if(!databaseMode||activeTab!=='compute'||typeof dbRefreshContext!=='function'||typeof dbCurrent!=='function')return;
 const w=dbCurrent(),ck=dbRuntimeContextKey();
 if(p.contextCheckedKey===ck)return;
 const reason=w?dbReason(w):t('Choose an available database and schema.');
 if(w&&!reason){p.contextCheckedKey='';void dbRuntimeLoadKnownJobs(true);return;}
 if(!w||reason!==t('Checking context…')){
  p.contextCheckedKey=ck;p.known={names:[],jobs:false,failed:true,pending:false,key:'unavailable:'+ck};dbRuntimeRenderImpact();return;
 }
 const serial=p.serial;
 let release;const gate=new Promise(resolve=>{release=resolve;});p.contextPromise=gate;
 const task=(async()=>{
  p.known={names:[],jobs:false,failed:false,pending:true,key:'pending:'+ck};dbRuntimeRenderImpact();
  try{await dbRefreshContext();}catch{/* dbRefreshContext records its own failure. */}
  if(serial!==p.serial)return;
  p.contextCheckedKey=ck;
  const updated=dbCurrent(),updatedReason=updated?dbReason(updated):t('Choose an available database and schema.');
  if(updated&&!updatedReason){p.contextCheckedKey='';await dbRuntimeLoadKnownJobs();}
  else{p.known={names:[],jobs:false,failed:true,pending:false,key:'unavailable:'+ck};dbRuntimeRender();}
 })();p.contextPromise=task;
 task.finally(()=>{if(p.contextPromise===task)p.contextPromise=null;release();});
 return task;
}
async function dbRuntimeLoadKnownJobs(force=false){
 const p=dbRuntime;if(p.knownPending)return p.knownPromise;
 if(typeof dataopsRequest!=='function'||typeof dbCurrent!=='function')return;
 const total=dbRuntimeBlockerTotal();
 if(!total){p.known={names:[],jobs:false,failed:false,pending:false,key:''};return;}
 const w=dbCurrent(),ready=!!w&&!dbReason(w);
 const key=ready?JSON.stringify([dbRuntimeStamp(),w.id,w.database_id,w.schema_id,total]):'pending:'+total;
 if(!force&&p.known.key===key)return;
 if(!ready){
  const ck=dbRuntimeContextKey();
  if(p.contextCheckedKey===ck){p.known={names:[],jobs:false,failed:true,pending:false,key:'unavailable:'+ck};dbRuntimeRenderImpact();return;}
  p.known={names:[],jobs:false,failed:false,pending:true,key:'pending:'+ck};dbRuntimeRenderImpact();
  void dbRuntimeEnsureJobContext();return;
 }
 let context;try{context=structuredClone(dbContext(w));}catch{return;}
 const serial=p.serial;p.knownPending=true;
 const task=(async()=>{
  try{
   const reply=await dataopsRequest('/api/v1/jobs/runs',context);if(serial!==p.serial)return;
   const runs=Array.isArray(reply?.runs)?reply.runs:[];
   p.known={names:runs.filter(r=>['running','queued'].includes(String(r.status||'').toLowerCase())).map(r=>({kind:'job',name:r.job_name||r.job_id||r.id})),jobs:true,failed:false,pending:false,key};
  }catch{if(serial===p.serial)p.known={names:[],jobs:false,failed:true,pending:false,key};}
 })();p.knownPromise=task;
 try{await task;}finally{if(serial===p.serial){p.knownPending=false;p.knownPromise=null;dbRuntimeRender();}}
}
function dbRuntimeImpact(){
 const total=dbRuntimeBlockerTotal();
 if(total===null)return t('Chill safety could not be confirmed. Refresh the status.');
 if(!total)return t('Chilling stops all SQL. There are no running processes.');
 const base=t('Chill is blocked because {count} processes are running.',{count:total}),gap=language==='ja'?'':' ';
 const names=dbRuntimeKnownProcesses(),pending=!!dbRuntime.known.pending,failed=!!dbRuntime.known.failed;
 if(names.length){
  let detail=t('Confirmed processes: {processes}',{processes:names.map(dbRuntimeProcessLabel).join(language==='ja'?'・':', ')});
  if(pending)detail+=' '+t('Checking job names…');
  else if(failed)detail+=' '+t('Job runs could not be confirmed in this context.');
  return base+gap+detail;
 }
 if(pending)return base+gap+t('Checking confirmed process names…');
 return base+gap+(failed?t('Job runs could not be confirmed in this context.'):t('No process names were returned by the server.'));
}
const workbenchAtlasBlockerKeys={
 'Runtime operations':['Running operations: {count}','Running operation sentence: {count}'],
 'Admitted work':['Admitted work: {count}','Admitted work sentence: {count}'],
 'Unresolved admissions (included in admitted work)':['Unresolved admissions: {count}','Unresolved admissions sentence: {count}'],
 'Creation requests':['Creation requests: {count}','Creation request sentence: {count}'],
 'Creation effects':['Creation effects: {count}','Creation effect sentence: {count}'],
 'Creation roots':['Creation roots: {count}','Creation root sentence: {count}']
};
function workbenchAtlasBlockerSubjects(counts){
 if(!counts)return [];
 const subjects=[];
 for(const [key,value] of counts)if(value>0&&workbenchAtlasBlockerKeys[key])subjects.push(t(workbenchAtlasBlockerKeys[key][0],{count:value}));
 return subjects;
}
function workbenchAtlasBlockerSentences(counts){
 if(!counts)return [t('Pause blockers could not be confirmed. Refresh the state.')];
 if(!counts.some(([,value])=>value>0))return [t('No running or unresolved process is known.')];
 const sentences=[];
 for(const [key,value] of counts)if(value>0&&workbenchAtlasBlockerKeys[key])sentences.push(t(workbenchAtlasBlockerKeys[key][1],{count:value}));
 return sentences;
}
function workbenchAtlasBlockerReason(counts){
 const subjects=workbenchAtlasBlockerSubjects(counts);
 if(!subjects.length)return '';
 return t('{reasons}: shared LOCAL cannot be paused.',{reasons:subjects.join(language==='ja'?'、':', ')});
}
function dbRuntimeRenderImpact(){
 const impact=$('runtime-chill-impact');if(!impact)return;
 const action=dbRuntime.confirmation?.action;
 impact.className='impact'+(action==='wake'?' neutral':'');
 impact.textContent=action==='wake'?t('Starting shared LOCAL returns it to available for all users.'):dbRuntimeImpact();
 const form=$('runtime-chill-confirm');if(form)form.hidden=!dbRuntime.confirmation;
 if($('runtime-actions'))$('runtime-actions').hidden=!!dbRuntime.confirmation;
 // Keep the one impact line immediately before the final control: the primary
 // buttons when closed, or the confirmation buttons when open.
 if(dbRuntime.confirmation&&$('runtime-chill-confirm-actions'))$('runtime-chill-confirm-actions').before(impact);
 else if($('runtime-actions'))$('runtime-actions').before(impact);
 if($('runtime-chill-confirm-title'))$('runtime-chill-confirm-title').textContent=t(action==='wake'?'Wake confirmation':'Chill confirmation');
 if($('runtime-chill-impact-text'))$('runtime-chill-impact-text').textContent=' '+t('I understand the impact on all users.');
 if($('runtime-chill-confirm-submit')){ui($('runtime-chill-confirm-submit'),action==='wake'?'Confirm wake':'Confirm chill');$('runtime-chill-confirm-submit').className=action==='wake'?'primary':'primary danger';}
 if($('runtime-chill-cancel'))ui($('runtime-chill-cancel'),'Back');
}

function dbRuntimePhysicalLabel(state){return t(({active:'Active',chilled:'Chilled',starting:'Runtime transitioning',unknown:'Unknown'})[state]||'Unknown');}
let dbHeaderComputeStateKey;
function dbRenderHeaderComputeState(label,stale=false,operation='',shared=false){
 const key=JSON.stringify([label,stale,operation,shared]);
 if(key===dbHeaderComputeStateKey)return;
 dbHeaderComputeStateKey=key;
 const unsupported=label==='Not supported';
 const state=stale?'stale':unsupported?'unsupported':shared&&(label==='Active'||label==='Running'||label==='Available')?'switching':label==='Active'||label==='Running'||label==='Available'?'available':label==='Chilled'||label==='Stopped'||label==='Paused'?'paused':label==='Runtime transitioning'||label==='Starting'||label==='Transitioning'||label==='Switching'?'switching':label==='Loading…'?'loading':'unknown';
 const tone=state==='available'?'tone-ok':state==='switching'?'tone-run':state==='paused'||state==='loading'?'':'tone-warn';
 const guidance=unsupported?dbRuntimeDefaultGuidance():operation?t(operation):'';
 workbenchSetComputeStatus(state,tone,guidance);
}
function dbRuntimePhaseLabel(phase){return t(({ACCEPTED:'Accepted',RUNNING:'Running',SUCCEEDED:'Completed',FINISHED:'Completed',FAILED:'Failed',UNKNOWN:'Outcome unknown',RECONCILED:'Revalidated'})[phase]||'Outcome unknown');}
function dbRuntimeActionLabel(action){return t(({WAREHOUSE_WAKE:'Wake',WAREHOUSE_CHILL:'Chill',WAREHOUSE_AUTO_CHILL:'Auto-Chill',WAREHOUSE_RECONCILE:'Revalidate state'})[action]||'Unknown action');}
// Pure explanations reuse the operation guards; observations never add age or health gates.
function dbRuntimeDraftMinutes(){const text=$('runtime-minutes').value,minutes=Number(text);return text.trim()&&Number.isInteger(minutes)&&minutes>=0&&minutes<=1440?minutes:null;}
function dbRuntimeCondition(action){
 const p=dbRuntime,data=p.status;
 if(dbRuntimeSupport()!=='logical')return dbRuntimeSupport()==='unsupported'?'This profile does not support shared runtime controls.':'Profile support is unconfirmed.';
 if(!dbAdminAllowed())return 'Current platform authority, maximum admin role and selected admin role are required.';
 if(!data)return 'No runtime observation is available.';
 const advertised=action==='save'||action==='reload'?'auto-chill':action==='receipt'?p.intent?.action:action;
 if(action==='receipt'&&!p.intent)return 'No original receipt awaits confirmation.';
 if(!dbRuntimeAllowed(advertised))return 'This action was not advertised in the captured observation.';
 if(p.pending)return 'A local request is in flight.';
 if(p.intent)return 'Original acceptance is unconfirmed. Check original receipt may POST the original body with the same key; it does not create a new intent.';
 if(['ACCEPTED','RUNNING'].includes(p.operation?.phase))return 'A local operation is accepted or running.';
 if(['wake','chill','reconcile'].includes(action)&&['ACCEPTED','RUNNING'].includes(data.operation_phase))return 'A shared operation was accepted or running in this observation.';
 if(!['active','chilled'].includes(data.physical_state))return 'The state is not confirmed. Refresh or reconcile first.';
 if(action==='wake'&&data.physical_state!=='chilled')return 'Wake is available while paused.';
 if(action==='chill'&&data.physical_state!=='active')return 'Chill is available while running.';
 if(action==='chill'){
  const counts=dbRuntimeBlockers();if(!counts)return 'Blocker observation unavailable; stopping safety is unverified.';
  if(!dbRuntimeCanChill())return 'Chill is blocked by observed categories: {counts}.';
 }
 if(action==='save'){
  const policy=data.policy;if(!Number.isSafeInteger(policy?.minutes)||policy.minutes<0||policy.minutes>1440||!Number.isSafeInteger(policy?.revision)||policy.revision<0)return 'The stored policy or its revision is unconfirmed.';
  if(p.conflict)return 'Policy changed elsewhere. Reload / discard draft before saving.';
  if(!p.dirty)return 'No unsaved policy changes.';
  if(dbRuntimeDraftMinutes()===null)return 'Enter an integer from 0 to 1440; 0 disables Auto-Chill.';
 }
 return 'Selectable in this observation. Rechecked when operated.';
}
function dbRuntimeStateGuidance(state){
 if(dbRuntimeSupport()==='unsupported')return dbRuntimeDefaultGuidance();
 if(dbRuntimeSupport()===null)return 'Runtime support is not confirmed. Refresh status; use the existing operator workflow until support is confirmed.';
 if(state==='chilled')return 'Chilled at the last observation. Wake shared LOCAL, then run SQL.';
 if(state==='active')return 'Shared LOCAL is active. Authorized SQL can run.';
 if(state==='starting')return 'Shared LOCAL is transitioning. Wait for the state to settle.';
 return 'The state is not confirmed. Refresh or revalidate before running SQL.';
}
function dbRuntimeRender(){
 const p=dbRuntime;if(!p.installed)return;const data=p.status,op=p.operation;
 const state=['active','chilled','starting','unknown'].includes(data?.physical_state)?data.physical_state:'unknown';
 const observed=typeof data?.observed_at==='string'?data.observed_at:'—';
 // A pending Wake/Chill confirmation belongs to one observation and one authority.
 const confirmAction=p.confirmation?.action;
 if(p.confirmation&&(p.confirmation.stamp!==dbRuntimeStamp()||p.confirmation.observed!==observed||!dbAdminAllowed()||dbRuntimeLocked()||confirmAction==='chill'&&(state!=='active'||!dbRuntimeCanChill())||confirmAction==='wake'&&state!=='chilled'))p.confirmation=null;
 for(const id of ['runtime-physical',...(data?['warehouse-state']:[])])ui($(id),'Physical {state} · observed {time}',()=>({state:dbRuntimePhysicalLabel(state),time:observed==='—'?'—':fmtJST(observed)}));
 ui($('runtime-proof'),data?.engine_verified===true&&state==='active'&&typeof data?.engine_verified_at==='string'&&Number.isFinite(Date.parse(data.engine_verified_at))?'Engine verified at {time}':'Engine readiness is unverified. Ready replicas alone do not prove SQL readiness.',{time:Number.isFinite(Date.parse(data?.engine_verified_at))?fmtJST(data.engine_verified_at):data?.engine_verified_at});
 const globalID=typeof data?.operation_id==='string'&&/^[A-Za-z0-9_-]{1,128}$/.test(data.operation_id)?data.operation_id:null;
 // A later global operation supersedes a completed local receipt, without adopting its protected detail handle.
 const showLocal=!!op&&(!globalID||globalID===op.id||op.observation===data||dbRuntimeLocked());
 const rawPhase=showLocal?op.phase:data?.operation_phase,rawAction=showLocal?op.action:data?.operation_action,operationID=showLocal?op.id:globalID;
 const phase=['ACCEPTED','RUNNING','SUCCEEDED','FINISHED','FAILED','UNKNOWN','RECONCILED'].includes(rawPhase)?rawPhase:rawPhase?'UNKNOWN':null,action=['WAREHOUSE_WAKE','WAREHOUSE_CHILL','WAREHOUSE_AUTO_CHILL','WAREHOUSE_RECONCILE'].includes(rawAction)?rawAction:null;
 const [,headerLabel,,minutes]=dbRuntimeSummary(),stale=minutes!==null&&minutes>=dbRuntimeStaleMinutes;
 let headerOperation='';
 if(data){
  if(p.pending)headerOperation='Operation in progress';
  else if(p.intent||showLocal&&op.observationUnavailable||phase==='UNKNOWN')headerOperation='Operation outcome unknown';
  else if(phase==='FAILED')headerOperation='Last operation failed';
  else if(['ACCEPTED','RUNNING'].includes(phase))headerOperation=stale?'Operation was in progress':'Operation in progress';
 }
 dbRenderHeaderComputeState(headerLabel,stale,headerOperation,['ACCEPTED','RUNNING'].includes(phase));
 ui($('replicas'),'Replicas {ready} / {desired}',{ready:Number.isSafeInteger(data?.ready_replicas)?data.ready_replicas:'—',desired:Number.isSafeInteger(data?.replicas)?data.replicas:'—'});
 const errorCode=showLocal&&/^[A-Z][A-Z0-9_]{0,63}$/.test(op.error_code)?op.error_code:null;
 ui($('runtime-receipt-info'),operationID?(errorCode?'Operation {id} · {code}':'Operation {id}'):'',{id:operationID,code:errorCode});
 ui($('runtime-observation'),op?.observationUnavailable?'Operation observation unavailable. Last confirmed phase: {phase}. Refresh to check again.':'',()=>({phase:dbRuntimePhaseLabel(op?.phase)}));
 ui($('runtime-operation'),phase?'Shared operation: {action} · {phase}':'No observed shared operation.',()=>({action:dbRuntimeActionLabel(action),phase:dbRuntimePhaseLabel(phase)}));
 const guidance=phase==='ACCEPTED'?'Accepted; completion is not verified.':phase==='RUNNING'&&action==='WAREHOUSE_WAKE'?'Starting shared LOCAL':phase==='RUNNING'&&['WAREHOUSE_CHILL','WAREHOUSE_AUTO_CHILL'].includes(action)?'Stopping shared LOCAL':phase==='FAILED'?'Operation FAILED. Review the shared state before a new action.':phase==='UNKNOWN'?'Operation UNKNOWN. Use explicit Reconcile; SQL is never resent.':['SUCCEEDED','FINISHED','RECONCILED'].includes(phase)?'Operation finished; refresh the physical observation and engine proof.':dbRuntimeStateGuidance(state);
 ui($('runtime-guidance'),guidance);
 const counts=dbRuntimeBlockers();
 ui($('runtime-blockers'),counts?'Blockers: {counts}':'Blocker observation unavailable; stopping safety is unverified.',()=>({counts:counts?counts.map(([label,value])=>t(label)+': '+value).join(' · '):''}));
 const policy=data?.policy,valid=Number.isSafeInteger(policy?.minutes)&&policy.minutes>=0&&policy.minutes<=1440&&Number.isSafeInteger(policy?.revision)&&policy.revision>=0;
 ui($('runtime-policy'),valid?'Stored Auto-Chill: {minutes} min · revision {revision} (0 = disabled)':'Runtime unavailable. Refresh to check again.',policy||{});
 ui($('runtime-draft'),p.conflict?'Policy changed elsewhere. Reload / discard draft before saving.':p.dirty?'Unsaved policy draft.':'');
 for(const action of ['wake','chill','reconcile'])for(const id of [action==='reconcile'?'runtime-reconcile':'runtime-'+action,...(action==='reconcile'?[]:[action])]){
  const node=$(id);node.disabled=!dbRuntimeAllowed(action)||dbRuntimeLocked()||action==='chill'&&!dbRuntimeCanChill()||['ACCEPTED','RUNNING'].includes(data?.operation_phase);node.dataset.databaseUnavailable=String(node.disabled);
 }
 $('runtime-save').disabled=!dbRuntimeAllowed('auto-chill')||dbRuntimeLocked()||!valid||!p.dirty||p.conflict||dbRuntimeDraftMinutes()===null;
 $('runtime-minutes').disabled=!dbRuntimeAllowed('auto-chill')||dbRuntimeLocked()||!valid;
 $('runtime-reload').disabled=!dbRuntimeAllowed('auto-chill')||dbRuntimeLocked();
 $('runtime-save').className=p.dirty&&!p.confirmation?'primary':'';
 $('runtime-receipt').hidden=!p.intent;$('runtime-receipt').disabled=p.pending||!p.intent||!dbRuntimeAllowed(p.intent?.action);
 ui($('runtime-authority'),dbAdminAllowed()?'':'Platform authority and the selected admin header role are required.');
 ui($('runtime-conditions-scope'),'Conditions observed at {time}. Captured eligibility is not current permission, admission, success or health assurance. The server rechecks when an operation is requested. Use “{refresh}” for a new observation.',()=>({time:observed==='—'?'—':fmtJST(observed),refresh:t('Refresh status')}));
 ui($('runtime-physical-meaning'),({active:'The active observation means the fixed Deployments matched expected replica counts and generation conditions. It does not confirm live SQL health.',chilled:'Chilled was observed; its cause and actual stop time are unconfirmed.',starting:'Transitioning may include starting or stopping.',unknown:'Physical state is unconfirmed; do not assume it is stopped.'})[state]);
 ui($('runtime-evidence-meaning'),'Engine verification is historical evidence, not live health. Saved policy and “{history}” do not establish the current stop cause.',()=>({history:t('Saved shared LOCAL operation history')}));
 for(const [action,label] of [['wake','Wake'],['chill','Chill'],['reconcile','Revalidate state'],['save','Save Auto-Chill'],['reload','Reload / discard draft'],['receipt','Check original receipt']]){
  ui($('runtime-condition-'+action),'{action}: {condition}',()=>({action:t(label),condition:t(dbRuntimeCondition(action),{counts:(dbRuntimeBlockers()||[]).filter(([,value])=>value>0).map(([key,value])=>t(key)+': '+value).join(' · ')})}));
 }
 $('runtime-condition-receipt').hidden=!p.intent;
 dbRuntimeSummaryRender();dbRuntimeRenderImpact();dbComputeLayout();
 if(activeTab==='bears'&&typeof workbenchAtlasBearsSync==='function'&&workbenchAtlasBears.installed)workbenchAtlasBearsSync();
}
async function dbRuntimeRequest(path,{body,key,stamp=dbRuntimeStamp()}={}){
 const current=()=>stamp===dbRuntimeStamp();
 const auth=await fetch('/api/session');if(!current())throw Error('STALE');
 if([401,403].includes(auth.status)){dbRuntimePurge();throw Error('FORBIDDEN');}
 const session=await auth.json();if(!current())throw Error('STALE');
 if(!auth.ok||JSON.stringify([session.username,session.authenticated])!==dbPrincipal||session.data_mode!=='database'){dbRuntimePurge();throw Error('STALE');}
 const platform=session.authenticated===true&&session.platform_admin===true&&session.maximum_role==='admin';
 if(platform!==dbAdmin.platform)dbAdminSession(session);
 if(session.maximum_role!==dbMaximumRole)dbUpdateMaximumRole(session.maximum_role);
 dbRuntimeSync();if(!current())throw Error('STALE');
 if(body!==undefined&&!dbRuntimeAllowed(path.split('/').at(-1)))throw Error('FORBIDDEN');
 const headers={'X-Shirokuma-Role':currentRole};if(body!==undefined)Object.assign(headers,{'Content-Type':'application/json','Idempotency-Key':key});
 const response=await fetch(path,{headers,...(body===undefined?{}:{method:'POST',body:JSON.stringify(body)})});
 if(!current())throw Error('STALE');
 if([401,403].includes(response.status)){dbRuntimePurge();throw Error('FORBIDDEN');}
 const data=await response.json();if(!current())throw Error('STALE');
 if(!response.ok||data?.error)throw Object.assign(Error('RUNTIME'),{status:response.status,code:data?.code||data?.error?.code});
 return data;
}
async function dbRuntimeRefresh(){
 const p=dbRuntime;if(!p.installed)return false;dbRuntimeSync();if(p.reading)return !!p.status;p.reading=true;
 const stamp=dbRuntimeStamp(),serial=p.serial,current=()=>stamp===dbRuntimeStamp()&&serial===p.serial;
 let supported=false;
 try{
  const data=await dbRuntimeRequest(dbRuntimeBase,{stamp});if(!current())return true;supported=data?.warehouse==='LOCAL';
  if(data?.warehouse!=='LOCAL'||!['active','chilled','starting','unknown'].includes(data.physical_state)||typeof data.observed_at!=='string'||!Number.isFinite(Date.parse(data.observed_at)))throw Error('DTO');
  if(!Array.isArray(data.available_actions)||!data.available_actions.includes('wake')){
   // Install the validated observation before rendering the cleared scope.
   dbRuntimePurge(false);p.scope=dbRuntimeStamp();
  }
  p.status=data;p.failed=false;p.support='logical';p.supportPrincipal=dbPrincipal;p.known={names:[],jobs:false,failed:false,pending:false,key:''};p.knownPromise=null;
  const policy=data.policy;if(Number.isSafeInteger(policy?.minutes)&&policy.minutes>=0&&policy.minutes<=1440&&Number.isSafeInteger(policy?.revision)&&policy.revision>=0){
   if(p.dirty){if(p.base!==policy.revision)p.conflict=true;}
   else{p.base=policy.revision;$('runtime-minutes').value=String(policy.minutes);}
  }
  ui($('runtime-message'),'');dbRuntimeRender();
  if(activeTab==='compute')await dbRuntimeLoadKnownJobs();
  if(p.operation?.id&&['ACCEPTED','RUNNING'].includes(p.operation.phase)&&dbAdminAllowed())await dbRuntimeObserve();
  return true;
 }catch(error){if(!current())return true;p.status=null;p.failed=true;ui($('runtime-message'),'Runtime unavailable. Refresh to check again.');dbRuntimeRender();if(supported)for(const id of ['warehouse-state'])ui($(id),'Unknown');return supported;}
 finally{if(current())p.reading=false;}
}
async function dbRuntimeAct(action){
 const p=dbRuntime;dbRuntimeSync();if(!dbRuntimeAllowed(action)||dbRuntimeLocked()||action==='chill'&&!dbRuntimeCanChill()||action!=='auto-chill'&&['ACCEPTED','RUNNING'].includes(p.status?.operation_phase))return;
 let body={};if(action==='auto-chill'){
  if(!p.dirty)return;
  const minutes=dbRuntimeDraftMinutes();if(minutes===null||!Number.isSafeInteger(p.base)||p.conflict)return;
  body={minutes,expected_revision:p.base};
 }
 p.intent={action,body,key:crypto.randomUUID()};p.operation=null;return dbRuntimeReceipt();
}
async function dbRuntimeReceipt(){
 const p=dbRuntime,intent=p.intent;if(!intent||p.pending||!dbRuntimeAllowed(intent.action))return;
 const stamp=dbRuntimeStamp(),serial=p.serial,current=()=>stamp===dbRuntimeStamp()&&serial===p.serial&&p.intent===intent;p.pending=true;dbRuntimeRender();
 let rejection=null;
 try{
  const ack=await dbRuntimeRequest(dbRuntimeBase+'/'+intent.action,{body:intent.body,key:intent.key,stamp});if(!current())return;
  if(typeof ack?.operation_id!=='string'||!ack.operation_id||ack.phase!=='ACCEPTED')throw Error('ACK');
  p.operation={id:ack.operation_id,observation:p.status,action:'WAREHOUSE_'+intent.action.replaceAll('-','_').toUpperCase(),phase:'ACCEPTED'};p.intent=null;
  ui($('runtime-message'),'Accepted; completion is not verified.');
 }catch(error){if(current()){
  if(error.status===409&&error.code==='REVISION_CONFLICT'){p.intent=null;p.conflict=true;ui($('runtime-message'),'Policy changed elsewhere. Reload / discard draft before saving.');}
  else if(error.status===409){p.intent=null;p.confirmation=null;rejection={busy:error.code==='WAREHOUSE_BUSY',code:error.code};ui($('runtime-message'),'The request was rejected. Refreshing the state…');}
  else ui($('runtime-message'),'Outcome UNKNOWN. Check the original receipt with the same key; no automatic retry.');
 }}finally{if(stamp===dbRuntimeStamp()&&serial===p.serial){p.pending=false;dbRuntimeRender();}}
 if(rejection&&stamp===dbRuntimeStamp()&&serial===p.serial){
  const refreshed=await dbRuntimeRefresh()&&!!p.status&&!p.failed;
  if(stamp===dbRuntimeStamp()&&serial===p.serial)ui($('runtime-message'),dbRuntimeRejectionMessage(rejection,refreshed));
 }
}
const dbRuntimeRejectionLabels={ADMISSION_CONFLICT:'Admission conflict',WAREHOUSE_UNAVAILABLE:'Warehouse unavailable',RUNTIME_OPERATION_FAILED:'Runtime operation failed',OPERATION_CONFLICT:'Operation conflict',STATE_CONFLICT:'State conflict',IDEMPOTENCY_CONFLICT:'Idempotency conflict'};
function dbRuntimeRejectionLabel(code){return code&&dbRuntimeRejectionLabels[code]?t(dbRuntimeRejectionLabels[code]):'';}
function dbRuntimeRejectionMessage(rejection,refreshed){
 if(rejection.busy)return refreshed?'The chill request was rejected because work is still running. The state was refreshed; review the running processes before trying again.':'The chill request was rejected because work is still running. The state could not be refreshed; use Refresh status.';
 const label=dbRuntimeRejectionLabel(rejection.code);
 if(label)return refreshed?t('The chill request was rejected ({reason}). The state was refreshed.',{reason:label}):t('The chill request was rejected ({reason}). The state could not be refreshed.',{reason:label});
 return refreshed?'The chill request was rejected. The state was refreshed.':'The chill request was rejected. The state could not be refreshed.';
}
function dbRuntimeRequestAction(action){
 const p=dbRuntime,state=p.status?.physical_state;
 if(!dbAdminAllowed()||dbRuntimeLocked())return;
 if(action==='wake'&&state!=='chilled')return;
 if(action==='chill'&&(state!=='active'||!dbRuntimeCanChill()))return;
 if(p.status?.operation_phase&&['ACCEPTED','RUNNING'].includes(p.status.operation_phase))return;
 p.confirmation={action,stamp:dbRuntimeStamp(),observed:p.status?.observed_at};
 dbRuntimeRender();
 ($('runtime-chill-confirm-title')||$('runtime-message')).focus();
}
function dbRuntimeRequestChill(){return dbRuntimeRequestAction('chill');}
function dbRuntimeRequestWake(){return dbRuntimeRequestAction('wake');}
async function dbRuntimeConfirmAction(){
 const p=dbRuntime,c=p.confirmation;
 if(!c||!['wake','chill'].includes(c.action)||p.pending)return;
 if($('runtime-chill-confirm-check')&&!$('runtime-chill-confirm-check').checked)return;
 const state=p.status?.physical_state;
 if(c.stamp!==dbRuntimeStamp()||c.observed!==p.status?.observed_at||dbRuntimeLocked()||c.action==='chill'&&(state!=='active'||!dbRuntimeCanChill())||c.action==='wake'&&state!=='chilled'){p.confirmation=null;dbRuntimeRender();return;}
 const action=c.action;p.confirmation=null;dbRuntimeRender();
 await dbRuntimeAct(action);
}
async function dbRuntimeConfirmChill(){
 if(dbRuntime.confirmation?.action!=='chill')return;
 return dbRuntimeConfirmAction();
}
async function dbRuntimeObserve(){
 const p=dbRuntime,op=p.operation;if(!op||!dbAdminAllowed()||p.pending||p.observing)return;p.observing=true;
 const stamp=dbRuntimeStamp(),serial=p.serial,current=()=>stamp===dbRuntimeStamp()&&serial===p.serial&&p.operation===op;
 try{
  const data=await dbRuntimeRequest(dbRuntimeBase+'/operations/'+encodeURIComponent(op.id),{stamp});if(!current())return;
  if(data?.id!==op.id||data.action!==op.action||!['ACCEPTED','RUNNING','SUCCEEDED','FINISHED','FAILED','UNKNOWN','RECONCILED'].includes(data.phase))throw Error('DTO');
  op.phase=data.phase;op.error_code=data.error_code;op.observationUnavailable=false;
  if(!op.policyReconciled&&op.action==='WAREHOUSE_AUTO_CHILL'&&['SUCCEEDED','FINISHED','RECONCILED'].includes(op.phase)){
   op.policyReconciled=true;
   p.dirty=false;p.conflict=false;p.base=null;
   const policy=p.status?.policy;
   if(Number.isSafeInteger(policy?.minutes)&&policy.minutes>=0&&policy.minutes<=1440&&Number.isSafeInteger(policy?.revision)&&policy.revision>=0){p.base=policy.revision;$('runtime-minutes').value=String(policy.minutes);}
  }
 }catch(error){if(current())op.observationUnavailable=true;}
 if(current()){p.observing=false;dbRuntimeRender();}
}
function dbRuntimeInstall(){
 const p=dbRuntime;if(p.installed)return;p.installed=true;
 const atlas=$('compute-atlas');
 workbenchAtlasComputeShell();
 workbenchAtlasShow(atlas,true);
 ui($('compute-heading'),'Compute & Governance');ui($('compute-environment'),'LOCAL · Local lab');
 const host=workbenchAtlasEnsureHost('compute-runtime-host',atlas);
 const panel=document.createElement('section');panel.id='runtime-panel';panel.className='section';panel.setAttribute('role','region');panel.setAttribute('aria-labelledby','runtime-title');host.append(panel);
 const title=document.createElement('h2');title.id='runtime-title';ui(title,'Shared LOCAL');panel.append(title);
 const facts=document.createElement('dl');facts.id='runtime-facts';facts.className='kv';panel.append(facts);
 const fact=(id,key)=>{const dt=document.createElement('dt');ui(dt,key);const dd=document.createElement('dd');dd.id=id;facts.append(dt,dd);return dd;};
 const stateFact=fact('runtime-state-fact','Status');
 const stateStatus=document.createElement('span');stateStatus.id='runtime-state-status';stateStatus.className='status';stateFact.append(stateStatus);
 const stateBadge=document.createElement('span');stateBadge.id='runtime-state-badge';stateBadge.className='badge';stateBadge.hidden=true;stateFact.append(stateBadge);
 fact('runtime-observed-fact','Observed time');
 const impact=fact('runtime-impact','Impact');ui(impact,'This shared LOCAL is used by every database. Pausing stops new work for everyone; waking makes it available again. Persistent storage is kept.');
 fact('runtime-blockers-fact','Pause blockers');
 const controls=document.createElement('div');controls.id='runtime-controls-section';controls.setAttribute('aria-labelledby','runtime-controls-title');panel.append(controls);
 const controlsTitle=document.createElement('h3');controlsTitle.id='runtime-controls-title';controlsTitle.className='visually-hidden';ui(controlsTitle,'Shared controls');controls.append(controlsTitle);
 const actions=document.createElement('div');actions.id='runtime-actions';actions.className='toolbar';controls.append(actions);
 const refresh=document.createElement('button');refresh.id='runtime-refresh';refresh.type='button';ui(refresh,'Refresh state');refresh.onclick=async()=>{await dbRuntimeRefresh();if(p.operation)await dbRuntimeObserve();};actions.append(refresh);
 const wake=document.createElement('button');wake.id='runtime-wake';wake.type='button';ui(wake,'Wake');wake.onclick=dbRuntimeRequestWake;actions.append(wake);
 const chill=document.createElement('button');chill.id='runtime-chill';chill.type='button';ui(chill,'Chill');chill.onclick=dbRuntimeRequestChill;actions.append(chill);
 const reason=document.createElement('p');reason.id='runtime-action-reason';reason.className='small';controls.append(reason);
 const capability=document.createElement('p');capability.id='runtime-capability';capability.className='small';capability.setAttribute('role','status');capability.setAttribute('aria-atomic','true');controls.append(capability);
 const chillImpact=document.createElement('p');chillImpact.id='runtime-chill-impact';chillImpact.className='impact';chillImpact.setAttribute('role','status');chillImpact.setAttribute('aria-atomic','true');controls.append(chillImpact);
 const confirm=document.createElement('form');confirm.id='runtime-chill-confirm';confirm.hidden=true;confirm.onsubmit=event=>{event.preventDefault();return dbRuntimeConfirmAction();};controls.append(confirm);
 const confirmTitle=document.createElement('h3');confirmTitle.id='runtime-chill-confirm-title';confirmTitle.tabIndex=-1;ui(confirmTitle,'Pause confirmation');confirm.append(confirmTitle);
 const confirmLabel=document.createElement('label');confirmLabel.className='job-enable-label';confirm.append(confirmLabel);
 const check=document.createElement('input');check.id='runtime-chill-confirm-check';check.type='checkbox';check.required=true;confirmLabel.append(check);
 const impactText=document.createElement('span');impactText.id='runtime-chill-impact-text';confirmLabel.append(impactText);
 const confirmActions=document.createElement('div');confirmActions.id='runtime-chill-confirm-actions';confirmActions.className='toolbar';confirm.append(confirmActions);
 const submit=document.createElement('button');submit.id='runtime-chill-confirm-submit';submit.type='submit';submit.className='primary danger';ui(submit,'Confirm pause');submit.onclick=event=>{event?.preventDefault?.();return dbRuntimeConfirmAction();};confirmActions.append(submit);
 const back=document.createElement('button');back.id='runtime-chill-cancel';back.type='button';ui(back,'Back');back.onclick=()=>{dbRuntime.confirmation=null;dbRuntimeRender();$('runtime-chill').focus();};confirmActions.append(back);
 const message=document.createElement('p');message.id='runtime-message';message.setAttribute('role','status');message.tabIndex=-1;controls.append(message);
 const receiptFacts=document.createElement('dl');receiptFacts.id='runtime-receipt-facts';receiptFacts.className='kv';receiptFacts.hidden=true;controls.append(receiptFacts);
 const rdt=document.createElement('dt');ui(rdt,'Original receipt');const rdd=document.createElement('dd');rdd.id='runtime-receipt-summary';receiptFacts.append(rdt,rdd);
 const receipt=document.createElement('button');receipt.id='runtime-receipt';receipt.type='button';ui(receipt,'Reconcile this original receipt');receipt.hidden=true;receipt.onclick=dbRuntimeReceipt;controls.append(receipt);
 for(const id of ['wake','chill'])for(const target of [id,'runtime-'+id]){
  const node=$(target);node.onclick=id==='chill'?dbRuntimeRequestChill:dbRuntimeRequestWake;
  for(let n=uiAttributes.length-1;n>=0;n--)if(uiAttributes[n].node===node&&uiAttributes[n].attribute==='title')uiAttributes.splice(n,1);
  node.title=t('All databases share this compute; Wake and Chill affect all databases while stored data and the catalog remain.');
  uiAttributes.push({node,attribute:'title',key:'All databases share this compute; Wake and Chill affect all databases while stored data and the catalog remain.'});
 }
 // The detailed state remains available inside Administrator diagnostics.
 const details=document.createElement('details');details.id='runtime-details';details.className='runtime-details';
 const detailsSummary=document.createElement('summary');detailsSummary.id='runtime-details-title';ui(detailsSummary,'Observation details and conditions');details.append(detailsSummary);
 const summary=document.createElement('p');summary.id='runtime-summary';summary.hidden=true;summary.setAttribute('role','status');summary.setAttribute('aria-atomic','true');details.append(summary);
 const summaryDetail=document.createElement('p');summaryDetail.id='runtime-summary-detail';summaryDetail.setAttribute('role','status');summaryDetail.setAttribute('aria-atomic','true');details.append(summaryDetail);
 const physical=document.createElement('div');physical.id='runtime-physical-group';details.append(physical);
 for(const [id,key] of [['runtime-physical',null],['runtime-proof',null]]){const node=document.createElement('p');node.id=id;node.setAttribute('role','status');node.setAttribute('aria-atomic','true');physical.append(node);}
 const meaning=document.createElement('details');meaning.id='runtime-meaning';meaning.className='runtime-meaning';const meaningTitle=document.createElement('summary');meaningTitle.id='runtime-meaning-title';ui(meaningTitle,'What this observation means');meaning.append(meaningTitle);
 for(const id of ['runtime-physical-meaning','runtime-evidence-meaning']){const node=document.createElement('p');node.id=id;node.className='runtime-condition help';meaning.append(node);}
 physical.append(meaning);
 const operation=document.createElement('div');operation.id='runtime-operation-group';details.append(operation);
 for(const id of ['runtime-operation','runtime-receipt-info','runtime-observation']){const node=document.createElement('p');node.id=id;node.setAttribute('role','status');node.setAttribute('aria-atomic','true');operation.append(node);}
 const safety=document.createElement('section');safety.id='runtime-safety-section';safety.className='section';safety.setAttribute('aria-labelledby','runtime-safety-title');details.append(safety);
 const safetyTitle=document.createElement('h3');safetyTitle.id='runtime-safety-title';ui(safetyTitle,'Chill safety');safety.append(safetyTitle);
 const blockers=document.createElement('p');blockers.id='runtime-blockers';blockers.setAttribute('role','status');blockers.setAttribute('aria-atomic','true');safety.append(blockers);
 const safetyHelp=document.createElement('p');safetyHelp.id='runtime-safety-help';safetyHelp.className='help';ui(safetyHelp,'Chill requires all observed categories to be clear. The backend rechecks before stopping.');safety.append(safetyHelp);
 const conditions=document.createElement('div');conditions.id='runtime-conditions';conditions.className='runtime-conditions';details.append(conditions);
 for(const id of ['conditions-scope','condition-wake','condition-reconcile','condition-receipt','condition-chill','condition-save','condition-reload','authority']){const node=document.createElement('p');node.id='runtime-'+id;node.className='runtime-condition help';conditions.append(node);}
 // Revalidation is an explicit recovery action, not one of the three primary
 // controls in the mock-up; it stays available in the diagnostics disclosure.
 const reconcile=document.createElement('button');reconcile.id='runtime-reconcile';reconcile.type='button';reconcile.hidden=true;ui(reconcile,'Revalidate state');reconcile.onclick=()=>dbRuntimeAct('reconcile');conditions.append(reconcile);
 const reconcileKey='Recheck shared LOCAL state. SQL is never resent.';reconcile.title=t(reconcileKey);uiAttributes.push({node:reconcile,attribute:'title',key:reconcileKey});
 const guidance=document.createElement('p');guidance.id='runtime-guidance';guidance.setAttribute('role','status');guidance.setAttribute('aria-atomic','true');conditions.append(guidance);
 panel.append(details);
 // Auto-chill is the only persistent setting; the existing CAS/revision
 // handler keeps ownership of the draft and save controls.
 const autoHost=workbenchAtlasEnsureHost('compute-auto-chill-body',$('compute-auto-chill'));
 const policy=document.createElement('section');policy.id='runtime-policy-section';policy.setAttribute('aria-labelledby','runtime-policy-title');autoHost.append(policy);
 const policyTitle=document.createElement('h3');policyTitle.id='runtime-policy-title';policyTitle.className='visually-hidden';ui(policyTitle,'Auto-Chill');policy.append(policyTitle);
 const policyText=document.createElement('p');policyText.id='runtime-policy';policyText.className='small';policyText.setAttribute('role','status');policyText.setAttribute('aria-atomic','true');policy.append(policyText);
 const draft=document.createElement('p');draft.id='runtime-draft';draft.className='small';draft.setAttribute('role','status');draft.setAttribute('aria-atomic','true');policy.append(draft);
 const editor=document.createElement('div');editor.id='runtime-policy-editor';editor.className='atlas-fields';policy.append(editor);
 const field=document.createElement('div');field.id='runtime-minutes-field';field.className='atlas-field';editor.append(field);
 const label=document.createElement('label');label.id='runtime-minutes-label';label.htmlFor='runtime-minutes';ui(label,'Auto-Chill minutes');field.append(label);
 const input=document.createElement('input');input.id='runtime-minutes';input.className='field';input.type='number';input.min='0';input.max='1440';input.step='1';input.setAttribute('aria-describedby','runtime-minutes-help runtime-policy runtime-draft');input.oninput=()=>{if(!p.dirty)p.base=p.status?.policy?.revision;p.dirty=true;dbRuntimeRender();};field.append(input);
 const help=document.createElement('p');help.id='runtime-minutes-help';help.className='help';ui(help,'0 disables Auto-Chill. Range: 0–1440 minutes.');field.append(help);
 const policyActions=document.createElement('div');policyActions.id='runtime-policy-actions';policyActions.className='toolbar';policy.append(policyActions);
 const save=document.createElement('button');save.id='runtime-save';save.type='button';ui(save,'Save Auto-Chill');save.onclick=()=>dbRuntimeAct('auto-chill');policyActions.append(save);
 const reload=document.createElement('button');reload.id='runtime-reload';reload.type='button';ui(reload,'Reload / discard draft');reload.onclick=async()=>{if(dbRuntimeLocked())return;p.dirty=false;p.conflict=false;p.base=null;await dbRuntimeRefresh();};policyActions.append(reload);
 for(const [id,description] of [['runtime-wake','runtime-condition-wake'],['runtime-chill','runtime-condition-chill'],['runtime-reconcile','runtime-condition-reconcile'],['runtime-save','runtime-condition-save'],['runtime-reload','runtime-condition-reload'],['runtime-receipt','runtime-condition-receipt'],['runtime-minutes','runtime-condition-save']]){const node=$(id);node.setAttribute('aria-describedby',[...new Set((node.getAttribute('aria-describedby')||'').split(' ').filter(Boolean).concat(description))].join(' '));}
 ui($('database-compute-notice'),'P-only read-only Platform observations are available. Legacy workload checks, policy audits and other Bear actions remain unavailable.');
 dbPlatformComputeInstall();dbResourcesInstall();dbUsageInstall();dbCapacityInstall();dbLifecycleHistoryInstall();dbBaselineInstall();dbFoundationBaselineInstall();dbRecentFailuresInstall();dbTelemetryReadinessInstall();dbPodBaselineInstall();dbActivityInstall();dbComputeLayout();dbRuntimeSync();
}
// Compute answers, in order: is shared LOCAL usable, what it uses, what this user may do, why Chill is blocked.
// Operator diagnostics stay reachable for platform operators, grouped by purpose.
Object.assign(i18n.ja,{
 'Check whether shared LOCAL is usable, how much it uses and what you can operate.':'共有 LOCAL が使えるか、どれだけ使っているか、自分が何を操作できるかを確認します。',
 'Usage and headroom':'使用量と空き容量',
 'Configured allocation, measured usage and node headroom are separate observations. Do not add or compare them directly.':'設定上の割当、実使用量、Node の空き容量は別々の観測です。そのまま合算・比較しないでください。',
 'Usage, capacity and operator diagnostics are fetched by platform operators. The state above is available to every role.':'使用量・容量・運用診断はプラットフォーム運用者が取得します。上の稼働状態はすべてのロールで確認できます。',
 'Select the admin role to fetch usage, capacity and operator diagnostics.':'使用量・容量・運用診断を取得するには管理者ロールを選択してください。',
 'Not supported':'未対応','Shared runtime controls are not supported for this profile. Use the existing operator workflow.':'このプロファイルは共有ランタイムの操作に対応していません。既存の運用手順を使ってください。',
 'Configured allocation':'設定割当','Measured usage':'観測使用量','Node headroom':'ノード余力',
 'Operator diagnostics':'運用診断の詳細',
 'Pods, Deployments, policy declarations and logs, grouped by purpose. Each fetch is manual and shows its own observation time.':'Pod・Deployment・ポリシー宣言・ログを目的別にまとめています。取得は手動で、結果ごとに観測時刻を表示します。',
 'Runtime records':'稼働の記録','Declarations and policy':'宣言とポリシー','Logs and telemetry':'ログとテレメトリ',
 'What this observation means':'この観測の意味',
 'State unavailable':'取得不可',
 'Stale observation: {state}':'古い観測: {state}',
 'Operation in progress':'操作中',
 'Operation was in progress':'観測時は操作中',
 'Last operation failed':'前回の操作は失敗',
 'Operation outcome unknown':'操作結果不明',
 'Reading the runtime state…':'稼働状態を取得しています…',
 'Could not read the runtime state. The current state is unknown; this is not the same as stopped or zero usage. Refresh to try again.':'稼働状態を取得できませんでした。現在の状態は不明で、休止中や使用量0とは異なります。更新して再度お試しください。',
 'Running at the last observation. The engine verification below is a separate record and may be older.':'最後の観測では稼働中です。下のエンジン検証は別の記録で、この観測より古い場合があります。',
 'Running at the last observation. Engine readiness was not verified.':'最後の観測では稼働中です。エンジンの準備完了は未検証です。',
 'Chilled at the last observation. Authorized SQL starts shared LOCAL automatically; the first query waits for startup.':'最後の観測では休止中です。許可済み SQL を実行すると共有 LOCAL が自動で起動し、最初のクエリは起動を待ちます。',
 'Starting or stopping at the last observation.':'最後の観測では起動または休止の途中です。',
 'The state was not confirmed at the last observation. Do not assume it is stopped.':'最後の観測では状態を確認できませんでした。休止中とはみなさないでください。',
 'Observed less than a minute ago.':'観測は1分以内です。','Observed {minutes} min ago.':'観測は {minutes} 分前です。',
 'This observation may be outdated. Refresh status.':'古い観測の可能性があります。状態を更新してください。',
 'You can Wake, Chill and change Auto-Chill as a platform operator. The server rechecks each request.':'プラットフォーム運用者として、起動・休止・自動休止の変更ができます。各操作はサーバーが再確認します。',
 'Read-only with the current role. Select the admin role to operate shared LOCAL.':'現在のロールでは閲覧のみです。共有 LOCAL を操作するには管理者ロールを選択してください。',
 'Read-only: you can check the state. Authorized SQL starts shared LOCAL automatically; platform operators Wake, Chill and change Auto-Chill.':'閲覧のみ：状態を確認できます。許可済み SQL は共有 LOCAL を自動起動します。起動・休止・自動休止の変更はプラットフォーム運用者が行います。'
});
for(const key of Object.keys(i18n.ja))if(!i18n.en[key])i18n.en[key]=key;
function dbRuntimeCapability(){
 if(dbRuntimeSupport()==='unsupported')return 'This profile does not support shared runtime controls.';
 if(dbAdminAllowed())return 'You can Wake, Chill and change Auto-Chill as a platform operator. The server rechecks each request.';
 if(dbAdmin.platform)return 'Read-only with the current role. Select the admin role to operate shared LOCAL.';
 return 'Read-only: you can check the state. Wake shared LOCAL before running SQL; platform operators Wake, Chill and change Auto-Chill.';
}
const dbRuntimeStaleMinutes=10;
// Missing, failed and observed states stay distinct; an observation never asserts the current state.
function dbRuntimeSummary(){
 const p=dbRuntime,data=p.status;
 if(dbRuntimeSupport()==='unsupported'&&!data)return ['','Not supported','Shared runtime controls are not supported for this profile. Use the existing operator workflow.',null];
 if(!data)return p.failed?['tone-bad','State unavailable','Could not read the runtime state. The current state is unknown; this is not the same as stopped or zero usage. Refresh to try again.',null]:['','Loading…','Reading the runtime state…',null];
 const state=['active','chilled','starting','unknown'].includes(data.physical_state)?data.physical_state:'unknown',at=Date.parse(data.observed_at);
 const minutes=Number.isFinite(at)?Math.max(0,Math.floor((Date.now()-at)/60000)):null;
 const verified=data.engine_verified===true&&typeof data.engine_verified_at==='string'&&Number.isFinite(Date.parse(data.engine_verified_at));
 const text={active:verified?'Running at the last observation. The engine verification below is a separate record and may be older.':'Running at the last observation. Engine readiness was not verified.',chilled:'Chilled at the last observation. Wake shared LOCAL, then run SQL.',starting:'Starting or stopping at the last observation.',unknown:'The state was not confirmed at the last observation. Do not assume it is stopped.'}[state];
 return [{active:'tone-ok',chilled:'',starting:'tone-run',unknown:'tone-warn'}[state],{active:'Available',chilled:'Paused',starting:'Switching',unknown:'Unknown'}[state],text,minutes];
}
function dbRuntimeSummaryRender(){
 if(!$('runtime-summary'))return;
 const [tone,label,text,minutes]=dbRuntimeSummary(),badge=$('runtime-state-badge'),stale=minutes!==null&&minutes>=dbRuntimeStaleMinutes;
 badge.className='badge'+(tone?' '+tone:'');ui(badge,stale?'Stale':label);
 const data=dbRuntime.status;
 if(!data){
  if(dbRuntime.support==='unsupported')ui($('runtime-summary'),'Not supported for this profile.');
  else if(dbRuntime.failed)ui($('runtime-summary'),'State unavailable. Refresh status.');
  else ui($('runtime-summary'),'Reading the runtime state…');
 }else ui($('runtime-summary'),'Observed at {time}',{time:fmtJST(data.observed_at)});
 ui($('runtime-summary-detail'),'{text} {age}',()=>({text:t(text),age:minutes===null?'':(minutes<1?t('Observed less than a minute ago.'):t('Observed {minutes} min ago.',{minutes}))+(minutes>=dbRuntimeStaleMinutes?' '+t('This observation may be outdated. Refresh status.'):'')}));
 const allowed=dbAdminAllowed(),capability=$('runtime-capability');
 if(capability){capability.hidden=allowed;if(!allowed)ui(capability,dbRuntimeCapability());else capability.textContent='';}
 // Per-action conditions explain disabled controls to operators; others read the capability line once.
 for(const [action,id] of [['wake','runtime-wake'],['chill','runtime-chill'],['reconcile','runtime-reconcile'],['reload','runtime-reload']])$('runtime-condition-'+action).hidden=!allowed||!$(id).disabled;
 $('runtime-condition-save').hidden=!allowed||!dbRuntime.dirty||!$('runtime-save').disabled;
 $('runtime-conditions-scope').hidden=!allowed;$('runtime-authority').hidden=true;
}
function dbComputeOrder(parent,nodes){
 nodes=nodes.filter(Boolean);const current=[...parent.children].filter(node=>nodes.includes(node));
 // Moving a node drops focus, so only reorder when the order really differs.
 if(current.length!==nodes.length||current.some((node,index)=>node!==nodes[index]))parent.append(...nodes);
}
function workbenchAtlasCpuQuantity(value){
 if(typeof value!=='string'||!value.trim())return null;
 const text=value.trim();
 if(/^\d+(?:\.\d+)?m$/.test(text))return Number(text.slice(0,-1))/1000;
 if(/^\d+(?:\.\d+)?$/.test(text))return Number(text);
 return null;
}
function workbenchAtlasByteQuantity(value){
 if(typeof value!=='string'||!value.trim())return null;
 const match=/^(\d+(?:\.\d+)?)(Ki|Mi|Gi|Ti|Pi|Ei|k|K|M|G|T|P|E)?$/.exec(value.trim());
 if(!match)return null;
 const units={k:1e3,K:1e3,M:1e6,G:1e9,T:1e12,P:1e15,E:1e18,Ki:1024,Mi:1024**2,Gi:1024**3,Ti:1024**4,Pi:1024**5,Ei:1024**6};
 return Number(match[1])*(units[match[2]||'']||1);
}
function workbenchAtlasFormatCpu(value){return value==null?t('Not collected'):(Math.round(value*100)/100).toString()+' '+t('cores');}
function workbenchAtlasFormatBytes(value){
 if(value==null)return t('Not collected');
 if(value>=1024**3)return (value/1024**3).toFixed(2)+' GiB';
 if(value>=1024**2)return (value/1024**2).toFixed(0)+' MiB';
 return Math.round(value)+' B';
}
function workbenchAtlasComputeMetrics(){
 const result={cpuSet:null,memorySet:null,cpuUse:null,memoryUse:null,cpuHead:null,memoryHead:null,storageHead:null,sameSample:true};
 const resources=dbResources.record?.resources;
 if(resources?.availability==='available'&&Array.isArray(resources.deployments)){
  let cpu=0,memory=0,filled=false;
  for(const deployment of resources.deployments)for(const item of deployment.containers||[]){
   const itemCpu=workbenchAtlasCpuQuantity(item.requests?.cpu),itemMemory=workbenchAtlasByteQuantity(item.requests?.memory);
   if(itemCpu!==null){cpu+=itemCpu;filled=true;}if(itemMemory!==null){memory+=itemMemory;filled=true;}
  }
  if(filled){result.cpuSet=cpu;result.memorySet=memory;}
 }
 const usage=dbUsage.record;
 if(usage?.availability==='available'&&Array.isArray(usage.samples)&&usage.samples.length){
  const samples=new Set(usage.samples.map(sample=>sample.sampled_at));
  result.sameSample=samples.size===1;
  if(result.sameSample){let cpu=0,memory=0;for(const sample of usage.samples){const value=Number(sample.cpu_cores);if(Number.isFinite(value))cpu+=value;const bytes=Number(sample.memory_bytes);if(Number.isFinite(bytes))memory+=bytes;}result.cpuUse=cpu;result.memoryUse=memory;}
 }
 const capacity=dbCapacity.record;
 if(capacity?.availability==='available'&&capacity.core){
  const cpu=Number(capacity.core.cpu_allocatable_cores);if(Number.isFinite(cpu))result.cpuHead=cpu;
  const memory=Number(capacity.core.memory_available_bytes);if(Number.isFinite(memory))result.memoryHead=memory;
  const storage=Number(capacity.core.root_fs?.available_bytes);if(Number.isFinite(storage))result.storageHead=storage;
 }
 return result;
}
function workbenchAtlasRenderComputeTable(){
 const host=$('compute-usage'),atlas=$('compute-atlas');if(host.parentElement!==atlas)atlas.append(host);
 let wrap=workbenchAtlasPeek('compute-resource-table');
 if(!wrap){wrap=document.createElement('div');wrap.id='compute-resource-table';wrap.className='tablewrap';wrap.tabIndex=0;wrap.setAttribute('role','region');host.prepend(wrap);}
 wrap.setAttribute('aria-label',t('LOCAL allocation, observed usage and node headroom'));
 const metrics=workbenchAtlasComputeMetrics();
 const key=JSON.stringify([metrics,dbResources.record?.observed_at,dbUsage.record?.observed_at,dbCapacity.record?.observed_at,language]);
 if(wrap.dataset.key===key&&wrap.children.length)return;
 wrap.dataset.key=key;wrap.replaceChildren();
 const table=document.createElement('table'),caption=document.createElement('caption');
 ui(caption,'Allocation, observed usage and node headroom are separate samples.');table.append(caption);
 const thead=document.createElement('thead'),headRow=document.createElement('tr');
 for(const key of ['Resource','Configured allocation','Observed usage','Node headroom']){const th=document.createElement('th');th.scope='col';ui(th,key);headRow.append(th);}
 thead.append(headRow);table.append(thead);
 const tbody=document.createElement('tbody');
 const observed={set:dbResources.record?.observed_at,use:dbUsage.record?.observed_at,head:dbCapacity.record?.observed_at};
 const rows=[
  ['CPU',workbenchAtlasFormatCpu(metrics.cpuSet),workbenchAtlasFormatCpu(metrics.cpuUse),workbenchAtlasFormatCpu(metrics.cpuHead)],
  ['Memory',workbenchAtlasFormatBytes(metrics.memorySet),workbenchAtlasFormatBytes(metrics.memoryUse),workbenchAtlasFormatBytes(metrics.memoryHead)],
  ['Storage',t('Not configured'),t('No measured value'),workbenchAtlasFormatBytes(metrics.storageHead)]
 ];
 for(const [label,set,use,head] of rows){
  const row=document.createElement('tr'),th=document.createElement('th');th.scope='row';ui(th,label);row.append(th);
  for(const [index,value] of [set,use,head].entries()){
   const td=document.createElement('td');td.textContent=value;
   if(index===0&&observed.set)td.append(Object.assign(document.createElement('small'),{textContent:fmtJST(observed.set)}));
   if(index===1&&observed.use)td.append(Object.assign(document.createElement('small'),{textContent:fmtJST(observed.use)}));
   if(index===2&&observed.head)td.append(Object.assign(document.createElement('small'),{textContent:fmtJST(observed.head)}));
   row.append(td);
  }
  tbody.append(row);
 }
 table.append(tbody);wrap.append(table);
}
function workbenchAtlasEnsureDetails(id,parent,summaryKey){
 let node=$(id);
 if(!node.parentElement){
  if(node.tagName!=='DETAILS'){
   const replacement=document.createElement('details');replacement.id=id;
   while(node.children.length)replacement.append(node.children[0]);
   node=replacement;
  }
  parent.append(node);
 }
 let summary=node.children[0]&&node.children[0].tagName==='SUMMARY'?node.children[0]:null;
 if(!summary){summary=document.createElement('summary');ui(summary,summaryKey);node.prepend(summary);}
 else ui(summary,summaryKey);
 if(typeof node.open!=='boolean')node.open=false;
 return node;
}
function workbenchAtlasComputeShell(){
 const root=$('compute'),atlas=$('compute-atlas');
 if(atlas.parentElement!==root)root.append(atlas);
 let overview=$('compute-overview');
 if(!overview.parentElement){
  overview=document.createElement('div');overview.id='compute-overview';overview.className='pageheading';atlas.prepend(overview);
 }
 workbenchAtlasEnsure('h1','compute-heading',overview);
 workbenchAtlasEnsure('span','compute-overview-title',overview,'visually-hidden');
 workbenchAtlasEnsure('span','compute-environment',overview,'badge');
}
function workbenchAtlasComputeSync(){
 if(!dbRuntime.installed)return;
 const summary=dbRuntimeSummary(),label=summary[1];
 const status=$('runtime-state-status');
 status.className='status'+(summary[0]==='tone-ok'?' success':summary[0]==='tone-warn'?' warning':summary[0]==='tone-bad'?' error':'');
 workbenchAtlasText(status,label);
 const stale=summary[3]!==null&&summary[3]>=dbRuntimeStaleMinutes;
 $('runtime-state-badge').className='badge'+(summary[0]?' '+summary[0]:'');
 workbenchAtlasText($('runtime-state-badge'),stale?'Stale':label);
 $('runtime-state-badge').hidden=true;
 const observed=dbRuntime.status?.observed_at;
 $('runtime-observed-fact').textContent=observed?fmtJST(observed)+' · '+t('Valid for 10 minutes'):'—';
 const admin=dbAdminAllowed(),blockers=dbRuntimeBlockers(),blockerFact=$('runtime-blockers-fact');
 const state=dbRuntime.status?.physical_state;
 const locked=dbRuntimeLocked()||['ACCEPTED','RUNNING'].includes(dbRuntime.status?.operation_phase);
 const wakeAllowed=dbRuntimeAllowed('wake')&&state==='chilled'&&!locked;
 const chillAllowed=dbRuntimeAllowed('chill')&&state==='active'&&dbRuntimeCanChill()&&!locked;
 for(const id of ['runtime-wake','wake']){const node=$(id);if(node)node.disabled=!wakeAllowed;}
 for(const id of ['runtime-chill','chill']){const node=$(id);if(node)node.disabled=!chillAllowed;}
 if(!admin)blockerFact.textContent=t('Details are visible to administrators.');
 else blockerFact.textContent=workbenchAtlasBlockerSentences(blockers).join(' ');
 let reason='';
 if(admin){
  const condition=action=>t(dbRuntimeCondition(action),{counts:(blockers||[]).filter(([,value])=>value>0).map(([key,value])=>t(key)+': '+value).join(' · ')});
  const blockerReason=workbenchAtlasBlockerReason(blockers);
  if(!['active','chilled'].includes(state))reason=condition('wake');
  else if(state==='chilled')reason=wakeAllowed?t('Wake returns shared LOCAL to available for all users.'):condition('wake');
  else if(!chillAllowed&&blockers&&blockers.some(([,value])=>value>0))reason=blockerReason;
  else if(!chillAllowed)reason=condition('chill');
  else reason=t('Pausing stops new work for all users.');
 }
 workbenchAtlasText($('runtime-action-reason'),reason);
 $('runtime-action-reason').hidden=!reason||!!dbRuntime.confirmation;
 $('runtime-chill-impact').hidden=!dbRuntime.confirmation;
 const capability=$('runtime-capability');
 if(admin){capability.hidden=true;capability.textContent='';}
 else{capability.hidden=false;ui(capability,dbRuntimeCapability());}
 const viewer=$('compute-viewer-guidance');if(viewer){viewer.hidden=true;viewer.textContent='';}
 const operation=dbRuntime.operation,intent=dbRuntime.intent,hasReceipt=!!intent;
 $('runtime-receipt-facts').hidden=!hasReceipt;
 if(hasReceipt){
  const action=operation?.action||intent?.action,phase=operation?.phase;
  $('runtime-receipt-summary').textContent=[action?t(dbRuntimeActionLabel(action)):t('Outcome unknown'),phase?t(dbRuntimePhaseLabel(phase)):t('Outcome unknown'),observed?fmtJST(observed):''].filter(Boolean).join(' · ');
 }
 $('runtime-receipt').hidden=!intent;
 $('runtime-receipt').disabled=!intent||dbRuntime.pending||!dbRuntimeAllowed(intent?.action);
 const usage=$('compute-usage'),diagnostics=$('compute-diagnostics');
 if(usage)usage.hidden=false;
 if(diagnostics)diagnostics.hidden=!admin;
 const list=$('compute-usage-list'),note=$('compute-usage-note');
 if(list)list.hidden=!admin;
 if(note)note.hidden=admin;
 ui(note,admin?'Configured allocation, measured usage and node headroom are separate observations. Do not add or compare them directly.':dbAdmin.platform?'Select the admin role to fetch usage, capacity and operator diagnostics.':'Usage, capacity and operator diagnostics are fetched by platform operators. The state above is available to every role.');
 if(admin)workbenchAtlasRenderComputeTable();
 const auto=$('compute-auto-chill');if(auto)auto.hidden=false;
 const notice=$('database-compute-notice');if(notice)notice.hidden=!admin;
}
function dbComputeLayout(){
 const root=$('compute'),atlas=$('compute-atlas');if(!dbRuntime.installed)return;
 workbenchAtlasComputeShell();
 workbenchAtlasShow(atlas,true);
 ui($('compute-heading'),'Compute & Governance');ui($('compute-overview-title'),'Compute & Governance');ui($('compute-environment'),'LOCAL · Local lab');
 const usage=workbenchAtlasEnsureDetails('compute-usage',atlas,'Resource allocation, usage and headroom');
 const auto=workbenchAtlasEnsureDetails('compute-auto-chill',atlas,'Auto-chill settings');
 const diagnostics=workbenchAtlasEnsureDetails('compute-diagnostics',atlas,'Administrator diagnostics');
 if(diagnostics.parentElement!==atlas)atlas.append(diagnostics);
 const usageList=$('compute-usage-list'),usageNote=$('compute-usage-note');
 if(usageNote.parentElement!==usage)usage.append(usageNote);
 if(usageList.parentElement!==usage)usage.append(usageList);
 const body=workbenchAtlasEnsureHost('compute-diagnostics-body',diagnostics);
 const notice=$('database-compute-notice');if(notice&&notice.parentElement!==body)body.prepend(notice);
 let ai=workbenchAtlasPeek('compute-ai-settings');
 if(!ai){ai=document.createElement('section');ai.id='compute-ai-settings';ai.className='compute-group';ai.setAttribute('aria-labelledby','compute-ai-settings-title');body.append(ai);}
 const aiTitle=workbenchAtlasPeek('compute-ai-settings-title');if(!aiTitle){const title=document.createElement('h2');title.id='compute-ai-settings-title';ui(title,'AI settings');ai.append(title);}
 const aiLead=workbenchAtlasPeek('compute-ai-settings-lead');if(!aiLead){const lead=document.createElement('p');lead.id='compute-ai-settings-lead';lead.className='help';ui(lead,'AI model settings are shared by the platform.');ai.append(lead);}
 const aiRead=workbenchAtlasPeek('ai-settings-readonly');if(!aiRead){const node=document.createElement('p');node.id='ai-settings-readonly';node.setAttribute('role','status');node.hidden=true;ai.append(node);}
 const aiOpen=workbenchAtlasPeek('compute-ai-settings-open');if(!aiOpen){const button=document.createElement('button');button.id='compute-ai-settings-open';button.type='button';ui(button,'Open AI settings');button.onclick=()=>{if(typeof dbAISettingsOpen==='function')return dbAISettingsOpen();};ai.append(button);}
 const settings=$('ai-settings');if(settings.parentElement!==ai)ai.append(settings);
 const groups=[['compute-diag-state','State and conditions',['runtime-details']],['compute-diag-history','History and activity',['observation-activity-panel']],['compute-diag-runtime','Runtime records',['observation-compute-panel','observation-history-panel','observation-foundation-controllers-panel']],['compute-diag-policy','Declarations and policy',['observation-baseline-panel','observation-foundation-baseline-panel','observation-pod-baseline-panel','observation-controller-baseline-panel']],['compute-diag-logs','Logs and telemetry',['observation-recent-failures-panel','observation-telemetry-readiness-panel','observation-telemetry-logs-panel']]];
 for(const [id,label,ids] of groups){let box=workbenchAtlasPeek(id);if(!box){box=document.createElement('section');box.id=id;box.className='compute-diag-group';box.setAttribute('aria-labelledby',id+'-title');const title=document.createElement('h3');title.id=id+'-title';ui(title,label);box.append(title);body.append(box);}else if(box.children[0])ui(box.children[0],label);}
 const attached=id=>{const node=$(id);return node?.parentElement?node:null;};
 dbComputeOrder(usageList,['observation-resources-panel','observation-usage-panel','observation-capacity-panel'].map(attached));
 for(const [id,,ids] of groups)dbComputeOrder(workbenchAtlasPeek(id),ids.map(attached));
 for(const [id,key] of [['observation-resources-panel','Configured allocation'],['observation-usage-panel','Measured usage'],['observation-capacity-panel','Node headroom']]){const panel=attached(id),headPanel=panel?.children[0];if(!headPanel||panel.dataset.computeKind)continue;panel.dataset.computeKind='true';const kind=document.createElement('span');kind.className='compute-kind';ui(kind,key);headPanel.prepend(kind);}
 workbenchAtlasRenderComputeTable();
 const allowed=dbAdminAllowed();
 if(usageList)usageList.hidden=!allowed;
 if(diagnostics)diagnostics.hidden=!allowed;
 if(aiRead)aiRead.hidden=allowed;
 if(activeTab!=='compute'){dbAISettings.autoOpened=false;dbAISettings.open=false;}
 dbAISettingsSync();
 if(activeTab==='compute'&&dbRuntimeBlockerTotal()>0)void dbRuntimeLoadKnownJobs();
 workbenchAtlasComputeSync();
}
function workbenchAtlasComputeLanguageChanged(){if(dbRuntime.installed)dbComputeLayout();}
