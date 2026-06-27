import { useCallback, useState } from 'react'
import { ChatPanel } from './components/ChatPanel'
import { FlowViewer } from './components/FlowViewer'
import { GeneratePanel } from './components/GeneratePanel'
import { PresetBar } from './components/PresetBar'

type Tab = 'story' | 'generate'

function LogoIcon() {
  return (
    <svg viewBox="0 0 20 20" xmlns="http://www.w3.org/2000/svg">
      <path d="M4 3.5v13l11-6.5L4 3.5Z" />
    </svg>
  )
}

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('story')
  const [currentRunId, setCurrentRunId] = useState<string | null>(null)
  const [currentRunTitle, setCurrentRunTitle] = useState<string | null>(null)
  const [artifactRefreshRunId, setArtifactRefreshRunId] = useState<string | null>(null)
  const [connected, setConnected] = useState(false)
  const [activePreset, setActivePreset] = useState<any>(null)

  const handleArtifactUpdated = useCallback((runId: string) => {
    setArtifactRefreshRunId(null)
    requestAnimationFrame(() => setArtifactRefreshRunId(runId))
  }, [])

  const handleRunChange = useCallback((runId: string, title?: string) => {
    setCurrentRunId(runId)
    setCurrentRunTitle(title || null)
  }, [])

  return (
    <div className="app-shell">
      <div className="topbar">
        <div className="topbar-logo">
          <span className="topbar-logo-icon"><LogoIcon /></span>
          <span className="topbar-title">Video Studio</span>
        </div>
        <span className="topbar-sep" />
        <div className="topbar-tabs">
          <button
            className={`topbar-tab ${activeTab === 'story' ? 'active' : ''}`}
            onClick={() => setActiveTab('story')}
          >
            Story
          </button>
          <button
            className={`topbar-tab ${activeTab === 'generate' ? 'active' : ''}`}
            onClick={() => setActiveTab('generate')}
          >
            Generate
          </button>
        </div>
        <span className="topbar-sep" />
        <span className={`topbar-status ${connected ? 'connected' : 'disconnected'}`}>
          {connected ? '● Claude Code connected' : '○ connecting…'}
        </span>
        <span className="topbar-spacer" />
        <span className="topbar-badge">local · M5 Pro</span>
      </div>

      <div style={{ display: activeTab === 'story' ? 'flex' : 'none', flexDirection: 'column', flex: 1, overflow: 'hidden' }}>
        <PresetBar onPresetChange={setActivePreset} />
        <div className="panels">
          <ChatPanel
            currentRunId={currentRunId}
            currentRunTitle={currentRunTitle}
            onArtifactUpdated={handleArtifactUpdated}
            onConnectionChange={setConnected}
            activePreset={activePreset}
          />
          <FlowViewer
            artifactRefreshRunId={artifactRefreshRunId}
            onRunIdChange={handleRunChange}
          />
        </div>
      </div>
      <GeneratePanel style={{ display: activeTab === 'generate' ? 'flex' : 'none' }} />
    </div>
  )
}
