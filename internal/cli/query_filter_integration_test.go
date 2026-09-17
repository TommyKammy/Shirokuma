package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestQueryFilterDiagnosisRealSourceLeaseAndOwner(t *testing.T) {
	root, err := filepath.Abs("../..")
	if err != nil {
		t.Fatal(err)
	}
	python := filepath.Join(root, dataPython)
	if _, err := os.Stat(python); err != nil {
		t.Skip("dedicated Python unavailable; no installation attempted")
	}
	harness := `
import fcntl,hashlib,json,os,pathlib,socket,sys
sys.path[:0]=[sys.argv[1],sys.argv[1]+'/tests']
from tools.local_experiment.query_diagnose import run as R,contract as D
from test_query_diagnose_filter import receipt
from test_query_diagnose_run import FakeAudit
from test_query_diagnose_contract import projections
base=pathlib.Path(sys.argv[2]).resolve();mode=sys.argv[3]
R.STATE=base/'diagnosis';R.READ_STATE=base/'v1';R.SNAPSHOT_READ_STATE=base/'v2';R.FILTER_READ_STATE=base/'v3'
for p in (R.READ_STATE,R.SNAPSHOT_READ_STATE,R.FILTER_READ_STATE):p.mkdir(mode=0o700)
def forbidden(*a,**k):raise AssertionError('network call forbidden')
socket.create_connection=forbidden;socket.socket.connect=forbidden
value=receipt();source_token=value['evidence_token'];folder=R.FILTER_READ_STATE/source_token;folder.mkdir(mode=0o700)
lock=folder/'run.lock';lock.touch(mode=0o600);path=folder/'summary.json'
if mode=='mutated-sql':value['observation']['queries']['worker'][2]['sql_sha256']='0'*64
if mode=='mutated-literal':value['request']['where']['literal']='2'
path.write_bytes(D.canonical(value));path.chmod(0o600)
if mode=='ambiguous':(R.READ_STATE/source_token).mkdir(mode=0o700)
initial=path.read_bytes();mark=R.fingerprint(path.stat());events=[]
class Model:
    def complete(self,phase,evidence):
        events.append('model');text=D.canonical(evidence).decode()
        assert phase=='query_diagnose' and not any(x in text for x in ('where','literal','snapshot_id','smoke.fixture_v1','SELECT '))
        return {'content':D.expected_diagnosis(evidence)}
def environment():events.append('environment');return {},Model()
class Audit(FakeAudit):
    def start(self):events.append('audit');super().start()
def observer(source,token,progress):
    events.append('observer')
    assert source['schema_version']==3 and source['profile']=='filtered-snapshot-reader-v3'
    assert source['identity']['current_snapshot_id']!=source['selection']['snapshot_id']
    assert ' WHERE ' in D.select_sql(source) and 'BIGINT' in D.select_sql(source)
    assert source['summary_sha256']==hashlib.sha256(initial).hexdigest()
    with lock.open('rb') as held:
        try:fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:events.append('source-lock-held')
        else:raise AssertionError('source lock not held')
    if mode=='interrupted':raise KeyboardInterrupt()
    result={'state':'verified','cleanup_verified':True,**projections(source)}
    if mode=='changed':value['request']['where']['literal']='3';path.write_bytes(D.canonical(value))
    progress(result);return result
def invoke(identity,read_token,emit):
    assert read_token==source_token
    return R.invoke(identity,read_token,emit,environment_factory=environment,observer=observer,audit_factory=Audit)
code=R.main(['diagnose','--evidence-token',sys.argv[4],'--request-stdin'],invoker=invoke)
with lock.open('rb') as held:fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
proof={'events':events,'source_unchanged':path.read_bytes()==initial and R.fingerprint(path.stat())==mark,'lock_released':True,'sha256':hashlib.sha256(initial).hexdigest()}
p=base/'harness-check.json';p.write_text(json.dumps(proof));p.chmod(0o600)
raise SystemExit(code)
`
	for _, tc := range []struct {
		mode  string
		exit  int
		code  string
		model bool
	}{{"passed", 0, "", true}, {"ambiguous", 1, "SOURCE_INVALID", false}, {"mutated-sql", 1, "SOURCE_INVALID", false}, {"mutated-literal", 1, "SOURCE_INVALID", false}, {"changed", 1, "SOURCE_CHANGED", false}, {"interrupted", 130, "INTERRUPTED", false}} {
		t.Run(tc.mode, func(t *testing.T) {
			output := t.TempDir()
			_ = os.Chmod(output, 0700)
			request := queryDiagnoseTestRequest()
			request.Request = []byte(`{"read_token":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}`)
			child := execQueryDiagnoseChild{command: func(r dataChildRequest) (*exec.Cmd, error) {
				cmd := exec.Command(python, "-I", "-B", "-c", harness, root, output, tc.mode, r.Token)
				cmd.Dir = root
				cmd.Env = []string{"PATH=/usr/bin:/bin"}
				cmd.Stdin = bytes.NewReader(r.Request)
				return cmd, nil
			}}
			var out bytes.Buffer
			err := runQueryDiagnoseCommand(context.Background(), child, request, &out)
			if ExitCode(err) != tc.exit || (tc.exit == 0) != strings.Contains(out.String(), `"result":"passed"`) || tc.code != "" && !strings.Contains(out.String(), tc.code) {
				t.Fatalf("%v %s", err, &out)
			}
			var proof struct {
				Events          []string `json:"events"`
				SourceUnchanged bool     `json:"source_unchanged"`
				LockReleased    bool     `json:"lock_released"`
				SHA             string   `json:"sha256"`
			}
			raw, e := os.ReadFile(filepath.Join(output, "harness-check.json"))
			if e != nil || json.Unmarshal(raw, &proof) != nil {
				t.Fatal("missing harness evidence")
			}
			if !proof.LockReleased || tc.mode != "changed" && !proof.SourceUnchanged {
				t.Fatal("source changed or lock not released")
			}
			calls := 0
			for _, event := range proof.Events {
				if event == "model" {
					calls++
				}
			}
			if tc.model && calls != 1 || !tc.model && calls != 0 {
				t.Fatal("model call count differs")
			}
			if (tc.mode == "ambiguous" || strings.HasPrefix(tc.mode, "mutated")) && len(proof.Events) != 0 {
				t.Fatal("invalid source reached environment")
			}
			raw, e = os.ReadFile(filepath.Join(output, "diagnosis", request.Token, "summary.json"))
			if e != nil {
				t.Fatal(e)
			}
			var saved map[string]any
			if json.Unmarshal(raw, &saved) != nil {
				t.Fatal("bad saved summary")
			}
			if saved["original_select_reexecuted"] != false || saved["retries"] != float64(0) || saved["cloud_calls"] != float64(0) || saved["model_calls"] != float64(calls) {
				t.Fatal("owner call/replay counts differ")
			}
			if tc.exit == 0 {
				source := saved["source"].(map[string]any)
				if source["profile"] != queryFilterCase || source["summary_sha256"] != proof.SHA {
					t.Fatal("v3 source binding absent")
				}
				if strings.Contains(out.String(), `"where"`) || strings.Contains(out.String(), `"literal"`) {
					t.Fatal("predicate exposed by diagnosis")
				}
			}
		})
	}
}

func TestQueryFilterSavedDiagnosisReportActualPython(t *testing.T) {
	root, e := filepath.Abs("../..")
	if e != nil {
		t.Fatal(e)
	}
	python := filepath.Join(root, dataPython)
	if _, e := os.Stat(python); e != nil {
		t.Skip("dedicated Python unavailable; no installation attempted")
	}
	harness := `
import hashlib,json,pathlib,socket,sys
sys.path[:0]=[sys.argv[1],sys.argv[1]+'/tests']
from tools.local_experiment.query_diagnose_report import run as R
from tools.local_experiment.query_diagnose import run as D
from test_query_diagnose_filter import report_documents
base=pathlib.Path(sys.argv[2]).resolve();R.STATE=base/'records';R.STATE.mkdir(mode=0o700)
def forbidden(*a,**k):raise AssertionError('external call forbidden')
socket.create_connection=forbidden;socket.socket.connect=forbidden;D.SourceLease=forbidden;D.environment=forbidden
value,spans=report_documents();folder=R.STATE/value['evidence_token'];folder.mkdir(mode=0o700);(folder/'run.lock').touch(mode=0o600)
(folder/'summary.json').write_text(json.dumps(value));(folder/'summary.json').chmod(0o600)
(folder/'spans.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in spans));(folder/'spans.jsonl').chmod(0o600)
def marks():return {str(p):[hashlib.sha256(p.read_bytes()).hexdigest(),R.fingerprint(p.stat())] for p in folder.iterdir()}
before=marks();code=R.main(['report','--request-stdin']);assert marks()==before
raise SystemExit(code)
`
	output := t.TempDir()
	_ = os.Chmod(output, 0700)
	request := qdrTestRequest()
	child := execQueryDiagnoseReportChild{command: func(r dataChildRequest) (*exec.Cmd, error) {
		cmd := exec.Command(python, "-I", "-B", "-c", harness, root, output)
		cmd.Dir = root
		cmd.Env = []string{"PATH=/usr/bin:/bin"}
		cmd.Stdin = bytes.NewReader(r.Request)
		return cmd, nil
	}}
	var out bytes.Buffer
	if e := runQueryDiagnoseReportCommand(context.Background(), child, request, &out); e != nil {
		t.Fatalf("%v %s", e, &out)
	}
	var result queryDiagnosisReport
	if json.Unmarshal(out.Bytes(), &result) != nil || len(result.Runs) != 1 || result.Runs[0].SourceProfile == nil || *result.Runs[0].SourceProfile != queryFilterCase || result.Runs[0].State != "passed" {
		t.Fatalf("profile projection differs: %s", &out)
	}
	if strings.Contains(out.String(), `"where"`) || strings.Contains(out.String(), `"literal"`) {
		t.Fatal("saved report exposed filter predicate")
	}
}

func TestQueryFilterPythonContractAndCodesAgree(t *testing.T) {
	root, e := filepath.Abs("../..")
	if e != nil {
		t.Fatal(e)
	}
	python := filepath.Join(root, dataPython)
	if _, e := os.Stat(python); e != nil {
		t.Skip("dedicated Python unavailable; no installation attempted")
	}
	script := `
import json,sys
sys.path[:0]=[sys.argv[1],sys.argv[1]+'/tests']
from tools.local_experiment.query_filter import contract as C,run as R
from test_query_read_contract import schema,result
from test_query_snapshots_contract import metadata,OLD
records=[]
for snapshot in (None,str(OLD)):
    for hidden in (False,True):
        request={'command':'select','table':'smoke.fixture_v1','columns':['name'] if hidden else ['id','name'],'limit':2,'snapshot_id':snapshot,'where':{'column':'id','literal':'-0'}}
        window=C.project_window(metadata(),request,schema())
        public=C.project_rows(result(C.base_request(request),[['雪\u007f']] if hidden else [[0,'雪\u007f']]),request,schema(),window)
        records.append({'request':request,'public':public})
typed=[]
for kind,literal in [('bigint','-0'),('integer','2147483647'),('bigint','-9223372036854775808'),('decimal(3,2)','1.2300'),('decimal(3,2)','-0.000'),('decimal(38,6)','99999999999999999999999999999999.999999'),('date','2024-02-29'),('boolean','false')]:
    request={'command':'select','table':'smoke.fixture_v1','columns':['id'],'limit':1,'snapshot_id':None,'where':{'column':'id','literal':literal}}
    predicate=C.resolve_predicate(request,[{'name':'id','type':kind,'nullable':True}])
    typed.append({'kind':kind,'literal':literal,'value':predicate['value']})
print(json.dumps({'records':records,'typed':typed,'codes':sorted(R.PUBLIC_CODES)}))
`
	cmd := exec.Command(python, "-I", "-B", "-c", script, root)
	cmd.Env = []string{"PATH=/usr/bin:/bin"}
	raw, e := cmd.Output()
	if e != nil {
		t.Fatal(e)
	}
	var result struct {
		Records []struct {
			Request json.RawMessage `json:"request"`
			Public  json.RawMessage `json:"public"`
		} `json:"records"`
		Typed []struct {
			Kind    string `json:"kind"`
			Literal string `json:"literal"`
			Value   any    `json:"value"`
		} `json:"typed"`
		Codes []string `json:"codes"`
	}
	if json.Unmarshal(raw, &result) != nil {
		t.Fatal("invalid pure fixture output")
	}
	for _, record := range result.Records {
		input, e := decodeQueryFilterRequest(record.Request, "select")
		if e != nil || !queryFilterObservation(record.Public, input) {
			t.Fatal("Python projected value rejected")
		}
	}
	for _, record := range result.Typed {
		if !queryFilterTypedLiteral(record.Literal, record.Kind) || queryFilterValue(record.Literal, record.Kind) != record.Value {
			t.Fatal("Python typed normalization differs")
		}
	}
	allowed := map[string]bool{}
	for _, code := range strings.Fields(queryReadPublicCodes + querySnapshotsExtraCodes + queryFilterExtraCodes) {
		allowed[code] = true
	}
	for _, code := range result.Codes {
		if !queryFilterErrorCode(code) || !allowed[code] {
			t.Fatalf("missing Python code %s", code)
		}
		delete(allowed, code)
	}
	if len(allowed) != 0 {
		t.Fatalf("Go accepts extra error codes: %v", allowed)
	}
}

func TestQueryFilterActualPythonOwner(t *testing.T) {
	root, e := filepath.Abs("../..")
	if e != nil {
		t.Fatal(e)
	}
	python := filepath.Join(root, dataPython)
	if _, e := os.Stat(python); e != nil {
		t.Skip("dedicated Python unavailable; no installation attempted")
	}
	harness := `
import contextlib,pathlib,socket,sys
sys.path[:0]=[sys.argv[1],sys.argv[1]+'/tests']
from tools.local_experiment.query_filter import run as R,contract as C
from test_query_filter_profile import observed
R.STATE=pathlib.Path(sys.argv[2]).resolve()/'filtered';mode=sys.argv[3]
def forbidden(*a,**k):raise AssertionError('network call forbidden')
socket.create_connection=forbidden;socket.socket.connect=forbidden
if mode=='late-failure':
    original=R.SHARED.owner_lock
    @contextlib.contextmanager
    def lock(output):
        with original(output):yield
        raise OSError('PRIVATE_AFTER_RESULT')
    R.SHARED.owner_lock=lock
def observer(value,token,progress):
    if mode=='not-retained':raise C.ContractError('SNAPSHOT_NOT_RETAINED')
    if mode=='wrong-equality':return observed(value,[[2,'private-row-sentinel']])
    if mode=='interrupted':raise KeyboardInterrupt()
    journal,public=observed(value)
    if mode=='incomplete':journal['cleanup_verified']=False
    progress(journal);return journal,public
def invoke(token,value,emit):return R.invoke(token,value,emit,observer=observer)
raise SystemExit(R.main(['select','--evidence-token',sys.argv[4],'--request-stdin'],invoker=invoke))
`
	for _, tc := range []struct {
		mode       string
		historical bool
		exit       int
		code       string
	}{{"current", false, 0, ""}, {"historical", true, 0, ""}, {"not-retained", true, 1, "SNAPSHOT_NOT_RETAINED"}, {"wrong-equality", false, 1, "FILTER_RESULT_INVALID"}, {"incomplete", false, 1, "SNAPSHOT_CLEANUP_UNVERIFIED"}, {"interrupted", false, 130, "INTERRUPTED"}, {"late-failure", false, 1, ""}} {
		t.Run(tc.mode, func(t *testing.T) {
			output := t.TempDir()
			_ = os.Chmod(output, 0700)
			r := filterRequest(tc.historical)
			if tc.historical {
				value, _ := decodeQueryFilterRequest(r.Request, "select")
				id := "8088807033386546122"
				value.SnapshotID = &id
				r.Request, _ = json.Marshal(value)
			}
			child := execQueryFilterChild{command: func(r dataChildRequest) (*exec.Cmd, error) {
				cmd := exec.Command(python, "-I", "-B", "-c", harness, root, output, tc.mode, r.Token)
				cmd.Env = []string{"PATH=/usr/bin:/bin"}
				cmd.Dir = root
				cmd.Stdin = bytes.NewReader(r.Request)
				return cmd, nil
			}}
			var out bytes.Buffer
			err := runQueryFilterCommand(context.Background(), child, r, &out)
			if ExitCode(err) != tc.exit || (tc.exit == 0) != strings.Contains(out.String(), `"result":"passed"`) || tc.code != "" && !strings.Contains(out.String(), tc.code) || strings.Contains(out.String(), "PRIVATE_AFTER_RESULT") {
				t.Fatalf("%v %s", err, &out)
			}
			raw, e := os.ReadFile(filepath.Join(output, "filtered", r.Token, "summary.json"))
			if e != nil {
				t.Fatal(e)
			}
			if bytes.Contains(raw, []byte("private-row-sentinel")) || bytes.Contains(raw, []byte(`"rows":`)) {
				t.Fatal("saved receipt contains explicit rows")
			}
			var saved map[string]any
			if json.Unmarshal(raw, &saved) != nil {
				t.Fatal("invalid saved receipt")
			}
			if saved["schema_version"] != float64(3) || saved["profile"] != queryFilterCase || saved["model_calls"] != float64(0) || saved["cloud_calls"] != float64(0) || saved["invocations"] != float64(1) {
				t.Fatal("owner profile/call counts differ")
			}
			if tc.exit == 0 && saved["cleanup_verified"] != true {
				t.Fatal("passed without cleanup")
			}
		})
	}
}
