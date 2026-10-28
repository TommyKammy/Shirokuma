package cli

import (
	"context"
	"fmt"
	"net"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/spf13/cobra"
)

func newWorkbenchCommand(runner commandRunner) *cobra.Command {
	return workbenchCommand(runner, waitForWorkbench)
}

func workbenchPython(root string) (string, error) {
	configured := strings.TrimSpace(os.Getenv("SHIROKUMA_RUNTIME_PROFILE")) != ""
	if !configured {
		_, err := os.Stat(filepath.Join(root, "var", "shirokuma", "runtime-profile.json"))
		configured = err == nil
		if err != nil && !os.IsNotExist(err) {
			return "", err
		}
		if !configured {
			_, err = os.Stat(filepath.Join(root, "var", "shirokuma", "runtime-profile.active"))
			configured = err == nil
			if err != nil && !os.IsNotExist(err) {
				return "", err
			}
		}
	}
	if !configured {
		return "python3", nil
	}
	python := filepath.Join(root, "tools", "local_experiment", "dataops", ".venv", "bin", "python3")
	info, err := os.Stat(python)
	if err != nil || info.IsDir() || info.Mode()&0111 == 0 {
		return "", fmt.Errorf("configured database Workbench requires the repository DataOps Python environment: %s", python)
	}
	return python, nil
}

func waitForWorkbench(ctx context.Context, address string) error {
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	ticker := time.NewTicker(100 * time.Millisecond)
	defer ticker.Stop()
	for {
		connection, err := (&net.Dialer{Timeout: 100 * time.Millisecond}).DialContext(ctx, "tcp", address)
		if err == nil {
			connection.Close()
			return nil
		}
		select {
		case <-ctx.Done():
			return fmt.Errorf("waiting for Workbench: %w", ctx.Err())
		case <-ticker.C:
		}
	}
}

func workbenchCommand(runner commandRunner, ready func(context.Context, string) error) *cobra.Command {
	var port int
	var open bool
	var enrollment string
	command := &cobra.Command{
		Use: "workbench", Short: "Launch the local Workbench web interface", Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			if port < 1 || port > 65535 {
				return fmt.Errorf("port must be between 1 and 65535")
			}
			root, err := resolveRepositoryRoot("")
			if err != nil {
				return err
			}
			python, err := workbenchPython(root)
			if err != nil {
				return err
			}
			ctx, stop := signal.NotifyContext(cmd.Context(), os.Interrupt, syscall.SIGTERM)
			defer stop()
			url := "http://127.0.0.1:" + strconv.Itoa(port)
			fmt.Fprintln(cmd.OutOrStdout(), "Shirokuma Workbench:", url)
			serverArgs := []string{filepath.Join(root, "tools", "local_experiment", "workbench_server.py"), "--port", strconv.Itoa(port)}
			if enrollment != "" {
				var started func(context.Context) error
				if open {
					started = func(ownerCtx context.Context) error {
						if err := ready(ownerCtx, "127.0.0.1:"+strconv.Itoa(port)); err != nil {
							return err
						}
						opener := "xdg-open"
						if runtime.GOOS == "darwin" {
							opener = "open"
						}
						_, err := runner.Run(ownerCtx, opener, url)
						return err
					}
				}
				return runManagedOwner(ctx, root, python, serverArgs, enrollment, "workbench", started)
			}
			if !open {
				_, err := runner.Run(ctx, python, serverArgs...)
				return err
			}
			ctx, cancel := context.WithCancel(ctx)
			defer cancel()
			done := make(chan error, 1)
			go func() {
				_, err := runner.Run(ctx, python, serverArgs...)
				done <- err
				cancel()
			}()
			if err := ready(ctx, "127.0.0.1:"+strconv.Itoa(port)); err != nil {
				cancel()
				serverErr := <-done
				if serverErr != nil {
					return serverErr
				}
				return err
			}
			{
				opener := "xdg-open"
				if runtime.GOOS == "darwin" {
					opener = "open"
				}
				if _, err := runner.Run(ctx, opener, url); err != nil {
					cancel()
					<-done
					return fmt.Errorf("open Workbench: %w", err)
				}
			}
			return <-done
		},
	}
	command.Flags().IntVar(&port, "port", 8888, "local HTTP port")
	command.Flags().BoolVar(&open, "open", false, "open the Workbench in the default browser")
	command.Flags().StringVar(&enrollment, "database-creation-enrollment", "", "private database creation enrollment")
	return command
}
