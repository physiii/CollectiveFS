import React, { useState, useMemo } from 'react'
import { LayoutGrid, List, ChevronUp, ChevronDown, UploadCloud } from 'lucide-react'
import clsx from 'clsx'
import { useFiles, useDeleteFile } from '../hooks/useFiles.js'
import { downloadFile } from '../api/client.js'
import toast from 'react-hot-toast'
import FileGrid from './FileGrid.jsx'
import FileList from './FileList.jsx'
import UploadZone from './UploadZone.jsx'
import Modal from './Modal.jsx'
import FileDetails from './FileDetails.jsx'

const SORT_OPTIONS = [
  { key: 'name',       label: 'Name' },
  { key: 'size',       label: 'Size' },
  { key: 'created_at', label: 'Date' },
  { key: 'status',     label: 'Status' },
]

export default function FileBrowser({ searchQuery, uploadTriggerRef }) {
  const { data: files = [], isLoading, isError } = useFiles()
  const { mutate: doDelete } = useDeleteFile()

  const [viewMode, setViewMode]       = useState('grid')  // 'grid' | 'list'
  const [sortKey, setSortKey]         = useState('created_at')
  const [sortDir, setSortDir]         = useState('desc')
  const [selectedFile, setSelectedFile] = useState(null)
  const [detailsOpen, setDetailsOpen]   = useState(false)

  const handleSort = (key) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir('asc')
    }
  }

  const filteredFiles = useMemo(() => {
    const q = searchQuery.toLowerCase().trim()
    let list = q ? files.filter((f) => f.name.toLowerCase().includes(q)) : files
    return [...list].sort((a, b) => {
      let av = a[sortKey] ?? ''
      let bv = b[sortKey] ?? ''
      if (sortKey === 'size') { av = Number(av); bv = Number(bv) }
      const cmp = av < bv ? -1 : av > bv ? 1 : 0
      return sortDir === 'asc' ? cmp : -cmp
    })
  }, [files, searchQuery, sortKey, sortDir])

  const handleDownload = async (file) => {
    try {
      await downloadFile(file.id, file.name)
    } catch (err) {
      toast.error(`Download failed: ${err.message}`)
    }
  }

  const handleDelete = (id) => {
    doDelete(id)
  }

  const handleFileClick = (file) => {
    setSelectedFile(file)
    setDetailsOpen(true)
  }

  return (
    <div data-testid="file-browser" className="flex flex-col h-full min-h-0">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-surface-200 bg-white shrink-0">
        <div className="flex items-center gap-1">
          {SORT_OPTIONS.map(({ key, label }) => (
            <button
              key={key}
              onClick={() => handleSort(key)}
              className={clsx(
                'flex items-center gap-1 px-2.5 py-1.5 rounded-md text-xs font-medium transition',
                sortKey === key
                  ? 'bg-primary-50 text-primary-700'
                  : 'text-surface-500 hover:bg-surface-50 hover:text-surface-700',
              )}
            >
              {label}
              {sortKey === key && (
                sortDir === 'asc'
                  ? <ChevronUp className="w-3 h-3" />
                  : <ChevronDown className="w-3 h-3" />
              )}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-1 bg-surface-100 rounded-lg p-0.5">
          <button
            data-testid="view-grid"
            onClick={() => setViewMode('grid')}
            className={clsx(
              'w-7 h-7 flex items-center justify-center rounded-md transition',
              viewMode === 'grid'
                ? 'bg-white shadow-sm text-primary-600'
                : 'text-surface-400 hover:text-surface-600',
            )}
          >
            <LayoutGrid className="w-4 h-4" />
          </button>
          <button
            data-testid="view-list"
            onClick={() => setViewMode('list')}
            className={clsx(
              'w-7 h-7 flex items-center justify-center rounded-md transition',
              viewMode === 'list'
                ? 'bg-white shadow-sm text-primary-600'
                : 'text-surface-400 hover:text-surface-600',
            )}
          >
            <List className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Content area wrapped in UploadZone */}
      <UploadZone triggerRef={uploadTriggerRef}>
        <div className="flex-1 overflow-y-auto min-h-0">
          {isLoading ? (
            <div className="flex flex-col items-center justify-center h-64 gap-3">
              <div className="w-8 h-8 border-2 border-primary-500 border-t-transparent rounded-full animate-spin" />
              <p className="text-sm text-surface-400">Loading files…</p>
            </div>
          ) : isError ? (
            <div className="flex flex-col items-center justify-center h-64 gap-3 text-center px-4">
              <div className="w-12 h-12 rounded-full bg-red-50 flex items-center justify-center">
                <span className="text-red-400 text-xl">!</span>
              </div>
              <p className="text-sm font-medium text-surface-700">Could not load files</p>
              <p className="text-xs text-surface-400">Check that the API server is running</p>
            </div>
          ) : filteredFiles.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-64 gap-4 text-center px-4">
              <div className="w-16 h-16 rounded-2xl bg-primary-50 flex items-center justify-center">
                <UploadCloud className="w-8 h-8 text-primary-400" />
              </div>
              <div>
                <p className="text-base font-semibold text-surface-700">
                  {searchQuery ? 'No files match your search' : 'No files yet'}
                </p>
                <p className="text-sm text-surface-400 mt-1">
                  {searchQuery
                    ? 'Try a different search term'
                    : 'Drag files here or click Upload to get started'}
                </p>
              </div>
            </div>
          ) : viewMode === 'grid' ? (
            <FileGrid
              files={filteredFiles}
              onDownload={handleDownload}
              onDelete={handleDelete}
              onFileClick={handleFileClick}
            />
          ) : (
            <FileList
              files={filteredFiles}
              onDownload={handleDownload}
              onDelete={handleDelete}
              onFileClick={handleFileClick}
            />
          )}
        </div>
      </UploadZone>

      {/* File details modal */}
      <Modal
        open={detailsOpen}
        onClose={() => setDetailsOpen(false)}
        title="File Details"
        size="md"
      >
        {selectedFile && (
          <FileDetails
            fileId={selectedFile.id}
            onDelete={handleDelete}
            onClose={() => setDetailsOpen(false)}
          />
        )}
      </Modal>
    </div>
  )
}
