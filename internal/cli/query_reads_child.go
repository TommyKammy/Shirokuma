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

const queryReadsScript = "tools/local_experiment/query_reads/run.py"
const queryReadsResponseLimit = 256 * 1024

type execQueryReadsChild struct {
	command func(dataChildRequest) (*exec.Cmd, error) // offline tests only
	grace   time.Duration
}

func (runner execQueryReadsChild) Run(ctx context.Context, request dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
	command := runner.command
	if command == nil {
		command = queryReadsCommand
	}
	return (execDataChild{command: command, grace: runner.grace, responseLimit: queryReadsResponseLimit}).Run(ctx, request, emit)
}

func resolveQueryReadsRoot(explicit string) (string, error) {
	root, err := resolveRepositoryRoot(explicit)
	if err != nil {
		return "", errors.New("query reads checkout was not found; specify --repo-root")
	}
	root, err = filepath.EvalSymlinks(root)
	if err != nil {
		return "", errors.New("query reads checkout could not be resolved")
	}
	for _, relative := range []string{queryReadsScript, "tools/local_experiment/llm/.venv/pyvenv.cfg"} {
		path := filepath.Join(root, relative)
		resolved, e := filepath.EvalSymlinks(path)
		if e != nil || resolved != path || !regularFile(path) {
			return "", errors.New("query reads checkout or dedicated environment is not ready; no installation was attempted")
		}
	}
	info, err := os.Stat(filepath.Join(root, dataPython))
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0111 == 0 {
		return "", errors.New("dedicated query reads Python is unavailable; no installation was attempted")
	}
	return root, nil
}

func queryReadsCommand(request dataChildRequest) (*exec.Cmd, error) {
	if _, err := decodeQueryReadsRequest(request); err != nil {
		return nil, err
	}
	root, err := resolveQueryReadsRoot(request.Root)
	if err != nil || root != request.Root {
		return nil, errors.New("invalid query reads checkout")
	}
	cmd := exec.Command(filepath.Join(root, dataPython), "-I", "-B", filepath.Join(root, queryReadsScript), "reads", "--request-stdin")
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
