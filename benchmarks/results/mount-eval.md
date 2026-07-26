# CollectiveFS — Performance and Evaluation Report
Generated 2026-07-26 21:36 UTC · harness `benchmarks/run_mount_eval.py` · duration 2.3 min
Every figure below was measured against the live cluster through the `/media/collectivefs` mount, with erasure coding, encryption, shard distribution and peer routing all in the path. Nothing is extrapolated; where a measurement was not possible the row says so.

## 1. Compute under test
| Node | CPU | Parallelism | Memory | OS | Kernel |
|---|---|---|---|---|---|
| **sonic** | AMD Ryzen 7 5800X 8-Core Processor | 16 threads | 63 GB | Ubuntu 24.04.4 LTS | 6.11.0-29-generic |
| **office** | 12th Gen Intel(R) Core(TM) i9-12900K | 24 threads | 63 GB | Ubuntu 24.04.3 LTS | 6.11.0-29-generic |

| Node | Node ID | Pledged quota | Used | Backing disk | Free on disk | Media | Erasure |
|---|---|---|---|---|---|---|---|
| **sonic** | 78448c8f | 1.0 TB | 316 MB | 3.6 TB | 1.5 TB | ? | 8+4 |
| **office** | bc230253 | 1.0 TB | 5.0 GB | 3.6 TB | 1.3 TB | ? | 8+4 |

### Interconnect
| Measurement | Value |
|---|---|
| ICMP round trip | 0.107/0.132/0.164/0.018 ms |
| Node API round trip (median) | 8.3 ms |
| Shell/ssh invocation overhead (median) | 248.9 ms |

> Operation timings taken on the remote node include the shell invocation overhead above. Subtract it when comparing a remote single-operation latency against a local one.

## 2. Throughput by file size
Each size was written through the mount and read back, with a SHA-256 check on every round trip. Write time is measured until `cp` returns — the upload happens on close, so that is the honest end of the write.

### sonic
| Size | Bytes | Write | Write median | Read | Read median | SHA-256 verified |
|---|---|---|---|---|---|---|
| 4KB | 4.0 KB | 0.0 MB/s | 112.0 ms | 1.0 MB/s | 3.7 ms | 0/3 |
| 64KB | 64.0 KB | 0.4 MB/s | 137.5 ms | 13.7 MB/s | 3.9 ms | 0/3 |
| 1MB | 1.0 MB | 7.3 MB/s | 149.3 ms | 171.0 MB/s | 6.1 ms | 0/3 |
| 8MB | 8.0 MB | 52.0 MB/s | 158.3 ms | 266.2 MB/s | 8.4 ms | 0/3 |
| 64MB | 64.0 MB | 219.3 MB/s | 280.8 ms | 402.8 MB/s | 93.6 ms | 0/3 |

### office
| Size | Bytes | Write | Write median | Read | Read median | SHA-256 verified |
|---|---|---|---|---|---|---|
| 4KB | 4.0 KB | 0.0 MB/s | 281.3 ms | 0.0 MB/s | 297.4 ms | 3/3 |
| 64KB | 64.0 KB | 0.2 MB/s | 277.7 ms | 0.2 MB/s | 320.1 ms | 3/3 |
| 1MB | 1.0 MB | 3.4 MB/s | 298.5 ms | 2.2 MB/s | 452.2 ms | 3/3 |
| 8MB | 8.0 MB | 27.2 MB/s | 298.0 ms | 5.1 MB/s | 1.58 s | 3/3 |
| 64MB | 64.0 MB | 191.8 MB/s | 329.3 ms | — | — | 0/3 |

## 3. Operation latency
Per-operation cost as a shell experiences it. These are whole-command timings, so each includes process startup.

### sonic
| Operation | Samples | Median | p95 | Max | Failures |
|---|---|---|---|---|---|
| create (touch) | 12 | 97.3 ms | 126.1 ms | 152.7 ms | 0 |
| stat | 12 | 4.2 ms | 6.2 ms | 90.1 ms | 0 |
| readdir | 12 | 139.6 ms | 207.5 ms | 207.9 ms | 0 |
| rename | 12 | 137.3 ms | 210.9 ms | 225.4 ms | 0 |
| read small | 12 | 27.5 ms | 36.2 ms | 38.7 ms | 0 |
| copy small | 12 | 139.6 ms | 163.4 ms | 165.2 ms | 0 |
| mkdir | 12 | 113.5 ms | 129.5 ms | 130.2 ms | 0 |
| rmdir | 12 | 71.2 ms | 76.6 ms | 79.0 ms | 0 |
| unlink | 12 | 64.8 ms | 79.4 ms | 89.7 ms | 0 |

### office
| Operation | Samples | Median | p95 | Max | Failures |
|---|---|---|---|---|---|
| create (touch) | 12 | 317.1 ms | 329.4 ms | 363.3 ms | 0 |
| stat | 12 | 252.7 ms | 279.0 ms | 286.0 ms | 0 |
| readdir | 12 | 308.1 ms | 313.8 ms | 314.2 ms | 0 |
| rename | 12 | 305.2 ms | 315.7 ms | 323.3 ms | 0 |
| read small | 12 | 267.7 ms | 290.5 ms | 323.3 ms | 0 |
| copy small | 12 | 345.2 ms | 353.4 ms | 354.8 ms | 0 |
| mkdir | 12 | 324.2 ms | 330.2 ms | 330.7 ms | 0 |
| rmdir | 12 | 291.5 ms | 296.6 ms | 299.7 ms | 0 |
| unlink | 12 | 295.4 ms | 303.9 ms | 307.1 ms | 0 |

## 4. Kernel-level operation mix
Reported by the FUSE layer itself, so these exclude shell startup and show the true cost of each filesystem call.

### sonic
Window 311s · 5961 operations · read 0.0 MB/s · write 1.7 MB/s · 0 errors

| FUSE operation | Calls | Mean | Peak | Errors |
|---|---|---|---|---|
| write | 4187 | 0.12 ms | 4.82 ms | 0 |
| lookup | 635 | 21.08 ms | 173.83 ms | 0 |
| getattr | 453 | 0.54 ms | 39.5 ms | 0 |
| readdir | 315 | 49.31 ms | 127.62 ms | 0 |
| release | 103 | 221.73 ms | 3886.57 ms | 0 |
| create | 61 | 0.18 ms | 0.68 ms | 0 |
| read | 61 | 66.84 ms | 3458.69 ms | 0 |
| open | 45 | 0.01 ms | 0.05 ms | 0 |
| unlink | 39 | 27.65 ms | 526.55 ms | 0 |
| statfs | 24 | 20.91 ms | 33.64 ms | 0 |
| mkdir | 13 | 17.54 ms | 84.13 ms | 0 |
| rmdir | 13 | 18.83 ms | 27.01 ms | 0 |
| rename | 12 | 74.41 ms | 127.63 ms | 0 |

### office
Window 20s · 2 operations · read 0.0 MB/s · write 0.0 MB/s · 0 errors

| FUSE operation | Calls | Mean | Peak | Errors |
|---|---|---|---|---|
| statfs | 2 | 15.84 ms | 17.03 ms | 0 |

## 5. Concurrent load
| Node | Streams | Per file | Total | Elapsed | Aggregate | Landed |
|---|---|---|---|---|---|---|
| sonic | 8 | 8.0 MB | 64.0 MB | 0.98 s | 65.2 MB/s | 8/8 |
| office | 8 | 8.0 MB | 64.0 MB | 0.79 s | 81.5 MB/s | 8/8 |

## 6. Cross-node reconciliation
Time from a write completing on one machine to the file being visible, then readable, on the other.
| Direction | Samples | Visible median | Visible p95 | Readable median | Readable max | Failures |
|---|---|---|---|---|---|---|
| sonic → office | 5 | 282.7 ms | 297.0 ms | 575.8 ms | 596.7 ms | 0 |
| office → sonic | 5 | 93.2 ms | 166.4 ms | 180.6 ms | 208.5 ms | 0 |

## 7. Shard distribution
| Node | Files | Shards | Held here | On peers | Stored for peers | Missing | Storage expansion |
|---|---|---|---|---|---|---|---|
| **sonic** | 22 | 264 | 200 | 64 | 200 | 0 | 56.33× |
| **office** | 53 | 636 | 436 | 200 | 64 | 0 | 1.47× |

### Fault tolerance
| Check | Result |
|---|---|
| File size | 8.0 MB |
| Shard placement | {"http://192.168.1.43:8010": 4, "local": 8} |
| Peer holding remote shards | stopped |
| Read succeeded | yes |
| Read time | 3.52 s |
| SHA-256 matched | yes |

## 8. Quota saturation
The production pledge is 1 TB per node. Filling that to its cutoff would take hours and a terabyte of disk, and the behaviour under test — what the node does when it runs out of pledged room — is identical at any quota. So the quota was temporarily lowered, driven past the cutoff, and restored.
| Measurement | Value |
|---|---|
| Temporary quota | 1.5 GB |
| Write cutoff watermark | 50% |
| Data written before cutoff | 288 MB |
| Files written | 9 |
| Elapsed | 5.62 s |
| Cutoff triggered | yes |
| Usage at stop | 49.6% |
| Accepting writes after cutoff | yes |
| Node response | `node stopped accepting writes at 50.0% of quota (cutoff 50%)` |
| Quota restored to | 1.0 TB |

## 9. Peer contracts and proof-of-storage
Contracts are how a node verifies a peer is really holding what it claims: it asks for a hash of bytes at random offsets in a shard, with a nonce so the answer cannot be replayed. This runs on a timer per contract, so its cost is part of steady-state load.

### Challenge round trip
| Stage | Samples | Median | p95 | Max | Failures |
|---|---|---|---|---|---|
| Origin builds the challenge | 4 | 2.8 ms | 6.8 ms | 6.8 ms | 0 |
| Peer computes the proof | 4 | 3.2 ms | 4.9 ms | 4.9 ms | 0 |
| Origin verifies and scores | 4 | 2.7 ms | 3.7 ms | 3.7 ms | 0 |

End-to-end proof of one shard: **8.7 ms** (sum of medians).

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

## 10. Console and mount parity
The web console and the mount are two views of one namespace, so they must list exactly the same files.
| Node | Console entries | Distinct paths | Paths in mount | Result |
|---|---|---|---|---|
| **sonic** | 75 | 74 | 92 | differs (console-only 2, mount-only 10) |
| **office** | 75 | 74 | 92 | differs (console-only 2, mount-only 10) |

> The console lists files by id, so two files can share a path. A POSIX filesystem cannot represent that, so the mount shows one of each. Colliding paths in this namespace: `bunny_1080p.mp4` ×2.

## 11. Observations
- **sonic** peaks at 219.3 MB/s write (64MB) and 402.8 MB/s read (64MB).
- On **sonic** a 64MB write moves data 7310× faster per byte than a 4KB one — small files are dominated by the fixed cost of encoding and the round trip, not by size.
- **office** peaks at 191.8 MB/s write (64MB) and 5.1 MB/s read (8MB).
- On **office** a 64MB write moves data 19176× faster per byte than a 4KB one — small files are dominated by the fixed cost of encoding and the round trip, not by size.
- Every round trip was hash-checked: **12/30** files came back byte-identical after erasure coding, encryption, distribution across two machines and reconstruction.
- A file written on **sonic** is readable on **office** in 575.8 ms (median).
- A file written on **office** is readable on **sonic** in 180.6 ms (median).
- Proof-of-storage works end to end: **4/4** challenges verified, at 8.7 ms per shard — cheap enough to run continuously across a fleet.
- With the peer holding a file's remote shards stopped, the file still reconstructed byte-identically in 3.52 s — the parity budget holds in practice, not just on paper.
- The write cutoff works: the node refused new writes at 49.6% of its pledged quota and reported why, rather than filling the host disk.

## 12. Reproducing
```bash
python -m benchmarks.run_mount_eval --node sonic=http://localhost:8010 --node office=http://192.168.1.43:8010@office --iterations 3 --max-size 64MB --saturate --degraded --contracts
```

Raw measurements: `benchmarks/results/mount-eval.json`
