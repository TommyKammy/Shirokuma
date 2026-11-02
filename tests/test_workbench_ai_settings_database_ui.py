"""Production Database AI settings handlers with delayed fetch and session replies."""
import unittest
import test_workbench_database_ui as harness

SETTINGS = r"""
Node.prototype.showModal=function(){this.open=true;};
Node.prototype.close=function(){this.open=false;this.dispatchEvent({type:'close'});};
Node.prototype.reportValidity=function(){return true;};
const configReply=()=>({provider:'local-openai',model:'test-model',api_url:'http://127.0.0.1:8001/v1',timeout:30,api_key:'********'});
const settingsCalls=()=>calls.filter(c=>c.path.startsWith('/api/v1/bears/ai-'));
const settingsEdit=(id,value)=>{$(id).value=value;$('ai-settings-form').oninput({target:$(id)});};
async function settingsBoot(){session.platform_admin=true;await boot();route=async(path,options)=>path.endsWith('ai-test')?{status:'connected',latency_ms:7}:configReply();}
const settingsOpen=()=> $('ai-settings-button').onclick();
const settingsTest=()=> $('ai-test').onclick();
const settingsSave=()=> $('ai-settings-form').onsubmit({preventDefault(){}});
const settingsClose=()=> $('ai-close').onclick();
"""


class AISettingsDatabaseUI(unittest.TestCase):
    def javascript(self, body):
        # Install minimal dialog methods before initializeDatabaseUI runs.
        harness.DatabaseUI.javascript(self, SETTINGS + body)

    def test_current_p_header_without_db_load_only_explicit_test_save_and_shared_help(self):
        self.javascript(r"""
await settingsBoot();worksheets=[];worksheetId='';inventory=[];schemas=[];calls.length=0;dbControls();
assert.equal($('ai-settings-button').disabled,false);await settingsOpen();
assert.equal($('ai-settings').open,true);assert.equal($('ai-model').value,'test-model');assert.equal($('ai-key').value,'');
assert.deepEqual(calls.map(c=>c.path),['/api/session','/api/v1/bears/ai-config','/api/session']);
assert.deepEqual(settingsCalls()[0].options,{headers:{'X-Shirokuma-Role':'admin'}});
assert.equal($('ai-test').disabled,false);settingsEdit('ai-key','new-secret');await settingsSave();
let c=settingsCalls().at(-1);assert.equal(c.path,'/api/v1/bears/ai-config');assert.equal(c.options.method,'POST');assert.equal(JSON.parse(c.options.body).api_key,'new-secret');assert.equal($('ai-key').value,'');assert.equal($('ai-config-status').textContent,t('Settings saved'));
await settingsTest();assert.equal(settingsCalls().at(-1).path,'/api/v1/bears/ai-test');assert.equal($('ai-config-status').textContent,t('Shared connection succeeded {latency}ms; Database AI readiness is not verified.',{latency:7}));
assert.equal(settingsCalls().length,3);assert.equal(writes.some(k=>/ai.*key/.test(k)),false);assert.ok([...storage.values()].every(v=>!v.includes('new-secret')));
worksheets=[input()];worksheetId='a';for(const locale of ['en','ja']){setLanguage(locale);assert.equal($('ai-shared-help').textContent,t(dbAISettingsHelp));if(locale==='ja')assert.notEqual(t(dbAISettingsHelp),dbAISettingsHelp);}
settingsClose();assert.equal($('ai-key').value,'');assert.equal($('ai-model').value,'');
""")

    def test_shared_help_states_inference_constraints_and_success_scope_in_both_languages(self):
        self.javascript(r"""
await settingsBoot();await settingsOpen();
for(const locale of ['en','ja']){
 setLanguage(locale);const help=$('ai-shared-help').textContent;
 for(const text of ['local-openai','HTTP','IP','/v1/','[A-Za-z0-9_.:/-]','128','60'])assert.ok(help.includes(text),text);
 await settingsTest();assert.equal($('ai-config-status').textContent,t('Shared connection succeeded {latency}ms; Database AI readiness is not verified.',{latency:7}));
 if(locale==='ja')assert.ok($('ai-config-status').textContent.includes('未確認'));
}
""")

    def test_edit_after_successful_test_and_save_resets_success_status(self):
        self.javascript(r"""
await settingsBoot();await settingsOpen();
for(const action of [settingsTest,settingsSave]){
 await action();assert.equal($('ai-config-status').className,'badge good');
 settingsEdit('ai-model','edited-model');
 assert.equal($('ai-config-status').textContent,t('Settings changed; test again.'));
 assert.equal($('ai-config-status').className.trim(),'badge');
 assert.equal($('ai-config-status').hidden,false);
}
""")

    def test_non_p_db_admin_false_like_p_and_lower_header_or_ceiling_denied(self):
        self.javascript(r"""
await settingsBoot();for(const flag of [false,'true',1,null]){session.platform_admin=flag;dbAdminSession(session);dbControls();await settingsOpen();assert.equal($('ai-settings-button').disabled,true);assert.ok(!$('ai-settings').open);}
session.platform_admin=true;dbAdminSession(session);for(const role of ['bi_viewer','data_engineer']){currentRole=role;dbControls();await settingsOpen();assert.equal($('ai-settings-button').disabled,true);}
currentRole='admin';dbUpdateMaximumRole('data_engineer');await settingsOpen();assert.equal($('ai-settings-button').disabled,true);assert.equal(settingsCalls().length,0);
""")

    def test_concurrent_save_test_blocked_edits_invalidate_labels_and_keep_secret(self):
        self.javascript(r"""
await settingsBoot();for(const action of [settingsTest,settingsSave]){await settingsOpen();settingsEdit('ai-key','draft-secret');const delayed=deferred();route=()=>delayed.promise;const pending=action();await tick();assert.equal($('ai-test').disabled,true);assert.equal($('ai-save').disabled,true);let count=settingsCalls().length;await settingsTest();await settingsSave();assert.equal(settingsCalls().length,count);
settingsEdit('ai-model','edited');delayed.resolve({status:'connected',latency_ms:99});await pending;assert.equal($('ai-key').value,'draft-secret');assert.equal($('ai-config-status').textContent,t('Settings changed; test again.'));assert.equal($('ai-save').disabled,false);settingsClose();route=async()=>configReply();}
""")

    def test_late_get_test_save_after_close_reopen_and_role_aba_cannot_refill(self):
        self.javascript(r"""
await settingsBoot();for(const kind of ['get','test','save'])for(const lose of ['close','role']){
route=async()=>configReply();if(kind!=='get')await settingsOpen();const delayed=deferred();route=()=>delayed.promise;
const pending=kind==='get'?settingsOpen():kind==='test'?settingsTest():settingsSave();await tick();
if(lose==='role'){currentRole='bi_viewer';dbControls();assert.equal($('ai-settings').open,false);assert.equal($('ai-key').value,'');currentRole='admin';dbControls();}else settingsClose();
route=async()=>({...configReply(),model:'fresh'});await settingsOpen();settingsEdit('ai-key','fresh-secret');const count=settingsCalls().length;
delayed.resolve(kind==='test'?{status:'connected',latency_ms:99}:{...configReply(),model:'stale',api_key:'SECRET'});await pending;
assert.equal($('ai-model').value,'fresh');assert.equal($('ai-key').value,'fresh-secret');assert.equal($('ai-config-status').textContent,t('Settings changed; test again.'));assert.equal(settingsCalls().length,count);assert.equal($('ai-save').disabled,false);settingsClose();}
""")

    def test_authority_loss_current_session_and_api_denial_close_purge(self):
        self.javascript(r"""
await settingsBoot();for(const loss of ['account','identity','maximum','header','401','403']){
sessionRoute=null;session.platform_admin=true;session.maximum_role='admin';dbPrincipal=JSON.stringify(['owner',true]);currentRole='admin';dbUpdateMaximumRole('admin');dbAdminSession(session);route=async()=>configReply();await settingsOpen();settingsEdit('ai-key','draft-secret');
if(loss==='account'){session.platform_admin=false;await settingsTest();}
else if(loss==='identity'){dbPrincipal=JSON.stringify(['other',true]);dbControls();}
else if(loss==='maximum')dbUpdateMaximumRole('bi_viewer');else if(loss==='header'){currentRole='data_engineer';dbControls();}
else{route=async()=>({http:Number(loss),body:{error:'SECRET'}});await settingsTest();}
assert.equal($('ai-settings').open,false);assert.equal($('ai-key').value,'');assert.equal($('ai-model').value,'');assert.equal($('ai-config-status').hidden,true);assert.equal($('ai-save').disabled,true);
}
""")

    def test_stale_session_headers_and_body_cannot_restore_or_remove_authority(self):
        self.javascript(r"""
await settingsBoot();for(const stage of ['headers','body']){
const delayed=deferred();sessionRoute=()=>stage==='headers'?delayed.promise:{http:200,body:delayed.promise};const pending=settingsOpen();await tick();settingsClose();sessionRoute=null;await settingsOpen();const model=$('ai-model').value;
delayed.resolve(stage==='headers'?{http:403,body:{error:'SECRET'}}:{...session,platform_admin:false});await pending;assert.equal(dbAdmin.platform,true);assert.equal($('ai-settings').open,true);assert.equal($('ai-model').value,model);assert.equal($('ai-save').disabled,false);settingsClose();}
""")

    def test_native_close_ignores_late_api_body_denial_and_rejection_for_all_actions(self):
        self.javascript(r"""
await settingsBoot();for(const kind of ['get','test','save'])for(const outcome of ['body','denial','rejection']){
 route=async()=>configReply();if(kind!=='get')await settingsOpen();
 const delayed=deferred();route=()=>outcome==='body'?{http:200,body:delayed.promise}:delayed.promise;
 const pending=kind==='get'?settingsOpen():kind==='test'?settingsTest():settingsSave();await tick();
 $('ai-settings').close();assert.equal($('ai-key').value,'');assert.equal($('ai-model').value,'');
 route=async()=>({...configReply(),model:'fresh'});await settingsOpen();settingsEdit('ai-key','fresh-secret');
 if(outcome==='rejection')delayed.reject(Error('SECRET failure'));
 else delayed.resolve(outcome==='denial'?{http:403,body:{error:'SECRET'}}:{...configReply(),model:'stale',status:'connected',latency_ms:99});
 await pending;assert.equal(dbAdmin.platform,true);assert.equal($('ai-settings').open,true);
 assert.equal($('ai-model').value,'fresh');assert.equal($('ai-key').value,'fresh-secret');
 assert.equal($('ai-config-status').textContent,t('Settings changed; test again.'));assert.equal($('ai-save').disabled,false);settingsClose();
}
""")

    def test_current_post_response_session_loss_discards_get_test_save_and_purges(self):
        self.javascript(r"""
await settingsBoot();for(const kind of ['get','test','save'])for(const loss of [{platform_admin:false},{username:'other'},{maximum_role:'bi_viewer'},{authenticated:false}]){
 sessionRoute=null;dbUpdateMaximumRole('admin');dbAdminSession(session);route=async()=>configReply();if(kind!=='get')await settingsOpen();
 const delayed=deferred();route=()=>delayed.promise;
 const pending=kind==='get'?settingsOpen():kind==='test'?settingsTest():settingsSave();await tick();
 sessionRoute=async()=>({...session,...loss});delayed.resolve(kind==='test'?{status:'connected',latency_ms:99}:configReply());await pending;
 assert.equal(dbAdmin.platform,false);assert.equal($('ai-settings').open,false);assert.equal($('ai-key').value,'');assert.equal($('ai-model').value,'');assert.equal($('ai-config-status').hidden,true);assert.equal($('ai-save').disabled,true);
}
""")

    def test_current_denial_no_parse_failure_sanitized_and_same_auth_preserves_drafts(self):
        self.javascript(r"""
await settingsBoot();await settingsOpen();const before=JSON.stringify(worksheets),sql=$('sql').value,explorer=JSON.stringify(dbExplorer),storageBefore=JSON.stringify([...storage]);
for(const action of [settingsTest,settingsSave]){route=async()=>({http:500,body:{error:'SECRET provider exception'}});await action();assert.ok(!$('ai-config-status').textContent.includes('SECRET'));assert.equal(JSON.stringify(worksheets),before);assert.equal($('sql').value,sql);assert.equal(JSON.stringify(dbExplorer),explorer);assert.equal(JSON.stringify([...storage]),storageBefore);}
let parsed=0;route=()=>{const value={http:403};Object.defineProperty(value,'body',{get(){parsed++;throw Error('SECRET');}});return value;};await settingsTest();assert.equal(parsed,0);assert.equal(dbAdmin.platform,false);assert.equal($('ai-key').value,'');
""")

    def test_programmatic_input_edit_during_get_ignored_and_retry_reopen(self):
        self.javascript(r"""
await settingsBoot();const delayed=deferred();route=()=>delayed.promise;const pending=settingsOpen();await tick();settingsEdit('ai-model','draft');delayed.resolve(configReply());await pending;assert.equal($('ai-model').value,'draft');assert.equal($('ai-save').disabled,true);settingsClose();route=async()=>configReply();await settingsOpen();assert.equal($('ai-model').value,'test-model');assert.equal($('ai-save').disabled,false);
""")


if __name__ == '__main__': unittest.main()
