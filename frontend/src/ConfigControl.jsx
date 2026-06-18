// ---
// file: frontend/src/ConfigControl.jsx
// stack: react+vite
// purpose: Config & Model control operator skin (US-56 AC-56.3, PRD §13.2#6).
//   Renders the active PipelineConfig + ModelRegistry, version diff viewer, and
//   operator-gated activate controls. Data from /api/control/config/ and
//   /api/control/registry/ — no new config math (Principle #1/#2).
//   Activate calls are gated by IsAdminUser on the server (HTTP 403 if not admin).
// created-by: dev-team
// sprint: sprint-11
// story: US-56 AC-56.3
// last-updated: 2026-06-18
// ---

import { useState, useEffect } from 'react'

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

const valueStyle = {
  color: STYLE.text,
  fontSize: '14px',
  marginBottom: '12px',
}

const activeBadge = (isActive) => ({
  display: 'inline-block',
  background: isActive ? '#1a3330' : '#2a1a1a',
  color: isActive ? STYLE.green : STYLE.red,
  border: `1px solid ${isActive ? STYLE.green : STYLE.red}`,
  borderRadius: '4px',
  padding: '2px 8px',
  fontSize: '12px',
  fontWeight: 600,
})

const buttonStyle = (variant = 'primary') => ({
  background: variant === 'primary' ? '#1a3330' : '#2a2d3e',
  color: variant === 'primary' ? STYLE.green : STYLE.text,
  border: `1px solid ${variant === 'primary' ? STYLE.green : '#3a3d4e'}`,
  borderRadius: '4px',
  padding: '6px 14px',
  fontSize: '13px',
  cursor: 'pointer',
  fontFamily: STYLE.font,
  marginRight: '8px',
})

const errorBox = (msg) =>
  msg ? (
    <div
      style={{
        background: '#2a1a1a',
        border: `1px solid ${STYLE.red}`,
        borderRadius: '4px',
        padding: '8px 12px',
        color: STYLE.red,
        fontSize: '13px',
        marginTop: '8px',
      }}
    >
      {msg}
    </div>
  ) : null

// ---------------------------------------------------------------------------
// Small presentational components
// ---------------------------------------------------------------------------

function JsonSnippet({ label, data }) {
  return (
    <div style={{ marginBottom: '10px' }}>
      <div style={labelStyle}>{label}</div>
      <pre
        style={{
          background: STYLE.codeBg,
          border: STYLE.border,
          borderRadius: '4px',
          padding: '8px 12px',
          color: STYLE.textSecondary,
          fontSize: '12px',
          overflowX: 'auto',
          margin: 0,
        }}
      >
        {JSON.stringify(data, null, 2)}
      </pre>
    </div>
  )
}

function DiffTable({ diff }) {
  if (!diff || Object.keys(diff).length === 0) {
    return <p style={{ color: STYLE.textSecondary, fontSize: '13px' }}>No differences.</p>
  }

  // Config diff has { meta: {...}, sections: {...} } shape
  // Model diff is a flat { field: { from, to } } shape
  const isConfigDiff = diff.meta !== undefined || diff.sections !== undefined

  if (isConfigDiff) {
    return (
      <div>
        {diff.meta && Object.keys(diff.meta).length > 0 && (
          <div style={{ marginBottom: '12px' }}>
            <div style={{ ...labelStyle, marginBottom: '8px' }}>Meta changes</div>
            {Object.entries(diff.meta).map(([field, change]) => (
              <DiffRow key={field} field={field} change={change} />
            ))}
          </div>
        )}
        {diff.sections && Object.keys(diff.sections).length > 0 && (
          <div>
            <div style={{ ...labelStyle, marginBottom: '8px' }}>Section changes</div>
            {Object.entries(diff.sections).map(([section, sectionDiff]) => (
              <div key={section} style={{ marginBottom: '10px' }}>
                <div style={{ color: STYLE.text, fontSize: '13px', fontWeight: 600, marginBottom: '4px' }}>
                  {section}
                </div>
                {sectionDiff.changed &&
                  Object.entries(sectionDiff.changed).map(([field, change]) => (
                    <DiffRow key={`changed-${field}`} field={field} change={change} indent />
                  ))}
                {sectionDiff.added &&
                  Object.entries(sectionDiff.added).map(([field, val]) => (
                    <DiffRow key={`added-${field}`} field={field} change={{ from: '(absent)', to: val }} indent />
                  ))}
                {sectionDiff.removed &&
                  Object.entries(sectionDiff.removed).map(([field, val]) => (
                    <DiffRow key={`removed-${field}`} field={field} change={{ from: val, to: '(removed)' }} indent />
                  ))}
              </div>
            ))}
          </div>
        )}
      </div>
    )
  }

  // Flat model diff
  return (
    <div>
      {Object.entries(diff).map(([field, change]) => (
        <DiffRow key={field} field={field} change={change} />
      ))}
    </div>
  )
}

function DiffRow({ field, change, indent }) {
  const hasFromTo = change && typeof change === 'object' && 'from' in change && 'to' in change
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: '12px',
        padding: '4px 8px',
        marginLeft: indent ? '12px' : 0,
        background: '#0f1117',
        borderRadius: '3px',
        marginBottom: '2px',
        fontSize: '12px',
        fontFamily: 'monospace',
      }}
    >
      <span style={{ color: STYLE.textSecondary, minWidth: '160px', flexShrink: 0 }}>{field}</span>
      {hasFromTo ? (
        <>
          <span style={{ color: STYLE.red }}>{JSON.stringify(change.from)}</span>
          <span style={{ color: STYLE.textSecondary }}>→</span>
          <span style={{ color: STYLE.green }}>{JSON.stringify(change.to)}</span>
        </>
      ) : (
        <span style={{ color: STYLE.text }}>{JSON.stringify(change)}</span>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// ConfigControl — main component
// ---------------------------------------------------------------------------

function ConfigControl() {
  // --- Config state ---
  const [configs, setConfigs] = useState({ active: null, all: [] })
  const [configError, setConfigError] = useState(null)

  // --- Registry state ---
  const [registry, setRegistry] = useState({ active: null, all: [] })
  const [registryError, setRegistryError] = useState(null)

  // --- Config diff state ---
  const [configDiffV1, setConfigDiffV1] = useState('')
  const [configDiffV2, setConfigDiffV2] = useState('')
  const [configDiff, setConfigDiff] = useState(null)
  const [configDiffError, setConfigDiffError] = useState(null)
  const [configDiffLoading, setConfigDiffLoading] = useState(false)

  // --- Registry diff state ---
  const [registryDiffCandidate, setRegistryDiffCandidate] = useState('')
  const [registryDiff, setRegistryDiff] = useState(null)
  const [registryDiffError, setRegistryDiffError] = useState(null)
  const [registryDiffLoading, setRegistryDiffLoading] = useState(false)

  // --- Activate state ---
  const [activateMsg, setActivateMsg] = useState(null)
  const [activateError, setActivateError] = useState(null)
  const [activateLoading, setActivateLoading] = useState(false)

  // ---------------------------------------------------------------------------
  // Data fetching
  // ---------------------------------------------------------------------------

  useEffect(() => {
    fetch('/api/control/config/')
      .then((r) => (r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`)))
      .then((data) => setConfigs(data))
      .catch((err) => setConfigError(String(err)))
  }, [activateMsg]) // re-fetch after activation

  useEffect(() => {
    fetch('/api/control/registry/')
      .then((r) => (r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`)))
      .then((data) => setRegistry(data))
      .catch((err) => setRegistryError(String(err)))
  }, [activateMsg]) // re-fetch after activation

  // ---------------------------------------------------------------------------
  // Handlers
  // ---------------------------------------------------------------------------

  function handleConfigDiff(e) {
    e.preventDefault()
    if (!configDiffV1 || !configDiffV2) {
      setConfigDiffError('Select both v1 and v2 configs.')
      return
    }
    setConfigDiffError(null)
    setConfigDiff(null)
    setConfigDiffLoading(true)
    fetch(`/api/control/config/diff/?v1=${configDiffV1}&v2=${configDiffV2}`)
      .then((r) => (r.ok ? r.json() : r.json().then((d) => Promise.reject(d.error || `HTTP ${r.status}`))))
      .then((data) => setConfigDiff(data))
      .catch((err) => setConfigDiffError(String(err)))
      .finally(() => setConfigDiffLoading(false))
  }

  function handleRegistryDiff(e) {
    e.preventDefault()
    if (!registryDiffCandidate) {
      setRegistryDiffError('Select a candidate model.')
      return
    }
    setRegistryDiffError(null)
    setRegistryDiff(null)
    setRegistryDiffLoading(true)
    fetch(`/api/control/registry/diff/?candidate=${registryDiffCandidate}`)
      .then((r) => (r.ok ? r.json() : r.json().then((d) => Promise.reject(d.error || `HTTP ${r.status}`))))
      .then((data) => setRegistryDiff(data))
      .catch((err) => setRegistryDiffError(String(err)))
      .finally(() => setRegistryDiffLoading(false))
  }

  function handleActivateConfig(pk) {
    setActivateError(null)
    setActivateMsg(null)
    setActivateLoading(true)
    fetch(`/api/control/config/${pk}/activate/`, { method: 'POST' })
      .then((r) => {
        if (r.status === 403) return Promise.reject('HTTP 403 Forbidden — admin access required.')
        return r.ok ? r.json() : r.json().then((d) => Promise.reject(d.error || `HTTP ${r.status}`))
      })
      .then((data) => setActivateMsg(`Config v${data.version} (${data.label}) activated.`))
      .catch((err) => setActivateError(String(err)))
      .finally(() => setActivateLoading(false))
  }

  function handleActivateRegistry(pk) {
    setActivateError(null)
    setActivateMsg(null)
    setActivateLoading(true)
    fetch(`/api/control/registry/${pk}/activate/`, { method: 'POST' })
      .then((r) => {
        if (r.status === 403) return Promise.reject('HTTP 403 Forbidden — admin access required.')
        return r.ok ? r.json() : r.json().then((d) => Promise.reject(d.error || `HTTP ${r.status}`))
      })
      .then((data) => setActivateMsg(`Model ${data.kind} ${data.model_version} activated.`))
      .catch((err) => setActivateError(String(err)))
      .finally(() => setActivateLoading(false))
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div style={{ fontFamily: STYLE.font, color: STYLE.text, maxWidth: '960px' }}>
      <h2 style={{ color: STYLE.text, fontSize: '18px', marginBottom: '20px', fontWeight: 600 }}>
        Config &amp; Model Control
      </h2>

      {/* Global activate feedback */}
      {activateMsg && (
        <div
          style={{
            background: '#1a3330',
            border: `1px solid ${STYLE.green}`,
            borderRadius: '4px',
            padding: '8px 14px',
            color: STYLE.green,
            fontSize: '13px',
            marginBottom: '16px',
          }}
        >
          {activateMsg}
        </div>
      )}
      {activateError && errorBox(`Activate error: ${activateError}`)}

      {/* ------------------------------------------------------------------ */}
      {/* PIPELINE CONFIG SECTION                                              */}
      {/* ------------------------------------------------------------------ */}
      <h3 style={{ color: STYLE.textSecondary, fontSize: '13px', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: '12px' }}>
        Pipeline Config
      </h3>

      {configError && errorBox(`Config fetch error: ${configError}`)}

      {/* Active config summary */}
      <div style={sectionStyle}>
        <div style={{ ...labelStyle, marginBottom: '10px' }}>Active Config</div>
        {configs.active ? (
          <div>
            <div style={{ display: 'flex', gap: '32px', flexWrap: 'wrap', marginBottom: '12px' }}>
              <div>
                <div style={labelStyle}>ID</div>
                <div style={valueStyle}>{configs.active.id}</div>
              </div>
              <div>
                <div style={labelStyle}>Version</div>
                <div style={valueStyle}>{configs.active.version}</div>
              </div>
              <div>
                <div style={labelStyle}>Label</div>
                <div style={valueStyle}>{configs.active.label}</div>
              </div>
              <div>
                <div style={labelStyle}>Created</div>
                <div style={valueStyle}>{configs.active.created_at ? new Date(configs.active.created_at).toLocaleString() : '—'}</div>
              </div>
              <div>
                <div style={labelStyle}>Status</div>
                <div style={{ marginBottom: '12px' }}>
                  <span style={activeBadge(configs.active.is_active)}>
                    {configs.active.is_active ? 'ACTIVE' : 'INACTIVE'}
                  </span>
                </div>
              </div>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '8px' }}>
              {['detection', 'tape', 'scoring', 'outcome', 'trading'].map((section) =>
                configs.active[section] ? (
                  <JsonSnippet key={section} label={section} data={configs.active[section]} />
                ) : null
              )}
            </div>
          </div>
        ) : (
          <p style={{ color: STYLE.textSecondary, fontSize: '13px', margin: 0 }}>No active config.</p>
        )}
      </div>

      {/* All configs table */}
      <div style={sectionStyle}>
        <div style={{ ...labelStyle, marginBottom: '10px' }}>All Configs</div>
        {configs.all.length === 0 ? (
          <p style={{ color: STYLE.textSecondary, fontSize: '13px', margin: 0 }}>None.</p>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
            <thead>
              <tr>
                {['ID', 'Version', 'Label', 'Status', 'Created', 'Actions'].map((h) => (
                  <th
                    key={h}
                    style={{
                      textAlign: 'left',
                      color: STYLE.textSecondary,
                      fontSize: '11px',
                      textTransform: 'uppercase',
                      letterSpacing: '0.07em',
                      paddingBottom: '8px',
                      borderBottom: `1px solid #2a2d3e`,
                    }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {configs.all.map((cfg) => (
                <tr key={cfg.id} style={{ borderBottom: '1px solid #1a1d2e' }}>
                  <td style={{ padding: '8px 0', color: STYLE.textSecondary }}>{cfg.id}</td>
                  <td style={{ padding: '8px 8px 8px 0', color: STYLE.text }}>{cfg.version}</td>
                  <td style={{ padding: '8px 8px 8px 0', color: STYLE.text }}>{cfg.label}</td>
                  <td style={{ padding: '8px 8px 8px 0' }}>
                    <span style={activeBadge(cfg.is_active)}>{cfg.is_active ? 'ACTIVE' : 'inactive'}</span>
                  </td>
                  <td style={{ padding: '8px 8px 8px 0', color: STYLE.textSecondary, fontSize: '12px' }}>
                    {cfg.created_at ? new Date(cfg.created_at).toLocaleDateString() : '—'}
                  </td>
                  <td style={{ padding: '8px 0' }}>
                    <button
                      style={buttonStyle('primary')}
                      onClick={() => handleActivateConfig(cfg.id)}
                      disabled={activateLoading || cfg.is_active}
                      title={cfg.is_active ? 'Already active' : 'Activate this config (admin only)'}
                    >
                      {cfg.is_active ? 'Active' : 'Activate'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Config diff viewer */}
      <div style={sectionStyle}>
        <div style={{ ...labelStyle, marginBottom: '10px' }}>Config Diff Viewer</div>
        <form onSubmit={handleConfigDiff} style={{ display: 'flex', gap: '12px', alignItems: 'flex-end', flexWrap: 'wrap', marginBottom: '12px' }}>
          <div>
            <div style={labelStyle}>v1 Config</div>
            <select
              value={configDiffV1}
              onChange={(e) => setConfigDiffV1(e.target.value)}
              style={{
                background: STYLE.codeBg,
                border: STYLE.border,
                borderRadius: '4px',
                color: STYLE.text,
                padding: '6px 10px',
                fontSize: '13px',
                fontFamily: STYLE.font,
              }}
            >
              <option value="">-- select --</option>
              {configs.all.map((cfg) => (
                <option key={cfg.id} value={cfg.id}>
                  {cfg.id}: v{cfg.version} {cfg.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <div style={labelStyle}>v2 Config</div>
            <select
              value={configDiffV2}
              onChange={(e) => setConfigDiffV2(e.target.value)}
              style={{
                background: STYLE.codeBg,
                border: STYLE.border,
                borderRadius: '4px',
                color: STYLE.text,
                padding: '6px 10px',
                fontSize: '13px',
                fontFamily: STYLE.font,
              }}
            >
              <option value="">-- select --</option>
              {configs.all.map((cfg) => (
                <option key={cfg.id} value={cfg.id}>
                  {cfg.id}: v{cfg.version} {cfg.label}
                </option>
              ))}
            </select>
          </div>
          <button type="submit" style={buttonStyle('secondary')} disabled={configDiffLoading}>
            {configDiffLoading ? 'Loading...' : 'Show Diff'}
          </button>
        </form>
        {configDiffError && errorBox(configDiffError)}
        {configDiff && <DiffTable diff={configDiff} />}
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* MODEL REGISTRY SECTION                                               */}
      {/* ------------------------------------------------------------------ */}
      <h3 style={{ color: STYLE.textSecondary, fontSize: '13px', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: '12px', marginTop: '8px' }}>
        Model Registry
      </h3>

      {registryError && errorBox(`Registry fetch error: ${registryError}`)}

      {/* Active model summary */}
      <div style={sectionStyle}>
        <div style={{ ...labelStyle, marginBottom: '10px' }}>Active Model</div>
        {registry.active ? (
          <div style={{ display: 'flex', gap: '32px', flexWrap: 'wrap' }}>
            <div>
              <div style={labelStyle}>ID</div>
              <div style={valueStyle}>{registry.active.id}</div>
            </div>
            <div>
              <div style={labelStyle}>Kind</div>
              <div style={valueStyle}>{registry.active.kind}</div>
            </div>
            <div>
              <div style={labelStyle}>Model Version</div>
              <div style={valueStyle}>{registry.active.model_version}</div>
            </div>
            <div>
              <div style={labelStyle}>Feature Set Version</div>
              <div style={valueStyle}>{registry.active.feature_set_version}</div>
            </div>
            <div>
              <div style={labelStyle}>Created</div>
              <div style={valueStyle}>{registry.active.created_at ? new Date(registry.active.created_at).toLocaleString() : '—'}</div>
            </div>
            <div>
              <div style={labelStyle}>Status</div>
              <div style={{ marginBottom: '12px' }}>
                <span style={activeBadge(registry.active.is_active)}>
                  {registry.active.is_active ? 'ACTIVE' : 'INACTIVE'}
                </span>
              </div>
            </div>
          </div>
        ) : (
          <p style={{ color: STYLE.textSecondary, fontSize: '13px', margin: 0 }}>No active model.</p>
        )}
      </div>

      {/* All models table */}
      <div style={sectionStyle}>
        <div style={{ ...labelStyle, marginBottom: '10px' }}>All Models</div>
        {registry.all.length === 0 ? (
          <p style={{ color: STYLE.textSecondary, fontSize: '13px', margin: 0 }}>None.</p>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
            <thead>
              <tr>
                {['ID', 'Kind', 'Model Ver', 'Feature Ver', 'Status', 'Created', 'Actions'].map((h) => (
                  <th
                    key={h}
                    style={{
                      textAlign: 'left',
                      color: STYLE.textSecondary,
                      fontSize: '11px',
                      textTransform: 'uppercase',
                      letterSpacing: '0.07em',
                      paddingBottom: '8px',
                      borderBottom: '1px solid #2a2d3e',
                    }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {registry.all.map((model) => (
                <tr key={model.id} style={{ borderBottom: '1px solid #1a1d2e' }}>
                  <td style={{ padding: '8px 0', color: STYLE.textSecondary }}>{model.id}</td>
                  <td style={{ padding: '8px 8px 8px 0', color: STYLE.text, fontSize: '12px' }}>{model.kind}</td>
                  <td style={{ padding: '8px 8px 8px 0', color: STYLE.text }}>{model.model_version}</td>
                  <td style={{ padding: '8px 8px 8px 0', color: STYLE.textSecondary }}>{model.feature_set_version}</td>
                  <td style={{ padding: '8px 8px 8px 0' }}>
                    <span style={activeBadge(model.is_active)}>{model.is_active ? 'ACTIVE' : 'inactive'}</span>
                  </td>
                  <td style={{ padding: '8px 8px 8px 0', color: STYLE.textSecondary, fontSize: '12px' }}>
                    {model.created_at ? new Date(model.created_at).toLocaleDateString() : '—'}
                  </td>
                  <td style={{ padding: '8px 0' }}>
                    <button
                      style={buttonStyle('primary')}
                      onClick={() => handleActivateRegistry(model.id)}
                      disabled={activateLoading || model.is_active}
                      title={model.is_active ? 'Already active' : 'Activate this model (admin only)'}
                    >
                      {model.is_active ? 'Active' : 'Activate'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Model diff viewer */}
      <div style={sectionStyle}>
        <div style={{ ...labelStyle, marginBottom: '10px' }}>Model Diff Viewer</div>
        <form onSubmit={handleRegistryDiff} style={{ display: 'flex', gap: '12px', alignItems: 'flex-end', flexWrap: 'wrap', marginBottom: '12px' }}>
          <div>
            <div style={labelStyle}>Candidate Model</div>
            <select
              value={registryDiffCandidate}
              onChange={(e) => setRegistryDiffCandidate(e.target.value)}
              style={{
                background: STYLE.codeBg,
                border: STYLE.border,
                borderRadius: '4px',
                color: STYLE.text,
                padding: '6px 10px',
                fontSize: '13px',
                fontFamily: STYLE.font,
              }}
            >
              <option value="">-- select candidate --</option>
              {registry.all.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.id}: {model.kind} {model.model_version}
                </option>
              ))}
            </select>
          </div>
          <button type="submit" style={buttonStyle('secondary')} disabled={registryDiffLoading}>
            {registryDiffLoading ? 'Loading...' : 'Show Diff'}
          </button>
        </form>
        {registryDiffError && errorBox(registryDiffError)}
        {registryDiff && (
          <div>
            <div style={{ ...labelStyle, marginBottom: '6px' }}>
              Base ID: {registryDiff.base_id ?? '(none)'} → Candidate ID: {registryDiff.candidate_id}
            </div>
            <DiffTable diff={registryDiff.diff || {}} />
          </div>
        )}
      </div>
    </div>
  )
}

export default ConfigControl
