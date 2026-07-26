<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`scan --against` crashed with an unhandled `KeyError` instead of
  reporting `NOT_COMPARABLE`** when the default text output format hit an
  ADR-050 D2 profile/scope mismatch. `render_baseline_lines` unconditionally
  indexed `breaking`/`api_break`/`risk`/`compatible` keys that don't exist
  on the `{"reason": ...}` shape a not-comparable result actually produces
  -- only `--format json` (which builds its report a different way) was
  covered by a test. It now renders the mismatch reason instead of
  crashing, matching `--format json`'s already-correct behavior.
- **`dumper.dump()`'s new `dump_manifest` parameter (ADR-050 D3, unreleased
  -- no CLI flag exposes it yet) didn't reject a caller-supplied
  `extra_includes`** alongside a manifest, even though the manifest-driven
  parse silently ignores it (each translation unit's own `includes` field
  is authoritative instead) while the value was still fed into the
  extraction-contract fingerprint -- a fingerprint that could overstate
  what was actually compiled. Now rejected the same way `headers`/
  `public_headers`/`public_header_dirs` already are.
