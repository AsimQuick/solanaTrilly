// ---
// file: frontend/src/FeatureBuilder.jsx
// stack: react+vite
// purpose: Feature Builder UI — trigger the §6.5 one-click labeled export (US-31, US-57 AC-57.1).
//   Invokes POST /api/export/trigger/ which dispatches the existing build_features Celery task
//   (core.tasks.build_features) to the celery-worker container.  This view is a pure trigger
//   surface — no export math lives here; all logic is in the US-31 task (Principle #2).
// created-by: dev-team
// sprint: sprint-11
// story: US-57 AC-57.1
// last-updated: 2026-06-18
// ---

import { useState } from 'react'

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
  statusError: {
    background: '#2a1a1a',
    border: '1px solid #ef5350',
    color: '#ef5350',
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
  row: {
    display: 'flex',
    gap: 32,
    flexWrap: 'wrap',
  },
}

export default function FeatureBuilder() {
  const [taskResult, setTaskResult] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  async function triggerExport() {
    setLoading(true)
    setError(null)
    setTaskResult(null)
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
      }
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

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

      {taskResult && (
        <div style={{ ...STYLES.status, ...STYLES.statusQueued }}>
          <div style={{ marginBottom: 8, fontWeight: 'bold' }}>Export queued</div>
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
            <div style={STYLES.field}>
              <span style={STYLES.label}>Status</span>
              <span style={STYLES.value}>{taskResult.status}</span>
            </div>
          </div>
          <div style={{ color: '#9598a1', fontSize: 12, marginTop: 4 }}>
            Task is running on the celery-worker container. Check worker logs for progress.
          </div>
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
