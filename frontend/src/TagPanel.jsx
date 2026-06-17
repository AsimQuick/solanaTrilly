// ---
// file: frontend/src/TagPanel.jsx
// stack: react+vite
// purpose: Tag panel beside the chart for human annotation (US-51 AC-51.1, PRD §13.3).
//   Categorical tags from config, free-text note, persisted to the annotations table.
//   NEVER writes to the raw lake — separate store keyed on mint (§6.4.1).
// created-by: dev-team
// sprint: sprint-10
// story: US-51 AC-51.1
// last-updated: 2026-06-17
// ---
import { useState, useEffect, useCallback } from "react";

/**
 * TagPanel — human annotation panel for a given token (mint).
 *
 * Props:
 *   mint          (string, required) — Solana mint address
 *   categoricalTags (string[], optional) — config-driven tag list; fetched from
 *                   /api/annotations/<mint>/ if omitted (Principle #1 — tags come
 *                   from config, never hardcoded here)
 *
 * The panel:
 *   - Renders categorical tags (from props or config); clicking toggles selection
 *   - Accepts a free-text note
 *   - Accepts an author name
 *   - POSTs to /api/annotations/<mint>/create/ on "Save Annotation"
 *   - Lists existing annotations via GET /api/annotations/<mint>/
 */
export default function TagPanel({ mint, categoricalTags: propTags }) {
  // --- state ---
  const [selectedTags, setSelectedTags] = useState([]);
  const [note, setNote] = useState("");
  const [author, setAuthor] = useState("");
  const [existingAnnotations, setExistingAnnotations] = useState([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);

  // Categorical tags — always from props (which must be passed from the config
  // API). Principle #1: tag literals live in AnnotationConfig only.
  const categoricalTags = propTags || [];

  // --- fetch existing annotations ---
  const fetchAnnotations = useCallback(() => {
    if (!mint) return;
    fetch(`/api/annotations/${mint}/`)
      .then((r) => r.json())
      .then((data) => setExistingAnnotations(Array.isArray(data) ? data : []))
      .catch((err) => console.error("TagPanel: failed to fetch annotations", err));
  }, [mint]);

  useEffect(() => {
    fetchAnnotations();
  }, [fetchAnnotations]);

  // --- tag toggle ---
  const toggleTag = (tag) => {
    setSelectedTags((prev) =>
      prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]
    );
  };

  // --- save handler ---
  const handleSave = async () => {
    if (!author.trim()) {
      setError("Author is required.");
      return;
    }
    setSaving(true);
    setError(null);
    setSuccess(null);
    try {
      const resp = await fetch(`/api/annotations/${mint}/create/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ author: author.trim(), tags: selectedTags, note }),
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        setError(body.error || `Server error: ${resp.status}`);
        return;
      }
      setSuccess("Annotation saved.");
      setSelectedTags([]);
      setNote("");
      fetchAnnotations();
    } catch (err) {
      setError(`Network error: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="tag-panel" style={{ padding: "1rem", border: "1px solid #ccc", borderRadius: 4 }}>
      <h3 style={{ marginTop: 0 }}>Annotate Token</h3>

      {/* Categorical tag buttons */}
      <div className="tag-panel__categorical" style={{ marginBottom: "0.75rem" }}>
        <label style={{ display: "block", fontWeight: "bold", marginBottom: 4 }}>
          Categorical Tags
        </label>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {categoricalTags.map((tag) => (
            <button
              key={tag}
              onClick={() => toggleTag(tag)}
              style={{
                padding: "4px 10px",
                borderRadius: 3,
                border: "1px solid #888",
                cursor: "pointer",
                background: selectedTags.includes(tag) ? "#2563eb" : "#f3f4f6",
                color: selectedTags.includes(tag) ? "#fff" : "#111",
              }}
            >
              {tag}
            </button>
          ))}
        </div>
      </div>

      {/* Free-text note */}
      <div className="tag-panel__note" style={{ marginBottom: "0.75rem" }}>
        <label style={{ display: "block", fontWeight: "bold", marginBottom: 4 }}>
          Note
        </label>
        <textarea
          rows={3}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Free-text observation..."
          style={{ width: "100%", boxSizing: "border-box", padding: "4px 8px" }}
        />
      </div>

      {/* Author */}
      <div className="tag-panel__author" style={{ marginBottom: "0.75rem" }}>
        <label style={{ display: "block", fontWeight: "bold", marginBottom: 4 }}>
          Author
        </label>
        <input
          type="text"
          value={author}
          onChange={(e) => setAuthor(e.target.value)}
          placeholder="your-handle"
          style={{ width: "100%", boxSizing: "border-box", padding: "4px 8px" }}
        />
      </div>

      {/* Feedback */}
      {error && <p style={{ color: "red", margin: "4px 0" }}>{error}</p>}
      {success && <p style={{ color: "green", margin: "4px 0" }}>{success}</p>}

      {/* Save button */}
      <button
        onClick={handleSave}
        disabled={saving}
        style={{
          padding: "6px 16px",
          background: "#16a34a",
          color: "#fff",
          border: "none",
          borderRadius: 3,
          cursor: saving ? "not-allowed" : "pointer",
        }}
      >
        {saving ? "Saving…" : "Save Annotation"}
      </button>

      {/* Existing annotations */}
      {existingAnnotations.length > 0 && (
        <div className="tag-panel__existing" style={{ marginTop: "1.25rem" }}>
          <h4 style={{ marginBottom: 6 }}>Saved Annotations ({existingAnnotations.length})</h4>
          {existingAnnotations.map((a, i) => (
            <div
              key={i}
              style={{
                padding: "6px 10px",
                border: "1px solid #ddd",
                borderRadius: 3,
                marginBottom: 6,
                fontSize: "0.875rem",
              }}
            >
              <div>
                <strong>{a.author}</strong>{" "}
                <span style={{ color: "#888" }}>
                  {new Date(a.created_at).toLocaleString()}
                </span>
              </div>
              {a.tags.length > 0 && (
                <div style={{ marginTop: 2 }}>
                  {a.tags.map((t) => (
                    <span
                      key={t}
                      style={{
                        display: "inline-block",
                        marginRight: 4,
                        padding: "1px 6px",
                        background: "#e0e7ff",
                        borderRadius: 2,
                        fontSize: "0.8rem",
                      }}
                    >
                      {t}
                    </span>
                  ))}
                </div>
              )}
              {a.note && <p style={{ margin: "4px 0 0" }}>{a.note}</p>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
