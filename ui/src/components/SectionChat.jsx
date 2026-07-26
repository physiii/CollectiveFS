import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useNavigate } from 'react-router-dom'

import { getProviders, postChat, putConfig } from '../lib/api'
import { fmtBytes } from '../lib/format'
import { SendIcon } from './icons'

function formatChangeValue(change, key) {
  const value = change[key]
  if (change.type === 'bytes') return fmtBytes(value)
  if (typeof value === 'boolean') return value ? 'enabled' : 'disabled'
  return String(value)
}

function AppliedChanges({ changes }) {
  if (!changes?.length) return null
  return (
    <div className="chat-applied">
      <span className="chat-applied-title">Configuration applied</span>
      {changes.map((change) => (
        <div className="chat-applied-row" key={change.field}>
          <code>{change.field}</code>
          <span className="before">{formatChangeValue(change, 'before')}</span>
          <span>→</span>
          <span className="after">{formatChangeValue(change, 'after')}</span>
        </div>
      ))}
    </div>
  )
}

export default function SectionChat({ sectionId, skillId, callSign, context, suggestions, onConfigChanged }) {
  const navigate = useNavigate()
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [providers, setProviders] = useState([])
  const [active, setActive] = useState('codewhale')
  const scrollRef = useRef(null)

  useEffect(() => {
    let cancelled = false
    getProviders()
      .then((payload) => {
        if (cancelled) return
        setProviders(payload.providers ?? [])
        setActive(payload.active ?? 'codewhale')
      })
      .catch(() => {
        // The switcher stays hidden if the node cannot report providers.
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [messages, loading])

  async function selectProvider(id) {
    const previous = active
    setActive(id)
    try {
      // Persist so every section — and the next page load — uses it.
      await putConfig({ 'agent.provider': id })
      onConfigChanged?.()
    } catch (error) {
      setActive(previous)
      setMessages((current) => [
        ...current,
        { id: `e-${Date.now()}`, role: 'assistant', content: `Could not switch provider: ${error.message}` },
      ])
    }
  }

  async function send(raw) {
    const question = raw.trim()
    if (!question || loading) return
    const userMessage = { id: `u-${Date.now()}`, role: 'user', content: question }
    setMessages((current) => [...current, userMessage])
    setLoading(true)
    try {
      const history = messages.slice(-10).map((message) => ({ role: message.role, content: message.content }))
      const payload = await postChat({
        message: question,
        section: sectionId,
        history,
        context,
        skill: skillId,
      })
      setMessages((current) => [
        ...current,
        {
          id: `a-${Date.now()}`,
          role: 'assistant',
          content: payload.reply || 'No response.',
          applied: payload.applied ?? [],
          provider: payload.provider,
          fellBack: payload.fell_back,
          requested: payload.provider_requested,
        },
      ])
      if (payload.applied?.length) onConfigChanged?.()
      if (payload.navigate) navigate(payload.navigate)
    } catch (error) {
      setMessages((current) => [
        ...current,
        { id: `e-${Date.now()}`, role: 'assistant', content: `Could not reach ${callSign}: ${error.message}` },
      ])
    } finally {
      setLoading(false)
    }
  }

  function onSubmit(event) {
    event.preventDefault()
    const next = input.trim()
    if (!next || loading) return
    setInput('')
    void send(next)
  }

  return (
    <div className="section-chat" data-testid={`section-chat-${sectionId}`}>
      {providers.length > 0 && (
        <div className="chat-toolbar">
          <span className="muted" style={{ fontSize: '0.72rem' }}>
            {callSign}
          </span>
          <div className="provider-switch" role="group" aria-label="Agent provider">
            {providers.map((provider) => (
              <button
                key={provider.id}
                type="button"
                className={provider.id === active ? 'active' : ''}
                disabled={!provider.available || loading}
                onClick={() => selectProvider(provider.id)}
                title={provider.available ? provider.description : `${provider.label} is not installed on this node`}
                aria-pressed={provider.id === active}
              >
                {provider.label}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="section-chat-log" ref={scrollRef}>
        {messages.length === 0 && !loading && (
          <div className="chat-empty">
            <span className="chat-empty-mark">C</span>
            <p>Ask {callSign} about this section.</p>
            {suggestions?.length > 0 && (
              <div className="chat-suggestions">
                {suggestions.map((suggestion) => (
                  <button
                    key={suggestion}
                    type="button"
                    className="chat-suggestion"
                    onClick={() => void send(suggestion)}
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
        {messages.map((message) => (
          <div key={message.id} className={`chat-row ${message.role}`}>
            <div className="chat-avatar">{message.role === 'assistant' ? 'C' : 'Y'}</div>
            <div className="chat-bubble">
              {message.role === 'assistant' ? (
                <>
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
                  <AppliedChanges changes={message.applied} />
                  {message.fellBack && (
                    <p className="metric-detail" style={{ marginTop: 8 }}>
                      {message.requested} was unavailable — answered by {message.provider}.
                    </p>
                  )}
                </>
              ) : (
                message.content
              )}
            </div>
          </div>
        ))}
        {loading && (
          <div className="chat-row assistant">
            <div className="chat-avatar">C</div>
            <div className="chat-bubble chat-loading">Thinking...</div>
          </div>
        )}
      </div>

      <form className="section-chat-form" onSubmit={onSubmit}>
        <input
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder={`Ask ${callSign}...`}
          disabled={loading}
          aria-label={`Ask ${callSign}`}
        />
        <button className="icon-control primary" type="submit" disabled={!input.trim() || loading} aria-label="Send message">
          <SendIcon />
        </button>
      </form>
    </div>
  )
}
