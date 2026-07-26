# CollectiveFS — Performance and Evaluation Report
Generated 2026-07-26 23:05 UTC · harness `benchmarks/run_mount_eval.py` · duration 11.1 min
Every figure below was measured against the live cluster through the `/media/collectivefs` mount, with erasure coding, encryption, shard distribution and peer routing all in the path. Nothing is extrapolated; where a measurement was not possible the row says so.

## 1. Compute under test
| Node | CPU | Parallelism | Memory | OS | Kernel |
|---|---|---|---|---|---|
| **sonic** | AMD Ryzen 7 5800X 8-Core Processor | 16 threads | 63 GB | Ubuntu 24.04.4 LTS | 6.11.0-29-generic |
| **office** | 12th Gen Intel(R) Core(TM) i9-12900K | 24 threads | 63 GB | Ubuntu 24.04.3 LTS | 6.11.0-29-generic |

| Node | Node ID | Pledged quota | Used | Backing disk | Free on disk | Media | Erasure |
|---|---|---|---|---|---|---|---|
| **sonic** | 78448c8f | 1.0 TB | 679 MB | 3.6 TB | 1.5 TB | Samsung SSD 990 EVO Plus 4TB (SSD/NVMe) | 8+4 |
| **office** | bc230253 | 1.0 TB | 5.4 GB | 3.6 TB | 1.3 TB | Samsung SSD 990 EVO Plus 4TB (SSD/NVMe) | 8+4 |

### Interconnect
| Measurement | Value |
|---|---|
| ICMP round trip | 0.302/0.433/0.542/0.085 ms |
| Node API round trip (median) | 9.1 ms |
| Shell/ssh invocation overhead (median) | 262.3 ms |

> Operation timings taken on the remote node include the shell invocation overhead above. Subtract it when comparing a remote single-operation latency against a local one.

## 2. Throughput by file size
Each size was written through the mount and read back, with a SHA-256 check on every round trip. Write time is measured until `cp` returns — the upload happens on close, so that is the honest end of the write.

### sonic
| Size | Bytes | Write | Write median | Read | Read median | SHA-256 verified |
|---|---|---|---|---|---|---|
| 4KB | 4.0 KB | 0.1 MB/s | 6.6 ms | 0.0 MB/s | 320.4 ms | 3/3 |
| 64KB | 64.0 KB | 10.3 MB/s | 5.7 ms | 0.2 MB/s | 360.0 ms | 3/3 |
| 1MB | 1.0 MB | 104.8 MB/s | 9.7 ms | 3.1 MB/s | 322.6 ms | 3/3 |
| 8MB | 8.0 MB | 259.3 MB/s | 29.8 ms | 16.4 MB/s | 506.8 ms | 3/3 |
| 64MB | 64.0 MB | 214.5 MB/s | 300.5 ms | 9.2 MB/s | 6.84 s | 3/3 |
| 256MB | 256 MB | 332.6 MB/s | 758.0 ms | 8.8 MB/s | 29.50 s | 3/3 |

### office
| Size | Bytes | Write | Write median | Read | Read median | SHA-256 verified |
|---|---|---|---|---|---|---|
| 4KB | 4.0 KB | 0.0 MB/s | 324.3 ms | 0.0 MB/s | 615.3 ms | 3/3 |
| 64KB | 64.0 KB | 0.2 MB/s | 322.7 ms | 0.1 MB/s | 604.1 ms | 3/3 |
| 1MB | 1.0 MB | 3.0 MB/s | 332.5 ms | 1.8 MB/s | 605.4 ms | 3/3 |
| 8MB | 8.0 MB | 22.9 MB/s | 337.6 ms | 20.1 MB/s | 407.9 ms | 3/3 |
| 64MB | 64.0 MB | 163.9 MB/s | 381.2 ms | 13.9 MB/s | 4.64 s | 3/3 |
| 256MB | 256 MB | 453.9 MB/s | 547.5 ms | 13.7 MB/s | 18.55 s | 3/3 |

## 3. Operation latency
Per-operation cost as a shell experiences it. These are whole-command timings, so each includes process startup.

### sonic
| Operation | Samples | Median | p95 | Max | Failures |
|---|---|---|---|---|---|
| create (touch) | 12 | 3.3 ms | 3.7 ms | 301.7 ms | 0 |
| stat | 12 | 4.2 ms | 4.8 ms | 12.8 ms | 0 |
| readdir | 12 | 5.0 ms | 5.4 ms | 5.9 ms | 0 |
| rename | 12 | 169.2 ms | 802.3 ms | 2.51 s | 0 |
| read small | 9 | 36.6 ms | 43.4 ms | 43.4 ms | 3 |
| copy small | 9 | 7.6 ms | 10.2 ms | 10.2 ms | 3 |
| mkdir | 12 | 156.5 ms | 832.3 ms | 1.81 s | 0 |
| rmdir | 12 | 153.5 ms | 184.6 ms | 187.7 ms | 0 |
| unlink | 12 | 140.5 ms | 153.0 ms | 155.9 ms | 0 |

### office
| Operation | Samples | Median | p95 | Max | Failures |
|---|---|---|---|---|---|
| create (touch) | 12 | 475.0 ms | 517.0 ms | 600.2 ms | 0 |
| stat | 12 | 263.8 ms | 321.6 ms | 497.4 ms | 0 |
| readdir | 12 | 323.7 ms | 334.2 ms | 337.5 ms | 0 |
| rename | 12 | 409.6 ms | 423.6 ms | 467.9 ms | 0 |
| read small | 12 | 292.1 ms | 369.4 ms | 389.9 ms | 0 |
| copy small | 12 | 509.7 ms | 652.6 ms | 659.6 ms | 0 |
| mkdir | 12 | 349.4 ms | 396.2 ms | 565.0 ms | 0 |
| rmdir | 12 | 370.8 ms | 411.7 ms | 418.9 ms | 0 |
| unlink | 12 | 353.7 ms | 371.7 ms | 384.6 ms | 0 |

## 4. Kernel-level operation mix
Reported by the FUSE layer itself, so these exclude shell startup and show the true cost of each filesystem call.

### sonic
Window 890s · 37535 operations · read 1.6 MB/s · write 1.6 MB/s · 0 errors

| FUSE operation | Calls | Mean | Peak | Errors |
|---|---|---|---|---|
| getattr | 13244 | 1.59 ms | 933.35 ms | 0 |
| write | 11386 | 0.13 ms | 1.78 ms | 0 |
| read | 6049 | 31.35 ms | 15801.4 ms | 0 |
| lookup | 3759 | 30.62 ms | 16892.69 ms | 0 |
| readdir | 1216 | 14.3 ms | 14145.72 ms | 0 |
| release | 758 | 708.67 ms | 5095.26 ms | 0 |
| open | 483 | 0.01 ms | 0.04 ms | 0 |
| create | 276 | 0.14 ms | 0.66 ms | 0 |
| unlink | 257 | 23.74 ms | 1210.32 ms | 0 |
| statfs | 63 | 1983.46 ms | 41824.92 ms | 0 |
| mkdir | 16 | 451.24 ms | 4518.96 ms | 0 |
| rmdir | 16 | 26.5 ms | 33.6 ms | 0 |
| rename | 12 | 445.42 ms | 2498.69 ms | 0 |

### office
Window 20s · 4 operations · read 0.0 MB/s · write 0.0 MB/s · 0 errors

| FUSE operation | Calls | Mean | Peak | Errors |
|---|---|---|---|---|
| statfs | 4 | 50.15 ms | 56.04 ms | 0 |

## 5. Concurrent load
| Node | Streams | Per file | Total | Elapsed | Aggregate | Landed |
|---|---|---|---|---|---|---|
| sonic | 8 | 8.0 MB | 64.0 MB | 0.37 s | 173.3 MB/s | 8/8 |
| office | 8 | 8.0 MB | 64.0 MB | 0.46 s | 137.8 MB/s | 8/8 |

## 6. Real directory tree
A genuine source tree copied in, read back, and compared with `diff -r` — every byte of every file, both directions. This is the shape most real data has: many small files, where per-file cost dominates and raw throughput barely matters.
| Node | Files | Total | Mean file | Write | Write rate | Read | Read rate | Verified |
|---|---|---|---|---|---|---|---|---|
| **sonic** | 218 files | 1.2 MB | 5.5 KB | 29.56 s | 7.4/s | 7.67 s | 28.4/s | identical |
| **office** | 218 files | 1.2 MB | 5.5 KB | 22.19 s | 9.8/s | 78.46 s | 2.8/s | identical |

## 7. Cross-node reconciliation
Time from a write completing on one machine to the file being visible, then readable, on the other.
| Direction | Samples | Visible median | Visible p95 | Readable median | Readable max | Failures |
|---|---|---|---|---|---|---|
| sonic → office | 5 | 392.0 ms | 411.9 ms | 698.4 ms | 708.4 ms | 0 |
| office → sonic | 5 | 833.2 ms | 1.18 s | 921.5 ms | 1.27 s | 0 |

## 8. Shard distribution
| Node | Files | Shards | Held here | On peers | Stored for peers | Missing | Own shards | Hosted for peers | Expansion |
|---|---|---|---|---|---|---|---|---|---|
| **sonic** | 44 | 528 | 264 | 128 | 1199 | 136 | 179 MB | 1.2 GB | 1.62x |
| **office** | 313 | 3756 | 2361 | 1199 | 128 | 196 | 6.6 GB | 59.9 MB | 1.39x |

> Expansion is our shards against our own data — the erasure-coding overhead. Data stored for peers occupies the quota but is counted separately, since it is not our storage cost.

### Fault tolerance
| Check | Result |
|---|---|
| File size | 8.0 MB |
| Shard placement | {"http://192.168.1.43:8010": 4, "local": 8} |
| Peer holding remote shards | stopped |
| Read succeeded | yes |
| Read time | 202.5 ms |
| SHA-256 matched | yes |

## 9. Quota saturation
The production pledge is 1 TB per node. Filling that to its cutoff would take hours and a terabyte of disk, and the behaviour under test — what the node does when it runs out of pledged room — is identical at any quota. So the quota was temporarily lowered, driven past the cutoff, and restored.
| Measurement | Value |
|---|---|
| Temporary quota | 2.3 GB |
| Write cutoff watermark | 50% |
| Data written before cutoff | 32.0 MB |
| Files written | 1 |
| Elapsed | 1.09 s |
| Cutoff triggered | yes |
| Usage at stop | 57.0% |
| Accepting writes after cutoff | no |
| Node response | `node stopped accepting writes at 57.0% of quota (cutoff 50%)` |
| Quota restored to | 1.0 TB |

## 10. Peer contracts and proof-of-storage
Contracts are how a node verifies a peer is really holding what it claims: it asks for a hash of bytes at random offsets in a shard, with a nonce so the answer cannot be replayed. This runs on a timer per contract, so its cost is part of steady-state load.

### Challenge round trip
| Stage | Samples | Median | p95 | Max | Failures |
|---|---|---|---|---|---|
| Origin builds the challenge | 4 | 3.1 ms | 8.1 ms | 8.1 ms | 0 |
| Peer computes the proof | 4 | 8.0 ms | 9.2 ms | 9.2 ms | 0 |
| Origin verifies and scores | 4 | 2.7 ms | 4.4 ms | 4.4 ms | 0 |

End-to-end proof of one shard: **13.8 ms** (sum of medians).

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
| **sonic** | 357 | 356 | 356 | identical |
| **office** | 357 | 356 | 356 | identical |

> The console lists files by id, so two files can share a path. A POSIX filesystem cannot represent that, so the mount shows one of each. Colliding paths in this namespace: `bunny_1080p.mp4` ×2.

## 12. Observations
- **sonic** peaks at 332.6 MB/s write (256MB) and 16.4 MB/s read (8MB).
- On **sonic** a 256MB write moves data 4158× faster per byte than a 4KB one — small files are dominated by the fixed cost of encoding and the round trip, not by size.
- **office** peaks at 453.9 MB/s write (256MB) and 20.1 MB/s read (8MB).
- On **office** a 256MB write moves data 45390× faster per byte than a 4KB one — small files are dominated by the fixed cost of encoding and the round trip, not by size.
- Every round trip was hash-checked: **36/36** files came back byte-identical after erasure coding, encryption, distribution across two machines and reconstruction.
- A file written on **sonic** is readable on **office** in 698.4 ms (median).
- A file written on **office** is readable on **sonic** in 921.5 ms (median).
- A real 218-file tree (1.2 MB, mean file 5.5 KB) copied into **sonic** in 29.56s and read back in 7.67s, verified byte-for-byte with `diff -r`. At this file size the cost is per-file, not per-byte: 7.4 files/s written.
- A real 218-file tree (1.2 MB, mean file 5.5 KB) copied into **office** in 22.19s and read back in 78.46s, verified byte-for-byte with `diff -r`. At this file size the cost is per-file, not per-byte: 9.8 files/s written.
- Proof-of-storage works end to end: **4/4** challenges verified, at 13.8 ms per shard — cheap enough to run continuously across a fleet.
- With the peer holding a file's remote shards stopped, the file still reconstructed byte-identically in 202.5 ms — the parity budget holds in practice, not just on paper.
- The write cutoff works: the node refused new writes at 57.0% of its pledged quota and reported why, rather than filling the host disk.
- Console and mount list identical file sets on sonic, office — the UI is a view of the same namespace, not a separate index.

## 13. Reproducing
```bash
python -m benchmarks.run_mount_eval --node sonic=http://localhost:8010 --node office=http://192.168.1.43:8010@office --iterations 3 --max-size 256MB --saturate --degraded --contracts --real-tree /usr/include/python3.12
```

Raw measurements: `benchmarks/results/mount-eval.json`
