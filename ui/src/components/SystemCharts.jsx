import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

function fmtTime(ts) {
  const date = new Date(ts)
  const pad = (value) => String(value).padStart(2, '0')
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

function fmtRate(value) {
  if (Math.abs(value) >= 1e9) return `${(value / 1e9).toFixed(2)} GB/s`
  if (Math.abs(value) >= 1e6) return `${(value / 1e6).toFixed(1)} MB/s`
  if (Math.abs(value) >= 1e3) return `${(value / 1e3).toFixed(1)} KB/s`
  return `${value.toFixed(0)} B/s`
}

export function RealtimeAreaChart({ data, lines, height = 120, unit = '%', yDomain, valueFormat }) {
  if (!data.length) return <p className="muted chart-empty">Collecting data…</p>
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: -12 }}>
        <defs>
          {lines.map((line) => (
            <linearGradient key={`grad-${line.key}`} id={`grad-${line.key}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={line.color} stopOpacity={0.35} />
              <stop offset="100%" stopColor={line.color} stopOpacity={0.02} />
            </linearGradient>
          ))}
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#1a2a44" />
        <XAxis
          dataKey="_ts"
          tickFormatter={fmtTime}
          stroke="#4a5f80"
          tick={{ fontSize: 10, fill: '#6f85a8' }}
          interval="preserveStartEnd"
          minTickGap={40}
        />
        <YAxis
          stroke="#4a5f80"
          tick={{ fontSize: 10, fill: '#6f85a8' }}
          unit={unit}
          domain={yDomain ?? [0, 'auto']}
          width={44}
          tickFormatter={valueFormat}
        />
        <Tooltip
          labelFormatter={(ts) => fmtTime(ts)}
          formatter={(value, name) => {
            const numeric = typeof value === 'number' ? value : Number(value) || 0
            if (valueFormat) return [valueFormat(numeric), name]
            return [`${numeric.toFixed(1)}${unit}`, name]
          }}
          contentStyle={{
            background: '#0d1b33',
            border: '1px solid #1e3256',
            borderRadius: 6,
            fontSize: '0.75rem',
            color: '#cdd6e9',
          }}
        />
        {lines.map((line) => (
          <Area
            key={line.key}
            type="monotone"
            dataKey={line.key}
            name={line.name ?? line.key}
            stroke={line.color}
            strokeWidth={1.5}
            fill={`url(#grad-${line.key})`}
            dot={false}
            activeDot={{ r: 3, stroke: line.color, strokeWidth: 1, fill: '#0d1b33' }}
            isAnimationActive={false}
          />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  )
}

export function CpuHistoryChart({ data }) {
  if (data.length < 2) return <p className="muted chart-empty">Collecting data…</p>
  return (
    <RealtimeAreaChart
      data={data}
      lines={[{ key: 'cpu', color: '#5fc48a', name: 'CPU %' }]}
      yDomain={[0, 100]}
    />
  )
}

export function MemoryHistoryChart({ data }) {
  if (data.length < 2) return <p className="muted chart-empty">Collecting data…</p>
  return (
    <RealtimeAreaChart
      data={data}
      lines={[{ key: 'memory', color: '#6f8bff', name: 'Memory %' }]}
      yDomain={[0, 100]}
    />
  )
}

export function NetworkHistoryChart({ data }) {
  if (data.length < 2) return <p className="muted chart-empty">Collecting data…</p>
  return (
    <RealtimeAreaChart
      data={data}
      lines={[
        { key: 'rx', color: '#33d06b', name: 'Received' },
        { key: 'tx', color: '#e8a030', name: 'Sent' },
      ]}
      unit=""
      valueFormat={fmtRate}
    />
  )
}

export function FilesystemThroughputChart({ data }) {
  if (data.length < 2) return <p className="muted chart-empty">Collecting data…</p>
  return (
    <RealtimeAreaChart
      data={data}
      lines={[
        { key: 'read_bps', color: '#33d06b', name: 'Read' },
        { key: 'write_bps', color: '#e8a030', name: 'Write' },
      ]}
      unit=""
      valueFormat={fmtRate}
    />
  )
}

export function FilesystemOpsChart({ data }) {
  if (data.length < 2) return <p className="muted chart-empty">Collecting data…</p>
  return (
    <RealtimeAreaChart
      data={data}
      lines={[
        { key: 'ops_per_sec', color: '#5aa8d6', name: 'Operations' },
        { key: 'avg_latency_ms', color: '#d49a3a', name: 'Latency ms' },
      ]}
      unit=""
      valueFormat={(value) => (value >= 100 ? value.toFixed(0) : value.toFixed(1))}
    />
  )
}

export function StorageHistoryChart({ data }) {
  if (data.length < 2) return <p className="muted chart-empty">Collecting data…</p>
  return (
    <RealtimeAreaChart
      data={data}
      lines={[{ key: 'quota', color: '#d49a3a', name: 'Quota used %' }]}
      yDomain={[0, 100]}
    />
  )
}
