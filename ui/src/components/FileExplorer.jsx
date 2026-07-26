import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  createFolder,
  deleteFile,
  deleteFolder,
  downloadUrl,
  getFile,
  updateFile,
  uploadFile,
} from '../lib/api'
import { fileKind, fmtBytes, fmtDate, statusTone } from '../lib/format'
import { StatusPill } from './primitives'
import {
  ChevronRight,
  CloseIcon,
  DownloadIcon,
  FolderIcon,
  GridIcon,
  KindIcon,
  ListIcon,
  NewFolderIcon,
  PencilIcon,
  TrashIcon,
  UploadIcon,
} from './icons'

const SORTS = {
  name: (a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' }),
  size: (a, b) => (b.size ?? 0) - (a.size ?? 0),
  created: (a, b) => String(b.created_at ?? '').localeCompare(String(a.created_at ?? '')),
}

function shardTone(available, total) {
  if (!total) return 'ok'
  const missing = total - available
  if (missing === 0) return 'ok'
  return missing <= Math.floor(total / 4) ? 'warn' : 'bad'
}

function ShardBar({ available, total }) {
  const percent = total ? Math.round((available / total) * 100) : 100
  return (
    <div className="entry-shards" title={`${available} of ${total} shards present`}>
      <div className={`shard-bar ${shardTone(available, total)}`}>
        <span style={{ width: `${percent}%` }} />
      </div>
      <span className="entry-meta">{total ? `${available}/${total}` : '—'}</span>
    </div>
  )
}

// ── folder tree ─────────────────────────────────────────────────────

function TreeNode({ node, depth, current, expanded, onToggle, onSelect }) {
  const hasChildren = node.children.length > 0
  const isOpen = expanded.has(node.path)
  const isActive = current === node.path
  return (
    <>
      <div
        className={`tree-row ${isActive ? 'active' : ''}`}
        style={{ paddingLeft: 8 + depth * 12 }}
        role="treeitem"
        aria-selected={isActive}
        aria-expanded={hasChildren ? isOpen : undefined}
      >
        <button
          type="button"
          className={`tree-caret ${hasChildren ? '' : 'leaf'} ${isOpen ? 'open' : ''}`}
          onClick={() => onToggle(node.path)}
          aria-label={isOpen ? `Collapse ${node.name}` : `Expand ${node.name}`}
          tabIndex={hasChildren ? 0 : -1}
        >
          <ChevronRight />
        </button>
        <span className="entry-icon folder" style={{ width: 20, height: 20 }}>
          <FolderIcon />
        </span>
        <button
          type="button"
          className="tree-name"
          onClick={() => onSelect(node.path)}
          style={{ border: 0, background: 'none', color: 'inherit', font: 'inherit', textAlign: 'left', padding: 0 }}
        >
          {node.name}
        </button>
        <span className="tree-count">{node.file_count}</span>
      </div>
      {hasChildren &&
        isOpen &&
        node.children.map((child) => (
          <TreeNode
            key={child.path}
            node={child}
            depth={depth + 1}
            current={current}
            expanded={expanded}
            onToggle={onToggle}
            onSelect={onSelect}
          />
        ))}
    </>
  )
}

// ── detail drawer ───────────────────────────────────────────────────

function DetailDrawer({ entry, folders, onClose, onRefresh, onNotify }) {
  const [detail, setDetail] = useState(null)
  const [name, setName] = useState(entry.name)
  const [folder, setFolder] = useState(entry.folder)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    setDetail(null)
    setName(entry.name)
    setFolder(entry.folder)
    getFile(entry.id)
      .then((payload) => {
        if (!cancelled) setDetail(payload)
      })
      .catch(() => {
        if (!cancelled) setDetail({ shard_list: [] })
      })
    return () => {
      cancelled = true
    }
  }, [entry.id, entry.name, entry.folder])

  const shards = detail?.shard_list ?? []
  const dataShards = Math.max(shards.length - Math.ceil(shards.length / 3), 0)
  const dirty = name !== entry.name || folder !== entry.folder

  async function save() {
    setBusy(true)
    try {
      await updateFile(entry.id, { name, folder })
      onNotify(`Saved ${name}`, 'ok')
      onRefresh()
    } catch (error) {
      onNotify(error.message, 'bad')
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    setBusy(true)
    try {
      await deleteFile(entry.id)
      onNotify(`Deleted ${entry.name}`, 'ok')
      onClose()
      onRefresh()
    } catch (error) {
      onNotify(error.message, 'bad')
    } finally {
      setBusy(false)
    }
  }

  return (
    <aside className="detail-drawer" data-testid="file-detail">
      <div className="detail-drawer-head">
        <h4>{entry.name}</h4>
        <button type="button" className="icon-control" onClick={onClose} aria-label="Close details">
          <CloseIcon />
        </button>
      </div>

      <div className="detail-facts">
        <div className="detail-fact">
          <span>Size</span>
          <strong>{fmtBytes(entry.size)}</strong>
        </div>
        <div className="detail-fact">
          <span>Stored</span>
          <strong>{fmtDate(entry.created_at)}</strong>
        </div>
        <div className="detail-fact">
          <span>Shards</span>
          <strong>
            {entry.shards_available}/{entry.shards_total}
          </strong>
        </div>
        <div className="detail-fact">
          <span>Status</span>
          <strong>
            <StatusPill label={entry.status} tone={entry.status} />
          </strong>
        </div>
        <div className="detail-fact">
          <span>Folder</span>
          <strong>{entry.folder || 'root'}</strong>
        </div>
        <div className="detail-fact">
          <span>File ID</span>
          <strong style={{ fontSize: '0.68rem' }}>{entry.id}</strong>
        </div>
      </div>

      {shards.length > 0 && (
        <div>
          <span className="metric-detail">Shard map — data, parity, missing</span>
          <div className="shard-map" style={{ marginTop: 6 }}>
            {shards.map((shard, index) => (
              <span
                key={shard.id ?? index}
                className={`shard-cell ${!shard.available ? 'missing' : index < dataShards ? 'data' : 'parity'}`}
                title={`Shard ${shard.num} · ${fmtBytes(shard.size)} · ${shard.available ? 'present' : 'missing'}${
                  shard.peer ? ` · ${shard.peer}` : ''
                }`}
              >
                {shard.num}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="inline-form">
        <input value={name} onChange={(event) => setName(event.target.value)} aria-label="File name" />
        <select value={folder} onChange={(event) => setFolder(event.target.value)} aria-label="Folder">
          <option value="">/ (root)</option>
          {folders
            .filter((item) => item.path)
            .map((item) => (
              <option key={item.path} value={item.path}>
                {item.path}
              </option>
            ))}
        </select>
      </div>

      <div className="explorer-actions">
        <button type="button" className="ghost-button accent" onClick={save} disabled={!dirty || busy}>
          <PencilIcon /> Save
        </button>
        <a className="ghost-button" href={downloadUrl(entry.id)}>
          <DownloadIcon /> Download
        </a>
        <button type="button" className="ghost-button" onClick={remove} disabled={busy}>
          <TrashIcon /> Delete
        </button>
      </div>
    </aside>
  )
}

// ── explorer ────────────────────────────────────────────────────────

export default function FileExplorer({ tree, path, onNavigate, onRefresh, onNotify, full = false }) {
  const [view, setView] = useState('list')
  const [sort, setSort] = useState('name')
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState(null)
  const [expanded, setExpanded] = useState(() => new Set(['']))
  const [dragging, setDragging] = useState(false)
  const [uploads, setUploads] = useState([])
  const fileInputRef = useRef(null)
  const dragDepth = useRef(0)

  const folders = tree?.folders ?? []
  const files = tree?.files ?? []

  // Keep every ancestor of the current folder open so the tree tracks navigation.
  useEffect(() => {
    if (!path) return
    setExpanded((current) => {
      const next = new Set(current)
      next.add('')
      const parts = path.split('/')
      for (let index = 1; index <= parts.length; index++) next.add(parts.slice(0, index).join('/'))
      return next
    })
  }, [path])

  const childFolders = useMemo(
    () => folders.filter((folder) => folder.parent === path && folder.path !== path).sort(SORTS.name),
    [folders, path],
  )

  const visibleFiles = useMemo(() => {
    const needle = query.trim().toLowerCase()
    // A search looks through everything below the current folder; without one we
    // only show direct children, which is what a file manager should do.
    const scope = needle
      ? files.filter((file) => (path ? file.folder === path || file.folder.startsWith(`${path}/`) : true))
      : files.filter((file) => file.folder === path)
    const filtered = needle ? scope.filter((file) => file.name.toLowerCase().includes(needle)) : scope
    return [...filtered].sort(SORTS[sort])
  }, [files, path, query, sort])

  const matchingFolders = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return childFolders
    return childFolders.filter((folder) => folder.name.toLowerCase().includes(needle))
  }, [childFolders, query])

  const breadcrumbs = useMemo(() => {
    const crumbs = [{ path: '', name: 'All Files' }]
    if (path) {
      const parts = path.split('/')
      for (let index = 1; index <= parts.length; index++) {
        crumbs.push({ path: parts.slice(0, index).join('/'), name: parts[index - 1] })
      }
    }
    return crumbs
  }, [path])

  const toggleNode = useCallback((target) => {
    setExpanded((current) => {
      const next = new Set(current)
      if (next.has(target)) next.delete(target)
      else next.add(target)
      return next
    })
  }, [])

  const runUploads = useCallback(
    async (list) => {
      const items = Array.from(list ?? [])
      if (!items.length) return
      for (const file of items) {
        const key = `${file.name}-${Date.now()}-${Math.random()}`
        setUploads((current) => [...current, { key, name: file.name, progress: 0 }])
        try {
          await uploadFile(file, path, (progress) =>
            setUploads((current) => current.map((item) => (item.key === key ? { ...item, progress } : item))),
          )
          onNotify(`Uploading ${file.name} — encoding into shards`, 'ok')
        } catch (error) {
          onNotify(error.message, 'bad')
        } finally {
          setUploads((current) => current.filter((item) => item.key !== key))
        }
      }
      // The encode pipeline is asynchronous; refresh once it has had a moment.
      onRefresh()
      setTimeout(onRefresh, 1500)
    },
    [path, onNotify, onRefresh],
  )

  async function addFolder() {
    const name = window.prompt('New folder name')
    if (!name) return
    try {
      await createFolder(path ? `${path}/${name}` : name)
      onNotify(`Created ${name}`, 'ok')
      onRefresh()
    } catch (error) {
      onNotify(error.message, 'bad')
    }
  }

  async function removeFolder(target) {
    if (!window.confirm(`Remove folder "${target}"? Files inside move to the root; nothing is deleted.`)) return
    try {
      const result = await deleteFolder(target)
      onNotify(`Removed ${target}${result.files_moved_to_root ? ` — ${result.files_moved_to_root} file(s) moved to root` : ''}`, 'ok')
      if (path === target || path.startsWith(`${target}/`)) onNavigate('')
      onRefresh()
    } catch (error) {
      onNotify(error.message, 'bad')
    }
  }

  const totalSize = visibleFiles.reduce((sum, file) => sum + (file.size ?? 0), 0)
  const isEmpty = matchingFolders.length === 0 && visibleFiles.length === 0

  return (
    <div
      className="explorer"
      style={{ position: 'relative' }}
      onDragEnter={(event) => {
        event.preventDefault()
        dragDepth.current += 1
        setDragging(true)
      }}
      onDragOver={(event) => event.preventDefault()}
      onDragLeave={() => {
        dragDepth.current = Math.max(dragDepth.current - 1, 0)
        if (dragDepth.current === 0) setDragging(false)
      }}
      onDrop={(event) => {
        event.preventDefault()
        dragDepth.current = 0
        setDragging(false)
        void runUploads(event.dataTransfer?.files)
      }}
      data-testid="file-explorer"
    >
      {dragging && <div className="drop-hint">Drop to upload into {path || 'All Files'}</div>}

      <aside className="explorer-aside">
        <div className="explorer-aside-head">
          <span>Folders</span>
          <button type="button" className="tree-caret" onClick={addFolder} aria-label="New folder" title="New folder">
            <NewFolderIcon />
          </button>
        </div>
        <div className="tree" role="tree" aria-label="Folder tree">
          {tree?.tree && (
            <TreeNode
              node={tree.tree}
              depth={0}
              current={path}
              expanded={expanded}
              onToggle={toggleNode}
              onSelect={onNavigate}
            />
          )}
        </div>
      </aside>

      <div className="explorer-main">
        <div className="explorer-toolbar">
          <nav className="breadcrumbs" aria-label="Breadcrumb">
            {breadcrumbs.map((crumb, index) => (
              <span key={crumb.path || 'root'} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                {index > 0 && <span className="crumb-sep">/</span>}
                <button
                  type="button"
                  className={`crumb ${index === breadcrumbs.length - 1 ? 'current' : ''}`}
                  onClick={() => onNavigate(crumb.path)}
                >
                  {crumb.name}
                </button>
              </span>
            ))}
          </nav>

          <div className="explorer-actions">
            <input
              className="explorer-search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search files…"
              aria-label="Search files"
            />
            <div className="view-toggle" role="group" aria-label="View mode">
              <button
                type="button"
                className={view === 'list' ? 'active' : ''}
                onClick={() => setView('list')}
                aria-label="List view"
                aria-pressed={view === 'list'}
              >
                <ListIcon />
              </button>
              <button
                type="button"
                className={view === 'grid' ? 'active' : ''}
                onClick={() => setView('grid')}
                aria-label="Grid view"
                aria-pressed={view === 'grid'}
              >
                <GridIcon />
              </button>
            </div>
            <button type="button" className="ghost-button" onClick={addFolder}>
              <NewFolderIcon /> Folder
            </button>
            <button type="button" className="ghost-button accent" onClick={() => fileInputRef.current?.click()}>
              <UploadIcon /> Upload
            </button>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              hidden
              onChange={(event) => {
                void runUploads(event.target.files)
                event.target.value = ''
              }}
            />
          </div>
        </div>

        {uploads.length > 0 && (
          <div className="compact-list">
            {uploads.map((item) => (
              <div className="compact-row" key={item.key}>
                <span className="row-dot warn" />
                <span className="row-main">{item.name}</span>
                <span className="row-sub">{Math.round(item.progress * 100)}%</span>
                <StatusPill label="uploading" tone="warning" />
              </div>
            ))}
          </div>
        )}

        {isEmpty ? (
          <div className="explorer-empty">
            <FolderIcon />
            <p className="muted">
              {query ? `Nothing matches “${query}”.` : 'This folder is empty. Drop files here or use Upload.'}
            </p>
          </div>
        ) : view === 'list' ? (
          <div className="entry-list" role="table" aria-label="Files and folders">
            <div className="entry-head" role="row">
              <span />
              <button type="button" onClick={() => setSort('name')}>
                Name {sort === 'name' ? '▾' : ''}
              </button>
              <button type="button" onClick={() => setSort('size')}>
                Size {sort === 'size' ? '▾' : ''}
              </button>
              <span className="hide-narrow">Shards</span>
              <button type="button" className="hide-narrow" onClick={() => setSort('created')}>
                Stored {sort === 'created' ? '▾' : ''}
              </button>
              <span />
            </div>

            {matchingFolders.map((folder) => (
              <div className="entry-row" role="row" key={`folder-${folder.path}`}>
                <span className="entry-icon folder">
                  <FolderIcon />
                </span>
                <button
                  type="button"
                  className="entry-name"
                  style={{ border: 0, background: 'none', color: 'inherit', font: 'inherit', textAlign: 'left', padding: 0 }}
                  onClick={() => onNavigate(folder.path)}
                >
                  {folder.name}
                </button>
                <span className="entry-meta">{fmtBytes(folder.size)}</span>
                <span className="entry-meta hide-narrow">
                  {folder.file_count} file{folder.file_count === 1 ? '' : 's'}
                </span>
                <span className="entry-meta hide-narrow">—</span>
                <button
                  type="button"
                  className="tree-caret"
                  onClick={() => removeFolder(folder.path)}
                  aria-label={`Remove folder ${folder.name}`}
                  title="Remove folder"
                >
                  <TrashIcon />
                </button>
              </div>
            ))}

            {visibleFiles.map((file) => (
              <div
                className={`entry-row ${selected?.id === file.id ? 'selected' : ''}`}
                role="row"
                key={file.id}
              >
                <span className={`entry-icon ${statusTone(file.status)}`}>
                  <KindIcon kind={fileKind(file.name)} />
                </span>
                <button
                  type="button"
                  className="entry-name"
                  style={{ border: 0, background: 'none', color: 'inherit', font: 'inherit', textAlign: 'left', padding: 0 }}
                  onClick={() => setSelected(file)}
                  title={file.path}
                >
                  {file.name}
                  {query && file.folder ? <span className="entry-meta"> · {file.folder}</span> : null}
                </button>
                <span className="entry-meta">{fmtBytes(file.size)}</span>
                <span className="hide-narrow">
                  <ShardBar available={file.shards_available} total={file.shards_total} />
                </span>
                <span className="entry-meta hide-narrow">
                  {file.status === 'stored' || file.status === 'complete' ? (
                    fmtDate(file.created_at)
                  ) : (
                    <StatusPill label={file.status} tone={file.status} />
                  )}
                </span>
                <a
                  className="tree-caret"
                  href={downloadUrl(file.id)}
                  aria-label={`Download ${file.name}`}
                  title="Download"
                >
                  <DownloadIcon />
                </a>
              </div>
            ))}
          </div>
        ) : (
          <div className="entry-grid">
            {matchingFolders.map((folder) => (
              <button
                type="button"
                className="entry-tile"
                key={`folder-${folder.path}`}
                onClick={() => onNavigate(folder.path)}
              >
                <span className="entry-icon folder">
                  <FolderIcon />
                </span>
                <span className="entry-name">{folder.name}</span>
                <span className="entry-meta">
                  {folder.file_count} file{folder.file_count === 1 ? '' : 's'} · {fmtBytes(folder.size)}
                </span>
              </button>
            ))}
            {visibleFiles.map((file) => (
              <button
                type="button"
                className={`entry-tile ${selected?.id === file.id ? 'selected' : ''}`}
                key={file.id}
                onClick={() => setSelected(file)}
              >
                <span className={`entry-icon ${statusTone(file.status)}`}>
                  <KindIcon kind={fileKind(file.name)} />
                </span>
                <span className="entry-name">{file.name}</span>
                <span className="entry-meta">{fmtBytes(file.size)}</span>
                <ShardBar available={file.shards_available} total={file.shards_total} />
              </button>
            ))}
          </div>
        )}

        {selected && (
          <DetailDrawer
            entry={visibleFiles.find((file) => file.id === selected.id) ?? selected}
            folders={folders}
            onClose={() => setSelected(null)}
            onRefresh={onRefresh}
            onNotify={onNotify}
          />
        )}

        {full && (
          <div className="explorer-footer">
            <span>
              {matchingFolders.length} folder{matchingFolders.length === 1 ? '' : 's'} ·{' '}
              {visibleFiles.length} file{visibleFiles.length === 1 ? '' : 's'} · {fmtBytes(totalSize)} shown
            </span>
            <span>
              {tree?.total_files ?? 0} files stored on this node · {fmtBytes(tree?.total_size ?? 0)} logical
            </span>
          </div>
        )}
      </div>
    </div>
  )
}
