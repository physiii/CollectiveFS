import React from 'react'
import clsx from 'clsx'
import { Shield, Share2, Database } from 'lucide-react'
import { useStats } from '../hooks/useFiles.js'
import { formatBytes } from '../utils/fileUtils.js'

export default function StatusBar({ connected }) {
  const { data: stats } = useStats()

  return (
    <footer
      data-testid="status-bar"
      className="h-8 bg-surface-900 text-surface-300 flex items-center px-4 gap-6 text-xs shrink-0"
    >
      {/* Connection dot */}
      <div className="flex items-center gap-1.5">
        <span
          className={clsx(
            'inline-block w-2 h-2 rounded-full',
            connected ? 'bg-emerald-400 animate-pulse-subtle' : 'bg-red-400',
          )}
        />
        <span className={connected ? 'text-emerald-400' : 'text-red-400'}>
          {connected ? 'API Online' : 'API Offline'}
        </span>
      </div>

      <div className="h-3 w-px bg-surface-700" />

      {/* Files count */}
      <div className="flex items-center gap-1.5">
        <Database className="w-3 h-3 text-surface-500" />
        <span data-testid="stats-total-files">
          {stats?.total_files ?? '—'} files
        </span>
      </div>

      {/* Storage */}
      <div className="flex items-center gap-1.5">
        <span data-testid="stats-storage-used">
          {stats ? formatBytes(stats.storage_used_bytes) : '—'} stored
        </span>
      </div>

      <div className="h-3 w-px bg-surface-700" />

      {/* Erasure coding */}
      <div className="flex items-center gap-1.5">
        <Share2 className="w-3 h-3 text-surface-500" />
        <span className="text-surface-400">
          {stats?.erasure_coding ?? 'Reed-Solomon 8+4'}
        </span>
      </div>

      {/* Encryption */}
      <div className="flex items-center gap-1.5">
        <Shield className="w-3 h-3 text-surface-500" />
        <span className="text-surface-400">
          {stats?.encryption ?? 'Fernet AES-128'}
        </span>
      </div>
    </footer>
  )
}
