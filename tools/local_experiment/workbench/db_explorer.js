'use strict';
// Explorer details: SQL actions stay with the title; copy, export and catalog removal sit apart with their conditions.
Object.assign(i18n.ja,{
 'Saved export':'保存済みの書き出し','Saved table operations':'保存済みのテーブル操作','Saved clone':'保存済みの複製','Saved catalog removal':'保存済みの登録解除',
 'Close form':'フォームを閉じる','Operation details':'操作の詳細','More about catalog removal':'登録解除の補足',
 'Removes the whole TABLE registration and all snapshots. Data and metadata files are kept. No automatic Undrop.':'TABLE 全体と全スナップショットの登録を解除します。データとメタデータのファイルは保持します。自動 Undrop はありません。',
 'Table management':'テーブルの管理','Copy and export':'複製と書き出し',
 'Clone opens a form to copy a snapshot into a new sandbox table. Export starts immediately in the selected file format.':'複製はフォームを開き、スナップショットを新しいサンドボックスのテーブルへコピーします。書き出しは選択した形式ですぐに開始します。',
 'Remove from catalog':'カタログからの登録解除',
 'Removes only the catalog registration. Data files are kept. You confirm the exact table name before sending.':'カタログ登録だけを解除し、データファイルは保持します。送信前に正確なテーブル名で確認します。'
});
for(const key of Object.keys(i18n.ja))if(!i18n.en[key])i18n.en[key]=key;
function dbManageInstall(){
 if(dbManageInstall.done)return;dbManageInstall.done=true;
 const section=document.createElement('details');section.open=false;section.id='db-table-manage';section.className='db-manage table-management';section.setAttribute('aria-labelledby','db-table-manage-title');
 const title=document.createElement('summary');title.id='db-table-manage-title';ui(title,'Table management');section.append(title);
 for(const [id,heading,help,controls,extra] of [
  ['db-manage-copy','Copy and export','Clone opens a form to copy a snapshot into a new sandbox table. Export starts immediately in the selected file format.',['clone-table-btn','export-table-format','export-table-btn'],''],
  ['db-manage-remove','Remove from catalog','Removes only the catalog registration. Data files are kept. You confirm the exact table name before sending.',['drop-table-btn'],' db-manage-danger']]){
  const box=document.createElement('div');box.id=id;box.className='db-manage-group'+extra;
  const h=document.createElement('h4');ui(h,heading);const p=document.createElement('p');p.className='help';ui(p,help);
  const bar=document.createElement('div');bar.className='toolbar';bar.append(...controls.map($));
  const reason=document.createElement('p');reason.id=id+'-reason';reason.className='db-manage-reason';reason.setAttribute('role','status');
  box.append(h,p,bar,reason);section.append(box);
 }
 // Receipts and opened forms follow the group that started them.
 section.append(...['p35-table-export','db-clone-panel','db-drop-panel'].map($).filter(node=>node?.parentElement));
 const recovery=dataopsNode('div','db-manage-recovery',$('table-operations-body')||$('database-details'));recovery.className='toolbar db-manage-recovery';recovery.setAttribute('role','group');recovery.setAttribute('aria-labelledby','db-manage-recovery-title');
 dataopsNode('span','db-manage-recovery-title',recovery,'Saved table operations').className='visually-hidden';
 for(const [kind,label] of [['clone','Saved clone'],['drop','Saved catalog removal'],['export','Saved export']]){
  const button=dataopsButton(recovery,'db-'+kind+'-recovery',label,()=>{const op=kind==='export'?dataopsExport:kind==='clone'?dbClone.op:dbDrop.op;if(op?.principal!==dbPrincipal)return;dbManageOpen(kind);$(kind==='export'?'p35-export-receipt':'db-'+kind+'-observe').focus();});
  button.hidden=true;
 }
 ($('explore-management')||$('database-details')).append(section);
 $('p35-export-receipt').tabIndex=-1;
 for(const kind of ['clone','drop']){
  const panel=$('db-'+kind+'-panel');panel.open=false;
  for(const ids of [['refresh','submit'],['observe','cancel','new']]){
   const bar=dataopsNode('div','db-'+kind+'-'+ids[0]+'-actions',panel);bar.className='toolbar';$('db-'+kind+'-'+ids[0]).before(bar);bar.append(...ids.map(id=>$('db-'+kind+'-'+id)));
  }
  dataopsButton(panel,'db-'+kind+'-close','Close form',()=>{panel.open=false;$('db-'+kind+'-summary').focus();});
 }
 // The Lineage tab is the single entry point; the duplicate header shortcut stays wired but hidden.
 $('explorer-open-lineage').hidden=true;
 dbManageSync();
}
function dbManageOpen(kind){
 dbManageSync();$('db-table-manage').open=true;
 for(const name of ['clone','drop'])$('db-'+name+'-panel').open=name===kind;
}
function dbManageSync(){
 if(!dbManageInstall.done)return;
 const object=dbObject,section=$('db-table-manage');
 const selection=dataopsRefKey(dataopsRef(object));
 if(dbManageInstall.selection!==selection){section.open=false;for(const kind of ['clone','drop'])$('db-'+kind+'-panel').open=false;dbManageInstall.selection=selection;}
 let saved=false;
 for(const [kind,op,label] of [['clone',dbClone.op,'Saved clone'],['drop',dbDrop.op,'Saved catalog removal']]){
  const button=$('db-'+kind+'-recovery'),own=op?.principal===dbPrincipal;button.hidden=!own;
  if(own){saved=true;ui(button,'{text}',()=>({text:t(label)+': '+t((kind==='drop'?op.state:dataopsState(op))||'UNKNOWN')}));}
 }
 const exportOwn=dataopsExport?.principal===dbPrincipal;const exportButton=$('db-export-recovery');exportButton.hidden=!exportOwn;
 if(exportOwn)ui(exportButton,'{text}',()=>({text:t('Saved export')+': '+t(dataopsState(dataopsExport)||'UNKNOWN')}));
 $('db-manage-recovery').hidden=!saved&&!exportOwn;section.hidden=!object&&!saved&&!exportOwn;
 // A cleared object (role, grant or context change) must not leave its name as the current target.
 const title=$('table-title');if(!object&&(['true','cleared'].includes(title.dataset.object)||title.textContent===t('Table Inspector'))){const d=dbDatabases.find(d=>d.id===dbExplorer.database_id),s=dbExplorer.schemas.find(s=>s.id===dbExplorer.schema_id);title.textContent=s?.display_name||d?.display_name||t('Data Explorer');title.dataset.object='cleared';}
 const copy=$('db-manage-copy-reason'),remove=$('db-manage-remove-reason');
 if(!object||dbExplorerReason()){ui(copy,'');ui(remove,'');return;}
 const exportable=dbExplorerCanOpen()&&object.kind==='TABLE'&&!!dataopsRef(object)&&object.capabilities?.export!==false;
 const reasons=[...new Set([dbCloneEligible()?'':'Clone requires an engineer or administrator and a readable TABLE.',exportable?'':object.kind==='VIEW'?'View export is unsupported. Select a stable readable table.':'Select a stable readable table.'].filter(Boolean))];
 ui(copy,'{text}',()=>({text:reasons.map(key=>t(key)).join(' ')}));
 const drop=dbDropReason();ui(remove,'{text}',()=>({text:drop?t(drop):''}));
 const operationSummary=$('table-operations-summary');
 if(operationSummary){const count=['db-clone-recovery','db-drop-recovery','db-export-recovery'].filter(id=>$(id)&&!$(id).hidden).length;operationSummary.textContent=t('{count} saved table operations',{count});}
}

// Saved Lineage owns its transient state; adapters read current Workbench authority.
Object.assign(i18n.ja,{
 'Saved lineage':'保存済みリネージュ','Refresh saved lineage':'保存済みリネージュを更新',
 'Lineage requires data_engineer or admin.':'リネージュにはデータ作成者または管理者ロールが必要です。',
 'Select a readable TABLE or VIEW in Explorer.':'Explorer で読取可能な TABLE または VIEW を選択してください。',
 'Visible saved records only; catalog completeness is not established.':'表示権限のある保存記録のみです。カタログ全体の依存関係を示すものではありません。',
 'Confirmed saved evidence':'確認済みの保存証跡','Declared model references':'モデル定義の参照',
 'Model definitions describe dependencies between objects. Pinned runtime versions appear in Confirmed saved evidence.':'モデル定義はオブジェクト間の依存関係です。実行時の固定版は『確認済みの保存証跡』に表示します。',
 'Ref':'Ref','Object ID':'オブジェクト ID','Incarnation':'世代',
 'No visible saved evidence for this object.':'このオブジェクトに表示可能な保存証跡はありません。',
 'Refresh to read saved lineage for the selected object.':'更新すると、選択中のオブジェクトの保存済みリネージュを取得します。',
 'Bounded visible coverage':'表示可能な記録の一部を表示','Current version':'現在の版','Historical version':'過去の版',
 'Snapshot':'スナップショット','VIEW version':'VIEW の版','Model':'モデル',
 'Saved plan':'保存済みプラン','Planned model':'計画中のモデル','WAP version':'WAP の版',
 'WAP build':'WAP ビルド','WAP publication':'WAP 公開','Native dbt graph':'ネイティブ dbt グラフ',
 'Native dbt publication':'ネイティブ dbt 公開','Saved model definition':'保存済みモデル定義',
 'Reading saved lineage…':'保存済みリネージュを取得中…',
 'Saved lineage is unavailable. Refresh Explorer context and try again.':'保存済みリネージュを取得できません。Explorer のコンテキストを更新して再度お試しください。'
});
for(const key of Object.keys(i18n.ja))i18n.en[key]=key;
const dbLineage=createSavedLineagePanel({
 explorer:{
  get state(){return dbExplorer;},get databases(){return dbDatabases;},get object(){return dbObject;},
  get selectionGeneration(){return dbSelectionGeneration;},get objectEpoch(){return dbObjectEpoch;},
  canOpen:()=>dbExplorerCanOpen(),catalogScope:()=>dbCatalogScope(),denied:error=>dbExplorerDenied(error)
 },
 authority:{
  get principal(){return dbPrincipal;},get role(){return currentRole;},get authorizationVersion(){return dbAuthorizationVersion;},
  roleVersion:role=>dbRoleVersion(role),roleWithinHeader:role=>dbRoleWithinHeader(role)
 },
 request:{
  ref:object=>dataopsRef(object),refKey:ref=>dataopsRefKey(ref),freeze:value=>dataopsFreeze(value),clone:value=>dataopsClone(value),
  authError:error=>dataopsAuthError(error),runAllowed:(run,items)=>dbRunAllowed(run,items),
  readInventory:(role,current)=>dbReadInventory(role,current),inventoryCurrent:items=>dbInventoryCurrent(items),
  api:(path,role)=>dbAPI(path,role),applyInventory:(items,role)=>dbApplyInventory(items,role)
 },
 view:{
  get databaseMode(){return databaseMode;},get activeTab(){return activeTab;},get workspacePane(){return dbWorkspacePane;},get activeInspector(){return activeInspector;},
  $,t,inspectorTabs,wireTabs,selectInspector:button=>dbSelectInspector(button),showWorkspace:pane=>dbShowWorkspace(pane),
  node:(...args)=>dataopsNode(...args),button:(...args)=>dataopsButton(...args)
 }
});
function dbLineageControls(){return dbLineage.controls();}
function dbLineageInvalidate(){return dbLineage.invalidate();}
function dbLineageRefresh(){return dbLineage.refresh();}
function dbLineageReason(){return dbLineage.reason();}
function dbOpenLineage(){return dbLineage.open();}

const dbProfileKey='shirokuma.table-profile.v1';
const dbProfile={revision:0,owner:null,handles:new Map(),op:null,busy:null,context:null,source:null,scope:'',snapshots:[],snapshot:'',notice:''};
Object.assign(i18n.ja,{
 'Lineage is unavailable in Database mode.':'Database モードではリネージュを利用できません。',
 'Aggregate all rows and all top-level columns in one frozen snapshot. Only top-level NULLs are counted, including nested types. A full scan can take several minutes.':'固定したスナップショットの全行・全トップレベル列を集計します。ネスト型を含め、トップレベルの NULL だけを数えます。全件走査には数分かかる場合があります。',
 'Refresh profile context and snapshots':'集計用コンテキストとスナップショットを更新',
 'Aggregate full snapshot':'スナップショット全体を集計',
 'New profile intent':'新しい集計要求',
 'Refresh profile context after selecting a readable TABLE.':'読取可能な TABLE を選択し、集計用コンテキストを更新してください。',
 'Reselect the original source TABLE and role, then Observe operation. Recovery uses the original key and immutable inputs; it never creates a new aggregation intent.':'元の参照元 TABLE とロールを選び直して「操作を照会」を選択してください。復旧には元のキーと固定済みの入力を使い、新しい集計要求は作成しません。',
 'Profile request could not be saved. No request was sent.':'集計要求を保存できません。要求は送信していません。',
 'Statistics are hidden until the original source and current access are verified.':'元の参照元と現在のアクセス権を確認するまで統計を表示しません。',
 'Full scan accepted. Observe manually or cancel using its operation ID.':'全件走査を受け付けました。操作 ID を使って手動で照会・取消できます。',
 'No snapshot yet: CURRENT will count the empty TABLE and verify that it remains empty.':'まだスナップショットがありません。CURRENT で空 TABLE を数え、空のままであることを確認します。',
 'Row count':'行数','Column count':'列数','NULL count':'NULL 数','NULL %':'NULL %'
});
for(const key of Object.keys(i18n.ja))i18n.en[key]=key;
function dbProfileScope(){return JSON.stringify([dbProfile.revision,dbPrincipal,currentRole,dbRoleVersion(currentRole),dbAuthorizationVersion,dbCatalogScope(),dbSelectionGeneration,dbObjectEpoch,dataopsRef(dbObject),activeTab,dbWorkspacePane]);}
function dbProfileEligible(){return dbExplorerCanOpen()&&dbObject?.kind==='TABLE'&&dbRoleWithinHeader(currentRole);}
function dbProfileContext(){
 const e=dbExplorer,d=dbDatabases.find(d=>d.id===e.database_id),s=e.schemas.find(s=>s.id===e.schema_id);
 if(!dbProfileEligible()||!d||!s)return null;
 return {database_id:d.id,schema_id:s.id,warehouse_id:'LOCAL',sql_catalog:d.sql_catalog,sql_schema:s.sql_name,selected_role:currentRole,binding_revision:d.binding_revision,generation_id:d.generation_id,grant_revision:d.grant_revision??null};
}
function dbProfileSaved(){return readStorage(dbProfileKey,{})?.owners||{};}
function dbProfileSync(){
 const p=dbProfile;if(p.owner===dbPrincipal)return;
 if(p.op){p.op.operation=null;p.op.protected=false;p.handles.set(p.op.principal,p.op);}
 p.owner=dbPrincipal;++p.revision;
 const live=p.handles.get(dbPrincipal),saved=live||dbProfileSaved()[dbPrincipal];
 p.op=saved?.key&&saved.path==='/api/v1/table-profiles'&&saved.payload?.source&&saved.payload?.context?
  live||{...saved,context:dataopsFreeze(saved.payload.context),payload:dataopsFreeze(saved.payload),operation:null,protected:false}:null;
 if(p.op){p.op.operation=null;p.op.protected=false;p.handles.set(dbPrincipal,p.op);}
 p.busy=null;p.context=null;p.source=null;p.scope='';p.snapshot=p.op?.payload.snapshot_id||'';p.snapshots=[];p.notice='';
}
function dbProfilePersist(op=dbProfile.op){
 try{
  const owners=dbProfileSaved();
  if(op){owners[op.principal]={principal:op.principal,key:op.key,id:op.id||null,path:op.path,payload:op.payload,state:op.state,ambiguous:!!op.ambiguous,childUnknown:!!op.childUnknown,sequence:op.sequence||0};dbProfile.handles.set(op.principal,op);}
  else {delete owners[dbPrincipal];dbProfile.handles.delete(dbPrincipal);}
  localStorage.setItem(dbProfileKey,JSON.stringify({owners}));return true;
 }catch{return false;}
}
function dbProfileHeld(op){return !!op&&(op.ambiguous||op.childUnknown||!dataopsTerminal(op));}
function dbProfileAuthorized(op){return !!op&&op.principal===dbPrincipal&&dbProfileEligible()&&dataopsSameContext(op.context,dbProfileContext())&&dataopsRefKey(op.payload.source)===dataopsRefKey(dataopsRef(dbObject))&&dataopsExportAuthorized(op);}
function dbProfileCanObserve(op){return !!op&&op.principal===dbPrincipal&&dbRoleWithinHeader(op.context.selected_role)&&dataopsExportAuthorized(op)&&dataopsCanObserve(op);}
function dbProfileCanCancel(op){return !!op&&op.principal===dbPrincipal&&dbRoleWithinHeader(op.context.selected_role)&&!op.ambiguous&&!op.childUnknown&&dataopsCanCancel(op);}
function dbProfileInvalidate(){
 const p=dbProfile;++p.revision;p.context=null;p.source=null;p.scope='';p.snapshots=[];p.snapshot=p.op?.payload.snapshot_id||'';p.notice='';
 if(p.op){p.op.operation=null;p.op.protected=false;}
 $('table-profile-results')?.replaceChildren();
 $('table-null-results')?.replaceChildren();
 const nullHint=$('table-null-hint');if(nullHint){ui(nullHint,'Run Profile to show NULL counts for this snapshot.');nullHint.hidden=false;}
}
function dbProfileMount(){
 const root=$('table-profile-panel-body')||$('inspect-profile');
 if(root.children.length&&Array.from(root.children).includes($('table-profile-run')?.parentElement))return;
 root.replaceChildren();
 dataopsNode('p','',root,'Aggregate all rows and all top-level columns in one frozen snapshot. Only top-level NULLs are counted, including nested types. A full scan can take several minutes.').className='help';
 dataopsNode('p','table-profile-source',root).className='status-line';
 const fields=dataopsNode('div','',root);fields.className='job-fields';
 const snapshot=dataopsField(fields,'table-profile-snapshot','Snapshot','select');
 snapshot.onchange=()=>{dbProfile.snapshot=dbProfile.op?dbProfile.op.payload.snapshot_id||'':snapshot.value;dbProfileControls();};
 const toolbar=dataopsNode('div','',root);toolbar.className='toolbar';
 dataopsButton(toolbar,'table-profile-refresh','Refresh profile context and snapshots',dbProfileRefresh);
 dataopsButton(toolbar,'table-profile-run','Aggregate full snapshot',()=>dbProfileRun());
 dataopsButton(toolbar,'table-profile-observe','Observe operation',()=>dbProfileRun(true));
 dataopsButton(toolbar,'table-profile-cancel','Cancel operation',()=>dbProfileRun(true,true));
 dataopsButton(toolbar,'table-profile-new','New profile intent',()=>{
  const p=dbProfile;if(p.busy||p.op?.pending||!p.op||dbProfileHeld(p.op))return;
  p.op=null;dbProfilePersist();dbProfileInvalidate();dbProfileControls();
 });
 dataopsNode('div','table-profile-status',root).setAttribute('role','status');
 dataopsNode('div','table-profile-results',root).setAttribute('aria-live','polite');
}
function dbProfileTypeText(type,depth=0){
 if(typeof type==='string')return type;
 if(!type||typeof type!=='object'||Array.isArray(type)||depth>=20)return 'Unknown type';
 const nested=value=>dbProfileTypeText(value,depth+1);
 if(type.type==='list'||type.type==='array')return type.type+'<'+nested(type.element??type.items)+'>';
 if(type.type==='map')return 'map<'+nested(type.key)+', '+nested(type.value)+'>';
 if(type.type==='struct'||type.type==='row'){
  if(!Array.isArray(type.fields))return 'Unknown type';
  return type.type+'<'+type.fields.map(field=>field&&typeof field.name==='string'?field.name+': '+nested(field.type):'Unknown field').join(', ')+'>';
 }
 return 'Unknown type';
}
function dbProfileControls(){
 if(!$('catalog-database'))return;
 dbProfileSync();dbProfileMount();
 if(dbProfile.op&&dbProfile.snapshot!==(dbProfile.op.payload.snapshot_id||'')){
  ++dbProfile.revision;dbProfile.snapshot=dbProfile.op.payload.snapshot_id||'';
  dbProfile.op.operation=null;dbProfile.op.protected=false;
 }
 const p=dbProfile,op=p.op,scope=dbProfileScope(),eligible=dbProfileEligible(),held=dbProfileHeld(op),busy=!!p.busy||!!op?.pending;
 const button=inspectorTabs.find(b=>b.dataset.inspect==='profile');button.disabled=!eligible;button.title=eligible?'':t('Refresh profile context after selecting a readable TABLE.');
 wireTabs(inspectorTabs,dbSelectInspector);
 if(p.scope&&p.scope!==scope){p.context=null;p.source=null;p.scope='';p.snapshots=[];p.snapshot='';p.notice='';}
 const ready=eligible&&p.scope===scope&&!!p.context;
 const visible=op?.protected&&op.receiptScope===scope&&op.receiptSnapshot===p.snapshot&&dbProfileAuthorized(op);
 if(op&&!visible){op.operation=null;op.protected=false;}
 if(op)p.snapshot=op.payload.snapshot_id||'';
 dataopsOptions($('table-profile-snapshot'),ready?p.snapshots.map(id=>[id,id]):[],p.snapshot,'CURRENT (freeze at acceptance)');
 $('table-profile-snapshot').value=p.snapshot;
 $('table-profile-snapshot').disabled=busy||!!op||!ready;
 $('table-profile-refresh').disabled=busy||!eligible;
 $('table-profile-run').disabled=busy||!!op||!ready;
 $('table-profile-observe').disabled=busy||!dbProfileCanObserve(op);
 $('table-profile-cancel').disabled=busy||!dbProfileCanCancel(op);
 $('table-profile-new').disabled=busy||!op||held;
 $('table-profile-source').textContent=ready?[dbProfileContext().sql_catalog,dbProfileContext().sql_schema,dbObject.physical_name,currentRole].join(' / '):'';
 const status=$('table-profile-status');status.replaceChildren();
 if(op){dataopsNode('p','',status,'Operation ID').append(': '+(op.id||'UNKNOWN'));dataopsNode('p','',status,'Status').append(': '+t(op.state||'UNKNOWN'));dataopsNode('p','',status,'Idempotency key').append(': '+op.key);}
 if(p.notice)status.append(t(p.notice));
 if(!ready)dataopsNode('p','',status,'Refresh profile context after selecting a readable TABLE.');
 if(held&&op.id)dataopsNode('p','',status,'Full scan accepted. Observe manually or cancel using its operation ID.');
 if(op?.ambiguous||op?.childUnknown||op?.state==='UNKNOWN')dataopsNode('p','',status,'Outcome unknown. Observe with the saved key; do not submit again.');
 if(op&&!visible)dataopsNode('p','',status,'Reselect the original source TABLE and role, then Observe operation. Recovery uses the original key and immutable inputs; it never creates a new aggregation intent.');
 if(op&&!op.id)dataopsNode('p','',status,'Cancellation is unavailable until an acceptance ID is received. Recover the original request first.');
 const out=$('table-profile-results');out.replaceChildren();
 const result=visible?op.operation?.result:null;
 const nullOut=$('table-null-results'),nullHint=$('table-null-hint');if(nullOut)nullOut.replaceChildren();
 if(result?.row_count!==undefined){
  dataopsNode('p','',out,'Row count').append(': '+result.row_count);dataopsNode('p','',out,'Column count').append(': '+String(result.column_count));
  dataopsNode('p','',out,'Snapshot').append(': '+(result.source?.snapshot??'CURRENT'));
  const grid=dataopsNode('div','',out);grid.className='tablewrap';
  table(grid,['Column','Type','NULL count','NULL %'],result.columns.map(c=>[c.name,dbProfileTypeText(c.type),c.null_count,c.null_percent===null?'—':c.null_percent+'%']),true);
  if(nullOut){const nullGrid=dataopsNode('div','',nullOut);nullGrid.className='tablewrap';table(nullGrid,['Column','NULL count','NULL %'],result.columns.map(c=>[c.name,c.null_count,c.null_percent===null?'—':c.null_percent+'%']),true);}
  if(nullHint)nullHint.hidden=true;
 }else if(op){dataopsNode('p','',out,'Statistics are hidden until the original source and current access are verified.');if(nullHint){ui(nullHint,'Statistics are hidden until the original source and current access are verified.');nullHint.hidden=false;}}
 else if(nullHint){ui(nullHint,'Run Profile to show NULL counts for this snapshot.');nullHint.hidden=false;}
}
async function dbProfileRefresh(){
 dbProfileControls();const p=dbProfile;if(p.busy||!dbProfileEligible())return;
 const source=dataopsFreeze(dataopsClone(dataopsRef(dbObject))),role=currentRole,owner=dbPrincipal,token={};let scope=dbProfileScope();
 p.busy=token;p.context=null;p.scope='';p.notice='';if(p.op){p.op.operation=null;p.op.protected=false;}dbProfileControls();
 try{
  const items=await dbReadInventory(role);if(scope!==dbProfileScope()||owner!==dbPrincipal||!dbInventoryCurrent(items))return;
  dbApplyInventory(items,role);scope=dbProfileScope();
  const schemas=await dbAPI('/api/v1/databases/'+encodeURIComponent(source.database_id)+'/schemas',role);if(scope!==dbProfileScope())return;
  const schema=schemas.items?.find(s=>s.id===source.schema_id&&s.capabilities?.query===true);
  if(!schema)throw Error('Refresh profile context after selecting a readable TABLE.');
  const reply=await dbAPI('/api/v1/objects/'+encodeURIComponent(source.object_id)+'?detail=snapshots',role);if(scope!==dbProfileScope())return;
  const currentItems=await dbReadInventory(role);if(scope!==dbProfileScope()||!dbInventoryCurrent(currentItems))return;
  dbApplyInventory(currentItems,role);if(scope!==dbProfileScope()||!dbProfileEligible())return;
  p.source=source;p.context=dataopsFreeze(dbProfileContext());p.scope=scope;p.snapshot=p.op?.payload.snapshot_id||'';
  p.snapshots=[...new Set((reply.snapshots||[]).map(s=>s.snapshot_id).filter(id=>typeof id==='string'&&/^[0-9]+$/.test(id)))];
  if(!p.snapshots.length)p.notice='No snapshot yet: CURRENT will count the empty TABLE and verify that it remains empty.';
 }catch(error){if(owner===dbPrincipal&&scope===dbProfileScope()){p.notice=error.message;dbProfileInvalidate();p.notice=error.message;if(dataopsAuthError(error))dbExplorerDenied(error);}}
 finally{if(p.busy===token)p.busy=null;dbProfileControls();}
}
async function dbProfileRun(observe=false,cancel=false){
 dbProfileControls();const p=dbProfile;if(p.busy||p.op?.pending)return;
 if(!observe){
  if($('table-profile-run').disabled)return;
  const payload=dataopsFreeze({context:dataopsClone(p.context),source:dataopsClone(p.source),snapshot_id:p.snapshot||null});
  p.op={principal:dbPrincipal,key:crypto.randomUUID(),id:null,path:'/api/v1/table-profiles',payload,context:payload.context,state:'ACTIVE',ambiguous:false,childUnknown:false,sequence:0};
 }
 const op=p.op;if(!op||!(cancel?dbProfileCanCancel(op):observe?dbProfileCanObserve(op):dbProfileAuthorized(op))||observe&&(cancel?$('table-profile-cancel').disabled:$('table-profile-observe').disabled))return;
 op.sequence++;
 if(!dbProfilePersist(op)){if(!observe)p.op=null;p.notice='Profile request could not be saved. No request was sent.';dbProfileControls();return;}
 const token={},scope=dbProfileScope(),snapshot=p.snapshot;
 const current=()=>p.op===op&&op.principal===dbPrincipal&&p.busy===token&&scope===dbProfileScope()&&snapshot===p.snapshot;
 op.pending=token;p.busy=token;p.notice='';op.operation=null;op.protected=false;dbProfileControls();
 const accept=operation=>{const visible=!cancel&&current()&&dbProfileAuthorized(op);dataopsAccept(op,operation,visible);op.childUnknown=typeof operation.child_unknown==='boolean'?operation.child_unknown:Array.isArray(operation.queries)?operation.queries.some(q=>q.state==='UNKNOWN'):op.childUnknown===true;op.ambiguous=op.ambiguous||op.childUnknown;op.receiptScope=visible?scope:null;op.receiptSnapshot=snapshot;};
 try{
  if(cancel){
   const reply=await dataopsRequest('/api/v1/dataops/'+encodeURIComponent(op.id)+'/cancel',op.context,{});
   accept(reply.operation);return;
  }
  // Recheck live authorization before every request, including cached inventory.
  const items=await dbReadInventory(op.context.selected_role);if(!current()||!dbInventoryCurrent(items))return;
  dbApplyInventory(items,op.context.selected_role);if(!current()||!(observe?dbProfileCanObserve(op):dbProfileAuthorized(op)))return;
  const reply=op.id?await dataopsRequest('/api/v1/dataops/'+encodeURIComponent(op.id)+(cancel?'/cancel':''),op.context,cancel?{}:undefined):await dataopsRequest(op.path,op.context,op.payload,op.key);
  accept(reply.operation);
  if(current()){
   const after=await dbReadInventory(op.context.selected_role);if(!current()||!dbInventoryCurrent(after)){op.operation=null;op.protected=false;return;}
   dbApplyInventory(after,op.context.selected_role);if(!current()||!dbProfileAuthorized(op)){op.operation=null;op.protected=false;}
  }
 }catch(error){
  const terminal=dataopsTerminal(op);
  if(error.observation)accept(error.observation);
  else if(!terminal&&!dataopsRejectBeforeAdmission(op,error))op.ambiguous=true;
  if(cancel&&error.code==='UNKNOWN_RECONCILIATION_REQUIRED'){op.ambiguous=true;op.childUnknown=true;}
  op.operation=null;op.protected=false;
  if(current()){p.notice=error.message;if(dataopsAuthError(error)){dbProfileInvalidate();p.notice=error.message;dbExplorerDenied(error);}}
 }finally{
  if(op.pending===token)op.pending=null;
  if(p.busy===token)p.busy=null;
  dbProfilePersist(op);dbProfileControls();
 }
}

// Clone owns its SANDBOX context and immutable saved intent, separate from SQL.
const dbCloneKey='shirokuma.table-clone.v1';
const dbClone={opened:false,role:'',owner:null,handles:new Map(),source:null,context:null,scope:'',snapshots:[],op:null,busy:false,notice:''};
Object.assign(i18n.ja,{
 'Physical snapshot copy':'スナップショットの物理コピー',
 'Copies snapshot rows into a NEW table. No overwrite, zero copy, history or partition/property copy.':'スナップショットの行を新しいテーブルに物理コピーします。上書き・ゼロコピー・履歴・パーティションやプロパティの複製は行いません。',
 'Refresh clone context and snapshots':'複製用コンテキストとスナップショットを更新',
 'Create snapshot copy':'スナップショットのコピーを作成',
 'New clone intent':'新しい複製要求',
 'Clone requires an engineer or administrator and a readable TABLE.':'複製にはデータ作成者または管理者ロールと、読取可能な TABLE が必要です。',
 'Clone requires a writable SANDBOX in the same Database.':'複製先には同じ Database の書込可能な SANDBOX が必要です。',
 'No integer snapshot is available. This source cannot be cloned.':'整数のスナップショットがありません。この参照元は複製できません。',
 'Refresh clone context after source or role changes.':'参照元またはロールを変更した後は複製用コンテキストを更新してください。',
 'Cancellation stops remaining work; it does not roll back a copied table.':'取消は残りの処理を停止します。コピー済みテーブルは元に戻りません。',
 'Clone request could not be saved. No request was sent.':'複製要求を保存できません。要求は送信していません。',
 'CURRENT (freeze at acceptance)':'CURRENT（受付時に固定）',
 'Use 1–31 characters: start with a lowercase letter, then lowercase letters, digits or underscores. Reserved names: system, information_schema, tpch, tpcds.':'1〜31文字で、先頭は英小文字、以降は英小文字・数字・アンダースコアを使用してください。予約名 system、information_schema、tpch、tpcds は使用できません。',
 'Enter a table name that follows the rule above.':'上記のルールに従ったテーブル名を入力してください。',
 'COPIED_UNVERIFIED':'コピー済み・未検証','VERIFIED':'検証済み','PENDING':'処理待ち',
 'Original clone request':'元の複製要求','Warning':'警告',
 'After reload or a selection change, reselect the original source TABLE in Explorer, then select Observe operation to recover the original request with its saved key and inputs.':'再読み込み後や選択を変更した後は、Explorer で元と同じ参照元 TABLE を選び直し、「操作を照会」を選択して、保存された元のキーと入力で要求を復旧してください。',
 'Cancellation is unavailable until an acceptance ID is received. Recover the original request first.':'受付 ID を受信するまで取消はできません。まず元の要求を復旧してください。',
 'Clone details are hidden until the original owner and current access are confirmed. After reload or a selection change, reselect the original source TABLE in Explorer, then select Observe operation to display the saved clone receipt.':'元の所有者と現在のアクセス権を確認するまで複製の詳細は表示されません。再読み込み後や選択を変更した後は、Explorer で元と同じ参照元 TABLE を選び直し、「操作を照会」を選択して保存済みの複製結果を表示してください。'
 ,'Copying can take several minutes. Refresh the original operation manually; do not submit another copy.':'コピーには数分かかる場合があります。元の操作を手動で照会し、別のコピーを送信しないでください。'
 ,'Drop is unavailable in Database mode. Use columns, preview, snapshots or DDL to inspect an authorized table.':'Database モードでは削除できません。列・プレビュー・スナップショット・DDL で権限のあるテーブルを確認してください。'
});
for(const key of Object.keys(i18n.ja))i18n.en[key]=key;
function dbCloneScope(){return JSON.stringify([dbPrincipal,currentRole,dbClone.role,dbRoleVersion(dbClone.role),dbAuthorizationVersion,dbCatalogScope(),dataopsRef(dbObject)]);}
function dbCloneEligible(){return dbExplorerCanOpen()&&dbObject?.kind==='TABLE'&&['data_engineer','admin'].includes(dbClone.role)&&dbRoleWithinHeader(dbClone.role);}
// Persist only this feature's owner handles; never store protected result data.
function dbCloneSaved(){
 const saved=readStorage(dbCloneKey,null);
 return saved?.owners||((saved?.principal&&saved?.payload)?{[saved.principal]:saved}:{});
}
function dbCloneRestore(saved){
 if(!saved||typeof saved.key!=='string'||saved.path!=='/api/v1/table-clones'||!saved.payload?.context||!saved.payload?.source||!saved.payload?.destination)return null;
 return {...saved,context:dataopsFreeze(saved.payload.context),payload:dataopsFreeze(saved.payload),operation:null,protected:false,
  sequence:saved.sequence||0,ambiguous:saved.ambiguous===true||!['FINISHED','FAILED','CANCELED','REJECTED'].includes(saved.state)};
}
function dbCloneSync(){
 const p=dbClone;if(p.owner===dbPrincipal)return;
 if(p.op){p.op.operation=null;p.op.protected=false;p.handles.set(p.op.principal,p.op);}
 p.owner=dbPrincipal;p.op=p.handles.get(dbPrincipal)||dbCloneRestore(dbCloneSaved()[dbPrincipal]);
 if(p.op)p.handles.set(dbPrincipal,p.op);
 p.busy=false;p.source=null;p.context=null;p.scope='';p.snapshots=[];p.notice='';
 p.role=p.op?.context.selected_role||(['data_engineer','admin'].includes(currentRole)?currentRole:'data_engineer');
 $('db-clone-name').value='';
}
function dbClonePersist(op=dbClone.op){
 try{
  const owners=dbCloneSaved();
  if(op){owners[op.principal]={principal:op.principal,key:op.key,id:op.id||null,payload:op.payload,path:op.path,state:dataopsState(op)||'UNKNOWN',ambiguous:op.ambiguous===true,childUnknown:op.childUnknown===true,sequence:op.sequence||0};dbClone.handles.set(op.principal,op);}
  else {delete owners[dbPrincipal];dbClone.handles.delete(dbPrincipal);}
  localStorage.setItem(dbCloneKey,JSON.stringify({owners}));return true;
 }catch{return false;}
}
function dbCloneUnknown(op){return !!op&&(op.ambiguous===true||op.childUnknown===true||dataopsState(op)==='UNKNOWN'||op.operation?.queries?.some(q=>q.state==='UNKNOWN'));}
function dbCloneHeld(op){return !!op&&(dbCloneUnknown(op)||!dataopsTerminal(op));}
function dbCloneCanObserve(op){return !!op&&op.principal===dbPrincipal&&dataopsExportAuthorized(op)&&dataopsCanObserve(op);}
function dbCloneCanCancel(op){return dbCloneCanObserve(op)&&!dbCloneUnknown(op)&&dataopsCanCancel(op);}
function dbCloneAuthorized(op){return dbCloneCanObserve(op)&&dbExplorerCanOpen()&&dataopsRefKey(dataopsRef(dbObject))===dataopsRefKey(op.payload.source);}
function dbCloneAccept(op,operation,visible){
 // Keep UNKNOWN child evidence even when protected query details are redacted.
 const childUnknown=Array.isArray(operation.queries)?operation.queries.some(q=>q.state==='UNKNOWN'):op.childUnknown===true;
 dataopsAccept(op,operation,visible);op.childUnknown=childUnknown;op.ambiguous=op.ambiguous||childUnknown;
 op.receiptScope=visible?dbCloneScope():null;
}
function dbCloneControls(){
 if(!$('db-clone-panel'))return;
 dbCloneSync();
 const p=dbClone,op=p.op,held=dbCloneHeld(op),busy=!!p.busy||!!op?.pending,ready=p.scope===dbCloneScope()&&!!p.context&&dbCloneEligible();
 dataopsOptions($('db-clone-role'),['data_engineer','admin'].filter(dbRoleWithinHeader).map(r=>[r,r]),p.role,'Execution role');
 if(!ready){p.source=null;p.context=null;p.snapshots=[];p.scope='';$('db-clone-source').textContent='';$('db-clone-context').textContent='';}
 dataopsOptions($('db-clone-snapshot'),ready?p.snapshots.map(id=>[id,id]):[],$('db-clone-snapshot').value&&ready?$('db-clone-snapshot').value:'','CURRENT (freeze at acceptance)');
 const protectedDisplay=!!op&&op.protected&&op.receiptScope===dbCloneScope()&&dbCloneAuthorized(op);
 if(op&&!protectedDisplay){op.operation=null;op.protected=false;}
 $('clone-table-btn').disabled=!dbCloneEligible()||busy||held;
 $('clone-table-btn').dataset.databaseUnavailable='false';
 $('clone-table-btn').title=dbCloneEligible()?'':t('Clone requires an engineer or administrator and a readable TABLE.');
 $('db-clone-panel').hidden=false;dbManageSync();
 const name=$('db-clone-name').value,validName=/^[a-z][a-z0-9_]{0,30}$/.test(name)&&!['system','information_schema','tpch','tpcds'].includes(name);
 $('db-clone-name').setAttribute('aria-invalid',String(!!name&&!validName));
 $('db-clone-name-invalid').hidden=!name||validName;
 ui($('db-clone-name-invalid'),'Enter a table name that follows the rule above.');
 $('db-clone-submit').disabled=busy||!!op||!ready||!p.snapshots.length||!validName;
 $('db-clone-refresh').disabled=busy||held||!dbCloneEligible();
 $('db-clone-observe').disabled=busy||!dbCloneCanObserve(op);
 ui($('db-clone-observe'),op&&!op.id?'Resend original request':'Look up clone operation');
 $('db-clone-cancel').disabled=busy||!dbCloneCanCancel(op);
 $('db-clone-new').disabled=busy||!op||held;
 for(const id of ['db-clone-name','db-clone-snapshot','db-clone-role'])$(id).disabled=busy||held;
 message('db-clone-notice',t((p.noticeScope===dbCloneScope()?p.notice:'')||(!ready?'Refresh clone context after source or role changes.':'')));
 const out=$('db-clone-receipt');out.replaceChildren();
 $('db-clone-resend-warning').hidden=!(op&&!op.id&&dbCloneCanObserve(op));
 if(!$('db-clone-resend-warning').hidden)$('db-clone-observe').setAttribute('aria-describedby','db-clone-resend-warning');else $('db-clone-observe').removeAttribute('aria-describedby');
 if(op){
  const line=(key,value)=>{const n=dataopsNode('p','',out);dataopsNode('strong','',n,key);n.append(': '+String(value??''));};
  if(protectedDisplay){
   line('Operation ID',op.id||'UNKNOWN');line('Idempotency key',op.key);line('Status',t(dataopsState(op)||'PENDING'));
   line('Accepted context',[op.context.sql_catalog,op.context.sql_schema,op.context.selected_role].join(' / '));
   line('Target table name',op.payload.destination.physical_name);line('Source',JSON.stringify(op.payload.source));
   if(op.operation){const r=op.operation.result;line('Committed',op.operation.committed);if(r){line('Status',t(r.phase));line('Snapshot',r.source?.snapshot);if(r.warning)line('Warning',r.warning);line('Target snapshot',r.target?.snapshot??'—');}}
  }else dataopsNode('p','',out,'Clone details are hidden until the original owner and current access are confirmed. After reload or a selection change, reselect the original source TABLE in Explorer, then select Observe operation to display the saved clone receipt.');
  if(held){
   dataopsNode('p','',out,'Copying can take several minutes. Refresh the original operation manually; do not submit another copy.');
   dataopsNode('p','',out,'After reload or a selection change, reselect the original source TABLE in Explorer, then select Observe operation to recover the original request with its saved key and inputs.');
   if(!op.id)dataopsNode('p','',out,'Cancellation is unavailable until an acceptance ID is received. Recover the original request first.');
  }
  if(dbCloneUnknown(op))dataopsNode('p','',out,'Outcome unknown. Observe with the saved key; do not submit again.');
 }
}
async function dbCloneRefresh(){
 dbCloneControls();if(dbClone.busy||dbClone.op?.pending||dbCloneHeld(dbClone.op)||!dbCloneEligible())return;
 const p=dbClone,source=dataopsFreeze(dataopsClone(dataopsRef(dbObject))),role=p.role,token={};let scope=dbCloneScope();
 p.busy=token;p.context=null;p.snapshots=[];p.notice='';dbCloneControls();
 try{
  const items=await dbReadInventory(role);if(scope!==dbCloneScope()||!dbInventoryCurrent(items))return;
  dbApplyInventory(items,role);scope=dbCloneScope();
  const data=await dbAPI('/api/v1/databases/'+encodeURIComponent(source.database_id)+'/schemas',role);
  if(scope!==dbCloneScope())return;
  const s=data.items?.find(s=>s.sql_name==='sandbox'&&s.capabilities?.write===true),d=items.find(d=>d.id===source.database_id);
  if(!s||!d)throw Error('Clone requires a writable SANDBOX in the same Database.');
  if(!data.items?.some(s=>s.id===source.schema_id&&s.capabilities?.query===true))throw Error('Clone requires an engineer or administrator and a readable TABLE.');
  const reply=await dbAPI('/api/v1/objects/'+encodeURIComponent(source.object_id)+'?detail=snapshots',role);
  if(scope!==dbCloneScope())return;
  // IDs remain decimal strings throughout; Number would round Iceberg IDs.
  const snapshots=(reply.snapshots||[]).map(s=>s.snapshot_id).filter(id=>typeof id==='string'&&/^[0-9]+$/.test(id));
  p.source=source;p.context=dataopsFreeze({database_id:d.id,schema_id:s.id,warehouse_id:'LOCAL',sql_catalog:d.sql_catalog,sql_schema:s.sql_name,selected_role:role,binding_revision:d.binding_revision,generation_id:d.generation_id,grant_revision:d.grant_revision??null});p.scope=scope;p.snapshots=snapshots;
  dataopsOptions($('db-clone-snapshot'),snapshots.map(id=>[id,id]),'','CURRENT (freeze at acceptance)');
  $('db-clone-source').textContent=[d.sql_catalog,dbExplorer.schemas.find(s=>s.id===source.schema_id)?.sql_name,dbObject.physical_name,JSON.stringify(source)].join(' / ');
  $('db-clone-context').textContent=[p.context.sql_catalog,p.context.sql_schema,p.context.selected_role].join(' / ');
  if(!snapshots.length){p.notice='No integer snapshot is available. This source cannot be cloned.';p.noticeScope=scope;}
 }catch(error){if(scope===dbCloneScope()){p.notice=error.message;p.noticeScope=scope;}}
 finally{if(p.busy===token)p.busy=false;dbCloneControls();}
}
async function dbCloneRun(observe=false,cancel=false){
 dbCloneControls();const p=dbClone;if(p.busy||p.op?.pending)return;
 if(!observe){
  if($('db-clone-submit').disabled)return;
  const payload=dataopsFreeze({context:dataopsClone(p.context),source:dataopsClone(p.source),destination:{database_id:p.context.database_id,schema_id:p.context.schema_id,physical_name:$('db-clone-name').value},snapshot_id:$('db-clone-snapshot').value||null});
  p.op={principal:dbPrincipal,key:crypto.randomUUID(),context:payload.context,payload,path:'/api/v1/table-clones',id:null,state:'ACTIVE',operation:null,sequence:0,ambiguous:false};
 }
 const op=p.op;if(!op||op.principal!==dbPrincipal||observe&&!(cancel?dbCloneCanCancel(op):dbCloneCanObserve(op)))return;
 op.sequence++;
 if(!dbClonePersist(op)){p.notice='Clone request could not be saved. No request was sent.';p.noticeScope=dbCloneScope();if(!observe){p.op=null;p.handles.delete(dbPrincipal);}dbCloneControls();return;}
 const token={},scope=dbCloneScope(),current=()=>p.op===op&&op.principal===dbPrincipal&&op.pending===token&&p.busy===token&&scope===dbCloneScope(),visible=()=>current()&&dbCloneAuthorized(op);
 op.pending=token;p.busy=token;p.notice='';dbCloneControls();
 try{
  const reply=op.id?await dataopsRequest('/api/v1/dataops/'+encodeURIComponent(op.id)+(cancel?'/cancel':''),op.context,cancel?{}:undefined):await dataopsRequest(op.path,op.context,op.payload,op.key);
  // A late reply may update only its original owner's saved handle.
  dbCloneAccept(op,reply.operation,visible());
 }catch(error){
  if(error.observation)dbCloneAccept(op,error.observation,visible());
  else if(!dataopsRejectBeforeAdmission(op,error))op.ambiguous=true;
  if(dataopsAuthError(error)){
   if(op.pending===token){op.operation=null;op.protected=false;}
   if(current()){
    dbInvalidateAccess(error,op.context.selected_role,op.context.database_id);
    p.source=null;p.context=null;p.scope='';p.snapshots=[];$('db-clone-name').value='';
   }
  }
  if(visible()){p.notice=error.message;p.noticeScope=scope;}
 }finally{
  if(op.pending===token)op.pending=null;
  if(p.busy===token)p.busy=false;
  dbClonePersist(op);dbCloneControls();
 }
}
function dbCloneInstall(){
 const p=dbClone;if(p.installed){p.owner=null;dbCloneSync();dbCloneControls();return;}p.installed=true;
 const root=dataopsNode('details','db-clone-panel',$('database-details'));root.open=false;
 dataopsNode('summary','db-clone-summary',root,'Physical snapshot copy');dataopsNode('p','',root,'Copies snapshot rows into a NEW table. No overwrite, zero copy, history or partition/property copy.');
 for(const id of ['source','context'])dataopsNode('p','db-clone-'+id,root).className='help';
 const fields=dataopsNode('div','',root);fields.className='job-fields';
 p.role=p.role||(['data_engineer','admin'].includes(currentRole)?currentRole:'data_engineer');
 const role=dataopsField(fields,'db-clone-role','Execution role','select',['data_engineer','admin'].filter(dbRoleWithinHeader).map(r=>[r,r]));role.value=p.role;
 role.onchange=()=>{p.role=role.value;p.scope='';p.context=null;dbCloneControls();};
 const name=dataopsField(fields,'db-clone-name','Target table name');name.oninput=dbCloneControls;
 name.setAttribute('maxlength','31');name.setAttribute('aria-describedby','db-clone-name-rule db-clone-name-invalid');
 dataopsNode('p','db-clone-name-rule',fields,'Use 1–31 characters: start with a lowercase letter, then lowercase letters, digits or underscores. Reserved names: system, information_schema, tpch, tpcds.').className='help';
 const invalid=dataopsNode('p','db-clone-name-invalid',fields);invalid.className='status-line';invalid.setAttribute('role','status');
 dataopsField(fields,'db-clone-snapshot','Snapshot','select').onchange=dbCloneControls;
 dataopsButton(root,'db-clone-refresh','Refresh clone context and snapshots',dbCloneRefresh);
 dataopsButton(root,'db-clone-submit','Create snapshot copy',()=>dbCloneRun());
 dataopsNode('p','',root,'Cancellation stops remaining work; it does not roll back a copied table.');
 const notice=dataopsNode('p','db-clone-notice',root);notice.setAttribute('role','status');
 const extra=dataopsNode('details','db-clone-operation-details',root);dataopsNode('summary','',extra,'Operation details');
 const receipt=dataopsNode('div','db-clone-receipt',extra);receipt.setAttribute('aria-live','polite');
 dataopsNode('p','db-clone-resend-warning',root,'Resending uses the original body and key. If the request was never accepted, it may start the operation.');
 dataopsButton(root,'db-clone-observe','Observe operation',()=>dbCloneRun(true));dataopsButton(root,'db-clone-cancel','Cancel operation',()=>dbCloneRun(true,true));
 dataopsButton(root,'db-clone-new','New clone intent',()=>{if(p.busy||p.op?.pending||!p.op||dbCloneHeld(p.op)||p.op.principal!==dbPrincipal)return;p.op=null;dbClonePersist();p.context=null;p.scope='';dbCloneControls();});
 p.owner=null;dbCloneSync();
 $('clone-table-btn').onclick=async()=>{dbManageOpen('clone');$('db-clone-summary').focus();dbCloneControls();await dbCloneRefresh();if(p.context&&$('db-clone-panel').open&&$('db-table-manage').open&&document.activeElement===$('db-clone-summary'))$('db-clone-name').focus();};
 for(let i=uiAttributes.length-1;i>=0;i--)if(uiAttributes[i].node===$('clone-table-btn')&&uiAttributes[i].attribute==='title')uiAttributes.splice(i,1);
 dbCloneControls();
}

// Whole TABLE unregister owns its preview and recovery handle independently.
const dbDropKey='shirokuma.table-drop.v1',dbDropMode='CATALOG_UNREGISTER_KEEP_DATA';
const dbDrop={revision:0,owner:null,handles:new Map(),op:null,preview:null,context:null,source:null,scope:'',busy:null,opened:false,notice:''};
Object.assign(i18n.ja,{
 'Remove from catalog (keep data)':'カタログから登録解除（データ保持）',
 'Removes the whole TABLE registration, including all snapshots. Data files and metadata files are retained. There is no automatic Undrop. VIEW, SQL and Job references are not rewritten. Dependency completeness is not established.':'全スナップショットを含む TABLE 全体の登録を解除します。データファイルとメタデータファイルは保持されます。自動 Undrop はありません。VIEW・SQL・Job の参照は書き換えません。依存関係の網羅性は確認できていません。',
 'Catalog removal requires current Admin, an explicit database grant, and a readable, writable TABLE in raw, sandbox, staging or marts.':'登録解除には現在の Admin データベースアクセスと raw・sandbox・staging・marts 内の読取・書込可能な TABLE が必要です。',
 'VIEW catalog removal is unavailable. Select a TABLE.':'VIEW の登録解除は利用できません。TABLE を選択してください。',
 'Refresh removal preview':'登録解除のプレビューを更新',
 'Type the exact physical table name':'物理テーブル名を大文字・小文字も含めて正確に入力',
 'I understand: remove registration and keep data':'登録を解除し、データを保持することを確認しました',
 'New removal intent':'新しい登録解除要求',
 'Refresh the preview before confirming removal.':'登録解除の確認前にプレビューを更新してください。',
 'Cancellation can prevent dispatch only. Once sent, the outcome remains uncertain until an acknowledgement; it cannot roll back removal.':'取消で送信前の処理を停止できます。送信後は応答を受け取るまで結果不明のままで、登録解除は元に戻せません。',
 'Removal details are hidden until current owner and Admin access are verified. Observe the saved request even if the source is gone or unselected.':'現在の所有者と Admin 権限を確認するまで詳細を表示しません。参照元が消失・未選択でも保存された元の要求を照会できます。',
 'Removal request could not be saved. No request was sent.':'登録解除要求を保存できません。要求は送信していません。',
 'Whole TABLE / all snapshots':'TABLE 全体／全スナップショット',
 'No current snapshot':'現在のスナップショットなし',
 'Preview digest':'プレビューのダイジェスト',
 'Identification details':'識別情報',
 'Before removing this registration, pause other applications and tools that could create or replace a TABLE with the same name. Catalog removal cannot atomically check the UUID or current snapshot.':'登録解除の前に、同名の TABLE を作成・置換する可能性がある他のアプリやツールを一時停止してください。カタログの登録解除では UUID や現在のスナップショットを不可分に照合できません。',
 'Acknowledged':'応答確認済み',
 'ACKNOWLEDGED':'登録解除の応答確認済み','PREPARED':'送信準備済み','SENT':'送信済み・応答未確認',
 'OUTCOME_UNKNOWN':'結果不明','POST_ACK_OBSERVATION_UNAVAILABLE':'登録解除済み・事後確認未完了',
 'REMOVAL_NOT_SENT_OR_REJECTED':'未送信または登録解除拒否'
 ,'CATALOG_REMOVED_RETIREMENT_UNPROVEN':'登録解除済み・元の登録行の退役確認未完了',
 'Original source Ref':'元の参照元 Ref','Table registration removed':'テーブルの登録を解除しました',
 'Removal is preparing. Observe manually; cancellation can prevent dispatch only.':'登録解除を準備しています。手動で照会してください。取消で停止できるのは送信前だけです。',
 'Removal was sent. Observe the original request; cancellation cannot establish stoppage or undo removal.':'登録解除を送信しました。元の要求を照会してください。取消で停止を証明したり登録解除を元に戻したりすることはできません。'
});
for(const key of Object.keys(i18n.ja))i18n.en[key]=key;
function dbDropScope(){return JSON.stringify([dbDrop.revision,dbPrincipal,currentRole,dbMaximumRole,dbAdmin.platform,dbAdmin.epoch,dbRoleVersion(currentRole),dbAuthorizationVersion,dbEpoch,dbCatalogScope(),dbSelectionGeneration,dbObjectEpoch,dataopsRef(dbObject),dbObject?.head,dbObject?.current_snapshot_id,activeTab,dbWorkspacePane]);}
function dbDropAdminAccess(revision){return Number.isInteger(revision)&&revision>0||revision===null&&dbAdmin.platform===true&&dbMaximumRole==='admin'&&dbRoleWithinHeader('admin');}
function dbDropReason(){
 if(dbObject?.kind==='VIEW')return 'VIEW catalog removal is unavailable. Select a TABLE.';
 const s=dbExplorer.schemas.find(s=>s.id===dbExplorer.schema_id),d=dbDatabases.find(d=>d.id===dbExplorer.database_id);
 if(currentRole!=='admin'||!dbRoleWithinHeader('admin')||!dbExplorerCanOpen()||dbObject?.kind!=='TABLE'||
   !s?.capabilities?.write||!['raw','sandbox','staging','marts'].includes(s.sql_name)||s.purpose!==s.sql_name||!dbDropAdminAccess(d?.grant_revision))
  return 'Catalog removal requires current Admin, an explicit database grant, and a readable, writable TABLE in raw, sandbox, staging or marts.';
 return '';
}
function dbDropContext(){
 if(dbDropReason())return null;
 const d=dbDatabases.find(d=>d.id===dbExplorer.database_id),s=dbExplorer.schemas.find(s=>s.id===dbExplorer.schema_id);
 return {database_id:d.id,schema_id:s.id,warehouse_id:'LOCAL',sql_catalog:d.sql_catalog,sql_schema:s.sql_name,selected_role:'admin',binding_revision:d.binding_revision,generation_id:d.generation_id,grant_revision:d.grant_revision};
}
function dbDropInvalidate(){
 const p=dbDrop;++p.revision;p.preview=null;p.context=null;p.source=null;p.scope='';p.notice='';
 if($('db-drop-name')){$('db-drop-name').value='';$('db-drop-ack').checked=false;$('db-drop-preview').textContent='';}
 if(p.op)p.op.operation=null;
 if(!p.op?.pending)p.busy=null;
 if(p.installed)dbDropControls();
}
function dbDropSaved(){return readStorage(dbDropKey,{})?.owners||{};}
function dbDropSync(){
 const p=dbDrop;if(p.owner===dbPrincipal)return;
 if(p.op){p.op.operation=null;p.handles.set(p.op.principal,p.op);}
 p.owner=dbPrincipal;
 const live=p.handles.get(dbPrincipal),saved=live||dbDropSaved()[dbPrincipal];
 p.op=saved?.principal===dbPrincipal&&typeof saved.key==='string'&&saved.key&&saved.path==='/api/v1/table-drops'&&saved.payload?.context?.selected_role==='admin'&&saved.payload?.mode===dbDropMode&&saved.payload?.source?
  live||{...saved,payload:dataopsFreeze(saved.payload),context:dataopsFreeze(saved.payload.context),operation:null}:null;
 if(p.op)p.handles.set(dbPrincipal,p.op);
 p.busy=p.op?.pending||null;p.opened=false;p.preview=null;p.context=null;p.source=null;p.scope='';p.notice='';++p.revision;
 $('db-drop-name').value='';$('db-drop-ack').checked=false;
}
function dbDropPersist(op=dbDrop.op){
 try{
  const owners=dbDropSaved();
  if(op){owners[op.principal]={principal:op.principal,key:op.key,id:op.id||null,path:op.path,payload:op.payload,state:op.state,ambiguous:!!op.ambiguous,phase:op.phase,sequence:op.sequence||0};dbDrop.handles.set(op.principal,op);}
  else{delete owners[dbPrincipal];dbDrop.handles.delete(dbPrincipal);}
  localStorage.setItem(dbDropKey,JSON.stringify({owners}));return true;
 }catch{return false;}
}
function dbDropHeld(op){return !!op&&(op.ambiguous||!['FINISHED','FAILED','CANCELED','REJECTED'].includes(op.state));}
function dbDropCanObserve(op){return !!op&&op.principal===dbPrincipal&&currentRole==='admin'&&op.context.selected_role==='admin'&&dbDropAdminAccess(op.context.grant_revision)&&dataopsExportAuthorized(op);}
function dbDropControls(){
 if(!dbDrop.installed)return;
 dbDropSync();const p=dbDrop,op=p.op,scope=dbDropScope(),reason=dbDropReason();
 if(p.busy?.scope&&p.busy.scope!==scope&&!op?.pending)p.busy=null;
 if(p.scope&&p.scope!==scope){p.preview=null;p.context=null;p.source=null;p.scope='';$('db-drop-name').value='';$('db-drop-ack').checked=false;}
 if(op&&(op.receiptScope!==scope||!dbDropCanObserve(op)))op.operation=null;
 const busy=!!p.busy||!!op?.pending,ready=!!p.preview&&p.scope===scope&&!reason;
 const button=$('drop-table-btn');button.hidden=false;button.disabled=!!reason||busy||!!op;button.dataset.databaseUnavailable='false';button.title=t(reason);ui(button,'Remove from catalog (keep data)');
 $('db-drop-panel').hidden=false;dbManageSync();
 $('db-drop-refresh').disabled=busy||!!op||!!reason;
 $('db-drop-submit').disabled=busy||!!op||!ready||$('db-drop-name').value!==p.preview?.confirmation_name||!$('db-drop-ack').checked;
 $('db-drop-name').disabled=busy||!!op||!ready;$('db-drop-ack').disabled=busy||!!op||!ready;
 $('db-drop-observe').disabled=busy||!dbDropCanObserve(op);
 ui($('db-drop-observe'),op&&!op.id?'Look up removal by original key':'Look up removal operation');
 $('db-drop-cancel').disabled=busy||!op?.id||!dbDropCanObserve(op)||op.phase==='SENT'||dbDropHeld(op)&&op.state==='UNKNOWN'||op?.ambiguous||['FINISHED','FAILED','CANCELED'].includes(op?.state);
 $('db-drop-new').disabled=busy||!op||dbDropHeld(op);
 const previewOpen=$('db-drop-identification')?.open;const receiptOpen=$('db-drop-receipt-identification')?.open;
 const out=$('db-drop-preview');out.replaceChildren();
 if(ready){
  dataopsNode('p','',out,[p.context.sql_catalog,p.context.sql_schema,p.preview.label].join(' / '));
  dataopsNode('p','',out,'Whole TABLE / all snapshots');
  dataopsNode('p','',out,'Snapshot').append(': '+(p.preview.head??t('No current snapshot')));
  const identity=dataopsNode('details','db-drop-identification',out);identity.open=!!previewOpen;
  dataopsNode('summary','',identity,'Identification details');
  dataopsNode('p','',identity,'Source').append(': '+JSON.stringify(p.preview.source));
  dataopsNode('p','',identity,'Context').append(': '+JSON.stringify(p.context));
  dataopsNode('p','',identity,'Preview digest').append(': '+p.preview.preview_fingerprint);
 }
 const status=$('db-drop-status');status.replaceChildren();
 const guidance=!op?(!ready?reason||'Refresh the preview before confirming removal.':''):op.ambiguous||op.state==='UNKNOWN'?'Outcome unknown. Observe with the saved key; do not submit again.':op.phase==='SENT'?'Removal was sent. Observe the original request; cancellation cannot establish stoppage or undo removal.':dbDropHeld(op)?'Removal is preparing. Observe manually; cancellation can prevent dispatch only.':'';
 ui($('db-drop-notice'),'{text}',()=>({text:[p.notice,guidance].filter(Boolean).map(key=>t(key)).join(' ')}));
 if(op){
  dataopsNode('p','',status,'Operation ID').append(': '+(op.id||'UNKNOWN'));
  dataopsNode('p','',status,'Status').append(': '+t(op.state||'UNKNOWN'));
  if(op.operation){const r=op.operation;
   dataopsNode('p','',status,[(r.context||op.context).sql_catalog,(r.context||op.context).sql_schema,r.label].join(' / '));
   const identity=dataopsNode('details','db-drop-receipt-identification',status);identity.open=!!receiptOpen;
   dataopsNode('summary','',identity,'Identification details');
   dataopsNode('p','',identity,'Original source Ref').append(': '+JSON.stringify(r.source||op.payload.source));
   dataopsNode('p','',identity,'Context').append(': '+JSON.stringify(r.context||op.context));
   dataopsNode('p','',identity,'Preview digest').append(': '+op.payload.expected_preview_fingerprint);
   for(const [label,value] of [['Committed',r.committed],['Acknowledged',r.acknowledged],['Status',t(r.phase)],['Warning',t(r.warning||'')]])dataopsNode('p','',status,label).append(': '+String(value));
  }else dataopsNode('p','',status,'Removal details are hidden until current owner and Admin access are verified. Observe the saved request even if the source is gone or unselected.');
  if(op.ambiguous||op.state==='UNKNOWN')dataopsNode('p','',status,'Outcome unknown. Observe with the saved key; do not submit again.');
  else if(op.phase==='SENT')dataopsNode('p','',status,'Removal was sent. Observe the original request; cancellation cannot establish stoppage or undo removal.');
  else if(dbDropHeld(op))dataopsNode('p','',status,'Removal is preparing. Observe manually; cancellation can prevent dispatch only.');
 }
}
async function dbDropVerify(context,current){
 const items=await dbReadInventory('admin',current);if(!current()||!dbInventoryCurrent(items))return false;
 if(!dbDropAdminAccess(context.grant_revision)||!dbRunAllowed({origin:{context,role:'admin',principal:dbPrincipal}},items))throw Object.assign(Error('DB_ACCESS_REVOKED'),{code:'DB_ACCESS_REVOKED'});
 const reply=await dbAPI('/api/v1/databases/'+encodeURIComponent(context.database_id)+'/schemas','admin');
 if(!current()||!dbInventoryCurrent(items))return false;
 if(!reply.items?.some(s=>s.id===context.schema_id&&s.database_id===context.database_id&&s.sql_name===context.sql_schema&&s.purpose===s.sql_name&&['raw','sandbox','staging','marts'].includes(s.sql_name)&&s.capabilities?.query===true&&s.capabilities?.write===true))throw Object.assign(Error('SQL_PERMISSION_DENIED'),{code:'SQL_PERMISSION_DENIED'});
 dbApplyInventory(items,'admin');return current()&&dbInventoryCurrent(items)&&dbDropAdminAccess(context.grant_revision);
}
async function dbDropRefresh(){
 dbDropControls();const p=dbDrop;if(p.busy||p.op||dbDropReason())return;
 const scope=dbDropScope(),owner=dbPrincipal,context=dataopsFreeze(dbDropContext()),source=dataopsFreeze(dataopsClone(dataopsRef(dbObject))),token={scope};
 const current=()=>scope===dbDropScope()&&owner===dbPrincipal&&p.busy===token;
 p.busy=token;p.preview=null;p.context=null;p.source=null;p.scope='';$('db-drop-name').value='';$('db-drop-ack').checked=false;p.notice='';dbDropControls();
 try{
  if(!await dbDropVerify(context,current))return;
  const reply=await dataopsRequest('/api/v1/table-drops/preview?source='+encodeURIComponent(JSON.stringify(source)),context);
  if(!current()||!await dbDropVerify(context,current))return;
  const v=reply.preview;
  if(v?.mode!==dbDropMode||v.scope!=='ALL_SNAPSHOTS'||dataopsRefKey(v.source)!==dataopsRefKey(source)||typeof v.confirmation_name!=='string'||!/^[a-f0-9]{64}$/.test(v.preview_fingerprint))throw Error('PREVIEW_CHANGED');
  p.preview=v;p.context=context;p.source=source;p.scope=scope;
 }catch(error){if(current()){dbDropInvalidate();p.notice=error.message;if(dataopsAuthError(error))dbExplorerDenied(error);}}
 finally{if(p.busy===token)p.busy=null;dbDropControls();}
}
function dbDropAccept(op,operation,visible){
 if(!operation||typeof operation.id!=='string'||op.id&&op.id!==operation.id)return;
 op.id=operation.id;op.state=operation.state;op.phase=operation.phase;
 op.ambiguous=operation.state==='UNKNOWN'||operation.queries?.some(q=>q.state==='UNKNOWN')===true;
 op.operation=visible?operation:null;op.receiptScope=visible?dbDropScope():null;
}
function dbDropEvict(op,operation){
 if(op.principal!==dbPrincipal||operation?.id!==op.id||!operation.committed||!operation.acknowledged||operation.mode!==dbDropMode||dataopsRefKey(operation.source)!==dataopsRefKey(op.payload.source))return;
 const ref=op.payload.source,key=dataopsRefKey(ref),same=o=>dataopsRefKey(dataopsRef(o))===key;
 dbObjects=dbObjects.filter(o=>!same(o));
 dbWorksheetObjects=dbWorksheetObjects.filter(o=>!same(o));
 for(const record of dbTreeObjects.values())record.items=record.items.filter(o=>!same(o));
 for(const cache of [dbDetails,dbPending])for(const [cacheKey,value] of cache){
  let saved;try{saved=JSON.parse(cacheKey)[2];}catch{}
  if(dataopsRefKey(saved)===key||dataopsRefKey(value?.ref)===key)cache.delete(cacheKey);
 }
 if(dbExplorer.database_id===ref.database_id&&dbExplorer.schema_id===ref.schema_id){
  ++dbListRequest;const prefix=dbTreeObjectKey(ref.database_id,ref.schema_id)+':';
  for(const pendingKey of dbTreePending.keys())if(pendingKey.startsWith(prefix))dbTreePending.delete(pendingKey);
 }
 const removedSelection=same(dbObject);
 if(removedSelection){
  ++dbObjectEpoch;++dbSelectionGeneration;dbObject=null;dbTreeSelection=null;
  ui($('table-title'),'Table registration removed');
  for(const id of [...Object.values(inspectorTargets),'snapshot-preview'])$(id).replaceChildren();
  $('refresh-table-details').disabled=true;
 }
 const p=dbDrop;p.preview=null;p.context=null;p.source=null;p.scope='';$('db-drop-name').value='';$('db-drop-ack').checked=false;
 dbExplorerControls();
 if(removedSelection){$('open-table-sql').disabled=true;$('open-table-sql').title=t('Select a resolved object in a query-enabled Explorer context with read-only mode.');}
}
async function dbDropRun(observe=false,cancel=false){
 dbDropControls();const p=dbDrop;if(p.busy||p.op?.pending)return;
 if(!observe){
  if($('db-drop-submit').disabled)return;
  const payload=dataopsFreeze({context:dataopsClone(p.context),source:dataopsClone(p.source),mode:dbDropMode,expected_preview_fingerprint:p.preview.preview_fingerprint,confirm_name:$('db-drop-name').value});
  p.op={principal:dbPrincipal,key:crypto.randomUUID(),id:null,path:'/api/v1/table-drops',payload,context:payload.context,state:'ACTIVE',phase:'PREPARED',ambiguous:false,sequence:0};
 }
 const op=p.op;if(!dbDropCanObserve(op)||cancel&&$('db-drop-cancel').disabled)return;
 op.sequence=(op.sequence||0)+1;
 if(!dbDropPersist(op)){if(!observe){p.op=null;p.handles.delete(dbPrincipal);}p.notice='Removal request could not be saved. No request was sent.';dbDropControls();return;}
 const token={},scope=dbDropScope(),current=()=>p.op===op&&op.principal===dbPrincipal&&p.busy===token&&scope===dbDropScope();let dispatched=false;
 op.pending=token;p.busy=token;op.operation=null;p.notice='';dbDropControls();
 try{
  if(!await dbDropVerify(op.context,current))return;
  dispatched=true;
  const reply=op.id?await dataopsRequest('/api/v1/dataops/'+encodeURIComponent(op.id)+(cancel?'/cancel':''),op.context,cancel?{}:undefined):observe?await dataopsRequest('/api/v1/table-drops/by-key?key='+encodeURIComponent(op.key),op.context):await dataopsRequest(op.path,op.context,op.payload,op.key);
  dbDropAccept(op,reply.operation,false);
  if(!current()){dbDropEvict(op,reply.operation);return;}
  if(!await dbDropVerify(op.context,current))return;
  dbDropEvict(op,reply.operation);
  dbDropAccept(op,reply.operation,true);
 }catch(error){
  if(error.observation)dbDropAccept(op,error.observation,false);
  else if(observe&&!op.id&&dbDropHeld(op)){op.state='UNKNOWN';op.ambiguous=true;}
  else if(observe&&dbDropHeld(op))op.ambiguous=true;
  else if(dispatched&&dbDropHeld(op)){if(!dataopsRejectBeforeAdmission(op,error))op.ambiguous=true;}
  op.operation=null;
  if(current()){p.notice=error.message;if(dataopsAuthError(error)){dbDropInvalidate();p.notice=error.message;dbInvalidateAccess(error,'admin',op.context.database_id);}}
 }finally{
  if(!observe&&!dispatched){op.state='REJECTED';op.ambiguous=false;}
  if(op.pending===token)op.pending=null;if(p.busy===token)p.busy=null;
  dbDropPersist(op);dbDropControls();
 }
}
function dbDropInstall(){
 const p=dbDrop;if(p.installed)return;p.installed=true;
 i18n.en['Catalog removal requires current Admin, an explicit database grant, and a readable, writable TABLE in raw, sandbox, staging or marts.']='Catalog removal requires current Admin database access and a readable, writable TABLE in raw, sandbox, staging or marts.';
 const button=$('drop-table-btn');button.parentElement.parentElement.append(button);button.dataset.action='catalog-unregister-keep-data';button.setAttribute('aria-controls','db-drop-panel');
 for(let i=uiAttributes.length-1;i>=0;i--)if(uiAttributes[i].node===button)uiAttributes.splice(i,1);
 const root=dataopsNode('details','db-drop-panel',$('database-details'));root.open=false;root.setAttribute('aria-labelledby','db-drop-summary');
 dataopsNode('summary','db-drop-summary',root,'Remove from catalog (keep data)');
 dataopsNode('p','',root,'Removes the whole TABLE registration and all snapshots. Data and metadata files are kept. No automatic Undrop.');
 const more=dataopsNode('details','db-drop-more',root);dataopsNode('summary','',more,'More about catalog removal');
 dataopsNode('p','',more,'Removes the whole TABLE registration, including all snapshots. Data files and metadata files are retained. There is no automatic Undrop. VIEW, SQL and Job references are not rewritten. Dependency completeness is not established.');
 dataopsNode('p','db-drop-cooperation-help',root,'Before removing this registration, pause other applications and tools that could create or replace a TABLE with the same name. Catalog removal cannot atomically check the UUID or current snapshot.').className='help';
 dataopsNode('div','db-drop-preview',root);
 const name=dataopsField(root,'db-drop-name','Type the exact physical table name');name.autocomplete='off';name.oninput=dbDropControls;
 const label=dataopsNode('label','',root),ack=dataopsNode('input','db-drop-ack',label);ack.type='checkbox';ack.onchange=dbDropControls;dataopsNode('span','',label,'I understand: remove registration and keep data');
 dataopsButton(root,'db-drop-refresh','Refresh removal preview',dbDropRefresh);
 dataopsButton(root,'db-drop-submit','Remove from catalog (keep data)',()=>dbDropRun()).className='small danger';
 dataopsNode('p','',root,'Cancellation can prevent dispatch only. Once sent, the outcome remains uncertain until an acknowledgement; it cannot roll back removal.');
 const notice=dataopsNode('p','db-drop-notice',root);notice.setAttribute('role','status');
 const extra=dataopsNode('details','db-drop-operation-details',root);dataopsNode('summary','',extra,'Operation details');
 const status=dataopsNode('div','db-drop-status',extra);status.setAttribute('role','status');status.setAttribute('aria-live','polite');
 dataopsButton(root,'db-drop-observe','Observe operation',()=>dbDropRun(true));
 dataopsButton(root,'db-drop-cancel','Cancel operation',()=>dbDropRun(true,true));
 dataopsButton(root,'db-drop-new','New removal intent',()=>{if(p.busy||!p.op||dbDropHeld(p.op))return;p.op=null;dbDropPersist();dbDropInvalidate();});
 button.onclick=async()=>{dbManageOpen('drop');$('db-drop-summary').focus();dbDropControls();await dbDropRefresh();if(p.preview&&$('db-drop-panel').open&&$('db-table-manage').open&&document.activeElement===$('db-drop-summary'))$('db-drop-name').focus();};
 dbDropControls();
}
