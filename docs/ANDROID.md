# CollectiveFS on Android

`lib/mobile` is the embedded storage node used by Chat's Android foreground
service. It runs inside the app process, with a stable node UUID and private
storage. It uses this repository's Reed–Solomon implementation (8 data + 4
parity shards), AES-256-GCM encryption per shard, SHA-256 integrity checks,
atomic metadata commits and a default 512 MiB quota. Missing or corrupt shards
up to the parity budget can be reconstructed. Existing keys are never silently
replaced. Android app sandboxing protects the private node keys.

The mobile node implements the existing `api/` upload, metadata, download,
file-tree and stats contract. Every local request also requires the private
`X-CFS-Local-Key` capability returned through native IPC. Namespaces use
`X-CFS-Token`; reads never cross namespaces. The listener binds only to
loopback. This is an on-device replica, not a publicly reachable peer service:
Chat copies its signed, end-to-end encrypted objects between it and the full
CollectiveFS nodes. Full nodes continue to provide peering, Fernet shards,
contracts, repair and FUSE; those services are not advertised by the mobile
component. Mobile AES-GCM shards stay local; the portable HTTP file contents
are interoperable with full nodes.

Node UUIDs identify storage installations. A person's identity is separate:
Chat derives a UUIDv8 from SHA-256 of `collectivefs-identity-v1:<Ed25519 public
key in base64url>`, uses SHA-256 of the raw public key as the fingerprint, and
signs the public identity card. Linked devices keep the same private identity;
each still has its own storage-node UUID. A public card contains no private key
or workspace access settings. Device transfers are encrypted to a requesting
device's ephemeral Curve25519 key, signed by the existing identity, bound to
the request, and expire after ten minutes.

Build and test on Linux:

```sh
cd lib
go test -race ./mobile ./cmd/mobile-node
go run ./cmd/mobile-node --root /tmp/cfs-test-node --listen 127.0.0.1:8012
```

Build the Android libraries with Go and Android NDK r28+:

```sh
cd lib
NDK_HOME=/path/to/android-ndk ./android/build.sh /tmp/cfs-jniLibs
```

Copy `android/org/collectivefs/mobile/Node.java` into the Android Java sources
and the generated libraries into `jniLibs`. `Node.start(configJson)` starts the
server; `Node.state()` returns private IPC status and its capability;
`Node.stop()` closes it. The config has `root`, optional loopback `listen`
(default ephemeral port), and optional `quotaBytes`. The host app owns Android
service/notification lifecycle. No Docker, Python, FUSE or root is needed.

For a Termux diagnostic install, cross-build `./cmd/mobile-node` with
`GOOS=android GOARCH=arm64 CGO_ENABLED=1 CC=<NDK aarch64 clang> go build` and run
it with an app-private root. `runtime.json` is mode 0600 and includes the local
capability; do not publish that file.
