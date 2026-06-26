// ---
// file: frontend/src/LivePositions.jsx
// stack: react+vite
// purpose: Live Positions dashboard board (PRD §13.2#1, US-69 AC-69.2).
//   US-90 AC-90.2: adds click-to-copy for mint addresses, paginated position
//   views (page/page_size query params to the API), and Dubai-time (UTC+4)
//   display for all timestamps.
//   Renders the shared US-64 Position rows for BOTH source=model and
//   source=copytrade (replay-sandbox + observe/paper).
//   Open positions: entry_price from Position row — NOT a live Birdeye call
//   (zero firehose, no unrealized PnL, no time-held).
//   Closed positions: exit_trigger + realized PnL.
// created-by: dev-team
// sprint: sprint-13, sprint-15
// story: US-69 AC-69.2, US-90 AC-90.2
// status: refactored
// last-updated: 2026-06-26
// dependencies: none
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
// Pagination constants (US-90 AC-90.2)
// ---------------------------------------------------------------------------

const DEFAULT_PAGE_SIZE = 25

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// US-90 AC-90.2: CopyableMint — displays first6…last4, copies full on click.
function CopyableMint({ mint }) {
  const [copied, setCopied] = useState(false)
  if (!mint) return <span style={{ color: STYLE.textSecondary }}>—</span>
  const short = mint.length > 12 ? `${mint.slice(0, 6)}…${mint.slice(-4)}` : mint

  function handleCopy() {
    if (typeof navigator !== 'undefined' && navigator.clipboard) {
      navigator.clipboard.writeText(mint).then(() => {
        setCopied(true)
        setTimeout(() => setCopied(false), 1500)
      }).catch(() => {
        // Fallback for environments without clipboard API
        _fallbackCopy(mint)
        setCopied(true)
        setTimeout(() => setCopied(false), 1500)
      })
    } else {
      _fallbackCopy(mint)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    }
  }

  return (
    <span
      onClick={handleCopy}
      title={`${mint} (click to copy)`}
      style={{
        cursor: 'pointer',
        color: copied ? STYLE.green : STYLE.text,
        fontFamily: 'monospace',
        fontSize: '12px',
        userSelect: 'none',
      }}
    >
      {copied ? 'Copied!' : short}
    </span>
  )
}

// Textarea-based clipboard fallback for contexts without navigator.clipboard
function _fallbackCopy(text) {
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
  } catch (_e) {
    // Clipboard not available — silent failure
  }
}

// US-90 AC-90.4: format timestamps as Dubai local time (UTC+4, Asia/Dubai).
// Falls back to ISO UTC if Intl.DateTimeFormat is unavailable.
const _DUBAI_TZ = 'Asia/Dubai'
const _DUBAI_FMT = (typeof Intl !== 'undefined')
  ? new Intl.DateTimeFormat('en-GB', {
      timeZone: _DUBAI_TZ,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    })
  : null

function fmtTs(ts) {
  if (!ts) return '—'
  try {
    const d = new Date(ts)
    if (_DUBAI_FMT) {
      // Format: "26/06/2026, 10:30:00" → "2026-06-26 10:30 GST"
      const parts = _DUBAI_FMT.formatToParts(d)
      const get = (type) => parts.find((p) => p.type === type)?.value ?? '00'
      return `${get('year')}-${get('month')}-${get('day')} ${get('hour')}:${get('minute')} GST`
    }
    return d.toISOString().replace('T', ' ').slice(0, 19) + ' UTC'
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
// US-90 AC-90.2: Pagination controls component
// ---------------------------------------------------------------------------

function PaginationControls({ page, totalPages, onPageChange, count, pageSize }) {
  if (totalPages <= 1) return null
  const start = (page - 1) * pageSize + 1
  const end = Math.min(page * pageSize, count)
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: '8px',
      marginTop: '10px',
      flexWrap: 'wrap',
    }}>
      <span style={{ color: STYLE.textSecondary, fontSize: '12px' }}>
        {start}–{end} of {count}
      </span>
      <button
        onClick={() => onPageChange(1)}
        disabled={page === 1}
        style={_pageBtnStyle(page === 1)}
        title="First page"
      >«</button>
      <button
        onClick={() => onPageChange(page - 1)}
        disabled={page === 1}
        style={_pageBtnStyle(page === 1)}
        title="Previous page"
      >‹</button>
      <span style={{ color: STYLE.text, fontSize: '12px' }}>
        {page} / {totalPages}
      </span>
      <button
        onClick={() => onPageChange(page + 1)}
        disabled={page >= totalPages}
        style={_pageBtnStyle(page >= totalPages)}
        title="Next page"
      >›</button>
      <button
        onClick={() => onPageChange(totalPages)}
        disabled={page >= totalPages}
        style={_pageBtnStyle(page >= totalPages)}
        title="Last page"
      >»</button>
    </div>
  )
}

function _pageBtnStyle(disabled) {
  return {
    background: disabled ? 'transparent' : '#2a3050',
    border: STYLE.border,
    borderRadius: '4px',
    color: disabled ? STYLE.textSecondary : STYLE.text,
    padding: '3px 8px',
    fontSize: '13px',
    cursor: disabled ? 'not-allowed' : 'pointer',
    opacity: disabled ? 0.5 : 1,
  }
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
          <th style={thStyle}>Entry Time (GST)</th>
          <th style={thStyle}>Entry Price</th>
          <th style={thStyle}>Size (SOL)</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((pos) => (
          <tr key={pos.id}>
            <td style={tdStyle}>{sourceChip(pos.source)}</td>
            <td style={{ ...tdStyle, fontFamily: 'monospace', fontSize: '12px' }}>
              <CopyableMint mint={pos.mint} />
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
          <th style={thStyle}>Closed At (GST)</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((pos) => (
          <tr key={pos.id}>
            <td style={tdStyle}>{sourceChip(pos.source)}</td>
            <td style={{ ...tdStyle, fontFamily: 'monospace', fontSize: '12px' }}>
              <CopyableMint mint={pos.mint} />
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

  // US-90 AC-90.2: pagination state for both open and closed tables
  const [openPage, setOpenPage] = useState(1)
  const [openTotalPages, setOpenTotalPages] = useState(1)
  const [openCount, setOpenCount] = useState(0)
  const [closedPage, setClosedPage] = useState(1)
  const [closedTotalPages, setClosedTotalPages] = useState(1)
  const [closedCount, setClosedCount] = useState(0)

  function buildUrl(base, source, page) {
    const params = new URLSearchParams()
    if (source) params.set('source', source)
    params.set('page', String(page))
    params.set('page_size', String(DEFAULT_PAGE_SIZE))
    return `${base}?${params.toString()}`
  }

  // Fetch open positions on source or page change
  useEffect(() => {
    setLoading(true)
    setError(null)
    const openUrl = buildUrl('/api/trading/positions/open/', openSource, openPage)
    fetch(openUrl)
      .then((r) => r.json())
      .then((data) => {
        setOpenPositions(data.positions || [])
        setOpenCount(data.count || 0)
        setOpenTotalPages(data.total_pages || 1)
        setLoading(false)
      })
      .catch((err) => {
        setError(String(err))
        setLoading(false)
      })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openSource, openPage])

  // Fetch closed positions on source or page change
  useEffect(() => {
    setLoading(true)
    setError(null)
    const closedUrl = buildUrl('/api/trading/positions/closed/', closedSource, closedPage)
    fetch(closedUrl)
      .then((r) => r.json())
      .then((data) => {
        setClosedPositions(data.positions || [])
        setClosedCount(data.count || 0)
        setClosedTotalPages(data.total_pages || 1)
        setLoading(false)
      })
      .catch((err) => {
        setError(String(err))
        setLoading(false)
      })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [closedSource, closedPage])

  // Reset to page 1 when source filter changes
  function handleOpenSourceChange(src) {
    setOpenSource(src)
    setOpenPage(1)
  }

  function handleClosedSourceChange(src) {
    setClosedSource(src)
    setClosedPage(1)
  }

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

      {!error && (
        <>
          {/* Open Positions */}
          <div style={sectionStyle}>
            <div style={{ ...labelStyle, marginBottom: '12px' }}>
              Open Positions ({openCount})
            </div>
            <SourceFilter value={openSource} onChange={handleOpenSourceChange} />
            <OpenPositionsTable rows={openPositions} />
            <PaginationControls
              page={openPage}
              totalPages={openTotalPages}
              onPageChange={setOpenPage}
              count={openCount}
              pageSize={DEFAULT_PAGE_SIZE}
            />
          </div>

          {/* Closed Positions */}
          <div style={sectionStyle}>
            <div style={{ ...labelStyle, marginBottom: '12px' }}>
              Closed Positions ({closedCount})
            </div>
            <SourceFilter value={closedSource} onChange={handleClosedSourceChange} />
            <ClosedPositionsTable rows={closedPositions} />
            <PaginationControls
              page={closedPage}
              totalPages={closedTotalPages}
              onPageChange={setClosedPage}
              count={closedCount}
              pageSize={DEFAULT_PAGE_SIZE}
            />
          </div>
        </>
      )}
    </div>
  )
}
