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

// Exercise the real two-folder SourceLease and durable Owner. Only external
// observer/model/audit dependencies are fake; no API or model is contacted.
func TestQueryDiagnoseHistoricalSourceLeaseAndOwnerCompatibility(t *testing.T) {
	root, err := filepath.Abs("../..")
	if err != nil {
		t.Fatal(err)
	}
	python := filepath.Join(root, dataPython)
	if _, err := os.Stat(python); err != nil {
		t.Skip("dedicated Python unavailable; no installation attempted")
	}
	harness := `
import fcntl,hashlib,json,os,pathlib,sys
sys.path[:0]=[sys.argv[1],sys.argv[1]+'/tests']
from tools.local_experiment.query_diagnose import run as R,contract as D
from test_query_diagnose_snapshot_source import receipt
from test_query_diagnose_run import receipt as legacy_receipt,FakeAudit
from test_query_diagnose_contract import projections
base=pathlib.Path(sys.argv[2]).resolve();mode=sys.argv[3]
R.STATE=base/'diagnosis';R.READ_STATE=base/'v1';R.SNAPSHOT_READ_STATE=base/'v2'
for p in (R.READ_STATE,R.SNAPSHOT_READ_STATE):p.mkdir(mode=0o700)
value=legacy_receipt() if mode=='legacy' else receipt()
source_token=value['evidence_token']
folder=(R.READ_STATE if mode=='legacy' else R.SNAPSHOT_READ_STATE)/source_token
folder.mkdir(mode=0o700)
lock=folder/'run.lock';lock.touch(mode=0o600)
source_path=folder/'summary.json'
if mode=='mutated':value['observation']['queries']['worker'][2]['sql_sha256']='0'*64
source_path.write_bytes(R.C.canonical(value));source_path.chmod(0o600)
if mode=='ambiguous':
    (R.READ_STATE/source_token).mkdir(mode=0o700)
    (R.READ_STATE/source_token/'summary.json').write_bytes(b'broken alternative')
initial=source_path.read_bytes();initial_stat=R.fingerprint(source_path.stat())
events=[]
class Model:
    def complete(self,phase,evidence):
        events.append('model')
        text=json.dumps(evidence)
        assert phase=='query_diagnose' and 'smoke.fixture_v1' not in text and 'SELECT ' not in text
        assert 'snapshot_id' not in text
        return {'content':D.expected_diagnosis(evidence)}
def environment():events.append('environment');return {},Model()
class Audit(FakeAudit):
    def start(self):events.append('audit');super().start()
def observer(source,token,progress):
    events.append('observer')
    if mode!='legacy':
        assert source['schema_version']==2 and source['profile']=='snapshot-reader-v2'
        assert source['identity']['current_snapshot_id']!=source['selection']['snapshot_id']
        assert 'BIGINT' in D.select_sql(source)
    assert source['summary_sha256']==hashlib.sha256(initial).hexdigest()
    with lock.open('rb') as held:
        try:fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:events.append('source-lock-held')
        else:raise AssertionError('source lease missing')
    if mode=='interrupted':raise KeyboardInterrupt()
    result={'state':'verified','cleanup_verified':True,**projections(source)}
    if mode=='changed':
        value['finished_at']='2026-09-18T00:02:00+00:00'
        source_path.write_bytes(R.C.canonical(value))
    progress(result)
    return result
def invoke(identity,read_token,emit):
    assert read_token==source_token
    return R.invoke(identity,read_token,emit,environment_factory=environment,observer=observer,audit_factory=Audit)
exit_code=R.main(['diagnose','--evidence-token',sys.argv[4],'--request-stdin'],invoker=invoke)
with lock.open('rb') as released:
    fcntl.flock(released,fcntl.LOCK_EX|fcntl.LOCK_NB)
    fcntl.flock(released,fcntl.LOCK_UN)
check={'events':events,'source_unchanged':source_path.read_bytes()==initial and R.fingerprint(source_path.stat())==initial_stat,'lock_released':True,'initial_sha256':hashlib.sha256(initial).hexdigest()}
p=base/'harness-check.json';p.write_text(json.dumps(check));p.chmod(0o600)
raise SystemExit(exit_code)
`
	for _, tc := range []struct {
		mode  string
		exit  int
		code  string
		model bool
	}{{"historical", 0, "", true}, {"legacy", 0, "", true}, {"ambiguous", 1, "SOURCE_INVALID", false}, {"mutated", 1, "SOURCE_INVALID", false}, {"changed", 1, "SOURCE_CHANGED", false}, {"interrupted", 130, "INTERRUPTED", false}} {
		t.Run(tc.mode, func(t *testing.T) {
			output := t.TempDir()
			if err := os.Chmod(output, 0700); err != nil {
				t.Fatal(err)
			}
			request := queryDiagnoseTestRequest()
			request.Request = []byte(`{"read_token":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}`)
			child := execQueryDiagnoseChild{command: func(r dataChildRequest) (*exec.Cmd, error) {
				cmd := exec.Command(python, "-I", "-B", "-c", harness, root, output, tc.mode, r.Token)
				cmd.Env = []string{"PATH=/usr/bin:/bin"}
				cmd.Dir = root
				cmd.Stdin = bytes.NewReader(r.Request)
				return cmd, nil
			}}
			var out bytes.Buffer
			err := runQueryDiagnoseCommand(context.Background(), child, request, &out)
			if ExitCode(err) != tc.exit {
				t.Fatalf("%s: %v %s", tc.mode, err, &out)
			}
			if tc.exit == 0 && !strings.Contains(out.String(), `"result":"passed"`) {
				t.Fatal("success missing")
			}
			if tc.exit != 0 && (strings.Contains(out.String(), `"result":"passed"`) || !strings.Contains(out.String(), tc.code)) {
				t.Fatalf("false success/failure code: %s", &out)
			}
			raw, e := os.ReadFile(filepath.Join(output, "harness-check.json"))
			if e != nil {
				t.Fatal(e)
			}
			var check struct {
				Events          []string `json:"events"`
				SourceUnchanged bool     `json:"source_unchanged"`
				LockReleased    bool     `json:"lock_released"`
				InitialSHA      string   `json:"initial_sha256"`
			}
			if e = json.Unmarshal(raw, &check); e != nil {
				t.Fatal(e)
			}
			if !check.LockReleased || tc.mode != "changed" && !check.SourceUnchanged {
				t.Fatal("source bytes/fingerprint or lease cleanup changed")
			}
			modelCalls := 0
			for _, event := range check.Events {
				if event == "model" {
					modelCalls++
				}
			}
			if tc.model && modelCalls != 1 || !tc.model && modelCalls != 0 {
				t.Fatal("model call count differs")
			}
			if (tc.mode == "ambiguous" || tc.mode == "mutated") && len(check.Events) != 0 {
				t.Fatal("invalid source reached external environment")
			}
			savedRaw, e := os.ReadFile(filepath.Join(output, "diagnosis", request.Token, "summary.json"))
			if e != nil {
				t.Fatal(e)
			}
			var saved map[string]any
			if e = json.Unmarshal(savedRaw, &saved); e != nil {
				t.Fatal(e)
			}
			if saved["original_select_reexecuted"] != false || saved["cloud_calls"] != float64(0) || saved["retries"] != float64(0) || saved["model_calls"] != float64(modelCalls) {
				t.Fatal("owner replay/call metadata differs")
			}
			if tc.mode == "historical" {
				source := saved["source"].(map[string]any)
				if source["schema_version"] != float64(2) || source["summary_sha256"] != check.InitialSHA {
					t.Fatal("v2 source binding absent")
				}
				if source["identity"].(map[string]any)["current_snapshot_id"] == source["selection"].(map[string]any)["snapshot_id"] {
					t.Fatal("selected snapshot replaced by head")
				}
			}
		})
	}
}
