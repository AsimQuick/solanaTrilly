// ---
// file: frontend/src/CalibrationPnL.jsx
// stack: react+vite
// purpose: Calibration & PnL analytics dashboard view (PRD §13.2#4, US-72 AC-72.2).
//   Renders four analytics from the AC-72.1 API (/api/trading/analytics/calibration-pnl/):
//     1. Win Rate by Score Band — positions grouped into 0.1-wide score bands
//     2. Realized PnL by Exit Trigger — totals and averages grouped by exit_trigger
//     3. Score vs Actual PnL Scatter — (score, realized_pnl_pct) pairs as a data table
//     4. Calibration Curve — predicted score bucket mid-point vs actual win rate
//   No candle charts, no TA — consumes the AC-72.1 API only.
// created-by: dev-team
// sprint: sprint-14
// story: US-72 AC-72.2
// last-updated: 2026-06-19
// ---

import { useState, useEffect } from 'react'

// H1 ImportError trap — fail fast if this module can't load
if (typeof useState !== 'function') {
  throw new Error('CalibrationPnL: React hooks unavailable — check React import')
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

function pct(val) {
  if (val == null) return '—'
  const sign = val >= 0 ? '+' : ''
  return `${sign}${(val * 100).toFixed(1)}%`
}

function pnlPct(val) {
  if (val == null) return '—'
  const sign = val >= 0 ? '+' : ''
  return `${sign}${val.toFixed(2)}%`
}

function pnlColor(val) {
  if (val == null) return STYLE.text
  return val >= 0 ? STYLE.green : STYLE.red
}

function winRateColor(rate) {
  if (rate == null) return STYLE.text
  if (rate >= 0.5) return STYLE.green
  if (rate >= 0.35) return STYLE.yellow
  return STYLE.red
}

// ---------------------------------------------------------------------------
// 1. Win Rate by Score Band table
// ---------------------------------------------------------------------------

function WinRateByScoreBand({ rows }) {
  if (!rows || rows.length === 0) {
    return <p style={{ color: STYLE.textSecondary, fontSize: '13px' }}>No score band data.</p>
  }
  return (
    <table style={tableStyle}>
      <thead>
        <tr>
          <th style={thStyle}>Score Band</th>
          <th style={thStyle}>Count</th>
          <th style={thStyle}>Wins</th>
          <th style={thStyle}>Win Rate</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.band}>
            <td style={{ ...tdStyle, fontFamily: 'monospace' }}>{row.band}</td>
            <td style={tdStyle}>{row.count}</td>
            <td style={tdStyle}>{row.wins}</td>
            <td style={{ ...tdStyle, color: winRateColor(row.win_rate) }}>
              {row.win_rate != null ? pct(row.win_rate) : '—'}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// ---------------------------------------------------------------------------
// 2. Realized PnL by Exit Trigger table (pnl_by_exit_trigger)
// ---------------------------------------------------------------------------

function PnLByExitTrigger({ rows }) {
  if (!rows || rows.length === 0) {
    return <p style={{ color: STYLE.textSecondary, fontSize: '13px' }}>No exit trigger data.</p>
  }
  return (
    <table style={tableStyle}>
      <thead>
        <tr>
          <th style={thStyle}>Exit Trigger</th>
          <th style={thStyle}>Count</th>
          <th style={thStyle}>Total PnL %</th>
          <th style={thStyle}>Avg PnL %</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.exit_trigger}>
            <td style={tdStyle}>{row.exit_trigger || '—'}</td>
            <td style={tdStyle}>{row.count}</td>
            <td style={{ ...tdStyle, color: pnlColor(row.total_pnl_pct) }}>
              {pnlPct(row.total_pnl_pct)}
            </td>
            <td style={{ ...tdStyle, color: pnlColor(row.avg_pnl_pct) }}>
              {pnlPct(row.avg_pnl_pct)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// ---------------------------------------------------------------------------
// 3. Score vs Actual PnL scatter table (scatter_points)
// ---------------------------------------------------------------------------

function ScatterTable({ rows }) {
  if (!rows || rows.length === 0) {
    return <p style={{ color: STYLE.textSecondary, fontSize: '13px' }}>No scatter data.</p>
  }
  // Limit to first 200 rows to keep the table usable
  const displayed = rows.slice(0, 200)
  return (
    <>
      {rows.length > 200 && (
        <p style={{ color: STYLE.textSecondary, fontSize: '11px', marginBottom: '8px' }}>
          Showing first 200 of {rows.length} scatter_points.
        </p>
      )}
      <table style={tableStyle}>
        <thead>
          <tr>
            <th style={thStyle}>#</th>
            <th style={thStyle}>Score</th>
            <th style={thStyle}>Realized PnL %</th>
          </tr>
        </thead>
        <tbody>
          {displayed.map((pt, idx) => (
            <tr key={idx}>
              <td style={{ ...tdStyle, color: STYLE.textSecondary, fontSize: '11px' }}>{idx + 1}</td>
              <td style={{ ...tdStyle, fontFamily: 'monospace' }}>
                {pt.score != null ? pt.score.toFixed(4) : '—'}
              </td>
              <td style={{ ...tdStyle, color: pnlColor(pt.realized_pnl_pct) }}>
                {pnlPct(pt.realized_pnl_pct)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}

// ---------------------------------------------------------------------------
// 4. Calibration Curve table (calibration_curve)
// ---------------------------------------------------------------------------

function CalibrationCurveTable({ rows }) {
  if (!rows || rows.length === 0) {
    return <p style={{ color: STYLE.textSecondary, fontSize: '13px' }}>No calibration curve data.</p>
  }
  return (
    <table style={tableStyle}>
      <thead>
        <tr>
          <th style={thStyle}>Bucket</th>
          <th style={thStyle}>Bucket Mid</th>
          <th style={thStyle}>Count</th>
          <th style={thStyle}>Wins</th>
          <th style={thStyle}>Actual Win Rate</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.bucket}>
            <td style={{ ...tdStyle, fontFamily: 'monospace' }}>{row.bucket}</td>
            <td style={{ ...tdStyle, fontFamily: 'monospace' }}>
              {row.bucket_mid != null ? row.bucket_mid.toFixed(2) : '—'}
            </td>
            <td style={tdStyle}>{row.count}</td>
            <td style={tdStyle}>{row.wins}</td>
            <td style={{ ...tdStyle, color: winRateColor(row.actual_win_rate) }}>
              {row.actual_win_rate != null ? pct(row.actual_win_rate) : '—'}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function CalibrationPnL() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    fetch('/api/trading/analytics/calibration-pnl/')
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
  }, [])

  return (
    <div style={{ fontFamily: STYLE.font, color: STYLE.text }}>
      <h2 style={{ color: STYLE.text, marginBottom: '4px', fontSize: '18px' }}>
        Calibration &amp; PnL Analytics
      </h2>
      {data && (
        <p style={{ color: STYLE.textSecondary, fontSize: '12px', marginBottom: '20px' }}>
          {data.total_closed} closed position{data.total_closed !== 1 ? 's' : ''}
        </p>
      )}

      {loading && (
        <p style={{ color: STYLE.textSecondary }}>Loading analytics…</p>
      )}

      {error && (
        <p style={{ color: STYLE.red }}>Error loading analytics: {error}</p>
      )}

      {!loading && !error && data && (
        <>
          {/* 1. Win Rate by Score Band */}
          <div style={sectionStyle}>
            <div style={labelStyle}>Win Rate by Score Band</div>
            <WinRateByScoreBand rows={data.win_rate_by_score_band} />
          </div>

          {/* 2. PnL by Exit Trigger */}
          <div style={sectionStyle}>
            <div style={labelStyle}>Realized PnL by Exit Trigger</div>
            <PnLByExitTrigger rows={data.pnl_by_exit_trigger} />
          </div>

          {/* 3. Score vs Actual PnL Scatter */}
          <div style={sectionStyle}>
            <div style={labelStyle}>Score vs Actual PnL Scatter (scatter_points)</div>
            <ScatterTable rows={data.scatter_points} />
          </div>

          {/* 4. Calibration Curve */}
          <div style={sectionStyle}>
            <div style={labelStyle}>Calibration Curve (bucket_mid vs actual win rate)</div>
            <CalibrationCurveTable rows={data.calibration_curve} />
          </div>
        </>
      )}
    </div>
  )
}
