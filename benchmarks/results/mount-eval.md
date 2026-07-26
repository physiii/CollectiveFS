# CollectiveFS — Performance and Evaluation Report
Generated 2026-07-26 22:33 UTC · harness `benchmarks/run_mount_eval.py` · duration 11.9 min
Every figure below was measured against the live cluster through the `/media/collectivefs` mount, with erasure coding, encryption, shard distribution and peer routing all in the path. Nothing is extrapolated; where a measurement was not possible the row says so.

## 1. Compute under test
| Node | CPU | Parallelism | Memory | OS | Kernel |
|---|---|---|---|---|---|
| **sonic** | AMD Ryzen 7 5800X 8-Core Processor | 16 threads | 63 GB | Ubuntu 24.04.4 LTS | 6.11.0-29-generic |
| **office** | 12th Gen Intel(R) Core(TM) i9-12900K | 24 threads | 63 GB | Ubuntu 24.04.3 LTS | 6.11.0-29-generic |

| Node | Node ID | Pledged quota | Used | Backing disk | Free on disk | Media | Erasure |
|---|---|---|---|---|---|---|---|
| **sonic** | 78448c8f | 1.0 TB | 423 MB | 3.6 TB | 1.5 TB | Samsung SSD 990 EVO Plus 4TB (SSD/NVMe) | 8+4 |
| **office** | bc230253 | 1.0 TB | 5.0 GB | 3.6 TB | 1.3 TB | Samsung SSD 990 EVO Plus 4TB (SSD/NVMe) | 8+4 |

### Interconnect
| Measurement | Value |
|---|---|
| ICMP round trip | 0.102/0.147/0.193/0.034 ms |
| Node API round trip (median) | 8.7 ms |
| Shell/ssh invocation overhead (median) | 243.9 ms |

> Operation timings taken on the remote node include the shell invocation overhead above. Subtract it when comparing a remote single-operation latency against a local one.

## 2. Throughput by file size
Each size was written through the mount and read back, with a SHA-256 check on every round trip. Write time is measured until `cp` returns — the upload happens on close, so that is the honest end of the write.

### sonic
| Size | Bytes | Write | Write median | Read | Read median | SHA-256 verified |
|---|---|---|---|---|---|---|
| 4KB | 4.0 KB | 0.1 MB/s | 4.7 ms | 0.0 MB/s | 285.8 ms | 3/3 |
| 64KB | 64.0 KB | 13.1 MB/s | 4.7 ms | 0.2 MB/s | 282.6 ms | 3/3 |
| 1MB | 1.0 MB | 121.3 MB/s | 7.3 ms | 3.0 MB/s | 323.6 ms | 3/3 |
| 8MB | 8.0 MB | 339.9 MB/s | 23.6 ms | 22.5 MB/s | 360.1 ms | 3/3 |
| 64MB | 64.0 MB | 257.8 MB/s | 258.7 ms | 1.0 MB/s | 63.67 s | 3/3 |

### office
| Size | Bytes | Write | Write median | Read | Read median | SHA-256 verified |
|---|---|---|---|---|---|---|
| 4KB | 4.0 KB | 0.0 MB/s | 308.4 ms | 0.0 MB/s | 548.4 ms | 3/3 |
| 64KB | 64.0 KB | 0.2 MB/s | 327.2 ms | 0.1 MB/s | 532.8 ms | 3/3 |
| 1MB | 1.0 MB | 3.1 MB/s | 313.0 ms | 2.4 MB/s | 414.3 ms | 3/3 |
| 8MB | 8.0 MB | 25.3 MB/s | 316.9 ms | 21.2 MB/s | 378.3 ms | 3/3 |
| 64MB | 64.0 MB | 171.1 MB/s | 370.2 ms | 2.4 MB/s | 26.69 s | 3/3 |

## 3. Operation latency
Per-operation cost as a shell experiences it. These are whole-command timings, so each includes process startup.

### sonic
| Operation | Samples | Median | p95 | Max | Failures |
|---|---|---|---|---|---|
| create (touch) | 12 | 2.4 ms | 2.8 ms | 189.6 ms | 0 |
| stat | 12 | 3.9 ms | 7.2 ms | 12.0 ms | 0 |
| readdir | 12 | 4.5 ms | 5.3 ms | 5.4 ms | 0 |
| rename | 12 | 138.8 ms | 1.25 s | 1.77 s | 0 |
| read small | 9 | 24.3 ms | 34.3 ms | 34.3 ms | 3 |
| copy small | 9 | 7.0 ms | 12.6 ms | 12.6 ms | 3 |
| mkdir | 12 | 132.5 ms | 721.5 ms | 1.67 s | 0 |
| rmdir | 12 | 143.8 ms | 152.9 ms | 159.1 ms | 0 |
| unlink | 12 | 121.0 ms | 133.5 ms | 138.7 ms | 0 |

### office
| Operation | Samples | Median | p95 | Max | Failures |
|---|---|---|---|---|---|
| create (touch) | 12 | 428.7 ms | 719.1 ms | 736.7 ms | 0 |
| stat | 12 | 251.1 ms | 333.3 ms | 473.4 ms | 0 |
| readdir | 12 | 306.0 ms | 320.1 ms | 325.9 ms | 0 |
| rename | 12 | 313.5 ms | 319.3 ms | 392.2 ms | 0 |
| read small | 12 | 268.9 ms | 325.9 ms | 339.3 ms | 0 |
| copy small | 12 | 439.6 ms | 730.6 ms | 930.7 ms | 0 |
| mkdir | 12 | 319.2 ms | 338.0 ms | 1.01 s | 0 |
| rmdir | 12 | 326.7 ms | 360.7 ms | 368.4 ms | 0 |
| unlink | 12 | 323.3 ms | 330.4 ms | 333.5 ms | 0 |

## 4. Kernel-level operation mix
Reported by the FUSE layer itself, so these exclude shell startup and show the true cost of each filesystem call.

### sonic
Window 894s · 18860 operations · read 0.3 MB/s · write 0.7 MB/s · 1 errors

| FUSE operation | Calls | Mean | Peak | Errors |
|---|---|---|---|---|
| lookup | 5151 | 21.55 ms | 16121.2 ms | 0 |
| write | 4914 | 0.14 ms | 3.6 ms | 0 |
| getattr | 4504 | 2.04 ms | 471.61 ms | 0 |
| read | 1638 | 246.1 ms | 60442.66 ms | 0 |
| release | 968 | 417.06 ms | 3533.66 ms | 1 |
| open | 691 | 0.01 ms | 0.04 ms | 0 |
| unlink | 474 | 17.73 ms | 1134.06 ms | 0 |
| create | 278 | 0.1 ms | 0.35 ms | 0 |
| readdir | 135 | 190.15 ms | 20306.66 ms | 0 |
| statfs | 60 | 3034.37 ms | 47847.95 ms | 0 |
| rmdir | 19 | 25.63 ms | 35.86 ms | 0 |
| mkdir | 16 | 560.62 ms | 4516.75 ms | 0 |
| rename | 12 | 412.43 ms | 1766.66 ms | 0 |

### office
Window 30s · 4 operations · read 0.0 MB/s · write 0.0 MB/s · 0 errors

| FUSE operation | Calls | Mean | Peak | Errors |
|---|---|---|---|---|
| statfs | 4 | 51.04 ms | 54.83 ms | 0 |

## 5. Concurrent load
| Node | Streams | Per file | Total | Elapsed | Aggregate | Landed |
|---|---|---|---|---|---|---|
| sonic | 8 | 8.0 MB | 64.0 MB | 0.31 s | 205.7 MB/s | 8/8 |
| office | 8 | 8.0 MB | 64.0 MB | 0.43 s | 149.3 MB/s | 7/8 |

## 6. Real directory tree
A genuine source tree copied in, read back, and compared with `diff -r` — every byte of every file, both directions. This is the shape most real data has: many small files, where per-file cost dominates and raw throughput barely matters.
| Node | Files | Total | Mean file | Write | Write rate | Read | Read rate | Verified |
|---|---|---|---|---|---|---|---|---|
| **sonic** | 218 files | 1.2 MB | 5.5 KB | 24.06 s | 9.1/s | 15.6 s | 14.0/s | identical |
| **office** | 218 files | 1.2 MB | 5.5 KB | 17.27 s | 12.6/s | 64.67 s | 3.4/s | identical |

## 7. Cross-node reconciliation
Time from a write completing on one machine to the file being visible, then readable, on the other.
| Direction | Samples | Visible median | Visible p95 | Readable median | Readable max | Failures |
|---|---|---|---|---|---|---|
| sonic → office | 5 | 415.0 ms | 435.6 ms | 754.4 ms | 788.7 ms | 0 |
| office → sonic | 5 | 732.0 ms | 850.7 ms | 813.2 ms | 922.0 ms | 0 |

## 8. Shard distribution
| Node | Files | Shards | Held here | On peers | Stored for peers | Missing | Own shards | Hosted for peers | Expansion |
|---|---|---|---|---|---|---|---|---|---|
| **sonic** | 43 | 516 | 256 | 124 | 1112 | 136 | 93.3 MB | 500 MB | 2.02x |
| **office** | 288 | 3456 | 2209 | 1112 | 124 | 135 | 5.3 GB | 17.2 MB | 1.43x |

> Expansion is our shards against our own data — the erasure-coding overhead. Data stored for peers occupies the quota but is counted separately, since it is not our storage cost.

### Fault tolerance
| Check | Result |
|---|---|
| File size | 8.0 MB |
| Shard placement | {"http://192.168.1.43:8010": 4, "local": 8} |
| Peer holding remote shards | stopped |
| Read succeeded | yes |
| Read time | 4.56 s |
| SHA-256 matched | yes |

## 9. Quota saturation
The production pledge is 1 TB per node. Filling that to its cutoff would take hours and a terabyte of disk, and the behaviour under test — what the node does when it runs out of pledged room — is identical at any quota. So the quota was temporarily lowered, driven past the cutoff, and restored.
| Measurement | Value |
|---|---|
| Temporary quota | 1.6 GB |
| Write cutoff watermark | 50% |
| Data written before cutoff | 288 MB |
| Files written | 9 |
| Elapsed | 9.95 s |
| Cutoff triggered | yes |
| Usage at stop | 51.2% |
| Accepting writes after cutoff | no |
| Node response | `node stopped accepting writes at 51.2% of quota (cutoff 50%)` |
| Quota restored to | 1.0 TB |

## 10. Peer contracts and proof-of-storage
Contracts are how a node verifies a peer is really holding what it claims: it asks for a hash of bytes at random offsets in a shard, with a nonce so the answer cannot be replayed. This runs on a timer per contract, so its cost is part of steady-state load.

### Challenge round trip
| Stage | Samples | Median | p95 | Max | Failures |
|---|---|---|---|---|---|
| Origin builds the challenge | 4 | 2.0 ms | 10.8 ms | 10.8 ms | 0 |
| Peer computes the proof | 4 | 8.3 ms | 12.1 ms | 12.1 ms | 0 |
| Origin verifies and scores | 4 | 2.4 ms | 2.8 ms | 2.8 ms | 0 |

End-to-end proof of one shard: **12.7 ms** (sum of medians).

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
| **sonic** | 331 | 330 | 330 | identical |
| **office** | 331 | 330 | 330 | identical |

> The console lists files by id, so two files can share a path. A POSIX filesystem cannot represent that, so the mount shows one of each. Colliding paths in this namespace: `bunny_1080p.mp4` ×2.

## 12. Observations
- **sonic** peaks at 339.9 MB/s write (8MB) and 22.5 MB/s read (8MB).
- On **sonic** a 64MB write moves data 2864× faster per byte than a 4KB one — small files are dominated by the fixed cost of encoding and the round trip, not by size.
- **office** peaks at 171.1 MB/s write (64MB) and 21.2 MB/s read (8MB).
- On **office** a 64MB write moves data 17109× faster per byte than a 4KB one — small files are dominated by the fixed cost of encoding and the round trip, not by size.
- Every round trip was hash-checked: **30/30** files came back byte-identical after erasure coding, encryption, distribution across two machines and reconstruction.
- A file written on **sonic** is readable on **office** in 754.4 ms (median).
- A file written on **office** is readable on **sonic** in 813.2 ms (median).
- A real 218-file tree (1.2 MB, mean file 5.5 KB) copied into **sonic** in 24.06s and read back in 15.6s, verified byte-for-byte with `diff -r`. At this file size the cost is per-file, not per-byte: 9.1 files/s written.
- A real 218-file tree (1.2 MB, mean file 5.5 KB) copied into **office** in 17.27s and read back in 64.67s, verified byte-for-byte with `diff -r`. At this file size the cost is per-file, not per-byte: 12.6 files/s written.
- Proof-of-storage works end to end: **4/4** challenges verified, at 12.7 ms per shard — cheap enough to run continuously across a fleet.
- With the peer holding a file's remote shards stopped, the file still reconstructed byte-identically in 4.56 s — the parity budget holds in practice, not just on paper.
- The write cutoff works: the node refused new writes at 51.2% of its pledged quota and reported why, rather than filling the host disk.
- Console and mount list identical file sets on sonic, office — the UI is a view of the same namespace, not a separate index.

## 13. Reproducing
```bash
python -m benchmarks.run_mount_eval --node sonic=http://localhost:8010 --node office=http://192.168.1.43:8010@office --iterations 3 --max-size 64MB --saturate --degraded --contracts --real-tree /usr/include/python3.12
```

Raw measurements: `benchmarks/results/mount-eval.json`
