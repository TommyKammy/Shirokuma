package cli

import (
	"bytes"
	"context"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"time"
)

const queryDiagnoseReportScript = "tools/local_experiment/query_diagnose_report/run.py"
const queryDiagnoseReportLimit = 256 * 1024

type execQueryDiagnoseReportChild struct {
	command func(dataChildRequest) (*exec.Cmd, error)
	grace   time.Duration
}

func (runner execQueryDiagnoseReportChild) Run(ctx context.Context, request dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
	command := runner.command
	if command == nil {
		command = queryDiagnoseReportCommand
	}
	return (execDataChild{command: command, grace: runner.grace, responseLimit: queryDiagnoseReportLimit}).Run(ctx, request, emit)
}

func resolveQueryDiagnoseReportRoot(explicit string) (string, error) {
	root, err := resolveQueryDiagnoseRoot(explicit)
	if err != nil {
		return "", errors.New("query diagnosis report checkout or dedicated Python is unavailable; no installation was attempted")
	}
	path := filepath.Join(root, queryDiagnoseReportScript)
	resolved, err := filepath.EvalSymlinks(path)
	if err != nil || resolved != path || !regularFile(path) {
		return "", errors.New("query diagnosis report checkout is not ready")
	}
	return root, nil
}

func queryDiagnoseReportCommand(request dataChildRequest) (*exec.Cmd, error) {
	if _, err := decodeQueryDiagnoseReportRequest(request); err != nil {
		return nil, err
	}
	root, err := resolveQueryDiagnoseReportRoot(request.Root)
	if err != nil || root != request.Root {
		return nil, errors.New("invalid query diagnosis report checkout")
	}
	cmd := exec.Command(filepath.Join(root, dataPython), "-I", "-B", filepath.Join(root, queryDiagnoseReportScript), "report", "--request-stdin")
	cmd.Dir, cmd.Env = root, []string{}
	for _, key := range []string{"PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL"} {
		if value, ok := os.LookupEnv(key); ok {
			cmd.Env = append(cmd.Env, key+"="+value)
		}
	}
	cmd.Stdin = bytes.NewReader(request.Request)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	return cmd, nil
}
