### Added

- **`compare --old-bundle-facts` compares a live release directory against a
  previously captured `--bundle-facts-out` document, from the CLI.**
  `bundle_side_input.compare_release_against_bundle_facts()` was fully
  implemented and parity-tested but, per its own docstring, was deliberately
  never exposed on any CLI command — `cli_compare_release.py`/
  `cli_compare_helpers.py`, the files that would host its dispatch, both sit
  at the AI-readiness 2000-line hard cap. `compare OLD_FACTS NEW_DIR
  --old-bundle-facts` reads `OLD_FACTS` as a stored `BundleFacts` document
  instead of a live directory, renders a `mode: "bundle_facts"` JSON/markdown
  report, and exits via the same legacy verdict-based scheme as
  `compare-release`. A new `--max-json-object-nodes` option overrides the
  JSON container-node budget (`bundle_facts.DEFAULT_MAX_JSON_OBJECT_NODES`,
  1,000,000) for this path — previously the only way to raise that budget for
  a real, large, template-heavy per-library facts blob (e.g. a SYCL/DPC++
  library) was to patch the constant in a private fork; it's now a supported
  CLI flag. `compare_release_against_bundle_facts()` itself also gained a
  `suppress` parameter, forwarded to each per-library `service.
  compare_snapshots()` call — previously this driver had no way to honor a
  caller's suppression list at all, unlike every other comparison entry point
  in this codebase. `--old-bundle-facts` now also rejects `--dry-run` and
  `--contract` explicitly (exit 64) instead of silently ignoring them, merges
  `.abicheck.yml`'s `compile.include_dirs` into the NEW side's header search
  the same way every other `compare` dispatch path does, and reports a
  malformed `OLD_FACTS` document as a clean CLI error instead of a raw
  traceback. NEW_INPUT now accepts a package archive (wheel/deb/rpm/tar), not
  just a directory, extracted with the same primitive the live release
  fan-out uses — `--devel-pkg new=...` is honored the same way (its header
  root/include roots feed the NEW side's header search) and `--write
  FORMAT=PATH` now renders and writes the promised second artifact instead of
  being silently accepted and ignored. `--debug-info`,
  `--severity-preset`/`--pack`/`--exit-code-scheme`, and
  `--no-scope-public-headers` are rejected explicitly (exit 64) rather than
  silently ignored, since none of them have a channel into
  `compare_release_against_bundle_facts()`. `--depth binary` now clears the
  NEW side's headers the same way `run_compare` does, and `--depth
  build`/`--depth source` are rejected explicitly (no channel for L3-L5
  evidence in this mode). `--no-bundle-analysis` is rejected explicitly
  rather than silently ignored. `--output-dir` now writes one
  `{library}.json` report per matched library, mirroring the live release
  fan-out's own layout, with the library name sanitized to a basename (it
  originates in the OLD_FACTS document, not a path this process resolved
  itself). A package-extraction failure (a malformed archive with a
  recognized extension) no longer leaks its temporary extraction directory.
  `--sources`/`--build-info`/`--dump-manifest` and the single-pair-only flag
  family (`--used-by`, `--required-symbol`, `--use-cases`, `--env-matrix`,
  `--reconcile-build-context`, `--diagnostic-comparison`,
  `--audit-suppressions`, `--require-complete-analysis`) are now rejected
  explicitly, reusing the same guard the live release fan-out applies to a
  directory/package operand. A project config — an explicit `--config`, or
  (matching `run_compare`'s own fallback) the nearest `.abicheck.yml`
  auto-discovered upward from the current directory when no `--config` is
  given at all — whose `severity:`/`scope:`/`suppression:`/
  `exit_code_scheme:`/`debug:` blocks would otherwise be silently unapplied
  (only `compile:` reaches this mode) is now rejected too, and so are
  `--debug-format`/`--dwarf-only`/`--debuginfod`/`--debuginfod-url`/
  `--debug-root` and `--pattern-verdicts`/`--explain-patterns`/
  `--surface-metrics` as explicit CLI flags. A comparison where nothing in
  NEW_INPUT matches any library in OLD_FACTS's stored facts is now a clean
  error instead of a `NO_CHANGE` verdict for a comparison that never
  actually ran. `--probe-matrix` and `--post-manifest` are now rejected
  explicitly too, for the same reason as the other single-pair-only/no-
  channel flags above. `--pdb-path`, `--follow-deps`/`--search-path`/
  `--ld-library-path`, and `--show-only` are now rejected explicitly too --
  the first two have no channel into
  `compare_release_against_bundle_facts()` either, and `--show-only`, while
  `reporter.to_json()` does accept it directly, is rejected rather than
  implemented since the live release fan-out's own per-library `to_json()`
  calls have this identical gap and threading it through only here would
  make this driver disagree with every other release-shaped comparison
  path on what `--show-only` does. The project-config `scope:` block
  rejection now checks all four of its independent fields (`public`,
  `collapse_versioned_symbols`, `public_symbols`, `show_redundant`)
  instead of only `public` -- a config setting only one of the other three
  previously passed the check unrejected even though none of them is
  applied in this mode either. An `old=`-scoped `--header`/`--include`
  operand is now rejected explicitly too -- `_resolve_new_side_headers_
  includes` only ever reads the `new=`-scoped/uniform fields, and OLD_FACTS
  is already a resolved, stored snapshot with no header re-extraction
  available, so a requested OLD-side header/include scope was previously
  discarded rather than applied. This surface is now registered in
  `docs/_meta/topics.yaml`'s `bundle-analysis` topic (`fact_sources` +
  `reference_page`), and `docs/use/multi-binary.md`'s "Comparing against a
  stored bundle baseline" section now documents the landed `--old-bundle-
  facts` flag instead of describing it as future work.

  `--report-mode`/`--show-filtered` and an explicit, non-default `--jobs`
  are now rejected too -- the first two have no channel into the per-library
  `to_json()` calls this driver renders through (the live release fan-out
  has this identical gap on its own per-library `to_json()` calls), and
  `compare_release_against_bundle_facts()` processes matched libraries in a
  synchronous loop with no parallelism parameter for an explicit worker
  count. `--jobs`'s silent default (`0`, "auto-detect") is deliberately
  left un-rejected -- unlike every other flag here it changes only
  wall-clock time, never a finding/verdict/exit code, and is
  indistinguishable at this point from the flag never having been given.

  `--debug-info old=...`/`--devel-pkg old=...` are now rejected explicitly
  too (only the NEW-side scope of each was previously checked), a package-
  extraction failure that raises `SnapshotError` from inside extraction
  itself (not from the later comparison call) is now translated to a clean
  CLI error instead of leaking a raw traceback, and `.abicheck.yml`'s
  `source.method` (s1-s6) field is rejected the same way `--depth build`/
  `source` is (no channel for L3-L5 build/source evidence collection).
  `compare_bundle_facts.py`'s growing list of unsupported-option guards was
  split into a new sibling module, `compare_bundle_facts_rejections.py`,
  once it pushed the dispatcher itself past the architecture no-growth
  800-line cap.

  A directory (or otherwise unreadable file) given as `OLD_INPUT` now
  produces a clean CLI error instead of a raw `IsADirectoryError`/`OSError`
  traceback -- `OLD_INPUT` is a plain `click.Path(exists=True)` argument,
  not `dir_okay=False`, since the ordinary live-directory `compare` mode
  needs a directory there too. An explicit `--lang c++` is now honored
  correctly for the NEW side: `compare_release_against_bundle_facts()`
  gained a `lang_explicit` parameter (forwarded to `service.resolve_input()`,
  mirroring `run_compare`'s own `ctx.get_parameter_source("lang") ==
  COMMANDLINE` detection) -- previously an explicit request was
  indistinguishable from Click's identical default, so a language-ambiguous
  header could be silently auto-detected away from the caller's request,
  changing the extracted API and findings.

  `-o`/`--write`'s output writes now route through the shared
  `_safe_write_output()` every other CLI output path uses, instead of a
  direct `write_text()` -- creates a missing parent directory instead of
  raising an uncaught `FileNotFoundError`, and translates any other write
  failure into a clean `ClickException`. `--ast-frontend old=...` is now
  rejected too, same root cause as the other `old=`-scoped rejections
  (`--header`/`--include`/`--debug-info`/`--devel-pkg`): OLD_FACTS is
  already a resolved, stored snapshot with no header re-extraction
  available.

  `--output-dir`'s per-library artifact writes also now route through the
  same shared writer, matching the live release fan-out's own per-library
  writes. An explicit `--demangle`/`--no-demangle` is now rejected too:
  `render_bundle_findings_markdown()` (shared with the live release fan-out's
  own bundle-findings markdown section, which has this identical
  pre-existing gap) has no demangle parameter at all, so a bundle finding
  naming a C++ symbol always rendered mangled regardless of the flag. The
  silent default (demangle ON) is left un-rejected, matching the `--jobs`
  precedent.

  Separately, `run_plan.py`'s newline-join fix for `RunPlanCheck.header`
  (public_headers reaching a generated run-plan cell) missed the
  single-element case: `"\n".join([x])` for a one-item list contains no
  newline, so `action/run.sh`'s `add_flag()` still took its legacy
  whitespace-splitting branch for a lone header root containing whitespace.
  A trailing newline now forces the multi-line branch for that case too.
