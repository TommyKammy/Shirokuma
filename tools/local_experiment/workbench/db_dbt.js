'use strict';
function dbDbtOwner(){return JSON.parse(dbPrincipal||'[]')[0];}
function dbDbtAllowed(){
 const p=dbDbt,c=p.context;if(p.invalid||!c||p.principal!==dbPrincipal||p.header!==currentRole||p.maximum!==dbMaximumRole||c.database_id!==p.database||c.schema_id!==p.schema||c.selected_role!==p.role||!['admin','data_engineer'].includes(p.role)||!dbRoleWithinHeader(p.role))return false;
 const d=dbRoleItems(p.role).find(d=>d.id===p.database),s=(p.schemas||[]).find(s=>s.id===p.schema);
 return !!(d&&s&&d.state==='READY'&&d.allowed_roles?.includes(p.role)&&d.capabilities?.query===true&&s.capabilities?.write===true&&s.capabilities?.query===true&&String(s.purpose||s.sql_name).toUpperCase()==='SANDBOX'&&d.binding_revision===c.binding_revision&&d.generation_id===c.generation_id&&(d.grant_revision??null)===c.grant_revision);
}
function dbDbtPurge(){
 const p=dbDbt;if(!p.installed)return;const hadStandalone=!!p.standalone;++p.epoch;++p.request;clearTimeout(p.timer);p.timer=null;p.pending=false;p.invalid=true;p.context=null;p.databases=[];p.schemas=[];p.plan=null;p.run=null;p.plans=[];p.runs=[];p.projects=[];p.publications={};p.publicationHistory=[];p.standalone=null;p.historyWarning=null;if(hadStandalone)dbDbtURL(true);p.review=null;p.preview=null;p.runOptions=null;p.graphOptions=null;p.node='';p.method='sql_wap';p.nativeTests=false;
 if(p.intent&&(p.intent.principal!==dbPrincipal||p.intent.header!==currentRole))p.intentBlocked=true;
 for(const name of ['plan-detail','models','sql','run-detail','candidates','tests','publications','confirmation','history'])dbDbtNode(name).replaceChildren();
 ui(p.status,p.role==='bi_viewer'||!dbRoleWithinHeader('data_engineer')?'Viewer access is unavailable. Choose an authorized data_engineer or admin role; source SQL is hidden.':'Refresh an authorized Database and SANDBOX context.');dbDbtRender();
}
function dbDbtValidate(){const p=dbDbt;if(!p.installed)return;if(!p.invalid&&!dbDbtAllowed()||p.principal!==dbPrincipal||p.header!==currentRole)dbDbtPurge();else dbDbtRender();}
function dbDbtGuard(){const p=dbDbt,epoch=p.epoch,request=++p.request,principal=dbPrincipal,header=currentRole,context=p.context;return ()=>epoch===p.epoch&&request===p.request&&principal===dbPrincipal&&header===currentRole&&dataopsSameContext(context,p.context)&&dbDbtAllowed();}
function dbDbtArtifact(a,kind,context,id){
 const p=dbDbt,states={plan:['QUEUED','COMPILING','READY','FAILED'],run:['QUEUED','BUILDING','TESTING','READY','FAILED','CANCELING','CANCELED','UNKNOWN','NATIVE_VERIFIED'],publication:['QUEUED','AUDITING','PUBLISHING','PUBLISHED','FAILED','CANCELING','CANCELED','UNKNOWN','COMMITTED_RECORDING_FAILED']};
 if(!a||a.kind!==kind||typeof a.id!=='string'||!a.id||id&&a.id!==id||!states[kind]?.includes(a.state)||!a.context||typeof a.actor!=='string')return false;
 if(a.state==='NATIVE_VERIFIED'&&!['native_table','native_graph'].includes(a.executor))return false;
 if(kind==='publication'&&Object.hasOwn(a,'views')&&(a.origin!=='native_graph'||a.context.selected_role!=='admin'||typeof a.source_digest!=='string'||!a.source_digest||typeof a.run_id!=='string'||!a.run_id||a.run_id!==a.parent_run_id||!Array.isArray(a.views)||!a.views.every(v=>typeof v?.node_id==='string'&&!!v.node_id&&v.node_id!==a.node_id)||!dbDbtViewsValid(a.views,a.views.map(v=>({unique_id:v.node_id,materialized:'view'})),a.context)))return false;
 if(kind==='run'&&a.executor==='native_table'&&(typeof a.selected_node_id!=='string'||!a.selected_node_id))return false;
 if(kind==='run'&&a.executor==='native_graph'&&(a.selected_node_id!=null||!Array.isArray(a.models)||a.models.some(m=>m.materialized==='view'&&(m.candidate!=null||m.state==='NATIVE_VERIFIED')&&!dbDbtNativeViewValid(m,a))))return false;
 const saved=a.context;
 return ['database_id','schema_id','warehouse_id','sql_catalog','sql_schema','binding_revision','generation_id'].every(k=>saved[k]===context[k])&&['admin','data_engineer'].includes(saved.selected_role)&&dbRoleWithinHeader(context.selected_role)&&(context.selected_role==='admin'||a.actor===dbDbtOwner()&&dataopsSameContext(saved,context))&&p.principal===dbPrincipal;
}
async function dbDbtRequest(path,context,body,key){
 const headers={'X-Shirokuma-Role':context.selected_role};if(key){headers['Idempotency-Key']=key;}
 const response=await fetch(body===undefined?path+(path.includes('?')?'&':'?')+'context='+encodeURIComponent(JSON.stringify(context)):path,body===undefined?{headers}:{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify({...body,context})});
 const data=await response.json();if(!response.ok)throw dbError(data,response.status);if(!dataopsSameContext(data.context,context))throw Error('context');return data;
}
function dbDbtError(error,mutation=false){
 if(dataopsAuthError(error)){dbDbtPurge();return;}
 if(error.status===409&&error.code==='DBT_VIEW_CHANGED'&&!mutation){ui(dbDbt.status,'SANDBOX VIEW changed. Build and test a fresh graph before reviewing publication again.');dataopsNode('span','',dbDbt.status).textContent=' '+error.code;return;}
 const identity=['STALE_RESOURCE','STAGE_CHANGED','DBT_SOURCE_CHANGED','DBT_CANDIDATE_CHANGED','INVALID_BINDING','UNKNOWN_SCHEMA','SCHEMA_NOT_FOUND'].includes(error.code);
 ui(dbDbt.status,identity?'Source or target identity changed. Refresh context and compile a new plan after inspecting the schemas.':mutation?'Receipt not observed. Recover repeats only the original request with the same key and context.':'Observation failed. Saved backend state is unchanged. Retry observation.');
 if(error.code)dataopsNode('span','',dbDbt.status).textContent=' '+error.code;
}
async function dbDbtRefreshAuthorization(){
 const p=dbDbt;if(!p.started||p.invalid)return;const epoch=p.epoch,principal=dbPrincipal,header=currentRole;
 try{const items=await dbReadInventory(p.role);if(epoch!==p.epoch||principal!==dbPrincipal||header!==currentRole||!dbInventoryCurrent(items))return;dbApplyInventory(items,p.role);
 const reply=await dbAPI('/api/v1/databases/'+encodeURIComponent(p.database)+'/schemas',p.role);if(epoch!==p.epoch||principal!==dbPrincipal||header!==currentRole)return;p.schemas=reply.items||[];dbDbtValidate();}
 catch(error){if(epoch===p.epoch&&principal===dbPrincipal&&error.code!=='AUTH_READ_STALE')dbDbtPurge();}
}
async function dbDbtRefresh(){
 const p=dbDbt;if(p.pending)return;const selection=Object.fromEntries(['source','target'].map(name=>[name,dbDbtNode(name).value]));p.started=true;dbDbtPurge();const epoch=p.epoch,request=++p.request,principal=dbPrincipal,header=currentRole,role=p.role;
 p.principal=principal;p.header=header;p.maximum=dbMaximumRole;const current=()=>epoch===p.epoch&&request===p.request&&principal===dbPrincipal&&header===currentRole&&role===p.role;
 if(!['admin','data_engineer'].includes(role)||!dbRoleWithinHeader(role)){dbDbtRender();return;}
 p.pending=true;dbDbtRender();
 try{const items=await dbReadInventory(role);if(!current()||!dbInventoryCurrent(items))return;dbApplyInventory(items,role);p.databases=items;
 const reply=p.database?await dbAPI('/api/v1/databases/'+encodeURIComponent(p.database)+'/schemas',role):{items:[]};if(!current())return;p.schemas=reply.items||[];
 p.schema=p.schemas.find(s=>String(s.purpose||s.sql_name).toUpperCase()==='SANDBOX')?.id||'';
 if(!p.schema){ui(p.status,'Refresh an authorized Database and SANDBOX context.');return;}
 p.context=dataopsFreeze(dataopsContext(p));p.invalid=false;if(!dbDbtAllowed()){dbDbtPurge();return;}
 for(const name of ['source','target'])dbDbtNode(name).value=p.schemas.some(s=>s.id===selection[name]&&s.capabilities?.query===true)?selection[name]:'';
 const projects=await dbDbtRequest(dbDbtPath('projects'),p.context);if(!current()||!dbDbtAllowed())return;if(!Array.isArray(projects.projects)||projects.projects.some(x=>typeof x.id!=='string'||!x.id))throw Error('projects');p.projects=projects.projects;
 p.rejectedIntent=readStorage(dbDbtKey+'.rejected',p.rejectedIntent);const stored=readStorage(dbDbtKey,null);if(!p.intent&&stored?.principal===principal&&stored.header===header&&dataopsSameContext(stored.context,p.context)&&dbDbtIntentValid(stored))p.intent=dataopsFreeze(stored);
 p.intentBlocked=!!p.intent&&(p.intent.principal!==principal||p.intent.header!==header||!dataopsSameContext(p.intent.context,p.context));
 ui(p.status,'Choose an approved project and explicit registered source and target schemas.');
 }catch(error){if(current())dbDbtError(error);}finally{if(current()){p.pending=false;dbDbtRender();}}
}
// Compatibility comes only from the selected saved plan. Unknown contracts fail closed.
function dbDbtCompatibility(a){
 if(!a||a.compatibility===undefined)return ['QUEUED','COMPILING'].includes(a?.state)?'pending':'legacy';
 const c=a.compatibility;
 if(!c||![1,2].includes(c.version)||c.executor!=='sql_wap'||!Array.isArray(c.issues)||!['supported','blocked'].includes(c.status))return 'invalid';
 return c.status==='supported'&&c.issues.length?'invalid':c.status;
}
function dbDbtCompatibilityAction(a){
 const state=dbDbtCompatibility(a);
 if(state==='pending')return 'Wait for validation of the original saved plan. No new compile request is needed.';
 if(state==='legacy'||state==='invalid')return 'Recompile to validate settings before a new build. Existing runs and publications retain their saved gates.';
 if(state==='blocked')return a.compatibility.issues.some(i=>i?.code==='UNSUPPORTED_MATERIALIZATION')?'For staging, explicitly choose ephemeral, or use a supported project, then recompile. No automatic conversion.':'Review the settings and compile a supported project.';
 return 'Build candidates and test using the saved plan and applied table formats below.';
}
function dbDbtFormat(m){
 if(['ephemeral','view'].includes(m.materialized))return t('Not applicable');
 const properties=m.table_properties;
 if(properties===undefined)return t('Not recorded');
 if(!properties||typeof properties!=='object'||Array.isArray(properties))return t('Not validated');
 const keys=Object.keys(properties);
 return !keys.length?t('Connector default'):keys.length===1&&properties.format==='PARQUET'?'PARQUET':t('Not validated');
}
function dbDbtRenderCompatibility(parent,a){
 const state=dbDbtCompatibility(a),section=dataopsNode('section','db-dbt-compatibility',parent);section.className='db-dbt-compatibility';section.setAttribute('aria-labelledby','db-dbt-compatibility-title');
 dataopsNode('h3','db-dbt-compatibility-title',section,'Compatibility');
 dataopsNode('p','',section,({pending:'Validating settings compatibility',supported:'Supported: SQL / WAP',blocked:'Blocked: unsupported settings',legacy:'Legacy plan: recompile required',invalid:'Compatibility not validated'})[state]);
 if(state==='supported')dataopsNode('p','',workflowDetails(section,'dbt compatibility and context'),a.compatibility.version===2?'SQL / WAP supports run-specific SANDBOX INVOKER VIEWs over TABLE sources and downstream TABLE candidates/tests. TABLE-only publication; VIEW to VIEW is unsupported. UI/API/CLI supports native TABLE execution for one compatible saved model. Native graph runs eligible saved VIEW / TABLE / EPHEMERAL models. Graphs containing VIEW may publish eligible TABLE candidates after complete graph and quality verification. Incremental is unsupported. Quality tests require explicit opt-in. Eligible graph TABLE candidates may be published one at a time after the complete saved quality suite passes.':'SQL / WAP uses compiled table/ephemeral SELECT and tests with error severity, COUNT(*) and zero failures. Views are unsupported by this plan. UI/API/CLI supports native TABLE execution for one compatible saved model. Native graph runs eligible saved VIEW / TABLE / EPHEMERAL models. Graphs containing VIEW may publish eligible TABLE candidates after complete graph and quality verification. Incremental is unsupported. Quality tests require explicit opt-in. Eligible graph TABLE candidates may be published one at a time after the complete saved quality suite passes.');
 if(state==='blocked'){
  const reasons={UNSUPPORTED_VIEW_SECURITY:'Views require INVOKER security',UNSUPPORTED_VIEW_PROPERTIES:'View write properties are unsupported',UNSUPPORTED_VIEW_DEPENDENCY:'VIEW to VIEW dependencies are unsupported',UNSUPPORTED_MATERIALIZATION:'Unsupported materialization',UNSUPPORTED_MODEL_CONFIG:'Unsupported model setting',UNSUPPORTED_TABLE_PROPERTIES:'Only fixed format=PARQUET is supported',UNSUPPORTED_TEST_CONFIG:'Test requires error severity, COUNT(*) and zero failures'};
  const sharedSettings=['pre_hook','post_hook','on_run_start','on_run_end','hooks','grants','persist_docs','sql_header','store_failures','store_failures_as','incremental_strategy','full_refresh','column_types','unique_key','partition_by','cluster_by','table_format','file_format','location','catalog_name','indexes','bind','dist','sort','sql_where','contract'];
  const settings=[...sharedSettings,...sharedSettings.map(s=>'test.'+s),'materialized','properties','view_security','depends_on','on_table_exists','on_schema_change','on_configuration_change','lookback','batch_size','begin','event_time','concurrent_batches','on_error','test.severity','test.fail_calc','test.error_if','test.warn_if','test.limit'];
  const wrap=dataopsNode('div','',section);wrap.className='table-wrap';
  dbDbtTable(wrap,['Node','Setting','Reason'],a.compatibility.issues.map(i=>[
   typeof i?.node_id==='string'&&i.node_id.length<=240&&/^(model|test)\.[a-zA-Z0-9_.-]+$/.test(i.node_id)?i.node_id:t('Project / unknown node'),
   settings.includes(i?.setting)?i.setting:t('Unknown setting'),
   t(Object.hasOwn(reasons,i?.code)?reasons[i.code]:'Unknown compatibility issue')
  ]));
 }
 dataopsNode('p','',section,dbDbtCompatibilityAction(a));
}
function dbDbtCanBuild(){const p=dbDbt,a=p.plan;return !p.standalone&&dbDbtAllowed()&&a?.state==='READY'&&(dbDbtMethod()==='native_graph'||dbDbtCompatibility(a)==='supported')&&a.actor===dbDbtOwner()&&dataopsSameContext(a.context,p.context)&&!p.intent&&!p.run;}
const dbDbtNativeReasons={CONTEXT_MISMATCH:'Only the original owner and saved execution role/context may build this plan.',DBT_PLAN_NOT_READY:'Native requires a READY saved plan. Wait for compile, or recompile a failed plan.',DBT_PLAN_RECOMPILE_REQUIRED:'Native requires a current v2 saved plan. Recompile to validate compatibility.',DBT_PLAN_CHANGED:'Saved plan changed. Inspect and compile a new compatible plan.',STALE_RESOURCE:'Saved source identity changed. Inspect sources and recompile.',DBT_NATIVE_TABLE_REQUIRED:'Native requires a TABLE model. Use a compatible saved plan; materialization is never changed automatically.',DBT_NATIVE_PARQUET_REQUIRED:'Native requires explicit fixed format=PARQUET. Compile a compatible saved plan.',DBT_NATIVE_MATERIALIZED_DEPENDENCY:'Native accepts source and expanded EPHEMERAL dependencies only. TABLE/VIEW dependencies require a compatible saved plan.',DBT_NATIVE_RAW_REQUIRED:'Native used sources must have RAW schema and purpose.',DBT_NATIVE_SELECT_REQUIRED:'Native requires a supported saved SELECT. Compile a compatible plan.'};
function dbDbtOptionsValid(o,a=dbDbt.plan){
 const gate=x=>typeof x?.eligible==='boolean'&&(x.eligible?x.reason_code===null:typeof x.reason_code==='string'&&Object.hasOwn(dbDbtNativeReasons,x.reason_code));
 if(!a||!o||o.version!==1||o.executor!=='native_table'||o.plan_id!==a.id||o.source_digest!==a.source_digest||!gate(o)||!Array.isArray(o.nodes))return false;
 if(!o.eligible)return o.nodes.length===0;
 const ids=(a.models||[]).map(m=>m.unique_id);
 return a.state==='READY'&&a.compatibility?.version===2&&dbDbtCompatibility(a)==='supported'&&o.nodes.length===ids.length&&new Set(o.nodes.map(n=>n.node_id)).size===ids.length&&o.nodes.every(n=>ids.includes(n.node_id)&&gate(n));
}
// Graph eligibility is a saved projection of ALL models and tests, never a node selection.
function dbDbtGraphProjection(a){return {id:a.id,source_digest:a.source_digest,models:a.models.map(m=>({unique_id:m.unique_id,materialized:m.materialized,depends_on:dataopsClone(m.depends_on)})),tests:a.tests.map(x=>({unique_id:x.unique_id}))};}
function dbDbtGraphOptionsValid(o,a=dbDbt.plan){
 const gate=x=>typeof x?.eligible==='boolean'&&(x.eligible?x.reason_code===null:typeof x.reason_code==='string'&&/^(DBT_[A-Z0-9_]+|CONTEXT_MISMATCH|STALE_RESOURCE|INVALID_BINDING|UNSUPPORTED_VIEW_DEPENDENCY)$/.test(x.reason_code));
 const uid=x=>typeof x==='string'&&/^[A-Za-z0-9_.-]{1,512}$/.test(x);
 if(!a||!o||o.version!==1||o.executor!=='native_graph'||o.plan_id!==a.id||typeof o.source_digest!=='string'||!o.source_digest||o.source_digest!==a.source_digest||!gate(o)||!Array.isArray(a.models)||!Array.isArray(a.tests)||!Array.isArray(o.models)||![...a.models,...a.tests,...o.models].every(x=>x&&typeof x==='object'&&!Array.isArray(x))||!Number.isSafeInteger(o.model_count)||!Number.isSafeInteger(o.table_count)||o.model_count!==a.models.length||o.models.length!==a.models.length||o.table_count!==a.models.filter(m=>m.materialized==='table').length||!gate(o.testability)||!Number.isSafeInteger(o.testability.count)||o.testability.count!==a.tests.length||o.testability.eligible&&o.testability.count===0)return false;
 const models=new Map(a.models.map(m=>[m.unique_id,m])),sources=new Set([...(a.source_bindings||[]),...(a.sources||[])].map(x=>typeof x==='string'?x:x?.unique_id));
 if(models.size!==a.models.length||new Set(o.models.map(m=>m.node_id)).size!==models.size||new Set(a.tests.map(x=>x.unique_id)).size!==a.tests.length||!a.tests.every(x=>uid(x.unique_id)))return false;
 for(const n of o.models){const m=models.get(n.node_id);if(!uid(n.node_id)||!m||typeof n.materialized!=='string'||n.materialized!==m.materialized||!Array.isArray(n.depends_on)||!Array.isArray(m.depends_on)||!n.depends_on.every(uid)||new Set(n.depends_on).size!==n.depends_on.length||!dbDbtEqual([...n.depends_on].sort(),[...m.depends_on].sort()))return false;}
 if(!o.eligible)return true;
 if(a.state!=='READY'||a.compatibility?.version!==2||!o.models.some(m=>['view','table'].includes(m.materialized))||!o.models.every(m=>['view','table','ephemeral'].includes(m.materialized))||a.models.some(m=>m.materialized==='view'&&m.view_security!=='INVOKER'))return false;
 const visiting=new Set(),done=new Set();
 function visit(id){if(done.has(id))return true;if(visiting.has(id))return false;const m=models.get(id);if(!m)return sources.has(id);if(m.materialized==='view'&&m.depends_on.some(x=>models.get(x)?.materialized==='view'))return false;visiting.add(id);if(!m.depends_on.every(visit))return false;visiting.delete(id);done.add(id);return true;}
 return [...models.keys()].every(visit);
}
function dbDbtNativeViewValid(m,r){
 const c=m.candidate,ref=c?.ref,n=m.native_receipt;
 return !!c&&Object.keys(c).sort().join(',')==='kind,ref,security,version,view_uuid'&&c.kind==='VIEW'&&c.security==='INVOKER'&&typeof c.view_uuid==='string'&&/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(c.view_uuid)&&Number.isSafeInteger(c.version)&&c.version>0&&!!ref&&Object.keys(ref).sort().join(',')==='database_id,incarnation,object_id,schema_id'&&ref.database_id===r.context.database_id&&ref.schema_id===r.context.schema_id&&typeof ref.object_id==='string'&&!!ref.object_id&&Number.isSafeInteger(ref.incarnation)&&ref.incarnation>0&&n?.executor==='native_graph'&&n.plan_id===r.plan_id&&n.node_id===m.unique_id&&n.phase==='build'&&n.native_verified===true&&dbDbtEqual(n.candidate,c);
}
function dbDbtGraphHasView(r=dbDbt.run,a=dbDbt.plan){return (r?.models||[]).some(m=>m.materialized==='view')||(!r||a?.id===r.plan_id)&&(a?.models||[]).some(m=>m.materialized==='view');}
function dbDbtGraphRunValid(r,a){
 if(!a||r?.executor!=='native_graph'||typeof r.native_tests!=='boolean'||typeof r.native_verified!=='boolean'||typeof r.publication_allowed!=='boolean'||r.selected_node_id!=null||r.plan_id!==a.id||r.source_digest!==a.source_digest||!Array.isArray(r.models)||!Array.isArray(a.models)||!Array.isArray(a.tests)||r.models.length!==a.models.length||!r.models.every(m=>m&&typeof m==='object')||!a.models.every(m=>m&&typeof m.unique_id==='string')||!a.tests.every(x=>x&&typeof x.unique_id==='string'))return false;
 const ids=new Set(r.models.map(m=>m.unique_id));
 if(!r.models.every(m=>['view','table','ephemeral'].includes(m.materialized)&&(!(m.materialized==='view'&&(m.candidate!=null||m.state==='NATIVE_VERIFIED'))||dbDbtNativeViewValid(m,r))))return false;
 if(ids.size!==a.models.length||!a.models.every(m=>r.models.some(n=>n.unique_id===m.unique_id&&n.materialized===m.materialized)))return false;
 if(!Array.isArray(r.tests)||!r.tests.every(x=>x&&typeof x.unique_id==='string')||r.native_tests!==true&&r.tests.length)return false;
 return r.native_tests!==true||r.tests.length===a.tests.length&&new Set(r.tests.map(x=>x.unique_id)).size===a.tests.length&&a.tests.every(x=>r.tests.some(y=>y.unique_id===x.unique_id));
}
const dbDbtGraphReasons={DBT_GRAPH_TABLE_REQUIRED:'Native graph requires a saved VIEW or TABLE model.',DBT_GRAPH_MATERIALIZATION_UNSUPPORTED:'Native graph supports saved INVOKER VIEW, TABLE and EPHEMERAL models. Use SQL / WAP for other supported plans.',UNSUPPORTED_VIEW_DEPENDENCY:'VIEW to VIEW dependencies are unsupported. Use TABLE inputs for a SANDBOX INVOKER VIEW.'};
function dbDbtGraphReason(){const o=dbDbt.graphOptions;if(!dbDbtGraphOptionsValid(o))return 'Inspect or recompile the saved plan to load full graph eligibility.';return o.eligible?'All saved VIEW / TABLE / EPHEMERAL models execute as one dependency graph.':dbDbtGraphReasons[o.reason_code]||dbDbtNativeReasons[o.reason_code]||o.reason_code;}
function dbDbtModelReason(node){
 if(dbDbtMethod()!=='native_graph')return dbDbtNativeReason(node);
 const o=dbDbt.graphOptions;if(!dbDbtGraphOptionsValid(o)||!o.eligible)return dbDbtGraphReason();
 const m=o.models.find(m=>m.node_id===node);
 return m?.materialized==='view'?'Run-scoped SANDBOX INVOKER VIEW; downstream TABLE and tests supported.':m?.materialized==='ephemeral'?'Expanded into graph SQL; no separate SQL operation.':'Graph TABLE candidate; TABLE dependencies supported.';
}
function dbDbtGraphTestability(){return dbDbtGraphOptionsValid(dbDbt.graphOptions)?dbDbt.graphOptions.testability:null;}
function dbDbtGraphQualityReason(){const x=dbDbtGraphTestability();return !x?'Quality test availability is not validated. Inspect or recompile the saved plan.':x.eligible?'Complete saved suite available':x.reason_code==='DBT_NATIVE_TEST_EMPTY_SUITE'?'No quality tests in the saved plan.':dbDbtQualityReasons[x.reason_code]||x.reason_code;}
function dbDbtSelectPlan(a,options,graphOptions){
 const p=dbDbt;p.standalone=null;p.review=null;p.preview=null;if(p.plan?.id!==a.id||p.plan?.source_digest!==a.source_digest){p.node='';p.method='sql_wap';p.nativeTests=false;}
 p.plan=dataopsFreeze(dataopsClone(a));p.runOptions=dbDbtOptionsValid(options,a)?dataopsFreeze(dataopsClone(options)):null;p.graphOptions=dbDbtGraphOptionsValid(graphOptions,a)?dataopsFreeze(dataopsClone(graphOptions)):null;
 if(!p.runOptions?.nodes.some(n=>n.node_id===p.node&&n.eligible))p.node='';
}
function dbDbtMethod(){return dbDbt.intent?.action==='build'?(dbDbt.intent.body.executor||'sql_wap'):dbDbt.method;}
function dbDbtNativeReason(node){
 const p=dbDbt,o=p.runOptions;if(p.pending&&!o)return 'Loading saved-plan compatibility. Execution is blocked while loading.';if(!dbDbtOptionsValid(o))return 'Inspect the saved plan to load native eligibility. Missing or invalid eligibility blocks execution.';
 if(!o.eligible)return dbDbtNativeReasons[o.reason_code];
 if(!o.nodes.length)return 'This saved plan has no models. Compile a compatible saved plan with a TABLE model.';
 if(!node&&!o.nodes.some(n=>n.eligible))return 'No compatible native model in this saved plan. Compile a saved plan with a PARQUET TABLE reading only RAW sources or expanded EPHEMERAL models.';
 if(!node)return 'Choose one eligible saved model. No model is selected automatically.';
 const n=o.nodes.find(n=>n.node_id===node);return n?(n.eligible?'Eligible saved model':dbDbtNativeReasons[n.reason_code]):'Inspect the saved plan to load native eligibility. Missing or invalid eligibility blocks execution.';
}
const dbDbtQualityReasons={
 DBT_NATIVE_TEST_RECOMPILE_REQUIRED:'Recompile this saved plan to validate attached test ownership.',
 DBT_NATIVE_TEST_EMPTY_SUITE:'No attached quality tests for this model.',
 DBT_NATIVE_TEST_MATERIALIZED_DEPENDENCY:'The complete test suite requires another TABLE/VIEW model. Choose a supported suite or SQL / WAP.',
 DBT_NATIVE_TEST_CONFIG_UNSUPPORTED:'The complete attached test suite has unsupported settings.',
 DBT_NATIVE_TEST_SELECT_UNSUPPORTED:'The attached test SQL is unsupported.',
 DBT_NATIVE_TEST_TABLE_REQUIRED:'Quality tests require a TABLE model.',
 DBT_NATIVE_TEST_OWNERS_INVALID:'Attached test ownership is invalid. Recompile the plan.',
 DBT_NATIVE_TEST_OWNERS_CHANGED:'Attached test ownership changed. Recompile the plan.'
};
function dbDbtQuality(){return dbDbt.intent?.action==='build'?dbDbt.intent.body.native_tests===true:dbDbt.nativeTests===true;}
function dbDbtTestability(node=dbDbt.node){
 const p=dbDbt;if(!dbDbtOptionsValid(p.runOptions)||!p.runOptions.eligible)return null;
 const x=p.runOptions.nodes.find(n=>n.node_id===node)?.testability;
 return x&&typeof x.eligible==='boolean'&&Number.isSafeInteger(x.count)&&x.count>=0&&(x.eligible?x.count>0&&x.reason_code===null:typeof x.reason_code==='string'&&/^DBT_NATIVE_TEST_[A-Z_]+$/.test(x.reason_code))?x:null;
}
function dbDbtQualityReason(node){const x=dbDbtTestability(node);return !x?'Quality test availability is not validated. Inspect or recompile the saved plan.':x.eligible?'Complete attached suite available':dbDbtQualityReasons[x.reason_code]||'The complete attached test suite is unavailable. Inspect or recompile the saved plan.';}
function dbDbtCanRun(){const p=dbDbt;return dbDbtCanBuild()&&(dbDbtMethod()==='native_graph'&&dbDbtGraphOptionsValid(p.graphOptions)&&p.graphOptions.eligible&&(!dbDbtQuality()||dbDbtGraphTestability()?.eligible===true)||dbDbtMethod()==='sql_wap'||dbDbtMethod()==='native_table'&&dbDbtOptionsValid(p.runOptions)&&p.runOptions.eligible&&p.runOptions.nodes.some(n=>n.node_id===p.node&&n.eligible)&&(!dbDbtQuality()||dbDbtTestability()?.eligible===true));}
function dbDbtBuildReason(){
 const p=dbDbt,a=p.plan;if(!a)return 'Choose a READY saved plan before running.';
 if(a.actor!==dbDbtOwner()||!dataopsSameContext(a.context,p.context))return 'Only the original owner and saved execution role/context may build this plan.';
 if(dbDbtMethod()==='native_graph')return dbDbtQuality()?dbDbtGraphQualityReason():dbDbtGraphReason();
 if(p.run)return 'Publication requires admin and every planned test to pass. Each model publishes independently.';
 if(dbDbtMethod()==='native_table')return dbDbtQuality()?dbDbtQualityReason(p.node):dbDbtNativeReason(p.node);
 if(dbDbtCompatibility(a)!=='supported')return dbDbtCompatibilityAction(a);
 if(a.state!=='READY')return 'Choose a READY saved plan before running.';
 return 'Publication requires admin and every planned test to pass. Each model publishes independently.';
}
function dbDbtOwnRun(){const p=dbDbt;return dbDbtAllowed()&&p.run?.actor===dbDbtOwner()&&dataopsSameContext(p.run.context,p.context);}
function dbDbtPublicationAssociated(a,run=dbDbt.run){return !!run&&dbDbtArtifact(run,'run',dbDbt.context,run.id)&&dbDbtArtifact(a,'publication',dbDbt.context,a?.id)&&a.run_id===run.id&&a.parent_run_id===run.id&&a.plan_id===run.plan_id&&(!['native_table','native_graph'].includes(run.executor)||a.origin===run.executor)&&run.models?.some(m=>m.unique_id===a.node_id&&(!m.publication_id||m.publication_id===a.id));}
function dbDbtOwnPublication(a){return dbDbtAllowed()&&dbDbtPublicationAssociated(a)&&a.actor===dbDbtOwner()&&dataopsSameContext(a.context,dbDbt.context);}
function dbDbtCanReconcile(){const p=dbDbt;return dbDbtOwnRun()||dbDbtAllowed()&&p.role==='admin'&&Object.values(p.publications).some(dbDbtOwnPublication);}
async function dbDbtRefreshPublications(current){
 const p=dbDbt,run=p.run,context=p.context;
 const ids=new Set(Object.values(p.publications).filter(dbDbtOwnPublication).map(a=>a.id));
 for(const id of ids){const reply=await dbDbtRequest(dbDbtPath('publications',id),context);if(!current()||p.run!==run)return;const a=reply.publication;if(!dbDbtOwnPublication(a)||a.id!==id)throw Error('publication');p.publications[a.node_id]=dataopsFreeze(dataopsClone(a));}
}
function dbDbtTestsPassed(){const p=dbDbt,r=p.run,a=p.plan;if(['native_table','native_graph'].includes(r?.executor)||!r||!a||r.plan_id!==a.id||r.state!=='READY'||r.cancel_requested||!Array.isArray(r.tests)||!Array.isArray(a.tests))return false;const ids=a.tests.map(t=>t.unique_id);return new Set(ids).size===ids.length&&r.tests.length===ids.length&&new Set(r.tests.map(t=>t.unique_id)).size===ids.length&&r.tests.every(t=>ids.includes(t.unique_id)&&t.state==='PASSED'&&t.failure_count===0);}
function dbDbtEqual(a,b){if(a===b)return true;if(!a||!b||typeof a!=='object'||typeof b!=='object'||Array.isArray(a)!==Array.isArray(b))return false;const keys=Object.keys(a);return keys.length===Object.keys(b).length&&keys.every(k=>Object.hasOwn(b,k)&&dbDbtEqual(a[k],b[k]));}
function dbDbtNativePublishable(r=dbDbt.run){
 const a=dbDbt.plan;if(!r||!a||r.plan_id!==a.id||r.source_digest!==a.source_digest||r.executor!=='native_table'||r.state!=='NATIVE_VERIFIED'||r.native_verified!==true||r.native_tests!==true||r.tests_status!=='PASSED'||r.publication_allowed!==true||r.cancel_requested||r.models?.length!==1||r.models[0].unique_id!==r.selected_node_id||!Array.isArray(r.tests)||!r.tests.length)return false;
 // The server proves suite ownership/completeness; dependencies may reference other models' tests.
 const ids=r.tests.map(t=>t.unique_id);
 return ids.every(id=>typeof id==='string'&&!!id)&&new Set(ids).size===ids.length&&r.tests.every(t=>t.state==='PASSED'&&t.failure_count===0);
}
// Publication requires the complete saved graph proof, including source-only tests.
function dbDbtGraphPublishable(r=dbDbt.run){
 const a=dbDbt.plan;
 if(!dbDbtGraphRunValid(r,a)||!r.models.some(m=>m.materialized==='table')||r.state!=='NATIVE_VERIFIED'||r.native_verified!==true||r.native_tests!==true||r.tests_status!=='PASSED'||r.publication_allowed!==true||r.cancel_requested||r.actor!==a.actor||!dataopsSameContext(r.context,a.context)||!r.tests.length||!Array.isArray(r.operations))return false;
 const tasks=r.task_receipts,object=x=>!!x&&typeof x==='object'&&!Array.isArray(x),sameIds=(xs,ids)=>Array.isArray(xs)&&xs.length===ids.length&&new Set(xs).size===ids.length&&xs.every(x=>ids.includes(x));
 const result=(x,status,view=false)=>object(x)&&Object.keys(x).sort().join(',')===(view?'adapter_response,failures,model_type,node_id,status,view_security':'adapter_response,failures,node_id,status')&&(!view||x.model_type==='view'&&x.view_security==='invoker')&&typeof x.node_id==='string'&&x.status===status&&object(x.adapter_response)&&Object.keys(x.adapter_response).every(k=>['code','rows_affected'].includes(k))&&(x.failures===null||Number.isSafeInteger(x.failures)&&x.failures>=0);
 function task(phase,items){
  const q=tasks?.[phase],ids=items.map(x=>x.unique_id),actual=phase==='build'?items.filter(x=>['table','view'].includes(x.materialized)).map(x=>x.unique_id):ids,status=phase==='build'?'success':'pass';
  if(!object(q)||q.task!==(phase==='build'?'RunTask':'TestTask')||q.completed!==true||q.success!==true||!sameIds(q.selected,ids)||!Array.isArray(q.nodes)||!sameIds(q.nodes.map(x=>x?.node_id),ids)||!q.nodes.every(x=>result(x,status,phase==='build'&&items.find(m=>m.unique_id===x.node_id)?.materialized==='view'))||!Array.isArray(q.results)||!sameIds(q.results.map(x=>x?.node_id),actual)||!q.results.every(x=>dbDbtEqual(x,q.nodes.find(y=>y.node_id===x.node_id))))return null;
  return new Map(q.nodes.map(x=>[x.node_id,x]));
 }
 const builds=task('build',a.models),tests=task('test',a.tests);if(!builds||!tests)return false;
 let digest=null;const used=new Set();
 function proof(item,phase,sdk){
  const n=item.native_receipt,id=phase==='build'?item.operation_ids?.[0]:item.operation_id;
  if(typeof id!=='string'||!id||used.has(id)||!object(n)||n.executor!=='native_graph'||n.plan_id!==a.id||n.node_id!==item.unique_id||n.phase!==phase||typeof n.digest!=='string'||!n.digest||digest!==null&&n.digest!==digest||!dbDbtEqual(n.sdk_result,sdk))return false;
  digest=n.digest;used.add(id);
  const ops=r.operations.filter(x=>x?.id===id||x?.phase===phase&&x?.node_id===item.unique_id);
  return ops.length===1&&ops[0].id===id&&ops[0].phase===phase&&ops[0].node_id===item.unique_id&&ops[0].state==='FINISHED'&&ops[0].committed===(phase==='build');
 }
 for(const m of r.models){
  const pm=a.models.find(x=>x.unique_id===m.unique_id),sdk=builds.get(m.unique_id);
  if(!['table','view','ephemeral'].includes(m.materialized)||!dbDbtEqual(m.table_properties,pm.table_properties)||m.depends_on!==undefined&&!dbDbtEqual(m.depends_on,pm.depends_on)||!Array.isArray(m.operation_ids))return false;
  if(m.materialized==='ephemeral'){if(m.state!=='EXPANDED'||m.operation_ids.length||m.candidate!=null||sdk.failures!==null||Object.keys(sdk.adapter_response).length)return false;continue;}
  if(m.materialized==='view'){if(m.view_security!=='INVOKER'||pm.view_security!=='INVOKER'||sdk.failures!==null||m.state!=='NATIVE_VERIFIED'||m.operation_ids.length!==1||!proof(m,'build',sdk)||!dbDbtNativeViewValid(m,r))return false;continue;}
  const c=m.candidate,ref=c?.ref;
  if(m.state!=='NATIVE_VERIFIED'||m.operation_ids.length!==1||!proof(m,'build',sdk)||m.native_receipt.native_verified!==true||!dbDbtEqual(c,m.native_receipt.candidate)||!object(ref)||Object.keys(ref).sort().join(',')!=='database_id,incarnation,object_id,schema_id'||ref.database_id!==r.context.database_id||ref.schema_id!==r.context.schema_id||typeof ref.object_id!=='string'||!ref.object_id||!Number.isSafeInteger(ref.incarnation)||ref.incarnation<1||!([undefined,'TABLE'].includes(c.kind))||['view_uuid','version','security'].some(k=>Object.hasOwn(c,k))||typeof c.snapshot!=='string'||!c.snapshot)return false;
 }
 const passed=r.tests.every(x=>x.state==='PASSED'&&Number.isSafeInteger(x.failure_count)&&x.failure_count===0&&proof(x,'test',tests.get(x.unique_id))&&x.native_receipt.test_verified===true&&x.native_receipt.status==='PASSED'&&Number.isSafeInteger(x.native_receipt.failures)&&x.native_receipt.failures===0&&tests.get(x.unique_id).failures===0);
 return passed&&r.operations.every(op=>['build','test'].includes(op?.phase)&&used.has(op.id))&&r.operations.length===used.size;
}
Object.assign(i18n.ja,{
 'Graph publication unavailable: the complete nonempty saved quality suite must pass.':'グラフ公開不可: 保存済み品質テスト一式が1件以上あり、すべて合格する必要があります。',
 'Graph publication unavailable: the server has no eligible unreserved TABLE candidate.':'グラフ公開不可: サーバーに公開可能な未予約の TABLE 候補がありません。',
 'Graph publication unavailable: complete graph task, model and quality evidence is missing or inconsistent.':'グラフ公開不可: グラフ全体のタスク・モデル・品質証跡が不足しているか一致しません。',
 'Saved graph quality tests passed. Open the original run and plan to check publication conditions.':'保存済みの品質テストは合格済みです。元の実行とプランを開いて公開条件を確認してください。',
 'Graph quality passed. Admin may review each eligible TABLE candidate separately with a fresh row audit >= 1.':'グラフ品質テストは合格済みです。管理者は適格な TABLE 候補を個別に確認し、毎回1行以上の新しい行数監査を行って公開できます。',
 'Whole graph quality: all saved tests PASSED.':'グラフ全体の品質: 保存済みテストはすべて PASSED です。',
 'Only the selected TABLE is published. Dependencies and other TABLEs are not published simultaneously.':'選択した TABLE だけを公開します。依存先や他の TABLE は同時に公開しません。'
});
Object.assign(i18n.ja,{
 'View UUID':'View UUID',
 'Native graph requires a saved VIEW or TABLE model.':'Native graph には保存済み VIEW または TABLE モデルが必要です。',
 'Native graph supports saved INVOKER VIEW, TABLE and EPHEMERAL models. Use SQL / WAP for other supported plans.':'Native graph は保存済み INVOKER VIEW・TABLE・EPHEMERAL に対応します。他の対応プランには SQL / WAP を使用してください。',
 'VIEW to VIEW dependencies are unsupported. Use TABLE inputs for a SANDBOX INVOKER VIEW.':'VIEW 間の依存は未対応です。SANDBOX INVOKER VIEW の入力には TABLE を使用してください。',
 'Run-scoped SANDBOX INVOKER VIEW; downstream TABLE and tests supported.':'実行ごとの SANDBOX INVOKER VIEW。後続 TABLE・テストに対応します。',
 'Select quality tests to run the complete saved suite, including source and unattached tests. Build-only quality is NOT_RUN. After complete graph verification and quality PASSED, admin may review eligible TABLE candidates with a fresh row audit >= 1.':'品質テストを選ぶと、ソース付属・未所属を含む保存済みテスト一式を実行します。構築のみの場合、品質は NOT_RUN です。グラフ全体の検証と品質 PASSED 後、管理者は適格な TABLE 候補を確認し、新しい行数監査 >= 1 を行って公開できます。'
});
function dbDbtGraphPublicationReason(r=dbDbt.run){
 if(r?.native_tests!==true||r.tests_status!=='PASSED')return 'Graph publication unavailable: the complete nonempty saved quality suite must pass.';
 if(r.publication_allowed!==true)return 'Graph publication unavailable: the server has no eligible unreserved TABLE candidate.';
 if(!dbDbt.plan||r.plan_id!==dbDbt.plan.id)return 'Saved graph quality tests passed. Open the original run and plan to check publication conditions.';
 if(!dbDbtGraphPublishable(r))return 'Graph publication unavailable: complete graph task, model and quality evidence is missing or inconsistent.';
 return 'Graph quality passed. Admin may review each eligible TABLE candidate separately with a fresh row audit >= 1.';
}
function dbDbtCanPublish(m){const p=dbDbt,native=['native_table','native_graph'].includes(p.run?.executor);return dbDbtAllowed()&&p.role==='admin'&&p.schemas.some(s=>s.id===p.plan?.target_schema&&s.capabilities?.write===true&&(!native||String(s.purpose||s.sql_name).toUpperCase()==='MARTS'))&&(p.run?.executor==='native_graph'?dbDbtGraphPublishable():native?dbDbtNativePublishable():dbDbtTestsPassed())&&!p.intent&&m?.materialized==='table'&&(native?m.state==='NATIVE_VERIFIED':m.state==='BUILT'&&!!m.build_id)&&m.candidate?.ref?.database_id===p.context.database_id&&m.candidate.snapshot!=null&&!m.publication_id&&!p.publications[m.unique_id];}
function dbDbtViewsValid(views,models,context){
 const expected=(models||[]).filter(m=>m.materialized==='view');
 if(views===undefined)return expected.length===0;
 if(!expected.length||!Array.isArray(views)||views.length!==expected.length||new Set(views.map(v=>v?.node_id)).size!==expected.length)return false;
 return views.every(v=>v&&Object.keys(v).sort().join(',')==='candidate,definition_digest,node_id'&&typeof v.definition_digest==='string'&&/^[a-f0-9]{64}$/.test(v.definition_digest)&&expected.some(m=>m.unique_id===v.node_id&&(!m.candidate||dbDbtEqual(m.candidate,v.candidate)))&&dbDbtNativeViewValid({unique_id:v.node_id,candidate:v.candidate,native_receipt:{executor:'native_graph',plan_id:'summary',node_id:v.node_id,phase:'build',native_verified:true,candidate:v.candidate}},{plan_id:'summary',context}));
}
function dbDbtPreviewValid(v,m){
 const p=dbDbt,r=p.run,a=p.plan,d=v?.expected_target,relation=a?.models?.find(x=>x.unique_id===m?.unique_id)?.relation,target=p.schemas.find(s=>s.id===a?.target_schema);
 if(!dbDbtCanPublish(m)||!v||!dbDbtViewsValid(v.views,r.models,r.context)||v.version!==1||v.origin!==r.executor||v.run_id!==r.id||v.plan_id!==a.id||v.node_id!==m.unique_id||v.source_digest!==r.source_digest||!dataopsSameContext(v.context,p.context)||v.source_actor!==r.actor||v.publisher_actor!==dbDbtOwner()||!dataopsSameContext(v.source_context,r.context)||!dbDbtEqual(v.candidate,m.candidate)||v.quality?.status!=='PASSED'||v.quality.count!==r.tests.length||!Array.isArray(v.quality.test_ids)||!dbDbtEqual([...v.quality.test_ids].sort(),r.tests.map(t=>t.unique_id).sort())||!dbDbtEqual(v.target,{catalog:p.context.sql_catalog,schema:target?.sql_name,name:relation?.[2]})||!d||d.version!==1||Object.keys(d).sort().join(',')!=='destination,target,version')return false;
 return dbDbtTargetValid(d,relation,a.target_schema,p.context);
}
function dbDbtTargetValid(d,relation,targetSchema,context){
 if(!d||d.version!==1||Object.keys(d).sort().join(',')!=='destination,target,version')return false;
 const dest=d.destination,t=d.target;if(!dest||dest.database_id!==context.database_id||dest.schema_id!==targetSchema)return false;
 if(Object.hasOwn(dest,'object_id'))return Object.keys(dest).sort().join(',')==='database_id,incarnation,object_id,schema_id'&&typeof dest.object_id==='string'&&!!dest.object_id&&Number.isSafeInteger(dest.incarnation)&&dest.incarnation>0&&!!t&&Object.keys(t).sort().join(',')==='columns,ref,schema_fingerprint,schema_id,snapshot'&&dbDbtEqual(t.ref,dest)&&typeof t.snapshot==='string'&&/^[0-9]+$/.test(t.snapshot)&&Number.isSafeInteger(t.schema_id)&&t.schema_id>=0&&Array.isArray(t.columns)&&t.columns.length>0&&t.columns.every(c=>c&&Object.keys(c).sort().join(',')==='name,type'&&typeof c.name==='string'&&!!c.name&&(typeof c.type==='string'||c.type&&typeof c.type==='object'&&!Array.isArray(c.type)))&&typeof t.schema_fingerprint==='string'&&/^[a-f0-9]{64}$/.test(t.schema_fingerprint);
 return Object.keys(dest).sort().join(',')==='database_id,physical_name,schema_id'&&dest.physical_name===relation?.[2]&&t===null;
}

async function dbDbtReview(m){
 const p=dbDbt;if(p.pending||!dbDbtCanPublish(m))return;p.review=null;p.preview=null;
 if(!['native_table','native_graph'].includes(p.run.executor)){p.review=m.unique_id;dbDbtRender();return;}
 const current=dbDbtGuard(),run=p.run,plan=p.plan,context=p.context,node=p.node;p.pending=true;dbDbtRender();
 try{const reply=await dbDbtRequest(dbDbtPath('runs',run.id)+'/publication-preview?node_id='+encodeURIComponent(m.unique_id),context);if(!current()||p.run!==run||p.plan!==plan||p.node!==node)return;if(!dbDbtPreviewValid(reply.preview,m))throw Error('preview');p.preview=dataopsFreeze(dataopsClone(reply.preview));p.review=m.unique_id;}
 catch(error){if(current())dbDbtError(error);}finally{if(current()){p.pending=false;dbDbtRender();}}
}
function dbDbtIntentValid(i){return !!i&&typeof i.key==='string'&&!!i.key&&!!i.body&&i.context&&typeof i.path==='string'&&/^\/api\/v1\/dbt\/(plans|runs)(\/[a-zA-Z0-9_-]+\/(cancel|reconcile|publish))?$/.test(i.path)&&['plan','run','publication'].includes(i.kind);}
function dbDbtCanRecover(){const p=dbDbt,i=p.intent;return (!i?.body?.expected_target||i.origin===undefined||['native_table','native_graph'].includes(i.origin))&&dbDbtAllowed()&&dbDbtIntentValid(i)&&!p.intentBlocked&&i.principal===dbPrincipal&&i.header===currentRole&&dataopsSameContext(i.context,p.context);}
function dbDbtCanCloseRejected(){const i=dbDbt.intent;return dbDbtCanRecover()&&(i.action==='compile'&&i.path===dbDbtPath('plans')&&i.rejection==='DBT_PROJECT_NOT_FOUND'||i.action==='publish'&&i.body.expected_target&&['STAGE_CHANGED','DBT_CANDIDATE_CHANGED','DBT_PUBLICATION_REVIEW_CHANGED','DBT_GRAPH_VIEW_PUBLICATION_UNSUPPORTED','DBT_VIEW_CHANGED'].includes(i.rejection));}
function dbDbtCloseRejected(){const p=dbDbt;if(p.pending||!dbDbtCanCloseRejected())return;p.rejectedIntent=p.intent;try{writeStorage(dbDbtKey+'.rejected',p.rejectedIntent);}catch{}p.intent=null;p.intentBlocked=false;p.review=null;p.preview=null;dbDbtRemember();ui(p.status,'Choose an approved project and explicit registered source and target schemas.');dbDbtRender();}
function dbDbtRemember(){try{if(dbDbt.intent)writeStorage(dbDbtKey,dbDbt.intent);else localStorage.removeItem(dbDbtKey);}catch{/* The in-session frozen receipt remains available. */}}
async function dbDbtSubmit(action,nodeId){
 const p=dbDbt;dbDbtRender();if(p.pending||p.intent||!dbDbtAllowed())return;let path,body,kind,id;
 if(action==='compile'){
  if(dbDbtNode('compile').disabled)return;path=dbDbtPath('plans');kind='plan';body={project_id:dbDbtNode('project').value,source_schema:dbDbtNode('source').value,target_schema:dbDbtNode('target').value,staging_materialization:dbDbtNode('materialization').value||null};
 }else if(action==='build'){if(!dbDbtCanRun())return;path=dbDbtPath('runs');kind='run';body={plan_id:p.plan.id};if(['native_table','native_graph'].includes(dbDbtMethod())){body.executor=dbDbtMethod();if(body.executor==='native_table')body.node_id=p.node;if(dbDbtQuality())body.native_tests=true;}}
 else{if(!p.run)return;id=p.run.id;path=dbDbtPath('runs',id)+'/'+action;body={};kind=action==='publish'?'publication':'run';
  if(action==='publish'){const m=p.run.models?.find(m=>m.unique_id===nodeId);if(p.review!==nodeId||!dbDbtCanPublish(m))return;body.node_id=nodeId;if(['native_table','native_graph'].includes(p.run.executor)){if(!dbDbtPreviewValid(p.preview,m))return;body.expected_target=dataopsClone(p.preview.expected_target);}}
  else if(action==='cancel'? !dbDbtOwnRun()||['FAILED','CANCELED','UNKNOWN','CANCELING'].includes(p.run.state)||p.run.cancel_requested : action!=='reconcile'||!dbDbtCanReconcile())return;
 }
 p.intent=dataopsFreeze({path,body:dataopsClone(body),key:crypto.randomUUID(),context:dataopsClone(p.context),principal:dbPrincipal,header:currentRole,kind,id,action,...(action==='build'&&body.executor==='native_graph'?{graphPlan:dbDbtGraphProjection(p.plan)}:{}),...(action==='publish'?{plan_id:p.run.plan_id,...(['native_table','native_graph'].includes(p.run.executor)?{origin:p.run.executor,...(p.preview?.views!==undefined?{views:dataopsClone(p.preview.views)}:{})}:{})}:{}),...(action==='reconcile'?{parent:{actor:p.run.actor,context:dataopsClone(p.run.context)}}:{})});p.intentBlocked=false;dbDbtRemember();await dbDbtRecover(true);
}
async function dbDbtRecover(fresh=false){
 const p=dbDbt,i=p.intent;if(p.pending||!dbDbtCanRecover()||dbDbtCanCloseRejected())return;const current=dbDbtGuard();p.pending=true;ui(p.status,'Request in progress. Inputs and receipt are fixed.');dbDbtRender();
 try{const reply=await dbDbtRequest(i.path,i.context,i.body,i.key);if(!current()||p.intent!==i)return;const a=reply[i.kind];
 // Reconcile acknowledges the selected parent metadata; new receipts belong to this submitter.
 const provenance=i.action==='reconcile'&&i.parent?a?.actor===i.parent.actor&&dataopsSameContext(a?.context,i.parent.context):a?.actor===dbDbtOwner()&&dataopsSameContext(a?.context,i.context);
 const methodMatches=i.action!=='build'||(i.body.executor==='native_graph'?dbDbtGraphRunValid(a,i.graphPlan):i.body.executor==='native_table'?a?.executor==='native_table'&&a.selected_node_id===i.body.node_id:(a?.executor===undefined||a.executor==='sql_wap')&&a.selected_node_id==null);
 const qualityMatches=i.action!=='build'||((a?.native_tests===undefined||typeof a.native_tests==='boolean')&&(a?.native_tests??false)===(i.body.native_tests===true));
 const publicationMatches=i.kind!=='publication'||((i.views===undefined?a?.views===undefined:dbDbtEqual(a?.views,i.views))&&a?.parent_run_id===i.id&&(!i.plan_id||a?.plan_id===i.plan_id)&&(!i.body.expected_target||a?.origin===(i.origin||'native_table')&&dbDbtEqual(a.expected_target,i.body.expected_target)));
 if(!publicationMatches||!qualityMatches||!methodMatches||!provenance||!dbDbtArtifact(a,i.kind,i.context,i.kind==='run'&&i.id?i.id:undefined)||i.body.plan_id&&a.plan_id!==i.body.plan_id||i.kind==='publication'&&(a.run_id!==i.id||a.node_id!==i.body.node_id)||i.kind==='plan'&&(a.project_id!==i.body.project_id||a.source_schema!==i.body.source_schema||a.target_schema!==i.body.target_schema||a.staging_materialization!==i.body.staging_materialization))throw Error('receipt');
 if(i.kind==='plan'){p.method='sql_wap';p.node='';p.nativeTests=false;dbDbtSelectPlan(a,null);p.run=null;p.publications={};}
 if(i.kind==='run'){p.standalone=null;if(i.action==='build')p.publications={};p.run=dataopsFreeze(dataopsClone(a));p.review=null;p.preview=null;}
 if(i.kind==='publication'){p.publications[a.node_id]=dataopsFreeze(dataopsClone(a));p.review=null;p.preview=null;}
 p.intent=null;dbDbtRemember();if(i.action==='reconcile')await dbDbtRefreshPublications(current);if(!current())return;dbDbtURL(true);ui(p.status,i.action==='compile'?'Compile may take a few minutes. It does not execute engine SQL.':i.action==='build'?(i.body.executor==='native_graph'?'Native graph execution may take a few minutes. This view only polls saved state.':i.body.executor==='native_table'?'Native execution may take a few minutes. This view only polls saved state.':'Candidate builds and tests may take a few minutes. This view only polls saved state.'):'Observe saved state');
 }catch(error){if(current()){
 if(p.intent===i&&fresh&&(i.action==='compile'&&i.path===dbDbtPath('plans')&&error.status===404&&error.code==='DBT_PROJECT_NOT_FOUND'||i.action==='publish'&&i.body.expected_target&&error.status===409&&['STAGE_CHANGED','DBT_CANDIDATE_CHANGED','DBT_PUBLICATION_REVIEW_CHANGED','DBT_GRAPH_VIEW_PUBLICATION_UNSUPPORTED','DBT_VIEW_CHANGED'].includes(error.code))){p.intent=dataopsFreeze({...i,rejection:error.code});dbDbtRemember();if(['STAGE_CHANGED','DBT_CANDIDATE_CHANGED'].includes(error.code)){dbDbtError(error);dataopsNode('br','',p.status);dataopsNode('span','',p.status,'Request rejected before acceptance. Close it explicitly to choose again; the original receipt is retained.');}else ui(p.status,'Request rejected before acceptance. Close it explicitly to choose again; the original receipt is retained.');}
 else dbDbtError(error,p.intent===i);
 }}finally{if(current()){p.pending=false;dbDbtRender();dbDbtSchedule();}}
}
function dbDbtStandaloneValid(a,plan){
 const c=dbDbt.context,intent=a?.native_publication?.intent||a?.native_audit?.intent,m=plan?.models?.find(m=>m.unique_id===a?.node_id);
 if(!dbDbtArtifact(a,'publication',c,a?.id)||!dbDbtArtifact(plan,'plan',c,a?.plan_id)||typeof a.run_id!=='string'||!a.run_id||a.run_id!==a.parent_run_id||a.source_digest!==plan.source_digest||m?.materialized!=='table'||!dbDbtViewsValid(a.views,a.origin==='native_graph'?plan.models:[],plan.context))return false;
 if(!['native_graph','native_table'].includes(a.origin))return [undefined,'sql_wap'].includes(a.origin)&&a.views===undefined;
 if(a.context.selected_role!=='admin')return false;
 if(!intent)return !['PUBLISHED','COMMITTED_RECORDING_FAILED'].includes(a.state);
 const candidate=intent.candidate,ref=candidate?.ref;
 if(!candidate||!ref||Object.keys(ref).sort().join(',')!=='database_id,incarnation,object_id,schema_id'||typeof ref.object_id!=='string'||!ref.object_id||!Number.isSafeInteger(ref.incarnation)||ref.incarnation<1||![undefined,'TABLE'].includes(candidate.kind)||['view_uuid','version','security'].some(k=>Object.hasOwn(candidate,k))||!dbDbtTargetValid(a.expected_target,m.relation,plan.target_schema,c))return false;
 const auditIntent=a.native_audit?.intent,publishIntent=a.native_publication?.intent;
 if(auditIntent&&publishIntent&&!dbDbtEqual(auditIntent,publishIntent))return false;
 const sameScope=(x,y)=>!!x&&!!y&&['database_id','schema_id','warehouse_id','sql_catalog','sql_schema','binding_revision','generation_id'].every(k=>x[k]===y[k]);
 return intent.origin===a.origin&&intent.run_id===a.run_id&&intent.plan_id===plan.id&&intent.node_id===m.unique_id&&intent.source_digest===plan.source_digest&&intent.source_actor===plan.actor&&dataopsSameContext(intent.source_context,plan.context)&&intent.publisher_actor===a.actor&&dataopsSameContext(intent.context,a.context)&&a.context.selected_role==='admin'&&sameScope(plan.context,a.context)&&dbDbtEqual(intent.expected_target,a.expected_target)&&intent.candidate?.ref?.database_id===c.database_id&&intent.candidate.ref.schema_id===plan.context.schema_id&&typeof intent.candidate.snapshot==='string'&&!!intent.candidate.snapshot;
}
async function dbDbtObserve(kind,id,expectedPlan,standalone=false){
 const p=dbDbt;if(p.pending||!dbDbtAllowed()||p.intent)return;kind=kind|| (p.standalone?'publication':p.run?'run':'plan');id=id||(kind==='publication'?p.standalone?.id:kind==='run'?p.run?.id:p.plan?.id);if(!id)return;
 const current=dbDbtGuard(),context=p.context;p.runOptions=null;p.graphOptions=null;p.pending=true;dbDbtRender();
 try{const reply=await dbDbtRequest(dbDbtPath(kind==='plan'?'plans':kind==='run'?'runs':'publications',id),context);if(!current())return;const a=reply[kind];if(!dbDbtArtifact(a,kind,context,id))throw Error('artifact');
 if(kind==='run'){
  if(typeof a.plan_id!=='string'||!a.plan_id||expectedPlan&&a.plan_id!==expectedPlan)throw Error('plan');
  let plan=p.plan,options=null,graphOptions=null;if(plan?.id!==a.plan_id){const result=await dbDbtRequest(dbDbtPath('plans',a.plan_id),context);if(!current())return;plan=result.plan;options=result.run_options;graphOptions=result.graph_options;if(!dbDbtArtifact(plan,'plan',context,a.plan_id))throw Error('plan');}
  if(a.actor!==plan.actor||!dataopsSameContext(a.context,plan.context)||a.executor==='native_graph'&&!dbDbtGraphRunValid(a,plan))throw Error('plan owner');if(p.run?.id!==a.id)p.publications={};dbDbtSelectPlan(plan,options,graphOptions);p.run=dataopsFreeze(dataopsClone(a));p.review=null;p.preview=null;
 }else if(kind==='plan'){dbDbtSelectPlan(a,reply.run_options,reply.graph_options);p.run=null;p.publications={};p.review=null;p.preview=null;}
 else if(!standalone&&p.run?.id===a.run_id){if(!dbDbtPublicationAssociated(a))throw Error('publication');p.publications[a.node_id]=dataopsFreeze(dataopsClone(a));}
 else{
  const result=await dbDbtRequest(dbDbtPath('plans',a.plan_id),context);if(!current())return;const plan=result.plan;
  if(!dbDbtStandaloneValid(a,plan))throw Error('publication');
  dbDbtSelectPlan(plan,null,null);p.run=null;p.publications={};p.standalone=dataopsFreeze(dataopsClone(a));p.review=null;p.preview=null;
 }
 dbDbtURL(true);ui(p.status,'Observe saved state');
 }catch(error){if(current())dbDbtError(error);}finally{if(current()){p.pending=false;dbDbtRender();dbDbtSchedule();}}
}
async function dbDbtHistory(){
 const p=dbDbt;if(p.pending||!dbDbtAllowed()||p.intent)return;const current=dbDbtGuard(),context=p.context;p.pending=true;p.historyWarning=null;dbDbtRender();
 try{
  const collections=[['plans','plan','items'],['runs','run','items'],['publications','publication','publications']];
  const results=await Promise.allSettled(collections.map(([path])=>dbDbtRequest(dbDbtPath(path),context)));if(!current())return;
  const authFailure=results.find(result=>result.status==='rejected'&&dataopsAuthError(result.reason));if(authFailure){dbDbtPurge();return;}
  const warnings=[];
  results.forEach((result,index)=>{const [path,kind,key]=collections[index],records=result.status==='fulfilled'?result.value[key]:null;
   if(Array.isArray(records)&&records.every(a=>dbDbtArtifact(a,kind,context))){if(kind==='publication')p.publicationHistory=dataopsFreeze(dataopsClone(records));else p[path]=dataopsFreeze(dataopsClone(records));}
   else{const error=result.status==='rejected'?result.reason:null;warnings.push({kind,code:error?.code||'DBT_HISTORY_INVALID'});}
  });
  p.historyWarning=warnings.length?warnings:null;
  if(warnings.length)ui(p.status,warnings.some(w=>w.kind==='run')?'Run history unavailable. Retry Refresh dbt history; saved publications remain available.':'Observation failed. Saved backend state is unchanged. Retry observation.');
 }catch(error){if(current())dbDbtError(error);}finally{if(current()){p.pending=false;dbDbtRender();}}
}
function dbDbtSchedule(){
 const p=dbDbt;clearTimeout(p.timer);p.timer=null;if(!dbDbtAllowed()||p.pending||p.intent||p.standalone)return;
 const pub=Object.values(p.publications).find(a=>['QUEUED','AUDITING','PUBLISHING','CANCELING'].includes(a.state));
 const a=pub||(['QUEUED','BUILDING','TESTING','CANCELING'].includes(p.run?.state)?p.run:['QUEUED','COMPILING'].includes(p.plan?.state)?p.plan:null);if(!a)return;
 const epoch=p.epoch;p.timer=setTimeout(()=>{if(epoch===p.epoch&&dbDbtAllowed())dbDbtObserve(a.kind,a.id);},1800);p.timer?.unref?.();
}
Object.assign(i18n.ja,{
 'Outcome unknown. Read execution was stopped and abandoned by the operator.':'結果不明。読取り実行は運用者が停止・放棄済み',
 'Recovery operation ID':'復旧操作 ID','Recovery phase':'復旧フェーズ','Query outcome':'クエリ結果',
 'Original query physically fenced. Service health is not confirmed.':'元のクエリは物理的に隔離済みです。サービスの健全性は未確認です。',
 'Original query physically fenced. Replacement healthy; admission reopened.':'元のクエリは物理的に隔離済みです。置換先は健全で、受付は再開済みです。'
});
function dbDbtExecutionRecovery(a){
 const r=a?.execution_recovery;
 if(a?.kind!=='publication'||a.state!=='UNKNOWN'||a.execution_closure!=='FENCED_READ_ONLY'||!r||typeof r!=='object'||Array.isArray(r)||![Object.prototype,null].includes(Object.getPrototypeOf(r)))return null;
 const keys=Reflect.ownKeys(r);if(keys.length!==3||!['operation_id','phase','query_outcome'].every(k=>Object.hasOwn(r,k)))return null;
 return typeof r.operation_id==='string'&&r.operation_id.length===36&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(r.operation_id)&&['CLOSED','OPEN'].includes(r.phase)&&r.query_outcome==='UNKNOWN'?r:null;
}
function dbDbtState(a){const recovery=dbDbtExecutionRecovery(a);if(recovery)return [a.state,t('Outcome unknown. Read execution was stopped and abandoned by the operator.'),t('Recovery operation ID')+': '+recovery.operation_id,t('Recovery phase')+': '+recovery.phase,t('Query outcome')+': '+recovery.query_outcome,t(recovery.phase==='OPEN'?'Original query physically fenced. Replacement healthy; admission reopened.':'Original query physically fenced. Service health is not confirmed.')].join(' · ');if(a?.kind==='run'&&a.executor==='native_graph'){const counts=(a.tests||[]).map(x=>x.failure_count).filter(x=>Number.isSafeInteger(x)&&x>=0);return ['native_graph',a.state,t(a.native_verified===true?'Graph build verified':'Graph build not verified'),t('Quality tests')+': '+(a.native_tests===true?a.tests_status||'UNKNOWN':'NOT_RUN'),t('Test count')+': '+(a.tests?.length||0),...(counts.length?[t('Failure count')+': '+counts.reduce((n,x)=>n+x,0)]:[]),t(dbDbtGraphPublicationReason(a))].join(' · '); }if(a?.kind==='run'&&a.executor==='native_table'){
 const verified=a.native_verified===true||a.state==='NATIVE_VERIFIED';
 if(a.native_tests!==true)return ['native_table',a.selected_node_id||'',a.state,a.state==='NATIVE_VERIFIED'?t('Native TABLE verified; tests not run; publication unavailable'):t('Tests not run; publication unavailable')].join(' · ');
 const status=['QUEUED','RUNNING','PASSED','FAILED','ERROR','CANCELED','UNKNOWN'].includes(a.tests_status)?a.tests_status:'UNKNOWN';
 const counts=(a.tests||[]).map(x=>x.failure_count).filter(x=>Number.isSafeInteger(x)&&x>=0);
 return ['native_table',a.selected_node_id||'',a.state,t(verified?'Native TABLE verified':'Native TABLE not verified'),t('Quality tests')+': '+t(status),...(counts.length?[t('Failure count')+': '+counts.reduce((n,x)=>n+x,0)]:[]),t(a.publication_allowed===true?'Native quality passed. Admin may review explicit MARTS publication with row audit >= 1.':'Publication unavailable')].join(' · ');
 }return a?.state==='COMMITTED_RECORDING_FAILED'?'Publication committed; recording failed. Do not republish.':a?.kind==='run'&&a.state==='READY'?'Tests passed, awaiting publication':a?.state==='PUBLISHED'?'Published':a?.state==='UNKNOWN'?'Backend outcome unknown. Reconcile existing operations; publication is blocked.':a?.state||'';}
function dbDbtTable(parent,columns,rows){const table=dataopsNode('table','',parent),head=dataopsNode('thead','',table),tr=dataopsNode('tr','',head);for(const col of columns)dataopsNode('th','',tr,col).setAttribute('scope','col');const body=dataopsNode('tbody','',table);for(const row of rows){const tr=dataopsNode('tr','',body);for(const cell of row){const td=dataopsNode('td','',tr);if(typeof cell==='function')cell(td);else td.textContent=String(cell??'');}}}
function dbDbtCandidateVersion(m){
 const c=m.candidate;
 if(m.materialized==='ephemeral')return 'N/A';
 if(m.materialized==='view')return t('View UUID')+': '+(c?.view_uuid??t('Not recorded'))+' · VIEW '+(c?.version!=null?'v'+c.version:t('Not recorded'))+(c?.security?' · '+c.security:'');
 return c?.snapshot??t('Not recorded');
}
function dbDbtCandidateDetails(parent,m){
 const detail=dataopsNode('details','',parent);dataopsNode('summary','',detail,'Candidate Ref details');
 dataopsNode('pre','',detail).textContent=JSON.stringify({node_id:m.unique_id,...m.candidate,wap_model_id:m.wap_model_id,wap_version:m.wap_version,build_id:m.build_id,operation_ids:m.operation_ids,depends_on:m.depends_on,dependency_refs:m.dependency_refs,native_receipt:m.native_receipt},null,2);
}
Object.assign(i18n.ja,{'Reviewed SANDBOX views':'確認済み SANDBOX View','Definition digest':'定義 digest','Publication history unavailable. Retry Refresh dbt history.':'公開履歴を取得できません。dbt 履歴を更新してください。','Run history unavailable. Retry Refresh dbt history; saved publications remain available.':'実行履歴を取得できません。dbt 履歴を更新してください。保存済み公開は引き続き確認できます。','Plan history unavailable. Retry Refresh dbt history.':'プラン履歴を取得できません。dbt 履歴を更新してください。','Native proof details':'Native 証跡の詳細','Origin':'発生元','Candidate Snapshot':'候補 Snapshot','Reviewed target Snapshot':'確認済み公開先 Snapshot','Published Snapshot':'公開済み Snapshot','Row audit':'行数監査','Minimum rows':'最小行数','Publication status':'公開状態','Committed':'コミット済み','Test count':'テスト数'});
function dbDbtNativeFacts(parent,v){
 const missing=t('Not recorded'),fact=(label,value)=>{dataopsNode('p','',parent).textContent=t(label)+': '+(value??missing);};
 fact('Target FQN',v.target?[v.target.catalog,v.target.schema,v.target.name].join('.'):null);
 fact('Origin',v.origin);fact('Builder',v.source_actor);fact('Publisher',v.publisher_actor);
 fact('Quality tests',v.quality?v.quality.status+' · '+t('Test count')+': '+v.quality.count:null);
 fact('Candidate Snapshot',v.candidate?.snapshot);
 fact('Reviewed target Snapshot',v.expected_target?.target===null?t('New target (reviewed absent)'):v.expected_target?.target?.snapshot);
 fact('Row audit',(v.audit?.count??missing)+' · '+t('Minimum rows')+': 1');
 if(v.views?.length){const detail=dataopsNode('div','',parent);dataopsNode('p','',detail,'Reviewed SANDBOX views');for(const view of v.views){const name=dbDbt.plan?.models?.find(m=>m.unique_id===view.node_id)?.name;dataopsNode('p','',detail).textContent=(name?name+' · ':'')+view.node_id+' · '+t('View UUID')+': '+view.candidate.view_uuid+' · v'+view.candidate.version+' · '+view.candidate.security;dataopsNode('p','',detail).textContent=t('Definition digest')+': '+view.definition_digest;}}
}
function dbDbtNativeProof(parent,proof){
 const detail=dataopsNode('details','',parent);dataopsNode('summary','',detail,'Native proof details');
 dataopsNode('pre','',detail).textContent=JSON.stringify(proof,null,2);
}
function dbDbtSummary(parent,a,compact=false){
 const p=dbDbt;if(compact)dataopsNode('p','',parent,dbDbtState(a));const identity=compact?workflowDetails(parent,'Saved run details'):parent;dataopsNode('p','',identity).textContent=a.id+' · '+a.actor+' · '+(a.created_at||'');
 dataopsNode('p','',identity,'Saved context');dataopsNode('p','',identity).textContent=[a.context.database_id,a.context.sql_catalog,a.context.schema_id,a.context.sql_schema,a.context.selected_role].join(' / ');
 if(!compact)dataopsNode('p','',parent,dbDbtState(a));if(a.error_code){dataopsNode('p','',parent).textContent=a.error_code;if(['STALE_RESOURCE','DBT_SOURCE_CHANGED','DBT_CANDIDATE_CHANGED','INVALID_BINDING','TARGET_CHANGED','SCHEMA_NOT_FOUND'].includes(a.error_code))dataopsNode('p','',parent,'Source or target identity changed. Refresh context and compile a new plan after inspecting the schemas.');}
 if(a.kind==='publication'&&['native_table','native_graph'].includes(a.origin)){
  const intent=a.native_publication?.intent||a.native_audit?.intent,run=p.run?.id===a.run_id?p.run:null,model=run?.models?.find(m=>m.unique_id===a.node_id),plan=p.plan?.id===a.plan_id?p.plan:null,pm=plan?.models?.find(m=>m.unique_id===a.node_id),schema=p.schemas.find(s=>s.id===plan?.target_schema);
  dbDbtNativeFacts(parent,{origin:a.origin,target:pm&&schema?{catalog:a.context.sql_catalog,schema:schema.sql_name,name:pm.relation?.[2]}:null,source_actor:intent?.source_actor??run?.actor,publisher_actor:intent?.publisher_actor??a.actor,candidate:intent?.candidate??model?.candidate,quality:intent?.quality?{status:intent.quality.length>0&&intent.quality.every(t=>t.status==='PASSED')?'PASSED':t('Not recorded'),count:intent.quality.length}:run?{status:run.tests_status,count:run.tests?.length??0}:null,expected_target:a.expected_target,audit:a.native_audit,views:a.views});
  dataopsNode('p','',parent).textContent=t('Publication status')+': '+a.state+' · '+t('Committed')+': '+(typeof a.committed==='boolean'?String(a.committed):t('Not recorded'));
  dataopsNode('p','',parent).textContent=t('Published Snapshot')+': '+(a.native_publication?.target?.snapshot??t('Not recorded'));
  dbDbtNativeProof(parent,{origin:a.origin,expected_target:a.expected_target,audit_id:a.audit_id,publication_id:a.publication_id,operations:a.operations,target:a.target,committed:a.committed,native_audit:a.native_audit,native_publication:a.native_publication});
 }
 const operations=(a.operations||[]).filter(op=>typeof op.id==='string');
 if(operations.length){
  const detail=dataopsNode('details','',parent),summary=dataopsNode('summary','',detail),states=new Map();
  for(const op of operations)if(op.state)states.set(op.state,(states.get(op.state)||0)+1);
  ui(dataopsNode('span','',summary),'Operation IDs ({count})',{count:operations.length});
  for(const [state,count] of states){dataopsNode('span','',summary).textContent=' · ';dataopsNode('span','',summary,state);dataopsNode('span','',summary).textContent=': '+count;}
  for(const op of operations)dataopsNode('p','',detail).textContent=t('Operation ID')+': '+op.id+(op.state?' · '+op.state:'');
 }
}
function dbDbtRender(){
 const p=dbDbt;if(!p.installed)return;const allowed=dbDbtAllowed(),locked=p.pending||!!p.intent;
 dataopsOptions(dbDbtNode('role'),['bi_viewer','data_engineer','admin'].filter(dbRoleWithinHeader).map(r=>[r,r]),p.role,'Execution role');
 dataopsOptions(dbDbtNode('database'),(p.databases||[]).map(d=>[d.id,dbDatabaseLabel(d)]),p.invalid?'':p.database,'Choose database');
 dataopsOptions(dbDbtNode('project'),(allowed?p.projects:[]).map(x=>[x.id,x.name||x.id]),dbDbtNode('project').value,'Approved project');
 for(const name of ['source','target'])dataopsOptions(dbDbtNode(name),(allowed?p.schemas:[]).filter(s=>s.capabilities?.query===true).map(s=>[s.id,dbSchemaLabel(s)]),allowed?dbDbtNode(name).value:'',name==='source'?'Source Schema':'Target Schema');
 for(const name of ['role','database'])dbDbtNode(name).disabled=p.pending;
 for(const name of ['project','source','target','materialization'])dbDbtNode(name).disabled=locked||!allowed;
 dbDbtNode('refresh').disabled=p.pending;
 dbDbtNode('compile').disabled=locked||!allowed||!p.projects.some(x=>x.id===dbDbtNode('project').value)||!dbDbtNode('source').value||!dbDbtNode('target').value;
 const method=dbDbtMethod(),graph=method==='native_graph',native=method==='native_table';
 for(const name of ['compiled-details','execution-details']){const n=dbDbtNode(name),identity=JSON.stringify([dbPrincipal,allowed,p.context,p.plan?.id,p.plan?.source_digest,name==='execution-details'?method:null]);if(n.dataset.identity!==identity){n.open=false;n.dataset.identity=identity;}}
 dbDbtNode('method').value=method;dbDbtNode('method').disabled=locked||!allowed||!p.plan;
 dbDbtNode('native-controls').hidden=!(native||graph)||!allowed;dbDbtNode('native-help').hidden=!(native||graph)||!allowed;dbDbtNode('native-quality-help').hidden=!(native||graph)||!allowed;
 dbDbtNode('node').parentElement.hidden=!native;
 dbDbtNode('graph-summary').hidden=!graph||!allowed;ui(dbDbtNode('graph-reason'),dbDbtGraphReason());
 const graphCounts=dbDbtNode('graph-counts');graphCounts.textContent=dbDbtGraphOptionsValid(p.graphOptions)?t('Saved graph counts')+': '+p.graphOptions.model_count+' · TABLE: '+p.graphOptions.models.filter(m=>m.materialized==='table').length+' · VIEW: '+p.graphOptions.models.filter(m=>m.materialized==='view').length+' · EPHEMERAL: '+p.graphOptions.models.filter(m=>m.materialized==='ephemeral').length+' · '+t('Test count')+': '+p.graphOptions.testability.count:'';
 const selected=allowed?(p.intent?.action==='build'?p.intent.body.node_id||'':p.node):'';
 const select=dbDbtNode('node');select.replaceChildren();const empty=new Option(t('Choose a target model'),'');ui(empty,'Choose a target model');select.add(empty);
 if(allowed)for(const m of p.plan?.models||[]){const n=dbDbtOptionsValid(p.runOptions)?p.runOptions.nodes.find(n=>n.node_id===m.unique_id):null,option=new Option(m.name+' · '+t(dbDbtNativeReason(m.unique_id)),m.unique_id);option.disabled=!p.runOptions?.eligible||!n?.eligible;select.add(option);}
 if(selected&&p.intent&&!Array.from(select.options).some(o=>o.value===selected)){const frozen=new Option(selected,selected);frozen.disabled=true;select.add(frozen);}
 select.value=selected;select.disabled=locked||!allowed||!native||!dbDbtOptionsValid(p.runOptions)||!p.runOptions.eligible||!p.runOptions.nodes.some(n=>n.eligible);select.setAttribute('aria-describedby','db-dbt-native-reason');
 ui(dbDbtNode('native-reason'),graph?dbDbtGraphReason():dbDbtNativeReason(selected));
 ui(dbDbtNode('native-help'),graph?'VIEWs are run-scoped same-DB SANDBOX INVOKER views over TABLE/RAW inputs, with downstream TABLEs and tests. VIEW to VIEW and formal VIEWs are unsupported. TABLEs create SANDBOX candidates; EPHEMERAL expands into SQL. Failed dependencies leave downstream nodes SKIPPED.':dbDbtQuality()?'Creates one new same-DB SANDBOX candidate and runs the complete attached quality suite. After quality PASSED, admin may explicitly review MARTS publication with row audit >= 1. Execution revalidates current project, policy and sources.':'Creates one new same-DB SANDBOX candidate. Tests are not run; publication is unavailable. Execution revalidates current project, policy and sources.');
 const quality=dbDbtNode('native-tests');quality.checked=allowed&&dbDbtQuality();quality.disabled=locked||!allowed||!(native||graph)||graph&&dbDbtGraphTestability()?.eligible!==true;
 ui(dbDbtNode('native-quality-help'),graph&&dbDbtGraphHasView(null,p.plan)?'Select quality tests to run the complete saved suite, including source and unattached tests. Build-only quality is NOT_RUN. After complete graph verification and quality PASSED, admin may review eligible TABLE candidates with a fresh row audit >= 1.':graph?'Select quality tests to run the complete saved suite, including source and unattached tests. Build proof and quality status are separate. Eligible graph TABLE candidates may be published one at a time after the complete saved quality suite passes.':'Select Run quality tests to run the complete attached suite against the new candidate. Build-only tests are NOT_RUN. Quality PASSED enables explicit admin review for same-DB MARTS publication and row audit >= 1.');
 const testability=graph?dbDbtGraphTestability():dbDbtTestability(selected);ui(dbDbtNode('native-tests-reason'),graph?dbDbtGraphQualityReason():dbDbtQualityReason(selected));if(testability)dbDbtNode('native-tests-reason').textContent+=' · '+t(graph?'Test count':'Attached tests')+': '+testability.count;
 ui(dbDbtNode('build'),graph?(dbDbtQuality()?'Run native graph and test':'Run native graph'):native?(dbDbtQuality()?'Run native TABLE and test':'Run native TABLE'):'Build candidates and test');dbDbtNode('build').disabled=locked||!dbDbtCanRun();
 dbDbtNode('observe').disabled=locked||!allowed||!(p.standalone||p.run||p.plan);
 dbDbtNode('recover').disabled=p.pending||!dbDbtCanRecover()||dbDbtCanCloseRejected();dbDbtNode('recover').hidden=!p.intent;dbDbtNode('close-rejected').hidden=!dbDbtCanCloseRejected();dbDbtNode('close-rejected').disabled=p.pending||!dbDbtCanCloseRejected();
 dbDbtNode('cancel').disabled=locked||!dbDbtOwnRun()||p.run.cancel_requested||['CANCELED','FAILED','UNKNOWN','CANCELING'].includes(p.run.state);
 dbDbtNode('reconcile').disabled=locked||!dbDbtCanReconcile();dbDbtNode('history-refresh').disabled=locked||!allowed;
 ui(dbDbtNode('reason'),!allowed?(p.role==='bi_viewer'||!dbRoleWithinHeader('data_engineer')?'Viewer access is unavailable. Choose an authorized data_engineer or admin role; source SQL is hidden.':'Refresh an authorized Database and SANDBOX context.'):p.intentBlocked?'Saved context differs. Reselect the original context; requests are never retargeted.':['native_table','native_graph'].includes(p.run?.executor)?dbDbtState(p.run):dbDbtBuildReason());
 dbDbtNode('run-effect').textContent=allowed&&p.plan?[t('Run effect'),p.context?.sql_catalog,p.plan.project_name||p.plan.project_id,t('Candidate Schema: SANDBOX (fixed)'),graph?t('Native graph (VIEW / TABLE / EPHEMERAL)'):native?p.plan.models?.find(m=>m.unique_id===selected)?.name:t('SQL / WAP'),method==='sql_wap'||dbDbtQuality()?t('Run quality tests'):'NOT_RUN',t(method!=='sql_wap'&&!dbDbtQuality()?'Build-only: publication unavailable.':'Creates SANDBOX candidates; publication requires separate review.')].filter(Boolean).join(' · '):'';
 const walk=n=>Array.from(n.children||[]).flatMap(c=>[c,...walk(c)]),retained=[];
 const keyed=node=>{const counts=new Map();return new Map(walk(node).filter(n=>['DETAILS','SUMMARY','BUTTON'].includes(n.tagName)).map(n=>{
  const label=n.tagName==='DETAILS'?n.children[0]:n,base=n.tagName+':'+(n.id||uiBindings.get(label)?.key||uiBindings.get(label?.children[0])?.key||label?.textContent||''),index=counts.get(base)||0;counts.set(base,index+1);return [base+':'+index,n];}));};
 for(const name of ['receipt','plan-detail','models','sql','run-detail','candidates','tests','publications','confirmation','history']){
  const node=dbDbtNode(name),identity=JSON.stringify([dbPrincipal,allowed,p.context,p.plan?.id,p.plan?.source_digest,...(['plan-detail','models','sql'].includes(name)?[]:[p.run?.id,p.standalone?.id,(p.run?.models||[]).map(m=>m.unique_id),Object.values(p.publications).map(a=>a.id),p.review,p.intent?.key||p.rejectedIntent?.key])]);
  const old=node.dataset.identity===identity?keyed(node):new Map();
  retained.push({node,old,focused:[...old].find(([,n])=>n===document.activeElement)?.[0]});node.dataset.identity=identity;node.replaceChildren();
 }
 try{
 const receipt=dbDbtNode('receipt');receipt.replaceChildren();const saved=p.intent||p.rejectedIntent;if(allowed&&saved?.principal===dbPrincipal){dataopsNode('p','',receipt).textContent=[t('Request key'),saved.key,saved.context.database_id,saved.context.sql_schema,saved.context.selected_role].join(' · ');if(saved.action==='build'&&saved.body.executor==='native_graph'){dataopsNode('p','',receipt).textContent=['native_graph',saved.body.plan_id,t('Quality tests')+': '+(saved.body.native_tests===true),t('Saved graph counts')+': '+(saved.graphPlan?.models?.length??''),t('Test count')+': '+(saved.graphPlan?.tests?.length??'')].join(' · ');}if(saved.rejection){const detail=dataopsNode('details','',receipt);dataopsNode('summary','',detail,'Rejected request receipt');dataopsNode('pre','',detail).textContent=JSON.stringify(saved,null,2);}}
 if(!allowed)return;
 const a=p.plan,r=p.run;
 if(a){dataopsNode('p','',dbDbtNode('plan-detail'),dbDbtState(a));if(a.error_code){dataopsNode('p','',dbDbtNode('plan-detail')).textContent=a.error_code;if(['STALE_RESOURCE','DBT_SOURCE_CHANGED','DBT_CANDIDATE_CHANGED','INVALID_BINDING','TARGET_CHANGED','SCHEMA_NOT_FOUND'].includes(a.error_code))dataopsNode('p','',dbDbtNode('plan-detail'),'Source or target identity changed. Refresh context and compile a new plan after inspecting the schemas.');}const savedPlan=workflowDetails(dbDbtNode('plan-detail'),'Saved plan details');dbDbtSummary(savedPlan,a);dbDbtRenderCompatibility(dbDbtNode('plan-detail'),a);dataopsNode('p','',savedPlan,'Project version / source digest');dataopsNode('p','',savedPlan).textContent=[a.project_name||a.project_id,a.project_version||JSON.stringify(a.compiler_versions||{}),a.source_digest||'',t('Staging materialization')+': '+(a.staging_materialization||t('Project default (no override)')),t('Source Schema')+': '+a.source_schema,t('Target Schema')+': '+a.target_schema].join(' · ');
 dbDbtTable(dbDbtNode('models'),['Model','Materialization','Applied table format','Dependencies',graph?'Native graph compatibility':'Native saved-plan compatibility'],(a.models||[]).map(m=>[m.name,m.materialized==='ephemeral'?t('Ephemeral, expanded into SQL'):m.materialized==='table'?t('Table candidate'):m.materialized==='view'?t('Sandbox INVOKER view'):m.materialized,dbDbtFormat(m),JSON.stringify(m.depends_on||[]),t(dbDbtModelReason(m.unique_id))]));
 for(const m of a.models||[]){const detail=dataopsNode('details','',dbDbtNode('sql'));dataopsNode('summary','',detail).textContent=m.unique_id+' · '+JSON.stringify(m.relation);dataopsNode('pre','',detail).textContent=m.compiled_sql||'';dataopsNode('p','',detail,'Dependencies');dataopsNode('pre','',detail).textContent=JSON.stringify(m.depends_on||[],null,2);}
 const sources=dataopsNode('details','',dbDbtNode('sql'));dataopsNode('summary','',sources,'Sources / refs');dataopsNode('pre','',sources).textContent=JSON.stringify({sources:a.sources||[],source_bindings:a.source_bindings||[],refs:a.refs||[]},null,2);const suite=dataopsNode('details','',dbDbtNode('sql'));dataopsNode('summary','',suite,'Tests and failure counts');dataopsNode('pre','',suite).textContent=JSON.stringify((a.tests||[]).map(x=>({unique_id:x.unique_id,name:x.name,test_type:x.test_type,depends_on:x.depends_on})),null,2);
 }else dataopsNode('p','',dbDbtNode('plan-detail'),'Choose a saved plan or compile an approved project.');
 if(r){dbDbtSummary(dbDbtNode('run-detail'),r,true);if(!locked&&['READY','NATIVE_VERIFIED','FAILED','CANCELED','PUBLISHED'].includes(r.state)){dataopsNode('p','',dbDbtNode('run-detail'),'Inspect the saved plan again to prepare a different explicit run.');dataopsButton(dbDbtNode('run-detail'),'','Inspect plan',()=>dbDbtObserve('plan',r.plan_id));}else if(r.state==='UNKNOWN'&&['native_table','native_graph'].includes(r.executor))dataopsNode('p','',dbDbtNode('run-detail'),'Backend outcome unknown. Reconcile existing operations; publication is blocked.');dbDbtTable(dbDbtNode('candidates'),['Model','Status','Applied table format','TABLE snapshot / VIEW version',...(r.executor==='native_graph'?['Dependencies','Operation ID']:[]),'Candidate Ref details'],(r.models||[]).map(m=>[m.name,m.state,dbDbtFormat(m),dbDbtCandidateVersion(m),...(r.executor==='native_graph'?[(m.depends_on||a?.models?.find(x=>x.unique_id===m.unique_id)?.depends_on||[]).join(', '),(m.operation_ids||[]).join(', ')]:[]),parent=>dbDbtCandidateDetails(parent,m)]));
 dbDbtTable(dbDbtNode('tests'),['Model','Status','Failure count',...(r.native_tests===true?['Operation ID']:[])],(r.tests||[]).map(x=>[x.unique_id+' · '+x.name,x.state,x.failure_count??'',...(r.native_tests===true?[x.operation_id||'']:[])]));
 if(r.executor==='native_graph'){dataopsNode('p','',dbDbtNode('publications'),dbDbtGraphPublicationReason(r));dbDbtNativeProof(dbDbtNode('run-detail'),r);}
 for(const m of r.models||[]){if(m.materialized!=='table')continue;const row=dataopsNode('div','',dbDbtNode('publications'));dataopsNode('p','',row).textContent=m.name;
 const pub=p.publications[m.unique_id];if(pub)dbDbtSummary(row,pub);
 const button=dataopsButton(row,'','Review publication',()=>dbDbtReview(m));button.disabled=locked||!dbDbtCanPublish(m);
 if(pub||m.publication_id)dataopsButton(row,'','Observe saved state',()=>dbDbtObserve('publication',pub?.id||m.publication_id)).disabled=locked;
 if(p.review===m.unique_id&&dbDbtCanPublish(m)&&(!['native_table','native_graph'].includes(r.executor)||dbDbtPreviewValid(p.preview,m))){
  const target=p.schemas.find(s=>s.id===a?.target_schema),pm=a?.models?.find(x=>x.unique_id===m.unique_id),name=Array.isArray(pm?.relation)?pm.relation[2]:null;
  const review=dbDbtNode('confirmation');review.className='db-dbt-confirm';dataopsNode('h3','',review,'Review publication');
  if(['native_table','native_graph'].includes(r.executor)){const v=p.preview;dbDbtNativeFacts(review,v);if(r.executor==='native_graph'){dataopsNode('p','',review,'Whole graph quality: all saved tests PASSED.');dataopsNode('p','',review,'Only the selected TABLE is published. Dependencies and other TABLEs are not published simultaneously.');}dataopsNode('p','',review,'Native publication requires admin confirmation, quality PASSED and a fresh row audit >= 1. Same-DB MARTS only.');dataopsNode('p','',review,v.expected_target.target===null?'New target (reviewed absent)':'Replace reviewed target');dbDbtNativeProof(review,v);}
  else{dataopsNode('p','',review,'Target FQN');dataopsNode('p','',review).textContent=target&&name?[a.context.sql_catalog,target.sql_name,name].join('.'):'Unavailable';dataopsNode('pre','',workflowDetails(review,'Candidate Ref details')).textContent=JSON.stringify({node_id:m.unique_id,candidate:m.candidate,wap_version:m.wap_version},null,2);dataopsNode('p','',review,'Publication requires admin and every planned test to pass. Each model publishes independently.');}
  const publish=dataopsButton(review,'db-dbt-publish','Audit and publish',()=>dbDbtSubmit('publish',m.unique_id));publish.className='primary';publish.disabled=locked||!target||!name;dataopsButton(review,'','Close publication review',()=>{p.review=null;p.preview=null;dbDbtRender();});
 }
 }
 }else dataopsNode('p','',dbDbtNode('run-detail'),'Choose a saved run or build a ready plan.');
 if(p.standalone){dbDbtSummary(dbDbtNode('publications'),p.standalone);dataopsButton(dbDbtNode('publications'),'','Inspect plan',()=>dbDbtObserve('plan',p.standalone.plan_id)).disabled=locked;}
 const history=dbDbtNode('history');for(const warning of p.historyWarning||[]){const label={run:'Run history unavailable. Retry Refresh dbt history; saved publications remain available.',plan:'Plan history unavailable. Retry Refresh dbt history.',publication:'Publication history unavailable. Retry Refresh dbt history.'}[warning.kind];dataopsNode('p','',history).textContent=t(label)+' '+warning.code;}if(!p.historyWarning&&!p.plans.length&&!p.runs.length&&!p.publicationHistory.length)dataopsNode('p','',history,'No saved records yet. Compile a project to create a plan.');
 for(const [kind,records] of [['plan',p.plans],['run',p.runs],['publication',p.publicationHistory]])for(const x of records){const row=dataopsNode('div','',history);dataopsNode('p','',row).textContent=[x.id,x.context.database_id,x.context.sql_catalog,x.context.sql_schema,x.context.selected_role,x.created_at||'',t(dbDbtState(x))].join(' · ');dataopsButton(row,'',kind==='plan'?'Inspect plan':kind==='publication'?'Observe saved state':'Inspect run',()=>dbDbtObserve(kind,x.id,undefined,kind==='publication')).disabled=locked;}
 }finally{
  for(const {node,old,focused} of retained){const next=keyed(node);for(const [key,n] of next)if(n.tagName==='DETAILS'&&old.has(key))n.open=old.get(key).open;
   if(focused&&next.has(focused)&&!next.get(focused).disabled)next.get(focused).focus();}
 }
}
function dbDbtURL(write=false){
 if(!globalThis.location)return {};const url=new URL(globalThis.location.href),hash=url.hash.slice(1),split=hash.indexOf('?'),prefix=split>=0?hash.slice(0,split):hash.includes('=')?'':hash,params=new URLSearchParams(split>=0?hash.slice(split+1):prefix?'':hash);
 const selection={database:params.get('dbt_db'),plan:params.get('dbt_plan'),run:params.get('dbt_run'),publication:params.get('dbt_publication')};
 if(write&&(!prefix||prefix==='transformation')&&globalThis.history?.replaceState){for(const k of ['dbt_db','dbt_plan','dbt_run','dbt_publication'])params.delete(k);params.set('dbt_db',dbDbt.database);if(dbDbt.plan)params.set('dbt_plan',dbDbt.plan.id);if(dbDbt.run)params.set('dbt_run',dbDbt.run.id);if(dbDbt.standalone)params.set('dbt_publication',dbDbt.standalone.id);url.hash=(prefix||'transformation')+'?'+params.toString();globalThis.history.replaceState(null,'',url);}
 return selection;
}
async function dbDbtRestore(){const navigation=dataopsRecoveryRevision,link=dbDbtURL();if(!link.database||![link.plan,link.run,link.publication].some(Boolean)||(link.publication?[link.database,link.publication]:[link.database,link.plan,link.run]).filter(Boolean).some(x=>! /^[a-zA-Z0-9_-]{1,160}$/.test(x)))return;const dbtHost=$('transformation-dbt');if(dbtHost)dbtHost.open=true;dbDbt.database=link.database;await dbDbtRefresh();if(navigation!==dataopsRecoveryRevision||!dbDbtAllowed())return;await dbDbtObserve(link.publication?'publication':link.run?'run':'plan',link.publication||link.run||link.plan,link.publication?undefined:link.run?link.plan:undefined,!!link.publication);}
function dbDbtInstall(){
 Object.assign(i18n.ja,{'Saved plan details':'保存プランの詳細','Execution details':'実行方式の詳細','Compiled model details':'コンパイル済みモデルの詳細','Run effect':'実行対象と影響','Build-only: publication unavailable.':'構築のみ。公開はできません。','Creates SANDBOX candidates; publication requires separate review.':'SANDBOX に候補を作成します。公開には別途確認が必要です。'});
 const p=dbDbt;if(p.installed)return;p.installed=true;p.principal=dbPrincipal;p.header=currentRole;p.maximum=dbMaximumRole;p.role=currentRole;p.database=dbCurrent()?.database_id||'';p.schema='';p.schemas=[];p.databases=dbRoleItems(p.role);
 const host=workflowDetails(workbenchAtlasHost('transformation-dbt'),'dbt plan');host.id='transformation-dbt';const dbtLink=dbDbtURL();host.open=!!(dbtLink.database&&(dbtLink.plan||dbtLink.run||dbtLink.publication));
 const root=dataopsNode('section','db-dbt-panel',host);root.className='panel';root.setAttribute('aria-labelledby','db-dbt-title');const body=dataopsNode('div','',root);body.className='panel-body';dataopsNode('h2','db-dbt-title',body,'Database dbt');
 dataopsNode('p','',body,'Choose a database and refresh context, then compile an approved project or inspect a saved plan.').className='help';
 const compatibility=workflowDetails(body,'dbt compatibility and context');
 for(const copy of ['SQL / WAP supports compiled table/ephemeral SELECT and run-specific SANDBOX INVOKER VIEWs. Publication is TABLE-only. Only fixed format=PARQUET table properties are supported. UI/API/CLI supports native TABLE execution for one compatible saved model. Native graph runs eligible saved VIEW / TABLE / EPHEMERAL models. Graphs containing VIEW may publish eligible TABLE candidates after complete graph and quality verification. Incremental is unsupported. Quality tests require explicit opt-in. Eligible graph TABLE candidates may be published one at a time after the complete saved quality suite passes.','Context is independent of Worksheets. History and deep links only read saved records.','Legacy dbt build/test is unavailable in Database mode.'])dataopsNode('p','',compatibility,copy).className='help';
 const context=dataopsNode('div','',body);context.className='toolbar transformation-context';
 for(const [name,label] of [['role','Execution role'],['database','Database']])dataopsField(context,'db-dbt-'+name,label,'select').onchange=()=>{const value=dbDbtNode(name).value;dbDbtPurge();p[name]=value;return dbDbtRefresh();};
 dataopsNode('p','',context,'Candidate Schema: SANDBOX (fixed)');dataopsButton(context,'db-dbt-refresh','Refresh dbt context',dbDbtRefresh);
 const inputs=dataopsNode('div','',body);inputs.className='toolbar transformation-inputs';for(const [name,label] of [['project','Approved project'],['source','Source Schema'],['target','Target Schema']])dataopsField(inputs,'db-dbt-'+name,label,'select').onchange=()=>{p.nativeTests=false;dbDbtRender();};
 dataopsField(inputs,'db-dbt-materialization','Staging materialization','select',[['','Project default (no override)'],['ephemeral','Explicit ephemeral override'],['table','Explicit table override']]).onchange=()=>{p.nativeTests=false;dbDbtRender();};
 dataopsNode('p','',body,'Project and schema inputs apply to the next compile. Build and publish use the selected saved artifacts.').className='help';
 dataopsNode('p','',compatibility,'Project defaults to views are supported as run-specific SANDBOX INVOKER VIEWs. Only TABLE candidates can be published.').className='help';
 const saved=dataopsNode('div','',body);saved.className='toolbar transformation-saved';saved.setAttribute('role','group');saved.setAttribute('aria-labelledby','db-dbt-run-label');dataopsNode('span','db-dbt-run-label',saved,'Run saved plan');
 dataopsField(saved,'db-dbt-method','Execution method','select',[['sql_wap','SQL / WAP'],['native_table','Native TABLE'],['native_graph','Native graph (VIEW / TABLE / EPHEMERAL)']]).onchange=()=>{if(p.pending||p.intent)return;const value=dbDbtNode('method').value;if(!['sql_wap','native_table','native_graph'].includes(value))return;p.review=null;p.preview=null;p.method=value;p.node='';p.nativeTests=false;dbDbtRender();};
 const graph=dataopsNode('div','db-dbt-graph-summary',body);dataopsNode('p','db-dbt-graph-reason',graph).setAttribute('aria-live','polite');dataopsNode('p','db-dbt-graph-counts',graph);
 const native=dataopsNode('div','db-dbt-native-controls',saved);native.className='toolbar';
 dataopsField(native,'db-dbt-node','Target model','select').onchange=()=>{if(p.pending||p.intent)return;p.review=null;p.preview=null;p.node=dbDbtNode('node').value;dbDbtRender();};
 const qualityLabel=dataopsNode('label','',native);const quality=dataopsNode('input','db-dbt-native-tests',qualityLabel);quality.type='checkbox';quality.setAttribute('aria-describedby','db-dbt-native-tests-reason');dataopsNode('span','',qualityLabel,'Run quality tests');quality.onchange=()=>{if(p.pending||p.intent)return;p.nativeTests=quality.checked===true;dbDbtRender();};
 dataopsNode('p','db-dbt-native-tests-reason',native).setAttribute('aria-live','polite');
 dataopsNode('p','db-dbt-native-reason',native).setAttribute('aria-live','polite');
 const executionDetails=workflowDetails(body,'Execution details');executionDetails.id='db-dbt-execution-details';
 dataopsNode('p','db-dbt-native-quality-help',executionDetails,'Select Run quality tests to run the complete attached suite against the new candidate. Build-only tests are NOT_RUN. Quality PASSED enables explicit admin review for same-DB MARTS publication and row audit >= 1.').className='help';
 dataopsNode('p','db-dbt-native-help',executionDetails,'Creates one new same-DB SANDBOX candidate. Tests are not run; publication is unavailable. Execution revalidates current project, policy and sources.').className='help';
 dataopsNode('p','db-dbt-run-effect',body);
 const actions=dataopsNode('div','',body);actions.className='toolbar';for(const [name,label,fn] of [['compile','Compile project',()=>dbDbtSubmit('compile')],['build','Build candidates and test',()=>dbDbtSubmit('build')],['observe','Observe saved state',()=>dbDbtObserve()],['recover','Recover original receipt',()=>dbDbtRecover()],['close-rejected','Close rejected request and choose again',dbDbtCloseRejected],['cancel','Cancel saved run',()=>dbDbtSubmit('cancel')],['reconcile','Reconcile existing operations',()=>dbDbtSubmit('reconcile')]])dataopsButton(actions,'db-dbt-'+name,label,fn);
 p.status=dataopsNode('p','db-dbt-status',body);p.status.className='status-line';p.status.setAttribute('role','status');p.status.setAttribute('aria-live','polite');dataopsNode('p','db-dbt-reason',body);dataopsNode('p','db-dbt-receipt',body);
 let compiled;
 for(const [name,label,tag] of [['plan-detail','Saved plans','div'],['models','Compiled models','div'],['sql','Model SQL and lineage','div'],['run-detail','Saved runs','div'],['candidates','Candidate models','div'],['tests','Tests and failure counts','div'],['publications','Publication','div'],['confirmation',null,'div']]){if(name==='models'){compiled=workflowDetails(body,'Compiled model details');compiled.id='db-dbt-compiled-details';}const parent=['models','sql'].includes(name)?compiled:body;if(label)dataopsNode('h3','',parent,label);const n=dataopsNode(tag,'db-dbt-'+name,parent);if(['models','candidates','tests'].includes(name))n.className='table-wrap';}
 dataopsButton(body,'db-dbt-history-refresh','Refresh dbt history',dbDbtHistory);dataopsNode('div','db-dbt-history',body);dbDbtNode('compile').className='primary';dbDbtNode('build').className='run';dbDbtNode('cancel').className='stop';dbDbtRender();
}
