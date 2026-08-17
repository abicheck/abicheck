### Changed

- **The composite GitHub Action now renders annotations itself, and the
  CLI's `--annotate`/`--annotate-additions` flags are removed —
  CLI cleanup phase two, PR E's final slice.** `action/run.sh` reads the
  persisted `annotations` report field (schema 2.43/2.44, see the prior
  fragments in this same series) directly and prints
  `::error`/`::warning`/`::notice` workflow commands itself, gated by two
  new Action inputs: `annotate` (the always-visible entries) and
  `annotate-additions` (also the opt-in addition/quality-issue notices).
  Works identically for a single-pair `compare` and a directory/package
  (release) fan-out. `abicheck compare`/`compare-release` no longer accept
  `--annotate`/`--annotate-additions` at all — passing either now exits
  `64` with Click's own "No such option". A workflow still invoking the
  CLI directly (not through the composite Action) can read the persisted
  `annotations` array itself; see `docs/use/annotations.md`'s "Reading
  annotations without the composite Action" section.
- **A directory/package (release) `compare`'s secondary JUnit output
  (`--format junit` / `--write junit=...`) no longer re-runs any
  comparison.** Its `(DiffResult, old_snapshot)` pairs are now read
  straight from the same single primary pass that already produces the
  release's per-library report and annotations, closing a real gap the
  old independent re-run had: a rerun failure silently *dropped* that
  library from the secondary JUnit report even when the primary pass had
  already succeeded for it, producing a JUnit report that undercounted a
  successful primary one.

### Fixed

- **`abicheck.annotations.emit_github_step_summary`** (moved to
  `abicheck.annotations_step_summary` in an earlier fragment of this same
  series) is reachable again via its historical import path through a
  lazy module-level re-export shim, instead of raising `ImportError` for
  an existing caller.
- **The composite Action's `annotate`/`annotate-additions` inputs now find
  a JSON report even when the primary output format isn't JSON and the
  caller supplied their own `--write json=PATH` via `extra-args`** — that
  combination previously suppressed the Action's own internal `--write`
  injection (to avoid a losing double `--write`) with nothing left for the
  annotation renderer to read, so `annotate: true` silently emitted
  nothing. The renderer now discovers the user-supplied path directly.
