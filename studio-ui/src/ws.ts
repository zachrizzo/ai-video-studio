import type { Preset } from './api'

// ─── WebSocket Message Types ─────────────────────────────────────────────────

export interface WsSessionMsg {
  type: 'session'
  session_id: string
  conversation_id?: string
}

export interface WsAssistantTextMsg {
  type: 'assistant_text'
  text: string
  conversation_id?: string
}

export interface WsToolUseMsg {
  type: 'tool_use'
  /** SDK tool_use_id; matches the corresponding tool_result frame. */
  id?: string
  name: string
  summary: string
  conversation_id?: string
}

export interface WsToolResultMsg {
  type: 'tool_result'
  /** SDK tool_use_id; matches the corresponding tool_use frame. */
  id?: string
  name: string
  ok: boolean
  /** Short excerpt of the tool error output when ok is false. */
  error?: string
  conversation_id?: string
}

export interface WsArtifactUpdatedMsg {
  type: 'artifact_updated'
  run_id: string
  conversation_id?: string
}

export interface WsDoneMsg {
  type: 'done'
  conversation_id?: string
  run_id?: string | null
  /** True when the turn ended because the user pressed Stop. */
  stopped?: boolean
}

export interface WsErrorMsg {
  type: 'error'
  message: string
  conversation_id?: string
}

/** Server confirmation that a turn detached by a dropped socket was reclaimed. */
export interface WsResumedMsg {
  type: 'resumed'
  conversation_id?: string
}

export type WsInboundMessage =
  | WsSessionMsg
  | WsAssistantTextMsg
  | WsToolUseMsg
  | WsToolResultMsg
  | WsArtifactUpdatedMsg
  | WsDoneMsg
  | WsErrorMsg
  | WsResumedMsg

export interface WsStopMsg {
  type: 'stop'
  conversation_id?: string
}

/** Ask the server to reclaim a turn that kept running after a disconnect. */
export interface WsResumeMsg {
  type: 'resume'
  conversation_id: string
}

export interface WsUserMessage {
  type: 'user_message'
  text: string
  run_id: string | null
  conversation_id?: string
  session_id?: string | null
  project_id?: string | null
  preset?: Preset | null
}

export type WsOutboundMessage = WsUserMessage | WsStopMsg | WsResumeMsg

// ─── ChatWebSocket class ──────────────────────────────────────────────────────

type MessageHandler = (msg: WsInboundMessage) => void
type OpenHandler = () => void
type CloseHandler = () => void

export class ChatWebSocket {
  private ws: WebSocket | null = null
  private onMessage: MessageHandler
  private onOpen: OpenHandler
  private onClose: CloseHandler
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private destroyed = false

  constructor(opts: {
    onMessage: MessageHandler
    onOpen: OpenHandler
    onClose: CloseHandler
  }) {
    this.onMessage = opts.onMessage
    this.onOpen = opts.onOpen
    this.onClose = opts.onClose
    this.connect()
  }

  private connect() {
    if (this.destroyed) return

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${protocol}//${window.location.host}/ws/chat`
    this.ws = new WebSocket(url)

    this.ws.onopen = () => {
      this.onOpen()
    }

    this.ws.onmessage = (event: MessageEvent) => {
      try {
        const msg = JSON.parse(event.data as string) as WsInboundMessage
        this.onMessage(msg)
      } catch {
        console.error('[ws] Failed to parse message', event.data)
      }
    }

    this.ws.onclose = () => {
      this.onClose()
      if (!this.destroyed) {
        this.reconnectTimer = setTimeout(() => this.connect(), 3000)
      }
    }

    this.ws.onerror = (e) => {
      console.error('[ws] error', e)
    }
  }

  send(msg: WsOutboundMessage) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg))
    }
  }

  destroy() {
    this.destroyed = true
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.ws?.close()
    this.ws = null
  }
}
