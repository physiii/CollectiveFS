# CollectiveFS — Performance Analysis and Optimization Roadmap

Every number is marked with its provenance:

- **`[C]`** measured on the live two-node cluster through `/media/collectivefs` (`benchmarks/results/mount-eval.md`)
- **`[L]`** measured in isolation against this repo's own code (encoder, decoder, container, `.venv`)
- **`[P]`** measured from a working prototype of the proposed change
- **`[cite]`** from published literature or vendor source, with a link

Unmarked statements are derivations from marked ones. Where a claim could not be verified it says so.

---

## 1. Baseline

Two nodes, 8+4 Reed–Solomon, Fernet per shard, 2.5 GbE.

| | sonic | office |
|---|---|---|
| CPU | Ryzen 7 5800X, 16T | i9-12900K, 24T |
| OS | Ubuntu 24.04, kernel 6.11 | Ubuntu 26.04, kernel 7.0 |
| Disk | Samsung 990 EVO Plus NVMe | Samsung 990 EVO Plus NVMe |
| ICMP RTT | 0.44/0.57/0.72 ms `[C]` | |
| Node API RTT | 16.5 ms median `[C]` | |

| Metric | sonic `[C]` | office `[C]` |
|---|---|---|
| Write 64 MB | 300.8 MB/s | 136.4 MB/s |
| Write 8 MB | 267.2 MB/s | 17.6 MB/s |
| **Read 64 MB** | **10.4 MB/s** (6.36 s) | **10.3 MB/s** (6.18 s) |
| Read 8 MB | 20.7 MB/s | 14.9 MB/s |
| Read 1 MB | 5.7 MB/s | 1.6 MB/s |
| Real tree write | 2.9 files/s | 88.8 files/s |
| Real tree read | 21.9 files/s | 13.1 files/s |
| Concurrent 8×8 MB | 233.2 MB/s | 127.5 MB/s |
| Storage expansion | 1.61× | 1.07× |
| Degraded read (peer down, 8 MB) | 193.1 ms, SHA-256 match | |
| Proof-of-storage round trip | 2.9 ms mean, 100% pass (4/4) | |

**Reads are 15–30× slower than writes.** That asymmetry is the whole problem. Everything below follows from decomposing it.

### 1.1 Where a 64 MB read actually goes

Instrumented against the real pipeline `[L]`:

| Stage | Time | Bytes moved | Share |
|---|---|---|---|
| Read 12 encrypted shards | 134.4 ms | 134.2 MB | 7.1% |
| **Fernet-decrypt all 12** | **1316.8 ms** | 100.7 MB | **69.8%** |
| Write plaintext to staging | 114.0 ms | 100.7 MB | 6.0% |
| Decoder subprocess (Verify+Join) | 232.2 ms | — | 12.3% |
| Read reconstructed file back | 90.1 ms | 67.1 MB | 4.8% |
| **Total** | **1887.5 ms** | | |
| **I/O amplification** | | | **8.5×** |

**Crypto is 70% of read time. Reed–Solomon is 12%.** The intuition that erasure coding is the cost is wrong.

### 1.2 Four structural defects behind those numbers

1. **`gather_shards` decrypts all 12 shards when RS needs 8** (`api/replication.py:285`) — it loops the whole `chunk_list`. Cost of the excess: 1186 ms vs 470 ms `[L]`.
2. **Fernet is 6.7% cipher and ~76% packaging** `[L]`. Decomposed on an 8 MB shard: base64 40.8%, unaccounted copies 35.7%, HMAC 9.3%, PKCS7 unpad 7.4%, **AES-128-CBC itself 6.7%**. `fernet.py` is pure Python and makes ~6 full-buffer passes; only 2 are cryptography. Corroborated by pyca [#4953](https://github.com/pyca/cryptography/issues/4953) `[cite]`.
3. **The container runs `cryptography` 41.0.7** `[L]` — `requirements.txt` says `>=41.0.7` and pip took the floor. That alone is 1.8× slower than 45.
4. **`replication.distribute()` pushes shards sequentially** (`api/replication.py:175`) — a plain `await` loop, no `asyncio.gather`. 8 × 9.1 ms = **72.8 ms/file** `[L]`, the single largest per-file write cost.

### 1.3 Two facts that make range reads possible today

**The shard mapping is exactly contiguous.** `reedsolomon.Split` computes `perShard = ceil(N/k)` and slices `data[i*perShard:(i+1)*perShard]`. Verified `[L]` on a deliberately non-divisible 64 MiB + 12345 B file: `shard_i == file[i*perShard:(i+1)*perShard]` exactly. So offset `o` → shard `o//perShard`, and any read below 8 MB in a 64 MB file touches **1–2 shards**.

**Degraded reads do not need whole shards.** RS parity is bytewise across shards, so recovering bytes `[t0,t1)` of a lost shard needs only bytes `[t0,t1)` from any k survivors. Verified `[L]` with exactly k=8 present: 1 MiB window in 1.05 ms, **unaligned 4 KiB window in 25.3 µs**, both byte-correct.

**Consequence: the on-disk RS layout already supports optimal range reads.** Nothing about erasure coding needs to change. What blocks ranged access is entirely the encryption framing (Fernet authenticates the whole shard, so you must decrypt all of it) and the HTTP plumbing.

### 1.4 Small files

For the measured tree (mean file 5.5 KB) `[L]`:

| Metric | Value |
|---|---|
| On-disk files per logical file | **14** (12 shards + `.size` + tree JSON) |
| Allocated bytes for a 5,500 B file | **57,344 B** = **10.4× amplification** |
| Metadata JSON per file | 2,736 B → 4,096 B allocated |
| Local-FS-only ceiling | 2.98 ms/file → 335 files/s |

**Below ~12–16 KiB, 8+4 erasure coding costs more space than 3× replication** `[L]`:

| File size | EC 8+4 + Fernet | 3× replication | Winner |
|---|---|---|---|
| 1 KiB | 57,344 B (56×) | 16,384 B (16×) | replication |
| **5.5 KiB** (our measured mean) | 57,344 B (10.4×) | 40,960 B | **replication** |
| 12 KiB | 57,344 B (4.7×) | 53,248 B (4.3×) | tie |
| 16 KiB | 57,344 B (3.5×) | 65,536 B (4.0×) | EC |
| 1 MiB | 2,121,728 B (2.0×) | 3,162,112 B (3.0×) | EC |

HDFS shows the identical effect: for `RS-6-3-1024k` a file ≤ 1 cell costs 4.0× `[cite]`.

### 1.5 Metadata is O(n) per operation and O(n²) per bulk copy

`_list_all_tree()` (`api/main.py:210`) globs and parses every tree JSON. Measured scaling `[L]`:

| Files | Full scan | µs/file | Implied bulk-copy cost |
|---|---|---|---|
| 1,000 | 48 ms | 47.7 | 24 s |
| 20,000 | 1,211 ms | 60.5 | **3.4 h** |
| 50,000 | 3,015 ms | 60.3 | **21 h** |

The O(n²) is real, not hypothetical: `cfs_mount.py` resets `self._tree_fetched = 0.0` after **every** mutation (6 sites), and `_refresh()` refetches and rebuilds the entire tree. Writing n files triggers n full scans. This is why `statfs` showed **mean 1983 ms, peak 41.8 s** `[C]`.

### 1.6 Kernel metadata caching is disabled

`cfs_mount.py:356` sets `entry_timeout = attr_timeout = 0`. Effect of enabling it, measured on a 200-file `ls -l` `[L]`:

| | readdir | lookup | getattr | total daemon round trips |
|---|---|---|---|---|
| `timeout=0` (today) | 9 | 202 | 407 | **618** |
| `timeout=60` | 9 | 2 | 1 | **12** |

At the measured 874 µs/op that is **540 ms → 8 ms**. The `readdir` calls themselves do not go away — READDIRPLUS already carries the attributes; what disappears is the lookup/getattr storm behind them.

**The stated justification for `0` is wrong.** The comment says a cached dentry could serve "a stale *negative* answer, hiding a file that now exists." Measured `[L]`: with `entry_timeout=60`, six consecutive lookups of a nonexistent name **all reached the daemon**, and a remotely-created file was found on the next lookup with no invalidation call. Mechanism: pyfuse3 signals a miss by raising `FUSEError(ENOENT)`, which sends an *error* reply, not the `ino=0` + timeout reply that creates a negative dentry. The kernel never caches error replies, so negative caching is structurally off regardless of the timeout. JuiceFS's `--negative-entry-cache` defaults to 0 for the same reason `[cite]`.

Raising the timeout *can* keep a deleted file visible or a size stale for up to the TTL. That is bounded, is what NFS has shipped for 30 years (`acregmin` 3 s / `acregmax` 60 s `[cite]`), and is cancellable on demand via `invalidate_entry_async()`. JuiceFS — the closest analogue, distributed and multi-writer — ships `--attr-cache 1 --entry-cache 1` `[cite]`.

---

## 2. Phases

Ordered by **return per unit of risk**, not by size of speedup. Each phase states the metric it optimizes and is independently shippable.

### Phase 0 — Four fixes with no format change
**Optimizes: read latency, write latency, event-loop fairness. Risk: negligible.**

| Fix | Change | Measured effect |
|---|---|---|
| Pin `cryptography>=45` | one line in `requirements.txt` | **1.8×** on all crypto `[L]` |
| Stop decrypting at k | early exit in `gather_shards` | 1186 → 470 ms, **2.5×** `[L]` |
| Parallelise shard push | `asyncio.gather` in `distribute()` | 72.8 → ~9 ms/file, **~8×** `[L]` |
| Get crypto off the event loop | `anyio.to_thread.run_sync` | no throughput gain; stops one 1.2 s download starving SSE, health checks and peer shard-serving |

Also hoist `_load_fernet()` — it re-reads the key file from disk on every call.

Combined: **~2.9 s off a 64 MB read** and **~8× on small-file writes**, with no on-disk change and no migration.

### Phase 1 — Framed AEAD: Fernet → AES-256-GCM
**Optimizes: read throughput and stored bytes. Risk: format change, needs versioning.**

Per-shard layout:

```
header: magic "CFS2" | version u8 | shard_idx u8 | frame_size u32 | file_size u64 | key_id u128
frames: [ AES-256-GCM(plaintext[frame], aad = file_id ‖ version ‖ shard_idx ‖ frame_idx) ‖ tag[16] ] *
```

Frame `f` sits at byte offset `32 + f*(frame+16)` — pure arithmetic, no frame table, so any byte range maps to a contiguous frame window.

**Frame size.** Throughput knee measured at 1 MiB `[L]`; overhead is 0.0015% there and 0.024% at 64 KiB. Choose **64 KiB** anyway — it matches FUSE's 128 KiB readahead (2 frames) and sets the minimum I/O for a ranged read. The throughput difference (1661 vs 3276 MB/s) is irrelevant once crypto is no longer the bottleneck, and read granularity is worth more than peak MB/s. This matches age (64 KiB) and rclone crypt (64 KiB, chosen for exactly this reason) `[cite]`.

Measured in **our container as it stands** (`cryptography` 41.0.7, 8 MB shard) `[L]`:

| | Fernet | AES-256-GCM | Gain |
|---|---|---|---|
| Decrypt 8 MB | 48.7 ms (164 MB/s) | 12.1 ms (659 MB/s) | **4.0×** |
| Stored bytes | **1.3333×** | **1.000002×** | **−25.0%** |

Projected for a 64 MB file `[L]`: **584.8 ms today (12 × Fernet) → 389.8 ms (stop at k) → 97.1 ms (k × GCM)**. With the `cryptography` 45 pin, ~20 ms.

> The often-quoted 11.7× Fernet→GCM figure is measured at 1 MB buffers. At the 8 MB shards we actually use it is **4.0×**. Use 4.0×.

**Storage matters more than CPU here.** Removing base64 takes a 64 MiB file from 128 MiB stored to 96 MiB — 25% less on every peer and 25% less on every wire transfer.

**Migration is per-shard and independent**: read shard, Fernet-decrypt, re-frame, write. No RS re-run, no cross-shard coordination, no downtime. Dispatch on a `cipher` field; `whole-v1` files keep working. Run lazily on first read plus a background sweep.

### Phase 2 — Decode-free fast path
**Optimizes: read latency. Risk: low.**

Because the mapping is contiguous (§1.3), when all 8 data shards are local the file **is** their concatenation — verified by exact SHA-256 `[L]`. Today's path instead fetches 4 parity shards over the network, decrypts 12, spawns the decoder, and does three full-size disk round trips.

For an intact file the read becomes: decrypt 8 shards, concatenate, truncate to recorded size. This eliminates the peer fetch (~0.50 s), the RS decode (0.17 s `[C]`), and the staging round trip (0.11 s `[L]`).

Keep the decoder for the degraded case — it is correct and fast.

### Phase 3 — Kernel metadata caching
**Optimizes: metadata op latency, small-file read rate. Risk: bounded staleness.**

Set `entry_timeout = attr_timeout = 1.0` and push `invalidate_entry_async()` / `invalidate_inode()` for changes this node originates. Locally-originated changes stay instant; remote ones are bounded at 1 s. This is JuiceFS's shipped default and 3× more conservative than NFS `[cite]`.

Expected: the 618 → 12 round-trip reduction of §1.6, i.e. **540 ms → 8 ms on `ls -l`**, and direct relief for the worst measured numbers (office `stat` 263.8 ms, `readdir` 323.7 ms `[C]`).

Pair with **incremental tree updates** — apply the single-file delta instead of invalidating the whole cache at 6 sites. That removes the O(n²) of §1.5 on its own.

### Phase 4 — Range reads
**Optimizes: seek latency, read amplification. Risk: needs Phase 1 first.**

With framed AEAD in place, for file range `[a,b)` and `P = ceil(size/8)`:

```
for j in a//P .. (b-1)//P:                      # 1-2 shards for any read < P
    lo, hi = clamp range into shard j
    fetch shard j bytes [32 + f0*framesz, 32 + (f1+1)*framesz)   # ONE HTTP range request
    decrypt those frames, splice
```

Prototype, built against the real `lib/encoder`, every byte compared to source `[P]`:

| Operation (64 MiB file) | Today | Prototype | Gain |
|---|---|---|---|
| 4 KiB range read | 1423 ms | **0.13 ms** | 10,837× |
| 64 KiB range read | 1423 ms | **0.20 ms** | 7,053× |
| 1 MiB range read | 1423 ms | **1.69 ms** | 844× |
| 8 MiB range read | 1423 ms | **36.67 ms** | 39× |
| Full sequential read | 1423–1888 ms | **104 ms** (647 MB/s) | **13.7–18.2×** |
| Write | 43.3 MB/s | **127.7 MB/s** | 2.95× |
| Read I/O amplification | 8.5× | 2.0× | 4.3× |

Add ~9.1 ms per remote shard. A 1 MiB seek in a 256 MB file goes **7.2 s → ~11 ms**.

**Two blockers, both confirmed:**
- **Starlette 0.35.1 has no `Range` support in `FileResponse`** — verified directly in the running container: `Range`, `206`, and `Accept-Ranges` are all absent from the source. It landed in 0.39.0. Upgrade or hand-roll 206. (The comment at `api/main.py:1018` claiming otherwise has been corrected.)
- **`cfs_mount.py:519 read()` downloads the whole file and slices it**, and its `_read_cache` holds 32 *whole files* — 32 × 256 MB = **8 GB worst case**. Must become a ranged fetch with a byte-budgeted frame cache.

### Phase 5 — Metadata store
**Optimizes: listing, statfs, scale ceiling. Risk: low, additive.**

Replace glob+parse with SQLite (WAL). Measured `[L]`:

| Operation | JSON-per-file | SQLite | Gain |
|---|---|---|---|
| Full listing (20k) | 1,541 ms | **68.9 ms** | **22×** |
| Point stat by id | — | 9.1–24.1 µs | — |
| Insert rate | 4,869/s | 20,401–69,710/s | 4–14× |
| Bytes per file | 4,096 B allocated | **95 B** | **43×** |
| statfs at 200k | 3,015 ms+ | 23 ms → **O(1)** with a counter | — |

### Phase 6 — Small-file packing
**Optimizes: files/s and space amplification. Risk: highest; needs compaction.**

Files < 1 MiB append to an open **container**; the container is erasure-coded, not the file. Per-file metadata becomes `(container_id, offset, length)` — **95 B vs 2,736 B** `[L]`. **Reading a packed file is a range read into the container** — the same machinery as Phase 4, not a second mechanism.

Every system that solved this does exactly one thing: erasure-code the container, never the file — Haystack (100 GB volumes, 10 B/photo index vs 536 B per inode), f4 (1 GB blocks), SeaweedFS (30 GB volumes, 16 B index), Azure WAS (1 GB extents) `[cite]`.

Prototype on the exact measured tree (218 files, mean 5,659 B) `[P]`:

```
today [C]:  sonic 7.4 files/s | office 9.8 files/s
packed:     57 ms local work for 218 files      -> 3,801 files/s
            + 1 batched round trip              -> 3,280 files/s
            + per-file round trip (no batching) ->   107 files/s
space: 1.51x (vs 10.4x)   inodes: 13 for the whole batch (vs 3,052)
```

Realistic target **500–1,500 files/s**, bounded by FUSE per-op cost (~0.4 ms ⇒ ~2,500/s ceiling), not storage. That is **50–150×**. Phase 0 alone should reach ~100–150 files/s without packing.

Cost: containers need tombstones and a compactor. f4 reports 6.8% deleted-but-uncompacted space `[cite]`.

### Phase 7 — Encrypt before encode
**Optimizes: repair cost, crypto volume. Risk: architectural.**

Today the Go encoder runs first, then each of the 12 output shards is Fernet-encrypted (`api/main.py:313–367`). Because RS reconstruction is linear over GF(2⁸), encrypt-*then*-encode would let **any peer holding k shards regenerate a lost shard without the key**. Encode-then-encrypt destroys that: `_rebuild_file` must pull every shard to the origin, decrypt, decode, re-encode, re-encrypt, redistribute.

Also encrypts n=12 shards (96 MB) instead of k=8 (64 MB) of plaintext — **33% wasted crypto**.

Tahoe-LAFS and Storj both encrypt first; the canonical construction is Krawczyk's *Secret Sharing Made Short* (CRYPTO '93), hardened as AONT-RS (FAST '11) `[cite]`.

### Phase 8 — Striped layout (new writes only)
**Optimizes: streaming writes, parallel sequential reads, bounded memory.**

Cell 256 KiB, stripe 2 MiB (HDFS-EC's block-group layout with a smaller cell). Peer assignment stays per shard *index*, constant across stripes, so a shard remains one file per peer and metadata stays O(k+m) per file, not O(stripes). Buys 8-way parallel sequential reads, streaming writes, and per-stripe repair. Keep the 64 KiB AEAD frame inside the 256 KiB cell.

---

## 3. Do not do

**k+Δ hedged reads (read 9–12 shards, use the first 8).** Two independent reasons:

- Hu et al., ACM SoCC 2017, verbatim: *"such a scheme mainly benefits non-systematic codes … With systematic codes, always downloading k or more blocks is not efficient."* `[cite]` EC-Cache's headline result works because EC-Cache is non-systematic and always decodes — it has no fast path to protect. Once Phase 2 lands, we are in the opposite regime.
- Azure measured it *harmful* above ~4 MB objects: at 4 KB, 13-of-12 cut 305 → 151 ms; at 4 MB it went bandwidth-bound and the extra fragment competed with useful ones `[cite]`. Our shards are 8 MB — 2000× past that crossover.

Ceph ships `fast_read` **off by default** for the same reason `[cite]`.

**Threads for crypto.** pyca/cryptography holds the GIL — definitively, and not version-specifically. Measured `[L]`: `AESGCM.decrypt` scales **0.91× on 8 threads** at every buffer size from 64 KB to 32 MB, on both `cryptography` 41.0.7 and 45. Confirmed in source: zero `allow_threads`/`detach` calls in `backend/aead.rs` and `backend/ciphers.rs`, while `rsa.rs`/`ec.rs`/`kdf.rs` do use them. Contrast `hashlib`, which releases above 2048 B and scales 5.96×.

A process pool is worse: measured IPC ceiling **469 MB/s** round-tripping 8×8 MB through `ProcessPoolExecutor` `[L]` — *slower than doing it inline*. The correct parallelism unit for concurrent requests is `uvicorn --workers N`.

**AES-GCM-SIV.** 395 MB/s measured `[L]`, 7.8× slower than GCM, and absent from `cryptography` 41. Unnecessary once nonces are structurally unique.

**Convergent encryption.** Would enable dedup, but with shards on untrusted peers any holder becomes an oracle for confirmation-of-file attacks (Bellare–Keelveedhi–Ristenpart) `[cite]`.

**Optimizing the encoder subprocess.** Spawn is **1.29 ms**, ~1% of per-file cost `[L]`. Far behind serialized shard POSTs, the O(n²) tree refresh, and the 14-inodes-per-file layout. A resident daemon is worth ~80× on that component eventually, and it is still not worth doing first.

**Reed–Solomon generally.** Our Go path measures **9,732 MB/s** at 8+4 `[L]` — 65× faster than Backblaze's shipping Java coder (149 MB/s single-thread `[cite]`). A 2.5 GbE link needs **0.065 cores** of decode. RS is not the bottleneck now and will not become one.

---

## 4. Risks in the proposed changes

**Nonce reuse is the real danger of Phase 1.** Fernet generated a random IV; AES-GCM will not forgive a repeat — a repeated (key, nonce) leaks the plaintext XOR *and* the GHASH subkey, enabling forgery. Use a random 8-byte prefix per shard stored in the header plus a monotonic frame counter. **`_rebuild_file` re-encrypts every shard during repair and must draw a fresh prefix**, or repair becomes a nonce-reuse generator.

**Integrity granularity shifts.** Fernet authenticated the whole shard; frames authenticate independently. Per-frame tags do **not** detect truncation or rollback on their own — bind `file_id ‖ version ‖ shard_idx ‖ frame_idx` into the AAD, require a `final=1` frame before accepting a stream, and record the frame count in metadata. Keep the per-shard SHA-256 for attribution: `InvalidTag` does not say which peer misbehaved.

**Whole-shard digest verification breaks with ranged fetch** (`replication.fetch_shard`).

**Existing key handling has a silent-failure path worth fixing regardless.** `_load_fernet()` returns `None` on any exception, and every call site treats `fernet is None` as "store plaintext" (`api/main.py:358`). A corrupt key file therefore silently disables encryption on shards headed to untrusted peers. That must become a hard failure. It also `chmod 0o600`s inside `try/except OSError: pass`, so on a filesystem where chmod fails the key is silently world-readable.

**There is no version field to migrate from.** `ShardInfo` (`api/models.py:12`) carries only `encrypted: bool`. Add `cipher_suite` and `key_id` **before** they are needed — every production format has one (AWS ESDK version byte + algorithm ID, Tink 5-byte prefix, age's `age-encryption.org/v1` line) and it cannot be retrofitted onto shards already written.

**Load skew on hot ranges.** With a contiguous split, an MP4's `moov` atom always lands in shard 0 or 7, so those peers get hot. Phase 8's striping fixes it.

**Per-file erasure params are already load-bearing** — `layout`, `cipher`, `frame_size` and `cell_size` must join `data_shards`/`parity_shards` in metadata, or files written before a config change become unreadable. (We already hit this once: a 4+2 file decoded with 8+4 settings downloaded corrupted.)

---

## 5. Summary

| Phase | Optimizes | Expected | Confidence |
|---|---|---|---|
| 0 — four fixes | read + write latency | −2.9 s on 64 MB read; ~8× small-file write | measured, no format change |
| 1 — framed AEAD | read throughput, storage | 4.0× crypto; **−25% stored bytes** | measured in our container |
| 2 — decode-free fast path | read latency | −0.78 s on 64 MB read | verified by SHA-256 |
| 3 — kernel caching | metadata latency | 618 → 12 round trips; 540 → 8 ms | measured |
| 4 — range reads | seek latency | 7.2 s → 11 ms on a 1 MiB seek | prototype |
| 5 — metadata store | listing, scale | 22× listing; statfs O(n) → O(1) | measured |
| 6 — packing | small-file rate, space | 50–150× files/s; 10.4× → 1.51× space | prototype |
| 7 — encrypt-then-encode | repair cost | keyless peer repair; −33% crypto | architectural |
| 8 — striping | streaming, parallel reads | 8-way parallel sequential | design |

**The single highest-value change is Phase 1**, not for the 4.0× CPU but for the 25% reduction in bytes stored and transferred on every peer — which compounds with every node added.

**The read path's problem was never erasure coding.** It is crypto packaging (70%), decrypting 12 shards for an 8-shard job, and moving each byte 8.5 times.

---

## 6. Gaps found while measuring — all closed

- **`symlink` was not implemented in the mount.** `cp -r` of any real tree containing links failed with `Function not implemented`; the eval's `diff -r` reported `differs` for exactly those entries (3 of 597 on `/usr/share/doc/git`, while all 597 regular files round-tripped byte-identically). **Fixed**: a link is stored as an ordinary tiny file whose body is the target, tagged with `symlink` in metadata, so it erasure-codes, replicates and repairs like anything else; `readlink` answers from the tree without reconstructing shards. Verified on both nodes for relative, absolute and directory targets, and bidirectionally across the cluster.
- **The real-tree check used a dereferencing `diff -r`.** It follows a relative link out of the copied tree and reports a missing target — which says nothing about the filesystem under test. Control: the same copy onto plain ext4 fails the identical way. **Fixed**: compares links as links (`--no-dereference`) and reports the symlink count.
- **The reconciliation probe budget was unsound.** It polled with no per-probe bound inside a 30 s deadline, so one slow `stat` could consume the whole budget and be recorded as a propagation failure, and it never checked that the write it was timing succeeded. One run reported 5/5 sonic→office failures that did not reproduce (3/3 clean at 270 ms visible / 557 ms readable). **Fixed**: the write is checked, each probe is bounded, and the reason is reported.
- **Both test suites defaulted to port 8000.** The API suite *mutates* config and files; an unrelated service holding 8000 answers with its own 404s, which reads as "the API is gone". **Fixed**: both default to a scratch port 8021, so reaching a live node is now an explicit act. (Found the hard way — the old default briefly set `contracts.max_peers=37` on sonic; both nodes were restored to 32 / 1 TB.)
- **A cold-start test flake.** The section-title assertion used `allTextContents()`, which does not auto-wait, and the cards render after the first telemetry fetch. **Fixed** and confirmed across two cold starts.
- **Peer-hosted shards were never reclaimed when an origin node was rebuilt.** Shards held for a peer are filed under that peer's node id; a rebuilt peer returns with a *new* id, so everything under the old one becomes unclaimable — its origin will never ask for it and never delete it, but it still occupies the quota. Office's single OS reinstall left **1.24 GB (305 shard sets) under the dead id `bc230253`** on sonic, against 313 MB legitimately held for the live id. **Fixed**: `POST /api/repair {"purge_dead_origins": true}` reclaims them, and — like redundancy scanning — treats an unreachable peer as *unknown*, not dead: if any configured peer fails to answer, the sweep does nothing. Verified both ways (1.24 GB reclaimed with peers up; zero bytes touched with the peer stopped).
- **Unreadable files could not be retired.** `purge_orphans` only covers metadata with no shards at all. A file below `data_shards` still holds shards that no arrangement reconstructs, so it stayed in the namespace advertising something nothing could read, with its bytes unreclaimable. **Fixed** as a separate opt-in, since discarding surviving shards is irreversible in a way orphan cleanup is not.
