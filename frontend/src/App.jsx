// ---
// file: frontend/src/App.jsx
// stack: react+vite
// purpose: Root React component for the solanaTrilly research-first dashboard (PRD §13).
//   Renders the token-detail research view (US-49 AC-49.2) or the cohort wall
//   (US-50 AC-50.3) based on the ?view= query param.  The cohort wall loads when
//   view=cohort; the token-detail view loads when view=token (default) with ?mint=.
//   The config/model control operator skin loads when view=control (US-56 AC-56.3).
// created-by: dev-team
// sprint: sprint-11
// story: US-48 AC-48.1, US-49 AC-49.2, US-50 AC-50.3, US-56 AC-56.3
// last-updated: 2026-06-18
// ---

import CohortWall from './CohortWall.jsx'
import ConfigControl from './ConfigControl.jsx'
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

  return (
    <div id="solanatrilly-app" style={{ background: '#0f1117', minHeight: '100vh', padding: '16px' }}>
      <h1 style={{ color: '#d1d4dc', fontFamily: 'sans-serif' }}>solanaTrilly Dashboard</h1>
      {view === 'cohort' ? (
        <CohortWall intervalS={intervalS} groupBy={groupBy} sortBy={sortBy} />
      ) : view === 'control' ? (
        <ConfigControl />
      ) : mint ? (
        <TokenDetail mint={mint} intervalS={intervalS} />
      ) : (
        <p style={{ color: '#9598a1' }}>
          Pass <code>?mint=&lt;address&gt;</code> to view a replayed token, or{' '}
          <code>?view=cohort</code> to open the cohort pattern-mining wall, or{' '}
          <code>?view=control</code> to open the config &amp; model control panel.
        </p>
      )}
    </div>
  )
}

export default App
