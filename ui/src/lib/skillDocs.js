// Each section is fronted by at least one skill. The markdown here is what the
// section's "skill" view renders, and it is the same contract the section agent
// is briefed against — so what the operator reads is what the agent follows.

export const FILES_SKILL_MD = `# Files

## Scope

Own the operator's view of everything this node stores: the folder tree, file
placement, shard health, and the upload/download path. A file is never a single
blob here — it is Reed-Solomon shards, each Fernet-encrypted, spread across
untrusted peers.

## Dashboard

- Open on the folder tree so structure is visible before contents.
- Show shard availability per file, not just size, because recoverability is the
  property that actually matters on an untrusted network.
- Keep navigation cheap: breadcrumbs, a persistent tree, list and grid views.
- Uploads land in the folder currently being viewed.

## Chat Context

The Files agent reasons about stored files, folder layout, shard placement and
whether a given file is still reconstructable given the parity budget.

## Guardrails

- Deleting a file drops its shards on this node and is not reversible.
- Removing a folder never deletes files; they move to the root.
- Never surface the Fernet key or raw shard bytes.
`

export const SYSTEM_SKILL_MD = `# System & Infrastructure

## Scope

Own node readiness end to end: host compute, memory and network, plus the
collective-specific signals — quota headroom, shard durability, the erasure
fault budget, peers, and contract enforcement.

## Dashboard

- Correlate host pressure with collective pressure instead of showing them apart.
- Storage is reported against the *pledged quota*, not just the filesystem, since
  the quota is what this node has promised the network.
- Surface the write cutoff watermark before it is hit, not after.
- Chart CPU, memory and network over a rolling window so trends are visible.

## Chat Context

This section's agent may **change the node**, not only describe it. Ask it to
allocate more or less space, retune Reed-Solomon data/parity shards, adjust the
upload ceiling, change the default contract tier, or switch the agent provider.
Every change is validated against the real machine and written to an audit log.

## Guardrails

- A quota larger than the underlying filesystem is refused.
- Erasure changes apply to subsequent uploads; already-encoded files keep their
  layout and must be re-uploaded to change shape.
- Reducing parity reduces how many peers can fail before data is unrecoverable.
- Every mutation is recorded with its before/after values and its source.
`

export const SKILL_DOCS = {
  files: FILES_SKILL_MD,
  system: SYSTEM_SKILL_MD,
}
