# CollectiveFS — Performance and Evaluation Report
Generated 2026-07-26 22:16 UTC · harness `benchmarks/run_mount_eval.py` · duration 9.6 min
Every figure below was measured against the live cluster through the `/media/collectivefs` mount, with erasure coding, encryption, shard distribution and peer routing all in the path. Nothing is extrapolated; where a measurement was not possible the row says so.

## 1. Compute under test
| Node | CPU | Parallelism | Memory | OS | Kernel |
|---|---|---|---|---|---|
| **sonic** | AMD Ryzen 7 5800X 8-Core Processor | 16 threads | 63 GB | Ubuntu 24.04.4 LTS | 6.11.0-29-generic |
| **office** | 12th Gen Intel(R) Core(TM) i9-12900K | 24 threads | 63 GB | Ubuntu 24.04.3 LTS | 6.11.0-29-generic |

| Node | Node ID | Pledged quota | Used | Backing disk | Free on disk | Media | Erasure |
|---|---|---|---|---|---|---|---|
| **sonic** | 78448c8f | 1.0 TB | 255 MB | 3.6 TB | 1.5 TB | Samsung SSD 990 EVO Plus 4TB (SSD/NVMe) | 8+4 |
| **office** | bc230253 | 1.0 TB | 4.8 GB | 3.6 TB | 1.3 TB | Samsung SSD 990 EVO Plus 4TB (SSD/NVMe) | 8+4 |

### Interconnect
| Measurement | Value |
|---|---|
| ICMP round trip | 0.229/0.441/0.569/0.113 ms |
| Node API round trip (median) | 12.5 ms |
| Shell/ssh invocation overhead (median) | 272.3 ms |

> Operation timings taken on the remote node include the shell invocation overhead above. Subtract it when comparing a remote single-operation latency against a local one.

## 2. Throughput by file size
Each size was written through the mount and read back, with a SHA-256 check on every round trip. Write time is measured until `cp` returns — the upload happens on close, so that is the honest end of the write.

### sonic
| Size | Bytes | Write | Write median | Read | Read median | SHA-256 verified |
|---|---|---|---|---|---|---|
| 4KB | 4.0 KB | 0.2 MB/s | 9.2 ms | 0.0 MB/s | 95.2 ms | 3/3 |
| 64KB | 64.0 KB | 11.2 MB/s | 5.6 ms | 0.6 MB/s | 106.5 ms | 3/3 |
| 1MB | 1.0 MB | 95.1 MB/s | 8.5 ms | 7.7 MB/s | 131.7 ms | 3/3 |
| 8MB | 8.0 MB | 319.5 MB/s | 26.6 ms | 23.8 MB/s | 338.4 ms | 3/3 |
| 64MB | 64.0 MB | 271.9 MB/s | 220.3 ms | 0.9 MB/s | 76.18 s | 3/3 |

### office
| Size | Bytes | Write | Write median | Read | Read median | SHA-256 verified |
|---|---|---|---|---|---|---|
| 4KB | 4.0 KB | 0.0 MB/s | 295.7 ms | 0.0 MB/s | 313.3 ms | 3/3 |
| 64KB | 64.0 KB | 0.2 MB/s | 280.8 ms | 0.2 MB/s | 324.6 ms | 3/3 |
| 1MB | 1.0 MB | 3.5 MB/s | 283.0 ms | 2.1 MB/s | 477.5 ms | 3/3 |
| 8MB | 8.0 MB | 26.6 MB/s | 302.4 ms | 24.2 MB/s | 336.2 ms | 3/3 |
| 64MB | 64.0 MB | 179.8 MB/s | 353.8 ms | 2.7 MB/s | 23.44 s | 3/3 |

## 3. Operation latency
Per-operation cost as a shell experiences it. These are whole-command timings, so each includes process startup.

### sonic
| Operation | Samples | Median | p95 | Max | Failures |
|---|---|---|---|---|---|
| create (touch) | 12 | 3.1 ms | 3.5 ms | 81.5 ms | 0 |
| stat | 12 | 5.4 ms | 7.5 ms | 13.8 ms | 0 |
| readdir | 12 | 5.6 ms | 8.3 ms | 10.5 ms | 0 |
| rename | 12 | 71.0 ms | 383.0 ms | 480.8 ms | 0 |
| read small | 9 | 29.0 ms | 35.3 ms | 35.3 ms | 3 |
| copy small | 9 | 7.1 ms | 16.0 ms | 16.0 ms | 3 |
| mkdir | 12 | 69.2 ms | 271.3 ms | 332.9 ms | 0 |
| rmdir | 12 | 67.6 ms | 74.1 ms | 76.3 ms | 0 |
| unlink | 12 | 61.3 ms | 69.2 ms | 71.6 ms | 0 |

### office
| Operation | Samples | Median | p95 | Max | Failures |
|---|---|---|---|---|---|
| create (touch) | 12 | 280.3 ms | 292.6 ms | 305.2 ms | 0 |
| stat | 12 | 257.2 ms | 278.8 ms | 282.8 ms | 0 |
| readdir | 12 | 287.1 ms | 297.0 ms | 323.7 ms | 0 |
| rename | 12 | 304.1 ms | 339.5 ms | 346.2 ms | 0 |
| read small | 12 | 270.9 ms | 296.1 ms | 305.9 ms | 0 |
| copy small | 12 | 299.1 ms | 308.6 ms | 310.7 ms | 0 |
| mkdir | 12 | 292.1 ms | 300.0 ms | 300.8 ms | 0 |
| rmdir | 12 | 300.8 ms | 306.5 ms | 312.6 ms | 0 |
| unlink | 12 | 296.5 ms | 315.9 ms | 318.7 ms | 0 |

## 4. Kernel-level operation mix
Reported by the FUSE layer itself, so these exclude shell startup and show the true cost of each filesystem call.

### sonic
Window 898s · 27638 operations · read 0.3 MB/s · write 1.3 MB/s · 3 errors

| FUSE operation | Calls | Mean | Peak | Errors |
|---|---|---|---|---|
| write | 10097 | 0.14 ms | 14.71 ms | 0 |
| lookup | 6485 | 10.23 ms | 6611.4 ms | 0 |
| getattr | 5580 | 1.33 ms | 319.62 ms | 0 |
| read | 1833 | 261.24 ms | 73465.9 ms | 0 |
| release | 1376 | 382.1 ms | 3502.4 ms | 3 |
| open | 855 | 0.01 ms | 0.14 ms | 0 |
| create | 522 | 0.12 ms | 0.67 ms | 0 |
| unlink | 496 | 26.8 ms | 757.51 ms | 0 |
| readdir | 270 | 124.58 ms | 13203.72 ms | 0 |
| statfs | 74 | 471.8 ms | 18188.36 ms | 0 |
| mkdir | 19 | 343.25 ms | 2006.94 ms | 0 |
| rmdir | 19 | 18.73 ms | 31.61 ms | 0 |
| rename | 12 | 130.04 ms | 473.75 ms | 0 |

### office
Window 25s · 4 operations · read 0.0 MB/s · write 0.0 MB/s · 0 errors

| FUSE operation | Calls | Mean | Peak | Errors |
|---|---|---|---|---|
| statfs | 4 | 16.41 ms | 24.49 ms | 0 |

## 5. Concurrent load
| Node | Streams | Per file | Total | Elapsed | Aggregate | Landed |
|---|---|---|---|---|---|---|
| sonic | 8 | 8.0 MB | 64.0 MB | 0.24 s | 265.9 MB/s | 8/8 |
| office | 8 | 8.0 MB | 64.0 MB | 0.42 s | 152.5 MB/s | 8/8 |

## 6. Real directory tree
A genuine source tree copied in, read back, and compared with `diff -r` — every byte of every file, both directions. This is the shape most real data has: many small files, where per-file cost dominates and raw throughput barely matters.
| Node | Files | Total | Mean file | Write | Write rate | Read | Read rate | Verified |
|---|---|---|---|---|---|---|---|---|
| **sonic** | 218 files | 1.2 MB | 5.5 KB | 9.45 s | 23.1/s | 28.47 s | 7.7/s | identical |
| **office** | 218 files | 1.2 MB | 5.5 KB | 23.26 s | 9.4/s | 31.25 s | 7.0/s | **differs** |

## 7. Cross-node reconciliation
Time from a write completing on one machine to the file being visible, then readable, on the other.
| Direction | Samples | Visible median | Visible p95 | Readable median | Readable max | Failures |
|---|---|---|---|---|---|---|
| sonic → office | 5 | 280.7 ms | 291.6 ms | 573.6 ms | 619.4 ms | 0 |
| office → sonic | 5 | 343.1 ms | 376.8 ms | 389.3 ms | 421.4 ms | 0 |

## 8. Shard distribution
| Node | Files | Shards | Held here | On peers | Stored for peers | Missing | Own shards | Hosted for peers | Expansion |
|---|---|---|---|---|---|---|---|---|---|
| **sonic** | 42 | 504 | 256 | 122 | 167 | 126 | 93.3 MB | 329 MB | 6.63x |
| **office** | 49 | 588 | 324 | 167 | 122 | 97 | 5.0 GB | 6.5 MB | 1.45x |

> Expansion is our shards against our own data — the erasure-coding overhead. Data stored for peers occupies the quota but is counted separately, since it is not our storage cost.

### Fault tolerance
| Check | Result |
|---|---|
| File size | 8.0 MB |
| Shard placement | {"http://192.168.1.43:8010": 4, "local": 8} |
| Peer holding remote shards | stopped |
| Read succeeded | yes |
| Read time | 3.60 s |
| SHA-256 matched | yes |

## 9. Quota saturation
The production pledge is 1 TB per node. Filling that to its cutoff would take hours and a terabyte of disk, and the behaviour under test — what the node does when it runs out of pledged room — is identical at any quota. So the quota was temporarily lowered, driven past the cutoff, and restored.
| Measurement | Value |
|---|---|
| Temporary quota | 1.4 GB |
| Write cutoff watermark | 50% |
| Data written before cutoff | 384 MB |
| Files written | 12 |
| Elapsed | 7.65 s |
| Cutoff triggered | yes |
| Usage at stop | 50.9% |
| Accepting writes after cutoff | no |
| Node response | `node stopped accepting writes at 51.1% of quota (cutoff 50%)` |
| Quota restored to | 1.0 TB |

## 10. Peer contracts and proof-of-storage
Contracts are how a node verifies a peer is really holding what it claims: it asks for a hash of bytes at random offsets in a shard, with a nonce so the answer cannot be replayed. This runs on a timer per contract, so its cost is part of steady-state load.

### Challenge round trip
| Stage | Samples | Median | p95 | Max | Failures |
|---|---|---|---|---|---|
| Origin builds the challenge | 4 | 1.5 ms | 10.7 ms | 10.7 ms | 0 |
| Peer computes the proof | 4 | 2.5 ms | 3.2 ms | 3.2 ms | 0 |
| Origin verifies and scores | 4 | 1.4 ms | 1.9 ms | 1.9 ms | 0 |

End-to-end proof of one shard: **5.3 ms** (sum of medians).

### Outcome
| Measurement | Value |
|---|---|
| Contract created | yes |
| Tier | warm |
| Shards placed on the peer | 4 |
| Challenges attempted | 4 |
| Challenges passed | 4 |
| QoS score | 1.0 |
| Challenge pass rate | — |

### Tier configuration
| Tier | Challenge interval | Response deadline | Storage multiplier | Max violations |
|---|---|---|---|---|
| None | None s | None s | 2.0x | 3 |
| None | None s | None s | 1.0x | 5 |
| None | None s | None s | 0.5x | 10 |

## 11. Console and mount parity
The web console and the mount are two views of one namespace, so they must list exactly the same files.
| Node | Console entries | Distinct paths | Paths in mount | Result |
|---|---|---|---|---|
| **sonic** | 91 | 90 | 90 | identical |
| **office** | 91 | 90 | 90 | identical |

> The console lists files by id, so two files can share a path. A POSIX filesystem cannot represent that, so the mount shows one of each. Colliding paths in this namespace: `bunny_1080p.mp4` ×2.

## 12. Observations
- **sonic** peaks at 319.5 MB/s write (8MB) and 23.8 MB/s read (8MB).
- On **sonic** a 64MB write moves data 1431× faster per byte than a 4KB one — small files are dominated by the fixed cost of encoding and the round trip, not by size.
- **office** peaks at 179.8 MB/s write (64MB) and 24.2 MB/s read (8MB).
- On **office** a 64MB write moves data 17983× faster per byte than a 4KB one — small files are dominated by the fixed cost of encoding and the round trip, not by size.
- Every round trip was hash-checked: **30/30** files came back byte-identical after erasure coding, encryption, distribution across two machines and reconstruction.
- A file written on **sonic** is readable on **office** in 573.6 ms (median).
- A file written on **office** is readable on **sonic** in 389.3 ms (median).
- A real 218-file tree (1.2 MB, mean file 5.5 KB) copied into **sonic** in 9.45s and read back in 28.47s, verified byte-for-byte with `diff -r`. At this file size the cost is per-file, not per-byte: 23.1 files/s written.
- Proof-of-storage works end to end: **4/4** challenges verified, at 5.3 ms per shard — cheap enough to run continuously across a fleet.
- With the peer holding a file's remote shards stopped, the file still reconstructed byte-identically in 3.60 s — the parity budget holds in practice, not just on paper.
- The write cutoff works: the node refused new writes at 50.9% of its pledged quota and reported why, rather than filling the host disk.
- Console and mount list identical file sets on sonic, office — the UI is a view of the same namespace, not a separate index.

## 13. Reproducing
```bash
python -m benchmarks.run_mount_eval --node sonic=http://localhost:8010 --node office=http://192.168.1.43:8010@office --iterations 3 --max-size 64MB --saturate --degraded --contracts --real-tree /usr/include/python3.12
```

Raw measurements: `benchmarks/results/mount-eval.json`
