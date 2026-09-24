//go:build android

package main

/*
#include <stdlib.h>
*/
import "C"
import (
	"collectivefs/lib/mobile"
	"encoding/json"
	"sync"
)

var mu sync.Mutex
var running *mobile.Node

func reply(v any) *C.char { b, _ := json.Marshal(v); return C.CString(string(b)) }

//export CFSStart
func CFSStart(raw *C.char) *C.char {
	mu.Lock()
	defer mu.Unlock()
	if running != nil {
		return reply(running.Descriptor())
	}
	var cfg mobile.Config
	if err := json.Unmarshal([]byte(C.GoString(raw)), &cfg); err != nil {
		return reply(map[string]string{"error": "invalid node configuration"})
	}
	n, err := mobile.Open(cfg)
	if err == nil {
		err = n.Start(cfg.Listen)
	}
	if err != nil {
		return reply(map[string]string{"error": err.Error()})
	}
	running = n
	return reply(n.Descriptor())
}

//export CFSState
func CFSState() *C.char {
	mu.Lock()
	defer mu.Unlock()
	if running == nil {
		return reply(map[string]any{"running": false})
	}
	return reply(running.Descriptor())
}

//export CFSStop
func CFSStop() {
	mu.Lock()
	defer mu.Unlock()
	if running != nil {
		running.Close()
		running = nil
	}
}
func main() {}
