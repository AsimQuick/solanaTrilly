// ---
// file: frontend/src/TokenDetail.jsx
// stack: react+vite+lightweight-charts
// purpose: Token-detail / research view (US-49 AC-49.2, PRD §13.2#2).
//   Renders a replayed token's real candle chart (TradingView Lightweight Charts)
//   with a buy/sell-pressure & net-flow overlay and t0/score markers.
//   Data is fetched from the AC-49.1 /api/token-detail/<mint>/ endpoint —
//   candles, overlay, and markers are all assembled server-side from the raw
//   lake via the shared extractor (Principle #2).  No firehose.
// created-by: dev-team
// sprint: sprint-10
// story: US-49 AC-49.2
// last-updated: 2026-06-17
// ---

import { useEffect, useRef, useState } from 'react'
import { createChart } from 'lightweight-charts'

// Default candle interval in seconds (matches DashboardConfig.candle_interval_s default).
const DEFAULT_INTERVAL_S = 15

/**
 * TokenDetail — research view for a single replayed token.
 *
 * Props:
 *   mint       (string, required) — Solana mint address of the replayed token.
 *   intervalS  (number, optional) — Candle interval in seconds (default: 15).
 *
 * Data flow (Principle #2 — one price basis):
 *   Fetches /api/token-detail/<mint>/?interval_s=<n>
 *   → {candles, overlay, markers} assembled server-side from the raw lake
 *   → renders candlestick series + net-flow histogram overlay + t0/score markers
 */
function TokenDetail({ mint, intervalS = DEFAULT_INTERVAL_S }) {
  const chartRef = useRef(null)
  const containerRef = useRef(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [detail, setDetail] = useState(null)

  // Fetch token-detail data from the backend assembler.
  useEffect(() => {
    if (!mint) return
    setLoading(true)
    setError(null)

    fetch(`/api/token-detail/${mint}/?interval_s=${intervalS}`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json()
      })
      .then((data) => {
        setDetail(data)
        setLoading(false)
      })
      .catch((err) => {
        setError(err.message)
        setLoading(false)
      })
  }, [mint, intervalS])

  // Build / update the chart once detail data arrives.
  useEffect(() => {
    if (!detail || !containerRef.current) return

    // Destroy previous chart instance to avoid duplicate canvas elements.
    if (chartRef.current) {
      chartRef.current.remove()
      chartRef.current = null
    }

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth || 900,
      height: 400,
      layout: { background: { color: '#0f1117' }, textColor: '#d1d4dc' },
      grid: {
        vertLines: { color: '#1e2130' },
        horzLines: { color: '#1e2130' },
      },
      timeScale: { timeVisible: true, secondsVisible: true },
    })
    chartRef.current = chart

    // --- Candlestick series (the REAL candle from the lake) ---
    const candleSeries = chart.addCandlestickSeries({
      upColor: '#26a69a',
      downColor: '#ef5350',
      borderVisible: false,
      wickUpColor: '#26a69a',
      wickDownColor: '#ef5350',
    })
    candleSeries.setData(
      detail.candles.map((c) => ({
        time: c.t,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      }))
    )

    // --- Net-flow overlay (buy pressure minus sell pressure per candle) ---
    const netFlowSeries = chart.addHistogramSeries({
      color: '#2196f3',
      priceFormat: { type: 'volume' },
      priceScaleId: 'netflow',
    })
    chart.priceScale('netflow').applyOptions({ scaleMargins: { top: 0.75, bottom: 0 } })
    netFlowSeries.setData(
      detail.overlay.map((o) => ({
        time: o.t,
        value: o.net_flow,
        color: o.net_flow >= 0 ? 'rgba(38,166,154,0.5)' : 'rgba(239,83,80,0.5)',
      }))
    )

    // --- Markers: t0 (graduation) and score_time ---
    const markers = []
    const { t0, score_time } = detail.markers
    if (t0 != null) {
      markers.push({
        time: t0,
        position: 'belowBar',
        color: '#ffffff',
        shape: 'arrowUp',
        text: 't0',
        size: 1,
      })
    }
    if (score_time != null) {
      markers.push({
        time: score_time,
        position: 'aboveBar',
        color: '#f59e0b',
        shape: 'arrowDown',
        text: 'score',
        size: 1,
      })
    }
    if (markers.length > 0) {
      candleSeries.setMarkers(markers)
    }

    // Fit chart to the full data range.
    chart.timeScale().fitContent()

    // Cleanup on unmount or re-render.
    return () => {
      chart.remove()
      chartRef.current = null
    }
  }, [detail])

  if (!mint) return <p>No token selected.</p>
  if (loading) return <p>Loading token detail for {mint}…</p>
  if (error) return <p style={{ color: '#ef5350' }}>Error: {error}</p>

  return (
    <div id="token-detail-view" data-mint={mint}>
      <h2 style={{ color: '#d1d4dc', fontFamily: 'monospace', fontSize: '0.9rem' }}>
        {mint} &mdash; {intervalS}s candles
      </h2>
      <div ref={containerRef} style={{ width: '100%' }} />
      <div
        id="token-detail-overlay-legend"
        style={{ color: '#9598a1', fontSize: '0.75rem', marginTop: 4 }}
      >
        <span style={{ color: 'rgba(38,166,154,0.8)' }}>&#9646;</span> buy pressure &nbsp;
        <span style={{ color: 'rgba(239,83,80,0.8)' }}>&#9646;</span> sell pressure &nbsp;
        <span style={{ color: '#2196f3' }}>net flow</span>
      </div>
    </div>
  )
}

export default TokenDetail
