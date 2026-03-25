import React from 'react'
import { Network, Search, Upload, Wifi, WifiOff } from 'lucide-react'
import clsx from 'clsx'
import { useStats } from '../hooks/useFiles.js'
import { formatBytes } from '../utils/fileUtils.js'

export default function TopBar({ onUploadClick, searchQuery, onSearchChange, connected }) {
  const { data: stats } = useStats()

  return (
    <header
      data-testid="top-bar"
      className="h-14 bg-white border-b border-surface-200 flex items-center px-4 gap-4 shrink-0 z-10"
    >
      {/* Logo */}
      <div className="flex items-center gap-2.5 min-w-[180px]">
        <div className="w-7 h-7 bg-gradient-to-br from-primary-600 to-accent-600 rounded-lg flex items-center justify-center shadow-sm">
          <Network className="w-4 h-4 text-white" />
        </div>
        <span className="font-semibold text-surface-900 tracking-tight text-[15px]">
          CollectiveFS
        </span>
      </div>

      {/* Search */}
      <div className="flex-1 max-w-lg relative">
        <Search className="w-4 h-4 text-surface-400 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
        <input
          data-testid="search-input"
          type="text"
          placeholder="Search files…"
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
          className="w-full h-9 pl-9 pr-4 text-sm bg-surface-50 border border-surface-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-500/30 focus:border-primary-400 transition placeholder-surface-400"
        />
      </div>

      <div className="flex items-center gap-3 ml-auto">
        {/* Stats summary */}
        {stats && (
          <div className="hidden md:flex items-center gap-4 text-xs text-surface-500 border-r border-surface-200 pr-4">
            <span>
              <span className="font-medium text-surface-700">{stats.total_files}</span> files
            </span>
            <span>
              <span className="font-medium text-surface-700">
                {formatBytes(stats.storage_used_bytes)}
              </span>{' '}
              used
            </span>
          </div>
        )}

        {/* Connection indicator */}
        <div
          className={clsx(
            'flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full',
            connected
              ? 'bg-emerald-50 text-emerald-600'
              : 'bg-red-50 text-red-500',
          )}
        >
          {connected ? (
            <Wifi className="w-3.5 h-3.5" />
          ) : (
            <WifiOff className="w-3.5 h-3.5" />
          )}
          <span className="hidden sm:inline">{connected ? 'Connected' : 'Offline'}</span>
        </div>

        {/* Upload button */}
        <button
          data-testid="upload-button"
          onClick={onUploadClick}
          className="flex items-center gap-2 h-9 px-4 bg-primary-600 hover:bg-primary-700 text-white text-sm font-medium rounded-lg transition shadow-sm"
        >
          <Upload className="w-4 h-4" />
          <span>Upload</span>
        </button>
      </div>
    </header>
  )
}
