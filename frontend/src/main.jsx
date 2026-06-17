// ---
// file: frontend/src/main.jsx
// stack: react+vite
// purpose: React application mount point — attaches App to the #root DOM node.
//   This is the entry point Vite resolves from index.html <script type="module">.
// created-by: dev-team
// sprint: sprint-10
// story: US-48 AC-48.1
// last-updated: 2026-06-17
// ---

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
