// ---
// file: frontend/src/ReplayViewer.jsx
// stack: react+vite+lightweight-charts
// purpose: Replay viewer position-open/close overlay (PRD §13.2#5, US-73 AC-73.2).
//   Renders a selected replay run_id's sandbox Positions on historical candles by
//   reusing the US-49 TokenDetail candle component for per-mint chart rendering and
//   overlaying entry/exit markers with exit_trigger labels from the AC-73.1 API
//   (/api/trading/replay/overlay/?run_id=<id>).  Reads only the isolated replay
//   sandbox + the existing recorded tape (Principle #2: no new candle math, no live
//   source, zero firehose).
// created-by: dev-team
// sprint: sprint-14
// story: US-73 AC-73.2
// last-updated: 2026-06-19
// ---

import { useState, useEffect } from 'react'
import TokenDetail from './TokenDetail.jsx'

// H1 ImportError trap — fail fast if this module can't load
if (typeof useState !== 'function') {
  throw new Error('ReplayViewer: React hooks unavailable — check React import')
}

// ---------------------------------------------------------------------------
// Style constants (dark theme matching dashboard)
// ---------------------------------------------------------------------------

const STYLE = {
  bg: '#0f1117',
  surface: '#1e2130',
  border: '1px solid #2a2d3e',
  text: '#d1d4dc',
  textSecondary: '#9598a1',
  green: '#26a69a',
  red: '#ef5350',
  yellow: '#ffd54f',
  font: 'sans-serif',
}

const sectionStyle = {
  background: STYLE.surface,
  border: STYLE.border,
  borderRadius: '6px',
  padding: '16px',
  marginBottom: '20px',
  fontFamily: STYLE.font,
}

const labelStyle = {
  color: STYLE.textSecondary,
  fontSize: '11px',
  textTransform: 'uppercase',
  letterSpacing: '0.08em',
  marginBottom: '12px',
}

const tableStyle = {
  width: '100%',
  borderCollapse: 'collapse',
  fontFamily: STYLE.font,
  fontSize: '13px',
}

const thStyle = {
  color: STYLE.textSecondary,
  textAlign: 'left',
  padding: '6px 10px',
  borderBottom: STYLE.border,
  fontSize: '11px',
  textTransform: 'uppercase',
  letterSpacing: '0.06em',
}

const tdStyle = {
  color: STYLE.text,
  padding: '6px 10px',
  borderBottom: '1px solid #1a1d2a',
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function pnlColor(val) {
  if (val == null) return STYLE.text
  return val >= 0 ? STYLE.green : STYLE.red
}

function pnlFmt(val) {
  if (val == null) return '—'
  const sign = val >= 0 ? '+' : ''
  return `${sign}${val.toFixed(2)}%`
}

function tsFmt(ts) {
  if (ts == null) return '—'
  try {
    return new Date(ts).toISOString().replace('T', ' ').replace('Z', 'Z')
  } catch (_) {
    return String(ts)
  }
}

// ---------------------------------------------------------------------------
// Position markers table — entry/exit markers + exit_trigger labels
// ---------------------------------------------------------------------------

function PositionMarkersTable({ positions }) {
  if (!positions || positions.length === 0) {
    return (
      <p style={{ color: STYLE.textSecondary, fontSize: '13px' }}>
        No positions for this run.
      </p>
    )
  }
  return (
    <table style={tableStyle}>
      <thead>
        <tr>
          <th style={thStyle}>Mint</th>
          <th style={thStyle}>Enterable</th>
          <th style={thStyle}>Entry Marker</th>
          <th style={thStyle}>Exit Marker</th>
          <th style={thStyle}>Exit Trigger</th>
          <th style={thStyle}>PnL %</th>
          <th style={thStyle}>Score</th>
        </tr>
      </thead>
      <tbody>
        {positions.map((pos) => (
          <tr key={pos.id}>
            <td style={{ ...tdStyle, fontFamily: 'monospace', fontSize: '11px' }}>
              {pos.mint}
            </td>
            <td style={tdStyle}>
              {pos.enterable ? (
                <span style={{ color: STYLE.green }}>Yes</span>
              ) : (
                <span style={{ color: STYLE.textSecondary }}>
                  No {pos.unentered_reason ? `(${pos.unentered_reason})` : ''}
                </span>
              )}
            </td>
            <td style={{ ...tdStyle, fontSize: '11px', fontFamily: 'monospace' }}>
              {/* entry_marker: entry_ts + entry_price */}
              {pos.entry_ts ? (
                <>
                  <span style={{ color: STYLE.green }}>▲ entry</span>
                  <br />
                  <span style={{ color: STYLE.textSecondary }}>
                    {tsFmt(pos.entry_ts)}
                  </span>
                  {pos.entry_price != null && (
                    <>
                      <br />
                      <span style={{ color: STYLE.text }}>
                        {pos.entry_price.toExponential(3)}
                      </span>
                    </>
                  )}
                </>
              ) : (
                <span style={{ color: STYLE.textSecondary }}>—</span>
              )}
            </td>
            <td style={{ ...tdStyle, fontSize: '11px', fontFamily: 'monospace' }}>
              {/* exit_marker: exit_ts + exit_price */}
              {pos.exit_ts ? (
                <>
                  <span style={{ color: STYLE.red }}>▼ exit</span>
                  <br />
                  <span style={{ color: STYLE.textSecondary }}>
                    {tsFmt(pos.exit_ts)}
                  </span>
                  {pos.exit_price != null && (
                    <>
                      <br />
                      <span style={{ color: STYLE.text }}>
                        {pos.exit_price.toExponential(3)}
                      </span>
                    </>
                  )}
                </>
              ) : (
                <span style={{ color: STYLE.textSecondary }}>—</span>
              )}
            </td>
            <td style={tdStyle}>
              {/* exit_trigger label */}
              {pos.exit_trigger ? (
                <span
                  style={{
                    color:
                      pos.exit_trigger === 'TAKE_PROFIT'
                        ? STYLE.green
                        : pos.exit_trigger === 'STOP_LOSS' || pos.exit_trigger === 'RUG_PULL'
                          ? STYLE.red
                          : STYLE.yellow,
                    fontFamily: 'monospace',
                    fontSize: '11px',
                  }}
                >
                  {pos.exit_trigger}
                </span>
              ) : (
                <span style={{ color: STYLE.textSecondary }}>—</span>
              )}
            </td>
            <td style={{ ...tdStyle, color: pnlColor(pos.realized_pnl_pct) }}>
              {pnlFmt(pos.realized_pnl_pct)}
            </td>
            <td style={{ ...tdStyle, fontFamily: 'monospace' }}>
              {pos.score != null ? pos.score.toFixed(4) : '—'}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// ---------------------------------------------------------------------------
// Per-mint candle + overlay section — reuses US-49 TokenDetail candle component
// ---------------------------------------------------------------------------

function MintReplaySection({ mint, positions }) {
  const mintPositions = positions.filter((p) => p.mint === mint && p.enterable)
  return (
    <div style={sectionStyle}>
      <div style={labelStyle}>
        Mint: <span style={{ fontFamily: 'monospace', color: STYLE.text }}>{mint}</span>
        {' '}— {mintPositions.length} position{mintPositions.length !== 1 ? 's' : ''}
      </div>

      {/* Reuse US-49 TokenDetail candle component for the historical candle chart */}
      <TokenDetail mint={mint} />

      {/* Position entry/exit overlay table for this mint */}
      {mintPositions.length > 0 && (
        <div style={{ marginTop: '12px' }}>
          <div style={{ ...labelStyle, marginBottom: '8px' }}>
            Replayed Positions — entry/exit markers + exit_trigger labels
          </div>
          <PositionMarkersTable positions={mintPositions} />
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main ReplayViewer component
// ---------------------------------------------------------------------------

export default function ReplayViewer({ runId: propRunId }) {
  const [runIdInput, setRunIdInput] = useState(propRunId || '')
  const [activeRunId, setActiveRunId] = useState(propRunId || '')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  // Fetch overlay data from the AC-73.1 API when activeRunId changes.
  useEffect(() => {
    if (!activeRunId) return
    setLoading(true)
    setError(null)
    setData(null)
    fetch(`/api/trading/replay/overlay/?run_id=${encodeURIComponent(activeRunId)}`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then((json) => {
        setData(json)
        setLoading(false)
      })
      .catch((err) => {
        setError(String(err))
        setLoading(false)
      })
  }, [activeRunId])

  // Deduplicated mint list from entered positions (preserves first-seen order).
  const enteredMints = data
    ? [...new Set(data.positions.filter((p) => p.enterable).map((p) => p.mint))]
    : []

  function handleLoad(e) {
    e.preventDefault()
    setActiveRunId(runIdInput.trim())
  }

  return (
    <div style={{ fontFamily: STYLE.font, color: STYLE.text }}>
      <h2 style={{ color: STYLE.text, marginBottom: '4px', fontSize: '18px' }}>
        Replay Viewer
      </h2>
      <p style={{ color: STYLE.textSecondary, fontSize: '12px', marginBottom: '20px' }}>
        Select a replay <code>run_id</code> to overlay sandbox position open/close markers
        on the recorded historical candles (PRD §13.2#5).
      </p>

      {/* run_id selector */}
      <form onSubmit={handleLoad} style={{ marginBottom: '20px', display: 'flex', gap: '8px' }}>
        <input
          type="text"
          value={runIdInput}
          onChange={(e) => setRunIdInput(e.target.value)}
          placeholder="Enter replay run_id…"
          style={{
            background: STYLE.surface,
            border: STYLE.border,
            borderRadius: '4px',
            color: STYLE.text,
            padding: '6px 10px',
            fontFamily: 'monospace',
            fontSize: '13px',
            width: '320px',
          }}
        />
        <button
          type="submit"
          style={{
            background: '#2563eb',
            border: 'none',
            borderRadius: '4px',
            color: '#fff',
            padding: '6px 16px',
            cursor: 'pointer',
            fontFamily: STYLE.font,
            fontSize: '13px',
          }}
        >
          Load
        </button>
      </form>

      {loading && (
        <p style={{ color: STYLE.textSecondary }}>Loading replay {activeRunId}…</p>
      )}

      {error && (
        <p style={{ color: STYLE.red }}>Error loading replay: {error}</p>
      )}

      {!loading && !error && data && (
        <>
          {/* Summary */}
          <div style={{ ...sectionStyle, marginBottom: '12px' }}>
            <div style={labelStyle}>Run Summary</div>
            <p style={{ color: STYLE.textSecondary, fontSize: '13px', margin: 0 }}>
              <strong style={{ color: STYLE.text }}>run_id:</strong>{' '}
              <code style={{ fontFamily: 'monospace' }}>{data.run_id}</code>
              &nbsp;&nbsp;
              <strong style={{ color: STYLE.text }}>total positions:</strong>{' '}
              {data.total_positions}
              &nbsp;&nbsp;
              <strong style={{ color: STYLE.text }}>entered:</strong>{' '}
              {data.positions.filter((p) => p.enterable).length}
            </p>
          </div>

          {/* All-positions table (entry/exit markers + exit_trigger labels) */}
          <div style={sectionStyle}>
            <div style={labelStyle}>
              All Positions — entry/exit markers + exit_trigger labels
            </div>
            <PositionMarkersTable positions={data.positions} />
          </div>

          {/* Per-mint candle sections — each reuses the US-49 TokenDetail component */}
          {enteredMints.length === 0 ? (
            <p style={{ color: STYLE.textSecondary, fontSize: '13px' }}>
              No entered positions for run_id <code>{data.run_id}</code>.
            </p>
          ) : (
            <>
              <div style={{ ...labelStyle, marginBottom: '12px' }}>
                Candle Charts — US-49 TokenDetail component with position overlay
              </div>
              {enteredMints.map((mint) => (
                <MintReplaySection key={mint} mint={mint} positions={data.positions} />
              ))}
            </>
          )}
        </>
      )}

      {!loading && !error && !data && activeRunId && (
        <p style={{ color: STYLE.textSecondary }}>No data returned for run_id: {activeRunId}</p>
      )}

      {!activeRunId && (
        <p style={{ color: STYLE.textSecondary, fontSize: '13px' }}>
          Enter a replay run_id above and click Load to view the position overlay.
        </p>
      )}
    </div>
  )
}
