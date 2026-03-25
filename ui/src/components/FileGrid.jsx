import React from 'react'
import FileCard from './FileCard.jsx'

export default function FileGrid({ files, onDownload, onDelete, onFileClick }) {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3 p-4">
      {files.map((file) => (
        <FileCard
          key={file.id}
          file={file}
          onDownload={onDownload}
          onDelete={onDelete}
          onClick={onFileClick}
        />
      ))}
    </div>
  )
}
