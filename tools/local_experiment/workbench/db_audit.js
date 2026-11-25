'use strict';
// Durable audit is an independent reference context, never an execution context.
const dbAudit={installed:false,scope:'self',role:'',database:'',epoch:0,list:0,selection:0,detailRequest:0,
 draft:{},applied:null,cursors:[],cursor:'',next:null,items:[],selected:'',detail:null,proof:null,choiceItems:null,capabilities:null,url:null,autoUsed:false};
Object.assign(i18n.ja,{
 'Saved AI metadata':'AI の保存済みメタデータ','Saved SQL explanation':'保存済み SQL の説明','Saved SQL execution plan explanation':'保存済み SQL 実行計画の説明','Saved-plan re-explanation':'保存済み計画の再説明','Saved SQL proposal':'保存済み SQL 案','Saved catalog description proposal':'保存済みカタログ説明文案',
 'Saved SQL queries':'保存済み SQL クエリ','DataOps operations':'DataOps 操作','Job runs':'ジョブ実行','Job steps':'ジョブの処理',
 'Stage runs':'ステージ実行（取込・出力）','Stage scans':'ステージ確認','Managed Mart runs':'Managed Marts の実行','Database dbt runs':'Database dbt 実行',
 'Saved schedules':'保存済みスケジュール','Schedule ticks':'スケジュールの予定消費','Saved alerts':'保存済みアラート','WAP model versions':'データ変換のモデル版',
 'WAP builds':'データ変換のビルド','WAP audits':'データ変換の監査','WAP publications':'データ変換の公開','Database creation records':'データベース作成記録',
 'Database requests':'データベース要求','Logical resource requests':'論理リソース要求','Management operations':'管理操作',
 'History guide':'履歴の見方','Source presets':'記録元のプリセット',
 'Choose only a source draft, then Search to apply. Availability follows this reference context; records may be absent.':'記録元の下書きだけを選び、検索で適用します。利用可否はこの参照コンテキストに従います。記録がない場合もあります。',
 'Runtime SQL queries → saved SQL queries (query). Worksheet Recent Query History restores SQL/context only; it does not execute SQL or retrieve result rows.':'runtime SQL queries → 保存済み SQL クエリ（query）。Worksheet の Recent Query History は SQL とコンテキストだけを復元します。SQL の実行や結果行の取得は行いません。',
 'lake.ingest → DataOps manual ingestion; Ingestion → Stages → ingestion/export records (stage_run) and scan history (stage_scan) for registered Stages. lake.export → DataOps table export. Worksheet result CSV/JSON/TSV downloads belong to the original query, not a new DataOps write operation.':'lake.ingest → DataOps の手動取込。登録済み Stages は Ingestion → Stages → 取込・出力記録（stage_run）と確認履歴（stage_scan）。lake.export → DataOps のテーブル出力。Worksheet の結果 CSV/JSON/TSV ダウンロードは元クエリに属し、新しい DataOps 書込操作ではありません。',
 'catalog.clone → DataOps clone operation. catalog.drop → DataOps catalog unregister with data kept; no physical deletion or automatic Undrop.':'catalog.clone → DataOps の複製操作。catalog.drop → データを保持する DataOps のカタログ登録解除。物理削除や自動 Undrop ではありません。',
 'bear.data_engineer → Saved AI metadata (ai), then explicit Manual WAP → model versions / builds / audits / publications and DataOps. Refresh history: Managed Marts → runs. This is not arbitrary snapshot rollback. Other paths: Jobs → run / step history; Database dbt → runs; Jobs / Stages / Managed Marts → schedules and ticks.':'bear.data_engineer → 保存済み AI メタデータ（ai）、続いて明示的な Manual WAP → モデルの版／ビルド／監査／公開と DataOps。更新履歴は Managed Marts → 実行へ。任意 snapshot へのロールバックとは異なります。その他はジョブ → 実行／処理履歴、Database dbt → 実行、ジョブ／ステージ／公開済み MARTS → スケジュールと予定消費。',
 'Saved QUEUED/RUNNING is not current health. Committed and cleanup are separate. Old raw engine last 50 and Pawprints historical samples (at most 100) are not imported into DB audit.':'保存済み QUEUED/RUNNING は現在の稼働状態を保証しません。コミットと清掃は別です。旧 engine の直近50件と Pawprints の過去サンプル（最大100件）は DB 監査に取り込みません。',
 'Saved metadata audit':'保存済みメタデータの監査','Scope':'参照範囲','My DB records':'自分の DB 記録','DB administration':'DB 管理情報','Platform management':'基盤管理記録',
 'Reference role':'参照ロール','Start UTC':'開始 UTC','End UTC':'終了 UTC','Source':'発生元','State':'状態','Search':'検索','Page size':'取得件数','Search audit logs':'監査ログを検索',
 'All sources':'すべて','Filters pending. Search to apply.':'条件は未適用です。検索で適用します。',
 'Search to load saved metadata.':'検索して保存済みメタデータを取得してください。','No saved metadata matches these filters.':'この条件に一致する保存済みメタデータはありません。',
 'Audit unavailable. Check the reference role and database, then search again.':'監査を利用できません。参照ロールと DB を確認して再検索してください。',
 'Cursor expired. Search again.':'ページの参照期限が切れました。再検索してください。',
 'Choose a currently available database and permitted reference role.':'現在利用可能な DB と許可された参照ロールを選択してください。',
 'DB administration requires the selected admin role.':'DB 管理情報には admin 参照ロールが必要です。',
 'Platform management requires current platform authority and the selected admin role.':'基盤管理記録には現在の基盤管理権限と admin 参照ロールが必要です。',
 'Creation time and latest persisted state; this is not a full transition history. Untimed records appear last and are excluded by time filters. AI records are latest saved metadata summaries only; AI success does not prove a SQL data commit.':'作成時刻と最新の保存状態です。全状態遷移の履歴ではありません。時刻未記録の行は末尾に表示し、期間指定時は除外します。AI 記録は最新の保存済みメタデータの要約のみです。AI の成功は SQL によるデータのコミットを証明しません。',
 'Metadata only. SQL, prompts, results, definitions and raw payloads are excluded. Existing retention is unchanged. Related IDs do not grant content access.':'メタデータのみです。SQL・プロンプト・結果・定義・生の payload は含みません。既存の保持方針は変更しません。関連 ID は内容へのアクセス権限を与えません。',
 'Applied result filters':'結果に適用済みの条件',
 'Scope: {scope} · Role: {role} · DB: {database}':'範囲: {scope} · ロール: {role} · DB: {database}',
 'No database scope':'DB の指定なし',
 'Audit target not yet verified':'確認できていません',
 'Period (UTC, inclusive): {from} to {to}':'期間 (UTC、境界を含む): {from} ～ {to}',
 'No start bound':'開始指定なし','No end bound':'終了指定なし',
 'Source: {source} · State: {state} · Search: {search}':'記録元: {source} · 状態: {state} · 検索語: {search}',
 'Any state':'すべての状態','No search term':'検索語なし',
 '{count} on this page · Page size: {limit}':'このページに {count} 件 · ページ上限: {limit} 件',
 'Time UTC':'時刻 UTC','Source / action':'記録元／操作','Target IDs':'対象 ID','Actor':'実行者','Not recorded':'未記録',
 'Previous page':'前のページ','Next page':'次のページ','Download metadata JSON':'メタデータ JSON を保存',
 'View linked audit metadata':'関連監査メタデータを表示','Committed':'コミット済み','Cleanup failure':'清掃エラーあり','Related IDs':'関連 ID','Saved role':'保存ロール',
 'Created UTC':'作成 UTC','Updated UTC':'更新 UTC','Finished UTC':'終了 UTC','Event ID':'イベント ID','Source ID':'記録元 ID','Revision':'リビジョン',
 'Action':'操作','Yes':'はい','No':'いいえ','Loading audit…':'監査を取得中…'
});
for(const key of Object.keys(i18n.ja))if(!(key in i18n.en))i18n.en[key]=key;

// Compute owns only this saved-summary list; Audit and Platform retain their own state.
const dbActivity={installed:false,epoch:0,auth:'',view:'',busy:null,role:'',database:'',scope:'self',source:'query',state:'',search:'',limit:'20',databases:[],capabilities:null,proof:null,items:[],applied:null,cursor:'',cursors:[],next:null,notice:''};
const dbActivityStates={query:['QUEUED','RUNNING','FINISHED','FAILED','CANCELED','UNKNOWN'],job_run:['queued','running','succeeded','failed','canceled','unknown']};
Object.assign(i18n.ja,{
 'Database saved activity':'データベースの保存済みアクティビティ',
 'Saved queries':'保存済みクエリ','Saved job runs':'保存済みジョブ実行',
 'Saved state':'保存済み状態','Metadata search':'メタデータ検索','All states':'すべての状態','None':'なし',
 'Saved summaries only; coverage and retention depend on each source. This is not live activity, complete transition history, or SQL commit proof.':'保存済み要約のみです。収録範囲と保持期間は参照元ごとに異なります。ライブの稼働状況、完全な状態遷移履歴、SQL のコミット証明ではありません。',
 'Refresh context, choose a READY Database and search explicitly.':'コンテキストを更新し、READY のデータベースを選択して明示的に検索してください。',
 'Activity context refreshed. Search explicitly.':'アクティビティのコンテキストを更新しました。明示的に検索してください。',
 'Activity filters changed. Search again.':'アクティビティの条件が変わりました。再検索してください。',
 'Saved activity unavailable. Refresh context and search again.':'保存済みアクティビティを取得できません。コンテキストを更新して再検索してください。',
 'Choose one available source, a valid saved state, search up to 256 characters and page size 1–100.':'利用可能な参照元を1つ選び、有効な保存済み状態、256文字以内の検索条件、1〜100件のページ件数を指定してください。',
 'Applied activity: {scope} · source: {source} · role: {role} · DB: {database} · saved state: {state} · search: {search} · limit: {limit} · observed: {time}':'適用済みアクティビティ: {scope} · 参照元: {source} · ロール: {role} · DB: {database} · 保存済み状態: {state} · 検索: {search} · 件数: {limit} · 観測日時: {time}',
 'Loading saved activity…':'保存済みアクティビティを読み込んでいます…',
 'No saved activity matches these filters.':'この条件に一致する保存済みアクティビティはありません。',
 'Source ID':'参照元 ID','Created UTC':'作成日時 UTC','Finished UTC':'終了日時 UTC'
});
for(const key of Object.keys(i18n.ja))if(!(key in i18n.en))i18n.en[key]=key;
function dbActivityAuth(){return JSON.stringify([databaseMode,dbPrincipal,dbEpoch,currentRole,dbMaximumRole,dbAuthorizationVersion,dbRoleVersion(dbActivity.role),dbAdmin.platform]);}
function dbActivityClear(notice='',authority=false){
 const p=dbActivity;if(authority){p.databases=[];p.capabilities=null;}++p.epoch;p.busy=null;p.items=[];p.applied=null;p.proof=null;p.cursor='';p.cursors=[];p.next=null;p.notice=notice;
 if(p.installed)dbActivityRender();
}
function dbActivitySync(){
 const p=dbActivity;if(!p.installed)return;
 const auth=dbActivityAuth();
 if(auth!==p.auth){p.auth=auth;p.databases=[];p.capabilities=null;dbActivityClear();}
 if(activeTab!==p.view){p.view=activeTab;if(p.busy){++p.epoch;p.busy=null;p.notice='';}}
 if(!databaseMode||!dbRoleWithinHeader(p.role))dbActivityClear();
 dbActivityChoices();dbActivityRender();
}
function dbActivityOptions(name,items,placeholder){
 const node=$('observation-activity-'+name),p=dbActivity,signature=JSON.stringify([items,placeholder]);
 if(node.dataset.activityChoices!==signature){
  node.replaceChildren(...(placeholder?[new Option(t(placeholder),'')]:[]),...items.map(item=>Object.assign(new Option(item.label,item.value),{disabled:!!item.disabled})));node.dataset.activityChoices=signature;
  if(placeholder)ui(node.options[0],placeholder);items.forEach((item,i)=>{if(item.key)ui(node.options[i+(placeholder?1:0)],item.key);});}
 node.value=items.some(item=>item.value===p[name])?p[name]:'';
}
function dbActivityChoices(){
 const p=dbActivity;
 dbActivityOptions('role',['bi_viewer','data_engineer','admin'].map(value=>({value,label:value,disabled:!dbRoleWithinHeader(value)})),'Reference role');
 dbActivityOptions('database',p.databases.filter(d=>d.state==='READY'&&d.capabilities?.query===true&&d.allowed_roles?.includes(p.role)).map(d=>({value:d.id,label:d.display_name+' · '+d.id})),'Choose database');
 dbActivityOptions('scope',[{value:'self',label:t('My DB records'),key:'My DB records'},{value:'database',label:t('DB administration'),key:'DB administration',disabled:p.role!=='admin'||!p.capabilities?.scopes?.includes('database')}]);
 dbActivityOptions('source',Object.keys(dbActivityStates).map(value=>({value,label:t(value==='query'?'Saved queries':'Saved job runs'),key:value==='query'?'Saved queries':'Saved job runs',disabled:!p.capabilities?.sources?.includes(value)})));
 dbActivityOptions('state',(dbActivityStates[p.source]||[]).map(value=>({value,label:value})),'All states');
 $('observation-activity-refresh').disabled=!!p.busy||!dbRoleWithinHeader(p.role);
 $('observation-activity-submit').disabled=!!p.busy||!dbRoleWithinHeader(p.role)||!p.databases.some(d=>d.id===p.database&&d.state==='READY'&&d.capabilities?.query===true&&d.allowed_roles?.includes(p.role)&&Number.isInteger(d.binding_revision)&&d.binding_revision>0&&!!d.generation_id)||!p.capabilities?.scopes?.includes(p.scope)||!p.capabilities?.sources?.includes(p.source)||!['self','database'].includes(p.scope)||p.scope==='database'&&p.role!=='admin';
 $('observation-activity-previous').disabled=!!p.busy||!p.applied||!p.cursors.length;
 $('observation-activity-next').disabled=!!p.busy||!p.applied||!p.next;
}
function dbActivityInstall(){
 const p=dbActivity;if(p.installed)return;p.installed=true;p.role=currentRole;p.auth=dbActivityAuth();p.view=activeTab;
 const panel=dbAdminNode('section','observation-activity-panel',$('compute'));panel.className='panel';panel.setAttribute('role','region');panel.setAttribute('aria-labelledby','observation-activity-title');
 const head=dbAdminNode('div','',panel);head.className='panel-head';dbAdminNode('h2','observation-activity-title',head,'Database saved activity');
 const body=dbAdminNode('div','',panel);body.className='panel-body';
 dbAdminNode('p','',body,'Saved summaries only; coverage and retention depend on each source. This is not live activity, complete transition history, or SQL commit proof.');
 const bar=dbAdminNode('div','',body);bar.className='toolbar';
 for(const [name,key] of [['role','Reference role'],['database','Database'],['scope','Scope'],['source','Source'],['state','Saved state'],['search','Metadata search'],['limit','Page size']]){
  const field=dbAdminNode('span','',bar);field.className='field';const label=dbAdminNode('label','',field,key),node=dbAdminNode(['search','limit'].includes(name)?'input':'select','observation-activity-'+name,field);label.htmlFor=node.id;
  if(name==='search'){node.type='search';node.maxLength=256;}if(name==='limit'){node.type='number';node.min='1';node.max='100';}node.value=p[name];
  const edit=()=>{p[name]=node.value;if(name==='role'){p.database='';p.databases=[];p.capabilities=null;}if(name==='source')p.state='';dbActivityClear('Activity filters changed. Search again.');p.auth=dbActivityAuth();dbActivityChoices();};
  node.onchange=edit;if(['search','limit'].includes(name))node.oninput=edit;
 }
 for(const [name,key,fn] of [['refresh','Refresh context',()=>dbActivityRefresh()],['submit','Search',()=>dbActivitySearch()],['previous','Previous page',()=>dbActivitySearch('previous')],['next','Next page',()=>dbActivitySearch('next')]])dbAdminButton(bar,'observation-activity-'+name,key,fn);
 dbAdminNode('p','observation-activity-context',body);const status=dbAdminNode('p','observation-activity-status',body);status.setAttribute('aria-live','polite');
 const rows=dbAdminNode('div','observation-activity-rows',body);rows.className='table-wrap';dbActivitySync();
}
function dbActivityCapture(){
 const p=dbActivity,epoch=++p.epoch,auth=dbActivityAuth(),token={};p.busy=token;
 return {token,current:()=>p.epoch===epoch&&p.busy===token&&auth===dbActivityAuth()};
}
async function dbActivityAuthorize(current,requireDatabase=true){
 const p=dbActivity,role=p.role,scope=p.scope,database=p.database,source=p.source;
 if(!dbRoleWithinHeader(role)||!['self','database'].includes(scope)||scope==='database'&&role!=='admin')throw Error('ACTIVITY_AUTH');
 const items=await dbReadInventory(role,current);if(!current()||!dbInventoryCurrent(items))return null;
 const capabilities=await dbAPI('/api/v1/audit/capabilities',role);if(!current()||!dbInventoryCurrent(items))return null;
 p.databases=items;p.capabilities={scopes:capabilities.scopes,sources:capabilities.sources};
 if(!capabilities.scopes?.includes(scope)||!capabilities.sources?.includes(source))throw Error('ACTIVITY_AUTH');
 const d=items.find(d=>d.id===database);
 if(requireDatabase&&(!database||!d||d.state!=='READY'||!d.allowed_roles?.includes(role)||d.capabilities?.query!==true||!Number.isInteger(d.binding_revision)||d.binding_revision<1||!d.generation_id))throw Error('ACTIVITY_AUTH');
 return {scope,database,role,items,d,binding:dbAuditBinding(d)};
}
async function dbActivityRefresh(){
 const p=dbActivity;dbActivitySync();
 // Ordinary reauthorization must not replace the LIST's pre/post authorization token.
 if(p.busy||!dbRoleWithinHeader(p.role))return;
 const read=dbActivityCapture();
 try{const proof=await dbActivityAuthorize(read.current,!!p.database);if(!proof||!read.current())return;
  if(p.proof&&p.proof.binding!==proof.binding)dbActivityClear();
  else p.notice='Activity context refreshed. Search explicitly.';
 }catch(error){if(read.current())dbActivityClear('Saved activity unavailable. Refresh context and search again.',error.code!=='STALE_CURSOR'&&error.status!==409);}
 finally{if(p.busy===read.token)p.busy=null;dbActivitySync();}
}
function dbActivityFilters(){
 const p=dbActivity;
 if(!dbActivityStates[p.source]||p.state&&!dbActivityStates[p.source].includes(p.state)||[...p.search].length>256||/[\x00-\x1f]/.test(p.search)||!/^\d+$/.test(p.limit)||+p.limit<1||+p.limit>100)throw Error('ACTIVITY_INPUT');
 return {scope:p.scope,database_id:p.database,source:p.source,state:p.state,search:p.search,limit:+p.limit};
}
const dbActivityCursor=value=>typeof value==='string'&&value.length>0&&value.length<=24000&&!/[\x00-\x1f]/.test(value);
async function dbActivitySearch(direction='search'){
 const p=dbActivity;dbActivitySync();let filters;
 try{filters=direction==='search'?dbActivityFilters():p.applied&&Object.fromEntries(['scope','database_id','source','state','search','limit'].map(key=>[key,p.applied[key]]));if(!filters)return;}catch{p.notice='Choose one available source, a valid saved state, search up to 256 characters and page size 1–100.';dbActivityRender();return;}
 const cursor=direction==='next'?p.next:direction==='previous'?p.cursors.at(-1):'';
 if(direction!=='search'&&!dbActivityCursor(cursor))return;
 const stack=direction==='next'?[...p.cursors,p.cursor]:direction==='previous'?p.cursors.slice(0,-1):[];
 const read=dbActivityCapture();p.notice='Loading saved activity…';dbActivityChoices();dbActivityRender();
 try{
  const before=await dbActivityAuthorize(read.current);if(!before||!read.current())return;
  if(p.proof&&p.proof.binding!==before.binding)throw Error('ACTIVITY_CONTEXT');
  const params=new URLSearchParams();for(const [key,value] of Object.entries(filters))if(value!=='')params.set(key,String(value));if(cursor)params.set('cursor',cursor);
  const data=await dbAPI('/api/v1/audit/events?'+params,before.role);if(!read.current())return;
  const expectedFilters={...filters,from:null,to:null};
  if(!data.filters||Array.isArray(data.filters)||Object.keys(data.filters).length!==8||!Object.keys(expectedFilters).every(key=>Object.hasOwn(data.filters,key)&&data.filters[key]===expectedFilters[key]))throw Error('ACTIVITY_RESPONSE');
  dbAuditContext(data.context,before);
  const schemaIds=data.context.schema_ids;
  if(!Array.isArray(schemaIds)||!schemaIds.every(id=>typeof id==='string')||!dbActivityCursor(data.page_cursor)||data.next_cursor!=null&&!dbActivityCursor(data.next_cursor)||!Array.isArray(data.items)||data.items.length>filters.limit||!dbAuditTime(data.observed_at))throw Error('ACTIVITY_RESPONSE');
  const items=data.items.map(raw=>{
   if(raw?.source!==filters.source||raw.database_id!==before.database||typeof raw.source_id!=='string'||!raw.source_id||!dbActivityStates[filters.source].includes(raw.state)||filters.state&&raw.state!==filters.state||raw.schema_id!=null&&!schemaIds.includes(raw.schema_id)||before.scope==='self'&&(raw.actor!==JSON.parse(dbPrincipal)[0]||raw.saved_role!==before.role||!schemaIds.includes(raw.schema_id)))throw Error('ACTIVITY_RESPONSE');
   const event=dbAuditEvent(raw);
   return Object.fromEntries(['source_id','actor','schema_id','saved_role','state','created_at','finished_at'].map(key=>[key,event[key]]));
  });
  const after=await dbActivityAuthorize(read.current);if(!after||!read.current())return;
  if(before.binding!==after.binding)throw Error('ACTIVITY_CONTEXT');dbAuditContext(data.context,after);
  p.items=items;p.proof=after;p.applied={...filters,role:after.role,observed_at:dbAuditTime(data.observed_at)};p.cursor=data.page_cursor;p.next=data.next_cursor||null;p.cursors=stack;p.notice=items.length?'':'No saved activity matches these filters.';
 }catch(error){if(read.current())dbActivityClear(error.code==='STALE_CURSOR'||error.status===409?'Cursor expired. Search again.':'Saved activity unavailable. Refresh context and search again.',error.code!=='STALE_CURSOR'&&error.status!==409);}
 finally{if(p.busy===read.token)p.busy=null;dbActivitySync();}
}
function dbActivityRender(){
 const p=dbActivity,a=p.applied,context=$('observation-activity-context'),rows=$('observation-activity-rows');
 ui(context,'');if(a)ui(context,'Applied activity: {scope} · source: {source} · role: {role} · DB: {database} · saved state: {state} · search: {search} · limit: {limit} · observed: {time}',{scope:t(a.scope==='self'?'My DB records':'DB administration'),source:t(a.source==='query'?'Saved queries':'Saved job runs'),role:a.role,database:a.database_id,state:a.state||t('All states'),search:a.search||t('None'),limit:a.limit,time:a.observed_at});
 ui($('observation-activity-status'),p.notice||(!a?'Refresh context, choose a READY Database and search explicitly.':''));rows.replaceChildren();if(!a)return;
 const table=dbAdminNode('table','',rows),head=dbAdminNode('thead','',table),hr=dbAdminNode('tr','',head),body=dbAdminNode('tbody','',table);
 for(const key of ['Source ID','Actor','Schema','Saved role','Saved state','Created UTC','Finished UTC']){const th=dbAdminNode('th','',hr,key);th.scope='col';}
 for(const item of p.items){const row=dbAdminNode('tr','',body);for(const key of ['source_id','actor','schema_id','saved_role','state','created_at','finished_at']){const td=dbAdminNode('td','',row);if(item[key]==null)ui(td,'Not recorded');else td.textContent=String(item[key]);}}
}

const dbAuditFields=['id','source','source_id','action','actor','database_id','schema_id','warehouse_id','saved_role','state','created_at','updated_at','finished_at','revision','committed','has_cleanup_error'];
const dbAuditAIKinds={sql_explanation:'Saved SQL explanation',sql_plan_explanation:'Saved SQL execution plan explanation',saved_plan_explanation:'Saved-plan re-explanation',sql_proposal:'Saved SQL proposal',catalog_proposal:'Saved catalog description proposal'};
const dbAuditSourceLabels={query:'Query executions',dataops:'DataOps operations',job_run:'Job runs',job_step:'Job steps',stage_run:'Stage runs',stage_scan:'Stage scans',mart_run:'Managed Mart runs',dbt:'Database dbt runs',schedule:'Saved schedules',schedule_tick:'Schedule ticks',alert:'Saved alerts',wap_model_version:'WAP model versions',wap_build:'WAP builds',wap_audit:'WAP audits',wap_publication:'WAP publications',database_creation:'Database creation records',database_request:'Database requests',logical_request:'Logical resource requests',management:'Management operations',ai:'Saved AI metadata'};
const dbAuditSourceKey=source=>Object.hasOwn(dbAuditSourceLabels,source)?dbAuditSourceLabels[source]:null;
const dbAuditContextFields=['scope','principal_id','selected_role','database_id','binding_revision','generation_id','grant_revision','platform_admin'];
const dbAuditScalar=v=>typeof v==='string'||typeof v==='number'||typeof v==='boolean';
function dbAuditTime(value){if(value==null||value==='')return null;const date=new Date(value);return Number.isFinite(date.getTime())?date.toISOString():null;}
function dbAuditEvent(event){
 if(!event||typeof event.id!=='string'||!event.id||typeof event.source!=='string')throw Error('AUDIT_RESPONSE');
 if(event.source==='ai'&&(typeof event.action!=='string'||!Object.hasOwn(dbAuditAIKinds,event.action)||!['QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELED','UNKNOWN'].includes(event.state)||!Array.isArray(event.links)||event.links.length!==0))throw Error('AUDIT_RESPONSE');
 const clean=Object.fromEntries(dbAuditFields.map(key=>[key,dbAuditScalar(event[key])?event[key]:null]));
 // Copy only the frozen relation DTO; IDs never become URLs or content routes.
 clean.links=(Array.isArray(event.links)?event.links:[]).filter(link=>link&&
  ['relation','source','source_id'].every(key=>typeof link[key]==='string')&&
  (link.event_id===null||typeof link.event_id==='string'&&!!link.event_id)
 ).map(link=>Object.fromEntries(['relation','source','source_id','event_id'].map(key=>[key,link[key]])));
 return clean;
}
function dbAuditStamp(){return JSON.stringify([dbPrincipal,dbEpoch,currentRole,dbMaximumRole,dbAuthorizationVersion,dbRoleVersion(dbAudit.role),dbAdmin.platform,dbAudit.epoch,dbAudit.scope,dbAudit.role,dbAudit.database]);}
function dbAuditPurge(){
 ++dbAudit.epoch;++dbAudit.list;++dbAudit.selection;++dbAudit.detailRequest;
 dbAudit.items=[];dbAudit.selected='';dbAudit.detail=null;dbAudit.applied=null;dbAudit.cursors=[];dbAudit.cursor='';dbAudit.next=null;dbAudit.proof=null;dbAudit.choiceItems=null;dbAudit.capabilities=null;
 if(dbAudit.url){URL.revokeObjectURL(dbAudit.url);dbAudit.url=null;}
 if(dbAudit.installed){const rows=$('db-audit-rows'),detail=$('db-audit-detail');if(rows)rows.replaceChildren();if(detail)detail.replaceChildren();ui($('db-audit-status'),'Search to load saved metadata.');dbAuditRenderApplied();dbAuditChoices([]);}
}
function dbAuditReason(){
 if(!dbRoleWithinHeader(dbAudit.role))return 'Choose a currently available database and permitted reference role.';
 if(dbAudit.scope==='platform')return dbAudit.role==='admin'&&currentRole==='admin'&&dbMaximumRole==='admin'&&dbAdmin.platform?'':'Platform management requires current platform authority and the selected admin role.';
 if(dbAudit.scope==='database'&&dbAudit.role!=='admin')return 'DB administration requires the selected admin role.';
 return '';
}
function dbAuditValidate(){
 if(!dbAudit.installed)return;
 const p=dbAudit.proof,cached=dbInventories.get(dbAudit.role)?.items,d=cached?.find(d=>d.id===dbAudit.database);
 if(dbAuditReason()||p&&cached&&p.scope!=='platform'&&(!d||dbAuditBinding(d)!==p.binding))dbAuditPurge();
 else{const items=p?.items??dbAudit.choiceItems;dbAuditChoices(dbInventoryCurrent(items)?items:[]);}
}
const dbAuditBinding=d=>JSON.stringify([d?.id,d?.state,d?.binding_revision,d?.generation_id,d?.grant_revision??null,d?.allowed_roles]);
function dbAuditPaging(){
 const submit=$('db-audit-submit'),previous=$('db-audit-previous'),next=$('db-audit-next');
 if(submit)submit.disabled=!dbAudit.proof||!dbInventoryCurrent(dbAudit.proof.items)||!!dbAuditReason();
 if(previous)previous.disabled=!dbAudit.applied||!dbAudit.cursors.length;
 if(next)next.disabled=!dbAudit.applied||!dbAudit.next;
}
function dbAuditSelectOptions(id,items,value,placeholder=''){
 const select=$(id);if(!select)return;
 const signature=JSON.stringify([items.map(item=>[item.value,item.label,item.labelKey,!!item.disabled]),placeholder]);
 if(select.dataset.auditChoices===signature){
  // Reuse the same Option nodes so focus, selection and language updates survive.
  let index=0;if(placeholder){if(select.options[0])ui(select.options[0],placeholder);index=1;}
  items.forEach((item,i)=>{const option=select.options[i+index];if(option&&item.labelKey)ui(option,item.labelKey);});
  select.value=value||'';return;
 }
 const nodes=[];
 if(placeholder)nodes.push(Object.assign(new Option(t(placeholder),''),{textContent:t(placeholder)}));
 for(const item of items){const option=new Option(item.label||'',item.value);option.disabled=!!item.disabled;if(item.labelKey)ui(option,item.labelKey);nodes.push(option);}
 if(id!=='db-audit-database'&&value&&!items.some(item=>item.value===value)){const option=new Option(t('Unavailable'),value);option.disabled=true;nodes.push(option);}
 select.dataset.auditChoices=signature;select.replaceChildren(...nodes);select.value=value||'';
}
function dbAuditResolveRole(){
 if(!dbRoleWithinHeader(dbAudit.role))dbAudit.role=dbRoleWithinHeader(currentRole)?currentRole:['data_engineer','bi_viewer','admin'].find(dbRoleWithinHeader)||'';
 return dbAudit.role;
}
function dbAuditPreferredDatabase(items,role){
 const allowed=items.filter(d=>d.state==='READY'&&d.allowed_roles?.includes(role));
 const workspace=(typeof dbCurrent==='function'?dbCurrent()?.database_id:'')||(typeof dbExplorer!=='undefined'?dbExplorer.database_id:'')||'';
 return allowed.find(d=>d.id===workspace)||allowed[0]||null;
}
function dbAuditChoices(items=[]){
 // Reference context follows the ceiling without changing worksheet ownership.
 dbAuditResolveRole();
 const scopes=dbAudit.capabilities?.scopes;
 if(dbAudit.scope==='database'&&dbAudit.role!=='admin'||dbAudit.scope==='platform'&&
  !(dbAudit.role==='admin'&&currentRole==='admin'&&dbMaximumRole==='admin'&&dbAdmin.platform)||
  scopes&&!scopes.includes(dbAudit.scope))dbAudit.scope='self';
 const scopedDatabases=items.filter(d=>d.state==='READY'&&d.allowed_roles?.includes(dbAudit.role));
 if(dbAudit.scope!=='platform'&&scopedDatabases.length&&!scopedDatabases.some(d=>d.id===dbAudit.database)){
  const preferred=dbAuditPreferredDatabase(items,dbAudit.role);
  dbAudit.database=preferred?.id||'';
 }
 const choices=[['self','My DB records'],['database','DB administration'],['platform','Platform management']];
 dbAuditSelectOptions('db-audit-scope',choices.map(([value,label])=>({value,label,labelKey:label,disabled:value==='database'&&!(dbAudit.role==='admin'&&dbRoleWithinHeader('admin')&&scopes?.includes('database'))||value==='platform'&&!(dbAudit.role==='admin'&&currentRole==='admin'&&dbMaximumRole==='admin'&&dbAdmin.platform&&dbAudit.capabilities?.scopes?.includes('platform'))})),dbAudit.scope,'');
 dbAuditSelectOptions('db-audit-role',['bi_viewer','data_engineer','admin'].map(value=>({value,label:value,labelKey:dbRoleDisplayNames[value],disabled:!dbRoleWithinHeader(value)})),dbAudit.role,'');
 const databases=dbAudit.scope==='platform'?[{value:'',label:'All databases',labelKey:'All databases'}]:[];
 databases.push(...scopedDatabases.map(d=>({value:d.id,label:d.display_name+' · '+d.sql_catalog})));
 dbAuditSelectOptions('db-audit-database',databases,dbAudit.scope==='platform'?'':dbAudit.database,dbAudit.scope==='platform'?'':'Choose database');
 const sources=[{value:'',label:'All sources',labelKey:'All sources'},...(dbAudit.capabilities?.sources||[]).filter(value=>typeof value==='string').map(value=>({value,label:value,labelKey:dbAuditSourceKey(value)}))];
 const source=dbAudit.draft.source||$('db-audit-source')?.value||'';
 dbAuditSelectOptions('db-audit-source',sources,source,'');
 const database=$('db-audit-database');if(database)database.disabled=dbAudit.scope==='platform';
 dbAuditPaging();
}
async function dbAuditAuthorize(stamp,current=()=>stamp===dbAuditStamp(),updateChoices=false,preloadedItems=null){
 if(!current())return null;
 dbAuditResolveRole();
 if(dbAuditReason())throw Error('AUDIT_AUTH');
 let role=dbAudit.role,scope=dbAudit.scope,database=dbAudit.database;
 const items=preloadedItems||await dbReadInventory(role,current);
 if(!current()||!dbInventoryCurrent(items))return null;
 if(updateChoices){dbAuditChoices(items);role=dbAudit.role;scope=dbAudit.scope;database=dbAudit.database;}
 const d=items.find(d=>d.id===database);
 if(scope!=='platform'&&(!d||d.state!=='READY'||!d.allowed_roles?.includes(role)))throw Object.assign(Error('AUDIT_AUTH'),{auditChoices:items});
 const capabilities=await dbAPI('/api/v1/audit/capabilities',role);
 if(!current()||!dbInventoryCurrent(items))return null;
 if(!Array.isArray(capabilities.scopes)||!capabilities.scopes.includes(scope)||scope==='platform'&&(!dbAdmin.platform||dbAuditReason()))throw Error('AUDIT_AUTH');
 return {scope,database,role,items,d,binding:dbAuditBinding(d),capabilities};
}
function dbAuditContext(context,proof){
 if(!context||context.scope!==proof.scope||context.selected_role!==proof.role||context.principal_id!==JSON.parse(dbPrincipal)[0]||typeof context.platform_admin!=='boolean')throw Error('AUDIT_CONTEXT');
 const expected=proof.scope==='platform'?null:proof.database;
 if(context.database_id!==expected)throw Error('AUDIT_CONTEXT');
 for(const key of ['binding_revision','generation_id','grant_revision'])if(!(key in context)||(context[key]??null)!==(proof.scope==='platform'?null:proof.d?.[key]??null))throw Error('AUDIT_CONTEXT');
 if(proof.scope==='platform'&&!context.platform_admin)throw Error('AUDIT_CONTEXT');
}
async function dbAuditResponse(path,stamp,current){
 const before=await dbAuditAuthorize(stamp,current);if(!before||!current())return null;
 if(dbAudit.proof&&dbAudit.proof.binding!==before.binding)throw Error('AUDIT_CONTEXT');
 const data=await dbAPI(path,dbAudit.role);if(!current())return null;
 const after=await dbAuditAuthorize(stamp,current);if(!after||!current())return null;
 if(before.binding!==after.binding)throw Error('AUDIT_CONTEXT');
 dbAuditContext(data.context,after);
 dbAudit.proof=after;dbAudit.capabilities=after.capabilities;
 return data;
}
function dbAuditFailure(error,current){
 if(!current())return;
 const items=error.auditChoices;
 const read=dbInventoryCurrent(items)?dbAuthorizationReads.get(items):null;
 dbAuditPurge();$('db-audit-status').setAttribute('role','alert');
 if(read){
  // Keep authorization for the selector after purge invalidates the audit request.
  const choices=items.slice();
  dbAuthorizationReads.set(choices,{...read,current:null});
  dbAudit.choiceItems=choices;dbAuditChoices(choices);
  ui($('db-audit-status'),'Choose a currently available database and permitted reference role.');return;
 }
 ui($('db-audit-status'),error.code==='STALE_CURSOR'?'Cursor expired. Search again.':'Audit unavailable. Check the reference role and database, then search again.');
}
function dbAuditFilters(recordDraft=false){
 const values={};for(const key of ['source','state','search','from','to','limit'])values[key]=$('db-audit-'+key).value;
 if(recordDraft)dbAudit.draft={...values};
 if(!/^\d+$/.test(values.limit)||Number(values.limit)<1||Number(values.limit)>100)throw Error('AUDIT_INPUT');
 values.limit=Number(values.limit);
 for(const key of ['from','to'])if(values[key]){
  // datetime-local is read as JST; the API keeps its existing UTC ISO form.
  const utc=jstInputToISO(values[key]);if(!utc)throw Error('AUDIT_INPUT');values[key]=utc;
 }
 if(values.from&&values.to&&values.from>values.to)throw Error('AUDIT_INPUT');
 return values;
}
function dbAuditParams(filters={},cursor=''){
 const p=new URLSearchParams({scope:dbAudit.scope});if(dbAudit.scope!=='platform')p.set('database_id',dbAudit.database);
 for(const [key,value] of Object.entries(filters))if(value!==''&&value!=null)p.set(key,String(value));
 if(cursor)p.set('cursor',cursor);return p.toString();
}
function dbAuditDraftPending(){
 if(!dbAudit.applied)return true;
 try{return JSON.stringify(dbAuditFilters())!==JSON.stringify(dbAudit.applied);}
 catch{return true;}
}
function dbAuditResultStatus(){
 const node=$('db-audit-status');if(!node)return;
 if(dbAuditDraftPending())ui(node,'Filters pending. Search to apply.');
 else ui(node,'');
}
function dbAuditScopeLabel(scope){return t({self:'My DB records',database:'DB administration',platform:'Platform management'}[scope]||'My DB records');}
function dbAuditSourceLabel(source){if(!source)return t('All sources');const key=dbAuditSourceKey(source);return key?t(key):source;}
function dbAuditDatabaseLabel(proof){
 const scope=dbAudit.applied?.scope||dbAudit.scope;
 if(scope==='platform')return t('All databases');
 const d=proof?.d;
 if(d){const name=dbDatabaseLabel(d),sql=d.sql_catalog;return name&&sql&&name!==sql?name+' · '+sql:name||sql||'—';}
 return dbAudit.database?t('Audit target not yet verified'):'—';
}
function dbAuditWorkspaceSnapshot(){
 try{const label=$('nav-workspace-context-label');return {text:label?.textContent||'',database:dbCurrent()?.database_id||'',schema:dbCurrent()?.schema_id||''};}catch{return null;}
}
function dbAuditWorkspaceRestore(snapshot){
 try{
  if(!snapshot||snapshot.database!==(dbCurrent()?.database_id||''))return;
  if(!dbTabItems().some(d=>d.id===snapshot.database))return;
  const label=$('nav-workspace-context-label');if(!label)return;
  if(label.textContent!==snapshot.text)workbenchRenderWorkspaceNav();
 }catch{/* Presentation only. */}
}
function dbAuditRenderApplied(){
 const node=$('audit-applied');if(!node)return;
 const applied=dbAudit.applied;
 if(!applied){node.textContent='';node.hidden=true;return;}
 node.hidden=false;
 ui(node,'Applied: {scope} / DB {database} / {from} to {to} / origin {source} · {count}',{
  scope:dbAuditScopeLabel(applied.scope),database:dbAuditDatabaseLabel(dbAudit.proof),
  from:applied.from?fmtJST(applied.from):t('No start bound'),to:applied.to?fmtJST(applied.to):t('No end bound'),
  source:dbAuditSourceLabel(applied.source),count:dbAudit.items.length});
}
function dbAuditDownloadHistory(){
 const p=dbAudit;
 if(!p.applied||!p.items.length){ui($('db-audit-status'),'No displayed history to save.');return;}
 const context={scope:p.applied.scope,selected_role:p.proof?.role||p.applied.role||'',database_id:p.applied.scope==='platform'?null:p.proof?.database??null};
 const payload={context,filters:{...p.applied},items:p.items,exported_at:new Date().toISOString()};
 const url=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}));
 try{const link=document.createElement('a');link.href=url;link.download='audit-history.json';link.click();}finally{URL.revokeObjectURL(url);}
 ui($('db-audit-status'),'Displayed history saved as JSON.');
}
async function dbAuditSearch(direction='search'){
 const workspace=dbAuditWorkspaceSnapshot();
 let filters;try{filters=direction==='search'?dbAuditFilters(true):dbAudit.applied;if(!filters)return;}catch{ui($('db-audit-status'),'Enter valid JST dates and a page size from 1 to 100.');return;}
 const cursor=direction==='next'?dbAudit.next:direction==='previous'?dbAudit.cursors.at(-1):'';
 if(direction!=='search'&&(!cursor||direction==='next'&&!dbAudit.cursor))return;
 const stack=direction==='next'?[...dbAudit.cursors,dbAudit.cursor]:direction==='previous'?dbAudit.cursors.slice(0,-1):[];
 const reason=dbAuditReason();if(reason){dbAuditPurge();ui($('db-audit-status'),reason);return;}
 const serial=++dbAudit.list,stamp=dbAuditStamp(),current=()=>serial===dbAudit.list&&stamp===dbAuditStamp();
 $('db-audit-status').setAttribute('role','status');
 ui($('db-audit-status'),'Loading audit…');
 try{
  const data=await dbAuditResponse('/api/v1/audit/events?'+dbAuditParams(filters,cursor),stamp,current);if(!data||!current())return;
  if(typeof data.page_cursor!=='string'||!data.page_cursor||!Array.isArray(data.items)||data.items.length>filters.limit||data.next_cursor!=null&&typeof data.next_cursor!=='string')throw Error('AUDIT_RESPONSE');
  const items=data.items.map(dbAuditEvent);
  dbAudit.items=items;dbAudit.applied={...filters};dbAudit.cursor=data.page_cursor;dbAudit.cursors=stack;dbAudit.next=data.next_cursor||null;
  dbAuditChoices(dbAudit.proof?.items||[]);
  dbAuditRenderApplied();dbAuditRenderRows();dbAuditPaging();dbAuditResultStatus();
 }catch(error){dbAuditFailure(error,current);}
 finally{dbAuditWorkspaceRestore(workspace);}
}
const dbAuditStateKeys={QUEUED:'QUEUED',RUNNING:'RUNNING',FINISHED:'FINISHED',SUCCEEDED:'SUCCEEDED',FAILED:'FAILED',CANCELED:'CANCELED',UNKNOWN:'UNKNOWN',queued:'queued',running:'running',succeeded:'succeeded',failed:'failed',canceled:'canceled',unknown:'unknown',pending:'Pending',skipped:'Skipped',rejected:'Rejected',partial:'Partially completed'};
function dbAuditActionKey(event){if(event.source==='ai')return dbAuditAIKinds[event.action]||null;return event.action&&Object.hasOwn(i18n.en,event.action)?event.action:null;}
function dbAuditValue(node,value){if(value==null||value==='')ui(node,'Not recorded');else if(typeof value==='boolean')ui(node,value?'Yes':'No');else if(typeof value==='string'&&Object.hasOwn(dbAuditStateKeys,value))ui(node,dbAuditStateKeys[value]);else node.textContent=String(value);}
function dbAuditTargetText(event){
 const key=dbAuditSourceKey(event.source);
 return key?t(key):'—';
}
function dbAuditRenderRows(){
 const host=$('db-audit-rows');if(!host)return;host.replaceChildren();
 if(!dbAudit.applied)return;
 if(!dbAudit.items.length){const empty=document.createElement('p');empty.className='empty';ui(empty,'No matching history. Reset the filters or run an import or transformation.');host.append(empty);return;}
 const table=document.createElement('table'),head=document.createElement('thead'),hr=document.createElement('tr'),body=document.createElement('tbody');
 for(const key of ['Time (JST)','Action','Target','Actor','State']){const th=document.createElement('th');th.scope='col';ui(th,key);hr.append(th);}head.append(hr);table.append(head,body);
 for(const event of dbAudit.items){const row=document.createElement('tr');
  const timeCell=document.createElement('td');
  if(event.created_at){const time=document.createElement('time');time.datetime=event.created_at;time.textContent=fmtJST(event.created_at,true);timeCell.append(time);}else ui(timeCell,'Not recorded');
  const actionCell=document.createElement('td'),actionLink=document.createElement('a');
  actionLink.href='#audit/'+encodeURIComponent(event.id);actionLink.onclick=click=>{click?.preventDefault?.();return dbAuditDetail(event.id,true);};
  actionLink.textContent=dbAuditSourceLabel(event.source)+' / '+(dbAuditActionKey(event)?t(dbAuditActionKey(event)):t('Recorded operation'));
  actionLink.dataset.auditSource=event.source;actionLink.dataset.auditAction=event.action||'';
  actionCell.append(actionLink);
  const targetCell=document.createElement('td');targetCell.textContent=dbAuditTargetText(event);
  const actorCell=document.createElement('td');actorCell.textContent=dbJobsSafeName(event.actor);
  const stateCell=document.createElement('td');dbAuditValue(stateCell,event.state);
  row.append(timeCell,actionCell,targetCell,actorCell,stateCell);body.append(row);
 }
 table.append(head,body);host.append(table);
}
async function dbAuditDetail(id,push=false){
 dbAudit.selected=id;const selection=++dbAudit.selection,serial=++dbAudit.detailRequest,stamp=dbAuditStamp();
 dbAudit.detail=null;$('db-audit-detail').replaceChildren();
 const current=()=>stamp===dbAuditStamp()&&selection===dbAudit.selection&&serial===dbAudit.detailRequest&&id===dbAudit.selected;
 try{const data=await dbAuditResponse('/api/v1/audit/events/'+encodeURIComponent(id)+'?'+dbAuditParams(),stamp,current);if(!data||!current())return;
  const event=dbAuditEvent(data.event);if(event.id!==id)throw Error('AUDIT_RESPONSE');dbAudit.detail=event;dbAuditRenderDetail(event);
  if(typeof workbenchShowDetail==='function'){try{workbenchShowDetail('pawprints',event.id,{push});}catch{}}
  if(push){const title=$('db-audit-detail-title');title?.scrollIntoView?.({block:'start'});title?.focus?.({preventScroll:true});}
 }catch(error){dbAuditFailure(error,current);}
}
function dbAuditRenderDetail(event){
 const host=$('db-audit-detail');if(!host)return;host.replaceChildren();
 const section=document.createElement('section');host.append(section);
 const title=document.createElement('h2');title.id='db-audit-detail-title';title.tabIndex=-1;ui(title,'Audit record details');section.append(title);
 dbAdminButton(section,'db-audit-record-download','Save this record metadata as JSON',dbAuditDownload);
 const summary=document.createElement('dl');summary.className='kv';section.append(summary);
 const add=(key,fill)=>{const dt=document.createElement('dt'),dd=document.createElement('dd');ui(dt,key);fill(dd);summary.append(dt,dd);return dd;};
 add('Time',dd=>{
  if(event.created_at==null){ui(dd,'Not recorded');return;}
  dd.textContent=fmtJST(event.created_at,true);
  const utc=document.createElement('small');utc.className='muted';utc.textContent=event.created_at+'（UTC）';dd.append(document.createElement('br'),utc);
 });
 const text=value=>{const node=document.createElement('span');node.textContent=value;return node;};
 add('Operation / state',dd=>{
  const action=document.createElement('span');if(dbAuditActionKey(event))ui(action,dbAuditActionKey(event));else ui(action,'Recorded operation');
  const state=document.createElement('span');dbAuditValue(state,event.state);
  dd.append(action,text(' / '),state);
 });
 add('Target / actor',dd=>{
  const target=document.createElement('span');target.textContent=dbAuditTargetText(event);
  const actor=document.createElement('span');actor.textContent=dbJobsSafeName(event.actor);
  dd.append(target,text(' / '),actor);
 });
 const related=document.createElement('p');ui(related,'Related records');section.append(related);
 const stamp=dbAuditStamp(),selection=dbAudit.selection,serial=dbAudit.detailRequest;
 if(!event.links.length){const none=document.createElement('p');ui(none,'None');section.append(none);}
 for(const link of event.links){
  const row=document.createElement('p'),caption=document.createElement('span');row.append(caption);ui(caption,'{relation} · {source} · {id}',()=>({relation:link.relation,source:dbAuditSourceKey(link.source)?t(dbAuditSourceKey(link.source)):link.source,id:link.source_id}));section.append(row);
  if(link.event_id)dbAdminButton(row,'','View linked audit metadata',()=>{
   if(stamp!==dbAuditStamp()||selection!==dbAudit.selection||serial!==dbAudit.detailRequest||dbAudit.detail!==event)return;
   return dbAuditDetail(link.event_id,true);
  });
 }
 const actions=document.createElement('p');section.append(actions);
 const route={query:'#sql',dataops:'#audit',job_run:'#jobs',job_step:'#jobs',schedule:'#jobs',schedule_tick:'#jobs',alert:'#jobs',stage_run:'#ingestion/stages',stage_scan:'#ingestion/stages',dbt:'#transformation',wap_model_version:'#transformation',wap_build:'#transformation',wap_audit:'#transformation',wap_publication:'#transformation',database_creation:'#databases',database_request:'#databases',logical_request:'#databases',management:'#databases'}[event.source]||'#audit';
 const operation=document.createElement('a');operation.href=route;ui(operation,'Open operation screen');actions.append(operation,text(' · '));
 const back=document.createElement('a');back.id='db-audit-back';back.href='#audit';ui(back,'Back to list');back.onclick=click=>{click?.preventDefault?.();dbAuditShowList();};actions.append(back);
 const details=document.createElement('details');details.id='db-audit-all-fields';section.append(details);
 const detailsSummary=document.createElement('summary');ui(detailsSummary,'All fields');details.append(detailsSummary);
 const list=document.createElement('dl');list.className='kv';details.append(list);
 const labels=['Event ID','Source','Source ID','Action','Actor','Database','Schema','Warehouse','Saved role','State','Created','Updated','Finished','Revision','Committed','Cleanup failure'];
 dbAuditFields.forEach((key,i)=>{const dt=document.createElement('dt'),dd=document.createElement('dd');ui(dt,labels[i]);
  if(key==='source'){const sourceKey=dbAuditSourceKey(event.source);if(sourceKey)ui(dd,sourceKey);else dd.textContent=event.source||'';}
  else if(key==='action'){const actionKey=dbAuditActionKey(event);if(actionKey)ui(dd,actionKey);else ui(dd,'Recorded operation');}
  else if(key==='saved_role'){if(event[key]==null)ui(dd,'Not recorded');else if(dbRoleDisplayNames[event[key]])ui(dd,dbRoleDisplayNames[event[key]]);else dd.textContent=dbRoleDisplayLabel(event[key]);}
  else if(key.endsWith('_at')){
   if(event[key]==null)ui(dd,'Not recorded');
   else{
    dd.textContent=fmtJST(event[key],true);
    const utc=document.createElement('small');utc.className='muted';utc.textContent='UTC '+event[key];dd.append(document.createElement('br'),utc);
   }
  }else dbAuditValue(dd,event[key]);
  list.append(dt,dd);
 });
}
function dbAuditShowList(){
 if(typeof dbAudit==='undefined'||!dbAudit.installed)return;
 ++dbAudit.selection;++dbAudit.detailRequest;dbAudit.selected='';dbAudit.detail=null;
 $('db-audit-detail').replaceChildren();
 if(typeof workbenchClearDetail==='function')workbenchClearDetail('pawprints');
}
async function dbAuditRestorePage(page){
 if(typeof dbAudit==='undefined'||!dbAudit.installed)return;
 const id=typeof workbenchDetailId==='function'?workbenchDetailId(page):'';
 if(!id)return dbAuditShowList();
 await dbAuditDetail(id,false);
}
function dbAuditRefreshRowLabels(){
 const host=$('db-audit-rows'),table=host?.children?.[0],body=table?.children?.[1];
 if(!body)return;
 for(const row of body.children){
  const link=row.children?.[1]?.children?.[0];if(!link?.dataset)continue;
  const source=link.dataset.auditSource||'',action=link.dataset.auditAction||'';
  const actionKey=dbAuditActionKey({source,action});
  link.textContent=dbAuditSourceLabel(source)+' / '+(actionKey?t(actionKey):t('Recorded operation'));
 }
}
function dbAuditLanguageChanged(){
 if(typeof dbAudit==='undefined'||!dbAudit.installed)return;
 dbAuditChoices(dbAudit.proof?.items||[]);
 dbAuditRenderApplied();dbAuditRefreshRowLabels();
}
async function dbAuditDownload(){
 const id=dbAudit.selected,selection=dbAudit.selection,serial=dbAudit.detailRequest,stamp=dbAuditStamp();if(!id||!dbAudit.detail)return;
 const current=()=>stamp===dbAuditStamp()&&selection===dbAudit.selection&&serial===dbAudit.detailRequest&&id===dbAudit.selected;
 try{const data=await dbAuditResponse('/api/v1/audit/events/'+encodeURIComponent(id)+'/download?'+dbAuditParams(),stamp,current);if(!data||!current())return;
  const event=dbAuditEvent(data.event);if(event.id!==id)throw Error('AUDIT_RESPONSE');
  const context=Object.fromEntries(dbAuditContextFields.map(key=>[key,data.context[key]]));
  const url=URL.createObjectURL(new Blob([JSON.stringify({event,context},null,2)],{type:'application/json'}));dbAudit.url=url;
  try{const a=document.createElement('a');a.href=url;a.download='audit-metadata.json';a.click();}finally{URL.revokeObjectURL(url);if(dbAudit.url===url)dbAudit.url=null;}
 }catch(error){dbAuditFailure(error,current);}
}
async function dbAuditRefreshAuthorization(){
 if(!dbAudit.installed)return;
 const stamp=dbAuditStamp();try{const p=await dbAuditAuthorize(stamp,undefined,true);if(!p||stamp!==dbAuditStamp())return;
  if(dbAudit.proof&&dbAudit.proof.binding!==p.binding){dbAuditPurge();return;}
  dbAudit.proof=p;dbAudit.capabilities=p.capabilities;dbAuditChoices(p.items);
 }catch(error){if(stamp===dbAuditStamp()){
  dbAuditFailure(error,()=>stamp===dbAuditStamp());
  dbAudit.scope='self';dbAuditChoices(dbAudit.choiceItems||[]);
 }}
}
async function dbAuditOpen(){
 const workspace=dbAuditWorkspaceSnapshot();
 dbAuditResolveRole();
 const reason=dbAuditReason();if(reason){dbAuditPurge();ui($('db-audit-status'),reason);return;}
 const preStamp=dbAuditStamp();let stamp=preStamp;
 try{
  const items=await dbReadInventory(dbAudit.role);
  if(preStamp!==dbAuditStamp()||!dbInventoryCurrent(items))return;
  dbAuditChoices(items);
  stamp=dbAuditStamp();
  const p=await dbAuditAuthorize(stamp,undefined,true,items);if(!p||stamp!==dbAuditStamp())return;
  dbAudit.proof=p;dbAudit.capabilities=p.capabilities;dbAuditChoices(p.items);
  if(!dbAudit.autoUsed&&activeTab==='pawprints'){dbAudit.autoUsed=true;await dbAuditSearch();}
 }catch(error){dbAuditFailure(error,()=>stamp===dbAuditStamp());}
 finally{dbAuditWorkspaceRestore(workspace);}
}
function dbAuditResetFilters(){
 const p=dbAudit;
 for(const [key,value] of Object.entries({from:'',to:'',source:'',state:'',search:'',limit:'50'})){
  const node=$('db-audit-'+key);if(node)node.value=value;p.draft[key]=value;
 }
 p.items=[];p.applied=null;p.cursor='';p.cursors=[];p.next=null;
 const rows=$('db-audit-rows'),detail=$('db-audit-detail');if(rows)rows.replaceChildren();if(detail)detail.replaceChildren();
 dbAuditRenderApplied();dbAuditPaging();dbAuditResultStatus();
}
function dbAuditInstall(){
 if(dbAudit.installed)return;dbAudit.installed=true;
 const w=dbCurrent();dbAudit.role=dbRoleWithinHeader(currentRole)?currentRole:(w?.selected_role||['data_engineer','bi_viewer','admin'].find(dbRoleWithinHeader)||'');dbAudit.database=w?.database_id||(typeof dbExplorer!=='undefined'?dbExplorer.database_id:'')||'';
 const host=$('pawprints');for(const child of host.children)child.hidden=true;
 const panel=dbAdminNode('div','db-audit-panel',host);panel.className='audit-page';
 const flow=dbAdminNode('div','db-audit-flow',panel);flow.className='atlas-flow';
 const title=dbAdminNode('h1','db-audit-title',flow,'Audit log');title.tabIndex=-1;
 const field=(parent,key,label,tag='select',type='')=>{
  const wrapper=document.createElement('label');wrapper.className='atlas-field-label';wrapper.htmlFor='db-audit-'+key;
  const span=document.createElement('span');span.className='atlas-label';ui(span,label);
  const node=dbAdminNode(tag,'db-audit-'+key,undefined);node.className='field';if(type)node.type=type;
  wrapper.append(span,node);parent.append(wrapper);return node;
 };
 const filters=dbAdminNode('details','audit-filters',flow);filters.open=typeof matchMedia!=='function'||!matchMedia('(max-width: 600px)').matches;
 dbAdminNode('summary','',filters,'Filters');
 const fields=dbAdminNode('div','db-audit-main-fields',filters);fields.className='atlas-fields';
 const scope=field(fields,'scope','Audit scope');
 const database=field(fields,'database','DB');
 const from=field(fields,'from','Start (JST)','input','datetime-local');
 const to=field(fields,'to','End (JST)','input','datetime-local');
 const source=field(fields,'source','Origin');
 const search=field(fields,'search','Search','input','search');
 const advanced=dbAdminNode('details','db-audit-advanced-filters',filters);
 dbAdminNode('summary','',advanced,'Advanced filters');
 const advancedFields=dbAdminNode('div','db-audit-advanced-fields',advanced);advancedFields.className='atlas-fields';
 const role=field(advancedFields,'role','Reference role');
 const state=field(advancedFields,'state','State','input');
 const limit=field(advancedFields,'limit','Page size','input','number');limit.min=1;limit.max=100;limit.value='50';
 const actions=dbAdminNode('div','db-audit-actions',filters);actions.className='atlas-actions';
 dbAdminButton(actions,'db-audit-submit','Filter',()=>dbAuditSearch());
 dbAdminButton(actions,'db-audit-reset','Reset filters',dbAuditResetFilters);
 dbAdminButton(actions,'db-audit-download','Save displayed history as JSON',dbAuditDownloadHistory);
 const applied=dbAdminNode('p','audit-applied',flow);applied.hidden=true;
 const status=dbAdminNode('p','db-audit-status',flow,'Search to load saved metadata.');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
 const rows=dbAdminNode('div','db-audit-rows',flow);rows.className='tablewrap';rows.tabIndex=0;rows.setAttribute('role','region');rows.setAttribute('aria-label',t('Audit history'));uiAttributes.push({node:rows,attribute:'aria-label',key:'Audit history'});
 const paging=dbAdminNode('div','db-audit-paging',flow);paging.className='toolbar';
 dbAdminButton(paging,'db-audit-previous','Previous page',()=>dbAuditSearch('previous'));dbAdminButton(paging,'db-audit-next','Next page',()=>dbAuditSearch('next'));
 dbAdminNode('div','db-audit-detail',flow);
 const guide=dbAdminNode('details','db-audit-guide',flow);
 dbAdminNode('summary','',guide,'What is saved');
 dbAdminNode('p','',guide,'The JSON contains only the displayed operation metadata. It does not contain table rows.');
 dbAdminNode('p','',guide,'Metadata only. SQL, prompts, results, definitions and raw payloads are excluded. Existing retention is unchanged. Related IDs do not grant content access.');
 dbAdminNode('p','',guide,'Creation time and latest persisted state; this is not a full transition history. Untimed records appear last and are excluded by time filters. AI records are latest saved metadata summaries only; AI success does not prove a SQL data commit.');
 const paths=dbAdminNode('ul','',guide);
 dbAdminNode('li','',paths,'Runtime SQL queries → saved SQL queries (query). Worksheet Recent Query History restores SQL/context only; it does not execute SQL or retrieve result rows.');
 dbAdminNode('li','',paths,'lake.ingest → DataOps manual ingestion; Ingestion → Stages → ingestion/export records (stage_run) and scan history (stage_scan) for registered Stages. lake.export → DataOps table export. Worksheet result CSV/JSON/TSV downloads belong to the original query, not a new DataOps write operation.');
 dbAdminNode('li','',paths,'catalog.clone → DataOps clone operation. catalog.drop → DataOps catalog unregister with data kept; no physical deletion or automatic Undrop.');
 dbAdminNode('li','',paths,'bear.data_engineer → Saved AI metadata (ai), then explicit Manual WAP → model versions / builds / audits / publications and DataOps. Refresh history: Managed Marts → runs. This is not arbitrary snapshot rollback. Other paths: Jobs → run / step history; Database dbt → runs; Jobs / Stages / Managed Marts → schedules and ticks.');
 dbAdminNode('p','',guide,'Saved QUEUED/RUNNING is not current health. Committed and cleanup are separate. Old raw engine last 50 and Pawprints historical samples (at most 100) are not imported into DB audit.');
 const contextChanged=async()=>{
  const next={scope:scope.value,role:role.value,database:database.value};
  dbAuditPurge();dbAudit.scope=next.scope;dbAudit.role=next.role;dbAudit.database=next.database;
  dbAuditChoices();await dbAuditOpen();
 };
 for(const node of [scope,role,database])node.onchange=contextChanged;
 for(const [node,key] of [[from,'from'],[to,'to'],[source,'source'],[search,'search'],[state,'state'],[limit,'limit']])node.oninput=()=>{dbAudit.draft[key]=node.value;dbAuditResultStatus();};
 filters.addEventListener?.('toggle',()=>{dbAudit.filtersOpen=filters.open;});
 dbAuditSelectOptions('db-audit-source',[{value:'',label:'All sources',labelKey:'All sources'}],'','');
 dbAuditRenderApplied();dbAuditPaging();
}
