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
  name: string
  summary: string
  conversation_id?: string
}

export interface WsToolResultMsg {
  type: 'tool_result'
  name: string
  ok: boolean
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
}

export interface WsErrorMsg {
  type: 'error'
  message: string
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

export interface WsOutboundMessage {
  type: 'user_message'
  text: string
  run_id: string | null
  conversation_id?: string
  session_id?: string | null
  preset?: {
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
  } | null
}

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
