### Fixed

- **`build-output.json`'s `"inferred"` evidence validator now matches a
  Windows-style `targets[].binary` path.** The `output://{basename}`
  identity `_inferred_evidence_projection_issues()` builds from a target's
  `binary` field (ADR-053 D4's Make-derived-attribution fallback) skipped
  the backslash-to-forward-slash normalization `link_attribution.py` itself
  applies before computing the identical identity, so a `binary` value
  recorded with `\`-separated path components (nothing in the schema
  forbids this) never matched — a genuinely correct Make/Windows-derived
  `"inferred"` claim would spuriously hard-fail validation (code review
  finding on PR #632).
