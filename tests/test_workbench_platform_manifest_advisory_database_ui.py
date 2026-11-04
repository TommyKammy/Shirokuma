"""Policy Platform review through the existing offline Workbench DOM harness."""
import unittest

import test_workbench_platform_controller_baseline_database_ui as controller_ui


SETUP = r"""
const manifestPath='/api/v1/bears/platform-manifest-advisory';
const manifestCalls=()=>calls.filter(c=>c.path===manifestPath);
const manifestPanel=()=>$(dbManifestPrefix);
const draft=value=>{dbManifestNode('draft').value=value;dbManifestNode('draft').oninput();};
const reply=(kind='Pod')=>({kind,containers:[{
 kind:'container',ordinal:1,checks:{read_only_root_filesystem:'met',non_root:'not_met',drop_all_capabilities:'met',cpu_memory_limits:'not_met'}
}]});
const review=()=>dbManifestNode('submit').onclick();
const refresh=()=>dbManifestNode('auth').onclick();
const result=()=>visibleText(dbManifestNode('result'));
const policy=()=>{$('tab-bears').click();$('bear-tab-policy').click();scope('policy');};
async function start(){await controllerBoot();policy();}
async function respond(data=reply()){
 route=async(path,options)=>{
  if(path!==manifestPath)return runtimeFixtureRoute(path,options);
  assert.deepEqual(options,{method:'POST',headers:{'Content-Type':'application/json','X-Shirokuma-Role':'admin'},body:JSON.stringify({manifest:dbManifestNode('draft').value})});
  return data;
 };
 await review();
}
"""


class ManifestAdvisoryUI(unittest.TestCase):
    def javascript(self, body):
        controller_ui.PlatformControllerBaselineDatabaseUI.javascript(self, SETUP + body)

    def navigation(self, body):
        controller_ui.PlatformControllerBaselineDatabaseUI.navigation_javascript(self, body)

    def test_policy_platform_native_manual_and_localized(self):
        self.navigation(r"""
await controllerBoot();
assert.equal(manifestPanel().parentElement,$('bear-panel-policy'));
assert.equal(manifestPanel().hidden,true);
policy();assert.equal(manifestPanel().hidden,false);
assert.equal(dbManifestNode('draft').tagName,'TEXTAREA');
assert.equal(dbManifestNode('label').htmlFor,dbManifestNode('draft').id);
assert.equal(dbManifestNode('status').role,'status');
assert.equal(dbManifestNode('draft').value,'');
draft('private YAML');await review();assert.equal(manifestCalls().length,0);
const before=calls.length;await refresh();assert.deepEqual(calls.slice(before).map(c=>c.path),['/api/session']);
await respond();assert.equal(manifestCalls().length,1);assert.match(result(),/Declaration matches/);assert.doesNotMatch(result(),/private/);
setLanguage('ja');assert.match(dbManifestNode('title').textContent,/宣言確認/);assert.match(result(),/通常コンテナ/);
$('bear-tab-sre').click();scope('sre');assert.equal(manifestPanel().hidden,true);
$('tab-compute').click();assert.equal(manifestPanel().hidden,true);
policy();assert.equal(dbManifestNode('draft').value,'private YAML');assert.equal(result(),'');
assert.equal(manifestCalls().length,1);
""")

    def test_edit_navigation_and_auth_aba_fence_old_success_and_denial(self):
        self.navigation(r"""
await start();await refresh();draft('original');await respond();
for(const transition of [
 ()=>{draft('changed');draft('original');},
 ()=>{scope('policy','database');scope('policy');},
 ()=>{$('tab-warehouse').click();policy();},
 ()=>{$('bear-tab-sre').click();$('bear-tab-policy').click();},
 ()=>{setLanguage('ja');setLanguage('en');},
 ()=>{currentRole='bi_viewer';dbControls();currentRole='admin';dbControls();},
 ()=>{dbAdminSession({...session,username:'other'});dbAdminSession(session);}
]){
 for(const status of [200,403]){
  const waiting=deferred();route=()=>waiting.promise;const old=review();await tick();transition();
  if(!dbManifestAdvisory.verified){assert.equal(dbManifestNode('draft').value,'');await refresh();}
  draft('newer');await respond(reply('Job'));const saved=dbManifestAdvisory.record;
  waiting.resolve(status===200?reply():{controllerResponse:{ok:false,status,text:()=>{throw Error('late denial');}}});
  await old;assert.equal(dbManifestAdvisory.record,saved);assert.equal(saved.kind,'Job');assert.equal(dbAdmin.platform,true);
 }
}
""")

    def test_pending_response_body_and_current_revocation_purge(self):
        self.navigation(r"""
await start();await refresh();draft('sensitive');
const body=deferred(),entered=deferred();
route=async()=>({controllerResponse:{ok:true,status:200,text:()=>{entered.resolve();return body.promise;}}});
const old=review();await entered.promise;draft('replacement');body.resolve(JSON.stringify(reply()));await old;
assert.equal(result(),'');assert.equal(dbManifestNode('draft').value,'replacement');
let parsed=0;
route=async()=>({controllerResponse:{ok:false,status:403,text:()=>{parsed++;throw Error('private');}}});
await review();assert.equal(parsed,0);assert.equal(dbManifestNode('draft').value,'');
assert.equal(dbManifestNode('submit').disabled,true);assert.equal(dbAdmin.platform,false);
await refresh();assert.equal(dbManifestAdvisory.verified,true);assert.equal(dbManifestNode('draft').value,'');
""")

    def test_closed_dto_and_existing_panel_independence(self):
        self.navigation(r"""
await start();await controllerFetch('policy');const held=dbControllerBaseline.policy.record;
const oldContext=dbObservations.policy.context,oldPlatform=dbPlatform.record;
await refresh();draft('manifest');
for(const data of [
 {...reply(),name:'private'},
 {...reply(),containers:[{...reply().containers[0],name:'private'}]},
 {...reply(),containers:[{...reply().containers[0],ordinal:2}]},
 {...reply('Job'),containers:[reply().containers[0],{...reply().containers[0],kind:'ephemeral_container'}]},
 {...reply(),containers:[{...reply().containers[0],checks:{...reply().containers[0].checks,non_root:'private'}}]}
]){
 await respond(data);assert.equal(result(),'');assert.doesNotMatch(dbManifestNode('status').textContent,/private/);
}
await respond();assert.match(result(),/Pod/);draft('edited');assert.equal(result(),'');
assert.equal(dbControllerBaseline.policy.record,held);
assert.equal(dbObservations.policy.context,oldContext);assert.equal(dbPlatform.record,oldPlatform);
draft('日'.repeat(22000));const before=manifestCalls().length;await review();assert.equal(manifestCalls().length,before);
""")


    def test_refresh_applies_matching_session_role_ceiling_and_purges_draft(self):
        self.navigation(r"""
await start();await refresh();draft('private');await respond();assert.ok(result());
sessionRoute=async()=>({...session,maximum_role:'data_engineer'});
await refresh();
assert.equal(dbMaximumRole,'data_engineer');
assert.equal(dbManifestAdvisory.verified,false);
assert.equal(dbManifestNode('draft').value,'');
assert.equal(dbManifestNode('submit').disabled,true);
assert.equal(result(),'');
const sent=manifestCalls().length;await review();assert.equal(manifestCalls().length,sent);
""")
