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

const queryCountScript = "tools/local_experiment/query_count/run.py"
const queryCountResponseLimit = 256 * 1024

type execQueryCountChild struct {
	command func(dataChildRequest) (*exec.Cmd, error) // offline tests only
	grace   time.Duration
}

func (runner execQueryCountChild) Run(ctx context.Context, request dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
	command := runner.command
	if command == nil {
		command = queryCountCommand
	}
	return (execDataChild{command: command, grace: runner.grace, responseLimit: queryCountResponseLimit}).Run(ctx, request, emit)
}

func resolveQueryCountRoot(explicit string) (string, error) {
	root, err := resolveRepositoryRoot(explicit)
	if err != nil {
		return "", errors.New("query count checkout was not found; specify --repo-root")
	}
	root, err = filepath.EvalSymlinks(root)
	if err != nil {
		return "", errors.New("query count checkout could not be resolved")
	}
	for _, relative := range []string{queryCountScript, "tools/local_experiment/llm/.venv/pyvenv.cfg"} {
		path := filepath.Join(root, relative)
		resolved, e := filepath.EvalSymlinks(path)
		if e != nil || resolved != path || !regularFile(path) {
			return "", errors.New("query count checkout or dedicated environment is not ready; no installation was attempted")
		}
	}
	info, err := os.Stat(filepath.Join(root, dataPython))
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0111 == 0 {
		return "", errors.New("dedicated query count Python is unavailable; no installation was attempted")
	}
	return root, nil
}

func queryCountCommand(request dataChildRequest) (*exec.Cmd, error) {
	if !warehouseSessionPattern.MatchString(request.Token) || request.Case != queryCountCase {
		return nil, errors.New("invalid fixed query count invocation")
	}
	if _, err := decodeQueryCountRequest(request.Request, request.Action); err != nil {
		return nil, err
	}
	root, err := resolveQueryCountRoot(request.Root)
	if err != nil || root != request.Root {
		return nil, errors.New("invalid query count checkout")
	}
	args := []string{"-I", "-B", filepath.Join(root, queryCountScript), request.Action, "--evidence-token", request.Token, "--request-stdin"}
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
