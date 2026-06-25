import { useEffect, useState, useCallback } from 'react'
import { fetchRuns, fetchRun } from '../api'
import type { RunSummary, RunDetail } from '../api'
import { SegmentCard } from './SegmentCard'

interface FlowViewerProps {
  /** run_id to highlight / auto-refresh when an artifact_updated event arrives */
  artifactRefreshRunId: string | null
  onRunIdChange: (runId: string) => void
}

function formatDuration(s: number): string {
  const m = Math.floor(s / 60)
  const sec = Math.round(s % 60)
  if (m > 0) return `${m}m ${sec}s`
  return `${sec}s`
}

export function FlowViewer({ artifactRefreshRunId, onRunIdChange }: FlowViewerProps) {
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<RunDetail | null>(null)
  const [loadingRuns, setLoadingRuns] = useState(true)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [runsError, setRunsError] = useState<string | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)

  // ── Load run list on mount ────────────────────────────────────────────────
  useEffect(() => {
    setLoadingRuns(true)
    fetchRuns()
      .then((r) => {
        setRuns(r)
        if (r.length > 0) {
          const first = r[0]
          setSelectedId(first.id)
          onRunIdChange(first.id)
        }
      })
      .catch((e: unknown) => {
        const msg = e instanceof Error ? e.message : String(e)
        setRunsError(msg)
      })
      .finally(() => setLoadingRuns(false))
  }, [onRunIdChange])

  // ── Load run detail whenever selected run changes ─────────────────────────
  const loadDetail = useCallback((runId: string) => {
    setLoadingDetail(true)
    setDetailError(null)
    fetchRun(runId)
      .then(setDetail)
      .catch((e: unknown) => {
        const msg = e instanceof Error ? e.message : String(e)
        setDetailError(msg)
      })
      .finally(() => setLoadingDetail(false))
  }, [])

  useEffect(() => {
    if (selectedId) loadDetail(selectedId)
  }, [selectedId, loadDetail])

  // ── Refresh when artifact_updated fires for this run ─────────────────────
  useEffect(() => {
    if (artifactRefreshRunId && artifactRefreshRunId === selectedId) {
      loadDetail(artifactRefreshRunId)
    }
  }, [artifactRefreshRunId, selectedId, loadDetail])

  // ── Handlers ─────────────────────────────────────────────────────────────
  function handleRunChange(e: React.ChangeEvent<HTMLSelectElement>) {
    const id = e.target.value
    setSelectedId(id)
    onRunIdChange(id)
  }

  // ── Computed ──────────────────────────────────────────────────────────────
  const doneCount = detail ? detail.segments.filter((s) => s.status === 'done').length : 0
  const totalCount = detail ? detail.segments.length : 0

  return (
    <div className="flow-viewer">
      {/* Header */}
      <div className="flow-viewer-header">
        <span className="panel-label">Flow</span>

        {loadingRuns ? (
          <span className="run-status">loading runs…</span>
        ) : runsError ? (
          <span className="run-status" style={{ color: 'var(--red)' }}>error: {runsError}</span>
        ) : (
          <select
            className="run-selector"
            value={selectedId ?? ''}
            onChange={handleRunChange}
            aria-label="Select run"
          >
            {runs.map((r) => (
              <option key={r.id} value={r.id}>
                {r.title || r.id}
              </option>
            ))}
          </select>
        )}

        {detail && (
          <span className="run-status">
            <em>{doneCount}/{totalCount}</em> segments done
          </span>
        )}

        {detail && detail.total_duration_seconds > 0 && (
          <span className="run-duration">
            {formatDuration(detail.total_duration_seconds)} total
          </span>
        )}
      </div>

      {/* Final video */}
      {detail?.final_video_url && (
        <div className="final-video-wrap">
          <div className="final-video-label">▶ Final Video</div>
          <video
            className="final-video"
            src={detail.final_video_url}
            controls
            preload="metadata"
          />
        </div>
      )}

      {/* Segments */}
      <div className="segments-list">
        {loadingDetail && (
          <div className="loading-state">
            <span className="spinner" />
            loading segments…
          </div>
        )}

        {detailError && !loadingDetail && (
          <div className="error-state">failed to load run: {detailError}</div>
        )}

        {!loadingDetail && !detailError && detail && detail.segments.length === 0 && (
          <div className="segments-empty">no segments yet</div>
        )}

        {!loadingDetail && !detailError && detail &&
          detail.segments.map((seg, i) => (
            <SegmentCard key={seg.segment_id} segment={seg} index={i} />
          ))
        }
      </div>
    </div>
  )
}
