### Fixed

- **C++20 `requires`/`concept` auto-detection no longer fires on preprocessor
  diagnostics, string literals, or comments.** The header scan that decides
  whether to force `-std=gnu++20` onto a CastXML dump previously matched
  `requires`/`concept` with a naive per-line regex after only stripping
  `/* */`/`//` comments, so a line like `#error Foo requires Base` (or the
  same text inside a string literal) was misread as a genuine C++20
  requires-clause, silently forcing the wrong dialect onto both old and new
  snapshots. `abicheck/dumper_ast_config.py`'s detector now skips
  preprocessor directive lines (joining backslash-continuations first) and
  blanks out string/char literal contents before matching, and distinguishes
  concept declarations, requires-expressions, and requires-clauses with
  structured `(reason, path, line)` results (`_find_cpp20_requirements`).
- **A hidden `friend` declaration is no longer unconditionally kept in the
  public ABI surface regardless of where its befriending class lives.**
  `hidden_friend_removed`/`hidden_friend_added` findings were previously
  exempted from all surface scoping via an unconditional
  `_NEVER_FILTER_KIND_NAMES` shortcut — correct for the "can never have an
  ELF export" not-exported gate, but it also skipped header-provenance
  demotion, so a hidden friend belonging to a system- or private-header class
  was wrongly retained as a public API break. `Function.hidden_friend_owner`
  (new, schema v12) now records the befriending class's qualified name,
  populated from castxml's `befriending` attribute and clang's friend-scope
  walk; `surface.py`'s new `_classify_hidden_friend_surface` demotes the
  finding when the owner (or, as a fallback, the friend function's own
  recorded origin) is confidently a system/private header, and keeps the
  not-exported exemption only for symbol-export status.
- **CastXML below the supported version range is now rejected before any
  header is parsed, instead of silently attempted.** abicheck previously had
  no runtime floor on the CastXML version at all — only a best-effort
  advisory note appended to a *parse failure* message, and only for the
  bundled Clang major, never CastXML's own version. New `castxml_policy.py`
  defines the supported range (`>=0.7.0,<0.8.0`, bundled/linked Clang `>=18`,
  tracking the current conda-forge feedstock line) and is now enforced in
  `dumper._castxml_dump` before the cache lookup or any subprocess
  invocation; an out-of-range build (notably the legacy PyPI `castxml`
  distribution) raises `UnsupportedCastxmlVersionError` with a clear
  remediation message. Exploratory reproduction of a legacy build remains
  possible via the explicit `ABICHECK_ALLOW_UNSUPPORTED_CASTXML=1` opt-in,
  which also stamps the resulting snapshot's new (schema v13)
  `ast_toolchain_supported`/`ast_toolchain_unsupported_reasons` fields so a
  degraded scan is never silently indistinguishable from a policy-compliant
  one.
- **A BREAKING verdict with no binary-level evidence at all no longer
  produces a confident SONAME-bump recommendation.** `semver.recommend_release()`
  previously derived the SONAME action purely from `Verdict`/`ChangeKind`
  membership, with no check that the comparison ever actually examined a
  real binary (ELF/PE/Mach-O/DWARF) — a comparison of hand-built or loaded
  snapshots with only a header/declaration surface (`DiffResult.evidence_tiers
  == ["header"]`) could still get a firm "bump your SONAME." New
  `SonameAction.NOT_DETERMINED` and `ReleaseRecommendationState`
  (`actionable`/`review`/`unavailable`) make this explicit: a BREAKING verdict
  backed by real binary evidence stays `actionable` (unchanged behavior); one
  backed by header evidence only becomes `unavailable` with `not_determined`;
  an `API_BREAK` verdict (a source break with no binary-layout change by
  design) is now explicitly `review` rather than silently `actionable`. A
  `DiffResult` that never populated `evidence_tiers` (most hand-built
  `DiffResult`s in the existing test suite, and any pre-existing caller) is
  treated as "unknown" and keeps its prior actionable behavior — this only
  fires when `checker.compare()` positively determined there was no binary
  evidence. `release_recommendation.state` is now included in the JSON/SARIF
  schema and the Markdown report table.
- **The GitHub Action now forwards the full L2 compile-context to `compare`
  and `scan`, not just `dump`.** `dump`/`compare`/`scan` have shared the
  identical `--ast-frontend`/`--gcc-path`/`--gcc-prefix`/`--gcc-options`/
  `--sysroot`/`--nostdinc` CLI options for a while (`compile_context_options`,
  ADR-037 D3) — but `action/run.sh` only forwarded all six in `dump` mode,
  forwarded just `--ast-frontend` in `compare` mode behind a comment that
  incorrectly claimed the rest were "dump-only flags... not exposed on the
  compare CLI", and forwarded none of them in `scan` mode. A workflow
  scanning/comparing against a cross-compiled or non-default toolchain could
  silently get a `dump`-only cross-toolchain context while `compare`/`scan`
  fell back to the host default. `action.yml`'s input descriptions and
  `docs/user-guide/github-action.md`'s reference table are corrected to match.
