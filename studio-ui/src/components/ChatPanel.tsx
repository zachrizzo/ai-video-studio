import { useEffect, useRef, useState, useCallback, useMemo } from 'react'
import '../styles/chat-panel.css'
import { ChatWebSocket } from '../ws'
import type { WsInboundMessage } from '../ws'
import { deleteProjectConversation, upsertProjectConversation } from '../api'

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

interface ActivePreset {
  name: string
  style_prompt: string
  narration_style: string
  video_length_minutes: number
  voice_speaker: string
  voice_language: string
  video_provider: string
  style_pack?: string | null
  default_visual_engine?: string | null
  sfx_style?: string | null
  tts_provider?: string | null
  voicebox_profile?: string | null
}

interface ChatPanelProps {
  /** The currently-viewed run id, sent with each user message for context. */
  currentRunId: string | null
  /** The currently-viewed run title, used to recover chat/run associations. */
  currentRunTitle?: string | null
  /** The active project — conversations are scoped to it. */
  currentProjectId: string | null
  /** Called when Claude writes/updates an artifact, so the viewer can refresh. */
  onArtifactUpdated: (runId: string) => void
  /** Reports websocket connection state up to the top bar. */
  onConnectionChange: (connected: boolean) => void
  /** Active preset settings to pass to the agent. */
  activePreset?: ActivePreset | null
}

// ── Icons ───────────────────────────────────────────────────────────────────────

function SendIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M3 10L17 3L10 17L8.5 11.5L3 10Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
    </svg>
  )
}

function HistoryIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h7" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  )
}

function PlusIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  )
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 12 12" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M2.5 6.5l2.5 2.5 4.5-5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function FailIcon() {
  return (
    <svg viewBox="0 0 12 12" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M3 3l6 6M9 3l-6 6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  )
}

function EmptyChatIcon() {
  return (
    <svg width="44" height="44" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="3" y="5" width="18" height="14" rx="2.5" stroke="currentColor" strokeWidth="1.5" />
      <path d="M10 9.8v4.4c0 .4.45.65.8.43l3.5-2.2a.5.5 0 0 0 0-.86l-3.5-2.2a.5.5 0 0 0-.8.43Z" fill="currentColor" />
    </svg>
  )
}

// ── Friendly tool-activity labels ──────────────────────────────────────────────
// Raw agent tool names mean nothing to someone making a video; translate them
// into plain-language, present-tense descriptions.

const TOOL_LABELS: Record<string, string> = {
  Bash: 'Running a command',
  BashOutput: 'Checking command output',
  KillShell: 'Stopping a command',
  Read: 'Reading a file',
  Write: 'Writing a file',
  Edit: 'Editing code',
  MultiEdit: 'Editing code',
  NotebookEdit: 'Editing a notebook',
  Grep: 'Searching the project',
  Glob: 'Looking for files',
  LS: 'Browsing folders',
  WebFetch: 'Searching the web',
  WebSearch: 'Searching the web',
  Agent: 'Delegating to a helper',
  Task: 'Delegating to a helper',
  TaskCreate: 'Updating the task list',
  TaskUpdate: 'Updating the task list',
  TodoWrite: 'Updating the task list',
  TaskList: 'Checking the task list',
  TaskGet: 'Checking the task list',
  Skill: 'Using a skill',
}

function friendlyToolLabel(rawName: string): string {
  const mapped = TOOL_LABELS[rawName]
  if (mapped) return mapped
  const base = rawName.startsWith('mcp__') ? rawName.split('__').pop() || rawName : rawName
  const spaced = base
    .replace(/[_-]+/g, ' ')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .trim()
  if (!spaced) return 'Working on a step'
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

// ── Component ─────────────────────────────────────────────────────────────────

let _idCounter = 1
const nextId = () => _idCounter++

interface Conversation {
  id: string
  title: string
  messages: ChatMessage[]
  createdAt: number
  runId?: string | null
  claudeSessionId?: string | null
  projectId?: string | null
}

const STORAGE_KEY = 'vs_conversations'

function normalizeTool(tool: Partial<ToolActivity>, settleRunning = false): ToolActivity {
  const status = settleRunning && tool.status === 'running'
    ? 'done'
    : tool.status === 'running' || tool.status === 'failed' || tool.status === 'done'
      ? tool.status
      : 'done'
  return {
    id: typeof tool.id === 'number' ? tool.id : nextId(),
    name: typeof tool.name === 'string' ? tool.name : 'Tool',
    summary: typeof tool.summary === 'string' ? tool.summary : '',
    status,
  }
}

function normalizeMessage(message: Partial<ChatMessage>, settleRunning = false): ChatMessage | null {
  if (message.role !== 'user' && message.role !== 'assistant' && message.role !== 'error') {
    return null
  }
  const id = typeof message.id === 'number' ? message.id : nextId()
  _idCounter = Math.max(_idCounter, id + 1)
  return {
    id,
    role: message.role,
    text: typeof message.text === 'string' ? message.text : '',
    tools: Array.isArray(message.tools) ? message.tools.map((tool) => normalizeTool(tool, settleRunning)) : [],
    streaming: settleRunning ? false : Boolean(message.streaming),
  }
}

function normalizeConversations(value: unknown, settleRunning = false): Conversation[] {
  if (!Array.isArray(value)) return []
  const seen = new Set<string>()
  const conversations: Conversation[] = []
  for (const raw of value) {
    if (!raw || typeof raw !== 'object') continue
    const item = raw as Partial<Conversation>
    const id = typeof item.id === 'string' && item.id ? item.id : newConvoId()
    if (seen.has(id)) continue
    const messages = Array.isArray(item.messages)
      ? item.messages.map((message) => normalizeMessage(message, settleRunning)).filter((m): m is ChatMessage => Boolean(m))
      : []
    // Empty flow placeholders were the source of the disappearing-chat UX bug.
    if (messages.length === 0) continue
    seen.add(id)
    conversations.push({
      id,
      title: typeof item.title === 'string' && item.title ? item.title : 'New Chat',
      messages,
      createdAt: typeof item.createdAt === 'number' ? item.createdAt : Date.now(),
      runId: typeof item.runId === 'string' ? item.runId : null,
      claudeSessionId: typeof item.claudeSessionId === 'string' ? item.claudeSessionId : null,
      projectId: typeof item.projectId === 'string' ? item.projectId : null,
    })
  }
  return conversations.sort((a, b) => b.createdAt - a.createdAt)
}

function loadConversations(): Conversation[] {
  try {
    return normalizeConversations(JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]'), true)
  } catch { return [] }
}

function saveConversations(convos: Conversation[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(normalizeConversations(convos)))
}

function newConvoId() {
  return 'conv_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6)
}

function flowTitle(runId: string | null) {
  return runId ? `Flow ${runId.replace(/^run_/, '').slice(0, 8)}` : 'New Chat'
}

const TITLE_STOP_WORDS = new Set([
  'about', 'after', 'before', 'bonaparte', 'code', 'create', 'figure', 'flow',
  'history', 'make', 'stick', 'that', 'the', 'this', 'video', 'with', 'youtube',
])

function titleTokens(text: string | null | undefined): string[] {
  if (!text) return []
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .split(/\s+/)
    .filter((token) => token.length >= 4 && !TITLE_STOP_WORDS.has(token))
}

function conversationSearchText(conversation: Conversation): string {
  const firstUser = conversation.messages.find((message) => message.role === 'user')?.text || ''
  return `${conversation.title} ${firstUser}`.toLowerCase()
}

function conversationMatchesRun(conversation: Conversation, runTitle: string | null | undefined): boolean {
  const tokens = titleTokens(runTitle)
  if (tokens.length === 0) return false
  const haystack = conversationSearchText(conversation)
  const score = tokens.reduce((sum, token) => sum + (haystack.includes(token) ? 1 : 0), 0)
  return score >= 2
}

function looksLikeVideoCreationRequest(conversation: Conversation): boolean {
  const haystack = conversationSearchText(conversation)
  return /\b(create|make|generate|build)\b/.test(haystack)
    && /\b(video|youtube|short|stick|figure|history)\b/.test(haystack)
}

function findLikelyConversationForRun(
  conversations: Conversation[],
  runId: string,
  runTitle: string | null | undefined,
): Conversation | null {
  const tokens = titleTokens(runTitle)
  if (tokens.length === 0) return null
  let best: { conversation: Conversation; score: number } | null = null
  for (const conversation of conversations) {
    if (conversation.runId === runId) continue
    if (!conversation.messages.some((message) => message.role === 'user')) continue
    const haystack = conversationSearchText(conversation)
    const score = tokens.reduce((sum, token) => sum + (haystack.includes(token) ? 1 : 0), 0)
    if (score >= 2 && (!best || score > best.score)) {
      best = { conversation, score }
    }
  }
  return best?.conversation || null
}

export function ChatPanel({ currentRunId, currentRunTitle, currentProjectId, onArtifactUpdated, onConnectionChange, activePreset }: ChatPanelProps) {
  const initialConversations = useMemo(() => loadConversations(), [])
  const [conversations, setConversations] = useState<Conversation[]>(initialConversations)
  const [activeConvoId, setActiveConvoId] = useState<string | null>(initialConversations[0]?.id ?? null)
  // Conversations are scoped to the active project. Legacy chats (no
  // projectId) belong to the default project so nothing disappears.
  const projectKey = currentProjectId || 'default'
  const visibleConversations = useMemo(
    () => conversations.filter(c => (c.projectId || 'default') === projectKey),
    [conversations, projectKey],
  )
  const projectIdRef = useRef(currentProjectId)
  useEffect(() => { projectIdRef.current = currentProjectId }, [currentProjectId])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [showHistory, setShowHistory] = useState(false)
  const [draftForRunId, setDraftForRunId] = useState<string | null>(null)
  const [manualConversationId, setManualConversationId] = useState<string | null>(null)
  const activeConvoIdRef = useRef(activeConvoId)
  useEffect(() => { activeConvoIdRef.current = activeConvoId }, [activeConvoId])

  const activeConversation = conversations.find(c => c.id === activeConvoId) || null
  const messages = activeConversation?.messages || []

  const commitConversations = useCallback((mutate: (prev: Conversation[]) => Conversation[]) => {
    setConversations(prev => {
      const updated = normalizeConversations(mutate(prev))
      saveConversations(updated)
      return updated
    })
  }, [])

  const switchConversation = useCallback((id: string) => {
    const convo = conversations.find(c => c.id === id)
    if (convo) {
      if (
        currentRunId
        && convo.runId !== currentRunId
        && !conversations.some(c => c.runId === currentRunId)
        && (conversationMatchesRun(convo, currentRunTitle) || looksLikeVideoCreationRequest(convo))
      ) {
        commitConversations(prev => (
          prev.map(c => (c.id === id ? { ...c, runId: currentRunId } : c))
        ))
      }
      setActiveConvoId(id)
      setDraftForRunId(null)
      setManualConversationId(id)
      setShowHistory(false)
    }
  }, [commitConversations, conversations, currentRunId, currentRunTitle])

  const newConversation = useCallback(() => {
    setActiveConvoId(null)
    setDraftForRunId(runIdRef.current)
    setManualConversationId(null)
    setInput('')
    setShowHistory(false)
  }, [])

  const deleteConversation = useCallback((id: string) => {
    const convo = conversations.find(c => c.id === id)
    commitConversations(prev => prev.filter(c => c.id !== id))
    if (id === activeConvoId) {
      setActiveConvoId(null)
      setDraftForRunId(runIdRef.current)
      setManualConversationId(null)
    }
    // Keep the server-side project record in sync (best-effort).
    deleteProjectConversation(convo?.projectId || projectIdRef.current || 'default', id).catch(() => {})
  }, [activeConvoId, commitConversations, conversations])

  const bindConversationToRun = useCallback((conversationId: string, runId: string) => {
    if (!conversationId || !runId || runId === 'unknown') return
    commitConversations(prev => (
      prev.map(c => (
        c.id === conversationId
          ? { ...c, runId, title: c.title || flowTitle(runId) }
          : c
      ))
    ))
  }, [commitConversations])

  const wsRef = useRef<ChatWebSocket | null>(null)
  const activeAssistantIds = useRef<Record<string, number>>({})
  const inFlightConvoId = useRef<string | null>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  // currentRunId can change; keep a ref so the send handler always sees the latest.
  const runIdRef = useRef<string | null>(currentRunId)
  useEffect(() => { runIdRef.current = currentRunId }, [currentRunId])
  const previousRunIdRef = useRef<string | null>(currentRunId)
  const presetRef = useRef(activePreset)
  useEffect(() => { presetRef.current = activePreset }, [activePreset])

  useEffect(() => {
    function onStorage(event: StorageEvent) {
      if (event.key !== STORAGE_KEY) return
      try {
        setConversations(normalizeConversations(JSON.parse(event.newValue || '[]')))
      } catch {
        setConversations([])
      }
    }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [])

  useEffect(() => {
    if (previousRunIdRef.current !== currentRunId) {
      previousRunIdRef.current = currentRunId
      setManualConversationId(null)
    }
    if (draftForRunId && draftForRunId !== currentRunId) {
      setDraftForRunId(null)
    }
  }, [currentRunId, draftForRunId])

  useEffect(() => {
    if (activeConvoId && !conversations.some(c => c.id === activeConvoId)) {
      setActiveConvoId(null)
    }
  }, [activeConvoId, conversations])

  // When the project changes, land on that project's most recent chat.
  useEffect(() => {
    const active = conversations.find(c => c.id === activeConvoId)
    if (active && (active.projectId || 'default') === projectKey) return
    const first = visibleConversations[0]
    setActiveConvoId(first ? first.id : null)
    setManualConversationId(null)
    setDraftForRunId(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectKey])

  // Keep the chat panel tied to the selected flow without saving empty ghost chats.
  useEffect(() => {
    if (!currentRunId || busy) return
    if (manualConversationId) return
    if (draftForRunId === currentRunId) return
    const existing = visibleConversations.find(c => c.runId === currentRunId)
    if (existing) {
      if (existing.id !== activeConvoId) {
        setActiveConvoId(existing.id)
        setShowHistory(false)
      }
      return
    }

    const likely = findLikelyConversationForRun(visibleConversations, currentRunId, currentRunTitle)
    if (likely) {
      bindConversationToRun(likely.id, currentRunId)
      setActiveConvoId(likely.id)
      setShowHistory(false)
      return
    }

    if (activeConversation?.runId && activeConversation.runId !== currentRunId) {
      setActiveConvoId(null)
      setShowHistory(false)
    }
  }, [
    activeConversation?.runId,
    activeConvoId,
    bindConversationToRun,
    busy,
    conversations,
    currentRunId,
    currentRunTitle,
    draftForRunId,
    manualConversationId,
  ])

  const updateConversationMessages = useCallback((
    conversationId: string,
    mutate: (messages: ChatMessage[]) => ChatMessage[],
  ) => {
    commitConversations(prev => (
      prev.map(c => (
        c.id === conversationId ? { ...c, messages: mutate(c.messages), createdAt: Date.now() } : c
      ))
    ))
  }, [commitConversations])

  // ── Mutate the in-flight assistant message ──────────────────────────────────
  const updateAssistantForConversation = useCallback(
    (conversationId: string | null, mutate: (m: ChatMessage) => ChatMessage) => {
      if (!conversationId) return
      const id = activeAssistantIds.current[conversationId]
      if (id == null) return
      updateConversationMessages(conversationId, (prev) => (
        prev.map((m) => (m.id === id ? mutate(m) : m))
      ))
    },
    [updateConversationMessages],
  )

  // ── Handle one inbound websocket message ────────────────────────────────────
  const handleMessage = useCallback(
    (msg: WsInboundMessage) => {
      const targetId = msg.conversation_id || inFlightConvoId.current || activeConvoIdRef.current
      switch (msg.type) {
        case 'session':
          if (targetId) {
            commitConversations(prev => (
              prev.map(c => (
                c.id === targetId ? { ...c, claudeSessionId: msg.session_id } : c
              ))
            ))
          }
          break
        case 'assistant_text':
          updateAssistantForConversation(targetId, (m) => ({ ...m, text: m.text + msg.text }))
          break
        case 'tool_use':
          updateAssistantForConversation(targetId, (m) => ({
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
          updateAssistantForConversation(targetId, (m) => {
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
          if (targetId && msg.run_id && msg.run_id !== 'unknown') {
            bindConversationToRun(targetId, msg.run_id)
          }
          onArtifactUpdated(msg.run_id)
          break
        case 'done':
          if (targetId && msg.run_id && msg.run_id !== 'unknown') {
            bindConversationToRun(targetId, msg.run_id)
          }
          updateAssistantForConversation(targetId, (m) => ({
            ...m,
            streaming: false,
            // Resolve any tools still marked running at end of turn.
            tools: m.tools.map((t) => (t.status === 'running' ? { ...t, status: 'done' as ToolStatus } : t)),
          }))
          if (targetId) delete activeAssistantIds.current[targetId]
          inFlightConvoId.current = null
          setBusy(false)
          break
        case 'error':
          if (targetId) {
            updateConversationMessages(targetId, (prev) => [
              ...prev,
              { id: nextId(), role: 'error', text: msg.message, tools: [], streaming: false },
            ])
            delete activeAssistantIds.current[targetId]
          }
          inFlightConvoId.current = null
          setBusy(false)
          break
      }
    },
    [bindConversationToRun, commitConversations, onArtifactUpdated, updateAssistantForConversation, updateConversationMessages],
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
    const runId = activeConversation?.runId || runIdRef.current
    const conversationId = activeConversation?.id || newConvoId()
    const claudeSessionId = activeConversation?.claudeSessionId || null
    const title = activeConversation?.title || text.slice(0, 40) || flowTitle(runId)
    const projectId = activeConversation?.projectId || projectIdRef.current || 'default'

    commitConversations(prev => {
      const exists = prev.find(c => c.id === conversationId)
      if (exists) {
        return prev.map(c => (
          c.id === conversationId
            ? {
                ...c,
                title: c.title || title,
                runId: c.runId || runId,
                projectId: c.projectId || projectId,
                messages: [...c.messages, userMsg, assistantMsg],
                createdAt: Date.now(),
              }
            : c
        ))
      }
      return [{
        id: conversationId,
        title,
        messages: [userMsg, assistantMsg],
        createdAt: Date.now(),
        runId,
        claudeSessionId: null,
        projectId,
      }, ...prev]
    })
    // Record the chat on its project server-side (best-effort).
    upsertProjectConversation(projectId, { id: conversationId, title }).catch(() => {})
    activeAssistantIds.current[conversationId] = assistantId
    inFlightConvoId.current = conversationId
    setActiveConvoId(conversationId)
    setDraftForRunId(null)
    setManualConversationId(null)
    setBusy(true)
    setInput('')
    wsRef.current.send({
      type: 'user_message',
      text,
      run_id: runId,
      conversation_id: conversationId,
      session_id: claudeSessionId,
      project_id: projectId,
      preset: presetRef.current || null,
    })
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  const linkedRunId = activeConversation?.runId || currentRunId

  return (
    <div className="chat-panel">
      <div className="chat-panel-header">
        <button
          className="chat-icon-btn"
          onClick={() => setShowHistory(!showHistory)}
          title="Chat history"
          aria-label="Chat history"
        >
          <HistoryIcon />
        </button>
        <span className="chat-panel-title">Chat</span>
        <span className={`chat-flow-badge${linkedRunId ? '' : ' is-new'}`}>
          {linkedRunId ? linkedRunId.replace(/^run_/, 'flow ') : 'new chat'}
        </span>
        <button
          className="chat-icon-btn chat-new-btn"
          onClick={newConversation}
          title="New chat"
          aria-label="New chat"
        >
          <PlusIcon />
        </button>
      </div>

      {showHistory && (
        <div className="chat-history-list">
          {visibleConversations.length === 0 && (
            <div className="chat-history-empty">
              No conversations in this project yet.<br />
              Send a message below to start one.
            </div>
          )}
          {visibleConversations.map(c => {
            const count = c.messages.filter(m => m.role === 'user').length
            return (
              <div
                key={c.id}
                className={`chat-history-item ${c.id === activeConvoId ? 'active' : ''}`}
                onClick={() => switchConversation(c.id)}
              >
                <span className="chat-history-title">{c.title}</span>
                {c.runId && <span className="chat-history-flow">{c.runId.replace(/^run_/, 'flow ')}</span>}
                <span className="chat-history-count">{count} {count === 1 ? 'message' : 'messages'}</span>
                <button
                  className="chat-history-delete"
                  title="Delete conversation"
                  onClick={e => { e.stopPropagation(); deleteConversation(c.id) }}
                >×</button>
              </div>
            )
          })}
        </div>
      )}

      <div className="chat-messages">
        {messages.length === 0 && (
          <div className="empty-state">
            <div className="empty-state-icon"><EmptyChatIcon /></div>
            {currentRunId ? (
              <>
                <div className="empty-state-title">No chat for this flow yet</div>
                <div className="empty-state-desc">
                  Send a message below to keep working on it, or open a past conversation from the history button above.
                </div>
              </>
            ) : (
              <>
                <div className="empty-state-title">Make something new</div>
                <div className="empty-state-desc">
                  Describe the video you want — try “Make a video about the fall of Rome.”
                </div>
              </>
            )}
          </div>
        )}

        {messages.map((m) => {
          const hasRunningTool = m.tools.some((t) => t.status === 'running')
          const showThinkingBubble = m.role === 'assistant' && m.streaming && !m.text && m.tools.length === 0
          const showBubble = !showThinkingBubble && (Boolean(m.text) || m.role !== 'assistant' || m.tools.length === 0)
          const showTrailingThinking = m.role === 'assistant' && m.streaming && !m.text && m.tools.length > 0 && !hasRunningTool
          return (
            <div className={`msg-row ${m.role}`} key={m.id}>
              <span className="msg-role-label">{m.role === 'user' ? 'You' : m.role === 'error' ? 'Error' : 'Claude'}</span>
              {showBubble && (
                <div className={`msg-bubble ${m.role}`}>
                  {m.text}
                  {m.streaming && <span className="msg-cursor" />}
                </div>
              )}
              {showThinkingBubble && (
                <div className="msg-thinking">
                  <span className="msg-thinking-dots"><span /><span /><span /></span>
                  Thinking it through…
                </div>
              )}
              {m.tools.length > 0 && (
                <div className="tool-activities">
                  {m.tools.map((t) => (
                    <div className={`tool-activity ${t.status}`} key={t.id}>
                      <span className="tool-activity-icon">
                        {t.status === 'running' ? <span className="spinner" /> : t.status === 'done' ? <CheckIcon /> : <FailIcon />}
                      </span>
                      <span className="tool-activity-label">{friendlyToolLabel(t.name)}</span>
                      {t.status === 'failed' && <span className="tool-activity-flag">didn’t work</span>}
                      {t.summary && <span className="tool-activity-summary">{t.summary}</span>}
                    </div>
                  ))}
                  {showTrailingThinking && (
                    <div className="tool-activity running">
                      <span className="tool-activity-icon"><span className="spinner" /></span>
                      <span className="tool-activity-label">Thinking about the next step…</span>
                    </div>
                  )}
                </div>
              )}
            </div>
          )
        })}
        <div ref={messagesEndRef} />
      </div>

      <div className="chat-input-area">
        <div className="chat-input-row">
          <textarea
            className="chat-textarea"
            placeholder="Ask Claude to make or change a video…"
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
        {busy ? (
          <div className="chat-input-hint busy">
            <span className="spinner" />
            Claude is working — follow the steps above
          </div>
        ) : (
          <div className="chat-input-hint">Enter to send · Shift+Enter for a new line</div>
        )}
      </div>
    </div>
  )
}
