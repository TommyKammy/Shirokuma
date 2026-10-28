package cli

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"
)

const managedTestRequest = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

func managedTestAttestation() json.RawMessage {
	return json.RawMessage(`{"version":1,"generation_id":"generation","databases":{},"profile_ref":"` + strings.Repeat("a", 64) + `"}`)
}

// Real subprocesses exercise ExtraFiles, process groups and independent lives.
// All execution substitution stays internal to these tests.
func TestManagedHelperProcess(t *testing.T) {
	index := -1
	for i, arg := range os.Args {
		if arg == "--managed-test-child" {
			index = i
			break
		}
	}
	if index < 0 {
		return
	}
	args := os.Args[index+1:]
	if len(args) > 0 && args[0] == "grandchild" {
		time.Sleep(time.Minute)
		os.Exit(0)
	}
	f := os.NewFile(3, "managed")
	c, err := net.FileConn(f)
	f.Close()
	if err != nil {
		os.Exit(3)
	}
	defer c.Close()
	var hello managedHello
	if json.NewDecoder(c).Decode(&hello) != nil {
		os.Exit(4)
	}
	var e managedEnrollment
	if managedRead(hello.EnrollmentPath, &e) != nil {
		os.Exit(5)
	}
	location := e.Owners[hello.Role]
	var record managedRecord
	if managedRead(location.Record, &record) != nil || record.Owner != hello.Owner || record.Capability != hello.Capability || (hello.Purpose == "http" && record.Runtime != hello.Runtime) {
		os.Exit(6)
	}
	attestation := json.RawMessage("null")
	if hello.Purpose == "http" {
		attestation = managedTestAttestation()
	}
	ready := managedReady{1, "READY", hello.Role, hello.Owner, hello.Runtime, attestation}
	if len(args) > 0 && args[0] == "bad-owner" {
		ready.Owner = managedID()
	}
	var readyWire any = ready
	if len(args) > 0 && args[0] == "extra-ready" {
		b, _ := json.Marshal(ready)
		var fields map[string]any
		json.Unmarshal(b, &fields)
		fields["extra"] = true
		readyWire = fields
	}
	if json.NewEncoder(c).Encode(readyWire) != nil {
		os.Exit(7)
	}
	if hello.Purpose == "http" {
		if len(args) > 0 && args[0] == "eof" {
			c.Close()
			time.Sleep(time.Minute)
			os.Exit(0)
		}
		if len(args) > 0 && args[0] == "group" {
			child := exec.Command(os.Args[0], "-test.run=^TestManagedHelperProcess$", "--", "--managed-test-child", "grandchild")
			if child.Start() != nil {
				os.Exit(8)
			}
			os.WriteFile(filepath.Join(location.Directory, "grandchild"), []byte(fmt.Sprint(child.Process.Pid)), 0600)
		}
		io.Copy(io.Discard, c)
		os.Exit(0)
	}
	sub := ""
	if len(args) > 1 {
		sub = args[1]
	}
	if sub == "dispatch" || sub == "observe" {
		io.Copy(io.Discard, c)
		os.Exit(0)
	}
	var input struct {
		Request    string `json:"request_id"`
		Activation string `json:"activation_id"`
		Role       string `json:"owner_role"`
		Owner      string `json:"owner_instance_id"`
		Leaf       string `json:"leaf"`
		Intent     string `json:"intent_id"`
	}
	if json.NewDecoder(os.Stdin).Decode(&input) != nil {
		os.Exit(9)
	}
	var d managedDescriptor
	if managedRead(filepath.Join(e.StateDir, "activation-plans", input.Request, input.Activation+".json"), &d) != nil {
		os.Exit(10)
	}
	switch sub {
	case "resolve-activation":
		// Model the resolver's refusal after real credential-file bytes change.
		if !managedCheckBinding(d) {
			os.Exit(13)
		}
		json.NewEncoder(os.Stdout).Encode(d)
	case "authorize-leaf":
		if _, err := os.Stat(filepath.Join(location.Directory, "reject-"+input.Leaf)); err == nil {
			os.Exit(11)
		}
		json.NewEncoder(os.Stdout).Encode(managedAuthorization{1, "AUTHORIZED", input.Request, input.Activation, input.Role, input.Owner, input.Leaf, input.Intent, d.Candidate, d.Binding})
	default:
		os.Exit(12)
	}
	os.Exit(0)
}

func managedFixture(t *testing.T, mode string) *managedOwner {
	return managedFixtureRole(t, mode, "workbench")
}

func managedFixtureRole(t *testing.T, mode, role string) *managedOwner {
	t.Helper()
	// Keep the configured Unix socket below the macOS sockaddr_un limit.
	root, err := os.MkdirTemp("/private/tmp", "p53-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(root) })
	write := func(path string, value any) {
		t.Helper()
		if managedWrite(path, value) != nil {
			t.Fatal("private write")
		}
	}
	for _, dir := range []string{"state", "workbench", "native"} {
		if managedDir(filepath.Join(root, dir)) != nil {
			t.Fatal("private directory")
		}
	}
	profile := filepath.Join(root, "profile.json")
	write(profile, map[string]any{})
	auth := filepath.Join(root, "auth.json")
	write(auth, map[string]any{})
	e := managedEnrollment{Version: 1, Environment: "env", Warehouse: "warehouse", ID: "enrollment", Revision: 1, Executor: "executor", StateDir: filepath.Join(root, "state"), Profile: profile, Auth: auth, Owners: map[string]managedLocation{}, Origins: map[string]string{"workbench": "http://127.0.0.1:8888", "native": "http://127.0.0.1:8080", "backend": "http://127.0.0.1:8081"}, Foundation: json.RawMessage(`{}`), Preparation: json.RawMessage(`{}`)}
	for _, role := range []string{"workbench", "native"} {
		dir := filepath.Join(root, role)
		e.Owners[role] = managedLocation{dir, filepath.Join(dir, "owner.sock"), filepath.Join(dir, "owner.json")}
	}
	path := filepath.Join(root, "enrollment.json")
	write(path, e)
	t.Setenv("SHIROKUMA_RUNTIME_PROFILE", profile)
	factory := func(_ string, args ...string) *exec.Cmd {
		helper := append([]string{"-test.run=^TestManagedHelperProcess$", "--", "--managed-test-child"}, args...)
		return exec.Command(os.Args[0], helper...)
	}
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	args := []string{mode, "--port", "8888"}
	if role == "native" {
		args = []string{mode, "--port", "8080", "--bind", "127.0.0.1"}
	}
	o, err := newManagedOwner(ctx, root, "unused-python", args, path, role, factory)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(o.close)
	if _, err = o.startHTTP(o.record.Runtime, profile); err != nil {
		t.Fatal(err)
	}
	return o
}
func managedTestCall(o *managedOwner, action string) managedResponse {
	r := managedRequest{Version: 1, Action: action, Request: managedTestRequest, Role: o.record.Role, Owner: o.record.Owner, Capability: o.record.Capability}
	if strings.Contains(action, "ACTIVAT") {
		r.Activation = strings.Repeat("b", 64)
	}
	b, _ := json.Marshal(r)
	return o.handle(b)
}
func managedPlan(t *testing.T, o *managedOwner) (managedRequest, managedDescriptor) {
	t.Helper()
	profile := filepath.Join(o.root, "candidate.json")
	files := map[string]managedFileBinding{}
	config := map[string]string{}
	for _, key := range []string{"auth_file", "trino_auth_file", "trino_ca_file", "catalog_auth_file", "client_auth_file"} {
		path := filepath.Join(o.root, key+".json")
		if os.WriteFile(path, []byte("private-test-content"), 0600) != nil {
			t.Fatal("write")
		}
		sum := sha256.Sum256([]byte("private-test-content"))
		files[key] = managedFileBinding{path, hex.EncodeToString(sum[:])}
		config[key] = path
	}
	if managedWrite(profile, config) != nil {
		t.Fatal("write profile")
	}
	b, _ := os.ReadFile(profile)
	sum := sha256.Sum256(b)
	r := managedRequest{1, "ACTIVATE_CANDIDATE", managedTestRequest, o.record.Role, o.record.Owner, o.record.Capability, strings.Repeat("b", 64)}
	d := managedDescriptor{1, r.Request, r.Activation, r.Role, r.Owner, o.record.Runtime, managedID(), profile, managedBinding{profile, hex.EncodeToString(sum[:]), files}, managedTestAttestation(), "operation", "plan", "candidate"}
	dir := filepath.Join(o.enrollment.StateDir, "activation-plans", r.Request)
	if managedDir(dir) != nil || managedWrite(filepath.Join(dir, r.Activation+".json"), d) != nil {
		t.Fatal("descriptor")
	}
	return r, d
}

func managedRecovery(t *testing.T, o *managedOwner, r managedRequest, d managedDescriptor) managedRequest {
	t.Helper()
	r.Request = managedID()
	d.Request = r.Request
	dir := filepath.Join(o.enrollment.StateDir, "activation-plans", r.Request)
	if managedDir(dir) != nil || managedWrite(filepath.Join(dir, r.Activation+".json"), d) != nil {
		t.Fatal("recovery descriptor")
	}
	return r
}

func managedCountHTTPStarts(o *managedOwner) *int {
	count := new(int)
	factory := o.factory
	o.factory = func(name string, args ...string) *exec.Cmd {
		if len(args) > 0 && args[0] == o.args[0] {
			*count++
		}
		return factory(name, args...)
	}
	return count
}

func TestManagedLaunchMismatchHasNoArtifactsOrSpawn(t *testing.T) {
	for _, tc := range []struct {
		name, role string
		args       []string
	}{
		{"workbench port", "workbench", []string{"http", "--port", "8889"}},
		{"native port", "native", []string{"http", "--port", "8082", "--bind", "127.0.0.1"}},
		{"native wildcard", "native", []string{"http", "--port", "8080", "--bind", "0.0.0.0"}},
		{"native other loopback", "native", []string{"http", "--port", "8080", "--bind", "127.0.0.2"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			o := managedFixture(t, "http")
			e := o.enrollment
			dir := filepath.Join(o.root, "rejected")
			if managedDir(dir) != nil {
				t.Fatal("directory")
			}
			e.Owners = map[string]managedLocation{}
			for role, location := range o.enrollment.Owners {
				e.Owners[role] = location
			}
			e.Owners[tc.role] = managedLocation{dir, filepath.Join(dir, "owner.sock"), filepath.Join(dir, "owner.json")}
			if managedWrite(o.path, e) != nil {
				t.Fatal("enrollment")
			}
			spawns := 0
			factory := func(name string, args ...string) *exec.Cmd {
				spawns++
				return o.factory(name, args...)
			}
			owner, err := newManagedOwner(context.Background(), o.root, o.python, tc.args, o.path, tc.role, factory)
			if err == nil {
				owner.close()
				t.Fatal("launch mismatch accepted")
			}
			entries, err := os.ReadDir(dir)
			if err != nil || len(entries) != 0 || spawns != 0 {
				t.Fatal("rejected launch created owner artifacts or child")
			}
		})
	}
}
func TestManagedStrictIPCAndReadOnly(t *testing.T) {
	o := managedFixture(t, "http")
	o.mu.Lock()
	count := len(o.children)
	o.mu.Unlock()
	for _, action := range []string{"GET_REQUEST", "GET_ACTIVATION"} {
		for i := 0; i < 3; i++ {
			if got := managedTestCall(o, action); got.State != "NOT_FOUND" {
				t.Fatalf("GET state %s", got.State)
			}
		}
	}
	o.mu.Lock()
	after := len(o.children)
	o.mu.Unlock()
	if after != count {
		t.Fatal("GET launched children")
	}
	base := managedRequest{Version: 1, Action: "DISPATCH_REQUEST", Request: managedTestRequest, Role: o.record.Role, Owner: o.record.Owner, Capability: o.record.Capability}
	for _, mutate := range []func(map[string]any){
		func(m map[string]any) { m["extra"] = "value" }, func(m map[string]any) { m["capability"] = "bad" }, func(m map[string]any) { m["owner_instance_id"] = managedID() }, func(m map[string]any) { m["owner_role"] = "native" }, func(m map[string]any) { m["version"] = "1" }, func(m map[string]any) { m["request_id"] = "../path" }, func(m map[string]any) { m["action"] = "EXEC" }, func(m map[string]any) { m["activation_id"] = nil },
	} {
		b, _ := json.Marshal(base)
		var m map[string]any
		json.Unmarshal(b, &m)
		mutate(m)
		b, _ = json.Marshal(m)
		if o.handle(b).State != "REJECTED" {
			t.Fatal("invalid request accepted")
		}
	}
	b, _ := json.Marshal(base)
	b = append(b[:len(b)-1], []byte(`,"version":1}`)...)
	if o.handle(b).State != "REJECTED" {
		t.Fatal("duplicate field accepted")
	}
	// Also exercise the actual 0600 Unix socket, without any HTTP listener.
	c, err := net.Dial("unix", o.location.Socket)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	base.Action = "GET_REQUEST"
	json.NewEncoder(c).Encode(base)
	var response managedResponse
	if json.NewDecoder(c).Decode(&response) != nil || response.State != "NOT_FOUND" {
		t.Fatal("socket GET")
	}
	i, _ := os.Stat(o.location.Socket)
	if i.Mode().Perm() != 0600 {
		t.Fatal("socket permissions")
	}
}
func TestManagedDuplicateDispatchAndIndependentLifetime(t *testing.T) {
	o := managedFixture(t, "http")
	var wg sync.WaitGroup
	states := make(chan string, 8)
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); states <- managedTestCall(o, "DISPATCH_REQUEST").State }()
	}
	wg.Wait()
	close(states)
	for s := range states {
		if s != "DISPATCHED" {
			t.Fatalf("dispatch %s", s)
		}
	}
	o.mu.Lock()
	var worker *managedChild
	count := len(o.children)
	for c := range o.children {
		if c != o.http {
			worker = c
		}
	}
	old := o.http
	o.mu.Unlock()
	if count != 2 || worker == nil {
		t.Fatal("duplicate worker spawn")
	}
	if o.stopChild(old) != nil {
		t.Fatal("stop HTTP")
	}
	select {
	case <-worker.done:
		t.Fatal("HTTP stop cancelled operator")
	default:
	}
	if managedTestCall(o, "GET_REQUEST").State != "DISPATCHED" {
		t.Fatal("IPC stopped with HTTP")
	}
	if managedTestCall(o, "DISPATCH_REQUEST").State != "DISPATCHED" {
		t.Fatal("duplicate receipt")
	}
	if o.stopChild(worker) != nil {
		t.Fatal("stop worker")
	}
	if managedTestCall(o, "DISPATCH_REQUEST").State != "DISPATCHED" {
		t.Fatal("replayed exited worker")
	}
	o.close()
	if managedTestCall(o, "DISPATCH_REQUEST").State != "UNAVAILABLE" {
		t.Fatal("shutdown dispatch")
	}
}
func TestManagedEOFAndProcessGroups(t *testing.T) {
	t.Run("EOF fences child", func(t *testing.T) {
		o := managedFixture(t, "eof")
		select {
		case <-o.http.fenced:
		case <-time.After(3 * time.Second):
			t.Fatal("EOF not observed")
		}
		select {
		case <-o.http.done:
		case <-time.After(3 * time.Second):
			t.Fatal("unfenced process")
		}
	})
	t.Run("own process group", func(t *testing.T) {
		o := managedFixture(t, "group")
		var pid int
		deadline := time.Now().Add(3 * time.Second)
		for time.Now().Before(deadline) {
			b, _ := os.ReadFile(filepath.Join(o.location.Directory, "grandchild"))
			if _, err := fmt.Sscan(string(b), &pid); err == nil {
				break
			}
			time.Sleep(10 * time.Millisecond)
		}
		if pid == 0 {
			t.Fatal("grandchild not started")
		}
		pgid, err := syscall.Getpgid(pid)
		if err != nil || pgid != o.http.cmd.Process.Pid {
			t.Fatal("descendant not in independent child group")
		}
		if pgid == syscall.Getpgrp() {
			t.Fatal("owner shares child group")
		}
		if o.stopChild(o.http) != nil {
			t.Fatal("group stop")
		}
		deadline = time.Now().Add(3 * time.Second)
		for time.Now().Before(deadline) {
			if syscall.Kill(pid, 0) == syscall.ESRCH {
				return
			}
			time.Sleep(10 * time.Millisecond)
		}
		t.Fatal("group descendant survived")
	})
}
func TestManagedActivationAndNoBootstrapCutover(t *testing.T) {
	o := managedFixture(t, "http")
	old := o.http
	// Enrollment/HTTP bootstrap and GET never invoke an activation resolver.
	if entries, _ := os.ReadDir(filepath.Join(o.location.Directory, "activations")); len(entries) != 0 {
		t.Fatal("bootstrap activation")
	}
	r, d := managedPlan(t, o)
	b, _ := json.Marshal(r)
	if got := o.handle(b); got.State != "ACK" || got.Previous != d.Old || got.Runtime != d.Candidate {
		t.Fatalf("activation %s", got.State)
	}
	select {
	case <-old.done:
	default:
		t.Fatal("old HTTP remains")
	}
	current := o.http
	starts := managedCountHTTPStarts(o)
	var original managedActivation
	if managedRead(o.activationPath(r), &original) != nil {
		t.Fatal("original journal")
	}
	r = managedRecovery(t, o, r, d)
	b, _ = json.Marshal(r)
	if got := o.handle(b); got.State != "ACK" || got.Request != r.Request || o.http != current || *starts != 0 {
		t.Fatal("completed activation restarted")
	}
	var a managedActivation
	if managedRead(o.activationPath(r), &a) != nil || a.Descriptor.Request != managedTestRequest || a.Stop.State != "ACK" || a.Start.State != "ACK" || a.Readiness.State != "ACK" {
		t.Fatal("missing actual leaf ACK")
	}
	if a.Stop != original.Stop || a.Start != original.Start || a.Readiness != original.Readiness {
		t.Fatal("finished recovery replaced original leaf receipts")
	}
	if managedTestCall(o, "GET_ACTIVATION").State != "ACK" {
		t.Fatal("GET receipt")
	}
}

func TestManagedPresendResolverDenialRetainsUnsent(t *testing.T) {
	for _, role := range []string{"native", "workbench"} {
		for _, recovery := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/fresh-request=%v", role, recovery), func(t *testing.T) {
				o := managedFixtureRole(t, "http", role)
				r, d := managedPlan(t, o)
				old := o.http
				starts := managedCountHTTPStarts(o)
				auth := d.Binding.Files["auth_file"].Path
				original, err := os.ReadFile(auth)
				if err != nil || os.WriteFile(auth, []byte(`{"accounts":[]}`), 0600) != nil {
					t.Fatal("change actual auth bytes")
				}
				b, _ := json.Marshal(r)
				if got := o.handle(b); got.State != "UNKNOWN" || *starts != 0 || o.http != old {
					t.Fatal("resolver denial sent an effect")
				}
				select {
				case <-old.done:
					t.Fatal("resolver denial stopped old child")
				default:
				}
				var a managedActivation
				if managedPrivate(o.activationPath(r), false) != nil || managedRead(o.activationPath(r), &a) != nil || !managedSameDescriptor(a.Descriptor, d) || a.State != "UNSENT" || a.Stop != (managedLeaf{State: "UNSENT"}) || a.Start != (managedLeaf{State: "UNSENT"}) || a.Readiness != (managedLeaf{State: "UNSENT"}) {
					t.Fatal("missing private positive zero-send evidence")
				}
				get := r
				get.Action = "GET_ACTIVATION"
				gb, _ := json.Marshal(get)
				if got := o.handle(gb); got.State != "UNSENT" || got.Previous != d.Old {
					t.Fatal("GET did not expose positive evidence")
				}
				if os.WriteFile(auth, original, 0600) != nil {
					t.Fatal("restore auth")
				}
				if recovery {
					r = managedRecovery(t, o, r, d)
				}
				b, _ = json.Marshal(r)
				if got := o.handle(b); got.State != "ACK" || *starts != 1 || got.Runtime != d.Candidate {
					t.Fatal("authorized continuation did not execute once")
				}
				if got := o.handle(b); got.State != "ACK" || *starts != 1 {
					t.Fatal("duplicate ACK restarted child")
				}
				if managedRead(o.activationPath(r), &a) != nil || !managedSameDescriptor(a.Descriptor, d) || a.Stop.State != "ACK" || a.Start.State != "ACK" || a.Readiness.State != "ACK" {
					t.Fatal("continuation replaced original descriptor or lost ACKs")
				}
			})
		}
	}
}

func TestManagedPresendInvalidObservationHasNoJournal(t *testing.T) {
	for _, kind := range []string{"missing", "invalid", "extra", "missing field", "owner", "role", "request", "activation", "old", "candidate", "profile path", "binding path", "binding digest", "binding keys", "binding extra", "unknown child", "unhandshaken", "dead", "fenced"} {
		t.Run(kind, func(t *testing.T) {
			o := managedFixture(t, "http")
			r, d := managedPlan(t, o)
			fixed := filepath.Join(o.enrollment.StateDir, "activation-plans", r.Request, r.Activation+".json")
			b, _ := json.Marshal(d)
			var fields map[string]any
			json.Unmarshal(b, &fields)
			switch kind {
			case "missing":
				os.Remove(fixed)
			case "invalid":
				os.WriteFile(fixed, []byte("{"), 0600)
			case "extra":
				fields["extra"] = true
			case "missing field":
				delete(fields, "original_plan_id")
			case "owner":
				fields["owner_instance_id"] = managedID()
			case "role":
				fields["owner_role"] = "native"
			case "request":
				fields["request_id"] = managedID()
			case "activation":
				fields["activation_id"] = strings.Repeat("c", 64)
			case "old":
				fields["old_runtime_instance_id"] = managedID()
			case "candidate":
				fields["candidate_runtime_instance_id"] = d.Old
			case "profile path":
				fields["profile_path"] = "relative.json"
			case "binding path":
				fields["profile_binding"].(map[string]any)["profile_path"] = filepath.Join(o.root, "other.json")
			case "binding digest":
				fields["profile_binding"].(map[string]any)["profile"] = "bad"
			case "binding keys", "binding extra":
				files := fields["profile_binding"].(map[string]any)["files"].(map[string]any)
				if kind == "binding keys" {
					delete(files, "auth_file")
				} else {
					files["auth_file"].(map[string]any)["extra"] = true
				}
			case "unknown child":
				o.mu.Lock()
				delete(o.children, o.http)
				o.mu.Unlock()
				defer o.stopChild(o.http)
			case "unhandshaken":
				o.http.ready = managedReady{}
			case "dead":
				if o.stopChild(o.http) != nil {
					t.Fatal("stop fixture")
				}
			case "fenced":
				// Substitute only the observation channel; the real FD fence and
				// cleanup still belong to the fixture's retained child.
				old := o.http
				fenced := make(chan struct{})
				close(fenced)
				copy := &managedChild{ready: old.ready, done: old.done, fenced: fenced}
				o.mu.Lock()
				o.children[copy] = true
				o.http = copy
				o.mu.Unlock()
				defer func() {
					o.mu.Lock()
					delete(o.children, copy)
					o.http = old
					o.mu.Unlock()
				}()
			}
			if kind != "missing" && kind != "invalid" && managedWrite(fixed, fields) != nil {
				t.Fatal("write invalid descriptor")
			}
			old := o.http
			starts := managedCountHTTPStarts(o)
			b, _ = json.Marshal(r)
			if got := o.handle(b); got.State != "UNKNOWN" || *starts != 0 || o.http != old {
				t.Fatal("invalid observation executed")
			}
			if _, err := os.Stat(o.activationPath(r)); !os.IsNotExist(err) {
				t.Fatal("invalid observation created positive journal")
			}
			if kind != "dead" {
				select {
				case <-old.done:
					t.Fatal("invalid observation stopped child")
				default:
				}
			}
		})
	}
}

func TestManagedPresendDenialPreservesExistingIntent(t *testing.T) {
	o := managedFixture(t, "http")
	r, d := managedPlan(t, o)
	a := managedActivation{Descriptor: d, Stop: managedLeaf{State: "INTENT", Intent: managedID()}, Start: managedLeaf{State: "UNSENT"}, Readiness: managedLeaf{State: "UNSENT"}, State: "UNKNOWN"}
	if managedWrite(o.activationPath(r), a) != nil || os.WriteFile(d.Binding.Files["auth_file"].Path, []byte(`{"accounts":[]}`), 0600) != nil {
		t.Fatal("set existing uncertainty and revoke auth")
	}
	before, _ := os.ReadFile(o.activationPath(r))
	old := o.http
	starts := managedCountHTTPStarts(o)
	b, _ := json.Marshal(r)
	if got := o.handle(b); got.State != "UNKNOWN" || *starts != 0 || o.http != old {
		t.Fatal("existing INTENT executed")
	}
	after, _ := os.ReadFile(o.activationPath(r))
	if string(before) != string(after) {
		t.Fatal("existing INTENT journal changed")
	}
}

func TestManagedActivationKeepsOperatorAndOwnerAlive(t *testing.T) {
	o := managedFixture(t, "http")
	if managedTestCall(o, "DISPATCH_REQUEST").State != "DISPATCHED" {
		t.Fatal("dispatch")
	}
	o.mu.Lock()
	var worker *managedChild
	for c := range o.children {
		if c != o.http {
			worker = c
		}
	}
	o.mu.Unlock()
	r, _ := managedPlan(t, o)
	b, _ := json.Marshal(r)
	if o.handle(b).State != "ACK" {
		t.Fatal("cutover")
	}
	select {
	case <-worker.done:
		t.Fatal("operator cancelled by cutover")
	default:
	}
	if managedTestCall(o, "GET_REQUEST").State != "DISPATCHED" {
		t.Fatal("owner/IPC lost during cutover")
	}
}

func TestManagedContinueOnlyPositivelyUnsentStart(t *testing.T) {
	o := managedFixture(t, "http")
	r, d := managedPlan(t, o)
	old := o.http
	if o.stopChild(old) != nil {
		t.Fatal("known stop")
	}
	// This represents an interrupted owner handler AFTER durable stop ACK and
	// BEFORE start intent. The resolver/authorizer must still grant current use.
	a := managedActivation{Descriptor: d, Stop: managedLeaf{State: "ACK", Intent: managedID()}, Start: managedLeaf{State: "UNSENT"}, Readiness: managedLeaf{State: "UNSENT"}, State: "UNKNOWN"}
	if managedWrite(o.activationPath(r), a) != nil {
		t.Fatal("journal")
	}
	r = managedRecovery(t, o, r, d)
	starts := managedCountHTTPStarts(o)
	b, _ := json.Marshal(r)
	if got := o.handle(b); got.State != "ACK" || got.Runtime != d.Candidate || *starts != 1 {
		t.Fatalf("unsent continuation %s", got.State)
	}
	var after managedActivation
	if managedRead(o.activationPath(r), &after) != nil || after.Stop.Intent != a.Stop.Intent || after.Descriptor.Request != d.Request {
		t.Fatal("repeated acknowledged stop")
	}
	entries, _ := os.ReadDir(filepath.Dir(o.activationPath(r)))
	if len(entries) != 1 {
		t.Fatal("recovery escaped original journal")
	}
}

func TestManagedPrivateEnrollmentRejectsUnknownAndDrift(t *testing.T) {
	for _, kind := range []string{"unknown", "permissions", "profile", "owner role"} {
		t.Run(kind, func(t *testing.T) {
			o := managedFixture(t, "http")
			data, err := os.ReadFile(o.path)
			if err != nil {
				t.Fatal(err)
			}
			var value map[string]any
			json.Unmarshal(data, &value)
			switch kind {
			case "unknown":
				value["extra"] = true
			case "permissions":
				os.Chmod(o.path, 0644)
			case "profile":
				value["base_runtime_profile"] = filepath.Join(o.root, "other-profile")
			case "owner role":
				owners := value["owners"].(map[string]any)
				owners["other"] = owners["native"]
				delete(owners, "native")
			}
			if kind != "permissions" {
				if managedWrite(o.path, value) != nil {
					t.Fatal("write")
				}
			}
			if _, err := newManagedOwner(context.Background(), o.root, o.python, o.args, o.path, "native", o.factory); err == nil {
				t.Fatal("invalid enrollment accepted")
			}
		})
	}
}
func TestManagedUnknownNeverReplays(t *testing.T) {
	for _, leaf := range []string{"stop", "start", "readiness"} {
		t.Run(leaf, func(t *testing.T) {
			o := managedFixture(t, "http")
			r, d := managedPlan(t, o)
			a := managedActivation{Descriptor: d, Stop: managedLeaf{State: "UNSENT"}, Start: managedLeaf{State: "UNSENT"}, Readiness: managedLeaf{State: "UNSENT"}, State: "UNKNOWN"}
			switch leaf {
			case "stop":
				a.Stop = managedLeaf{State: "INTENT", Intent: managedID()}
			case "start":
				a.Stop.State = "ACK"
				a.Start = managedLeaf{State: "INTENT", Intent: managedID()}
			case "readiness":
				a.Stop.State = "ACK"
				a.Start.State = "ACK"
				a.Readiness = managedLeaf{State: "INTENT", Intent: managedID()}
			}
			if managedWrite(o.activationPath(r), a) != nil {
				t.Fatal("write")
			}
			// Perform the real transition, then deliberately omit its ACK.
			if o.stopChild(o.http) != nil {
				t.Fatal("stop effect")
			}
			if leaf == "start" {
				if _, err := o.spawn("http", d.Candidate, d.Profile, o.args, nil); err != nil {
					t.Fatal("start effect")
				}
			}
			if leaf == "readiness" {
				if _, err := o.startHTTP(d.Candidate, d.Profile); err != nil {
					t.Fatal("readiness effect")
				}
			}
			old := o.http
			starts := managedCountHTTPStarts(o)
			r = managedRecovery(t, o, r, d)
			b, _ := json.Marshal(r)
			if o.handle(b).State != "UNKNOWN" || o.http != old || *starts != 0 {
				t.Fatal("uncertain leaf replay")
			}
			var after managedActivation
			if managedRead(o.activationPath(r), &after) != nil || !managedSameDescriptor(after.Descriptor, a.Descriptor) || after.Stop != a.Stop || after.Start != a.Start || after.Readiness != a.Readiness {
				t.Fatal("recovery changed original uncertainty")
			}
		})
	}
	t.Run("dispatch intent", func(t *testing.T) {
		o := managedFixture(t, "http")
		r := managedRequest{Request: managedTestRequest}
		if managedWrite(o.requestPath(r), o.response(r, "INTENT")) != nil {
			t.Fatal("write")
		}
		if managedTestCall(o, "DISPATCH_REQUEST").State != "UNKNOWN" || managedTestCall(o, "GET_REQUEST").State != "UNKNOWN" {
			t.Fatal("intent replay")
		}
	})
}

func TestManagedRecoveryRejectsChangedDescriptor(t *testing.T) {
	for _, field := range []string{"operation", "plan", "candidate", "old", "successor", "profile", "binding", "attestation"} {
		t.Run(field, func(t *testing.T) {
			o := managedFixture(t, "http")
			r, d := managedPlan(t, o)
			a := managedActivation{Descriptor: d, Stop: managedLeaf{State: "ACK", Intent: managedID()}, Start: managedLeaf{State: "UNSENT"}, Readiness: managedLeaf{State: "UNSENT"}, State: "UNKNOWN"}
			if managedWrite(o.activationPath(r), a) != nil {
				t.Fatal("original journal")
			}
			switch field {
			case "operation":
				d.Operation = "other"
			case "plan":
				d.Plan = "other"
			case "candidate":
				d.CandidateID = "other"
			case "old":
				d.Old = managedID()
			case "successor":
				d.Candidate = managedID()
			case "profile":
				d.Profile = filepath.Join(o.root, "other-profile")
			case "binding":
				d.Binding.Profile = strings.Repeat("c", 64)
			case "attestation":
				d.Attestation = json.RawMessage(strings.Replace(string(d.Attestation), "generation\"", "other\"", 1))
			}
			r = managedRecovery(t, o, r, d)
			starts := managedCountHTTPStarts(o)
			b, _ := json.Marshal(r)
			if o.handle(b).State != "UNKNOWN" || *starts != 0 {
				t.Fatal("changed immutable recovery descriptor accepted")
			}
			var after managedActivation
			if managedRead(o.activationPath(r), &after) != nil || !managedSameDescriptor(after.Descriptor, a.Descriptor) || after.Stop != a.Stop || after.Start != a.Start {
				t.Fatal("changed original journal")
			}
		})
	}
}
func TestManagedActivationSealAndAuthorization(t *testing.T) {
	for _, kind := range []string{"profile drift", "descriptor identity", "stop rejection"} {
		t.Run(kind, func(t *testing.T) {
			o := managedFixture(t, "http")
			r, d := managedPlan(t, o)
			switch kind {
			case "profile drift":
				os.WriteFile(d.Profile, []byte("{}"), 0600)
			case "descriptor identity":
				d.Owner = managedID()
				managedWrite(filepath.Join(o.enrollment.StateDir, "activation-plans", r.Request, r.Activation+".json"), d)
			case "stop rejection":
				os.WriteFile(filepath.Join(o.location.Directory, "reject-stop"), []byte("x"), 0600)
			}
			b, _ := json.Marshal(r)
			old := o.http
			if o.handle(b).State != "UNKNOWN" || o.http != old {
				t.Fatal("unauthorized cutover")
			}
			select {
			case <-old.done:
				t.Fatal("old process stopped before authorization")
			default:
			}
		})
	}
}
func TestManagedStaleRecordRequiresObservation(t *testing.T) {
	o := managedFixture(t, "http")
	o.close()
	if _, err := newManagedOwner(context.Background(), o.root, o.python, o.args, o.path, o.record.Role, o.factory); err == nil {
		t.Fatal("stale record adopted")
	}
	if managedTestCall(o, "GET_REQUEST").State != "UNAVAILABLE" {
		t.Fatal("closed owner")
	}
}

func TestManagedRejectsUnattestedChildIdentity(t *testing.T) {
	for _, mode := range []string{"bad-owner", "extra-ready"} {
		t.Run(mode, func(t *testing.T) {
			o := managedFixture(t, "http")
			o.stopChild(o.http)
			o.args = []string{mode}
			c, err := o.startHTTP(managedID(), o.enrollment.Profile)
			if err == nil {
				t.Fatal("unattested child accepted")
			}
			select {
			case <-c.done:
			case <-time.After(3 * time.Second):
				t.Fatal("rejected child survived")
			}
		})
	}
}

func TestManagedBothOwnerRoles(t *testing.T) {
	o := managedFixture(t, "http")
	native, err := newManagedOwner(context.Background(), o.root, o.python, []string{"http", "--port", "8080", "--bind", "127.0.0.1"}, o.path, "native", o.factory)
	if err != nil {
		t.Fatal(err)
	}
	defer native.close()
	if _, err = native.startHTTP(native.record.Runtime, native.enrollment.Profile); err != nil {
		t.Fatal(err)
	}
	if native.record.Owner == o.record.Owner || native.record.Capability == o.record.Capability {
		t.Fatal("owner identity shared")
	}
	if managedTestCall(native, "GET_REQUEST").State != "NOT_FOUND" {
		t.Fatal("native read-only IPC")
	}
	native.close()
	select {
	case <-o.http.done:
		t.Fatal("peer owner shutdown cancelled workbench")
	default:
	}
}

// Keep the line reader exercised with the actual wire framing used by Python.
func TestManagedDecodeStrict(t *testing.T) {
	for _, raw := range []string{`{"version":1,"version":1}`, `{"version":"1"}`, `{"version":1} {}`, `{"version":1,"extra":true}`} {
		var r managedRequest
		if managedDecode([]byte(raw), &r) == nil {
			t.Fatal("invalid JSON accepted")
		}
	}
	reader := bufio.NewReader(strings.NewReader("{}\n"))
	line, err := reader.ReadBytes('\n')
	if err != nil || string(line) != "{}\n" {
		t.Fatal("framing")
	}
}
