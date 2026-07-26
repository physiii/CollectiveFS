import { useEffect, useMemo, useState } from 'react'

import { getConfigAudit, putConfig } from '../lib/api'

import { fmtBytes, fmtDate, fmtPercent, fmtUptime, statusTone } from '../lib/format'
import {
  CpuHistoryChart,
  FilesystemOpsChart,
  FilesystemThroughputChart,
  MemoryHistoryChart,
  NetworkHistoryChart,
  StorageHistoryChart,
} from './SystemCharts'
import { DiskMetric, GaugeMetric, MetricBar, MiniStat, SkillPanel, StatusPill } from './primitives'

function fmtRate(value) {
  const rate = Number(value) || 0
  if (rate >= 1e9) return `${(rate / 1e9).toFixed(2)} GB/s`
  if (rate >= 1e6) return `${(rate / 1e6).toFixed(1)} MB/s`
  if (rate >= 1e3) return `${(rate / 1e3).toFixed(1)} KB/s`
  return `${rate.toFixed(0)} B/s`
}

// Fields exposed as direct controls. The chat can reach every field in the
// schema; these are the ones worth a permanent widget.
const QUICK_FIELDS = [
  { field: 'storage.quota_bytes', label: 'Allocated space', kind: 'bytes' },
  { field: 'storage.reserve_bytes', label: 'Reserved free space', kind: 'bytes' },
  { field: 'storage.high_watermark_percent', label: 'Write cutoff %', kind: 'number' },
  { field: 'erasure.data_shards', label: 'Data shards', kind: 'number' },
  { field: 'erasure.parity_shards', label: 'Parity shards', kind: 'number' },
  { field: 'upload.max_file_bytes', label: 'Max upload size', kind: 'bytes' },
  { field: 'contracts.default_tier', label: 'Default tier', kind: 'enum', choices: ['hot', 'warm', 'cold'] },
  { field: 'contracts.challenges_enabled', label: 'Proof challenges', kind: 'bool' },
]

function readField(config, field) {
  return field.split('.').reduce((cursor, part) => (cursor == null ? cursor : cursor[part]), config)
}

function SettingControl({ spec, config, onApply, busy }) {
  const stored = readField(config, spec.field)
  const [draft, setDraft] = useState('')

  useEffect(() => {
    if (spec.kind === 'bytes') setDraft(fmtBytes(stored).replace(' ', ''))
    else setDraft(String(stored ?? ''))
  }, [stored, spec.kind])

  if (spec.kind === 'bool') {
    return (
      <div className="setting-cell">
        <label htmlFor={spec.field}>{spec.label}</label>
        <button
          id={spec.field}
          type="button"
          className={`ghost-button ${stored ? 'accent' : ''}`}
          disabled={busy}
          onClick={() => onApply({ [spec.field]: !stored })}
        >
          {stored ? 'Enabled' : 'Disabled'}
        </button>
        <span className="setting-field">{spec.field}</span>
      </div>
    )
  }

  if (spec.kind === 'enum') {
    return (
      <div className="setting-cell">
        <label htmlFor={spec.field}>{spec.label}</label>
        <div className="inline-form">
          <select
            id={spec.field}
            value={stored ?? ''}
            disabled={busy}
            onChange={(event) => onApply({ [spec.field]: event.target.value })}
          >
            {spec.choices.map((choice) => (
              <option key={choice} value={choice}>
                {choice}
              </option>
            ))}
          </select>
        </div>
        <span className="setting-field">{spec.field}</span>
      </div>
    )
  }

  return (
    <div className="setting-cell">
      <label htmlFor={spec.field}>{spec.label}</label>
      <form
        className="inline-form"
        onSubmit={(event) => {
          event.preventDefault()
          onApply({ [spec.field]: draft })
        }}
      >
        <input
          id={spec.field}
          value={draft}
          disabled={busy}
          onChange={(event) => setDraft(event.target.value)}
          aria-label={spec.label}
        />
        <button type="submit" className="ghost-button" disabled={busy}>
          Set
        </button>
      </form>
      <span className="setting-field">{spec.field}</span>
    </div>
  )
}

export default function SystemPanel({ system, config, history, onConfigChanged, onNotify, full = false }) {
  const [busy, setBusy] = useState(false)
  const [audit, setAudit] = useState([])
  const [showVirtual, setShowVirtual] = useState(false)

  useEffect(() => {
    if (!full) return
    let cancelled = false
    getConfigAudit(10)
      .then((payload) => {
        if (!cancelled) setAudit(payload.entries ?? [])
      })
      .catch(() => {
        if (!cancelled) setAudit([])
      })
    return () => {
      cancelled = true
    }
  }, [full, config])

  const cpuData = useMemo(
    () => history.map((snapshot) => ({ _ts: snapshot._ts, cpu: snapshot.cpu?.percent ?? 0 })),
    [history],
  )
  const memoryData = useMemo(
    () => history.map((snapshot) => ({ _ts: snapshot._ts, memory: snapshot.memory?.percent ?? 0 })),
    [history],
  )
  const networkData = useMemo(
    () => history.map((snapshot) => ({ _ts: snapshot._ts, rx: snapshot._rxRate ?? 0, tx: snapshot._txRate ?? 0 })),
    [history],
  )
  const quotaData = useMemo(
    () => history.map((snapshot) => ({ _ts: snapshot._ts, quota: snapshot.collective?.used_percent ?? 0 })),
    [history],
  )

  async function applyUpdates(updates) {
    setBusy(true)
    try {
      const payload = await putConfig(updates)
      const changes = payload.changes ?? []
      onNotify(
        changes.length
          ? changes.map((change) => `${change.label} → ${change.after}`).join(', ')
          : 'Already at that value',
        'ok',
      )
      onConfigChanged?.()
    } catch (error) {
      onNotify(error.message, 'bad')
    } finally {
      setBusy(false)
    }
  }

  if (!system) return <p className="muted">System telemetry unavailable.</p>

  const collective = system.collective ?? {}
  const erasure = system.erasure ?? {}
  const peers = system.peers ?? { total: 0, online: 0, items: [] }
  const durabilityTone = collective.shards_missing > 0 ? 'warning' : 'healthy'

  const hosted = system.hosted_for_peers ?? { nodes: [], shards: 0, bytes: 0 }
  const filesystem = system.filesystem ?? { mounts: [], series: [], operations: [], active: false }
  const fsTotals = filesystem.totals ?? {}
  // The node already keeps a rolling window per mount, so chart it as reported
  // rather than re-deriving rates from the polling loop.
  const fsSeries = filesystem.series ?? []
  // Local first, then peers by share size — the story is "where does this live".
  const placementRows = Object.entries(collective.placement ?? {}).sort(
    ([a, countA], [b, countB]) => (a === 'local' ? -1 : b === 'local' ? 1 : countB - countA),
  )

  const interfaces = system.network ?? []
  const physicalLinks = interfaces.filter((iface) => !iface.virtual)
  const virtualLinks = interfaces.filter((iface) => iface.virtual)
  const shownLinks = showVirtual ? interfaces : physicalLinks.slice(0, full ? undefined : 3)

  return (
    <div className="preview-stack">
      <div className="mini-stat-grid four">
        <MiniStat label="Node" value={system.hostname ?? 'unknown'} />
        <MiniStat label="Uptime" value={fmtUptime(system.uptime_seconds)} />
        <MiniStat
          label="Allocated"
          value={`${fmtBytes(collective.used_bytes)} / ${fmtBytes(collective.quota_bytes)}`}
          tone={collective.status}
        />
        <MiniStat
          label="Peers"
          value={`${peers.online}/${peers.total}`}
          tone={peers.total === 0 || peers.online === peers.total ? 'healthy' : 'warning'}
        />
      </div>

      <div className="metric-grid">
        <GaugeMetric metric={system.quota} />
        <GaugeMetric metric={system.cpu} />
        <GaugeMetric metric={system.memory} />
        {system.disks?.slice(0, 1).map((disk) => (
          <DiskMetric key={disk.id} disk={disk} />
        ))}
      </div>

      <div className="health-row">
        <StatusPill
          label={collective.accepting_writes ? 'accepting writes' : 'writes paused — quota'}
          tone={collective.accepting_writes ? 'healthy' : 'critical'}
        />
        <StatusPill
          label={`${erasure.data_shards}+${erasure.parity_shards} erasure · tolerates ${erasure.can_lose}`}
          tone="healthy"
        />
        <StatusPill
          label={
            collective.shards_missing > 0
              ? `${collective.shards_missing} shards missing`
              : `${collective.shards_total} shards intact`
          }
          tone={durabilityTone}
        />
        {collective.shards_remote > 0 && (
          <StatusPill
            label={`${collective.shards_local} here · ${collective.shards_remote} on peers`}
            tone="healthy"
          />
        )}
        <StatusPill
          label={system.swap ? `swap ${fmtPercent(system.swap.percent)}` : 'swap disabled'}
          tone={system.swap?.status ?? 'healthy'}
        />
        {!collective.quota_fully_backed && (
          <StatusPill
            label={`${fmtBytes(collective.unbacked_bytes)} of the pledge has no free disk behind it`}
            tone="warning"
          />
        )}
      </div>

      <div className="skill-panel-grid">
        <SkillPanel title="Storage & Quota">
          <div className="preview-stack">
            <GaugeMetric metric={system.quota} />
            <div className="mini-stat-grid three">
              <MiniStat label="Logical" value={fmtBytes(collective.logical_bytes)} />
              <MiniStat label="On disk" value={fmtBytes(collective.used_bytes)} />
              <MiniStat label="Expansion" value={collective.expansion_ratio ? `${collective.expansion_ratio}x` : 'n/a'} />
            </div>
            <StorageHistoryChart data={quotaData} />
            <p className="metric-detail">
              The disk below is shared with the host — most of what it reports is not
              CollectiveFS data. This node occupies {fmtBytes(collective.used_bytes)}.
            </p>
            <div className="metric-grid">
              {system.disks?.map((disk) => (
                <DiskMetric key={disk.id} disk={disk} />
              ))}
            </div>
            <div className="mini-stat-grid three">
              <MiniStat label="Pledged" value={fmtBytes(collective.quota_bytes)} />
              <MiniStat label="Free on disk" value={fmtBytes(collective.device_free_bytes)} />
              <MiniStat
                label="Unbacked pledge"
                value={collective.unbacked_bytes ? fmtBytes(collective.unbacked_bytes) : 'none'}
                tone={collective.quota_fully_backed ? 'healthy' : 'warning'}
              />
            </div>
          </div>
        </SkillPanel>

        <SkillPanel title="Compute">
          <div className="preview-stack">
            <GaugeMetric metric={system.cpu} />
            <div className="mini-stat-grid three">
              <MiniStat label="Load 1m" value={system.load_average?.[0]?.toFixed(2) ?? 'n/a'} />
              <MiniStat label="Load 5m" value={system.load_average?.[1]?.toFixed(2) ?? 'n/a'} />
              <MiniStat label="Load 15m" value={system.load_average?.[2]?.toFixed(2) ?? 'n/a'} />
            </div>
            <CpuHistoryChart data={cpuData} />
          </div>
        </SkillPanel>

        <SkillPanel title="Memory">
          <div className="preview-stack">
            <GaugeMetric metric={system.memory} />
            {system.swap ? <GaugeMetric metric={system.swap} /> : <StatusPill label="swap disabled" tone="healthy" />}
            <MemoryHistoryChart data={memoryData} />
          </div>
        </SkillPanel>

        <SkillPanel title="Network">
          <div className="preview-stack">
            <div className="mini-stat-grid three">
              <MiniStat label="Links" value={`${physicalLinks.length} + ${virtualLinks.length} virt`} />
              <MiniStat label="Peers online" value={`${peers.online}/${peers.total}`} tone={peers.online === peers.total ? 'healthy' : 'warning'} />
              <MiniStat label="Platform" value={system.platform ?? 'n/a'} />
            </div>
            <NetworkHistoryChart data={networkData} />
            <div className="compact-list">
              {shownLinks.map((iface) => (
                <div className="compact-row" key={iface.name}>
                  <span className={`row-dot ${iface.up ? 'ok' : 'bad'}`} />
                  <span className="row-main">{iface.name}</span>
                  <span className="row-sub">RX {fmtBytes(iface.rx_bytes)}</span>
                  <span className="row-sub">TX {fmtBytes(iface.tx_bytes)}</span>
                </div>
              ))}
            </div>
            {virtualLinks.length > 0 && (
              <button
                type="button"
                className="ghost-button"
                onClick={() => setShowVirtual((current) => !current)}
              >
                {showVirtual
                  ? 'Hide virtual interfaces'
                  : `Show ${virtualLinks.length} virtual interface${virtualLinks.length === 1 ? '' : 's'}`}
              </button>
            )}
          </div>
        </SkillPanel>

        <SkillPanel title="Durability">
          <div className="preview-stack">
            <MetricBar
              label="Shards present"
              percent={collective.durability_percent}
              value={`${collective.shards_available}/${collective.shards_total}`}
              status={durabilityTone}
              detail={`Reed-Solomon ${erasure.data_shards}+${erasure.parity_shards} — any ${erasure.can_lose} shards may be lost per file`}
            />
            <div className="mini-stat-grid three">
              <MiniStat label="Files" value={String(collective.files ?? 0)} />
              <MiniStat label="Missing" value={String(collective.shards_missing ?? 0)} tone={durabilityTone} />
              <MiniStat label="Overhead" value={erasure.overhead_percent != null ? `${erasure.overhead_percent}%` : 'n/a'} />
            </div>
          </div>
        </SkillPanel>

        <SkillPanel title="Mounted Filesystem">
          <div className="preview-stack">
            {!filesystem.active && (
              <p className="muted">
                No mount is reporting. Start <code>collectivefs-mount.service</code> to serve this
                account at <code>/media/collectivefs</code>.
              </p>
            )}
            <div className="mini-stat-grid three">
              <MiniStat
                label="Throughput"
                value={`${fmtRate(fsTotals.read_bps)} / ${fmtRate(fsTotals.write_bps)}`}
                tone={filesystem.active ? 'healthy' : undefined}
              />
              <MiniStat label="Operations" value={`${(fsTotals.ops_per_sec ?? 0).toFixed(1)}/s`} />
              <MiniStat
                label="Errors"
                value={String(fsTotals.errors ?? 0)}
                tone={fsTotals.errors ? 'warning' : 'healthy'}
              />
            </div>

            <span className="metric-detail">Read and write throughput</span>
            <FilesystemThroughputChart data={fsSeries} />
            <span className="metric-detail">Operations per second and average latency</span>
            <FilesystemOpsChart data={fsSeries} />

            {filesystem.mounts?.length > 0 && (
              <div className="compact-list">
                {filesystem.mounts.map((mount) => (
                  <div className="compact-row" key={`${mount.node}-${mount.mountpoint}`}>
                    <span className={`row-dot ${Date.now() / 1000 - mount.last_seen < 30 ? 'ok' : 'warn'}`} />
                    <span className="row-main">{mount.mountpoint}</span>
                    <span className="row-sub">{mount.node}</span>
                    <span className="row-sub">{mount.files ?? 0} files</span>
                  </div>
                ))}
              </div>
            )}

            {filesystem.operations?.length > 0 && (
              <>
                <span className="metric-detail">Slowest operations in the window</span>
                <div className="compact-list">
                  {[...filesystem.operations]
                    .sort((a, b) => b.avg_ms - a.avg_ms)
                    .slice(0, full ? 8 : 4)
                    .map((operation) => (
                      <div className="compact-row" key={operation.op}>
                        <span className={`row-dot ${operation.errors ? 'warn' : 'ok'}`} />
                        <span className="row-main">{operation.op}</span>
                        <span className="row-sub">{operation.count} calls</span>
                        <span className="row-sub">
                          {operation.avg_ms}ms avg · {operation.max_ms}ms peak
                        </span>
                      </div>
                    ))}
                </div>
              </>
            )}
          </div>
        </SkillPanel>

        <SkillPanel title="Shard Placement">
          <div className="preview-stack">
            <div className="mini-stat-grid three">
              <MiniStat label="Held here" value={String(collective.shards_local ?? 0)} />
              <MiniStat
                label="On peers"
                value={String(collective.shards_remote ?? 0)}
                tone={collective.shards_remote > 0 ? 'healthy' : undefined}
              />
              <MiniStat label="Stored for peers" value={String(hosted.shards ?? 0)} />
            </div>

            <span className="metric-detail">Our shards, by location</span>
            <div className="compact-list">
              {placementRows.length === 0 && (
                <p className="muted">Nothing stored yet.</p>
              )}
              {placementRows.map(([where, count]) => (
                <div className="compact-row" key={where}>
                  <span className={`row-dot ${where === 'local' ? 'ok' : 'warn'}`} />
                  <span className="row-main">{where === 'local' ? 'This node' : where.replace(/^https?:\/\//, '')}</span>
                  <span className="row-sub">{count} shards</span>
                  <StatusPill label={where === 'local' ? 'local' : 'peer'} tone={where === 'local' ? 'healthy' : 'neutral'} />
                </div>
              ))}
            </div>

            {hosted.nodes?.length > 0 && (
              <>
                <span className="metric-detail">Shards we store for other nodes</span>
                <div className="compact-list">
                  {hosted.nodes.map((node) => (
                    <div className="compact-row" key={node.origin_node}>
                      <span className="row-dot ok" />
                      <span className="row-main">
                        {node.origin_url?.replace(/^https?:\/\//, '') || node.origin_node.slice(0, 12)}
                      </span>
                      <span className="row-sub">{node.shards} shards</span>
                      <span className="row-sub">{fmtBytes(node.bytes)}</span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        </SkillPanel>

        <SkillPanel title="Peers & Contracts">
          <div className="preview-stack">
            <div className="mini-stat-grid three">
              <MiniStat label="Known" value={String(peers.total)} />
              <MiniStat label="Online" value={String(peers.online)} tone={peers.online === peers.total ? 'healthy' : 'warning'} />
              <MiniStat label="Contracts" value={String(system.contracts?.total_contracts ?? 0)} />
            </div>
            {peers.items?.length ? (
              <div className="compact-list">
                {peers.items.slice(0, full ? undefined : 4).map((peer) => (
                  <div className="compact-row" key={peer.url}>
                    <span className={`row-dot ${peer.healthy ? 'ok' : 'bad'}`} />
                    <span className="row-main">{peer.url}</span>
                    <span className="row-sub">{peer.node_id ? peer.node_id.slice(0, 8) : 'unknown'}</span>
                    <StatusPill label={peer.healthy ? 'online' : 'offline'} tone={peer.healthy ? 'healthy' : 'offline'} />
                  </div>
                ))}
              </div>
            ) : (
              <p className="muted">No peers registered. This node is storing for itself only.</p>
            )}
          </div>
        </SkillPanel>
      </div>

      {full && config && (
        <SkillPanel title="Configuration">
          <div className="preview-stack">
            <p className="metric-detail">
              Changes are validated against this machine and written to an audit log. The chat can change
              anything here — and everything else in the schema — in plain language.
            </p>
            <div className="settings-grid">
              {QUICK_FIELDS.map((spec) => (
                <SettingControl key={spec.field} spec={spec} config={config} onApply={applyUpdates} busy={busy} />
              ))}
            </div>
            {audit.length > 0 && (
              <>
                <h3 style={{ margin: '6px 0 0', fontSize: '0.72rem', textTransform: 'uppercase' }}>Recent changes</h3>
                <div className="audit-list">
                  {audit.map((entry) => (
                    <div className="audit-row" key={entry.id}>
                      <span className="audit-when">{fmtDate(entry.at)}</span>
                      <StatusPill label={entry.source} tone="neutral" />
                      {entry.changes.map((change) => (
                        <span key={change.field} className="chat-applied-row">
                          <code>{change.field}</code>
                          <span className="before">{String(change.before)}</span>
                          <span>→</span>
                          <span className="after">{String(change.after)}</span>
                        </span>
                      ))}
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        </SkillPanel>
      )}
    </div>
  )
}
