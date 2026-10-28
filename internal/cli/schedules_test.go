package cli

import (
	"bytes"
	"path/filepath"
	"reflect"
	"testing"
)

func TestScheduleAlertForwarding(t *testing.T) {
	for _, tc := range []struct {
		group, action string
		extra         []string
	}{
		{"schedule", "list", []string{"--target-kind=stage", "--target-id=id"}},
		{"schedule", "show", []string{"--schedule-id=id"}},
		{"schedule", "ticks", []string{"--schedule-id=id"}},
		{"schedule", "save", []string{"--definition=-file.json", "--idempotency-key=-key"}},
		{"alert", "list", nil},
		{"alert", "ack", []string{"--alert-id=id", "--idempotency-key=-ack"}},
	} {
		t.Run(tc.group+"/"+tc.action, func(t *testing.T) {
			runner := &fakeRunner{responses: []fakeResponse{{}}}
			cmd := newScheduleCommand(runner)
			if tc.group == "alert" {
				cmd = newAlertCommand(runner)
			}
			cmd.SetArgs(append([]string{tc.action, "--context-file=-context.json"}, tc.extra...))
			if err := cmd.Execute(); err != nil {
				t.Fatal(err)
			}
			root, err := resolveRepositoryRoot("")
			if err != nil {
				t.Fatal(err)
			}
			want := []string{databaseTestPython(t, root), filepath.Join(root, "tools", "local_experiment", "schedules_client.py"), tc.group, tc.action, "--api-url=http://127.0.0.1:8080", "--context-file=-context.json"}
			want = append(want, tc.extra...)
			if len(runner.calls) != 1 || !reflect.DeepEqual(runner.calls[0], want) {
				t.Fatalf("wrong invocation: %#v", runner.calls)
			}
		})
	}
}

func TestScheduleAlertRequiredFlags(t *testing.T) {
	for _, args := range [][]string{{"list"}, {"show", "--database=d", "--schema=s"}, {"save", "--context-file=c", "--definition=d"}, {"ticks", "--context-file=c"}, {"list", "--context-file=c", "--target-kind=bad"}, {"list", "--context-file= "}} {
		runner := &fakeRunner{}
		cmd := newScheduleCommand(runner)
		cmd.SetOut(&bytes.Buffer{})
		cmd.SetErr(&bytes.Buffer{})
		cmd.SetArgs(args)
		if err := cmd.Execute(); err == nil || len(runner.calls) != 0 {
			t.Fatalf("accepted invalid args %#v", args)
		}
	}
	runner := &fakeRunner{}
	cmd := newAlertCommand(runner)
	cmd.SetOut(&bytes.Buffer{})
	cmd.SetErr(&bytes.Buffer{})
	cmd.SetArgs([]string{"ack", "--context-file=c", "--alert-id=a"})
	if err := cmd.Execute(); err == nil || len(runner.calls) != 0 {
		t.Fatal("ack requires key")
	}
}
