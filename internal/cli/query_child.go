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

// Reuse the owner-only SIGINT and bounded cleanup lifecycle without changing
// Data's fixed command or interpreting any of its workflow protocol.
type execQueryChild struct {
	command func(dataChildRequest) (*exec.Cmd, error) // offline subprocess tests only
	grace   time.Duration
}

func (runner execQueryChild) Run(ctx context.Context, request dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
	command := runner.command
	if command == nil {
		command = queryCommand
	}
	return (execDataChild{command: command, grace: runner.grace}).Run(ctx, request, emit)
}

func queryCommand(request dataChildRequest) (*exec.Cmd, error) {
	if !warehouseSessionPattern.MatchString(request.Token) || !dataOneOf(request.Action, "run", "status") ||
		request.Request != nil || (request.Action == "run" && request.Case != "retained-fixture") ||
		(request.Action == "status" && request.Case != "") {
		return nil, errors.New("invalid fixed query invocation")
	}
	args := []string{"-I", "-B", filepath.Join(request.Root, queryScript), request.Action, "--evidence-token", request.Token}
	if request.Action == "run" {
		args = append(args, "--case", "retained-fixture")
	}
	cmd := exec.Command(filepath.Join(request.Root, dataPython), args...)
	cmd.Dir = request.Root
	cmd.Env = []string{}
	for _, key := range []string{"PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL"} {
		if value, ok := os.LookupEnv(key); ok {
			cmd.Env = append(cmd.Env, key+"="+value)
		}
	}
	cmd.Stdin = bytes.NewReader(nil)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	return cmd, nil
}
