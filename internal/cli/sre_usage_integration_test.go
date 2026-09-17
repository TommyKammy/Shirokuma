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

func TestSREUsageRealPythonSavedStatus(t *testing.T) {
	root, err := filepath.Abs("../..")
	if err != nil {
		t.Fatal(err)
	}
	python := filepath.Join(root, dataPython)
	if _, err := os.Stat(python); err != nil {
		t.Skip("dedicated Python unavailable; no installation attempted")
	}
	// Create only disposable saved receipts, then forbid workflows, networking
	// and journal writes while the real Python main and Go transport inspect them.
	harness := `
import hashlib, os, pathlib, socket, sys
sys.path[:0]=[sys.argv[1]+'/tools/local_experiment/bears',sys.argv[1]+'/tests']
import sre_cli as C
from test_sre_cli import fixture_documents, usage_documents, write_fixture
state=pathlib.Path(sys.argv[2]).resolve()
C.STATE=state/'intents'
C.EVIDENCE=state/'evidence'
mode, token=sys.argv[3:5]
state.mkdir(mode=0o700)
if mode!='missing':
    C.EVIDENCE.mkdir(mode=0o700)
    if mode.startswith('legacy'):
        docs=fixture_documents(legacy=mode=='legacy4',three=mode=='legacy3')
    else:
        docs=usage_documents(state='unknown' if mode=='unknown' else 'observed',
            followup='same' if mode=='cached' else 'different' if mode=='different' else 'none',zero=mode=='zero')
    if mode=='failed':
        r=docs['result.json']
        r.update(result='failed',error_code='SRE_OBSERVATION_INFERENCE_OR_AUDIT_FAILED')
        r.pop('checkpoint')
        r['audit_records'][-1]['status']='failed'
        docs['spans.jsonl'][-1]['status']='ERROR'
    elif mode=='invalid':
        docs['observations.json'][1]['snapshot']['resource_usage']['sample']['cpu_nanocores']='01'
    elif mode=='journal-only':
        docs.pop('observations.json')
    write_fixture(C.EVIDENCE/token,docs)
def fingerprint():
    rows={}
    for p in [state,*sorted(state.rglob('*'))]:
        s=p.lstat()
        rows[str(p.relative_to(state))]=[s.st_mode,s.st_mtime_ns,s.st_ctime_ns,s.st_ino,
            hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None]
    return rows
before=fingerprint()
def never(*args,**kwargs):
    raise AssertionError('saved status attempted a side effect')
C.runner=C.configured_runner=C.persist=socket.socket=never
code=C.main(['status','--evidence-token',token,'--usage'])
assert fingerprint()==before, 'saved status changed evidence'
raise SystemExit(code)
`
	for _, mode := range []string{"observed", "zero", "unknown", "cached", "different", "legacy3", "legacy4", "failed", "invalid", "journal-only", "missing"} {
		t.Run(mode, func(t *testing.T) {
			request := dataChildRequest{Root: root, Action: sreUsageAction, Token: testWarehouseSession}
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
				t.Fatalf("saved status exit mismatch: %v", err)
			}
			lines := bytes.Split(bytes.TrimSpace(out.Bytes()), []byte("\n"))
			if len(lines) != 2 || bytes.Contains(out.Bytes(), []byte("PRIVATE")) {
				t.Fatal("saved status emitted invalid frames")
			}
			var event sreEvent
			if json.Unmarshal(lines[1], &event) != nil || event.SchemaVersion != 2 || event.ResourceUsage == nil {
				t.Fatal("saved status lost its typed usage")
			}
			var usage map[string]any
			raw, _ := json.Marshal(event.ResourceUsage)
			_ = json.Unmarshal(raw, &usage)
			availability := "available"
			if strings.HasPrefix(mode, "legacy") {
				availability = "legacy-absent"
			} else if mode == "failed" || mode == "invalid" || mode == "journal-only" || mode == "missing" {
				availability = "unverified"
			}
			if usage["availability"] != availability {
				t.Fatal("saved availability mismatch")
			}
			if availability != "available" {
				if usage["initial"] != nil || usage["followup"] != nil || usage["comparison"] != nil {
					t.Fatal("unavailable usage exposed a payload")
				}
				return
			}
			initial := usage["initial"].(map[string]any)
			if mode == "unknown" {
				if initial["state"] != "unknown" || initial["sample"] != nil {
					t.Fatal("unknown measurement was converted to a value")
				}
			} else if initial["state"] != "observed" || initial["sample"] == nil {
				t.Fatal("observed sample was lost")
			}
			if mode == "zero" {
				sample := initial["sample"].(map[string]any)
				if sample["cpu_nanocores"] != "0" || sample["memory_working_set_bytes"] != "0" {
					t.Fatal("zero measurement was converted to unknown")
				}
			}
			wantComparison := "not-reobserved"
			if mode == "cached" {
				wantComparison = "same-sample-window"
			} else if mode == "different" {
				wantComparison = "different-sample-window"
			}
			if usage["comparison"].(map[string]any)["comparison"] != wantComparison {
				t.Fatal("saved window comparison mismatch")
			}
		})
	}
}
