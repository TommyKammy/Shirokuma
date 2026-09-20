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

const querySnapshotsScript = "tools/local_experiment/query_snapshots/run.py"
const querySnapshotsResponseLimit = 256 * 1024

type execQuerySnapshotsChild struct {
	command func(dataChildRequest) (*exec.Cmd, error) // offline tests only
	grace   time.Duration
}

func (runner execQuerySnapshotsChild) Run(ctx context.Context, request dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
	command := runner.command
	if command == nil {
		command = querySnapshotsCommand
	}
	return (execDataChild{command: command, grace: runner.grace, responseLimit: querySnapshotsResponseLimit}).Run(ctx, request, emit)
}

func resolveQuerySnapshotsRoot(explicit string) (string, error) {
	root, err := resolveRepositoryRoot(explicit)
	if err != nil {
		return "", errors.New("query snapshots checkout was not found; specify --repo-root")
	}
	root, err = filepath.EvalSymlinks(root)
	if err != nil {
		return "", errors.New("query snapshots checkout could not be resolved")
	}
	for _, relative := range []string{querySnapshotsScript, "tools/local_experiment/llm/.venv/pyvenv.cfg"} {
		path := filepath.Join(root, relative)
		resolved, e := filepath.EvalSymlinks(path)
		if e != nil || resolved != path || !regularFile(path) {
			return "", errors.New("query snapshots checkout or dedicated environment is not ready; no installation was attempted")
		}
	}
	info, err := os.Stat(filepath.Join(root, dataPython))
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0111 == 0 {
		return "", errors.New("dedicated query snapshots Python is unavailable; no installation was attempted")
	}
	return root, nil
}

func querySnapshotsCommand(request dataChildRequest) (*exec.Cmd, error) {
	if !warehouseSessionPattern.MatchString(request.Token) || request.Case != querySnapshotsCase {
		return nil, errors.New("invalid fixed query snapshots invocation")
	}
	if _, err := decodeQuerySnapshotsRequest(request.Request, request.Action); err != nil {
		return nil, err
	}
	root, err := resolveQuerySnapshotsRoot(request.Root)
	if err != nil || root != request.Root {
		return nil, errors.New("invalid query snapshots checkout")
	}
	args := []string{"-I", "-B", filepath.Join(root, querySnapshotsScript), request.Action, "--evidence-token", request.Token, "--request-stdin"}
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
