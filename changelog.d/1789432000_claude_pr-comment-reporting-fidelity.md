### Changed

- **PR comments now carry the report's own evidence and change breakdown** —
  the sticky comment renders the comparison's `confidence`, analysis depth,
  evidence sources and `coverage_warnings` beside the headline, a
  collapsed detector-applicability block that distinguishes "did not run",
  "not applicable to these artifacts" and "partial coverage", and a new
  entity-by-operation *What changed* table built from the canonical
  `ChangeKindMeta` dimensions (never a kind-name prefix rule) over the
  complete finding list, before any display cap. Two new report-owned
  modules, `abicheck/report/change_summary.py` and
  `abicheck/report/evidence_summary.py`, project these facts so every
  renderer reads one answer.
- **`pr-comment-on: changes` now also posts for a material analysis
  limitation** — a run that found no ABI/API changes but recorded
  `coverage_warnings` (e.g. *"No header/AST data; type-level changes may be
  missed"*) previously produced no comment at all, and in sticky mode
  deleted the previous one. Routine detector inapplicability (the PE/Mach-O
  detectors on an ELF run) is split out structurally and still posts
  nothing. This changes posting eligibility only — no verdict, gate or exit
  code moves.
- **Comment shortening is section-aware** — the per-section row budget is
  tightened before the detail level is downgraded, so a large report keeps
  per-symbol rows instead of collapsing to 25 grouped ones, omitted-row
  counts are exact, and a truncated body is cut on a line boundary with
  every `<details>` block closed.
- **The footer distinguishes "View workflow run" from "Download full
  report"** — the second appears only when the new
  `pr-comment-report-artifact-url` Action input (CLI:
  `--report-artifact-url`) is given, so an artifact link always corresponds
  to an upload that succeeded.

### Fixed

- **One-sided, zero, `false` and empty-string finding values reached no
  comment at any detail level** — `_detail_text` rendered `old_value`/
  `new_value` only when *both* sides were non-empty, so an added enum
  member's value `3`, a removed default's value, and every zero/false/empty
  value disappeared from the comment while remaining present in the JSON it
  was built from. Values are now rendered one-sided (`→ 3`, `3 →`), and
  "the report stated no value" stays distinct from "the value is 0".
- **Grouped rows are no longer a dead end** — standard detail rolled an API
  family up to one row listing at most eight member symbols, so thirty
  additions in one namespace lost twenty-two names and every member's
  description, location and impact. Every aggregated family now carries an
  *All grouped members* block, and a family whose findings all concern the
  same symbol is no longer aggregated at all (it rendered as
  `` `foo_init`, `foo_init` `` and dropped both findings' values).
- **Source locations no longer expose runner-specific absolute paths** —
  the Action passes `$GITHUB_WORKSPACE` through the new `--path-prefix`
  option, stripped as a whole leading path component.
