'use strict';

// Stages keeps its context, drafts and accepted execution separate from SQL tabs.
const DB_STAGES_CAPABILITY='stage_admin';
const dbStages={installed:false,started:false,epoch:0,request:0,authorizationRequest:0,drafts:new Map(),stages:[],files:[],history:[],receipts:[],objects:[],roots:[],scans:[],scan:null,invalid:true,pending:false,run:null};
Object.assign(i18n.ja,{
 'Database Stages':'データベースステージ','Stage context is independent of worksheets and Explorer.':'ステージのコンテキストはワークシートと探索から独立しています。',
 'Mode':'モード','create':'新規作成','replace':'置換','append':'追記','upsert':'更新・追加','auto':'自動判定',
 'Refresh Stage context':'ステージの接続先を更新',
 'Load latest Stage definition':'最新のステージ定義を読み込む',
 'Refresh Stage files':'ステージのファイル一覧を更新',
 'Refresh Stage history':'ステージの実行履歴を更新',
 'Refresh export receipts':'エクスポート受領記録一覧を更新',
 'Look up export receipt':'エクスポート受領記録を照会',
 'Look up Stage run':'ステージの実行を照会',
 'Look up scan':'スキャンを照会',
 'Scan outcome UNKNOWN. Check the original scan before starting another.':'スキャン結果は UNKNOWN です。次のスキャンを始める前に元の処理を確認してください。',
 'Stage run outcome UNKNOWN. Check the original run before submitting again.':'実行結果は UNKNOWN です。次の送信前に元の処理を確認してください。',
 'Stage context ({state}) · Database {database} · Schema {schema} · Role {role}':'ステージの接続先（{state}） · データベース {database} · スキーマ {schema} · ロール {role}',
 'Stage access verified':'利用可能','Stage context pending':'選択中','Stage access unavailable':'利用不可',
 'Stage selection · {stage}':'選択中のステージ · {stage}',
 'Loaded Stage · {stage}':'読み込み済みステージ · {stage}',
 'Selected Stage {selected} · Loaded Stage {loaded}':'選択中のステージ {selected} · 読み込み済みステージ {loaded}',
 'No Stage loaded':'読み込み済みステージなし',
 'Choose a database and schema for Stages.':'ステージのデータベースとスキーマを選択してください。',
 'Refreshing Stage context…':'ステージの接続先を更新しています…',
 'Wait for the current Stage request to finish.':'現在のステージ要求が完了するまでお待ちください。',
 'Choose an authorized database and schema or refresh the Stage context.':'利用可能なデータベースとスキーマを選び直すか、ステージの接続先を更新してください。',
 'Load the selected Stage or create a new Stage.':'選択したステージを読み込むか、新しいステージを作成してください。',
 'Load a saved Stage or fill in and save a new Stage.':'保存済みステージを読み込むか、新しいステージの項目を入力して保存してください。',
 'Select and load a Stage before continuing.':'続けるにはステージを選んで読み込んでください。',
 'Fill in and save a new Stage.':'新しいステージの項目を入力して保存してください。',
 'Save the Stage changes before previewing files or scanning.':'ファイルのプレビューやスキャンの前にステージの変更を保存してください。',
 'Refresh the Stage file list to choose a file, or start a manual scan.':'ステージのファイル一覧を更新してファイルを選ぶか、手動スキャンを開始してください。',
 'Refresh the Stage file list to choose and preview a file.':'ステージのファイル一覧を更新し、ファイルを選んでプレビューしてください。',
 'Choose a Stage file to preview.':'プレビューするステージファイルを選択してください。',
 'Preview the selected Stage file.':'選択したステージファイルをプレビューしてください。',
 'Review the preview and confirm the import.':'プレビューを確認して取り込みを確定してください。',
 'Check the original Stage operation before starting another.':'新しい処理を始める前に元のステージ処理を確認してください。',
 'Saved Stages':'保存済みステージ','New Stage':'新しいステージ','Load Stage':'ステージを読み込む','Save Stage':'ステージを保存','Refresh Stages':'ステージを更新',
 'Stage name':'ステージ名','Existing directory':'既存のディレクトリ','Approved roots':'許可されたルート','Typed options JSON':'型付きオプション JSON',
 'Destination table name':'作成するテーブル名','Stage files':'ステージのファイル','Refresh files':'ファイルを更新','Preview file':'ファイルをプレビュー','Confirm import':'取込を確定',
 'Snapshot source table':'スナップショットの出力元テーブル','Export filename':'出力ファイル名','Export snapshot':'スナップショットを出力','Stage history':'ステージの履歴','Refresh receipts':'レシートを更新',
 'Stage access unavailable. Refresh an authorized context.':'ステージのアクセスを利用できません。許可されたコンテキストを更新してください。',
 'Stage request failed. Draft retained.':'ステージ要求が失敗しました。下書きは保持されています。','Stage inputs are invalid.':'ステージの入力が無効です。',
 'Stage saved.':'ステージを保存しました。','Save or load the Stage definition before running.':'実行前にステージ定義を保存するか読み込んでください。',
 'Discard this Stage draft and load the current server revision?':'このステージの下書きを破棄して最新リビジョンを読み込みますか？',
 'Preview required after every file or definition change.':'ファイルまたは定義の変更後はプレビューが必要です。',
 'Run outcome UNKNOWN. Inspect the original run before another submission.':'実行結果は UNKNOWN です。次の送信前に元の実行を照会してください。',
 'Stage input rejected before admission. Preview again before submitting.':'ステージの入力は受付前に拒否されました。再度プレビューしてから送信してください。',
 'Run ID unavailable. Retain the original context and key for reconciliation.':'実行 ID を取得できません。照合のため元のコンテキストとキーを保持してください。',
 'Directory is immutable after saving.':'保存後にディレクトリは変更できません。','Complete published export required.':'公開済みの完全な出力が必要です。',
 'Expected rows':'予定行数','Written rows':'出力行数','Publication':'公開状態','Recording':'記録状態','SHA256':'SHA256',
 'Manual scan':'手動スキャン','Refresh scans':'スキャン一覧を更新','Observe scan':'スキャンを照会','Inspect scan':'スキャンの詳細','Scan ID':'スキャン ID','Attempt':'試行',
 'Retry failed item':'失敗した項目を再試行','Scan requires upsert or atomic append.':'スキャンには upsert または atomic append が必要です。',
 'Scan outcome UNKNOWN. Observe the original request.':'スキャン結果は UNKNOWN です。元の要求を照会してください。'
});
for(const key of Object.keys(i18n.ja))i18n.en[key]=key;
const dbStagesFields=['name','directory','mode','format','destination','target','options','export-source','export-file','export-format'];
const dbStagesTerminal=r=>!!r&&['succeeded','skipped','failed','canceled'].includes(r.status);
const dbStagesScanTerminal=r=>!!r&&['succeeded','partial','failed','canceled'].includes(r.status)&&!r.details?.items.some(i=>i.status==='unknown');
const dbStagesBusy=()=>!!(dbStages.run&&!dbStagesTerminal(dbStages.run)||dbStages.scan&&!dbStagesScanTerminal(dbStages.scan));
function dbStagesNavAllowed(){return dbRoleWithinHeader('admin')&&dbRoleItems('admin').some(d=>d.state==='READY'&&d.allowed_roles?.includes('admin')&&d.capabilities?.[DB_STAGES_CAPABILITY]===true);}
const dbStagesKey=(context=dbStages.context,id=dbStages.stage?.id||'new')=>JSON.stringify([dbPrincipal,context,id]);
function dbStagesAllowed(context=dbStages.context,schemas=dbStages.schemas){
 if(!context||dbStages.principal!==dbPrincipal||context.selected_role!=='admin'||!dbRoleWithinHeader('admin'))return false;
 const d=dbRoleItems('admin').find(d=>d.id===context.database_id),s=(schemas||[]).find(s=>s.id===context.schema_id);
 return !!(d&&d.state==='READY'&&d.allowed_roles?.includes('admin')&&d.capabilities?.[DB_STAGES_CAPABILITY]===true&&s?.capabilities?.[DB_STAGES_CAPABILITY]===true&&d.binding_revision===context.binding_revision&&d.generation_id===context.generation_id&&(d.grant_revision??null)===context.grant_revision);
}
function dbStagesValidStage(stage,context,id){return !!stage&&typeof stage.id==='string'&&!!stage.id&&(!id||stage.id===id)&&stage.owner===JSON.parse(dbPrincipal)[0]&&stage.selected_role==='admin'&&context?.selected_role==='admin'&&['database_id','schema_id','warehouse_id','sql_catalog','sql_schema','selected_role'].every(k=>stage.context?.[k]===context[k])&&Number.isInteger(stage.revision)&&stage.revision>0&&typeof stage.definition?.directory==='string';}
function dbStagesOwn(stage=dbStages.stage){return !stage||dbStagesValidStage(stage,dbStages.context,stage.id);}
function dbStagesRefValue(value){try{const ref=dataopsRef({ref:typeof value==='string'?JSON.parse(value):value});return ref?JSON.stringify(ref):value||'';}catch{return value||'';}}
function dbStagesCapture(){const p=dbStages;if(!p.installed||p.invalid||!p.context)return;p.drafts.set(dbStagesKey(),{context:p.context,schemas:dataopsClone(p.schemas),stage:p.stage?dataopsClone(p.stage):null,save:p.save,values:Object.fromEntries(dbStagesFields.map(k=>[k,$('db-stages-'+k).value]))});}
function dbStagesClearPreview(){const p=dbStages;++p.inputSerial;p.preview=null;$('db-stages-preview-detail')?.replaceChildren();const impact=$('db-stages-impact');if(impact){impact.hidden=true;impact.replaceChildren();}const confirm=$('db-stages-confirm');if(confirm){confirm.hidden=true;confirm.className='primary';ui(confirm,'Confirm import');}}
function dbStagesEditor(stage=null){
 const p=dbStages;if(p.stage?.id!==stage?.id){dbStagesClearScans();if(p.definitionDetails)p.definitionDetails.open=!stage;}p.stage=stage;p.save=null;p.receipt=null;const draft=p.drafts.get(dbStagesKey());if(draft){p.stage=draft.stage;p.save=draft.save;}p.selection=p.stage?.id||'';
 const d=p.stage?.definition,options=d?Object.fromEntries(Object.entries(d).filter(([k])=>!['name','directory','mode','format','destination','target'].includes(k))):{};
 const values=draft?.values?{...draft.values}:{name:d?.name||'',directory:d?.directory||'',mode:d?.mode||'create',format:d?.format||'auto',destination:d?.destination?.physical_name||'',target:d?.target?JSON.stringify(dataopsRef({ref:d.target})):'',options:JSON.stringify(options,null,2),'export-source':'','export-file':'','export-format':'csv'};
 // Context invalidation can leave cleared selects in a captured New Stage draft.
 if(!p.stage)for(const [key,allowed,fallback] of [['mode',['create','replace','append','upsert'],'create'],['format',['auto','csv','json','jsonl'],'auto'],['export-format',['csv','jsonl'],'csv']])if(!allowed.includes(values[key]))values[key]=fallback;
 for(const k of dbStagesFields)$('db-stages-'+k).value=['target','export-source'].includes(k)?dbStagesRefValue(values[k]):values[k];p.files=[];p.history=[];p.receipts=[];p.scans=[];dbStagesClearPreview();dbStagesRender();
}
function dbStagesPurge(){
 dbStages.scheduleRefreshing=false;
 const p=dbStages;if(!p.installed)return;++p.epoch;++p.request;p.invalid=true;p.editorKey=null;p.stage=null;p.selection='';p.save=null;p.stages=[];p.files=[];p.objects=[];p.history=[];p.receipts=[];p.roots=[];p.drafts.clear();
 for(const k of dbStagesFields)$('db-stages-'+k).value='';dbStagesClearPreview();
 if(p.run){p.run.details=null;p.run.schemas=[];p.run.purged=true;}
 if(p.scan){p.scan.details=null;p.scan.schemas=[];p.scan.payload=null;p.scan.purged=true;}p.scans=[];
 if(p.principal!==dbPrincipal)p.run=null;
 if(p.principal!==dbPrincipal)p.scan=null;
 ui(p.status,'Stage access unavailable. Refresh an authorized context.');dbStagesRender();
}
function dbStagesValidate(){
 const p=dbStages;if(!p.installed)return;
 for(const [key,d] of p.drafts)if(!dbStagesAllowed(d.context,d.schemas))p.drafts.delete(key);
 if(!p.invalid&&!dbStagesAllowed())dbStagesPurge();
 if(p.run&&!p.run.purged&&(p.run.principal!==dbPrincipal||!dbStagesAllowed(p.run.context,p.run.schemas))){p.run.details=null;p.run.schemas=[];p.run.purged=true;}
 if(p.run?.principal!==dbPrincipal)p.run=null;
 if(p.scan&&!p.scan.purged&&(p.scan.principal!==dbPrincipal||!dbStagesAllowed(p.scan.context,p.scan.schemas))){p.scan.details=null;p.scan.schemas=[];p.scan.payload=null;p.scan.purged=true;p.scans=[];}
 if(p.scan?.principal!==dbPrincipal)p.scan=null;dbStagesRender();
}
async function dbStagesRefreshAuthorization(){
 const p=dbStages;if(!p.started)return;const epoch=p.epoch,request=++p.authorizationRequest,principal=dbPrincipal;
 const current=()=>epoch===p.epoch&&request===p.authorizationRequest&&principal===dbPrincipal;
 try{
  const items=await dbReadInventory('admin');if(!current()||!dbInventoryCurrent(items))return;dbApplyInventory(items,'admin');
  const contexts=[...(p.context?[{context:p.context}]:[]),...p.drafts.values(),...(p.run&&!p.run.purged?[p.run]:[]),...(p.scan&&!p.scan.purged?[p.scan]:[])],cache=new Map();
  for(const entry of contexts){const id=entry.context.database_id;if(!cache.has(id)){const reply=await dbAPI('/api/v1/databases/'+encodeURIComponent(id)+'/schemas','admin');if(!current()||!dbInventoryCurrent(items))return;cache.set(id,reply.items||[]);}entry.schemas=cache.get(id);}
  if(p.context)p.schemas=cache.get(p.context.database_id)||[];dbStagesValidate();
 }catch(error){if(current()&&error.code!=='AUTH_READ_STALE')dbStagesPurge();}
}
function dbStagesOpen(){if(dbStages.installed&&!dbStages.started)return dbStagesRefresh();}
async function dbStagesRefresh(retry=false){
 const p=dbStages;p.started=true;dbStagesCapture();const epoch=++p.epoch,request=++p.request,principal=dbPrincipal,database=p.database;
 const current=()=>epoch===p.epoch&&request===p.request&&principal===dbPrincipal;
 if(retry)await new Promise(resolve=>setTimeout(resolve,0));
 p.scheduleRefreshing=true;p.invalid=true;dbStagesClearPreview();dbStagesRender();
 try{
  const items=await dbReadInventory('admin');if(!current()||!dbInventoryCurrent(items))return;dbApplyInventory(items,'admin');p.databases=items;
  const reply=database?await dbAPI('/api/v1/databases/'+encodeURIComponent(database)+'/schemas','admin'):{items:[]};if(!current()||!dbInventoryCurrent(items))return;p.schemas=reply.items||[];p.principal=principal;
  if(!database||!p.schema){p.context=null;ui(p.status,'Choose an authorized context.');dbStagesRender();return;}
  p.context=dataopsFreeze(dataopsContext(p));p.invalid=!dbStagesAllowed();if(p.invalid){dbStagesPurge();return;}
  const context=p.context,result=await dataopsRequest('/api/v1/stages',context);if(!current()||!dbStagesAllowed())return;
  const tables=[];
  for(const schema of p.schemas.filter(s=>s.capabilities?.browse)){let cursor='';do{
   const page=await dbAPI('/api/v1/schemas/'+encodeURIComponent(schema.id)+'/objects?limit=100'+(cursor?'&cursor='+encodeURIComponent(cursor):''),'admin');if(!current()||!dbStagesAllowed())return;
   tables.push(...(page.items||[]).filter(o=>o.kind==='TABLE'&&dataopsRef(o)&&o.ref.database_id===database&&o.ref.schema_id===schema.id).map(o=>({...o,label:schema.sql_name+'.'+o.physical_name})));cursor=page.next_cursor||'';
  }while(cursor);}
  p.objects=tables;p.stages=result.stages||[];p.roots=result.local_roots||[];
  // Metadata refresh never replaces the editor's saved revision or focus.
  if(!p.editorKey||p.editorKey!==dbStagesKey()){dbStagesEditor(p.stage);p.editorKey=dbStagesKey();ui(p.status,'Loaded');}
  dbStagesRender();
 }catch(error){if(current()){if(error.code==='AUTH_READ_STALE'&&!retry){p.scheduleRefreshing=false;return dbStagesRefresh(true);}if(dataopsAuthError(error))dbStagesPurge();else ui(p.status,'Stage request failed. Draft retained.');}}
 finally{if(current()){p.scheduleRefreshing=false;dbStagesRender();}}
}
function dbStagesDefinition(){
 const p=dbStages,options=JSON.parse($('db-stages-options').value),mode=$('db-stages-mode').value;
 if(!options||Array.isArray(options)||typeof options!=='object'||Object.keys(options).some(k=>!['encoding','delimiter','schema_hints','key_columns','batch_size','atomic_append'].includes(k)))throw Error('options');
 const d={...options,name:$('db-stages-name').value,directory:$('db-stages-directory').value,mode,format:$('db-stages-format').value};
 if(!/^[a-z][a-z0-9_-]{0,47}$/.test(d.name)||!d.directory.startsWith('/')||!['create','replace','append','upsert'].includes(mode)||!['auto','csv','json','jsonl'].includes(d.format))throw Error('definition');
 if(p.stage&&d.directory!==p.stage.definition.directory)throw Error('directory');
 if(mode==='create'){const name=$('db-stages-destination').value;if(!name||name.includes('.'))throw Error('destination');d.destination={database_id:p.context.database_id,schema_id:p.stage?.definition.destination?.schema_id||p.context.schema_id,physical_name:name};}
 else{d.target=JSON.parse($('db-stages-target').value);if(!p.objects.some(o=>dataopsRefKey(dataopsRef(o))===dataopsRefKey(d.target)))throw Error('target');}return d;
}
const dbStagesSelectionMatches=()=>dbStages.selection===(dbStages.stage?.id||'');
function dbStagesMatches(){try{const canonical=v=>JSON.stringify(v,(_,x)=>x&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.keys(x).sort().map(k=>[k,x[k]])):x);return !!dbStages.stage&&dbStagesSelectionMatches()&&canonical(dbStagesDefinition())===canonical(dbStages.stage.definition);}catch{return false;}}
async function dbStagesSave(){
 const p=dbStages;if(p.pending||p.invalid||!dbStagesAllowed()||!dbStagesOwn()||!dbStagesSelectionMatches())return;
 let payload;try{payload={expected_revision:p.stage?.revision||0,definition:dbStagesDefinition()};if(p.stage)payload.stage_id=p.stage.id;}catch{ui(p.status,'Stage inputs are invalid.');return;}
 const signature=JSON.stringify([p.context,payload]);if(p.save?.signature!==signature)p.save={signature,key:crypto.randomUUID(),context:p.context,payload:dataopsFreeze(payload)};
 const saved=p.save,epoch=p.epoch;p.pending=true;dbStagesCapture();dbStagesRender();
 try{const reply=await dataopsRequest('/api/v1/stages',saved.context,saved.payload,saved.key);if(epoch!==p.epoch||!dbStagesAllowed())return;
  const s=reply.stage;if(!dbStagesValidStage(s,p.context,p.stage?.id)||s.revision!==saved.payload.expected_revision+1)throw Error('receipt');
  p.drafts.delete(dbStagesKey());p.stage=s;p.selection=s.id;p.save=null;p.editorKey=dbStagesKey();$('db-stages-directory').value=s.definition.directory;p.stages=p.stages.filter(v=>v.id!==s.id).concat(s);dbStagesClearPreview();dbStagesCapture();ui(p.status,'Stage saved.');
 }catch(error){if(epoch===p.epoch){if(dataopsAuthError(error))dbStagesPurge();else ui(p.status,'Stage request failed. Draft retained.');}}
 finally{p.pending=false;dbStagesRender();}
}
async function dbStagesLoad(latest=false){
 const p=dbStages,id=$('db-stages-list').value;if(!id||p.pending||p.invalid||!dbStagesAllowed())return;dbStagesCapture();const epoch=p.epoch,request=++p.request;
 const current=()=>epoch===p.epoch&&request===p.request&&$('db-stages-list').value===id&&dbStagesAllowed();
 try{
  const listed=p.stages.find(s=>s.id===id),context=dataopsFreeze(dataopsContext(p,listed?.context?.schema_id||p.schema));if(!dbStagesAllowed(context))return;
  if(latest&&!confirm(t('Discard this Stage draft and load the current server revision?')))return;
  const reply=await dataopsRequest('/api/v1/stages/'+encodeURIComponent(id),context);if(!current()||!dbStagesAllowed(context))return;if(!dbStagesValidStage(reply.stage,context,id))throw Error('receipt');
  if(latest){p.drafts.delete(dbStagesKey(context,id));p.stages=p.stages.map(s=>s.id===id?reply.stage:s);}
  p.context=context;p.schema=context.schema_id;dbStagesEditor(reply.stage);p.editorKey=dbStagesKey();ui(p.status,'Loaded');await dbStagesFiles();
 }catch(error){if(current()){if(dataopsAuthError(error))dbStagesPurge();else ui(p.status,'Stage request failed. Draft retained.');}}
}
function dbStagesPath(stage=dbStages.stage){return '/api/v1/stages/'+encodeURIComponent(stage.id);}
async function dbStagesRead(kind){
 const p=dbStages;if(p.pending||p.invalid||!p.stage||!dbStagesAllowed()||!dbStagesOwn())return;
 const epoch=p.epoch,request=++p.request,id=p.stage.id,context=p.context;
 try{const reply=await dataopsRequest(dbStagesPath()+'/'+kind,context);if(epoch!==p.epoch||request!==p.request||p.stage?.id!==id||!dbStagesAllowed())return;
  if(kind==='files'){if(reply.revision!==p.stage.revision){dbStagesClearPreview();throw Error('revision');}const old=p.files.find(f=>f.name===$('db-stages-file').value);p.files=reply.files||[];const fresh=p.files.find(f=>f.name===old?.name);if(!fresh||fresh.version!==old?.version)dbStagesClearPreview();}
  else if(kind==='runs')p.history=reply.runs||[];else p.receipts=reply.receipts||[];dbStagesRender();
 }catch(error){if(epoch===p.epoch){if(dataopsAuthError(error))dbStagesPurge();else ui(p.status,'Stage request failed. Draft retained.');}}
}
const dbStagesFiles=()=>dbStagesRead('files');
async function dbStagesPreview(){
 const p=dbStages;if(p.pending||p.invalid||!dbStagesAllowed()||!dbStagesOwn()||!dbStagesMatches())return;
 const file=p.files.find(f=>f.name===$('db-stages-file').value);if(!file)return;dbStagesClearPreview();const epoch=p.epoch,serial=p.inputSerial,id=p.stage.id,context=p.context,revision=p.stage.revision;p.pending=true;dbStagesRender();
 try{const reply=await dataopsRequest(dbStagesPath()+'/preview',context,{expected_revision:revision,file:file.name,version:file.version},crypto.randomUUID());
  if(epoch!==p.epoch||serial!==p.inputSerial||p.stage?.id!==id||!dbStagesAllowed()||!dbStagesMatches())return;
  if(reply.stage_id!==id||reply.revision!==revision||!reply.confirmation_fingerprint)throw Error('receipt');p.preview=dataopsFreeze({file:file.name,version:file.version,revision,fingerprint:reply.confirmation_fingerprint,preview:reply.preview,impact:dataopsIngestImpactFrom(reply.preview,p.stage?.definition.mode)});
  const area=$('db-stages-preview-detail');area.replaceChildren();const schema=dataopsNode('div','',area),rows=dataopsNode('div','',area);schema.className=rows.className='table-wrap';table(schema,['Column','Type'],(reply.preview.columns||[]).map(c=>[c.name,c.type]));table(rows,(reply.preview.columns||[]).map(c=>c.name),reply.preview.rows||[],false);
 }catch(error){if(epoch===p.epoch){dbStagesClearPreview();if(dataopsAuthError(error))dbStagesPurge();else ui(p.status,'Preview required after every file or definition change.');}}
 finally{p.pending=false;dbStagesRender();}
}
async function dbStagesSubmit(kind){
 const p=dbStages;if(p.pending||p.invalid||!dbStagesAllowed()||!dbStagesOwn()||!dbStagesMatches()||dbStagesBusy())return;
 let payload;
 try{if(kind==='ingest'){const v=p.preview,f=p.files.find(f=>f.name===$('db-stages-file').value);if(!v||!f||v.file!==f.name||v.version!==f.version||v.revision!==p.stage.revision)return;payload={expected_revision:v.revision,file:v.file,version:v.version,expected_preview_fingerprint:v.fingerprint};}
  else{const source=JSON.parse($('db-stages-export-source').value),format=$('db-stages-export-format').value,file=$('db-stages-export-file').value;
   if(!p.objects.some(o=>o.kind==='TABLE'&&dataopsRefKey(dataopsRef(o))===dataopsRefKey(source))||!['csv','jsonl'].includes(format)||!file||file.startsWith('.')||/[\/\\\x00-\x1f]/.test(file))throw Error('inputs');payload={expected_revision:p.stage.revision,source,format,file};}
 }catch{ui(p.status,'Stage inputs are invalid.');return;}
 const context=dataopsFreeze(dataopsClone(p.context)),r={id:null,stage_id:p.stage.id,stage_revision:p.stage.revision,context,key:crypto.randomUUID(),path:dbStagesPath()+(kind==='ingest'?'/imports':'/exports'),principal:dbPrincipal,schemas:dataopsClone(p.schemas),status:'unknown',details:null,purged:false,kind,sequence:0,payload:dataopsFreeze({...payload,context}),accepted:false,ambiguous:false};p.run=r;p.pending=true;dbStagesClearPreview();dbStagesRender();
 // Only explicit Observe can replay the frozen request with its original key.
 try{await dbStagesRunRequest(r,'',r.payload,r.key,true);}finally{p.pending=false;dbStagesRender();}
}
function dbStagesRejectionOnly(data){
 const layers=[data,...(data?.error&&typeof data.error==='object'?[data.error]:[])],details=data?.details||data?.error?.details;
 const exact=d=>!!d&&Object.keys(d).length===2&&d.admission==='NOT_ATTEMPTED'&&typeof d.reason==='string'&&/^[A-Z][A-Z0-9_]{0,95}$/.test(d.reason)&&!dataopsAuthError({code:d.reason})&&!/^(AUTH|FORBIDDEN|IDEMPOTENCY|KEY_|REPLAY)/.test(d.reason);
 return exact(details)&&(!data.error||typeof data.error==='object')&&layers.every((v,index)=>Object.keys(v).every(k=>['code','message','request_id','retryable','details','error'].includes(k))&&(!index||!Object.hasOwn(v,'error'))&&(!v.code||v.code==='STAGE_INPUT_REJECTED')&&(!v.details||exact(v.details)&&v.details.reason===details.reason));
}
async function dbStagesRunRequest(r,suffix='',body,key,fresh=false){
 const p=dbStages,epoch=p.epoch,serial=++r.sequence;
 const freshSubmission=fresh&&serial===1&&!r.id&&!r.accepted&&!r.details&&!r.ambiguous&&body===r.payload&&key===r.key;
 try{const reply=await dataopsRequest(r.id?'/api/v1/stages/'+encodeURIComponent(r.stage_id)+'/runs/'+encodeURIComponent(r.id)+suffix:r.path,r.context,body,key);
  if(p.run!==r||serial!==r.sequence||r.principal!==dbPrincipal)return;const run=reply.run;
  if(r.kind==='ingest'&&!r.id&&run&&Object.keys(run).length===5&&['id','status','committed','deduplicated','context'].every(k=>Object.hasOwn(run,k))&&run.id===null&&run.status==='skipped'&&run.committed===true&&run.deduplicated===true&&dataopsSameContext(run.context,r.context)){
   r.accepted=true;r.payload=null;r.path=null;r.status='skipped';r.operation_id=null;r.details=null;if(epoch===p.epoch&&!r.purged)ui(p.status,'skipped');return;
  }
  if(!run?.id||(r.id&&run.id!==r.id)||run.stage_id!==r.stage_id||run.owner!==JSON.parse(r.principal)[0]||!['queued','running','succeeded','failed','canceled','unknown'].includes(run.status))throw Error('receipt');
  const minimalCancel=suffix==='/cancel'&&!!r.id&&Object.keys(run).length===5&&['id','stage_id','owner','status','operation_id'].every(k=>Object.hasOwn(run,k));
  if(!minimalCancel&&(run.stage_revision!==r.stage_revision||!dataopsSameContext(run.context,r.context)))throw Error('receipt');
  r.id=run.id;r.accepted=true;r.payload=null;r.path=null;r.status=run.status;r.operation_id=run.operation_id;r.details=!minimalCancel&&!r.purged&&epoch===p.epoch&&dbStagesAllowed(r.context,r.schemas)?run:null;
  if(epoch===p.epoch)ui(p.status,run.status==='unknown'?'Stage run outcome UNKNOWN. Check the original run before submitting again.':run.status);
 }catch(error){if(p.run!==r||serial!==r.sequence)return;
  if(freshSubmission&&!r.id&&!r.accepted&&!r.details&&!r.ambiguous&&!r.purged&&epoch===p.epoch&&r.principal===dbPrincipal&&dbStagesAllowed(r.context,r.schemas)&&[400,409].includes(error.status)&&error.code==='STAGE_INPUT_REJECTED'&&error.stageRejectionOnly&&!error.observation){p.run=null;dbStagesClearPreview();ui(p.status,'Stage input rejected before admission. Preview again before submitting.');return;}
  r.ambiguous=true;if(dataopsAuthError(error))dbStagesPurge();else if(epoch===p.epoch)ui(p.status,'Stage run outcome UNKNOWN. Check the original run before submitting again.');}
}
async function dbStagesObserve(cancel=false){
 const p=dbStages,r=p.run;if(!r||(!r.id&&!r.payload)||p.pending||r.principal!==dbPrincipal||(cancel?!r.id||dbStagesTerminal(r):r.purged||!dbStagesAllowed(r.context,r.schemas)))return;
 p.pending=true;dbStagesRender();try{await dbStagesRunRequest(r,r.id?(cancel?'/cancel':''):(r.kind==='ingest'?'/imports':'/exports'),r.id?(cancel?{}:undefined):r.payload,r.id?(cancel?crypto.randomUUID():undefined):r.key);}finally{p.pending=false;dbStagesRender();}
}
function dbStagesValidScan(scan,r){
 if(!scan||typeof scan.id!=='string'||!scan.id||scan.stage_id!==r.stage_id||scan.owner!==JSON.parse(r.principal)[0]||!dataopsSameContext(scan.context,r.context)||!Number.isInteger(scan.stage_revision)||scan.stage_revision<1||typeof scan.committed!=='boolean'||!['queued','running','succeeded','partial','failed','unknown','canceled'].includes(scan.status)||!Array.isArray(scan.items))return false;
 const ids=new Set();return scan.items.every(item=>{if(!item||typeof item.id!=='string'||!item.id||ids.has(item.id)||typeof item.file!=='string'||!item.file||typeof item.input_fingerprint!=='string'||!item.input_fingerprint||!Number.isInteger(item.attempt)||item.attempt<0||typeof item.committed!=='boolean'||typeof item.retryable!=='boolean'||!['pending','running','succeeded','skipped','failed','unknown','not_started','canceled'].includes(item.status))return false;ids.add(item.id);return true;});
}
function dbStagesScanAssociated(r){const p=dbStages;return !!r&&!!p.stage&&r.principal===dbPrincipal&&dataopsSameContext(r.context,p.context)&&r.stage_id===p.stage.id&&p.selection===p.stage.id&&dbStagesOwn()&&dbStagesAllowed();}
function dbStagesClearScans(){const p=dbStages;if(p.scan)p.scan.details=null;p.scans=[];}
function dbStagesCanRetry(r,item){return !dbStages.invalid&&dbStagesScanAssociated(r)&&dbStagesMatches()&&!r.purged&&dbStagesScanTerminal(r)&&!r.details?.items.some(i=>i.status==='unknown')&&item.retryable===true&&item.status==='failed'&&item.committed===false&&Number.isInteger(item.attempt)&&item.attempt>0;}
async function dbStagesScanStart(item=null){
 const p=dbStages;if(p.pending||p.invalid||!dbStagesAllowed()||!dbStagesOwn()||!dbStagesMatches()||dbStagesBusy())return;
 const d=p.stage.definition;if(d.mode!=='upsert'&&!(d.mode==='append'&&d.atomic_append===true)){ui(p.status,'Scan requires upsert or atomic append.');return;}
 const source=p.scan;if(item&&(!source?.id||!dataopsSameContext(source.context,p.context)||source.stage_id!==p.stage.id||!source.details?.items.includes(item)||!dbStagesCanRetry(source,item)))return;
 const context=dataopsFreeze(dataopsClone(p.context)),payload={context,expected_revision:p.stage.revision};
 if(item)Object.assign(payload,{item_id:item.id,expected_attempt:item.attempt});
 const r={id:null,stage_id:p.stage.id,stage_revision:p.stage.revision,context,principal:dbPrincipal,schemas:dataopsClone(p.schemas),key:crypto.randomUUID(),path:dbStagesPath()+'/scans'+(item?'/'+encodeURIComponent(source.id)+'/retry':''),payload:dataopsFreeze(payload),source_scan_id:item?source.id:null,source_item_id:item?item.id:null,status:'unknown',details:null,purged:false,sequence:0};
 p.scan=r;p.pending=true;dbStagesRender();try{await dbStagesScanRequest(r);}finally{p.pending=false;dbStagesRender();}
}
async function dbStagesScanRequest(r){
 if(dbStages.invalid||!dbStagesScanAssociated(r))return;
 const p=dbStages,epoch=p.epoch,request=p.request,stage=p.stage,context=p.context,payload=r.payload,serial=++r.sequence;
 const current=()=>epoch===p.epoch&&request===p.request&&p.stage===stage&&dataopsSameContext(p.context,context)&&!p.invalid&&!r.purged&&dbStagesScanAssociated(r);
 try{
  const reply=await dataopsRequest(r.id?'/api/v1/stages/'+encodeURIComponent(r.stage_id)+'/scans/'+encodeURIComponent(r.id):r.path,r.context,r.id?undefined:r.payload,r.id?undefined:r.key);
  if(p.scan!==r||serial!==r.sequence||r.principal!==dbPrincipal)return;const scan=reply.scan;
  if(!dbStagesValidScan(scan,r)||(r.id&&scan.id!==r.id)||scan.stage_revision!==r.stage_revision)throw Error('receipt');
  if(!r.id&&r.source_scan_id&&(scan.id===r.source_scan_id||scan.source_scan_id!==r.source_scan_id||scan.source_item_id!==r.source_item_id||scan.items.length!==1||scan.items[0].attempt!==payload.expected_attempt+1))throw Error('retry receipt');
  // Keep only a safe accepted handle if authorization or the selected Stage changed.
  r.id=scan.id;r.payload=null;r.path=null;r.status=scan.status;r.details=current()?dataopsFreeze(dataopsClone(scan)):null;
  if(current())ui(p.status,scan.status);
 }catch(error){if(p.scan!==r||serial!==r.sequence||r.principal!==dbPrincipal)return;r.status='unknown';if(dataopsAuthError(error))dbStagesPurge();else if(current())ui(p.status,'Scan outcome UNKNOWN. Check the original scan before starting another.');}
}
async function dbStagesScanObserve(){
 const p=dbStages,r=p.scan;
 if(p.pending||p.invalid||!dbStagesScanAssociated(r)||r.purged&&!r.id||(!r.id&&!r.payload))return;
 r.schemas=dataopsClone(p.schemas);r.purged=false;
 p.pending=true;dbStagesRender();try{await dbStagesScanRequest(r);}finally{p.pending=false;dbStagesRender();}
}
async function dbStagesScans(){
 const p=dbStages;if(p.pending||p.invalid||!p.stage||p.selection!==p.stage.id||!dbStagesOwn()||!dbStagesAllowed())return;
 const epoch=p.epoch,request=++p.request,stage=p.stage,context=p.context,principal=dbPrincipal;
 const current=()=>epoch===p.epoch&&request===p.request&&principal===dbPrincipal&&p.stage===stage&&dataopsSameContext(p.context,context)&&!p.invalid&&dbStagesAllowed();
 try{const reply=await dataopsRequest(dbStagesPath()+'/scans',context);if(!current())return;
  const r={stage_id:stage.id,context,principal};if(!Array.isArray(reply.scans)||!reply.scans.every(s=>dbStagesValidScan(s,r)))throw Error('receipt');p.scans=reply.scans;dbStagesRender();
 }catch(error){if(current()){if(dataopsAuthError(error))dbStagesPurge();else ui(p.status,'Stage request failed. Draft retained.');}}
}
function dbStagesScanInspect(scan){
 const p=dbStages;if(p.pending||p.invalid||dbStagesBusy()||!dbStagesScanAssociated({stage_id:scan?.stage_id,context:scan?.context,principal:dbPrincipal})||!dbStagesValidScan(scan,{stage_id:p.stage?.id,context:p.context,principal:dbPrincipal}))return;
 p.scan={id:scan.id,stage_id:scan.stage_id,stage_revision:scan.stage_revision,context:dataopsFreeze(dataopsClone(p.context)),principal:dbPrincipal,schemas:dataopsClone(p.schemas),status:scan.status,details:null,purged:false,sequence:0};return dbStagesScanObserve();
}
function dbStagesRenderScans(ready,waiting){
 const p=dbStages,r=p.scan,allowed=!p.invalid&&dbStagesAllowed()&&dbStagesOwn(),d=p.stage?.definition;
 const associated=dbStagesScanAssociated(r);if(r&&!associated)r.details=null;
 p.scans=(dbStagesAllowed()&&dbStagesOwn()&&p.stage&&p.selection===p.stage.id?p.scans:[]).filter(scan=>dbStagesValidScan(scan,{stage_id:p.stage.id,context:p.context,principal:dbPrincipal}));
 $('db-stages-scan').disabled=waiting||!ready||dbStagesBusy()||!(d?.mode==='upsert'||d?.mode==='append'&&d.atomic_append===true);
 $('db-stages-scans').disabled=waiting||!allowed||!p.stage||p.selection!==p.stage.id;
 $('db-stages-scan-observe').disabled=waiting||!allowed||!associated||r.purged&&!r.id||(!r.id&&!r.payload);
 const observe=$('db-stages-scan-observe'),resend=associated&&allowed&&!r.id&&!!r.payload&&!r.purged;
 if(resend)observe.setAttribute('aria-describedby','db-stages-scan-resend');else observe.removeAttribute('aria-describedby');
 ui(observe,resend?'Resend original request':'Look up scan');
 $('db-stages-scan-resend').hidden=waiting||!resend;
 const area=$('db-stages-scan-detail');area.replaceChildren();
 const field=(row,label,value,translated=false)=>{const part=dataopsNode('span','',row);dataopsNode('strong','',part,label);dataopsNode('span','',part).textContent=': ';const text=dataopsNode('span','',part);if(translated)ui(text,value);else text.textContent=String(value);dataopsNode('span','',part).textContent=' · ';};
 if(r){const header=dataopsNode('p','',area);field(header,'Scan ID',r.id||'UNKNOWN');field(header,'Status',r.status,true);if(r.key)field(header,'Idempotency key',r.key);
  if(allowed&&associated&&!r.purged&&r.details){for(const item of r.details.items){const row=dataopsNode('div','',area),report=dataopsNode('p','',row);dataopsNode('span','',report).textContent=item.file+' · ';field(report,'Status',item.status,true);field(report,'Attempt',item.attempt);field(report,'Committed',item.committed);
   if(item.run_id)field(report,'Run ID',item.run_id);if(item.operation_id)field(report,'Operation ID',item.operation_id);if(/^[A-Z][A-Z0-9_]{0,95}$/.test(item.error_code||''))dataopsNode('span','',report).textContent=item.error_code;
   if(dbStagesCanRetry(r,item)){const b=dataopsButton(row,'','Retry failed item',()=>dbStagesScanStart(item));b.disabled=waiting||!ready||dbStagesBusy()||r.stage_id!==p.stage?.id||!dataopsSameContext(r.context,p.context);}
  }}
 }
 const history=$('db-stages-scan-list');history.replaceChildren();if(allowed)for(const scan of p.scans||[]){const row=dataopsNode('div','',history);dataopsNode('p','',row).textContent=[scan.id,t(scan.status)].join(' · ');dataopsButton(row,'','Look up scan',()=>dbStagesScanInspect(scan)).disabled=waiting||dbStagesBusy();}
}
function dbStagesReceiptMatches(r,stage=dbStages.stage,context=dbStages.context,id=$('db-stages-receipt').value){return !!r&&!!stage&&r.export_id===id&&r.stage_id===stage.id&&Number.isInteger(r.stage_revision)&&r.stage_revision>0&&r.stage_revision<=stage.revision&&typeof r.run_id==='string'&&!!r.run_id&&typeof r.file==='string'&&!!r.file&&!r.file.startsWith('.')&&!/[\/\\\x00-\x1f]/.test(r.file)&&r.owner===JSON.parse(dbPrincipal)[0]&&dataopsSameContext(r.context,context)&&r.source?.ref?.database_id===context.database_id;}
function dbStagesDownloadable(r){return dbStagesReceiptMatches(r)&&r.publication_state==='published'&&['csv','jsonl'].includes(r.format)&&Number.isInteger(r.expected_count)&&r.expected_count>=0&&r.written_count===r.expected_count&&Number.isInteger(r.byte_count)&&r.byte_count>=0&&/^[a-f0-9]{64}$/.test(r.sha256||'')&&r.complete!==false;}
async function dbStagesReceipt(download=false){
 const p=dbStages,id=$('db-stages-receipt').value;if(p.pending||p.invalid||!p.stage||!id||!dbStagesAllowed()||!dbStagesOwn())return;
 const epoch=p.epoch,request=++p.request,stage=p.stage,context=p.context,current=()=>epoch===p.epoch&&request===p.request&&p.stage===stage&&dbStagesAllowed();p.pending=true;dbStagesRender();
 try{const path=dbStagesPath()+'/exports/'+encodeURIComponent(id),reply=await dataopsRequest(path,context);if(!current())return;const receipt=reply.receipt;
  if(reply.stage_id!==stage.id||!dbStagesReceiptMatches(receipt,stage,context,id))throw Error('receipt');p.receipt=receipt;dbStagesRenderReceipt(receipt);
  if(download){if(!dbStagesDownloadable(receipt))throw Error('incomplete');const response=await fetch(path+'/content?context='+encodeURIComponent(JSON.stringify(context)),{headers:{'X-Shirokuma-Role':context.selected_role}});if(!response.ok){const data=await response.json();throw dbError(data,response.status);}const bytes=await response.arrayBuffer();if(!current())return;
   const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');if(!current())return;if(bytes.byteLength!==receipt.byte_count||hash!==receipt.sha256)throw Error('integrity');
   const url=URL.createObjectURL(new Blob([bytes])),link=document.createElement('a');link.href=url;link.download='stage-export.'+receipt.format;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
 }catch(error){if(current()){p.receipt=null;$('db-stages-receipt-detail').replaceChildren();if(dataopsAuthError(error))dbStagesPurge();else ui(p.status,'Complete published export required.');}}
 finally{p.pending=false;dbStagesRender();}
}
function dbStagesRenderReceipt(receipt){const area=$('db-stages-receipt-detail');area.replaceChildren();for(const [label,value] of [['Export ID',receipt.export_id],['Snapshot',receipt.source?.snapshot_id??receipt.source?.snapshot],['Expected rows',receipt.expected_count],['Written rows',receipt.written_count],['Bytes',receipt.byte_count],['SHA256',receipt.sha256],['Publication',receipt.publication_state]]){const row=dataopsNode('p','',area);dataopsNode('strong','',row,label);dataopsNode('span','',row).textContent=': '+String(value??'');}}
function dbStagesRenderImpact(){
 const p=dbStages,node=$('db-stages-impact'),confirm=$('db-stages-confirm');if(!node||!confirm)return;
 const impact=p.preview?.impact;
 if(!impact){node.hidden=true;node.replaceChildren();confirm.hidden=true;confirm.className='primary';ui(confirm,'Confirm import');return;}
 node.hidden=false;confirm.hidden=false;node.className='impact'+(impact.mode==='create'?' neutral':'');
 const incoming=impact.incoming_rows??p.preview?.preview?.total_rows??0,existing=impact.existing_rows,after=impact.after_rows;
 let key,values;
 if(impact.mode==='create'){key='Create a new table and write {incoming} rows.';values={incoming};}
 else if(impact.mode==='append'&&existing!=null){key='Add {incoming} rows to the existing {existing} rows (total {after}).';values={incoming,existing,after};}
 else if(impact.mode==='append'){key='Add {incoming} rows. The existing row count is unavailable.';values={incoming};}
 else if(impact.mode==='replace'&&existing!=null){key='Delete all {existing} existing rows and replace them with {incoming} rows.';values={incoming,existing};}
 else if(impact.mode==='replace'){key='Replace the table with {incoming} rows. The existing row count is unavailable.';values={incoming};}
 else if(impact.mode==='upsert'&&existing!=null){key='Update or add {incoming} rows by key (existing {existing} rows).';values={incoming,existing};}
 else {key='Update or add {incoming} rows by key. The existing row count is unavailable.';values={incoming};}
 node.replaceChildren();const line=document.createElement('strong');ui(line,key,values);node.append(line);
 let label,labelValues;
 if(impact.mode==='create'){label='Create and ingest';labelValues={};}
 else if(impact.mode==='append'){label='Append {incoming} rows';labelValues={incoming};}
 else if(impact.mode==='replace'&&existing!=null){label='Delete {existing} rows and replace';labelValues={existing};}
 else if(impact.mode==='replace'){label='Delete all rows and replace';labelValues={};}
 else {label='Update or add {incoming} rows';labelValues={incoming};}
 confirm.className='primary'+(impact.mode==='replace'||impact.mode==='upsert'?' danger':'');
 ui(confirm,label,labelValues);
}
function dbStagesRender(){
 dbScheduleSync('stage');
 const p=dbStages;if(!p.installed)return;const allowed=!p.invalid&&dbStagesAllowed(),own=dbStagesOwn(),waiting=p.pending,ready=allowed&&own&&dbStagesMatches(),unresolved=dbStagesBusy();
 workbenchAtlasStageTabState();
 dataopsOptions($('db-stages-role'),dbRoleWithinHeader('admin')?[['admin',dbRoleDisplayNames.admin]]:[],p.role,'Execution role');
 dataopsOptions($('db-stages-database'),dbReadyDatabases('admin').map(d=>[d.id,dbDatabaseLabel(d)]),p.database,'Choose database');
 dataopsOptions($('db-stages-schema'),(p.schemas||[]).map(s=>[s.id,dbSchemaLabel(s)]),p.schema,'Choose schema');
 dataopsOptions($('db-stages-list'),(allowed?p.stages:[]).map(s=>[s.id,s.definition.name+' · v'+s.revision]),p.selection,'Choose stage');
 const objects=(allowed?p.objects:[]).map(o=>[JSON.stringify(dataopsRef(o)),o.label||o.display_name||o.physical_name]);
 for(const key of ['target','export-source'])dataopsOptions($('db-stages-'+key),objects,dbStagesRefValue($('db-stages-'+key).value),'Select a stable readable table.');
 dataopsOptions($('db-stages-file'),(allowed?p.files:[]).map(f=>[f.name,f.name+' · '+f.size+' B · '+(f.modified_at?fmtJST(f.modified_at):'—')]),$('db-stages-file').value,'Stage files');
 dataopsOptions($('db-stages-receipt'),(allowed?p.receipts:[]).map(r=>[r.export_id,r.export_id]),$('db-stages-receipt').value,'Export receipt');
 for(const k of dbStagesFields)$('db-stages-'+k).disabled=waiting||!allowed||!own;
 $('db-stages-directory').disabled=waiting||!allowed||!own||!!p.stage;
 for(const key of ['role','database','schema','refresh','list','load','reload','new'])$('db-stages-'+key).disabled=waiting;
 $('db-stages-save').disabled=waiting||!allowed||!own||!dbStagesSelectionMatches();
 $('db-stages-save').className=p.preview?'':'primary';
 for(const key of ['files','history','receipts','receipt','inspect-receipt'])$('db-stages-'+key).disabled=waiting||!allowed||!own||!p.stage;
 $('db-stages-file').disabled=waiting||!ready;
 $('db-stages-preview').disabled=waiting||!ready||!$('db-stages-file').value;
 $('db-stages-confirm').disabled=waiting||!ready||!p.preview||!!unresolved;
 $('db-stages-export').disabled=waiting||!ready||!!unresolved||!$('db-stages-export-source').value;
 $('db-stages-download').disabled=waiting||!allowed||!p.receipt||!dbStagesDownloadable(p.receipt)||p.receipt.export_id!==$('db-stages-receipt').value;
 const r=p.run;$('db-stages-observe').disabled=waiting||!r||(!r.id&&!r.payload)||r.purged||!dbStagesAllowed(r.context,r.schemas);$('db-stages-cancel').disabled=waiting||!r?.id||r.principal!==dbPrincipal||dbStagesTerminal(r);
 const observe=$('db-stages-observe'),resend=!!r?.payload&&!r.id&&!r.purged&&dbStagesAllowed(r.context,r.schemas);
 if(resend)observe.setAttribute('aria-describedby','db-stages-run-resend');else observe.removeAttribute('aria-describedby');
 ui(observe,resend?'Resend original request':'Look up Stage run');
 $('db-stages-run-resend').hidden=waiting||!resend;
 const usable=allowed&&own;
 const db=p.databases.find(d=>d.id===p.database),schema=p.schemas.find(s=>s.id===p.schema);
 const context=usable?p.context:null,chosen=p.database&&p.schema;
 wbTaskText($('db-stages-context'),'Stage context ({state}) · Database {database} · Schema {schema} · Role {role}',{
  state:t(usable?'Stage access verified':chosen&&p.started&&!p.scheduleRefreshing?'Stage access unavailable':'Stage context pending'),
  database:context?.sql_catalog||db?.display_name||db?.sql_catalog||t(p.database?'Unavailable':'No selection'),
  schema:context?.sql_schema||schema?.display_name||schema?.sql_name||t(p.schema?'Unavailable':'No selection'),
  role:p.role?dbRoleDisplayLabel(p.role):'—',
 });
 const selected=usable&&p.stages.find(stage=>stage.id===p.selection),loaded=usable&&p.stage;
 const stageLabel=stage=>stage.definition.name+' · v'+stage.revision;
 const selection=$('db-stages-current');
 if(selected&&loaded&&selected.id!==loaded.id)wbTaskText(selection,'Selected Stage {selected} · Loaded Stage {loaded}',{selected:stageLabel(selected),loaded:stageLabel(loaded)});
 else if(selected&&!loaded)wbTaskText(selection,'Stage selection · {stage}',{stage:stageLabel(selected)});
 else if(loaded)wbTaskText(selection,'Loaded Stage · {stage}',{stage:stageLabel(loaded)});
 else wbTaskText(selection,'No Stage loaded');
 const latestRun=p.run?.created_at?p.run:(p.history||[]).find(run=>run?.created_at)||null;
 wbTaskText($('db-stages-latest'),latestRun?'Latest run: {time} · {status}':'Not run yet',
  {time:latestRun?fmtJST(latestRun.created_at):'',status:latestRun?t(latestRun.status||'Unknown'):''});
 let next='Refresh the Stage file list to choose and preview a file.';
 if(p.scheduleRefreshing)next='Refreshing Stage context…';
 else if(waiting)next='Wait for the current Stage request to finish.';
 else if(!chosen)next='Choose a database and schema for Stages.';
 else if(!usable)next='Choose an authorized database and schema or refresh the Stage context.';
 else if(selected&&selected.id!==loaded?.id)next='Load the selected Stage or create a new Stage.';
 else if(!dbStagesSelectionMatches())next='Select and load a Stage before continuing.';
 else if(!loaded)next=p.stages.length?'Load a saved Stage or fill in and save a new Stage.':'Fill in and save a new Stage.';
 else if(unresolved)next='Check the original Stage operation before starting another.';
 else if(!ready)next='Save the Stage changes before previewing files or scanning.';
 else if(p.preview)next='Review the preview and confirm the import.';
 else if($('db-stages-file').value)next='Preview the selected Stage file.';
 else if(p.files.length)next='Choose a Stage file to preview.';
 else if(loaded.definition.mode==='upsert'||loaded.definition.mode==='append'&&loaded.definition.atomic_append===true)next='Refresh the Stage file list to choose a file, or start a manual scan.';
 wbTaskText($('db-stages-next'),next);
 $('db-stages-roots').textContent=allowed?p.roots.map(r=>typeof r==='string'?r:JSON.stringify(r)).join('\n'):'';
 $('db-stages-owner').textContent=allowed&&p.stage?[t('Owner')+': '+p.stage.owner,t('Saved role')+': '+p.stage.selected_role,t('Revision')+': '+p.stage.revision].join(' · '):'';
 const area=$('db-stages-run-detail');area.replaceChildren();if(r){for(const [label,value] of [...(r.status==='skipped'?[]:[['Run ID',r.id||'UNKNOWN']]),['Idempotency key',r.key],['Status',t(r.status)],['Execution context',[r.context.sql_catalog,r.context.sql_schema,r.context.selected_role,'v'+r.stage_revision].join(' / ')]]){const row=dataopsNode('p','',area);dataopsNode('strong','',row,label);dataopsNode('span','',row).textContent=': '+String(value??'');}
  if(r.purged)dataopsNode('p','',area,'Run details cleared after access changed. Original cancellation handle retained.');
  else if(r.details)for(const [label,value] of [['Operation ID',r.details.operation_id],['Committed',r.details.committed],['Publication',r.details.publication_state],['Summary',JSON.stringify(r.details.summary??{})]]){const row=dataopsNode('p','',area);dataopsNode('strong','',row,label);dataopsNode('span','',row).textContent=': '+String(value??'');}
  if(!r.id&&r.status!=='skipped')dataopsNode('p','',area,'Run ID unavailable. Retain the original context and key for reconciliation.');
 }
 const history=$('db-stages-history-list');history.replaceChildren();if(allowed)for(const run of p.history){const row=dataopsNode('div','',history);dataopsNode('p','',row).textContent=[run.id,t(run.status),run.kind,run.file].join(' · ');dataopsButton(row,'','Look up Stage run',()=>{if(p.pending||(p.run&&!dbStagesTerminal(p.run)&&p.run.id!==run.id)||!dataopsSameContext(run.context,p.context))return;p.run={id:run.id,stage_id:run.stage_id,stage_revision:run.stage_revision,context:dataopsFreeze(dataopsClone(run.context)),principal:dbPrincipal,schemas:dataopsClone(p.schemas),status:run.status,sequence:0,purged:false};return dbStagesObserve();});}
 if(!allowed||!p.receipt){p.receipt=null;$('db-stages-receipt-detail').replaceChildren();}
 dbStagesRenderImpact();
 dbStagesRenderScans(ready,waiting);
 const def=p.stage?.definition,target=$('db-stages-output');
 if(target){
  const schema=p.schemas.find(s=>s.id===(def?.destination?.schema_id||def?.target?.schema_id))?.sql_name;
  const name=def?.destination?.physical_name||def?.target?.physical_name||(def?.target?p.objects.find(o=>dataopsRefKey(dataopsRef(o))===dataopsRefKey(def.target))?.physical_name:'');
  target.textContent=schema&&name?[p.context?.sql_catalog||'',schema,name].filter(Boolean).join('.'):t('No selection');
 }
 const title=$('db-stages-stage-title');if(title)ui(title,p.stage?p.stage.definition.name:'No stage selected');
 const schedule=$('db-schedule-stage-panel'),slot=$('db-stages-schedule-slot');
 if(schedule&&slot&&schedule.parentElement!==slot)slot.append(schedule);
 if(workbenchAtlasIngestionView()==='stages')workbenchAtlasMessage('ingestion',p.status?.textContent?.trim()||'');
 else workbenchAtlasSyncIngestion();
 if(typeof workbenchRenderWorkspaceNav==='function')workbenchRenderWorkspaceNav();
}
function dbStagesInstall(){
 const p=dbStages;if(p.installed)return;p.installed=true;p.inputSerial=0;p.principal=dbPrincipal;p.role='admin';p.database=dbCurrent()?.database_id||'';p.schema=dbCurrent()?.schema_id||'';p.databases=dbRoleItems('admin');p.schemas=[];
 for(const child of $('ingestion-stages').children)child.hidden=true;
 const root=dataopsNode('section','db-stages-panel',workbenchAtlasHost('ingestion-stages'));root.className='db-stages-panel atlas-section';const body=dataopsNode('div','',root);body.className='atlas-section-body';dataopsNode('h2','db-stages-heading',body,'Saved stages');const stageNote=dataopsNode('p','db-stages-note',body,'A stage registers a folder on the Workbench host. Automatic ingestion starts disabled.');stageNote.className='tiny muted';const context=dataopsNode('div','',body);context.className='wb-task-context';context.setAttribute('aria-live','polite');
 for(const id of ['context','current','latest','next'])dataopsNode('p','db-stages-'+id,context);
 const selectors=dataopsNode('div','',body);selectors.className='toolbar';for(const [key,label] of [['role','Execution role'],['database','Database'],['schema','Schema']]){const node=dataopsField(selectors,'db-stages-'+key,label,'select');node.onchange=()=>{dbStagesCapture();++p.request;dbStagesClearPreview();p[key]=node.value;if(key==='database')p.schema='';p.stage=null;p.selection='';p.editorKey=null;p.save=null;p.context=null;p.invalid=true;p.receipt=null;p.history=[];p.receipts=[];p.files=[];for(const k of dbStagesFields)$('db-stages-'+k).value='';return dbStagesRefresh();};}
 dataopsButton(selectors,'db-stages-refresh','Refresh stage',dbStagesRefresh);
 const saved=dataopsNode('div','',body);saved.className='toolbar';dataopsField(saved,'db-stages-list','Stage','select').onchange=()=>{p.selection=$('db-stages-list').value;++p.request;dbStagesClearScans();dbStagesRender();};dataopsButton(saved,'db-stages-load','Load Stage',()=>dbStagesLoad());dataopsButton(saved,'db-stages-reload','Load latest Stage definition',()=>dbStagesLoad(true));dataopsButton(saved,'db-stages-new','New Stage',()=>{if(p.pending||p.invalid)return;dbStagesCapture();++p.request;dbStagesEditor();p.editorKey=dbStagesKey();});
 p.status=dataopsNode('p','db-stages-status',body);p.status.className='status-line';p.status.setAttribute('role','status');p.status.setAttribute('aria-live','polite');
 const definition=workflowDetails(body,'Stage definition');definition.open=true;p.definitionDetails=definition;
 p.form=dataopsNode('form','db-stages-form',definition);p.form.onsubmit=e=>e.preventDefault();dataopsNode('p','db-stages-owner',p.form).className='help';const fields=dataopsNode('div','',p.form);fields.className='job-fields';
 for(const [key,label,tag,options] of [['name','Stage name','input'],['directory','Existing directory','input'],['mode','Mode','select',[['create','Create new'],['append','Append'],['replace','Full replace'],['upsert','Update and add by key']]],['format','File format','select',['auto','csv','json','jsonl']],['destination','Destination table name','input'],['target','Target table','select'],['options','Typed options JSON','textarea']]){
  const node=dataopsField(fields,'db-stages-'+key,label,tag,(options||[]).map(v=>Array.isArray(v)?v:[v,v]));if(tag==='textarea'){node.rows=6;node.spellcheck=false;node.parentElement.className='job-json';}node.oninput=node.onchange=()=>{dbStagesClearPreview();dbStagesCapture();dbStagesRender();};
 }
 dataopsNode('p','',p.form,'Directory is immutable after saving.').className='help';dataopsNode('strong','',p.form,'Approved roots');dataopsNode('pre','db-stages-roots',p.form);dataopsButton(p.form,'db-stages-save','Save Stage',dbStagesSave).className='primary';
 const controls=dataopsNode('div','',body);controls.className='toolbar';dataopsButton(controls,'db-stages-observe','Look up Stage run',()=>dbStagesObserve());dataopsButton(controls,'db-stages-cancel','Cancel run',()=>dbStagesObserve(true));dataopsNode('p','db-stages-run-resend',body,'Resending uses the original body and key. If the request was never accepted, it may start the operation.').className='help';dataopsNode('div','db-stages-run-detail',body).setAttribute('aria-live','polite');
 const scans=dataopsNode('div','',body);scans.className='toolbar';dataopsButton(scans,'db-stages-scan','Manual scan',()=>dbStagesScanStart());dataopsButton(scans,'db-stages-scans','Refresh scans',dbStagesScans);dataopsButton(scans,'db-stages-scan-observe','Look up scan',dbStagesScanObserve);dataopsNode('p','db-stages-scan-resend',body,'Resending uses the original body and key. If the request was never accepted, it may start the operation.').className='help';dataopsNode('div','db-stages-scan-detail',body).setAttribute('aria-live','polite');dataopsNode('div','db-stages-scan-list',body);
 const files=dataopsNode('div','',body);files.className='toolbar';dataopsButton(files,'db-stages-files','Refresh Stage files',dbStagesFiles);dataopsField(files,'db-stages-file','Stage files','select').onchange=()=>{dbStagesClearPreview();dbStagesRender();};dataopsButton(files,'db-stages-preview','Preview file',dbStagesPreview);
 dataopsNode('div','db-stages-preview-detail',body);
 const impact=dataopsNode('p','db-stages-impact',body);impact.className='impact';impact.hidden=true;impact.setAttribute('role','status');
 const confirmRow=dataopsNode('div','',body);confirmRow.className='form-actions';dataopsButton(confirmRow,'db-stages-confirm','Confirm import',()=>dbStagesSubmit('ingest')).className='primary';
 const exp=dataopsNode('div','',body);exp.className='toolbar';dataopsField(exp,'db-stages-export-source','Snapshot source table','select');dataopsField(exp,'db-stages-export-file','Export filename');dataopsField(exp,'db-stages-export-format','File format','select',[['csv','CSV'],['jsonl','JSONL (one object per line)']]);dataopsButton(exp,'db-stages-export','Export snapshot',()=>dbStagesSubmit('export'));
 for(const key of ['export-source','export-file','export-format'])$('db-stages-'+key).oninput=()=>{dbStagesCapture();dbStagesRender();};
 dataopsNode('h3','',body,'Stage history');dataopsButton(body,'db-stages-history','Refresh Stage history',()=>dbStagesRead('runs'));dataopsNode('div','db-stages-history-list',body);
 const receipts=dataopsNode('div','',body);receipts.className='toolbar';dataopsButton(receipts,'db-stages-receipts','Refresh export receipts',()=>dbStagesRead('exports'));dataopsField(receipts,'db-stages-receipt','Export receipt','select').onchange=()=>{++p.request;p.receipt=null;dbStagesRender();};dataopsButton(receipts,'db-stages-inspect-receipt','Look up export receipt',()=>dbStagesReceipt());dataopsButton(receipts,'db-stages-download','Download authorized file',()=>dbStagesReceipt(true));dataopsNode('div','db-stages-receipt-detail',body);
 workbenchAtlasStageTabState();dbStagesRender();
}
