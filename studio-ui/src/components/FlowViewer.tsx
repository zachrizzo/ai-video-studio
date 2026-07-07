import { useEffect, useState, useCallback } from 'react'
import { fetchRuns, fetchRun, startRunProduction } from '../api'
import type { RunSummary, RunDetail } from '../api'
import { SegmentCard } from './SegmentCard'

interface FlowViewerProps {
  /** run_id to highlight / auto-refresh when an artifact_updated event arrives */
  artifactRefreshRunId: string | null
  /** The active project — the flow list is scoped to it. */
  currentProjectId: string | null
  onRunIdChange: (runId: string, title?: string) => void
}

function inProject(run: RunSummary, projectId: string | null): boolean {
  return (run.project_id || 'default') === (projectId || 'default')
}

function formatDuration(s: number): string {
  const m = Math.floor(s / 60)
  const sec = Math.round(s % 60)
  if (m > 0) return `${m}m ${sec}s`
  return `${sec}s`
}

export function FlowViewer({ artifactRefreshRunId, currentProjectId, onRunIdChange }: FlowViewerProps) {
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const projectRuns = runs.filter((r) => inProject(r, currentProjectId))
  const [detail, setDetail] = useState<RunDetail | null>(null)
  const [loadingRuns, setLoadingRuns] = useState(true)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [runsError, setRunsError] = useState<string | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [produceBusy, setProduceBusy] = useState(false)
  const [produceError, setProduceError] = useState<string | null>(null)

  // ── Load run list on mount and whenever the project changes ──────────────
  useEffect(() => {
    setLoadingRuns(true)
    fetchRuns()
      .then((r) => {
        setRuns(r)
        const scoped = r.filter((x) => inProject(x, currentProjectId))
        const selectedStillVisible = scoped.some((x) => x.id === selectedId)
        if (!selectedStillVisible) {
          const first = scoped[0]
          if (first) {
            setSelectedId(first.id)
            onRunIdChange(first.id, first.title)
          } else {
            setSelectedId(null)
            setDetail(null)
          }
        }
      })
      .catch((e: unknown) => {
        const msg = e instanceof Error ? e.message : String(e)
        setRunsError(msg)
      })
      .finally(() => setLoadingRuns(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onRunIdChange, currentProjectId])

  // ── When an artifact event arrives, refresh the run list. If it names a run
  //    we don't have selected (e.g. a brand-new chat-created run), switch to it.
  useEffect(() => {
    if (!artifactRefreshRunId) return
    fetchRuns()
      .then((r) => {
        setRuns(r)
        const known = r.some((x) => x.id === artifactRefreshRunId)
        const run = r.find((x) => x.id === artifactRefreshRunId)
        if (known && artifactRefreshRunId !== selectedId) {
          setSelectedId(artifactRefreshRunId)
          onRunIdChange(artifactRefreshRunId, run?.title)
        }
      })
      .catch(() => {})
  }, [artifactRefreshRunId, selectedId, onRunIdChange])

  // ── Load run detail whenever selected run changes ─────────────────────────
  const loadDetail = useCallback((runId: string, showLoading = true) => {
    if (showLoading) setLoadingDetail(true)
    setDetailError(null)
    fetchRun(runId)
      .then(setDetail)
      .catch((e: unknown) => {
        const msg = e instanceof Error ? e.message : String(e)
        setDetailError(msg)
      })
      .finally(() => {
        if (showLoading) setLoadingDetail(false)
      })
  }, [])

  useEffect(() => {
    if (selectedId) loadDetail(selectedId)
  }, [selectedId, loadDetail])

  useEffect(() => {
    if (!selectedId) return
    const run = runs.find((r) => r.id === selectedId)
    if (run) onRunIdChange(selectedId, run.title)
  }, [onRunIdChange, runs, selectedId])

  // ── Refresh when artifact_updated fires for this run ─────────────────────
  useEffect(() => {
    if (artifactRefreshRunId && artifactRefreshRunId === selectedId) {
      loadDetail(artifactRefreshRunId)
    }
  }, [artifactRefreshRunId, selectedId, loadDetail])

  useEffect(() => {
    if (!selectedId || detail?.production?.status !== 'running') return
    const timer = window.setInterval(() => {
      loadDetail(selectedId, false)
      fetchRuns().then(setRuns).catch(() => {})
    }, 3000)
    return () => window.clearInterval(timer)
  }, [detail?.production?.status, loadDetail, selectedId])

  // ── Handlers ─────────────────────────────────────────────────────────────
  function handleRunChange(e: React.ChangeEvent<HTMLSelectElement>) {
    const id = e.target.value
    const run = runs.find((r) => r.id === id)
    setSelectedId(id)
    onRunIdChange(id, run?.title)
  }

  function handleProduce(mode: 'full' | 'videos' = 'full') {
    if (!selectedId) return
    const runId = selectedId
    setProduceBusy(true)
    setProduceError(null)
    const options = mode === 'videos' ? { mode, force_video: true } : undefined
    startRunProduction(runId, options)
      .then((production) => {
        setDetail((current) => (
          current && current.id === production.run_id
            ? { ...current, production }
            : current
        ))
        loadDetail(runId, false)
        fetchRuns().then(setRuns).catch(() => {})
      })
      .catch((e: unknown) => {
        const msg = e instanceof Error ? e.message : String(e)
        setProduceError(msg)
      })
      .finally(() => setProduceBusy(false))
  }

  // ── Computed ──────────────────────────────────────────────────────────────
  const doneCount = detail
    ? detail.segments.filter((s) => s.status === 'done' || s.status === 'approved').length
    : 0
  const approvedCount = detail ? detail.segments.filter((s) => s.status === 'approved').length : 0
  const totalCount = detail ? detail.segments.length : 0
  const production = detail?.production ?? null
  const isProducing = production?.status === 'running'
  const productionNeedsAction = production?.status === 'failed' || production?.status === 'stalled'
  const showProduceButton = Boolean(
    detail && selectedId && (isProducing || productionNeedsAction || !detail.final_video_url)
  )
  const hasExistingImages = Boolean(
    detail?.segments.some((s) => (
      (s.image_urls?.length ?? 0) > 0 || Boolean(s.image_url)
    ))
  )
  const showRerunVideosButton = Boolean(detail && selectedId && hasExistingImages && !isProducing)
  const produceButtonText = isProducing
    ? (production?.step_label || 'Producing')
    : productionNeedsAction
      ? 'Resume production'
      : 'Produce video'
  const productionText = production
    ? production.status === 'running'
      ? `${Math.round(production.progress)}% · ${production.step_label}`
      : production.status === 'failed'
        ? `failed${production.error ? `: ${production.error}` : ''}`
          : production.status === 'stalled'
            ? 'stalled · resume needed'
            : production.status === 'done'
              ? (production.step_label || 'video ready')
              : null
    : null

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
            {projectRuns.length === 0 && <option value="">No videos in this project yet</option>}
            {projectRuns.map((r) => (
              <option key={r.id} value={r.id}>
                {r.title || r.id}
              </option>
            ))}
          </select>
        )}

        {detail && (
          <span className="run-status">
            <em>{doneCount}/{totalCount}</em> segments done
            {approvedCount > 0 && <> · <em>{approvedCount}</em> approved</>}
          </span>
        )}

        {detail && (
          <div className="run-production">
            {productionText && (
              <span className={`production-note ${production?.status ?? ''}`}>
                {productionText}
              </span>
            )}
            {produceError && (
              <span className="production-note failed">start failed: {produceError}</span>
            )}
            {showProduceButton && (
              <button
                className="produce-btn"
                onClick={() => handleProduce('full')}
                disabled={produceBusy || isProducing}
                title={isProducing ? 'Production is running' : 'Start full video production'}
              >
                {(produceBusy || isProducing) && <span className="spinner" />}
                {produceButtonText}
              </button>
            )}
            {showRerunVideosButton && (
              <button
                className="produce-btn secondary"
                onClick={() => handleProduce('videos')}
                disabled={produceBusy || isProducing}
                title="Regenerate LTX clips from existing images"
              >
                {produceBusy && <span className="spinner" />}
                Rerun videos
              </button>
            )}
            {detail.total_duration_seconds > 0 && (
              <span className="run-duration">
                {formatDuration(detail.total_duration_seconds)} total
              </span>
            )}
          </div>
        )}
      </div>

      {/* Final video */}
      {detail?.final_video_url && (
        <div className="final-video-wrap">
          <div className="final-video-label">
            <span>▶ Final Video</span>
            {detail.qa && (
              <span className={`qa-summary ${detail.qa.status}`}>
                QA {detail.qa.status}
                {detail.qa.summary.errors > 0 && ` · ${detail.qa.summary.errors} errors`}
                {detail.qa.summary.warnings > 0 && ` · ${detail.qa.summary.warnings} warnings`}
              </span>
            )}
          </div>
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
