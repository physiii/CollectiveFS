import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, Navigate, Route, Routes, useNavigate, useParams, useSearchParams } from 'react-router-dom'

import FileExplorer from './components/FileExplorer'
import SectionCard from './components/SectionCard'
import SystemPanel from './components/SystemPanel'
import { SectionGlyph, StatusPill } from './components/primitives'
import { eventsSocket, getConfig, getFileTree, getSystemOverview } from './lib/api'
import { fmtBytes, fmtPercent } from './lib/format'
import { SKILL_DOCS } from './lib/skillDocs'
import { useSystemHistory } from './lib/useSystemHistory'

const SECTIONS = [
  {
    id: 'files',
    title: 'Files',
    skillId: 'files',
    callSign: 'Archivist',
    icon: 'FS',
    description:
      'Every file this node stores, as a navigable tree. Files are Reed-Solomon shards, encrypted and spread across peers — shard health is shown alongside size.',
    suggestions: ['What is stored here?', 'Which files are missing shards?', 'How much space do my folders use?'],
  },
  {
    id: 'system',
    title: 'System & Infrastructure',
    skillId: 'system',
    callSign: 'Infrastructure Steward',
    icon: 'SY',
    description:
      'Node readiness across compute, memory, network, allocated storage, shard durability and peer contracts. This section can change the node, not just report on it.',
    suggestions: ['Allocate 500GB to the collective', 'Set parity shards to 6', 'How much headroom is left?'],
  },
]

function useToasts() {
  const [toasts, setToasts] = useState([])
  const notify = useCallback((message, tone = 'ok') => {
    const id = `${Date.now()}-${Math.random()}`
    setToasts((current) => [...current, { id, message, tone }])
    setTimeout(() => setToasts((current) => current.filter((toast) => toast.id !== id)), 5000)
  }, [])
  return { toasts, notify }
}

// Declared at module scope on purpose: a component defined inside App would get
// a new identity on every render, and the 5s telemetry poll re-renders App — which
// would remount the section and throw away explorer state (selection, search,
// expanded folders) several times a minute.
function SectionPage({ sections, renderSection }) {
  const params = useParams()
  const section = sections.find((item) => item.id === params.sectionId)
  if (!section) return <Navigate to="/" replace />
  return (
    <div className="section-page">
      <div className="section-page-bar">
        <Link className="back-link" to="/">
          Back to sections
        </Link>
      </div>
      {renderSection(section, { full: true, wide: true })}
    </div>
  )
}

function ToastStack({ toasts }) {
  if (!toasts.length) return null
  return (
    <div className="toast-stack" role="status" aria-live="polite">
      {toasts.map((toast) => (
        <div className={`toast ${toast.tone}`} key={toast.id}>
          {toast.message}
        </div>
      ))}
    </div>
  )
}

export default function App() {
  const navigate = useNavigate()
  const { toasts, notify } = useToasts()

  const [tree, setTree] = useState(null)
  const [system, setSystem] = useState(null)
  const [config, setConfig] = useState(null)
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  // Folder position lives in the URL so a view can be linked and survives reloads.
  const [searchParams, setSearchParams] = useSearchParams()
  const path = searchParams.get('path') ?? ''

  const history = useSystemHistory(5000)
  const latest = history[history.length - 1]
  const liveSystem = latest ?? system

  const refreshTimer = useRef(null)

  const refreshTree = useCallback(async () => {
    try {
      setTree(await getFileTree())
      setError(null)
    } catch (err) {
      setError(err.message)
    }
  }, [])

  const refreshConfig = useCallback(async () => {
    try {
      const payload = await getConfig()
      setConfig(payload.config)
    } catch {
      // Config is optional for rendering; the section still works without it.
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      const [treeResult, systemResult] = await Promise.allSettled([getFileTree(), getSystemOverview()])
      if (cancelled) return
      if (treeResult.status === 'fulfilled') setTree(treeResult.value)
      else setError(treeResult.reason?.message ?? 'Failed to load files.')
      if (systemResult.status === 'fulfilled') setSystem(systemResult.value)
      await refreshConfig()
      if (!cancelled) setLoading(false)
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [refreshConfig])

  // The node pushes pipeline and config events; debounce so a burst of shard
  // updates costs one refetch rather than one per event.
  useEffect(() => {
    let socket
    try {
      socket = eventsSocket()
      socket.onopen = () => setConnected(true)
      socket.onclose = () => setConnected(false)
      socket.onerror = () => setConnected(false)
      socket.onmessage = (event) => {
        let payload = {}
        try {
          payload = JSON.parse(event.data)
        } catch {
          return
        }
        if (payload.type === 'heartbeat' || payload.type === 'pong') return
        if (refreshTimer.current) clearTimeout(refreshTimer.current)
        refreshTimer.current = setTimeout(() => {
          void refreshTree()
          if (payload.type === 'config') void refreshConfig()
        }, 400)
      }
    } catch {
      setConnected(false)
    }
    return () => {
      if (refreshTimer.current) clearTimeout(refreshTimer.current)
      socket?.close()
    }
  }, [refreshTree, refreshConfig])

  const navigateFolder = useCallback(
    (next) => {
      setSearchParams(next ? { path: next } : {})
    },
    [setSearchParams],
  )

  const sectionBadge = useCallback(
    (section) => {
      if (section.id === 'files') {
        if (!tree) return 'loading'
        return `${tree.total_files} file${tree.total_files === 1 ? '' : 's'} · ${fmtBytes(tree.total_size)}`
      }
      if (!liveSystem) return 'node'
      const collective = liveSystem.collective ?? {}
      return `${liveSystem.hostname} · ${fmtPercent(collective.used_percent)} of ${fmtBytes(collective.quota_bytes)}`
    },
    [tree, liveSystem],
  )

  const sectionContext = useCallback(
    (section) => {
      const collective = liveSystem?.collective ?? {}
      return [
        `section=${section.id}`,
        `files=${tree?.total_files ?? 0}`,
        `logical=${tree?.total_size ?? 0}`,
        `folder=${path || 'root'}`,
        `quotaUsed=${collective.used_percent ?? 0}%`,
        `shards=${collective.shards_available ?? 0}/${collective.shards_total ?? 0}`,
      ].join('; ')
    },
    [tree, liveSystem, path],
  )

  const onConfigChanged = useCallback(() => {
    void refreshConfig()
    void refreshTree()
  }, [refreshConfig, refreshTree])

  const renderBody = useCallback(
    (section, full) => {
      if (section.id === 'files') {
        if (!tree) return <p className="muted">Loading files…</p>
        return (
          <FileExplorer
            tree={tree}
            path={path}
            onNavigate={navigateFolder}
            onRefresh={refreshTree}
            onNotify={notify}
            full={full}
          />
        )
      }
      return (
        <SystemPanel
          system={liveSystem}
          config={config}
          history={history}
          onConfigChanged={onConfigChanged}
          onNotify={notify}
          full={full}
        />
      )
    },
    [tree, path, navigateFolder, refreshTree, notify, liveSystem, config, history, onConfigChanged],
  )

  const renderSection = useCallback(
    (section, options = {}) => (
      <SectionCard
        key={section.id}
        title={section.title}
        icon={<SectionGlyph label={section.icon} />}
        sectionId={section.id}
        skillId={section.skillId}
        callSign={section.callSign}
        badge={sectionBadge(section)}
        description={options.full ? section.description : undefined}
        context={sectionContext(section)}
        skillMarkdown={SKILL_DOCS[section.id]}
        suggestions={section.suggestions}
        onOpen={options.full ? undefined : () => navigate(`/sections/${section.id}`)}
        onConfigChanged={onConfigChanged}
        wide={options.wide}
        full={options.full}
        startCollapsed={options.startCollapsed}
      >
        {renderBody(section, options.full)}
      </SectionCard>
    ),
    [sectionBadge, sectionContext, navigate, renderBody, onConfigChanged],
  )

  const home = useMemo(
    () => (
      <div className="dashboard-flow">
        {renderSection(SECTIONS[0], { wide: true })}
        {renderSection(SECTIONS[1], { wide: true })}
      </div>
    ),
    [renderSection],
  )

  return (
    <div className="cfs-shell">
      <header className="cfs-topbar">
        <div className="topbar-brand">CollectiveFS</div>
        <div className="explorer-actions">
          {liveSystem?.hostname && (
            <span className="section-card-call" title={liveSystem.node_id}>
              {liveSystem.hostname}
            </span>
          )}
          <StatusPill label={connected ? 'live' : 'reconnecting'} tone={connected ? 'healthy' : 'warning'} />
        </div>
      </header>
      <main className="console-main">
        {error && <div className="error-banner">{error}</div>}
        {loading ? (
          <div className="loading">Loading CollectiveFS…</div>
        ) : (
          <Routes>
            <Route path="/" element={home} />
            <Route
              path="/sections/:sectionId"
              element={<SectionPage sections={SECTIONS} renderSection={renderSection} />}
            />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        )}
      </main>
      <ToastStack toasts={toasts} />
    </div>
  )
}
