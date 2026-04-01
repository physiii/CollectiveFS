.PHONY: all clean

all: encoder decoder

encoder: cmd/encoder/encoder.go
	cd cmd/encoder && go build -o ../../encoder .

decoder: cmd/decoder/decoder.go
	cd cmd/decoder && go build -o ../../decoder .

clean:
	rm -f encoder decoder
