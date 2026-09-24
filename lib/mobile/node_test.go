package mobile

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync"
	"testing"
)

func fresh(t *testing.T) *Node {
	t.Helper()
	n, e := Open(Config{Root: t.TempDir()})
	if e != nil {
		t.Fatal(e)
	}
	return n
}
func upload(t *testing.T, n *Node, owner, name string, data []byte) File {
	t.Helper()
	f, e := n.put(owner, name, "Chat/v1", data)
	if e != nil {
		t.Fatal(e)
	}
	return f
}
func TestRecoveryIntegrityRestart(t *testing.T) {
	n := fresh(t)
	content := bytes.Repeat([]byte("private chat content "), 101)
	f := upload(t, n, "alice", "chat-event-a.json", content)
	for i := 0; i < 4; i++ {
		if e := os.Remove(filepath.Join(n.root, "objects", f.ID, fmt.Sprint(i))); e != nil {
			t.Fatal(e)
		}
	}
	b, e := n.read(f)
	if e != nil || !bytes.Equal(b, content) {
		t.Fatal("four missing shards were not recovered", e)
	}
	recovered, e := Open(Config{Root: n.root})
	if e != nil {
		t.Fatal(e)
	}
	if n.id != recovered.id || n.capability != recovered.capability {
		t.Fatal("node identity changed")
	}
	b, e = recovered.read(f)
	if e != nil || !bytes.Equal(b, content) {
		t.Fatal("restart lost data", e)
	}
	os.WriteFile(filepath.Join(n.root, "objects", f.ID, "4"), []byte("corrupt"), 0600)
	if _, e = recovered.read(f); e == nil {
		t.Fatal("corruption beyond parity accepted")
	}
}
func TestConcurrentIdempotencyAndQuota(t *testing.T) {
	n := fresh(t)
	var wg sync.WaitGroup
	ids := make(chan string, 50)
	for i := 0; i < 50; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			f, e := n.put("alice", "message.json", "Chat/v1", []byte("same object"))
			if e != nil {
				t.Error(e)
				return
			}
			ids <- f.ID
		}()
	}
	wg.Wait()
	close(ids)
	unique := map[string]bool{}
	for id := range ids {
		unique[id] = true
	}
	if len(unique) != 1 || len(n.files) != 1 {
		t.Fatalf("duplicate objects: %d", len(unique))
	}
	n.quota = n.usage() + 200
	if _, e := n.put("bob", "new.json", "Chat/v1", bytes.Repeat([]byte("x"), 400)); e == nil {
		t.Fatal("quota exceeded")
	}
}
func TestHTTPIsolationAndEncryptedDisk(t *testing.T) {
	n := fresh(t)
	server := httptest.NewServer(n)
	defer server.Close()
	f := upload(t, n, "alice", "event.json", []byte("Alice secret"))
	for _, tc := range []struct {
		owner, cap string
		status     int
	}{{"alice", "", 401}, {"bob", n.capability, 404}, {"alice", n.capability, 200}} {
		r, _ := http.NewRequest("GET", server.URL+"/api/files/"+f.ID+"/download", nil)
		r.Header.Set("X-CFS-Local-Key", tc.cap)
		r.Header.Set("X-CFS-Token", tc.owner)
		resp, e := http.DefaultClient.Do(r)
		if e != nil {
			t.Fatal(e)
		}
		body, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		if resp.StatusCode != tc.status {
			t.Fatalf("status %d expected %d: %s", resp.StatusCode, tc.status, body)
		}
	}
	for _, c := range f.Chunks {
		raw, e := os.ReadFile(filepath.Join(n.root, "objects", f.ID, fmt.Sprint(c.Num)))
		if e != nil || bytes.Contains(raw, []byte("Alice secret")) {
			t.Fatal("unencrypted shard")
		}
	}
	body := new(bytes.Buffer)
	form := multipart.NewWriter(body)
	p, _ := form.CreateFormFile("file", "event2.json")
	p.Write([]byte("peer-compatible upload"))
	form.WriteField("folder", "Chat/v1")
	form.Close()
	r, _ := http.NewRequest("POST", server.URL+"/api/files/upload", body)
	r.Header.Set("Content-Type", form.FormDataContentType())
	r.Header.Set("X-CFS-Local-Key", n.capability)
	r.Header.Set("X-CFS-Token", "bob")
	resp, e := http.DefaultClient.Do(r)
	if e != nil {
		t.Fatal(e)
	}
	defer resp.Body.Close()
	var result File
	json.NewDecoder(resp.Body).Decode(&result)
	if resp.StatusCode != 200 || result.ID == "" {
		t.Fatal("CFS upload failed")
	}
	r, _ = http.NewRequest("GET", server.URL+"/api/files/tree?scope=network", nil)
	r.Header.Set("X-CFS-Local-Key", n.capability)
	r.Header.Set("X-CFS-Token", "bob")
	resp, e = http.DefaultClient.Do(r)
	if e != nil {
		t.Fatal(e)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	if bytes.Contains(raw, []byte(f.ID)) {
		t.Fatal("Alice namespace leaked to Bob")
	}
}
func TestPreserveInvalidKeyAndRejectUnsafeBinding(t *testing.T) {
	n := fresh(t)
	if e := n.Start("0.0.0.0:0"); e == nil {
		t.Fatal("bound external interface")
	}
	os.WriteFile(filepath.Join(n.root, "encryption.key"), []byte("broken"), 0600)
	if _, e := Open(Config{Root: n.root}); e == nil {
		t.Fatal("silently replaced an existing key")
	}
	b, _ := os.ReadFile(filepath.Join(n.root, "encryption.key"))
	if string(b) != "broken" {
		t.Fatal("key modified")
	}
}
func TestWrongShardAADFails(t *testing.T) {
	n := fresh(t)
	a := upload(t, n, "alice", "a", bytes.Repeat([]byte("a"), 200))
	b := upload(t, n, "alice", "b", bytes.Repeat([]byte("b"), 200))
	for i := 0; i < 5; i++ {
		raw, _ := os.ReadFile(filepath.Join(n.root, "objects", a.ID, fmt.Sprint(i)))
		os.WriteFile(filepath.Join(n.root, "objects", b.ID, fmt.Sprint(i)), raw, 0600)
		b.Chunks[i].SHA256 = hash(raw)
	}
	if _, e := n.read(b); e == nil {
		t.Fatal("cross-file shard substitution accepted")
	}
}

func TestMissingKeysNeverReinitializeExistingObjects(t *testing.T) {
	for _, name := range []string{"encryption.key", "node_id"} {
		t.Run(name, func(t *testing.T) {
			n := fresh(t)
			payload := []byte("keep the original encrypted history")
			f := upload(t, n, "alice", "chat-event-preserve.json", payload)
			path := filepath.Join(n.root, name)
			original, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			if err = os.Remove(path); err != nil {
				t.Fatal(err)
			}
			if _, err = Open(Config{Root: n.root}); err == nil {
				t.Fatal("missing node key or UUID was silently replaced")
			}
			if _, err = os.Stat(path); !os.IsNotExist(err) {
				t.Fatal("a replacement was written")
			}
			if err = os.WriteFile(path, original, 0600); err != nil {
				t.Fatal(err)
			}
			reopened, err := Open(Config{Root: n.root})
			if err != nil {
				t.Fatal(err)
			}
			value, err := reopened.read(f)
			if err != nil || !bytes.Equal(value, payload) {
				t.Fatal("original backup did not restore history", err)
			}
		})
	}
}
