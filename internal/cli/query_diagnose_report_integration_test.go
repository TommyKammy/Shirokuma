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

func TestQueryDiagnoseReportActualPythonReader(t *testing.T) {
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
from tools.local_experiment.query_diagnose_report import run as R
from test_query_diagnose_report_contract import documents,failed_documents,TOKEN
from tools.local_experiment.query_diagnose import run as D
base=pathlib.Path(sys.argv[2]).resolve();mode=sys.argv[3];R.STATE=base/'records'
calls=[]
def forbidden(*a,**k):
    calls.append('forbidden');raise AssertionError('external dependency invoked')
socket.create_connection=forbidden;socket.socket.connect=forbidden
D.SourceLease=forbidden;D.environment=forbidden
lock=None
if mode!='absent':R.STATE.mkdir(mode=0o700)
if mode not in ('missing','absent','known-cost'):
    folder=R.STATE/TOKEN;folder.mkdir(mode=0o700)
    (folder/'run.lock').touch(mode=0o600)
    value,spans=failed_documents(interrupted=mode=='interrupted') if mode in ('failed','interrupted') else documents(2 if mode=='v2' else 1)
    (folder/'summary.json').write_text(json.dumps(value));(folder/'summary.json').chmod(0o600)
    (folder/'spans.jsonl').write_text(''.join(json.dumps(s)+'\n' for s in spans));(folder/'spans.jsonl').chmod(0o600)
    if mode in ('invalid','active'):(folder/'summary.json').write_text('PRIVATE_UNTRUSTED_TEXT')
    if mode=='active':
        lock=os.open(folder/'run.lock',os.O_RDONLY);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
if mode=='known-cost':
    for index,cost in enumerate((0.1,0.2,0.3)):
        identity='cde'[index]*32;folder=R.STATE/identity;folder.mkdir(mode=0o700)
        (folder/'run.lock').touch(mode=0o600)
        value,spans=documents(identity=identity)
        trace=f'{index+1:032x}';value['audit']['trace_id']=trace
        for j,record in enumerate(value['audit']['records']):
            record['trace_id']=trace;record['event_id']=f'11111111-1111-4111-8111-{index*3+j+1:012d}'
        for span in spans:span['trace_id']=trace
        value['audit']['records'][1]['inference_cost_estimate']=cost
        (folder/'summary.json').write_text(json.dumps(value));(folder/'summary.json').chmod(0o600)
        (folder/'spans.jsonl').write_text(''.join(json.dumps(s)+'\n' for s in spans));(folder/'spans.jsonl').chmod(0o600)
def marks():
    if not R.STATE.exists():return {}
    return {str(p.relative_to(R.STATE)):(hashlib.sha256(p.read_bytes()).hexdigest(),R.fingerprint(p.stat()))
            for p in R.STATE.rglob('*') if p.is_file()}
before=marks()
code=R.main(['report','--request-stdin'])
assert not calls and marks()==before
if lock is not None:os.close(lock)
if R.STATE.exists():
    for folder in R.STATE.iterdir():
        fd=os.open(folder/'run.lock',os.O_RDONLY);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);os.close(fd)
p=base/'harness-check.json';p.write_text(json.dumps({'inputs_unchanged':True,'external_calls':0,'locks_released':True,'python_exit':code}));p.chmod(0o600)
raise SystemExit(code)
`
	for _, tc := range []struct {
		mode, state string
		exit        int
	}{{"v1", "passed", 0}, {"v2", "passed", 0}, {"failed", "failed", 0}, {"interrupted", "interrupted", 0}, {"active", "active", 0}, {"invalid", "invalid", 1}, {"missing", "missing", 1}, {"absent", "", 0}, {"known-cost", "passed", 0}} {
		t.Run(tc.mode, func(t *testing.T) {
			output := t.TempDir()
			if err := os.Chmod(output, 0700); err != nil {
				t.Fatal(err)
			}
			request := qdrTestRequest()
			if tc.mode == "missing" {
				request.Request = qdrTestLine(t, queryDiagnoseReportRequest{Token: qdrPtr(strings.Repeat("b", 32))})
			}
			child := execQueryDiagnoseReportChild{command: func(r dataChildRequest) (*exec.Cmd, error) {
				cmd := exec.Command(python, "-I", "-B", "-c", harness, root, output, tc.mode)
				cmd.Dir = root
				cmd.Env = []string{"PATH=/usr/bin:/bin"}
				cmd.Stdin = bytes.NewReader(r.Request)
				return cmd, nil
			}}
			var out bytes.Buffer
			err := runQueryDiagnoseReportCommand(context.Background(), child, request, &out)
			if ExitCode(err) != tc.exit || out.Len() == 0 || strings.Contains(out.String(), "PRIVATE_") {
				t.Fatalf("%s: %v %s", tc.mode, err, &out)
			}
			var result queryDiagnosisReport
			if json.Unmarshal(out.Bytes(), &result) != nil {
				t.Fatal("invalid public JSON")
			}
			if tc.state == "" {
				if len(result.Runs) != 0 || result.Inventory.Root != "absent" {
					t.Fatal("absent inventory mismatch")
				}
			} else if tc.mode == "known-cost" {
				if len(result.Runs) != 3 || len(result.Groups) != 1 || result.Groups[0].InferenceCostEstimate == nil || *result.Groups[0].InferenceCostEstimate != 0.6000000000000001 {
					t.Fatalf("known costs diverged: %s", &out)
				}
			} else if len(result.Runs) != 1 || result.Runs[0].State != tc.state {
				t.Fatalf("state mismatch: %s", &out)
			}
			raw, e := os.ReadFile(filepath.Join(output, "harness-check.json"))
			if e != nil {
				t.Fatal(e)
			}
			var proof struct {
				InputsUnchanged bool `json:"inputs_unchanged"`
				ExternalCalls   int  `json:"external_calls"`
				LocksReleased   bool `json:"locks_released"`
				PythonExit      int  `json:"python_exit"`
			}
			if json.Unmarshal(raw, &proof) != nil || !proof.InputsUnchanged || !proof.LocksReleased || proof.ExternalCalls != 0 || proof.PythonExit != tc.exit {
				t.Fatal("read-only boundary mismatch")
			}
		})
	}
}
