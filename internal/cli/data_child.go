package cli

import (
	"bufio"
	"bytes"
	"context"
	"errors"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"time"
)

// The child owns Kubernetes/query cleanup. Never use execRunner's immediate
// process-group SIGKILL, CommandContext's default Kill, or WaitDelay's Kill here.
const dataCleanupGrace = 240 * time.Second

var errDataInterrupted = errors.New("data invocation interrupted; completion requires saved evidence")

type execDataChild struct {
	command       func(dataChildRequest) (*exec.Cmd, error) // offline subprocess tests only
	grace         time.Duration
	responseLimit int // fixed by the owning command; zero retains Data's bound
}

func dataCommand(request dataChildRequest) (*exec.Cmd, error) {
	if !warehouseSessionPattern.MatchString(request.Token) || !dataOneOf(request.Action, "run", "resume", "status") {
		return nil, errors.New("invalid fixed data invocation")
	}
	args := []string{"-I", "-B", filepath.Join(request.Root, dataScript), request.Action, "--handoff-token", request.Token}
	if request.Action == "run" {
		if request.Request != nil {
			args = append(args, "--request-stdin")
		} else {
			args = append(args, "--case", request.Case)
		}
	}
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

type dataChildMessage struct {
	line []byte
	err  error
	done bool
	exit int
}

func (runner execDataChild) Run(ctx context.Context, request dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
	result := dataChildResult{ExitCode: -1}
	if ctx.Err() != nil {
		return result, errors.New("data child was not started")
	}
	build := runner.command
	if build == nil {
		build = dataCommand
	}
	cmd, err := build(request)
	if err != nil {
		return result, errors.New("data child setup failed")
	}
	cmd.Stderr = io.Discard
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return result, errors.New("data child output unavailable")
	}
	if err = cmd.Start(); err != nil {
		stdout.Close()
		return result, errors.New("data child start failed")
	}
	result.Started = true
	result.PID = cmd.Process.Pid
	messages := make(chan dataChildMessage, 2)
	closed := make(chan struct{})
	defer close(closed)
	defer stdout.Close()
	send := func(message dataChildMessage) bool {
		select {
		case messages <- message:
			return true
		case <-closed:
			return false
		}
	}
	go func() {
		limit := runner.responseLimit
		if limit <= 0 {
			limit = dataResponseLimit
		}
		scanner := bufio.NewScanner(stdout)
		scanner.Buffer(make([]byte, 1024), limit+1)
		for scanner.Scan() {
			if !send(dataChildMessage{line: append([]byte(nil), scanner.Bytes()...)}) {
				break
			}
		}
		if scanner.Err() != nil {
			send(dataChildMessage{err: errors.New("data child output exceeded its protocol bound")})
			// Continue draining without retaining bytes while SIGINT cleanup runs.
			_, _ = io.Copy(io.Discard, stdout)
		}
		waitErr := cmd.Wait()
		exit := 0
		if waitErr != nil {
			exit = -1
			var exited *exec.ExitError
			if errors.As(waitErr, &exited) {
				exit = exited.ExitCode()
			}
		}
		send(dataChildMessage{done: true, exit: exit})
	}()
	grace := runner.grace
	if grace <= 0 {
		grace = dataCleanupGrace
	}
	var timer *time.Timer
	var expiry <-chan time.Time
	var outcome error
	done := ctx.Done()
	interrupt := func(reason error) {
		if timer != nil {
			return
		}
		outcome = reason
		result.Unconfirmed = true
		// Signal only the owner; its children remain available for cleanup.
		_ = cmd.Process.Signal(os.Interrupt)
		timer = time.NewTimer(grace)
		expiry = timer.C
		done = nil
	}
	defer func() {
		if timer != nil {
			timer.Stop()
		}
	}()
	for {
		select {
		case <-done:
			if errors.Is(ctx.Err(), context.Canceled) {
				result.Interrupted = true
				interrupt(errDataInterrupted)
			} else {
				interrupt(errors.New("data execution deadline exceeded; outcome unknown"))
			}
		case <-expiry:
			// The known PID and handoff token are returned for status/recovery.
			// No claim of cleanup, process exit, or success is made after this point.
			return result, errors.New("data cleanup grace expired; child outcome unknown")
		case message := <-messages:
			if message.done {
				result.ExitCode = message.exit
				return result, outcome
			}
			if message.err != nil {
				interrupt(message.err)
				continue
			}
			if outcome == nil {
				if err := emit(message.line); err != nil {
					interrupt(errors.New("data child protocol or output delivery failed"))
				}
			}
		}
	}
}
