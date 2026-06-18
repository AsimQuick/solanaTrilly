// ---
// file: frontend/src/LivePositions.jsx
// stack: react+vite
// purpose: Live Positions dashboard board (PRD §13.2#1, US-69 AC-69.2).
//   Renders the shared US-64 Position rows for BOTH source=model and
//   source=copytrade (replay-sandbox + observe/paper).
//   Open positions: entry_price from Position row — NOT a live Birdeye call
//   (zero firehose, no unrealized PnL, no time-held).
//   Closed positions: exit_trigger + realized PnL.
//   Completes the P8 offline-gate clause 'sandbox Positions render in the
//   dashboard'.
// created-by: dev-team
// sprint: sprint-13
// story: US-69 AC-69.2
// last-updated: 2026-06-18
// ---

import { useState, useEffect } from 'react'

// H1 ImportError trap — fail fast if this module can't load
if (typeof useState !== 'function') {
  throw new Error('LivePositions: React hooks unavailable — check React import')
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
  marginBottom: '8px',
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

function shortMint(mint) {
  if (!mint) return '—'
  return mint.slice(0, 6) + '…' + mint.slice(-4)
}

function fmtTs(ts) {
  if (!ts) return '—'
  try {
    return new Date(ts).toISOString().replace('T', ' ').slice(0, 19) + ' UTC'
  } catch {
    return ts
  }
}

function pnlColor(pct) {
  if (pct == null) return STYLE.text
  return pct >= 0 ? STYLE.green : STYLE.red
}

function sourceChip(source) {
  const bg = source === 'model' ? '#1a3a2a' : '#1a1f3a'
  const color = source === 'model' ? STYLE.green : '#7986cb'
  return (
    <span style={{ background: bg, color, borderRadius: '4px', padding: '2px 6px', fontSize: '11px' }}>
      {source}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Open Positions table
// ---------------------------------------------------------------------------

function OpenPositionsTable({ rows }) {
  if (!rows || rows.length === 0) {
    return <p style={{ color: STYLE.textSecondary, fontSize: '13px' }}>No open positions.</p>
  }

  return (
    <table style={tableStyle}>
      <thead>
        <tr>
          <th style={thStyle}>Source</th>
          <th style={thStyle}>Mint</th>
          <th style={thStyle}>Mode</th>
          <th style={thStyle}>Status</th>
          <th style={thStyle}>Entry Time</th>
          <th style={thStyle}>Entry Price</th>
          <th style={thStyle}>Size (SOL)</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((pos) => (
          <tr key={pos.id}>
            <td style={tdStyle}>{sourceChip(pos.source)}</td>
            <td style={{ ...tdStyle, fontFamily: 'monospace', fontSize: '12px' }}>
              {shortMint(pos.mint)}
            </td>
            <td style={tdStyle}>{pos.mode}</td>
            <td style={tdStyle}>{pos.status}</td>
            <td style={{ ...tdStyle, fontSize: '12px' }}>{fmtTs(pos.entry_ts)}</td>
            <td style={tdStyle}>{pos.entry_price != null ? pos.entry_price.toExponential(4) : '—'}</td>
            <td style={tdStyle}>{pos.size_sol != null ? pos.size_sol.toFixed(4) : '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// ---------------------------------------------------------------------------
// Closed Positions table
// ---------------------------------------------------------------------------

function ClosedPositionsTable({ rows }) {
  if (!rows || rows.length === 0) {
    return <p style={{ color: STYLE.textSecondary, fontSize: '13px' }}>No closed positions.</p>
  }

  return (
    <table style={tableStyle}>
      <thead>
        <tr>
          <th style={thStyle}>Source</th>
          <th style={thStyle}>Mint</th>
          <th style={thStyle}>Mode</th>
          <th style={thStyle}>Entry Price</th>
          <th style={thStyle}>Exit Price</th>
          <th style={thStyle}>Trigger</th>
          <th style={thStyle}>PnL %</th>
          <th style={thStyle}>PnL SOL</th>
          <th style={thStyle}>Closed At</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((pos) => (
          <tr key={pos.id}>
            <td style={tdStyle}>{sourceChip(pos.source)}</td>
            <td style={{ ...tdStyle, fontFamily: 'monospace', fontSize: '12px' }}>
              {shortMint(pos.mint)}
            </td>
            <td style={tdStyle}>{pos.mode}</td>
            <td style={tdStyle}>{pos.entry_price != null ? pos.entry_price.toExponential(4) : '—'}</td>
            <td style={tdStyle}>{pos.exit_price != null ? pos.exit_price.toExponential(4) : '—'}</td>
            <td style={tdStyle}>{pos.exit_trigger || '—'}</td>
            <td style={{ ...tdStyle, color: pnlColor(pos.realized_pnl_pct) }}>
              {pos.realized_pnl_pct != null ? `${pos.realized_pnl_pct > 0 ? '+' : ''}${pos.realized_pnl_pct.toFixed(2)}%` : '—'}
            </td>
            <td style={{ ...tdStyle, color: pnlColor(pos.realized_pnl_sol) }}>
              {pos.realized_pnl_sol != null ? `${pos.realized_pnl_sol > 0 ? '+' : ''}${pos.realized_pnl_sol.toFixed(4)}` : '—'}
            </td>
            <td style={{ ...tdStyle, fontSize: '12px' }}>{fmtTs(pos.closed_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// ---------------------------------------------------------------------------
// Source filter toggle
// ---------------------------------------------------------------------------

function SourceFilter({ value, onChange }) {
  const opts = [
    { key: '', label: 'All' },
    { key: 'model', label: 'Model' },
    { key: 'copytrade', label: 'Copy Trade' },
  ]
  return (
    <div style={{ display: 'flex', gap: '8px', marginBottom: '12px' }}>
      <span style={{ ...labelStyle, alignSelf: 'center', marginBottom: 0 }}>Filter:</span>
      {opts.map((o) => (
        <button
          key={o.key}
          onClick={() => onChange(o.key)}
          style={{
            background: value === o.key ? '#2a3050' : 'transparent',
            border: STYLE.border,
            borderRadius: '4px',
            color: value === o.key ? STYLE.text : STYLE.textSecondary,
            padding: '4px 10px',
            fontSize: '12px',
            cursor: 'pointer',
          }}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function LivePositions() {
  const [openPositions, setOpenPositions] = useState([])
  const [closedPositions, setClosedPositions] = useState([])
  const [openSource, setOpenSource] = useState('')
  const [closedSource, setClosedSource] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  function buildUrl(base, source) {
    return source ? `${base}?source=${source}` : base
  }

  useEffect(() => {
    setLoading(true)
    setError(null)

    const openUrl = buildUrl('/api/trading/positions/open/', openSource)
    const closedUrl = buildUrl('/api/trading/positions/closed/', closedSource)

    Promise.all([
      fetch(openUrl).then((r) => r.json()),
      fetch(closedUrl).then((r) => r.json()),
    ])
      .then(([openData, closedData]) => {
        setOpenPositions(openData.positions || [])
        setClosedPositions(closedData.positions || [])
        setLoading(false)
      })
      .catch((err) => {
        setError(String(err))
        setLoading(false)
      })
  }, [openSource, closedSource])

  return (
    <div style={{ fontFamily: STYLE.font, color: STYLE.text }}>
      <h2 style={{ color: STYLE.text, marginBottom: '20px', fontSize: '18px' }}>
        Live Positions
      </h2>

      {loading && (
        <p style={{ color: STYLE.textSecondary }}>Loading positions…</p>
      )}

      {error && (
        <p style={{ color: STYLE.red }}>Error loading positions: {error}</p>
      )}

      {!loading && !error && (
        <>
          {/* Open Positions */}
          <div style={sectionStyle}>
            <div style={{ ...labelStyle, marginBottom: '12px' }}>
              Open Positions ({openPositions.length})
            </div>
            <SourceFilter value={openSource} onChange={setOpenSource} />
            <OpenPositionsTable rows={openPositions} />
          </div>

          {/* Closed Positions */}
          <div style={sectionStyle}>
            <div style={{ ...labelStyle, marginBottom: '12px' }}>
              Closed Positions ({closedPositions.length})
            </div>
            <SourceFilter value={closedSource} onChange={setClosedSource} />
            <ClosedPositionsTable rows={closedPositions} />
          </div>
        </>
      )}
    </div>
  )
}
