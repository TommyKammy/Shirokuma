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

const queryDiagnoseScript = "tools/local_experiment/query_diagnose/run.py"

type execQueryDiagnoseChild struct {
	command func(dataChildRequest) (*exec.Cmd, error) // offline tests only
	grace   time.Duration
}

func (runner execQueryDiagnoseChild) Run(ctx context.Context, request dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
	command := runner.command
	if command == nil {
		command = queryDiagnoseCommand
	}
	return (execDataChild{command: command, grace: runner.grace}).Run(ctx, request, emit)
}

func resolveQueryDiagnoseRoot(explicit string) (string, error) {
	root, err := resolveRepositoryRoot(explicit)
	if err != nil {
		return "", errors.New("query diagnosis checkout was not found; specify --repo-root")
	}
	root, err = filepath.EvalSymlinks(root)
	if err != nil {
		return "", errors.New("query diagnosis checkout could not be resolved")
	}
	for _, relative := range []string{queryDiagnoseScript, "tools/local_experiment/llm/.venv/pyvenv.cfg"} {
		path := filepath.Join(root, relative)
		resolved, e := filepath.EvalSymlinks(path)
		if e != nil || resolved != path || !regularFile(path) {
			return "", errors.New("query diagnosis checkout or dedicated environment is not ready; no installation was attempted")
		}
	}
	info, err := os.Stat(filepath.Join(root, dataPython))
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0111 == 0 {
		return "", errors.New("dedicated query diagnosis Python is unavailable; no installation was attempted")
	}
	return root, nil
}

func queryDiagnoseCommand(request dataChildRequest) (*exec.Cmd, error) {
	if !warehouseSessionPattern.MatchString(request.Token) || request.Case != "" {
		return nil, errors.New("invalid fixed query diagnosis invocation")
	}
	if _, err := decodeQueryDiagnoseRequest(request); err != nil {
		return nil, err
	}
	root, err := resolveQueryDiagnoseRoot(request.Root)
	if err != nil || root != request.Root {
		return nil, errors.New("invalid query diagnosis checkout")
	}
	args := []string{"-I", "-B", filepath.Join(root, queryDiagnoseScript), request.Action, "--evidence-token", request.Token, "--request-stdin"}
	cmd := exec.Command(filepath.Join(root, dataPython), args...)
	cmd.Dir, cmd.Env = root, []string{}
	for _, key := range []string{"PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL", "KUBECONFIG"} {
		if value, ok := os.LookupEnv(key); ok {
			cmd.Env = append(cmd.Env, key+"="+value)
		}
	}
	cmd.Stdin = bytes.NewReader(request.Request)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	return cmd, nil
}
