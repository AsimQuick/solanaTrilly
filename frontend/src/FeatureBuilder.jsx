// ---
// file: frontend/src/FeatureBuilder.jsx
// stack: react+vite
// purpose: Feature Builder UI — trigger the §6.5 one-click labeled export (US-31, US-57 AC-57.1)
//   and surface the export RESULT — MANIFEST + content hash (US-57 AC-57.2).
//   Invokes POST /api/export/trigger/ which dispatches the existing build_features Celery task
//   (core.tasks.build_features) to the celery-worker container.  After triggering, polls
//   GET /api/export/result/<task_id>/ every 5s (up to 60s) and displays the MANIFEST when
//   complete.  This view is a pure trigger + result surface — no export math lives here.
// created-by: dev-team
// sprint: sprint-11
// story: US-57 AC-57.1 AC-57.2
// last-updated: 2026-06-18
// ---

import { useState, useEffect, useRef } from 'react'

const POLL_INTERVAL_MS = 5000
const POLL_MAX_ATTEMPTS = 12  // 12 * 5s = 60s

const STYLES = {
  container: {
    fontFamily: 'monospace',
    color: '#d1d4dc',
    maxWidth: 700,
  },
  heading: {
    color: '#d1d4dc',
    marginBottom: 4,
  },
  sub: {
    color: '#9598a1',
    fontSize: 13,
    marginBottom: 20,
    marginTop: 0,
  },
  section: {
    background: '#1e2130',
    border: '1px solid #2a2d3e',
    borderRadius: 6,
    padding: '16px 20px',
    marginBottom: 16,
  },
  label: {
    color: '#9598a1',
    fontSize: 12,
    marginBottom: 4,
    display: 'block',
  },
  value: {
    color: '#d1d4dc',
    fontSize: 14,
  },
  button: {
    background: '#26a69a',
    color: '#fff',
    border: 'none',
    borderRadius: 4,
    padding: '8px 20px',
    fontSize: 14,
    cursor: 'pointer',
    marginTop: 8,
  },
  buttonDisabled: {
    background: '#3a3f5c',
    color: '#9598a1',
    border: 'none',
    borderRadius: 4,
    padding: '8px 20px',
    fontSize: 14,
    cursor: 'not-allowed',
    marginTop: 8,
  },
  status: {
    marginTop: 16,
    padding: '12px 16px',
    borderRadius: 4,
    fontSize: 13,
  },
  statusQueued: {
    background: '#1a2a1a',
    border: '1px solid #26a69a',
    color: '#26a69a',
  },
  statusRunning: {
    background: '#1a221a',
    border: '1px solid #ffb300',
    color: '#ffb300',
  },
  statusComplete: {
    background: '#0e1f1a',
    border: '1px solid #26a69a',
    color: '#26a69a',
  },
  statusFailed: {
    background: '#2a1a1a',
    border: '1px solid #ef5350',
    color: '#ef5350',
  },
  statusError: {
    background: '#2a1a1a',
    border: '1px solid #ef5350',
    color: '#ef5350',
  },
  manifestSection: {
    background: '#131722',
    border: '1px solid #2a2d3e',
    borderRadius: 6,
    padding: '14px 18px',
    marginTop: 14,
  },
  manifestHeading: {
    color: '#9598a1',
    fontSize: 12,
    fontWeight: 'bold',
    marginBottom: 10,
    textTransform: 'uppercase',
    letterSpacing: '0.08em',
  },
  field: {
    marginBottom: 12,
  },
  code: {
    background: '#131722',
    padding: '2px 6px',
    borderRadius: 3,
    fontSize: 12,
    color: '#81d4fa',
  },
  hash: {
    background: '#131722',
    padding: '2px 6px',
    borderRadius: 3,
    fontSize: 11,
    color: '#ce93d8',
    wordBreak: 'break-all',
  },
  row: {
    display: 'flex',
    gap: 32,
    flexWrap: 'wrap',
  },
  statusPill: {
    display: 'inline-block',
    padding: '2px 10px',
    borderRadius: 10,
    fontSize: 12,
    fontWeight: 'bold',
    marginLeft: 8,
  },
}

function statusPillStyle(status) {
  if (status === 'complete') return { background: '#1b3a2a', color: '#26a69a', border: '1px solid #26a69a' }
  if (status === 'running') return { background: '#2a2200', color: '#ffb300', border: '1px solid #ffb300' }
  if (status === 'failed')  return { background: '#2a1a1a', color: '#ef5350', border: '1px solid #ef5350' }
  return { background: '#1e2130', color: '#9598a1', border: '1px solid #2a2d3e' }
}

export default function FeatureBuilder() {
  const [taskResult, setTaskResult] = useState(null)    // trigger response
  const [exportResult, setExportResult] = useState(null) // poll response
  const [exportStatus, setExportStatus] = useState(null) // queued/running/complete/failed
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)
  const [polling, setPolling] = useState(false)
  const pollRef = useRef(null)
  const pollCountRef = useRef(0)

  // Stop polling on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [])

  function startPolling(taskId) {
    pollCountRef.current = 0
    setPolling(true)
    pollRef.current = setInterval(async () => {
      pollCountRef.current += 1
      try {
        const resp = await fetch(`/api/export/result/${taskId}/`)
        const data = await resp.json()
        setExportStatus(data.status)
        if (data.status === 'complete' || data.status === 'failed') {
          setExportResult(data)
          setPolling(false)
          clearInterval(pollRef.current)
        } else if (pollCountRef.current >= POLL_MAX_ATTEMPTS) {
          // Timed out — stop polling, keep last known status
          setPolling(false)
          clearInterval(pollRef.current)
        }
      } catch (e) {
        // Network error during poll — stop silently
        setPolling(false)
        clearInterval(pollRef.current)
      }
    }, POLL_INTERVAL_MS)
  }

  async function triggerExport() {
    setLoading(true)
    setError(null)
    setTaskResult(null)
    setExportResult(null)
    setExportStatus(null)
    if (pollRef.current) clearInterval(pollRef.current)
    try {
      const resp = await fetch('/api/export/trigger/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      })
      const data = await resp.json()
      if (!resp.ok) {
        setError(data.error || `HTTP ${resp.status}`)
      } else {
        setTaskResult(data)
        setExportStatus('queued')
        startPolling(data.task_id)
      }
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  const manifest = exportResult?.manifest

  return (
    <div style={STYLES.container}>
      <h2 style={STYLES.heading}>Feature Builder</h2>
      <p style={STYLES.sub}>
        Triggers the §6.5 one-click labeled export (US-31). Parameters are read from the
        active PipelineConfig. The export runs on the celery-worker container (never web/gunicorn).
      </p>

      <div style={STYLES.section}>
        <div style={STYLES.field}>
          <span style={STYLES.label}>Export task</span>
          <span style={STYLES.value}>
            <code style={STYLES.code}>core.tasks.build_features</code>
            {' '}— dispatched via Celery (US-31, §6.5)
          </span>
        </div>
        <div style={STYLES.field}>
          <span style={STYLES.label}>Parameters</span>
          <span style={STYLES.value}>
            Config-driven: <code style={STYLES.code}>feature_set_id</code> and{' '}
            <code style={STYLES.code}>label_def</code> read from active PipelineConfig
          </span>
        </div>
        <div style={STYLES.field}>
          <span style={STYLES.label}>Mint cohort</span>
          <span style={STYLES.value}>All distinctly annotated mints (default)</span>
        </div>
        <button
          style={loading ? STYLES.buttonDisabled : STYLES.button}
          onClick={triggerExport}
          disabled={loading}
        >
          {loading ? 'Dispatching…' : 'Trigger Export'}
        </button>
      </div>

      {/* Trigger queued confirmation */}
      {taskResult && exportStatus === 'queued' && !exportResult && (
        <div style={{ ...STYLES.status, ...STYLES.statusQueued }}>
          <div style={{ marginBottom: 8, fontWeight: 'bold' }}>
            Export queued
            <span style={{ ...STYLES.statusPill, ...statusPillStyle('queued') }}>queued</span>
          </div>
          <div style={STYLES.row}>
            <div style={STYLES.field}>
              <span style={STYLES.label}>Task ID</span>
              <code style={STYLES.code}>{taskResult.task_id}</code>
            </div>
            <div style={STYLES.field}>
              <span style={STYLES.label}>Dataset ID</span>
              <code style={STYLES.code}>{taskResult.dataset_id}</code>
            </div>
            <div style={STYLES.field}>
              <span style={STYLES.label}>Feature set</span>
              <code style={STYLES.code}>{taskResult.feature_set_id}</code>
            </div>
          </div>
          {polling && (
            <div style={{ color: '#9598a1', fontSize: 12, marginTop: 4 }}>
              Polling for result every 5s (up to 60s)…
            </div>
          )}
        </div>
      )}

      {/* Running state */}
      {exportStatus === 'running' && !exportResult && (
        <div style={{ ...STYLES.status, ...STYLES.statusRunning }}>
          <div style={{ marginBottom: 8, fontWeight: 'bold' }}>
            Export running
            <span style={{ ...STYLES.statusPill, ...statusPillStyle('running') }}>running</span>
          </div>
          <div style={{ color: '#9598a1', fontSize: 12 }}>
            Task ID: <code style={STYLES.code}>{taskResult?.task_id}</code>
          </div>
          {polling && (
            <div style={{ color: '#9598a1', fontSize: 12, marginTop: 4 }}>
              Polling for result every 5s…
            </div>
          )}
        </div>
      )}

      {/* Complete — show MANIFEST */}
      {exportResult && exportResult.status === 'complete' && manifest && (
        <div style={{ ...STYLES.status, ...STYLES.statusComplete }}>
          <div style={{ marginBottom: 8, fontWeight: 'bold' }}>
            Export complete
            <span style={{ ...STYLES.statusPill, ...statusPillStyle('complete') }}>complete</span>
          </div>
          <div style={STYLES.row}>
            <div style={STYLES.field}>
              <span style={STYLES.label}>Task ID</span>
              <code style={STYLES.code}>{exportResult.task_id}</code>
            </div>
            <div style={STYLES.field}>
              <span style={STYLES.label}>Dataset ID</span>
              <code style={STYLES.code}>{taskResult?.dataset_id}</code>
            </div>
            <div style={STYLES.field}>
              <span style={STYLES.label}>Row count</span>
              <span style={STYLES.value}>{exportResult.row_count ?? manifest.row_count}</span>
            </div>
          </div>

          {/* MANIFEST panel */}
          <div style={STYLES.manifestSection}>
            <div style={STYLES.manifestHeading}>Export MANIFEST</div>
            <div style={STYLES.field}>
              <span style={STYLES.label}>Content hash (SHA-256)</span>
              <span style={STYLES.hash}>{manifest.content_hash}</span>
            </div>
            <div style={STYLES.row}>
              <div style={STYLES.field}>
                <span style={STYLES.label}>Date range</span>
                <span style={STYLES.value}>
                  {manifest.date_range?.start} → {manifest.date_range?.end}
                </span>
              </div>
              <div style={STYLES.field}>
                <span style={STYLES.label}>Feature set version</span>
                <code style={STYLES.code}>{manifest.feature_set_version}</code>
              </div>
              <div style={STYLES.field}>
                <span style={STYLES.label}>Feature set hash</span>
                <span style={STYLES.hash}>{manifest.feature_set_hash}</span>
              </div>
            </div>
            <div style={STYLES.row}>
              <div style={STYLES.field}>
                <span style={STYLES.label}>Mint cohort size</span>
                <span style={STYLES.value}>{manifest.mint_cohort?.length ?? '—'}</span>
              </div>
              <div style={STYLES.field}>
                <span style={STYLES.label}>Sources</span>
                <span style={STYLES.value}>
                  {Array.isArray(manifest.sources) ? manifest.sources.join(', ') : manifest.sources}
                </span>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Failed state */}
      {exportResult && exportResult.status === 'failed' && (
        <div style={{ ...STYLES.status, ...STYLES.statusFailed }}>
          <div style={{ marginBottom: 8, fontWeight: 'bold' }}>
            Export failed
            <span style={{ ...STYLES.statusPill, ...statusPillStyle('failed') }}>failed</span>
          </div>
          <div>Error: {exportResult.error}</div>
        </div>
      )}

      {error && (
        <div style={{ ...STYLES.status, ...STYLES.statusError }}>
          <strong>Error: </strong>{error}
        </div>
      )}
    </div>
  )
}
