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
	"unicode/utf8"
)

type execPolicyChild struct {
	command func(dataChildRequest) (*exec.Cmd, error) // offline subprocess tests only
	grace   time.Duration
}

func (runner execPolicyChild) Run(ctx context.Context, request dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
	command := runner.command
	if command == nil {
		command = policyCommand
	}
	return (execDataChild{command: command, grace: runner.grace}).Run(ctx, request, emit)
}

func policyCommand(request dataChildRequest) (*exec.Cmd, error) {
	if !warehouseSessionPattern.MatchString(request.Token) || !dataOneOf(request.Action, "check", "status") || request.Case != "" {
		return nil, errors.New("invalid fixed policy invocation")
	}
	if request.Action == "check" {
		if len(request.Request) > policyInputLimit || !utf8.Valid(request.Request) || len(bytes.TrimSpace(request.Request)) == 0 {
			return nil, errors.New("invalid bounded policy input")
		}
	} else if request.Request != nil {
		return nil, errors.New("policy status does not accept input")
	}
	args := []string{"-I", "-B", filepath.Join(request.Root, policyScript), request.Action, "--evidence-token", request.Token}
	cmd := exec.Command(filepath.Join(request.Root, dataPython), args...)
	cmd.Dir = request.Root
	cmd.Env = []string{}
	for _, key := range []string{"PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL"} {
		if value, ok := os.LookupEnv(key); ok {
			cmd.Env = append(cmd.Env, key+"="+value)
		}
	}
	cmd.Stdin = bytes.NewReader(request.Request)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	return cmd, nil
}
