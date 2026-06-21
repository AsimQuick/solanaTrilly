// ---
// file: frontend/src/CopyTradeTab.jsx
// stack: react+vite
// purpose: Copy Trade dashboard tab (SPEC §8, US-63 AC-63.2) + click-to-download
//   export (SPEC §11, US-63 AC-63.3).
//   Renders the five §8 sections: (1) top bar — Upload JSON (with §6 wipe confirm),
//   ON/OFF master toggle, Observe/Live mode toggle (Live is P8-gated INERT), editable
//   SOL size / TP% / SL%, Download Export button (AC-63.3), status chips (engine state,
//   mode, # wallets, # open positions, cohort_id, created_at); (2) per-wallet PnL table
//   (address short/copyable, #trades, win-rate, total realized PnL SOL & %, avg hold,
//   last-trigger; SORTABLE by PnL and other columns); (3) open positions table;
//   (4) recent trades log; (5) cohort summary.
//   Honors §1/§8 NON-goals: NO candle charts/TA, NO per-row action buttons, NO
//   per-wallet toggles. Data from /api/copytrade/* endpoints (US-63 AC-63.1).
//   Export dispatched via /api/copytrade/export/trigger/ to celery-worker (NEVER
//   web/gunicorn, #289); result polled via existing §6.5 /api/export/result/<task_id>/.
//
//   EPIC-copy-paper-fill-repricing additions:
//   - Net-PnL cell shows "(curve-sim — not yet repriced)" annotation in yellow
//     when pnl_is_repriced=false (summary field from API).
//   - "N signals skipped (slippage)" chip shown when n_rejected>0.
//   - No FE PnL math change — reads API values as before.
// created-by: dev-team
// sprint: sprint-12, epic/copy-paper-fill-repricing
// story: US-63 AC-63.2, AC-63.3, EPIC-copy-paper-fill-repricing
// last-updated: 2026-06-21
// ---

import { useState, useEffect, useRef } from 'react'

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
  codeBg: '#161924',
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
  marginBottom: '4px',
}

const chipStyle = (active, color) => ({
  display: 'inline-block',
  background: active ? '#1a3330' : '#2a1a1a',
  color: color || (active ? STYLE.green : STYLE.red),
  border: `1px solid ${color || (active ? STYLE.green : STYLE.red)}`,
  borderRadius: '4px',
  padding: '2px 8px',
  fontSize: '12px',
  fontWeight: 600,
  marginRight: '6px',
})

const neutralChipStyle = {
  display: 'inline-block',
  background: '#1e2840',
  color: '#7b8ec8',
  border: '1px solid #2a3d6e',
  borderRadius: '4px',
  padding: '2px 8px',
  fontSize: '12px',
  fontWeight: 500,
  marginRight: '6px',
}

const btnStyle = (variant = 'primary', disabled = false) => ({
  background: disabled ? '#1a1d2e' : (variant === 'primary' ? '#1a3330' : variant === 'danger' ? '#2a1a1a' : '#2a2d3e'),
  color: disabled ? STYLE.textSecondary : (variant === 'primary' ? STYLE.green : variant === 'danger' ? STYLE.red : STYLE.text),
  border: `1px solid ${disabled ? '#2a2d3e' : (variant === 'primary' ? STYLE.green : variant === 'danger' ? STYLE.red : '#3a3d4e')}`,
  borderRadius: '4px',
  padding: '6px 14px',
  fontSize: '13px',
  cursor: disabled ? 'not-allowed' : 'pointer',
  fontFamily: STYLE.font,
  marginRight: '8px',
  opacity: disabled ? 0.6 : 1,
})

const inputStyle = {
  background: STYLE.codeBg,
  border: STYLE.border,
  borderRadius: '4px',
  color: STYLE.text,
  padding: '5px 10px',
  fontSize: '13px',
  fontFamily: STYLE.font,
  width: '72px',
}

const thStyle = (sortable, active) => ({
  textAlign: 'left',
  color: active ? STYLE.green : STYLE.textSecondary,
  fontSize: '11px',
  textTransform: 'uppercase',
  letterSpacing: '0.07em',
  paddingBottom: '8px',
  paddingRight: '12px',
  borderBottom: '1px solid #2a2d3e',
  cursor: sortable ? 'pointer' : 'default',
  userSelect: 'none',
  whiteSpace: 'nowrap',
})

const tdStyle = {
  padding: '7px 12px 7px 0',
  fontSize: '13px',
  borderBottom: '1px solid #1a1d2e',
  verticalAlign: 'middle',
}

function ErrorBox({ msg }) {
  if (!msg) return null
  return (
    <div style={{ background: '#2a1a1a', border: `1px solid ${STYLE.red}`, borderRadius: '4px', padding: '8px 12px', color: STYLE.red, fontSize: '13px', marginTop: '8px' }}>
      {msg}
    </div>
  )
}

function InfoBox({ msg }) {
  if (!msg) return null
  return (
    <div style={{ background: '#1a3330', border: `1px solid ${STYLE.green}`, borderRadius: '4px', padding: '8px 12px', color: STYLE.green, fontSize: '13px', marginTop: '8px' }}>
      {msg}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Short address helper — displays first6…last4, copies full on click
// ---------------------------------------------------------------------------

function ShortAddress({ address }) {
  const [copied, setCopied] = useState(false)
  if (!address) return <span style={{ color: STYLE.textSecondary }}>—</span>
  const short = address.length > 12 ? `${address.slice(0, 6)}…${address.slice(-4)}` : address

  function handleCopy() {
    navigator.clipboard.writeText(address).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  return (
    <span
      onClick={handleCopy}
      title={`${address} (click to copy)`}
      style={{ cursor: 'pointer', color: copied ? STYLE.green : STYLE.text, fontFamily: 'monospace', fontSize: '12px' }}
    >
      {copied ? 'Copied!' : short}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Format helpers
// ---------------------------------------------------------------------------

function fmtPnl(val) {
  if (val === null || val === undefined) return '—'
  const n = parseFloat(val)
  const color = n > 0 ? STYLE.green : n < 0 ? STYLE.red : STYLE.text
  return <span style={{ color }}>{n >= 0 ? '+' : ''}{n.toFixed(4)} SOL</span>
}

function fmtPct(val) {
  if (val === null || val === undefined) return '—'
  const n = parseFloat(val)
  const color = n > 0 ? STYLE.green : n < 0 ? STYLE.red : STYLE.text
  return <span style={{ color }}>{n >= 0 ? '+' : ''}{n.toFixed(1)}%</span>
}

function fmtWinRate(val) {
  if (val === null || val === undefined) return '—'
  return `${(parseFloat(val) * 100).toFixed(0)}%`
}

function fmtHold(seconds) {
  if (seconds === null || seconds === undefined) return '—'
  const s = parseFloat(seconds)
  if (s < 60) return `${s.toFixed(0)}s`
  if (s < 3600) return `${(s / 60).toFixed(1)}m`
  return `${(s / 3600).toFixed(1)}h`
}

function fmtTs(ts) {
  if (!ts) return '—'
  try { return new Date(ts).toLocaleString() } catch { return ts }
}

function fmtTsShort(ts) {
  if (!ts) return '—'
  try { return new Date(ts).toLocaleTimeString() } catch { return ts }
}

// ---------------------------------------------------------------------------
// SortIcon — small arrow for table header
// ---------------------------------------------------------------------------

function SortIcon({ active, dir }) {
  if (!active) return <span style={{ color: '#3a3d4e', marginLeft: '4px' }}>↕</span>
  return <span style={{ color: STYLE.green, marginLeft: '4px' }}>{dir === 'asc' ? '↑' : '↓'}</span>
}

// ---------------------------------------------------------------------------
// Section 2 — Per-wallet PnL table (sortable)
// ---------------------------------------------------------------------------

function PnlTable({ wallets }) {
  const [sortCol, setSortCol] = useState('total_pnl_sol')
  const [sortDir, setSortDir] = useState('desc')

  function handleSort(col) {
    if (sortCol === col) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortCol(col)
      setSortDir('desc')
    }
  }

  const sortedWallets = [...(wallets || [])].sort((a, b) => {
    const aVal = a[sortCol] ?? 0
    const bVal = b[sortCol] ?? 0
    const dir = sortDir === 'asc' ? 1 : -1
    if (typeof aVal === 'string') return aVal.localeCompare(bVal) * dir
    return (aVal - bVal) * dir
  })

  const PNL_COLUMNS = [
    { key: 'address', label: 'Wallet', sortable: true },
    { key: 'n_trades', label: '#Trades', sortable: true },
    { key: 'win_rate', label: 'Win Rate', sortable: true },
    { key: 'total_pnl_sol', label: 'Total PnL (SOL)', sortable: true },
    { key: 'total_pnl_pct', label: 'Total PnL %', sortable: false },
    { key: 'avg_hold_s', label: 'Avg Hold', sortable: true },
    { key: 'last_trigger', label: 'Last Trigger', sortable: false },
  ]

  return (
    <div>
      <div style={{ ...labelStyle, marginBottom: '10px' }}>Per-Wallet PnL (click column header to sort)</div>
      {sortedWallets.length === 0 ? (
        <p style={{ color: STYLE.textSecondary, fontSize: '13px', margin: 0 }}>No wallet PnL data yet.</p>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
            <thead>
              <tr>
                {PNL_COLUMNS.map((col) => (
                  <th
                    key={col.key}
                    style={thStyle(col.sortable, sortCol === col.key)}
                    onClick={col.sortable ? () => handleSort(col.key) : undefined}
                  >
                    {col.label}
                    {col.sortable && <SortIcon active={sortCol === col.key} dir={sortDir} />}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sortedWallets.map((w, i) => (
                <tr key={w.address || i}>
                  <td style={tdStyle}><ShortAddress address={w.address} /></td>
                  <td style={{ ...tdStyle, color: STYLE.text }}>{w.n_trades ?? '—'}</td>
                  <td style={{ ...tdStyle, color: STYLE.text }}>{fmtWinRate(w.win_rate)}</td>
                  <td style={tdStyle}>{fmtPnl(w.total_pnl_sol)}</td>
                  <td style={tdStyle}>{fmtPct(w.total_pnl_pct)}</td>
                  <td style={{ ...tdStyle, color: STYLE.text }}>{fmtHold(w.avg_hold_s)}</td>
                  <td style={{ ...tdStyle, color: STYLE.textSecondary, fontSize: '12px' }}>{fmtTsShort(w.last_trigger)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Section 3 — Open positions table
// ---------------------------------------------------------------------------

function OpenPositionsTable({ positions }) {
  return (
    <div>
      <div style={{ ...labelStyle, marginBottom: '10px' }}>Open Positions</div>
      {!positions || positions.length === 0 ? (
        <p style={{ color: STYLE.textSecondary, fontSize: '13px', margin: 0 }}>No open positions.</p>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
            <thead>
              <tr>
                {['Mint', 'Trigger Wallet', 'Mode', 'Entry Time', 'Entry Price', 'SOL In'].map((h) => (
                  <th key={h} style={thStyle(false, false)}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {positions.map((pos) => (
                <tr key={pos.id}>
                  <td style={tdStyle}><ShortAddress address={pos.mint} /></td>
                  <td style={tdStyle}><ShortAddress address={pos.trigger_wallet} /></td>
                  <td style={tdStyle}>
                    <span style={neutralChipStyle}>{pos.mode}</span>
                  </td>
                  <td style={{ ...tdStyle, color: STYLE.textSecondary, fontSize: '12px' }}>{fmtTs(pos.entry_ts)}</td>
                  <td style={{ ...tdStyle, color: STYLE.text }}>{pos.entry_price ? parseFloat(pos.entry_price).toFixed(8) : '—'}</td>
                  <td style={{ ...tdStyle, color: STYLE.text }}>{pos.sol_in ? parseFloat(pos.sol_in).toFixed(4) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Section 4 — Recent trades log
// ---------------------------------------------------------------------------

const EXIT_REASON_COLOR = { TP: STYLE.green, SL: STYLE.red, CURVE: STYLE.yellow, TIMER: '#9598a1', SETTLE: '#7b8ec8' }

function RecentTradesLog({ trades }) {
  return (
    <div>
      <div style={{ ...labelStyle, marginBottom: '10px' }}>Recent Trades (last 50)</div>
      {!trades || trades.length === 0 ? (
        <p style={{ color: STYLE.textSecondary, fontSize: '13px', margin: 0 }}>No closed trades yet.</p>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
            <thead>
              <tr>
                {['Mint', 'Wallet', 'Mode', 'Entry', 'Exit', 'PnL SOL', 'PnL %', 'Exit Reason'].map((h) => (
                  <th key={h} style={thStyle(false, false)}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {trades.map((t) => (
                <tr key={t.id}>
                  <td style={tdStyle}><ShortAddress address={t.mint} /></td>
                  <td style={tdStyle}><ShortAddress address={t.trigger_wallet} /></td>
                  <td style={tdStyle}><span style={neutralChipStyle}>{t.mode}</span></td>
                  <td style={{ ...tdStyle, color: STYLE.textSecondary, fontSize: '12px' }}>{fmtTsShort(t.entry_ts)}</td>
                  <td style={{ ...tdStyle, color: STYLE.textSecondary, fontSize: '12px' }}>{fmtTsShort(t.exit_ts)}</td>
                  <td style={tdStyle}>{fmtPnl(t.realized_pnl_sol)}</td>
                  <td style={tdStyle}>{fmtPct(t.realized_pnl_pct)}</td>
                  <td style={tdStyle}>
                    <span style={{ color: EXIT_REASON_COLOR[t.exit_reason] || STYLE.text, fontWeight: 600 }}>
                      {t.exit_reason || '—'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Section 5 — Cohort summary
// ---------------------------------------------------------------------------

function CohortSummary({ summary }) {
  if (!summary) return null

  // EPIC-copy-paper-fill-repricing: trustworthiness annotation
  const pnlIsRepriced = summary.pnl_is_repriced === true
  const nRejected = summary.n_rejected ?? 0

  return (
    <div>
      <div style={{ display: 'flex', gap: '32px', flexWrap: 'wrap' }}>
        <div>
          <div style={labelStyle}>Total Trades</div>
          <div style={{ color: STYLE.text, fontSize: '20px', fontWeight: 600 }}>{summary.total_trades ?? 0}</div>
        </div>
        <div>
          <div style={labelStyle}>Overall Win Rate</div>
          <div style={{ color: STYLE.text, fontSize: '20px', fontWeight: 600 }}>{fmtWinRate(summary.win_rate)}</div>
        </div>
        <div>
          <div style={labelStyle}>Net PnL</div>
          <div style={{ fontSize: '20px', fontWeight: 600 }}>{fmtPnl(summary.net_pnl_sol)}</div>
          {/* EPIC-copy-paper-fill-repricing: warn when PnL is still curve-sim */}
          {!pnlIsRepriced && (
            <div style={{ color: STYLE.yellow, fontSize: '11px', marginTop: '3px', fontStyle: 'italic' }}>
              (curve-sim — not yet repriced)
            </div>
          )}
        </div>
        <div>
          <div style={labelStyle}>Since</div>
          <div style={{ color: STYLE.textSecondary, fontSize: '14px' }}>{summary.since ? new Date(summary.since).toLocaleString() : '—'}</div>
        </div>
      </div>

      {/* EPIC-copy-paper-fill-repricing: "N signals skipped (slippage)" chip */}
      {nRejected > 0 && (
        <div style={{ marginTop: '12px' }}>
          <span style={{
            display: 'inline-block',
            background: '#2a1e10',
            color: STYLE.yellow,
            border: `1px solid ${STYLE.yellow}`,
            borderRadius: '4px',
            padding: '3px 10px',
            fontSize: '12px',
            fontWeight: 600,
          }}>
            {nRejected} signal{nRejected !== 1 ? 's' : ''} skipped (slippage)
          </span>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// CopyTradeTab — main component
// ---------------------------------------------------------------------------

function CopyTradeTab() {
  // Summary / status bar state (also used for top-bar chips)
  const [summary, setSummary] = useState(null)
  const [summaryError, setSummaryError] = useState(null)

  // Per-wallet PnL
  const [pnl, setPnl] = useState({ cohort_id: null, wallets: [] })
  const [pnlError, setPnlError] = useState(null)

  // Open positions
  const [positions, setPositions] = useState([])
  const [positionsError, setPositionsError] = useState(null)

  // Recent trades
  const [trades, setTrades] = useState([])
  const [tradesError, setTradesError] = useState(null)

  // Action feedback
  const [actionMsg, setActionMsg] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [actionLoading, setActionLoading] = useState(false)

  // Editable overrides (local state; persisted on blur/submit)
  const [solSize, setSolSize] = useState('')
  const [tpPct, setTpPct] = useState('')
  const [slPct, setSlPct] = useState('')
  const [overridesMsg, setOverridesMsg] = useState(null)
  const [overridesError, setOverridesError] = useState(null)

  // Upload
  const fileInputRef = useRef(null)

  // Export (AC-63.3) — click-to-download via §6.5 celery export channel
  const [exportTaskId, setExportTaskId] = useState(null)
  const [exportStatus, setExportStatus] = useState(null)
  const [exportMsg, setExportMsg] = useState(null)
  const [exportError, setExportError] = useState(null)
  const [exportLoading, setExportLoading] = useState(false)

  // ---------------------------------------------------------------------------
  // Data fetch — called on mount and after every successful action
  // ---------------------------------------------------------------------------

  const [refreshKey, setRefreshKey] = useState(0)

  function refresh() { setRefreshKey((k) => k + 1) }

  useEffect(() => {
    fetch('/api/copytrade/summary/')
      .then((r) => (r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`)))
      .then((data) => {
        setSummary(data)
        // Seed editable fields from live settings if not yet touched
        setSolSize((prev) => prev === '' && data.sol_size_per_trade ? String(data.sol_size_per_trade) : prev)
        setTpPct((prev) => prev === '' && data.take_profit_pct ? String(data.take_profit_pct) : prev)
        setSlPct((prev) => prev === '' && data.stop_loss_pct ? String(data.stop_loss_pct) : prev)
        setSummaryError(null)
      })
      .catch((err) => setSummaryError(String(err)))
  }, [refreshKey])

  useEffect(() => {
    fetch('/api/copytrade/pnl/')
      .then((r) => (r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`)))
      .then((data) => { setPnl(data); setPnlError(null) })
      .catch((err) => setPnlError(String(err)))
  }, [refreshKey])

  useEffect(() => {
    fetch('/api/copytrade/positions/')
      .then((r) => (r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`)))
      .then((data) => { setPositions(data.positions || []); setPositionsError(null) })
      .catch((err) => setPositionsError(String(err)))
  }, [refreshKey])

  useEffect(() => {
    fetch('/api/copytrade/trades/?limit=50')
      .then((r) => (r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`)))
      .then((data) => { setTrades(data.trades || []); setTradesError(null) })
      .catch((err) => setTradesError(String(err)))
  }, [refreshKey])

  // ---------------------------------------------------------------------------
  // Handlers
  // ---------------------------------------------------------------------------

  function handleEngineToggle() {
    if (!summary) return
    const newVal = !summary.engine_on
    setActionMsg(null)
    setActionError(null)
    setActionLoading(true)
    fetch('/api/copytrade/engine/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ engine_on: newVal }),
    })
      .then((r) => (r.ok ? r.json() : r.json().then((d) => Promise.reject(d.error || `HTTP ${r.status}`))))
      .then((data) => {
        setActionMsg(`Engine turned ${data.engine_on ? 'ON' : 'OFF'}.`)
        refresh()
      })
      .catch((err) => setActionError(String(err)))
      .finally(() => setActionLoading(false))
  }

  function handleModeToggle() {
    if (!summary) return
    const newMode = summary.mode === 'observe' ? 'live' : 'observe'
    setActionMsg(null)
    setActionError(null)
    setActionLoading(true)
    fetch('/api/copytrade/mode/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: newMode }),
    })
      .then((r) => r.json().then((d) => ({ ok: r.ok, data: d })))
      .then(({ ok, data }) => {
        if (!ok) {
          setActionError(data.error || `HTTP error`)
        } else {
          setActionMsg(`Mode set to ${data.mode}.`)
          refresh()
        }
      })
      .catch((err) => setActionError(String(err)))
      .finally(() => setActionLoading(false))
  }

  function handleOverrides(e) {
    e.preventDefault()
    setOverridesMsg(null)
    setOverridesError(null)
    const payload = {}
    if (solSize !== '') payload.sol_size_per_trade = parseFloat(solSize)
    if (tpPct !== '') payload.take_profit_pct = parseFloat(tpPct)
    if (slPct !== '') payload.stop_loss_pct = parseFloat(slPct)
    if (Object.keys(payload).length === 0) return
    fetch('/api/copytrade/overrides/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
      .then((r) => (r.ok ? r.json() : r.json().then((d) => Promise.reject(d.error || `HTTP ${r.status}`))))
      .then((data) => {
        setOverridesMsg(`Saved: SOL=${data.sol_size_per_trade} TP=${data.take_profit_pct}% SL=${data.stop_loss_pct}%`)
        refresh()
      })
      .catch((err) => setOverridesError(String(err)))
  }

  function handleUploadClick() {
    const confirmed = window.confirm(
      'Upload a new cohort JSON?\n\n' +
      'WARNING (§6 wipe): this will settle and PURGE all current copytrade positions, ' +
      'wallets, and PnL records for the active cohort. The engine will be stopped. ' +
      'This cannot be undone.\n\nProceed?'
    )
    if (confirmed && fileInputRef.current) {
      fileInputRef.current.value = ''
      fileInputRef.current.click()
    }
  }

  // ---------------------------------------------------------------------------
  // Export handler (AC-63.3) — POST trigger → poll §6.5 result endpoint
  // ---------------------------------------------------------------------------

  function handleExport() {
    setExportMsg(null)
    setExportError(null)
    setExportLoading(true)
    setExportTaskId(null)
    setExportStatus(null)
    fetch('/api/copytrade/export/trigger/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    })
      .then((r) => (r.ok ? r.json() : r.json().then((d) => Promise.reject(d.error || `HTTP ${r.status}`))))
      .then((data) => {
        setExportTaskId(data.task_id)
        setExportStatus('queued')
        setExportMsg(`Export queued (task ${data.task_id.slice(0, 8)}…). Polling for result…`)
        _pollExportResult(data.task_id, 0)
      })
      .catch((err) => {
        setExportError(String(err))
        setExportLoading(false)
      })
  }

  function _pollExportResult(taskId, attempt) {
    const MAX_ATTEMPTS = 20
    const POLL_MS = 2000
    if (attempt >= MAX_ATTEMPTS) {
      setExportError('Export timed out — check the celery-worker logs.')
      setExportLoading(false)
      return
    }
    setTimeout(() => {
      fetch(`/api/export/result/${taskId}/`)
        .then((r) => (r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`)))
        .then((data) => {
          setExportStatus(data.status)
          if (data.status === 'complete') {
            const rows = data.row_count != null ? ` (${data.row_count} rows)` : ''
            const path = data.manifest && data.manifest.dataset_id ? ` — dataset: ${data.manifest.dataset_id}` : ''
            setExportMsg(`Export complete${rows}${path}`)
            setExportLoading(false)
          } else if (data.status === 'failed') {
            setExportError(`Export failed: ${data.error || 'unknown error'}`)
            setExportLoading(false)
          } else {
            setExportMsg(`Export ${data.status}… (attempt ${attempt + 1}/${MAX_ATTEMPTS})`)
            _pollExportResult(taskId, attempt + 1)
          }
        })
        .catch((err) => {
          setExportError(String(err))
          setExportLoading(false)
        })
    }, POLL_MS)
  }

  function handleFileChange(e) {
    const file = e.target.files && e.target.files[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = (ev) => {
      let parsed
      try {
        parsed = JSON.parse(ev.target.result)
      } catch {
        setActionError('Invalid JSON file — could not parse.')
        return
      }
      setActionMsg(null)
      setActionError(null)
      setActionLoading(true)
      fetch('/api/copytrade/upload/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(parsed),
      })
        .then((r) => (r.ok ? r.json() : r.json().then((d) => Promise.reject(d.error || `HTTP ${r.status}`))))
        .then((data) => {
          setActionMsg(`Cohort loaded: ${data.cohort_id} (${data.description || 'no description'})`)
          refresh()
        })
        .catch((err) => setActionError(String(err)))
        .finally(() => setActionLoading(false))
    }
    reader.readAsText(file)
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  const engineOn = summary?.engine_on ?? false
  const mode = summary?.mode ?? 'observe'
  const cohortId = summary?.cohort_id ?? null
  const createdAt = summary?.since ?? null
  const nWallets = summary?.n_wallets ?? 0
  const nOpen = summary?.n_open_positions ?? 0

  return (
    <div style={{ fontFamily: STYLE.font, color: STYLE.text, maxWidth: '1100px' }}>
      <h2 style={{ color: STYLE.text, fontSize: '18px', marginBottom: '20px', fontWeight: 600 }}>
        Copy Trade
      </h2>

      {/* Global action feedback */}
      {actionMsg && <InfoBox msg={actionMsg} />}
      {actionError && <ErrorBox msg={`Action error: ${actionError}`} />}

      {/* ------------------------------------------------------------------ */}
      {/* SECTION 1 — Top bar: controls + status chips                        */}
      {/* ------------------------------------------------------------------ */}
      <div style={sectionStyle}>
        <div style={{ ...labelStyle, marginBottom: '12px' }}>Controls</div>

        {/* Control row */}
        <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'center', marginBottom: '14px' }}>

          {/* Upload JSON — hidden file input + visible button */}
          <input
            ref={fileInputRef}
            type="file"
            accept=".json,application/json"
            style={{ display: 'none' }}
            onChange={handleFileChange}
          />
          <button style={btnStyle('secondary', actionLoading)} onClick={handleUploadClick} disabled={actionLoading}>
            Upload JSON
          </button>

          {/* ON/OFF master toggle */}
          <button
            style={btnStyle(engineOn ? 'danger' : 'primary', actionLoading)}
            onClick={handleEngineToggle}
            disabled={actionLoading}
            title={engineOn ? 'Turn copy-trade engine OFF' : 'Turn copy-trade engine ON'}
          >
            {engineOn ? 'Engine ON → Turn OFF' : 'Engine OFF → Turn ON'}
          </button>

          {/* Observe/Live mode toggle */}
          <button
            style={btnStyle('secondary', actionLoading)}
            onClick={handleModeToggle}
            disabled={actionLoading}
            title={mode === 'observe' ? 'Switch to Live (P8-gated — INERT this sprint)' : 'Switch to Observe (paper mode)'}
          >
            Mode: {mode === 'observe' ? 'Observe → Live ⚠' : 'Live → Observe'}
          </button>

          {/* Download Export (AC-63.3 / SPEC §11) — dispatches to celery via §6.5 channel */}
          <button
            style={btnStyle('secondary', exportLoading || actionLoading)}
            onClick={handleExport}
            disabled={exportLoading || actionLoading || !cohortId}
            title={cohortId ? 'Export copytrade_positions CSV (dispatched to celery-worker)' : 'No active cohort — upload one first'}
          >
            {exportLoading ? 'Exporting…' : 'Download Export'}
          </button>
        </div>

        {/* Export feedback */}
        {exportMsg && <InfoBox msg={exportMsg} />}
        {exportError && <ErrorBox msg={`Export error: ${exportError}`} />}

        {/* SOL size / TP% / SL% overrides */}
        <form onSubmit={handleOverrides} style={{ display: 'flex', gap: '12px', alignItems: 'flex-end', flexWrap: 'wrap', marginBottom: '14px' }}>
          <div>
            <div style={labelStyle}>SOL Size</div>
            <input
              style={inputStyle}
              type="number"
              step="0.01"
              min="0.01"
              value={solSize}
              onChange={(e) => setSolSize(e.target.value)}
              placeholder="0.1"
            />
          </div>
          <div>
            <div style={labelStyle}>TP %</div>
            <input
              style={inputStyle}
              type="number"
              step="0.1"
              min="0.1"
              value={tpPct}
              onChange={(e) => setTpPct(e.target.value)}
              placeholder="20"
            />
          </div>
          <div>
            <div style={labelStyle}>SL %</div>
            <input
              style={inputStyle}
              type="number"
              step="0.1"
              min="0.1"
              max="100"
              value={slPct}
              onChange={(e) => setSlPct(e.target.value)}
              placeholder="10"
            />
          </div>
          <button type="submit" style={btnStyle('secondary')}>Save Overrides</button>
        </form>

        {overridesMsg && <InfoBox msg={overridesMsg} />}
        {overridesError && <ErrorBox msg={`Override error: ${overridesError}`} />}

        {/* Status chips */}
        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', alignItems: 'center', marginTop: '4px' }}>
          <span style={labelStyle}>Status:</span>
          <span style={chipStyle(engineOn)}>{engineOn ? 'ENGINE ON' : 'ENGINE OFF'}</span>
          <span style={neutralChipStyle}>MODE: {mode.toUpperCase()}</span>
          <span style={neutralChipStyle}>{nWallets} wallet{nWallets !== 1 ? 's' : ''}</span>
          <span style={neutralChipStyle}>{nOpen} open position{nOpen !== 1 ? 's' : ''}</span>
          {cohortId && (
            <span style={neutralChipStyle} title={cohortId}>
              cohort: {cohortId.length > 14 ? `${cohortId.slice(0, 12)}…` : cohortId}
            </span>
          )}
          {createdAt && (
            <span style={{ ...neutralChipStyle, fontSize: '11px' }} title={createdAt}>
              since {new Date(createdAt).toLocaleDateString()}
            </span>
          )}
          {mode === 'live' && (
            <span style={{ ...chipStyle(false, STYLE.yellow), fontSize: '11px' }}>
              LIVE is P8-gated INERT — no real orders placed
            </span>
          )}
        </div>

        {summaryError && <ErrorBox msg={`Status fetch error: ${summaryError}`} />}
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* SECTION 2 — Per-wallet PnL table (sortable)                         */}
      {/* ------------------------------------------------------------------ */}
      <div style={sectionStyle}>
        {pnlError && <ErrorBox msg={`PnL fetch error: ${pnlError}`} />}
        <PnlTable wallets={pnl.wallets} />
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* SECTION 3 — Open positions                                          */}
      {/* ------------------------------------------------------------------ */}
      <div style={sectionStyle}>
        {positionsError && <ErrorBox msg={`Positions fetch error: ${positionsError}`} />}
        <OpenPositionsTable positions={positions} />
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* SECTION 4 — Recent trades log                                       */}
      {/* ------------------------------------------------------------------ */}
      <div style={sectionStyle}>
        {tradesError && <ErrorBox msg={`Trades fetch error: ${tradesError}`} />}
        <RecentTradesLog trades={trades} />
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* SECTION 5 — Cohort summary                                          */}
      {/* ------------------------------------------------------------------ */}
      <div style={sectionStyle}>
        <div style={{ ...labelStyle, marginBottom: '12px' }}>Cohort Summary</div>
        {summaryError && <ErrorBox msg={`Summary fetch error: ${summaryError}`} />}
        {summary ? <CohortSummary summary={summary} /> : (
          <p style={{ color: STYLE.textSecondary, fontSize: '13px', margin: 0 }}>No active cohort.</p>
        )}
      </div>

      {/* Refresh button (manual) */}
      <div style={{ marginBottom: '20px' }}>
        <button style={btnStyle('secondary')} onClick={refresh}>↻ Refresh</button>
      </div>
    </div>
  )
}

export default CopyTradeTab
