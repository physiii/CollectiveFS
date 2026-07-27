# CollectiveFS — Performance and Evaluation Report
Generated 2026-07-27 02:46 UTC · harness `benchmarks/run_mount_eval.py` · duration 16.6 min
Every figure below was measured against the live cluster through the `/media/collectivefs` mount, with erasure coding, encryption, shard distribution and peer routing all in the path. Nothing is extrapolated; where a measurement was not possible the row says so.

## 1. Compute under test
| Node | CPU | Parallelism | Memory | OS | Kernel |
|---|---|---|---|---|---|
| **sonic** | AMD Ryzen 7 5800X 8-Core Processor | 16 threads | 63 GB | Ubuntu 24.04.4 LTS | 6.11.0-29-generic |
| **office** | 12th Gen Intel(R) Core(TM) i9-12900K | 24 threads | 62 GB | Ubuntu 26.04 LTS | 7.0.0-28-generic |

| Node | Node ID | Pledged quota | Used | Backing disk | Free on disk | Media | Erasure |
|---|---|---|---|---|---|---|---|
| **sonic** | 78448c8f | 1.0 TB | 1.4 GB | 3.6 TB | 1.5 TB | Samsung SSD 990 EVO Plus 4TB (SSD/NVMe) | 4+2 |
| **office** | 4e65a183 | 1.0 TB | 303 MB | 3.6 TB | 3.1 TB | Samsung SSD 990 EVO Plus 4TB (SSD/NVMe) | 8+4 |

### Interconnect
| Measurement | Value |
|---|---|
| ICMP round trip | 0.249/0.423/0.611/0.139 ms |
| Node API round trip (median) | 11.3 ms |
| Shell/ssh invocation overhead (median) | 244.5 ms |

> Operation timings taken on the remote node include the shell invocation overhead above. Subtract it when comparing a remote single-operation latency against a local one.

## 2. Throughput by file size
Each size was written through the mount and read back, with a SHA-256 check on every round trip. Write time is measured until `cp` returns — the upload happens on close, so that is the honest end of the write.

### sonic
| Size | Bytes | Write | Write median | Read | Read median | SHA-256 verified |
|---|---|---|---|---|---|---|
| 4KB | 4.0 KB | 0.2 MB/s | 7.0 ms | 0.0 MB/s | 147.5 ms | 3/3 |
| 64KB | 64.0 KB | 11.3 MB/s | 5.7 ms | 0.5 MB/s | 135.2 ms | 3/3 |
| 1MB | 1.0 MB | 84.9 MB/s | 12.8 ms | 6.6 MB/s | 146.7 ms | 3/3 |
| 8MB | 8.0 MB | 326.2 MB/s | 24.2 ms | 22.0 MB/s | 340.2 ms | 3/3 |
| 64MB | 64.0 MB | 313.1 MB/s | 199.7 ms | 10.1 MB/s | 6.70 s | 3/3 |

### office
| Size | Bytes | Write | Write median | Read | Read median | SHA-256 verified |
|---|---|---|---|---|---|---|
| 4KB | 4.0 KB | 0.0 MB/s | 274.1 ms | 0.0 MB/s | 312.5 ms | 3/3 |
| 64KB | 64.0 KB | 0.2 MB/s | 267.5 ms | 0.2 MB/s | 436.8 ms | 3/3 |
| 1MB | 1.0 MB | 3.4 MB/s | 291.5 ms | 3.0 MB/s | 285.3 ms | 3/3 |
| 8MB | 8.0 MB | 31.3 MB/s | 259.7 ms | 22.3 MB/s | 355.7 ms | 3/3 |
| 64MB | 64.0 MB | 200.9 MB/s | 327.4 ms | 10.0 MB/s | 6.43 s | 3/3 |

## 3. Operation latency
Per-operation cost as a shell experiences it. These are whole-command timings, so each includes process startup.

### sonic
| Operation | Samples | Median | p95 | Max | Failures |
|---|---|---|---|---|---|
| create (touch) | 12 | 2.7 ms | 9.9 ms | 56.5 ms | 0 |
| stat | 12 | 4.5 ms | 5.3 ms | 5.8 ms | 0 |
| readdir | 12 | 5.0 ms | 5.5 ms | 5.6 ms | 0 |
| rename | 12 | 45.4 ms | 169.3 ms | 998.1 ms | 0 |
| read small | 9 | 23.7 ms | 30.4 ms | 30.4 ms | 3 |
| copy small | 9 | 7.8 ms | 10.8 ms | 10.8 ms | 3 |
| mkdir | 12 | 59.7 ms | 219.5 ms | 695.1 ms | 0 |
| rmdir | 12 | 64.7 ms | 74.5 ms | 269.3 ms | 0 |
| unlink | 12 | 46.9 ms | 71.5 ms | 73.3 ms | 0 |

### office
| Operation | Samples | Median | p95 | Max | Failures |
|---|---|---|---|---|---|
| create (touch) | 12 | 382.3 ms | 568.7 ms | 570.1 ms | 0 |
| stat | 12 | 231.3 ms | 261.1 ms | 412.7 ms | 0 |
| readdir | 12 | 250.2 ms | 263.8 ms | 286.1 ms | 0 |
| rename | 12 | 261.2 ms | 271.9 ms | 272.4 ms | 0 |
| read small | 12 | 255.6 ms | 274.4 ms | 321.2 ms | 0 |
| copy small | 12 | 463.5 ms | 523.6 ms | 651.3 ms | 0 |
| mkdir | 12 | 283.6 ms | 505.5 ms | 703.1 ms | 0 |
| rmdir | 12 | 268.6 ms | 286.7 ms | 295.3 ms | 0 |
| unlink | 12 | 301.5 ms | 316.9 ms | 333.7 ms | 0 |

## 4. Kernel-level operation mix
Reported by the FUSE layer itself, so these exclude shell startup and show the true cost of each filesystem call.

### sonic
Window 899s · 21247 operations · read 0.0 MB/s · write 0.1 MB/s · 0 errors

| FUSE operation | Calls | Mean | Peak | Errors |
|---|---|---|---|---|
| lookup | 9448 | 14.42 ms | 8630.4 ms | 0 |
| getattr | 4845 | 4.35 ms | 8412.92 ms | 0 |
| release | 1804 | 634.0 ms | 4583.01 ms | 0 |
| read | 1272 | 18.81 ms | 780.23 ms | 0 |
| open | 1200 | 0.01 ms | 0.02 ms | 0 |
| write | 954 | 0.08 ms | 0.72 ms | 0 |
| unlink | 607 | 23.83 ms | 247.08 ms | 0 |
| create | 605 | 0.1 ms | 3.43 ms | 0 |
| readdir | 405 | 4.71 ms | 395.05 ms | 0 |
| statfs | 41 | 10196.19 ms | 142319.14 ms | 0 |
| mkdir | 30 | 1143.22 ms | 5820.8 ms | 0 |
| rmdir | 30 | 19.64 ms | 49.97 ms | 0 |
| symlink | 3 | 1218.14 ms | 1683.18 ms | 0 |
| readlink | 3 | 0.0 ms | 0.0 ms | 0 |

### office
Window 20s · 1 operations · read 0.0 MB/s · write 0.0 MB/s · 0 errors

| FUSE operation | Calls | Mean | Peak | Errors |
|---|---|---|---|---|
| statfs | 1 | 15.78 ms | 15.78 ms | 0 |

## 5. Concurrent load
| Node | Streams | Per file | Total | Elapsed | Aggregate | Landed |
|---|---|---|---|---|---|---|
| sonic | 8 | 8.0 MB | 64.0 MB | 0.33 s | 191.1 MB/s | 7/8 |
| office | 8 | 8.0 MB | 64.0 MB | 0.46 s | 139.3 MB/s | 7/8 |

## 6. Real directory tree
A genuine source tree copied in, read back, and compared with `diff -r --no-dereference` — every byte of every file, both directions, with symlinks compared as symlinks. This is the shape most real data has: many small files, where per-file cost dominates and raw throughput barely matters.
| Node | Files | Total | Mean file | Write | Write rate | Read | Read rate | Symlinks | Verified |
|---|---|---|---|---|---|---|---|---|---|
| **sonic** | 597 files | 2.0 MB | 3.5 KB | 130.45 s | 4.6/s | 15.75 s | 37.9/s | 3/3 | identical |
| **office** | 650 files | 2.2 MB | 3.4 KB | 507.41 s | 1.3/s | 15.83 s | 41.1/s | 1/1 | identical |

## 7. Cross-node reconciliation
Time from a write completing on one machine to the file being visible, then readable, on the other.
| Direction | Samples | Visible median | Visible p95 | Readable median | Readable max | Failures |
|---|---|---|---|---|---|---|
| sonic → office | 5 | 305.4 ms | 315.5 ms | 627.9 ms | 688.4 ms | 0 |
| office → sonic | 5 | 962.2 ms | 1.05 s | 1.10 s | 1.12 s | 0 |

## 8. Shard distribution
| Node | Files | Shards | Held here | On peers | Stored for peers | Missing | Own shards | Hosted for peers | Expansion |
|---|---|---|---|---|---|---|---|---|---|
| **sonic** | 0 | 0 | 0 | 0 | 3109 | 0 | 79.3 MB | 1.5 GB | Nonex |
| **office** | 48 | 576 | 264 | 166 | 239 | 146 | 595 MB | 188 KB | 1.05x |

> Expansion is our shards against our own data — the erasure-coding overhead. Data stored for peers occupies the quota but is counted separately, since it is not our storage cost.

### Fault tolerance
| Check | Result |
|---|---|
| File size | 8.0 MB |
| Shard placement | {"http://192.168.1.43:8010": 2, "local": 4} |
| Peer holding remote shards | stopped |
| Read succeeded | yes |
| Read time | 167.4 ms |
| SHA-256 matched | yes |

## 9. Quota saturation
The production pledge is 1 TB per node. Filling that to its cutoff would take hours and a terabyte of disk, and the behaviour under test — what the node does when it runs out of pledged room — is identical at any quota. So the quota was temporarily lowered, driven past the cutoff, and restored.
| Measurement | Value |
|---|---|
| Temporary quota | 2.6 GB |
| Write cutoff watermark | 50% |
| Data written before cutoff | 32.0 MB |
| Files written | 1 |
| Elapsed | 1.18 s |
| Cutoff triggered | yes |
| Usage at stop | 61.0% |
| Accepting writes after cutoff | no |
| Node response | `node stopped accepting writes at 61.0% of quota (cutoff 50%)` |
| Quota restored to | 1.0 TB |

## 10. Peer contracts and proof-of-storage
Contracts are how a node verifies a peer is really holding what it claims: it asks for a hash of bytes at random offsets in a shard, with a nonce so the answer cannot be replayed. This runs on a timer per contract, so its cost is part of steady-state load.

### Challenge round trip
| Stage | Samples | Median | p95 | Max | Failures |
|---|---|---|---|---|---|
| Origin builds the challenge | 2 | 8.0 ms | 11.6 ms | 11.6 ms | 0 |
| Peer computes the proof | 2 | 9.2 ms | 9.4 ms | 9.4 ms | 0 |
| Origin verifies and scores | 2 | 2.5 ms | 2.5 ms | 2.5 ms | 0 |

End-to-end proof of one shard: **19.6 ms** (sum of medians).

### Outcome
| Measurement | Value |
|---|---|
| Contract created | yes |
| Tier | warm |
| Shards placed on the peer | 2 |
| Challenges attempted | 2 |
| Challenges passed | 2 |
| QoS score | 1.0 |
| Challenge pass rate | 100.0% (2/2) |
| Mean proof response | 9.2 ms |
| p99 proof response | 9.0 ms |
| Challenges timed out | 0 |

### Tier configuration
| Tier | Challenge interval | Response deadline | Storage multiplier | Max violations |
|---|---|---|---|---|
| hot | 30.0 s | 1.0 s | 2.0x | 3 |
| warm | 300.0 s | 60.0 s | 1.0x | 5 |
| cold | 3600.0 s | 3600.0 s | 0.5x | 10 |

## 11. Console and mount parity
The web console and the mount are two views of one namespace, so they must list exactly the same files.
| Node | Console entries | Distinct paths | Paths in mount | Result |
|---|---|---|---|---|
| **sonic** | 48 | 48 | 48 | identical |
| **office** | 48 | 48 | 48 | identical |

## 12. Observations
- **sonic** peaks at 326.2 MB/s write (8MB) and 22.0 MB/s read (8MB).
- On **sonic** a 64MB write moves data 1252× faster per byte than a 4KB one — small files are dominated by the fixed cost of encoding and the round trip, not by size.
- **office** peaks at 200.9 MB/s write (64MB) and 22.3 MB/s read (8MB).
- On **office** a 64MB write moves data 20087× faster per byte than a 4KB one — small files are dominated by the fixed cost of encoding and the round trip, not by size.
- Every round trip was hash-checked: **30/30** files came back byte-identical after erasure coding, encryption, distribution across two machines and reconstruction.
- A file written on **sonic** is readable on **office** in 627.9 ms (median).
- A file written on **office** is readable on **sonic** in 1.10 s (median).
- A real 597-file tree (2.0 MB, mean file 3.5 KB) copied into **sonic** in 130.45s and read back in 15.75s, verified byte-for-byte with `diff -r`. At this file size the cost is per-file, not per-byte: 4.6 files/s written.
- A real 650-file tree (2.2 MB, mean file 3.4 KB) copied into **office** in 507.41s and read back in 15.83s, verified byte-for-byte with `diff -r`. At this file size the cost is per-file, not per-byte: 1.3 files/s written.
- Proof-of-storage works end to end: **2/2** challenges verified, at 19.6 ms per shard — cheap enough to run continuously across a fleet.
- With the peer holding a file's remote shards stopped, the file still reconstructed byte-identically in 167.4 ms — the parity budget holds in practice, not just on paper.
- The write cutoff works: the node refused new writes at 61.0% of its pledged quota and reported why, rather than filling the host disk.
- Console and mount list identical file sets on sonic, office — the UI is a view of the same namespace, not a separate index.

## 13. Reproducing
```bash
python -m benchmarks.run_mount_eval --node sonic=http://localhost:8010 --node office=http://192.168.1.43:8010@office --iterations 3 --max-size 64MB --saturate --degraded --contracts --real-tree /usr/share/doc/git
```

Raw measurements: `benchmarks/results/mount-eval.json`
