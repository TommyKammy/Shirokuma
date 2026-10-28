package cli

// The managed owner is deliberately limited to the two product HTTP commands
// and the database creation worker. It never adopts an existing PID or socket.
import (
	"bufio"
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

var errManaged = errors.New("managed database creation owner unavailable; private observation required")
var managedUUID = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
var managedDigest = regexp.MustCompile(`^[0-9a-f]{64}$`)

type managedLocation struct {
	Directory string `json:"directory"`
	Socket    string `json:"socket_path"`
	Record    string `json:"record_path"`
}
type managedEnrollment struct {
	Version     int                        `json:"version"`
	Environment string                     `json:"environment_id"`
	Warehouse   string                     `json:"warehouse_id"`
	ID          string                     `json:"enrollment_id"`
	Revision    int                        `json:"revision"`
	Executor    string                     `json:"executor_id"`
	StateDir    string                     `json:"state_dir"`
	Profile     string                     `json:"base_runtime_profile"`
	Auth        string                     `json:"executor_auth_file"`
	Owners      map[string]managedLocation `json:"owners"`
	Origins     map[string]string          `json:"origins"`
	Foundation  json.RawMessage            `json:"foundation"`
	Preparation json.RawMessage            `json:"preparation"`
}
type managedRecord struct {
	Version     int    `json:"version"`
	Environment string `json:"environment_id"`
	Enrollment  string `json:"enrollment_id"`
	Revision    int    `json:"enrollment_revision"`
	Role        string `json:"owner_role"`
	Owner       string `json:"owner_instance_id"`
	Runtime     string `json:"runtime_instance_id"`
	Socket      string `json:"socket_path"`
	Capability  string `json:"capability"`
}
type managedHello struct {
	managedRecord
	Purpose        string `json:"purpose"`
	EnrollmentPath string `json:"private_enrollment_path"`
	Profile        string `json:"profile_path"`
}
type managedReady struct {
	Version     int             `json:"version"`
	State       string          `json:"state"`
	Role        string          `json:"owner_role"`
	Owner       string          `json:"owner_instance_id"`
	Runtime     string          `json:"runtime_instance_id"`
	Attestation json.RawMessage `json:"runtime_attestation"`
}
type managedRequest struct {
	Version    int    `json:"version"`
	Action     string `json:"action"`
	Request    string `json:"request_id"`
	Role       string `json:"owner_role"`
	Owner      string `json:"owner_instance_id"`
	Capability string `json:"capability"`
	Activation string `json:"activation_id,omitempty"`
}
type managedResponse struct {
	Version    int    `json:"version"`
	Action     string `json:"action"`
	Request    string `json:"request_id"`
	State      string `json:"state"`
	Role       string `json:"owner_role"`
	Owner      string `json:"owner_instance_id"`
	Runtime    string `json:"runtime_instance_id"`
	Activation string `json:"activation_id,omitempty"`
	Previous   string `json:"previous_runtime_instance_id,omitempty"`
	Code       string `json:"code,omitempty"`
}

func (r managedResponse) MarshalJSON() ([]byte, error) {
	type wire managedResponse
	b, err := json.Marshal(wire(r))
	if err != nil {
		return nil, err
	}
	if r.Activation == "" {
		return b, nil
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(b, &fields) != nil {
		return nil, errManaged
	}
	fields["previous_runtime_instance_id"], err = json.Marshal(r.Previous)
	if err != nil {
		return nil, err
	}
	return json.Marshal(fields)
}

type managedFileBinding struct {
	Path   string `json:"path"`
	Digest string `json:"digest"`
}
type managedBinding struct {
	Path    string                        `json:"profile_path"`
	Profile string                        `json:"profile"`
	Files   map[string]managedFileBinding `json:"files"`
}
type managedDescriptor struct {
	Version     int             `json:"version"`
	Request     string          `json:"request_id"`
	Activation  string          `json:"activation_id"`
	Role        string          `json:"owner_role"`
	Owner       string          `json:"owner_instance_id"`
	Old         string          `json:"old_runtime_instance_id"`
	Candidate   string          `json:"candidate_runtime_instance_id"`
	Profile     string          `json:"profile_path"`
	Binding     managedBinding  `json:"profile_binding"`
	Attestation json.RawMessage `json:"runtime_attestation"`
	Operation   string          `json:"original_operation_id"`
	Plan        string          `json:"original_plan_id"`
	CandidateID string          `json:"candidate_id"`
}
type managedAuthorization struct {
	Version    int            `json:"version"`
	State      string         `json:"state"`
	Request    string         `json:"request_id"`
	Activation string         `json:"activation_id"`
	Role       string         `json:"owner_role"`
	Owner      string         `json:"owner_instance_id"`
	Leaf       string         `json:"leaf"`
	Intent     string         `json:"intent_id"`
	Candidate  string         `json:"candidate_runtime_instance_id"`
	Binding    managedBinding `json:"profile_binding"`
}
type managedLeaf struct {
	State  string `json:"state"`
	Intent string `json:"intent_id,omitempty"`
}
type managedActivation struct {
	Descriptor managedDescriptor `json:"descriptor"`
	Stop       managedLeaf       `json:"stop"`
	Start      managedLeaf       `json:"start"`
	Readiness  managedLeaf       `json:"readiness"`
	State      string            `json:"state"`
}

// Tests substitute only command construction, retaining real spawn/FD/group paths.
type managedProcessFactory func(string, ...string) *exec.Cmd
type managedChild struct {
	cmd      *exec.Cmd
	fd       net.Conn
	done     chan struct{}
	fenced   chan struct{}
	stopOnce sync.Once
	stopErr  error
	ready    managedReady
	err      error
}
type managedOwner struct {
	mu                 sync.Mutex
	activationMu       sync.Mutex
	enrollment         managedEnrollment
	path, root, python string
	location           managedLocation
	record             managedRecord
	args               []string
	factory            managedProcessFactory
	listener           net.Listener
	children           map[*managedChild]bool
	http               *managedChild
	closed             bool
	ctx                context.Context
	cancel             context.CancelFunc
}

func managedID() string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic(err)
	}
	b[6] = b[6]&15 | 64
	b[8] = b[8]&63 | 128
	h := hex.EncodeToString(b[:])
	return h[:8] + "-" + h[8:12] + "-" + h[12:16] + "-" + h[16:20] + "-" + h[20:]
}

// Reject duplicate keys, nulls, unknown fields and trailing JSON as well as bad
// scalar types. Embedded RawMessages are private Python-owned typed objects.
func managedDecode(data []byte, value any) error {
	d := json.NewDecoder(bytes.NewReader(data))
	var walk func() error
	walk = func() error {
		t, err := d.Token()
		if err != nil {
			return err
		}
		if t == nil {
			return nil
		}
		if delim, ok := t.(json.Delim); ok {
			switch delim {
			case '{':
				seen := map[string]bool{}
				for d.More() {
					k, e := d.Token()
					if e != nil {
						return e
					}
					key, ok := k.(string)
					if !ok || seen[key] {
						return errManaged
					}
					seen[key] = true
					if e = walk(); e != nil {
						return e
					}
				}
			case '[':
				for d.More() {
					if e := walk(); e != nil {
						return e
					}
				}
			default:
				return errManaged
			}
			_, err = d.Token()
			return err
		}
		return nil
	}
	if walk() != nil {
		return errManaged
	}
	if _, err := d.Token(); err != io.EOF {
		return errManaged
	}
	d = json.NewDecoder(bytes.NewReader(data))
	d.DisallowUnknownFields()
	if d.Decode(value) != nil {
		return errManaged
	}
	return nil
}
func managedExact(data []byte, keys ...string) bool {
	var m map[string]json.RawMessage
	if json.Unmarshal(data, &m) != nil || len(m) != len(keys) {
		return false
	}
	for _, k := range keys {
		v, ok := m[k]
		if !ok || bytes.Equal(bytes.TrimSpace(v), []byte("null")) {
			return false
		}
	}
	return true
}
func managedPrivate(path string, directory bool) error {
	if !filepath.IsAbs(path) || filepath.Clean(path) != path {
		return errManaged
	}
	i, err := os.Stat(path)
	if err != nil {
		return errManaged
	}
	want := os.FileMode(0600)
	if directory {
		want = 0700
	}
	if i.IsDir() != directory || i.Mode().Perm() != want || (!directory && !i.Mode().IsRegular()) {
		return errManaged
	}
	return nil
}
func managedRead(path string, v any) error {
	if managedPrivate(path, false) != nil {
		return errManaged
	}
	b, err := os.ReadFile(path)
	if err != nil || len(b) > 1024*1024 {
		return errManaged
	}
	return managedDecode(b, v)
}

// File sync followed by directory sync makes intent durable before sending.
func managedWrite(path string, v any) error {
	b, err := json.Marshal(v)
	if err != nil {
		return errManaged
	}
	f, err := os.CreateTemp(filepath.Dir(path), ".owner-")
	if err != nil {
		return errManaged
	}
	name := f.Name()
	defer os.Remove(name)
	if err = f.Chmod(0600); err == nil {
		_, err = f.Write(append(b, '\n'))
	}
	if err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err != nil || closeErr != nil {
		return errManaged
	}
	if os.Rename(name, path) != nil {
		return errManaged
	}
	dir, err := os.Open(filepath.Dir(path))
	if err != nil {
		return errManaged
	}
	defer dir.Close()
	if dir.Sync() != nil {
		return errManaged
	}
	return nil
}
func managedDir(path string) error {
	if os.MkdirAll(path, 0700) != nil || managedPrivate(path, true) != nil {
		return errManaged
	}
	return nil
}

// Compare the actual fixed HTTP command's address before creating any owner
// artifacts. These product servers speak HTTP and Workbench binds 127.0.0.1.
func managedLaunchMatches(origin, role string, args []string) bool {
	u, err := url.Parse(origin)
	if err != nil || u.Scheme != "http" {
		return false
	}
	values := map[string]string{}
	for i := 0; i < len(args); i++ {
		if args[i] != "--port" && args[i] != "--bind" {
			continue
		}
		key := args[i]
		if _, duplicate := values[key]; duplicate || i+1 >= len(args) {
			return false
		}
		i++
		values[key] = args[i]
	}
	bind := "127.0.0.1"
	if role == "native" {
		bind = values["--bind"]
	} else if role != "workbench" || values["--bind"] != "" {
		return false
	}
	ip := net.ParseIP(bind)
	port, err := strconv.Atoi(values["--port"])
	if err != nil || port < 1 || port > 65535 || ip == nil || !ip.IsLoopback() || u.Hostname() != bind {
		return false
	}
	enrolledPort := u.Port()
	if enrolledPort == "" {
		enrolledPort = "80"
	}
	return enrolledPort == strconv.Itoa(port)
}

func newManagedOwner(ctx context.Context, root, python string, args []string, path, role string, factory managedProcessFactory) (*managedOwner, error) {
	var e managedEnrollment
	if managedRead(path, &e) != nil || e.Version != 1 || e.Revision < 1 || e.Environment == "" || e.Warehouse == "" || e.ID == "" || e.Executor == "" || len(e.Owners) != 2 || len(e.Origins) != 3 {
		return nil, errManaged
	}
	if managedPrivate(filepath.Dir(path), true) != nil || managedPrivate(e.StateDir, true) != nil || managedPrivate(e.Profile, false) != nil || managedPrivate(e.Auth, false) != nil {
		return nil, errManaged
	}
	for _, role := range []string{"workbench", "native"} {
		l, ok := e.Owners[role]
		if !ok || managedPrivate(l.Directory, true) != nil || filepath.Dir(l.Record) != l.Directory || filepath.Dir(l.Socket) != l.Directory || !filepath.IsAbs(l.Socket) || filepath.Clean(l.Socket) != l.Socket || filepath.Clean(l.Record) != l.Record || len(l.Socket) > 100 {
			return nil, errManaged
		}
	}
	for _, role := range []string{"workbench", "native", "backend"} {
		u, err := url.Parse(e.Origins[role])
		if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Path != "" && u.Path != "/") {
			return nil, errManaged
		}
		h := u.Hostname()
		ip := net.ParseIP(h)
		if h != "localhost" && (ip == nil || !ip.IsLoopback()) {
			return nil, errManaged
		}
	}
	if e.Origins["workbench"] == e.Origins["native"] || e.Origins["workbench"] == e.Origins["backend"] || e.Origins["native"] == e.Origins["backend"] {
		return nil, errManaged
	}
	if !managedLaunchMatches(e.Origins[role], role, args) {
		return nil, errManaged
	}
	for _, raw := range []json.RawMessage{e.Foundation, e.Preparation} {
		var object map[string]json.RawMessage
		if json.Unmarshal(raw, &object) != nil || object == nil {
			return nil, errManaged
		}
	}
	profile, set := os.LookupEnv("SHIROKUMA_RUNTIME_PROFILE")
	if !set {
		profile = filepath.Join(root, "var", "shirokuma", "runtime-profile.json")
	}
	profile, err := filepath.Abs(profile)
	if err != nil || profile != e.Profile {
		return nil, errManaged
	}
	l, ok := e.Owners[role]
	if !ok || managedDir(l.Directory) != nil || managedPrivate(filepath.Dir(l.Socket), true) != nil {
		return nil, errManaged
	}
	// Never delete/reclaim a prior owner's record or listener.
	for _, p := range []string{l.Record, l.Socket} {
		if _, err := os.Lstat(p); !os.IsNotExist(err) {
			return nil, errManaged
		}
	}
	var secret [32]byte
	if _, err := rand.Read(secret[:]); err != nil {
		return nil, errManaged
	}
	ownerCtx, cancel := context.WithCancel(ctx)
	o := &managedOwner{enrollment: e, path: path, root: root, python: python, args: append([]string(nil), args...), location: l, factory: factory, children: map[*managedChild]bool{}, ctx: ownerCtx, cancel: cancel}
	if o.factory == nil {
		o.factory = exec.Command
	}
	o.record = managedRecord{1, e.Environment, e.ID, e.Revision, role, managedID(), managedID(), l.Socket, hex.EncodeToString(secret[:])}
	// O_EXCL provides owner exclusion; no PID/age based reclaim.
	f, err := os.OpenFile(l.Record, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		cancel()
		return nil, errManaged
	}
	f.Close()
	if managedWrite(l.Record, o.record) != nil {
		cancel()
		return nil, errManaged
	}
	for _, dir := range []string{"requests", "activations"} {
		if managedDir(filepath.Join(l.Directory, dir)) != nil {
			cancel()
			return nil, errManaged
		}
	}
	listener, err := net.Listen("unix", l.Socket)
	if err != nil {
		cancel()
		return nil, errManaged
	}
	if os.Chmod(l.Socket, 0600) != nil {
		listener.Close()
		cancel()
		return nil, errManaged
	}
	o.listener = listener
	go o.serve()
	go func() { <-ownerCtx.Done(); o.close() }()
	return o, nil
}

func (o *managedOwner) close() {
	o.mu.Lock()
	if o.closed {
		o.mu.Unlock()
		return
	}
	o.closed = true
	o.cancel()
	o.listener.Close()
	children := make([]*managedChild, 0, len(o.children))
	for c := range o.children {
		children = append(children, c)
	}
	o.mu.Unlock()
	for _, c := range children {
		o.stopChild(c)
	}
	// Retain the owner record and uncertain journals for explicit observation.
}
func (o *managedOwner) stopChild(c *managedChild) error {
	// The retained process handle/group is ours; never discover/adopt by PID.
	c.stopOnce.Do(func() {
		// Signal the retained group before closing the lifetime FD; otherwise a
		// cooperative leader can exit before its descendants receive the signal.
		if err := syscall.Kill(-c.cmd.Process.Pid, syscall.SIGKILL); err != nil && err != syscall.ESRCH {
			c.stopErr = errManaged
		}
		c.fd.Close()
	})
	if c.stopErr != nil {
		return errManaged
	}
	select {
	case <-c.done:
		return nil
	case <-time.After(5 * time.Second):
		return errManaged
	}
}
func (o *managedOwner) spawn(purpose, runtimeID, profile string, args []string, input []byte) (*managedChild, error) {
	return o.spawnWithOutput(purpose, runtimeID, profile, args, input, io.Discard)
}
func (o *managedOwner) spawnWithOutput(purpose, runtimeID, profile string, args []string, input []byte, output io.Writer) (*managedChild, error) {
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.closed || o.ctx.Err() != nil {
		return nil, errManaged
	}
	fds, err := syscall.Socketpair(syscall.AF_UNIX, syscall.SOCK_STREAM, 0)
	if err != nil {
		return nil, errManaged
	}
	syscall.CloseOnExec(fds[0])
	syscall.CloseOnExec(fds[1])
	parent, child := os.NewFile(uintptr(fds[0]), "managed-parent"), os.NewFile(uintptr(fds[1]), "managed-child")
	connection, err := net.FileConn(parent)
	parent.Close()
	if err != nil {
		child.Close()
		return nil, errManaged
	}
	cmd := o.factory(o.python, args...)
	cmd.Dir = o.root
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	cmd.Env = make([]string, 0, len(os.Environ())+2)
	for _, e := range os.Environ() {
		if !strings.HasPrefix(e, "SHIROKUMA_MANAGED_") && !strings.HasPrefix(e, "SHIROKUMA_RUNTIME_PROFILE=") {
			cmd.Env = append(cmd.Env, e)
		}
	}
	cmd.Env = append(cmd.Env, "SHIROKUMA_MANAGED_FD=3", "SHIROKUMA_RUNTIME_PROFILE="+profile)
	cmd.ExtraFiles = []*os.File{child}
	cmd.Stdin = bytes.NewReader(input)
	// stdout is read only by fixed resolver/authorizer, not exposed to clients.
	cmd.Stdout = output
	cmd.Stderr = io.Discard
	if cmd.Start() != nil {
		connection.Close()
		child.Close()
		return nil, errManaged
	}
	child.Close()
	c := &managedChild{cmd: cmd, fd: connection, done: make(chan struct{}), fenced: make(chan struct{})}
	o.children[c] = true
	go func() {
		c.err = cmd.Wait()
		close(c.done)
		o.mu.Lock()
		delete(o.children, c)
		o.mu.Unlock()
	}()
	record := o.record
	record.Runtime = runtimeID
	if purpose == "http" {
		o.http = c
		o.record.Runtime = runtimeID
		record = o.record
		if managedWrite(o.location.Record, record) != nil {
			connection.Close()
			return c, errManaged
		}
	}
	hello := managedHello{record, purpose, o.path, profile}
	if connection.SetDeadline(time.Now().Add(10*time.Second)) != nil || json.NewEncoder(connection).Encode(hello) != nil {
		return c, errManaged
	}
	return c, nil
}
func (o *managedOwner) handshake(c *managedChild, purpose string, runtimeID string) error {
	if c.fd.SetReadDeadline(time.Now().Add(10*time.Second)) != nil {
		return errManaged
	}
	line, err := bufio.NewReader(io.LimitReader(c.fd, 1024*1024)).ReadBytes('\n')
	if err != nil || !managedExact(line, "version", "state", "owner_role", "owner_instance_id", "runtime_instance_id", "runtime_attestation") {
		// worker attestation is explicitly null, so check its exact keys separately.
		var m map[string]json.RawMessage
		if purpose != "worker" || err != nil || json.Unmarshal(line, &m) != nil || len(m) != 6 || string(m["runtime_attestation"]) != "null" {
			return errManaged
		}
	}
	if managedDecode(line, &c.ready) != nil || c.ready.Version != 1 || c.ready.State != "READY" || c.ready.Role != o.record.Role || c.ready.Owner != o.record.Owner || c.ready.Runtime != runtimeID {
		return errManaged
	}
	if purpose == "http" && !managedAttestation(c.ready.Attestation) {
		return errManaged
	}
	if purpose == "worker" && string(c.ready.Attestation) != "null" {
		return errManaged
	}
	if c.fd.SetDeadline(time.Time{}) != nil {
		return errManaged
	}
	go func() {
		io.Copy(io.Discard, c.fd)
		close(c.fenced)
		// A one-shot worker normally closes its FD while exiting. Do not turn
		// its successful output into a signal exit while Wait is completing.
		if purpose == "http" {
			o.stopChild(c)
		} else {
			c.fd.Close()
		}
	}()
	return nil
}
func managedAttestation(raw json.RawMessage) bool {
	if !managedExact(raw, "version", "generation_id", "databases", "profile_ref") {
		return false
	}
	var v struct {
		Version    int                        `json:"version"`
		Generation string                     `json:"generation_id"`
		Databases  map[string]json.RawMessage `json:"databases"`
		Profile    string                     `json:"profile_ref"`
	}
	return managedDecode(raw, &v) == nil && v.Version == 1 && v.Generation != "" && v.Databases != nil && managedDigest.MatchString(v.Profile)
}
func (o *managedOwner) startHTTP(runtimeID, profile string) (*managedChild, error) {
	c, err := o.spawn("http", runtimeID, profile, o.args, nil)
	if err == nil {
		err = o.handshake(c, "http", runtimeID)
	}
	if err != nil && c != nil {
		o.stopChild(c)
	}
	return c, err
}
func runManagedOwner(ctx context.Context, root, python string, args []string, path, role string, started func(context.Context) error) error {
	o, err := newManagedOwner(ctx, root, python, args, path, role, nil)
	if err != nil {
		return errManaged
	}
	defer o.close()
	if _, err = o.startHTTP(o.record.Runtime, o.enrollment.Profile); err != nil {
		return errManaged
	}
	if started != nil {
		if started(o.ctx) != nil {
			return errManaged
		}
	}
	// HTTP exit does not end the owner or an independently running operator.
	<-o.ctx.Done()
	return nil
}
func (o *managedOwner) serve() {
	for {
		conn, err := o.listener.Accept()
		if err != nil {
			return
		}
		go func() {
			defer conn.Close()
			conn.SetDeadline(time.Now().Add(30 * time.Second))
			line, e := bufio.NewReader(io.LimitReader(conn, 65536)).ReadBytes('\n')
			if e != nil {
				return
			}
			response := o.handle(line)
			json.NewEncoder(conn).Encode(response)
		}()
	}
}
func (o *managedOwner) response(r managedRequest, state string) managedResponse {
	o.mu.Lock()
	defer o.mu.Unlock()
	return managedResponse{Version: 1, Action: r.Action, Request: r.Request, State: state, Role: o.record.Role, Owner: o.record.Owner, Runtime: o.record.Runtime, Activation: r.Activation}
}
func (o *managedOwner) handle(line []byte) managedResponse {
	var r managedRequest
	bad := func() managedResponse { return o.response(managedRequest{}, "REJECTED") }
	if managedDecode(line, &r) != nil || r.Version != 1 || !managedUUID.MatchString(r.Request) || r.Role != o.record.Role || r.Owner != o.record.Owner || subtle.ConstantTimeCompare([]byte(r.Capability), []byte(o.record.Capability)) != 1 {
		return bad()
	}
	keys := []string{"version", "action", "request_id", "owner_role", "owner_instance_id", "capability"}
	switch r.Action {
	case "GET_ACTIVATION", "ACTIVATE_CANDIDATE":
		if !managedDigest.MatchString(r.Activation) {
			return bad()
		}
		keys = append(keys, "activation_id")
	case "GET_REQUEST", "DISPATCH_REQUEST", "OBSERVE_REQUEST":
		if r.Activation != "" {
			return bad()
		}
	default:
		return bad()
	}
	if !managedExact(line, keys...) {
		return bad()
	}
	o.mu.Lock()
	closed := o.closed
	o.mu.Unlock()
	if closed {
		return o.response(r, "UNAVAILABLE")
	}
	switch r.Action {
	case "GET_REQUEST":
		var receipt managedResponse
		if managedRead(o.requestPath(r), &receipt) != nil {
			if _, err := os.Stat(o.requestPath(r)); os.IsNotExist(err) {
				return o.response(r, "NOT_FOUND")
			}
			return o.response(r, "UNKNOWN")
		}
		if receipt.State == "INTENT" {
			receipt.State = "UNKNOWN"
		}
		receipt.Action = r.Action
		return receipt
	case "GET_ACTIVATION":
		var a managedActivation
		if managedRead(o.activationPath(r), &a) != nil {
			if _, err := os.Stat(o.activationPath(r)); os.IsNotExist(err) {
				return o.response(r, "NOT_FOUND")
			}
			return o.response(r, "UNKNOWN")
		}
		response := o.response(r, a.State)
		response.Previous = a.Descriptor.Old
		return response
	case "DISPATCH_REQUEST", "OBSERVE_REQUEST":
		return o.dispatch(r)
	case "ACTIVATE_CANDIDATE":
		return o.activate(r)
	}
	return bad()
}
func (o *managedOwner) requestPath(r managedRequest) string {
	return filepath.Join(o.location.Directory, "requests", r.Request+".json")
}
func (o *managedOwner) activationPath(r managedRequest) string {
	return filepath.Join(o.location.Directory, "activations", r.Activation+".json")
}
func (o *managedOwner) dispatch(r managedRequest) managedResponse {
	// Serialize receipt creation. A retained receipt, including INTENT/UNKNOWN,
	// permanently prevents an automatic duplicate of that fixed action.
	o.activationMu.Lock()
	defer o.activationMu.Unlock()
	path := o.requestPath(r)
	if r.Action == "OBSERVE_REQUEST" {
		path = strings.TrimSuffix(path, ".json") + "-observe.json"
	}
	var prior managedResponse
	if _, err := os.Stat(path); err == nil {
		if managedRead(path, &prior) != nil {
			return o.response(r, "UNKNOWN")
		}
		prior.Action = r.Action
		if prior.State == "INTENT" {
			prior.State = "UNKNOWN"
		}
		return prior
	} else if !os.IsNotExist(err) {
		return o.response(r, "UNKNOWN")
	}
	response := o.response(r, "INTENT")
	if managedWrite(path, response) != nil {
		return o.response(r, "UNKNOWN")
	}
	sub := "dispatch"
	if r.Action == "OBSERVE_REQUEST" {
		sub = "observe"
	}
	o.mu.Lock()
	snapshot := o.record.Runtime
	o.mu.Unlock()
	c, err := o.spawn("worker", snapshot, o.enrollment.Profile, []string{filepath.Join(o.root, "tools", "local_experiment", "database_creation_worker.py"), sub, r.Request}, nil)
	if err == nil {
		err = o.handshake(c, "worker", snapshot)
	}
	response.State = "DISPATCHED"
	if err != nil {
		response.State = "UNKNOWN"
		if c != nil {
			o.stopChild(c)
		}
	}
	if managedWrite(path, response) != nil {
		response.State = "UNKNOWN"
	}
	return response
}

// The bounded resolver and authorizer share the same inherited-FD identity.
// They cannot introduce executable paths or command-line arguments.
func (o *managedOwner) workerCall(sub string, input any) ([]byte, error) {
	b, err := json.Marshal(input)
	if err != nil {
		return nil, errManaged
	}
	o.mu.Lock()
	snapshot := o.record.Runtime
	o.mu.Unlock()
	return o.spawnOutput(sub, snapshot, append(b, '\n'))
}

type managedLimitedBuffer struct {
	bytes.Buffer
	overflow bool
}

func (b *managedLimitedBuffer) Write(p []byte) (int, error) {
	if b.Len()+len(p) > 1024*1024 {
		b.overflow = true
		return 0, errManaged
	}
	return b.Buffer.Write(p)
}

func (o *managedOwner) spawnOutput(sub, snapshot string, input []byte) ([]byte, error) {
	// A dedicated constructor argument avoids a global/public execution hook.
	var out managedLimitedBuffer
	c, err := o.spawnCommandOutput(snapshot, sub, input, &out)
	if err == nil {
		err = o.handshake(c, "worker", snapshot)
	}
	if err != nil {
		if c != nil {
			o.stopChild(c)
		}
		return nil, errManaged
	}
	select {
	case <-c.done:
		if c.err != nil || out.overflow {
			return nil, errManaged
		}
		return out.Bytes(), nil
	case <-o.ctx.Done():
		o.stopChild(c)
		return nil, errManaged
	case <-time.After(30 * time.Second):
		o.stopChild(c)
		return nil, errManaged
	}
}
func (o *managedOwner) spawnCommandOutput(snapshot, sub string, input []byte, out io.Writer) (*managedChild, error) {
	return o.spawnWithOutput("worker", snapshot, o.enrollment.Profile, []string{filepath.Join(o.root, "tools", "local_experiment", "database_creation_worker.py"), sub}, input, out)
}

func managedSameJSON(a, b json.RawMessage) bool {
	var x, y any
	return json.Unmarshal(a, &x) == nil && json.Unmarshal(b, &y) == nil && reflect.DeepEqual(x, y)
}
func managedSameDescriptor(a, b managedDescriptor) bool {
	x, _ := json.Marshal(a)
	y, _ := json.Marshal(b)
	return managedSameJSON(x, y)
}
func managedSameActivation(a, b managedDescriptor) bool {
	// Recovery carries fresh authorization, while the original journal keeps
	// its request linkage and every previously recorded leaf intent and ACK.
	a.Request = b.Request
	return managedSameDescriptor(a, b)
}
func managedCheckBinding(d managedDescriptor) bool {
	if d.Profile != d.Binding.Path || !managedDigest.MatchString(d.Binding.Profile) || len(d.Binding.Files) != 5 {
		return false
	}
	hash := func(path, digest string) bool {
		if managedPrivate(path, false) != nil || !managedDigest.MatchString(digest) {
			return false
		}
		b, e := os.ReadFile(path)
		if e != nil {
			return false
		}
		sum := sha256.Sum256(b)
		return hex.EncodeToString(sum[:]) == digest
	}
	if !hash(d.Profile, d.Binding.Profile) {
		return false
	}
	var profile map[string]json.RawMessage
	b, e := os.ReadFile(d.Profile)
	if e != nil || json.Unmarshal(b, &profile) != nil {
		return false
	}
	for _, k := range []string{"auth_file", "trino_auth_file", "trino_ca_file", "catalog_auth_file", "client_auth_file"} {
		binding, ok := d.Binding.Files[k]
		var path string
		if !ok || json.Unmarshal(profile[k], &path) != nil || path != binding.Path || !hash(binding.Path, binding.Digest) {
			return false
		}
	}
	return true
}

// Load only the fixed private plan. Its shape is evidence of which activation
// this handler has not sent, not authority to execute it. In particular, account
// revocation may have changed credential bytes since the plan was sealed.
func (o *managedOwner) activationDescriptor(r managedRequest) (managedDescriptor, error) {
	var d managedDescriptor
	fixed := filepath.Join(o.enrollment.StateDir, "activation-plans", r.Request, r.Activation+".json")
	if managedPrivate(fixed, false) != nil {
		return d, errManaged
	}
	b, err := os.ReadFile(fixed)
	if err != nil || len(b) > 1024*1024 || !managedExact(b, "version", "request_id", "activation_id", "owner_role", "owner_instance_id", "old_runtime_instance_id", "candidate_runtime_instance_id", "profile_path", "profile_binding", "runtime_attestation", "original_operation_id", "original_plan_id", "candidate_id") || managedDecode(b, &d) != nil {
		return d, errManaged
	}
	canonical := func(path string) bool { return filepath.IsAbs(path) && filepath.Clean(path) == path }
	if d.Version != 1 || d.Request != r.Request || d.Activation != r.Activation || d.Role != o.record.Role || d.Owner != o.record.Owner || !managedUUID.MatchString(d.Old) || !managedUUID.MatchString(d.Candidate) || d.Old == d.Candidate || d.Operation == "" || d.Plan == "" || d.CandidateID == "" || !managedAttestation(d.Attestation) || !canonical(d.Profile) || d.Profile != d.Binding.Path || !managedDigest.MatchString(d.Binding.Profile) || len(d.Binding.Files) != 5 {
		return d, errManaged
	}
	for _, key := range []string{"auth_file", "trino_auth_file", "trino_ca_file", "catalog_auth_file", "client_auth_file"} {
		binding, ok := d.Binding.Files[key]
		if !ok || !canonical(binding.Path) || !managedDigest.MatchString(binding.Digest) {
			return d, errManaged
		}
	}
	return d, nil
}

func (o *managedOwner) activationInput(r managedRequest) map[string]any {
	return map[string]any{"version": 1, "request_id": r.Request, "activation_id": r.Activation, "owner_role": o.record.Role, "owner_instance_id": o.record.Owner}
}
func (o *managedOwner) authorize(r managedRequest, d managedDescriptor, leaf, intent string) error {
	input := o.activationInput(r)
	input["leaf"] = leaf
	input["intent_id"] = intent
	b, err := o.workerCall("authorize-leaf", input)
	var a managedAuthorization
	if err != nil || !managedExact(b, "version", "state", "request_id", "activation_id", "owner_role", "owner_instance_id", "leaf", "intent_id", "candidate_runtime_instance_id", "profile_binding") || managedDecode(b, &a) != nil || a.Version != 1 || a.State != "AUTHORIZED" || a.Request != r.Request || a.Activation != r.Activation || a.Role != o.record.Role || a.Owner != o.record.Owner || a.Leaf != leaf || a.Intent != intent || a.Candidate != d.Candidate || !reflect.DeepEqual(a.Binding, d.Binding) || !managedCheckBinding(d) {
		return errManaged
	}
	return nil
}
func (o *managedOwner) activate(r managedRequest) managedResponse {
	o.activationMu.Lock()
	defer o.activationMu.Unlock()
	unknown := func() managedResponse { return o.response(r, "UNKNOWN") }
	path := o.activationPath(r)
	var a managedActivation
	_, statErr := os.Stat(path)
	existing := statErr == nil
	if statErr != nil && !os.IsNotExist(statErr) {
		return unknown()
	}
	if existing && managedRead(path, &a) != nil {
		return unknown()
	}
	// No process observation can turn an unacknowledged INTENT into UNSENT.
	if existing {
		for _, leaf := range []managedLeaf{a.Stop, a.Start, a.Readiness} {
			if leaf.State == "INTENT" || (leaf.State != "UNSENT" && leaf.State != "ACK") {
				return unknown()
			}
		}
	}
	if !existing {
		d, err := o.activationDescriptor(r)
		if err != nil {
			return unknown()
		}
		o.mu.Lock()
		current := o.http
		known := current != nil && o.children[current]
		valid := !o.closed && known && o.record.Runtime == d.Old && current.ready.Version == 1 && current.ready.State == "READY" && current.ready.Role == o.record.Role && current.ready.Owner == o.record.Owner && current.ready.Runtime == d.Old && managedAttestation(current.ready.Attestation)
		o.mu.Unlock()
		if !valid {
			return unknown()
		}
		select {
		case <-current.done:
			return unknown()
		case <-current.fenced:
			return unknown()
		default:
		}
		// Positive zero-send evidence must precede even resolver denial. Never
		// recreate an existing journal or derive this evidence from a missing PID.
		a = managedActivation{Descriptor: d, Stop: managedLeaf{State: "UNSENT"}, Start: managedLeaf{State: "UNSENT"}, Readiness: managedLeaf{State: "UNSENT"}, State: "UNSENT"}
		if managedWrite(path, a) != nil {
			return unknown()
		}
	}
	resolved, err := o.workerCall("resolve-activation", o.activationInput(r))
	var d managedDescriptor
	if err != nil || !managedExact(resolved, "version", "request_id", "activation_id", "owner_role", "owner_instance_id", "old_runtime_instance_id", "candidate_runtime_instance_id", "profile_path", "profile_binding", "runtime_attestation", "original_operation_id", "original_plan_id", "candidate_id") || managedDecode(resolved, &d) != nil {
		return unknown()
	}
	sealed, err := o.activationDescriptor(r)
	if err != nil || !managedSameDescriptor(d, sealed) || !managedCheckBinding(d) {
		return unknown()
	}
	if (existing && !managedSameActivation(d, a.Descriptor)) || (!existing && !managedSameDescriptor(d, a.Descriptor)) {
		return unknown()
	}
	o.mu.Lock()
	current := o.http
	runtimeID := o.record.Runtime
	closed := o.closed
	o.mu.Unlock()
	if closed || current == nil {
		return unknown()
	}
	if existing && a.State == "ACK" {
		if runtimeID != d.Candidate || !managedSameJSON(current.ready.Attestation, d.Attestation) {
			return unknown()
		}
		select {
		case <-current.done:
			return unknown()
		case <-current.fenced:
			return unknown()
		default:
		}
		response := o.response(r, "ACK")
		response.Previous = d.Old
		return response
	}
	// A known successor is usable only with this same activation's durable ACK.
	if a.Start.State == "ACK" {
		if runtimeID != d.Candidate {
			return unknown()
		}
	} else if runtimeID != d.Old {
		return unknown()
	}
	// Persist each local INTENT first, then require the current transactional
	// Python authorization immediately before the fixed effect. If authorization
	// fails while this handler is alive, it positively knows it sent no Go effect.
	// A crash before that UNSENT record remains an uncertain INTENT instead.
	begin := func(leaf *managedLeaf, name string) bool {
		if leaf.State != "UNSENT" {
			return false
		}
		leaf.State = "INTENT"
		leaf.Intent = managedID()
		a.State = "UNKNOWN"
		if managedWrite(path, a) != nil {
			return false
		}
		if o.authorize(r, d, name, leaf.Intent) != nil {
			leaf.State = "UNSENT"
			a.State = "REJECTED"
			// Failure to save this positive observation leaves the durable INTENT.
			managedWrite(path, a)
			return false
		}
		return true
	}
	ack := func(leaf *managedLeaf) bool { leaf.State = "ACK"; return managedWrite(path, a) == nil }
	if a.Stop.State == "UNSENT" {
		// Require a live, handshaken old generation before authorizing its stop.
		select {
		case <-current.done:
			return unknown()
		case <-current.fenced:
			return unknown()
		default:
		}
		if !begin(&a.Stop, "stop") {
			return unknown()
		}
		if o.stopChild(current) != nil || !ack(&a.Stop) {
			return unknown()
		}
	}
	if a.Start.State == "UNSENT" {
		if a.Stop.State != "ACK" || !begin(&a.Start, "start") {
			return unknown()
		}
		// Start ACK records actual successful spawn, independently of readiness.
		current, err = o.spawn("http", d.Candidate, d.Profile, o.args, nil)
		if err != nil || !ack(&a.Start) {
			return unknown()
		}
	}
	if a.Readiness.State == "UNSENT" {
		if !begin(&a.Readiness, "readiness") {
			return unknown()
		}
		if o.handshake(current, "http", d.Candidate) != nil || !managedSameJSON(current.ready.Attestation, d.Attestation) {
			return unknown()
		}
		select {
		case <-current.done:
			return unknown()
		default:
		}
		if !ack(&a.Readiness) {
			return unknown()
		}
	}
	a.State = "ACK"
	if managedWrite(path, a) != nil {
		return unknown()
	}
	response := o.response(r, "ACK")
	response.Previous = d.Old
	return response
}
