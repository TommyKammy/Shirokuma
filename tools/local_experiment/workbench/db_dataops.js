'use strict';

// Manual DataOps owns its inputs, authorization receipts and accepted requests.
// No worksheet object is mutated here. Follow-ups always use the accepted role.
const dataopsPanels=new Map();
let dataopsExport=null;
Object.assign(i18n.ja,{
 'Manual file ingestion':'手動ファイル取り込み','Manual WAP':'手動 WAP',
 'Refresh panel context':'パネルのコンテキストを更新','Choose mode':'モードを選択',
 'Create table name':'作成するテーブル名','Existing table':'既存テーブル',
 'File format':'ファイル形式','Encoding':'文字コード','Delimiter':'区切り文字',
 'Column types (name=type, one per line)':'列の型（name=type、1行1列）',
 'Atomic append':'原子的な追記','Confirm preview and ingest':'プレビューを確認して取り込む',
 'Input preview only. Existing target types are checked at execution.':'入力のみのプレビューです。既存の対象テーブルの型は実行時に検証します。',
 'Preview required after every input change.':'入力を変更するたびにプレビューが必要です。',
 'Choose a file, context, explicit mode and target.':'ファイル、コンテキスト、モード、対象を明示的に選択してください。',
 'Observe operation':'操作を照会','Cancel operation':'操作を取り消す',
 'Outcome unknown. Observe with the saved key; do not submit again.':'結果不明です。保存したキーで確認し、再送信しないでください。',
 'Access changed. Protected data cleared; refresh context explicitly.':'アクセスが変わりました。保護されたデータを消去しました。コンテキストを明示的に更新してください。',
 'Operation ID':'操作 ID','Committed':'コミット済み','Cleanup error':'清掃エラー',
 'Idempotency key':'冪等性キー','Accepted context':'受理済みコンテキスト',
 'Input rows: {count}':'入力行数: {count}', 'Choose a supported file.':'対応ファイルを選択してください。',
 "Edit in Manual WAP":"Manual WAP で編集",
 "Original proposal context":"元の提案のコンテキスト",
 "Current Manual WAP context":"現在の Manual WAP コンテキスト",
 "Original proposal":"元の提案",
 "Created at":"作成日時",
 "Choose the same database and data_engineer/admin role. Select a writable SANDBOX panel schema and the same target schema, then explicitly Refresh panel context. Use New model for default SQL and one default source with an otherwise empty draft; verify the target schema still matches the panel SANDBOX before explicitly choosing Load proposal draft. Unresolved saved original requests must be resolved first.":"同じデータベースと data_engineer/admin ロールを選んでください。書込可能な SANDBOX のパネルスキーマと同じ対象スキーマを選び、Refresh panel context を明示的に実行します。New model（新しいモデル）で既定 SQL・既定ソース1件以外が空の下書きにし、対象スキーマがパネルの SANDBOX と一致することを確認してから Load proposal draft（提案の下書きを読込）を明示的に選んでください。未解決の保存済み元要求があれば、先に解決が必要です。",
 "Load proposal draft":"提案の下書きを読込",
 "Clear selection":"選択を解除",
 "Original proposal (read only). Review the saved model version using the normal Manual WAP actions.":"元の提案（読取専用）。保存済みのモデルの版を、通常の Manual WAP の操作で確認してください。",
 "Imported as an unsaved draft. Review and edit, then explicitly save a model version before building.":"未保存の下書きとして読み込みました。確認・編集後、ビルド前にモデルの版を明示的に保存してください。",
 "Original request and SQL (read only)":"元の依頼と SQL（読取専用）",
 "This proposal cannot be imported into Manual WAP. Used sources must be supported tables.":"この提案は Manual WAP に読み込めません。使用するソースは対応するテーブルである必要があります。",
 "Source metadata changed. Refresh the inventory and select a new proposal.":"ソースのメタデータが変わりました。一覧を更新し、新しい提案を選択してください。",
 "Proposal draft unavailable. Check both contexts and permissions, then explicitly retry.":"提案の下書きを取得できません。両方のコンテキストと権限を確認し、明示的に再試行してください。",
 'Model ID':'モデル名','Saved models':'保存済みモデル','New model':'新しいモデル',
 'Save model version':'モデルの版を保存','Build saved version':'保存済みの版をビルド',
 'Audit build':'ビルドを監査','Publish audited build (admin)':'監査済みビルドを公開（管理者）',
 'Rollback publication (admin)':'公開を元に戻す（管理者）','Source alias':'ソースの別名',
 'Add source':'ソースを追加','Remove source':'ソースを削除','Target schema':'対象スキーマ',
 'Target table name':'対象テーブル名','Minimum rows':'最小行数','Not-null columns':'NULL を許可しない列',
 'Build':'ビルド','Audit':'監査','Publication':'公開記録','Load metadata':'メタデータを読込',
 'Metadata ID (optional, from another authorized session)':'メタデータ ID（別の許可済みセッションから、任意）',
 'Save or load a model version before building.':'ビルドの前にモデルの版を保存または読込してください。',
 'Export table':'テーブルをエクスポート','JSONL (one object per line)':'JSONL（1行1オブジェクト）',
 'View export is unsupported. Select a stable readable table.':'ビューのエクスポートは未対応です。読取可能な確定済みテーブルを選択してください。',
 'Download authorized file':'権限を確認してファイルをダウンロード','Export receipt':'エクスポート受領記録',
 'Snapshot':'スナップショット','Owner':'所有者','Rows':'行数','Bytes':'バイト数',
 'Select a stable readable table.':'読取可能な確定済みテーブルを選択してください。',
 'Duplicate or invalid source alias.':'ソースの別名が重複しているか無効です。',
 'Enter column types as name=type.':'列の型を name=type で入力してください。',
 'Choose an authorized context.':'許可されたコンテキストを選択してください。',
 'Request context changed; response ignored.':'要求コンテキストが変わったため、応答を破棄しました。',
 'Pending':'処理中','Saved version':'保存済みの版','Admin role required.':'管理者ロールが必要です。',
 'Choose a saved metadata record.':'保存済みのメタデータを選択してください。',
 'Export receipt unavailable. Observe the operation.':'受領記録を取得できません。操作を照会してください。'
 ,'New export':'新しいエクスポート','Batch size':'バッチの行数',
 'Saved version {version}':'保存済みの版 {version}',
 'Choose a positive integer batch size.':'バッチの行数は正の整数で指定してください。',
 'Choose a model ID, SQL, sources and target.':'モデル名、SQL、ソース、対象を指定してください。',
 'Results hidden until access is revalidated.':'アクセスを再確認するまで結果を非表示にします。',
 'Unknown execution requires manual reconciliation. Observe the saved operation.':'実行結果不明のため手動照合が必要です。保存した操作を照会してください。',
 'Reselect the original table and role, then observe to retrieve its receipt.':'元のテーブルとロールを再選択し、操作を照会して受領記録を取得してください。',
 'Select an execution role within the current ceiling for this panel.':'このパネルで現在のロール上限内の実行ロールを明示的に選択してください。',
 'Manual Ingest and WAP require data_engineer or admin. Select an execution role for this panel.':'手動取り込みと WAP にはデータ作成者または管理者ロールが必要です。このパネルの実行ロールを選択してください。',
 'Panel context is unavailable. Refresh this panel context or reselect its database, schema and execution role.':'このパネルのコンテキストは利用できません。このパネルのコンテキストを更新するか、データベース、スキーマ、実行ロールを再選択してください。',
 'Stage snapshot':'ステージのスナップショット','Target snapshot':'対象のスナップショット',
 'Null counts':'NULL 件数','Quality':'品質','Passed':'合格','Failed':'不合格','REJECTED':'受付拒否'
});
for(const key of Object.keys(i18n.ja))i18n.en[key]=key;
// The pending proposal survives B1 navigation; it never follows the active worksheet.
function dataopsAiAuth(context){return JSON.stringify([dbPrincipal,currentRole,dbMaximumRole,dbEpoch,dbAuthorizationVersion,dbRoleVersion(context.selected_role)]);}
function dataopsAiAuthorized(origin){
 if(!origin||origin.auth!==dataopsAiAuth(origin.context)||!dbRoleWithinHeader(origin.context.selected_role))return false;
 let actor;try{actor=JSON.parse(dbPrincipal);}catch{return false;}
 if(actor?.[1]!==true||actor[0]!==origin.actor)return false;
 const c=origin.context,d=dbRoleItems(c.selected_role).find(d=>d.id===c.database_id);
 return !!d&&d.state==='READY'&&d.allowed_roles?.includes(c.selected_role)&&d.sql_catalog===c.sql_catalog&&d.binding_revision===c.binding_revision&&d.generation_id===c.generation_id&&(d.grant_revision??null)===c.grant_revision;
}
function dataopsAiSelect(){
 dbProposalSync();const r=dbProposal.record,p=dataopsPanels.get('p35-wap');
 if(!p||r!==dbProposal.wapRecord||r?.state!=='SUCCEEDED'||!['data_engineer','admin'].includes(r.context.selected_role)||dbReason()||dbProposal.busy)return;
 // Validate again without consulting the mutable request, picker or either SQL editor.
 try{dbProposalRecord({context:r.context,explanation:r},r.context);}catch{return;}
 const origin=dataopsFreeze(dataopsClone({id:r.id,actor:r.actor,context:r.context,original_sql:r.sql,request:r.input.request,refs:r.input.relations,labels:r.metadata.relations,created_at:r.created_at,auth:dataopsAiAuth(r.context)}));
 if(!dataopsAiAuthorized(origin))return;
 p.aiPending=origin;p.aiToken=null;p.aiNotice='';
 activate($('tab-transformation'));
 showView('transformation-view-wap');
 dataopsAiRender(p);
 p.root.setAttribute('tabindex','-1');p.root.focus();
}
function dataopsAiClear(p){p.aiPending=null;p.aiToken=null;p.aiNotice='';dataopsAiRender(p);}
function dataopsAiPristine(p){
 const value=k=>$(p.id+'-'+k).value;
 if(p.model||p.pending||p.trackingPending||p.inventoryLoading||!p.inventoryReady||p.invalid||!dataopsSameContext(p.inventoryContext,p.context)||dataopsOwnedUnresolved(p)||dataopsRecoveryHeld(p)||p.metadata)return false;
 for(const records of [p.requests,p.actions])for(const record of records?.values()||[])if(dataopsUnresolved(record.operation))return false;
 if(['models','model-id','name','target','not-null','metadata-id','build','audit','publication'].some(k=>value(k)!==''))return false;
 return value('target-schema')===p.schema&&value('sql')==='SELECT * FROM source'&&String(value('min'))==='1'&&p.sources.length===1&&p.sources[0].alias.value==='source'&&p.sources[0].table.value==='';
}
function dataopsAiDestination(p,origin){
 if(!dataopsAiAuthorized(origin)||!dataopsAllowed(p,'wap_models')||p.principal!==dbPrincipal)return false;
 const a=origin.context,b=p.context,s=p.schemas.find(s=>s.id===p.schema),primary=p.schemas.find(s=>s.id===a.schema_id&&s.database_id===a.database_id&&s.sql_name===a.sql_schema);
 if(primary?.capabilities?.query!==true||s?.capabilities?.query!==true)return false;
 if(!dbExplanationSameContext(b,b)||!dataopsSameContext(b,dataopsContext(p))||a.warehouse_id!=='LOCAL'||b.warehouse_id!=='LOCAL'||s?.capabilities?.write!==true||String(s.purpose||s.sql_name).toUpperCase()!=='SANDBOX')return false;
 return origin.refs.every(r=>p.schemas.some(s=>s.id===r.schema_id&&s.database_id===a.database_id&&s.capabilities?.query===true))&&dbExplanationContextKeys.every(k=>['schema_id','sql_schema'].includes(k)||a[k]===b[k]);
}
function dataopsAiFingerprint(p){
 return JSON.stringify([p.epoch,dbPrincipal,currentRole,dbMaximumRole,dbEpoch,dbAuthorizationVersion,dbRoleVersion(p.role),p.role,p.database,p.schema,p.context,p.inventoryContext,p.inventoryLoading,p.inventoryReady,p.invalid,p.pending,p.trackingPending,p.tables,p.schemas,p.models,p.model,p.metadata,p.op,[...(p.requests?.values()||[])],[...(p.actions?.values()||[])],
 ['role','database','schema','models','model-id','sql','target-schema','name','target','min','not-null','metadata-id','metadata-kind','build','audit','publication'].map(k=>$(p.id+'-'+k).value),p.sources.map(s=>[s.alias.value,s.table.value]),dbProposalView(),dbSelectionGeneration,dbExplorer]);
}
function dataopsAiRender(p){
 if(!p.aiBar)return;
 if(p.aiOrigin&&!dataopsSameContext(p.aiOrigin.destination_context,p.context))p.aiOrigin=null;
 for(const key of ['aiPending','aiOrigin'])if(p[key]&&!dataopsAiAuthorized(p[key])){p[key]=null;p.aiToken=null;p.aiNotice='';}
 const fingerprint=dataopsAiFingerprint(p);
 if(p.aiFingerprint!==fingerprint){p.aiFingerprint=fingerprint;++p.aiRevision;p.aiToken=null;}
 const origin=p.aiPending||p.aiOrigin,bar=p.aiBar;bar.replaceChildren();bar.hidden=!origin;if(!origin){p.aiDetails=null;p.aiDetailsKey='';return;}
 const line=(label,text)=>{const n=dataopsNode('p','',bar);dataopsNode('strong','',n,label);n.append(': '+text);};
 const context=c=>[c.sql_catalog,c.sql_schema,c.selected_role,c.warehouse_id].join(' / ');
 line('Original proposal context',context(origin.context));line('Current Manual WAP context',p.context&&!p.invalid?context(p.context):t('Choose an authorized context.'));
 line('Original proposal',origin.id);line('Created at',origin.created_at);
 if(p.aiPending){
  dataopsNode('p','',bar,'Choose the same database and data_engineer/admin role. Select a writable SANDBOX panel schema and the same target schema, then explicitly Refresh panel context. Use New model for default SQL and one default source with an otherwise empty draft; verify the target schema still matches the panel SANDBOX before explicitly choosing Load proposal draft. Unresolved saved original requests must be resolved first.');
  const load=dataopsButton(bar,p.id+'-ai-load','Load proposal draft',()=>dataopsAiLoad(p));
  let allowed=false;try{allowed=dataopsAiDestination(p,origin)&&dataopsAiPristine(p);}catch{/* Context is not ready. */}
  load.disabled=!!p.aiToken||!allowed;
  dataopsButton(bar,p.id+'-ai-clear','Clear selection',()=>dataopsAiClear(p));
 }else dataopsNode('p','',bar,p.model&&!p.modelDirty?'Original proposal (read only). Review the saved model version using the normal Manual WAP actions.':'Imported as an unsaved draft. Review and edit, then explicitly save a model version before building.');
 const detailsKey=JSON.stringify([origin.id,origin.context,origin.auth,origin.request,origin.original_sql,origin.created_at,origin.destination_context||p.context]);
 if(!p.aiDetails||p.aiDetailsKey!==detailsKey){
  p.aiDetailsKey=detailsKey;p.aiDetails=dataopsNode('details');dataopsNode('summary','',p.aiDetails,'Original request and SQL (read only)');
  for(const text of [origin.request,origin.original_sql])dataopsNode('pre','',p.aiDetails).textContent=text;
 }
 bar.append(p.aiDetails);
 if(p.aiNotice)dataopsNode('p','',bar,p.aiNotice);
}
function dataopsAiDraft(data,origin,destination,p){
 const d=data?.draft,fail=()=>{throw Error('AI_WAP_UNAVAILABLE');};
 const date=v=>typeof v==='string'&&/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$/.test(v)&&Number.isFinite(Date.parse(v));
 if(!dbSavedExact(data,['context','draft'])||!dbExplanationSameContext(data.context,origin.context)||!dbSavedExact(d,['version','proposal_id','source_context','destination_context','original_sql','sql','sources','request','proposal_created_at','prepared_at'])||d.version!==1||d.proposal_id!==origin.id||!dbExplanationSameContext(d.source_context,origin.context)||!dbExplanationSameContext(d.destination_context,destination)||d.original_sql!==origin.original_sql||d.request!==origin.request||d.proposal_created_at!==origin.created_at||!date(d.proposal_created_at)||!date(d.prepared_at)||!dbProposalText(d.sql,65536)||!Array.isArray(d.sources)||d.sources.length<1||d.sources.length>8)fail();
 const aliases=new Set(),refs=new Set();
 for(const source of d.sources){
  if(!dbSavedExact(source,['alias','ref','schema','table'])||typeof source.alias!=='string'||!/^[a-z_][a-z0-9_]*$/.test(source.alias)||!dbProposalRef(source.ref)||!dbProposalText(source.schema,4096)||!dbProposalText(source.table,4096))fail();
  const key=JSON.stringify(['database_id','schema_id','object_id','incarnation','binding_revision','generation_id'].map(k=>source.ref[k]));
  if(aliases.has(source.alias)||refs.has(key)||!origin.refs.some(r=>Object.keys(r).every(k=>r[k]===source.ref[k])))fail();aliases.add(source.alias);refs.add(key);
  const label=origin.labels.find(l=>l.schema===source.schema&&l.table===source.table);
  if(label?.kind==='VIEW')throw Error('AI_WAP_UNSUPPORTED');
  const matches=p.tables.filter(o=>dataopsRefKey(dataopsRef(o))===dataopsRefKey(source.ref)),object=matches[0];
  if(!label||label.kind!=='TABLE'||matches.length!==1||object.kind!=='TABLE'||object.capabilities?.resolve!==true||!dbProposalRef(object.ref)||!Object.keys(source.ref).every(k=>source.ref[k]===object.ref[k])||object.physical_name!==source.table||!p.schemas.some(s=>s.id===object.ref.schema_id&&s.database_id===destination.database_id&&s.sql_name===source.schema&&s.capabilities?.query===true))throw Error('AI_METADATA_CHANGED');
 }
 return dataopsClone(d);
}
async function dataopsAiLoad(p){
 dataopsAiRender(p);const origin=p.aiPending;
 try{if(!origin||p.aiToken||!dataopsAiPristine(p)||!dataopsAiDestination(p,origin))return;}catch{return;}
 const destination=dataopsFreeze(dataopsClone(p.context)),tables=p.tables,schemas=p.schemas,sources=p.sources,fingerprint=dataopsAiFingerprint(p),revision=p.aiRevision,token={};p.aiToken=token;p.aiNotice='';
 // Render without changing the request fingerprint. A changed field cancels this token permanently.
 dataopsAiRender(p);
 const current=()=>{dataopsAiRender(p);return p.aiToken===token&&p.aiPending===origin&&p.tables===tables&&p.schemas===schemas&&p.sources===sources&&revision===p.aiRevision&&fingerprint===dataopsAiFingerprint(p)&&dataopsAiPristine(p)&&dataopsAiDestination(p,origin);};
 try{
  const params=new URLSearchParams({context:JSON.stringify(origin.context),destination_context:JSON.stringify(destination)});
  const response=await fetch(dbProposalEndpoint+'/'+encodeURIComponent(origin.id)+'/wap-draft?'+params,{headers:{'X-Shirokuma-Role':origin.context.selected_role}});
  let data;try{data=await response.json();}catch{const error=Error('AI_WAP_UNAVAILABLE');error.status=response.status;throw error;}
  if(!current())return;
  if(!response.ok){const code=data?.code||data?.error?.code,error=Error('AI_WAP_UNAVAILABLE');error.code=code;error.status=response.status;
   if(['AI_WAP_UNAVAILABLE','AI_WAP_UNSUPPORTED','AI_METADATA_CHANGED','AI_WAP_CONTEXT_MISMATCH'].includes(code))error.message=code;throw error;}
  const draft=dataopsAiDraft(data,origin,destination,p);if(!current())return;
  // All validation precedes the first form mutation. The normal explicit Save remains mandatory.
  $(p.id+'-sql').value=draft.sql;$(p.id+'-sources').replaceChildren();p.sources=[];
  for(const source of draft.sources)dataopsAddSource(p,source.alias,source.ref);
  p.model=null;p.modelDirty=true;p.publishReview=null;p.publishImpact=null;$(p.id+'-editor').open=true;p.aiOrigin=dataopsFreeze({...origin,destination_context:destination});p.aiPending=null;p.aiToken=null;p.aiNotice='';++p.epoch;
  ui(p.status,'Save or load a model version before building.');dataopsRender(p);
 }catch(error){
  if(!current())return;
  const code=error.message;
  if(dataopsAuthError(error)||['ROLE_FORBIDDEN','AI_ACCESS_REVOKED','SQL_PERMISSION_DENIED'].includes(error.code)){
   for(const key of ['aiPending','aiOrigin'])if(p[key]?.id===origin.id&&dbExplanationSameContext(p[key].context,origin.context))p[key]=null;
   p.aiNotice='';return;
  }
  p.aiNotice=code==='AI_WAP_UNSUPPORTED'?'This proposal cannot be imported into Manual WAP. Used sources must be supported tables.':code==='AI_METADATA_CHANGED'?'Source metadata changed. Refresh the inventory and select a new proposal.':'Proposal draft unavailable. Check both contexts and permissions, then explicitly retry.';
 }finally{if(p.aiToken===token){p.aiToken=null;dataopsAiRender(p);}}
}

const dataopsClone=value=>JSON.parse(JSON.stringify(value));
function dataopsFreeze(value){if(value&&typeof value==='object'){Object.values(value).forEach(dataopsFreeze);Object.freeze(value);}return value;}
function dataopsRef(object){const r=object?.ref;return r&&['database_id','schema_id','object_id','incarnation'].every(k=>r[k]!=null)?Object.fromEntries(['database_id','schema_id','object_id','incarnation'].map(k=>[k,r[k]])):null;}
const dataopsRefKey=ref=>JSON.stringify(['database_id','schema_id','object_id','incarnation'].map(k=>ref?.[k]));
function dataopsNode(tag,id,parent,label){const node=document.createElement(tag);if(id)node.id=id;if(label)ui(node,label);if(parent)parent.append(node);return node;}
function dataopsField(parent,id,label,tag='input',options=[]){const wrap=dataopsNode('div','',parent),l=dataopsNode('label','',wrap,label);l.htmlFor=id;const n=dataopsNode(tag,id,wrap);for(const [value,text] of options){const o=new Option(t(text),value);ui(o,text);n.add(o);}if(options.length)n.value=options[0][0];return n;}
function dataopsButton(parent,id,label,task){const b=dataopsNode('button',id,parent,label);b.type='button';b.onclick=task;return b;}
function dataopsOptions(node,items,value,placeholder='Choose from lists'){
 const signature=JSON.stringify([language,items,value,placeholder]);if(node.dataset.options===signature)return;
 node.dataset.options=signature;const placeholderOption=new Option(t(placeholder),'');ui(placeholderOption,placeholder);node.replaceChildren(placeholderOption);
 for(const [id,label] of items){const o=new Option(label,id);if(Object.hasOwn(i18n.en,label))ui(o,label);node.add(o);}
 if(value&&!items.some(([id])=>id===value)){const o=new Option(t('Unavailable'),value);o.disabled=true;node.add(o);}node.value=value||'';
}
function dataopsContext(p,schema=p.schema){
 const d=p.databases.find(d=>d.id===p.database),s=p.schemas.find(s=>s.id===schema);
 if(!d||!s||!dbRoleWithinHeader(p.role)||!d.allowed_roles?.includes(p.role)||d.state!=='READY')throw Error(t('Choose an authorized context.'));
 return {database_id:d.id,schema_id:s.id,warehouse_id:'LOCAL',sql_catalog:d.sql_catalog,sql_schema:s.sql_name,selected_role:p.role,binding_revision:d.binding_revision,generation_id:d.generation_id,grant_revision:d.grant_revision??null};
}
function dataopsAuthError(error){return [401,403].includes(error.status)||['DB_ACCESS_REVOKED','AUTHENTICATION_REQUIRED','STALE_BINDING','STALE_CONTEXT','CONTEXT_MISMATCH','UNKNOWN_CONTEXT'].includes(error.code);}
const dataopsState=op=>op?.state||op?.operation?.state;
const dataopsTerminal=op=>!!op&&!op.ambiguous&&['FINISHED','FAILED','CANCELED','REJECTED'].includes(dataopsState(op));
const dataopsUnresolved=op=>!!op&&!dataopsTerminal(op);
const dataopsCanObserve=op=>!!op&&dataopsState(op)!=='REJECTED'&&!!(op.id||op.path);
const dataopsCanCancel=op=>!!op?.id&&(!op.panel||!op.ambiguous)&&!['UNKNOWN','FINISHED','FAILED','CANCELED'].includes(dataopsState(op));
function dataopsRejectBeforeAdmission(op,error){
 // Only a fresh request's definite validation rejection proves no execution began.
 // A replay after a lost response, authorization failure or server error stays unresolved.
 if(op.id||op.ambiguous||op.sequence!==1||error.observation||error.details?.operation)return false;
 if(!((error.status===400&&error.code==='INVALID_INPUT')||(error.status===409&&error.code==='PREVIEW_CHANGED')))return false;
 op.state='REJECTED';op.ambiguous=false;return true;
}
function dataopsAccept(op,operation,visible){
 if(op.panel&&(!operation||!dataopsRecoveryID(operation.id)||!dataopsRecoveryStates.slice(1,-1).includes(operation.state)||op.id&&operation.id!==op.id))throw Error(t('Invalid recovery response.'));
 op.id=operation.id;op.state=operation.state;op.ambiguous=operation.state==='UNKNOWN';
 if(op.panel)op.ambiguous=op.ambiguous||operation.child_unknown===true||operation.queries?.some(q=>q.state==='UNKNOWN')===true;
 op.protected=visible;op.operation=visible?operation:null;
 if(op.panel){const p=dataopsPanels.get(op.panel);if(visible&&p)op.capture=dataopsRecoveryStamp(p);dataopsRecoverySave(op);}
}

function dataopsAllowed(p,cap){
 if(p.invalid||!p.context||p.principal!==dbPrincipal||!dbRoleWithinHeader(p.role))return false;
 const d=dbRoleItems(p.role).find(d=>d.id===p.database),s=p.schemas.find(s=>s.id===p.schema),c=p.context;
 if(!d||d.state!=='READY'||!d.allowed_roles?.includes(p.role)||d.binding_revision!==c.binding_revision||d.generation_id!==c.generation_id||(d.grant_revision??null)!==c.grant_revision)return false;
 if(s?.capabilities?.[cap]!==undefined)return s.capabilities[cap]===true;
 const write=p.role==='admin'||p.role==='data_engineer';
 if(cap==='manual_ingest')return write&&s?.capabilities?.write===true&&['RAW','SANDBOX'].includes(String(s.purpose||s.sql_name).toUpperCase());
 return cap==='wap_publish'||cap==='wap_rollback'?p.role==='admin':write&&s?.capabilities?.query===true;
}
function dataopsPurgeLoadedModel(p){
 if(!p.model||p.id!=='p35-wap')return;
 for(const key of ['model-id','sql','name','not-null'])$(p.id+'-'+key).value='';
 $(p.id+'-model-id').readOnly=false;$(p.id+'-min').value='1';
 for(const source of p.sources||[]){source.alias.value='';source.table.replaceChildren();}
}
function dataopsPurgeForeignDraft(p){
 if(p.draftPrincipal===dbPrincipal)return;
 // Only an actual identity change purges browser inputs. Saved operations and
 // their original live objects remain private in the original owner's store.
 p.draftPrincipal=dbPrincipal;p.file=null;p.preview=null;
 p.databases=[];p.schemas=[];p.database='';p.schema='';p.context=null;p.inventoryContext=null;p.inventoryReady=false;p.inventoryLoading=false;
 p.role=['admin','data_engineer','bi_viewer'].find(dbRoleWithinHeader)||'';
 for(const key of ['role','database','schema']){const n=$(p.id+'-'+key);n.replaceChildren();n.value='';delete n.dataset.options;}
 dataopsOptions($(p.id+'-role'),['admin','data_engineer','bi_viewer'].filter(dbRoleWithinHeader).map(role=>[role,role]),p.role,'Execution role');
 p.requests?.clear();p.actions?.clear();
 if(p.id==='p35-ingest'){
  for(const key of ['file','name','hints','keys']){const n=$(p.id+'-'+key);if(n)n.value='';}
 }else{
  for(const key of ['model-id','sql','name','not-null','metadata-id'])$(p.id+'-'+key).value='';
  $(p.id+'-model-id').readOnly=false;$(p.id+'-min').value='1';
  for(const key of ['models','target','target-schema','build','audit','publication']){const n=$(p.id+'-'+key);n.replaceChildren();n.value='';}
  for(const source of p.sources||[]){source.alias.value='';source.table.replaceChildren();source.table.value='';}
 }
 p.recoverySelection='';p.receipt?.replaceChildren();
 const context=$(p.id+'-accepted-context');if(context)context.textContent='';
 dataopsInvalidate(p);
}
function dataopsInvalidate(p){
 p.aiPending=null;p.aiOrigin=null;p.aiToken=null;p.reviewToken=null;p.publishReview=null;p.publishImpact=null;
 dataopsPurgeLoadedModel(p);
 ++p.epoch;p.preview=null;p.model=null;p.models=[];p.objects=[];p.tables=[];p.invalid=true;
 for(const id of ['sample','models','target','target-schema','build','audit','publication']){const n=$(p.id+'-'+id);if(n)n.replaceChildren();}
 for(const source of p.sources||[])source.table.replaceChildren();
 p.metadata=null;p.wapEvidence=null;
 if(p.op){p.op.protected=false;p.op.receipt=null;p.op.operation=null;}
 for(const records of [p.requests,p.actions])for(const record of records?.values()||[])if(record.operation){record.operation.protected=false;record.operation.operation=null;record.operation.receipt=null;}
 ui(p.status,'Access changed. Protected data cleared; refresh context explicitly.');dataopsRender(p);
}
function dataopsValidate(){
 for(const p of dataopsPanels.values()){
  dataopsPurgeForeignDraft(p);
  if(p.context&&!p.invalid&&!dataopsAllowed(p,p.id==='p35-ingest'?'manual_ingest':'wap_models'))dataopsInvalidate(p);
  else dataopsRenderAvailability(p);
  if(p.id==='p35-ingest')dataopsIngestContext(p);
  dataopsRecoveryRender(p);
  $(p.id+'-accepted-context').hidden=['p35-ingest','p35-wap'].includes(p.id)||p.principal!==dbPrincipal;
  if(p.op?.principal!==undefined&&p.op.principal!==dbPrincipal){dataopsRenderOperation(p);$(p.id+'-observe').hidden=true;$(p.id+'-cancel').hidden=true;$(p.id+'-observe-help').hidden=true;}
  if(p.id==='p35-wap'){dataopsRenderWapGuide(p);dataopsAiRender(p);}
 }
 if(dataopsExport&&!dataopsExport.invalid&&!dataopsExportAuthorized(dataopsExport)){dataopsExport.invalid=true;dataopsExport.receipt=null;dataopsExport.operation=null;dataopsExportRender();}
 dataopsExportControls();
}
async function dataopsRefreshAuthorization(){
 const roles=new Set([...dataopsPanels.values()].filter(p=>p.started&&!p.invalid).map(p=>p.role));
 if(dataopsExport&&!dataopsExport.invalid)roles.add(dataopsExport.context.selected_role);
 for(const role of roles){if(role===currentRole||role===dbTabRole())continue;
  try{const items=await dbReadInventory(role);if(dbInventoryCurrent(items))dbApplyInventory(items,role);}
  catch(error){if(error.code!=='AUTH_READ_STALE'){for(const p of dataopsPanels.values())if(p.role===role)dataopsInvalidate(p);if(dataopsExport?.context.selected_role===role){dataopsExport.invalid=true;dataopsExport.receipt=null;dataopsExportRender();}}}
 }
 dataopsValidate();
}
function dataopsOpen(target){if(dbNavigationRestoring)return;const p=dataopsPanels.get(target==='ingestion'?'p35-ingest':target==='transformation'?'p35-wap':'');if(p&&!p.started&&!p.aiPending){p.started=true;dataopsRefresh(p);}}
function dataopsChanged(p){++p.epoch;p.preview=null;p.downstream=[];p.modelDirty=true;p.reviewToken=null;p.publishReview=null;p.publishImpact=null;$(p.id+'-sample')?.replaceChildren();ui(p.status,p.id==='p35-ingest'?'Preview required after every input change.':'Save or load a model version before building.');dataopsRender(p);}
const dataopsSameContext=(a,b)=>JSON.stringify(Object.keys(a||{}).sort().map(k=>[k,a[k]]))===JSON.stringify(Object.keys(b||{}).sort().map(k=>[k,b[k]]));
async function dataopsRequest(path,context,body,key){
 const headers={'X-Shirokuma-Role':context.selected_role};if(key)headers['Idempotency-Key']=key;
 const url=body===undefined?path+(path.includes('?')?'&':'?')+'context='+encodeURIComponent(JSON.stringify(context)):path;
 const response=await fetch(url,body===undefined?{headers}:{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify({...body,context})});
 const data=await dbResponseJSON(response);if(!response.ok){const error=dbError(data,response.status),details=data?.details||data?.error?.details;
  if(error.code==='JOB_INPUT_REJECTED')error.jobRejectionOnly=Object.keys(details||{}).length===1&&[data,data.error].filter(v=>v&&typeof v==='object').every(v=>Object.keys(v).every(k=>['code','message','request_id','retryable','details','error'].includes(k))&&(!v.details||(Object.keys(v.details).length===1&&v.details.admission==='NOT_ATTEMPTED')));
  if(error.code==='STAGE_INPUT_REJECTED')error.stageRejectionOnly=dbStagesRejectionOnly(data);
  if(details?.operation&&dataopsSameContext(context,details.context))error.observation=details.operation;throw error;}
 if(!dataopsSameContext(context,data.context))throw Error(t('Request context changed; response ignored.'));
 return data;
}
async function dataopsTask(p,task){
 if(p.pending||p.trackingPending)return;p.pending=true;const epoch=p.epoch,stamp=dataopsRecoveryStamp(p);p.status.className='status-line';p.status.setAttribute('role','status');ui(p.status,'Pending');dataopsRender(p);
 try{await task(()=>epoch===p.epoch&&!p.invalid&&p.principal===dbPrincipal);}catch(error){if(epoch===p.epoch&&stamp===dataopsRecoveryStamp(p)&&p.principal===dbPrincipal){if(dataopsAuthError(error))dataopsInvalidate(p);p.status.className='error';p.status.setAttribute('role','alert');ui(p.status,error.message);}}
 finally{p.pending=false;dataopsRender(p);}
}
async function dataopsRefresh(p){
 dataopsPurgeForeignDraft(p);
 p.started=true;++p.epoch;p.inventoryLoading=true;p.inventoryReady=false;p.aiOrigin=null;p.reviewToken=null;p.publishReview=null;p.publishImpact=null;const epoch=p.epoch,role=p.role,database=p.database,schema=p.schema,principal=dbPrincipal;
 dataopsPurgeLoadedModel(p);
 p.metadata=null;p.wapEvidence=null;if(p.op){p.op.protected=false;p.op.operation=null;}
 for(const key of ['build','audit','publication'])$(p.id+'-'+key)?.replaceChildren();
 for(const source of p.sources||[])source.table.value='';
 p.invalid=true;p.preview=null;p.model=null;p.models=[];p.tables=[];p.objects=[];$(p.id+'-sample')?.replaceChildren();dataopsRender(p);
 const current=()=>epoch===p.epoch&&role===p.role&&database===p.database&&principal===dbPrincipal;
 try{
  const items=await dbReadInventory(role);if(!current()||!dbInventoryCurrent(items))return;dbApplyInventory(items,role);p.databases=items;
  const d=items.find(d=>d.id===database),result=d?await dbAPI('/api/v1/databases/'+encodeURIComponent(database)+'/schemas',role):{items:[]};
  if(!current()||!dbInventoryCurrent(items))return;p.schemas=result.items||[];
  if(d&&p.schemas.some(s=>s.id===schema)){
   p.context=dataopsFreeze(dataopsContext(p));p.principal=principal;p.invalid=false;
   // Inventory all authorized schemas for same-DB source and target selectors.
   const tables=[];
   for(const s of p.schemas.filter(s=>s.capabilities?.browse)){
    let cursor='';do{
     const url='/api/v1/schemas/'+encodeURIComponent(s.id)+'/objects?limit=100'+(cursor?'&cursor='+encodeURIComponent(cursor):'');
     const page=await dbAPI(url,role);if(!current()||!dbInventoryCurrent(items))return;
     tables.push(...(page.items||[]).filter(o=>o.kind==='TABLE'&&dataopsRef(o)&&o.ref.database_id===database&&o.ref.schema_id===s.id).map(o=>({...o,label:s.sql_name+'.'+o.physical_name})));
     cursor=page.next_cursor||'';
    }while(cursor);
   }
   p.tables=tables;p.objects=tables.filter(o=>o.ref.schema_id===schema);
   if(p.id==='p35-wap'&&dataopsAllowed(p,'wap_models')){const reply=await dataopsRequest('/api/v1/wap/models',p.context);if(!current())return;p.models=reply.items||[];}
  }else{p.context=null;p.schemas=result.items||[];}
  if(current()){p.inventoryLoading=false;p.inventoryReady=!p.invalid;p.inventoryContext=p.context;ui(p.status,p.invalid?'Choose an authorized context.':'Ready');dataopsRender(p,true);}
 }catch(error){if(current()){p.inventoryLoading=false;dataopsInvalidate(p);ui(p.status,error.message);}}
}

// Manual Ingest/WAP recovery stores intent handles only, never replay bodies.
const dataopsRecoveryPaths=Object.freeze({'manual-ingest':'/api/v1/ingests','manual-wap-build':'/api/v1/wap/build','manual-wap-audit':'/api/v1/wap/audit','manual-wap-publish':'/api/v1/wap/publish','manual-wap-rollback':'/api/v1/wap/rollback'});
const dataopsRecoveryStates=['UNCONFIRMED','ACTIVE','CANCELING','UNKNOWN','FINISHED','FAILED','CANCELED','REJECTED'];
const dataopsRecoveryOwners=new Map();let dataopsRecoveryRevision=0;
const dataopsRecoveryKey=owner=>'shirokuma.manual-dataops.v1.'+encodeURIComponent(owner);
const dataopsRecoveryText=value=>typeof value==='string'&&!!value.trim()&&value.length<=256&&!/[\x00-\x1f\x7f]/.test(value);
const dataopsRecoveryID=value=>typeof value==='string'&&/^[A-Za-z0-9_-]{1,128}$/.test(value);
function dataopsRecoveryContext(c){return dbExplanationSameContext(c,c)&&dbExplanationContextKeys.every(k=>['binding_revision','grant_revision'].includes(k)?(k==='grant_revision'&&c[k]===null)||Number.isSafeInteger(c[k])&&c[k]>0:typeof c[k]==='string'&&!!c[k].trim());}
function dataopsRecoveryValid(h){
 return dbSavedExact(h,['version','panel','action','key','context','time','id','state','uncertain'])&&h.version===1&&Object.hasOwn(dataopsRecoveryPaths,h.action)&&h.panel===(h.action==='manual-ingest'?'p35-ingest':'p35-wap')&&dataopsRecoveryText(h.key)&&dataopsRecoveryContext(h.context)&&Number.isSafeInteger(h.time)&&h.time>0&&(h.id===null||dataopsRecoveryID(h.id))&&dataopsRecoveryStates.includes(h.state)&&typeof h.uncertain==='boolean';
}
function dataopsRecoveryList(saved){return Array.isArray(saved)&&saved.length<=25&&saved.every(dataopsRecoveryValid)&&new Set(saved.map(h=>h.key)).size===saved.length;}
function dataopsRecoveryOwner(owner=dbPrincipal){
 if(dataopsRecoveryOwners.has(owner))return dataopsRecoveryOwners.get(owner);
 const store={owner,ops:[],error:false,lastRaw:null};dataopsRecoveryOwners.set(owner,store);
 try{
  const raw=localStorage.getItem(dataopsRecoveryKey(owner)),saved=raw===null?[]:JSON.parse(raw);
  if(!dataopsRecoveryList(saved))throw Error('storage');
  store.lastRaw=raw;
  store.ops=saved.map(h=>({...h,context:dataopsFreeze(h.context),path:dataopsRecoveryPaths[h.action],principal:owner,restored:true,ambiguous:h.uncertain,protected:false,operation:null}));
 }catch{store.error=true;}
 return store;
}
function dataopsRecoveryDTO(op){return {version:1,panel:op.panel,action:op.action,key:op.key,context:op.context,time:op.time,id:op.id,state:dataopsState(op)||'UNCONFIRMED',uncertain:!!op.ambiguous};}
function dataopsRecoveryWrite(store){
 try{
  // Recheck storage on every write, including fresh submission preflight.
  const raw=localStorage.getItem(dataopsRecoveryKey(store.owner));
  if(store.error||raw!==store.lastRaw||raw!==null&&!dataopsRecoveryList(JSON.parse(raw)))throw Error('storage');
  const next=store.ops.map(dataopsRecoveryDTO);if(!dataopsRecoveryList(next))throw Error('storage');
  const rawNext=JSON.stringify(next);
  localStorage.setItem(dataopsRecoveryKey(store.owner),rawNext);
  if(localStorage.getItem(dataopsRecoveryKey(store.owner))!==rawNext)throw Error('storage');
  store.lastRaw=rawNext;return true;
 }catch{store.error=true;return false;}
}
function dataopsRecoveryNotice(p){ui(p.recoveryNotice,'Recovery storage unavailable or full. Keep the original key and draft; a fresh request is blocked.');}
function dataopsRecoverySave(op){
 if(!op.panel)return;
 const store=dataopsRecoveryOwner(op.principal);
 if(!dataopsRecoveryWrite(store)){for(const p of dataopsPanels.values())if(p.id===op.panel&&dbPrincipal===op.principal)dataopsRecoveryNotice(p);}
}
function dataopsRecoveryHeld(p){const store=dataopsRecoveryOwner();return store.error||store.ops.length>=25||store.ops.some(op=>op.panel===p.id&&dataopsUnresolved(op));}
const dataopsOwnedUnresolved=p=>p.op?.principal===dbPrincipal&&dataopsUnresolved(p.op);
function dataopsRecoverySelected(p){return dataopsRecoveryOwner().ops.find(op=>op.panel===p.id&&op.key===p.recoverySelection);}
function dataopsRecoveryBoundary(){++dataopsRecoveryRevision;}
function dataopsRecoveryStamp(p){return JSON.stringify([dataopsRecoveryRevision,p.epoch,dbPrincipal,currentRole,dbMaximumRole,dbEpoch,dbAuthorizationVersion,dbRoleVersion(p.role),p.role,p.context,activeTab,language]);}
function dataopsRecoveryAuthorized(p,op){
 try{return op.principal===dbPrincipal&&p.principal===dbPrincipal&&!p.invalid&&p.inventoryReady&&!p.inventoryLoading&&dataopsExportAuthorized(op)&&dataopsSameContext(p.inventoryContext,op.context)&&dataopsSameContext(p.context,op.context)&&dataopsSameContext(dataopsContext(p),op.context);}
 catch{return false;}
}
async function dataopsRecoveryAccess(p,op,current){
 const deny=()=>{if(current())dataopsInvalidate(p);};
 const items=await dbReadInventory(op.context.selected_role,current,deny);
 if(!current()||!dbInventoryCurrent(items))return false;
 if(!items.some(d=>d.id===op.context.database_id&&d.sql_catalog===op.context.sql_catalog)){deny();return false;}
 dbApplyInventory(items,op.context.selected_role);
 if(!current()||!dataopsRecoveryAuthorized(p,op))return false;
 try{
  const reply=await dbAPI('/api/v1/databases/'+encodeURIComponent(op.context.database_id)+'/schemas',op.context.selected_role);
  if(!current())return false;
  if(!Array.isArray(reply?.items)||!reply.items.some(s=>s.id===op.context.schema_id&&s.database_id===op.context.database_id&&s.sql_name===op.context.sql_schema&&s.capabilities?.query===true)){deny();return false;}
  return dataopsRecoveryAuthorized(p,op);
 }catch(error){if(dataopsAuthError(error))deny();throw error;}
}

function dataopsRecoveryCanCancel(p,op){
 return !!op&&op.principal===dbPrincipal&&dbRoleWithinHeader(op.context.selected_role)&&dataopsCanCancel(op)&&!p.pending&&!p.trackingPending&&!(p.op!==op&&dataopsOwnedUnresolved(p));
}
function dataopsRecoveryCancel(p){
 const op=dataopsRecoverySelected(p);if(!dataopsRecoveryCanCancel(p,op))return;
 p.op=op;return dataopsObserve(p,true);
}
const dataopsRecoveryActionLabel=action=>t({'manual-ingest':'Ingest','manual-wap-build':'Stage','manual-wap-audit':'Audit','manual-wap-publish':'Publish','manual-wap-rollback':'Rollback'}[action]);
function dataopsRecoveryWhen(time,seconds=false){
 const date=new Date(time);
 if(!Number.isFinite(date.getTime()))return '';
 return date.toLocaleString(undefined,{year:'numeric',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',...(seconds?{second:'2-digit'}:{})});
}
function dataopsRecoveryRender(p){
 if(!p.recovery)return;
 const store=dataopsRecoveryOwner(),items=store.ops.filter(op=>op.panel===p.id);
 const ownerChanged=p.recoveryOwner!==dbPrincipal;
 if(ownerChanged||!items.some(op=>op.key===p.recoverySelection))p.recoverySelection='';
 p.recoveryOwner=dbPrincipal;
 const records=items.map(op=>{
  const action=dataopsRecoveryActionLabel(op.action),stateKey=dataopsState(op)||'UNCONFIRMED',state=t(stateKey)||t('UNCONFIRMED');
  return {op,action,state,minute:dataopsRecoveryWhen(op.time)};
 });
 const counts=new Map();
 for(const record of records){const key=[record.action,record.state,record.minute].join('|');counts.set(key,(counts.get(key)||0)+1);}
 dataopsOptions($(p.id+'-recovery-select'),records.map(record=>{
  const key=[record.action,record.state,record.minute].join('|');
  const when=counts.get(key)>1?dataopsRecoveryWhen(record.op.time,true):record.minute;
  return [record.op.key,[record.action,record.state,when].filter(Boolean).join(' · ')];
 }),p.recoverySelection,'Saved original request');
 const op=dataopsRecoverySelected(p),held=items.some(item=>dataopsUnresolved(item));
 if(p.id==='p35-ingest'){
  p.recovery.hidden=!items.length&&!store.error&&store.ops.length<25;
  p.recoveryHistory.hidden=!items.length;
  if(ownerChanged||held&&p.recoveryHeldHelp.hidden)p.recoveryHistory.open=held;
 }
 p.recoveryHeldHelp.hidden=!held;ui(p.recoveryHeldHelp,held?'Select the saved operation and reconnect or cancel it before creating a fresh request.':'');
 p.recoveryContext.hidden=!op;
 if(op)ui(p.recoveryContext,'Saved request context: {context}. {guidance}',()=>({context:[op.context.sql_catalog,op.context.sql_schema,op.context.selected_role,op.context.warehouse_id].join(' / '),guidance:t(dataopsRecoveryAuthorized(p,op)?'Refresh panel context if access changed, then reconnect explicitly.':'Match the original database, schema and execution role manually, then refresh panel context.')}));
 else ui(p.recoveryContext,'');
 $(p.id+'-recovery-cancel').disabled=!dataopsRecoveryCanCancel(p,op);
 $(p.id+'-reconnect').disabled=!!(p.pending||p.trackingPending)||!op||!dataopsRecoveryAuthorized(p,op);
 $(p.id+'-remove-handle').disabled=!!(p.pending||p.trackingPending)||!op||!dataopsTerminal(op);
 if(store.error||store.ops.length>=25)dataopsRecoveryNotice(p);else ui(p.recoveryNotice,'');
}
function dataopsRecoveryInstall(p){
 p.recovery=dataopsNode('div',p.id+'-recovery',p.body);
 let controls=p.recovery;
 if(p.id==='p35-ingest'){
  p.recoveryHistory=workflowDetails(p.recovery,'Saved ingestion operations');
  controls=dataopsNode('div','',p.recoveryHistory);
 }
 controls.className='toolbar';
 dataopsField(controls,p.id+'-recovery-select','Saved original request','select').onchange=()=>{p.recoverySelection=$(p.id+'-recovery-select').value;dataopsRecoveryBoundary();dataopsRecoveryRender(p);};
 dataopsButton(controls,p.id+'-reconnect','Reconnect original operation',()=>dataopsReconnect(p));
 dataopsButton(controls,p.id+'-recovery-cancel','Cancel selected saved operation',()=>dataopsRecoveryCancel(p));
 p.recoveryContext=dataopsNode('p',p.id+'-recovery-context',controls);p.recoveryContext.className='help';p.recoveryContext.setAttribute('role','status');p.recoveryContext.setAttribute('aria-live','polite');
 dataopsButton(controls,p.id+'-remove-handle','Remove terminal handle',()=>{
  const op=dataopsRecoverySelected(p);if(p.pending||p.trackingPending||!op||!dataopsTerminal(op))return;
  const store=dataopsRecoveryOwner(),previous=store.ops;store.ops=previous.filter(item=>item!==op);
  if(!dataopsRecoveryWrite(store)){store.ops=previous;dataopsRecoveryNotice(p);return;}
  p.recoverySelection='';if(p.op===op)p.op=null;for(const panel of dataopsPanels.values())dataopsRender(panel);
 });
 dataopsNode('p','',controls,'Choose the original account, role, database and schema, then refresh panel context and reconnect. Reconnect inspects the saved operation without resubmitting SQL; normal inspection may reconcile state or attempt cancellation after access loss. A missing key does not prove the request was unsent. Removing a terminal handle only removes this browser handle.').className='help';
 p.recoveryHeldHelp=dataopsNode('p',p.id+'-recovery-held',p.recovery);p.recoveryHeldHelp.className='help';p.recoveryHeldHelp.setAttribute('role','status');
 p.recoveryNotice=dataopsNode('p',p.id+'-recovery-notice',p.recovery);p.recoveryNotice.setAttribute('role','status');p.recoveryNotice.setAttribute('aria-live','polite');
 if(p.recoveryHistory)p.recovery.prepend(p.recoveryHeldHelp,p.recoveryNotice);
 for(const event of ['input','change'])p.form.addEventListener(event,dataopsRecoveryBoundary);
}
async function dataopsReconnect(p){
 const op=dataopsRecoverySelected(p);if(p.pending||p.trackingPending||!op||!dataopsRecoveryAuthorized(p,op))return;
 p.op=op;op.protected=false;op.operation=null;const tracking={};p.trackingToken=tracking;p.trackingPending=true;
 const stamp=dataopsRecoveryStamp(p),sequence=op.sequence=(op.sequence||0)+1;
 const current=()=>p.op===op&&sequence===op.sequence&&stamp===dataopsRecoveryStamp(p)&&dataopsRecoveryAuthorized(p,op);
 dataopsRender(p);
 try{
  // Refresh current account and role-specific inventory before requesting saved data.
  if(!await dataopsRecoveryAccess(p,op,current))return;
  if(!op.id){
   const reply=await dataopsRequest('/api/v1/dataops/by-key?route='+encodeURIComponent(op.action)+'&key='+encodeURIComponent(op.key),op.context),r=reply.recovery;
   if(!dbSavedExact(reply,['context','recovery'])||!dbSavedExact(r,['operation_id','route','state'])||r.route!==op.action||!dataopsRecoveryID(r.operation_id)||!dataopsRecoveryStates.slice(1,-1).includes(r.state)||!dataopsSameContext(reply.context,op.context))throw Error(t('Invalid recovery response.'));
   if(!current()||op.id||dataopsTerminal(op))return;
   op.id=r.operation_id;op.state=r.state;op.ambiguous=r.state==='UNKNOWN';dataopsRecoverySave(op);
  }
  if(!current())return;
  const reply=await dataopsRequest('/api/v1/dataops/'+encodeURIComponent(op.id),op.context);
  if(!current())return;
  dataopsAccept(op,reply.operation,false);
  const visible=await dataopsRecoveryAccess(p,op,current);
  if(visible&&current())dataopsAccept(op,reply.operation,true);
  if(op.protected){dataopsCaptureWap(p,op.operation,true);ui(p.status,op.state);}
 }catch(error){
  if(sequence!==op.sequence)return;
  if(current()&&error.observation&&!dataopsTerminal(op))dataopsAccept(op,error.observation,false);
  op.protected=false;op.operation=null;
  if(current()&&dataopsAuthError(error))dataopsInvalidate(p);
  if(stamp===dataopsRecoveryStamp(p))ui(p.status,'Reconnect unavailable. Keep the original handle; missing does not mean unsent.');
 }finally{if(p.trackingToken===tracking){p.trackingToken=null;p.trackingPending=false;dataopsRender(p);}}
}
Object.assign(i18n.ja,{
 'Cancel selected saved operation':'選択した保存済み処理をキャンセル',
 'Saved request context: {context}. {guidance}':'保存した要求のコンテキスト：{context}。{guidance}',
 'Match the original database, schema and execution role manually, then refresh panel context.':'元のデータベース・スキーマ・実行ロールを手動で選び、パネルのコンテキストを更新してください。',
 'Refresh panel context if access changed, then reconnect explicitly.':'アクセスが変わった場合はパネルのコンテキストを更新し、明示的に再接続してください。',
 'Select the saved operation and reconnect or cancel it before creating a fresh request.':'新しい要求を作成する前に、保存済みの処理を選び、再接続またはキャンセルしてください。',
 'Saved original request':'保存済みの処理','Reconnect original operation':'元の処理へ再接続','Remove terminal handle':'終了済みの追跡情報を削除','UNCONFIRMED':'受付未確認',
 'Recovery storage unavailable or full. Keep the original key and draft; a fresh request is blocked.':'復旧用ストレージが使用不可または上限に達しました。元のキーと入力を保持してください。新規要求は停止しています。',
 'Reconnect unavailable. Keep the original handle; missing does not mean unsent.':'再接続できません。元のハンドルを保持してください。未検出でも未送信とは限りません。',
 'Invalid recovery response.':'復旧応答が不正です。',
 'Resending uses the original request body and key and may accept execution if the first request never arrived. Reloaded handles cannot resend.':'再送は元の本文とキーを使用し、初回要求が届いていなければ実行を受け付ける可能性があります。再読み込みしたハンドルからの再送はできません。',
 'Resend original request (may accept execution)':'元の要求を再送（実行を受け付ける可能性あり）',
 'Choose the original account, role, database and schema, then refresh panel context and reconnect. Reconnect inspects the saved operation without resubmitting SQL; normal inspection may reconcile state or attempt cancellation after access loss. A missing key does not prove the request was unsent. Removing a terminal handle only removes this browser handle.':'元のアカウント・ロール・DB・スキーマを選び、パネルのコンテキストを更新して再接続してください。SQLの再送信は行わず、保存済み処理を通常の方法で確認します。状態調整やアクセス喪失後のキャンセルを試みる場合があります。キー未検出でも未送信とは限りません。終了済みハンドルの削除はこのブラウザ内の記録のみを削除します。'
});

function workbenchAtlasHost(kind){
 if(kind==='ingestion-file')return workbenchAtlasPeek('ingestion-file');
 if(kind==='ingestion-stages')return workbenchAtlasPeek('ingestion-stages');
 if(kind==='transformation-models')return workbenchAtlasPeek('transformation-atlas-models');
 if(kind==='transformation-marts')return workbenchAtlasPeek('transformation-atlas-marts');
 if(kind==='transformation-dbt')return workbenchAtlasPeek('transformation-atlas-dbt');
 return null;
}
function workbenchAtlasPeek(id){const node=$(id);return node&&node.parentElement?node:null;}
function workbenchAtlasMessageSlot(kind){
 return workbenchAtlasPeek(kind==='ingestion'?'atlas-ingestion-message':'atlas-transformation-message');
}
function workbenchAtlasShell(){
 // The static document already carries the Atlas shell. The Node component
 // harness starts from an empty root, so create the same structure there.
 for(const [section,kind,heading] of [['ingestion','ingestion','Ingestion & Stages'],['transformation','transformation','Transformation']]){
  const root=$(section);if(!root)continue;
  let flow=workbenchAtlasPeek(kind+'-atlas');
  if(!flow){
   flow=document.createElement('div');flow.id=kind+'-atlas';flow.className='atlas-flow';
   root.prepend(flow);
  }
  if(!workbenchAtlasPeek(kind+'-heading')){const h=document.createElement('h1');h.id=kind+'-heading';ui(h,heading);flow.append(h);}
  if(!workbenchAtlasPeek(kind+'-role-field')){const f=document.createElement('div');f.id=kind+'-role-field';f.className='atlas-fields';flow.append(f);}
  if(!workbenchAtlasMessageSlot(kind)){const m=document.createElement('div');m.id='atlas-'+kind+'-message';m.className='atlas-message-slot';m.tabIndex=-1;flow.append(m);}
  if(kind==='ingestion'){
   if(!workbenchAtlasPeek('ingestion-method')){
    const nav=document.createElement('nav');nav.id='ingestion-method';nav.className='atlas-actions';nav.setAttribute('aria-label',t('Ingestion method'));
    for(const [id,key,href] of [['ingestion-tab-file','File import','#ingestion'],['ingestion-tab-stages','Saved stages','#ingestion/stages']]){
     const link=document.createElement('a');link.id=id;link.className='atlas-link';link.href=href;ui(link,key);nav.append(link);
    }
    flow.append(nav);
   }
   if(!workbenchAtlasPeek('ingestion-file')){const host=document.createElement('div');host.id='ingestion-file';flow.append(host);}
   if(!workbenchAtlasPeek('ingestion-stages')){const host=document.createElement('div');host.id='ingestion-stages';host.hidden=true;flow.append(host);}
  }else{
   if(!workbenchAtlasPeek('transformation-atlas-models')){const host=document.createElement('div');host.id='transformation-atlas-models';flow.append(host);}
   if(!workbenchAtlasPeek('transformation-publications')){
    const sectionNode=document.createElement('section');sectionNode.id='transformation-publications';sectionNode.className='atlas-publications';sectionNode.setAttribute('aria-labelledby','transformation-publications-heading');
    const h=document.createElement('h2');h.id='transformation-publications-heading';ui(h,'Published MARTS');sectionNode.append(h);
    const host=document.createElement('div');host.id='transformation-atlas-marts';sectionNode.append(host);flow.append(sectionNode);
   }
   if(!workbenchAtlasPeek('transformation-atlas-dbt')){const host=document.createElement('div');host.id='transformation-atlas-dbt';flow.append(host);}
  }
  const legacy=$(kind+'-legacy');if(legacy)legacy.hidden=true;
 }
}
function workbenchAtlasShowStatus(kind){
 const stages=kind==='ingestion'&&$('ingestion-stages')&&$('ingestion-stages').hidden===false;
 for(const [id,active] of [['p35-ingest-status',kind==='ingestion'&&!stages],['db-stages-status',kind==='ingestion'&&stages],['p35-wap-status',kind==='transformation']]){
  const node=$(id);if(node)node.hidden=!active;
 }
}
function workbenchAtlasMessage(kind,text){
 const slot=workbenchAtlasMessageSlot(kind);if(!slot)return;
 slot.dataset.message=String(text??'').trim();
}
function workbenchAtlasIngestionView(){return $('ingestion-stages')&&$('ingestion-stages').hidden===false?'stages':'file';}
async function workbenchAtlasShowIngestion(view,{remember=true,focus=false}={}){
 const stages=view==='stages';
 if(stages&&typeof dbStagesNavAllowed==='function'&&typeof dbStages!=='undefined'&&dbStages.installed&&!dbStages.invalid&&!dbStagesNavAllowed()){workbenchAtlasMessage('ingestion','Stage access unavailable. Refresh an authorized context.');return;}
 if($('ingestion-file'))$('ingestion-file').hidden=stages;
 if($('ingestion-stages'))$('ingestion-stages').hidden=!stages;
 for(const [id,active] of [['ingestion-tab-file',!stages],['ingestion-tab-stages',stages]]){
  const link=$(id);if(!link)continue;
  if(active)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');
  link.setAttribute('aria-selected',String(active));
  link.tabIndex=active?0:-1;
 }
 workbenchAtlasShowStatus('ingestion');
 if(remember&&typeof workbenchRememberPage==='function'){
  const previous=workbenchNavigationPush;workbenchNavigationPush=true;
  try{workbenchRememberPage('ingestion',stages?'stages':'');}finally{workbenchNavigationPush=previous;}
 }
 if(stages){if(typeof dbStagesOpen==='function'){const pending=typeof dbInventoryPending!=='undefined'?dbInventoryPending.get('admin'):null;if(pending?.done)await pending.done;return dbStagesOpen();}}
 else if(typeof dataopsOpen==='function')dataopsOpen('ingestion');
 if(focus){const heading=stages?$('db-stages-heading'):$('p35-ingest-heading');heading?.focus?.({preventScroll:true});}
}
function workbenchAtlasStageTabState(){
 const enabled=typeof dbStagesNavAllowed==='function'&&dbStagesNavAllowed();
 const tab=$('ingestion-tab-stages');if(tab){tab.disabled=!enabled;tab.setAttribute('aria-disabled',String(!enabled));}
}
function workbenchAtlasSyncIngestion(){
 workbenchAtlasShowStatus('ingestion');
 const p=dataopsPanels.get('p35-ingest');
 if(!p)return;
 const stages=workbenchAtlasIngestionView()==='stages';
 const ingestRole=$('p35-ingest-role')?.parentElement,stageRole=$('db-stages-role')?.parentElement;
 if(ingestRole)ingestRole.hidden=stages;
 if(stageRole)stageRole.hidden=!stages;
 if(!stages){
  const message=p.status?.textContent?.trim()||$('p35-ingest-file-status')?.textContent?.trim()||'';
  workbenchAtlasMessage('ingestion',message);
 }
}
function workbenchAtlasSyncTransform(p=dataopsPanels.get('p35-wap')){
 workbenchAtlasShowStatus('transformation');
 if(!p)return;
 const source=$('transformation-source');
 if(source){
  dataopsOptions(source,p.tables.map((o,i)=>[String(i),o.label]),p.sources?.[0]?.table?.value||'','Choose RAW table');
 }
 const database=p.context?.sql_catalog||p.databases.find(d=>d.id===p.database)?.sql_catalog||'—';
 const line=$('transformation-destination-line');
 if(line)ui(line,'RAW → {database}.SANDBOX → {database}.MARTS · same database',{database});
 const summary=$('transformation-model-history-summary');
 if(summary)ui(summary,'Model history ({count} versions)',{count:p.models?.length||0});
 const sourceLink=$('transformation-source-link');
 if(sourceLink){
  const index=p.sources?.[0]?.table?.value,object=index!==undefined&&index!==''?p.tables[Number(index)]:null,schema=p.schemas?.find(s=>s.id===object?.ref?.schema_id);
  if(object&&schema&&p.context?.sql_catalog){sourceLink.hidden=false;sourceLink.href='#explore/'+dbExplorePath({sql_catalog:p.context.sql_catalog},{sql_name:schema.sql_name},{physical_name:object.physical_name});}
  else sourceLink.hidden=true;
 }
 const message=p.status?.textContent?.trim()||'';
 if(message)workbenchAtlasMessage('transformation',message);
}
function workbenchAtlasClass(node,add){if(!node)return node;const set=new Set(String(node.className||'').split(' ').filter(Boolean));for(const name of String(add||'').split(' '))if(name)set.add(name);node.className=[...set].join(' ');return node;}
function workbenchAtlasMove(node,parent){if(node&&parent&&node.parentElement!==parent)parent.append(node);return node;}
function workbenchAtlasIndexOf(parent,node){
 if(!parent)return -1;
 for(let index=0;index<parent.children.length;index++)if(parent.children[index]===node)return index;
 return -1;
}
function workbenchAtlasPlace(parent,node,index){
 if(!parent||!node)return node;
 if(index>=parent.children.length){parent.append(node);return node;}
 const ref=parent.children[Math.max(0,index)];
 if(ref!==node)ref.before(node);
 return node;
}
function workbenchAtlasField(node){const wrap=node?.parentElement;if(wrap)workbenchAtlasClass(wrap,'atlas-field');workbenchAtlasClass(node,'field');return wrap;}
function workbenchAtlasTopField(node,key,parent){
 const wrap=node?.parentElement;if(!wrap||!parent)return wrap;
 const label=document.createElement('label');label.htmlFor=node.id;label.className='atlas-field-label';
 const text=document.createElement('span');text.className='atlas-label';ui(text,key);label.append(text,node);
 workbenchAtlasClass(node,'field');
 if(wrap.parentElement)wrap.hidden=true;
 parent.append(label);return label;
}
function workbenchAtlasInlineField(node,keyOverride){
 const wrap=node?.parentElement;if(!wrap)return wrap;
 const oldLabel=wrap.children?.[0],textNode=oldLabel?.children?.[0];
 const binding=(textNode&&uiBindings.get(textNode))||uiBindings.get(oldLabel)||{};
 const key=keyOverride||binding.key||oldLabel?.textContent||'';
 const label=document.createElement('label');label.htmlFor=node.id;label.className='atlas-field-label';
 const text=document.createElement('span');text.className='atlas-label';ui(text,key,binding.values||{});label.append(text,node);
 workbenchAtlasClass(node,'field');wrap.hidden=true;
 return label;
}
function workbenchAtlasHeading(id,label,parent,level=2){let node=$(id);if(!node){node=document.createElement('h'+level);node.id=id;ui(node,label);parent.append(node);}return node;}
function workbenchAtlasDetails(id,label,parent,{open=false}={}){
 let node=$(id);
 if(!node){node=document.createElement('details');node.id=id;const summary=document.createElement('summary');ui(summary,label);node.append(summary);parent.append(node);}
 node.open=open;return node;
}
function workbenchAtlasInstallIngestion(ingest){
 if(!ingest||!ingest.body)return;
  const roleWrap=workbenchAtlasTopField($('p35-ingest-role'),'Operation role',$('ingestion-role-field'));
  const form=ingest.form;
  // Capture every node before detaching the destination row from the document.
  // The browser getElementById returns null for detached descendants.
  let heading=$('p35-ingest-destination-heading');
  const fields=$('p35-ingest-destination-fields')||document.createElement('div');
  const database=$('p35-ingest-database'),schema=$('p35-ingest-schema'),mode=$('p35-ingest-mode');
  const name=$('p35-ingest-name'),targetSelect=$('p35-ingest-target'),keys=$('p35-ingest-keys'),refresh=$('p35-ingest-refresh');
  const fileDetails=$('p35-ingest-file-details'),selected=$('p35-ingest-selected-file');
  const previewActions=$('p35-ingest-preview')?.parentElement,sample=$('p35-ingest-sample');
  const impact=$('p35-ingest-impact'),confirmActions=$('p35-ingest-confirm')?.parentElement,options=$('p35-ingest-options');
  const fileStatus=$('p35-ingest-file-status'),availability=$('p35-ingest-availability'),status=$('p35-ingest-status');
  const section=document.createElement('section');section.id='p35-ingest-destination';
  if(!heading){heading=document.createElement('h2');heading.id='p35-ingest-destination-heading';}
  ui(heading,'Destination and update method');section.append(heading);
  fields.id='p35-ingest-destination-fields';workbenchAtlasClass(fields,'atlas-fields');
  section.append(fields);
  for(const [node,key] of [[database,'Database'],[schema,'Schema'],[mode,'Update method'],[name,'RAW table name (letters, digits, _; start with a letter)'],[targetSelect,'Existing table'],[keys,'Upsert key columns (required for upsert)']]){
   const label=workbenchAtlasInlineField(node,key);if(label)workbenchAtlasPlace(fields,label,fields.children.length);
  }
  if(refresh){const old=refresh.parentElement;workbenchAtlasClass(refresh,'atlas-inline-action');workbenchAtlasMove(refresh,fields);if(old)old.hidden=true;}
  const modeValue=mode?.value||'';
  if(name)name.parentElement.hidden=false;
  if(targetSelect)targetSelect.parentElement.hidden=!modeValue||modeValue==='create';
  if(keys)keys.parentElement.hidden=modeValue!=='upsert';
  const target=document.createElement('p');target.id='p35-ingest-target-summary';target.className='atlas-target-line';
  const targetLabel=document.createElement('span');targetLabel.className='atlas-target-label';ui(targetLabel,'Target:');
  const targetCode=document.createElement('code');targetCode.id='p35-ingest-target-value';target.append(targetLabel,targetCode);section.append(fields,target);
  if(fileDetails){
   workbenchAtlasClass(fileDetails,'atlas-file-details');
   const summary=fileDetails.children[0];
   if(summary)ui(summary,'Input file (CSV / JSON / JSONL · up to 10 MiB)');
  }
  if(selected)selected.hidden=true;
  if(previewActions)workbenchAtlasClass(previewActions,'atlas-actions');
  if(impact)impact.removeAttribute('role');
  if(confirmActions)workbenchAtlasClass(confirmActions,'atlas-actions');
  if(options)options.open=false;
  for(const [index,node] of [fileDetails,selected,section,previewActions,sample,impact,confirmActions,options].entries())if(node)workbenchAtlasPlace(form,node,index);
  if(fileStatus)fileStatus.removeAttribute('role');
  if(availability)availability.removeAttribute('role');
  // One primary status element at the mock-up message position.
  if(status){workbenchAtlasMove(status,workbenchAtlasMessageSlot('ingestion'));status.hidden=false;}
  if(ingest.body.children[0]?.tagName==='H2')ingest.body.children[0].hidden=true;
  workbenchAtlasClass(ingest.root,'atlas-panel');
}
function workbenchAtlasInstall(){
 workbenchAtlasShell();
 workbenchAtlasInstallIngestion(dataopsPanels.get('p35-ingest'));
 // ---- Ingestion: saved stages ----------------------------------------------
 const stages=$('db-stages-panel');
 if(stages){
  const body=stages.children[0];
  const roleWrap=workbenchAtlasTopField($('db-stages-role'),'Operation role',$('ingestion-role-field'));
  const contextFields=document.createElement('div');contextFields.id='db-stages-context-fields';contextFields.className='atlas-fields';
  for(const [id,key] of [['db-stages-database','Database'],['db-stages-schema','Schema']]){const label=workbenchAtlasInlineField($(id),key);if(label)workbenchAtlasMove(label,contextFields);}
  const refresh=$('db-stages-refresh');if(refresh){const old=refresh.parentElement;workbenchAtlasClass(refresh,'atlas-inline-action');workbenchAtlasMove(refresh,contextFields);if(old)old.hidden=true;}
  const heading=$('db-stages-heading');if(heading)workbenchAtlasMove(heading,body);
  const saved=$('db-stages-list')?.parentElement?.parentElement;
  if(saved){workbenchAtlasClass(saved,'atlas-fields');workbenchAtlasField($('db-stages-list'));}
  const status=$('db-stages-status');if(status){workbenchAtlasMove(status,workbenchAtlasMessageSlot('ingestion'));status.hidden=true;}
  // Stage overview: state, output, latest run and next plan.
  let overview=workbenchAtlasPeek('db-stages-overview');
  if(!overview){
   overview=document.createElement('section');overview.id='db-stages-overview';overview.setAttribute('aria-labelledby','db-stages-stage-title');
   const title=document.createElement('h2');title.id='db-stages-stage-title';ui(title,'Saved stage');overview.append(title);
   const current=$('db-stages-current');if(current){current.className='tiny muted';overview.append(current);}
   const kv=document.createElement('dl');kv.id='db-stages-overview-kv';kv.className='kv';
   for(const [key,label,node] of [['context','Stage state',$('db-stages-context')],['target','Destination',null],['latest','Latest run',$('db-stages-latest')],['next','Next plan',$('db-stages-next')]]){
    const dt=document.createElement('dt');ui(dt,label);const dd=document.createElement('dd');
    if(node)dd.append(node);
    else{const target=document.createElement('p');target.id='db-stages-output';dd.append(target);}
    kv.append(dt,dd);
   }
   overview.append(kv);body.append(overview);
  }
  let scheduleSlot=workbenchAtlasPeek('db-stages-schedule-slot');
  if(!scheduleSlot){scheduleSlot=document.createElement('div');scheduleSlot.id='db-stages-schedule-slot';}
  const definition=$('db-stages-form')?.parentElement;
  if(definition&&definition.tagName==='DETAILS'){definition.open=false;if(definition.children[0])ui(definition.children[0],'Stage definition');}
  for(const [index,node] of [heading,$('db-stages-note'),contextFields,saved,overview,scheduleSlot,definition].entries())if(node)workbenchAtlasPlace(body,node,index);
  let records=workbenchAtlasPeek('db-stages-records');
  if(!records){records=document.createElement('details');records.id='db-stages-records';const summary=document.createElement('summary');ui(summary,'Run and export records');records.append(summary);body.append(records);}
  records.className='atlas-records';
  const exportRow=$('db-stages-export')?.parentElement;
  for(const id of ['db-stages-history','db-stages-history-list','db-stages-observe','db-stages-cancel','db-stages-run-resend','db-stages-run-detail','db-stages-receipts','db-stages-receipt','db-stages-inspect-receipt','db-stages-download','db-stages-receipt-detail','db-stages-export-source','db-stages-export-file','db-stages-export-format','db-stages-export']){
   const node=$(id);if(!node)continue;
   workbenchAtlasMove(node.tagName==='SELECT'||node.tagName==='INPUT'||id==='db-stages-export'?(node.parentElement||node):node,records);
  }
  if(exportRow)workbenchAtlasClass(exportRow,'toolbar');
  for(const node of [$('db-stages-scan'),$('db-stages-scans'),$('db-stages-scan-observe')])if(node)workbenchAtlasClass(node.parentElement,'toolbar');
  const definitionsHeading=$('db-stages-form')?.parentElement?.parentElement;
  if(definitionsHeading&&definitionsHeading!==body&&definitionsHeading.tagName==='DETAILS')workbenchAtlasMove(definitionsHeading,body);
  const roleToggle=()=>{const stagesView=workbenchAtlasIngestionView()==='stages';if(roleWrap)roleWrap.hidden=!stagesView;const ingestRole=$('p35-ingest-role')?.parentElement;if(ingestRole)ingestRole.hidden=stagesView;};
  roleToggle();
 }
 // ---- Transformation: model, candidate, history -----------------------------
 const wap=dataopsPanels.get('p35-wap');
 if(wap&&wap.form){
  const roleWrap=workbenchAtlasTopField($('p35-wap-role'),'Operation role',$('transformation-role-field'));
  const contextFields=document.createElement('div');contextFields.id='transformation-context-fields';contextFields.className='atlas-fields';
  for(const [id,key] of [['p35-wap-database','Database'],['p35-wap-schema','Schema']]){const label=workbenchAtlasInlineField($(id),key);if(label)workbenchAtlasMove(label,contextFields);}
  const refresh=$('p35-wap-refresh');if(refresh){const old=refresh.parentElement;workbenchAtlasClass(refresh,'atlas-inline-action');workbenchAtlasMove(refresh,contextFields);if(old)old.hidden=true;}
  wap.form.prepend(contextFields);
  const core=document.createElement('div');core.id='transformation-core-fields';core.className='atlas-fields';
  // RAW source mirror. It drives the first existing WAP source without duplicating state.
  const sourceField=document.createElement('label');sourceField.htmlFor='transformation-source';ui(sourceField,'Source RAW table');
  const source=document.createElement('select');source.id='transformation-source';source.className='field';source.onchange=()=>{
   const first=wap.sources?.[0];if(!first)return;first.table.value=source.value;first.table.onchange?.();
  };
  sourceField.append(source);core.append(sourceField);
  const modelWrap=workbenchAtlasInlineField($('p35-wap-model-id'),'Model name (letters, digits, _; start with a letter)');workbenchAtlasMove(modelWrap,core);
  const operationField=document.createElement('div');operationField.className='atlas-field';
  const operationLabel=document.createElement('span');operationLabel.className='atlas-label';ui(operationLabel,'Transformation operation');
  const operation=document.createElement('button');operation.id='transformation-operation';operation.type='button';operation.className='field';ui(operation,'Edit SQL');operation.onclick=()=>{const editor=$('p35-wap-editor');if(editor){editor.open=true;editor.children[0]?.focus?.();}};
  operationField.append(operationLabel,operation);core.append(operationField);
  const keyWrap=workbenchAtlasInlineField($('p35-wap-not-null'),'Quality key columns');workbenchAtlasMove(keyWrap,core);
  const destinationLine=document.createElement('p');destinationLine.id='transformation-destination-line';
  destinationLine.className='atlas-target-line';ui(destinationLine,'RAW → {database}.SANDBOX → {database}.MARTS · same database',{database:'—'});
  let sourceDetails=workbenchAtlasPeek('transformation-source-details');
  if(!sourceDetails){
   sourceDetails=document.createElement('details');sourceDetails.id='transformation-source-details';
   const summary=document.createElement('summary');ui(summary,'Source columns and rows');sourceDetails.append(summary);
   const note=document.createElement('p');note.id='transformation-source-note';ui(note,'Inspect the source columns and rows in Data Explorer.');sourceDetails.append(note);
   const link=document.createElement('a');link.id='transformation-source-link';link.className='textlink';ui(link,'Open source in Data Explorer');sourceDetails.append(link);
  }
  const editor=$('p35-wap-editor');if(editor){editor.open=false;if(editor.children[0])ui(editor.children[0],'SQL and sources');}
  const saveActions=$('p35-wap-save')?.parentElement;if(saveActions)workbenchAtlasClass(saveActions,'atlas-actions');
  const savedVersion=$('p35-wap-saved-version');
  const guide=$('p35-wap-guide');
  const steps=$('p35-wap-steps');
  if(guide&&!workbenchAtlasPeek('p35-wap-guide-details')){
   const d=document.createElement('details');d.id='p35-wap-guide-details';const s=document.createElement('summary');ui(s,'How the transformation proceeds');d.append(s);
   if(steps)d.append(steps);
   for(const id of ['p35-wap-guide-context','p35-wap-guide-model','p35-wap-action-target'])if($(id))d.append($(id));
   guide.append(d);
  }
  let candidate=workbenchAtlasPeek('transformation-candidate');
  if(guide&&!candidate){
   candidate=document.createElement('section');candidate.id='transformation-candidate';candidate.setAttribute('aria-labelledby','transformation-candidate-heading');
   const h=document.createElement('h2');h.id='transformation-candidate-heading';ui(h,'Candidate and quality checks');candidate.append(h,guide);
  }
  const status=$('p35-wap-status');if(status)workbenchAtlasMove(status,workbenchAtlasMessageSlot('transformation'));
  const availability=$('p35-wap-availability');if(availability)availability.removeAttribute('role');
  if(wap.body.children[0]?.tagName==='H2')wap.body.children[0].hidden=true;
  // Explicit order: context, the four top fields, target line, primary save,
  // saved version, then closed details and the candidate section.
  const form=wap.form;
  for(const [index,node] of [contextFields,core,destinationLine,sourceDetails,saveActions,savedVersion,editor,candidate].entries())if(node)workbenchAtlasPlace(form,node,index);
  // Saved model history.
  const models=$('p35-wap-models')?.parentElement;
  const history=$('p35-wap-history');
  let modelHistory=workbenchAtlasPeek('transformation-model-history');
  if(!modelHistory){modelHistory=document.createElement('details');modelHistory.id='transformation-model-history';const summary=document.createElement('summary');summary.id='transformation-model-history-summary';ui(summary,'Model history ({count} versions)',{count:0});modelHistory.append(summary);}
  if(models)workbenchAtlasMove(models,modelHistory);
  if(history){history.open=false;if(history.children[0])ui(history.children[0],'History, metadata and recovery');workbenchAtlasMove(history,modelHistory);}
  workbenchAtlasMove(modelHistory,form);
  const ai=$('p35-wap-ai-import');if(ai)ai.hidden=true;
  const aiActions=$('copilot-stage')?.parentElement;if(aiActions)aiActions.hidden=true;
  if(availability)workbenchAtlasMove(availability,form);
 }
 // ---- Transformation: published MARTS --------------------------------------
 const marts=$('db-marts-panel');
 if(marts){
  const body=marts.children[0];
  const update=$('db-marts-form')?.parentElement;
  if(update&&update.tagName==='DETAILS'){update.open=false;if(update.children[0])ui(update.children[0],'Review and save updates');}
  const selectors=$('db-marts-role')?.parentElement?.parentElement;
  const martsRole=$('db-marts-role')?.parentElement;
  if(martsRole)martsRole.hidden=true;
  const listWrap=workbenchAtlasField($('db-marts-list'));
  const listLabel=listWrap?.children?.[0];if(listLabel)ui(listLabel,'Published model');
  let publicationFields=workbenchAtlasPeek('transformation-publication-fields');
  if(!publicationFields){publicationFields=document.createElement('div');publicationFields.id='transformation-publication-fields';publicationFields.className='atlas-fields';}
  const heading=$('transformation-publications-heading');
  if(heading&&heading.parentElement)workbenchAtlasPlace(heading.parentElement,publicationFields,workbenchAtlasIndexOf(heading.parentElement,heading)+1);
  if(listWrap)workbenchAtlasMove(listWrap,publicationFields);
  const targetSummary=$('db-marts-target-summary');
  if(targetSummary)workbenchAtlasMove(targetSummary,publicationFields.parentElement);
  let summary=workbenchAtlasPeek('transformation-publication-summary');
  if(!summary){summary=document.createElement('p');summary.id='transformation-publication-summary';ui(summary,'No published model selected.');publicationFields.parentElement.append(summary);}
  let open=workbenchAtlasPeek('transformation-open-published');
  if(!open){open=document.createElement('a');open.id='transformation-open-published';open.className='textlink';ui(open,'Open published table');publicationFields.parentElement.append(open);}
  let rollback=workbenchAtlasPeek('transformation-rollback');
  if(!rollback){rollback=document.createElement('details');rollback.id='transformation-rollback';const summary=document.createElement('summary');ui(summary,'Publication history and rollback');rollback.append(summary);publicationFields.parentElement.append(rollback);}
  for(const id of ['db-marts-history','db-marts-history-refresh','p35-wap-publication','p35-wap-rollback']){const node=$(id);if(node)workbenchAtlasMove(node.tagName==='SELECT'?(node.parentElement||node):node,rollback);}
  if(update){
   update.open=false;if(update.children[0])ui(update.children[0],'Review and save updates');
   if(selectors)workbenchAtlasMove(selectors,update);
   for(const id of ['db-marts-status','db-marts-run-detail'])if($(id))workbenchAtlasMove($(id),update);
   workbenchAtlasMove(update,publicationFields.parentElement||body);
  }
  const historyHeading=$('db-marts-history-heading');if(historyHeading)historyHeading.hidden=true;
  workbenchAtlasClass(marts,'atlas-section atlas-empty');
 }
 // ---- Transformation: dbt ----------------------------------------------------
 const dbt=$('transformation-dbt');if(dbt){dbt.open=false;workbenchAtlasMove(dbt,$('transformation-atlas-dbt'));}
 for(const [id,view] of [['ingestion-tab-file','file'],['ingestion-tab-stages','stages']]){
  const link=$(id);if(!link)continue;
  link.onclick=event=>{event?.preventDefault?.();workbenchAtlasShowIngestion(view);workbenchResetRouteView();};
  link.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault?.();workbenchAtlasShowIngestion(view);workbenchResetRouteView();}};
 }
 workbenchAtlasShowStatus('ingestion');workbenchAtlasShowStatus('transformation');
 if(typeof workbenchAtlasLanguageChanged==='function')workbenchAtlasLanguageChanged();
}
function workbenchAtlasShowTransformation(){
 workbenchAtlasShowStatus('transformation');
}
function workbenchAtlasLanguageChanged(){
 const label=(id,key)=>{const node=$(id);if(node)ui(node,key);};
 label('ingestion-heading','Ingestion & Stages');label('transformation-heading','Transformation');
 label('ingestion-tab-file','File import');label('ingestion-tab-stages','Saved stages');
 if(dataopsPanels.get('p35-ingest')?.started){const ingest=dataopsPanels.get('p35-ingest');if(!ingest.op)dataopsRender(ingest);else dataopsIngestContext(ingest);}
 if(typeof dbStages!=='undefined'&&dbStages.installed)dbStagesRender();
 if(dataopsPanels.get('p35-wap')?.started){const wap=dataopsPanels.get('p35-wap');if(!wap.op)dataopsRender(wap);}
 if(typeof dbMarts!=='undefined'&&dbMarts.installed)dbMartsRender();
 if(typeof dbDbt!=='undefined'&&dbDbt.installed)dbDbtRender();
 workbenchAtlasShowStatus('ingestion');workbenchAtlasShowStatus('transformation');
 if(typeof workbenchAtlasBearsLanguageChanged==='function')workbenchAtlasBearsLanguageChanged();
 if(typeof workbenchAtlasComputeLanguageChanged==='function')workbenchAtlasComputeLanguageChanged();
}
// UX-R7: AI Bears and Compute & Governance are rebuilt as the Atlas mock-up's
// flat pages. The existing API state machines, guards and recovery controls are
// moved into these sections; no business handler is reimplemented here.
const workbenchAtlasBears={installed:false,message:'',loadKey:'',objectKey:'',inventoryLoading:false,inventoryRole:'',inventoryAttempted:'',inventoryFailed:'',contextLoading:false,refreshKey:'',sharedKey:'',applyingShared:false,shareContext:false,sharedPrimed:false,autoObjects:false,database:'',schema:''};
function workbenchAtlasText(node,text,values){
 if(!node)return;
 if(typeof text==='string'&&Object.hasOwn(i18n.en,text))ui(node,text,values||{});
 else node.textContent=text==null?'':String(text);
}
function workbenchAtlasShow(node,visible){if(node)node.hidden=!visible;}
function workbenchAtlasEnsureHost(id,parent,className){
 const node=$(id);if(parent&&node.parentElement!==parent)parent.append(node);
 if(className&&!node.className)node.className=className;
 return node;
}
function workbenchAtlasEnsure(tag,id,parent,className){
 let node=$(id);
 if(!node.parentElement){
  if(node.tagName!==String(tag).toUpperCase()){
   const replacement=document.createElement(tag);replacement.id=id;node=replacement;
  }
  if(className)node.className=className;
  if(parent)parent.append(node);
 }else if(className&&!node.className)node.className=className;
 return node;
}
function workbenchAtlasBearsField(parent,id,key,labelId){
 const select=$(id);
 let label=select.parentElement&&select.parentElement.tagName==='LABEL'?select.parentElement:null;
 if(!label){
  label=document.createElement('label');label.id=labelId;label.htmlFor=id;ui(label,key);parent.append(label);
  select.className='field';label.append(select);
 }else if(select.parentElement!==label)label.append(select);
 return select;
}
function workbenchAtlasFillOptions(select,items){
 if(!select.children.length)for(const [value,key] of items){const option=new Option(t(key),value);ui(option,key);select.add(option);}
 for(const option of select.children)if(option.tagName==='OPTION'){const key=option.textContent.trim();if(Object.hasOwn(i18n.en,key))ui(option,key);}
}
function workbenchAtlasBearsShell(){
 const root=$('bears'),atlas=$('bears-atlas');
 if(atlas.parentElement!==root)root.append(atlas);
 const page=workbenchAtlasEnsure('div','bears-pageheading',atlas,'pageheading');
 workbenchAtlasEnsure('h1','bears-heading',page);
 workbenchAtlasEnsure('span','bears-environment',page,'badge');
 const form=workbenchAtlasEnsure('form','bears-form',atlas);
 form.setAttribute('novalidate','novalidate');
 const context=workbenchAtlasEnsure('div','bears-context-fields',form,'atlas-fields');
 workbenchAtlasBearsField(context,'bears-database','DB','bears-database-label');
 workbenchAtlasBearsField(context,'bears-schema','Schema','bears-schema-label');
 workbenchAtlasBearsField(context,'bears-role','Role','bears-role-label');
 const target=workbenchAtlasEnsure('div','bears-target-fields',form,'atlas-fields');
 workbenchAtlasBearsField(target,'bears-table','Target table','bears-table-label');
 const source=workbenchAtlasBearsField(target,'bears-source','Source SQL input','bears-source-label');
 workbenchAtlasFillOptions(source,[['worksheet','Current worksheet'],['selection','Worksheet selection'],['table','Target table'],['input','Direct input']]);
 const inherit=workbenchAtlasEnsure('button','bears-inherit',form);inherit.type='button';ui(inherit,'Use current worksheet');
 workbenchAtlasEnsure('div','bears-sql-field',form,'atlas-sql-field');
 const recipeField=workbenchAtlasEnsure('div','bears-recipe-field',form,'atlas-fields');
 const recipe=workbenchAtlasBearsField(recipeField,'bears-recipe','Purpose','bears-recipe-label');
 workbenchAtlasFillOptions(recipe,[['columns','Show the needed columns'],['daily','Aggregate numbers by date']]);
 const toolbar=workbenchAtlasEnsure('div','bears-toolbar',form,'toolbar');
 const explain=workbenchAtlasEnsure('button','bears-explain',toolbar);explain.type='button';explain.className='primary';ui(explain,'Explain the SQL');
 const transform=workbenchAtlasEnsure('button','bears-transform',toolbar);transform.type='button';ui(transform,'Create a transform draft');
 const message=workbenchAtlasEnsure('p','bears-message',atlas);message.setAttribute('role','status');message.tabIndex=-1;
 const explanation=workbenchAtlasEnsure('section','bears-explanation',atlas,'section');explanation.setAttribute('aria-labelledby','bears-explanation-title');
 const explanationTitle=workbenchAtlasEnsure('h2','bears-explanation-title',explanation);explanationTitle.tabIndex=-1;
 workbenchAtlasEnsure('div','bears-explanation-body',explanation);
 workbenchAtlasEnsure('p','bears-explanation-note',explanation,'small');
 const explanationDetails=workbenchAtlasEnsure('details','bears-explanation-details',explanation);
 if(!explanationDetails.children.length){const summary=document.createElement('summary');ui(summary,'Explanation options and recovery');explanationDetails.append(summary);}
 explanationDetails.open=false;
 workbenchAtlasEnsure('div','bears-explanation-tools',explanationDetails);
 const proposal=workbenchAtlasEnsure('section','bears-proposal',atlas,'section');proposal.setAttribute('aria-labelledby','bears-proposal-title');
 const proposalTitle=workbenchAtlasEnsure('h2','bears-proposal-title',proposal);proposalTitle.tabIndex=-1;
 workbenchAtlasEnsure('div','bears-proposal-body',proposal);
 workbenchAtlasEnsure('p','bears-proposal-note',proposal,'small');
 const proposalDetails=workbenchAtlasEnsure('details','bears-proposal-details',proposal);
 if(!proposalDetails.children.length){const summary=document.createElement('summary');ui(summary,'Draft input and handoff conditions');proposalDetails.append(summary);}
 proposalDetails.open=false;
 workbenchAtlasEnsure('div','bears-proposal-tools',proposalDetails);
 const curator=workbenchAtlasEnsure('section','bears-curator',atlas,'section');curator.setAttribute('aria-labelledby','bears-curator-title');
 workbenchAtlasEnsure('h2','bears-curator-title',curator);
 const curatorFacts=workbenchAtlasEnsure('dl','bears-curator-facts',curator,'kv');
 if(!curatorFacts.children.length){for(const [id,key] of [['bears-curator-target','Target'],['bears-curator-description','Description']]){const dt=document.createElement('dt');ui(dt,key);const dd=document.createElement('dd');dd.id=id;curatorFacts.append(dt,dd);}}
 const curatorToolbar=workbenchAtlasEnsure('div','bears-curator-toolbar',curator,'toolbar');
 const curatorButton=workbenchAtlasEnsure('button','bears-curator-propose',curatorToolbar);curatorButton.type='button';ui(curatorButton,'Propose a description');
 workbenchAtlasEnsure('p','bears-curator-reason',curator,'small');
 const curatorDetails=workbenchAtlasEnsure('details','bears-curator-details',curator);
 if(!curatorDetails.children.length){const summary=document.createElement('summary');ui(summary,'Curator target and history');curatorDetails.append(summary);}
 curatorDetails.open=false;
 workbenchAtlasEnsure('div','bears-curator-body',curatorDetails);
 const policy=workbenchAtlasEnsure('section','bears-policy',atlas,'section');policy.setAttribute('aria-labelledby','bears-policy-title');
 workbenchAtlasEnsure('h2','bears-policy-title',policy);
 const policyFacts=workbenchAtlasEnsure('dl','bears-policy-facts',policy,'kv');
 if(!policyFacts.children.length){for(const [id,key] of [['bears-sre-fact','SRE'],['bears-policy-fact','Policy'],['bears-sample-fact','Sample time']]){const dt=document.createElement('dt');ui(dt,key);const dd=document.createElement('dd');dd.id=id;policyFacts.append(dt,dd);}}
 const computeLink=workbenchAtlasEnsure('a','bears-compute-link',policy);computeLink.href='#compute';ui(computeLink,'Open Compute & Governance →');
 const evidence=workbenchAtlasEnsure('details','bears-evidence-details',policy);
 if(!evidence.children.length){const summary=document.createElement('summary');ui(summary,'Evidence, history and requests');evidence.append(summary);}
 evidence.open=false;
 workbenchAtlasEnsure('div','bears-evidence-body',evidence);
}
function workbenchAtlasBearsInstall(){
 if(workbenchAtlasBears.installed)return;workbenchAtlasBears.installed=true;
 const root=$('bears'),atlas=$('bears-atlas');
 workbenchAtlasShow(atlas,true);
 if(atlas.parentElement!==root)root.append(atlas);
 workbenchAtlasBearsShell();
 ui($('bears-heading'),'AI Bears');ui($('bears-environment'),'LOCAL · Local lab');
 ui($('bears-explanation-title'),'SQL explanation');ui($('bears-explanation-note'),'Query Doctor · input syntax and the selected context only. No execution plan was collected.');
 const explanationDetails=$('bears-explanation-details');if(explanationDetails&&explanationDetails.children[0])ui(explanationDetails.children[0],'Explanation options and recovery');
 ui($('bears-proposal-title'),'Transform draft');ui($('bears-proposal-note'),'There is no automatic execution or publication. The draft is reviewed again in the destination.');
 const proposalDetails=$('bears-proposal-details');if(proposalDetails&&proposalDetails.children[0])ui(proposalDetails.children[0],'Draft input and handoff conditions');
 ui($('bears-curator-title'),'Catalog Curator');ui($('bears-curator-propose'),'Propose a description');
 const curatorDetails=$('bears-curator-details');if(curatorDetails&&curatorDetails.children[0])ui(curatorDetails.children[0],'Curator target and history');
 ui($('bears-policy-title'),'Observation & policy');ui($('bears-compute-link'),'Open Compute & Governance →');
 const evidenceDetails=$('bears-evidence-details');if(evidenceDetails&&evidenceDetails.children[0])ui(evidenceDetails.children[0],'Evidence, history and requests');
 // The Database mode gate disables every control in #bears; these are the
 // controls this page owns and re-enables under its own permission checks.
 for(const id of ['bears-database','bears-schema','bears-role','bears-table','bears-source','bears-recipe','bear-query-sql','bears-inherit','bears-explain','bears-transform','bears-curator-propose']){const node=$(id);if(node){node.disabled=false;node.dataset.databaseUnavailable='false';}}
 // Move the Query Doctor input into the mock-up's form. The old panel keeps
 // its history/recovery controls, now inside a closed disclosure.
 const sql=$('bear-query-sql'),sqlField=$('bears-sql-field');
 if(sql){
  const field=sql.parentElement;
  if(field&&field!==sqlField&&field.tagName!=='LABEL'){
   field.className='';
   let label=null;for(const node of field.children)if(node.tagName==='LABEL'){label=node;break;}
   if(label){label.htmlFor='bear-query-sql';ui(label,'Source SQL');}
   sqlField.append(field);
   const tools=$('bears-explanation-tools');
   if(tools)for(const node of Array.from(field.children))if(node!==sql&&node.tagName!=='LABEL')tools.append(node);
  }else{
   const label=document.createElement('label');label.htmlFor='bear-query-sql';ui(label,'Source SQL');
   const wrap=document.createElement('div');wrap.className='';wrap.append(label,sql);sqlField.append(wrap);
  }
  sql.className='field';sql.rows=5;
 }
 for(const id of ['explanation-doctor-context','explanation-source']){const node=$(id);if(node&&!node.parentElement){const tools=$('bears-explanation-tools');if(tools)tools.append(node);}}
 $('bears-explain').onclick=()=>workbenchAtlasBearsExplain();
 const output=$('explanation-doctor-output'),explanationBody=$('bears-explanation-body');
 if(output&&explanationBody&&output.parentElement!==explanationBody)explanationBody.append(output);
 const queryPanel=$('bear-panel-query'),explanationTools=$('bears-explanation-tools');
 if(queryPanel&&explanationTools&&queryPanel.parentElement!==explanationTools){
  queryPanel.className='';queryPanel.removeAttribute?.('role');
  for(const node of queryPanel.children)if(['H2','P'].includes(node.tagName))node.hidden=true;
  explanationTools.append(queryPanel);
 }
 const handoff=$('bear-handoff'),proposalBody=$('bears-proposal-body');
 if(handoff&&proposalBody&&handoff.parentElement!==proposalBody)proposalBody.append(handoff);
 const dataPanel=$('bear-panel-data'),proposalTools=$('bears-proposal-tools');
 if(dataPanel&&proposalTools){
  dataPanel.className='';for(const node of dataPanel.children)if(['H2','P'].includes(node.tagName)&&!node.id)node.hidden=true;
  let guides=workbenchAtlasPeek('bears-workflow-guides');
  if(!guides){guides=document.createElement('details');guides.id='bears-workflow-guides';const summary=document.createElement('summary');ui(summary,'Draft workflow guides');guides.append(summary);$('bears-evidence-body').append(guides);}
  guides.open=false;workbenchAtlasMove(dataPanel,guides);
 }
 const proposalDisclosure=$('bears-proposal-details');if(proposalDisclosure)proposalDisclosure.hidden=true;
 const curator=$('curator-panel'),curatorBody=$('bears-curator-body');
 if(curator&&curatorBody&&curator.parentElement!==curatorBody)curatorBody.append(curator);
 const catalogHost=$('bear-panel-catalog');if(catalogHost)catalogHost.hidden=true;
 for(const kind of ['sre','query','data','policy']){const panel=$('bear-panel-'+kind);if(panel)panel.hidden=false;}
 for(const kind of ['sre','policy']){
  const panel=$('bear-panel-'+kind),evidence=$('bears-evidence-body');
  if(!panel||!evidence)continue;
  let group=workbenchAtlasPeek('bears-evidence-'+kind);
  if(!group){group=document.createElement('details');group.id='bears-evidence-'+kind;group.className='bear-details';const summary=document.createElement('summary');ui(summary,kind==='sre'?'SRE':'Policy');group.append(summary);evidence.append(group);}
  panel.className='';for(const node of panel.children)if(node.tagName==='H2')node.hidden=true;
  if(panel.parentElement!==group)group.append(panel);
 }
 let ids=workbenchAtlasPeek('bears-request-ids');
 if(!ids){ids=document.createElement('dl');ids.id='bears-request-ids';ids.className='kv';$('bears-evidence-body').prepend(ids);}
 let lead=workbenchAtlasPeek('bears-evidence-lead');
 if(!lead){lead=document.createElement('p');lead.id='bears-evidence-lead';lead.className='small';ui(lead,'Catalog metadata and the source SQL are used. SQL is not executed.');$('bears-evidence-body').prepend(lead);}
 // The page has one visible context: the top DB, schema, role and target table.
 // The existing Curator/Observation controllers keep their hidden state selects,
 // but those duplicates are not shown.
 // Form wiring. The buttons are plain buttons so a test adapter and a browser
 // both run one handler; the form still supplies Enter-free explicit actions.
 const form=$('bears-form');
 form.onsubmit=event=>event.preventDefault?.();
 $('bears-explain').onclick=()=>workbenchAtlasBearsExplain();
 $('bears-transform').onclick=()=>workbenchAtlasBearsTransform();
 $('bears-inherit').onclick=()=>workbenchAtlasBearsInherit();
 $('bears-source').onchange=()=>workbenchAtlasBearsSourceChanged();
 $('bears-table').onchange=()=>workbenchAtlasBearsTableChanged();
 $('bears-table').onfocus=()=>workbenchAtlasBearsLoadObjects(true);
 $('bears-table').onclick=()=>workbenchAtlasBearsLoadObjects(true);
 $('bears-database').onchange=()=>workbenchAtlasBearsContextChanged('database');
 $('bears-schema').onchange=()=>workbenchAtlasBearsContextChanged('schema');
 $('bears-role').onchange=()=>workbenchAtlasBearsContextChanged('role');
 $('bears-curator-propose').onclick=()=>{if(typeof dbCuratorSubmit==='function')return dbCuratorSubmit();};
 workbenchAtlasBearsSync();
}
function workbenchAtlasBearsContextChanged(kind){
 const w=typeof dbCurrent==='function'?dbCurrent():null;if(!w)return;
 const value=$(kind==='database'?'bears-database':kind==='schema'?'bears-schema':'bears-role').value;
 workbenchAtlasBears.message='';workbenchAtlasBears.loadKey='';workbenchAtlasBears.refreshKey='';workbenchAtlasBears.sharedKey='';workbenchAtlasBears.autoObjects=false;
 if(kind==='database'){
  const role=$('bears-role').value||w.selected_role||currentRole,d=dbRoleItems(role).find(d=>d.id===value);
  if(!d)return;
  w.selected_role=role;w.database_id=d.id;w.binding_revision=d.binding_revision;w.generation_id=d.generation_id;w.schema_id='';w._grantChanged=false;
  workbenchAtlasBears.database=d.id;workbenchAtlasBears.schema='';
  dbSave();dbRenderRun();void workbenchAtlasBearsRefreshContext();
 }else if(kind==='schema'){
  const role=$('bears-role').value||w.selected_role||currentRole,s=dbSchemasFor({selected_role:role,database_id:w.database_id}).find(s=>s.id===value);
  if(!s)return;
  w.selected_role=role;w.schema_id=s.id;workbenchAtlasBears.schema=s.id;workbenchAtlasBears.autoObjects=true;
  dbSave();dbRenderRun();if(typeof dbReselect==='function')Promise.resolve(dbReselect()).then(()=>workbenchAtlasBearsSync());
 }else{
  if(!dbRoleWithinHeader(value)){dbControls();return;}
  w.selected_role=value;const d=dbRoleItems(value).find(d=>d.id===w.database_id);
  if(!d){w.database_id='';w.schema_id='';w.binding_revision=null;w.generation_id=null;}
  workbenchAtlasBears.database=w.database_id;workbenchAtlasBears.schema='';workbenchAtlasBears.inventoryAttempted='';
  dbSave();dbPersist();dbRenderRun();void workbenchAtlasBearsEnsureInventory();void workbenchAtlasBearsRefreshContext();
 }
 workbenchAtlasBearsSync();
}
function workbenchAtlasBearsSourceChanged(){
 const source=$('bears-source').value;
 if(source==='worksheet'||source==='selection'){
  const selected=source==='selection',start=$('sql')?.selectionStart||0,end=$('sql')?.selectionEnd||0;
  let value=String($('sql')?.value||'');
  if(selected)value=end>start?value.slice(start,end):'';
  if(value.trim()){
   $('bear-query-sql').value=value;$('bear-query-sql').dispatchEvent(new Event('input'));
   workbenchAtlasBears.message=selected?'The worksheet selection was copied.':'The current worksheet SQL was copied.';
  }else workbenchAtlasBears.message=selected?'Select SQL in the worksheet before copying it.':'The current worksheet has no SQL.';
 }else if(source==='table'){
  workbenchAtlasBears.message='';
  workbenchAtlasBearsSetSqlFromTable();
 }else{
  workbenchAtlasBears.message='';$('bear-query-sql')?.focus();
 }
 workbenchAtlasBearsSync();
}
function workbenchAtlasBearsInherit(){
 $('bears-source').value='worksheet';
 workbenchAtlasBearsSourceChanged();
}
function workbenchAtlasBearsSetSqlFromTable(){
 const source=dbProposal.sources?.[0];
 if(!source){workbenchAtlasBears.message='Select a source table first.';return false;}
 $('bear-query-sql').value='SELECT *\nFROM '+source.schema+'.'+source.name+'\nLIMIT 10;';
 $('bear-query-sql').dispatchEvent(new Event('input'));
 return true;
}
function workbenchAtlasBearsTableChanged(){
 const value=$('bears-table').value,source=(dbProposal.objects||[]).find(object=>String(object.ref?.object_id)===value);
 workbenchAtlasBears.message='';
 if(source){
  dbProposal.sources=[source];dbProposalChanged();
  const match=(dbCurator.objects||[]).find(object=>object.name===source.name&&object.schema===source.schema);
  if(match&&dbCurator.installed){dbCurator.source=match;dbCuratorChanged();}
  if($('bears-source').value==='table')workbenchAtlasBearsSetSqlFromTable();
 }
 workbenchAtlasBearsSync();
}
async function workbenchAtlasBearsExplain(){
 const sql=String($('bear-query-sql')?.value||'').trim();
 if(!sql){workbenchAtlasBears.message='Enter SQL to explain.';workbenchAtlasBearsSync();$('bear-query-sql')?.focus();return;}
 workbenchAtlasBears.message='';
 if(typeof dbExplain==='function')await dbExplain('doctor');
 if(dbExplanation.record)workbenchAtlasBears.message='The explanation is ready. Review it before use.';
 workbenchAtlasBearsSync();
}
async function workbenchAtlasBearsTransform(){
 const source=dbProposal.sources?.[0];
 if(!source){workbenchAtlasBears.message='Choose a target table for the transform draft.';workbenchAtlasBearsSync();$('bears-table')?.focus();return;}
 const recipe=$('bears-recipe').value==='daily'?'Aggregate a numeric column by date from the selected table.':'Show the needed columns from the selected table.';
 workbenchAtlasBears.message='';
 const request=$('copilot-request');request.value=t(recipe);request.dispatchEvent(new Event('input'));
 if(typeof dbBearHandoffCreate==='function')await dbBearHandoffCreate();
 if(dbProposal.record)workbenchAtlasBears.message='Review the returned draft before approval.';
 workbenchAtlasBearsSync();
}
function workbenchAtlasBearsLoadObjects(force=false){
 const p=workbenchAtlasBears;
 if(!p.installed||activeTab!=='bears')return false;
 const w=dbCurrent();if(!w||!w.schema_id||dbReason())return false;
 const key=JSON.stringify([dbPrincipal,w.id,w.database_id,w.schema_id,w.selected_role]);
 if(!force&&p.loadKey===key)return false;
 if(dbProposal.objects?.length||dbProposal.fetchToken)return false;
 p.loadKey=key;p.autoObjects=false;dbProposal.schema=w.schema_id;
 if(typeof dbProposalLoad==='function'){void dbProposalLoad();return true;}
 return false;
}
async function workbenchAtlasBearsEnsureInventory(){
 const p=workbenchAtlasBears;if(!p.installed||activeTab!=='bears')return;
 const w=dbCurrent(),role=$('bears-role')?.value||w?.selected_role||currentRole;
 if(!dbRoleWithinHeader(role)||dbInventories.has(role))return;
 if(p.inventoryLoading||p.inventoryAttempted===role)return;
 p.inventoryAttempted=role;p.inventoryLoading=true;p.inventoryRole=role;
 try{const items=await dbReadInventory(role);if(!p.installed||p.inventoryRole!==role)return;dbApplyInventory(items,role);p.inventoryFailed='';}
 catch(error){if(p.inventoryRole===role){p.inventoryFailed=role;p.message=error?.message||'Database list unavailable. Refresh to try again.';}}
 finally{if(p.inventoryRole===role){p.inventoryLoading=false;p.inventoryRole='';}workbenchAtlasBearsSync();}
}
async function workbenchAtlasBearsRefreshContext(){
 const p=workbenchAtlasBears,w=dbCurrent();
 if(!p.installed||activeTab!=='bears'||!w?.database_id||!w?.selected_role||dbSchemaScope===dbSchemaKey(w.selected_role,w.database_id))return;
 if(p.contextLoading)return;
 p.contextLoading=true;
 try{await dbRefreshContext();}
 finally{p.contextLoading=false;workbenchAtlasBearsSync();}
}
function workbenchAtlasBearsHideField(id){
 const node=$(id);if(!node)return;
 const wrapper=node.parentElement;
 if(wrapper&&wrapper.tagName==='SPAN'&&wrapper.className.split(' ').includes('field'))wrapper.hidden=true;
 else node.hidden=true;
}
function workbenchAtlasBearsApplySharedContext(w,ready){
 const p=workbenchAtlasBears;if(p.applyingShared)return;
 const role=w?.selected_role||'',d=w?dbWorksheetDatabase(w):null,s=w?dbWorksheetSchema(w):null;
 const sourceRef=dbProposal.sources?.[0]?.ref?.object_id||'';
 const key=JSON.stringify([ready,role,w?.database_id,w?.schema_id,sourceRef]);
 if(p.sharedKey===key)return;
 p.sharedKey=key;
 const first=!p.sharedPrimed;p.sharedPrimed=true;
 const context=(ready&&d&&s&&role)?{database_id:d.id,schema_id:s.id,warehouse_id:'LOCAL',binding_revision:d.binding_revision,generation_id:d.generation_id,grant_revision:d.grant_revision??null,sql_catalog:d.sql_catalog,sql_schema:s.sql_name,selected_role:role}:null;
 const validContext=context&&dbCuratorContext(context)?context:null;
 p.applyingShared=true;
 try{
  const c=dbCurator,proposalSource=dbProposal.sources?.[0];
  if(validContext&&(!first||!c.context)){
   const sameCurator=!!c.context&&c.shared===true&&c.role===role&&c.database===w?.database_id&&c.schema===w?.schema_id&&c.context.database_id===validContext.database_id&&c.context.schema_id===validContext.schema_id&&c.inventoryAuth===dbCuratorAuth();
   const sameSource=(c.source?.ref?.object_id||'')===(proposalSource?.ref?.object_id||'');
   if(!sameCurator){c.context=null;c.inventoryAuth='';c.record=null;c.origin=null;c.history=[];c.notice='';++c.epoch;c.busy=null;}
   else if(!sameSource){c.record=null;c.origin=null;c.notice='';}
   c.role=role;c.database=w?.database_id||'';c.schema=w?.schema_id||'';
   c.databases=d?[d]:[];c.schemas=s?[s]:[];
   c.objects=dbProposal.objects||[];
   c.source=(c.objects||[]).find(object=>object.name===proposalSource?.name&&object.schema===proposalSource?.schema)||(proposalSource&&proposalSource.ref?.database_id===w?.database_id&&proposalSource.ref?.schema_id===w?.schema_id?proposalSource:null);
   if(!sameCurator){c.context=dataopsFreeze(validContext);c.inventoryAuth=dbCuratorAuth();}
   c.shared=true;c.auth=dbCuratorAuth();
  }else if(c.shared===true){
   c.shared=false;c.context=null;c.inventoryAuth='';c.record=null;c.origin=null;c.history=[];c.notice='';++c.epoch;c.busy=null;c.objects=[];c.cursor=null;c.source=null;c.databases=[];c.schemas=[];
  }
  c.navigation=activeTab;
  dbCuratorSync();
  for(const o of Object.values(dbObservations)){
   if(!o.installed)continue;
   if(validContext&&(!first||!o.context)){
    const sameObservation=!!o.context&&o.shared===true&&o.role===role&&o.database===w?.database_id&&o.schema===w?.schema_id&&o.context.database_id===validContext.database_id&&o.context.schema_id===validContext.schema_id&&o.inventoryAuth===dbObservationAuth(o);
    if(!sameObservation){o.context=null;o.ceiling=null;o.record=null;o.notice='';++o.epoch;o.busy=null;}
    o.role=role;o.database=w?.database_id||'';o.schema=w?.schema_id||'';
    o.databases=d?[d]:[];o.schemas=s?[s]:[];
    o.auth=dbObservationAuth(o);o.inventoryAuth=o.auth;
    if(!sameObservation){o.context=dataopsFreeze(validContext);o.ceiling=dbObservationCeiling(o);if(!o.ceiling){o.context=null;o.record=null;}}
    else if(!dbObservationSameCeiling(o.ceiling,dbObservationCeiling(o))){o.context=null;o.ceiling=null;o.record=null;}
    o.shared=true;
   }else if(o.shared===true){
    o.shared=false;o.context=null;o.ceiling=null;o.record=null;o.notice='';++o.epoch;o.busy=null;o.databases=[];o.schemas=[];
   }
   o.navigation=activeTab;
   dbObservationSync(o);
  }
 }finally{p.applyingShared=false;}
}
function workbenchAtlasBearsSync(){
 if(!workbenchAtlasBears.installed||workbenchAtlasBears.applyingShared)return;
 const p=workbenchAtlasBears,w=typeof dbCurrent==='function'?dbCurrent():null;
 ui($('bears-heading'),'AI Bears');ui($('bears-environment'),'LOCAL · Local lab');
 const role=dbRoleWithinHeader(w?.selected_role)?w.selected_role:currentRole;
 const inventoryLoaded=dbInventories.has(role);
 const databases=inventoryLoaded?dbRoleItems(role).filter(d=>d.state==='READY'&&d.allowed_roles?.includes(role)):[];
 const selectedDb=w?.database_id||p.database||'';
 const databaseValid=databases.some(d=>d.id===selectedDb);
 const databaseValue=inventoryLoaded?selectedDb:'';
 const retainedDatabase=inventoryLoaded&&selectedDb&&!databaseValid?dbDatabaseLabel(dbRoleItems(role).find(d=>d.id===selectedDb)||{}):'';
 dbSelectOptions('bears-database',databases.map(d=>({value:d.id,label:dbDatabaseLabel(d),disabled:!d.capabilities?.query})),databaseValue,inventoryLoaded?'Choose database':'Loading databases…',retainedDatabase);
 const schemaScope=dbSchemaKey(role,selectedDb);
 const schemaReady=inventoryLoaded&&!!selectedDb&&dbSchemaScope===schemaScope;
 if(activeTab==='bears'&&inventoryLoaded&&selectedDb){const refreshKey=JSON.stringify([role,selectedDb]);if(schemaReady)p.refreshKey=refreshKey;else if(p.refreshKey!==refreshKey){p.refreshKey=refreshKey;void workbenchAtlasBearsRefreshContext();}}
 const schemas=schemaReady?dbSchemasFor({selected_role:role,database_id:selectedDb}).filter(s=>s.capabilities?.query):[];
 const selectedSchemaRaw=schemaReady?(w?.schema_id||p.schema||''):'';
 const schemaValid=schemas.some(s=>s.id===selectedSchemaRaw);
 const schemaValue=schemaValid?selectedSchemaRaw:'';
 const retainedSchema=schemaReady&&selectedSchemaRaw&&!schemaValid?dbSchemaLabel(dbWorksheetSchema(w)||{}):'';
 dbSelectOptions('bears-schema',schemas.map(s=>({value:s.id,label:dbSchemaLabel(s)})),schemaValue,!selectedDb?'Choose database':schemaReady?'Choose schema':'Loading schemas…',retainedSchema);
 dbSelectOptions('bears-role',['bi_viewer','data_engineer','admin'].map(value=>({value,label:dbRoleDisplayLabel(value),disabled:!dbRoleWithinHeader(value)})),role,'Choose role');
 const working=w?{...w,selected_role:role,database_id:selectedDb,schema_id:schemaValue}:null;
 const contextReason=working?dbReason(working):t('Choose a database and schema to begin.');
 const worksheetSelected=!!w&&w.selected_role===role&&w.database_id===selectedDb&&w.schema_id===schemaValue&&schemaValid;
 const actionReason=worksheetSelected?dbReason(w):(contextReason||t('Choose a database and schema to begin.'));
 const source=dbProposal.sources?.[0],objects=dbProposal.objects||[];
 const options=objects.map(object=>({value:String(object.ref.object_id),label:object.schema+'.'+object.name+' · '+object.kind}));
 if(source&&!options.some(option=>option.value===String(source.ref.object_id)))options.push({value:String(source.ref.object_id),label:source.schema+'.'+source.name+' · '+source.kind});
 const tableContextReady=!!selectedDb&&!!schemaValue;
 const tablePlaceholder=dbProposal.fetchToken?'Preparing source objects…':!tableContextReady?'Select a schema first.':objects.length?'Select a source table':dbProposal.pickerNotice||'Select a source table';
 dbSelectOptions('bears-table',options,source?String(source.ref.object_id):'',tablePlaceholder,source?source.schema+'.'+source.name:t('Select a source table'));
 $('bears-database').disabled=!inventoryLoaded;
 $('bears-schema').disabled=!selectedDb||!schemaReady;
 $('bears-role').disabled=false;
 $('bear-query-sql').disabled=false;
 $('bears-inherit').disabled=false;$('bears-explain').disabled=!!actionReason||dbExplanation.explainDisabled===true;$('bears-transform').disabled=!!actionReason;
 $('bears-source').disabled=!!actionReason;$('bears-recipe').disabled=!!actionReason;$('bears-table').disabled=!!actionReason;
 workbenchAtlasShow($('bears-explanation'),!!dbExplanation.record||!!dbExplanation.notice||dbExplanation.intents.length>0);
 const record=dbProposal.record;
 workbenchAtlasShow($('bears-proposal'),!!record);
 // Proposal facts and the handoff controls share the same records as the
 // existing state machine; no new API is introduced.
 $('bears-proposal-body').hidden=false;
 if(p.shareContext&&activeTab==='bears')workbenchAtlasBearsApplySharedContext(w,worksheetSelected&&!dbReason(w));
 // Catalog Curator facts use display names only.
 const curatorTarget=dbCurator.source?dbCurator.source.schema+'.'+dbCurator.source.name+' · '+dbCurator.source.kind:t('Select a table.');
 $('bears-curator-target').textContent=curatorTarget;
 const description=dbCurator.record?.proposal?.description||dbCurator.record?.proposal?.summary||t('No description is registered.');
 $('bears-curator-description').textContent=description;
 const curatorButton=$('bears-curator-propose');
 curatorButton.disabled=dbCurator.createDisabled??(!dbCuratorAllowed()||!dbCurator.source);
 curatorButton.className='';
 let curatorGuidance='';
 if(!worksheetSelected||dbReason(w))curatorGuidance=t('Choose a database and schema to begin.');
 else if(!dbCurator.source)curatorGuidance=t('Select a target table.');
 else if(curatorButton.disabled)curatorGuidance=dbCuratorGuidance();
 ui($('bears-curator-reason'),curatorGuidance);
 workbenchAtlasShow($('bears-curator-reason'),!!curatorGuidance);
 const summary=dbRuntimeSummary(),stateLabel=t(summary[1]),observed=dbRuntime.status?.observed_at;
 $('bears-sre-fact').textContent='LOCAL · '+stateLabel+(observed?' · '+t('Observed at')+' '+fmtJST(observed):'');
 const policyTarget=worksheetSelected&&w?.selected_role?dbRoleDisplayLabel(w.selected_role):t('No target selected');
 $('bears-policy-fact').textContent=policyTarget+' · '+(worksheetSelected&&!dbReason(w)?t('Target metadata is readable'):t('Target not selected or permission not confirmed'))+' · '+t('Fixed rules');
 const sample=dbExplanation.record?.created_at||dbProposal.record?.created_at||dbCurator.record?.created_at||observed||'';
 $('bears-sample-fact').textContent=sample?fmtJST(sample):t('Not observed');
 // Reconciliation IDs stay inside the closed evidence disclosure.
 for(const node of Array.from($('bears-request-ids').children))node.remove();
 for(const [label,value] of [['Proposal ID',dbProposal.record?.id],['Explanation ID',dbExplanation.record?.id],['Curator request ID',dbCurator.record?.id]]){
  const dt=document.createElement('dt');ui(dt,label);const dd=document.createElement('dd');dd.textContent=value||'—';$('bears-request-ids').append(dt,dd);
 }
 const inventoryMessage=inventoryLoaded?'':p.inventoryFailed===role?t('Database list unavailable. Refresh to try again.'):t('Loading databases…');
 const schemaMessage=!selectedDb?t('Choose a database and schema to begin.'):!schemaReady?t('Loading schemas…'):!schemaValue?t('Choose a schema.'):'';
 const contextMessage=inventoryMessage||schemaMessage||(worksheetSelected&&!dbReason(w)?'':(actionReason||t('Choose a database and schema to begin.')));
 const actionNotice=p.message||(typeof dbBearHandoff!=='undefined'&&dbBearHandoff.message)||dbProposal.notice||dbExplanation.notice||dbCurator.notice;
 const notice=actionNotice||contextMessage;
 if(notice&&typeof notice==='object')ui($('bears-message'),notice.key,notice.values||{});
 else workbenchAtlasText($('bears-message'),notice);
 if(typeof dbBearHandoffRender==='function')dbBearHandoffRender();
 if(!inventoryLoaded)void workbenchAtlasBearsEnsureInventory();
 if(activeTab==='bears'&&worksheetSelected&&!dbReason(w)&&!dbProposal.objects?.length&&!dbProposal.fetchToken)void workbenchAtlasBearsLoadObjects(false);
}
function workbenchAtlasBearsLanguageChanged(){
 if(!workbenchAtlasBears.installed)return;
 workbenchAtlasBearsSync();
}
function dataopsPanel(id,title,parent){
 const root=dataopsNode('section',id,parent);root.className='panel';const body=dataopsNode('div','',root);body.className='panel-body';dataopsNode('h2','',body,title);
 const form=dataopsNode('form',id+'-form',body),fields=dataopsNode('div','',form);fields.className='job-fields';
 const p={id,root,body,form,fields,epoch:0,pending:false,invalid:true,databases:[],schemas:[],tables:[],objects:[],models:[],role:dbCurrent()?.selected_role||currentRole,database:dbCurrent()?.database_id||'',schema:dbCurrent()?.schema_id||'',context:null,op:null,model:null,modelDirty:true,principal:dbPrincipal,draftPrincipal:dbPrincipal};dataopsPanels.set(id,p);
 for(const [key,label] of [['role','Execution role'],['database','Database'],['schema','Schema']]){
  const n=dataopsField(fields,id+'-'+key,label,'select');n.onchange=()=>{p[key]=n.value;dataopsChanged(p);return dataopsRefresh(p);};
 }
 dataopsButton(form,id+'-refresh','Refresh context',()=>dataopsRefresh(p));
 p.availability=dataopsNode('p',id+'-availability',body);p.availability.className='help';p.availability.setAttribute('role','status');
 p.status=dataopsNode('p',id+'-status',body);p.status.className='status-line';p.status.setAttribute('role','status');p.status.setAttribute('aria-live','polite');
 dataopsNode('p',id+'-accepted-context',body).className='help';
 p.receipt=dataopsNode('div',id+'-operation',body);p.receipt.setAttribute('aria-live','polite');
 dataopsButton(body,id+'-observe','Observe operation',()=>dataopsObserve(p,false));
 dataopsButton(body,id+'-cancel','Cancel operation',()=>dataopsObserve(p,true));
 dataopsNode('p',id+'-observe-help',body,'Resending uses the original request body and key and may accept execution if the first request never arrived. Reloaded handles cannot resend.').className='help';
 dataopsRecoveryInstall(p);
 return p;
}
function dataopsRenderOperation(p){
 const area=p.receipt,op=p.op,wap=p.id==='p35-wap';
 const identity=JSON.stringify([dbPrincipal,p.context,op?.key,p.metadata?.model_id,p.metadata?.version,p.metadata?.id,p.metadata?.build_id,p.metadata?.audit_id,p.metadata?.publication_id]);
 const walk=n=>Array.from(n.children||[]).flatMap(c=>[c,...walk(c)]);
 const old=wap&&area.dataset.identity===identity?walk(area).filter(n=>n.tagName==='DETAILS'):[],focused=old.find(n=>n.children[0]===document.activeElement);
 const label=n=>uiBindings.get(n.children[0])?.key||n.children[0]?.textContent;
 area.dataset.identity=identity;area.replaceChildren();
 try{
 if(p.principal!==dbPrincipal)return;
 if(p.metadata&&!p.invalid)dataopsRenderWapMetadata(area,p.metadata);
 if(!op||op.principal!==undefined&&op.principal!==dbPrincipal)return;
 if(op.protected&&(!dataopsRecoveryAuthorized(p,op)||op.capture!==undefined&&op.capture!==dataopsRecoveryStamp(p))){op.protected=false;op.operation=null;}
 const row=(key,value)=>{const line=dataopsNode('p','',area),label=dataopsNode('strong','',line,key);line.append(': '+String(value??''));return label;};
 row('Idempotency key',op.key);row('Operation ID',op.id||'UNKNOWN');
 row('Accepted context',[op.context.sql_catalog,op.context.sql_schema,op.context.selected_role].join(' / '));
 if(dataopsState(op))row('Status',t(dataopsState(op)));
 if(op.ambiguous||dataopsState(op)==='UNCONFIRMED')dataopsNode('p','',area,'Outcome unknown. Observe with the saved key; do not submit again.');
 if(dataopsState(op)==='UNKNOWN')dataopsNode('p','',area,'Unknown execution requires manual reconciliation. Observe the saved operation.');
 if(!op.protected){dataopsNode('p','',area,'Results hidden until access is revalidated.');return;}
 if(op.operation){row('Committed',op.operation.committed);row('Cleanup error',op.operation.cleanup_error||'');
  if(wap){for(const key of ['error','error_code','errors'])if(op.operation[key])row('Error',typeof op.operation[key]==='string'?op.operation[key]:JSON.stringify(op.operation[key]));}
  const result=op.operation.result;if(result){if(wap)for(const key of ['error','error_code','errors'])if(result[key])row('Error',typeof result[key]==='string'?result[key]:JSON.stringify(result[key]));const metadata=result.build||result.audit||result.publication;if(metadata)dataopsRenderWapMetadata(area,metadata);else{const parent=wap?workflowDetails(area,'Raw operation result'):area;const pre=dataopsNode('pre','',parent);pre.textContent=JSON.stringify(result,null,2);}}}
 }finally{
  if(wap){const details=walk(area).filter(n=>n.tagName==='DETAILS');details.forEach((n,i)=>{if(old[i]&&label(n)===label(old[i])){n.open=old[i].open;if(old[i]===focused)n.children[0].focus();}});}
 }
}

function dataopsRenderWapMetadata(area,record){
 const row=(label,value)=>{if(value===undefined||value===null)return;const line=dataopsNode('p','',area);dataopsNode('strong','',line,label);line.append(': '+String(value));};
 row('Model ID',record.model_id);row('Version',record.version);row('Stage snapshot',record.stage?.snapshot??record.stage?.snapshot_id);row('Target snapshot',record.target?.snapshot??record.target?.snapshot_id);row('Rows',record.count);
 if(record.null_counts)row('Null counts',Object.entries(record.null_counts).map(([key,count])=>key+': '+count).join(', '));
 if(record.passed!==undefined)row('Quality',t(record.passed?'Passed':'Failed'));
 const details=dataopsNode('details','',area);dataopsNode('summary','',details,'Request details');const pre=dataopsNode('pre','',details);pre.textContent=JSON.stringify(record,null,2);
}
function dataopsAvailabilityReason(p,allowed){
 if(!dbRoleWithinHeader(p.role))return 'Saved execution role exceeds the current role ceiling.';
 if(p.role==='bi_viewer')return 'Manual Ingest and WAP require data_engineer or admin. Select an execution role for this panel.';
 if(!p.database||!p.schema)return 'Choose an available database and schema.';
 return allowed?'':'Panel context is unavailable. Refresh this panel context or reselect its database, schema and execution role.';
}
function dataopsRenderAvailability(p,allowed=dataopsAllowed(p,p.id==='p35-ingest'?'manual_ingest':'wap_models')){
 const reason=dataopsAvailabilityReason(p,allowed);
 if(p.availability.dataset.reason!==reason){
  ui(p.availability,reason==='Saved execution role exceeds the current role ceiling.'?'{reason} {action}':reason,()=>({reason:t(reason),action:t('Select an execution role within the current ceiling for this panel.')}));
  p.availability.dataset.reason=reason;
 }
 p.availability.hidden=!reason;
}
function dataopsRender(p,inventory=false){
 const id=p.id,waiting=!!(p.pending||p.trackingPending);
 if(inventory){
  dataopsOptions($(id+'-role'),['bi_viewer','data_engineer','admin'].filter(dbRoleWithinHeader).map(r=>[r,dbRoleDisplayNames[r]]),p.role,'Execution role');
  const databases=dbReadyDatabases(p.role);
  dataopsOptions($(id+'-database'),databases.map(d=>[d.id,dbDatabaseLabel(d)]),p.database,'Choose database');
  dataopsOptions($(id+'-schema'),p.schemas.map(s=>[s.id,dbSchemaLabel(s)]),p.schema,'Choose schema');
 }
 if(p.context&&!p.invalid){const context=$(id+'-accepted-context');context.textContent=[p.context.sql_catalog,p.context.sql_schema,p.context.selected_role].join(' / ');}
 const cap=id==='p35-ingest'?'manual_ingest':'wap_models',allowed=dataopsAllowed(p,cap);
 dataopsRenderAvailability(p,allowed);
 // Context selectors remain usable for explicit recovery. Never blindly enable after await.
 for(const n of p.form.querySelectorAll('input,select,textarea,button'))n.disabled=waiting||!allowed;
 for(const k of ['role','database','schema','refresh'])$(id+'-'+k).disabled=waiting;
 const owner=!!p.op&&p.op.principal===dbPrincipal;
 $(id+'-observe').hidden=!!p.op&&!owner;$(id+'-cancel').hidden=!!p.op&&!owner;
 $(id+'-observe').disabled=!!p.trackingPending||!owner||!!p.op?.restored||!dataopsCanObserve(p.op);$(id+'-cancel').disabled=!!p.trackingPending||!owner||!dbRoleWithinHeader(p.op.context.selected_role)||!dataopsCanCancel(p.op);
 $(id+'-observe-help').hidden=!owner||!!p.op.id||!!p.op.restored;
 ui($(id+'-observe'),p.op&&!p.op.id?'Resend original request (may accept execution)':'Observe operation');
 dataopsRecoveryRender(p);
 if(id==='p35-ingest'){
  $(id+'-choose-file').disabled=$(id+'-file').disabled=waiting||!dataopsIngestFileAllowed(p);
  const mode=$(id+'-mode').value,create=mode==='create';
  $(id+'-name').parentElement.hidden=false;
  $(id+'-target').parentElement.hidden=!mode||create;
  $(id+'-keys').parentElement.hidden=mode!=='upsert';
  $(id+'-name').disabled=!allowed||waiting;$(id+'-target').disabled=!allowed||waiting||!mode||create;
  $(id+'-keys').disabled=!allowed||waiting||mode!=='upsert';
  $(id+'-preview').disabled=waiting||!allowed||dataopsOwnedUnresolved(p);
  $(id+'-confirm').disabled=waiting||!allowed||!p.preview||!!p.preview.submitted||(dataopsOwnedUnresolved(p)||dataopsRecoveryHeld(p));
  if(inventory)dataopsOptions($(id+'-target'),p.objects.map((o,i)=>[String(i),o.label]),'');
  ui($(id+'-observe'),p.op&&!p.op.id?'Resend original request':'Look up ingestion operation');
  $(id+'-observe').hidden=!owner||!!p.op.restored;
  $(id+'-cancel').hidden=!owner;
  dataopsIngestContext(p);
  dataopsIngestImpact(p);
 }else{
  if(inventory){
   dataopsOptions($(id+'-models'),p.models.map((m,i)=>[String(i),m.model_id+' · v'+m.version]),p.model?String(p.models.indexOf(p.model)):'','New model');
   dataopsOptions($(id+'-target-schema'),p.schemas.map(s=>[s.id,s.sql_name]),$(id+'-target-schema').value||p.schema,'Target schema');
   dataopsOptions($(id+'-target'),p.tables.map((o,i)=>[String(i),o.label]),p.model?$(id+'-target').value:'','Create table name');
   for(const source of p.sources||[])dataopsOptions(source.table,p.tables.map((o,i)=>[String(i),o.label]),source.table.value);
  }
  dataopsRenderWapGuide(p);
 }
 dataopsRenderOperation(p);
 if(id==='p35-wap')dataopsAiRender(p);
 if(id==='p35-ingest')workbenchAtlasSyncIngestion();
 if(id==='p35-wap')workbenchAtlasSyncTransform(p);
 if(typeof workbenchRenderWorkspaceNav==='function')workbenchRenderWorkspaceNav();
}
async function dataopsSubmit(p,path,body,record){
 if(p.pending||p.trackingPending||p.invalid||record.submitted||dataopsOwnedUnresolved(p)||dataopsRecoveryHeld(p)){const store=dataopsRecoveryOwner();if(store.error||store.ops.length>=25)dataopsRecoveryNotice(p);else dataopsRecoveryRender(p);return;}
 record.key??=crypto.randomUUID();
 const op={path,key:record.key,context:record.payload?.context||dataopsFreeze(dataopsClone(p.context)),payload:record.payload||dataopsFreeze(dataopsClone({...body,context:p.context})),principal:dbPrincipal,protected:false,id:null,operation:null};
 const action=Object.keys(dataopsRecoveryPaths).find(action=>dataopsRecoveryPaths[action]===path),store=dataopsRecoveryOwner();
 if(!action||store.error||store.ops.length>=25){dataopsRecoveryNotice(p);return;}
 Object.assign(op,{panel:p.id,action,time:Date.now(),state:'UNCONFIRMED',ambiguous:true,sequence:1});
 const token={},old=p.op,stamp=dataopsRecoveryStamp(p);p.submitToken=token;p.pending=true;
 const current=()=>p.submitToken===token&&stamp===dataopsRecoveryStamp(p)&&p.principal===dbPrincipal&&!p.invalid;
 const visible=()=>current()&&dataopsRecoveryAuthorized(p,op);
 let attempted=false;p.status.className='status-line';p.status.setAttribute('role','status');ui(p.status,'Pending');dataopsRender(p);
 try{
  // Authorization finishes before a durable intent exists. No await is allowed
  // between the synchronous sentinel write and the mutation request's fetch.
  if(!await dataopsRecoveryAccess(p,op,visible)||!visible()||p.op!==old)return;
  if(dataopsRecoveryHeld(p)){dataopsRecoveryRender(p);return;}
  store.ops.push(op);
  if(!dataopsRecoveryWrite(store)){store.ops.pop();dataopsRecoveryNotice(p);return;}
  record.submitted=true;record.operation=op;p.op=op;p.recoveryOwner=dbPrincipal;p.recoverySelection=op.key;
  attempted=true;op.ambiguous=false;
  const request=dataopsRequest(path,op.context,op.payload,op.key);dataopsRender(p);
  const reply=await request;if(op.sequence!==1)return;
  dataopsAccept(op,reply.operation,false);
  const authorized=visible()&&await dataopsRecoveryAccess(p,op,visible);
  if(authorized&&visible())dataopsAccept(op,reply.operation,true);
  if(op.protected){dataopsCaptureWap(p,op.operation);ui(p.status,reply.operation.state);}
 }catch(error){
  if(attempted&&op.sequence===1){
   if(error.observation){dataopsAccept(op,error.observation,false);let authorized=false;try{authorized=visible()&&await dataopsRecoveryAccess(p,op,visible);}catch{/* Keep the original minimal observation. */}if(authorized&&visible())dataopsAccept(op,error.observation,true);if(op.protected)dataopsCaptureWap(p,op.operation);}
   else if(!op.id&&!dataopsRejectBeforeAdmission(op,error))op.ambiguous=true;
   dataopsRecoverySave(op);
  }
  if(current()){if(dataopsAuthError(error))dataopsInvalidate(p);p.status.className='error';p.status.setAttribute('role','alert');ui(p.status,error.message);}
 }finally{if(p.submitToken===token){p.submitToken=null;p.pending=false;dataopsRender(p);}}
}
async function dataopsObserve(p,cancel){
 const op=p.op;if(!op||op.principal!==dbPrincipal||!dbRoleWithinHeader(op.context.selected_role)||!cancel&&op.restored||p.trackingPending||(cancel?!dataopsCanCancel(op):!dataopsCanObserve(op)))return;
 if(!cancel&&!dataopsRecoveryAuthorized(p,op))return;
 const stamp=dataopsRecoveryStamp(p);
 const tracking={};p.trackingToken=tracking;p.trackingPending=true;const epoch=p.epoch,sequence=op.sequence=(op.sequence||0)+1;
 const current=()=>p.op===op&&sequence===op.sequence&&epoch===p.epoch&&!p.invalid&&p.principal===dbPrincipal;
 const visible=()=>!cancel&&current()&&stamp===dataopsRecoveryStamp(p)&&dataopsRecoveryAuthorized(p,op);
 p.status.className='status-line';p.status.setAttribute('role','status');ui(p.status,'Pending');dataopsRender(p);
 try{
  if(!cancel&&!await dataopsRecoveryAccess(p,op,visible))return;
  const reply=op.id?await dataopsRequest('/api/v1/dataops/'+encodeURIComponent(op.id)+(cancel?'/cancel':''),op.context,cancel?{}:undefined):await dataopsRequest(op.path,op.context,op.payload,op.key);
  if(p.op!==op||sequence!==op.sequence)return;dataopsAccept(op,reply.operation,false);const authorized=!cancel&&visible()&&await dataopsRecoveryAccess(p,op,visible);if(authorized&&visible())dataopsAccept(op,reply.operation,true);if(op.protected)dataopsCaptureWap(p,op.operation);if(epoch===p.epoch)ui(p.status,reply.operation.state);
 }catch(error){
  if(p.op!==op||sequence!==op.sequence)return;
  op.protected=false;op.operation=null;
  if(error.observation&&!dataopsTerminal(op)){dataopsAccept(op,error.observation,false);let authorized=false;try{authorized=!cancel&&visible()&&await dataopsRecoveryAccess(p,op,visible);}catch{/* Retain state without protected results. */}if(authorized&&visible())dataopsAccept(op,error.observation,true);if(op.protected)dataopsCaptureWap(p,op.operation);}
  if(current()&&stamp===dataopsRecoveryStamp(p)){if(dataopsAuthError(error))dataopsInvalidate(p);p.status.className='error';p.status.setAttribute('role','alert');ui(p.status,error.message);}
 }finally{if(p.trackingToken===tracking){p.trackingToken=null;p.trackingPending=false;dataopsRender(p);}}
}
// Keep only the last authorized immutable artifact of each kind, in memory.
function dataopsWapEvidence(p){
 const scope=JSON.stringify([dataopsAiAuth({selected_role:p.role}),p.role,p.database,p.schema,p.context]);
 if(!dataopsAllowed(p,'wap_models')||!p.inventoryReady||!dataopsSameContext(p.context,p.inventoryContext)){p.wapEvidence=null;return null;}
 if(p.wapEvidence?.scope!==scope)p.wapEvidence={scope};
 return p.wapEvidence;
}
function dataopsRememberWap(p,kind,id,record,{historical=false,operationId}={}){
 const evidence=dataopsWapEvidence(p);
 if(!evidence||!id||record?.id!==id||record.database_id!==p.context.database_id||
    !record.model_id||!Number.isSafeInteger(record.version)||record.version<1||!record.operation_id||
    (!historical&&record.operation_id!==operationId))return;
 // An authorized GET can read across execution roles in the same binding.
 // Platform Admin and granted roles can have different grant revisions.
 const switchedRole=historical&&record.context?.selected_role!==p.role;
 const context=switchedRole?{...record.context,selected_role:p.role,grant_revision:p.context.grant_revision}:record.context;
 if(!['data_engineer','admin'].includes(record.context?.selected_role)||!dataopsSameContext(context,p.context))return;
 if(kind==='build'&&(!record.stage?.snapshot||record.destination?.database_id!==p.database))return;
 if(kind==='audit'&&(!record.stage?.snapshot||!record.build_id||typeof record.passed!=='boolean'||
    !Number.isSafeInteger(record.count)||record.count<0||!record.null_counts||typeof record.null_counts!=='object'||Array.isArray(record.null_counts)||
    !Object.values(record.null_counts).every(n=>Number.isSafeInteger(n)&&n>=0&&n<=record.count)))return;
 if(kind==='publication'&&(record.kind!=='publish'||!record.build_id||!record.audit_id||record.destination?.database_id!==p.database||!record.target?.snapshot))return;
 evidence[kind]=dataopsFreeze(dataopsClone(record));
}
function dataopsWapSelection(p){
 const evidence=dataopsWapEvidence(p),op=p.op?.principal===dbPrincipal?p.op:null;
 const selected=kind=>{
  const record=evidence?.[kind];
  return !p.modelDirty&&record&&record.id===$(p.id+'-'+kind).value&&
   record.model_id===p.model?.model_id&&record.version===p.model?.version&&
   (op?.id!==record.operation_id||!op.ambiguous&&dataopsState(op)==='FINISHED')?record:null;
 };
 const candidate=selected('build'),audit=selected('audit'),publication=selected('publication');
 const build=candidate&&[p.model.target,p.model.current_target_ref].some(target=>dataopsSameContext(target,candidate.destination))?candidate:null;
 const paired=build&&audit?.build_id===build.id&&audit.stage.snapshot===build.stage.snapshot&&
  dataopsRefKey(audit.stage.ref)===dataopsRefKey(build.stage.ref)?audit:null;
 const published=paired?.passed===true&&publication?.build_id===build.id&&publication.audit_id===paired.id&&
  dataopsSameContext(publication.destination,build.destination)?publication:null;
 return {build,audit:paired,published};
}
function dataopsCaptureWap(p,operation,preserveSelection=false){
 if(p.id!=='p35-wap'||!operation?.result)return;
 p.reviewToken=null;p.publishReview=null;p.publishImpact=null;
 const complete=operation.state==='FINISHED'&&!p.op?.ambiguous&&
  operation.child_unknown!==true&&!operation.queries?.some(q=>q.state==='UNKNOWN');
 for(const kind of ['build','audit','publication']){
  const value=operation.result[kind+'_id'];if(!value)continue;
  const n=$(p.id+'-'+kind),selected=n.value;
  if(![...n.options].some(o=>o.value===''))n.add(new Option(t('No selection'),''));
  if(![...n.options].some(o=>o.value===value))n.add(new Option(value,value));
  n.value=preserveSelection?selected:value;
  // Audit reads the build and persists evidence without committing a table change.
  if(complete&&(kind==='audit'||operation.committed===true)){
   dataopsRememberWap(p,kind,value,operation.result[kind],{operationId:operation.id});
  }
 }
}
function dataopsIngestFileAllowed(p){
 return p.inventoryReady&&!p.inventoryLoading&&p.inventoryContext===p.context&&dataopsAllowed(p,'manual_ingest');
}
async function dataopsReadFile(p,file,dropped=false){
 const input=$(p.id+'-file');
 if(p.pending||p.trackingPending||!dataopsIngestFileAllowed(p)){dataopsRender(p);return;}
 if(dropped||!file)input.value='';
 p.file=null;const epoch=p.epoch+1;
 p.fileFeedback=file?{epoch,key:'Reading file…',reading:true}:null;
 dataopsChanged(p);if(!file)return;
 try{const bytes=new Uint8Array(await file.arrayBuffer());if(epoch!==p.epoch)return;
  if(!dataopsIngestFileAllowed(p)){dataopsRender(p);return;}
  let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
  p.file=dataopsFreeze({filename:file.name,content_base64:btoa(binary)});p.fileFeedback=null;
  p.downstream=[];if(p.fileDetails)p.fileDetails.open=false;
  if(dropped&&typeof DataTransfer==='function'){
   try{const transfer=new DataTransfer();transfer.items.add(file);input.files=transfer.files;}
   catch{/* The field status also identifies the selected file. */}
  }
 }catch{
  if(epoch!==p.epoch)return;
  input.value='';p.fileFeedback={epoch,key:'Could not read {name}',values:{name:file.name}};
 }
 dataopsRender(p);
}
function dataopsIngestPayload(p){
 const id=p.id,mode=$(id+'-mode').value;if(!p.file||!['create','replace','append','upsert'].includes(mode)||!dataopsAllowed(p,'manual_ingest'))throw Error(t('Choose a file, context, explicit mode and target.'));
 const batch=Number($(id+'-batch').value);if(!Number.isSafeInteger(batch)||batch<1)throw Error(t('Choose a positive integer batch size.'));
 const payload={context:dataopsClone(p.context),...p.file,mode,format:$(id+'-format').value,encoding:$(id+'-encoding').value,batch_size:batch,atomic_append:$(id+'-atomic').checked,key_columns:$(id+'-keys').value.split(',').map(v=>v.trim()).filter(Boolean)};
 const delimiter=$(id+'-delimiter').value;if(delimiter)payload.delimiter=delimiter;
 const hints={};for(const line of $(id+'-hints').value.split('\n').filter(l=>l.trim())){const at=line.indexOf('=');if(at<1||!line.slice(at+1).trim())throw Error(t('Enter column types as name=type.'));hints[line.slice(0,at).trim()]=line.slice(at+1).trim();}if(Object.keys(hints).length)payload.schema_hints=hints;
 if(mode==='create'){const name=$(id+'-name').value.trim();if(!name)throw Error(t('Choose a file, context, explicit mode and target.'));payload.destination={database_id:p.database,schema_id:p.schema,physical_name:name};}
 else{const o=p.objects[Number($(id+'-target').value)];if($(id+'-target').value===''||!dataopsRef(o))throw Error(t('Choose a file, context, explicit mode and target.'));payload.target=dataopsRef(o);}
 if(mode==='upsert'&&!payload.key_columns.length)throw Error(t('Upsert key columns (required for upsert)'));return dataopsFreeze(payload);
}
function dataopsIngestImpactFrom(preview,mode){
 if(preview?.impact&&typeof preview.impact==='object')return preview.impact;
 const incoming=Number.isSafeInteger(preview?.total_rows)?preview.total_rows:0;
 return {mode,incoming_rows:incoming,existing_rows:mode==='create'?0:null,after_rows:mode==='create'?incoming:null};
}
async function dataopsIngestDownstream(p,target,current){
 if(!target?.object_id||!p.context||!dataopsAllowed(p,'manual_ingest'))return [];
 try{
  const path='/api/v1/objects/'+encodeURIComponent(target.object_id)+'/lineage?context='+encodeURIComponent(JSON.stringify(p.context));
  const reply=await dbAPI(path,p.context.selected_role);
  if(!current())return [];
  const names=[];
  for(const fact of [...(reply.confirmed||[]),...(reply.declared||[])]){
   if(!fact||typeof fact!=='object'||dataopsRefKey(fact.source?.ref)!==dataopsRefKey(target))continue;
   const ref=fact.target?.ref,label=fact.target?.label;
   const name=fact.model?.id||label||p.objects.find(o=>dataopsRefKey(dataopsRef(o))===dataopsRefKey(ref))?.label;
   if(typeof name==='string'&&name.trim()&&!names.includes(name))names.push(name);
  }
  return names.slice(0,10);
 }catch{return [];}
}
function dataopsIngestImpact(p){
 const node=$(p.id+'-impact'),confirm=$(p.id+'-confirm');if(!node||!confirm)return;
 const record=p.preview,impact=record?.impact;
 if(!impact||record.submitted){node.hidden=true;node.replaceChildren();confirm.hidden=true;confirm.className='primary';ui(confirm,'Confirm preview and ingest');return;}
 node.hidden=false;confirm.hidden=false;node.className='impact'+(impact.mode==='create'?' neutral':'');
 const incoming=impact.incoming_rows??record.total_rows??0,existing=impact.existing_rows,after=impact.after_rows;
 let key,values;
 if(impact.mode==='create'){key='Create a new table and write {incoming} rows.';values={incoming};}
 else if(impact.mode==='append'&&existing!=null){key='Add {incoming} rows to the existing {existing} rows (total {after}).';values={incoming,existing,after};}
 else if(impact.mode==='append'){key='Add {incoming} rows. The existing row count is unavailable.';values={incoming};}
 else if(impact.mode==='replace'&&existing!=null){key='Delete all {existing} existing rows and replace them with {incoming} rows.';values={incoming,existing};}
 else if(impact.mode==='replace'){key='Replace the table with {incoming} rows. The existing row count is unavailable.';values={incoming};}
 else if(impact.mode==='upsert'&&existing!=null){key='Update or add {incoming} rows by key (existing {existing} rows).';values={incoming,existing};}
 else {key='Update or add {incoming} rows by key. The existing row count is unavailable.';values={incoming};}
 node.replaceChildren();const line=document.createElement('strong');ui(line,key,values);node.append(line);
 if(p.downstream?.length){node.append(document.createElement('br'));const down=document.createElement('span');ui(down,'Downstream transformations: {names} · Published contents stay unchanged until rebuilt.',{names:p.downstream.join(language==='ja'?'、':', ')});node.append(down);}
 let label,labelValues;
 if(impact.mode==='create'){label='Create and ingest';labelValues={};}
 else if(impact.mode==='append'){label='Append {incoming} rows';labelValues={incoming};}
 else if(impact.mode==='replace'&&existing!=null){label='Delete {existing} rows and replace';labelValues={existing};}
 else if(impact.mode==='replace'){label='Delete all rows and replace';labelValues={};}
 else {label='Update or add {incoming} rows';labelValues={incoming};}
 confirm.className='primary'+(impact.mode==='replace'||impact.mode==='upsert'?' danger':'');
 ui(confirm,label,labelValues);
}
function dataopsIngestContext(p){
 const id=p.id,allowed=dataopsAllowed(p,'manual_ingest'),context=$(id+'-task-context');
 const fileAllowed=dataopsIngestFileAllowed(p);
 if(p.fileDetails&&!p.file)p.fileDetails.open=true;
 if(p.fileSummary)ui(p.fileSummary,p.file?'Change input file':'Input file (CSV / JSON / JSONL · up to 10 MiB)');
 if(p.clearFile){p.clearFile.hidden=!p.file;p.clearFile.disabled=!fileAllowed||p.pending||p.trackingPending;}
 const selected=$(id+'-selected-file');if(selected){selected.hidden=!p.file;if(p.file)wbTaskText(selected,'Selected input: {name}',{name:p.file.filename});}
 if(p.fileFeedback&&(p.fileFeedback.epoch!==p.epoch||!fileAllowed)){
  if(p.fileFeedback.reading)$(id+'-file').value='';
  p.fileFeedback=null;
 }
 const feedback=p.pending||p.trackingPending?{key:'Wait for the current ingestion request before choosing a file.'}:
  !fileAllowed?{key:'Choose or refresh an authorized destination before choosing a file.'}:
  p.fileFeedback||(p.file?{key:'Selected file: {name}',values:{name:p.file.filename}}:{key:'Choose a CSV / JSON / JSONL file.'});
 const fileStatus=$(id+'-file-status');
 fileStatus.hidden=!feedback.key;wbTaskText(fileStatus,feedback.key,feedback.values);
 const d=p.databases.find(d=>d.id===p.database),s=p.schemas.find(s=>s.id===p.schema);
 const selection=allowed?[p.context.sql_catalog,p.context.sql_schema,p.context.selected_role].join(' / '):
  [d?dbNeutralName(d.display_name||d.sql_catalog,'Unavailable database'):t(p.database?'Unavailable database':'No selection'),
   s?dbNeutralName(s.display_name||s.sql_name,'Unavailable schema'):t(p.schema?'Unavailable schema':'No selection'),p.role||t('No selection')].join(' / ');
 wbTaskText(context?.children[0],allowed?'Authorized destination: {context}':'Selected destination: {context}',{context:selection});
 const mode=$(id+'-mode').value,target=$(id+'-target').value;
 const table=mode==='create'?$(id+'-name').value.trim():mode&&target!==''?p.objects[Number(target)]?.label:'';
 wbTaskText(context?.children[1],'Draft table: {table}',{table:table||t('No selection')});
 const next=!p.database||!p.schema?'Choose a destination, then choose a file and target table, preview, and confirm.':
  p.pending||p.trackingPending?'Wait for the current ingestion request.':
  p.inventoryLoading?'Checking the ingestion destination…':
  !allowed?'Destination unavailable. Refresh the ingestion destination or choose a different database, schema or role.':
  dataopsOwnedUnresolved(p)||dataopsRecoveryOwner().ops.some(op=>op.panel===id&&dataopsUnresolved(op))?'New ingestion is held. Resolve the original operation before submitting.':
  p.preview?.submitted?'Preview already submitted. Check the original operation.':
  p.preview?(dataopsRecoveryHeld(p)?'Preview held. Check the recovery storage notice before submitting.':'Ready to confirm. Review the input preview, then confirm ingestion.'):
  p.file&&table?'Draft. Preview the input before confirming ingestion.':'Draft. Choose a file and table, then preview.';
 wbTaskText(context?.children[2],next);
 const targetCode=$(id+'-target-value');
 const targetText=mode==='create'?`${allowed?p.context.sql_catalog:dbNeutralName(d?.display_name||d?.sql_catalog,'Unavailable database')}.${allowed?p.context.sql_schema:dbNeutralName(s?.display_name||s?.sql_name,'Unavailable schema')}.${$(id+'-name').value.trim()||t('No selection')}`:
  mode&&target!==''?p.objects[Number(target)]?.label||t('No selection'):t('No selection');
 if(targetCode)targetCode.textContent=targetText;
 const message=p.status?.textContent?.trim()||next;
 if(workbenchAtlasIngestionView()==='file')workbenchAtlasMessage('ingestion',message);
}
function dataopsInstallIngest(){
 const p=dataopsPanel('p35-ingest','Manual file ingestion',workbenchAtlasHost('ingestion-file')),id=p.id;
 const context=dataopsNode('div',id+'-task-context');context.className='wb-task-context';context.setAttribute('aria-live','polite');context.hidden=true;
 for(let i=0;i<3;i++)dataopsNode('p','',context);
 p.form.before(context);
 $(id+'-accepted-context').hidden=true;
 ui($(id+'-refresh'),'Refresh destination');
 ui($(id+'-reconnect'),'Reconnect to original ingestion');
 p.fields.id=id+'-context-controls';
 p.fields.append($(id+'-refresh'));
 const fileDetails=dataopsNode('details',id+'-file-details',p.form);
 fileDetails.open=true;p.fileDetails=fileDetails;
 p.fileSummary=dataopsNode('summary','',fileDetails,'Input file (CSV / JSON / JSONL · up to 10 MiB)');
 const dropzone=dataopsNode('div',id+'-dropzone',fileDetails);
 const file=dataopsNode('input',id+'-file',dropzone);file.hidden=true;
 file.type='file';file.accept='.csv,.json,.jsonl,.ndjson';file.onchange=()=>dataopsReadFile(p,file.files[0]);
 const choose=dataopsButton(dropzone,id+'-choose-file','Choose file',()=>{if(!file.disabled)file.click();});
 const clear=dataopsButton(dropzone,'clear-upload','Clear selected file',()=>dataopsReadFile(p,null));clear.className='small';clear.hidden=true;p.clearFile=clear;
 const fileStatus=dataopsNode('p',id+'-file-status',dropzone);fileStatus.className='help';
 choose.setAttribute('aria-describedby',fileStatus.id);
 p.form.addEventListener('dragover',e=>e.preventDefault());p.form.addEventListener('drop',e=>e.preventDefault());
 dropzone.addEventListener('drop',e=>{e.preventDefault();const dropped=e.dataTransfer?.files[0];if(dropped)return dataopsReadFile(p,dropped,true);});
 const selectedFile=dataopsNode('p',id+'-selected-file',p.form);selectedFile.className='help';selectedFile.hidden=true;
 const destinationHeading=dataopsNode('h2',id+'-destination-heading',p.form,'Destination and update method');
 const destination=dataopsNode('div',id+'-destination-fields',p.form);destination.className='atlas-fields';
 dataopsField(destination,id+'-mode','Update method','select',[['','Choose mode'],['create','Create new'],['append','Append'],['replace','Full replace'],['upsert','Update and add by key']]);
 dataopsField(destination,id+'-name','Create table name').placeholder='raw_orders';dataopsField(destination,id+'-target','Existing table','select');
 dataopsField(destination,id+'-keys','Upsert key columns (required for upsert)').parentElement.id=id+'-keys-field';
 const options=workflowDetails(p.form,'File parsing and ingestion options');options.id=id+'-options';
 const tuning=dataopsNode('div','',options);tuning.className='job-fields';
 dataopsField(tuning,id+'-format','File format','select',[['auto','auto'],['csv','CSV'],['json','JSON'],['jsonl','JSONL (one object per line)']]);
 dataopsField(tuning,id+'-encoding','Encoding').value='utf-8';dataopsField(tuning,id+'-delimiter','Delimiter');
 dataopsField(tuning,id+'-hints','Column types (name=type, one per line)','textarea');
 const atomic=dataopsField(tuning,id+'-atomic','Atomic append');atomic.type='checkbox';atomic.checked=true;
 const batch=dataopsField(tuning,id+'-batch','Batch size');batch.type='number';batch.min='1';batch.value='500';
 const previewActions=dataopsNode('div','',p.form);previewActions.className='atlas-actions';
 const sample=dataopsNode('div',id+'-sample',p.form);sample.className='table-wrap';sample.setAttribute('aria-live','polite');
 dataopsButton(previewActions,id+'-preview','Preview ingestion result',()=>dataopsTask(p,async current=>{
  if(dataopsOwnedUnresolved(p))return;
  p.preview=null;p.downstream=[];dataopsRender(p);const payload=dataopsIngestPayload(p),reply=await dataopsRequest('/api/v1/ingests/preview',payload.context,payload);if(!current())return;
  const preview=reply.preview;if(!preview?.request_fingerprint)throw Error(t('Preview required after every input change.'));
  const signature=JSON.stringify([dbPrincipal,payload.context,preview.request_fingerprint]);p.requests??=new Map();
  const impact=dataopsIngestImpactFrom(preview,payload.mode);
  if(!p.requests.has(signature))p.requests.set(signature,{payload:dataopsFreeze({...dataopsClone(payload),expected_preview_fingerprint:preview.request_fingerprint}),fingerprint:preview.request_fingerprint,total_rows:preview.total_rows,impact,key:crypto.randomUUID(),submitted:false});
  else Object.assign(p.requests.get(signature),{total_rows:preview.total_rows,impact});
  p.preview=p.requests.get(signature);if(p.preview.operation)p.op=p.preview.operation;
  sample.replaceChildren();dataopsNode('p','',sample,'Input preview only. Existing target types are checked at execution.').className='help';const count=dataopsNode('p','',sample);ui(count,'Input rows: {count}',{count:preview.total_rows});const grid=dataopsNode('div','',sample);table(grid,preview.columns,preview.rows||[],false);
  if(payload.mode!=='create'){
   const target=payload.target;
   const downstream=await dataopsIngestDownstream(p,target,current);
   if(!current()||p.preview?.fingerprint!==preview.request_fingerprint||p.preview?.submitted)return;
   p.downstream=downstream||[];
  }
  dataopsRender(p);ui(p.status,'');
 }));
 const impact=dataopsNode('p',id+'-impact',p.form);impact.className='impact';impact.hidden=true;
 const confirmActions=dataopsNode('div','',p.form);confirmActions.className='atlas-actions';
 const confirm=dataopsButton(confirmActions,id+'-confirm','Confirm preview and ingest',()=>{if(!p.preview||!dataopsAllowed(p,'manual_ingest'))return;return dataopsSubmit(p,'/api/v1/ingests',p.preview.payload,p.preview);});confirm.className='primary';
 const operationActions=dataopsNode('div','',p.body);operationActions.className='toolbar';
 p.recovery.before(operationActions);
 operationActions.append($(id+'-observe'),$(id+'-cancel'));
 operationActions.after($(id+'-observe-help'));
 p.form.onsubmit=e=>{e.preventDefault();};
 for(const key of ['mode','name','target','format','encoding','delimiter','hints','keys','atomic','batch']){const n=$(id+'-'+key);n.oninput=n.onchange=()=>dataopsChanged(p);}
 return p;
}
function dataopsAddSource(p,alias='',ref=null){
 const row=dataopsNode('div','',$(p.id+'-sources'));row.className='toolbar';const key=crypto.randomUUID();
 const a=dataopsField(row,p.id+'-alias-'+key,'Source alias'),select=dataopsField(row,p.id+'-source-'+key,'Input table','select');a.value=alias;
 const source={row,alias:a,table:select};p.sources.push(source);dataopsOptions(select,p.tables.map((o,i)=>[String(i),o.label]),ref?String(p.tables.findIndex(o=>dataopsRefKey(dataopsRef(o))===dataopsRefKey(ref))):'');
 a.oninput=select.onchange=()=>dataopsChanged(p);dataopsButton(row,'','Remove source',()=>{p.sources=p.sources.filter(s=>s!==source);row.remove();dataopsChanged(p);});
}
function dataopsWapErrors(p,focus=false){
 const id=p.id,name=$(id+'-name').value.trim(),min=Number($(id+'-min').value);
 const errors=[
  ['name',$(id+'-target').value===''&&(!/^[a-z][a-z0-9_]{0,30}$/.test(name)||['system','information_schema','tpch','tpcds'].includes(name))?'Enter a bare table name: 1–31 lowercase letters, digits or underscores, starting with a letter. Reserved names are unavailable.':''],
  ['min',!Number.isSafeInteger(min)||min<1?'Enter a whole number of at least 1.':'']
 ];
 for(const [field,message] of errors){
  const input=$(id+'-'+field),error=$(id+'-'+field+'-error');
  input.setAttribute('aria-invalid',String(!!message));error.hidden=!message;wbTaskText(error,message);
 }
 const invalid=errors.find(([,message])=>message);
 if(invalid&&focus)$(id+'-'+invalid[0]).focus();
 return !!invalid;
}
function dataopsWapTarget(p,target){
 const schema=p.schemas.find(s=>s.id===target?.schema_id)?.sql_name;
 const table=target?.physical_name||p.tables.find(o=>target?.object_id&&dataopsRefKey(dataopsRef(o))===dataopsRefKey(target))?.physical_name;
 return target?.database_id===p.database&&schema&&table?[p.context.sql_catalog,schema,table].join('.'):'';
}
function dataopsPublishFingerprint(p,build,audit){
 return JSON.stringify([dbPrincipal,p.context,p.model?.model_id,p.model?.version,p.modelDirty,build?.id,audit?.id,build?.stage?.snapshot,audit?.stage?.snapshot]);
}
function dataopsPublishReviewCurrent(p){
 const {build,audit,published}=dataopsWapSelection(p);
 return !!build&&audit?.passed===true&&!published&&!!p.publishReview&&
  p.publishReview.fingerprint===dataopsPublishFingerprint(p,build,audit);
}
function dataopsReviewWap(p){
 const id=p.id,allowed=dataopsAllowed(p,'wap_models'),waiting=p.pending||p.trackingPending||p.inventoryLoading;
 const held=dataopsOwnedUnresolved(p)||dataopsRecoveryHeld(p);
 if(!allowed||waiting||held)return;
 const {build,audit,published}=dataopsWapSelection(p);
 if(!build||audit?.passed!==true||published)return;
 const fingerprint=dataopsPublishFingerprint(p,build,audit),token={};
 p.reviewToken=token;p.publishReview={fingerprint};p.publishImpact={fingerprint,loading:true};
 dataopsRenderWapGuide(p);
 const current=()=>p.reviewToken===token&&!p.invalid&&p.publishReview?.fingerprint===fingerprint&&
  dataopsWapSelection(p).build?.id===build.id&&dataopsWapSelection(p).audit?.id===audit.id;
 const run=async()=>{
  const value=await dataopsPublishImpact(p,build,audit,current);
  if(!current())return;
  p.publishImpact={fingerprint,value};
 };
 return run().catch(error=>{
  if(!current())return;
  if(dataopsAuthError(error))dataopsInvalidate(p);
  else p.publishImpact={fingerprint,error:error.message};
 }).finally(()=>{if(p.reviewToken===token)p.reviewToken=null;dataopsRenderWapGuide(p);});
}
async function dataopsPublishImpact(p,build,audit,current){
 const destination=build?.destination;
 if(!destination)return null;
 const target=dataopsWapTarget(p,destination)||destination.physical_name||'';
 const database=p.databases.find(d=>d.id===p.context.database_id);
 const roles=(database?.allowed_roles||[]).map(dbRoleDisplayLabel).filter(Boolean);
 let existing=!!destination.object_id,objectID=destination.object_id||null,existingRows=null;
 if(!objectID&&destination.schema_id&&destination.physical_name){
  try{
   const params=new URLSearchParams({database_id:p.context.database_id,schema_id:destination.schema_id,name:destination.physical_name});
   const reply=await dbAPI('/api/v1/objects/resolve?'+params.toString(),p.role);
   if(!current())return null;
   objectID=reply?.ref?.object_id||reply?.object?.ref?.object_id||reply?.object?.id||null;
   existing=!!objectID;
  }catch(error){
   if(!current())return null;
   if(![404].includes(error.status)&&error.code!=='NOT_FOUND')throw error;
   existing=false;
  }
 }
 if(existing&&objectID){
  try{
   const reply=await dbAPI('/api/v1/objects/'+encodeURIComponent(objectID)+'?detail=snapshots',p.role);
   if(!current())return null;
   const snapshots=Array.isArray(reply?.snapshots)?reply.snapshots:[];
   // Only the catalog's current snapshot holds the published rows.
   const currentID=typeof reply?.current_snapshot_id==='string'?reply.current_snapshot_id:null;
   const latest=currentID?snapshots.find(item=>item.snapshot_id===currentID):null;
   const summary=latest?.summary||{};
   const raw=summary['total-records']??summary['total_records']??summary['record-count']??summary['record_count']??summary['total-rows']??summary['total_rows'];
   const value=typeof raw==='string'&&/^\d+$/.test(raw)?Number(raw):Number.isSafeInteger(raw)&&raw>=0?raw:null;
   existingRows=value;
  }catch(error){
   if(!current())return null;
   if(dataopsAuthError(error))throw error;
   existingRows=null;
  }
 }
 return {target,existing,incoming:audit?.count??build?.count??null,existingRows,roles};
}
function dataopsWapAction(p,path,capability){
 dataopsRenderWapGuide(p);
 const id=p.id,action={build:'build-action',audit:'audit-action',publish:'publish',rollback:'rollback'}[path];
 const button=action&&$(id+'-'+action);
 const capabilityName=capability||{build:'wap_build',audit:'wap_audit',publish:'wap_publish',rollback:'wap_rollback'}[path];
 if(!button||button.disabled||!dataopsAllowed(p,capabilityName))return;
 const body=path==='build'?{model_id:p.model?.model_id,version:p.model?.version}:path==='audit'?{build_id:$(id+'-build').value}:path==='publish'?{build_id:$(id+'-build').value,audit_id:$(id+'-audit').value}:{publication_id:$(id+'-publication').value};
 if(path==='build'&&(!p.model||p.modelDirty))return;
 if(path==='publish'&&!dataopsPublishReviewCurrent(p))return;
 const signature=JSON.stringify([dbPrincipal,path,p.context,body]);p.actions??=new Map();if(!p.actions.has(signature)||dataopsTerminal(p.actions.get(signature).operation))p.actions.set(signature,{});
 const focused=document.activeElement===button;
 return dataopsSubmit(p,'/api/v1/wap/'+path,body,p.actions.get(signature)).then(()=>{
  if(!focused||p.invalid||![button,document.body].includes(document.activeElement))return;
  const next=[id+'-save',id+'-build-action',id+'-audit-action','copilot-publish',id+'-publish'].map(key=>$(key)).find(n=>n&&!n.hidden&&!n.disabled);
  (next||p.stageReason||$(id+'-guide-next')).focus();
 });
}
function dataopsRenderWapGuide(p){
 const id=p.id,allowed=dataopsAllowed(p,'wap_models'),waiting=p.pending||p.trackingPending||p.inventoryLoading;
 const held=dataopsOwnedUnresolved(p)||dataopsRecoveryHeld(p);
 const {build,audit,published}=dataopsWapSelection(p);
 const evidenceBuild=p.wapEvidence?.build||null;
 const candidate=build||evidenceBuild;
 const stale=!build&&!!candidate&&!published;
 const saved=allowed&&p.model&&!p.modelDirty;
 const target=allowed?dataopsWapTarget(p,published?.destination||build?.destination||p.model?.current_target_ref||p.model?.target):'';
 const reviewCurrent=dataopsPublishReviewCurrent(p);
 const phase=published?6:!saved?0:!build?1:audit?.passed===false?3:audit?.passed!==true?2:!reviewCurrent?4:5;
 const stepIndex=published?6:!saved?0:!build?2:audit?.passed===false?2:audit?.passed!==true?3:!reviewCurrent?4:5;
 $(id+'-steps').setAttribute('aria-label',t('Transformation flow'));
 const steps=['Transform model','Save model version','Build SANDBOX candidate','Quality check','Review publication','Publish'];
 steps.forEach((label,i)=>{
  const n=$(id+'-step-'+i);n.setAttribute('aria-current',i===stepIndex?'step':'false');
  wbTaskText(n,i<stepIndex?'{step}: complete':i===stepIndex?'{step}: current':'{step}',{step:t(label)});
 });
 const c=p.context;
 wbTaskText($(id+'-guide-context'),allowed?'Database / schema / role: {context}':'Select an authorized database, schema and role.',{context:allowed?[c.sql_catalog,c.sql_schema,c.selected_role].join(' / '):''});
 wbTaskText($(id+'-guide-model'),allowed&&p.model?'Saved model: {model} · v{version}':'No saved model selected.',{model:p.model?.model_id,version:p.model?.version});
 wbTaskText($(id+'-action-target'),target?'Saved transformation target: {target}':'Saved output table not available.',{target});
 const latest=p.model?`v${p.model.version}`:t('No saved version');
 wbTaskText($(id+'-saved-version'),'Selected saved version: {version}',{version:latest+(p.model?` · ${t(p.modelDirty?'Inputs differ from the saved version. Save again.':'Matches the current inputs')}`:'')});
 const op=p.op?.principal===dbPrincipal?p.op:null;
 const next=waiting?'Wait for the current request.':held?
  op?.ambiguous?'Outcome unknown. Check the original request before continuing.':'Check the original request before continuing.':
  !allowed?'Choose or refresh the transformation context.':
  phase===0?'Save the model to build a candidate.':
  phase===1?'Build a candidate from the saved model.':
  phase===2?'Pass the quality check to review publication.':
  phase===3?'Quality failed. Edit the model or build again.':
  phase===4?'Review the publication to publish.':
  phase===5?(dataopsAllowed(p,'wap_publish')?'Publication reviewed. Publish the candidate.':'An Admin can publish this audited build.'):
  'Published. Open the table to use it.';
 const availabilityShown=!p.availability.hidden;
 const reasonHost=phase===0?p.stageRows.model:[1,2,3].includes(phase)?p.stageRows.candidate:phase===4?p.stageRows.review:phase===5?p.stageRows.publish:p.stageRows.published;
 p.stageReason.hidden=availabilityShown||phase===6;
 if(!p.stageReason.hidden){reasonHost.append(p.stageReason);wbTaskText(p.stageReason,next);}
 p.guideNext.hidden=true;
 const candidateLine=$(id+'-candidate');
 if(candidate){
  const destination=dataopsWapTarget(p,candidate.destination)||candidate.destination?.physical_name||'';
  const statusKey=published?'Published':stale?'Input changed; the candidate is invalid. Save and rebuild.':audit?.passed===true?'Quality check passed':audit?.passed===false?'Quality check failed · publication is blocked':'Awaiting quality check';
  candidateLine.textContent=t('Candidate: {target} · model v{version} / build {build}',{target:destination,version:candidate.version,build:candidate.build||candidate.id})+' · '+t(statusKey);
  candidateLine.className=stale?'error':'';
 }else{
  wbTaskText(candidateLine,'Build a candidate from the saved model.');
  candidateLine.className='';
 }
 const quality=$(id+'-quality-result');quality.hidden=!audit;
 wbTaskText(quality,audit?'{quality} · Rows: {count} · NULL counts: {nulls}':'',{quality:t(audit?.passed?'Passed':'Failed'),count:audit?.count,nulls:audit?Object.entries(audit.null_counts).map(([k,v])=>k+': '+v).join(', ')||t('No columns checked'):''});
 const existing=$(id+'-target').value!=='';
 for(const key of ['name','target-schema']){
  const n=$(id+'-'+key);n.disabled=!!waiting||!allowed||existing;n.parentElement.hidden=existing;
 }
 const save=$(id+'-save');save.disabled=!!waiting||!allowed;save.className=phase===0?'primary':'';
 const buildReady=saved&&!published,auditReady=!!build&&audit?.passed!==true&&!published,reviewReady=!!build&&audit?.passed===true&&!published,publishReady=reviewCurrent&&!published;
 for(const [action,capability,ready,primary] of [
  ['build-action','wap_build',buildReady,phase===1||phase===3],
  ['audit-action','wap_audit',auditReady,phase===2],
  ['publish','wap_publish',publishReady,phase===5]
 ]){
  const n=$(id+'-'+action);n.disabled=!!waiting||held||!dataopsAllowed(p,capability)||!ready;n.className=primary?'primary':'';
 }
 p.reviewButton.disabled=!!waiting||held||!dataopsAllowed(p,'wap_audit')||!reviewReady;
 p.reviewButton.className=phase===4?'primary':'';
 p.reviewButton.hidden=published;
 const described=availabilityShown?p.availability.id:p.stageReason.id;
 for(const node of [save,$(id+'-build-action'),$(id+'-audit-action'),p.reviewButton])if(node)node.setAttribute('aria-describedby',described);
 p.publishButton.setAttribute('aria-describedby',id+'-publish-review '+described);
 const review=$(id+'-publish-review'),impact=p.publishImpact?.fingerprint===p.publishReview?.fingerprint?p.publishImpact:null;
 review.hidden=!reviewCurrent;
 if(reviewCurrent){
  if(impact?.loading)wbTaskText(review,'Loading publication impact…');
  else if(impact?.error)wbTaskText(review,impact.error);
  else if(impact?.value){
   const {value}=impact,incoming=value.incoming;
   const existingLine=value.existing?
    (value.existingRows==null?t('Replace published rows with {incoming} rows. The published row count is unavailable.',{incoming}):t('Replace {existing} published rows with {incoming} rows.',{existing:value.existingRows,incoming}))
    :t('Create a new MARTS table with {incoming} rows.',{incoming});
   const rolesLine=value.existing?(value.roles.length?t('Referencing roles: {roles} · Previous versions can be restored from publication history.',{roles:value.roles.join(language==='ja'?'、':', ')}):t('The referencing roles are unavailable.')):'';
   review.replaceChildren();
   review.textContent=existingLine+(rolesLine?'\n'+rolesLine:'');
   review.className='impact'+(value.existing?'':' neutral');
   const label=value.existing?'Replace and publish':'Publish to MARTS';
   ui($(id+'-publish'),label);
   $(id+'-publish').className='primary'+(value.existing?' danger':'');
  }else wbTaskText(review,'Review the output and the Build/Audit pair before publishing.');
  if(!impact?.value){const existing=!!build?.destination?.object_id;ui($(id+'-publish'),existing?'Replace and publish':'Publish to MARTS');$(id+'-publish').className='primary'+(existing?' danger':'');}
  if(!dataopsAllowed(p,'wap_publish'))$(id+'-publish').className='';
 }else{review.replaceChildren();review.className='';}
 if(!reviewCurrent)ui($(id+'-publish'),'Publish to MARTS');
 p.publishedRow.hidden=!published;
 if(published){
  p.openTable.hidden=!target;p.openTable.setAttribute('href','#explore/'+target);p.openTable.href='#explore/'+target;
  p.auditLink.hidden=!published.id;p.auditLink.setAttribute('href','#audit/'+published.id);p.auditLink.href='#audit/'+published.id;
  ui(p.auditLink,'Publication record {id}',{id:published.id});
 }else{p.openTable.hidden=true;p.auditLink.hidden=true;}
 p.aiActions.hidden=!(p.aiPending||p.aiOrigin);
 p.stageButton.hidden=!(p.aiPending||p.aiOrigin);
 p.stageButton.disabled=!!waiting||!(p.aiPending||p.aiOrigin);
 ui(p.stageButton,p.aiPending?'Load AI draft into the model':'Edit AI draft');
 for(const key of ['metadata-id','metadata-kind','metadata-load','build','audit','publication'])$(id+'-'+key).disabled=!!waiting||!allowed;
 $(id+'-open-recovery').hidden=!held;
 const outcome=$(id+'-outcome');
 p.status.hidden=p.status.className!=='error';
 const state=op&&dataopsState(op);
 const expected={'manual-wap-build':build,'manual-wap-audit':audit,'manual-wap-publish':published}[op?.action];
 const unverifiedResult=state==='FINISHED'&&op.action!=='manual-wap-rollback'&&
  op.action?.startsWith('manual-wap-')&&expected?.operation_id!==op.id;
 const receipt=op?.protected&&op.capture===dataopsRecoveryStamp(p)&&dataopsRecoveryAuthorized(p,op)?op.operation:null;
 const showOutcome=op&&(op.ambiguous||['FAILED','CANCELED','REJECTED','UNKNOWN','UNCONFIRMED'].includes(state)||receipt?.cleanup_error);
 outcome.hidden=!showOutcome&&!unverifiedResult;
 $(id+'-observe').hidden=!op||op.restored||(!held&&!showOutcome)||!dataopsCanObserve(op);
 $(id+'-cancel').hidden=!op||!dataopsCanCancel(op);
 $(id+'-observe-help').hidden=$(id+'-observe').hidden||!!op?.id;
 wbTaskText(outcome,showOutcome?'Original operation: {state}. {detail}':unverifiedResult?'Operation finished. No matching result is verified; check saved metadata.':'',{state:state?t(state):'',detail:receipt?.cleanup_error||receipt?.error_code||(typeof receipt?.error==='string'?receipt.error:'')});
 const unverified=allowed&&[['build',build],['audit',audit],['publication',published]].some(([k,record])=>$(id+'-'+k).value&&!record);
 $(id+'-history-note').hidden=!unverified;
 if(availabilityShown||phase===6)p.stageReason.hidden=true;
}
function dataopsWapPayload(p){
 const id=p.id,sources={};for(const s of p.sources){const alias=s.alias.value.trim(),ref=s.table.value!==''?dataopsRef(p.tables[Number(s.table.value)]):null;if(!/^[A-Za-z_][A-Za-z0-9_]*$/.test(alias)||alias in sources||!ref)throw Error(t('Duplicate or invalid source alias.'));sources[alias]=ref;}
 const targetIndex=$(id+'-target').value,target=targetIndex!==''?dataopsRef(p.tables[Number(targetIndex)]):{database_id:p.database,schema_id:$(id+'-target-schema').value,physical_name:$(id+'-name').value.trim()};
 if(!Object.keys(sources).length||!$(id+'-model-id').value.trim()||!$(id+'-sql').value.trim()||!target||(!target.object_id&&(!p.schemas.some(s=>s.id===target.schema_id)||!target.physical_name)))throw Error(t('Choose a model ID, SQL, sources and target.'));
 const min=Number($(id+'-min').value);
 return {model_id:$(id+'-model-id').value.trim(),sql:$(id+'-sql').value,sources,target,expected_version:p.model?.version??null,min_rows:min,not_null:$(id+'-not-null').value.split(',').map(v=>v.trim()).filter(Boolean)};
}
function dataopsLoadModel(p,model,{preserveOrigin=false}={}){
 if(!preserveOrigin){p.aiOrigin=null;p.aiDetails=null;p.aiDetailsKey='';}p.aiToken=null;p.reviewToken=null;p.publishReview=null;p.publishImpact=null;++p.epoch;p.model=model||null;p.modelDirty=!model;const id=p.id;
 $(id+'-model-id').value=model?.model_id||'';$(id+'-model-id').readOnly=!!model;$(id+'-sql').value=model?.sql||'SELECT * FROM source';
 for(const field of ['name','min']){
  $(id+'-'+field).setAttribute('aria-invalid','false');$(id+'-'+field+'-error').hidden=true;
 }
 $(id+'-min').value=model?.min_rows??1;$(id+'-not-null').value=(model?.not_null||[]).join(', ');
 $(id+'-sources').replaceChildren();p.sources=[];for(const [alias,ref] of Object.entries(model?.sources||{source:null}))dataopsAddSource(p,alias,ref);
 const target=model?.current_target_ref||model?.target;$(id+'-name').value=target?.physical_name||'';$(id+'-target-schema').value=target?.schema_id||p.schema;
 $(id+'-target').value=target?.object_id?String(p.tables.findIndex(o=>dataopsRefKey(dataopsRef(o))===dataopsRefKey(target))):'';
 ui(p.status,model?'Saved version {version}':'Save or load a model version before building.',{version:model?.version});dataopsRender(p);
}
Object.assign(i18n.ja,{
 'Manual WAP progress':'手動 WAP の進捗','Define and save':'定義と保存','Audit quality':'品質監査',
 '{step}: complete':'{step}：完了','{step}: current':'{step}：現在',
 'Database / schema / role: {context}':'データベース / スキーマ / ロール：{context}',
 'Select an authorized database, schema and role.':'権限のあるデータベース、スキーマ、ロールを選択してください。',
 'Saved model: {model} · v{version}':'保存済みモデル：{model} · v{version}','No saved model selected.':'保存済みモデルが選択されていません。',
 'Saved WAP target: {target}':'保存済みの公開先：{target}','Saved output table not available.':'保存済みの出力テーブルを確認できません。',
 'Outcome unknown. Check the original request before continuing.':'結果が不明です。続行する前に元のリクエストを確認してください。',
 'Check the original request before continuing.':'続行する前に元のリクエストを確認してください。',
 'Choose or refresh the WAP context.':'WAP の接続先を選択または更新してください。',
 'Define and save a model version, or load a saved model.':'モデルを定義して保存するか、保存済みモデルを読み込んでください。',
 'Build the saved model version.':'保存済みのモデルをビルドしてください。',
 'Quality failed. Review the results and edit the model or build again.':'品質監査に失敗しました。結果を確認してモデルを編集するか、再度ビルドしてください。',
 'Audit the selected build to check its quality.':'選択したビルドの品質を監査してください。',
 'The output table could not be resolved. Review the saved build.':'出力テーブルを特定できません。保存済みのビルドを確認してください。',
 'Review the output and the Build/Audit pair before publishing.':'公開前に出力先とビルド・監査の組み合わせを確認してください。',
 'An Admin can publish this audited build.':'管理者は監査済みのビルドを公開できます。',
 'Publication verified for the selected Build/Audit pair.':'選択したビルドと監査の組み合わせで公開を確認しました。',
 '{quality} · Rows: {count} · NULL counts: {nulls}':'{quality} · 行数：{count} · NULL 件数：{nulls}','No columns checked':'検査対象の列なし',
 'Publish to {target} using Build {build} and Audit {audit}. {effect}':'ビルド {build} と監査 {audit} を使い、{target} に公開します。{effect}',
 'Replace the output table contents with this audited build.':'出力テーブルの内容をこの監査済みビルドで置き換えます。',
 'Create the output table from this audited build.':'この監査済みビルドから出力テーブルを作成します。',
 'Define or edit model':'モデルの定義・編集','Source SQL and input tables':'ソース SQL と入力テーブル',
 'Choose the input tables and use their aliases in SQL.':'入力テーブルを選び、SQL ではその別名を使用してください。',
 'Input table':'入力テーブル','Publish destination':'公開先','Existing output table (optional)':'既存の出力テーブル（任意）',
 'Output schema':'出力スキーマ','New output table name':'新規出力テーブル名',
 'Enter only the new table name, for example daily_totals. Select its schema separately.':'daily_totals のようにテーブル名だけを入力してください。スキーマは別に選びます。',
 'Quality rules':'品質ルール','Enter a whole number of at least 1.':'1 以上の整数を入力してください。',
 'Enter a bare table name: 1–31 lowercase letters, digits or underscores, starting with a letter. Reserved names are unavailable.':'英小文字で始まる 1〜31 文字の英小文字、数字、アンダースコアでテーブル名を入力してください。予約名は使用できません。',
 'Operation finished. No matching result is verified; check saved metadata.':'操作は終了しましたが、選択と一致する結果は確認できません。保存済みメタデータを確認してください。','Open WAP recovery':'WAP の復旧を開く','History, metadata and recovery':'履歴・メタデータ・復旧','Original operation: {state}. {detail}':'元の操作：{state}。{detail}',
 'Selection alone does not verify a result. Load the matching saved model and authorized metadata.':'選択だけでは結果を確認できません。対応する保存済みモデルと権限のあるメタデータを読み込んでください。',
 'Saved model response did not match this request.':'保存の応答がリクエストと一致しません。',
 'Saved metadata did not match the requested ID.':'保存済みメタデータが指定した ID と一致しません。',
 'Rollback restores the linked publication.':'ロールバックは紐づく公開を復元します。'
});
function dataopsInstallWap(){
 Object.assign(i18n.ja,{'Draft WAP target':'データ変換の下書きの対象','Saved WAP target':'データ変換の保存済みモデルの対象','Saved run details':'保存実行の詳細','Draft Mart target':'Mart 下書きの対象','Not accepted':'未受付'});
 Object.assign(i18n.ja,{'Saved WAP metadata':'保存済みのデータ変換メタデータ','Raw operation result':'操作結果の JSON','Error':'エラー','WAP target and action':'データ変換の対象と操作','Publication replaces the selected target with the audited build. Rollback restores the linked publication.':'公開は選択した対象を監査済み構築結果で置き換えます。ロールバックは紐づく公開を復元します。'});
 const p=dataopsPanel('p35-wap','Transform model',workbenchAtlasHost('transformation-models')),id=p.id;p.sources=[];p.aiRevision=0;
 const context=dataopsNode('div',id+'-context-controls',p.form);context.className='toolbar transformation-context';
 for(const name of ['role','database','schema'])context.append($(id+'-'+name).parentElement);
 context.append($(id+'-refresh'));p.form.prepend(context);
 p.aiBar=dataopsNode('div',id+'-ai-import',p.form);p.aiBar.className='help';p.aiBar.setAttribute('aria-live','polite');
 const aiActions=dataopsNode('div','',p.form);aiActions.className='toolbar transformation-ai-actions';
 const stageButton=dataopsButton(aiActions,'copilot-stage','Load AI draft into the model',()=>{});stageButton.hidden=true;stageButton.disabled=false;
 ui(stageButton,'Load AI draft into the model');stageButton.onclick=()=>{
  if(p.pending||p.trackingPending||p.invalid)return;
  if(p.aiPending)return dataopsAiLoad(p).then(()=>{if(!p.pending)$(id+'-save').focus();});
  if(p.aiOrigin){$(id+'-save').focus();return;}
 };
 dataopsNode('span','',aiActions,'AI draft').className='help';
 aiActions.append(stageButton);aiActions.hidden=true;
 const models=dataopsField(p.form,id+'-models','Saved models','select');
 models.onchange=()=>{
  dataopsChanged(p);const n=$(id+'-models').value;dataopsLoadModel(p,n===''?null:p.models[Number(n)]);editor.open=n==='';
 };
 const editor=workflowDetails(p.form,'Define or edit model');editor.id=id+'-editor';editor.className+=' wap-editor';editor.open=true;
 p.fields.hidden=true;
 const group=label=>{
  dataopsNode('h3','',editor,label);const fields=dataopsNode('div','',editor);fields.className='job-fields';return fields;
 };
 const source=group('Source RAW tables and SQL');
 dataopsField(source,id+'-model-id','Model ID');dataopsField(source,id+'-sql','SQL','textarea').value='SELECT * FROM source';
 dataopsNode('p','',editor,'Choose the input RAW tables and use their aliases in SQL.').className='help';
 dataopsNode('div',id+'-sources',editor);
 dataopsButton(editor,id+'-add-source','Add source',()=>{dataopsAddSource(p);dataopsChanged(p);});dataopsAddSource(p,'source');
 const destination=group('Publish destination');
 dataopsField(destination,id+'-target','Existing output table (optional)','select');
 dataopsField(destination,id+'-target-schema','Output schema','select');
 const name=dataopsField(destination,id+'-name','New output table name');
 const hint=dataopsNode('p',id+'-name-hint',name.parentElement,'Enter only the new table name, for example daily_totals. Select its schema separately.');hint.className='help';
 const quality=group('Quality rules');
 const min=dataopsField(quality,id+'-min','Minimum rows');min.type='number';min.min='1';min.value='1';dataopsField(quality,id+'-not-null','Not-null columns');
 for(const input of [name,min]){
  const error=dataopsNode('p',input.id+'-error',input.parentElement);error.hidden=true;error.className='error';error.setAttribute('aria-live','polite');
  input.setAttribute('aria-describedby',(input===name?hint.id+' ':'')+error.id);
 }
 const modelActions=dataopsNode('div','',p.form);modelActions.className='transformation-actions';
 dataopsButton(modelActions,id+'-save','Save model and create version',async()=>{
  if(!dataopsAllowed(p,'wap_models')||p.pending||p.trackingPending||dataopsWapErrors(p,true))return;
  let saved=false;
  await dataopsTask(p,async current=>{
   const scope=dataopsWapEvidence(p)?.scope;if(!scope)return;
   const payload=dataopsWapPayload(p),reply=await dataopsRequest('/api/v1/wap/models',p.context,payload);
   if(!current()||scope!==dataopsWapEvidence(p)?.scope)return;
   if(reply.model?.model_id!==payload.model_id||reply.model.version!==(payload.expected_version??0)+1)throw Error(t('Saved model response did not match this request.'));
   p.models=[...p.models.filter(m=>m.model_id!==reply.model.model_id),reply.model];
   dataopsLoadModel(p,reply.model,{preserveOrigin:true});dataopsRender(p,true);saved=true;
  });
  if(saved){editor.open=false;const next=$(id+'-build-action');if(!next.disabled)next.focus();else editor.children[0].focus();}
 }).className='primary';
 const guide=dataopsNode('section',id+'-guide',p.form);guide.className='wap-guide';
 const steps=dataopsNode('ol',id+'-steps',guide);steps.className='wap-steps';steps.setAttribute('aria-label',t('Transformation flow'));
 for(let i=0;i<6;i++)dataopsNode('li',id+'-step-'+i,steps);
 const scope=dataopsNode('div','',guide);scope.className='wb-task-context';
 for(const key of ['guide-context','guide-model','action-target'])dataopsNode('p',id+'-'+key,scope);
 const savedVersion=dataopsNode('p',id+'-saved-version',scope);
 const next=dataopsNode('p',id+'-guide-next',guide);next.setAttribute('role','status');next.tabIndex=-1;next.hidden=true;
 dataopsNode('p',id+'-candidate',guide);
 const candidateActions=dataopsNode('div','',guide);candidateActions.className='transformation-actions';
 dataopsButton(candidateActions,id+'-build-action','Build SANDBOX candidate',()=>dataopsWapAction(p,'build'));
 dataopsButton(candidateActions,id+'-audit-action','Run quality checks',()=>dataopsWapAction(p,'audit'));
 dataopsNode('p',id+'-quality-result',guide).setAttribute('role','status');
 dataopsNode('h3','',guide,'Review publication');
 const reviewActions=dataopsNode('div','',guide);reviewActions.className='transformation-actions';
 const review=dataopsButton(reviewActions,'copilot-publish','Preview MARTS publication',()=>{});review.hidden=false;review.disabled=false;
 ui(review,'Preview MARTS publication');review.className='';
 review.onclick=()=>dataopsReviewWap(p);
 reviewActions.append(review);
 dataopsNode('p',id+'-publish-review',guide).setAttribute('role','status');
 const publishActions=dataopsNode('div','',guide);publishActions.className='transformation-actions';
 const publish=dataopsButton(publishActions,id+'-publish','Publish to MARTS',()=>{
  if($(id+'-publish').disabled)return;
  if(!dataopsPublishReviewCurrent(p)){ui(p.status,'Review the publication to publish.');p.status.className='error';p.status.setAttribute('role','alert');return;}
  return dataopsWapAction(p,'publish');
 });
 publish.setAttribute('aria-describedby',id+'-publish-review');
 const publishedRow=dataopsNode('p',id+'-published',guide);publishedRow.hidden=true;
 const openTable=dataopsNode('a',id+'-open-table',publishedRow,'Open published table');openTable.className='primary';openTable.hidden=true;
 dataopsNode('span','',publishedRow,' ');const auditLink=dataopsNode('a',id+'-audit-link',publishedRow);auditLink.hidden=true;
 const reason=dataopsNode('p',id+'-stage-reason',guide);reason.setAttribute('role','status');reason.tabIndex=-1;
 guide.append(p.status);dataopsNode('p',id+'-outcome',guide).setAttribute('role','status');
 const tracking=dataopsNode('div','',guide);tracking.className='toolbar';
 tracking.append($(id+'-observe'),$(id+'-cancel'));guide.append($(id+'-observe-help'));
 $(id+'-accepted-context').hidden=true;
 const history=workflowDetails(p.form,'History, metadata and recovery');history.className+=' wap-history';history.id='p35-wap-history';
 dataopsButton(tracking,id+'-open-recovery','Open transformation recovery',()=>{history.open=true;$(id+'-recovery-select').focus();});
 const selections=dataopsNode('div','',history);selections.className='job-fields';
 for(const [kind,label] of [['build','Build'],['audit','Audit'],['publication','Publication']])dataopsField(selections,id+'-'+kind,label,'select').onchange=()=>dataopsRender(p);
 dataopsNode('p',id+'-history-note',history,'Selection alone does not verify a result. Load the matching saved model and authorized metadata.').className='help';
 const metadata=workflowDetails(history,'Saved transformation metadata');
 dataopsField(metadata,id+'-metadata-id','Metadata ID (optional, from another authorized session)');
 dataopsField(metadata,id+'-metadata-kind','Type','select',[['build','Build'],['audit','Audit'],['publication','Publication']]);
 dataopsButton(metadata,id+'-metadata-load','Load metadata',()=>dataopsTask(p,async current=>{
  const kind=$(id+'-metadata-kind').value,value=$(id+'-metadata-id').value.trim();if(!value)throw Error(t('Choose a saved metadata record.'));
  const scope=dataopsWapEvidence(p)?.scope;
  const reply=await dataopsRequest('/api/v1/wap/'+({build:'builds',audit:'audits',publication:'publications'})[kind]+'/'+encodeURIComponent(value),p.context);
  if(!current()||!scope||scope!==dataopsWapEvidence(p)?.scope)return;
  const record=reply[kind],n=$(id+'-'+kind);
  if(record?.id!==value)throw Error(t('Saved metadata did not match the requested ID.'));
  if(![...n.options].some(o=>o.value===''))n.add(new Option(t('No selection'),''));
  if(![...n.options].some(o=>o.value===value))n.add(new Option(value,value));
  n.value=value;p.metadata=record;dataopsRememberWap(p,kind,value,record,{historical:true});ui(p.status,'Ready');
 }));
 const receipt=workflowDetails(history,'Saved run details');receipt.append(p.receipt);
 // Keep recovery outside the form-wide mutation disable loop.
 p.form.after(history);history.append(p.recovery);
 dataopsNode('p','',history,'Rollback restores the linked publication.').className='help';
 dataopsButton(history,id+'-rollback','Rollback publication (admin)',()=>dataopsWapAction(p,'rollback','wap_rollback'));
 p.stageButton=stageButton;p.aiActions=aiActions;p.reviewButton=review;p.publishButton=publish;
 p.openTable=openTable;p.auditLink=auditLink;p.publishedRow=publishedRow;p.stageReason=reason;p.savedVersion=savedVersion;p.candidateLine=$(id+'-candidate');
 p.stageRows={model:modelActions,candidate:candidateActions,review:reviewActions,publish:publishActions,published:publishedRow};p.guideNext=next;
 for(const event of ['input','change'])p.form.addEventListener(event,()=>dataopsAiRender(p));
 p.form.onsubmit=e=>e.preventDefault();
 for(const key of ['model-id','sql','target-schema','name','target','min','not-null'])$(id+'-'+key).oninput=$(id+'-'+key).onchange=()=>{
  if(['name','min','target'].includes(key)&&['name','min'].some(field=>!$(id+'-'+field+'-error').hidden))dataopsWapErrors(p);
  dataopsChanged(p);
 };
 return p;
}
function dataopsExportAuthorized(op){
 const c=op.context,d=dbRoleItems(c.selected_role).find(d=>d.id===c.database_id);
 return op.principal===dbPrincipal&&dbRoleWithinHeader(c.selected_role)&&!!d&&d.state==='READY'&&d.allowed_roles?.includes(c.selected_role)&&d.binding_revision===c.binding_revision&&d.generation_id===c.generation_id&&(d.grant_revision??null)===c.grant_revision;
}
const dataopsExportSelection=()=>JSON.stringify([dbPrincipal,currentRole,dbSelectionGeneration,dbExplorer.database_id,dbExplorer.schema_id,dataopsRefKey(dataopsRef(dbObject))]);
function dataopsExportSelected(op){return dbExplorerCanOpen()&&currentRole===op.context.selected_role&&dbExplorer.database_id===op.context.database_id&&dbExplorer.schema_id===op.context.schema_id&&dataopsRefKey(dataopsRef(dbObject))===dataopsRefKey(op.source);}
function dataopsExportSelectionCurrent(op){return dataopsExportSelected(op)&&op.selection===dataopsExportSelection();}
function dataopsExportControls(){
 if(!dataopsPanels.size)return;
 const op=dataopsExport;if(op&&!dataopsExportSelectionCurrent(op)&&(op.receipt||op.operation)){op.receipt=null;op.operation=null;op.protected=false;dataopsExportRender();}
 const allowed=dbExplorerCanOpen()&&dbObject?.kind==='TABLE'&&!!dataopsRef(dbObject)&&dbObject.capabilities?.export!==false;
 $('export-table-btn').disabled=!allowed||!!op?.pending||!!op?.trackingPending||dataopsUnresolved(op);$('export-table-format').disabled=!allowed||!!op?.pending||!!op?.trackingPending;
 $('export-table-btn').title=t(dbObject?.kind==='VIEW'?'View export is unsupported. Select a stable readable table.':'Select a stable readable table.');
}
function dataopsExportRender(){
 const area=$('p35-export-receipt');if(!area)return;area.replaceChildren();const op=dataopsExport;dbManageSync();
 // The receipt area appears only once an export was requested.
 if(area.parentElement)area.parentElement.hidden=!op;
 $('p35-export-observe').disabled=!dataopsCanObserve(op)||!!op?.trackingPending;$('p35-export-cancel').disabled=!dataopsCanCancel(op)||!!op?.trackingPending;
 $('p35-export-new').disabled=!op||op.pending||!!op.trackingPending||!dataopsTerminal(op);if(!op)return;
 ui($('p35-export-observe'),op.id?'Look up export operation':'Resend original request');
 if(!op.id&&dataopsCanObserve(op))dataopsNode('p','',area,'Resending uses the original body and key. If the request was never accepted, it may start the operation.');
 const line=(label,value)=>{const n=dataopsNode('p','',area);dataopsNode('strong','',n,label);n.append(': '+String(value??''));};
 line('Operation ID',op.id||'UNKNOWN');line('Idempotency key',op.key);line('Accepted context',[op.context.sql_catalog,op.context.sql_schema,op.context.selected_role,op.table].join(' / '));
 if(dataopsState(op))line('Status',t(dataopsState(op)));
 if(op.invalid){dataopsNode('p','',area,'Access changed. Protected data cleared; refresh context explicitly.');return;}
 if(op.error){const n=dataopsNode('p','',area);n.className='error';n.setAttribute('role','alert');n.textContent=op.error;}
 if(!dataopsExportSelectionCurrent(op))dataopsNode('p','',area,'Reselect the original table and role, then observe to retrieve its receipt.');
 if(dataopsState(op)==='UNKNOWN')dataopsNode('p','',area,'Unknown execution requires manual reconciliation. Observe the saved operation.');
 if(op.operation){line('Committed',op.operation.committed);line('Cleanup error',op.operation.cleanup_error||'');}
 if(op.receipt&&dataopsExportSelectionCurrent(op)){const r=op.receipt;for(const [label,value] of [['Owner',r.owner],['Database',op.context.sql_catalog],['Table',op.table],['Snapshot',r.source?.snapshot_id||r.source?.snapshot],['File format',r.format],['Rows',r.written_count],['Bytes',r.byte_count]])line(label,value);
  dataopsButton(area,'p35-export-download','Download authorized file',dataopsDownload).disabled=op.pending||!!op.trackingPending;
 }
}
async function dataopsExportReceipt(op){
 const exportId=op.operation?.result?.export_id;if(!exportId||op.operation.state!=='FINISHED')return;
 const selection=op.selection,sequence=op.sequence;
 const reply=await dataopsRequest('/api/v1/table-exports/'+encodeURIComponent(exportId),op.context);
 if(dataopsExport!==op||op.invalid||sequence!==op.sequence||selection!==op.selection||!dataopsExportSelectionCurrent(op)||!dataopsExportAuthorized(op))return;op.exportId=exportId;op.receipt=reply.receipt;
}
async function dataopsStartExport(){
 dataopsExportControls();if($('export-table-btn').disabled)return;
 const d=dbDatabases.find(d=>d.id===dbExplorer.database_id),s=dbExplorer.schemas.find(s=>s.id===dbExplorer.schema_id);
 const context=dataopsFreeze({database_id:d.id,schema_id:s.id,warehouse_id:'LOCAL',sql_catalog:d.sql_catalog,sql_schema:s.sql_name,selected_role:currentRole,binding_revision:d.binding_revision,generation_id:d.generation_id,grant_revision:d.grant_revision??null});
 const source=dataopsFreeze(dataopsRef(dbObject)),format=$('export-table-format').value,signature=JSON.stringify([context,source,format]);
 if(dataopsUnresolved(dataopsExport))return;
 const op={signature,context,source,payload:dataopsFreeze({source,format,context}),path:'/api/v1/table-exports',selection:dataopsExportSelection(),table:s.sql_name+'.'+dbObject.physical_name,format,key:crypto.randomUUID(),principal:dbPrincipal,pending:true,invalid:false,sequence:1};dataopsExport=op;dataopsExportRender();dataopsExportControls();const sequence=op.sequence;
 try{const reply=await dataopsRequest(op.path,context,op.payload,op.key);if(sequence!==op.sequence)return;dataopsAccept(op,reply.operation,dataopsExport===op&&!op.invalid&&dataopsExportSelectionCurrent(op)&&dataopsExportAuthorized(op));if(op.protected)await dataopsExportReceipt(op);}
 catch(error){if(dataopsExport===op&&sequence===op.sequence){if(error.observation)dataopsAccept(op,error.observation,!op.invalid&&dataopsExportSelectionCurrent(op)&&dataopsExportAuthorized(op));else if(!op.id&&!dataopsRejectBeforeAdmission(op,error))op.ambiguous=true;
  op.error=error.message+(dataopsTerminal(op)?'':' · '+t('Outcome unknown. Observe with the saved key; do not submit again.'));if(dataopsAuthError(error)){op.invalid=true;op.receipt=null;}}}
 finally{op.pending=false;dataopsExportRender();dataopsExportControls();}
}
async function dataopsObserveExport(cancel=false){
 const op=dataopsExport;if(!op||op.trackingPending||(cancel?!dataopsCanCancel(op):!dataopsCanObserve(op)))return;op.trackingPending=true;const sequence=op.sequence=(op.sequence||0)+1;
 if(!cancel&&dataopsExportSelected(op)&&dataopsExportAuthorized(op)){op.invalid=false;op.selection=dataopsExportSelection();}
 dataopsExportRender();dataopsExportControls();
 try{const reply=op.id?await dataopsRequest('/api/v1/dataops/'+encodeURIComponent(op.id)+(cancel?'/cancel':''),op.context,cancel?{}:undefined):await dataopsRequest(op.path,op.context,op.payload,op.key);
  if(dataopsExport!==op||sequence!==op.sequence)return;dataopsAccept(op,reply.operation,!op.invalid&&dataopsExportSelectionCurrent(op)&&dataopsExportAuthorized(op));op.error='';if(op.protected)await dataopsExportReceipt(op);}
 catch(error){if(dataopsExport!==op||sequence!==op.sequence)return;op.receipt=null;op.error=error.message;if(error.observation)dataopsAccept(op,error.observation,!op.invalid&&dataopsExportSelectionCurrent(op)&&dataopsExportAuthorized(op));if(dataopsAuthError(error)){op.invalid=true;op.operation=null;}}
 finally{op.trackingPending=false;dataopsExportRender();dataopsExportControls();}
}
async function dataopsDownload(){
 const op=dataopsExport;if(!op?.receipt||op.invalid||op.pending||op.trackingPending||!dataopsExportSelectionCurrent(op)||!dataopsExportAuthorized(op))return;const selection=op.selection;op.pending=true;dataopsExportRender();dataopsExportControls();
 try{
  const response=await fetch('/api/v1/table-exports/'+encodeURIComponent(op.exportId)+'/content?context='+encodeURIComponent(JSON.stringify(op.context)),{headers:{'X-Shirokuma-Role':op.context.selected_role}});
  if(!response.ok){const data=await response.json();throw dbError(data,response.status);}
  const blob=await response.blob();if(op!==dataopsExport||op.invalid||selection!==op.selection||!dataopsExportSelectionCurrent(op)||!dataopsExportAuthorized(op))return;
  const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='table-export.'+op.format;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
 }catch(error){op.receipt=null;op.invalid=true;op.error=error.message;}
 finally{op.pending=false;if(!dataopsExportSelectionCurrent(op)){op.receipt=null;op.operation=null;}dataopsExportRender();dataopsExportControls();}
}
function dataopsInstall(){
 if(dataopsPanels.size)return;
 const ingest=dataopsInstallIngest(),wap=dataopsInstallWap();
 // The worksheet keeps one handoff: open the transformation flow. AI staging and
 // publication happen in that flow with the normal save/build/audit/review steps.
 $('open-transformation').onclick=async()=>{
  if(typeof dbProposal!=='undefined'&&dbProposal.wapRecord){dataopsAiSelect();return;}
  await activate($('tab-transformation'));
  showView('transformation-view-models');
  const p=dataopsPanels.get('p35-wap');
  if(p){p.root.setAttribute('tabindex','-1');p.root.focus();}
 };
 for(const p of [ingest,wap]){p.databases=dbRoleItems(p.role);p.schemas=dbSchemasFor({selected_role:p.role,database_id:p.database});dataopsRender(p,true);}
 dataopsOptions($('export-table-format'),[['csv','CSV'],['jsonl','JSONL (one object per line)']],'csv','File format');
 $('export-table-btn').onclick=dataopsStartExport;
 const area=dataopsNode('section','p35-table-export',$('explore-management')||$('database-details'));dataopsNode('h3','',area,'Export receipt');const receipt=dataopsNode('div','p35-export-receipt',area);receipt.setAttribute('aria-live','polite');
 dataopsButton(area,'p35-export-observe','Observe operation',()=>dataopsObserveExport());dataopsButton(area,'p35-export-cancel','Cancel operation',()=>dataopsObserveExport(true));
 dataopsButton(area,'p35-export-new','New export',()=>{if(dataopsExport&&!dataopsExport.pending&&!dataopsExport.trackingPending&&dataopsTerminal(dataopsExport)){dataopsExport=null;dataopsExportRender();dataopsExportControls();$('export-table-btn').focus();}});
 dataopsExportRender();dataopsExportControls();dbCloneInstall();dbDropInstall();dbManageInstall();
}
