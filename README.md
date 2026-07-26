# Objective
The objective of CollectiveFS is to create a public file system where users can store personal files. I draw from protocols such as BitTorrent and BitCoin to create resilant distributed networks.

# Description
The cloud is a cluster of servers owned by a single entity. Typically, their motive is to collect payments directly or from 3rd parties which creates reliability and security risks. CollectiveFS serves to be a public alternative to privately owned cloud storage. Control is distributed among entities who choose to provide disc space in exchange for having their files on the network.

# Hidden in Plain Sight
File chunks are exchanged with untrusted peers but are encrypted. Symetric keys are used since the encryptor/decryptor are the same entity.

# Similar Projects
IPFS - Aims to replace IP based HTTP websites with content addressed ones hosted by p2p clusters. They introduce the concept of pinning where you can prioritize data. On CollectiveFS, each byte is as valued as any other byte on the network and parity can be configured so users can choose their desired level of fault tolerance against data erasures. IPFS also uses version control to track file history. On CollectiveFS, there is no version control although this can be implemented at the user level.

Hadoop - A distributed file system (HDFS) for big data. Used at companies like Facebook. Hadoop must be configured from the top down by a single entity where CollectiveFS is built from the bottom up by the individual nodes.

Syncthing - Synchronizes files over many nodes using p2p. Only synchronizes between nodes you own therefor is not public.


# Technologies
WebRTC  
Symmetric Encryption (Fernet)  
Encoding (ReedSolomon)  
FUSE  

# Storage
Files are Reed-Solomon erasure coded, every shard is Fernet-encrypted, and the
shards are spread across this node and its peers. No peer is given more than
`parity_shards` of any file, so losing a whole peer stays inside what the code
can rebuild. A shard only leaves the origin once the peer has echoed back a
matching digest, and reads pull remote shards home automatically.

```
upload on node A (8+4)     8 shards -> node A
                           4 shards -> node B     node B can vanish; A rebuilds
```

Peering is configured per host in `.env` (see `.env.example`): `CFS_OWN_URL` is
how peers reach this node, `CFS_PEER_URLS` is who to announce to.

# Performance

`make eval-mount` measures the whole system end to end — throughput by file
size, per-operation latency, concurrent load, cross-node reconciliation, shard
placement, degraded reads, proof-of-storage cost, quota saturation and
console/mount parity — and writes a report to
`benchmarks/results/mount-eval.md` alongside the raw JSON.

# Console
Each node serves a console at its own address (`http://<node>:8010/`). It is a
stack of section cards; every section has a dashboard, a chat, and the skill
document that governs both.

**Files** is the first section — a file explorer over what this node stores.
A folder tree, breadcrumbs, list and grid views, and shard availability shown
next to size, because on an untrusted network *recoverable* matters more than
*stored*.

**System & Infrastructure** is the second — compute, memory, network, allocated
storage, shard durability, and peer contracts, with live charts. Its chat can
change the node rather than only describe it: ask it to allocate more or less
space, retune the Reed-Solomon data/parity split, or adjust limits, and it
applies the change against a validated schema and writes it to an audit log.

The agent behind the chats is pluggable — `codewhale` by default, switchable to
`claude` or `codex` from the UI. See `docs/ARCHITECTURE.md`.

```bash
make build          # Go encoder/decoder + console UI
docker compose up -d # node on :8010
```

## Saving a file 
![Alt text](/images/CollectiveFS_save_file.png?raw=true "Saving files")


## Getting a file:
![Alt text](/images/CollectiveFS_get_file.png?raw=true "Saving files")
