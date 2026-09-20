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

func TestQueryReadsActualPythonMain(t *testing.T) {
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
from tools.local_experiment.query_reads import run as R
from test_query_reads import summary,put
base=pathlib.Path(sys.argv[2]).resolve();mode=sys.argv[3];token=sys.argv[4]
R.STATE=base/'records';R.STATE.mkdir(mode=0o700)
def forbidden(*a,**k):raise AssertionError('external dependency invoked')
socket.create_connection=forbidden;socket.socket.connect=forbidden
lock=None
if mode=='versions':
    for version in range(1,5):put(R.STATE,version,token,summary(version,token))
elif mode not in ('missing','absent'):
    outcome=mode if mode in ('failed','interrupted') else 'passed'
    value=summary(1,token,result=outcome)
    if mode=='unfinished':value['finished_at']=None
    put(R.STATE,1,token,value)
    folder=R.STATE/'query-read'/token
    if mode in ('invalid','active'):(folder/'summary.json').write_text('PRIVATE_UNTRUSTED_BODY')
    if mode=='missing-receipt':(folder/'summary.json').unlink()
    if mode=='active':
        lock=os.open(folder/'run.lock',os.O_RDONLY);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
def marks():
    return {str(p.relative_to(R.STATE)):(hashlib.sha256(p.read_bytes()).hexdigest(),R.FS.fingerprint(p.stat()))
            for p in R.STATE.rglob('*') if p.is_file()}
before=marks();code=R.main(['reads','--request-stdin']);assert marks()==before
if lock is not None:os.close(lock)
for folder in R.STATE.glob('*/*'):
    fd=os.open(folder/'run.lock',os.O_RDONLY);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);os.close(fd)
p=base/'harness-check.json';p.write_text(json.dumps({'inputs_unchanged':True,'external_calls':0,'locks_released':True,'python_exit':code}));p.chmod(0o600)
raise SystemExit(code)
`
	for _, tc := range []struct {
		mode, state   string
		exit, entries int
	}{
		{"versions", "saved", 0, 4}, {"failed", "saved", 0, 1}, {"interrupted", "saved", 0, 1},
		{"active", "active", 0, 1}, {"invalid", "invalid", 1, 1}, {"unfinished", "incomplete", 1, 1},
		{"missing-receipt", "incomplete", 1, 1}, {"missing", "", 1, 0}, {"absent", "", 0, 0},
	} {
		t.Run(tc.mode, func(t *testing.T) {
			output := t.TempDir()
			if err := os.Chmod(output, 0700); err != nil {
				t.Fatal(err)
			}
			request := readsRequest(nil)
			if tc.mode == "missing" {
				request = readsRequest(readsString(testWarehouseSession))
			}
			child := execQueryReadsChild{command: func(r dataChildRequest) (*exec.Cmd, error) {
				cmd := exec.Command(python, "-I", "-B", "-c", harness, root, output, tc.mode, testWarehouseSession)
				cmd.Dir = root
				cmd.Env = []string{"PATH=/usr/bin:/bin"}
				cmd.Stdin = bytes.NewReader(r.Request)
				return cmd, nil
			}}
			var out bytes.Buffer
			err := runQueryReadsCLI(context.Background(), child, request, &out)
			if ExitCode(err) != tc.exit || out.Len() == 0 || strings.Contains(out.String(), "PRIVATE_") {
				t.Fatalf("%s: %v %s", tc.mode, err, &out)
			}
			var report queryReadsReport
			if json.Unmarshal(out.Bytes(), &report) != nil || len(report.Entries) != tc.entries {
				t.Fatal("entry coverage differs")
			}
			for _, entry := range report.Entries {
				if entry.InspectionState != tc.state {
					t.Fatal("saved state differs")
				}
			}
			raw, e := os.ReadFile(filepath.Join(output, "harness-check.json"))
			if e != nil {
				t.Fatal(e)
			}
			var guard struct {
				InputsUnchanged bool `json:"inputs_unchanged"`
				ExternalCalls   int  `json:"external_calls"`
				LocksReleased   bool `json:"locks_released"`
				PythonExit      int  `json:"python_exit"`
			}
			if json.Unmarshal(raw, &guard) != nil || !guard.InputsUnchanged || guard.ExternalCalls != 0 || !guard.LocksReleased || guard.PythonExit != tc.exit {
				t.Fatal("Python saved-only guard failed")
			}
		})
	}
}
