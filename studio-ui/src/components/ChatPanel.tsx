import { useEffect, useRef, useState, useCallback, useMemo } from 'react'
import '../styles/chat-panel.css'
import { ChatWebSocket } from '../ws'
import type { WsInboundMessage } from '../ws'
import { deleteProjectConversation, fetchConversationMessages, upsertProjectConversation } from '../api'
import type { Preset, ProjectConversation, ServerChatMessage } from '../api'

// ── Local view models ──────────────────────────────────────────────────────────

type ToolStatus = 'running' | 'done' | 'failed'

interface ToolActivity {
  id: number
  name: string
  summary: string
  status: ToolStatus
  /** SDK tool_use_id, used to match tool_result frames to their tool. */
  toolUseId?: string
  /** Short error excerpt when the tool failed. */
  error?: string
}

/**
 * Ordered record of what an assistant message actually contains, in the
 * order it arrived — so tool calls render where they happened instead of
 * all grouped after the text. Tool parts reference a ToolActivity by id
 * (resolved from ChatMessage.tools at render time) so tool_result updates
 * need only touch the tools array, not the ordering.
 */
type ChatPart = { kind: 'text'; text: string } | { kind: 'tool'; id: number }

interface ChatMessage {
  id: number
  role: 'user' | 'assistant' | 'error'
  text: string
  tools: ToolActivity[]
  parts: ChatPart[]
  streaming: boolean
}

interface ChatPanelProps {
  /** The currently-viewed run id, sent with each user message for context. */
  currentRunId: string | null
  /** The active project — conversations are scoped to it. */
  currentProjectId: string | null
  /** The active project's server-side conversation records, used to seed the
   * chat list so conversations survive a fresh browser / cleared storage. */
  serverConversations?: ProjectConversation[] | null
  /** Called when Claude writes/updates an artifact, so the viewer can refresh. */
  onArtifactUpdated: (runId: string) => void
  /** Called when the user opens a conversation bound to a run, so the viewer
   * can follow along. */
  onRunSelected?: (runId: string) => void
  /** Reports websocket connection state up to the top bar. */
  onConnectionChange: (connected: boolean) => void
  /** Active preset settings to pass to the agent. */
  activePreset?: Preset | null
}

// ── Icons ───────────────────────────────────────────────────────────────────────

function SendIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M3 10L17 3L10 17L8.5 11.5L3 10Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
    </svg>
  )
}

function StopIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="5.5" y="5.5" width="9" height="9" rx="1.5" fill="currentColor" />
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

// ── Ordered rendering blocks ──────────────────────────────────────────────────
// Resolve ChatPart -> renderable blocks, merging adjacent same-kind parts
// (consecutive tool calls still read as one grouped activity box, just now
// interleaved with the text around them instead of always trailing it).

type MessageBlock = { kind: 'text'; text: string } | { kind: 'tools'; tools: ToolActivity[] }

function groupMessageParts(m: ChatMessage): MessageBlock[] {
  const toolsById = new Map(m.tools.map((t) => [t.id, t]))
  const blocks: MessageBlock[] = []
  for (const part of m.parts) {
    const last = blocks[blocks.length - 1]
    if (part.kind === 'text') {
      if (last && last.kind === 'text') last.text += part.text
      else blocks.push({ kind: 'text', text: part.text })
    } else {
      const tool = toolsById.get(part.id)
      if (!tool) continue
      if (last && last.kind === 'tools') last.tools.push(tool)
      else blocks.push({ kind: 'tools', tools: [tool] })
    }
  }
  return blocks
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
    toolUseId: typeof tool.toolUseId === 'string' ? tool.toolUseId : undefined,
    error: typeof tool.error === 'string' ? tool.error : undefined,
  }
}

function isChatPart(value: unknown): value is ChatPart {
  if (!value || typeof value !== 'object') return false
  const p = value as { kind?: unknown; text?: unknown; id?: unknown }
  if (p.kind === 'text') return typeof p.text === 'string'
  if (p.kind === 'tool') return typeof p.id === 'number'
  return false
}

interface NormalizeMessageInput extends Omit<Partial<ChatMessage>, 'tools'> {
  tools?: Partial<ToolActivity>[]
}

function normalizeMessage(message: NormalizeMessageInput, settleRunning = false): ChatMessage | null {
  if (message.role !== 'user' && message.role !== 'assistant' && message.role !== 'error') {
    return null
  }
  const id = typeof message.id === 'number' ? message.id : nextId()
  _idCounter = Math.max(_idCounter, id + 1)
  const text = typeof message.text === 'string' ? message.text : ''
  const tools = Array.isArray(message.tools) ? message.tools.map((tool) => normalizeTool(tool, settleRunning)) : []
  // Live-streamed messages record true text/tool interleaving in `parts` as
  // it happens. Older localStorage entries and server-hydrated history only
  // know "all the text" and "all the tools" with no relative order between
  // them, so those fall back to the original text-then-tools presentation.
  const parts: ChatPart[] = Array.isArray(message.parts) && message.parts.every(isChatPart)
    ? message.parts
    : [
        ...(text ? [{ kind: 'text' as const, text }] : []),
        ...tools.map((t) => ({ kind: 'tool' as const, id: t.id })),
      ]
  return {
    id,
    role: message.role,
    text,
    tools,
    parts,
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
  // Callers (commitConversations) already normalize before calling this, so
  // re-normalizing here would just redo the same work on every commit.
  localStorage.setItem(STORAGE_KEY, JSON.stringify(convos))
}

function newConvoId() {
  return 'conv_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6)
}

function flowTitle(runId: string | null) {
  return runId ? `Flow ${runId.replace(/^run_/, '').slice(0, 8)}` : 'New Chat'
}

function serverMessagesToChat(serverMessages: ServerChatMessage[]): ChatMessage[] {
  return serverMessages
    .map((m) => normalizeMessage({ role: m.role, text: m.text || '', tools: m.tools || [] }, true))
    .filter((m): m is ChatMessage => Boolean(m))
}

export function ChatPanel({ currentRunId, currentProjectId, serverConversations, onArtifactUpdated, onRunSelected, onConnectionChange, activePreset }: ChatPanelProps) {
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

  // localStorage is a convenience cache (the server transcript is the source
  // of truth), but commitConversations fires on every streamed token during a
  // turn — writing synchronously that often is wasteful. Debounce the write
  // only; setConversations itself stays synchronous so the UI still renders
  // every token immediately.
  const saveDebounceRef = useRef<number | null>(null)
  const commitConversations = useCallback((mutate: (prev: Conversation[]) => Conversation[]) => {
    setConversations(prev => {
      const updated = normalizeConversations(mutate(prev))
      if (saveDebounceRef.current != null) window.clearTimeout(saveDebounceRef.current)
      saveDebounceRef.current = window.setTimeout(() => saveConversations(updated), 250)
      return updated
    })
  }, [])

  const switchConversation = useCallback((id: string) => {
    const convo = conversations.find(c => c.id === id)
    if (convo) {
      setActiveConvoId(id)
      setDraftForRunId(null)
      setManualConversationId(id)
      setShowHistory(false)
      // Follow the conversation's bound run in the viewer. Binding itself
      // only ever comes from server frames (artifact_updated/done) — never
      // from guessing here.
      if (convo.runId && convo.runId !== currentRunId) {
        onRunSelected?.(convo.runId)
      }
    }
  }, [conversations, currentRunId, onRunSelected])

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
  // Reconnect bookkeeping: the socket dropped mid-turn, so the done/error
  // frame died with it. We ask the server to reclaim the turn on reconnect
  // and re-sync the conversation from the server transcript when it settles.
  const connectedRef = useRef(false)
  const pendingResumeConvoRef = useRef<string | null>(null)
  // A page reload has no memory of whether the active conversation had a
  // turn detached server-side (localStorage always settles streaming to
  // false on load) — so on the very first connect, ask the server once
  // whether the initially-active conversation is still going.
  const hasCheckedInitialResumeRef = useRef(false)
  const interruptedConvosRef = useRef<Set<string>>(new Set())
  const interruptNoteIds = useRef<Record<string, number>>({})
  const conversationsRef = useRef(conversations)
  useEffect(() => { conversationsRef.current = conversations }, [conversations])
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const messagesContainerRef = useRef<HTMLDivElement>(null)
  // Tracks whether the user is scrolled to (near) the bottom, so a streamed
  // response doesn't yank the view back down while they've scrolled up to
  // read earlier messages.
  const stickToBottomRef = useRef(true)
  const handleMessagesScroll = useCallback(() => {
    const el = messagesContainerRef.current
    if (!el) return
    stickToBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
  }, [])
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

  // Keep the chat panel tied to the selected flow without saving empty ghost
  // chats. Only exact runId bindings count — fuzzy title matching used to
  // mis-bind old chats to unrelated runs.
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

    if (activeConversation?.runId && activeConversation.runId !== currentRunId) {
      setActiveConvoId(null)
      setShowHistory(false)
    }
  }, [
    activeConversation?.runId,
    activeConvoId,
    busy,
    currentRunId,
    draftForRunId,
    manualConversationId,
    visibleConversations,
  ])

  // Hydrate the active conversation from the server-side transcript. Local
  // storage stays the optimistic write path; the server copy only wins when
  // it has more history (localStorage cleared, or the chat ran in another
  // browser).
  useEffect(() => {
    if (!activeConvoId || busy) return
    let cancelled = false
    fetchConversationMessages(activeConvoId)
      .then((serverMessages) => {
        if (cancelled || serverMessages.length === 0) return
        commitConversations(prev => prev.map(c => {
          if (c.id !== activeConvoId || serverMessages.length <= c.messages.length) return c
          return { ...c, messages: serverMessagesToChat(serverMessages) }
        }))
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [activeConvoId, busy, commitConversations])

  // Re-sync a conversation from the server transcript unconditionally — used
  // after a turn that outlived a disconnect settles, when the server copy is
  // authoritative (frames streamed while the socket was down are lost).
  const forceHydrateFromServer = useCallback((conversationId: string) => {
    fetchConversationMessages(conversationId)
      .then((serverMessages) => {
        if (serverMessages.length === 0) return
        commitConversations(prev => prev.map(c => (
          c.id === conversationId ? { ...c, messages: serverMessagesToChat(serverMessages) } : c
        )))
      })
      .catch(() => {})
  }, [commitConversations])

  // Seed the conversation list from the active project's server-side records
  // so chats survive a fresh browser or cleared localStorage. Merge by id:
  // the server wins on claude_session_id, localStorage wins on local state.
  useEffect(() => {
    const records = serverConversations || []
    if (records.length === 0) return
    let cancelled = false
    const known = new Map(conversationsRef.current.map(c => [c.id, c]))
    const missing = records.filter(r => !known.has(r.id))
    const sessionUpdates = records.filter(r => {
      const local = known.get(r.id)
      return local && r.claude_session_id && local.claudeSessionId !== r.claude_session_id
    })
    if (missing.length === 0 && sessionUpdates.length === 0) return
    Promise.all(missing.map(async (record) => {
      try {
        return { record, messages: await fetchConversationMessages(record.id) }
      } catch {
        return { record, messages: [] as ServerChatMessage[] }
      }
    })).then((fetched) => {
      if (cancelled) return
      commitConversations(prev => {
        let next = prev
        if (sessionUpdates.length > 0) {
          const byId = new Map(sessionUpdates.map(r => [r.id, r.claude_session_id as string]))
          next = next.map(c => (byId.has(c.id) ? { ...c, claudeSessionId: byId.get(c.id)! } : c))
        }
        const additions: Conversation[] = []
        for (const { record, messages } of fetched) {
          if (messages.length === 0) continue
          if (next.some(c => c.id === record.id)) continue
          additions.push({
            id: record.id,
            title: record.title || 'New chat',
            messages: serverMessagesToChat(messages),
            createdAt: Math.round(((record.updated_at ?? record.created_at) || 0) * 1000),
            runId: null,
            claudeSessionId: record.claude_session_id ?? null,
            projectId: projectKey,
          })
        }
        return additions.length > 0 ? [...next, ...additions] : next
      })
    })
    return () => { cancelled = true }
  }, [serverConversations, projectKey, commitConversations])

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
          updateAssistantForConversation(targetId, (m) => {
            // Extend the trailing text part so text keeps rendering where it
            // arrived instead of jumping to the end of the message.
            const parts = [...m.parts]
            const last = parts[parts.length - 1]
            if (last && last.kind === 'text') {
              parts[parts.length - 1] = { kind: 'text', text: last.text + msg.text }
            } else {
              parts.push({ kind: 'text', text: msg.text })
            }
            return { ...m, text: m.text + msg.text, parts }
          })
          break
        case 'tool_use':
          updateAssistantForConversation(targetId, (m) => {
            // With a tool_use_id each tool resolves via its own tool_result, so
            // parallel tools can stay running. Without one (older backend),
            // keep the sequential assumption: settle prior running tools.
            const prior = msg.id
              ? m.tools
              : m.tools.map((t) => (t.status === 'running' ? { ...t, status: 'done' as ToolStatus } : t))
            const newTool: ToolActivity = {
              id: nextId(), name: msg.name, summary: msg.summary, status: 'running', toolUseId: msg.id,
            }
            return {
              ...m,
              tools: [...prior, newTool],
              parts: [...m.parts, { kind: 'tool', id: newTool.id }],
            }
          })
          break
        case 'tool_result':
          updateAssistantForConversation(targetId, (m) => {
            // Match by tool_use_id when present; otherwise fall back to name /
            // most recent still-running tool.
            const tools = [...m.tools]
            let idx = msg.id ? tools.findIndex((t) => t.toolUseId === msg.id && t.status === 'running') : -1
            if (idx === -1) {
              for (let i = tools.length - 1; i >= 0; i--) {
                const nameMatches = !msg.name || tools[i].name === msg.name
                if (nameMatches && tools[i].status === 'running') {
                  idx = i
                  break
                }
              }
            }
            if (idx !== -1) {
              tools[idx] = { ...tools[idx], status: msg.ok ? 'done' : 'failed', error: msg.error }
            }
            return { ...m, tools }
          })
          break
        case 'artifact_updated':
          if (targetId && msg.run_id && msg.run_id !== 'unknown') {
            bindConversationToRun(targetId, msg.run_id)
            onArtifactUpdated(msg.run_id)
          }
          break
        case 'resumed': {
          // The server reclaimed a turn that outlived a disconnect; go back
          // to the normal busy/streaming state.
          const convoId = msg.conversation_id
          if (convoId) {
            inFlightConvoId.current = convoId
            setBusy(true)
            const noteId = interruptNoteIds.current[convoId]
            if (noteId != null) {
              delete interruptNoteIds.current[convoId]
              updateConversationMessages(convoId, (prev) => prev.filter((m) => m.id !== noteId))
            }
            if (activeAssistantIds.current[convoId] == null) {
              // A page reload has no record of which message was mid-stream
              // (activeAssistantIds starts empty every mount) — without a
              // placeholder to attach to, the reclaimed text/tool frames
              // would silently have nowhere to go.
              const assistantId = nextId()
              activeAssistantIds.current[convoId] = assistantId
              updateConversationMessages(convoId, (prev) => [
                ...prev,
                { id: assistantId, role: 'assistant', text: '', tools: [], parts: [], streaming: true },
              ])
            } else {
              updateAssistantForConversation(convoId, (m) => ({ ...m, streaming: true }))
            }
          }
          break
        }
        case 'done': {
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
          // A done/error frame for a conversation OTHER than the one actually
          // in flight (e.g. the initial-connect resume probe replying `done`
          // for an idle conversation after the user already started a new
          // turn elsewhere) must not clobber that real in-flight turn's busy
          // state. When nothing is in flight, clearing again is a harmless
          // no-op, so that case stays unguarded.
          if (inFlightConvoId.current === null || targetId === inFlightConvoId.current) {
            inFlightConvoId.current = null
            setBusy(false)
          }
          if (targetId && interruptedConvosRef.current.has(targetId)) {
            interruptedConvosRef.current.delete(targetId)
            delete interruptNoteIds.current[targetId]
            forceHydrateFromServer(targetId)
          }
          break
        }
        case 'error': {
          if (targetId) {
            updateConversationMessages(targetId, (prev) => [
              ...prev,
              { id: nextId(), role: 'error', text: msg.message, tools: [], parts: [], streaming: false },
            ])
            delete activeAssistantIds.current[targetId]
          }
          if (inFlightConvoId.current === null || targetId === inFlightConvoId.current) {
            inFlightConvoId.current = null
            setBusy(false)
          }
          if (targetId && interruptedConvosRef.current.has(targetId)) {
            interruptedConvosRef.current.delete(targetId)
            delete interruptNoteIds.current[targetId]
            forceHydrateFromServer(targetId)
          }
          break
        }
      }
    },
    [bindConversationToRun, commitConversations, forceHydrateFromServer, onArtifactUpdated, updateAssistantForConversation, updateConversationMessages],
  )

  // ── Connect the websocket once on mount ─────────────────────────────────────
  useEffect(() => {
    const ws = new ChatWebSocket({
      onMessage: handleMessage,
      onOpen: () => {
        connectedRef.current = true
        onConnectionChange(true)
        // If the socket dropped mid-turn, ask the server to reclaim the turn
        // it kept running; it answers with `resumed` (still going) or `done`.
        // On the very first connect (page load/reload), we have no such
        // witnessed drop to go on, so fall back to asking about whatever
        // conversation is initially active — a resume check is a harmless
        // no-op (server replies `done`) when nothing was actually in flight.
        const witnessedDropConvoId = pendingResumeConvoRef.current
        pendingResumeConvoRef.current = null
        const convoId = witnessedDropConvoId
          || (!hasCheckedInitialResumeRef.current ? activeConvoIdRef.current : null)
        hasCheckedInitialResumeRef.current = true
        if (convoId) {
          ws.send({ type: 'resume', conversation_id: convoId })
        }
      },
      onClose: () => {
        connectedRef.current = false
        onConnectionChange(false)
        const convoId = inFlightConvoId.current
        if (convoId) {
          // The done/error frame for this turn died with the socket. The
          // server keeps the turn alive for a grace window; say so honestly
          // and unlock the input instead of spinning forever.
          inFlightConvoId.current = null
          pendingResumeConvoRef.current = convoId
          interruptedConvosRef.current.add(convoId)
          // Keep activeAssistantIds so a reclaimed turn streams back into
          // the same message bubble.
          updateAssistantForConversation(convoId, (m) => ({ ...m, streaming: false }))
          const noteId = nextId()
          interruptNoteIds.current[convoId] = noteId
          updateConversationMessages(convoId, (prev) => [
            ...prev,
            {
              id: noteId,
              role: 'error',
              text: 'Connection lost — Claude may still be working; reconnecting…',
              tools: [],
              parts: [],
              streaming: false,
            },
          ])
          setBusy(false)
        }
      },
    })
    wsRef.current = ws
    return () => ws.destroy()
    // handleMessage/onConnectionChange are stable enough; connect only once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── Auto-scroll to the newest message ───────────────────────────────────────
  // Only when the user is stuck to the bottom — otherwise a streamed response
  // fights their manual scroll-up on every token. Instant scroll while busy
  // avoids queuing up a smooth animation on every token; smooth once settled.
  useEffect(() => {
    if (!stickToBottomRef.current) return
    requestAnimationFrame(() => {
      messagesEndRef.current?.scrollIntoView({ behavior: busy ? 'auto' : 'smooth' })
    })
  }, [messages, busy])

  // ── Send a user message ─────────────────────────────────────────────────────
  function send() {
    const text = input.trim()
    if (!text || busy || !wsRef.current) return
    // Never pretend a message went out on a dead socket — that used to lock
    // the input behind an eternal spinner.
    if (!connectedRef.current) return

    const userMsg: ChatMessage = { id: nextId(), role: 'user', text, tools: [], parts: [], streaming: false }
    const assistantId = nextId()
    const assistantMsg: ChatMessage = { id: assistantId, role: 'assistant', text: '', tools: [], parts: [], streaming: true }
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
                // A conversation's own runId is only ever set by
                // bindConversationToRun (from artifact_updated/done frames),
                // never pre-seeded from the currently-viewed run.
                runId: c.runId,
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
        runId: null,
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

  // ── Stop the in-flight agent turn ───────────────────────────────────────────
  function stopTurn() {
    if (!busy || !wsRef.current) return
    wsRef.current.send({
      type: 'stop',
      conversation_id: inFlightConvoId.current || undefined,
    })
    // Stay busy until the backend confirms with a done/error frame.
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

      <div className="chat-messages" ref={messagesContainerRef} onScroll={handleMessagesScroll}>
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
          if (m.role !== 'assistant') {
            return (
              <div className={`msg-row ${m.role}`} key={m.id}>
                <span className="msg-role-label">{m.role === 'user' ? 'You' : 'Error'}</span>
                <div className={`msg-bubble ${m.role}`}>{m.text}</div>
              </div>
            )
          }

          const hasRunningTool = m.tools.some((t) => t.status === 'running')
          const blocks = groupMessageParts(m)
          const showThinkingBubble = m.streaming && blocks.length === 0
          const lastBlock = blocks[blocks.length - 1]
          const showTrailingThinking = m.streaming && lastBlock?.kind === 'tools' && !hasRunningTool

          return (
            <div className="msg-row assistant" key={m.id}>
              <span className="msg-role-label">Claude</span>
              {blocks.map((block, i) => {
                const isLast = i === blocks.length - 1
                if (block.kind === 'text') {
                  return (
                    <div className="msg-bubble assistant" key={i}>
                      {block.text}
                      {isLast && m.streaming && <span className="msg-cursor" />}
                    </div>
                  )
                }
                return (
                  <div className="tool-activities" key={i}>
                    {block.tools.map((t) => (
                      <div className={`tool-activity ${t.status}`} key={t.id} title={t.error || undefined}>
                        <span className="tool-activity-icon">
                          {t.status === 'running' ? <span className="spinner" /> : t.status === 'done' ? <CheckIcon /> : <FailIcon />}
                        </span>
                        <span className="tool-activity-label">{friendlyToolLabel(t.name)}</span>
                        {t.status === 'failed' && <span className="tool-activity-flag">didn’t work</span>}
                        {t.summary && <span className="tool-activity-summary">{t.summary}</span>}
                      </div>
                    ))}
                    {isLast && showTrailingThinking && (
                      <div className="tool-activity running">
                        <span className="tool-activity-icon"><span className="spinner" /></span>
                        <span className="tool-activity-label">Thinking about the next step…</span>
                      </div>
                    )}
                  </div>
                )
              })}
              {showThinkingBubble && (
                <div className="msg-thinking">
                  <span className="msg-thinking-dots"><span /><span /><span /></span>
                  Thinking it through…
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
          {busy ? (
            <button className="send-btn is-stop" onClick={stopTurn} title="Stop" aria-label="Stop">
              <StopIcon />
            </button>
          ) : (
            <button className="send-btn" onClick={send} disabled={!input.trim()} title="Send">
              <SendIcon />
            </button>
          )}
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
