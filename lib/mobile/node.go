// Package mobile is the embeddable CollectiveFS node used on Android. It uses
// the repository's Reed-Solomon codec and the same file HTTP contract as api/.
package mobile

import (
	"bytes"
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/klauspost/reedsolomon"
)

const MaxObject = 12 * 1024 * 1024
const DataShards, ParityShards = 8, 4

var Version = "mobile-1"
var uuidPattern = regexp.MustCompile(`^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$`)

type Config struct {
	Root   string `json:"root"`
	Listen string `json:"listen"`
	Quota  int64  `json:"quotaBytes"`
}
type Chunk struct {
	Num       int    `json:"num"`
	ID        string `json:"id"`
	SHA256    string `json:"sha256"`
	Size      int    `json:"size"`
	Encrypted bool   `json:"encrypted"`
}
type File struct {
	ID        string  `json:"id"`
	Name      string  `json:"name"`
	Folder    string  `json:"folder"`
	Token     string  `json:"token"`
	Size      int     `json:"size"`
	CreatedAt string  `json:"created_at"`
	Status    string  `json:"status"`
	SHA256    string  `json:"sha256"`
	Data      int     `json:"data_shards"`
	Parity    int     `json:"parity_shards"`
	Chunks    []Chunk `json:"chunk_list"`
}
type Node struct {
	mu                        sync.RWMutex
	root, id, capability, url string
	quota                     int64
	aead                      cipher.AEAD
	files                     map[string]File
	server                    *http.Server
	listener                  net.Listener
}

func randomHex(n int) (string, error) {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return hex.EncodeToString(b), nil
}
func uuid() (string, error) {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	b[6] = (b[6] & 15) | 64
	b[8] = (b[8] & 63) | 128
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[:4], b[4:6], b[6:8], b[8:10], b[10:]), nil
}
func hash(b []byte) string { h := sha256.Sum256(b); return hex.EncodeToString(h[:]) }
func atomic(path string, b []byte) error {
	f, err := os.CreateTemp(filepath.Dir(path), ".cfs-write-")
	if err != nil {
		return err
	}
	name := f.Name()
	defer os.Remove(name)
	if err = f.Chmod(0600); err == nil {
		_, err = f.Write(b)
	}
	if err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	if err = os.Rename(name, path); err != nil {
		return err
	}
	d, err := os.Open(filepath.Dir(path))
	if err != nil {
		return err
	}
	defer d.Close()
	return d.Sync()
}
func secret(path string, n int) ([]byte, error) {
	b, err := os.ReadFile(path)
	if err == nil {
		if len(b) != n {
			return nil, errors.New("invalid existing node key; refusing to replace it")
		}
		return b, nil
	}
	if !os.IsNotExist(err) {
		return nil, err
	}
	b = make([]byte, n)
	if _, err = rand.Read(b); err != nil {
		return nil, err
	}
	return b, atomic(path, b)
}
func Open(config Config) (*Node, error) {
	if config.Root == "" {
		return nil, errors.New("private storage directory required")
	}
	if config.Quota == 0 {
		config.Quota = 512 * 1024 * 1024
	}
	if config.Quota < 1024 {
		return nil, errors.New("invalid quota")
	}
	if err := os.MkdirAll(filepath.Join(config.Root, "objects"), 0700); err != nil {
		return nil, err
	}
	existing, err := os.ReadDir(filepath.Join(config.Root, "objects"))
	if err != nil {
		return nil, err
	}
	if len(existing) > 0 {
		for _, required := range []string{"encryption.key", "node_id"} {
			if _, err := os.Stat(filepath.Join(config.Root, required)); err != nil {
				return nil, fmt.Errorf("existing encrypted storage is missing %s; restore the original node backup", required)
			}
		}
	}
	key, err := secret(filepath.Join(config.Root, "encryption.key"), 32)
	if err != nil {
		return nil, err
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	cap, err := secret(filepath.Join(config.Root, "local.capability"), 32)
	if err != nil {
		return nil, err
	}
	idPath := filepath.Join(config.Root, "node_id")
	idBytes, err := os.ReadFile(idPath)
	if os.IsNotExist(err) {
		id, e := uuid()
		if e != nil {
			return nil, e
		}
		idBytes = []byte(id)
		err = atomic(idPath, idBytes)
	}
	if err != nil {
		return nil, err
	}
	if !uuidPattern.Match(idBytes) {
		return nil, errors.New("invalid existing node UUID")
	}
	n := &Node{root: config.Root, id: string(idBytes), capability: hex.EncodeToString(cap), quota: config.Quota, aead: aead, files: map[string]File{}}
	entries, err := os.ReadDir(filepath.Join(config.Root, "objects"))
	if err != nil {
		return nil, err
	}
	for _, entry := range entries {
		if !entry.IsDir() || !uuidPattern.MatchString(entry.Name()) {
			continue
		}
		raw, err := os.ReadFile(filepath.Join(config.Root, "objects", entry.Name(), "metadata.json"))
		if os.IsNotExist(err) {
			continue
		}
		if err != nil {
			return nil, err
		}
		var f File
		if json.Unmarshal(raw, &f) != nil || f.ID != entry.Name() || f.Size < 0 || f.Size > MaxObject || f.Data != DataShards || f.Parity != ParityShards || len(f.Chunks) != DataShards+ParityShards {
			return nil, errors.New("damaged node metadata; storage preserved")
		}
		n.files[f.ID] = f
	}
	return n, nil
}
func (n *Node) Start(address string) error {
	if address == "" {
		address = "127.0.0.1:0"
	}
	host, _, err := net.SplitHostPort(address)
	if err != nil || net.ParseIP(host) == nil || !net.ParseIP(host).IsLoopback() {
		return errors.New("mobile node must bind to a loopback address")
	}
	listener, err := net.Listen("tcp", address)
	if err != nil {
		return err
	}
	n.listener = listener
	n.url = "http://" + listener.Addr().String()
	n.server = &http.Server{Handler: n, ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 30 * time.Second, WriteTimeout: 30 * time.Second, IdleTimeout: 30 * time.Second, MaxHeaderBytes: 16 * 1024}
	go n.server.Serve(listener)
	return nil
}
func (n *Node) Close() error {
	if n.server == nil {
		return nil
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	err := n.server.Shutdown(ctx)
	if err != nil {
		_ = n.server.Close()
	}
	return err
}

// Descriptor is private IPC. The capability is never returned by HTTP stats.
func (n *Node) Descriptor() map[string]any {
	return map[string]any{"running": n.listener != nil, "url": n.url, "nodeId": n.id, "capability": n.capability, "version": Version}
}
func (n *Node) usage() int64 {
	var total int64
	filepath.WalkDir(filepath.Join(n.root, "objects"), func(_ string, d os.DirEntry, err error) error {
		if err == nil && !d.IsDir() {
			if s, e := d.Info(); e == nil {
				total += s.Size()
			}
		}
		return nil
	})
	return total
}
func (n *Node) put(token, name, folder string, data []byte) (File, error) {
	n.mu.Lock()
	defer n.mu.Unlock()
	if token == "" || len(token) > 256 || name == "" || len(name) > 200 || len(folder) > 200 || strings.ContainsAny(name, "/\\\x00") || strings.Contains(folder, "..") || strings.ContainsAny(folder, "\\\x00") {
		return File{}, errors.New("invalid object namespace or name")
	}
	if len(data) == 0 || len(data) > MaxObject {
		return File{}, errors.New("object must contain 1 byte to 12 MiB")
	}
	digest := hash(data)
	for _, f := range n.files {
		if f.Token == token && f.Name == name && f.Folder == folder && f.SHA256 == digest {
			return f, nil
		}
	}
	if n.usage()+int64(len(data))*2+8192 > n.quota {
		return File{}, errors.New("node storage quota reached")
	}
	id, err := uuid()
	if err != nil {
		return File{}, err
	}
	directory := filepath.Join(n.root, "objects", id)
	if err = os.Mkdir(directory, 0700); err != nil {
		return File{}, err
	}
	committed := false
	defer func() {
		if !committed {
			os.RemoveAll(directory)
		}
	}()
	enc, err := reedsolomon.New(DataShards, ParityShards)
	if err != nil {
		return File{}, err
	}
	shards, err := enc.Split(data)
	if err != nil {
		return File{}, err
	}
	if err = enc.Encode(shards); err != nil {
		return File{}, err
	}
	f := File{ID: id, Token: token, Name: name, Folder: folder, Size: len(data), CreatedAt: time.Now().UTC().Format(time.RFC3339Nano), Status: "stored", SHA256: digest, Data: DataShards, Parity: ParityShards}
	for i, part := range shards {
		nonce := make([]byte, n.aead.NonceSize())
		if _, err = rand.Read(nonce); err != nil {
			return File{}, err
		}
		payload := n.aead.Seal(nonce, nonce, part, []byte(fmt.Sprintf("%s:%d", id, i)))
		if err = atomic(filepath.Join(directory, fmt.Sprint(i)), payload); err != nil {
			return File{}, err
		}
		chunkID, err := uuid()
		if err != nil {
			return File{}, err
		}
		f.Chunks = append(f.Chunks, Chunk{Num: i, ID: chunkID, SHA256: hash(payload), Size: len(payload), Encrypted: true})
	}
	raw, err := json.Marshal(f)
	if err != nil {
		return File{}, err
	}
	if err = atomic(filepath.Join(directory, "metadata.json"), raw); err != nil {
		return File{}, err
	}
	n.files[id] = f
	committed = true
	return f, nil
}
func (n *Node) read(f File) ([]byte, error) {
	shards := make([][]byte, DataShards+ParityShards)
	for i, c := range f.Chunks {
		if c.Num != i {
			return nil, errors.New("invalid shard order")
		}
		b, err := os.ReadFile(filepath.Join(n.root, "objects", f.ID, fmt.Sprint(i)))
		if err != nil || hash(b) != c.SHA256 || len(b) < n.aead.NonceSize() {
			continue
		}
		nonce := b[:n.aead.NonceSize()]
		plain, err := n.aead.Open(nil, nonce, b[n.aead.NonceSize():], []byte(fmt.Sprintf("%s:%d", f.ID, i)))
		if err == nil {
			shards[i] = plain
		}
	}
	enc, err := reedsolomon.New(DataShards, ParityShards)
	if err != nil {
		return nil, err
	}
	if err = enc.Reconstruct(shards); err != nil {
		return nil, err
	}
	var out bytes.Buffer
	if err = enc.Join(&out, shards, f.Size); err != nil {
		return nil, err
	}
	if hash(out.Bytes()) != f.SHA256 {
		return nil, errors.New("object SHA-256 mismatch")
	}
	return out.Bytes(), nil
}
func jsonResponse(w http.ResponseWriter, code int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(value)
}
func (n *Node) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Headers", "Content-Type, X-CFS-Token, X-CFS-Local-Key")
	w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	if r.Method == "OPTIONS" {
		w.WriteHeader(204)
		return
	}
	// Every request, including reads, needs the per-install capability. A web
	// page outside the native app cannot enumerate or fill the phone's node.
	if subtle.ConstantTimeCompare([]byte(r.Header.Get("X-CFS-Local-Key")), []byte(n.capability)) != 1 {
		jsonResponse(w, 401, map[string]string{"error": "local node capability required"})
		return
	}
	token := r.Header.Get("X-CFS-Token")
	route := r.URL.Path
	switch {
	case r.Method == "GET" && (route == "/api/stats" || route == "/api/health"):
		n.mu.RLock()
		defer n.mu.RUnlock()
		jsonResponse(w, 200, map[string]any{"ok": true, "node_id": n.id, "version": Version, "total_files": len(n.files), "total_chunks": len(n.files) * (DataShards + ParityShards), "storage_used_bytes": n.usage(), "quota_bytes": n.quota, "encryption": "AES-256-GCM (mobile shards)", "erasure_coding": "Reed-Solomon 8+4", "runtime": "android-native"})
	case r.Method == "GET" && route == "/api/peers":
		jsonResponse(w, 200, []any{})
	case r.Method == "GET" && route == "/api/files/tree":
		n.mu.RLock()
		defer n.mu.RUnlock()
		files := []File{}
		for _, f := range n.files {
			if token != "" && f.Token == token {
				f.Token = ""
				files = append(files, f)
			}
		}
		sort.Slice(files, func(i, j int) bool { return files[i].ID < files[j].ID })
		jsonResponse(w, 200, map[string]any{"files": files, "folders": []any{}, "node_id": n.id})
	case r.Method == "POST" && route == "/api/files/upload":
		r.Body = http.MaxBytesReader(w, r.Body, MaxObject+16*1024)
		if err := r.ParseMultipartForm(MaxObject + 16*1024); err != nil {
			jsonResponse(w, 413, map[string]string{"error": "invalid or oversized upload"})
			return
		}
		if r.MultipartForm != nil {
			defer r.MultipartForm.RemoveAll()
		}
		file, header, err := r.FormFile("file")
		if err != nil {
			jsonResponse(w, 400, map[string]string{"error": "file required"})
			return
		}
		defer file.Close()
		data, err := io.ReadAll(io.LimitReader(file, MaxObject+1))
		if err != nil {
			jsonResponse(w, 400, map[string]string{"error": "could not read upload"})
			return
		}
		f, err := n.put(token, header.Filename, r.FormValue("folder"), data)
		if err != nil {
			jsonResponse(w, 400, map[string]string{"error": err.Error()})
			return
		}
		jsonResponse(w, 200, f)
	case r.Method == "GET" && strings.HasPrefix(route, "/api/files/"):
		parts := strings.Split(strings.TrimPrefix(route, "/api/files/"), "/")
		if len(parts) > 2 || !uuidPattern.MatchString(parts[0]) || (len(parts) == 2 && parts[1] != "download") {
			http.NotFound(w, r)
			return
		}
		n.mu.RLock()
		f, ok := n.files[parts[0]]
		n.mu.RUnlock()
		if !ok || token == "" || f.Token != token {
			http.NotFound(w, r)
			return
		}
		if len(parts) == 1 {
			f.Token = ""
			jsonResponse(w, 200, f)
			return
		}
		data, err := n.read(f)
		if err != nil {
			jsonResponse(w, 503, map[string]string{"error": "insufficient valid shards to recover object"})
			return
		}
		w.Header().Set("Content-Type", "application/octet-stream")
		w.Header().Set("X-CFS-SHA256", f.SHA256)
		http.ServeContent(w, r, f.Name, time.Time{}, bytes.NewReader(data))
	default:
		http.NotFound(w, r)
	}
}
