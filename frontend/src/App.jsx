// ---
// file: frontend/src/App.jsx
// stack: react+vite
// purpose: Root React component for the solanaTrilly research-first dashboard (PRD §13).
//   Renders the token-detail research view (US-49 AC-49.2) for a hardcoded replay
//   token (overridden via ?mint= query param for manual testing).  US-50/US-51
//   views are added on their respective branches.
// created-by: dev-team
// sprint: sprint-10
// story: US-48 AC-48.1, US-49 AC-49.2
// last-updated: 2026-06-17
// ---

import TokenDetail from './TokenDetail.jsx'

// Read mint and interval from query params so operators can test any replay token
// without rebuilding: ?mint=<address>&interval_s=15
function getQueryParam(name, fallback) {
  if (typeof window === 'undefined') return fallback
  const params = new URLSearchParams(window.location.search)
  return params.get(name) || fallback
}

function App() {
  const mint = getQueryParam('mint', null)
  const intervalS = parseInt(getQueryParam('interval_s', '15'), 10)

  return (
    <div id="solanatrilly-app" style={{ background: '#0f1117', minHeight: '100vh', padding: '16px' }}>
      <h1 style={{ color: '#d1d4dc', fontFamily: 'sans-serif' }}>solanaTrilly Dashboard</h1>
      {mint ? (
        <TokenDetail mint={mint} intervalS={intervalS} />
      ) : (
        <p style={{ color: '#9598a1' }}>
          Pass <code>?mint=&lt;address&gt;</code> to view a replayed token.
          (US-50 cohort wall and US-51 annotation panel load on their branches.)
        </p>
      )}
    </div>
  )
}

export default App
