'use strict';

// Saved-lineage state and operations use only the supplied Workbench adapters.
function createSavedLineagePanel({explorer,authority,request,view}){
const dbLineage={revision:0,scope:'',result:null,busy:null,notice:''};
function dbLineageContext(){
 const e=explorer.state,d=explorer.databases.find(d=>d.id===e.database_id),s=e.schemas.find(s=>s.id===e.schema_id);
 if(!explorer.canOpen()||!d||!s)return null;
 return {database_id:d.id,schema_id:s.id,warehouse_id:'LOCAL',sql_catalog:d.sql_catalog,sql_schema:s.sql_name,selected_role:authority.role,binding_revision:d.binding_revision,generation_id:d.generation_id,grant_revision:d.grant_revision??null};
}
function dbLineageScope(){return JSON.stringify([dbLineage.revision,authority.principal,authority.role,authority.roleVersion(authority.role),authority.authorizationVersion,explorer.catalogScope(),explorer.selectionGeneration,explorer.objectEpoch,request.ref(explorer.object),dbLineageContext(),view.activeTab,view.workspacePane,view.activeInspector]);}
function dbLineageReason(){
 if(!['data_engineer','admin'].includes(authority.role))return 'Lineage requires data_engineer or admin.';
 return explorer.canOpen()&&['TABLE','VIEW'].includes(explorer.object?.kind)&&authority.roleWithinHeader(authority.role)?'':'Select a readable TABLE or VIEW in Explorer.';
}
function dbLineageInvalidate(){
 const p=dbLineage;++p.revision;p.scope='';p.result=null;p.notice='';p.busy=null;
 if(view.$('table-lineage'))view.$('table-lineage').replaceChildren();
 dbLineageControls();
}
function dbLineageEndpoint(endpoint){
 if(endpoint.kind==='LOGICAL')return endpoint.label+' · '+view.t('Planned model')+' · '+endpoint.materialization;
 const version=endpoint.kind==='VIEW'?view.t('VIEW version')+' '+endpoint.view_version:view.t('Snapshot')+' '+(endpoint.snapshot_id??'—');
 return endpoint.label+' · '+endpoint.kind+' · '+version;
}
function dbLineageDeclaredEndpoint(endpoint){
 if(endpoint.kind==='LOGICAL')return dbLineageEndpoint(endpoint);
 const ref=endpoint.ref;
 return endpoint.label+' · '+endpoint.kind+(ref?.object_id!=null?' · '+view.t('Ref')+': '+view.t('Object ID')+' '+ref.object_id+(ref.incarnation!=null?' · '+view.t('Incarnation')+' '+ref.incarnation:''):'');
}
async function dbOpenLineage(){
 if(dbLineageReason()||dbLineage.busy)return;
 view.showWorkspace('details');
 const tab=view.inspectorTabs.find(b=>b.dataset.inspect==='lineage');
 await view.selectInspector(tab);tab.focus();
 await dbLineageRefresh();
}
function dbLineageControls(){
 if(!view.databaseMode||!view.$('catalog-database'))return;
 const p=dbLineage,reason=dbLineageReason(),scope=dbLineageScope();
 if(p.scope&&p.scope!==scope){p.result=null;p.scope='';p.notice='';}
 const tab=view.inspectorTabs.find(b=>b.dataset.inspect==='lineage');tab.disabled=!!reason;tab.title=reason?view.t(reason):'';
 view.wireTabs(view.inspectorTabs,view.selectInspector);
 const open=view.$('explorer-open-lineage');if(open){open.disabled=!!reason||!!p.busy;open.title=reason?view.t(reason):'';}
 const root=view.$('table-lineage');root.replaceChildren();
 const toolbar=view.node('div','',root);toolbar.className='toolbar';
 const button=view.button(toolbar,'db-lineage-refresh','Refresh saved lineage',dbLineageRefresh);button.disabled=!!reason||!!p.busy;
 if(reason){view.node('p','',root,reason);return;}
 const context=dbLineageContext();view.node('p','',root,[context.sql_catalog,context.sql_schema,explorer.object.physical_name,view.t({admin:'Admin',data_engineer:'Data Engineer',bi_viewer:'BI Viewer'}[authority.role])].join(' / '));
 view.node('p','',root,'Visible saved records only; catalog completeness is not established.');
 const status=view.node('p','',root,p.busy?'Reading saved lineage…':p.notice||(!p.result?'Refresh to read saved lineage for the selected object.':''));status.setAttribute('role','status');
 if(!p.result)return;
 const result=p.result;view.node('p','',root,dbLineageEndpoint(result.focus));
 if(result.coverage?.limited)view.node('p','',root,'Bounded visible coverage');
 if(!result.confirmed.length&&!result.declared.length)view.node('p','',root,'No visible saved evidence for this object.');
 for(const [key,heading] of [['confirmed','Confirmed saved evidence'],['declared','Declared model references']]){
  const section=view.node('section','',root);view.node('h3','',section,heading);
  if(key==='declared')view.node('p','',section,'Model definitions describe dependencies between objects. Pinned runtime versions appear in Confirmed saved evidence.');
  const endpoint=key==='declared'?dbLineageDeclaredEndpoint:dbLineageEndpoint;
  const list=view.node('ul','',section);
  for(const fact of result[key]){
   const row=view.node('li','',list);row.className='lineage-fact';
   view.node('p','',row,view.t(({wap_build:'WAP build',wap_publication:'WAP publication',native_graph:'Native dbt graph',native_publication:'Native dbt publication',declared_model:'Saved model definition'})[fact.category])+(fact.temporal?' · '+view.t(fact.temporal==='current'?'Current version':'Historical version'):''));
   view.node('p','',row,endpoint(fact.source)+' → '+endpoint(fact.target));
   if(fact.model)view.node('p','',row,view.t('Model')+' '+fact.model.id+(fact.model.version?' · '+view.t('WAP version')+' '+fact.model.version:fact.model.plan_id?' · '+view.t('Saved plan')+' '+fact.model.plan_id:''));
  }
 }
}
async function dbLineageRefresh(){
 dbLineageControls();if(dbLineageReason()||dbLineage.busy)return;
 const p=dbLineage,token={},scope=dbLineageScope(),context=request.freeze(dbLineageContext()),source=request.freeze(request.clone(request.ref(explorer.object))),role=authority.role,id=explorer.object.id;
 const current=()=>scope===dbLineageScope()&&p.busy===token;
 const allowed=items=>request.runAllowed({origin:{context,role,principal:authority.principal}},items);
 const verify=async()=>{const items=await request.readInventory(role,current);if(!current()||!request.inventoryCurrent(items))return false;if(!allowed(items)){throw Object.assign(Error('DB_ACCESS_REVOKED'),{code:'DB_ACCESS_REVOKED'});}const schemas=await request.api('/api/v1/databases/'+encodeURIComponent(context.database_id)+'/schemas',role);if(!current())return false;if(!schemas.items?.some(s=>s.id===source.schema_id&&s.database_id===context.database_id&&s.sql_name===context.sql_schema&&s.capabilities?.query===true))throw Object.assign(Error('DB_ACCESS_REVOKED'),{code:'DB_ACCESS_REVOKED'});request.applyInventory(items,role);return current()&&request.inventoryCurrent(items)&&!dbLineageReason();};
 p.busy=token;p.result=null;p.scope='';p.notice='';dbLineageControls();
 try{
  if(!await verify())return;
  const result=await request.api('/api/v1/objects/'+encodeURIComponent(id)+'/lineage?context='+encodeURIComponent(JSON.stringify(context)),role);
  if(!current())return;
  if(!await verify())return;
  if(request.refKey(result.focus?.ref)!==request.refKey(source))throw Error('STALE_RESOURCE');
  p.result=result;p.scope=scope;
 }catch(error){
  if(scope===dbLineageScope()&&p.busy===token){p.result=null;p.scope='';p.notice='Saved lineage is unavailable. Refresh Explorer context and try again.';if(request.authError(error)){dbLineageInvalidate();explorer.denied(error);}}
 }finally{if(p.busy===token)p.busy=null;dbLineageControls();}
}

return Object.freeze({
 controls:dbLineageControls,invalidate:dbLineageInvalidate,refresh:dbLineageRefresh,reason:dbLineageReason,open:dbOpenLineage,
 get busy(){return dbLineage.busy;},
 get result(){return dbLineage.result;}
});
}
