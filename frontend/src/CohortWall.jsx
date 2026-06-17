// ---
// file: frontend/src/CohortWall.jsx
// stack: react+vite+lightweight-charts
// purpose: Cohort small-multiples pattern-mining wall (US-50 AC-50.3, PRD §13.2#3).
//   Renders a grid of mini candle sparklines grouped/sorted by the active dimension.
//   Data from /api/cohort/?interval_s=...&group_by=...&sort_by=... — assembled
//   server-side from the raw lake via build_cohort_wall (Principle #2). No firehose.
// created-by: dev-team
// sprint: sprint-10
// story: US-50 AC-50.3
// last-updated: 2026-06-17
// ---

import { useEffect, useRef, useState } from 'react'
import { createChart } from 'lightweight-charts'

// Mini chart dimensions for the small-multiples grid.
const SPARKLINE_WIDTH = 200
const SPARKLINE_HEIGHT = 100

/**
 * Sparkline — renders a single mini TradingView Lightweight Charts line chart.
 *
 * Props:
 *   mint    (string) — Solana mint address (shown truncated below the chart).
 *   candles (array)  — Array of {t, open, high, low, close, vol, interval_s} candle objects.
 *
 * Design: dark theme matching existing dashboard. No axis labels, no crosshair,
 * no scroll/scale — pure shape-reading mini chart.
 */
function Sparkline({ mint, candles }) {
  const containerRef = useRef(null)
  const chartRef = useRef(null)

  useEffect(() => {
    if (!containerRef.current) return

    // Destroy previous chart instance on re-render.
    if (chartRef.current) {
      chartRef.current.remove()
      chartRef.current = null
    }

    // Empty candle list — render nothing (no LW Charts instance created).
    if (!candles || candles.length === 0) return

    const chart = createChart(containerRef.current, {
      width: SPARKLINE_WIDTH,
      height: SPARKLINE_HEIGHT,
      layout: {
        background: { color: '#0f1117' },
        textColor: '#d1d4dc',
      },
      grid: {
        vertLines: { visible: false },
        horzLines: { visible: false },
      },
      timeScale: {
        visible: false,
      },
      rightPriceScale: {
        visible: false,
      },
      crosshair: {
        mode: 0, // CrosshairMode.Normal = 0; use numeric to avoid enum import
      },
      handleScroll: false,
      handleScale: false,
    })
    chartRef.current = chart

    // Line series showing close price — shape over price level (mini view).
    const lineSeries = chart.addLineSeries({
      color: '#26a69a',
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
    })
    lineSeries.setData(
      candles.map((c) => ({ time: c.t, value: c.close }))
    )

    chart.timeScale().fitContent()

    return () => {
      if (chartRef.current) {
        chartRef.current.remove()
        chartRef.current = null
      }
    }
  }, [candles])

  const shortMint = mint ? `${mint.slice(0, 6)}…${mint.slice(-4)}` : ''

  return (
    <div
      style={{
        display: 'inline-flex',
        flexDirection: 'column',
        alignItems: 'center',
        margin: '4px',
        background: '#151822',
        border: '1px solid #1e2130',
        borderRadius: '4px',
        padding: '4px',
      }}
    >
      <div
        ref={containerRef}
        style={{ width: SPARKLINE_WIDTH, height: candles && candles.length > 0 ? SPARKLINE_HEIGHT : 0 }}
      />
      <span
        style={{
          color: '#9598a1',
          fontFamily: 'monospace',
          fontSize: '0.65rem',
          marginTop: '2px',
          userSelect: 'all',
        }}
        title={mint}
      >
        {shortMint}
      </span>
    </div>
  )
}

/**
 * CohortWall — cohort pattern-mining wall: small-multiples grid of mini sparklines.
 *
 * Props:
 *   intervalS  (number, optional)  — Candle interval in seconds (default: 15).
 *   groupBy    (string | null)     — Grouping dimension (e.g. 'depth_bucket'). Default: null.
 *   sortBy     (string | null)     — Sort dimension within groups. Default: null.
 *
 * Fetches /api/cohort/?interval_s=...&group_by=...&sort_by=...
 * Renders groups from wall.groups — each group has a header and a flex-wrap grid of Sparklines.
 */
function CohortWall({ intervalS = 15, groupBy = null, sortBy = null }) {
  const [wall, setWall] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    setLoading(true)
    setError(null)

    const params = new URLSearchParams({ interval_s: intervalS })
    if (groupBy) params.set('group_by', groupBy)
    if (sortBy) params.set('sort_by', sortBy)

    fetch(`/api/cohort/?${params.toString()}`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json()
      })
      .then((data) => {
        setWall(data)
        setLoading(false)
      })
      .catch((err) => {
        setError(err.message)
        setLoading(false)
      })
  }, [intervalS, groupBy, sortBy])

  if (loading) {
    return (
      <p style={{ color: '#9598a1', fontFamily: 'sans-serif' }}>
        Loading cohort wall…
      </p>
    )
  }

  if (error) {
    return (
      <p style={{ color: '#ef5350', fontFamily: 'sans-serif' }}>
        Error loading cohort wall: {error}
      </p>
    )
  }

  if (!wall) return null

  return (
    <div
      id="cohort-wall-view"
      data-group-by={groupBy || ''}
      data-sort-by={sortBy || ''}
      style={{ fontFamily: 'sans-serif' }}
    >
      <div style={{ color: '#9598a1', fontSize: '0.8rem', marginBottom: '8px' }}>
        {wall.count} token{wall.count !== 1 ? 's' : ''} &mdash; {intervalS}s candles
        {groupBy ? ` · grouped by ${groupBy}` : ''}
        {sortBy ? ` · sorted by ${sortBy}` : ''}
      </div>

      {(wall.groups || []).map((group, gi) => (
        <div key={gi} style={{ marginBottom: '16px' }}>
          {group.key != null && (
            <div
              style={{
                color: '#d1d4dc',
                fontSize: '0.75rem',
                fontWeight: 600,
                marginBottom: '4px',
                paddingLeft: '4px',
                borderLeft: '3px solid #26a69a',
              }}
            >
              {groupBy}: {group.key} ({group.count})
            </div>
          )}
          <div style={{ display: 'flex', flexWrap: 'wrap' }}>
            {(group.entries || []).map((entry, ei) => (
              <Sparkline
                key={`${gi}-${ei}-${entry.mint}`}
                mint={entry.mint}
                candles={entry.candles}
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

export default CohortWall
