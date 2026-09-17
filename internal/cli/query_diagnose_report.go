package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"os/signal"
	"syscall"
	"time"
	"unicode/utf8"

	"github.com/spf13/cobra"
)

type queryDiagnoseReportRequest struct {
	Token *string `json:"token"`
}

func newQueryDiagnoseReportCommand(child dataChildRunner, root, profile *string) *cobra.Command {
	var token string
	command := &cobra.Command{Use: "report", Short: "Inspect saved diagnosis outcomes without running another diagnosis", Args: func(_ *cobra.Command, args []string) error {
		if len(args) != 0 {
			return errors.New("query diagnose report does not accept positional arguments")
		}
		return nil
	}, RunE: func(cmd *cobra.Command, _ []string) error {
		if cmd.Parent() != nil && cmd.Parent().Flags().Changed("read-token") {
			return errors.New("query diagnose report accepts --token instead of --read-token")
		}
		if *profile != localExperimentProfile {
			return errors.New("query diagnose report requires --profile local-experiment")
		}
		input := queryDiagnoseReportRequest{}
		if cmd.Flags().Changed("token") {
			if !warehouseSessionPattern.MatchString(token) {
				return errors.New("query diagnose report token must be 32 lowercase hexadecimal characters")
			}
			input.Token = &token
		}
		checkout, err := resolveQueryDiagnoseReportRoot(*root)
		if err != nil {
			return err
		}
		raw, err := json.Marshal(input)
		if err != nil {
			return errors.New("query diagnosis report input is invalid")
		}
		request := dataChildRequest{Root: checkout, Action: "diagnose-report", Request: raw}
		ctx, stop := signal.NotifyContext(cmd.Context(), os.Interrupt, syscall.SIGTERM)
		defer stop()
		ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
		defer cancel()
		return runQueryDiagnoseReportCommand(ctx, child, request, cmd.OutOrStdout())
	}}
	command.Flags().StringVar(&token, "token", "", "inspect one diagnosis evidence token; omit to inspect all saved diagnoses")
	command.SetFlagErrorFunc(func(_ *cobra.Command, _ error) error { return errors.New("query diagnose report flags are invalid") })
	return command
}

func decodeQueryDiagnoseReportRequest(request dataChildRequest) (queryDiagnoseReportRequest, error) {
	var input queryDiagnoseReportRequest
	invalid := errors.New("invalid fixed query diagnosis report invocation")
	if request.Action != "diagnose-report" || request.Token != "" || request.Case != "" || len(request.Request) == 0 || len(request.Request) > 1024 || !utf8.Valid(request.Request) || uniqueWarehouseJSON(request.Request) != nil {
		return input, invalid
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(request.Request, &fields) != nil || !warehouseKeys(fields, "token") || json.Unmarshal(request.Request, &input) != nil || input.Token != nil && !warehouseSessionPattern.MatchString(*input.Token) {
		return input, invalid
	}
	return input, nil
}

func runQueryDiagnoseReportCommand(ctx context.Context, child dataChildRunner, request dataChildRequest, output io.Writer) error {
	if _, err := decodeQueryDiagnoseReportRequest(request); err != nil {
		return err
	}
	var final []byte
	want := -1
	result, err := child.Run(ctx, request, func(line []byte) error {
		if final != nil {
			return errors.New("query diagnosis report returned multiple results")
		}
		code, e := decodeQueryDiagnoseReport(line, request)
		if e != nil {
			return e
		}
		final, want = bytes.Clone(line), code
		return nil
	})
	if result.Interrupted && errors.Is(err, errDataInterrupted) && (result.ExitCode == 130 || result.ExitCode == 143) {
		return &dataExitError{code: result.ExitCode, message: "query diagnosis report interrupted; inspection was not confirmed"}
	}
	if err != nil || !result.Started || result.Unconfirmed || final == nil || result.ExitCode != want {
		return errors.New("query diagnosis report inspection was not confirmed; saved diagnoses were not rerun")
	}
	if writeQueryReadLine(output, final) != nil {
		return errors.New("query diagnosis report output could not be delivered")
	}
	if want == 1 {
		return &dataExitError{code: 1, message: "query diagnosis report is incomplete; inspect the reported evidence states"}
	}
	return nil
}
