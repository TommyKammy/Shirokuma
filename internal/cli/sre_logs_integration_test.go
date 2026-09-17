package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func sreLogsPython(t *testing.T) (string, string) {
	t.Helper()
	root, err := filepath.Abs("../..")
	if err != nil {
		t.Fatal(err)
	}
	python := filepath.Join(root, dataPython)
	if _, err := os.Stat(python); err != nil {
		t.Skip("dedicated Python unavailable; no installation attempted")
	}
	return root, python
}

func TestSRELogsPythonContractEquivalence(t *testing.T) {
	root, python := sreLogsPython(t)
	harness := `
import copy,json,sys
sys.path[:0]=[sys.argv[1],sys.argv[1]+'/tools/local_experiment/bears',sys.argv[1]+'/tests']
import sre_cli as C
from tools.local_experiment.gateway import pod_logs as L
from test_local_sre_log_signals import log_observed,log_unknown
from test_sre_cli_log_signals import maximum_log_projection
rows=[]
def add(name,a,b=None,reobserved=False):
    a=L.validate(a) if a is not None else None
    b=L.validate(b) if b is not None else None
    rows.append(dict(name=name,view=C.log_projection(a,b,C.C._compare_log_signals(a,b,reobserved=reobserved),verified=True)))
a=log_observed()
add('nonempty',a)
add('empty',log_observed(empty=True))
add('same',a,copy.deepcopy(a),True)
add('missing-followup',a,None,True)
add('missing-initial',None,a,True)
for reason in sorted(L.REASONS): add('unknown-'+reason,log_unknown(reason))
b=copy.deepcopy(a);b['identity']['container_id_sha256']='e'*64
add('different-identity',a,b,True)
b=copy.deepcopy(a);b['fragment']['warn_header_lines']=0
add('changed-count-only',a,b,True)
b=copy.deepcopy(a);b.update(request_started_at='2026-09-17T02:05:03Z',observed_at='2026-09-17T02:05:04Z',since_time='2026-09-17T02:00:03Z')
b['fragment'].update(first_prefix_at='2026-09-17T02:05:03Z',last_prefix_at='2026-09-17T02:05:04Z')
add('disjoint',a,b,True)
add('backward-clock',b,a,True)
maximum=maximum_log_projection()['log_signals']
rows.append(dict(name='maximum',view=maximum))
a=log_observed(empty=True)
a['identity']['started_at']='9999-01-01T01:00:00.000000Z'
a.update(request_started_at='9999-01-01T02:00:00.000000001Z',observed_at='9999-01-01T02:00:00.000000002Z',since_time='9999-01-01T01:55:00Z')
add('beyond-nanosecond-epoch',a,copy.deepcopy(a),True)
print(json.dumps(rows,separators=(',',':')))
`
	raw, err := exec.Command(python, "-I", "-B", "-c", harness, root).CombinedOutput()
	if err != nil {
		t.Fatalf("pure Python fixture failed: %v\n%s", err, raw)
	}
	var rows []struct {
		Name string          `json:"name"`
		View json.RawMessage `json:"view"`
	}
	if json.Unmarshal(raw, &rows) != nil || len(rows) < 20 {
		t.Fatal("pure fixture coverage missing")
	}
	for _, row := range rows {
		t.Run(row.Name, func(t *testing.T) {
			decoded, e := decodeSRELogs(row.View, sreEvent{Result: "passed", Verification: "saved-evidence"})
			if e != nil {
				t.Fatal(e)
			}
			reencoded, _ := json.Marshal(decoded)
			var want, got any
			_ = json.Unmarshal(row.View, &want)
			_ = json.Unmarshal(reencoded, &got)
			if !reflect.DeepEqual(got, want) {
				t.Fatal("Python log semantics changed in Go")
			}
		})
	}
}

func TestSRELogsRealPythonSavedStatus(t *testing.T) {
	root, python := sreLogsPython(t)
	// These are disposable, synthetic receipts. Status must only read their
	// existing lock and evidence; any workflow, socket or journal write fails.
	harness := `
import fcntl,hashlib,io,json,pathlib,socket,sys
sys.path[:0]=[sys.argv[1],sys.argv[1]+'/tools/local_experiment/bears',sys.argv[1]+'/tests']
import sre_cli as C
from test_sre_cli import fixture_documents,usage_documents,write_fixture
from test_sre_cli_log_signals import logs_documents
from test_local_sre_log_signals import log_snapshots
state=pathlib.Path(sys.argv[2]).resolve()
C.STATE=state/'intents';C.EVIDENCE=state/'evidence'
mode,token=sys.argv[3:5]
state.mkdir(mode=0o700)
if mode!='missing':
    C.EVIDENCE.mkdir(mode=0o700)
    if mode.startswith('legacy'):
        docs=fixture_documents(legacy=mode=='legacy4',three=mode=='legacy3')
    elif mode=='prelogs': docs=usage_documents()
    elif mode=='nonempty': docs=fixture_documents(initial_snapshots=log_snapshots())
    else:
        docs=logs_documents(state='unknown' if mode=='unknown' else 'observed',followup={'overlap':'same','followup-unknown':'unknown','followup-absent':'absent'}.get(mode,'none'))
    if mode=='failed':
        r=docs['result.json'];r.update(result='failed',error_code='SRE_OBSERVATION_INFERENCE_OR_AUDIT_FAILED');r.pop('checkpoint')
        r['audit_records'][-1]['status']='failed';docs['spans.jsonl'][-1]['status']='ERROR'
    elif mode=='invalid': docs['observations.json'][1]['snapshot']['log_signals']['raw']='PRIVATE_LOG_SQL_TOKEN'
    elif mode=='journal-only': docs.pop('observations.json')
    write_fixture(C.EVIDENCE/token,docs)
lock=None
if mode=='busy':
    C.STATE.mkdir(mode=0o700);directory=C.STATE/token;directory.mkdir(mode=0o700)
    intent=dict(schema_version=1,evidence_token=token,case=C.CASE,invocations=1,state='returned',files=C.snapshot(C.EVIDENCE/token)[1])
    for name,data in [('intent.json',C.canonical(intent).encode()),('run.lock',b'')]:
        p=directory/name;p.write_bytes(data);p.chmod(0o600)
    lock=(directory/'run.lock').open('rb');fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
def fingerprint():
    values={}
    for p in [state,*sorted(state.rglob('*'))]:
        s=p.lstat();values[str(p.relative_to(state))]=[s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_mtime_ns,s.st_ctime_ns,s.st_ino,hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None]
    return values
before=fingerprint()
def never(*args,**kwargs): raise AssertionError('saved status attempted a side effect')
C.runner=C.configured_runner=C.persist=socket.socket=never
def capture(flags):
    output=io.StringIO();code=C.main(['status','--evidence-token',token,*flags],stdout=output)
    return code,output.getvalue()
plain=capture([]);usage=capture(['--usage'])
code,logs=capture(['--logs'])
assert capture([])==plain and capture(['--usage'])==usage,'existing views changed'
assert fingerprint()==before,'saved status changed evidence'
if lock is not None: lock.close()
sys.stdout.write(logs)
raise SystemExit(code)
`
	for _, mode := range []string{"empty", "nonempty", "unknown", "overlap", "followup-unknown", "followup-absent", "legacy3", "legacy4", "prelogs", "failed", "invalid", "journal-only", "missing", "busy"} {
		t.Run(mode, func(t *testing.T) {
			request := dataChildRequest{Root: root, Action: sreLogsAction, Token: testWarehouseSession}
			state := filepath.Join(t.TempDir(), "saved-status")
			runner := execSREChild{command: func(r dataChildRequest) (*exec.Cmd, error) {
				return exec.Command(python, "-I", "-B", "-c", harness, root, state, mode, r.Token), nil
			}}
			var out bytes.Buffer
			err := runSRECommand(context.Background(), runner, request, &out)
			wantExit := 0
			if mode == "missing" || mode == "invalid" {
				wantExit = 1
			}
			if ExitCode(err) != wantExit {
				t.Fatalf("saved logs status exit mismatch: %v", err)
			}
			lines := bytes.Split(bytes.TrimSpace(out.Bytes()), []byte("\n"))
			if len(lines) != 2 || bytes.Contains(out.Bytes(), []byte("PRIVATE")) || bytes.Contains(out.Bytes(), []byte(state)) || bytes.Contains(out.Bytes(), []byte(`"resource_usage"`)) {
				t.Fatal("saved status exposed invalid frames")
			}
			allocated, e := decodeSREEvent(lines[0], request)
			if e != nil || allocated.SchemaVersion != 1 || allocated.Event != "allocated" {
				t.Fatal("allocated protocol changed")
			}
			event, e := decodeSREEvent(lines[1], request)
			if e != nil || event.SchemaVersion != 3 || event.LogSignals == nil {
				t.Fatal("saved logs lost their typed view")
			}
			view := event.LogSignals
			availability := "available"
			if strings.HasPrefix(mode, "legacy") || mode == "prelogs" {
				availability = "legacy-absent"
			} else if dataOneOf(mode, "failed", "invalid", "journal-only", "missing", "busy") {
				availability = "unverified"
			}
			if view.Availability != availability {
				t.Fatal("saved availability mismatch")
			}
			if availability != "available" {
				if view.Initial != nil || view.Followup != nil || view.Comparison != nil {
					t.Fatal("unverified or legacy logs exposed a payload")
				}
				return
			}
			if mode == "unknown" {
				if view.Initial.State != "unknown" || view.Initial.Fragment != nil {
					t.Fatal("unknown logs became zero")
				}
			} else if view.Initial.State != "observed" || view.Initial.Fragment == nil {
				t.Fatal("observed fragment lost")
			}
			if mode == "empty" && view.Initial.Fragment.ReturnedBytes != 0 || mode == "nonempty" && view.Initial.Fragment.ReturnedBytes == 0 {
				t.Fatal("fragment value changed")
			}
			wantAfter := "not-reobserved"
			if mode == "overlap" {
				wantAfter = "observed"
				if view.Comparison.Identity != "same" || view.Comparison.RequestedWindowOverlap == nil || !*view.Comparison.RequestedWindowOverlap {
					t.Fatal("request window comparison differs")
				}
			} else if mode == "followup-unknown" {
				wantAfter = "unknown"
			} else if mode == "followup-absent" {
				wantAfter = "not-observed"
			}
			if view.Comparison.AfterState != wantAfter {
				t.Fatal("reobservation state lost")
			}
		})
	}
}
