// Copyright 2015, Klaus Post, see LICENSE for details.
//
// Simple decoder reverses the process of encoder.go
//
// Usage:
//   decoder [-flags] basefile.ext
//
// Do not add the shard number to the filename.

package main

import (
	"flag"
	"fmt"
	"io/ioutil"
	"os"
	"strconv"
	"strings"

	"github.com/klauspost/reedsolomon"
)

var dataShards = flag.Int("data", 4, "Number of shards to split the data into")
var parShards = flag.Int("par", 2, "Number of parity shards")
var outFile = flag.String("out", "", "Alternative output path/file")

func init() {
	flag.Usage = func() {
		fmt.Fprintf(os.Stderr, "Usage of %s:\n", os.Args[0])
		fmt.Fprintf(os.Stderr, "  simple-decoder [-flags] basefile.ext\nDo not add the number to the filename.\n")
		fmt.Fprintf(os.Stderr, "Valid flags:\n")
		flag.PrintDefaults()
	}
}

func main() {
	flag.Parse()
	args := flag.Args()
	if len(args) != 1 {
		fmt.Fprintf(os.Stderr, "Error: No filenames given\n")
		flag.Usage()
		os.Exit(1)
	}
	fname := args[0]

	enc, err := reedsolomon.New(*dataShards, *parShards)
	checkErr(err)

	shards := make([][]byte, *dataShards+*parShards)
	for i := range shards {
		infn := fmt.Sprintf("%s.%d", fname, i)
		fmt.Println("Opening", infn)
		shards[i], err = ioutil.ReadFile(infn)
		if err != nil {
			fmt.Println("Error reading file", err)
			shards[i] = nil
		}
	}

	ok, err := enc.Verify(shards)
	if ok {
		fmt.Println("No reconstruction needed")
	} else {
		fmt.Println("Verification failed. Reconstructing data")
		err = enc.Reconstruct(shards)
		if err != nil {
			fmt.Println("Reconstruct failed -", err)
			os.Exit(1)
		}
		ok, err = enc.Verify(shards)
		if !ok {
			fmt.Println("Verification failed after reconstruction, data likely corrupted.")
			os.Exit(1)
		}
		checkErr(err)
	}

	outfn := *outFile
	if outfn == "" {
		outfn = fname
	}

	// Determine output size: use original file size if .size file exists,
	// otherwise fall back to shardSize * dataShards (may include padding)
	outputSize := len(shards[0]) * *dataShards
	sizefn := fmt.Sprintf("%s.size", fname)
	sizeData, sizeErr := ioutil.ReadFile(sizefn)
	if sizeErr == nil {
		n, convErr := strconv.Atoi(strings.TrimSpace(string(sizeData)))
		if convErr == nil && n > 0 && n <= outputSize {
			outputSize = n
			fmt.Printf("Using original file size: %d bytes\n", outputSize)
		}
	}

	fmt.Println("Writing data to", outfn)
	f, err := os.Create(outfn)
	checkErr(err)

	err = enc.Join(f, shards, outputSize)
	checkErr(err)
}

func checkErr(err error) {
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error: %s", err.Error())
		os.Exit(2)
	}
}
