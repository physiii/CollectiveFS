# CollectiveFS — Design Document

> Last updated: 2026-03-25

---

## Table of Contents

1. [Overview and Philosophy](#1-overview-and-philosophy)
2. [Goals and Non-Goals](#2-goals-and-non-goals)
3. [System Architecture](#3-system-architecture)
4. [Component Descriptions](#4-component-descriptions)
5. [Data Flows](#5-data-flows)
6. [Protocol Design](#6-protocol-design)
7. [Security Model](#7-security-model)
8. [Technology Choices and Rationale](#8-technology-choices-and-rationale)
9. [Comparison to Existing Solutions](#9-comparison-to-existing-solutions)
10. [Current Status and Roadmap](#10-current-status-and-roadmap)

---

## 1. Overview and Philosophy

CollectiveFS is a decentralized, peer-to-peer distributed file storage system. It treats storage as a commons: participants contribute disk space to the network and, in return, receive the ability to store their own files across that same network.

### Core Tenets

**Decentralization of control.** There is no single authority that owns, operates, or can revoke access to the network. Control is distributed proportionally among the individuals who provide the underlying infrastructure.

**Hidden in plain sight.** Files are broken into chunks and distributed across untrusted peers. Before leaving the owner's machine, every chunk is encrypted with a key that only the owner holds. Peers store ciphertext they cannot read. The data is visible to the network but meaningless without the key.

**Configurable fault tolerance.** Unlike systems that impose fixed replication strategies, CollectiveFS lets each user configure the ratio of data shards to parity shards. A user who wants higher redundancy pays with more storage contribution; a user who wants to minimize overhead can choose lower parity. Every byte on the network is treated with equal priority — there is no concept of "pinning" or tiered storage.

**Built from the bottom up.** The network grows organically as individual nodes join. There is no central administrator that must provision or configure the cluster.

---

## 2. Goals and Non-Goals

### Goals

- Store files durably across a public peer-to-peer network with no trusted third party.
- Encrypt all data before it leaves the owner's machine, using a key that never leaves local storage.
- Split files using erasure coding so that the original can be reconstructed even when some chunks are lost or unavailable.
- Transfer chunks to peers using a direct, encrypted transport channel (WebRTC data channels).
- Provide a simple local interface (watched directory + web UI) so that using the system feels like using a local folder.
- Package the daemon as a single deployable binary for ease of distribution.

### Non-Goals

- **Version control.** CollectiveFS does not track file history. Versioning, if desired, must be implemented at the application layer above CollectiveFS.
- **Private synchronization.** CollectiveFS is a public network. It is not a replacement for tools like Syncthing, which synchronize a fixed set of personally-owned devices.
- **Enterprise / big-data workloads.** The design targets individual users storing personal files, not petabyte-scale analytics pipelines.
- **Content addressing.** Files are identified by UUID, not by content hash. Two identical files uploaded by different users produce independent entries on the network.
- **Access control lists.** Authorization is implicit in key possession. There are no per-file permissions or sharing mechanisms beyond giving someone your Fernet key (which would grant access to all your files).

---

## 3. System Architecture

### High-Level Diagram

```
+-------------------------------------------------------------+
|                      User's Machine                         |
|                                                             |
|  Watched Directory                                          |
|  +-----------+                                              |
|  | file.mp4  |  <-- user drops file here                   |
|  +-----------+                                              |
|        |                                                    |
|        v  (watchdog inotify event)                          |
|  +---------------------+      +-------------------------+  |
|  |   cfs.py daemon     |      |  HTTP Server :8080      |  |
|  |                     |      |  index.html (Web UI)    |  |
|  |  1. Encode (RS)     |      +-------------------------+  |
|  |  2. Encrypt (Fernet)|                                   |
|  |  3. Transfer (RTC)  |                                   |
|  +---------------------+                                   |
|        |          |                                         |
|        |          v                                         |
|        |   ~/.collective/                                   |
|        |   ├── config       (root path)                     |
|        |   ├── key          (Fernet key)                    |
|        |   ├── proc/        (in-progress chunk staging)     |
|        |   ├── cache/       (retrieved chunk cache)         |
|        |   ├── public/      (chunks offered to peers)       |
|        |   └── tree/        (file metadata JSON)            |
|        |                                                    |
|        v                                                    |
|  +---------------------+                                    |
|  | lib/encoder (Go)    |  Reed-Solomon encoding             |
|  | --data 8 --par 4    |  12 shards out (8 data + 4 parity)|
|  +---------------------+                                    |
|        |                                                    |
|        v                                                    |
|  +---------------------+                                    |
|  | filexfer/filexfer.py|  WebRTC data channel (aiortc)      |
|  | RTCPeerConnection   |  16 KB chunks over SCTP            |
|  | signaling: TCP 1234 |                                    |
|  +---------------------+                                    |
|        |                                                    |
+--------|----------------------------------------------------+
         |  WebRTC (DTLS/SCTP)
         v
+------------------+   +------------------+   +------------------+
|    Peer Node A   |   |    Peer Node B   |   |    Peer Node C   |
|  (stores shard 0)|   |  (stores shard 4)|   |  (stores shard 8)|
+------------------+   +------------------+   +------------------+
```

### Internal Directory Layout

```
~/.collective/
├── config          # plain text: one line containing the watched root path
├── key             # raw bytes: the user's Fernet symmetric key
├── proc/           # staging area for chunks while encode/encrypt is in progress
│   └── <relpath>.d/
│       ├── filename.0   # shard 0 (data)
│       ├── filename.1   # shard 1 (data)
│       ...
│       └── filename.11  # shard 11 (parity)
├── cache/          # locally cached chunks retrieved from peers
├── public/         # encrypted chunks staged for upload to peers
└── tree/           # per-file metadata (JSON)
    └── <uuid>.json
```

---

## 4. Component Descriptions

### 4.1 cfs.py — Main Daemon

The entry point and orchestrator for all local operations. Runs as a long-lived process.

**Responsibilities:**
- Reads configuration from `~/.collective/config` (root path) and `~/.collective/key` (Fernet key). Generates a new key on first run.
- Starts an HTTP server on `localhost:8080` serving `index.html` and accepting POST requests from the web UI.
- Starts a `watchdog` filesystem observer on the configured root directory.
- On a `created` event for any non-`.collective` file, triggers the encode → encrypt → transfer pipeline.
- Manages the `.collective/` subdirectory tree (creates directories as needed).

**CLI flags:**

| Flag | Description |
|------|-------------|
| `--verbose` / `-v` | Enable DEBUG-level logging |
| `--version` | Print version |
| `--input` | Override the watched source directory |
| `--output` | Override the destination directory |
| `--service` | Run continuously (default behavior) |

**Key state:**
- `rootPath` — watched directory root (from config file)
- `processPath` — `<root>/.collective/proc`
- `fernet` — live `Fernet` instance using the loaded or newly-generated key

### 4.2 lib/encoder — Reed-Solomon Encoder (Go)

A compiled Go binary that wraps the `klauspost/reedsolomon` library. It splits a single input file into `N` data shards and `M` parity shards, writing each shard as a separate file.

**Invocation:**

```
./lib/encoder --data <N> --par <M> --out "<output_dir>" "<input_file>"
```

**Default configuration:**

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `--data`  | 8       | Number of data shards |
| `--par`   | 4       | Number of parity shards |
| Total shards | 12  | Files distributed across 12 peers |
| Loss tolerance | 4 | Any 4 shards can be missing and the file is still recoverable |

**Output naming convention:**

For an input file `video.mp4`, with 12 total shards, the encoder writes:

```
<output_dir>/
├── video.mp4.0
├── video.mp4.1
├── ...
└── video.mp4.11
```

Shards `0` through `7` are data shards; shards `8` through `11` are parity shards.

**Shortcomings noted in source:**
- If the file size is not evenly divisible by the number of data shards, trailing zero-padding is added. The metadata layer must record the original file size to strip padding on reconstruction.
- Shard order must be preserved. Swapping two shards prevents reconstruction.

### 4.3 filexfer/filexfer.py — WebRTC File Transfer

Handles peer-to-peer transfer of individual encrypted shards using WebRTC data channels via the `aiortc` Python library.

**Roles:**

| Role | Behavior |
|------|----------|
| `send` (offerer) | Opens a data channel named `filexfer`, reads the shard file in 16 KB chunks, streams it to the peer, sends empty message as EOF signal |
| `receive` (answerer) | Listens for the `datachannel` event, writes incoming bytes to a local file, sends `BYE` on receipt of the EOF signal |

**Signaling:**
- Out-of-band signaling uses a local TCP socket at `127.0.0.1:1234`.
- The offerer creates an SDP offer and sends it via the signaling socket.
- The answerer receives the offer, creates an SDP answer, and sends it back.
- ICE candidates are exchanged through the same socket.
- A `BYE` message signals graceful teardown.

**Flow control:**
- Sender checks `channel.bufferedAmount` against `channel.bufferedAmountLowThreshold` before writing the next chunk to avoid overwhelming the SCTP send buffer.
- The `bufferedamountlow` event re-triggers sending when the buffer drains.

**Return value:**
- `start_transfer("send", fileInfo, chunkInfo)` currently returns a placeholder string. In the intended design it will return the completed SDP offer so it can be stored in the chunk metadata for later use by the receiving peer.

### 4.4 Cryptography — Fernet Symmetric Encryption

CollectiveFS vendors the `pyca/cryptography` library (version 3.3.2) directly in the repository under `cryptography/`.

**Key management:**
- One Fernet key per user, generated on first run with `Fernet.generate_key()`.
- Stored at `~/.collective/key` as raw bytes.
- The key never leaves the local machine in the current design.

**Encryption scheme (Fernet internals):**
- Signing key: HMAC-SHA256 (128-bit key)
- Encryption key: AES-128-CBC (128-bit key)
- IV: randomly generated per encryption call
- Format: `Version | Timestamp | IV | Ciphertext | HMAC`

Each shard is encrypted independently. This means:
- Shards cannot be correlated with each other by a passive observer who does not have the key.
- Losing the Fernet key makes all stored data permanently unrecoverable.

### 4.5 index.html — Web UI

A minimal HTML form served by the built-in HTTP server. Uses jQuery 1.7.2 to POST a JSON payload to `http://127.0.0.1:8080`. Currently accepts free-form JSON text and is used for development and manual testing of the HTTP endpoint.

### 4.6 cfs.spec — PyInstaller Packaging

A PyInstaller spec file that packages `cfs.py` and all its Python dependencies into a self-contained binary under `dist/cfs/`. UPX compression is enabled. The Go encoder binary (`lib/encoder`) is a separate artifact that must be distributed alongside the Python bundle.

---

## 5. Data Flows

### 5.1 Saving a File

```
User drops file into watched directory
        |
        v
watchdog fires on_created() in ModifiedDirHandler
        |
        | (skips if path contains '.collective' or is a directory)
        v
[1] ENCODE
    subprocess: ./lib/encoder --data 8 --par 4 \
        --out "~/.collective/proc/<relpath>.d" "<filepath>"
    -> produces 12 shard files (.0 through .11)
        |
        v
[2] BUILD METADATA SKELETON
    fileInfo = {
        "id":               <uuid4>,
        "name":             <filename>,
        "folder":           <proc_shard_dir>,
        "number_of_chunks": 12,
        "chunks":           []          # populated next
    }
        |
        v
[3] ENCRYPT + TRANSFER (parallel threads, one per shard)
    For each shard file:
        a. Read shard bytes
        b. fernet.encrypt(bytes)  -> overwrites shard file with ciphertext
        c. Create chunkInfo = {
               "num":    <shard_index>,
               "id":     <uuid4>,
               "path":   <shard_file_path>,
               "offer":  {},        # populated by transfer
               "answer": {}         # populated when peer responds
           }
        d. filexfer.start_transfer("send", fileInfo, chunkInfo)
           -> creates RTCPeerConnection
           -> creates data channel "filexfer"
           -> generates SDP offer
           -> sends offer via signaling socket (127.0.0.1:1234)
           -> streams encrypted shard bytes (16 KB at a time)
           -> awaits SDP answer from peer
           -> stores offer/answer in chunkInfo
        e. Insert chunkInfo into fileInfo['chunks']
        |
        v
[4] PERSIST METADATA
    Write fileInfo JSON to ~/.collective/tree/<uuid>.json
    (planned — not yet fully implemented)
```

### 5.2 Retrieving a File (Planned)

```
User requests file by name or UUID
        |
        v
[1] LOAD METADATA
    Read ~/.collective/tree/<uuid>.json
    -> get list of chunk IDs, their peer locations (SDP answers)
        |
        v
[2] FETCH CHUNKS FROM PEERS (parallel)
    For each chunk:
        a. filexfer.start_transfer("receive", fileInfo, chunkInfo)
           -> uses stored SDP offer/answer to reconnect to peer
           -> receives encrypted shard bytes
           -> writes to ~/.collective/cache/<chunk_id>
        b. If chunk is unavailable, note as missing
           (up to 4 of 12 chunks may be missing with default config)
        |
        v
[3] DECRYPT CHUNKS
    For each received chunk file:
        fernet.decrypt(ciphertext)  -> plaintext shard
        |
        v
[4] RECONSTRUCT FILE (Reed-Solomon)
    ./lib/decoder --data 8 --par 4 <shard_files...>
    -> reconstructs any missing shards from parity
    -> concatenates data shards 0..7
    -> trims zero-padding to original file size
        |
        v
[5] DELIVER FILE
    Write reconstructed file to user's directory
```

---

## 6. Protocol Design

### 6.1 Signaling Protocol

WebRTC requires an out-of-band signaling channel to exchange SDP (Session Description Protocol) offers, answers, and ICE (Interactive Connectivity Establishment) candidates before the direct peer connection can be established.

Current implementation uses a local TCP socket (`aiortc`'s built-in socket signaling on `127.0.0.1:1234`). This is a **development placeholder** — in production, signaling must happen over the network.

**Message sequence:**

```
Sender (Offerer)                    Signaling Server            Receiver (Answerer)
      |                                    |                           |
      |-- connect() ------------------->   |                           |
      |                                    |  <--- connect() ----------|
      |                                    |                           |
      |-- SDP Offer ------------------>    |                           |
      |                                    |-- SDP Offer ----------->  |
      |                                    |                           |
      |                                    |  <-- SDP Answer ----------|
      |  <-- SDP Answer ----------------   |                           |
      |                                    |                           |
      |  <-- ICE Candidates -----------    |-- ICE Candidates ----->   |
      |                                    |                           |
      |====== WebRTC Data Channel (DTLS/SCTP) ========================>|
      |                                    |                           |
      |-- encrypted shard bytes ===============================>       |
      |-- empty message (EOF) =================================>       |
      |                                    |                           |
      |                                    |  <-- BYE ---------------  |
      |  <-- BYE ----------------------    |                           |
```

### 6.2 Chunk Transfer Protocol

Once the WebRTC data channel `filexfer` is open, the protocol is:

| Step | Sender Action | Receiver Action |
|------|---------------|-----------------|
| 1 | Read up to 16,384 bytes from shard file | — |
| 2 | `channel.send(bytes)` | Receive bytes, write to file |
| 3 | Repeat until EOF | Accumulate bytes |
| 4 | `channel.send(b"")` (empty = EOF sentinel) | Detect empty message, close file |
| 5 | Await BYE from receiver | `signaling.send(BYE)` |
| 6 | `signaling.send(BYE)` | — |

### 6.3 Metadata Format

Each stored file is described by a JSON document persisted in `~/.collective/tree/`.

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "video.mp4",
  "folder": "/home/user/.collective/proc/video.mp4.d",
  "number_of_chunks": 12,
  "chunks": [
    {
      "num": 0,
      "id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
      "path": "/home/user/.collective/proc/video.mp4.d/video.mp4.0",
      "offer": { ... },
      "answer": { ... }
    },
    ...
  ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID string | Unique identifier for this file entry |
| `name` | string | Original filename |
| `folder` | string | Absolute path to the local shard staging directory |
| `number_of_chunks` | integer | Total shards (data + parity) |
| `chunks[].num` | integer | Shard index (0-based) |
| `chunks[].id` | UUID string | Unique identifier for this shard |
| `chunks[].path` | string | Local path where this encrypted shard is staged |
| `chunks[].offer` | object | SDP offer generated by the sender |
| `chunks[].answer` | object | SDP answer received from the storing peer |

### 6.4 Peer Discovery (Planned)

Peer discovery is not yet implemented. The intended mechanism:

1. A lightweight **bootstrap/rendezvous server** maintains a list of active peer addresses and their available storage capacity.
2. When a node starts, it announces itself to the rendezvous server.
3. When a node needs to store a chunk, it queries the rendezvous server for candidate peers with sufficient free capacity.
4. The rendezvous server facilitates the SDP exchange (acting as the signaling channel), then steps out of the data path.
5. Long-term: gossip-based peer discovery to reduce reliance on any single rendezvous server.

---

## 7. Security Model

### 7.1 Threat Model

| Threat | Mitigation |
|--------|------------|
| Peer reads stored chunk content | Fernet encryption — peer sees only ciphertext |
| Passive network eavesdropping on transfer | WebRTC data channels use DTLS encryption in transit |
| Peer corrupts a stored chunk | Reed-Solomon parity can detect and recover from corrupted/missing shards (up to `par` shards) |
| Key theft from local machine | Key is stored in `~/.collective/key`; OS filesystem permissions are the only protection |
| Peer refuses to return a chunk | Parity shards allow reconstruction from any `data` of `total` shards |
| Shard correlation across peers | Each shard is independently encrypted with the same key but a random IV; shards are not linkable without the key |

### 7.2 Key Management

- **Generation:** `Fernet.generate_key()` generates 32 cryptographically random bytes (via `os.urandom`), base64url-encoded.
- **Storage:** Written to `~/.collective/key` on first run. The file is read back on every subsequent startup.
- **Scope:** A single key encrypts all of a user's shards. There is currently no per-file or per-session key rotation.
- **Loss:** Losing the key is equivalent to losing all stored data. There is no key recovery mechanism.
- **Distribution:** The key is never transmitted over the network in the current design. Sharing files with another user would require out-of-band key exchange (not currently supported).

### 7.3 Transport Security

WebRTC data channels are secured by DTLS 1.2 (mandatory per the WebRTC specification). The `aiortc` library handles DTLS negotiation automatically. All chunk bytes transit peers' networks as DTLS-encrypted SCTP datagrams, providing a second layer of confidentiality beyond the Fernet application-layer encryption.

### 7.4 Known Limitations

- The signaling channel (TCP socket) is currently unencrypted and unauthenticated. A production signaling server must use TLS and authenticate nodes.
- There is no mechanism to verify that a peer has actually stored a shard (proof of storage). A malicious peer could acknowledge storage and then discard the chunk.
- The Fernet key is stored in plaintext on disk. A compromised local machine exposes all stored data.
- There is no per-file access control; possessing the key grants access to all files.

---

## 8. Technology Choices and Rationale

### Python 3.9+ (cfs.py, filexfer.py)

Python was chosen for rapid development of the daemon and transfer logic. The `asyncio` ecosystem (used by `aiortc`) integrates naturally with Python's threading model used for parallel chunk encryption.

### watchdog (filesystem monitoring)

`watchdog` provides a cross-platform abstraction over OS filesystem notification APIs (inotify on Linux, FSEvents on macOS, ReadDirectoryChangesW on Windows). The `on_created` event triggers the full encode/encrypt/transfer pipeline, making the watched directory feel like a magic drop folder.

### Go + klauspost/reedsolomon (encoder binary)

Reed-Solomon encoding is a computationally intensive operation, especially for large files. The Go implementation using `klauspost/reedsolomon` achieves high throughput through SIMD-accelerated Galois Field arithmetic (AVX2/AVX512 on x86-64, NEON on ARM64). Calling it as a subprocess from Python keeps the hot computational path out of the Python interpreter while keeping the orchestration logic simple.

### aiortc (WebRTC)

WebRTC provides:
- **NAT traversal** via ICE/STUN/TURN, enabling peers behind firewalls and NAT to connect directly.
- **Encrypted transport** via mandatory DTLS.
- **Reliable, ordered delivery** via SCTP data channels (appropriate for file transfer).
- **Flow control** via SCTP backpressure, exposed through the `bufferedAmount` API.

`aiortc` is a pure-Python WebRTC implementation that does not require a browser, making it suitable for server-side/daemon use.

### Fernet (pyca/cryptography)

Fernet is a high-level, misuse-resistant symmetric encryption scheme. It bundles AES-128-CBC encryption with HMAC-SHA256 authentication into a single token format, preventing common mistakes such as unauthenticated encryption or IV reuse. The library is vendored directly in the repository to ensure a specific known version is used regardless of the host system's installed packages.

### PyInstaller (packaging)

PyInstaller bundles the Python interpreter, all dependencies, and `cfs.py` into a distributable directory (`dist/cfs/`). This simplifies installation for end users who do not have a Python environment configured. The Go encoder binary is compiled separately and co-distributed.

---

## 9. Comparison to Existing Solutions

| Project | Architecture | Key Difference from CollectiveFS |
|---------|-------------|----------------------------------|
| **IPFS** | Content-addressed P2P, DHT routing | IPFS uses content addressing (CID) and version-controlled MerkleDAG. CollectiveFS uses UUID-addressed files with no version history. IPFS has a concept of "pinning" to prioritize data; CollectiveFS treats all bytes equally with user-configured parity. |
| **Hadoop HDFS** | Centrally administered cluster, block replication | HDFS is configured top-down by a single organization. CollectiveFS is built bottom-up by individual participants. HDFS uses 3x replication; CollectiveFS uses erasure coding for more storage-efficient redundancy. |
| **Syncthing** | P2P synchronization between owned devices | Syncthing is a private network — you sync only between your own nodes. CollectiveFS is a public network where you store data on nodes owned by strangers. |
| **Storj** | Erasure-coded distributed storage, token economy | Closest conceptually. Storj uses a cryptocurrency token economy to incentivize storage. CollectiveFS uses a reciprocal barter model (store others' data to earn storage capacity) with no blockchain component, targeting lower complexity and broader accessibility. |
| **Tahoe-LAFS** | Erasure-coded, capability-based security | Tahoe-LAFS uses capability URIs for access control and has a mature security model. CollectiveFS is simpler (single symmetric key) and more accessible but less flexible for sharing scenarios. |

---

## 10. Current Status and Roadmap

### 10.1 What Works Today

| Feature | Status | Notes |
|---------|--------|-------|
| Configuration loading | Working | Reads `~/.collective/config` and `~/.collective/key` |
| Key generation and persistence | Working | Generates Fernet key on first run, reloads on subsequent runs |
| Filesystem watching | Working | `watchdog` detects new files in watched directory |
| Reed-Solomon encoding | Working | `lib/encoder` binary splits files into 12 shards |
| Fernet encryption of shards | Working | Each shard encrypted independently in parallel threads |
| WebRTC offer generation | Working | `aiortc` generates SDP offer per chunk |
| HTTP server + web UI | Working | Serves `index.html` on `localhost:8080`, accepts POST |
| PyInstaller packaging | Working | `cfs.spec` produces distributable binary |

### 10.2 What Is Not Yet Implemented

| Feature | Priority | Notes |
|---------|----------|-------|
| Peer discovery | Critical | No mechanism to find peers on the network |
| Network signaling server | Critical | Current signaling is local-only (127.0.0.1:1234) |
| SDP answer handling | Critical | Receiver side of WebRTC handshake not integrated |
| Chunk storage confirmation | High | No acknowledgment that a peer has stored a shard |
| Metadata persistence | High | `fileInfo` JSON is built in memory but not reliably written to `~/.collective/tree/` |
| File retrieval | High | Decode path (`lib/decoder`) and fetch-from-peer flow not implemented |
| Reed-Solomon decoding | High | `lib/decoder` binary exists but retrieval pipeline is not wired up |
| Fernet decryption on retrieval | High | `decryptChunk()` function exists but is not called in a retrieval flow |
| Storage accounting | Medium | No mechanism to track how much space a node has contributed vs. consumed |
| Peer authentication | Medium | Nodes cannot verify the identity of peers they connect to |
| Key backup / recovery | Medium | Losing `~/.collective/key` makes all stored data unrecoverable |
| ConfigDir portability | Low | `ConfigDir` is hardcoded to `/home/andy/.collective/` in source |

### 10.3 Roadmap

**Phase 1 — Complete the Core Pipeline**
- Wire up the SDP answer flow so that `chunkInfo['answer']` is populated after transfer.
- Persist `fileInfo` JSON to `~/.collective/tree/<uuid>.json` after all chunks are transferred.
- Implement the retrieval path: load metadata, fetch chunks, decrypt, Reed-Solomon reconstruct, deliver file.
- Replace the hardcoded `ConfigDir` path with a user-relative default (`~/.collective/`).

**Phase 2 — Network Signaling**
- Build or integrate a lightweight rendezvous/signaling server (e.g., a small WebSocket server).
- Replace the local TCP signaling socket with the network signaling server.
- Define and implement the node announcement and chunk-placement protocol.

**Phase 3 — Peer Discovery and Storage Accounting**
- Implement peer discovery (bootstrap nodes + gossip).
- Track storage contributed vs. consumed per node to enforce the reciprocal barter model.
- Add basic proof-of-storage challenges to detect peers who discard chunks after acknowledging storage.

**Phase 4 — Hardening**
- Add TLS to the signaling channel.
- Implement node identity (public/private keypair) and authenticated signaling messages.
- Add key backup guidance and optional passphrase protection for `~/.collective/key`.
- Resolve edge cases in the Reed-Solomon encoder (zero-padding, shard ordering).

**Phase 5 — Usability**
- Build out the web UI to support file browsing, upload progress, and retrieval.
- Cross-platform testing (macOS, Windows).
- Package `lib/encoder` and `lib/decoder` binaries alongside the PyInstaller bundle.
