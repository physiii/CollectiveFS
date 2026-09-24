package main

import (
	"collectivefs/lib/mobile"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"
)

func main() {
	root := flag.String("root", "", "private storage directory")
	listen := flag.String("listen", "127.0.0.1:8011", "loopback listen address")
	flag.Parse()
	n, err := mobile.Open(mobile.Config{Root: *root})
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if err = n.Start(*listen); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	descriptor, _ := json.Marshal(n.Descriptor())
	if err = os.WriteFile(*root+"/runtime.json", descriptor, 0600); err != nil {
		panic(err)
	}
	fmt.Println("CollectiveFS mobile node running")
	ch := make(chan os.Signal, 1)
	signal.Notify(ch, os.Interrupt, syscall.SIGTERM)
	<-ch
	n.Close()
}
