import { fmtBytes, fmtPercent, statusTone } from '../lib/format'

export function SectionGlyph({ label }) {
  return <span className="section-glyph">{label}</span>
}

export function MetricBar({ label, percent, value, status, detail }) {
  const safePercent = Math.max(0, Math.min(100, percent ?? 0))
  return (
    <div className="metric-line">
      <div className="metric-line-head">
        <span>{label}</span>
        <strong>{value ?? fmtPercent(percent)}</strong>
      </div>
      <div className="meter" aria-label={`${label} ${fmtPercent(percent)}`}>
        <span className={`meter-fill ${statusTone(status)}`} style={{ width: `${safePercent}%` }} />
      </div>
      {detail && <span className="metric-detail">{detail}</span>}
    </div>
  )
}

export function StatusPill({ label, tone }) {
  return <span className={`status-pill ${statusTone(tone ?? label)}`}>{label}</span>
}

export function MiniStat({ label, value, tone }) {
  return (
    <div className={`mini-stat ${tone ? statusTone(tone) : ''}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

export function SkillPanel({ title, children }) {
  return (
    <section className="skill-panel-block">
      <h3>{title}</h3>
      {children}
    </section>
  )
}

/** Renders a ResourceGauge from /api/system/overview. */
export function GaugeMetric({ metric }) {
  if (!metric) return null
  return (
    <MetricBar
      label={metric.label}
      percent={metric.percent}
      value={metric.unit === 'bytes' && metric.value != null ? fmtBytes(metric.value) : fmtPercent(metric.percent)}
      status={metric.status}
      detail={metric.detail}
    />
  )
}

export function DiskMetric({ disk }) {
  return (
    <MetricBar
      label={disk.label}
      percent={disk.used_percent}
      value={
        disk.used_bytes != null && disk.total_bytes != null
          ? `${fmtBytes(disk.used_bytes)} / ${fmtBytes(disk.total_bytes)}`
          : disk.status
      }
      status={disk.status}
      detail={disk.path}
    />
  )
}
