import { useCallback, useState } from 'react'
import { ChatPanel } from './components/ChatPanel'
import { FlowViewer } from './components/FlowViewer'

function LogoIcon() {
  return (
    <svg viewBox="0 0 20 20" xmlns="http://www.w3.org/2000/svg">
      <path d="M4 3.5v13l11-6.5L4 3.5Z" />
    </svg>
  )
}

export default function App() {
  const [currentRunId, setCurrentRunId] = useState<string | null>(null)
  // Bumped whenever an artifact_updated event arrives, so FlowViewer refreshes.
  const [artifactRefreshRunId, setArtifactRefreshRunId] = useState<string | null>(null)
  const [connected, setConnected] = useState(false)

  const handleArtifactUpdated = useCallback((runId: string) => {
    // Force a refresh even if the same run id fires twice in a row.
    setArtifactRefreshRunId(null)
    requestAnimationFrame(() => setArtifactRefreshRunId(runId))
  }, [])

  return (
    <div className="app-shell">
      <div className="topbar">
        <div className="topbar-logo">
          <span className="topbar-logo-icon"><LogoIcon /></span>
          <span className="topbar-title">Video Studio</span>
        </div>
        <span className="topbar-sep" />
        <span className={`topbar-status ${connected ? 'connected' : 'disconnected'}`}>
          {connected ? '● Claude Code connected' : '○ connecting…'}
        </span>
        <span className="topbar-spacer" />
        <span className="topbar-badge">local · M5 Pro</span>
      </div>

      <div className="panels">
        <ChatPanel
          currentRunId={currentRunId}
          onArtifactUpdated={handleArtifactUpdated}
          onConnectionChange={setConnected}
        />
        <FlowViewer
          artifactRefreshRunId={artifactRefreshRunId}
          onRunIdChange={setCurrentRunId}
        />
      </div>
    </div>
  )
}
