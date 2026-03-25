import React, { useState, useRef, useCallback } from 'react'
import TopBar from './components/TopBar.jsx'
import Sidebar from './components/Sidebar.jsx'
import FileBrowser from './components/FileBrowser.jsx'
import Settings from './components/Settings.jsx'
import StatusBar from './components/StatusBar.jsx'
import { useWebSocket } from './hooks/useWebSocket.js'

export default function App() {
  const [currentView, setCurrentView] = useState('files')
  const [searchQuery,  setSearchQuery]  = useState('')
  const [connected,    setConnected]    = useState(false)

  // Shared ref so TopBar upload button can open the dropzone
  const uploadTriggerRef = useRef(null)

  const handleConnected = useCallback((val) => setConnected(val), [])
  useWebSocket({ onConnected: handleConnected })

  const handleUploadClick = () => {
    if (uploadTriggerRef.current) {
      uploadTriggerRef.current()
    }
  }

  // For "Recent" and "Encrypted" views we still show FileBrowser with a special filter;
  // for simplicity those views are treated as file-browser variants with no extra filter
  // (the back-end doesn't yet expose those semantics).
  const showFileBrowser = ['files', 'recent', 'encrypted'].includes(currentView)

  return (
    <div className="flex flex-col h-screen overflow-hidden bg-surface-50">
      <TopBar
        onUploadClick={handleUploadClick}
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        connected={connected}
      />

      <div className="flex flex-1 min-h-0 overflow-hidden">
        <Sidebar currentView={currentView} onViewChange={setCurrentView} />

        <main className="flex-1 flex flex-col min-h-0 overflow-hidden">
          {showFileBrowser ? (
            <FileBrowser
              searchQuery={searchQuery}
              uploadTriggerRef={uploadTriggerRef}
            />
          ) : (
            <div className="flex-1 overflow-y-auto">
              <Settings />
            </div>
          )}
        </main>
      </div>

      <StatusBar connected={connected} />
    </div>
  )
}
