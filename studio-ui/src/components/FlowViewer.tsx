import { Fragment, useEffect, useState, useCallback, useRef } from 'react'
import '../styles/flow-viewer.css'
import { fetchRuns, fetchRun, startRunProduction, stopRunProduction } from '../api'
import type {
  RunSummary,
  RunDetail,
  RunProductionStatus,
  RunQA,
  StartRunProductionOptions,
} from '../api'
import { SegmentCard } from './SegmentCard'
import { formatDuration } from './formatters'

interface FlowViewerProps {
  /** run_id to highlight / auto-refresh when an artifact_updated event arrives */
  artifactRefreshRunId: string | null
  /** The active project — the flow list is scoped to it. */
  currentProjectId: string | null
  onRunIdChange: (runId: string, title?: string) => void
  /** The currently selected style preset's id, sent along with Produce so
   * production picks up the preset's voice/quality/video-provider overrides. */
  activePresetId?: string | null
}

function inProject(run: RunSummary, projectId: string | null): boolean {
  return (run.project_id || 'default') === (projectId || 'default')
}

// ── Pipeline stages ──────────────────────────────────────────────────────────
// The real step order comes from the backend (GET /api/pipeline-steps, which
// exposes producer._pipeline_steps). The frontend only owns the cosmetic
// grouping of those steps into plain-language stages so the stepper stays
// readable; unknown step ids fall back to their backend-provided label.

interface PipelineStep {
  id: string
  label: string
}

type PipelineStepsByMode = Record<string, PipelineStep[]>

async function fetchPipelineSteps(): Promise<PipelineStepsByMode> {
  const res = await fetch('/api/pipeline-steps')
  if (!res.ok) {
    throw new Error(`API /api/pipeline-steps → ${res.status} ${res.statusText}`)
  }
  const data = (await res.json()) as { modes: PipelineStepsByMode }
  return data.modes
}

const STAGE_LABELS: Record<string, string> = {
  storyboard: 'Storyboard',
  narration: 'Narration',
  timing: 'Timing',
  sound: 'Sound',
  images: 'Images',
  animation: 'Animation',
  assembly: 'Assembly',
  quality: 'Quality',
}

// Step id → stage key. 'storyboard' means the timing refresh except when it is
// the opening step of the full pipeline (the initial storyboard build).
const STEP_STAGE: Record<string, string> = {
  storyboard: 'timing',
  synthesize: 'narration',
  align: 'timing',
  sfx: 'sound',
  imagegen: 'images',
  assets: 'images',
  videogen: 'animation',
  collage: 'animation',
  manifest: 'assembly',
  composite: 'assembly',
  qa: 'quality',
}

type StageState = 'pending' | 'active' | 'done' | 'failed'

interface StageView {
  key: string
  label: string
  state: StageState
}

interface PipelineView {
  stages: StageView[]
  stepNumber: number
  totalSteps: number
}

/**
 * Map the backend production status onto the backend-reported pipeline.
 * Returns null when the steps haven't loaded yet or don't match what the
 * production status reports (e.g. server restarted mid-deploy with a changed
 * pipeline) so the caller can fall back to the honest raw status line instead
 * of showing a made-up stepper.
 */
function derivePipeline(
  production: RunProductionStatus,
  stepsByMode: PipelineStepsByMode | null,
): PipelineView | null {
  if (!stepsByMode) return null
  const mode = production.mode && stepsByMode[production.mode] ? production.mode : 'full'
  const steps = stepsByMode[mode]
  if (!steps || production.total_steps !== steps.length) return null

  let stepIdx: number
  if (production.status === 'done') {
    stepIdx = steps.length
  } else {
    // Backend sets progress = round(((index - 1) / total) * 100) when a step
    // starts, so this recovers the zero-based index of the current step.
    stepIdx = Math.round((production.progress / 100) * steps.length)
    stepIdx = Math.min(Math.max(stepIdx, 0), steps.length - 1)
    if (production.step && steps[stepIdx].id !== production.step) {
      const byId = steps.findIndex((s) => s.id === production.step)
      if (byId === -1) return null
      stepIdx = byId
    }
  }

  const broken =
    production.status === 'failed' ||
    production.status === 'stalled' ||
    production.status === 'stopped'
  const stages: StageView[] = []
  steps.forEach((step, i) => {
    const stageKey =
      step.id === 'storyboard' && i === 0 && mode === 'full'
        ? 'storyboard'
        : STEP_STAGE[step.id] ?? step.id
    if (stages.length === 0 || stages[stages.length - 1].key !== stageKey) {
      stages.push({
        key: stageKey,
        label: STAGE_LABELS[stageKey] ?? step.label,
        state: 'pending',
      })
    }
    const stage = stages[stages.length - 1]
    if (i < stepIdx && stage.state === 'pending') stage.state = 'done'
    if (i === stepIdx) stage.state = broken ? 'failed' : 'active'
  })

  return { stages, stepNumber: Math.min(stepIdx + 1, steps.length), totalSteps: steps.length }
}

// ── Production progress strip ────────────────────────────────────────────────

function ProductionProgress({
  production,
  pipelineSteps,
}: {
  production: RunProductionStatus
  pipelineSteps: PipelineStepsByMode | null
}) {
  const pipeline = derivePipeline(production, pipelineSteps)
  const running = production.status === 'running'
  const stalled = production.status === 'stalled'
  const stopped = production.status === 'stopped'
  const progress = Math.min(Math.max(Math.round(production.progress), 0), 100)

  const note = running
    ? pipeline
      ? `Step ${pipeline.stepNumber} of ${pipeline.totalSteps} — ${production.step_label}…`
      : `${production.step_label}… ${progress}%`
    : stalled
      ? 'Production paused before finishing — press Resume to pick up where it left off.'
      : stopped
        ? 'Production stopped — press Resume to continue from where it left off.'
        : `Production hit a problem${production.error ? `: ${production.error}` : ''}`

  return (
    <div className={`production-strip ${production.status}`} role="status">
      <div className="production-strip-row">
        {pipeline ? (
          <div className="stepper production-stepper">
            {pipeline.stages.map((stage, i) => (
              <Fragment key={stage.key}>
                {i > 0 && <span className="stepper-connector" />}
                <span
                  className={`stepper-step${stage.state === 'pending' ? '' : ` ${stage.state}`}`}
                >
                  <span className="stepper-step-dot" />
                  {stage.label}
                </span>
              </Fragment>
            ))}
          </div>
        ) : (
          running && <span className="spinner" />
        )}
        <span className="production-note" aria-live="polite" title={note}>
          {note}
        </span>
      </div>
      <div className="production-bar">
        <div className="production-bar-fill" style={{ width: `${progress}%` }} />
      </div>
    </div>
  )
}

// ── QA badge helpers ─────────────────────────────────────────────────────────

function qaBadgeTone(status: RunQA['status']): string {
  if (status === 'passed') return 'badge-success'
  if (status === 'warning') return 'badge-warning'
  return 'badge-danger'
}

function qaBadgeText(qa: RunQA): string {
  if (qa.status === 'passed') return 'Quality check passed'
  const parts: string[] = []
  if (qa.summary.errors > 0) {
    parts.push(`${qa.summary.errors} error${qa.summary.errors === 1 ? '' : 's'}`)
  }
  if (qa.summary.warnings > 0) {
    parts.push(`${qa.summary.warnings} warning${qa.summary.warnings === 1 ? '' : 's'}`)
  }
  return `Quality check — ${parts.join(', ') || qa.status}`
}

// ── FlowViewer ───────────────────────────────────────────────────────────────

export function FlowViewer({ artifactRefreshRunId, currentProjectId, onRunIdChange, activePresetId }: FlowViewerProps) {
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
  const [stopBusy, setStopBusy] = useState(false)
  const [stopError, setStopError] = useState<string | null>(null)
  const [speed, setSpeed] = useState(1)
  const [pipelineSteps, setPipelineSteps] = useState<PipelineStepsByMode | null>(null)
  const selectedIdRef = useRef<string | null>(null)
  useEffect(() => {
    selectedIdRef.current = selectedId
  }, [selectedId])

  // ── Load pipeline step order once; on failure the progress strip simply
  //    falls back to the plain status line instead of the stepper.
  useEffect(() => {
    let cancelled = false
    fetchPipelineSteps()
      .then((modes) => {
        if (!cancelled) setPipelineSteps(modes)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

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
  //    Runs outside the current project only refresh the list — a turn still
  //    streaming for project A must not hijack the viewer while the user is
  //    in project B.
  useEffect(() => {
    if (!artifactRefreshRunId) return
    fetchRuns()
      .then((r) => {
        setRuns(r)
        const run = r.find((x) => x.id === artifactRefreshRunId && inProject(x, currentProjectId))
        if (run && artifactRefreshRunId !== selectedId) {
          setSelectedId(artifactRefreshRunId)
          onRunIdChange(artifactRefreshRunId, run.title)
        }
      })
      .catch(() => {})
  }, [artifactRefreshRunId, selectedId, onRunIdChange, currentProjectId])

  // ── Load run detail whenever selected run changes ─────────────────────────
  const loadDetail = useCallback((runId: string, showLoading = true) => {
    if (showLoading) setLoadingDetail(true)
    setDetailError(null)
    fetchRun(runId)
      .then((result) => {
        if (selectedIdRef.current === runId) setDetail(result)
      })
      .catch((e: unknown) => {
        const msg = e instanceof Error ? e.message : String(e)
        if (selectedIdRef.current === runId) setDetailError(msg)
      })
      .finally(() => {
        if (showLoading && selectedIdRef.current === runId) setLoadingDetail(false)
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
    const options: StartRunProductionOptions = {}
    if (mode === 'videos') {
      options.mode = mode
      options.force_video = true
    }
    if (speed !== 1) options.speed = speed
    if (activePresetId) options.preset_id = activePresetId
    startRunProduction(runId, Object.keys(options).length > 0 ? options : undefined)
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

  function handleStop() {
    if (!selectedId) return
    const runId = selectedId
    setStopBusy(true)
    setStopError(null)
    stopRunProduction(runId)
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
        setStopError(msg)
      })
      .finally(() => setStopBusy(false))
  }

  // ── Computed ──────────────────────────────────────────────────────────────
  const doneCount = detail
    ? detail.segments.filter((s) => s.status === 'done' || s.status === 'approved').length
    : 0
  const approvedCount = detail ? detail.segments.filter((s) => s.status === 'approved').length : 0
  const totalCount = detail ? detail.segments.length : 0
  const production = detail?.production ?? null
  const isProducing = production?.status === 'running'
  const productionNeedsAction =
    production?.status === 'failed' ||
    production?.status === 'stalled' ||
    production?.status === 'stopped'
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
    ? 'Producing…'
    : productionNeedsAction
      ? 'Resume production'
      : 'Produce video'
  const hasRuns = projectRuns.length > 0
  const showProductionStrip = Boolean(production && (isProducing || productionNeedsAction))
  const showDoneBadge = production?.status === 'done' && !detail?.final_video_url

  return (
    <div className="flow-viewer">
      {/* Header */}
      <div className="flow-viewer-header">
        {loadingRuns ? (
          <div className="run-select-skeleton skeleton" aria-hidden="true" />
        ) : runsError ? (
          <span className="flow-header-error" title={runsError}>
            Couldn't load videos: {runsError}
          </span>
        ) : hasRuns ? (
          <select
            className="select run-select"
            value={selectedId ?? ''}
            onChange={handleRunChange}
            aria-label="Choose a video"
          >
            {projectRuns.map((r) => (
              <option key={r.id} value={r.id}>
                {r.title || r.id}
              </option>
            ))}
          </select>
        ) : (
          <span className="flow-header-title">Production</span>
        )}

        {detail && totalCount > 0 && (
          <span className="flow-summary">
            {doneCount} of {totalCount} scenes ready
            {approvedCount > 0 && <> · {approvedCount} approved</>}
            {detail.total_duration_seconds > 0 && (
              <> · {formatDuration(detail.total_duration_seconds)}</>
            )}
          </span>
        )}

        {detail && (
          <div className="flow-actions">
            {produceError && (
              <span className="flow-error-note" title={produceError}>
                Couldn't start: {produceError}
              </span>
            )}
            {stopError && (
              <span className="flow-error-note" title={stopError}>
                Couldn't stop: {stopError}
              </span>
            )}
            {showDoneBadge && (
              <span className="badge badge-success">{production?.step_label || 'Done'}</span>
            )}
            {isProducing && (
              <button
                className="btn btn-danger btn-sm"
                onClick={handleStop}
                disabled={stopBusy}
                title="Stop the running production"
              >
                {stopBusy && <span className="spinner" />}
                Stop
              </button>
            )}
            {(showProduceButton || showRerunVideosButton) && !isProducing && (
              <select
                className="select select-compact"
                value={speed}
                onChange={(e) => setSpeed(Number(e.target.value))}
                disabled={produceBusy}
                aria-label="Final video speed"
                title="Playback speed of the final video"
              >
                <option value={0.75}>0.75×</option>
                <option value={1}>1×</option>
                <option value={1.25}>1.25×</option>
                <option value={1.5}>1.5×</option>
                <option value={1.75}>1.75×</option>
                <option value={2}>2×</option>
              </select>
            )}
            {showRerunVideosButton && (
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => handleProduce('videos')}
                disabled={produceBusy || isProducing}
                title="Regenerate the animated clips from the existing images"
              >
                {produceBusy && <span className="spinner" />}
                Rerun videos
              </button>
            )}
            {showProduceButton && (
              <button
                className="btn btn-primary btn-sm"
                onClick={() => handleProduce('full')}
                disabled={produceBusy || isProducing}
                title={isProducing ? 'Production is running' : 'Produce the full video from this storyboard'}
              >
                {(produceBusy || isProducing) && <span className="spinner" />}
                {produceButtonText}
              </button>
            )}
          </div>
        )}
      </div>

      {/* What's happening now */}
      {showProductionStrip && production && (
        <ProductionProgress production={production} pipelineSteps={pipelineSteps} />
      )}

      {/* Final video */}
      {detail?.final_video_url && (
        <div className="final-video-wrap">
          <div className="final-video-head">
            <span className="final-video-title">Final video</span>
            {detail.qa && (
              <span className={`badge ${qaBadgeTone(detail.qa.status)}`}>
                {qaBadgeText(detail.qa)}
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
          <>
            <div className="segment-skeleton skeleton" />
            <div className="segment-skeleton skeleton" />
            <div className="segment-skeleton skeleton" />
          </>
        )}

        {detailError && !loadingDetail && (
          <div className="error-state">Couldn't load this video: {detailError}</div>
        )}

        {!loadingRuns && !runsError && !hasRuns && (
          <div className="empty-state">
            <svg
              className="empty-state-icon"
              width="40"
              height="40"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <rect x="2.5" y="4.5" width="19" height="15" rx="2" />
              <path d="M7 4.5v15M17 4.5v15M2.5 9.5H7M2.5 14.5H7M17 9.5h4.5M17 14.5h4.5" />
            </svg>
            <div className="empty-state-title">No videos yet</div>
            <div className="empty-state-desc">
              Ask Claude in the chat to start a storyboard — your video will appear here as it
              takes shape.
            </div>
          </div>
        )}

        {!loadingDetail && !detailError && detail && detail.segments.length === 0 && (
          <div className="empty-state">
            <svg
              className="empty-state-icon"
              width="40"
              height="40"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <rect x="3" y="4" width="8" height="6" rx="1" />
              <rect x="13" y="4" width="8" height="6" rx="1" />
              <rect x="3" y="14" width="8" height="6" rx="1" />
              <path d="M13 16h8M13 19h5" />
            </svg>
            <div className="empty-state-title">No scenes yet</div>
            <div className="empty-state-desc">
              This video doesn't have a storyboard yet — ask Claude in the chat to plan its
              scenes.
            </div>
          </div>
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
