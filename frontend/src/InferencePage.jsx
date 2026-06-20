// ---
// file: frontend/src/InferencePage.jsx
// stack: react+vite
// purpose: Inference engine control page (operator-facing).
//   Polls GET /api/control/pipeline/ every 5 s for live status.
//   Renders a RUNNING/STOPPED status pill, a Start/Stop button that POSTs
//   to /api/control/inference/ {on: bool}, four stat tiles for counts, and a
//   trading_enabled / paper-mode note.  Handles fetch errors with an error
//   banner while continuing to poll.
// created-by: dev-team
// sprint: sprint-15
// story: US-inference-control
// status: implemented
// last-updated: 2026-06-20
// dependencies: none
// ---

import { useState, useEffect, useRef } from 'react'

// ---------------------------------------------------------------------------
// Design tokens — dark theme matching the rest of the dashboard
// ---------------------------------------------------------------------------

const BG          = '#0f1117'
const SURFACE     = '#1a1d27'
const BORDER_CLR  = '#242833'
const TEXT        = '#d1d4dc'
const TEXT_MUTED  = '#9598a1'
const ACCENT      = '#4f8bff'
const GREEN       = '#26a69a'
const RED         = '#ef5350'
const FONT        = 'sans-serif'

const sectionStyle = {
  background: SURFACE,
  border: `1px solid ${BORDER_CLR}`,
  borderRadius: '8px',
  padding: '20px',
  marginBottom: '20px',
  fontFamily: FONT,
}

const labelStyle = {
  color: TEXT_MUTED,
  fontSize: '11px',
  textTransform: 'uppercase',
  letterSpacing: '0.08em',
  marginBottom: '4px',
  fontFamily: FONT,
}

// ---------------------------------------------------------------------------
// StatusPill — big RUNNING / STOPPED indicator
// ---------------------------------------------------------------------------

function StatusPill({ inferenceOn }) {
  const running = Boolean(inferenceOn)
  return (
    <div
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '8px',
        background: running ? '#0d2b1e' : '#2a1a1a',
        border: `2px solid ${running ? GREEN : RED}`,
        borderRadius: '24px',
        padding: '8px 22px',
        fontFamily: FONT,
      }}
    >
      {/* pulsing dot */}
      <span
        style={{
          display: 'inline-block',
          width: '10px',
          height: '10px',
          borderRadius: '50%',
          background: running ? GREEN : RED,
          boxShadow: running ? `0 0 6px ${GREEN}` : 'none',
        }}
      />
      <span
        style={{
          color: running ? GREEN : RED,
          fontWeight: 700,
          fontSize: '18px',
          letterSpacing: '0.12em',
        }}
      >
        {running ? 'RUNNING' : 'STOPPED'}
      </span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// StatTile — small labelled count box
// ---------------------------------------------------------------------------

function StatTile({ label, value }) {
  return (
    <div
      style={{
        background: BG,
        border: `1px solid ${BORDER_CLR}`,
        borderRadius: '6px',
        padding: '14px 20px',
        minWidth: '140px',
        flex: '1 1 140px',
      }}
    >
      <div style={labelStyle}>{label}</div>
      <div
        style={{
          color: TEXT,
          fontSize: '26px',
          fontWeight: 700,
          fontFamily: FONT,
          lineHeight: 1.2,
        }}
      >
        {value ?? '—'}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// ErrorBanner
// ---------------------------------------------------------------------------

function ErrorBanner({ msg }) {
  if (!msg) return null
  return (
    <div
      style={{
        background: '#2a1a1a',
        border: `1px solid ${RED}`,
        borderRadius: '6px',
        padding: '10px 14px',
        color: RED,
        fontSize: '13px',
        fontFamily: FONT,
        marginBottom: '16px',
      }}
    >
      {msg}
    </div>
  )
}

// ---------------------------------------------------------------------------
// InferencePage
// ---------------------------------------------------------------------------

const POLL_INTERVAL_MS = 5000

export default function InferencePage() {
  const [status, setStatus]         = useState(null)   // pipeline status object
  const [fetchError, setFetchError] = useState(null)   // polling error message
  const [actionError, setActionError] = useState(null) // button POST error
  const [inFlight, setInFlight]     = useState(false)  // button disabled while posting

  const timerRef = useRef(null)

  // ---------------------------------------------------------------------------
  // Fetch status from GET /api/control/pipeline/
  // ---------------------------------------------------------------------------

  function fetchStatus() {
    fetch('/api/control/pipeline/')
      .then((r) => {
        if (!r.ok) return Promise.reject(`HTTP ${r.status}`)
        return r.json()
      })
      .then((data) => {
        setStatus(data)
        setFetchError(null)
      })
      .catch((err) => {
        setFetchError(`Status fetch failed: ${String(err)} — retrying every 5 s`)
        // keep the stale status visible; do not null it out
      })
  }

  // On mount: fetch immediately, then schedule polling
  useEffect(() => {
    fetchStatus()
    timerRef.current = setInterval(fetchStatus, POLL_INTERVAL_MS)
    return () => clearInterval(timerRef.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ---------------------------------------------------------------------------
  // Toggle inference via POST /api/control/inference/
  // ---------------------------------------------------------------------------

  function handleToggle() {
    if (inFlight || status == null) return
    const targetOn = !status.inference_on
    setInFlight(true)
    setActionError(null)

    fetch('/api/control/inference/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ on: targetOn }),
    })
      .then((r) => {
        if (!r.ok) return r.json().then((d) => Promise.reject(d.error || d.detail || `HTTP ${r.status}`))
        return r.json()
      })
      .then((data) => {
        setStatus(data)
        setFetchError(null)
      })
      .catch((err) => {
        setActionError(`Toggle failed: ${String(err)}`)
      })
      .finally(() => {
        setInFlight(false)
      })
  }

  // ---------------------------------------------------------------------------
  // Derived display values
  // ---------------------------------------------------------------------------

  const inferenceOn    = status?.inference_on ?? false
  const tradingEnabled = status?.trading_enabled ?? false
  const counts         = status?.counts ?? {}

  const btnLabel    = inFlight
    ? (inferenceOn ? 'Stopping…' : 'Starting…')
    : (inferenceOn ? 'Stop Inference' : 'Start Inference')

  const btnBg      = inFlight
    ? SURFACE
    : inferenceOn
      ? '#2a1a1a'
      : '#0d2b1e'

  const btnColor   = inFlight
    ? TEXT_MUTED
    : inferenceOn
      ? RED
      : GREEN

  const btnBorder  = inFlight
    ? `1px solid ${BORDER_CLR}`
    : inferenceOn
      ? `1px solid ${RED}`
      : `1px solid ${GREEN}`

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div style={{ fontFamily: FONT, color: TEXT, maxWidth: '860px' }}>
      <h2 style={{ color: TEXT, fontSize: '18px', fontWeight: 600, marginBottom: '20px' }}>
        Inference Engine
      </h2>

      {/* Polling error banner — shown even when status is stale */}
      <ErrorBanner msg={fetchError} />

      {/* ------------------------------------------------------------------ */}
      {/* SECTION 1 — Status pill + Start/Stop button                         */}
      {/* ------------------------------------------------------------------ */}
      <div style={sectionStyle}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '24px', flexWrap: 'wrap' }}>
          {/* Big status pill */}
          <StatusPill inferenceOn={inferenceOn} />

          {/* Primary action button */}
          <button
            onClick={handleToggle}
            disabled={inFlight || status == null}
            style={{
              background: btnBg,
              color: btnColor,
              border: btnBorder,
              borderRadius: '6px',
              padding: '10px 24px',
              fontSize: '15px',
              fontWeight: 700,
              fontFamily: FONT,
              cursor: inFlight || status == null ? 'not-allowed' : 'pointer',
              opacity: inFlight || status == null ? 0.7 : 1,
              transition: 'opacity 0.15s',
            }}
          >
            {btnLabel}
          </button>
        </div>

        {/* Action error inline below the button */}
        {actionError && (
          <div
            style={{
              marginTop: '12px',
              color: RED,
              fontSize: '13px',
              fontFamily: FONT,
            }}
          >
            {actionError}
          </div>
        )}

        {/* Firehose / pipeline flags (secondary chips) */}
        {status != null && (
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginTop: '16px' }}>
            {[
              { key: 'firehose_active', label: 'Firehose' },
              { key: 'scoring_enabled', label: 'Scoring' },
            ].map(({ key, label }) => {
              const active = Boolean(status[key])
              return (
                <span
                  key={key}
                  style={{
                    display: 'inline-block',
                    background: active ? '#0d2b1e' : '#1a1a1a',
                    color: active ? GREEN : TEXT_MUTED,
                    border: `1px solid ${active ? GREEN : BORDER_CLR}`,
                    borderRadius: '4px',
                    padding: '3px 10px',
                    fontSize: '12px',
                    fontWeight: 600,
                  }}
                >
                  {label}: {active ? 'ON' : 'OFF'}
                </span>
              )
            })}
          </div>
        )}
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* SECTION 2 — Count tiles                                             */}
      {/* ------------------------------------------------------------------ */}
      <div style={sectionStyle}>
        <div style={{ ...labelStyle, marginBottom: '14px' }}>Pipeline counts</div>
        <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
          <StatTile label="Tokens detected"         value={counts.tokens} />
          <StatTile label="Predictions recorded"    value={counts.predictions} />
          <StatTile label="Model positions open"    value={counts.model_positions_open} />
          <StatTile label="Model positions closed"  value={counts.model_positions_closed} />
        </div>
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* SECTION 3 — Trading gate note                                       */}
      {/* ------------------------------------------------------------------ */}
      <div
        style={{
          ...sectionStyle,
          display: 'flex',
          alignItems: 'center',
          gap: '14px',
          flexWrap: 'wrap',
        }}
      >
        <span
          style={{
            display: 'inline-block',
            background: tradingEnabled ? '#1a3a2a' : '#1a1d27',
            color: tradingEnabled ? GREEN : TEXT_MUTED,
            border: `1px solid ${tradingEnabled ? GREEN : BORDER_CLR}`,
            borderRadius: '4px',
            padding: '4px 12px',
            fontSize: '13px',
            fontWeight: 700,
            letterSpacing: '0.06em',
          }}
        >
          {tradingEnabled ? 'TRADING' : 'OBSERVE'}
        </span>
        <span style={{ color: TEXT_MUTED, fontSize: '13px', fontFamily: FONT }}>
          Paper&thinsp;/&thinsp;observe only &mdash; <code style={{ color: ACCENT, fontSize: '12px' }}>trading_enabled</code> is
          the separate capital gate.
        </span>
      </div>
    </div>
  )
}
