import React from 'react'
import { Files, Clock, Lock, Settings, HardDrive } from 'lucide-react'
import clsx from 'clsx'
import { useStats } from '../hooks/useFiles.js'
import { formatBytes } from '../utils/fileUtils.js'

const NAV_ITEMS = [
  { id: 'files',    label: 'All Files',  icon: Files },
  { id: 'recent',   label: 'Recent',     icon: Clock },
  { id: 'encrypted',label: 'Encrypted',  icon: Lock },
  { id: 'settings', label: 'Settings',   icon: Settings },
]

export default function Sidebar({ currentView, onViewChange }) {
  const { data: stats } = useStats()

  // Rough capacity estimate: show something reasonable
  const usedBytes = stats?.storage_used_bytes ?? 0
  const maxBytes  = 10 * 1024 * 1024 * 1024 // 10 GB display cap
  const pct       = Math.min(100, (usedBytes / maxBytes) * 100)

  return (
    <aside
      data-testid="sidebar"
      className="w-[200px] shrink-0 bg-white border-r border-surface-200 flex flex-col h-full overflow-y-auto"
    >
      <nav className="flex-1 p-3 space-y-0.5">
        {NAV_ITEMS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => onViewChange(id)}
            className={clsx(
              'w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm font-medium transition text-left',
              currentView === id
                ? 'bg-primary-50 text-primary-700'
                : 'text-surface-600 hover:bg-surface-50 hover:text-surface-900',
            )}
          >
            <Icon
              className={clsx(
                'w-4 h-4',
                currentView === id ? 'text-primary-600' : 'text-surface-400',
              )}
            />
            {label}
          </button>
        ))}
      </nav>

      {/* Storage section */}
      <div className="p-4 border-t border-surface-100">
        <div className="flex items-center gap-2 mb-2">
          <HardDrive className="w-3.5 h-3.5 text-surface-400" />
          <span className="text-xs font-medium text-surface-500">Storage</span>
        </div>
        <div className="h-1.5 bg-surface-100 rounded-full overflow-hidden mb-1.5">
          <div
            className={clsx(
              'h-full rounded-full transition-all duration-500',
              pct > 80 ? 'bg-red-500' : pct > 60 ? 'bg-amber-500' : 'bg-primary-500',
            )}
            style={{ width: `${pct}%` }}
          />
        </div>
        <p className="text-xs text-surface-400">
          {formatBytes(usedBytes)} used
        </p>
      </div>

      {/* Version */}
      <div className="px-4 pb-3">
        <p className="text-[11px] text-surface-300 font-mono">v0.1.0</p>
      </div>
    </aside>
  )
}
