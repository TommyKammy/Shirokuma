"""Public creation lifecycle behavior exercised against production JS handlers."""
import json
import io
import os
from pathlib import Path
import sys
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from http.client import HTTPResponse
from urllib.request import Request
from unittest.mock import patch

import test_workbench_admin_ui as admin


SETUP = admin.SETUP + r'''
const C='/api/v1/admin/database-creations';
const process=(extra={})=>({creation_id:'creation',database_id:'db',requester_id:'owner',revision:3,
 sql_catalog:'sales',display_name:'売上 <script> 🐻',description:'literal <img>',initial_grants:[],
 lifecycle:'REGISTERED',progress_code:'PLAN_REVIEW_REQUIRED',plans:[],next_actions:['plans','reconcile'],access_source:'platform_admin',...extra});
const plan=(phase='bootstrap',state='AWAITING_APPLY',purpose='APPLY')=>({plan_id:phase+'-plan',operation_id:phase+'-op',phase,purpose,state,
 operation_revision:2,delivery_digest:'digest-'+phase,recovery_digest:purpose==='VERIFY_RECOVERY'?'recovery':null,
 public_summary:{code:'PLAN',impact:phase==='bootstrap'?'BOOTSTRAP_MAINTENANCE':'FULL_RUNTIME_HANDOFF',fixed_steps:['apply','verify']},steps:[{step:'apply',state:'PENDING'}]});
let creation=process(),creationPosts=[],creationRoute=null;
let capability={environment_id:'envA',warehouse_id:'LOCAL',warehouse_revision:3,configured:true,available:true,blocker:null};
adminRoute=async(path,options)=>{
 if(!path.startsWith(C))return {};
 if(options.method==='POST'){
  creationPosts.push({path,options});
  if(creationRoute)return creationRoute(path,options);
  return {creation_id:'creation',request_id:'ack',accepted_revision:3};
 }
 if(path===C+'/capabilities')return structuredClone(capability);
 if(path===C+'/principals')return {principals:[{principal_id:'alice',display_name:'Alice <b>',maximum_role:'data_engineer'}]};
 return path===C?{creations:[creation],cursor:null}:structuredClone(creation);
};
dbCreation.capability=null;await dbCreationLoad();
const edit=(id,value)=>{$(id).value=value;$(id).oninput();};
const begin=()=>{dbCreation.process=null;dbCreationRender();edit('db-creation-sql_catalog','new_sales');edit('db-creation-display_name','名前 <script> 🐻');edit('db-creation-description','説明 <img>');};
const show=p=>{creation=p;dbCreation.process=structuredClone(p);dbCreationRender();};
const contents=node=>[node.textContent,...node.children.map(contents)].join('\n');
'''


TIMER_SETUP = SETUP + r'''
dbCreationStop();activeTab='db-admin';
let timerNow=0,timerId=0;const timers=new Map();
globalThis.setTimeout=(callback,delay)=>{const id=++timerId;timers.set(id,{callback,due:timerNow+delay});return id;};
globalThis.clearTimeout=id=>timers.delete(id);
const advanceTimers=async milliseconds=>{
 timerNow+=milliseconds;
 for(const [id,timer] of [...timers])if(timer.due<=timerNow&&timers.delete(id))timer.callback();
 await tick();
};
'''


class CreationUI(unittest.TestCase):
    javascript = admin.harness.DatabaseUI.javascript

    def test_observation_hidden_before_request_and_visible_for_selected_or_uncertain_request(self):
        self.javascript(SETUP + r'''
begin();
for(const locale of ['en','ja']){
 setLanguage(locale);dbCreationRender();
 assert.equal($('db-creation-observation').hidden,true);assert.equal($('db-creation-observation').textContent,'');
}
setLanguage('en');show(process());assert.equal($('db-creation-observation').hidden,false);
await dbCreationLoad();assert.match($('db-creation-observation').textContent,/Last successful status check/);
begin();assert.equal($('db-creation-observation').hidden,true);
await $('db-creation-next').onclick();await $('db-creation-next').onclick();
const held=deferred();creationRoute=()=>held.promise;const posting=$('db-creation-submit').onclick();await tick();
assert.equal($('db-creation-observation').hidden,true);
held.reject(Error('lost reply'));await posting;
assert.equal(dbCreation.process,null);assert.ok(dbCreation.pending);assert.equal(dbCreation.pending.busy,false);
assert.equal($('db-creation-observation').hidden,false);
assert.match(contents($('db-creation-form')),/Request outcome uncertain/);
assert.match($('db-creation-status').textContent,/Connection unavailable/);
assert.doesNotMatch($('db-creation-observation').textContent,/Last successful status check/);
assert.equal(creationPosts.length,1);
''')

    def test_name_form_required_optional_help_and_primary_controls_preserve_payload(self):
        self.javascript(SETUP + r'''
dbCreation.process=null;dbCreation.stage=0;dbCreation.draft={sql_catalog:'',display_name:'',description:'',initial_grants:[]};dbCreationRender();
for(const locale of ['en','ja']){
 setLanguage(locale);dbCreationRender();
 for(const [key,label,help] of [
  ['sql_catalog','SQL catalog name (required)','Required. Example: analytics. Start with a lowercase letter, then use lowercase letters, digits or underscores (up to 31 characters).'],
  ['display_name','Display name (optional)','Optional. If empty, the SQL catalog name is used.'],
  ['description','Description (optional)','Optional. Add a short description of this database.']]){
  const field=$('db-creation-'+key),hint=$('db-creation-'+key+'-help');
  assert.equal(field.parentElement.children.find(n=>n.tagName==='LABEL').textContent,t(label));
  assert.equal(hint.textContent,t(help));assert.equal(field.getAttribute('aria-describedby'),hint.id);
 }
 assert.match($('db-creation-sql_catalog-help').textContent,/analytics/);assert.match($('db-creation-sql_catalog-help').textContent,/31/);
 assert.equal($('db-creation-next').className,'primary');
 edit('db-creation-sql_catalog','analytics');await $('db-creation-next').onclick();
 assert.equal($('db-creation-next').className,'primary');
 await $('db-creation-next').onclick();assert.equal($('db-creation-submit').className,'primary');
 assert.equal(dbCreation.draft.display_name,'');assert.equal(dbCreation.draft.description,'');assert.deepEqual(dbCreation.draft.initial_grants,[]);
 assert.equal(creationPosts.length,0);
 await $('db-creation-back').onclick();await $('db-creation-back').onclick();
}
await $('db-creation-next').onclick();await $('db-creation-next').onclick();await $('db-creation-submit').onclick();
assert.deepEqual(JSON.parse(creationPosts[0].options.body),{sql_catalog:'analytics',display_name:'analytics',description:'',initial_grants:[]});
''')

    def test_summary_distinguishes_ready_retired_current_and_future_maintenance(self):
        self.javascript(SETUP + r'''
const maintenance='Foundation setup and SQL connection setup require separate plan review and approval. LOCAL maintenance continues through full verification.';
for(const locale of ['en','ja']){
 setLanguage(locale);
 for(const access_source of ['platform_admin','none']){
  show(process({lifecycle:'READY',progress_code:'READY',access_source,plans:[plan('bootstrap','PREPARED'),plan('full','APPLIED')],next_actions:[]}));
  const text=contents($('db-creation-process'));
  assert.ok(text.includes(t(access_source==='none'?'Verification is complete. SQL use is unavailable for the selected role.':'Verification is complete. The database is ready to use.')));
  assert.equal(text.includes(t(maintenance)),false);
  assert.equal(text.includes(t('Warehouse maintenance; waiting for full approval or verification.')),false);
  assert.equal($('db-creation-actions').descendants().some(n=>n.id==='db-creation-sql'),access_source!=='none');
 }
 for(const lifecycle of ['RETIRING','RETIRED']){
  show(process({lifecycle,progress_code:'READY',plans:[plan('full','APPLIED')],next_actions:[]}));
  const text=contents($('db-creation-process'));
  assert.ok(text.includes(t('Creation history is shown for this retiring or retired database.')));
  assert.equal(text.includes(t(maintenance)),false);assert.equal(text.includes(t('Verification is complete. The database is ready to use.')),false);
 }
 show(process({lifecycle:'RECONCILING',progress_code:'VERIFYING',plans:[plan('full','APPLYING')],next_actions:['withdraw']}));
 assert.ok(contents($('db-creation-process')).includes(t('Warehouse maintenance; waiting for full approval or verification.')));
 assert.equal(contents($('db-creation-process')).includes(t(maintenance)),false);
 begin();await $('db-creation-next').onclick();await $('db-creation-next').onclick();
 assert.ok(contents($('db-creation-form')).includes(t('Foundation setup and SQL connection setup require separate plan review and approval. Creation will place all of LOCAL in maintenance until full verification completes.')));
 await $('db-creation-back').onclick();await $('db-creation-back').onclick();
}
assert.equal(creationPosts.length,0);assert.equal(writeCalls().length,0);
''')

    def test_registered_intent_and_unapproved_bootstrap_show_future_maintenance_impact(self):
        self.javascript(SETUP + r'''
const future='Foundation setup and SQL connection setup require separate plan review and approval. Creation will place all of LOCAL in maintenance until full verification completes.';
const activeSummary='Foundation setup and SQL connection setup require separate plan review and approval. LOCAL maintenance continues through full verification.';
const current='Warehouse maintenance; waiting for full approval or verification.';
for(const locale of ['en','ja']){
 setLanguage(locale);
 for(const extra of [
  {progress_code:'PREPARATION_PENDING',plans:[],next_actions:['withdraw','plans']},
  {progress_code:'PLAN_REVIEW_REQUIRED',plans:[plan()],next_actions:['withdraw','approve','replan']}
 ]){
  show(process({lifecycle:'REGISTERED',...extra}));
  const text=contents($('db-creation-process'));
  assert.ok(text.includes(t(future)));assert.equal(text.includes(t(activeSummary)),false);assert.equal(text.includes(t(current)),false);
  if(extra.plans.length){
   await $('db-creation-review-bootstrap').onclick();
   const reviewed=contents($('db-creation-process'));
   assert.ok(reviewed.includes(t(future)));assert.equal(reviewed.includes(t(activeSummary)),false);assert.equal(reviewed.includes(t(current)),false);
  }
 }
 for(const extra of [
  {progress_code:'APPLYING',plans:[plan('bootstrap','APPLYING')],next_actions:['withdraw']},
  {progress_code:'FULL_REVIEW_REQUIRED',plans:[plan('bootstrap','PREPARED')],next_actions:['withdraw','plans']},
  {progress_code:'FULL_REVIEW_REQUIRED',plans:[plan('bootstrap','PREPARED'),plan('full')],next_actions:['withdraw','approve','replan']}
 ]){
  show(process({lifecycle:'RECONCILING',...extra}));
  const text=contents($('db-creation-process'));
  assert.ok(text.includes(t(current)));assert.ok(text.includes(t(future)));assert.equal(text.includes(t(activeSummary)),false);
 }
}
assert.equal(creationPosts.length,0);assert.equal(writeCalls().length,0);
''')

    def test_first_screen_journey_guidance_and_current_step_in_both_locales(self):
        self.javascript(SETUP + r'''
begin();
for(const locale of ['en','ja']){
 setLanguage(locale);dbCreationRender();
 const steps=$('db-creation-journey').children[0];assert.equal(steps.tagName,'OL');assert.equal(steps.children.length,6);
 assert.equal(steps.children.filter(n=>n.getAttribute('aria-current')==='step').length,1);
 assert.equal(steps.children[0].getAttribute('aria-current'),'step');
 assert.equal(steps.children[3].textContent,t('Foundation setup (approval 1/2)'));
 assert.equal(steps.children[4].textContent,t('SQL connection setup (approval 2/2)'));
 const text=contents($('db-creation-form'));assert.ok(text.includes(t(dbCreationGuidance)));
 assert.match(text,locale==='ja'?/数分.*LOCAL 全体/:/several minutes.*all of LOCAL/);
 assert.equal($('db-creation-next-action').textContent,t('Enter a name, then choose who can use the database.'));
 await $('db-creation-next').onclick();assert.equal($('db-creation-journey').children[0].children[1].getAttribute('aria-current'),'step');
 await $('db-creation-next').onclick();assert.equal($('db-creation-journey').children[0].children[2].getAttribute('aria-current'),'step');
 assert.ok(contents($('db-creation-form')).includes(t(dbCreationGuidance)));
 await $('db-creation-back').onclick();await $('db-creation-back').onclick();
}
assert.equal(creationPosts.length,0);assert.equal(writeCalls().length,0);
''')

    def test_visible_phase_review_and_localized_fixed_steps_without_private_ids(self):
        self.javascript(SETUP + r'''
const bootstrap=plan();bootstrap.steps=[{step:'apply_secrets',state:'SUCCEEDED'},{step:'reconcile_trino',state:'PENDING'},{step:'verify',state:'RUNNING'}];
bootstrap.public_summary.fixed_steps=bootstrap.steps.map(s=>s.step);
for(const phase of ['bootstrap','full'])for(const locale of ['en','ja']){
 setLanguage(locale);
 const current={...structuredClone(bootstrap),phase,plan_id:'private-plan-id',operation_id:'private-operation-id'};
 show(process({lifecycle:'RECONCILING',plans:phase==='full'?[plan('bootstrap','PREPARED'),current]:[current],next_actions:['approve']}));
 const button=$('db-creation-review-'+phase);assert.ok(button);
 for(let node=button;node;node=node.parentElement)assert.notEqual(node.tagName,'DETAILS');
 assert.equal($('db-creation-next-action').textContent,t(phase==='bootstrap'?'Review the foundation plan, then explicitly approve step 1/2.':'Review the SQL connection plan, then explicitly approve step 2/2.'));
 assert.equal($('db-creation-journey').children[0].children[phase==='bootstrap'?3:4].getAttribute('aria-current'),'step');
 await button.onclick();const section=$('db-creation-confirm').parentElement.parentElement;
 assert.ok(contents(section).includes(t(dbCreationGuidance)));
 const visible=contents($('db-creation-actions'));assert.ok(visible.includes(t('Issue connection information')+' · '+t('Completed')));
 assert.ok(visible.includes(t('Verify access')+' · '+t('Running')));
 if(phase==='bootstrap')assert.ok(visible.includes(t('Deferred to SQL connection setup (approval 2/2)')));
 assert.doesNotMatch(visible,/private-plan-id|private-operation-id|BOOTSTRAP_MAINTENANCE|FULL_RUNTIME_HANDOFF|reconcile_trino/);
 assert.equal($('db-creation-approve').disabled,true);
}
assert.equal(creationPosts.length,0);
''')

    def test_prepared_foundation_is_not_ready_and_lifecycle_controls_journey(self):
        self.javascript(SETUP + r'''
const bootstrap=plan('bootstrap','PREPARED');bootstrap.steps=[{step:'reconcile_trino',state:'PENDING'}];bootstrap.public_summary.fixed_steps=['reconcile_trino'];
show(process({plans:[bootstrap],next_actions:['plans']}));
assert.match($('db-creation-next-action').textContent,/not ready yet/);
assert.ok(contents($('db-creation-actions')).includes('Deferred to SQL connection setup (approval 2/2)'));
assert.ok($('db-creation-actions').descendants().includes($('db-creation-full')));
assert.equal($('db-creation-process').descendants().some(n=>n.id==='db-creation-sql'),false);
for(const lifecycle of ['REGISTERED','RETIRING','RETIRED','READY']){
 show(process({lifecycle,progress_code:'READY',plans:[bootstrap,plan('full','APPLIED')],next_actions:[]}));
 const current=$('db-creation-journey').children[0].children.filter(n=>n.getAttribute('aria-current')==='step');
 assert.equal(current.length,['RETIRING','RETIRED'].includes(lifecycle)?0:1);
 if(lifecycle==='READY')assert.equal(current[0].textContent,'Ready');
 else assert.equal(current.some(n=>n.textContent==='Ready'),false);
}
for(const changes of [{progress_code:'STOPPED'}, {blocker:'PRINCIPAL_STOPPED'}, {requester_id:'another'}, {progress_code:'RECONCILE_REQUIRED',plans:[plan('full','UNKNOWN')],next_actions:['reconcile']}]){
 show(process(changes));const text=$('db-creation-next-action').textContent;
 assert.match(text,/stopped or blocked|original requester|outcome is unknown/);
 assert.equal(creationPosts.length,0);
}
const literal='<script>'+ 'verylongword'.repeat(90)+'🐻';
show(process({display_name:literal,description:literal}));assert.ok(contents($('db-creation-process')).includes(literal));
assert.equal($('db-creation-process').descendants().some(n=>n.tagName==='SCRIPT'),false);
''')

    def test_mutation_resumes_single_automatic_get_loop_through_ready(self):
        self.javascript(TIMER_SETUP + r'''
show(process());dbCreationSchedule();assert.equal(timers.size,1);
const held=deferred();creationRoute=()=>held.promise;
const posting=$('db-creation-bootstrap').onclick();await tick();assert.equal(timers.size,0);
await advanceTimers(10000);assert.equal(timers.size,0);assert.equal(creationPosts.length,1);
creation=process({lifecycle:'RECONCILING',progress_code:'APPLYING',plans:[plan('bootstrap','APPLYING')],next_actions:[]});
held.resolve({creation_id:'creation',request_id:'ack',accepted_revision:3});await posting;
assert.equal(dbCreation.process.progress_code,'APPLYING');assert.equal(timers.size,1);
assert.match($('db-creation-observation').textContent,/Automatic status checking every 5 seconds/);
assert.match($('db-creation-observation').textContent,/Last successful status check/);
let checks=0;const old=adminRoute;adminRoute=(path,options)=>{if(path===C+'/creation')++checks;return old(path,options);};
for(const update of [process({revision:4,plans:[plan('bootstrap','PREPARED')],next_actions:['plans']}),process({revision:5,lifecycle:'READY',progress_code:'READY',next_actions:[]})]){
 creation=update;await advanceTimers(4999);assert.notDeepEqual(dbCreation.process,creation);
 await advanceTimers(1);assert.deepEqual(dbCreation.process,creation);assert.equal(timers.size,1);
}
assert.equal(checks,2);assert.equal(creationPosts.length,1);assert.equal(writeCalls().length,1);
assert.equal($('db-creation-journey').children[0].children[5].getAttribute('aria-current'),'step');
dbCreationStop();
''')

    def test_automatic_checks_and_failed_reads_preserve_consent_focus_and_last_success(self):
        self.javascript(TIMER_SETUP + r'''
show(process({plans:[plan()],next_actions:['approve']}));await dbCreationLoad();
await $('db-creation-review-bootstrap').onclick();const checkbox=$('db-creation-confirm');checkbox.checked=true;checkbox.onchange();checkbox.focus();
const reviewed=dbCreation.review,old=adminRoute,checked=dbCreation.lastCheck;
await advanceTimers(5000);
assert.equal($('db-creation-confirm'),checkbox);assert.equal(checkbox.checked,true);assert.equal(document.activeElement,checkbox);assert.equal(dbCreation.review,reviewed);
assert.ok(dbCreation.lastCheck>=checked);const success=dbCreation.lastCheck;
adminRoute=(path,options)=>{if(path===C+'/creation')throw Error('read failed');return old(path,options);};
await advanceTimers(5000);assert.equal(dbCreation.lastCheck,success);assert.equal(timers.size,1);
assert.match($('db-creation-observation').textContent,/Status check failed.*last known state/);
assert.equal($('db-creation-confirm'),checkbox);assert.equal(checkbox.checked,true);assert.equal(document.activeElement,checkbox);
adminRoute=old;await advanceTimers(5000);assert.equal(dbCreation.error,'');assert.equal(dbCreation.readFailed,false);
assert.doesNotMatch($('db-creation-observation').textContent,/Status check failed/);
assert.equal($('db-creation-confirm'),checkbox);assert.equal(checkbox.checked,true);assert.equal(document.activeElement,checkbox);
assert.equal(dbCreation.review,reviewed);assert.equal(creationPosts.length,0);assert.equal(writeCalls().length,0);
''')

    def test_hidden_mutation_completion_pauses_then_revalidates_without_post_replay(self):
        self.javascript(TIMER_SETUP + r'''
show(process());const held=deferred();creationRoute=()=>held.promise;
const posting=$('db-creation-bootstrap').onclick();await tick();document.hidden=true;
held.resolve({creation_id:'creation',request_id:'ack',accepted_revision:3});await posting;
assert.equal(timers.size,0);assert.match($('db-creation-observation').textContent,/checking paused/);
await advanceTimers(15000);assert.equal(creationPosts.length,1);
dbCreationSuspend();document.hidden=false;await dbCreationLoad();assert.equal(timers.size,1);
await advanceTimers(5000);assert.equal(timers.size,1);assert.equal(creationPosts.length,1);
document.hidden=true;dbCreationSuspend();assert.equal(timers.size,0);
await advanceTimers(5000);document.hidden=false;await dbCreationLoad();assert.equal(timers.size,1);assert.equal(creationPosts.length,1);
''')

    def test_uncertain_mutation_resumes_get_without_replaying_frozen_request(self):
        self.javascript(TIMER_SETUP + r'''
show(process());dbCreationSchedule();creationRoute=()=>{throw Error('connection lost');};
await $('db-creation-bootstrap').onclick();assert.equal(creationPosts.length,1);assert.equal(timers.size,1);
const frozen=structuredClone(dbCreation.pending);
creation=process({revision:4,plans:[plan()],next_actions:['approve']});
await advanceTimers(5000);assert.deepEqual(dbCreation.process,creation);assert.deepEqual(dbCreation.pending,frozen);
assert.equal(creationPosts.length,1);assert.equal(timers.size,1);
assert.equal($('db-creation-review-bootstrap').disabled,true);
await advanceTimers(5000);assert.equal(creationPosts.length,1);assert.equal(timers.size,1);
''')

    def test_unchanged_list_check_keeps_draft_and_review_controls(self):
        self.javascript(SETUP + r'''
begin();const field=$('db-creation-description');field.focus();field.setSelectionRange(1,4);
await dbCreationLoad(true);assert.equal($('db-creation-description'),field);assert.equal(document.activeElement,field);assert.equal(field.selectionStart,1);
show(process({plans:[plan()],next_actions:['approve']}));await dbCreationLoad(true);
await $('db-creation-review-bootstrap').onclick();const checkbox=$('db-creation-confirm');checkbox.checked=true;checkbox.onchange();checkbox.focus();
await dbCreationLoad(true);assert.equal($('db-creation-confirm'),checkbox);assert.equal(checkbox.checked,true);assert.equal(document.activeElement,checkbox);
assert.equal(creationPosts.length,0);
''')

    def test_canonical_normal_review_is_primary_even_when_replan_is_allowed(self):
        self.javascript(SETUP + r'''
for(const locale of ['en','ja'])for(const phase of ['bootstrap','full']){
 setLanguage(locale);
 const p=process({lifecycle:'RECONCILING',progress_code:phase==='bootstrap'?'PLAN_REVIEW_REQUIRED':'FULL_REVIEW_REQUIRED',
  plans:phase==='bootstrap'?[plan()]:[plan('bootstrap','PREPARED'),plan('full')],next_actions:['withdraw','approve','replan']});
 const original=JSON.stringify(p);show(p);
 const next=phase==='bootstrap'?'Review the foundation plan, then explicitly approve step 1/2.':'Review the SQL connection plan, then explicitly approve step 2/2.';
 assert.equal($('db-creation-next-action').textContent,t(next));
 const area=$('db-creation-actions');assert.ok(area.descendants().includes($('db-creation-review-'+phase)));
 assert.ok(area.descendants().includes($('db-creation-replan')));
 const heading=t(phase==='bootstrap'?'Foundation setup (approval 1/2)':'SQL connection setup (approval 2/2)');
 assert.ok(contents(area).includes(heading+' · '+t('Awaiting approval')));
 await $('db-creation-review-'+phase).onclick();assert.equal($('db-creation-approve').disabled,true);
 assert.equal($('db-creation-next-action').textContent,t(next));
 assert.equal(JSON.stringify(dbCreation.process),original);assert.equal(creationPosts.length,0);
}
''')

    def test_canonical_blocked_without_blocker_never_claims_running_or_invents_recovery(self):
        self.javascript(SETUP + r'''
for(const locale of ['en','ja']){
 setLanguage(locale);
 const p=process({lifecycle:'REGISTERED',progress_code:'BLOCKED',blocker:null,plans:[],next_actions:['withdraw']});
 const original=JSON.stringify(p);show(p);
 assert.equal($('db-creation-next-action').textContent,t('Creation is stopped or blocked. Review the status and available actions below.'));
 assert.equal($('db-creation-progress').textContent,t('Creation blocked'));
 assert.deepEqual($('db-creation-actions').descendants().filter(n=>n.tagName==='BUTTON').map(n=>n.id),['db-creation-withdraw']);
 assert.equal(contents($('db-creation-actions')).includes(t('Setup or verification is running. Status is checked automatically while this screen is visible.')),false);
 assert.equal(JSON.stringify(dbCreation.process),original);assert.equal(creationPosts.length,0);
}
''')

    def test_canonical_replan_required_without_permission_shows_attention_only(self):
        self.javascript(SETUP + r'''
for(const locale of ['en','ja'])for(const plans of [[],[plan('bootstrap','AWAITING_APPLY')]]){
 setLanguage(locale);
 const p=process({progress_code:'REPLAN_REQUIRED',blocker:null,plans,next_actions:['withdraw']});
 const original=JSON.stringify(p);show(p);
 assert.equal($('db-creation-next-action').textContent,t('A revised plan is required, but preparing it is currently unavailable. Review the status and available actions.'));
 assert.deepEqual($('db-creation-actions').descendants().filter(n=>n.tagName==='BUTTON').map(n=>n.id),['db-creation-withdraw']);
 if(plans.length){
  assert.ok(contents($('db-creation-actions')).includes(t('Foundation setup (approval 1/2)')+' · '+t('Needs attention')));
  assert.equal(contents($('db-creation-actions')).includes(t('Awaiting approval')),false);
 }
 assert.equal(JSON.stringify(dbCreation.process),original);assert.equal(creationPosts.length,0);
}
setLanguage('en');show(process({progress_code:'REPLAN_REQUIRED',blocker:'STALE_PLAN',plans:[plan()],next_actions:['withdraw','replan']}));
assert.equal($('db-creation-next-action').textContent,'Prepare a revised plan, then review it again before approval.');
assert.equal(creationPosts.length,0);
''')

    def test_canonical_running_recovery_overrides_retained_unknown_attention_without_rewriting_history(self):
        self.javascript(SETUP + r'''
const full=plan('full','UNKNOWN');full.steps=[{step:'apply_secrets',state:'UNKNOWN'},{step:'verify',state:'UNKNOWN'}];
full.public_summary.fixed_steps=['apply_secrets','verify'];
const bootstrap=plan('bootstrap','PREPARED');bootstrap.steps=[{step:'apply',state:'SUCCEEDED'},{step:'verify',state:'SUCCEEDED'}];
const recovery=plan('full','UNKNOWN','VERIFY_RECOVERY');recovery.operation_id=full.operation_id;
recovery.steps=structuredClone(full.steps);recovery.public_summary.fixed_steps=['verify'];
for(const locale of ['en','ja'])for(const progress_code of ['VERIFYING','APPLYING']){
 setLanguage(locale);
 const p=process({lifecycle:'RECONCILING',progress_code,blocker:null,
  plans:[bootstrap,full,recovery],next_actions:['withdraw']});
 const original=JSON.stringify(p);show(p);
 const area=$('db-creation-actions'),visible=contents(area);
 assert.equal($('db-creation-next-action').textContent,t('Setup or verification is running. Status is checked automatically while this screen is visible.'));
 assert.ok(visible.includes(t('SQL connection setup (approval 2/2)')+' · '+t('Running')));
 assert.equal(visible.includes(t('Needs attention')),false);assert.equal(visible.includes(t('Awaiting approval')),false);
 assert.ok(visible.includes(t('Earlier result unknown; current setup or verification is running.')));
 assert.deepEqual(area.descendants().filter(n=>n.tagName==='BUTTON').map(n=>n.id),['db-creation-withdraw']);
 if(progress_code==='VERIFYING'){
  assert.ok(visible.includes(t('Recovery verification is limited to the server-reviewed fixed steps.')));
  assert.equal(area.descendants().some(n=>n.tagName==='LI'&&n.textContent.startsWith(t('Issue connection information')+' · ')),false);
 }
 const history=$('db-creation-process').descendants().filter(n=>n.tagName==='DETAILS');
 assert.ok(history.some(n=>contents(n).includes('full · APPLY · UNKNOWN')));
 assert.ok(history.some(n=>contents(n).includes('full · VERIFY_RECOVERY · UNKNOWN')));
 assert.ok(history.some(n=>contents(n).includes('verify · UNKNOWN')));
 assert.equal(JSON.stringify(dbCreation.process),original);assert.equal(creationPosts.length,0);assert.equal(writeCalls().length,0);
 assert.equal(area.descendants().some(n=>n.id==='db-creation-sql'),false);
}
''')

    def test_current_lifecycle_overrides_historical_creation_progress_on_selection_and_refresh(self):
        for lifecycle, progress in [('retiring', 'READY'), ('retired', 'READY'),
                                    ('RETIRING', 'VERIFYING'), ('ReTiReD', 'FULL_REVIEW_REQUIRED'),
                                    ('REGISTERED', 'READY')]:
            with self.subTest(lifecycle=lifecycle, progress=progress):
                self.javascript(SETUP + '\nconst lifecycle=' + json.dumps(lifecycle)
                                + ',progress=' + json.dumps(progress) + r''';
const history=[plan('full','APPLIED'),plan('full','APPLIED','VERIFY_RECOVERY')];
for(const p of history)p.steps=[{step:'VERIFY_RECOVERY',state:'APPLIED'}];
const grants=['alice','bob','carol'].map(principal_id=>({principal_id,desired_role:'revoked',effective_role:'revoked'}));
creation=process({lifecycle,progress_code:progress,revision:13,next_actions:[],blocker:null,plans:history,initial_grants:grants});
const original=structuredClone(creation),draft=JSON.stringify(dbCreation.draft),sheets=JSON.stringify(worksheets),worksheet=worksheetId;
dbCreation.process=null;await dbCreationLoad(true);
const button=$('db-creation-form').descendants().find(n=>n.tagName==='BUTTON'&&n.textContent==='sales · '+lifecycle.toUpperCase());
assert.ok(button,'list must show the current lifecycle');
const old=adminRoute,held=deferred();let details=0;
adminRoute=(path,options)=>{if(path===C+'/creation'){++details;return held.promise;}return old(path,options);};
const reading=button.onclick();
const assertCurrent=()=>{
 assert.equal($('db-creation-progress').textContent,lifecycle.toUpperCase());
 const box=$('db-creation-process'),text=contents(box),controls=box.descendants().filter(n=>n.tagName==='BUTTON');
 assert.equal(box.descendants().some(n=>n.textContent==='READY'),false);
 assert.equal(box.descendants().some(n=>n.textContent==='Warehouse maintenance; waiting for full approval or verification.'),false);
 assert.equal(controls.some(n=>n.id==='db-creation-sql'),false);
 assert.equal(controls.length,0);
 for(const p of history){
  assert.ok(text.includes(p.phase+' · '+p.purpose+' · APPLIED'));
  assert.ok(text.includes('VERIFY_RECOVERY · APPLIED'));
 }
 for(const g of grants)assert.ok(text.includes(g.principal_id+' · revoked · effective: revoked'));
 assert.ok(text.includes('Platform admin access is independent of desired and effective grants.'));
 assert.deepEqual(dbCreation.process,creation);assert.deepEqual(dbCreation.process.plans,history);assert.deepEqual(dbCreation.process.initial_grants,grants);
 assert.equal(JSON.stringify(dbCreation.draft),draft);assert.equal(JSON.stringify(worksheets),sheets);assert.equal(worksheetId,worksheet);
 assert.equal(creationPosts.length,0);assert.equal(writeCalls().length,0);
 assert.equal(calls.some(c=>c.options?.method==='POST'),false);
};
assertCurrent();await tick();assert.equal(details,1);
held.resolve(structuredClone(creation));await reading;assertCurrent();
adminRoute=old;creation={...creation,display_name:'Refreshed history'};
await $('db-creation-refresh').onclick();assertCurrent();assert.equal(dbCreation.error,'');
assert.deepEqual(original,{...creation,display_name:original.display_name});
''')

    def test_current_ready_and_active_full_progress_display_controls(self):
        self.javascript(SETUP + r'''
const maintenance='Warehouse maintenance; waiting for full approval or verification.';
for(const [progress_code,label] of [['READY','READY'],['VERIFYING','Verifying creation'],['RECONCILE_REQUIRED','Reconciliation required']]){
 show(process({lifecycle:'READY',progress_code,plans:[plan('full','APPLIED')],next_actions:[]}));
 assert.equal($('db-creation-progress').textContent,label);
 assert.equal($('db-creation-process').descendants().some(n=>n.textContent===maintenance),false);
 assert.ok($('db-creation-process').descendants().some(n=>n.id==='db-creation-sql'));
}
for(const [progress_code,label] of [['FULL_REVIEW_REQUIRED','Full plan review required'],['APPLYING','Applying creation'],['VERIFYING','Verifying creation']]){
 show(process({lifecycle:'RECONCILING',progress_code,plans:[plan('full')],next_actions:['approve']}));
 assert.equal($('db-creation-progress').textContent,label);
 assert.ok($('db-creation-process').descendants().some(n=>n.textContent===maintenance));
 assert.equal($('db-creation-process').descendants().some(n=>n.id==='db-creation-sql'),false);
 assert.ok($('db-creation-process').descendants().some(n=>n.id==='db-creation-review-full'));
}
assert.equal(creationPosts.length,0);assert.equal(writeCalls().length,0);
''')

    def test_rediscover_cancels_old_poll_until_list_finishes_then_detail_preserves_list(self):
        self.javascript(TIMER_SETUP + r'''
show(process({lifecycle:'RECONCILING',plans:[plan()],next_actions:['approve']}));
dbCreation.list=[structuredClone(creation),process({creation_id:'second',sql_catalog:'old2'})];dbCreationRender();
await $('db-creation-review-bootstrap').onclick();
$('db-creation-confirm').checked=true;$('db-creation-confirm').onchange();
const selected=dbCreation.process,review=dbCreation.review,draft=JSON.stringify(dbCreation.draft),sheets=JSON.stringify(worksheets);
const labels=()=>$('db-creation-form').descendants().filter(n=>n.tagName==='BUTTON').map(n=>n.textContent);
assert.ok(labels().includes('old2 · REGISTERED'));
dbCreationSchedule();const oldTimer=dbCreation.timer;assert.equal(timers.get(oldTimer).due,5000);
const held=deferred(),old=adminRoute;let listReads=0,detailReads=0;
adminRoute=(path,options)=>{
 if(path===C){++listReads;return held.promise;}
 if(path===C+'/creation')++detailReads;
 return old(path,options);
};
const reading=$('db-creation-discover').onclick();
assert.equal(timers.has(oldTimer),false);assert.equal(dbCreation.timer,null);
await tick();assert.equal(listReads,1);assert.equal(timers.size,0);
await advanceTimers(5000);assert.equal(detailReads,0);assert.equal(timers.size,0);
assert.equal(dbCreation.process,selected);assert.equal(dbCreation.review,review);
const updated=[process({sql_catalog:'updated1',lifecycle:'READY'}),
 process({creation_id:'second',sql_catalog:'updated2',lifecycle:'READY'}),
 process({creation_id:'third',sql_catalog:'updated3',lifecycle:'REGISTERED'})];
held.resolve({creations:updated,cursor:'next'});await reading;
assert.deepEqual(dbCreation.list,updated);assert.equal(dbCreation.cursor,'next');
for(const label of ['updated1 · READY','updated2 · READY','updated3 · REGISTERED'])assert.ok(labels().includes(label));
assert.equal(labels().includes('old2 · REGISTERED'),false);assert.equal(dbCreation.error,'');assert.equal($('db-creation-status').textContent,'');
assert.equal(dbCreation.process,selected);assert.equal(dbCreation.review,review);
assert.equal($('db-creation-approve').disabled,true); // A rerender never carries checkbox consent forward.
assert.equal(JSON.stringify(dbCreation.draft),draft);assert.equal(JSON.stringify(worksheets),sheets);
assert.equal(timers.size,1);assert.equal(timers.get(dbCreation.timer).due,timerNow+5000);
creation=process({revision:4,lifecycle:'READY',next_actions:[]});
await advanceTimers(4999);assert.equal(detailReads,0);
await advanceTimers(1);assert.equal(detailReads,1);assert.equal(listReads,1);
assert.equal(dbCreation.process.lifecycle,'READY');assert.equal(dbCreation.review,null);
assert.deepEqual(dbCreation.list,updated);assert.ok(labels().includes('updated3 · REGISTERED'));
assert.equal(dbCreation.error,'');assert.equal(timers.size,1);assert.equal(timers.get(dbCreation.timer).due,timerNow+5000);
assert.equal(JSON.stringify(dbCreation.draft),draft);assert.equal(JSON.stringify(worksheets),sheets);
assert.equal(creationPosts.length,0);assert.equal(writeCalls().length,0);
''')

    def test_overlapping_explicit_loads_only_current_completion_reschedules_poll(self):
        self.javascript(TIMER_SETUP + r'''
show(process());dbCreationSchedule();const first=deferred(),second=deferred(),old=adminRoute;let reads=0;
adminRoute=(path,options)=>path===C?(++reads===1?first.promise:second.promise):old(path,options);
const earlier=$('db-creation-discover').onclick();await tick();
const current=$('db-creation-discover').onclick();await tick();assert.equal(reads,2);
first.resolve({creations:[process({sql_catalog:'superseded'})]});await earlier;
assert.equal(timers.size,0);assert.equal(dbCreation.list.some(p=>p.sql_catalog==='superseded'),false);
await advanceTimers(5000);assert.equal(timers.size,0);
const latest=[process({sql_catalog:'latest'})];second.resolve({creations:latest});await current;
assert.deepEqual(dbCreation.list,latest);assert.equal(timers.size,1);const scheduled=dbCreation.timer;
// A stale response arriving after the current completion also cannot replace its list or timer.
const third=deferred(),fourth=deferred();reads=0;
adminRoute=(path,options)=>path===C?(++reads===1?third.promise:fourth.promise):old(path,options);
const stale=$('db-creation-discover').onclick();await tick();assert.equal(timers.has(scheduled),false);
const fresh=$('db-creation-discover').onclick();await tick();fourth.resolve({creations:latest});await fresh;
const freshTimer=dbCreation.timer;third.resolve({creations:[]});await stale;
assert.deepEqual(dbCreation.list,latest);assert.equal(dbCreation.timer,freshTimer);assert.equal(timers.size,1);
assert.equal(dbCreation.error,'');assert.equal(creationPosts.length,0);assert.equal(writeCalls().length,0);
''')

    def test_list_selection_renders_blocked_creation_before_identical_detail_response(self):
        self.javascript(r'''
globalThis.location={href:'http://localhost/#workbench?keep=yes',origin:'http://localhost'};
globalThis.history={replaceState:(_,__,url)=>{location.href=String(url);}};
''' + SETUP + r'''
show(process({creation_id:'p54cli',sql_catalog:'p54cli',lifecycle:'READY',progress_code:'READY',next_actions:[]}));
dbCreationRememberURL('p54cli');
const stopped=process({creation_id:'p54stop',sql_catalog:'p54stop',lifecycle:'REGISTERED',progress_code:'BLOCKED',
 blocker:'PRINCIPAL_STOPPED',next_actions:['withdraw','reconcile']});
dbCreation.list=[structuredClone(creation),stopped];dbCreationRender();
assert.ok($('db-creation-process').descendants().some(n=>n.id==='db-creation-sql'));
assert.equal($('db-creation-heading').textContent,'p54cli');
dbCreation.review={token:dbCreationActionToken()};
const sheets=JSON.stringify(worksheets),worksheet=worksheetId,held=deferred(),old=adminRoute;let details=0;
adminRoute=(path,options)=>{if(path===C+'/p54stop'){++details;return held.promise;}return old(path,options);};
const button=$('db-creation-form').descendants().find(n=>n.tagName==='BUTTON'&&n.textContent==='p54stop · REGISTERED');
assert.ok(button);const reading=button.onclick();
const assertSelected=()=>{
 assert.equal(dbCreation.process.creation_id,'p54stop');assert.equal(dbCreation.review,null);
 assert.equal($('db-creation-heading').textContent,'p54stop');assert.equal($('db-creation-progress').textContent,'Creation blocked');
 assert.match(contents($('db-creation-process')),/PRINCIPAL_STOPPED/);
 const controls=$('db-creation-process').descendants().filter(n=>n.tagName==='BUTTON').map(n=>n.id);
 assert.ok(controls.includes('db-creation-withdraw'));assert.ok(controls.includes('db-creation-reconcile'));
 for(const id of ['db-creation-sql','db-creation-bootstrap','db-creation-full','db-creation-replan','db-creation-approve'])assert.equal(controls.includes(id),false);
 const url=new URL(location.href),params=new URLSearchParams(url.hash.split('?')[1]);
 assert.equal(params.get('creation'),'p54stop');assert.equal(params.get('creation_environment'),'envA');assert.equal(params.get('keep'),'yes');
 assert.equal(url.search,'');assert.equal(JSON.stringify(worksheets),sheets);assert.equal(worksheetId,worksheet);
 assert.equal(creationPosts.length,0);assert.equal(writeCalls().length,0);
 assert.equal(calls.some(c=>c.path.startsWith('/api/v1/queries')&&c.options?.method==='POST'),false);
};
assertSelected();const heading=$('db-creation-heading');await tick();assert.equal(details,1);assertSelected();
held.resolve(structuredClone(stopped));await reading;
assert.deepEqual(dbCreation.process,stopped);assert.equal($('db-creation-heading'),heading);assertSelected();assert.equal(dbCreation.error,'');
''')

    def test_generated_fragment_url_reloads_native_static_root_without_query(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
        import workbench_server
        import request_auth
        source = '''
globalThis.location={href:'http://localhost/',origin:'http://localhost'};
globalThis.history={replaceState:(_,__,url)=>{location.href=String(url);}};
const dbCreation={environment_id:'envA'};function dbCreationSave(){}
''' + admin.harness.function('dbCreationURL') + '\n' + admin.harness.function('dbCreationRememberURL') + '''
dbCreationRememberURL('creation');console.log(location.href);
'''
        result = subprocess.run(['node','-e',source],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        generated = result.stdout.strip()
        self.assertEqual(generated,'http://localhost/#creation=creation&creation_environment=envA')
        # urllib, like a browser, strips the fragment from the HTTP selector.
        selector = Request(generated).selector
        self.assertEqual(selector,'/')
        class Connection:
            def __init__(self,data):
                self.incoming,self.outgoing=io.BytesIO(data),io.BytesIO()
            def makefile(self,mode,*args):
                return self.incoming if 'r' in mode else self.outgoing
            def sendall(self,data):
                self.outgoing.write(data)
        def request(target):
            connection=Connection(('GET '+target+' HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n').encode())
            with patch.object(workbench_server.WorkbenchHandler,'log_message'),\
                    patch('runtime_profile.active',return_value=True),\
                    patch.object(request_auth,'authenticate_principal',return_value=SimpleNamespace(principal_id='owner',maximum_role='admin',platform_admin=True)):
                workbench_server.WorkbenchHandler(connection,('127.0.0.1',1),SimpleNamespace())
            response=HTTPResponse(Connection(connection.outgoing.getvalue()));response.begin()
            return response.status,response.read()
        status,body=request(selector)
        self.assertEqual(status,200)
        self.assertIn(b'<html',body.lower())
        status,body=request('/?creation=creation&creation_environment=envA')
        self.assertEqual(status,400)
        self.assertEqual(json.loads(body)['code'],'INVALID_INPUT')

    def test_fragment_helpers_preserve_unrelated_route_and_hash_state(self):
        self.javascript(SETUP + r'''
globalThis.history={replaceState:(_,__,url)=>{location.href=String(url);}};
for(const hash of ['','#worksheet-a','#sql','#worksheet-a?filter=one%20two','#filter=one%20two&pane=sql']){
 globalThis.location={href:'http://localhost/workbench?view=sql&locale=ja'+hash,origin:'http://localhost'};
 const original=new URL(location.href);dbCreationRememberURL('creation');const saved=new URL(location.href);
 assert.equal(saved.pathname,original.pathname);assert.equal(saved.searchParams.get('view'),'sql');assert.equal(saved.searchParams.get('locale'),'ja');
 assert.equal(saved.searchParams.has('creation'),false);assert.equal(saved.searchParams.has('creation_environment'),false);
 assert.equal(dbCreationURL().id,'creation');assert.equal(dbCreationURL().environment,'envA');
 dbCreationURL(null);const cleared=new URL(location.href);
 assert.equal(dbCreationURL().id,null);assert.equal(cleared.pathname,original.pathname);assert.equal(cleared.search,original.search);
 if(!hash.includes('='))assert.equal(cleared.hash,hash);
 else{const params=new URLSearchParams(cleared.hash.slice(cleared.hash.includes('?')?cleared.hash.indexOf('?')+1:1));assert.equal(params.get('filter'),'one two');assert.equal(params.has('creation'),false);if(hash.startsWith('#worksheet-a'))assert.ok(cleared.hash.startsWith('#worksheet-a?'));else assert.equal(params.get('pane'),'sql');}
}
assert.equal(creationPosts.length,0);dbCreationStop();
''')

    def test_initialization_and_reload_activate_creation_url_once_get_only_preserving_worksheets(self):
        self.javascript(SETUP + r'''
$('sql').value='SELECT 保存したドラフト';dbSave();setLanguage('ja');const original=structuredClone(worksheets),selected=worksheetId,role=currentRole;
globalThis.location={href:'http://localhost/workbench?view=sql#worksheet-a?creation=creation&creation_environment=envA',origin:'http://localhost'};
globalThis.history={replaceState:(_,__,url)=>{location.href=String(url);}};
for(let reload=0;reload<2;reload++){
 dbCreationClear();activeTab='warehouse';const before=calls.length;
 await initializeDatabaseUI(session);await tick();
 assert.equal(activeTab,'db-admin');assert.equal($('db-admin').hidden,false);assert.equal($('tab-db-admin').getAttribute('aria-selected'),'true');
 assert.equal(dbCreation.process.creation_id,'creation');assert.equal(dbCreation.environment_id,'envA');assert.match(contents($('db-creation-process')),/sales/);
 assert.equal(calls.slice(before).filter(c=>c.path==='/api/v1/admin/databases').length,1);
 assert.equal(calls.slice(before).some(c=>c.path===C+'/creation'),true);
 assert.equal(calls.slice(before).every(c=>!c.options?.method||c.options.method==='GET'),true);
 assert.deepEqual(worksheets,original);assert.equal(worksheetId,selected);assert.equal($('sql').value,'SELECT 保存したドラフト');assert.equal(currentRole,role);assert.equal(language,'ja');
 assert.equal(new URL(location.href).searchParams.get('view'),'sql');assert.equal(new URL(location.href).searchParams.has('creation'),false);
 assert.equal(new URL(location.href).hash,'#worksheet-a?creation=creation&creation_environment=envA');
}
assert.equal(creationPosts.length,0);dbCreationStop();
''')

    def test_initial_creation_selection_waits_for_detail_and_does_not_follow_hash_after_sql_open(self):
        for selection in [
            '#creation=creation&creation_environment=envA',
            '?creation=creation&creation_environment=envA',
        ]:
            with self.subTest(selection=selection):
                self.javascript(SETUP + 'const selection=' + json.dumps(selection) + r''';
globalThis.location={href:'http://localhost/workbench'+selection,origin:'http://localhost'};
globalThis.history={replaceState:(_,__,url)=>{location.href=String(url);}};
dbCreationClear();activeTab='warehouse';const held=deferred(),old=adminRoute;let initialized=false;
adminRoute=(path,options)=>path===C+'/creation'?held.promise:old(path,options);
const initializing=initializeDatabaseUI(session).then(()=>{initialized=true;});await tick();
assert.equal(activeTab,'db-admin');assert.equal(initialized,false);
held.resolve(process({lifecycle:'READY',progress_code:'READY',next_actions:[]}));await initializing;
assert.equal(dbCreation.process.creation_id,'creation');assert.equal(initialized,true);
await $('db-creation-sql').onclick();assert.equal(activeTab,'warehouse');
const href=location.href;await periodicRefresh();await tick();assert.equal(activeTab,'warehouse');
assert.equal(location.href,href);assert.equal(writeCalls().length,0);dbCreationStop();
''')

    def test_initial_creation_url_without_matching_environment_only_discovers_current_list(self):
        for environment in ['', '&creation_environment=envB']:
            self.javascript(SETUP + r'''
globalThis.location={href:'http://localhost/workbench#sql?creation=creation''' + environment + r'''',origin:'http://localhost'};
dbCreationClear();activeTab='warehouse';const before=calls.length;
await initializeDatabaseUI(session);await tick();
assert.equal(activeTab,'db-admin');assert.equal(dbCreation.process,null);assert.equal(dbCreation.environment_id,'envA');
assert.equal(dbCreation.list.some(p=>p.creation_id==='creation'),true);assert.equal(calls.slice(before).some(c=>c.path===C),true);
assert.equal(calls.slice(before).some(c=>c.path===C+'/creation'),false);assert.equal(creationPosts.length,0);
assert.equal(calls.slice(before).every(c=>!c.options?.method||c.options.method==='GET'),true);dbCreationStop();
''')

    def test_initial_creation_url_nonadmin_or_invalid_id_stays_in_worksheet(self):
        for session_change, creation_id in [
            ('session.platform_admin=false', 'creation'),
            ("session.maximum_role='data_engineer';currentRole='data_engineer'", 'creation'),
            ("session.maximum_role='bi_viewer';currentRole='bi_viewer'", 'creation'),
            ('', 'invalid%2Fid'),
            ('', ''),
        ]:
            self.javascript(SETUP + r'''
$('sql').value='SELECT untouched';dbSave();setLanguage('ja');const beforeWorksheets=structuredClone(worksheets);
globalThis.location={href:'http://localhost/workbench#creation=''' + creation_id + r'''&creation_environment=envA',origin:'http://localhost'};
dbCreationClear();activeTab='warehouse';''' + session_change + r''';const before=calls.length;
await initializeDatabaseUI(session);await tick();assert.equal(activeTab,'warehouse');
assert.equal(calls.slice(before).some(c=>c.path.startsWith(C)),false);assert.equal(creationPosts.length,0);
assert.deepEqual(worksheets,beforeWorksheets);assert.equal($('sql').value,'SELECT untouched');assert.equal(language,'ja');
''')

    def test_ordinary_initialization_without_creation_keeps_worksheet_and_does_not_read_creations(self):
        self.javascript(SETUP + r'''
globalThis.location={href:'http://localhost/workbench?view=sql#worksheet-a',origin:'http://localhost'};
dbCreationClear();activeTab='warehouse';$('sql').value='SELECT untouched';dbSave();
const original=structuredClone(worksheets),before=calls.length;await initializeDatabaseUI(session);
assert.equal(activeTab,'warehouse');assert.deepEqual(worksheets,original);assert.equal($('sql').value,'SELECT untouched');
assert.equal(calls.slice(before).some(c=>c.path.startsWith(C)),false);assert.equal(writeCalls().length,0);
''')

    def test_empty_grants_literal_metadata_back_navigation_and_request_shape(self):
        self.javascript(SETUP + r'''
begin();await $('db-creation-next').onclick();assert.equal(document.activeElement.id,'db-creation-heading');
assert.equal(creationPosts.length,0);await $('db-creation-next').onclick();
assert.match(contents($('db-creation-form')),/名前 <script> 🐻/);
await $('db-creation-back').onclick();await $('db-creation-back').onclick();
assert.equal($('db-creation-sql_catalog').value,'new_sales');
await $('db-creation-next').onclick();await $('db-creation-next').onclick();await $('db-creation-submit').onclick();
assert.equal(creationPosts.length,1);const call=creationPosts[0];assert.equal(call.path,C);
assert.deepEqual(JSON.parse(call.options.body),{sql_catalog:'new_sales',display_name:'名前 <script> 🐻',description:'説明 <img>',initial_grants:[]});
assert.equal(call.options.headers['X-Shirokuma-Role'],'admin');assert.ok(call.options.headers['Idempotency-Key']);
assert.equal(dbCreation.process.lifecycle,'REGISTERED');
''')

    def test_principal_role_ceiling_and_duplicate_prevention(self):
        self.javascript(SETUP + r'''
begin();await $('db-creation-next').onclick();$('db-creation-principal').value='alice';$('db-creation-principal').onchange();
assert.deepEqual($('db-creation-role').options.map(o=>o.value),['bi_viewer','data_engineer']);
$('db-creation-role').value='admin';await $('db-creation-add').onclick();assert.equal(dbCreation.draft.initial_grants.length,0);
$('db-creation-role').value='data_engineer';await $('db-creation-add').onclick();
assert.deepEqual(dbCreation.draft.initial_grants,[{principal_id:'alice',maximum_role:'data_engineer'}]);
assert.equal($('db-creation-principal').options.some(o=>o.value==='alice'),false);
''')

    def test_distinct_plan_review_approval_and_no_automatic_posts(self):
        self.javascript(SETUP + r'''
show(process());await $('db-creation-bootstrap').onclick();assert.deepEqual(JSON.parse(creationPosts[0].options.body),{expected_revision:3,phase:'bootstrap'});
show(process({plans:[plan()],next_actions:['approve']}));assert.equal(creationPosts.length,1);
await $('db-creation-review-bootstrap').onclick();assert.equal($('db-creation-approve').disabled,true);
$('db-creation-confirm').checked=true;$('db-creation-confirm').onchange();await $('db-creation-approve').onclick();
assert.equal(creationPosts[1].path,C+'/creation/phases/bootstrap/approve');
assert.deepEqual(JSON.parse(creationPosts[1].options.body),{expected_revision:3,plan_id:'bootstrap-plan',operation_id:'bootstrap-op',expected_operation_revision:2,delivery_digest:'digest-bootstrap'});
show(process({plans:[plan('bootstrap','PREPARED')],next_actions:['plans']}));await $('db-creation-full').onclick();
assert.deepEqual(JSON.parse(creationPosts[2].options.body),{expected_revision:3,phase:'full'});
show(process({plans:[plan('bootstrap','PREPARED'),plan('full')],next_actions:['approve']}));
await $('db-creation-review-full').onclick();assert.equal($('db-creation-confirm').checked,undefined);
$('db-creation-confirm').checked=true;$('db-creation-confirm').onchange();await $('db-creation-approve').onclick();
assert.equal(creationPosts[3].path,C+'/creation/phases/full/approve');
const count=creationPosts.length;await dbCreationLoad();await dbCreationLoad(true);await periodicRefresh();assert.equal(creationPosts.length,count);
assert.notEqual(dbCreation.process.lifecycle,'READY');
''')

    def test_uncertain_request_persisted_before_post_and_exact_retry_after_reload(self):
        self.javascript(SETUP + r'''
begin();await $('db-creation-next').onclick();await $('db-creation-next').onclick();
creationRoute=async()=>{assert.equal(JSON.parse(storage.get(dbCreationStorageKey())).pending.body,creationPosts[0].options.body);throw Error('connection lost');};
await $('db-creation-submit').onclick();const first=creationPosts[0];assert.ok(dbCreation.pending);
await dbCreationLoad();assert.equal(creationPosts.length,1);
dbCreation.pending=null;dbCreation.scope='';dbCreationRestore();assert.ok(dbCreation.pending);await dbCreationLoad();assert.equal(creationPosts.length,1);
creationRoute=async()=>({creation_id:'creation',request_id:'ack',accepted_revision:3});
await $('db-creation-retry').onclick();assert.equal(creationPosts.length,2);
assert.equal(creationPosts[1].path,first.path);assert.equal(creationPosts[1].options.body,first.options.body);
assert.equal(creationPosts[1].options.headers['Idempotency-Key'],first.options.headers['Idempotency-Key']);assert.equal(dbCreation.pending,null);
''')

    def test_stale_authority_ignored_inventory_and_management_selection_do_not_invalidate(self):
        for change in ["dbPrincipal='[\"other\",true]'", "currentRole='data_engineer'", "dbAdminClear()"]:
            self.javascript(SETUP + r'''
show(process());const held=deferred();creationRoute=()=>held.promise;
const posting=dbCreationMutate('/creation/plans',{expected_revision:3,phase:'bootstrap'});await tick();
''' + change + r''';held.resolve({creation_id:'stale',request_id:'ack',accepted_revision:3});await posting;
assert.notEqual(dbCreation.process?.creation_id,'stale');
''')
        self.javascript(SETUP + r'''
show(process());const scope=dbCreationScope();++dbInventoryVersion;dbAdminSelect('finance');assert.equal(dbCreationScope(),scope);
await dbCreationMutate('/creation/plans',{expected_revision:3,phase:'bootstrap'});assert.equal(creationPosts.length,1);
''')

    def test_executor_stop_403_keeps_admin_and_process_and_transport_retry_clears(self):
        self.javascript(SETUP + r'''
show(process());creationRoute=async()=>({http:403,body:{error:'PRINCIPAL_STOPPED'}});
await dbCreationMutate('/creation/plans',{expected_revision:3,phase:'bootstrap'});
assert.equal(dbAdminAllowed(),true);assert.equal(dbCreation.process.progress_code,'STOPPED');assert.match(contents($('db-creation-process')),/Creation stopped/);
creationRoute=null;const old=adminRoute;adminRoute=async(path,options)=>{if(path===C+'/creation')throw Error('cutover');return old(path,options);};
await dbCreationLoad();assert.match($('db-creation-status').textContent,/Connection unavailable/);assert.equal(dbCreation.process.creation_id,'creation');
adminRoute=old;await dbCreationLoad();assert.equal(dbCreation.error,'');assert.doesNotMatch($('db-creation-status').textContent,/Connection unavailable/);
''')

    def test_unknown_recovery_dedicated_review_and_other_admin_read_only(self):
        self.javascript(SETUP + r'''
show(process({plans:[plan('full','UNKNOWN','VERIFY_RECOVERY')],next_actions:['recover-verify','reconcile'],progress_code:'RECONCILE_REQUIRED'}));
await $('db-creation-review-full-recovery').onclick();$('db-creation-confirm').checked=true;$('db-creation-confirm').onchange();await $('db-creation-approve').onclick();
assert.equal(creationPosts[0].path,C+'/creation/phases/full/recover-verify');assert.equal(JSON.parse(creationPosts[0].options.body).recovery_digest,'recovery');
show(process({requester_id:'other',plans:[plan()],next_actions:['approve','plans']}));const before=creationPosts.length;
await dbCreationMutate('/creation/plans',{expected_revision:3,phase:'bootstrap'});assert.equal(creationPosts.length,before);
assert.match(contents($('db-creation-process')),/Only the original requester/);
''')

    def test_canonical_limited_recovery_requires_fresh_explicit_full_approval(self):
        self.javascript(SETUP + r'''
const bootstrap=plan('bootstrap','PREPARED'),full=plan('full');
show(process({plans:[bootstrap,full],next_actions:['approve'],progress_code:'FULL_REVIEW_REQUIRED'}));
await $('db-creation-review-full').onclick();
$('db-creation-confirm').checked=true;$('db-creation-confirm').onchange();
const oldApprove=$('db-creation-approve'),oldReview=dbCreation.review;
const recovery={...plan('full','UNKNOWN','VERIFY_RECOVERY'),plan_id:'full-recovery',
 public_summary:{code:'RECOVERY_READY',impact:'VERIFY_RECOVERY_LIMITED',fixed_steps:['verify']},
 steps:[{step:'verify',state:'UNKNOWN'}]};
const unresolved=process({revision:4,plans:[bootstrap,{...full,state:'UNKNOWN',steps:[{step:'verify',state:'UNKNOWN'}]},recovery],
 progress_code:'RECONCILE_REQUIRED',next_actions:['recover-verify','reconcile']});
const history=JSON.stringify(unresolved);
creation=structuredClone(unresolved);await dbCreationLoad();
assert.equal(dbCreation.review,null);await oldApprove.onclick();assert.equal(creationPosts.length,0);
const ordinaryBody={expected_revision:4,plan_id:full.plan_id,operation_id:full.operation_id,
 expected_operation_revision:full.operation_revision,delivery_digest:full.delivery_digest};
assert.equal(dbCreationCanMutate('/creation/phases/full/approve',ordinaryBody),false);
await dbCreationMutate('/creation/phases/full/approve',ordinaryBody);assert.equal(creationPosts.length,0);
await $('db-creation-review-full-recovery').onclick();
assert.match(contents($('db-creation-process')),/VERIFY_RECOVERY_LIMITED/);
const recoveryApprove=$('db-creation-approve');assert.equal(recoveryApprove.disabled,true);
await recoveryApprove.onclick();assert.equal(creationPosts.length,0);
creationRoute=async(path,options)=>{
 const body=JSON.parse(options.body);
 if(path.endsWith('/recover-verify')){
  assert.deepEqual(body,{expected_revision:4,plan_id:recovery.plan_id,operation_id:full.operation_id,
   expected_operation_revision:2,delivery_digest:full.delivery_digest,recovery_digest:'recovery'});
  // Canonical GET projects the shared Operation onto BOTH plans. Private UNKNOWN history remains intact.
  creation={...structuredClone(unresolved),revision:5,progress_code:'FULL_REVIEW_REQUIRED',next_actions:['approve','reconcile']};
  for(const p of creation.plans)if(p.operation_id===full.operation_id){
   p.state='AWAITING_APPLY';p.operation_revision=3;p.steps=[{step:'verify',state:'SUCCEEDED'}];
  }
 }else{
  assert.equal(path,C+'/creation/phases/full/approve');
  assert.deepEqual(body,{expected_revision:5,plan_id:full.plan_id,operation_id:full.operation_id,
   expected_operation_revision:3,delivery_digest:full.delivery_digest});
  creation={...creation,revision:6,lifecycle:'READY',progress_code:'READY',next_actions:[]};
 }
 return {creation_id:'creation',request_id:'ack-'+creationPosts.length,accepted_revision:creation.revision};
};
$('db-creation-confirm').checked=true;$('db-creation-confirm').onchange();await recoveryApprove.onclick();
assert.equal(creationPosts.length,1);assert.equal(creationPosts[0].path,C+'/creation/phases/full/recover-verify');
assert.equal(JSON.stringify(unresolved),history);assert.equal(oldReview.plan.state,'AWAITING_APPLY');
assert.equal(dbCreation.process.progress_code,'FULL_REVIEW_REQUIRED');assert.notEqual(dbCreation.process.lifecycle,'READY');
assert.deepEqual(dbCreation.process.plans.filter(p=>p.operation_id===full.operation_id).map(p=>p.state),['AWAITING_APPLY','AWAITING_APPLY']);
assert.equal(dbCreation.review,null);await oldApprove.onclick();await recoveryApprove.onclick();
await dbCreationLoad();assert.equal(creationPosts.length,1);assert.equal(dbCreation.review,null);
await $('db-creation-review-full').onclick();assert.match(contents($('db-creation-process')),/FULL_RUNTIME_HANDOFF/);
const freshApprove=$('db-creation-approve');assert.equal(freshApprove.disabled,true);
await freshApprove.onclick();assert.equal(creationPosts.length,1);
$('db-creation-confirm').checked=true;$('db-creation-confirm').onchange();await freshApprove.onclick();
assert.equal(creationPosts.length,2);assert.equal(dbCreation.process.progress_code,'READY');
assert.equal(dbCreation.process.lifecycle,'READY');assert.equal(JSON.stringify(unresolved),history);
assert.notEqual(creationPosts[0].options.headers['Idempotency-Key'],creationPosts[1].options.headers['Idempotency-Key']);
assert.deepEqual(writeCalls().map(c=>c.path),[C+'/creation/phases/full/recover-verify',C+'/creation/phases/full/approve']);
dbCreationStop();
''')

    def test_ready_new_worksheet_preserves_drafts_contexts_and_never_executes(self):
        self.javascript(SETUP + r'''
show(process({lifecycle:'READY',progress_code:'READY',next_actions:[]}));$('sql').value='SELECT preserved';
const original=structuredClone(dbCurrent()),count=worksheets.length;
await $('db-creation-sql').onclick();assert.equal(worksheets.length,count+1);
assert.equal(worksheets[0].sql,'SELECT preserved');for(const key of ['database_id','schema_id','binding_revision','generation_id','selected_role'])assert.equal(worksheets[0][key],original[key]);
assert.equal(dbCurrent().database_id,'db');assert.equal(dbCurrent().schema_id,'raw');assert.equal(dbCurrent().mode,'read-only');assert.equal(dbCurrent().sql,'');
assert.equal(calls.some(c=>c.path==='/api/v1/queries'&&c.options?.method==='POST'),false);
show(process({lifecycle:'READY',progress_code:'READY',access_source:'none'}));assert.match(contents($('db-creation-process')),/SQL use unavailable/);
''')

    def test_ready_handoff_awaits_one_production_wrapper_refresh_with_fresh_reads(self):
        self.javascript(SETUP + r'''
show(process({lifecycle:'READY',progress_code:'READY',next_actions:[]}));
$('sql').value='SELECT preserved draft';dbSave();const original=structuredClone(worksheets),explorer=structuredClone(dbExplorer);
const refreshContext=dbRefreshContext,held=deferred();let refreshes=0,settled=false;
dbRefreshContext=()=>{refreshes++;return refreshContext();};
sessionRoute=()=>refreshes===1?held.promise:structuredClone(session);
calls.length=0;const opening=dbCreationSQL().then(()=>{settled=true;});await tick();
assert.equal(refreshes,1);assert.equal(settled,false);
assert.equal(worksheets.length,original.length+1);assert.equal(dbWorkspacePane,'worksheet');assert.equal(activeTab,'warehouse');
assert.ok($('worksheet-tabs').descendants().some(node=>node.dataset.worksheet===worksheetId));
held.resolve(structuredClone(session));await opening;
assert.equal(refreshes,1);assert.equal(settled,true);
assert.deepEqual(calls.map(c=>c.path),[
 '/api/session','/api/v1/databases','/api/session','/api/v1/databases/db/schemas',
 '/api/session','/api/v1/databases','/api/v1/databases/db/schemas','/api/v1/queries?database_id=db']);
for(const c of calls.filter(c=>c.path!=='/api/session'))assert.equal(c.options.headers['X-Shirokuma-Role'],'admin');
assert.deepEqual(worksheets.slice(0,-1),original);assert.deepEqual(dbExplorer,explorer);
assert.deepEqual(JSON.parse(storage.get(dbWorksheetKey)).tabs,worksheets);
assert.equal(dbCurrent().database_id,'db');assert.equal(dbCurrent().schema_id,'raw');
assert.equal(dbCurrent().binding_revision,1);assert.equal(dbCurrent().generation_id,'gen');assert.equal(dbCurrent().selected_role,'admin');
assert.equal(dbContext(dbCurrent()).sql_schema,'raw');assert.equal(dbSchemaScope,dbSchemaKey('admin','db'));
assert.equal($('sql').value,'');assert.equal(document.activeElement,$('sql'));assert.equal(writeCalls().length,0);dbCreationStop();
''')

    def test_held_handoff_refresh_cannot_overwrite_changed_context_or_steal_focus(self):
        for stage in ['session', 'inventory', 'schemas']:
            for change in ['role', 'context', 'worksheet', 'details', 'tab']:
                with self.subTest(stage=stage, change=change):
                    self.javascript(SETUP + 'const stage=' + json.dumps(stage) + ';const change=' + json.dumps(change) + r''';
show(process({lifecycle:'READY',progress_code:'READY',next_actions:[]}));
$('sql').value='SELECT saved draft';dbSave();const saved=structuredClone(worksheets);
inventory.push({...inventory[0],id:'finance',sql_catalog:'finance'});
const refreshContext=dbRefreshContext,held=deferred();let refreshes=0,waiting=false;
dbRefreshContext=()=>{refreshes++;return refreshContext();};
const hold=(kind,value)=>{if(stage===kind&&refreshes===1){waiting=true;return held.promise;}return value;};
sessionRoute=()=>hold('session',structuredClone(session));
inventoryRoute=()=>hold('inventory',{items:structuredClone(inventory)});
schemasRoute=path=>hold('schemas',{items:structuredClone(path.includes('/finance/')?[financeSchema]:schemas)});
const opening=dbCreationSQL();await tick();assert.equal(waiting,true);assert.equal(refreshes,1);
const added=worksheets.at(-1);
if(change==='role'){currentRole='bi_viewer';dbApplyRole();await dbRefreshContext();}
if(change==='context'){dbCurrent().database_id='finance';dbCurrent().schema_id=financeSchema.id;dbSave();await dbRefreshContext();}
if(change==='worksheet'){dbSave();worksheetId=saved[0].id;$('sql').value=dbCurrent().sql;dbPersist();await renderWorksheets();}
if(change==='details')dbShowWorkspace('details');
if(change==='tab')activate($('tab-compute'));
const focus=$('catalog-filter');focus.focus();
const before=structuredClone(worksheets),explorer=structuredClone(dbExplorer),scope=dbSchemaScope,knownSchemas=structuredClone(dbSchemas),draft=$('sql').value;
held.resolve(stage==='session'?structuredClone(session):stage==='inventory'?{items:structuredClone(inventory)}:{items:structuredClone(schemas)});
await opening;
assert.deepEqual(worksheets,before);assert.deepEqual(worksheets[0],saved[0]);assert.deepEqual(dbExplorer,explorer);
assert.equal($('sql').value,draft);assert.equal(document.activeElement,focus);
if(['role','context','worksheet'].includes(change)){assert.equal(dbSchemaScope,scope);assert.deepEqual(dbSchemas,knownSchemas);}
assert.equal(added.sql,'');assert.equal(writeCalls().length,0);dbCreationStop();
''')

    def test_ready_sql_prefers_permitted_raw_in_unsorted_schema_response(self):
        for order in [[0, 1], [1, 0]]:
            with self.subTest(order=order):
                self.javascript(SETUP + 'const order=' + json.dumps(order) + r''';
schemas=order.map(i=>schemas[i]);
show(process({lifecycle:'READY',progress_code:'READY',next_actions:[]}));
const explorer=structuredClone(dbExplorer);$('sql').value='SELECT untouched';dbSave();const original=structuredClone(worksheets);
await dbCreationSQL();assert.equal(dbCurrent().schema_id,'raw');assert.equal(dbCurrent().mode,'read-only');
assert.equal(dbCurrent().sql,'');assert.deepEqual(worksheets.slice(0,-1),original);assert.deepEqual(dbExplorer,explorer);
assert.equal(writeCalls().length,0);dbCreationStop();
''')

    def test_ready_sql_falls_back_to_first_permitted_schema_when_raw_unavailable(self):
        for change in [
            "schemas=schemas.filter(s=>s.sql_name!=='raw')",
            "schemas[1].capabilities.query=false",
            "schemas[1].allowed_modes=['sandbox-write']",
            "schemas[1].database_id='foreign-db'",
        ]:
            with self.subTest(change=change):
                self.javascript(SETUP + change + r''';
show(process({lifecycle:'READY',progress_code:'READY',next_actions:[]}));
const before=worksheets.length;await dbCreationSQL();assert.equal(worksheets.length,before+1);
assert.equal(dbCurrent().schema_id,'schema');assert.equal(dbContext(dbCurrent()).sql_schema,'marts');
assert.equal(dbCurrent().mode,'read-only');assert.equal(writeCalls().length,0);dbCreationStop();
''')

    def test_ready_sql_ignores_schema_response_after_scope_or_inventory_changes(self):
        for change in [
            "dbCreation.process=process({database_id:'other'})",
            "currentRole='bi_viewer';dbApplyRole()",
            "dbCreation.environment_id='envB'",
            "session.username='other';dbPrincipal=JSON.stringify(['other',true])",
            "dbAdminSession({...session,platform_admin:false})",
            "inventory=inventory.map(d=>({...d,state:'RETIRED',revision:d.revision+1}));dbApplyInventory(await dbReadInventory(currentRole),currentRole)",
        ]:
            with self.subTest(change=change):
                self.javascript(SETUP + r'''
show(process({lifecycle:'READY',progress_code:'READY',next_actions:[]}));
$('sql').value='SELECT untouched';dbSave();const original=structuredClone(worksheets),held=deferred();
schemasRoute=()=>held.promise;const opening=dbCreationSQL();await tick();
''' + change + r''';held.resolve({items:structuredClone(schemas)});await opening;
assert.deepEqual(worksheets,original);assert.equal($('sql').value,'SELECT untouched');
assert.equal(writeCalls().length,0);dbCreationStop();
''')

    def test_storage_failure_retains_session_request_and_notice(self):
        self.javascript(SETUP + r'''
show(process());localStorage.setItem=()=>{throw Error('quota');};creationRoute=async()=>{throw Error('lost');};
await dbCreationMutate('/creation/plans',{expected_revision:3,phase:'bootstrap'});assert.ok(dbCreation.pending);
assert.match($('db-creation-status').textContent,/retained only in this session/);
''')

    def test_unchanged_get_preserves_review_focus_and_explicit_rejection_requires_review_again(self):
        self.javascript(SETUP + r'''
show(process({plans:[plan()],next_actions:['approve']}));const reviewButton=$('db-creation-review-bootstrap');await dbCreationLoad();
assert.equal($('db-creation-review-bootstrap'),reviewButton);await reviewButton.onclick();
const checkbox=$('db-creation-confirm');checkbox.checked=true;checkbox.onchange();checkbox.focus();
await dbCreationLoad();assert.equal($('db-creation-confirm'),checkbox);assert.equal(document.activeElement,checkbox);
creationRoute=async()=>({http:409,body:{error:'STALE_PLAN',message:'Database creation request rejected'}});await $('db-creation-approve').onclick();
assert.equal(dbCreation.pending,null);assert.equal(dbCreation.review,null);assert.equal(dbCreation.error,'STALE_PLAN');
''')

    def test_visibility_revalidation_refreshes_full_control_and_rejects_retained_handler(self):
        self.javascript(SETUP + r'''
show(process({plans:[plan('bootstrap','PREPARED')],next_actions:['plans']}));
const old=$('db-creation-full'),retained=old.onclick;assert.equal(old.disabled,false);old.focus();
dbCreationSuspend();dbCreationSuspend();await retained();assert.equal(creationPosts.length,0);
await dbCreationLoad();const fresh=$('db-creation-full');assert.notEqual(fresh,old);
assert.equal(fresh.disabled,false);assert.equal(document.activeElement,fresh);assert.equal(dbCreation.review,null);
await retained();assert.equal(creationPosts.length,0);
await fresh.onclick();assert.equal(creationPosts.length,1);
assert.equal(creationPosts[0].path,C+'/creation/plans');
assert.deepEqual(JSON.parse(creationPosts[0].options.body),{expected_revision:3,phase:'full'});
await retained();assert.equal(creationPosts.length,1);
''')

    def test_visibility_revalidation_preserves_draft_focus_and_text_selection(self):
        self.javascript(SETUP + r'''
begin();const draft=structuredClone(dbCreation.draft),field=$('db-creation-description');
field.focus();field.setSelectionRange(1,4);dbCreationSuspend();dbCreationSuspend();await dbCreationLoad();
const refreshed=$('db-creation-description');assert.notEqual(refreshed,field);
assert.deepEqual(dbCreation.draft,draft);assert.equal(refreshed.value,draft.description);
assert.equal($('db-creation-sql_catalog').value,draft.sql_catalog);
assert.equal($('db-creation-display_name').value,draft.display_name);
assert.equal(document.activeElement,refreshed);assert.equal(refreshed.selectionStart,1);assert.equal(refreshed.selectionEnd,4);
assert.equal(creationPosts.length,0);
''')

    def test_visibility_revalidation_discards_review_and_obsolete_approval_focus(self):
        self.javascript(SETUP + r'''
show(process({plans:[plan()],next_actions:['approve']}));await $('db-creation-review-bootstrap').onclick();
const checkbox=$('db-creation-confirm');checkbox.checked=true;checkbox.onchange();checkbox.focus();
const approve=$('db-creation-approve').onclick;
dbCreationSuspend();dbCreationSuspend();await dbCreationLoad();
assert.equal(dbCreation.review,null);assert.equal(document.activeElement,$('db-creation-heading'));
assert.equal($('db-creation-process').descendants().some(n=>n.id==='db-creation-confirm'||n.id==='db-creation-approve'),false);
await approve();assert.equal(creationPosts.length,0);
''')

    def test_authority_loss_during_visibility_revalidation_clears_admin_without_submission(self):
        for change in ["authenticated:false", "username:'other'", "maximum_role:'data_engineer'", "platform_admin:false"]:
            with self.subTest(change=change):
                self.javascript(SETUP + r'''
show(process({plans:[plan()],next_actions:['approve']}));await $('db-creation-review-bootstrap').onclick();
$('db-creation-confirm').checked=true;$('db-creation-confirm').onchange();const approve=$('db-creation-approve').onclick;
dbCreationSuspend();dbCreationSuspend();const held=deferred();sessionRoute=()=>held.promise;
const reading=dbCreationLoad();await tick();held.resolve({...session,''' + change + r'''});await reading;
assert.equal(dbAdminAllowed(),false);assert.equal($('db-admin-content').children.length,0);
assert.equal(dbCreation.process,null);assert.equal(dbCreation.review,null);assert.equal(dbCreation.pending,null);
await approve();await dbCreationLoad();assert.equal(creationPosts.length,0);
''')

    def test_tab_leave_discards_delayed_render_and_reopen_never_replays(self):
        self.javascript(SETUP + r'''
show(process());const held=deferred();creationRoute=()=>held.promise;
const posting=dbCreationMutate('/creation/plans',{expected_revision:3,phase:'bootstrap'});await tick();
activate($('tab-warehouse'));held.resolve({creation_id:'stale',request_id:'ack',accepted_revision:3});await posting;
assert.equal(dbCreation.process.creation_id,'creation');assert.ok(dbCreation.pending);assert.equal(dbCreation.pending.busy,false);
await dbAdminOpen();assert.equal(creationPosts.length,1);
''')

    def test_url_return_is_get_only_and_preserves_existing_route(self):
        self.javascript(SETUP + r'''
globalThis.location={href:'http://localhost/workbench?view=sql&creation=creation&creation_environment=envA#worksheet-a',origin:'http://localhost'};
let replacement;globalThis.history={replaceState:(_,__,url)=>{replacement=String(url);}};
dbCreation.scope='';dbCreation.process=null;await dbCreationLoad();
assert.equal(dbCreation.process.creation_id,'creation');assert.equal(creationPosts.length,0);
assert.equal(new URL(replacement).searchParams.get('view'),'sql');assert.equal(new URL(replacement).searchParams.has('creation'),false);
assert.equal(new URL(replacement).searchParams.has('creation_environment'),false);
assert.equal(new URL(replacement).hash,'#worksheet-a?creation=creation&creation_environment=envA');
''')

    def test_session_preflight_denies_mutation_after_current_admin_loss(self):
        for change in ["session.username='another'", "session.maximum_role='data_engineer'", "session.platform_admin=false"]:
            self.javascript(SETUP + r'''
show(process());
''' + change + r''';await dbCreationMutate('/creation/plans',{expected_revision:3,phase:'bootstrap'});
assert.equal(creationPosts.length,0);assert.equal(dbAdminAllowed(),false);assert.equal(dbCreation.process,null);
''')

    def test_schema_copy_and_explicit_new_creation_refresh_capabilities_preserve_draft(self):
        self.javascript(SETUP + r'''
globalThis.location={href:'http://localhost/workbench?view=sql&creation=creation&creation_environment=envA#worksheet-a',origin:'http://localhost'};
globalThis.history={replaceState:(_,__,url)=>{location.href=String(url);}};
dbCreationClear();await dbCreationLoad();
show(process({lifecycle:'READY',progress_code:'READY',next_actions:[]}));
assert.match(contents($('db-creation-process')),/Fixed schemas: raw, marts, sandbox, staging\./);
setLanguage('ja');assert.match(contents($('db-creation-process')),/固定スキーマ: raw、marts、sandbox、staging。/);setLanguage('en');
dbCreation.draft.sql_catalog='unfinished_draft';dbCreation.capability={available:false};
await dbCreationLoad();assert.equal(dbCreation.process.creation_id,'creation');assert.equal(dbCreationURL().id,'creation');
assert.equal(new URL(location.href).searchParams.has('creation'),false);
const before=calls.filter(c=>c.path===C+'/capabilities').length;
assert.equal($('db-creation-new').textContent,'New creation');await $('db-creation-new').onclick();
assert.equal(dbCreation.process,null);assert.equal(new URL(location.href).searchParams.has('creation'),false);
assert.equal(new URL(location.href).searchParams.get('view'),'sql');assert.equal(new URL(location.href).hash,'#worksheet-a');
assert.equal($('db-creation-sql_catalog').value,'unfinished_draft');assert.equal(dbCreation.capability.available,true);
assert.equal(calls.filter(c=>c.path===C+'/capabilities').length>before,true);assert.equal(creationPosts.length,0);
show(process());const oldNew=$('db-creation-new').onclick;creationRoute=async()=>{throw Error('lost');};
await dbCreationMutate('/creation/plans',{expected_revision:3,phase:'bootstrap'});const frozen=JSON.stringify(dbCreation.pending);
await oldNew();assert.equal(dbCreation.process.creation_id,'creation');assert.equal(JSON.stringify(dbCreation.pending),frozen);assert.equal($('db-creation-new').disabled,true);
''')

    def test_canonical_validation_rejection_preserves_draft_and_allows_only_explicit_correction(self):
        self.javascript(SETUP + r'''
begin();await $('db-creation-next').onclick();await $('db-creation-next').onclick();
creationRoute=async()=>({http:400,body:{error:'INVALID_NAME',message:'Database creation request rejected'}});
await $('db-creation-submit').onclick();assert.equal(dbCreation.pending,null);assert.equal(dbCreation.stage,2);
const original=creationPosts[0];await dbCreationLoad();assert.equal(creationPosts.length,1);
await $('db-creation-back').onclick();await $('db-creation-back').onclick();assert.equal($('db-creation-display_name').value,'名前 <script> 🐻');
edit('db-creation-sql_catalog','corrected_name');await $('db-creation-next').onclick();await $('db-creation-next').onclick();
creationRoute=null;await $('db-creation-submit').onclick();assert.equal(creationPosts.length,2);
assert.equal(JSON.parse(creationPosts[1].options.body).sql_catalog,'corrected_name');assert.notEqual(creationPosts[1].options.headers['Idempotency-Key'],original.options.headers['Idempotency-Key']);
''')

    def test_ambiguous_rejection_transport_and_invalid_ack_preserve_frozen_request(self):
        for response in [
            "throw Error('lost')",
            "return {http:503,body:{error:'OPERATOR_UNAVAILABLE',message:'Database creation request rejected'}}",
            "return {http:409,body:{error:'STALE_PLAN'}}",
            "return {http:409,body:{error:'STALE_PLAN',message:'rejected',request_id:'possibly-admitted'}}",
            "return {http:409,body:{error:'IDEMPOTENCY_CONFLICT',message:'Database creation request rejected'}}",
            "return {http:403,body:{error:'PRINCIPAL_STOPPED',message:'Database creation request rejected'}}",
            "return {creation_id:'wrong-creation',request_id:'ack',accepted_revision:3}",
            "return {creation_id:'creation',request_id:'ack',accepted_revision:0}",
            "return {malformed:true}",
        ]:
            with self.subTest(response=response):
                self.javascript(SETUP + 'creationRoute=async()=>{' + response + r'''};
show(process());await dbCreationMutate('/creation/plans',{expected_revision:3,phase:'bootstrap'});
assert.ok(dbCreation.pending);const frozen=structuredClone(dbCreation.pending);await dbCreationLoad();
assert.equal(creationPosts.length,1);assert.deepEqual(dbCreation.pending,frozen);
assert.match(contents($('db-creation-form')),/Request outcome uncertain/);
''')

    def test_stale_review_handlers_cannot_approve_after_poll_changes(self):
        for change in ["revision:4", "next_actions:[]", "progress_code:'STOPPED'", "requester_id:'other'", "plans:[{...plan(),operation_revision:4}]", "plans:[{...plan(),state:'UNKNOWN'}]"]:
            with self.subTest(change=change):
                self.javascript(SETUP + r'''
show(process({plans:[plan()],next_actions:['approve']}));await $('db-creation-review-bootstrap').onclick();
const checkbox=$('db-creation-confirm');checkbox.checked=true;checkbox.onchange();const approve=$('db-creation-approve').onclick;
creation=process({plans:[plan()],next_actions:['approve'],''' + change + r'''});await dbCreationLoad();
assert.equal(dbCreation.review,null);await approve();assert.equal(creationPosts.length,0);
''')

    def test_action_rechecked_after_session_preflight_before_post(self):
        self.javascript(SETUP + r'''
show(process({plans:[plan()],next_actions:['approve']}));await $('db-creation-review-bootstrap').onclick();
$('db-creation-confirm').checked=true;$('db-creation-confirm').onchange();const held=deferred();sessionRoute=()=>held.promise;
const posting=$('db-creation-approve').onclick();await tick();
dbCreation.process={...dbCreation.process,next_actions:[]};held.resolve(structuredClone(session));await posting;
assert.equal(creationPosts.length,0);assert.equal(dbCreation.pending,null);assert.equal(dbCreation.review,null);
assert.match($('db-creation-status').textContent,/Creation changed/);
''')

    def test_inflight_wording_no_retry_then_same_saved_request_after_authority_loss(self):
        self.javascript(SETUP + r'''
show(process());activeTab='db-admin';dbCreation.principals=[{principal_id:'private-old'}];dbCreation.list=[process()];dbCreation.draft.sql_catalog='old_draft';
const key=dbCreationStorageKey(),held=deferred();creationRoute=()=>held.promise;
const posting=dbCreationMutate('/creation/plans',{expected_revision:3,phase:'bootstrap'});await tick();
assert.match(contents($('db-creation-form')),/Request in progress/);assert.doesNotMatch(contents($('db-creation-form')),/outcome uncertain/);
assert.equal($('db-creation-retry').disabled,true);assert.equal(JSON.parse(storage.get(key)).pending.busy,true);
const first=creationPosts[0];currentRole='data_engineer';dbApplyRole();
assert.equal(dbCreation.pending,null);assert.deepEqual(dbCreation.list,[]);assert.deepEqual(dbCreation.principals,[]);assert.equal(dbCreation.review,null);assert.equal(dbCreation.draft.sql_catalog,'');
assert.equal(JSON.parse(storage.get(key)).pending.key,first.options.headers['Idempotency-Key']);
assert.equal(storage.has(dbCreationStorageKey()),false);
held.resolve({creation_id:'creation',request_id:'ack',accepted_revision:3});await posting;
currentRole='admin';dbApplyRole();dbAdminSession(session);dbAdminInstall();await dbCreationLoad();assert.ok(dbCreation.pending);assert.equal(dbCreation.pending.busy,false);
assert.equal(creationPosts.length,1);creationRoute=null;await $('db-creation-retry').onclick();
assert.equal(creationPosts[1].options.body,first.options.body);assert.equal(creationPosts[1].options.headers['Idempotency-Key'],first.options.headers['Idempotency-Key']);
''')

    def test_session_fallback_survives_clear_only_for_same_authority(self):
        self.javascript(SETUP + r'''
show(process());localStorage.setItem=()=>{throw Error('quota');};creationRoute=async()=>{throw Error('lost');};
await dbCreationMutate('/creation/plans',{expected_revision:3,phase:'bootstrap'});const key=dbCreation.pending.key;
dbCreationClear();dbPrincipal=JSON.stringify(['other',true]);session.username='other';await dbCreationLoad();
assert.equal(dbCreation.pending,null);assert.deepEqual(dbCreation.principals,[]);
dbCreationClear();dbPrincipal=JSON.stringify(['owner',true]);session.username='owner';await dbCreationLoad();
assert.equal(dbCreation.pending.key,key);assert.equal(creationPosts.length,1);
''')

    def test_delayed_list_cannot_restore_previous_account_records(self):
        self.javascript(SETUP + r'''
const held=deferred(),old=adminRoute;adminRoute=(path,options)=>path===C?held.promise:old(path,options);
const reading=dbCreationLoad(true);await tick();session.maximum_role='data_engineer';dbAdminSession(session);
held.resolve({creations:[process({sql_catalog:'old-owner-private'})],cursor:null});await reading;
assert.deepEqual(dbCreation.list,[]);assert.deepEqual(dbCreation.principals,[]);assert.equal(dbCreation.process,null);
''')

    def test_ready_worksheet_with_production_inventory_and_schema_dtos(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
        import control_store
        import data_management as registry
        import database_inventory as inventory_api
        import request_auth

        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, SHIROKUMA_STATE_DIR=temp):
            with control_store.connect() as db:
                database = registry.create_database(db, 'fresh_catalog', '新しい DB', '', 'test')
                db.execute("UPDATE databases SET state='READY' WHERE id=?", (database.id,))
                registry.create_binding(db, database.id, 'polaris', 'SALES', 'catalog', 'storage', 'credential', 's3://synthetic/')
                registry.record_generation(db, registry.WarehouseConfigGeneration('fresh-generation', 'LOCAL', 'apply', (), (), (), 'READY'))
                registry.update_binding(db, database.id, 1, 1, 'fresh-generation')
                for name in ('raw', 'marts', 'sandbox', 'staging'):
                    registry.create_schema(db, database.id, name, name, name, 'test')
            principal = request_auth.AuthenticatedPrincipal('owner', 'admin', True)
            with patch.object(request_auth, 'authorize_database', return_value=SimpleNamespace(grant_revision=7)):
                databases = inventory_api.list_databases(principal, 'admin')
                schema_dto = inventory_api.list_schemas(principal, 'admin', database.id)
        self.javascript(SETUP + 'const actualDatabases=' + json.dumps(databases) + ';const actualSchemas=' + json.dumps(schema_dto) + r''';
const actual=actualDatabases.items[0];inventory.push(actual);schemasRoute=async(path)=>({items:path.includes('/'+actual.id+'/')?actualSchemas.items:schemas});
setLanguage('ja');$('sql').value='SELECT 元のドラフト';dbSave();const original=structuredClone(worksheets),role=currentRole;
show(process({database_id:actual.id,lifecycle:'READY',progress_code:'READY',next_actions:[]}));
await $('db-creation-sql').onclick();const added=dbCurrent();
assert.equal(worksheets.length,original.length+1);assert.deepEqual(worksheets.slice(0,-1),original);assert.equal(language,'ja');assert.equal(currentRole,role);
assert.equal(added.database_id,actual.id);assert.equal(added.binding_revision,actual.binding_revision);assert.equal(added.generation_id,actual.generation_id);assert.equal(added.selected_role,role);
const raw=actualSchemas.items.find(s=>s.sql_name==='raw');assert.equal(added.schema_id,raw.id);assert.equal(added.sql,'');assert.equal(added.mode,'read-only');
assert.equal($('worksheet-tabs').descendants().some(node=>node.dataset.worksheet===added.id),true);
assert.equal(dbSchemas.some(s=>s.id===added.schema_id),true);assert.equal(dbContext(added).sql_catalog,'fresh_catalog');assert.equal(dbContext(added).sql_schema,'raw');
assert.equal(document.activeElement,$('sql'));assert.equal(calls.some(c=>c.path==='/api/v1/queries'&&c.options?.method==='POST'),false);
''')

    def test_stopped_executor_allows_only_server_offered_safe_actions_after_preflight(self):
        self.javascript(SETUP + r'''
capability.available=false;capability.blocker='PRINCIPAL_STOPPED';
show(process({progress_code:'STOPPED',blocker:'PRINCIPAL_STOPPED',next_actions:['reconcile','withdraw']}));
const stopped=structuredClone(creation);creationRoute=async()=>{assert.equal(calls.at(-2).path,C+'/capabilities');return {creation_id:'creation',request_id:'ack',accepted_revision:3};};
const before=calls.length;await $('db-creation-reconcile').onclick();assert.equal(creationPosts.at(-1).path,C+'/creation/reconcile');
assert.equal(calls.slice(before).some(c=>c.path==='/api/session'),true);assert.equal(dbAdminAllowed(),true);assert.equal(dbCreation.process.progress_code,'STOPPED');
await $('db-creation-withdraw').onclick();assert.equal(creationPosts.at(-1).path,C+'/creation/withdraw');
const count=creationPosts.length;show({...stopped,next_actions:[]});await dbCreationMutate('/creation/reconcile',{expected_revision:3});assert.equal(creationPosts.length,count);
show({...stopped,next_actions:['plans','replan','approve','recover-verify'],plans:[plan(),plan('full','UNKNOWN','VERIFY_RECOVERY')]});
for(const [suffix,body] of [['plans',{phase:'bootstrap'}],['replan',{phase:'full'}],['phases/bootstrap/approve',{}],['phases/full/recover-verify',{}]])await dbCreationMutate('/creation/'+suffix,{expected_revision:3,...body});
assert.equal(creationPosts.length,count);assert.equal($('db-creation-process').descendants().some(n=>/db-creation-(bootstrap|replan|review-)/.test(n.id||'')),false);
show(stopped);session.platform_admin=false;await dbCreationMutate('/creation/reconcile',{expected_revision:3});assert.equal(creationPosts.length,count);assert.equal(dbAdminAllowed(),false);
''')

    def test_collection_retry_environment_mismatch_preserves_original_binding_and_no_post(self):
        self.javascript(SETUP + r'''
begin();await $('db-creation-next').onclick();await $('db-creation-next').onclick();creationRoute=async()=>{throw Error('lost');};
const keyA=dbCreationStorageKey();await $('db-creation-submit').onclick();const frozen=JSON.parse(storage.get(keyA)).pending;
assert.equal(frozen.path,C);assert.deepEqual(frozen.binding,{origin:'local',principal:dbPrincipal,role:'admin',environment_id:'envA'});
const before=creationPosts.length;capability.environment_id='envB';await $('db-creation-retry').onclick();
assert.equal(creationPosts.length,before);assert.deepEqual(JSON.parse(storage.get(keyA)).pending,frozen);
assert.equal(dbCreation.environment_id,'envB');assert.equal(dbCreation.process,null);assert.equal(dbCreation.pending,null);assert.deepEqual(dbCreation.list,[]);
assert.equal(storage.has(dbCreationStorageKey()),false);assert.match(contents($('db-creation-form')),/original environment/);
''')

    def test_same_environment_reload_get_only_retry_exact_request_even_when_unavailable(self):
        self.javascript(SETUP + r'''
begin();await $('db-creation-next').onclick();await $('db-creation-next').onclick();creationRoute=async()=>{throw Error('lost');};
await $('db-creation-submit').onclick();const first=creationPosts[0],frozen=structuredClone(dbCreation.pending);
dbCreationSuspend();dbCreationSuspend();await dbCreationLoad();
assert.deepEqual(dbCreation.pending,frozen);assert.equal(creationPosts.length,1);
dbCreationClear();capability.available=false;capability.blocker='PRINCIPAL_STOPPED';await dbCreationLoad();
assert.equal(creationPosts.length,1);assert.deepEqual(dbCreation.pending,frozen);assert.equal(dbCreation.pending.busy,false);
assert.equal(dbCreation.pending.path,first.path);assert.equal(dbCreation.pending.body,first.options.body);
assert.equal(dbCreation.pending.key,first.options.headers['Idempotency-Key']);
creationRoute=null;await $('db-creation-retry').onclick();assert.equal(creationPosts.length,2);
assert.equal(creationPosts[1].options.body,first.options.body);assert.equal(creationPosts[1].options.headers['Idempotency-Key'],first.options.headers['Idempotency-Key']);
assert.equal(dbCreation.process.creation_id,'creation');assert.equal(calls.some(c=>c.path===C+'/creation'),true);
''')

    def test_environment_changes_between_session_and_first_post_freeze_without_admission(self):
        self.javascript(SETUP + r'''
begin();await $('db-creation-next').onclick();await $('db-creation-next').onclick();const keyA=dbCreationStorageKey();
const held=deferred();sessionRoute=()=>held.promise;const posting=$('db-creation-submit').onclick();await tick();
const frozen=JSON.parse(storage.get(keyA)).pending;assert.equal(frozen.binding.environment_id,'envA');
capability.environment_id='envB';held.resolve(structuredClone(session));await posting;
assert.equal(creationPosts.length,0);assert.equal(JSON.parse(storage.get(keyA)).pending.key,frozen.key);assert.equal(JSON.parse(storage.get(keyA)).pending.body,frozen.body);
assert.equal(dbCreation.environment_id,'envB');assert.equal(dbCreation.pending,null);
''')

    def test_unconfigured_or_unknown_environment_denies_retry_and_preserves_evidence(self):
        for cap in [
            "{environment_id:'envA',warehouse_id:'LOCAL',warehouse_revision:3,configured:false,available:false,blocker:'FOUNDATION_NOT_READY'}",
            "{environment_id:null,warehouse_id:null,warehouse_revision:null,configured:false,available:false,blocker:'FOUNDATION_NOT_READY'}",
            "{configured:true,available:true}",
        ]:
            self.javascript(SETUP + r'''
show(process());creationRoute=async()=>{throw Error('lost');};await dbCreationMutate('/creation/plans',{expected_revision:3,phase:'bootstrap'});
const keyA=dbCreationStorageKey(),saved=JSON.parse(storage.get(keyA)).pending,before=creationPosts.length;
capability=''' + cap + r''';await $('db-creation-retry').onclick();assert.equal(creationPosts.length,before);
assert.deepEqual(JSON.parse(storage.get(keyA)).pending,saved);assert.equal(dbCreation.environment_id,null);
''')

    def test_environment_change_inflight_ignores_ack_and_old_selected_records(self):
        self.javascript(SETUP + r'''
begin();await $('db-creation-next').onclick();await $('db-creation-next').onclick();const keyA=dbCreationStorageKey(),held=deferred();creationRoute=()=>held.promise;
const posting=$('db-creation-submit').onclick();await tick();const frozen=JSON.parse(storage.get(keyA)).pending;
capability.environment_id='envB';held.resolve({creation_id:'old-creation',request_id:'ack',accepted_revision:3});await posting;
assert.equal(dbCreation.environment_id,'envB');assert.equal(dbCreation.process,null);assert.equal(dbCreation.review,null);assert.deepEqual(dbCreation.list,[]);assert.deepEqual(dbCreation.principals,[]);
assert.equal(JSON.parse(storage.get(keyA)).pending.key,frozen.key);assert.equal(calls.some(c=>c.path===C+'/old-creation'),false);
const keyB=dbCreationStorageKey();assert.equal(storage.has(keyB),false);
capability.environment_id='envA';await dbCreationLoad();assert.equal(dbCreation.pending.key,frozen.key);assert.equal(creationPosts.length,1);
''')

    def test_environment_scoped_selection_and_legacy_pending_are_not_migrated(self):
        self.javascript(SETUP + r'''
globalThis.location={href:'http://localhost/workbench?creation=old-creation&creation_environment=envA#sql',origin:'http://localhost'};
globalThis.history={replaceState:(_,__,url)=>{location.href=String(url);}};
dbCreationClear();await dbCreationLoad();dbCreation.process=process({creation_id:'old-creation'});dbCreationRememberURL('old-creation');
const keyA=dbCreationStorageKey(),savedA=storage.get(keyA);
dbCreationClear();capability.environment_id='envB';const before=calls.length;await dbCreationLoad();
assert.equal(dbCreation.process,null);assert.equal(calls.slice(before).some(c=>c.path===C+'/old-creation'),false);assert.equal(storage.get(keyA),savedA);assert.equal(creationPosts.length,0);
const legacyKey='shirokuma.creation.v1:'+JSON.stringify([location.origin,dbPrincipal,currentRole]);
const legacy=JSON.stringify({creation_id:'legacy-creation',pending:{path:C,body:'{"sql_catalog":"legacy"}',key:'old-key',busy:true}});storage.set(legacyKey,legacy);
dbCreationClear();await dbCreationLoad();assert.equal(dbCreation.pending,null);assert.equal(dbCreation.process,null);assert.equal(storage.get(legacyKey),legacy);
await dbCreationMutate('',null,true);assert.equal(creationPosts.length,0);assert.match(contents($('db-creation-form')),/no environment binding/);
''')

    def test_environment_binding_survives_complete_storage_failure_in_session(self):
        self.javascript(SETUP + r'''
show(process());localStorage.setItem=localStorage.getItem=()=>{throw Error('storage disabled');};creationRoute=async()=>{throw Error('lost');};
await dbCreationMutate('/creation/plans',{expected_revision:3,phase:'bootstrap'});const original=structuredClone(dbCreation.pending);
dbCreationClear();await dbCreationLoad();assert.deepEqual(dbCreation.pending.binding,original.binding);assert.equal(dbCreation.pending.key,original.key);
capability.environment_id='envB';const before=creationPosts.length;await $('db-creation-retry').onclick();assert.equal(creationPosts.length,before);
capability.environment_id='envA';await dbCreationLoad();assert.equal(dbCreation.pending.body,original.body);assert.equal(dbCreation.pending.key,original.key);
''')

    def test_environment_change_during_get_ignores_old_list_response(self):
        self.javascript(SETUP + r'''
const held=deferred(),old=adminRoute;adminRoute=(path,options)=>path===C?held.promise:old(path,options);
const reading=dbCreationLoad(true);await tick();capability.environment_id='envB';held.resolve({creations:[process({sql_catalog:'old-environment-record'})],cursor:null});await reading;
assert.equal(dbCreation.environment_id,'envB');assert.deepEqual(dbCreation.list,[]);assert.equal(dbCreation.process,null);assert.equal(creationPosts.length,0);
''')

    def test_frozen_environment_header_first_post_retry_and_gets_without_mutation_header(self):
        self.javascript(SETUP + r'''
show(process());creationRoute=async()=>{throw Error('lost');};
await dbCreationMutate('/creation/plans',{expected_revision:3,phase:'bootstrap'});const first=creationPosts[0],frozen=structuredClone(dbCreation.pending);
assert.equal(first.options.headers['X-Shirokuma-Environment'],frozen.binding.environment_id);assert.equal(first.options.headers['X-Shirokuma-Environment'],'envA');
capability.available=false;capability.blocker='PRINCIPAL_STOPPED';creationRoute=null;
await $('db-creation-retry').onclick();assert.equal(creationPosts.length,2);const retry=creationPosts[1];
assert.equal(retry.options.headers['X-Shirokuma-Environment'],'envA');assert.equal(retry.options.body,first.options.body);
assert.equal(retry.options.headers['Idempotency-Key'],first.options.headers['Idempotency-Key']);
assert.equal(calls.filter(c=>c.path.startsWith(C)&&c.options?.method!=='POST').every(c=>!c.options?.headers?.['X-Shirokuma-Environment']),true);
''')

    def test_server_environment_fence_preserves_collection_uncertainty_en_ja(self):
        for language in ['en', 'ja']:
            self.javascript(SETUP + "setLanguage('" + language + "');" + r'''
begin();await $('db-creation-next').onclick();await $('db-creation-next').onclick();
const keyA=dbCreationStorageKey();let first;
creationRoute=async(path,options)=>{
 assert.equal(capability.environment_id,'envA');assert.equal(options.headers['X-Shirokuma-Environment'],'envA');
 const saved=JSON.parse(storage.get(keyA)).pending;assert.equal(saved.binding.environment_id,'envA');assert.equal(saved.body,options.body);
 first??={body:options.body,key:options.headers['Idempotency-Key']};
 return {http:409,body:{error:'ENVIRONMENT_MISMATCH',message:'Database creation request rejected'}};
};
await $('db-creation-submit').onclick();assert.equal(creationPosts.length,1);assert.ok(dbCreation.pending);
assert.equal(dbCreation.pending.body,first.body);assert.equal(dbCreation.pending.key,first.key);assert.equal(dbCreation.pending.binding.environment_id,'envA');
assert.match($('db-creation-status').textContent,language==='en'?/original outcome is unknown/:/元の結果は不明/);
assert.doesNotMatch($('db-creation-status').textContent,/UNSENT/);
await dbCreationLoad();assert.equal(creationPosts.length,1);assert.equal(dbCreation.pending.key,first.key);
await $('db-creation-retry').onclick();assert.equal(creationPosts.length,2);assert.equal(creationPosts[1].options.headers['X-Shirokuma-Environment'],'envA');
assert.equal(creationPosts[1].options.body,first.body);assert.equal(creationPosts[1].options.headers['Idempotency-Key'],first.key);
capability.environment_id='envB';await $('db-creation-retry').onclick();assert.equal(creationPosts.length,2);
const saved=JSON.parse(storage.get(keyA)).pending;assert.equal(saved.binding.environment_id,'envA');assert.equal(saved.body,first.body);assert.equal(saved.key,first.key);
''')


LOGICAL_SETUP = r'''
capability={...capability,creation_mode:'logical_v1',warehouse_state:'ACTIVE',foundation_id:'private-foundation',foundation_revision:7};
const logical=(extra={})=>process({creation_mode:'logical_v1',foundation_id:'private-foundation',foundation_revision:7,
 binding_revision:2,policy_revision:4,progress_code:'CREATING',plans:[],next_actions:['withdraw'],...extra});
const visibleIDs=()=>$('db-creation').descendants().map(n=>n.id);
const noLegacy=()=>assert.doesNotMatch(contents($('db-creation')),/approval [12]\/2|two separate approvals|warehouse maintenance|all of LOCAL|bootstrap|full verification|承認 [12]\/2|個別.*承認|LOCAL 全体|private-foundation|foundation_revision|policy_revision/i);
const logicalBegin=async()=>{dbCreation.process=null;dbCreation.stage=0;await dbCreationLoad();begin();};
'''


class LogicalCreationUI(unittest.TestCase):
    def test_ready_new_creation_loads_with_absent_heading_during_capability_refresh(self):
        body = SETUP + LOGICAL_SETUP + r'''
await logicalBegin();
show(logical({lifecycle:'READY',progress_code:'READY',next_actions:[]}));
dbCreation.draft.sql_catalog='retained_new_name';
// Only this test uses actual tree membership for the heading lookup. Detached
// nodes in the adapter's ID map must not masquerade as a live Loading heading.
document.getElementById=id=>$('db-creation').descendants().find(node=>node.id===id)??null;
assert.ok(document.getElementById('db-creation-heading'));
const heldCap=deferred(),heldList=deferred(),heldPeople=deferred();
const oldRoute=adminRoute,newCap={...capability,warehouse_revision:4};
const ready=structuredClone(creation),previous={...ready,creation_id:'previous',sql_catalog:'previous_db'};
const people=[{principal_id:'bob',display_name:'Bob',maximum_role:'bi_viewer'}];
adminRoute=(path,options)=>{
 if(path===C+'/capabilities')return heldCap.promise;
 if(path===C)return heldList.promise;
 if(path===C+'/principals')return heldPeople.promise;
 return oldRoute(path,options);
};
const newButton=$('db-creation-new');assert.equal(newButton.textContent,'New creation');
const loading=newButton.onclick(); // Actual production READY → New handler.
assert.equal(dbCreation.capability,null);
assert.equal(document.getElementById('db-creation-heading'),null);
assert.equal($('db-creation-heading'),null);
assert.equal(document.getElementById('db-creation-loading').textContent,t('Loading…'));
assert.equal(dbCreation.pending,null);assert.equal(creationPosts.length,0);
await tick();assert.ok(calls.some(call=>call.path===C+'/capabilities'));
assert.equal(document.getElementById('db-creation-heading'),null);
heldCap.resolve(newCap);await tick();
heldList.resolve({creations:[ready,previous],cursor:null});await tick();
heldPeople.resolve({principals:people});await loading;
assert.deepEqual(dbCreation.capability,newCap);assert.deepEqual(dbCreation.principals,people);
assert.deepEqual(dbCreation.list,[ready,previous]);assert.equal(dbCreation.process,null);
assert.equal(document.getElementById('db-creation-loading'),null);
assert.equal($('db-creation-sql_catalog').value,'retained_new_name');
assert.equal($('db-creation-principal').options.some(option=>option.value==='bob'),true);
assert.ok(document.getElementById('db-creation-submit'));
assert.equal(dbCreation.pending,null);assert.equal(dbCreation.readFailed,false);assert.equal(dbCreation.error,'');
assert.equal(creationPosts.length,0);assert.equal(writeCalls().length,0);
'''
        run = subprocess.run
        legacy = False
        def strict_heading_lookup(argv, *args, **kwargs):
            self.assertEqual(argv[:2], ['node', '-e'])
            source = argv[2]
            needle = 'const $=id=>strictRemoved&&'
            self.assertEqual(source.count(needle), 1)
            source = source.replace(needle, "const $=id=>id==='db-creation-heading'&&document.getElementById?document.getElementById(id):strictRemoved&&", 1)
            if legacy:
                fixed = "function dbCreationStage(stage){dbCreation.stage=stage;dbCreationRender();$('db-creation-heading')?.focus();}"
                self.assertEqual(source.count(fixed), 1)
                source = source.replace(fixed, fixed.replace('?.focus()', '.focus()'), 1)
            return run([*argv[:2], source], *args, **kwargs)
        with patch.object(subprocess, 'run', side_effect=strict_heading_lookup):
            self.javascript(body)
            legacy = True
            # Negative control changes only the in-memory test script, never HTML.
            with self.assertRaisesRegex(AssertionError, r"Cannot read properties of null.*focus"):
                self.javascript(body)

    def test_capability_loading_unknown_failure_and_legacy_compatibility_preserve_intent(self):
        self.javascript(SETUP + LOGICAL_SETUP + r'''
await logicalBegin();$('sql').value='SELECT untouched';dbSave();
const draft=dbCreation.draft;draft.sql_catalog='retained_name';draft.description='retained description';
draft.initial_grants=[{principal_id:'alice',maximum_role:'bi_viewer'}];dbCreation.stage=2;
const frozen={key:'frozen-key',body:'{"sql_catalog":"retained_name"}',busy:false,binding:dbCreationBinding()};
dbCreation.pending=frozen;
const before=JSON.stringify({draft,stage:dbCreation.stage,worksheets,worksheetId,jobs:{context:dbJobs.context,save:dbJobs.save,database:dbJobs.database,schema:dbJobs.schema,role:dbJobs.role,selection:dbJobs.selection,drafts:[...dbJobs.drafts]},explorer:dbExplorer});
const savedStorage=JSON.stringify([...storage].filter(([key])=>key!=='language'));
const preserve=()=>{
 assert.equal(dbCreation.draft,draft);assert.equal(dbCreation.pending,frozen);
 assert.equal(frozen.key,'frozen-key');assert.equal(frozen.body,'{"sql_catalog":"retained_name"}');
 assert.equal(JSON.stringify({draft,stage:dbCreation.stage,worksheets,worksheetId,jobs:{context:dbJobs.context,save:dbJobs.save,database:dbJobs.database,schema:dbJobs.schema,role:dbJobs.role,selection:dbJobs.selection,drafts:[...dbJobs.drafts]},explorer:dbExplorer}),before);
 assert.equal($('sql').value,'SELECT untouched');assert.equal(JSON.stringify([...storage].filter(([key])=>key!=='language')),savedStorage);
 assert.equal(dbCreation.process,null);assert.equal(creationPosts.length,0);
};
const assertWaiting=()=>{
 noLegacy();const ids=visibleIDs();
 assert.ok(ids.includes('db-creation-loading'));assert.equal($('db-creation-loading').getAttribute('role'),'status');
 for(const id of ['db-creation-sql_catalog','db-creation-principal','db-creation-submit','db-creation-next','db-creation-back'])assert.equal(ids.includes(id),false);
 assert.equal($('db-creation-journey').children.length,0);
 assert.ok(ids.includes('db-creation-refresh'));assert.ok(ids.includes('db-creation-retry'));preserve();
};
const originalRoute=adminRoute,originalRender=dbCreationRender,frames=[];
dbCreationRender=()=>{originalRender();frames.push(contents($('db-creation')));};
for(const locale of ['en','ja']){
 setLanguage(locale);dbCreation.capability=null;dbCreation.error='';dbCreationRender();assertWaiting();
 assert.equal($('db-creation-loading').textContent,t('Loading…'));
 const held=deferred();adminRoute=async(path,options)=>path===C+'/capabilities'?held.promise:originalRoute(path,options);
 const reading=dbCreationLoad();await tick();dbCreationRender();assertWaiting();
 held.resolve(structuredClone(capability));await reading;noLegacy();preserve();
 assert.equal(visibleIDs().includes('db-creation-submit'),true);assert.equal(visibleIDs().includes('db-creation-next'),false);
 assert.equal($('db-creation-sql_catalog').value,'retained_name');
 for(const mode of ['future_v2',null,'']){
  adminRoute=originalRoute;capability.creation_mode=mode;await dbCreationLoad();assertWaiting();
  assert.equal($('db-creation-loading').textContent,t('The database creation service is unavailable. Try refreshing its status.'));
 }
 capability.creation_mode='logical_v1';dbCreation.capability=null;dbCreationRender();
 adminRoute=async(path,options)=>{if(path===C+'/capabilities')throw Error('offline');return originalRoute(path,options);};
 await dbCreationLoad();assertWaiting();assert.ok($('db-creation-status').textContent);
 adminRoute=originalRoute;await dbCreationLoad();noLegacy();preserve();
}
// Only a successful response with no mode opts into the old backend wizard.
setLanguage('en');delete capability.creation_mode;await dbCreationLoad();preserve();
assert.equal($('db-creation-journey').descendants().filter(n=>n.tagName==='LI').length,6);
assert.match(contents($('db-creation')),/several minutes|two separate approvals/);
assert.equal(visibleIDs().includes('db-creation-submit'),true);
assert.equal(frames.slice(0,-1).some(frame=>/approval [12]\/2|two separate approvals|LOCAL 全体/.test(frame)),false);
''')

    def test_logical_root_list_public_progress_localized_and_legacy_root_mode_independent(self):
        self.javascript(SETUP + LOGICAL_SETUP + r'''
await logicalBegin();$('sql').value='SELECT list_preserved';dbSave();
globalThis.location={href:'http://localhost/#db-admin?keep=yes',origin:'http://localhost'};
globalThis.history={replaceState:(_,__,url)=>{location.href=String(url);}};
const before=JSON.stringify({worksheets,worksheetId,jobs:{context:dbJobs.context,save:dbJobs.save,database:dbJobs.database,schema:dbJobs.schema,role:dbJobs.role,selection:dbJobs.selection,drafts:[...dbJobs.drafts]},explorer:dbExplorer});
const pending={key:'list-key',body:'frozen-list-body',busy:false};dbCreation.pending=pending;
const draft=dbCreation.draft;draft.sql_catalog='list_draft';
const roots=['BLOCKED','CREATING','UNKNOWN','READY'].map(progress_code=>logical({creation_id:progress_code,sql_catalog:progress_code.toLowerCase(),progress_code,lifecycle:'REGISTERED'}));
const legacy=process({creation_id:'legacy',sql_catalog:'legacy',progress_code:'BLOCKED',lifecycle:'REGISTERED'});
dbCreation.list=[...roots,legacy];
for(const locale of ['en','ja'])for(const mode of ['logical_v1',undefined]){
 setLanguage(locale);dbCreation.capability={...capability};if(mode===undefined)delete dbCreation.capability.creation_mode;
 dbCreationRender();
 const buttons=$('db-creation-form').descendants().filter(n=>n.tagName==='BUTTON');
 for(const [index,label] of ['Creation blocked','Creating database','Creation outcome unknown','Ready'].entries())assert.ok(buttons.some(n=>n.textContent===roots[index].sql_catalog+' · '+t(label)));
 assert.ok(buttons.some(n=>n.textContent==='legacy · REGISTERED'));
 for(const root of dbCreation.list){
  const label=root.creation_mode==='logical_v1'?dbCreationLogicalLabel(root.progress_code):'REGISTERED';
  // Select through the real handler, keeping the background read mocked out.
  const load=dbCreationLoad;dbCreationLoad=async()=>{};
  await buttons.find(n=>n.textContent===root.sql_catalog+' · '+label).onclick();dbCreationLoad=load;
  assert.equal(dbCreation.process,root);assert.equal(dbCreationURL().id,root.creation_id);
  assert.equal(dbCreationLogical(),root.creation_mode==='logical_v1');
  if(root===legacy)assert.equal($('db-creation-journey').descendants().filter(n=>n.tagName==='LI').length,6);else noLegacy();
  assert.equal(dbCreation.pending,pending);assert.equal(pending.key,'list-key');assert.equal(pending.body,'frozen-list-body');
  assert.equal(dbCreation.draft,draft);assert.equal(draft.sql_catalog,'list_draft');
  assert.equal(JSON.stringify({worksheets,worksheetId,jobs:{context:dbJobs.context,save:dbJobs.save,database:dbJobs.database,schema:dbJobs.schema,role:dbJobs.role,selection:dbJobs.selection,drafts:[...dbJobs.drafts]},explorer:dbExplorer}),before);assert.equal($('sql').value,'SELECT list_preserved');
 }
 dbCreation.process=null;
}
assert.equal(creationPosts.length,0);
''')

    def test_actual_logical_status_is_read_only_localized_and_preserves_context(self):
        self.javascript(SETUP + LOGICAL_SETUP + r'''
api=async path=>(await fetch(path)).json();await initializeDatabaseUI(session);
$('sql').value='SELECT preserved';dbSave();const original=structuredClone(worksheets),selected=worksheetId;
const priorWrites=writeCalls().length;
for(const locale of ['en','ja']){
 setLanguage(locale);
 for(const [state,label] of [['active','Active'],['chilled','Stopped'],['starting','Transitioning'],['unavailable','Unavailable'],[undefined,'Unavailable']]){
  route=async()=>({status:'healthy',creation_mode:'logical_v1',warehouse:{state}});await refreshStatus();
  assert.equal($('warehouse-state').textContent,t(label));assert.equal($('quick-warehouse-state').textContent,t(label));
  assert.equal($('quick-status-error').className,label==='Unavailable'?'error':'muted');
  if(state==='chilled')assert.equal($('quick-status-error').textContent,t('Ask an administrator to start the shared warehouse through administrator management before creating a database or running SQL.'));
  for(const id of ['wake','chill','quick-wake','quick-chill'])assert.equal($(id).disabled,true);
 }
 route=async()=>{throw Error('/private/secret');};await refreshStatus();
 assert.equal($('warehouse-state').textContent,t('Unavailable'));assert.doesNotMatch($('status-error').textContent,/private|secret/);
 assert.equal($('quick-status-error').className,'error');
}
assert.deepEqual(worksheets,original);assert.equal(worksheetId,selected);assert.equal($('sql').value,'SELECT preserved');assert.equal(writeCalls().length,priorWrites);
assert.equal(calls.some(c=>c.path.includes('/warehouse/wake')||c.path.includes('/warehouse/chill')),false);
route=async()=>({status:'healthy'});await refreshStatus();assert.equal($('warehouse-state').textContent,t('Ready'));
assert.equal($('quick-warehouse-state').textContent,t('Database mode supports LOCAL only.'));
''')

    def test_logical_capability_state_fences_creation_and_refreshes_without_context_changes(self):
        self.javascript(SETUP + LOGICAL_SETUP + r'''
await logicalBegin();const original=structuredClone(worksheets),selected=worksheetId;
for(const locale of ['en','ja']){
 setLanguage(locale);
 for(const [state,label] of [['ACTIVE','Active'],['STOPPED','Stopped'],['TRANSITIONING','Transitioning'],['raw secret','Unavailable'],[null,'Unavailable']]){
  capability.warehouse_state=state;capability.available=true;capability.blocker=null;
  await dbCreationLoad();
  assert.equal($('db-creation-submit').disabled,state!=='ACTIVE');
  assert.equal(dbCreationCanMutate('',{}),state==='ACTIVE');
  assert.ok($('db-creation-warehouse-state').textContent.startsWith(t(label)+':'));
  assert.doesNotMatch($('db-creation-warehouse-state').textContent,/raw secret/);
  if(state==='STOPPED')assert.ok($('db-creation-warehouse-state').textContent.includes(t('Ask an administrator to start the shared warehouse through administrator management before creating a database or running SQL.')));
 }
}
assert.deepEqual(worksheets,original);assert.equal(worksheetId,selected);assert.equal(creationPosts.length,0);
''')

    javascript = admin.harness.DatabaseUI.javascript

    def test_one_form_localized_access_one_create_and_no_implicit_self_grant(self):
        self.javascript(SETUP + LOGICAL_SETUP + r'''
await logicalBegin();
for(const locale of ['en','ja']){
 setLanguage(locale);dbCreationRender();noLegacy();
 for(const id of ['sql_catalog','display_name','description','principal','role']){
  const field=$('db-creation-'+id);assert.ok(visibleIDs().includes(field.id));
  assert.equal(field.parentElement.children.find(n=>n.tagName==='LABEL').htmlFor,field.id);
 }
 assert.equal($('db-creation-submit').textContent,t('Create database'));assert.equal($('db-creation-submit').className,'primary');
 assert.equal(visibleIDs().some(id=>['db-creation-next','db-creation-back','db-creation-approve'].includes(id)),false);
 assert.deepEqual(dbCreation.draft.initial_grants,[]);
}
$('db-creation-principal').value='alice';$('db-creation-principal').onchange();$('db-creation-role').value='data_engineer';await $('db-creation-add').onclick();
assert.equal(creationPosts.length,0);creation=logical();
creationRoute=async()=>({creation_id:'creation',request_id:'ack',accepted_revision:3,action:'CREATE',phase:null});
await $('db-creation-submit').onclick();
assert.equal(creationPosts.length,1);assert.equal(creationPosts[0].path,C);
assert.deepEqual(JSON.parse(creationPosts[0].options.body),{sql_catalog:'new_sales',display_name:'名前 <script> 🐻',description:'説明 <img>',initial_grants:[{principal_id:'alice',maximum_role:'data_engineer'}]});
assert.equal(dbCreation.process.progress_code,'CREATING');noLegacy();
assert.equal(visibleIDs().some(id=>/bootstrap|full|approve|review/.test(id)),false);
''')

    def test_validation_focus_errors_and_no_grants_by_default(self):
        self.javascript(SETUP + LOGICAL_SETUP + r'''
await logicalBegin();
for(const name of ['', 'system','Bad-name','a'.repeat(32)]){
 edit('db-creation-sql_catalog',name);await $('db-creation-submit').onclick();
 assert.equal(creationPosts.length,0);assert.equal(document.activeElement,$('db-creation-sql_catalog'));
 assert.equal($('db-creation-sql_catalog').getAttribute('aria-invalid'),'true');assert.equal($('db-creation-status').getAttribute('role'),'alert');
}
edit('db-creation-sql_catalog','analytics');assert.equal($('db-creation-sql_catalog').getAttribute('aria-invalid'),'false');
for(const [field,value] of [['display_name','🐻'.repeat(129)],['description','x'.repeat(2049)],['description','bad\u0001']]){
 edit('db-creation-'+field,value);await $('db-creation-submit').onclick();assert.equal(creationPosts.length,0);
 assert.equal(document.activeElement,$('db-creation-'+field));edit('db-creation-'+field,'');
}
edit('db-creation-display_name','');edit('db-creation-description','');creation=logical();await $('db-creation-submit').onclick();
assert.deepEqual(JSON.parse(creationPosts[0].options.body),{sql_catalog:'analytics',display_name:'analytics',description:'',initial_grants:[]});
''')

    def test_logical_states_only_offered_reconcile_resume_stop_and_legacy_root_mode(self):
        self.javascript(SETUP + LOGICAL_SETUP + r'''
for(const locale of ['en','ja']){
 setLanguage(locale);
 for(const progress_code of ['CREATING','VERIFYING','UNKNOWN','BLOCKED','STOPPED','READY']){
  show(logical({progress_code,lifecycle:progress_code==='READY'?'READY':'REGISTERED',next_actions:['reconcile','resume','withdraw','plans','approve']}));noLegacy();
  const ids=visibleIDs();assert.equal(ids.includes('db-creation-resume'),progress_code==='BLOCKED');
  assert.equal(ids.includes('db-creation-withdraw'),['CREATING','VERIFYING','BLOCKED'].includes(progress_code));
  assert.equal(ids.includes('db-creation-reconcile'),!['STOPPED','READY'].includes(progress_code));
  assert.equal(ids.some(id=>/bootstrap|full|approve|review/.test(id)),false);
  if(ids.includes('db-creation-withdraw'))assert.equal($('db-creation-withdraw').textContent,t('Stop creation'));
  assert.equal($('db-creation-progress').getAttribute('role'),'status');
 }
 show(logical({progress_code:'BLOCKED',next_actions:[]}));assert.equal(visibleIDs().includes('db-creation-resume'),false);
 show(process({plans:[plan()],next_actions:['approve']}));assert.ok(visibleIDs().includes('db-creation-review-bootstrap'));
}
assert.equal(creationPosts.length,0);
''')

    def test_logical_guidance_respects_owner_and_offered_actions_in_both_locales(self):
        self.javascript(SETUP + LOGICAL_SETUP + r'''
for(const locale of ['en','ja']){
 setLanguage(locale);
 show(logical({progress_code:'UNKNOWN',next_actions:[]}));
 assert.equal($('db-creation-next-action').textContent,t('The outcome is unknown. Review the status and available actions below.'));
 assert.equal(visibleIDs().includes('db-creation-reconcile'),false);
 show(logical({progress_code:'UNKNOWN',requester_id:'another',next_actions:['reconcile']}));
 assert.equal($('db-creation-next-action').textContent,t('Only the original requester can change this creation.'));
 assert.equal(visibleIDs().includes('db-creation-reconcile'),false);
 show(logical({progress_code:'UNKNOWN',next_actions:['reconcile']}));
 assert.equal($('db-creation-next-action').textContent,t('Reconcile the saved creation to check its outcome. No creation work is resent.'));
 assert.equal(visibleIDs().includes('db-creation-reconcile'),true);
 assert.equal($('db-creation-reconcile').textContent,t('Reconcile creation'));
 show(logical({progress_code:'READY',lifecycle:'RECONCILING',next_actions:[]}));
 assert.equal($('db-creation-next-action').textContent,t('Creation needs review. Use only the actions currently available below.'));
 assert.equal(visibleIDs().includes('db-creation-reconcile'),false);
 assert.equal(visibleIDs().includes('db-creation-sql'),false);
}
assert.equal(creationPosts.length,0);assert.equal(writeCalls().length,0);
''')

    def test_unknown_reconcile_then_explicit_resume_only_revision_body(self):
        self.javascript(SETUP + LOGICAL_SETUP + r'''
show(logical({progress_code:'UNKNOWN',next_actions:['reconcile']}));
creationRoute=async(path)=>{creation=logical({revision:4,progress_code:'BLOCKED',next_actions:['resume']});return {creation_id:'creation',request_id:'ack',accepted_revision:4,action:'RECONCILE',phase:null};};
await $('db-creation-reconcile').onclick();assert.equal(creationPosts.length,1);assert.equal(creationPosts[0].path,C+'/creation/reconcile');
await dbCreationLoad();assert.equal(creationPosts.length,1);assert.equal(dbCreation.process.progress_code,'BLOCKED');
creationRoute=async()=>{creation=logical({revision:5,progress_code:'VERIFYING'});return {creation_id:'creation',request_id:'resume',accepted_revision:5,action:'RESUME',phase:null};};
await $('db-creation-resume').onclick();assert.equal(creationPosts.length,2);assert.equal(creationPosts[1].path,C+'/creation/resume');
assert.deepEqual(JSON.parse(creationPosts[1].options.body),{expected_revision:4});assert.equal(dbCreation.process.progress_code,'VERIFYING');
assert.notEqual(creationPosts[0].options.headers['Idempotency-Key'],creationPosts[1].options.headers['Idempotency-Key']);
for(const progress_code of ['UNKNOWN','STOPPED','CREATING']){show(logical({progress_code,next_actions:['resume']}));await dbCreationMutate('/creation/resume',{expected_revision:3});}
assert.equal(creationPosts.length,2);
''')

    def test_logical_single_one_second_get_poll_reload_deeplink_and_visibility(self):
        self.javascript(TIMER_SETUP + LOGICAL_SETUP + r'''
show(logical());dbCreationRememberURL('creation');dbCreationSchedule();assert.equal(timers.size,1);assert.equal([...timers.values()][0].due-timerNow,1000);
const held=deferred(),original=adminRoute;let details=0;
adminRoute=(path,options)=>path===C+'/creation'?(details++,held.promise):original(path,options);
await advanceTimers(1000);assert.equal(details,1);assert.equal(timers.size,0);
await advanceTimers(6000);assert.equal(details,1);assert.equal(timers.size,0);
held.resolve(logical({progress_code:'VERIFYING'}));await tick();assert.equal(timers.size,1);
adminRoute=original;creation=logical({progress_code:'VERIFYING'});dbCreationClear();await dbCreationLoad();
assert.equal(dbCreation.process.creation_mode,'logical_v1');assert.equal(timers.size,1);assert.equal(creationPosts.length,0);
document.hidden=true;dbCreationSuspend();assert.equal(timers.size,0);await advanceTimers(2000);assert.equal(timers.size,0);
document.hidden=false;await dbCreationLoad();assert.equal(timers.size,1);noLegacy();
show(process());dbCreationSchedule();assert.equal([...timers.values()][0].due-timerNow,5000);
assert.equal(creationPosts.length,0);
''')

    def test_logical_current_lifecycle_overrides_history_in_both_locales(self):
        self.javascript(SETUP + LOGICAL_SETUP + r'''
for(const locale of ['en','ja']){
 setLanguage(locale);
 for(const lifecycle of ['RETIRING','RETIRED'])for(const progress_code of ['CREATING','READY']){
  show(logical({lifecycle,progress_code,next_actions:['reconcile','resume','withdraw']}));noLegacy();
  assert.equal($('db-creation-progress').textContent,t(lifecycle==='RETIRING'?'Retiring database':'Retired database'));
  assert.equal($('db-creation-next-action').textContent,t('This database is retiring or retired. Creation history is available below.'));
  assert.equal(visibleIDs().some(id=>['db-creation-sql','db-creation-resume','db-creation-withdraw','db-creation-reconcile'].includes(id)),false);
 }
 show(logical({lifecycle:'READY',progress_code:'READY',access_source:'none'}));noLegacy();
 assert.equal($('db-creation-next-action').textContent,t('READY; SQL use unavailable for the selected role.'));
 assert.equal(visibleIDs().includes('db-creation-sql'),false);
 for(const lifecycle of ['RECONCILING','REGISTERED']){
  show(logical({lifecycle,progress_code:'READY'}));noLegacy();
  assert.equal($('db-creation-progress').textContent,t(lifecycle==='RECONCILING'?'Reconciling database':'Creation blocked'));
  assert.equal($('db-creation-next-action').textContent,t('Creation needs review. Use only the actions currently available below.'));
  assert.doesNotMatch(contents($('db-creation-process')),/Ready|ready|READY|利用準備完了/);
  assert.equal(visibleIDs().includes('db-creation-sql'),false);
 }
}
assert.equal(creationPosts.length,0);assert.equal(writeCalls().length,0);
''')

    def test_logical_poll_delay_and_observation_follow_active_progress(self):
        self.javascript(TIMER_SETUP + LOGICAL_SETUP + r'''
for(const locale of ['en','ja']){
 setLanguage(locale);
 for(const progress_code of ['CREATING','VERIFYING','READY','BLOCKED','STOPPED','UNKNOWN']){
  const active=['CREATING','VERIFYING'].includes(progress_code);
  show(logical({progress_code,lifecycle:progress_code==='READY'?'READY':'REGISTERED'}));dbCreationSchedule();
  assert.equal(timers.size,1);assert.equal([...timers.values()][0].due-timerNow,active?1000:5000);
  assert.ok($('db-creation-observation').textContent.includes(t(active?'Automatic status checking every second.':'Automatic status checking every 5 seconds.')));
 }
 for(const lifecycle of ['RETIRING','RETIRED']){
  show(logical({lifecycle,progress_code:'CREATING'}));dbCreationSchedule();
  assert.equal(timers.size,1);assert.equal([...timers.values()][0].due-timerNow,5000);
 }
}
assert.equal(creationPosts.length,0);assert.equal(writeCalls().length,0);
''')

    def test_logical_resume_uncertainty_storage_failure_reload_and_exact_retry(self):
        self.javascript(SETUP + LOGICAL_SETUP + r'''
show(logical({progress_code:'BLOCKED',next_actions:['resume']}));
localStorage.setItem=()=>{throw Error('quota');};creationRoute=async()=>{throw Error('lost ACK');};
await $('db-creation-resume').onclick();const first=creationPosts[0],frozen=structuredClone(dbCreation.pending),key=dbCreation.storageKey;
assert.match($('db-creation-status').textContent,/retained only in this session/);assert.equal(frozen.path,C+'/creation/resume');
dbCreationClear();creation=logical({progress_code:'UNKNOWN',next_actions:['reconcile']});await dbCreationLoad();
assert.equal(creationPosts.length,1);assert.equal(dbCreation.pending.key,frozen.key);assert.equal(dbCreation.pending.path,frozen.path);
creationRoute=async()=>({creation_id:'creation',request_id:'ack',accepted_revision:3,action:'RESUME',phase:null});
await $('db-creation-retry').onclick();assert.equal(creationPosts.length,2);const retried=creationPosts[1];
assert.equal(retried.path,first.path);assert.equal(retried.options.body,first.options.body);assert.equal(retried.options.headers['Idempotency-Key'],first.options.headers['Idempotency-Key']);
assert.equal(retried.options.headers['X-Shirokuma-Environment'],'envA');assert.equal(dbCreation.pending,null);
assert.equal(JSON.parse(dbCreationSaved.get(key)).pending,null);
''')

    def test_logical_create_uncertain_503_retains_frozen_request_until_explicit_retry(self):
        self.javascript(SETUP + LOGICAL_SETUP + r'''
await logicalBegin();creationRoute=async()=>({http:503,body:{error:'OPERATOR_UNAVAILABLE',message:'Database creation request rejected'}});
await $('db-creation-submit').onclick();assert.ok(dbCreation.pending);const first=creationPosts[0],pending=structuredClone(dbCreation.pending);
await dbCreationLoad();assert.equal(creationPosts.length,1);assert.equal(dbCreation.pending.key,pending.key);
creation=logical();creationRoute=async()=>({creation_id:'creation',request_id:'ack',accepted_revision:3,action:'CREATE',phase:null});
await $('db-creation-retry').onclick();assert.equal(creationPosts.length,2);assert.equal(dbCreation.pending,null);
assert.equal(creationPosts[1].options.body,first.options.body);assert.equal(creationPosts[1].options.headers['Idempotency-Key'],first.options.headers['Idempotency-Key']);
await dbCreationLoad();assert.equal(creationPosts.length,2);noLegacy();
''')

    def test_fresh_session_and_environment_preflight_fence_create_and_resume(self):
        for action in ('create', 'resume'):
            for change in ("session.maximum_role='data_engineer'", "session.username='another'", "capability.environment_id='envB'"):
                with self.subTest(action=action, change=change):
                    self.javascript(SETUP + LOGICAL_SETUP + r'''
await logicalBegin();
''' + ("show(logical({progress_code:'BLOCKED',next_actions:['resume']}));" if action == 'resume' else '') + r'''
const action=$('db-creation-''' + ('resume' if action == 'resume' else 'submit') + r'''');
''' + change + r''';await action.onclick();assert.equal(creationPosts.length,0);
''')

    def test_delayed_logical_principals_cannot_restore_previous_role(self):
        self.javascript(SETUP + LOGICAL_SETUP + r'''
dbCreation.process=null;const held=deferred(),original=adminRoute;adminRoute=(path,options)=>path===C+'/principals'?held.promise:original(path,options);
const loading=dbCreationLoad();await tick();session.maximum_role='data_engineer';dbAdminSession(session);
held.resolve({principals:[{principal_id:'private-old-account',maximum_role:'admin'}]});await loading;
assert.deepEqual(dbCreation.principals,[]);assert.equal(creationPosts.length,0);
''')

    def test_ready_logical_sql_preserves_worksheets_and_inventory_schema_checks(self):
        self.javascript(SETUP + LOGICAL_SETUP + r'''
show(logical({lifecycle:'READY',progress_code:'READY',next_actions:[]}));$('sql').value='SELECT preserved';dbSave();const original=structuredClone(worksheets);
await $('db-creation-sql').onclick();assert.deepEqual(worksheets.slice(0,-1),original);
assert.equal(dbCurrent().database_id,'db');assert.equal(dbCurrent().schema_id,'raw');assert.equal(dbCurrent().sql,'');assert.equal(dbCurrent().mode,'read-only');
assert.equal(creationPosts.length,0);assert.equal(writeCalls().length,0);
show(logical({lifecycle:'READY',progress_code:'READY',access_source:'none'}));assert.equal(visibleIDs().includes('db-creation-sql'),false);
assert.equal($('db-creation-next-action').textContent,t('READY; SQL use unavailable for the selected role.'));
show(logical({lifecycle:'READY',progress_code:'READY',next_actions:[]}));inventory[0].capabilities.query=false;const count=worksheets.length;
await $('db-creation-sql').onclick();assert.equal(worksheets.length,count);assert.equal(writeCalls().length,0);
''')

    def test_logical_unavailable_guidance_hides_implementation_details(self):
        self.javascript(SETUP + LOGICAL_SETUP + r'''
for(const blocker of ['PRINCIPAL_STOPPED','WAREHOUSE_START_REQUIRED','FOUNDATION_NOT_READY','OPERATOR_UNAVAILABLE','RECONCILIATION_REQUIRED','CREATION_FAILED']){
 capability.available=false;capability.blocker=blocker;await logicalBegin();
 for(const locale of ['en','ja']){setLanguage(locale);dbCreationRender();assert.equal($('db-creation-submit').disabled,true);noLegacy();assert.equal(contents($('db-creation')).includes(blocker),false);
  if(blocker==='RECONCILIATION_REQUIRED')assert.ok(contents($('db-creation')).includes(t('Reconcile the saved creation to check its outcome. No creation work is resent.')));
  if(blocker==='CREATION_FAILED')assert.ok(contents($('db-creation')).includes(t('Creation failed. Ask an administrator to review the status and available recovery actions.')));}
}
assert.equal(creationPosts.length,0);
''')


if __name__ == '__main__':
    unittest.main()
