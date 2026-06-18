// ---
// file: frontend/src/App.jsx
// stack: react+vite
// purpose: Root React component for the solanaTrilly research-first dashboard (PRD §13).
//   Renders the token-detail research view (US-49 AC-49.2) or the cohort wall
//   (US-50 AC-50.3) based on the ?view= query param.  The cohort wall loads when
//   view=cohort; the token-detail view loads when view=token (default) with ?mint=.
//   The config/model control operator skin loads when view=control (US-56 AC-56.3).
//   The Feature Builder UI loads when view=features (US-57 AC-57.1).
//   The Copy Trade tab loads when view=copytrade (US-63 AC-63.2).
//   The Live Positions board loads when view=positions (US-69 AC-69.2).
//   The Calibration & PnL analytics view loads when view=calibration (US-72 AC-72.2).
//   The Replay viewer position-open/close overlay loads when view=replay (US-73 AC-73.2).
// created-by: dev-team
// sprint: sprint-14
// story: US-48 AC-48.1, US-49 AC-49.2, US-50 AC-50.3, US-56 AC-56.3, US-57 AC-57.1, US-63 AC-63.2, US-69 AC-69.2, US-72 AC-72.2, US-73 AC-73.2
// last-updated: 2026-06-19
// ---

import CalibrationPnL from './CalibrationPnL.jsx'
import CohortWall from './CohortWall.jsx'
import ConfigControl from './ConfigControl.jsx'
import CopyTradeTab from './CopyTradeTab.jsx'
import FeatureBuilder from './FeatureBuilder.jsx'
import LivePositions from './LivePositions.jsx'
import ReplayViewer from './ReplayViewer.jsx'
import TokenDetail from './TokenDetail.jsx'

// Read a named query param from the current URL.
function getQueryParam(name, fallback) {
  if (typeof window === 'undefined') return fallback
  const params = new URLSearchParams(window.location.search)
  return params.get(name) || fallback
}

function App() {
  const view = getQueryParam('view', 'token')
  const mint = getQueryParam('mint', null)
  const intervalS = parseInt(getQueryParam('interval_s', '15'), 10)
  const groupBy = getQueryParam('group_by', null)
  const sortBy = getQueryParam('sort_by', null)
  const runId = getQueryParam('run_id', null)

  return (
    <div id="solanatrilly-app" style={{ background: '#0f1117', minHeight: '100vh', padding: '16px' }}>
      <h1 style={{ color: '#d1d4dc', fontFamily: 'sans-serif' }}>solanaTrilly Dashboard</h1>
      {view === 'cohort' ? (
        <CohortWall intervalS={intervalS} groupBy={groupBy} sortBy={sortBy} />
      ) : view === 'control' ? (
        <ConfigControl />
      ) : view === 'features' ? (
        <FeatureBuilder />
      ) : view === 'copytrade' ? (
        <CopyTradeTab />
      ) : view === 'positions' ? (
        <LivePositions />
      ) : view === 'calibration' ? (
        <CalibrationPnL />
      ) : view === 'replay' ? (
        <ReplayViewer runId={runId} />
      ) : mint ? (
        <TokenDetail mint={mint} intervalS={intervalS} />
      ) : (
        <p style={{ color: '#9598a1' }}>
          Pass <code>?mint=&lt;address&gt;</code> to view a replayed token, or{' '}
          <code>?view=cohort</code> to open the cohort pattern-mining wall, or{' '}
          <code>?view=control</code> to open the config &amp; model control panel, or{' '}
          <code>?view=features</code> to open the Feature Builder (§6.5 export), or{' '}
          <code>?view=copytrade</code> to open the Copy Trade tab, or{' '}
          <code>?view=positions</code> to open the Live Positions board, or{' '}
          <code>?view=calibration</code> to open the Calibration &amp; PnL analytics view, or{' '}
          <code>?view=replay</code> to open the Replay viewer position-open/close overlay.
        </p>
      )}
    </div>
  )
}

export default App
