import { useRef, useState } from 'react'
import type { Segment, SegmentStatus } from '../api'

// ── Status Badge ─────────────────────────────────────────────────────────────

const STATUS_META: Record<SegmentStatus, { label: string; tone: string }> = {
  approved: { label: 'Approved', tone: 'badge-success' },
  done: { label: 'Ready', tone: 'badge-success' },
  needs_review: { label: 'Needs review', tone: 'badge-warning' },
  qa_failed: { label: 'QA failed', tone: 'badge-danger' },
  pending: { label: 'Waiting', tone: 'badge-neutral' },
  generating: { label: 'Rendering', tone: 'badge-accent' },
  failed: { label: 'Failed', tone: 'badge-danger' },
}

function StatusBadge({ status }: { status: SegmentStatus }) {
  const meta = STATUS_META[status]
  return (
    <span className={`badge ${meta.tone} status-badge${status === 'generating' ? ' generating' : ''}`}>
      <span className="status-dot" />
      {meta.label}
    </span>
  )
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatDuration(s: number): string {
  if (!s) return ''
  const m = Math.floor(s / 60)
  const sec = Math.round(s % 60)
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`
}

function zeroPad(n: number): string {
  return String(n).padStart(2, '0')
}

// ── AudioButton ────────────────────────────────────────────────────────────────

function AudioButton({ src }: { src: string }) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const [playing, setPlaying] = useState(false)

  function toggle() {
    const audio = audioRef.current
    if (!audio) return
    if (audio.paused) {
      audio.play().catch(() => {/* ignore */})
    } else {
      audio.pause()
    }
  }

  return (
    <>
      <audio
        ref={audioRef}
        src={src}
        preload="none"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
      />
      <button className="audio-btn" onClick={toggle}>
        {playing ? (
          <svg width="11" height="12" viewBox="0 0 11 12" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M2.5 1.5v9M8.5 1.5v9" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
          </svg>
        ) : (
          <svg width="11" height="12" viewBox="0 0 11 12" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M1 1.5L10 6L1 10.5V1.5Z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
          </svg>
        )}
        {playing ? 'Pause narration' : 'Play narration'}
      </button>
    </>
  )
}

// ── SegmentCard ────────────────────────────────────────────────────────────────

interface SegmentCardProps {
  segment: Segment
  index: number
}

export function SegmentCard({ segment, index }: SegmentCardProps) {
  const {
    section_title,
    narration_text,
    cues,
    status,
    image_url,
    clip_url,
    scene_url,
    audio_url,
    duration_seconds,
    visual_count,
    storyboard = [],
    qa,
  } = segment

  const imageUrls = segment.image_urls?.length ? segment.image_urls : image_url ? [image_url] : []
  const clipUrls = segment.clip_urls?.length ? segment.clip_urls : clip_url ? [clip_url] : []
  const videoSrc = clipUrls[0] || scene_url || null
  const beatCount = visual_count || Math.max(imageUrls.length, clipUrls.length)

  return (
    <div className="segment-card card">
      {/* Header */}
      <div className="segment-card-header">
        <span className="segment-index" aria-label={`Scene ${index + 1}`}>{zeroPad(index + 1)}</span>
        <span className="segment-title" title={section_title}>{section_title}</span>
        {duration_seconds > 0 && (
          <span className="segment-duration">{formatDuration(duration_seconds)}</span>
        )}
        {beatCount > 1 && (
          <span className="badge badge-neutral segment-beats">{beatCount} visuals</span>
        )}
        <StatusBadge status={status} />
      </div>

      {/* Body — 3 columns */}
      <div className="segment-body">
        {/* Col 1: Script */}
        <div className="seg-col">
          <span className="section-title seg-col-label">Script</span>
          <p className="narration-text">{narration_text}</p>
          {cues.length > 0 && (
            <div className="cues-list">
              {cues.map((cue, i) => (
                <div className="cue-item" key={i}>
                  <span className="cue-timestamp">{cue.timestamp_hint}</span>
                  <span className="cue-desc">{cue.description}</span>
                </div>
              ))}
            </div>
          )}
          {storyboard.length > 0 && (
            <div className="storyboard-list">
              <span className="section-title seg-col-label">Storyboard</span>
              {storyboard.slice(0, 4).map((frame, i) => (
                <div className="storyboard-frame" key={frame.frame_id || `${frame.beat_id}-${i}`}>
                  <div className="storyboard-frame-head">
                    <span className="storyboard-beat">{frame.beat_id || `b${zeroPad(i + 1)}`}</span>
                    {frame.shot_type && <span className="storyboard-shot">{frame.shot_type}</span>}
                    {frame.duration_seconds && (
                      <span className="storyboard-time">{formatDuration(frame.duration_seconds)}</span>
                    )}
                  </div>
                  <p className="storyboard-desc">
                    {frame.description || frame.composition || frame.action || 'planned shot'}
                  </p>
                  {(frame.camera_motion || frame.transition) && (
                    <div className="storyboard-meta">
                      {[frame.camera_motion, frame.transition].filter(Boolean).join(' · ')}
                    </div>
                  )}
                </div>
              ))}
              {storyboard.length > 4 && (
                <div className="storyboard-more">+{storyboard.length - 4} more frames</div>
              )}
            </div>
          )}
        </div>

        {/* Col 2: Images */}
        <div className="seg-col">
          <span className="section-title seg-col-label">Images</span>
          {imageUrls[0] ? (
            <img
              className="seg-image"
              src={imageUrls[0]}
              alt={`Frame for ${section_title}`}
              loading="lazy"
            />
          ) : (
            <div className="media-placeholder">No images yet</div>
          )}
          {imageUrls.length > 1 && (
            <div className="beat-strip" aria-label={`${imageUrls.length} visual beats`}>
              {imageUrls.slice(0, 5).map((url, i) => (
                <img
                  className="beat-thumb"
                  src={url}
                  alt={`${section_title} visual ${i + 1}`}
                  loading="lazy"
                  key={url}
                />
              ))}
              {imageUrls.length > 5 && (
                <span className="beat-more">+{imageUrls.length - 5}</span>
              )}
            </div>
          )}
        </div>

        {/* Col 3: Clip */}
        <div className="seg-col">
          <span className="section-title seg-col-label">Clip</span>
          {videoSrc ? (
            <video
              className="seg-video"
              src={videoSrc}
              controls
              preload="metadata"
              title={section_title}
            />
          ) : (
            <div className="media-placeholder">No clip yet</div>
          )}
          {clipUrls.length > 1 && (
            <div className="beat-progress">
              {clipUrls.length} clips rendered
            </div>
          )}
          {audio_url && <AudioButton src={audio_url} />}
          {qa && qa.checks.length > 0 && (
            <div className="qa-issues">
              <span className="section-title seg-col-label">Quality</span>
              {qa.checks.slice(0, 3).map((check, i) => (
                <div className={`qa-issue ${check.severity}`} key={`${check.id}-${i}`}>
                  <span className="qa-issue-severity">{check.severity}</span>
                  <span className="qa-issue-message">{check.message}</span>
                </div>
              ))}
              {qa.checks.length > 3 && (
                <div className="qa-issue more">+{qa.checks.length - 3} more</div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
