import { useEffect, useRef, useState, useCallback } from 'react'
import { ChatWebSocket } from '../ws'
import type { WsInboundMessage } from '../ws'

// ── Local view models ──────────────────────────────────────────────────────────

type ToolStatus = 'running' | 'done' | 'failed'

interface ToolActivity {
  id: number
  name: string
  summary: string
  status: ToolStatus
}

interface ChatMessage {
  id: number
  role: 'user' | 'assistant' | 'error'
  text: string
  tools: ToolActivity[]
  streaming: boolean
}

interface ChatPanelProps {
  /** The currently-viewed run id, sent with each user message for context. */
  currentRunId: string | null
  /** Called when Claude writes/updates an artifact, so the viewer can refresh. */
  onArtifactUpdated: (runId: string) => void
  /** Reports websocket connection state up to the top bar. */
  onConnectionChange: (connected: boolean) => void
}

// ── Icons ───────────────────────────────────────────────────────────────────────

function SendIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M3 10L17 3L10 17L8.5 11.5L3 10Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
    </svg>
  )
}

function toolGlyph(status: ToolStatus): string {
  if (status === 'running') return '○' // ○
  if (status === 'done') return '✓' // ✓
  return '✗' // ✗
}

// ── Component ─────────────────────────────────────────────────────────────────

let _idCounter = 1
const nextId = () => _idCounter++

export function ChatPanel({ currentRunId, onArtifactUpdated, onConnectionChange }: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)

  const wsRef = useRef<ChatWebSocket | null>(null)
  const activeAssistantId = useRef<number | null>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  // currentRunId can change; keep a ref so the send handler always sees the latest.
  const runIdRef = useRef<string | null>(currentRunId)
  useEffect(() => { runIdRef.current = currentRunId }, [currentRunId])

  // ── Mutate the in-flight assistant message ──────────────────────────────────
  const updateActiveAssistant = useCallback(
    (mutate: (m: ChatMessage) => ChatMessage) => {
      const id = activeAssistantId.current
      if (id == null) return
      setMessages((prev) => prev.map((m) => (m.id === id ? mutate(m) : m)))
    },
    [],
  )

  // ── Handle one inbound websocket message ────────────────────────────────────
  const handleMessage = useCallback(
    (msg: WsInboundMessage) => {
      switch (msg.type) {
        case 'session':
          break
        case 'assistant_text':
          updateActiveAssistant((m) => ({ ...m, text: m.text + msg.text }))
          break
        case 'tool_use':
          updateActiveAssistant((m) => ({
            ...m,
            // Tools run sequentially here: when a new one starts, mark prior
            // still-running tools as done. Any straggler resolves on 'done'.
            tools: [
              ...m.tools.map((t) => (t.status === 'running' ? { ...t, status: 'done' as ToolStatus } : t)),
              { id: nextId(), name: msg.name, summary: msg.summary, status: 'running' },
            ],
          }))
          break
        case 'tool_result':
          updateActiveAssistant((m) => {
            // Mark the last running tool done/failed. Match by name when provided,
            // otherwise resolve the most recent still-running tool.
            const tools = [...m.tools]
            for (let i = tools.length - 1; i >= 0; i--) {
              const nameMatches = !msg.name || tools[i].name === msg.name
              if (nameMatches && tools[i].status === 'running') {
                tools[i] = { ...tools[i], status: msg.ok ? 'done' : 'failed' }
                break
              }
            }
            return { ...m, tools }
          })
          break
        case 'artifact_updated':
          onArtifactUpdated(msg.run_id)
          break
        case 'done':
          updateActiveAssistant((m) => ({
            ...m,
            streaming: false,
            // Resolve any tools still marked running at end of turn.
            tools: m.tools.map((t) => (t.status === 'running' ? { ...t, status: 'done' as ToolStatus } : t)),
          }))
          activeAssistantId.current = null
          setBusy(false)
          break
        case 'error':
          setMessages((prev) => [
            ...prev,
            { id: nextId(), role: 'error', text: msg.message, tools: [], streaming: false },
          ])
          activeAssistantId.current = null
          setBusy(false)
          break
      }
    },
    [updateActiveAssistant, onArtifactUpdated],
  )

  // ── Connect the websocket once on mount ─────────────────────────────────────
  useEffect(() => {
    const ws = new ChatWebSocket({
      onMessage: handleMessage,
      onOpen: () => onConnectionChange(true),
      onClose: () => onConnectionChange(false),
    })
    wsRef.current = ws
    return () => ws.destroy()
    // handleMessage/onConnectionChange are stable enough; connect only once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── Auto-scroll to the newest message ───────────────────────────────────────
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // ── Send a user message ─────────────────────────────────────────────────────
  function send() {
    const text = input.trim()
    if (!text || busy || !wsRef.current) return

    const userMsg: ChatMessage = { id: nextId(), role: 'user', text, tools: [], streaming: false }
    const assistantId = nextId()
    const assistantMsg: ChatMessage = { id: assistantId, role: 'assistant', text: '', tools: [], streaming: true }

    setMessages((prev) => [...prev, userMsg, assistantMsg])
    activeAssistantId.current = assistantId
    setBusy(true)
    setInput('')
    wsRef.current.send({ type: 'user_message', text, run_id: runIdRef.current })
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  return (
    <div className="chat-panel">
      <div className="chat-panel-header">
        <span className="panel-label">Chat — Claude Code</span>
      </div>

      <div className="chat-messages">
        {messages.length === 0 && (
          <div className="segments-empty">
            Ask Claude Code to make a video.<br />
            e.g. "make a video about the fall of Rome"
          </div>
        )}

        {messages.map((m) => (
          <div className={`msg-row ${m.role}`} key={m.id}>
            <span className="msg-role-label">{m.role === 'user' ? 'You' : m.role === 'error' ? 'Error' : 'Claude'}</span>
            {(m.text || m.role !== 'assistant' || m.tools.length === 0) && (
              <div className={`msg-bubble ${m.role}`}>
                {m.text}
                {m.streaming && <span className="msg-cursor" />}
              </div>
            )}
            {m.tools.length > 0 && (
              <div className="tool-activities">
                {m.tools.map((t) => (
                  <div className={`tool-activity ${t.status}`} key={t.id}>
                    <span className="tool-activity-icon">{toolGlyph(t.status)}</span>
                    <span className="tool-activity-name">{t.name}</span>
                    <span className="tool-activity-summary">{t.summary}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      <div className="chat-input-area">
        <div className="chat-input-row">
          <textarea
            className="chat-textarea"
            placeholder="Ask Claude Code to make or change a video…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            disabled={busy}
            rows={1}
          />
          <button className="send-btn" onClick={send} disabled={busy || !input.trim()} title="Send">
            <SendIcon />
          </button>
        </div>
        <div className="chat-input-hint">
          {busy ? 'Claude is working…' : 'Enter to send · Shift+Enter for newline'}
        </div>
      </div>
    </div>
  )
}
