# CollectiveFS — Performance and Evaluation Report
Generated 2026-07-26 21:30 UTC · harness `benchmarks/run_mount_eval.py` · duration 2.1 min
Every figure below was measured against the live cluster through the `/media/collectivefs` mount, with erasure coding, encryption, shard distribution and peer routing all in the path. Nothing is extrapolated; where a measurement was not possible the row says so.

## 1. Compute under test
| Node | CPU | Parallelism | Memory | OS | Kernel |
|---|---|---|---|---|---|
| **sonic** | AMD Ryzen 7 5800X 8-Core Processor | 16 threads | 63 GB | Ubuntu 24.04.4 LTS | 6.11.0-29-generic |
| **office** | 12th Gen Intel(R) Core(TM) i9-12900K | 24 threads | 63 GB | Ubuntu 24.04.3 LTS | 6.11.0-29-generic |

| Node | Node ID | Pledged quota | Used | Backing disk | Free on disk | Media | Erasure |
|---|---|---|---|---|---|---|---|
| **sonic** | 78448c8f | 1.0 TB | 169 MB | 3.6 TB | 1.5 TB | ? | 8+4 |
| **office** | bc230253 | 1.0 TB | 4.7 GB | 3.6 TB | 1.3 TB | ? | 8+4 |

### Interconnect
| Measurement | Value |
|---|---|
| ICMP round trip | 0.097/0.125/0.198/0.037 ms |
| Node API round trip (median) | 8.6 ms |
| Shell/ssh invocation overhead (median) | 239.7 ms |

> Operation timings taken on the remote node include the shell invocation overhead above. Subtract it when comparing a remote single-operation latency against a local one.

## 2. Throughput by file size
Each size was written through the mount and read back, with a SHA-256 check on every round trip. Write time is measured until `cp` returns — the upload happens on close, so that is the honest end of the write.

### sonic
| Size | Bytes | Write | Write median | Read | Read median | SHA-256 verified |
|---|---|---|---|---|---|---|
| 4KB | 4.0 KB | 0.0 MB/s | 103.6 ms | 1.1 MB/s | 3.5 ms | 0/3 |
| 64KB | 64.0 KB | 0.6 MB/s | 110.1 ms | 17.4 MB/s | 3.4 ms | 0/3 |
| 1MB | 1.0 MB | 9.3 MB/s | 116.6 ms | 252.9 MB/s | 3.9 ms | 0/3 |
| 8MB | 8.0 MB | 72.9 MB/s | 119.5 ms | 154.9 MB/s | 72.0 ms | 0/3 |
| 64MB | 64.0 MB | 239.7 MB/s | 251.3 ms | 530.8 MB/s | 91.2 ms | 0/3 |

### office
| Size | Bytes | Write | Write median | Read | Read median | SHA-256 verified |
|---|---|---|---|---|---|---|
| 4KB | 4.0 KB | 0.0 MB/s | 276.9 ms | 0.0 MB/s | 301.9 ms | 3/3 |
| 64KB | 64.0 KB | 0.2 MB/s | 272.8 ms | 0.2 MB/s | 314.8 ms | 3/3 |
| 1MB | 1.0 MB | 3.6 MB/s | 279.6 ms | 2.2 MB/s | 446.6 ms | 3/3 |
| 8MB | 8.0 MB | 27.8 MB/s | 285.9 ms | 5.0 MB/s | 1.60 s | 3/3 |
| 64MB | 64.0 MB | 190.0 MB/s | 340.9 ms | — | — | 0/3 |

## 3. Operation latency
Per-operation cost as a shell experiences it. These are whole-command timings, so each includes process startup.

### sonic
| Operation | Samples | Median | p95 | Max | Failures |
|---|---|---|---|---|---|
| create (touch) | 12 | 81.0 ms | 106.2 ms | 123.5 ms | 0 |
| stat | 12 | 4.0 ms | 5.3 ms | 64.2 ms | 0 |
| readdir | 12 | 91.3 ms | 201.8 ms | 278.9 ms | 0 |
| rename | 12 | 111.7 ms | 143.2 ms | 212.9 ms | 0 |
| read small | 12 | 55.9 ms | 61.8 ms | 68.4 ms | 0 |
| copy small | 12 | 119.6 ms | 147.2 ms | 150.5 ms | 0 |
| mkdir | 12 | 98.7 ms | 108.7 ms | 109.7 ms | 0 |
| rmdir | 12 | 59.4 ms | 82.2 ms | 82.6 ms | 0 |
| unlink | 12 | 53.0 ms | 58.0 ms | 59.2 ms | 0 |

### office
| Operation | Samples | Median | p95 | Max | Failures |
|---|---|---|---|---|---|
| create (touch) | 12 | 302.5 ms | 319.6 ms | 334.1 ms | 0 |
| stat | 12 | 251.3 ms | 275.5 ms | 276.3 ms | 0 |
| readdir | 12 | 305.5 ms | 315.3 ms | 317.3 ms | 0 |
| rename | 12 | 303.8 ms | 315.1 ms | 334.3 ms | 0 |
| read small | 12 | 275.0 ms | 306.0 ms | 315.8 ms | 0 |
| copy small | 12 | 331.1 ms | 345.7 ms | 350.0 ms | 0 |
| mkdir | 12 | 307.5 ms | 324.1 ms | 339.6 ms | 0 |
| rmdir | 12 | 290.3 ms | 300.2 ms | 302.0 ms | 0 |
| unlink | 12 | 277.9 ms | 286.6 ms | 287.0 ms | 0 |

## 4. Kernel-level operation mix
Reported by the FUSE layer itself, so these exclude shell startup and show the true cost of each filesystem call.

### sonic
Window 893s · 10625 operations · read 0.0 MB/s · write 1.0 MB/s · 0 errors

| FUSE operation | Calls | Mean | Peak | Errors |
|---|---|---|---|---|
| write | 7542 | 0.13 ms | 3.61 ms | 0 |
| lookup | 1221 | 17.99 ms | 210.68 ms | 0 |
| getattr | 836 | 0.53 ms | 88.35 ms | 0 |
| readdir | 276 | 38.26 ms | 165.18 ms | 0 |
| release | 203 | 220.2 ms | 4469.59 ms | 0 |
| read | 122 | 75.49 ms | 3909.84 ms | 0 |
| create | 117 | 0.17 ms | 0.46 ms | 0 |
| open | 90 | 0.01 ms | 0.03 ms | 0 |
| unlink | 76 | 22.61 ms | 328.11 ms | 0 |
| statfs | 66 | 18.34 ms | 41.02 ms | 0 |
| mkdir | 26 | 14.7 ms | 77.55 ms | 0 |
| rmdir | 26 | 16.79 ms | 27.1 ms | 0 |
| rename | 24 | 59.79 ms | 128.52 ms | 0 |

### office
Window 15s · 4 operations · read 0.0 MB/s · write 0.0 MB/s · 0 errors

| FUSE operation | Calls | Mean | Peak | Errors |
|---|---|---|---|---|
| statfs | 4 | 12.61 ms | 20.17 ms | 0 |

## 5. Concurrent load
| Node | Streams | Per file | Total | Elapsed | Aggregate | Landed |
|---|---|---|---|---|---|---|
| sonic | 8 | 8.0 MB | 64.0 MB | 0.72 s | 89.1 MB/s | 8/8 |
| office | 8 | 8.0 MB | 64.0 MB | 0.83 s | 77.4 MB/s | 7/8 |

## 6. Cross-node reconciliation
Time from a write completing on one machine to the file being visible, then readable, on the other.
| Direction | Samples | Visible median | Visible p95 | Readable median | Readable max | Failures |
|---|---|---|---|---|---|---|
| sonic → office | 5 | 278.9 ms | 281.8 ms | 563.8 ms | 585.1 ms | 0 |
| office → sonic | 5 | 70.0 ms | 129.0 ms | 134.7 ms | 172.1 ms | 0 |

## 7. Shard distribution
| Node | Files | Shards | Held here | On peers | Stored for peers | Missing | Storage expansion |
|---|---|---|---|---|---|---|---|
| **sonic** | 22 | 264 | 200 | 64 | 140 | 0 | 38.3× |
| **office** | 38 | 456 | 316 | 140 | 64 | 0 | 1.48× |

### Fault tolerance
| Check | Result |
|---|---|
| File size | 8.0 MB |
| Shard placement | {"http://192.168.1.43:8010": 4, "local": 8} |
| Peer holding remote shards | stopped |
| Read succeeded | yes |
| Read time | 4.00 s |
| SHA-256 matched | yes |

## 8. Quota saturation
The production pledge is 1 TB per node. Filling that to its cutoff would take hours and a terabyte of disk, and the behaviour under test — what the node does when it runs out of pledged room — is identical at any quota. So the quota was temporarily lowered, driven past the cutoff, and restored.
| Measurement | Value |
|---|---|
| Temporary quota | 1.3 GB |
| Write cutoff watermark | 50% |
| Data written before cutoff | 416 MB |
| Files written | 13 |
| Elapsed | 8.2 s |
| Cutoff triggered | yes |
| Usage at stop | 54.6% |
| Accepting writes after cutoff | no |
| Node response | `node stopped accepting writes at 54.5% of quota (cutoff 50%)` |
| Quota restored to | 1.0 TB |

## 9. Peer contracts and proof-of-storage
Contracts are how a node verifies a peer is really holding what it claims: it asks for a hash of bytes at random offsets in a shard, with a nonce so the answer cannot be replayed. This runs on a timer per contract, so its cost is part of steady-state load.
| Measurement | Value |
|---|---|
| Contract created | yes |
| Tier | warm |
| Status | active |
| Challenges issued | 0 |
| Challenge median | — |
| Challenge p95 | — |
| Challenge max | — |
| Challenge failures | 10 |
| QoS score | 1.0 |

### Tier configuration
| Tier | Challenge interval | Response deadline | Storage multiplier | Max violations |
|---|---|---|---|---|
| None | None s | None s | 2.0× | 3 |
| None | None s | None s | 1.0× | 5 |
| None | None s | None s | 0.5× | 10 |

## 10. Console and mount parity
The web console and the mount are two views of one namespace, so they must list exactly the same files.
| Node | Files in console | Files in mount | Result |
|---|---|---|---|
| **sonic** | 56 | 55 | differs (ui-only 0, mount-only 0) |
| **office** | 4 | 55 | differs (ui-only 4, mount-only 10) |

## 11. Observations
- **sonic** peaks at 239.7 MB/s write (64MB) and 530.8 MB/s read (64MB).
- On **sonic** a 64MB write moves data 5992× faster per byte than a 4KB one — small files are dominated by the fixed cost of encoding and the round trip, not by size.
- **office** peaks at 190.0 MB/s write (64MB) and 5.0 MB/s read (8MB).
- On **office** a 64MB write moves data 19001× faster per byte than a 4KB one — small files are dominated by the fixed cost of encoding and the round trip, not by size.
- Every round trip was hash-checked: **12/30** files came back byte-identical after erasure coding, encryption, distribution across two machines and reconstruction.
- A file written on **sonic** is readable on **office** in 563.8 ms (median).
- A file written on **office** is readable on **sonic** in 134.7 ms (median).
- With the peer holding a file's remote shards stopped, the file still reconstructed byte-identically in 4.00 s — the parity budget holds in practice, not just on paper.
- The write cutoff works: the node refused new writes at 54.6% of its pledged quota and reported why, rather than filling the host disk.

## 12. Reproducing
```bash
python -m benchmarks.run_mount_eval --node sonic=http://localhost:8010 --node office=http://192.168.1.43:8010@office --iterations 3 --max-size 64MB --saturate --degraded --contracts
```

Raw measurements: `benchmarks/results/mount-eval.json`
