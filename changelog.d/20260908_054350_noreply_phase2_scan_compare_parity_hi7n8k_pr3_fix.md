### Fixed

- **`compare`'s pattern/preprocessor pre-scan report sections (Phase 2b)
  now version, cover, and render honestly across every mode.**
  `REPORT_SCHEMA_VERSION` bumped 3.12 → 3.13 (both `compare_report.
  schema.json` copies now describe the per-side `coverage` block the
  previous round added); a `preprocessor_prescan` side whose `clang -E`
  coverage is `partial` (some probes failed, or the scan's own probe cap
  truncated it) now renders a visible partial-coverage warning instead of
  reading identically to a clean, fully-scanned side; `--report-mode
  leaf`/`root-cause` now render both pre-scan sections too (previously
  full-mode-only); and a `pattern_prescan` side now distinguishes, in its
  rendered message, three previously-collapsed reasons nothing was
  scanned: no headers/`--sources` were supplied, a `--since`/
  `--changed-path` seed resolved to a real, valid, empty scope by design,
  or the supplied inputs were unreadable/unscannable. Two follow-up rounds
  (Codex review, PR #1169): first, a seeded `--since`/`--changed-path` run
  that selected candidate files which were all unreadable
  (`files_skipped > 0`) was misclassified as `empty_seed` (a real, valid,
  empty-by-design scope) rather than `unreadable_inputs` (a genuine
  acquisition failure) -- `_pattern_scan_scope_reason` now consults the
  scan's own `files_skipped` count instead of trusting `seeded` alone.
  Second, `--format review`'s digest (`build_review_digest_document`) never
  called either pre-scan renderer at all -- both are leaf/root-cause/full-
  mode-only -- so an `unreadable_inputs` pattern-scan side or a `partial`
  preprocessor-scan side could vanish entirely from the one GitHub-facing
  summary a reviewer approves a merge from. `reporter_markdown.
  compute_review_digest` now folds a dedicated warning for each into
  `coverage_warnings` (silent for the ordinary `no_inputs`/`empty_seed`
  cases, which every other report view already covers via each side's own
  `coverage` block). Third follow-up round (Codex review, PR #1169, second
  round, fresh evidence): the second round's own review-digest fix was
  itself incomplete on two axes. The pattern-scan warning silently dropped
  `empty_seed` (a real, valid, by-design 0-file scope -- still worth a
  reviewer knowing the diff's own lexical surface wasn't covered) and never
  examined `coverage.status == "partial"` at all (a side with
  `files_scanned > 0` AND `files_skipped > 0` reads `scope_reason: null`,
  since real coverage exists, but is still not fully covered); both now
  surface a warning, and only the structural `no_inputs` case (no headers/
  `--sources` supplied at all) stays silent. The preprocessor-scan warning
  only recognized `coverage.status == "partial"`, missing the case where
  clang and build evidence are both available but *every* `clang -E`
  invocation failed (`PreprocessorScanResult.all_failed`, `coverage()`
  returns `not_collected` with `ran: True`) -- now also surfaced,
  distinguished from the ordinary never-attempted `ran: False` skip (which
  stays silent). The `pattern_prescan` schema description's own closing
  sentence ("An all-zero side means nothing was in scope to scan, not a
  failure") directly contradicted `unreadable_inputs`'s own documented
  meaning a few sentences earlier -- qualified to state both directions
  (both schema copies). Fourth follow-up round (Codex review, PR #1169,
  third round, fresh evidence), two more gaps: (a) `pattern_scan.
  iter_source_files` silently drops a supplied root that is neither a file
  nor a directory *before* ever incrementing `files_skipped`, so a seeded
  run whose selected header/source path is missing/deleted read
  `files_scanned == 0, files_skipped == 0` -- indistinguishable from a real
  empty diff by the third round's own `files_skipped`-based check.
  (b) the `pattern_prescan`/`preprocessor_prescan` per-side objects were
  still typed as unconstrained `additionalProperties: true` objects in the
  schema despite the description documenting a `coverage` block and a
  `scope_reason` enum -- a consumer validating against the 3.13 schema
  could not actually catch a malformed value on either field. Added
  `$defs/pattern_prescan_side`/`preprocessor_prescan_side` typing every
  public field (still `additionalProperties: true` for forward
  compatibility). Fifth follow-up round (Codex review, PR #1169, fourth
  round, fresh evidence): (a)'s own fix (an existence check inside
  `_pattern_scan_scope_reason`) was itself only a partial, consumer-side
  patch -- it can only fire when `files_scanned == 0`, so a side supplying
  *several* roots where one is missing and another scans successfully
  (`[existing.hpp, missing.hpp]`) still read as a clean, fully-covered
  `present` row, since the missing root never incremented `files_skipped`
  at all. Fixed at the true root cause instead: `pattern_scan.scan_files`
  itself now counts a supplied root that doesn't exist as skipped, the
  same way it already counts an unreadable *found* file -- so every
  consumer of `PatternScanResult.coverage()` (the Markdown/JSON pre-scan
  sections here, and `scan`'s own pre-existing text output) inherits the
  fix uniformly, and `_pattern_scan_scope_reason`'s own now-redundant
  existence check was removed. The process-pool path was split into a new
  `_scan_files_parallel` helper so the accounting has one shared return
  value to adjust regardless of which path (serial/parallel/fallback) ran.
  Sixth follow-up round (Codex review, PR #1169, fifth round, fresh
  evidence): the fifth round's own producer-level fix surfaced a `partial`
  `coverage.status` correctly to the review digest, but `--report-mode`
  full/leaf/root-cause Markdown's own per-side line
  (`_pattern_side_markdown_line`) never examined `coverage.status` at
  all -- a side with a missing sibling root still rendered as plain file/
  fact counts, indistinguishable from a fully-covered scan, in every mode
  except the review digest. Now mirrors `_preprocessor_side_markdown_
  line`'s own pre-existing `partial` handling (a visible "⚠️ partial
  coverage (...)" suffix). Seventh follow-up round (Codex review, PR #1169,
  sixth round, fresh evidence): a `changed_paths` entry naming a deleted/
  renamed file beneath an EXISTING root is the same acquisition-failure
  shape the fourth round's fix closed for a missing *root*, but invisible
  to a root-existence check alone -- `roots=[src]` (exists) +
  `changed_paths=["src/deleted.hpp"]` (doesn't exist under it) previously
  read `files_skipped == 0`, misclassified as a real, valid empty diff.
  `iter_source_files` split into `_discover_candidate_files` (the
  unfiltered walk) plus `_iter_source_files_with_unresolved` (filters it
  AND counts `changed_paths` entries matching no real candidate anywhere
  under the roots) so `scan_files` gets both the filtered file list and the
  unresolved count from one walk, not two; an empty (but non-`None`)
  `changed_paths` -- the real, valid `empty_seed` scope -- has zero
  entries to be unresolved, so it is correctly left alone. **Reverted**
  (Codex review, PR #1169, seventh round, fresh evidence): the seventh
  round's own "unresolved" mechanism above was unsound. `changed_paths` is
  the WHOLE PR diff's file list, not a pre-filtered subset -- an ordinary
  out-of-scope entry (`README.md`, a `.cpp` file outside `roots`, anything
  outside `_is_scannable`'s suffix set) never matches any candidate either,
  which is the normal case for every OTHER file in a real multi-file diff,
  so most real PRs would have false-positived a partial-coverage/
  acquisition-failure warning. A second, independent bug in the same
  mechanism: a root named as both a root AND a matching `changed_paths`
  entry double-counted the identical missing file. Per this repo's own
  "attempted twice, reverted twice" discipline for a heuristic that keeps
  finding one more counterexample, `_discover_candidate_files`/
  `_iter_source_files_with_unresolved` were removed and `iter_source_files`
  restored to its fourth-round shape; its own docstring now documents the
  narrow residual (a `changed_paths` entry naming a file deleted from
  within an otherwise-existing, in-scope root reads as a real, valid empty
  diff rather than an acquisition failure) as an accepted, deliberately-
  not-fixed gap rather than attempting a third heuristic. Eighth follow-up
  round (Codex review, PR #1169, eighth round, fresh evidence), two more
  gaps: (a) the fourth round's missing-root check was itself unconditional,
  so a missing root the seed's own `changed_paths` filter would never have
  selected anyway (`roots=[missing.hpp]`, `changed_paths=
  ["different.hpp"]`) was still counted, misreporting a real, valid
  `empty_seed` as `unreadable_inputs`. Exempted only for a root with an
  unambiguous known source/header suffix (`SOURCE_SUFFIXES`) that the
  changed-path filter would exclude -- an ambiguous no-suffix root (the
  `sources` directory) stays unconditionally counted, since exempting it
  the same way would silently swallow a genuine directory-acquisition
  failure. (b) a directory root that exists but whose `os.walk` traversal
  hits an `OSError` (permission denied, a broken mount) partway through
  reads identically to "this directory genuinely contains nothing in
  scope," since `os.walk`'s default error handling silently swallows such
  errors. Investigated and documented as an accepted, deliberately-not-
  fixed gap (same docstring) rather than fixed, since a sound fix needs
  either a second, duplicate directory walk purely for error detection or
  the same kind of return-shape restructuring the reverted sixth-round fix
  attempted -- disproportionate to an adversarial-only, OS-level failure
  mode a best-effort advisory pre-scan (ADR-035 D2/D3) already degrades
  gracefully from. Ninth follow-up round (Codex review, PR #1169, ninth
  round, fresh evidence): `changed_paths` is typed `Iterable[str] | None`,
  permitting a one-shot generator; the eighth round's own missing-root
  accounting consumed it once (building `changed_suffixes`) before passing
  the SAME, now-exhausted object to `iter_source_files`, silently excluding
  every real candidate even when the generator genuinely named one.
  `scan_files` now materializes `changed_paths` into a list once, up
  front, so both consumers see the same entries. Tenth follow-up round
  (Codex review, PR #1169, tenth round, fresh evidence): the eighth
  round's suffix-based exemption only resolves ambiguity for a NON-empty
  changed-path set; a genuinely empty (but non-`None`) `changed_paths`
  selects nothing at all, unambiguously, so a missing, no-suffix
  directory root under `changed_paths=()` still read `unreadable_inputs`
  instead of `empty_seed`. Added a short-circuit exemption for a truly
  empty changed-path set before the suffix heuristic runs. Eleventh
  follow-up round (Codex review, PR #1169, eleventh round, fresh
  evidence), two more gaps: (a) `prescan_layer_coverage` only typed
  `layer`/`status`/`confidence`/`detail`, leaving `LayerCoverage.
  to_dict()`'s other unconditionally-emitted fields (`elapsed_s`/
  `requested_roots`/`resolved_roots`/`transitive_targets`/
  `compile_units`/`link_units`) unconstrained despite the newly-versioned
  coverage shape advertising them. Now typed. (b) the missing-root
  accounting loop didn't dedupe `roots` the way `iter_source_files`
  dedupes existing candidates into a `set[Path]`, so the same missing
  file listed twice (duplicate `--header` values, a directly-constructed
  API input) inflated `files_skipped` by however many times it repeated.
  Added a `seen_roots: set[Path]` guard. Twelfth follow-up round
  (Codex review, PR #1169, twelfth round, fresh evidence), two more
  findings: (a) `docs/use/output-formats.md`'s worked JSON example
  hard-coded the current `"report_schema_version": "3.13"` literal even
  though `abicheck.schemas.REPORT_SCHEMA_VERSION` is the canonical fact
  owner -- recreating the exact doc/schema drift class this PR exists to
  fix. Replaced with a `"<REPORT_SCHEMA_VERSION>"` placeholder plus an
  explanatory paragraph pointing back at the real constant. (b) the
  eighth round's `SOURCE_SUFFIXES`-based missing-root exemption only
  resolves the file-vs-directory ambiguity for a root carrying a known
  header/source suffix -- a missing, extensionless, explicit FILE root
  (the libstdc++-style header shape `_is_scannable` already documents as
  legitimate, e.g. `include/mylib/Core`) has no suffix to match, so it
  still falls through to the unconditional "ambiguous, count it" branch
  even when `changed_paths` would never have selected it. Structurally
  undecidable from the path string alone -- both a missing extensionless
  file and a missing directory are legitimate `roots` shapes, and
  resolving it needs real file-vs-directory provenance threaded through
  from the two call sites that already know it, a materially larger
  change than any fix in this chain. Per this repo's own "attempted
  twice, reverted twice" discipline, documented as a third accepted,
  deliberately-not-fixed gap in `iter_source_files`'s own docstring
  (alongside the deleted-changed-path and `os.walk`-error gaps already
  there) rather than a third heuristic patch, with a pinning regression
  test.
