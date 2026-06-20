// ---
// file: frontend/src/App.jsx
// stack: react+vite
// purpose: Root React component for the solanaTrilly research-first dashboard (PRD §13).
//   Renders a persistent top nav bar plus the selected view based on the ?view=
//   query param.  The inference engine control page loads when view=inference and
//   is the DEFAULT landing view.  The cohort wall loads when view=cohort; the
//   token-detail view loads when view=token with ?mint=.  The config/model control
//   operator skin loads when view=control (US-56 AC-56.3).  The Feature Builder UI
//   loads when view=features (US-57 AC-57.1).  The Copy Trade tab loads when
//   view=copytrade (US-63 AC-63.2).  The Live Positions board loads when
//   view=positions (US-69 AC-69.2).  The Calibration & PnL analytics view loads
//   when view=calibration (US-72 AC-72.2).  The Replay viewer loads when
//   view=replay (US-73 AC-73.2).
// created-by: dev-team
// sprint: sprint-14
// story: US-48 AC-48.1, US-49 AC-49.2, US-50 AC-50.3, US-56 AC-56.3, US-57 AC-57.1, US-63 AC-63.2, US-69 AC-69.2, US-72 AC-72.2, US-73 AC-73.2
// last-updated: 2026-06-20
// ---

import CalibrationPnL from './CalibrationPnL.jsx'
import CohortWall from './CohortWall.jsx'
import ConfigControl from './ConfigControl.jsx'
import CopyTradeTab from './CopyTradeTab.jsx'
import FeatureBuilder from './FeatureBuilder.jsx'
import InferencePage from './InferencePage.jsx'
import LivePositions from './LivePositions.jsx'
import ReplayViewer from './ReplayViewer.jsx'
import TokenDetail from './TokenDetail.jsx'

// Read a named query param from the current URL.
function getQueryParam(name, fallback) {
  if (typeof window === 'undefined') return fallback
  const params = new URLSearchParams(window.location.search)
  return params.get(name) || fallback
}

// Top-level views reachable from the nav bar (in display order).
const NAV_ITEMS = [
  { view: 'inference', label: 'Inference' },
  { view: 'positions', label: 'Live Positions' },
  { view: 'copytrade', label: 'Copy Trade' },
  { view: 'cohort', label: 'Cohort Wall' },
  { view: 'calibration', label: 'Calibration & PnL' },
  { view: 'replay', label: 'Replay' },
  { view: 'features', label: 'Feature Builder' },
  { view: 'control', label: 'Config & Models' },
]

function NavBar({ activeView }) {
  return (
    <nav
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: '8px',
        margin: '0 0 18px 0',
        paddingBottom: '12px',
        borderBottom: '1px solid #242833',
      }}
    >
      {NAV_ITEMS.map((item) => {
        const isActive = item.view === activeView
        return (
          <a
            key={item.view}
            href={`?view=${item.view}`}
            style={{
              color: isActive ? '#0f1117' : '#d1d4dc',
              background: isActive ? '#4f8bff' : '#1a1d27',
              border: '1px solid #242833',
              borderRadius: '6px',
              padding: '6px 12px',
              fontFamily: 'sans-serif',
              fontSize: '13px',
              fontWeight: isActive ? 700 : 500,
              textDecoration: 'none',
            }}
          >
            {item.label}
          </a>
        )
      })}
    </nav>
  )
}

function App() {
  const mint = getQueryParam('mint', null)
  // Default landing view = Inference engine control page, unless a ?mint=
  // is present (then show that token's detail view).
  const view = getQueryParam('view', null) || (mint ? 'token' : 'inference')
  const intervalS = parseInt(getQueryParam('interval_s', '15'), 10)
  const groupBy = getQueryParam('group_by', null)
  const sortBy = getQueryParam('sort_by', null)
  const runId = getQueryParam('run_id', null)

  return (
    <div id="solanatrilly-app" style={{ background: '#0f1117', minHeight: '100vh', padding: '16px' }}>
      <h1 style={{ color: '#d1d4dc', fontFamily: 'sans-serif', margin: '0 0 14px 0' }}>
        solanaTrilly Dashboard
      </h1>
      <NavBar activeView={view} />
      {view === 'inference' ? (
        <InferencePage />
      ) : view === 'cohort' ? (
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
        <p style={{ color: '#9598a1', fontFamily: 'sans-serif' }}>
          Select a view above, or pass <code>?mint=&lt;address&gt;</code> to open a
          replayed token&rsquo;s detail view.
        </p>
      )}
    </div>
  )
}

export default App
