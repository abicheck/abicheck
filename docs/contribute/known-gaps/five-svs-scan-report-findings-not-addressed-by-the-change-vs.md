---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Five SVS-scan report findings not addressed by the change-vs-inventory/depth/exclusion fixes (2026-09-17)

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-11-21, reproduced through the CLI with gcc + castxml): OPEN, three of five.** The header-accounting false-clean is fixed (2026-11-21) and the 294 KB Markdown default is fixed (2026-10-23). Still open, each reproduced on `compare liba.so libb.so -H include/` with byte-identical libraries: the not-applicable detector vocabulary, the partial telemetry, and the identical-binary coverage warning. See each item below.


An external reproduction of a real PR scan (SVS, Linux ELF C++, Clang 20,
two byte-identical runtime artifacts) reported nine defects against
`abicheck`'s report. Four were fixed together, because they are one
invariant -- a report may only count, and claim coverage for, what the
comparison actually observed (`tests/regressions/manifest_report.py`'s
`report.unobserved_population_counted_as_observed`): persistent hygiene
counted as this release's risk, an implicit depth reported as unanswered,
reduced source-graph evidence reported as source depth, and unmatched
`--exclude-header` rules reported as lost coverage.

The remaining five are recorded here rather than patched narrowly. Each is
a genuinely separate piece of work with its own owner, and the run that
reported them is reproducible, so none of them needs re-discovery.

- **Fixed (2026-11-21): a dropped public header no longer reads
  `complete`.** Reproduced: `compare -H include/` where castxml could not
  compile one header (`#error` under `__castxml__`) dropped it, logged a
  warning, and still reported `analysis_assurance.status: complete`.
  Cause: clang's `#error` retry and castxml's unparseable-header fallback
  record the drop under two `ast_toolchain` keys, and the assurance rollup
  read neither. The registered unobserved dimension
  `policy/analysis_assurance/_unparsed_headers.py` now reads both and
  lowers the status to `partial` with a per-side note
  (`tests/test_analysis_assurance_unparsed_headers.py`, including a CLI
  integration case). Still open from this item: `target_accounting` and
  `translation_units` stay `null` on the header-AST path (no
  selected/parsed header counts are recorded); the drop record is what now
  carries the failure signal. Also still split: other consumers read only
  one key each (`edge_query`/`dependency_scope` read clang's,
  `confidence`'s coverage warning reads castxml's); unifying them changes
  findings (`tests/test_unparseable_header_exclusion_parity.py` pins the
  castxml behaviour), so it is its own change. Original report:
  **Header coverage cannot be audited even on a `complete` run.**
  `analysis_assurance` reported `status: complete` while requested/resolved
  header roots were empty, `translation_units`'
  selected/parsed/failed/skipped counts were all `null`, the L2 elapsed
  time was zero, and no matched-header count was recorded anywhere. Nothing
  in the document demonstrates that the intended public headers were found
  and parsed. Owner: `analysis_assurance._translation_units`/
  `_target_accounting` (which already have the shape, and are simply not
  populated on the header-AST path), and the accounting must then
  *contribute* to `status` -- an unpopulated accounting block currently
  cannot lower it, which is what let `complete` stand.
- **"Not applicable" detectors are reported beside genuine coverage gaps.**
  Still open (verified 2026-11-21): a Linux ELF C run lists `kabi`, `pe`,
  `macho`, `python_ext`, `python_api` and `sycl` as `Detector '…' disabled`
  rows in `coverage_warnings` and `not_evaluated: true` in `detectors`.
  Not fixed here: it changes the `detectors`/`coverage_warnings` contract
  (schema and golden outputs) and needs the applicability rule below.
  PE, Mach-O, kABI, Python, SYCL and the DWARF family are all listed as
  detectors "not evaluated" for a Linux ELF C++ comparison where most of
  them could never apply. The vocabulary needs at least four states --
  *applicable and evaluated*, *applicable but missing evidence*, *not
  applicable to this artifact/platform*, *not requested at the selected
  depth* -- and only the second may reduce confidence or appear
  prominently. Owner: `policy/evidence_status.py` plus whatever populates
  the detector roster; note this is a classification change, so the
  applicability rule must be derived from the artifact's own container/
  language facts rather than from a hand-maintained per-detector list that
  will drift.
- **Fixed (2026-10-23): the default Markdown report was ~294 KB for a
  no-change result.** Every non-gating list is now bounded by one shared
  budget (`report/markdown_budget.py`): kinds roll up past 25 findings,
  notes show 10 rows, names are clipped and the demangle pass clips the
  demangled half while keeping the mangled spelling
  (`tests/unit/report/test_markdown_report_budget.py`). Original report:
  the default Markdown report was ~294 KB for a no-change result.
  Mostly 32 persistent findings expanded with full C++ mangled names, plus
  suppressed findings. The default CI/PR report should summarize persistent
  hygiene by family, collapse or truncate mangled symbols, cap example
  counts, and leave the complete inventory to JSON or a downloadable full
  report. The 31 `std::once_flag` guard/thunk symbols are one obvious
  grouped finding. Owner: `report/render_markdown_document.py` plus
  `report/review_groups.py` (the grouping already exists; what is missing
  is a family-level rollup for hygiene and a default cap).
- **Performance telemetry misses almost all runtime.** Still open
  (verified 2026-11-21): `evidence_metrics` has only
  `extractor.duration_seconds` (0.0035s on a run with two castxml and two
  clang parses); no phase table exists. Wall time was ~31s;
  `extractor.duration_seconds` reported 0.457s. Header discovery, AST
  parsing, graph construction, detector execution and report generation are
  all invisible. Owner: whatever publishes `evidence_metrics` -- the point
  is one phase table covering the whole run, not another individual timer.
- **Byte-identical binaries are categorized as a coverage warning.** Still
  open (verified 2026-11-21): the "old and new binaries are byte-identical"
  text is still a `coverage_warnings` entry; there is no informational
  observation channel yet. That
  the two artifacts match is useful provenance, not reduced coverage --
  especially on a run that still parsed headers, where
  `confidence.note_if_same_binary_compared` already words it as "any
  difference would have to come from the header/build evidence". It belongs
  in an informational observation channel, which does not exist yet; adding
  one is the actual work, and inventing it as a side effect of this fix
  would have been the wrong place for it. (The same run also suggests
  reusing/caching binary-side extraction when the digests match while still
  scanning headers -- a separate performance opportunity.)

Three further items from that report are **not** `abicheck` defects and are
recorded only so they are not re-filed: the 32 retained
`exported_not_public` findings are real SVS visibility hygiene (one
`svs::datatype_v<unsigned int>` instantiation, 31 `std::once_flag`
guard/thunk symbols from IVF/LeanVec) to be fixed there or classified by
policy; the missing DWARF/layout evidence is an artifact-build limitation;
and the configured header exclusions match nothing and should be removed
from the SVS integration.
