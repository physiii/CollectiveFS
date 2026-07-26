# CollectiveFS — Performance and Evaluation Report
Generated 2026-07-26 22:04 UTC · harness `benchmarks/run_mount_eval.py` · duration 9.9 min
Every figure below was measured against the live cluster through the `/media/collectivefs` mount, with erasure coding, encryption, shard distribution and peer routing all in the path. Nothing is extrapolated; where a measurement was not possible the row says so.

## 1. Compute under test
| Node | CPU | Parallelism | Memory | OS | Kernel |
|---|---|---|---|---|---|
| **sonic** | AMD Ryzen 7 5800X 8-Core Processor | 16 threads | 63 GB | Ubuntu 24.04.4 LTS | 6.11.0-29-generic |
| **office** | 12th Gen Intel(R) Core(TM) i9-12900K | 24 threads | 63 GB | Ubuntu 24.04.3 LTS | 6.11.0-29-generic |

| Node | Node ID | Pledged quota | Used | Backing disk | Free on disk | Media | Erasure |
|---|---|---|---|---|---|---|---|
| **sonic** | 78448c8f | 1.0 TB | 76.9 MB | 3.6 TB | 1.5 TB | Samsung SSD 990 EVO Plus 4TB (SSD/NVMe) | 8+4 |
| **office** | bc230253 | 1.0 TB | 4.5 GB | 3.6 TB | 1.3 TB | Samsung SSD 990 EVO Plus 4TB (SSD/NVMe) | 8+4 |

### Interconnect
| Measurement | Value |
|---|---|
| ICMP round trip | 0.132/0.389/0.571/0.169 ms |
| Node API round trip (median) | 10.8 ms |
| Shell/ssh invocation overhead (median) | 261.9 ms |

> Operation timings taken on the remote node include the shell invocation overhead above. Subtract it when comparing a remote single-operation latency against a local one.

## 2. Throughput by file size
Each size was written through the mount and read back, with a SHA-256 check on every round trip. Write time is measured until `cp` returns — the upload happens on close, so that is the honest end of the write.

### sonic
| Size | Bytes | Write | Write median | Read | Read median | SHA-256 verified |
|---|---|---|---|---|---|---|
| 4KB | 4.0 KB | 0.2 MB/s | 12.5 ms | 0.0 MB/s | 86.6 ms | 3/3 |
| 64KB | 64.0 KB | 11.1 MB/s | 5.5 ms | 0.8 MB/s | 81.7 ms | 3/3 |
| 1MB | 1.0 MB | 133.5 MB/s | 7.3 ms | 9.5 MB/s | 102.3 ms | 3/3 |
| 8MB | 8.0 MB | 205.3 MB/s | 38.5 ms | 27.1 MB/s | 299.0 ms | 3/3 |
| 64MB | 64.0 MB | 290.4 MB/s | 221.6 ms | 0.9 MB/s | 71.40 s | 3/3 |

### office
| Size | Bytes | Write | Write median | Read | Read median | SHA-256 verified |
|---|---|---|---|---|---|---|
| 4KB | 4.0 KB | 0.0 MB/s | 295.7 ms | 0.0 MB/s | 321.5 ms | 3/3 |
| 64KB | 64.0 KB | 0.2 MB/s | 287.6 ms | 0.2 MB/s | 327.9 ms | 3/3 |
| 1MB | 1.0 MB | 3.4 MB/s | 295.5 ms | 2.1 MB/s | 470.0 ms | 3/3 |
| 8MB | 8.0 MB | 26.5 MB/s | 305.2 ms | 23.7 MB/s | 335.2 ms | 3/3 |
| 64MB | 64.0 MB | 185.6 MB/s | 346.7 ms | 2.5 MB/s | 25.06 s | 3/3 |

## 3. Operation latency
Per-operation cost as a shell experiences it. These are whole-command timings, so each includes process startup.

### sonic
| Operation | Samples | Median | p95 | Max | Failures |
|---|---|---|---|---|---|
| create (touch) | 12 | 3.0 ms | 4.1 ms | 69.4 ms | 0 |
| stat | 12 | 4.5 ms | 5.3 ms | 5.4 ms | 0 |
| readdir | 12 | 5.3 ms | 7.3 ms | 7.9 ms | 0 |
| rename | 12 | 62.7 ms | 97.0 ms | 645.1 ms | 0 |
| read small | 10 | 29.6 ms | 36.7 ms | 36.7 ms | 2 |
| copy small | 10 | 7.0 ms | 8.7 ms | 8.7 ms | 2 |
| mkdir | 12 | 69.2 ms | 275.6 ms | 283.9 ms | 0 |
| rmdir | 12 | 72.6 ms | 83.7 ms | 87.1 ms | 0 |
| unlink | 12 | 68.3 ms | 71.3 ms | 280.7 ms | 0 |

### office
| Operation | Samples | Median | p95 | Max | Failures |
|---|---|---|---|---|---|
| create (touch) | 12 | 293.9 ms | 302.2 ms | 305.3 ms | 0 |
| stat | 12 | 260.7 ms | 302.0 ms | 489.6 ms | 0 |
| readdir | 12 | 285.1 ms | 298.9 ms | 300.5 ms | 0 |
| rename | 12 | 328.5 ms | 342.5 ms | 348.6 ms | 0 |
| read small | 12 | 275.3 ms | 305.6 ms | 318.8 ms | 0 |
| copy small | 12 | 290.8 ms | 306.2 ms | 324.4 ms | 0 |
| mkdir | 12 | 301.3 ms | 312.8 ms | 330.4 ms | 0 |
| rmdir | 12 | 314.4 ms | 321.9 ms | 322.7 ms | 0 |
| unlink | 12 | 295.7 ms | 304.6 ms | 305.7 ms | 0 |

## 4. Kernel-level operation mix
Reported by the FUSE layer itself, so these exclude shell startup and show the true cost of each filesystem call.

### sonic
Window 622s · 16753 operations · read 0.4 MB/s · write 1.2 MB/s · 0 errors

| FUSE operation | Calls | Mean | Peak | Errors |
|---|---|---|---|---|
| write | 6451 | 0.15 ms | 14.71 ms | 0 |
| getattr | 3759 | 1.17 ms | 319.62 ms | 0 |
| lookup | 3144 | 12.51 ms | 6611.4 ms | 0 |
| read | 1384 | 321.59 ms | 68557.27 ms | 0 |
| release | 735 | 346.94 ms | 3116.41 ms | 0 |
| open | 457 | 0.01 ms | 0.03 ms | 0 |
| create | 285 | 0.15 ms | 1.71 ms | 0 |
| unlink | 254 | 30.92 ms | 778.78 ms | 0 |
| readdir | 193 | 80.59 ms | 10759.98 ms | 0 |
| statfs | 47 | 503.13 ms | 18188.36 ms | 0 |
| mkdir | 16 | 202.9 ms | 1521.54 ms | 0 |
| rmdir | 16 | 21.23 ms | 31.61 ms | 0 |
| rename | 12 | 110.92 ms | 640.57 ms | 0 |

### office
Window 25s · 4 operations · read 0.0 MB/s · write 0.0 MB/s · 0 errors

| FUSE operation | Calls | Mean | Peak | Errors |
|---|---|---|---|---|
| statfs | 4 | 16.78 ms | 31.39 ms | 0 |

## 5. Concurrent load
| Node | Streams | Per file | Total | Elapsed | Aggregate | Landed |
|---|---|---|---|---|---|---|
| sonic | 8 | 8.0 MB | 64.0 MB | 0.29 s | 224.2 MB/s | 8/8 |
| office | 8 | 8.0 MB | 64.0 MB | 0.4 s | 161.5 MB/s | 8/8 |

## 6. Real directory tree
A genuine source tree copied in, read back, and compared with `diff -r` — every byte of every file, both directions. This is the shape most real data has: many small files, where per-file cost dominates and raw throughput barely matters.
| Node | Files | Total | Mean file | Write | Write rate | Read | Read rate | Verified |
|---|---|---|---|---|---|---|---|---|
| **sonic** | 218 files | 1.2 MB | 5.5 KB | 15.43 s | 14.1/s | 23.74 s | 9.2/s | identical |
| **office** | 218 files | 1.2 MB | 5.5 KB | 22.72 s | 9.6/s | 28.11 s | 7.8/s | identical |

## 7. Cross-node reconciliation
Time from a write completing on one machine to the file being visible, then readable, on the other.
| Direction | Samples | Visible median | Visible p95 | Readable median | Readable max | Failures |
|---|---|---|---|---|---|---|
| sonic → office | 5 | 302.6 ms | 313.5 ms | 608.6 ms | 623.2 ms | 0 |
| office → sonic | 5 | 133.0 ms | 160.8 ms | 201.2 ms | 211.6 ms | 0 |

## 8. Shard distribution
| Node | Files | Shards | Held here | On peers | Stored for peers | Missing | Own shards | Hosted for peers | Expansion |
|---|---|---|---|---|---|---|---|---|---|
| **sonic** | 42 | 504 | 256 | 122 | 92 | 126 | 91.9 MB | 163 MB | 6.53x |
| **office** | 28 | 336 | 204 | 92 | 122 | 40 | 4.8 GB | 6.5 MB | 1.47x |

> Expansion is our shards against our own data — the erasure-coding overhead. Data stored for peers occupies the quota but is counted separately, since it is not our storage cost.

### Fault tolerance
| Check | Result |
|---|---|
| File size | 8.0 MB |
| Shard placement | {"http://192.168.1.43:8010": 4, "local": 8} |
| Peer holding remote shards | stopped |
| Read succeeded | yes |
| Read time | 4.96 s |
| SHA-256 matched | yes |

## 9. Quota saturation
The production pledge is 1 TB per node. Filling that to its cutoff would take hours and a terabyte of disk, and the behaviour under test — what the node does when it runs out of pledged room — is identical at any quota. So the quota was temporarily lowered, driven past the cutoff, and restored.
| Measurement | Value |
|---|---|
| Temporary quota | 1.2 GB |
| Write cutoff watermark | 50% |
| Data written before cutoff | 512 MB |
| Files written | 16 |
| Elapsed | 10.95 s |
| Cutoff triggered | yes |
| Usage at stop | 51.2% |
| Accepting writes after cutoff | no |
| Node response | `node stopped accepting writes at 51.1% of quota (cutoff 50%)` |
| Quota restored to | 1.0 TB |

## 10. Peer contracts and proof-of-storage
Contracts are how a node verifies a peer is really holding what it claims: it asks for a hash of bytes at random offsets in a shard, with a nonce so the answer cannot be replayed. This runs on a timer per contract, so its cost is part of steady-state load.

### Challenge round trip
| Stage | Samples | Median | p95 | Max | Failures |
|---|---|---|---|---|---|
| Origin builds the challenge | 4 | 2.1 ms | 7.2 ms | 7.2 ms | 0 |
| Peer computes the proof | 4 | 3.3 ms | 4.1 ms | 4.1 ms | 0 |
| Origin verifies and scores | 4 | 2.7 ms | 5.0 ms | 5.0 ms | 0 |

End-to-end proof of one shard: **8.1 ms** (sum of medians).

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
| **sonic** | 70 | 69 | 87 | differs (console-only 2, mount-only 10) |
| **office** | 70 | 69 | 87 | differs (console-only 2, mount-only 10) |

> The console lists files by id, so two files can share a path. A POSIX filesystem cannot represent that, so the mount shows one of each. Colliding paths in this namespace: `bunny_1080p.mp4` ×2.

## 12. Observations
- **sonic** peaks at 290.4 MB/s write (64MB) and 27.1 MB/s read (8MB).
- On **sonic** a 64MB write moves data 1528× faster per byte than a 4KB one — small files are dominated by the fixed cost of encoding and the round trip, not by size.
- **office** peaks at 185.6 MB/s write (64MB) and 23.7 MB/s read (8MB).
- On **office** a 64MB write moves data 18558× faster per byte than a 4KB one — small files are dominated by the fixed cost of encoding and the round trip, not by size.
- Every round trip was hash-checked: **30/30** files came back byte-identical after erasure coding, encryption, distribution across two machines and reconstruction.
- A file written on **sonic** is readable on **office** in 608.6 ms (median).
- A file written on **office** is readable on **sonic** in 201.2 ms (median).
- A real 218-file tree (1.2 MB, mean file 5.5 KB) copied into **sonic** in 15.43s and read back in 23.74s, verified byte-for-byte with `diff -r`. At this file size the cost is per-file, not per-byte: 14.1 files/s written.
- A real 218-file tree (1.2 MB, mean file 5.5 KB) copied into **office** in 22.72s and read back in 28.11s, verified byte-for-byte with `diff -r`. At this file size the cost is per-file, not per-byte: 9.6 files/s written.
- Proof-of-storage works end to end: **4/4** challenges verified, at 8.1 ms per shard — cheap enough to run continuously across a fleet.
- With the peer holding a file's remote shards stopped, the file still reconstructed byte-identically in 4.96 s — the parity budget holds in practice, not just on paper.
- The write cutoff works: the node refused new writes at 51.2% of its pledged quota and reported why, rather than filling the host disk.

## 13. Reproducing
```bash
python -m benchmarks.run_mount_eval --node sonic=http://localhost:8010 --node office=http://192.168.1.43:8010@office --iterations 3 --max-size 64MB --saturate --degraded --contracts --real-tree /usr/include/python3.12
```

Raw measurements: `benchmarks/results/mount-eval.json`
