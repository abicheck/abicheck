---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Known gaps — acknowledged remaining work

This page is the full, unabridged history of every investigated-but-unfixed
gap, reverted fix attempt, and multi-round review finding this codebase has
accumulated — relocated here, verbatim, from `AGENTS.md`'s own "Known gaps"
section (2026-08-28) purely to keep that file's per-session context cost
down. Nothing was trimmed, summarized, or rephrased in the move: every
"Not fixed here," every "Reverted," every review-round citation, and every
code reference is preserved exactly as it read in `AGENTS.md`.

**`/AGENTS.md`** (imported into `/CLAUDE.md` via `@AGENTS.md`, and the
target `.github/copilot-instructions.md` points back to) remains the
canonical, primary contract for this repository — read it first. This page
is *load-bearing institutional memory*, not a changelog: per this
repository's own "Fix the cause, not the instance" and "known gaps over
risky reactive patches" conventions (see `AGENTS.md`'s "Decision-making
principles"), **read the relevant entry below before re-attempting a fix in
an area it already covers** — several entries record a heuristic or a
narrow patch that was tried, reviewed, and reverted specifically because it
looked like the obvious fix and wasn't.

---

## Known gaps — acknowledged remaining work

- **A `kind: bundle` check still cannot run at `depth: headers`: only the
  *baseline* half of per-member header staging is wired (2026-09-12).**
  `abicheck/buildsource/bundle_member_snapshots.py` now resolves and stages
  each bundle member's own historical baseline snapshot -- the per-member
  header evidence `actions/baseline` already dumps from the baseline
  checkout -- into a clean, one-snapshot-per-member directory usable as a
  directory-`compare` old-side operand, and reports per member whether that
  snapshot genuinely carries header-derived evidence
  (`AbiSnapshot.from_headers`), exposed by `actions/resolve-baseline` as the
  `member-snapshots-dir`/`member-header-evidence` outputs
  (`stage-member-snapshots: true`). `BUNDLE_CHECK_DEPTHS` and
  `actions/check-target/validate-inputs.sh` are deliberately **unchanged**:
  the restriction is a false-clean guard, and two parts of the evidence path
  are still missing, either of which alone would re-create the very
  silent-miss it prevents.
  (a) **`check-target` still routes the bundle compare at `binaries-dir`.**
  Switching it to the staged snapshots directory is not a one-line change,
  because the cross-library bundle graph (`abicheck/bundle.py`'s
  `build_bundle_snapshot()`) builds from real ELF binaries and skips
  snapshots -- so the old side would gain header evidence and lose its
  bundle-graph evidence, trading one silent narrowing for another. The
  likely shape is a baseline-time bundle-facts document staged alongside the
  member snapshots (the auto-classified `BundleFacts` operand already
  exists), so both kinds of old-side evidence survive; that is a separate
  slice.
  (b) **The candidate side still has no per-member header selection.**
  `check-project.yml` carries one project-wide `header:` input, and the
  release fan-out resolves headers run-wide
  (`cli_compare_release_helpers._resolve_release_headers`, threaded to every
  pair) rather than per member. A bundle whose members have disjoint public
  header sets would therefore parse each member's candidate side against
  the union.
  Until both land, a bundle check stays binary-depth, and staging is
  additive: it changes no existing outcome, and every pre-existing
  invocation (which does not pass `stage-member-snapshots`) is unaffected.
  Covered by `tests/test_bundle_member_baseline_staging.py`, whose central
  test proves executably -- over generated member sets, against an oracle
  independent of every abicheck detector -- that a header-derived change
  *is* detected per member once the old side comes from the staged
  baseline, and *is not* when both sides come from the candidate (today's
  behavior).

- **`unversioned_exported_symbol` (ADR-035 D8) fires on a real library's
  base/default-version-bound symbols — real, by-design, not fixed here
  (2026-09-07, cross-source-stage-automatic PR).** Found once ADR-068 §3
  row 3's first slice made `checker.compare()` run this check
  automatically: self-comparing a real, canonically GNU-symbol-versioned
  C library (`zlib1g`, exercised by `.github/workflows/realworld-
  validation.yml`'s "Ubuntu package and bundle smoke" job) reports
  `COMPATIBLE_WITH_RISK` with one `unversioned_exported_symbol` finding
  per base-API function — `readelf -V`/`objdump -T` confirm every one of
  them is legitimately bound to the anonymous/default GNU version node
  (`Base`, i.e. the library's own SONAME), the standard, permanent
  convention for a versioned library's original API: symbols present
  before the library adopted symbol versioning stay on the base version
  forever, and retroactively assigning one a named version tag would
  itself be an ABI break. `_check_unversioned_exported_symbol`
  (`buildsource/cross_source_checks.py`) cannot currently distinguish "bound to the
  base version" from "genuinely absent from `.gnu.version`" — both leave
  `ElfSymbol.version == ""` (`elf_metadata.py`'s `_apply_version_to_symbol`
  intentionally never populates `version`/`is_default` for `ver_idx < 2`,
  and `_build_verdef_index` intentionally excludes the base/`VER_FLG_BASE`
  entry from `ver_index_map`), so nothing in the persisted model lets the
  check tell them apart.

  **Why not fixed here.** A sound fix needs a real distinguishing fact
  (e.g. a new `ElfSymbol` field recording "explicitly bound to the base
  version"), which is a `model/`+`storage/` schema change (`AbiSnapshot.
  SCHEMA_VERSION` bump, `elf_from_dict`/`elf_to_dict` wiring in
  `snapshot_platform_blocks.py`) — real, scoped work, but a materially
  larger and riskier change than "make an already-migrated check
  reachable," and touching the shared `ElfSymbol.version` field's *value*
  for the base case (the cheaper-looking alternative) risks silently
  changing every other detector that already reads `version == ""` as
  "unversioned" (symbol-version add/remove detection, OLD/NEW symbol
  matching by `(name, version)`, and others) — an unbounded blast radius
  for a targeted fix.

  **Why it stands regardless.** `buildsource/cross_source_checks.py`'s own module
  docstring states the design tolerance this falls squarely inside: these
  findings "are never BREAKING on their own... default to RISK or
  API_BREAK and are advisory/suppressible until a check earns its
  FP-rate-gate corpus and is promoted" (ADR-035 D4). The smoke-test
  assertions were widened to accept `COMPATIBLE_WITH_RISK` alongside
  `NO_CHANGE`/`COMPATIBLE` rather than silently suppressing or re-gating
  the check — per this repository's standing rule against trading a
  flag for a real capability (ADR-068 D5). **Next step, if picked up**:
  the model change above, or narrowing the check to require some other
  corroborating evidence of genuine omission (unresearched; a naive
  ratio/threshold heuristic is explicitly not the fix — see this file's
  own "attempted twice, reverted twice" discipline for why an unvalidated
  heuristic is worse than a documented gap).

- **`dump -H <dir>` changed which channel carries the directory when the ELF
  `dump` CLI migrated onto the shared typed executor — recorded, deliberately
  not reverted (ADR-063 Track 1, 2026-09-05).** Found while retiring
  `cli_dump_helpers.perform_elf_dump`: the one assertion in its test suite
  that could not be rehomed, because the behaviour it pinned is no longer
  the behaviour. `perform_elf_dump` split its `-H` list with
  `header_utils.split_public_header_inputs` and routed the *directory* half
  into `dumper.dump`'s `scope_header_dirs` — folded into the extraction
  contract's scope, with ADR-015 declaration-provenance tagging
  deliberately left off (`tests/test_dumper_contract_wiring.py::
  test_scope_header_dirs_does_not_enable_provenance_tagging` still pins that
  distinction at the `dumper` level, which is where it lives). The typed
  request performs the identical split in `service_dump_pipeline.
  resolve_dump_request` but passes the directory as a real
  `public_header_dirs` entry, so a plain `dump -H include/` now *does* tag
  provenance for everything under it. `scope_header_dirs` has no typed-request
  field populating it at all any more — `api_types.py` says as much, where it
  excludes the field from the `dump_manifest` mutual-exclusivity set.

  **Why it stands.** This is convergence, not drift: `compare` has always
  treated its own `-H` list this way, and `dump`'s own
  `--public-header/--public-header-dir` pair — removed earlier precisely for
  saying the same thing a second way — is what the new behaviour matches. The
  original comment on `perform_elf_dump`'s fold gave "so a `dump --header
  <dir>` baseline and a live `compare --header <dir>` candidate of the
  identical header set agree on `scope_fingerprint`" as its whole purpose;
  both commands now reach that agreement through one shared split rather than
  two hand-maintained ones. Reverting would mean re-introducing a `dump`-only
  channel and a `dump`-vs-`compare` provenance asymmetry to preserve a
  behaviour whose own stated goal the convergence already serves.

  **What is *not* established.** Nobody has measured whether the added
  provenance tagging changes any real snapshot's classification for a
  directory operand that contains genuinely private siblings — the analogous
  hazard the `public_include_search_dirs` rule exists for (an auto-derived
  umbrella directory holding a private sibling header). The difference is
  that `-H <dir>` is an *explicit operand*, not an auto-derived one, and
  declaring a directory public is exactly what typing it means; that is the
  reasoning, not a measurement. If a real case turns up where it is wrong,
  the fix is at the split (`split_public_header_inputs`' contract, shared by
  both commands), not by restoring a per-command channel.
  `tests/test_dump_cli_execution_behaviors.py::
  test_dump_header_directory_reaches_the_extraction_scope` pins the current
  channel with this reasoning attached, so the next reader finds the decision
  rather than re-deriving it from a diff.

- **`--build-target` silently does nothing when combined with a pre-captured
  Bazel `--build-info` (an `aquery`/`cquery` jsonproto), on both `dump` and
  `scan` — investigated, not fixed (Codex review, fresh evidence, P0.2
  follow-up). Fixed (ADR-063 Phase 4, "option 2" below): the combination now
  raises a clean usage error instead of silently collecting an unscoped
  graph.** **Status note, added once this was closed:** `scan` (including
  every `scan`-specific mechanism this entry narrates —
  `scan_bazel_scoping_failure`, `ScanRequest`, `cli_scan.py`) was later
  removed outright (ADR-068 Phase 6), and `dump`'s own `--build-target` CLI
  flag was removed after it (once `scan`'s removal resolved the routing
  hazard that had deferred it). `.abicheck.yml`'s `build.targets` is now the
  *only* front-end-reachable source of root-target scoping for `dump`/
  `compare`; the historical narrative below (written while both the flag and
  `scan` still existed) is kept for the reasoning it records about the
  underlying `bazel_target_scoping_failure`/`_discovered_config_build_targets`
  mechanism, which is unchanged. `abicheck.workflows.plan.bazel_target_scoping_failure()` is the
  one check both `dump`/`compare` (via `AnalysisPlanner`, wired into
  `service_dump_pipeline.resolve_dump_request`/`service_compare_pipeline.
  resolve_compare_request`) and `scan --against`'s own candidate resolution
  (which has no `CompareRequest`/`DumpRequest` of its own to resolve through
  `AnalysisPlanner` and so calls the same free function directly) now run
  before collecting anything — `dump`/`compare`/`scan` alike raise
  `PlanningError` (framework-neutral) from the engine layer, translated to
  `click.UsageError` at each CLI boundary (`cli_resolve.py`/
  `cli_buildsource.py` for `dump`/`compare`; `cli_scan.py`'s own `scan_cmd`,
  around its `run_scan_core` call, for `scan`). `scan_engine._build_new_
  snapshot` originally raised `click.UsageError` directly instead — cheaper
  in lines, but it leaked a Click-specific exception type out of the same
  engine function the typed `run_scan(ScanRequest(...))` API calls, with no
  Click context to catch it in (a third Codex review round, fresh evidence).
  Fixed by raising `PlanningError` there instead and adding the translation
  at `cli_scan.py`'s boundary, matching the `dump`/`compare` pattern. **A
  fourth Codex review round found that placement itself was still wrong**:
  `_build_new_snapshot` runs *after* `run_scan_core`'s own S3 pattern-scan and
  points-of-interest work, so a typed `run_scan()`/`run_scan_subprocess()`
  caller — which has no `cli_scan.py` pre-flight ahead of `run_scan_core` the
  way the CLI does — paid for that (cheap but real) work before the
  rejection fired. Fixed by moving the check to the very top of
  `run_scan_core`, guarded on the same `collection_for_ci_mode(collect_mode)`
  emptiness `workflows.plan._check_bazel_target_scoping` already uses for its
  `depth=binary` exemption, and removing it from `_build_new_snapshot`
  entirely rather than leaving a second, independently-maintained copy —
  which is exactly what the removed copy was: **testing the move found the
  old `_build_new_snapshot`-only check had no `depth=binary` exemption at
  all**, a real, separate bug the earlier "scan's own path was already
  immune" note only verified for the CLI (whose `_normalize_depth_inputs`
  prunes `build_info` to `None` at that depth) and never checked for the
  typed API (`service_scan.run_scan` does not prune it). `tests/
  test_bazel_root_targets_scan.py`'s `test_run_scan_typed_api_raises_
  planning_error_not_click_usage_error` (raises `PlanningError`, not
  `click.UsageError`), `test_run_scan_rejects_before_wasted_pattern_scan_
  and_poi_work` (fires before the S3 pass, monkeypatch-verified), and
  `test_run_scan_depth_binary_exempts_the_early_bazel_scoping_check` (the
  exemption holds at the new call site) pin all three properties together.
  All three CLIs still name the mismatch and the documented workaround, exit
  64, not a silent, unscoped collection. **The same fourth round also found
  `scan --artifact-set`'s own pre-flight check (`cli_scan.py`'s
  `_run_artifact_set`) had never had the `depth=binary` exemption at all** —
  unlike the single-binary path, whose `_normalize_depth_inputs` prunes
  `build_info` to `None` at that depth before its own copy of the check runs,
  `_run_artifact_set` checked the raw, unpruned inputs directly. Fixed by
  adding the same `(depth or "").lower() != "binary"` guard used elsewhere;
  `tests/test_bazel_root_targets_scan.py::
  test_scan_cli_artifact_set_depth_binary_exempts_the_bazel_scoping_check`
  pins it.
  **A second Codex review round found this only covered `InputSpec.
  build_targets` (the `--build-target` CLI flag/typed-API field) — a root-
  target scope declared in `.abicheck.yml`'s own `build.targets:` instead
  reaches `BuildConfig.targets` through `embed_build_source`'s own CLI-
  overrides-config merge, a value none of the request-level pre-flight
  checks above can see (no config is discovered yet at that point).** Fixed
  at the one place both sources are already unified into a single value:
  `buildsource.inline._maybe_collect_bazel_build_info()` itself, which every
  `embed_build_source` caller (`dump`/`compare`/`scan` alike) goes through —
  it now raises `ValidationError` when given a non-empty `configured_targets`
  and a pre-captured jsonproto. Note this closes the *silent* half
  everywhere, but the *exit code* still varies by front end because of a
  pre-existing, deliberate design choice one layer up:
  `workflows.artifact.execute.embed_side_build_source` (the shared
  primitive `dump`'s typed request, `compare`, and `scan` all resolve
  through) already flattens any `ValidationError` from `embed_build_source`
  into `SnapshotError` ("this surface has always flattened both onto
  SnapshotError" — see that function's own comment), so those three paths
  now fail loudly with a correctly-typed error at exit 1, not exit 64;
  only the `dump --sources <tree>` (no `SO_PATH`) source-only path, which
  calls `cli_buildsource.embed_build_source`'s own adapter directly and
  was never routed through that flattening primitive, gets the full exit
  64. Changing the shared primitive's flattening behavior to recover
  exit-64 uniformly is a separate, riskier change (that comment's own
  "has always" — other `ValidationError`s reaching it likely already rely
  on the flattened exit 1) and was not attempted here. See
  `abicheck/workflows/plan.py`'s own module docstring and
  `docs/contribute/plans/one-semantic-pipeline.md`'s Phase 4 "Landed"/
  "Still not landed" notes (ADR-063 itself no longer carries a per-phase
  status entry — its duplicated status block was removed by PR 0,
  2026-09-02) for what changed and what this fix deliberately does not
  attempt (option 1 below, teaching the adapter to filter an already-parsed
  graph, remains unimplemented).
  **A third Codex review round found a remaining dry-run/execution parity
  gap for this same `.abicheck.yml` case, investigated and deliberately left
  open rather than fixed reactively.** `dump --dry-run` (`cli_dump_helpers.
  render_dump_dry_run`) never calls `collect_inline_pack`/
  `_maybe_collect_bazel_build_info` at all — a dry-run resolves and renders
  purely from `resolve_dump_request`'s `ResolvedDumpRequest`, which stops
  well before `execute_dump_request` ever reaches `embed_build_source`. So
  when the root-target scope comes *only* from `.abicheck.yml`'s
  `build.targets:` (no `--build-target` flag, the case the fix above
  covers), the dry-run preview reports success for a request the real run
  then rejects — the same shape of parity gap the earlier `scan --dry-run`/
  `scan --artifact-set --dry-run` fix (above, in the first Codex round)
  closed for the CLI-flag case, just not yet closed for the config-sourced
  one, and not yet checked on `compare --dry-run`/`scan --dry-run` either
  (neither discovers `.abicheck.yml`'s build config during their own dry-run
  preview today).
  **Fixed in a later session, closing this gap for `dump`/`compare`/`scan`
  alike.** Neither of the two designs originally floated here turned out to
  be necessary in the form stated: `AnalysisPlanner`'s own `SidePlan`
  already carries `sources` (exactly what auto-discovery needs), and
  `scan`'s `ScanRequest` already carries both `sources` *and*
  `build_config` (the explicit `--config` override — a seam `dump`/
  `compare` don't have at the request level at all, so only their
  auto-discovery half closes). `workflows.plan.bazel_target_scoping_failure`/
  `scan_bazel_scoping_failure` gained two new, defaulted keyword parameters
  (`sources`, `build_config`): when the request's own `build_targets` is
  empty, the check now falls back to whatever an explicit `build_config` or
  an auto-discovered `.abicheck.yml` at `sources` declares under
  `build.targets:` (`_discovered_config_build_targets`), reproducing
  `embed_build_source`'s own `targets=list(build_targets) if build_targets
  else cfg.targets` precedence exactly — never running the P0.3
  compile-context fold itself, just a pure, deterministic config read, so
  it fits `AnalysisPlanner`'s own side-effect-free constraint. A malformed
  `.abicheck.yml` degrades to "no config found" here (`except ValueError:
  return ()`) rather than raising a second, independently-worded error,
  since `embed_build_source` already raises a correctly-typed
  `ValidationError` for it at real-execution time. Every existing caller
  keeps passing neither parameter, so this is additive. `dump`/`compare`'s
  `--dry-run` resolve through the identical `resolve_dump_request`/
  `resolve_compare_request` chokepoint the real run does, so widening the
  check there closed their dry-run parity for free, with **no change to
  either renderer**; three of `scan`'s four pre-flight call sites
  (`scan_engine.run_scan_core` and both of `cli_scan.py`'s direct call
  sites — the single-binary pre-flight and `_run_artifact_set`'s own, each
  already running ahead of both their real-run and `--dry-run` branches)
  were each updated to forward their own already-in-scope
  `sources`/`build_config` locals. **The fourth, `service_scan.
  run_scan_set`, was deliberately left unwidened for one session**:
  `service_scan.py` sat exactly at the AI-readiness 2000-line hard cap, and
  the widened call didn't fit `ruff format`'s column budget on one line —
  the resulting explosion would have pushed the file over. Adding it to
  `LARGE_FILE_ALLOWLIST` was rejected (that allowlist is reserved for
  pre-existing `scripts:`/`tests/` debt, not a fresh production-file
  exemption for an unrelated fix); trimming unrelated content in that
  already-densely-reviewed file to buy back the budget was rejected too. So
  this one call site kept its pre-fix behavior for a while: a direct
  `run_scan_set(ScanRequest(...))` typed-API call with no CLI in front of
  it wouldn't see the `.abicheck.yml`-only scope pre-flight (it still
  failed, just later and less cleanly, inside real embedding) — `scan
  --artifact-set`'s own CLI path was unaffected throughout, since
  `cli_scan._run_artifact_set`'s pre-flight (already widened) already ran
  ahead of `run_scan_set` and caught the mismatch first.
  **Closed in a later session, by splitting `service_scan.py` rather than
  raising its baseline.** `_descendant_pgids`/`_kill_process_tree` — the
  process-group kill machinery behind `run_scan_subprocess`/
  `run_scan_set_subprocess`, with zero dependency on anything scan-specific
  — moved to the new `abicheck/workflows/scan_subprocess.py`, re-exported
  from `service_scan.py` for backward compatibility (mirroring
  `cxx20_pair_dialect.py`'s own precedent: a genuine one-directional edge,
  since the worker/harness functions that actually call back into
  `run_scan`/`run_scan_set` stayed put, avoiding the mutual-dependency
  shape a full move of the subprocess harness would have created). That
  bought back the room `run_scan_set`'s own `scan_bazel_scoping_failure`
  call needed to forward `sources=`/`build_config=` too, landing at a new,
  lowered `architecture/debt.yaml` baseline (2000 → 1933) rather than
  exactly at the hard cap. See
  `docs/contribute/plans/one-semantic-pipeline.md`'s Phase 4 "Landed"
  notes ("Second slice" / "Third slice" — ADR-063 itself no longer carries
  a per-phase status entry, its duplicated status block having been
  removed by PR 0, 2026-09-02) for the full accounting, and
  `tests/test_analysis_plan.py::TestBazelBuildTargetScoping`/`tests/
  test_bazel_root_targets.py::test_dot_abicheck_yml_build_targets_dry_run_parity`/
  `tests/test_bazel_root_targets_scan.py::
  test_run_scan_depth_headers_config_sourced_target_scope_raises_planning_error`/
  `tests/test_bazel_root_targets_scan.py::
  test_run_scan_set_config_sourced_target_scope_raises_planning_error`
  for the regression coverage (the third-to-last of these also replaces this
  paragraph's earlier pinning of the *pre-fix* `click.ClickException` leak
  from the typed `run_scan()` API with the now-clean `PlanningError`,
  raised earlier too, from `run_scan_core`'s own pre-flight check rather
  than leaking out of `_build_new_snapshot`'s pre-existing `except
  AbicheckError` wart — that wart itself is unrelated and stays open, see
  this entry's earlier notes on it). **Phase 4 is now complete: all four
  pre-flight call sites forward `sources=`/`build_config=`.**
  **A later Codex/CodeRabbit review round found the `.abicheck.yml` fix
  itself had a second, distinct gap — not deferrable the way the dry-run
  parity gap above was, since this one had a direct, bounded fix.** At
  `--depth headers`, `embed_build_source`'s own real check never runs at all
  (that depth's `collect_mode` is `"off"`, the identical condition the
  `depth=binary` exemption elsewhere in this entry relies on) — but
  `buildsource.l2_seed`'s three L2-seed/compile-context helpers
  (`derive_l2_include_dirs`, `derive_l2_compile_context`,
  `seed_includes_and_fold_compile_context`) each run their *own*,
  independent `collect_inline_pack` call regardless of `collect_mode` (they
  exist to seed useful `-I` dirs and fold build context into the header
  parse even for a headers-only scan), each wrapped in a broad best-effort
  `except Exception` that swallows any failure and degrades to "no seeded
  context" — by design, documented in `_l2_seed_config`'s own docstring ("a
  malformed/invalid config surfaces loudly elsewhere ... this is a
  best-effort include-dir hint"). That documented assumption is false for
  this one input: at `--depth headers`, this L2-seed call is the *only*
  place the Bazel-scoping `ValidationError` can fire at all, so the broad
  catch silently swallowed it, with the run proceeding with no diagnostic
  and without the build-derived context it should have used. Fixed by
  extending the file's own pre-existing carve-out for
  `HeaderCompileContextAmbiguousError` (raised for the identical reason — a
  deliberate, fail-closed error is not "best-effort collection failed") to
  cover `ValidationError` too, in all three helpers. `tests/
  test_bazel_root_targets_l2_seed.py::
  test_seed_includes_and_fold_compile_context_raises_on_bazel_scoping_mismatch`
  reproduces it end-to-end with a real (unmocked) pre-captured jsonproto and
  a `.abicheck.yml`-only (no `--build-target`) target scope, pinning that it
  now raises instead of degrading silently. **Verified against `scan`
  specifically (a sixth review round asked for it by name, for the typed
  `run_scan(ScanRequest(depth="headers", ...))` shape)**: the silent
  `COMPATIBLE`/exit-0 outcome is gone — the request now fails loudly with the
  same clear diagnostic — but the exit code inherits the identical front-end
  variance already noted above for the sibling `embed_build_source` case, via
  a different, independently pre-existing mechanism: `scan_engine.
  _build_new_snapshot`'s own `except AbicheckError: raise click.
  ClickException(...)` (documented in this module's own header as a
  pre-existing wart predating ADR-063 entirely, deliberately left alone) maps
  the `ValidationError` to exit 1 for the CLI, and — since `click.
  ClickException` is not itself caught anywhere further out — leaks that same
  Click-specific exception type to a typed `run_scan()` caller too. Fixing
  that leak means touching the same pre-existing, out-of-scope `except
  AbicheckError` clause the module docstring already flags for a future
  cleanup, not a new regression this phase introduced — left alone here for
  the same reason.
  **A seventh review round found that "verified" claim itself rested on a
  false premise, and had a real, bounded fix — unlike the sibling
  `click.ClickException` leak just above.** `run_scan_core`'s own early
  pre-flight check (added for the "run scan planning before pattern and POI
  extraction" fix elsewhere in this entry) exempted `--depth headers` the
  same way it exempts `--depth binary`, on the theory that both resolve to a
  `collect_mode` with no collection layers. That theory is only half
  right: `--depth binary` *also* clears the header list to empty
  (`service_scan.run_scan`'s `eff_headers = [] if eff_depth is
  EvidenceDepth.BINARY else ...`, mirrored by the CLI's own
  `_normalize_depth_inputs`), which is the actual reason `build_info` is
  never consulted anywhere for that depth — `--depth headers` keeps real
  headers, so the L2-seed's own independent `build_info`-consuming pass
  (the one this whole round's fix concerns) still runs, and the early check
  was wrongly skipping it. Concretely: `run_scan(ScanRequest(depth=
  "headers", build_targets=("//:lib",), build_info=<precaptured jsonproto>))`
  reached the `.abicheck.yml`-only code path's own `click.ClickException`
  leak instead of the framework-neutral `PlanningError` an *explicit*
  `build_targets` should get. Fixed by widening the exemption from
  `collection_for_ci_mode(collect_mode)[1]` alone to `headers or
  collection_for_ci_mode(collect_mode)[1]` — exempt only when *neither*
  consumer (`embed_build_source` nor the L2 seed) can reach `build_info` at
  all. This closes the gap for every case `AnalysisPlanner`/`run_scan_core`'s
  own inputs can see (an explicit `--build-target`/`ScanRequest.
  build_targets`); the `.abicheck.yml`-only case above is unaffected by this
  fix and remains the already-documented `click.ClickException` leak, since
  neither `run_scan_core` nor `AnalysisPlanner` can see a value that
  isn't discovered until deep inside `collect_inline_pack`.
  `tests/test_bazel_root_targets_scan.py::
  test_run_scan_depth_headers_with_explicit_build_target_raises_planning_error`
  pins the fixed (explicit `build_targets`) case; the pre-existing
  `test_run_scan_depth_headers_still_rejects_bazel_scoping_mismatch` was
  re-verified to still exercise the unaffected (`.abicheck.yml`-only) case
  correctly.
  **An eighth review round found the same class of gap on the plural entry
  point.** `service_scan.run_scan_set` (`scan --artifact-set`'s typed API)
  had no Bazel-scoping pre-flight of its own: an unsupported request
  reached each member's own `run_scan_core` check only *after*
  `discover_artifact_set()`, `check_artifact_set_soname_collisions()`, and
  `artifact_set_member_exports()` had already run for every member, wasting
  real discovery/parsing work before the request was ultimately rejected
  anyway. Fixed by adding the same pre-flight check to the top of
  `run_scan_set`, right after `_reject_comparison_only_fields(req)` and
  before the shared budget clock starts or `discover_artifact_set()` runs.
  Rather than duplicate `run_scan_core`'s exemption logic (the depth=binary
  header-clearing rule two findings above) a second time, both call sites
  now share one function, `workflows.plan.scan_bazel_scoping_failure()`, so
  a future refinement to the exemption rule has exactly one implementation
  to change instead of two independently-maintained copies.
  `tests/test_bazel_root_targets_scan.py::
  test_run_scan_set_rejects_bazel_scoping_mismatch_before_discovery` pins
  the fix by asserting `discover_artifact_set()` is never even called for a
  mismatched request; `test_run_scan_set_depth_binary_exempts_the_early_
  bazel_scoping_check` pins the sibling depth=binary exemption for the
  plural entry point.
  **A ninth review round found that raw depth alone is not the whole story
  for `dump`/`compare`'s own `AnalysisPlanner` check either.**
  `_check_bazel_target_scoping` exempted `depth="binary"` purely by reading
  the request's raw, requested depth — but `DumpRequest.resolved_collect_mode`
  (a private CLI hook: `compare`'s own implicit-dump path resolves collect
  mode from the *pair* and forwards it in, so the real run doesn't re-derive
  a possibly-different mode from `depth` in isolation), when set, overrides
  what `depth` alone would resolve to, and `resolve_dump_request_evidence`
  honors that override. A `DumpRequest(depth="binary",
  resolved_collect_mode="build", ...)` therefore still runs
  `collect_inline_pack` for real at execution time — the override, not the
  raw depth, decides whether `build_info` is ever consulted — so the
  pre-flight check's exemption on raw depth alone let an unsupported request
  reach `resolve()`, then fail later inside `collect_inline_pack` as a
  flattened `SnapshotError` instead of the promised `PlanningError`. Fixed
  by adding `resolved_collect_mode` to `SidePlan` (populated only for `dump`
  sides — `CompareRequest` has no such field, so every `compare` side keeps
  `None` and this changes nothing for `compare`) and checking it first: when
  set, it alone decides the exemption (`"off"` exempts, anything else
  doesn't, regardless of raw depth); only when unset does the check fall
  back to the pre-existing raw-depth-only rule. `tests/test_analysis_plan.py::
  TestBazelBuildTargetScoping::test_resolved_collect_mode_override_defeats_the_binary_exemption`
  pins the fixed case; its sibling
  `test_resolved_collect_mode_off_override_is_exempt_even_at_other_depths`
  pins the converse (an explicit `"off"` override exempts even at a depth,
  e.g. `"build"`, that the raw-depth-only rule would otherwise reject).
  **A tenth review round found the eighth round's fix (moving `run_scan_set`'s
  own check before discovery) had a CLI-level sibling gap.**
  `cli_scan._run_artifact_set` (`scan --artifact-set`'s own CLI entry point)
  has its own pre-flight check, separate from `run_scan_set`'s — but it ran
  only *after* `_resolve_artifact_set_paths()`/`discover_artifact_set()` had
  already traversed a directory and statted/format-validated every explicit
  member. An invalid member's own error could therefore mask the request's
  intended `PlanningError`/usage error, and a mismatched request paid for
  real discovery work before `run_scan_set`'s own (already-fixed) check
  rejected it anyway. Fixed by moving the check to the very top of
  `_run_artifact_set`, before any discovery work starts — every value the
  check needs (`depth`, `build_info`, `build_targets`) is already a raw
  function parameter, so no reordering of the rest of the function was
  needed. `tests/test_bazel_root_targets_scan.py::
  test_scan_cli_artifact_set_rejects_bazel_scoping_mismatch_before_discovery`
  pins it, the CLI-level sibling of the eighth round's own
  `test_run_scan_set_rejects_bazel_scoping_mismatch_before_discovery`.
  **An eleventh review round found the ninth round's own fix (the
  `resolved_collect_mode` override) was itself incomplete.** Even a genuine
  `"off"` collect mode — whether from raw `depth="binary"` or from an
  explicit `resolved_collect_mode="off"` override — does not by itself mean
  `build_info` is never consulted: the L2 seed's own independent
  header-seeding pass (`_seeded_includes_and_compile_context`/
  `collect_inline_pack`) still runs whenever real headers are present,
  regardless of collect mode — the identical class of gap the seventh round
  above already fixed for `scan_bazel_scoping_failure` (`headers or
  collection_for_ci_mode(...)[1]`), just not yet ported to `dump`/`compare`'s
  own check. Fixed by adding the side's raw `headers` to `SidePlan` and
  exempting `"off"` only when there is no header-seeding consumer:
  `depth="binary"` still clears headers to empty independent of any
  override (`service_compare_evidence._headers` keys off raw depth alone),
  so that clearing is folded into the effective-headers computation rather
  than re-derived from collect mode. `tests/test_analysis_plan.py::
  TestBazelBuildTargetScoping::test_resolved_collect_mode_off_does_not_exempt_real_headers`
  pins the fixed case (an explicit `"off"` override with real headers and a
  scoped pre-captured Bazel jsonproto); its sibling
  `test_resolved_collect_mode_off_with_no_headers_stays_exempt` pins that the
  headers check doesn't over-reject a genuinely headerless request.
  **A twelfth review round found the eleventh round's own fix introduced a
  new false positive it didn't have before.** The no-override branch only
  ever equated collect mode `"off"` with `depth="binary"` -- but
  `depth="headers"` resolves to `"off"` too (`_resolve_depth_collect_mode`'s
  mapping: only `"build"`/`"source"` resolve to something else). A
  headerless `dump`/`compare` request at `depth="headers"` combined with an
  explicit `build_targets` and a scoped pre-captured Bazel jsonproto was
  therefore wrongly rejected: neither `embed_build_source` (collect mode
  `"off"`) nor the L2 seed (nothing to seed, no real headers) would ever
  have consulted `build_info`, yet the planner still raised `PlanningError`.
  Fixed by adding `_depth_implied_collect_mode()` -- mirroring
  `service_compare_evidence._resolve_depth_collect_mode`'s explicit-depth
  mapping (duplicated, not imported, for the same leaf-module reason that
  function's own docstring states) -- and using it for any explicit depth
  in the no-override branch, rather than special-casing `"binary"` alone.
  `depth="headers"` with *real* headers still correctly stays rejected,
  since only `"binary"` clears headers to empty before this check runs;
  `"headers"` keeps them, so the L2 seed still runs.
  `tests/test_analysis_plan.py::TestBazelBuildTargetScoping::
  test_headerless_depth_headers_is_exempt` pins the fixed case; its sibling
  `test_depth_headers_with_real_headers_is_still_rejected` pins that real
  headers at that same depth still correctly reject. The pre-existing
  `test_other_depths_still_rejected_alongside_binary` was narrowed from
  `("headers", "build", "source")` to `("build", "source")` accordingly --
  `"headers"` alone (headerless) is no longer part of the always-rejected
  set, which is the corrected behavior this round establishes, not a
  weakening of the test.
  **A thirteenth review round found `scan --artifact-set`'s own pre-flight
  (`cli_scan._run_artifact_set`) had a second, narrower version of the same
  false-positive/false-negative pair, specific to an *unset* `--depth`.**
  Two intermediate designs were tried and rejected in this same round before
  landing on the fix below: a bespoke
  `workflows.plan.artifact_set_bazel_scoping_failure` that treated an
  unset `--depth` as always non-`"off"` (matching every prior caller's
  shape) false-positive-rejected a genuine no-op artifact-set request whose
  real per-member risk scoring would resolve to `"off"`; a second attempt
  that instead treated an unset `--depth` as always exempt
  false-negative-accepted a seeded, high-risk request (e.g. a public-header
  edit) that `run_scan_core`'s own later, correctly-resolved per-member
  check would still reject with exit 64 -- recreating the exact dry-run/
  execution parity defect this whole known-gap entry exists to close, just
  one level narrower (real-run vs. real-run instead of dry-run vs.
  real-run). Both were symptoms of approximating a value `AnalysisPlan`'s
  own design (`workflows/plan.py`'s module docstring) deliberately excludes
  from a pre-flight check: an unset `--depth` only resolves to a real
  `collect_mode` via risk scoring over the request's own seeded change
  (`service_scan._resolve_member_scan_level`, the identical primitive
  `estimate_artifact_set`'s own `--dry-run` cost totals already resolve
  through). The fix drops the approximation entirely: `_run_artifact_set`
  now builds the same probe `ScanRequest` `estimate_artifact_set` would and
  calls `_resolve_member_scan_level` on it to get the real `eff_depth`/
  `collect_mode` before discovery, then hands those to the existing,
  already-shared `scan_bazel_scoping_failure` -- no bespoke artifact-set-
  shaped guard needed. `_resolve_member_scan_level` reads only
  request-level fields (`depth`/`changed_paths`/`seeded`/`risk_rules_path`/
  `mode`), none of them derived from discovery, so this probe needs no
  `binaries` and costs nothing discovery-shaped to build. The now-dead
  `artifact_set_bazel_scoping_failure` was deleted from `workflows/plan.py`
  rather than left as an unused second copy.
  `tests/test_bazel_root_targets_scan.py::
  test_scan_cli_artifact_set_unset_depth_low_risk_seed_config_scope_is_unaffected`
  pins the no-op case (a low-risk `--changed-path`, e.g. a docs file, seeds
  `S0`/`collect_mode="off"`, so a config-sourced scope stays unenforced);
  its sibling
  `test_scan_cli_artifact_set_high_risk_seed_config_scope_still_rejects`
  pins the risky case (a high-risk `--changed-path`, e.g. a public header,
  seeds a non-`"off"` `collect_mode`, so the same config-sourced scope is
  still enforced and rejects with exit 64).
  **A fourteenth review round found the thirteenth round's own fix had no
  error translation of its own.** `_resolve_member_scan_level()` raises a
  plain `ValueError` for a malformed `--risk-rules` profile (via
  `_load_risk_rules_for_service`, which converts the single-binary path's
  `click.ClickException` into exactly that so a direct typed-API caller
  never sees a Click-flavored exception) -- but the thirteenth round's new
  resolution call sat ahead of both of `_run_artifact_set`'s existing
  `try`/`except (ArtifactSetError, ValueError)` blocks, so that `ValueError`
  now leaked past them (exit 1, an unhandled traceback) for both a real run
  and `--dry-run` alike, instead of the established clean usage error (exit
  64) `TestArtifactSetMalformedRiskRules::
  test_malformed_risk_rules_yaml_is_usage_error` already pinned for this
  exact class of input (a ninth-round-era regression, per that test's own
  docstring). Fixed by wrapping the new resolution call in its own
  `try`/`except ValueError`, translating to `click.UsageError` the same way
  the two pre-existing blocks below it already do.
  Historical analysis retained below for the record.
  `BazelAdapter.collect()`'s `self.targets` scoping is applied
  in exactly two places: gating whether a *live* `bazel query` subprocess
  runs at all (`_resolve`/`_run_bazel`, only reachable when `workspace` is
  given and no pre-captured `aquery=`/`cquery=` path was supplied), and
  populating the `TargetScope` report (`requested`/`resolved`/
  `transitive_count`) on the returned `BuildEvidence`. Neither path filters
  `ev.compile_units`/`ev.targets` themselves once a pre-captured file is
  parsed — `_collect_aquery`/`_collect_cquery` walk the *entire* captured
  action/target graph unconditionally. `buildsource/inline.py`'s
  `_maybe_collect_bazel_build_info` (the function `collect_inline_pack`
  routes a `--build-info` recognized as `bazel_aquery`/`bazel_cquery` through)
  doesn't even accept a `targets` parameter, so `--build-target` isn't merely
  unenforced here — it's never threaded to this call site at all, and no
  diagnostic or `TargetScope` records that a scope was requested. Confirmed
  by reading the code (no live Bazel repro run in this pass): `scan
  --build-info saved-aquery.json --build-target //:lib` (or the identical
  `dump` invocation) collects every TU in the captured workspace, with no
  error, warning, or `TargetScope` entry showing the mismatch between what
  was requested and what was actually scoped — unrelated targets can pollute
  L3 evidence and any detector built on it. **Not fixed here, and the two
  candidate fixes are not equally easy:** (1) *Actually filter* the captured
  graph to the transitive closure of the requested roots. Feasible in
  principle for `cquery` data, whose `Target.dependencies` already carries a
  real label-to-label dependency edge list (`attrs.get("deps", ...)` in
  `_collect_cquery`) a BFS could walk — but a `cquery`-only capture produces
  no `compile_units` at all (only `_collect_aquery` does), so this alone
  doesn't scope the TUs that actually matter. `aquery` data has no equivalent
  target-level `deps` list — only a flat action list, each tagged with its own
  `targetId` (`CompileUnit.target_id`) — so a correct closure would have to be
  reconstructed from the action graph's own input-artifact edges
  (`_AqueryGraph`'s depset walk), a materially different and more involved
  algorithm than the `cquery` case, not a shared implementation. (2) *Reject*
  the combination with a clear usage error instead — architecturally cleaner,
  but `collect_inline_pack`/`_maybe_collect_bazel_build_info` sit in a shared
  Tier-2 module used by both CLI and typed-API callers (`embed_build_source`,
  in turn called from `cli_dump_helpers.py`, `scan_engine.py`, and
  `service_input_resolution.py`), and raising there can only be a plain
  `ValueError`/`AbicheckError` (per this codebase's Tier-1/Tier-2 separation —
  Click-specific exceptions belong to the CLI layer only). Neither `dump_cmd`
  nor `scan_cmd` currently wraps its `embed_build_source`/`run_scan_core` call
  in a catch for that error class (confirmed by reading both), so today such
  an error would propagate as an unhandled Python exception with a raw
  traceback instead of a clean `click.UsageError`/exit 64 — fixing that
  requires adding (and testing) a catch-and-reraise at every one of those call
  sites, not a single choke point. Either option is a real, multi-call-site
  feature needing its own dedicated design and test coverage, not a
  same-session reactive patch under continued review pressure — per this
  file's own "known gaps over risky reactive patches" convention. Until then,
  the safe workaround is to only combine `--build-target` with a *live* query
  (`--sources <workspace>` and no `--build-info`, letting `BazelAdapter` run
  `bazel query deps(...)` itself, which does scope correctly) or to
  pre-capture the `aquery`/`cquery` jsonproto already scoped to the desired
  targets before passing it as `--build-info`.

- **The native ELF `abicheck dump` path never applies L3 build context to its own
  L2 header parse, and this is now confirmed by an external end-to-end
  reproduction, not only by the pre-existing module-map note about the
  `DumpRequest` migration (2026-08-15, `napetrov/abicheck-bazel-lab` audit
  against `5b52989`).** The "Module map"'s `service_dump_pipeline.py` entry
  above already named the cause — "the native `dump` CLI does not build a
  `DumpRequest` yet" — but that note described a *migration gap*, not a
  concrete, reproduced *symptom*. Traced here to the exact call graph: P0.3's
  L3→L2 fold (`service_input_resolution._seeded_compile_context`, wired via
  `buildsource/l2_seed.derive_l2_compile_context` and
  `buildsource/header_compile_context.resolve_header_compile_context`) is
  real and already correct — `resolve_side_snapshot()`, the function that
  calls it, is shared by *both* `service_compare_pipeline.
  resolve_compare_request` (the path `compare`'s own implicit-dump operand
  takes) *and* `service_dump_pipeline.run_dump_request`. But
  `cli.py`'s `dump_cmd` (the ELF path) never calls `run_dump_request` at
  all — it calls `cli_dump_helpers.perform_elf_dump()` directly, a separate,
  older code path whose `CompileContext` is built only from explicit
  `--ast-frontend`/`--compiler*`/`--sysroot`/`--nostdinc` flags (confirmed by
  grep: `cli_dump_helpers.py` contains no reference to
  `resolve_side_snapshot`, `_seeded_compile_context`, or
  `derive_l2_compile_context` anywhere). The external repro: a fresh
  `abicheck dump lib.so --sources . --build-info compile_commands.json
  --depth source` snapshot's own `parsed_with_build_context` reads `false`
  and `language_standard` reads `""` even though real L3 evidence was
  supplied and is embedded in the snapshot — the evidence is collected and
  stored, but never routed to the L2 header-AST invocation, because that
  invocation runs through a path P0.3's fold was never wired into.
  Consequence: a `dump`-produced baseline and a `scan --against`
  live-binary comparison of the *same* project, given the *same* L3
  evidence, resolve to genuinely different `CompileContext`s
  (`profile_fingerprint` mismatch on `include_sequence`/
  `language_standard`), so `scan` correctly (per ADR-050 D2) refuses the
  comparison as `NOT_COMPARABLE` — not because the evidence was
  insufficient, but because the two commands extracted under
  non-comparable recipes for reasons neither command's own diagnostics
  name. **Not fixed here**: migrating `dump_cmd`/`perform_elf_dump`
  (`cli_dump_helpers.py` is already at 1914 of its 2000-line hard cap —
  real headroom for an inline fix is tight) to route through
  `run_dump_request` the way `compare`'s implicit-dump path already does
  is a genuine, cross-cutting architecture change — the "what that
  migration needs first" the module-map note already flags — not a
  same-pass reactive patch. The PE/Mach-O `dump` paths (`service.py`'s
  `_dump_pe`/`_dump_macho` mirrors) were not independently checked for the
  identical gap in this pass.

  **Closed, additively, without the `run_dump_request` migration this entry
  originally called for.** Full migration would have meant rewriting
  `perform_elf_dump`'s already-large, carefully-ordered pipeline (header
  parse, ADR-039 build-context harvest, G14/G23/G26 Python/NumPy attach,
  the header-graph and clang-layout-tool second passes) around a
  fundamentally different call shape — real, but out of proportion to the
  actual gap, which is narrowly "the L3→L2 fold never runs on this path,"
  not "this path's whole architecture is wrong." The fix is additive
  instead: `buildsource/l2_seed.fold_l3_compile_context()` — a new, shared
  wrapper around the already-correct `derive_l2_compile_context()` +
  `_merge_l3_compile_context()` pair (the latter *moved* into `l2_seed.py`
  from `service_input_resolution.py`; see that function's own docstring for
  why — leaving it in place would have closed an
  `l2_seed -> service_input_resolution -> cli_dump_helpers -> l2_seed`
  import cycle the AI-readiness gate correctly rejects once
  `cli_dump_helpers` needed it directly) — called from both
  `perform_elf_dump` (ELF) and `handle_non_elf_dump` (PE/Mach-O, which
  shared the identical gap: confirmed by reading `service.run_dump`'s own
  `compile` parameter never receiving anything beyond the CLI/config-
  resolved context either). Both call sites now fold the real L3
  `CompileUnit`-derived context (`-std=`, ABI-relevant `-D`/`-U`, target,
  sysroot) into the *same* explicit context CLI flags/`.abicheck.yml`
  already built, exactly mirroring what `_seeded_compile_context` already
  does for `compare`'s implicit-dump path via `resolve_side_snapshot` — so
  `dump` and `compare` now fold under one shared primitive, even though
  `dump`'s own CLI pipeline still doesn't route through `run_dump_request`.
  `perform_elf_dump`'s two independent second-pass clang re-parses (the
  header-graph attach and the clang-layout-tool attach) also now receive
  this same fully-merged context via `effective_compile_context =
  l3_effective_ctx`, closing a narrower, previously-undocumented sibling
  gap where those passes' own `gcc_options`-string-only re-derivation never
  looked at `gcc_option_tokens`/`sysroot`/`nostdinc`/deferred include roots
  at all — so a real disagreement on any of those between the primary
  parse and the second passes could previously have gone unnoticed even
  without any L3 evidence in play. `AbiSnapshot.parsed_with_build_context`
  is now stamped from either this fold or the older `-p`/`--compile-db`
  mechanism (OR'd, matching `resolve_side_snapshot`'s own rule), on both
  the ELF and PE/Mach-O paths.

  **A third, independent instance of the same gap, found only by testing
  the actual end-to-end repro rather than trusting this entry's original
  framing: `scan`'s own candidate resolution never applied the fold
  either.** This entry's first draft claimed `_seeded_compile_context`
  "already does" this for `scan` — false. `scan_engine._build_new_snapshot`
  calls `service.resolve_input` directly, not `resolve_side_snapshot`, so
  `compare`'s/`dump`'s fold never ran on `scan`'s candidate side. A
  same-project `scan --against` a freshly-fixed `dump` baseline still
  returned `NOT_COMPARABLE` on `language_standard` even after the two
  `dump`-path fixes above — confirmed by actually running the repro end to
  end (a real `g++`-compiled library + `compile_commands.json`), not by
  re-reading the code. Fixed the identical way: `_build_new_snapshot` now
  calls `fold_l3_compile_context` immediately before `resolve_input`, and
  stamps `parsed_with_build_context` the same way. The same real repro now
  produces `NO_CHANGE` end to end (`dump` baseline → `scan --against`),
  which is the actual acceptance criterion this whole entry exists for.

  **A fourth finding, from a Codex review of this same change, on the
  *shared* `_merge_l3_compile_context` primitive itself — pre-existing in
  the `compare`/`scan`-implicit-dump fold this PR reused, not introduced by
  it, but real and now fixed alongside it.** "Derived leads, explicit
  wins" (last-flag-wins) is the right rule for a macro/std/sysroot switch,
  but `header_compile_context._context_flags` also renders a matched
  `CompileUnit`'s own `include_paths`/`system_include_paths` as `-I`/
  `-isystem` tokens, and an include search path is *first*-match-wins —
  so putting a derived `-I` ahead of an explicit one (the pre-existing
  order) silently let the build's own header win over a caller's explicit
  override for a colliding basename. Fixed with a new
  `_split_include_tokens()` helper that carves derived's own include-search
  entries (`-I`/`-isystem`/`-iquote`/`-idirafter`/MSVC `/I`/`/imsvc`/
  `/external:I`, spaced-pair-aware) out of the leading last-flag-wins group
  and appends them *after* explicit instead — every other derived token
  keeps its original leading, overridable position.

  Verified end-to-end against a real `compile_commands.json` fixture
  through the actual `perform_elf_dump`/`handle_non_elf_dump`/
  `_build_new_snapshot` entry points (not a hand-built `CompileContext`) —
  see `tests/test_cli_dump_helpers_coverage.py::
  test_perform_elf_dump_folds_l3_compile_context_into_header_parse`,
  `tests/test_non_elf_dump_l2_seed.py::
  test_non_elf_dump_folds_l3_compile_context_into_header_parse`,
  `tests/test_scan_l2_cleanup_ordering.py::
  test_scan_candidate_folds_l3_compile_context_into_header_parse`, and
  `tests/test_header_compile_context.py`'s
  `test_merge_l3_compile_context_explicit_include_search_wins_first_match`/
  `test_merge_l3_compile_context_attached_include_form_stays_paired` —
  confirming the derived `-std=`/`-D` flags reach the primary header-AST
  parse, `parsed_with_build_context` is stamped, and an explicit `-I`
  outranks a derived one. Full test suite (28k+ tests) green; no
  import-cycle or file-size regression (`cli_dump_helpers.py` stayed under
  its 2000-line hard cap by moving the shared fold logic into `l2_seed.py`
  rather than duplicating it inline for both call sites).

  **Two more findings on the same change, both fixed, one left as a
  narrower residual gap.** (1) A derived `-I`/`-isystem` reaches the header
  parse only as an opaque `gcc_option_tokens` string, which the AST cache
  key's own directory-mtime hashing (`extra_includes`/`extra_hash_dirs`,
  `dumper_ast_config.py`) never inspected — editing a header under a
  derived include dir could reuse a stale cached AST. `fold_l3_compile_
  context()` now also returns the derived include directories
  (`_include_operand_dirs()`), threaded into `perform_elf_dump`'s existing
  `extra_hash_dirs`. **Not closed for `handle_non_elf_dump` (PE/Mach-O) or
  `scan_engine._build_new_snapshot`**: neither `dump_native_binary`→
  `service.run_dump` nor `service.resolve_input` exposes a public
  `extra_hash_dirs` hook the way `perform_elf_dump`'s own direct `dump()`
  call does — and `service.py`'s own internal cache-key computation for
  those paths already has the identical, broader, pre-existing gap for
  *any* `CompileContext.gcc_option_tokens` include entry (not just an
  L3-derived one), so a scoped fix here would still leave the general case
  open. Threading a real `extra_hash_dirs` channel through `resolve_input`'s
  public signature is a genuine, separate change affecting every caller of
  that function, not a follow-up to this one. (2) `scan`'s own fold call
  hard-coded `lang_explicit=False`; since `scan --lang c` is never the
  Click default (only `"c++"` is), it is always a genuine explicit
  request, and the hard-coded `False` let a matched C++ compile unit's own
  `-std=c++20` reach a parse `scan --lang c` was explicitly forcing into C
  mode. Fixed by treating `lang == "c"` as explicit, mirroring `perform_
  elf_dump`'s identical squash-guard rule elsewhere in this same area.

  **A fifth finding, on the fold's own call shape rather than its logic: the
  include-dir seed and the L3→L2 fold each independently collected L3
  evidence, and a caller genuinely needing the inferred build query (no
  existing compile database) could self-deadlock.** All three call sites
  (`perform_elf_dump`, `handle_non_elf_dump`, `scan_engine._build_new_
  snapshot`) called `seed_l2_includes()` and then, immediately after,
  `fold_l3_compile_context()` — each independently calling
  `buildsource.inline.collect_inline_pack()`. Harmless when at most one call
  can trigger the zero-config *inferred* build-system query (cmake/make/
  bazel, gated by `allow_inferred_build_query`/`collect_mode`), but a real
  inferred query is a `flock`-protected, deterministic per-source-tree temp
  build dir (`build_query._claim_inferred_build_dir`) held until its own
  *cleanup* runs — deliberately deferred until after the header parse
  consumes the seeded dirs, i.e. long after the seed call returns. The
  second call's own inferred-query attempt then contends on the identical
  lock the first call is still holding, and — being the *same process*
  reopening the same lock file, not a different one — `flock`'s
  per-open-file-description semantics mean this is genuine self-contention,
  not a race with some other process: it blocks for up to
  `INFERRED_QUERY_TIMEOUT_S` (600s) before falling back to a throwaway
  sibling dir (Codex review). Fixed by collapsing the two into one
  collection: `buildsource.l2_seed.seed_includes_and_fold_compile_context()`
  runs `collect_inline_pack()` exactly once and derives both the include-dir
  seed (mirroring `seed_l2_includes`'s own gating and directory derivation)
  and the compile-context fold (mirroring `derive_l2_compile_context`'s
  resolution + merge) from that single `BuildEvidence`, so only one inferred
  query — if any — ever runs per call. All three call sites now call this
  one combined function instead of the two separate ones; `seed_l2_includes`
  itself is unchanged and still used independently elsewhere (well-defined
  on its own — the bug was specifically two independent *collections* of
  the same evidence in one logical operation, not either function
  individually). Verified against the same real `compile_commands.json`
  fixtures the fourth finding's own regression tests use, now exercised
  through the combined entry point. `fold_l3_compile_context` itself —
  the standalone wrapper this combined function was built to replace — is
  **not** still used independently: once all three call sites moved to
  the combined function, nothing called it anymore, so it was removed
  entirely as dead code rather than left orphaned alongside its
  replacement (found while writing this entry's own closure notes; no
  call site or test referenced it directly by the time this was checked).

  **A seventh finding, from writing direct unit tests for the combined
  function itself (not through any of the three call sites), on the
  combined function's own body — a real regression relative to both
  siblings it was assembled from.** `derive_l2_include_dirs`'s and
  `derive_l2_compile_context`'s own identical comments both state "pack
  resolution stays inside this protected section... a corrupt/unreadable
  pack must degrade best-effort, not raise" — but
  `seed_includes_and_fold_compile_context`'s own `_resolve_l2_seed_pack_args`
  call was placed *before* the `try:` block, not inside it, so a corrupt
  pack (a `manifest.json` present but unparseable, `is_pack_dir`'s own
  documented "still a pack" case) raised a bare `JSONDecodeError` straight
  out of the function instead of degrading to the pre-existing "nothing to
  apply" no-op every other caller relies on. Caught immediately by writing
  `test_seed_and_fold_corrupt_pack_degrades_to_empty` (mirroring the two
  siblings' own `test_derive_l2_*_corrupt_*_pack_degrades_to_empty`
  tests) — it failed with the raw `JSONDecodeError` before the fix, not
  the intended empty-degrade result. Fixed by moving the
  `_resolve_l2_seed_pack_args` call inside the `try:`, matching both
  siblings exactly. The four new direct tests (no-inputs no-op, no-match
  returns none, ambiguous raises and drains pending cleanups, corrupt pack
  degrades) live in `tests/test_non_elf_dump_l2_seed.py` (not the more
  natural `test_header_compile_context.py`, which is near its own
  1500-line soft/2000-line hard cap) as a
  "seed_includes_and_fold_compile_context branch coverage" section.

  **A sixth finding, on `_split_include_tokens` itself, documented as a
  residual gap rather than fixed here (Codex review).** The split
  distinguishes include-vs-non-include tokens and preserves relative order
  within each group, but not GCC/Clang's distinct include-search *buckets*
  (`-iquote` > `-I` > `-isystem` > `-idirafter`, each a separate search
  class the compiler consults in that fixed order regardless of argv
  position). An explicit `-isystem` therefore still searches ahead of a
  derived `-iquote`/`-I` after the split, even though a real compiler
  would consult the quote/regular buckets first regardless of flag order.
  A correct fix needs the merge to track bucket membership, not just
  include-vs-non-include — a real, if narrow, redesign of the function's
  output shape, not a follow-up to the explicit-vs-derived ordering fix
  (the fourth finding above) this function exists for. See the function's
  own docstring for the same note.

  **An eighth and ninth finding, both from a fresh Codex review round on the
  P1/P2-labeled commit that added the combined function above, both real
  and both fixed.** (8, P1) `scan_engine._build_new_snapshot`'s own L3->L2
  fold only updated its *local* `compile_context` variable — `run_scan_core`
  still held the caller's original, un-folded `compile_context` and
  forwarded *that* to `_run_baseline_compare`, so a `scan --against` a
  native library could fold real L3 context into the *candidate*'s header
  parse while the *baseline*'s own native-library header parse never
  received it — silently recreating the exact `NOT_COMPARABLE`/false-ABI-
  difference risk this whole P0.3 fold exists to close, just moved from the
  dump-vs-scan pairing to the candidate-vs-baseline pairing within one
  `scan --against` invocation. Fixed by widening `_build_new_snapshot`'s
  return from `(snapshot, effective_includes)` to `(snapshot,
  effective_includes, effective_compile_context)` — mirroring the existing
  `effective_includes` precedent for exactly the same reason — and having
  `run_scan_core` forward that third value to `_run_baseline_compare`
  instead of its own original. Regression test:
  `tests/test_cli_scan.py::test_baseline_compare_receives_l3_folded_compile_context`
  (mocks both `_build_new_snapshot` and `_run_baseline_compare` at the
  `cli_scan.py`/`scan_engine.py` module boundary, confirmed to fail against
  the pre-fix code with the un-folded context forwarded instead of the
  sentinel folded one). (9, P2) `perform_elf_dump`'s ADR-039 build-context
  collector (`_attach_build_context`/`_user_define_flags`) has its own
  long-standing, explicit rule — stated in both functions' docstrings —
  that the auto-derived, per-header-matched build context must never be
  unioned snapshot-wide, since doing so would mark one TU's `-D` active for
  every scanned header. The L3->L2 fold's `l3_context_applied`
  reassignment folds that same derived context into the *identically-named*
  `gcc_option_tokens` local variable used for the primary header parse,
  which silently defeated that rule once `_user_define_flags` was called
  with the (by-then-merged) `gcc_option_tokens` rather than the caller's
  original tokens. Fixed by capturing `_user_gcc_option_tokens =
  gcc_option_tokens` before the fold's reassignment and passing that
  captured value to `_user_define_flags` instead. Regression test:
  `tests/test_non_elf_dump_l2_seed.py::
  test_perform_elf_dump_keeps_l3_derived_flags_out_of_build_context_collector`
  (confirmed to fail against the pre-fix code, asserting the L3-derived
  `-DL3ONLY=1` reaches `_attach_build_context`'s `extra_flags` when it must
  not). `handle_non_elf_dump` (PE/Mach-O) was checked and does not call
  `_attach_build_context` at all — the ADR-039 collector is ELF-only — so
  finding 9 has no PE/Mach-O counterpart.

  **A tenth finding, from the same Codex review round (P1), on
  `service._attach_header_graph`'s own second, independent
  `_clang_header_dump` pass — real and fixed.** That pass has its own AST
  cache key, computed from its own `deferred_dirs`, which only covered
  `resolve_inferred_header_roots`'s own deferred roots — never any
  include-search directory riding in `compile.gcc_option_tokens` itself
  (an explicit `--gcc-options`/`--compiler-option -I`, or — since the
  P0.3 fold — a compile-DB-derived one). A directory the primary snapshot
  pass already hashes into its own cache key was therefore invisible to
  this second pass's key, so editing a header under it could silently
  reuse a stale cached graph even though the primary snapshot re-parsed
  correctly and picked up the change. Fixed by extracting
  `l2_seed._include_operand_dirs`'s logic into a new, shared
  `header_utils.include_operand_dirs()` (a leaf module both `l2_seed.py`
  and `service.py` already import from) and folding its result into
  `_attach_header_graph`'s own `deferred_dirs` — closing the gap
  generically for *any* include-search token the merged context carries,
  not just an L3-derived one, since `_attach_header_graph` has no way to
  distinguish the two once they're both flattened into
  `gcc_option_tokens`. `l2_seed._include_operand_dirs` itself is now a
  thin alias forwarding to the shared function, so existing callers/tests
  needed no changes. Regression tests:
  `tests/test_service_unit.py::TestAttachHeaderGraphHashesIncludeSearchTokens`
  (both confirmed to fail against the pre-fix code — the positive case
  asserting a `gcc_option_tokens`-carried `-I` dir reaches
  `_clang_header_dump`'s own `extra_hash_dirs`, the negative case pinning
  the no-tokens baseline stays `()`).

  **Several smaller findings from the same CodeRabbit review round, all
  fixed alongside the above.** (1) The changelog fragment's early entries
  still named the now-removed `fold_l3_compile_context()` wrapper as the
  shipped fix — corrected to the combined function's real name (this
  file's own narrative-history style is left as-is, since later
  paragraphs already record the removal). (2) `seed_includes_and_fold_
  compile_context`'s own guard (`(sources is None and build_info is
  None) or (not want_seed and not headers)`) had a redundant second
  clause — `not want_seed and not headers` always reduces to `not
  headers`, since `want_seed = bool(headers) and ...` is already `False`
  whenever `headers` is empty — leaving a genuinely unreachable `if not
  headers:` guard a few lines further down; both simplified/removed. (3)
  The same function's `pending_cleanups.extend(cleanups)` ran before
  `_merge_l3_compile_context`/`_include_operand_dirs`, so either raising
  would have the `except Exception` branch's `_run_cleanups(cleanups)`
  remove the same already-handed-off thunks a second time — not fatal
  (`_run_cleanups` logs rather than raises on an already-closed handle),
  but a real, avoidable double-removal; moved the extend to after every
  remaining fallible step succeeds. (4) A genuinely weakened test
  assertion in `test_scan_l2_cleanup_ordering.py`
  (`test_scan_l2_seed_cleanup_runs_before_embed`): `assert seed_kwargs.
  get("pending_cleanups") is not None` also passes for the outer scan
  list itself (`defer_cleanup=[]` is never `None` either), so it would
  not have caught a regression that reused it — fixed to assert identity
  against the outer list directly. (5) Two cosmetic-only cleanups: a
  redundant local `import json` shadowing the same module-level import,
  and an unparenthesized chained `and`/`or` (ruff `RUF021`, not in this
  repo's enabled rule set but still real and fixed) in `perform_elf_dump`'s
  `parsed_with_build_context` stamp condition. (6) The three fake
  `seed_includes_and_fold_compile_context` implementations in
  `test_scan_l2_cleanup_ordering.py` each hand-built an identical
  `CompileContext` from the same eight kwargs — extracted into one shared
  `_CC_FIELDS`/`_explicit_ctx_from_kwargs()` helper, mirroring the
  identical pattern `test_cli_dump_helpers_coverage.py` already used.

  **An eleventh finding, from a further Codex review round (P1), on
  `_existing_include_dirs`'s own selection scope — real, but a
  pre-existing, cross-cutting design shared with `compare`'s implicit-dump
  path, documented as a known gap rather than fixed here.** When no
  explicit `-I` is given, `_existing_include_dirs` gathers directories
  from *every* `CompileUnit` in the build evidence, not only the unit(s)
  `resolve_header_compile_context` actually matched to the headers being
  parsed. Those directories reach the AST command as `extra_includes`,
  emitted ahead of the matched context's own include tokens — so in a
  multi-TU build, an unrelated TU's own colliding generated header (e.g.
  a stray `config.h`) can shadow the header the matched TU would have
  resolved, while the snapshot may still get stamped
  `parsed_with_build_context=True` from the (separate) successful fold,
  reading as more authoritative than the seed actually was. Confirmed
  **not** new to this PR: `service_input_resolution._seeded_includes`/
  `_seeded_compile_context` — the pre-existing pair `resolve_side_snapshot`
  already uses for `compare`'s implicit-dump operand, and the reference
  implementation this PR's own fold was modeled after — combines the
  identical broad-seed-plus-matched-fold shape already, unchanged by this
  PR. A correct fix needs `HeaderCompileContextResolution` to expose which
  `CompileUnit`s actually matched (today it exposes only a
  `matched_unit_count`), then both `_existing_include_dirs`'s caller here
  *and* `_seeded_includes` in `service_input_resolution.py` to restrict
  the seed to that set — a genuine, cross-cutting widening of a
  well-tested module's return shape and two independent call sites, not a
  scoped fix reactive to one review comment on one PR. Documented in
  `_existing_include_dirs`'s own docstring alongside this entry.

  **Closed by PR D (plan "PR 3B", build-context completeness), and the
  cross-cutting worry above turned out to be obsolete rather than
  addressed.** The entry says the fix needs restricting "both
  `_existing_include_dirs`'s caller here *and* `_seeded_includes` in
  `service_input_resolution.py`" — two independent call sites. That was
  true when written; PR C (#795) since merged those two into one, so
  `service_input_resolution._seeded_includes_and_compile_context` and all
  three CLI-side resolvers now reach the seed through the single
  `l2_seed.seed_includes_and_fold_compile_context`. There was one call site
  left to restrict, not two. `HeaderCompileContextResolution` gained
  `matched_units` (the tuple; `matched_unit_count` stays as a derived
  property, so the two cannot drift), and the combined primitive now
  resolves the compile context *before* seeding and passes
  `resolution.matched_units` to `_existing_include_dirs` — falling back to
  every unit only when nothing matched, which is the case the seed was
  built for in the first place (a public header the compile DB does not
  cover, reaching into a dependency SDK) and where there is no narrower set
  to prefer. Reordering is otherwise unobservable: the one path that skips
  the seed is the fail-closed ambiguity raise, which aborts the call either
  way. Regression coverage:
  `tests/test_build_context_completeness.py::TestIncludeSeedIsRestrictedToMatchedUnits`
  (the positive case, the no-match fallback, and the `matched_units`/
  `matched_unit_count` consistency), verified to fail against the pre-fix
  `seed_units = units`.

  **A twelfth finding, from a further Codex review round (P1), on
  `run_scan_core`'s own forwarding of the folded context to the baseline
  parse — real and fixed.** The eighth finding above fixed `run_scan_core`
  forwarding its *original* `compile_context` to `_run_baseline_compare`
  unconditionally; that fix itself was too broad in the other direction.
  The fold is derived by matching the *candidate*'s own headers against
  the *new* build's compile units, so its `-D`/`-U`/`-std`/include flags
  describe the new side specifically — but `_run_baseline_compare`'s own
  `_resolve_baseline_header_scope` parses a side-aware `-H old=PATH`
  baseline through its own, different old headers, whose macros/standard/
  generated-header paths may genuinely differ from the new build's.
  Forwarding the new side's folded context there unconditionally risked a
  bad parse or a false ABI diff on exactly the old-side-headers path the
  eighth finding's own fix never distinguished from the common case (no
  `baseline_headers`, baseline reuses the candidate's headers). No
  old-side build evidence exists to derive a matching fold for that case
  (there is no `--build-info-old`/`--sources-old` flag), so the correct
  fallback is the caller's plain, unfolded `compile_context` — not a
  second, unfounded fold attempt. Fixed by conditioning the forwarded
  value on whether `baseline_headers` was given:
  `eff_compile_context if not baseline_headers else compile_context`.
  Regression test:
  `tests/test_cli_scan.py::test_baseline_compare_with_side_aware_headers_keeps_unfolded_context`
  (confirmed to fail against the pre-fix code — the folded sentinel
  reached `_run_baseline_compare` even with `-H old=...` given).

  **A thirteenth finding, from a further Codex review round (P1), on the
  twelfth finding's own fix — a real regression in the fix itself, caught
  before merge.** `not baseline_headers` was the wrong signal: `cli_scan.py`
  builds `baseline_header = header_both + header_old`, so a bare, *shared*
  `-H api.h` (no `old=` scoping at all — the ordinary, most common
  `scan --against` usage) already makes `baseline_headers` truthy and
  identical in *content* to `headers`, since both draw from the same
  `header_both` list. The twelfth finding's own fix therefore treated
  every `scan --against` invocation with any headers at all as
  "old-side-scoped" and fell back to the unfolded `compile_context` —
  silently reintroducing this whole PR's own `NOT_COMPARABLE`/false-ABI-
  diff bug for the common case, one commit after fixing the narrower
  `-H old=PATH` case. Fixed by checking content equality instead of mere
  truthiness: `not baseline_headers or list(baseline_headers) ==
  list(headers)` — the fold is used whenever the old side's resolved
  headers are the same as the candidate's (shared, or genuinely absent),
  and only the caller's plain, unfolded context is used when they
  actually diverge (a real `old=` override). Regression test:
  `tests/test_cli_scan.py::test_baseline_compare_with_shared_bare_header_still_gets_folded_context`
  (confirmed to fail against the pre-fix — twelfth-finding-only — code,
  which forwarded `None` rather than the folded sentinel for a bare
  shared `-H` with no `old=` at all).

  **A fourteenth finding, from a further Codex review round (P1), on
  `ABI_RELEVANT_FLAG_PREFIXES` itself — real, pre-existing (confirmed
  present at this PR's own base commit, before any of its changes), and
  documented as a known gap rather than fixed here.** GNU/clang
  `-include`/`-imacros` and MSVC `/FI`/`/FU` (forced pre-include) are
  absent from this list, so `extract_abi_relevant_flags()` never captures
  them into `CompileUnit.abi_relevant_flags` — `buildsource.adapters.
  base.SOURCE_OPERAND_FLAGS` already recognizes these as value-taking, but
  for an unrelated purpose (not mistaking the operand for the TU source
  file), and that recognition never feeds this list. A matched compile
  unit's own forced-include header is therefore silently absent from
  `header_compile_context`'s derived L2 `CompileContext`, so the P0.3
  fold (reached from `dump`/`scan`/`compare`'s implicit-dump path alike,
  not just the three call sites this PR added) can report a real match
  and stamp `parsed_with_build_context` while still parsing without a
  macro-controlling forced-include header the real build always applies.
  Confirmed genuinely pre-existing, not introduced by this PR:
  `ABI_RELEVANT_FLAG_PREFIXES` and its consumers (`extract_
  abi_relevant_flags`, `header_compile_context.py`) already existed at
  commit `674a506` (this PR's own base, before any of its changes) with
  the identical omission — this PR only added two more call sites
  (`dump` ELF/PE-Mach-O, `scan`) to the same, already-existing
  `resolve_header_compile_context` machinery `compare`'s implicit-dump
  path already used. Not fixed here because a correct fix is a genuine,
  non-trivial extraction-logic change: unlike every other entry in
  `ABI_RELEVANT_FLAG_PREFIXES` (a bare prefix match that appends the
  single matched token), `-include`/`-imacros`/`/FI` always carry a
  required following value that must travel with the flag — appending
  just the bare prefix (the pattern this whole list otherwise uses)
  would silently drop the header filename that is the entire point. A
  correct fix needs a new spaced-value-flag branch in
  `extract_abi_relevant_flags` (mirroring, but distinct from, its
  existing `-D`/`/D` split-form handling), verified end-to-end through
  `header_compile_context._context_flags()`'s reconstruction and the
  real header-parse invocation's own handling of `-include`/`/FI` —
  a cross-cutting change to a shared, well-tested primitive several
  other pre-existing paths already depend on, not a scoped fix reactive
  to one review comment on this PR. Documented in `ABI_RELEVANT_FLAG_
  PREFIXES`'s own docstring alongside this entry.

  **Closed by PR D (plan "PR 3B"), but *not* by the fix this entry
  proposed — that fix was investigated and found to be actively wrong,
  which is the part worth not rediscovering.** The gap is real and the
  consequence stated above is accurate: the derived L2 `CompileContext`
  never carried a matched compile unit's own macro-controlling
  forced-include header, so the header parse saw a materially different
  translation unit while still reporting a real match and stamping
  `parsed_with_build_context`. But routing the fix through
  `ABI_RELEVANT_FLAG_PREFIXES`/`extract_abi_relevant_flags`, as this entry
  proposed, would have **broken L4 replay**, which already handles forced
  includes correctly and by a different route:
  `source_extractors._argv.replay_extra_flags` carries
  `abi_relevant_flags` through (`_carry_abi_relevant_flags`) *and*,
  separately and unconditionally, re-scans the unit's raw `argv` for
  forced-include/include-search tokens (`_scan_argv_for_extra_flags`,
  which is deliberately **not** passed the `seen` set the first pass
  builds — see that function's own docstring for why deduping there was
  itself a reverted bug). Capturing a forced include into
  `abi_relevant_flags` would therefore have made every L4 replay command
  carry `-include config.h` twice: a silent double inclusion that a header
  without include guards turns into a hard redefinition error. The general
  shape is worth naming, since this list has now attracted two fixes aimed
  at it: `ABI_RELEVANT_FLAG_PREFIXES` is not the only channel a compile
  unit's flags reach a consumer through, so "the flag is missing from the
  list" is not by itself evidence that adding it to the list is the fix —
  check which consumers already reach the same fact by another route first.
  Closed at the layer that actually had the gap instead:
  `header_utils.forced_include_operands` is now the one shared recognizer
  (the same option vocabulary and the same separate/joined spellings
  `_argv`'s replay matchers use, since `match_gnu_forced_include`/
  `match_msvc_forced_include` moved into that leaf — the one that already
  owns this codebase's include-flag vocabulary and that both consumers
  already sit above, so sharing costs no new import edge — and `_argv`
  imports them), and `header_compile_context._forced_include_flags` renders its
  result into the L2 command straight from `cu.argv`, never through
  `abi_relevant_flags` — so L4 replay is bit-for-bit untouched. Three
  rendering decisions carry their own reasoning in the code: a relative
  operand is pinned to the compile unit's own `directory` **only when that
  resolves to a real file** (GCC documents a two-stage lookup, and pinning
  a generated header the build finds through its `-I` chain to a
  non-existent path would turn a header that would have been found into a
  hard "file not found"); MSVC `/FI` renders as GNU `-include`, matching
  what this module already does for `-D`/`-I`/`--sysroot=`, since the
  consumer is always a GNU-driver castxml/clang invocation; and
  `-include-pch` (version-locked to the compiler build that produced it,
  which L2's castxml-bundled/host clang is not) plus `/FU` (managed C++/CLI
  `#using`, naming no C/C++ header at all) are deliberately dropped rather
  than rendered. Two consequences beyond the rendering itself: a forced
  include now participates in `_EffectiveContextSignature`, so two units
  forcing *different* macro-controlling headers fail closed instead of
  silently applying whichever grouped first (equivalent spellings of the
  *same* header still agree, since the signature compares the rendered
  tokens); and `header_utils.cache_relevant_operand_dirs` — the union of
  `include_operand_dirs` with the new `forced_include_operand_dirs`, now
  used by all three header-parse cache keys (`service._dump_elf`,
  `service._attach_header_graph`, the L2 seed's own `derived_include_dirs`
  return) — covers the forced header's directory, closing for this input
  the same staleness class the tenth and seventeenth findings above each
  had to close individually. **Eight follow-on findings from review of this
  same change, every one real and every one fixed.** (1) The cache-key channel
  takes directories and walks them with `iter_cache_header_files`, which is
  suffix-filtered by `CACHE_HEADER_SUFFIXES` — correct for "catch transitive
  includes under a search root", wrong for a file named explicitly because it
  is *itself* part of the parse. A forced include routinely carries a suffix
  that list does not name (`-imacros settings.def`) or none at all
  (`-include generated/config`), so hashing only its parent left an edit to it
  invisible while the unchanged option token kept the key identical. Fixed by
  having `dumper_ast_config._cache_key` hash a non-directory entry's own mtime
  directly (a strict widening — such an entry previously contributed only its
  path string) and having `forced_include_operand_paths` return the file
  alongside its parent; `cache_relevant_operand_dirs` was renamed
  `cache_relevant_operand_paths` to stay honest about carrying both.
  (2) The PE/Mach-O path never received the widened set at all:
  `cli_dump_helpers.handle_non_elf_dump` binds the derived-dirs return as
  `_l3_include_dirs` and discards it, since `dump_native_binary`/
  `service.run_dump` exposes no `extra_hash_dirs` hook. Rather than thread one
  through `run_dump`/`resolve_input` — the change this same entry's earlier
  text assumed was required, carried by every caller of those — the fix is
  local: `service_header_scoped._try_header_scoped_dump` folds
  `cache_relevant_operand_paths(cc.gcc_option_tokens)` into its own
  `deferred_dirs`, deriving the identical set from the very tokens those dirs
  came from, since `handle_non_elf_dump` already passes the merged L3 context
  as `compile=`. That is the same "close it where the tokens land, rather than
  threading a new channel" move the tenth and seventeenth findings made, now
  applied to the fourth and last header-parse cache key.
  (3) A third round found the *dialect* vocabulary had drifted between the two
  layers this work now shares a recognizer across:
  `adapters.base._is_msvc_command` listed only `cl`/`clang-cl` while
  `_argv.is_msvc_mode` (L4) also knew `dpcpp-cl` and version-suffixed drivers
  (`clang-cl-20`). A CL-mode command spelled with GNU `-c` rather than `/c`
  therefore read as GNU dialect on the build-evidence side, silently dropping
  its `/FI` from the derived L2 context while L4 replayed it correctly. Fixed
  at the cause rather than at the new call site: `header_utils.
  is_msvc_driver_stem` is now the one vocabulary both use, so the fix also
  reaches `_is_msvc_command`'s pre-existing consumers (source detection,
  forced-language detection, structured-field masking). Only the *name* test
  is shared — each caller keeps its own basename derivation, since the
  adapter's is backslash-aware and `_argv`'s is not, and changing L4's would
  have been a behavior change outside this PR's scope.
  (4) A fourth round found the file-hashing fix in (1) still missed the case
  where a forced include is resolved only through the `-I` chain
  (`-I /build/gen -include config`): the bare operand stats against
  abicheck's own working directory rather than the build's, and the search
  directory is walked only through the suffix-filtered
  `iter_cache_header_files`, which skips an extensionless or `.def` name — so
  nothing hashed the real file. `forced_include_operand_paths` now also emits
  one candidate per include-search directory in the same token list, whether
  or not it exists. Deliberate: `_cache_key` contributes a non-existent path's
  string and moves on, a candidate that *starts* existing is a real change to
  what the compiler resolves, and since the search is first-match-wins a
  candidate under a directory the compiler would never reach can only
  over-invalidate — a spurious miss, never a stale hit, which is the correct
  direction for a cache key to err in. Worth noting how this one was found:
  the previous round's own new test *pinned* the unresolved bare operand as
  expected output, which made a real gap read as a settled decision.
  (5) A fifth round found the rendered forced include could point at nothing:
  `_context_flags` emits only the structured `include_paths`/
  `system_include_paths`, and the compile-DB adapter parses only
  `-I`/`-isystem` into those — so a unit resolving its forced include through
  an argv-only `-iquote gen` or MSVC `/Igen` had that directory in neither
  field, and a bare `-include config` was emitted into a command that could
  not find it. That is *worse* than the pre-existing behaviour of not
  forwarding the forced include at all: a hard "file not found", or silently
  the wrong same-named file. Fixed by resolving the operand against the
  unit's own full search chain (`_forced_include_search_dirs`: `directory`
  first, then quote/normal/system/after-system buckets, each combining the
  structured field with the argv-only spellings) and emitting the absolute
  path of the first match — removing the dependency on the rendered search
  order rather than betting on it. **Deliberately *not* done: rendering
  argv-only search dirs into the derived context.** That is the wider,
  pre-existing fidelity gap the same review names — a *transitively* included
  header the build reaches through `-iquote` is still unreachable to the L2
  parse — but closing it changes include search order for every matched unit,
  a materially broader behaviour change than this function's own correctness
  requires, and it interacts with the bucket-ordering gap `_split_include_
  tokens` already documents (the sixth finding above). Recorded in
  `_forced_include_flags`'s own docstring as a residual.
  (6) A sixth round found the same double-inclusion hazard that rules out
  routing through `abi_relevant_flags`, reached from the other side:
  `_merge_l3_compile_context` concatenates derived and explicit tokens
  without deduplication, so a caller passing `--compiler-option -include
  config.h` for a build whose compile database records the same forced header
  got `-include config.h` **twice** — and an unguarded header is then
  processed twice and fails to compile. Reproduced literally. This one is
  worse than its arithmetic suggests, and the reason is worth keeping: the
  caller passing the option by hand is precisely the one who was *working
  around* the absence of this feature, so the duplicate would have broken
  exactly the users the change exists to help. Fixed by
  `explicit_forced_include_keys` (both caller spellings —
  `gcc_option_tokens` and the free-form `gcc_options` string — each
  contributing the operand as written and its resolved absolute path) with
  the *derived* copy dropped on a match, keeping the established "explicit
  wins" precedence and losing nothing, since both name the same file. A
  *different* explicit forced header does not suppress the derived one.
  (7) A seventh round found the search chain (5) added resolved through the
  *wrong order within a bucket*: it sorted the argv-derived dirs, because
  `_build_context_include_dirs` returns a set and determinism was the stated
  goal. But a compiler takes the **first** match in a bucket in argv order, so
  `-iquote z -iquote a -include config.h` resolves `z/config.h` while the
  sorted chain pinned `a/config.h` — a different file, potentially different
  macros, i.e. deterministic and wrong. The lesson is the trade that was made
  without noticing it was a trade: determinism was available *by preserving
  argv order*, so sorting bought nothing and cost correctness. Fixed by
  factoring `header_utils.build_context_include_dirs_ordered` out as the real
  implementation (argv order, first-occurrence-wins dedup) with the
  set-returning `_build_context_include_dirs` now a thin collapse of it — every
  pre-existing caller only asks "is this directory covered", so none change.
  One residual, recorded at the call site: within a bucket, structured entries
  are emitted before argv-derived ones rather than interleaved by true argv
  position, which the structured fields do not record. It can only matter for a
  command mixing spellings in one bucket (a structurally-captured GNU `-I`
  alongside an MSVC `/I`), which no single real driver accepts.
  (8) An eighth round (CodeRabbit) found the dedup in (6) flattened the
  caller's two option spellings through its own second copy of that
  flattening, unguarded — so an unbalanced quote in the free-form
  `gcc_options` string made `shlex` raise `ValueError: No closing quotation`
  straight out of `resolve_header_compile_context`, aborting an L2
  compile-context resolution that every caller treats as best-effort, over a
  malformed *caller* string. `_explicit_pin_tokens` — the same module's own
  flattening for the `_ExplicitPin` scan, ten lines away — already had the
  guard and already documented why ("degrades to 'no tokens from it' rather
  than raising, since this is only used to *widen* what's accepted"). The
  duplicate is now routed through it, so there is one flattening definition
  and one degrade rule rather than two that could disagree again. Worth
  naming the shape, since it is the same one as (3): a second copy of an
  existing primitive drifts from it silently, and the drift shows up as the
  copy lacking a property the original was deliberately given.
  **Residual, deliberately unclosed:** because a
  forced include still never enters `abi_relevant_flags`, it is still not
  projected into a `BuildOption` by `derive_build_options`, so swapping one
  build's forced-include header for another does not raise
  `ABI_RELEVANT_BUILD_FLAG_CHANGED` (ADR-029 D9's build-evidence drift
  signal). Closing *that* half needs a structured `CompileUnit` field every
  adapter populates, a `BUILD_EVIDENCE_VERSION` bump and `build_diff`
  wiring — a schema slice of its own, not a follow-on to the L2 rendering
  fix, and specifically not another attempt to route it through this list.
  Regression coverage: `tests/test_build_context_completeness.py` (the
  recognizer, the rendered context, the ambiguity signature, the cache-key
  union, and — as the executable record of the wrong fix —
  `TestReplayStillEmitsForcedIncludesExactlyOnce`).

  **A fifteenth finding, from a further Codex review round (P1), on the
  thirteenth finding's own header-equality fix — a real gap in the fix
  itself, caught before merge (fresh evidence).** Comparing only the
  resolved *header* lists was not sufficient: `cli_scan.py` builds
  `baseline_include = include_both + include_old` completely
  independently of `baseline_header = header_both + header_old`, so a
  shared, bare `-H api.h` (no `old=` scoping — passing the thirteenth
  finding's own equality check) combined with side-specific `-I
  old=old-build -I new=new-build` shares one header list across both
  sides while still routing each through a genuinely different include
  tree. The header-only check let the new side's folded `-D`/`-std`/
  sysroot/include context reach a baseline parsed through a different
  include scope — parsing the old binary under the new build's own
  configuration risks a bad parse or a false ABI diff on the old side,
  the exact failure mode this whole fix exists to prevent. Fixed by also
  requiring the old side's resolved include scope to match the
  candidate's own effective includes (`not baseline_includes or
  list(baseline_includes) == list(eff_includes)`, ANDed with the
  existing header-equality check) before reusing the fold — a genuine
  `-I old=PATH`/`-I new=PATH` override on either side now falls back to
  the caller's plain, unfolded context, same as a genuine `-H old=PATH`
  override already did. Regression test:
  `tests/test_cli_scan.py::test_baseline_compare_with_side_aware_includes_keeps_unfolded_context`
  (confirmed to fail against the pre-fix — header-equality-only — code,
  which forwarded the folded sentinel even though the old side's
  resolved include scope diverged from the candidate's).

  **A sixteenth finding, from a CodeRabbit review round (Major), on
  `header_utils._INCLUDE_FLAG_PREFIXES`'s own matching itself — real,
  and confirmed pre-existing for most of its consumers (present at this
  PR's own base commit `dc09aec`, before any of its changes), documented
  as a known gap rather than fixed here.** Every match against this
  tuple — in `_has_include_build_context`, `_build_context_include_dirs`,
  `_flag_tokens`, `_msvc_deferred_flag`, `include_operand_dirs`, and
  `buildsource.l2_seed._split_include_tokens` alike — is exactly
  case-sensitive `str.startswith`. That is correct for the GNU/clang
  spellings (a real compiler only ever accepts the documented lowercase
  forms), but wrong for the two clang-cl-only entries: verified against
  real documentation for both drivers — native `cl.exe`'s own options are
  case-sensitive (and it doesn't recognize `/imsvc` at all, a clang-cl-only
  spelling), while clang-cl's own option parsing is documented
  case-insensitive, so `/IMsvc`/`/EXTERNAL:I` are legal, real spellings
  this tuple's exact-case matching silently fails to recognize as
  include-search flags at all. Concretely: a real clang-cl build record
  using `/IMsvc` would have that directory not suppress the L2 include-dir
  seed, not resolve through the existing-dir dedup, and — this PR's own
  two new consumers specifically — not be hashed into the AST cache key's
  `extra_hash_dirs` (`include_operand_dirs`) and not be carved out of the
  leading last-flag-wins token group by `_split_include_tokens`, so an
  explicit `/IMsvc` override could still lose to a derived `-I` for a
  colliding header. Confirmed pre-existing for `_has_include_build_context`/
  `_build_context_include_dirs`/`_flag_tokens`/`_msvc_deferred_flag` — all
  four already existed at commit `dc09aec` (this PR's own base) with the
  identical case-sensitive matching; only `include_operand_dirs` (moved
  from `l2_seed._include_operand_dirs`) is new to this PR. Not fixed here:
  a correct fix needs case-insensitive matching applied *consistently* to
  every one of those six consumers, not just the two this PR added — fixing
  only the new ones while leaving the pre-existing four case-sensitive
  would be strictly worse than today's uniform gap, making the
  seed-suppression logic and the cache-hashing/ordering logic silently
  *disagree* about whether a given `/IMsvc` token is an include-search
  flag at all. It also needs a genuine new tie-break case-folding
  introduces that a case-sensitive scan never had: `/imsvc` case-
  insensitively also matches the shorter `/I` prefix, and picking the
  wrong one changes which include bucket the directory is treated as (see
  `_msvc_deferred_flag`'s own bucket-priority docstring) — a real, if
  narrow, cross-cutting change to a shared, well-tested primitive several
  pre-existing callers depend on, not a scoped fix reactive to one review
  comment on this PR. Documented in `_INCLUDE_FLAG_PREFIXES`'s own
  docstring alongside this entry.

  **A seventeenth finding, from a further Codex review round (P1), on the
  counterpart cache key the tenth finding's `_attach_header_graph` fix was
  supposed to stay aligned with — real, and fixed.** `service._dump_elf`'s
  own `deferred_dirs` computation (its PRIMARY header parse's cache key,
  not the header-graph attach) hashed only `resolve_inferred_header_roots`'s
  deferred roots, never any include-search directory riding in the compile
  context's own `gcc_option_tokens` — exactly the gap the tenth finding
  closed on `_attach_header_graph`'s side, left open on the primary parse
  whose cache key that fix was meant to match. Editing a header under such
  a directory could let the primary parse reuse a stale cached AST while
  `_attach_header_graph`'s own independent second parse correctly
  reparsed, producing a snapshot whose declarations and embedded graph
  describe different source states — the exact divergence the tenth
  finding's fix was supposed to prevent, just from the other side. Fixed
  by folding `include_operand_dirs(cc.gcc_option_tokens)` into `_dump_elf`'s
  `deferred_dirs` too, the identical fold `_attach_header_graph` already
  applies. Regression test:
  `tests/test_service_unit.py::TestDumpElf::test_gcc_option_tokens_include_dir_is_hashed`
  (confirmed to fail against the pre-fix code, which never hashed the
  directory into `extra_hash_dirs`).

  **An eighteenth finding, from a further Codex review round (P2), on a
  real ordering bug in `perform_elf_dump`'s own composition — distinct
  from `_merge_l3_compile_context`'s already-fixed explicit-vs-derived
  split — and fixed.** `resolve_inferred_header_roots`'s own `deferred`
  tokens (the inferred `-H` header root, emitted in a search bucket that
  defers below any *existing* build context) were folded into the
  "explicit" side of the L3 fold purely to suppress the fold's own
  internal broad include-dir seed — but `_merge_l3_compile_context` has no
  way to tell a synthetic deferred marker apart from a genuine user token,
  so it ranked `deferred` ahead of the L3-derived include tokens too. A
  colliding generated header under the L3-derived directory could then be
  shadowed by the inferred root — the exact inversion "deferred" exists to
  prevent. Fixed by excluding `deferred` from the tokens passed into the
  fold and appending it back at its correct lowest-priority position
  (after the L3-derived includes) once the fold result is known. Verified
  this doesn't break the suppression it was providing: `deferred` is
  non-empty only when the *original* tokens (unmodified, still passed to
  the fold) already showed build-context evidence, which the fold's own
  suppression check reads directly — so nothing is lost by leaving
  `deferred` itself out. Regression test:
  `tests/test_non_elf_dump_l2_seed.py::test_perform_elf_dump_keeps_deferred_inferred_root_below_l3_derived_includes`
  (confirmed to fail against the pre-fix code, both via an internal
  assertion catching `deferred` leaking into the fold's own explicit
  tokens and via the final ordering check).

  **A nineteenth finding, from real Bazel/castxml CI evidence (not a
  hand-built fixture) on `napetrov/abicheck-bazel-lab`'s diagnostic PR #14,
  after repinning it to `abicheck/abicheck@84cf3d4` (PR #788) — a narrower
  residual of this same topic survives the eighth/`_build_new_snapshot`
  fold fix above, investigated but not fixed.** The lab's
  `validate-two-fresh-mains.yml` workflow builds BASE and HEAD with real
  Bazel, captures target-scoped cquery/aquery evidence for both, `dump`s
  each fresh (`fresh-base.abi.json`/`fresh-head.abi.json`, same code,
  identical `--sources`/`--build-info`), then separately `scan`s HEAD
  `--against` the fresh BASE dump. `compare fresh-base fresh-head` (pure
  `dump` vs `dump`) reads `COMPATIBLE`/`NO_CHANGE` with a complete
  `analysis_assurance` — confirming `language_standard` parity holds, i.e.
  the eighth-finding fix above genuinely works. But `scan HEAD --against
  fresh-base.abi.json` (the same HEAD code, same `-H`/`--sources`/
  `--build-info` inputs, `dump`'s baseline) still reads `NOT_COMPARABLE`:
  `diff.reason` names exactly one differing field, `include_sequence` (not
  `language_standard`, which no longer reproduces) — a narrower,
  previously-undetected sibling of the field this whole topic exists to
  close, confirmed via the run's own uploaded `two-fresh-mains-validation`
  artifact (run
  https://github.com/napetrov/abicheck-bazel-lab/actions/runs/31950549361,
  job 95173233150, commit `a7f5a7f`).

  **A disconfirmed hypothesis, corrected by Codex review — recorded so a
  future pass doesn't re-propose it.** This entry's first draft claimed the
  cause was `scan_engine._build_new_snapshot` passing a raw, unexpanded
  `headers` list (still containing the directory entry) into
  `seed_includes_and_fold_compile_context`/`service.resolve_input`, while
  `perform_elf_dump` pre-expands via `expand_header_inputs()` first. False,
  verified by reading both paths fully rather than trusting the first,
  partial read: (1) `header_compile_context.resolve_header_compile_context`
  — reached from *inside* `seed_includes_and_fold_compile_context`'s own
  compile-context fold, not just its truthiness-only `seed_l2_includes`
  half this entry's first draft checked — calls its own
  `_expand_header_directories()` on the raw `headers` list, whose docstring
  states it deliberately reuses `header_utils.iter_directory_headers` (the
  same walk `expand_header_inputs` itself delegates to, same suffix/pruned-
  segment filters) specifically so the expanded set matches what L2 actually
  parses. (2) `service.resolve_input`'s own ELF dispatch, `_dump_elf`, calls
  `expand_header_inputs(headers)` internally (`service.py:1281`) before
  ever reaching `dumper.dump()` — the identical function `perform_elf_dump`
  calls explicitly upfront, just one call-stack frame deeper. Both `scan`'s
  and `dump`'s header lists therefore converge on the identical expanded,
  deduped, deterministically-ordered file set before any header-AST parse
  runs; a raw-vs-expanded asymmetry cannot be the cause of the `include_
  sequence` mismatch.

  **Two further Codex review rounds, each finding the previous round's
  proposed single "the mechanism is X" narrative was itself incomplete —
  pattern worth naming before the specifics: this area (`perform_elf_dump`
  vs. `scan_engine._build_new_snapshot`'s relative ordering of the L3→L2
  fold and `header_utils.resolve_inferred_header_roots`) has enough real
  asymmetry that every single-paragraph explanation attempted so far turned
  out to be a true but partial slice of it, not the whole story — so this
  entry stops trying to assert one and instead lists the verified-by-
  reading candidate mechanisms found, unranked, without claiming which one
  (if any single one) explains the specific `include_sequence` mismatch in
  the CI evidence above.** `perform_elf_dump` calls
  `resolve_inferred_header_roots(headers, includes, gcc_options=
  effective_gcc_options, gcc_option_tokens=tuple(gcc_option_tokens))` using
  its own *pre-fold* local variables, before its later
  `seed_includes_and_fold_compile_context` call folds real L3 evidence into
  them; `scan_engine._build_new_snapshot` folds *first*, then passes the
  already-folded `compile_context` into `resolve_input(..., compile=
  compile_context)`, whose ELF dispatch `_dump_elf` makes its own internal
  `resolve_inferred_header_roots(...)` call reading that already-folded
  context. Two concrete, verified effects of this ordering difference, not
  one:
  (a) `resolve_inferred_header_roots`'s own `skip` set is built from
  `user_includes` (the caller's `includes` parameter) *and*
  `_build_context_include_dirs(ctx)` (dirs implied by the passed-in
  `gcc_options`/`gcc_option_tokens`) — for scan's post-fold call, both of
  these already carry L3-derived content, so an inferred `-H` root whose
  directory the L3 evidence already covers is **skipped entirely**
  (`inferred` ends up empty, the function returns `([], [])` for that
  root); dump's pre-fold call has a much smaller `skip` set (no L3 content
  yet), so the same root is far more likely to survive as a plain `-I`
  extra-include instead. This omission is real only for the two branches of
  `skip` that do **not** independently reach `extra_includes`: a
  `_build_context_include_dirs(ctx)` match (a dir implied only by
  `gcc_options`/`gcc_option_tokens`, never itself added to `includes`), and
  the sibling deferred-token branch below. It is *not* an omission when
  `skip` matches purely because the root is already literally present in
  `user_includes` — `_dump_elf`'s own `eff_includes = list(includes)`
  starts from that same list before `resolve_inferred_header_roots` ever
  runs and is only ever added to, never filtered, so that root's slot
  survives via the pre-existing `includes` entry regardless of whether the
  inferred-root call re-adds it (Codex review: an earlier draft of this
  entry collapsed all three skip/defer shapes into one "no slot" outcome,
  which is only true for two of them).
  (b) Separately, `perform_elf_dump` computes `inc_extra` from this
  *pre-fold* call and only later builds `extra_includes=eff_includes +
  inc_extra` for the actual `dump(...)` call, where `eff_includes` is the
  (by then real) L2-seeded include list — if `inc_extra`'s root and
  `eff_includes` overlap (plausible, since both can independently resolve
  to the same L3-derived directory), the *same* directory can appear twice
  in dump's own `extra_includes`, which is what `comparability_fields.
  _include_slot_tokens` actually tokenizes into `include_sequence` — one
  token per `declared_includes` slot, and `dumper_contract.
  _attach_extraction_contract` builds `declared_includes` **exclusively**
  from `extra_includes` (`IncludeDir(path=p, ...) for p in extra_includes`,
  absent a `dump_manifest`); `gcc_option_tokens` (where a *deferred* root
  rides, as `-isystem`/etc.) never contributes a slot at all (Codex review
  — corrected from this entry's own prior, wrong claim that both jointly
  derive slots). This sharpens rather than weakens candidates (a)/(b)
  below: a root that lands in `extra_includes` always produces a slot: once
  for dump's `inc_extra`, and *again* if `eff_includes` also independently
  picked up the same directory (candidate (b), a real duplicate slot); a
  root that `resolve_inferred_header_roots` either skips outright or
  reclassifies to a deferred `gcc_option_tokens` flag produces **no** slot
  at all either way, since neither reaches `extra_includes` (candidate (a),
  collapsing what looked like two distinct scan-side outcomes — "skipped"
  vs. "deferred" — into the same net effect on `include_sequence`). scan's
  candidate side has no equivalent double-add, since its single fold call
  already produced the final `includes`/`compile_context` `resolve_input`
  uses directly. Either effect alone — a missing slot on scan's side, or a
  duplicated slot on dump's — changes the resulting slot *count*, which is
  sufficient to make `include_sequence` differ regardless of which specific
  slot moved. **Not fixed here, and deliberately not narrowed to one of (a)/
  (b) without more evidence**: neither this pass nor either Codex round
  inspected the actual differing `include_sequence` token values from the
  CI artifact (only `diff.reason`'s field name was read, not its content),
  so which effect (or another one still unfound) actually fired in this
  specific repro is genuinely unknown; a real fix needs that inspection (or
  a live `bazel`/`castxml` repro, absent from this pass's environment)
  before it can even be scoped, let alone attempted — this file's own
  "known gaps over risky reactive patches" convention applies doubly here,
  given how many single-paragraph "found it" claims this same footnote has
  already had to walk back. Consequence for
  `napetrov/abicheck-bazel-lab`: PR #14's `fresh-to-fresh` job (its real
  workflow-job name) genuinely fails overall — its own
  `compare fresh-base fresh-head` step passes, but its separate
  `scan HEAD --against fresh-base.abi.json` step is the one that returns
  `NOT_COMPARABLE` and fails the job — so it should be treated as still red
  on this one residual field, and the lab's own checked-in
  `abi/math.abicheck.json` should **not** be regenerated
  against the new core pin yet — doing so now would just encode a baseline
  that a fresh `scan --against` still can't cleanly compare to, the same
  problem this diagnostic exists to catch, not fix.

  **Two real mechanisms found and fixed by actually inspecting the
  differing `include_sequence` token values, using a minimal, castxml-free
  local repro (a `g++`-compiled one-header library + a hand-written
  `compile_commands.json`, `--ast-frontend clang`) rather than a live
  Bazel/castxml run — the repro this whole footnote's own "not fixed here"
  note said was the missing prerequisite.** Both are real instances of
  candidate (b) above (a duplicated `-I`/`-isystem` entry), reached from
  two different composition points, neither previously identified:
  (1) `perform_elf_dump`'s own `extra_includes=eff_includes + inc_extra`
  (and `service_header_scoped._try_header_scoped_dump`'s identical
  `eff_includes += inc_extra`) concatenate two independently-derived
  include-dir lists with no dedup — fixed with a new
  `header_utils.dedup_paths_preserve_order()`, applied at both composition
  sites. (2) The load-bearing one for this specific repro:
  `l2_seed._merge_l3_compile_context`'s `derived_includes` (the P0.3 fold's
  own derived compile-unit include dirs) and the caller's own *explicit*
  `gcc_options`/`gcc_option_tokens` can independently carry an `-I` for the
  identical directory — concretely, a `--build-info` compile database is
  matched *both* by this fold's own resolver *and* by the pre-existing
  legacy `-p`/`--compile-db` auto-match mechanism that populates
  `perform_elf_dump`'s `effective_gcc_options` string before the fold ever
  runs, so the same directory reaches the merged `gcc_option_tokens` twice:
  once via `explicit_tail` (the split `gcc_options` string) and once via
  `derived_includes`. Confirmed via direct inspection of both
  `CompileContext` objects the merge receives (`explicit.gcc_options`
  literally contained `"-I <dir>"`, `derived.gcc_option_tokens` literally
  contained `("-I", "<dir>", ...)`  for the same `<dir>`) — not
  reconstructed from `diff.reason` alone, closing exactly the evidentiary
  gap the "Two further Codex review rounds" paragraph above says was still
  missing. Fixed with a new `header_utils.drop_include_tokens_duplicating_
  paths()`, which drops a `derived_includes` pair whose directory already
  appears among `explicit_tail + explicit.gcc_option_tokens`'s own
  include-search operands — "explicit wins, searches first" is unaffected,
  since only the later, redundant `derived` copy is ever dropped. Verified
  end to end: the real compiler argv `dump` sends to clang no longer
  repeats `-I <dir>`, and a fresh `dump` baseline's `ast_compile_args` now
  matches a `scan --against` candidate's own (both carry exactly one `-I
  <dir>` for the matched project directory) — see
  `tests/test_build_context_completeness.py`'s
  `TestDedupIncludeDirsAcrossCompositionSites`/
  `TestMergeL3CompileContextDropsDuplicateExplicitInclude`.

  **A Codex review round on the same PR found the first version of this
  fix class-blind, and it was fixed before merge.** `drop_include_tokens_
  duplicating_paths()`'s first cut matched purely on resolved directory,
  ignoring which include-search *class* (`-I`/`-isystem`/`-iquote`/
  `-idirafter`) each entry belonged to — but a real compiler consults
  those as distinct search buckets in a fixed order regardless of argv
  position (the identical class-blindness `_split_include_tokens`'s own
  docstring already documents as a known gap one function over), so
  dropping an `-isystem <dir>` merely because an unrelated `-I <dir>`
  existed elsewhere could change which bucket that directory is searched
  from for a colliding header basename — concretely, `-I A -I B -isystem
  A` resolves a colliding name from `B`, while the class-blind rewrite
  `-I A -I B` resolves it from `A` instead. Fixed by making the dedup key
  `(flag-class, resolved directory)` rather than the directory alone, via
  a new `_include_class_path_pairs()` shared by both the "already
  covered" side and the token walk being filtered — see
  `TestDedupIncludeDirsAcrossCompositionSites::
  test_drop_include_tokens_duplicating_paths_is_class_sensitive`. Both
  call sites' "already covered" argument changed shape accordingly (raw
  tokens, not bare `Path`s) so the class of an *already-emitted* entry is
  never lost either.

  **Not fully closed — a third, deeper mechanism survives and still
  reproduces `NOT_COMPARABLE` on `include_sequence` for the identical
  repro, confirmed by direct inspection after the two fixes above.** Even
  with both duplicate-`-I` bugs fixed, `dump`'s baseline and `scan`'s
  candidate still disagree, because the matched directory reaches
  `declared_includes` (the sole source `dumper_contract.
  _attach_extraction_contract` builds `include_sequence`'s slots from) via
  **structurally different channels on the two paths** rather than merely
  differing in count: on `dump`, the legacy `-p`/`--compile-db` match
  supplies the directory as part of `effective_gcc_options` *before* the
  L2 seed ever runs, and `seed_l2_includes`'s own suppression rule
  (correctly) declines to seed a directory when explicit context already
  supplies one — so the directory reaches the parse only through
  `gcc_option_tokens`, and `eff_includes`/`declared_includes` stays empty
  for it. `scan_engine._build_new_snapshot` has no equivalent legacy `-p`
  step at all — nothing pre-populates `effective_gcc_options` before its
  own fold runs, so the identical directory reaches the *same* fold's L2
  seed unsuppressed, landing in `eff_includes`/`declared_includes` instead.
  Two structurally different `declared_includes` — empty vs. one entry —
  for the same real include root is exactly what `include_sequence` is
  built to detect, so it correctly (if unhelpfully) still fires. **Not
  fixed here**: closing it needs a design decision this pass did not have
  standing to make on its own — either `dump`'s CLI stops running the
  legacy `-p`/`--compile-db` auto-match whenever `--build-info` already
  feeds the new P0.3 fold (the two have overlapped, silently, since the
  fold was introduced — this pass is the first evidence either mechanism's
  *placement choice* for a resolved directory is externally observable,
  not just its content), or `scan`'s candidate resolution gains an
  equivalent legacy-match step so both paths agree on *which* channel
  supplies a matched directory, not merely that they supply the same one.
  Either is a real, cross-cutting change to which of two established
  mechanisms wins for a `dump`-only surface `scan` has no counterpart to
  — not a mechanical follow-up to either dedup fix above. Confirmed via
  the same local repro: after both fixes, `dump`'s `ast_compile_args`
  still carries `-I <dir>` (via `gcc_option_tokens`) while `declared_
  includes` is empty; `scan`'s candidate carries the identical `-I <dir>`
  via `declared_includes` instead — same effective compiler argv, still a
  real `profile_fingerprint`/`include_sequence` disagreement.

  **Re-verified end to end (2026-08-20) against the literal bug report this
  whole entry was originally opened from** (a `dump --sources --build-info
  --depth source` baseline compared against an unchanged codebase via
  `scan --against`, reported carrying empty `ast_resolved_standard`/
  `ast_compile_args` and failing as `NOT_COMPARABLE`): the reported
  symptom does **not** reproduce on current `main` for a plain,
  single-compilation-unit `compile_commands.json` (real `g++ -std=c++17`
  build, real `clang` L2 frontend, no `-p`/`--compile-db` involved) — the
  `dump` baseline's `ast_resolved_standard`/`ast_compile_args` are
  correctly populated and the follow-up `scan --against` resolves
  `NO_CHANGE`, not `NOT_COMPARABLE`. This confirms the accumulated fixes
  above (the L3->L2 fold itself, the include-dir dedup fixes, the
  class-sensitive dedup fix) already close the reported case for real —
  the pinned commit the report was filed against
  (`abicheck/abicheck@891bd9d7`) predates essentially all of them. Added
  as a real, non-mocked end-to-end regression:
  `tests/test_dump_scan_l3_comparability.py` (a real `g++`-compiled
  library + `compile_commands.json`, driven through the actual `dump`/
  `scan` CLI commands via `CliRunner`, not a stubbed `dump()` call the way
  the existing unit coverage in `tests/test_cli_dump_helpers_coverage.py`
  is) — closing the gap this entry's own earlier text noted between "the
  mechanism is unit-tested" and "the reported end-to-end symptom is
  verified gone." **The residual, narrower gap immediately above this
  note — the legacy `-p`/`--compile-db` auto-match (`dump`-only) landing a
  matched directory through a structurally different channel than the
  P0.3 fold's own seeded `declared_includes`, reproduced against real
  Bazel `aquery`/`cquery` evidence in `napetrov/abicheck-bazel-lab`'s own
  PR #14 — is unaffected by this re-verification and remains genuinely
  open** for a `--build-info` combined with an explicit `-p`/
  `--compile-db`, or for real multi-target Bazel `aquery` graphs this
  environment cannot reproduce (no live Bazel/castxml toolchain
  available). A user hitting `NOT_COMPARABLE` after this date should
  check first whether their invocation combines `-p`/`--compile-db` with
  `--build-info`, or is a genuine multi-TU Bazel build — the plain,
  single-compile-unit case this note re-verifies is not the culprit.

  **Narrower still: the residual gap also reproduces without any Bazel
  toolchain or `-p`/`--compile-db` at all — the minimal trigger is just
  "the matched compile unit's own flags include an `-I<dir>` that is not
  already in the caller's explicit `includes`" (2026-08-20, found while
  adding generalized parity testing for this bug class, prompted by a
  direct question — "how did we end up with two different behaviours,
  and can this be caught generically?" — rather than by a new field
  report).** A real `g++ -std=c++17 -I<dep-dir>` build, `--build-info` a
  matching `compile_commands.json`, no `-p`/`--compile-db` anywhere:
  `dump`'s own baseline JSON correctly carries `-I <dep-dir>` in
  `ast_compile_args`, but its `contract.profile_fields.include_sequence`
  reads `"[]"` — confirmed directly by inspecting the emitted JSON, not
  inferred. Root cause, read from the code rather than guessed:
  `dumper_contract._attach_extraction_contract` builds `declared_includes`
  (the source `include_sequence` tokenizes) **exclusively** from
  `extra_includes`, and for this shape the `-I<dep-dir>` reaches the
  parse only through `gcc_option_tokens` (the L3->L2 fold's own derived
  compile-unit include dirs), never through `extra_includes` — while
  `scan_engine._build_new_snapshot`'s own candidate resolution (which
  calls `service.resolve_input` directly, per this same entry's "PR C"
  paragraphs below, rather than through the shared `resolve_side_snapshot`
  primitive `compare`'s implicit-dump path and the typed `DumpRequest`
  API both already use) seeds `eff_includes`/`declared_includes` for the
  identical directory instead — two structurally different channels for
  the same fact, exactly the shape this entry's own "third, deeper
  mechanism" paragraph already names for the Bazel case, just reproduced
  here without Bazel. Confirms `compare`'s implicit-dump path is
  genuinely unaffected: comparing the same `dump` baseline against the
  same live binary via `compare` (not `scan --against`) resolves cleanly
  for this exact shape — the divergence is specifically between `dump`'s
  CLI path and `scan`'s own candidate-resolution path, narrower than
  "any comparison against a `dump` baseline." **Not fixed here** — same
  reasoning as the Bazel-specific case above (a real design decision on
  which of two established include-seeding channels should win, not a
  drive-by patch) — but now has permanent, generalized regression
  coverage rather than depending on a Bazel CI lab to notice it again:
  `tests/test_dump_cli_typed_api_parity.py`'s
  `test_scan_against_real_dump_baseline_is_comparable` parametrizes over
  several real build-evidence shapes (plain, an added macro, this
  extra-include-dir shape). Rather than a bare `xfail(strict=True)` (three
  Codex review rounds on this test found real gaps in cruder versions of
  this idea — see the test module's own comments for the full history),
  the known-divergent shape is checked against the *exact* diagnosed
  failure signature (`NOT_COMPARABLE` naming `include_sequence`) before
  being treated as expected via a conditional `pytest.xfail()`; anything
  else for that shape — including the gap closing entirely — fails the
  test outright, forcing this note and `_SCAN_KNOWN_DIVERGENT_SHAPES` to
  be updated deliberately rather than the test quietly going green. A
  future regression that widens the gap to a *previously-passing* shape
  fails immediately too, since only the shape explicitly listed gets any
  tolerance at all. The sibling
  `test_dump_cli_and_typed_api_agree_on_resolved_compile_context` in the
  same module separately pins the narrower invariant that *does* already
  hold across all three shapes today (`dump`'s CLI path and the typed
  `DumpRequest` API path agree on `ast_resolved_standard`/
  `ast_compile_args`) — so a regression in *that* invariant, which is
  what #810's original literal symptom was about, is caught independently
  of the `include_sequence` gap this note documents.

  **The nineteenth finding's own missing prerequisite — real evidence from
  the exact Bazel `include_sequence` mismatch — has now been supplied, and
  the mismatch does not reproduce on current `main` (2026-08-22).**
  `napetrov/abicheck-bazel-lab`'s `UPSTREAM_TO_ABICHECK.md` (2026-08-21
  entry, "`abicheck scan --against` reports NOT_COMPARABLE against every
  `abicheck dump` baseline") recorded exactly this: `mode: dump` and
  `mode: scan` for real Bazel `//:math` evidence (`cc_library(includes =
  ["include"])`, verified byte-identical evidence packs across runs)
  disagreeing on `contract.profile_fields.include_sequence`, deterministic,
  on every CI run pinned to `abicheck/abicheck@6fb8536` (#812). Reproduced
  directly, without Bazel or castxml: a `g++`-compiled library with the
  identical real shape a `cc_library(includes = ["include"])` action
  produces — two simultaneous `-I` search directories (the package's own
  `include` dir and Bazel's always-present package/workspace-root search
  path) that are both real, legitimate ancestors of the *same* physical
  public header, tokenizing as two `hdrs:` slots in `include_sequence`
  (`abicheck_lab/math.h` under the `include` dir, `include/abicheck_lab/
  math.h` under the root — matching the committed `abi/math.abicheck.json`
  baseline's own recorded fields exactly). Checked out at the reported pin
  (`6fb85361c`, #812) in a separate worktree: the mismatch reproduces
  there verbatim (`dump`'s baseline records `include_sequence: []` — the
  P0.3 L3→L2 fold never reached the ELF `dump` CLI's header parse at that
  commit — while `scan`'s candidate resolves two `hdrs:` slots for the
  identical evidence, so `scan --against` correctly, if unhelpfully,
  refuses the pair as `NOT_COMPARABLE`). The identical repro against
  current `main` (well past #812 — the "PR C" `dump`/`scan` convergence
  work chronicled throughout this same entry, `#814`/`#815`/`#817`/`#823`,
  landed after the lab's pin) produces `NO_CHANGE`/exit 0 on `scan
  --against`, `compare`'s implicit-dump operand, and both CLI-vs-typed-API
  parity lenses alike — the `dump`/`scan` convergence work already closed
  this specific shape as a side effect, not as a targeted fix for it.
  Given `main` was already correct, no `abicheck` source change was made
  for this finding; what was missing was permanent regression coverage
  pinning this exact real-world shape (a Bazel `includes`-attribute-style
  *duplicate owned include directory*, distinct from `extra-include-dir`'s
  unrelated-second-header shape), so a future regression in the shared
  `seed_includes_and_fold_compile_context`/`_slot_token_for_ancestor`
  machinery fails a fast, deterministic test here instead of needing a
  fresh Bazel CI report to notice again. Added as a fourth parametrized
  shape, `"duplicate-owned-include-dirs"`, in
  `tests/test_dump_cli_typed_api_parity.py`'s `_BUILD_SHAPES` — it runs
  through all four existing parity/comparability tests in that module
  (both CLI-vs-typed-API lenses, `scan --against`, `compare`'s
  implicit-dump operand) with no `xfail`, matching every other closed
  shape there. The pinned Action commit `abicheck-bazel-lab` reported
  against (`6fb8536`) predates the fix entirely; upstream consumers hitting
  this exact symptom need to move their pin forward past `#814`, not wait
  on a new `abicheck` change.

- **`dump --lang c++` is silently discarded on the primary clang header-AST
  pass for a language-ambiguous header, diverging from `_attach_header_graph`'s
  own pass on the identical headers — investigated, not fixed (G31 Phase C
  castxml-installation pass, fresh evidence, reproduced end-to-end).**
  `cli_dump_helpers.py`'s ELF `dump` path (and `service.py`'s two mirrors,
  `_dump_elf`/PE-Mach-O's `_header_graph_lang`) all normalize the caller's
  `lang` to `lang if lang == "c" else None` before calling `dumper.dump()` —
  deliberately, per their own comment: "every format's own main pass
  normalizes `lang` to only ever force a language explicitly requested,
  letting auto-detection run otherwise (including for the default 'c++')",
  so `_attach_header_graph`'s own `_clang_header_dump` call computes the
  *identical* cache key and hits the in-process AST memo instead of paying a
  second clang invocation. That assumption — "an explicit `--lang c++` and
  auto-detection converge on the same result anyway, so unifying their cache
  keys is free" — is false whenever the header itself is language-ambiguous:
  `_resolve_force_cpp`'s auto-detection (`_detect_cpp_headers`/
  `_detect_cpp20_headers`/an explicit `-std=c++NN`) requires the header to
  contain *some* C++-only syntax, but a plain POD struct with no such syntax
  (`struct Widget { int x; int y; };` — ordinary, real C++ code, e.g. a
  value/DTO type) compiles as valid C too and auto-detects as C. Reproduced
  end-to-end: `abicheck dump lib.so -H widget.h --ast-frontend clang --lang
  c++` silently parses `widget.h` in **C mode** for the primary snapshot
  (confirmed via the raw clang AST: a plain `RecordDecl`, not
  `CXXRecordDecl`, no `definitionData` at all) — with no error, warning, or
  any user-visible sign the explicit `--lang c++` was overridden — while the
  *same* `dump`'s internal `_attach_header_graph` pass, reached through a
  different `lang` derivation one call removed
  (`abicheck/service.py:1281`/`abicheck/service.py:608`'s ELF branch shares
  the identical `lang if lang == "c" else None` squash, so it too loses the
  signal — the divergence traced here is specifically the ELF `_dump_elf`
  helper's own second, ADR-050/G31-era call site,
  `abicheck/cli_dump_helpers.py:1717`, versus `_clang_header_dump`'s
  documented "an explicit `--lang c++`/`cpp` always wins" contract at the
  function it's calling into — the squash happens one layer *above* that
  contract, defeating it before it ever runs). Concrete, silent correctness
  cost: `RecordType.is_standard_layout`/`is_trivially_copyable` (and any
  other clang-only, C++-semantic-only fact — `_clang_record_type_traits`
  itself documents "a plain C `RecordDecl`... genuinely absent", the correct
  behavior *for C mode*, just not the mode the user asked for) silently read
  `None` instead of a real value for a header that would parse identically
  either way *except* for these C++-only facts, with the user's own explicit
  override having no effect. **Not fixed here**: the squashing is not an
  oversight — it is a deliberate, twice-Codex-reviewed cache/memo-consistency
  design (see `abicheck/service.py:608`'s own comment) that this finding does
  not invalidate for the *common* case (a header with real C++ syntax
  auto-detects as C++ regardless, so squashing costs nothing there) — only
  for the specific, real, silently-wrong case: an explicit `--lang c++` on a
  syntactically-ambiguous header. A correct fix needs the squash itself to
  stop conflating "auto-detect, and it happens to reach the same verdict as
  the default" with "explicitly override, must not be silently downgraded to
  a guess" — e.g. resolving `force_cpp` once, upstream of all three call
  sites and their `_attach_header_graph` mirrors, and threading the
  *resolved* boolean (or an unsquashed `lang` plus a corrected memo key that
  hashes the resolved language rather than the raw flag) through consistently
  — a change to a shared cache-key contract three call sites and two
  Codex-reviewed comments currently rely on, not a one-line fix at any single
  site. No test in the repository currently exercises this path: every
  existing `is_standard_layout`/`is_trivially_copyable` regression test
  (`tests/test_dumper_clang.py::test_parse_types_populates_standard_layout_and_trivially_copyable`,
  `tests/test_diff_layout.py`) hand-builds the parsed AST or `RecordType`
  directly, bypassing `dumper.dump()`'s CLI-level `--lang` resolution
  entirely — closing this gap should add an end-to-end regression case in
  the shape of `tests/test_clang_header_backend_integration.py`'s existing
  siblings, verified against a real compiled library with an
  intentionally-C-compatible C++ struct, once the fix itself is designed.

  **Closed for the `abicheck dump` ELF CLI path — scoped narrower than the
  "resolve `force_cpp` once, upstream of all three call sites" design sketch
  above (G31 continuation).** Rather than a shared, force_cpp-boolean
  cache-key contract spanning `cli_dump_helpers.py`, `service.py`'s
  `_dump_elf`, and `service.py`'s PE/Mach-O `_header_graph_lang`, the actual
  fix is narrower: only `cli_dump_helpers.py`'s ELF `dump` CLI path had a
  real *internal* divergence between its own primary pass (squashed) and its
  own `_attach_header_graph` call (raw, unsquashed) — `service.py`'s
  `_dump_elf` and its `_header_graph_lang` computation already squash
  *consistently* with each other (both feed the identical normalized value),
  so `compare`'s implicit-dump path and the Python `service.run_dump` API
  were never the site of this specific divergence; PE/Mach-O's own primary
  pass (`_try_header_scoped_dump`) never squashed at all. What was missing
  everywhere is the one bit no string-normalization scheme can recover on
  its own: whether `--lang` was genuinely given on the command line, since
  Click's own default for `--lang` (`LANG_DEFAULT`, `cli_options.py`) is the
  identical string `"c++"` a real `--lang c++` produces. `dump_cmd` now
  resolves this once via Click's own parameter-source tracking
  (`click.get_current_context().get_parameter_source("lang") ==
  click.core.ParameterSource.COMMANDLINE`) and threads a new
  `lang_explicit: bool` keyword parameter through `perform_elf_dump`, which
  derives one `_effective_lang` (the real `lang` when explicit or `"c"`,
  else `None`) and passes that identical value to *both* the primary
  `dump()` call and the `_attach_header_graph` call, instead of the primary
  pass's own one-off squash and the graph pass's raw pass-through. This also
  fixes a second, previously-undocumented half of the same divergence in the
  *other* direction: on a plain default invocation (no `--lang`, still
  `lang="c++"`), the header-graph pass previously force-parsed C++
  unconditionally (since a non-empty `lang` was always treated as explicit
  by `_resolve_force_cpp`), while the primary pass correctly auto-detected —
  so even a default, no-flags `dump --ast-frontend clang` could already
  silently disagree with itself between its own primary snapshot and its
  own embedded header-graph. Verified end-to-end against a real compiled
  library with an intentionally-C-compatible POD struct (`struct Widget {
  int x; int y; };`, exactly this entry's own repro shape) through the real
  `abicheck dump` CLI, not a hand-built AST or `RecordType` — see
  `tests/test_clang_header_backend_integration.py::
  test_cli_dump_explicit_lang_cpp_forces_cpp_mode_on_ambiguous_header`.
  **Closed for `service.run_dump`/`DumpRequest`/`CompareRequest` in a later
  pass, once a real conda-forge castxml build (0.7.0, within the
  `>=0.6.11,<0.8.0` policy range — `castxml_policy.py`) was available in this
  environment to verify against, alongside clang 18.** Rather than the
  `lang: str | None = None` tri-state default this entry originally
  sketched — a public-API *shape* change with a wide, hard-to-verify blast
  radius across every `resolve_input`/`run_dump` caller (`compare`, `scan`,
  `appcompat`, `l0_export_delta`, ...), most of which still legitimately pass
  a concrete, Click-defaulted `lang` string that must keep auto-detecting —
  the actual fix is the same **additive** `lang_explicit: bool = False`
  parameter the CLI fix above already established, generalized one layer
  up: `service.resolve_input`/`run_dump`/`_dump_elf`/`_dump_pe`/
  `_dump_macho` and `service_header_scoped._try_header_scoped_dump` all gain
  it (default `False`, a no-op — every existing caller's behavior is
  bit-for-bit unchanged), `DumpRequest.lang_explicit`/
  `CompareRequest.lang_explicit` carry it on the typed API, and
  `service_dump_pipeline.run_dump_request`/`service_compare_pipeline.
  resolve_compare_request` thread it through `resolve_side_snapshot` (their
  one shared per-side resolution function) to `service.resolve_input`. The
  `dump`/`compare` CLIs resolve it the identical way `dump_cmd` already did
  — `compare_cmd` mirrors the established `_frontend_explicit`/
  `_nostdinc_explicit` `ctx.get_parameter_source(...)==COMMANDLINE` pattern
  already used one function over in `cli_compare_helpers._embed_inline_
  source_side` for `--ast-frontend`/`--nostdinc`, extended to `--lang` and
  threaded through `cli_resolve._resolve_compare_snapshots` into
  `CompareRequest`. The whole-snapshot disk cache key
  (`service_dump_cache._dump_cache_extra_key`/`cached_run_dump`) folds
  `lang_explicit` in too — the identical `lang` string now legitimately
  resolves to two different parsed ASTs depending on it, so a cache entry
  for one must never serve the other. `scan`/`appcompat`/the
  release/set-input fan-out/`l0_export_delta` are **not** touched by this
  pass — they still pass their own already-resolved, Click-defaulted `lang`
  string with `lang_explicit` defaulted `False`, so they keep their
  pre-existing (unfixed, but also not regressed) behavior; wiring each is
  the identical mechanical pattern applied here, left for its own follow-up
  rather than expanding this pass's verified surface further. Verified
  end-to-end against the same real, intentionally-C-compatible POD struct
  through `service.resolve_input`, `DumpRequest`/`run_dump_request`,
  `CompareRequest`/`resolve_compare_request`, and the real `compare` CLI
  (Click parameter-source spy) — see
  `tests/test_clang_header_backend_integration.py::
  test_dump_request_and_compare_request_lang_explicit_forces_cpp_mode` and
  `tests/test_service_dump_cache.py`'s
  `test_differs_by_lang_explicit`/`test_lang_explicit_reaches_run_dump_and_keys_separately`.
- **Opaque-type suppression is keyed by bare `RecordType.name`, not a
  qualified identity — pre-existing on both header backends, newly reachable
  on direct-clang by PR #719's opaque-handle-type fix (Codex review,
  investigated, not fixed).** `diff_filtering._find_opaque_types()` (and its
  siblings `_find_by_value_types()`/`_downgrade_opaque_type_changes()`) index
  a snapshot's opaque/impl-private types by `t.name` alone, and
  `_root_type_name()` derives a `Change`'s matching key from `Change.symbol`
  the same way `diff_types.py` stamps it — also the bare name for a
  top-level type change (`name = t_old.name`), never `qualified_name`. Two
  distinct records sharing a bare name in different namespaces (a complete,
  genuinely public `api::Foo` and an unrelated forward-only `impl::Foo`)
  therefore collide in the same `opaque: set[str]`: if `impl::Foo` is opaque,
  `_downgrade_opaque_type_changes()` silently suppresses a real
  `TYPE_SIZE_CHANGED`/field-change finding on the unrelated, complete,
  public `api::Foo` too, since both match the bare key `"Foo"`. Confirmed
  by reading the code (no live repro run); this predates PR #719 and
  already applied identically to castxml's own `is_opaque=True` types — the
  PR's clang-backend opaque-stub fix (previously clang silently dropped
  every forward-decl-only type instead of emitting a stub) makes this
  reachable from a new source, not a new bug class. **Not fixed here**: a
  correct fix needs qualified identity threaded consistently through
  `_find_opaque_types`/`_find_by_value_types`/`_downgrade_opaque_type_changes`
  and `_root_type_name`'s several call sites in `diff_filtering.py` (at
  least five, by grep), none of which currently have test coverage for the
  cross-namespace-collision case to validate a change against — a
  systematic, cross-cutting rework, not a scoped fix reactive to one review
  comment. Filed here rather than attempted under this PR's time budget,
  per this file's own "known gaps over risky reactive patches" convention.
- **`dumper_clang.py`'s `parse_types()` conflates a C/C++ tag-namespace
  identity with an ordinary-namespace typedef identity that happens to
  share the same spelling — pre-existing, not introduced by PR #719's own
  changes, but investigated and confirmed reachable in this pass (Codex
  review, investigated, not fixed).** For a legal (if unusual) header like
  `struct Foo; typedef struct { int x; } Foo;` — an unrelated forward-only
  tag `Foo` and a SEPARATE anonymous struct given the ordinary-namespace
  name `Foo` via typedef, which C's two-namespace rule keeps genuinely
  distinct — `parse_types()`'s `identity = "::".join([*entry.scope, name])`
  computation uses the same bare `name` for both (the tag's own `name`,
  and the anonymous record's `anon_names`-derived typedef fallback name),
  so the two collide into one `identity` key. Reproduced directly against
  `_ClangAstParser`: only ONE `RecordType` named `Foo` is emitted (the
  typedef-backed definition), and the unrelated opaque tag `struct Foo;`
  is silently absent from the snapshot entirely — the exact regression
  PR #719's own opaque-handle-type fix was written to prevent, just
  reached through a different, adjacent mechanism (a spelling collision
  across namespaces rather than declaration-order/redecl-set instability).
  **Not fixed here**: closing it correctly needs the tag-namespace vs.
  ordinary-namespace distinction threaded through the `identity` key
  itself (and every downstream consumer that currently assumes `identity`
  uniquely names one type — `_build_record`, the opaque/deprecated/kind
  merge maps this same function already builds), which is a real, if
  narrow, data-model change to a function this same PR already revised
  three times this session (the opaque-stub fix, the kind-canonicalization
  fix, and that fix's own regression fix) — each of which independently
  needed careful re-verification against `dumper_clang.py`'s exact
  2000-line hard cap. A fourth, differently-shaped change to the same
  function under continued review pressure is exactly the risk profile
  this file's own "known gaps over risky reactive patches" convention
  exists to avoid; a correct fix needs its own dedicated pass with fresh
  test coverage for the namespace-collision case specifically, not a
  same-session extension.
- **The castxml L4 source-ABI extractor does not fold the resolved
  EMULATED compiler's identity into either `fact_set.compiler_version` or
  the D8 TU cache key — attempted once for the persisted half, and
  REVERTED after a follow-up review caught it as a real regression, not a
  fix (Codex review, PR #719, three follow-up rounds).** castxml shells
  out to the emulated compiler (`cc_bin`, resolved per compile unit by
  `pick_compiler_binary` — the real build's own recorded `argv[0]`, absent
  an explicit `--gcc-path` override) purely to discover its built-in
  defines/include paths (`docs/learn/architecture.md`), so a header
  conditional on `__GNUC__`/`_MSC_VER` can extract differently once that
  compiler is upgraded at the same path, even though castxml itself and
  its own `--version` probe stay identical — a real gap. The second
  follow-up round's fix folded a STAT signature (`dev`/`ino`/`mtime_ns`/
  `size`) of the resolved `cc_bin` into `fact_set.compiler_version`
  (`_stamp_fact_set_and_coverage()` already has the per-TU `compile_unit`
  in scope). The third follow-up round found this made things WORSE, not
  better: those stat fields are filesystem-local, so (1) two TUs in ONE
  surface resolved to DIFFERENT but same-toolchain drivers (`gcc` for a
  `.c` TU, `g++` for a `.cpp` TU — an entirely ordinary mixed-language
  build) get different suffixes purely from being different files on
  disk, tripping `rollup_fact_set()`'s exact-equality check into
  `fact_set_inconsistent` for a perfectly healthy, unchanged surface; and
  (2) an identical build run on two different machines/paths (baseline
  collected in one CI run, compared against another — an entirely
  ordinary workflow) never shares device/inode at all, making every such
  cross-machine comparison spuriously inconsistent and silently
  suppressing every structured/opaque/source-edge finding. Between
  "silently under-detect genuine toolchain drift" (the pre-existing gap)
  and "spuriously suppress every finding on completely ordinary mixed-
  language or cross-machine comparisons" (the attempted fix), the second
  is strictly worse for typical usage, so the stat-based fold was
  reverted rather than patched further under review pressure. **Not fixed
  here, either half**: a correct fix needs a portable SEMANTIC identity —
  a real `cc_bin --version` probe, normalized (mirroring
  `_castxml_tool_version`'s existing shape, but for an arbitrary
  gcc/clang/MSVC driver rather than castxml specifically) — not
  filesystem stat fields, for the persisted half; the D8 cache-key half
  additionally needs a wider, per-instance-hook-signature change to
  `source_replay.py`'s shared cache-key infra (also used by
  `ClangSourceExtractor`, whose analogous `--gcc-path` case resolves once
  per extractor construction, not per TU, so its existing zero-arg hook
  shape doesn't transfer directly), plus a decision on probing cost (a
  version probe per distinct resolved `cc_bin` across a build with mixed
  toolchains, cached the same way `_castxml_tool_version`'s `lru_cache`
  already is). Left for a dedicated follow-up with its own MSVC-vs-
  GCC-vs-Clang version-probe design, not a same-PR reactive patch.
- **A compatible-but-ambiguous opaque redeclaration set (`class H; struct
  H;`) that later gains a same-key COMPLETE definition still produces a
  false `SOURCE_LEVEL_KIND_CHANGED` — investigated, not fixed (Codex
  review, PR #719, fourth follow-up round on this same area).** The
  earlier "keep the definition's own kind" fix (this same file, above)
  deliberately never applies `dumper_clang.py`'s canonicalized opaque
  `override_kind` to a record that survives as a COMPLETE definition — the
  definition's own real, unmodified `_record_kind()` always wins, which is
  correct in isolation (a real kind change on the definition itself must
  never be hidden). But this means an identity whose opaque forward decls
  were genuinely ambiguous (`class H;` AND `struct H;`, both present,
  canonicalized to the fixed `"struct"` spelling per the kind-stability fix
  above) reports "struct" in an old snapshot with no definition, while a
  new snapshot adding a same-key `class H {...};` definition reports the
  definition's real "class" — a spurious kind-change finding even though
  "class" was always one of the two already-compatible, already-declared
  keys. Reproduced directly against `_ClangAstParser`. **Not fixable at
  the extraction layer**: an opaque snapshot has no way to know in advance
  which of the ambiguous, compatible keys a LATER definition will use, so
  no fixed canonicalization choice can match every possible future
  definition. The generic comparison (`diff_types_abicc_parity.
  _diff_type_kind_changes()`, shared across all producers) does a plain
  `t_old.kind != t_new.kind` check with no notion of "this kind was
  extracted from a genuinely ambiguous, unresolved forward-decl set" —
  and correctly so, since blanket-suppressing every class↔struct
  transition would hide the genuine, intentional ones this detector
  exists to catch. Closing this properly needs new PROVENANCE carried on
  `RecordType` itself (e.g. a `kind_ambiguous`-shaped field, schema-
  versioned, populated by both header backends, read by the diff layer to
  skip exactly this one shape of transition) — a cross-cutting model
  change touching `model.py`, both backends, serialization, and the
  detector, not a scoped fix reactive to one review comment, and
  `dumper_clang.py`'s `parse_types()` has already been revised four times
  in this same PR session for adjacent findings in this exact area. Filed
  here per this file's own "known gaps over risky reactive patches"
  convention rather than attempted under continued review pressure.
- **`advanced_facts_collected` infers "advanced DWARF extraction ran" from
  its output rather than recording it.** The predicate answers by checking
  whether any field `dwarf_advanced.diff_advanced_dwarf` consumes is
  non-empty, plus `target_arch` as a discriminator for a parse that completed
  but established nothing (`dwarf_advanced` sets it from the ELF header;
  the presence-only helpers leave it `""`). That is sound for every shape
  reachable today, and it is mutation-checked per consumed family in
  `tests/test_debug_evidence_presence.py`. But it is still an inference: the
  honest signal is a **status**, not a reconstruction from findings.

  The reason it matters is asymmetric. `checker.py` requires the predicate on
  *both* sides, so any shape the inference gets wrong does not merely mislabel
  coverage — it disables the `advanced_dwarf` detector wholesale and the run
  silently misses real drift. That is a false negative, the worse direction,
  and two such shapes were already found by review during one PR (the three
  `toolchain` flag sets, then the successful-but-empty parse).

  **The durable fix is an explicit "advanced extraction completed" field on
  `AdvancedDwarfMetadata`**, set where the parse succeeds and persisted, with
  the predicate reading it instead of inferring. Not folded into the fix PR
  because it is a persisted-model change: a new field means a
  `SCHEMA_VERSION` bump and a decision about how a pre-bump snapshot reads
  (almost certainly "unknown", resolved conservatively), which this file's own
  contract says gets its own ADR and migration rather than riding along with a
  behavior fix. Until then the inference stands, and any new consumer of
  `AdvancedDwarfMetadata` must be added to `_CONSUMED_ADVANCED_FIELDS` in that
  test or it will silently disable the detector again.

- **A pre-fix clang-backend baseline compares against a post-fix candidate
  as a wave of `FUNC_BECAME_INLINE` — no reliability flag guards
  `Function.is_inline`.** The implicit-inline fix
  (`extract/headers/clang/inline_semantics.py`) changed what the clang
  header backend *records* for a `constexpr`/in-class/`= default`
  declaration, from `False` to the correct `True`. A snapshot dumped with
  the clang backend before that fix therefore carries a wrong value, and
  `diff_symbols._check_inline_transitions` compares `is_inline` raw on both
  sides with no producer or generation gate — so a stored baseline from
  before the fix, compared against a freshly-dumped candidate, reports
  `FUNC_BECAME_INLINE` (RISK) for every such declaration. The findings are
  spurious: nothing about the library changed.

  **Bounded, with a one-step remedy.** castxml is the default header
  backend and always recorded these as inline, so only a baseline captured
  under the opt-in `--ast-frontend clang` / `ABICHECK_AST_FRONTEND=clang` is
  affected, and re-dumping that baseline clears it permanently. The findings
  are RISK-class, never breaking, so no gate flips from pass to fail.

  **The proper fix is the established one, and it is a schema change.** This
  codebase already has the mechanism for exactly this situation — a fact
  whose stored value is wrong in snapshots from an earlier generation:
  `clang_restrict_facts_reliable`, `clang_va_list_facts_reliable`,
  `param_kind_facts_reliable` and their siblings
  (`model/snapshot_reliability.py`), each set `False` at load time for a
  snapshot below a `_MIN_SCHEMA_VERSION_FOR_*` threshold from the affected
  producer, and read by the consuming detector, which then declines rather
  than fabricating a finding. Adding `clang_inline_facts_reliable` the same
  way is the complete fix. It is **not** folded into the fix PR because it
  cannot work without bumping `SCHEMA_VERSION` (a pre-fix snapshot is v46,
  and so is a post-fix one — there is otherwise no way to tell them apart),
  and this file's own contract is that a schema change gets its own ADR and
  migration rather than riding along with a behavior fix. It also lands in
  `policy/analysis_assurance_degraded_facts.py`'s `consulted_when` map,
  whose every-flag-has-a-key invariant is enforced by a `KeyError` rather
  than a check, and — if `is_inline` is converted to a `Fact[bool]` at the
  same time, which is the `param_kind` precedent — in the storage backfill
  rules too. Recorded here rather than attempted under the same review, per
  this file's own convention.

- **Linkage-blind removal — attempted twice, reverted twice. The evidence
  keeps proving something adjacent to the invariant.** A symbol vanishing from
  the export table is reported as `func_removed` (and, on the same symbol,
  `func_deleted_elf_fallback`) regardless of its *linkage*, so a weak
  vague-linkage export reads as a hard break. The demotion's invariant is
  *"every consumer already emitted its own copy"*, and both attempts
  established something else:

  1. **`Function.is_inline`** proves the declaration's inline *linkage*, not
     that a definition exists. Verified against real clang: `inline int f();`
     yields `inline=True, has_body=False`. This stays true after the
     implicit-inline fix (`extract/headers/clang/inline_semantics.py`), which
     widened the field to cover `constexpr`/`consteval`, in-class definitions
     and in-class `= default` so the two backends agree: those are all still
     *linkage* facts about the library's own declaration, and none of them
     says a consumer emitted a copy — which is the shared shape this entry
     exists to name. Read it as "may have vague linkage", never as "every
     consumer already has its own definition".
  2. **COMDAT-group membership** proves the *library* used vague linkage, not
     that its *consumers* did. `extern template` is the counterexample, and it
     is ordinary code: a public header carrying `extern template struct
     Box<int>;` tells every consumer TU **not** to instantiate, while the
     library's own explicit instantiation still emits a weak COMDAT
     definition. Verified against g++ — the library object has
     `_ZNK3BoxIiE3getEv` inside a COMDAT group while the consumer object has
     it as `NOTYPE GLOBAL UND` with an empty COMDAT set. Dropping that export
     breaks the consumer, and the predicate demoted it (Codex review).

  The shared shape is worth naming, because it is what a third attempt will
  hit too: a fact about the **library's own build** was read as a fact about
  **its consumers**. Nothing in two library snapshots, and nothing in one
  library's object files, distinguishes "the consumer emitted a copy" from
  "the consumer holds an undefined reference". Only consumer-side evidence, or
  a header fact recording `extern template` (which castxml does not expose —
  checked through 0.7.0), can separate them.

  **Kept, because it is sound and answers a real question:**
  `buildsource/comdat_groups.py` — an ELF `SHT_GROUP`/`GRP_COMDAT` parser
  (byte-order correct, ELF-only, degrading to diagnostics on unreadable
  objects) and its `BuildEvidence.comdat` collection. It answers "did *this
  build* emit this symbol vaguely", which is genuinely useful and was already
  reserved vocabulary in `graph_facts.py` (`comdat_group`) for a future
  linker-artifact extractor. It is simply not sufficient for the demotion.

  **Separately open, and independent of the above:** the L3 collection path
  does not deliver usable object paths. `CompileUnit.output` is a label
  normalized *for persistence*, not a path — home paths redacted to `~/...`
  (ADR-032 D7), Ninja/Make/Bazel outputs relative to `CompileUnit.directory` —
  and `CompileDbAdapter` never assigns it at all, discarding the compile
  database's `output` field and `-o` alike. **Half-closed on the reading
  side:** `build_evidence._resolved_object` now expands `~` and joins a
  relative label onto the unit's own `directory`, and skips a label naming no
  file that exists, so an adapter that *does* record an output is readable
  from outside the build directory. That also makes the scan conservative
  where it used to be destructive: a build whose objects resolve to nothing
  leaves `comdat` untouched rather than replacing a scan loaded from an
  existing pack with an empty, unresolvable one, and a fresh scan that
  established nothing never displaces one that did. **Still open:** the
  producing side — `CompileDbAdapter` recording an output at all, and every
  adapter keeping the raw resolved path alongside the redacted label, so a
  pack collected on one machine can be scanned on another. Until then the
  scan is opt-in (`ABICHECK_COLLECT_COMDAT=1` at `inline.py`'s call site):
  parsing every object's symbol table is real I/O, and no detector consumes
  the result.

- **A third instance of the same shape (code-review report item 3):
  demoting a stdlib closure instantiation as "unnameable" — attempted,
  reverted.** A stdlib/runtime template instantiated over a caller-
  supplied lambda (e.g. `std::once_flag::_Prepare_execution<...Widget::
  run()::{lambda()#1}...>`, from a real `std::call_once` guard) mangles to
  a symbol whose closure-ordinal encoding is per-translation-unit and
  compiler-ordering dependent, so it seemed unconditionally safe to demote
  in `surface.classify_change_surface`: "no consumer's *source code* could
  ever name this exact template argument, so there is no possible
  external caller to break." That reasoning is the identical mistake
  the linkage-blind-removal entry above already names, just one layer
  removed: *source-level nameability* is not *binary/ABI compatibility*.
  A consumer's own object code never has to name the symbol in source —
  the SAME template, instantiated from the SAME public header over its
  own local lambda, produces the IDENTICAL mangled symbol in the
  consumer's own translation unit via vague/weak linkage, and that
  consumer can depend on the library's copy being the one that resolves.
  A two-snapshot comparison has no way to rule that out, for exactly the
  reason the entry above states: "nothing in two library snapshots...
  distinguishes 'the consumer emitted a copy' from 'the consumer holds an
  undefined reference'." Reverted rather than shipped (Codex review,
  two findings — the unsoundness above, and separately that the fix was
  dead code for its own ELF-only motivating case:
  `post_processing.FilterNonPublicSurface.run` returns unmodified changes
  before ever calling `classify_change_surface` when neither side's
  surface is resolvable). Closing this for real needs the same
  consumer-side evidence the linkage-blind-removal entry says is missing
  — not a cleverer read of the mangled name alone.
- **`Function.elf_binding`/`Variable.elf_binding` (and the pre-existing
  `elf_visibility` it mirrors) collapse mixed bindings across symbol-versioned
  aliases sharing one bare name — investigated, not fixed (Codex review,
  fresh evidence).** `ElfMetadata.symbol_map` is a `name -> ElfSymbol` dict
  built from `elf.symbols`, last-entry-wins; `elf_metadata.py`'s own parser
  deliberately never embeds a version suffix in `ElfSymbol.name` (pyelftools
  doesn't decode `@@`/`@` into the name either — see the `_pyelftools_exported_symbols`
  comment), so two legally distinct versioned definitions of one exported
  name — e.g. a `GLOBAL` `foo@GLIBC_2.2` and a `WEAK` `foo@@GLIBC_2.14` —
  collapse to a single dict entry, and `_populate_elf_visibility` (which both
  fields share) reads only that survivor. A `Function`/`Variable` whose
  `mangled` matches such a bare name therefore reports whichever binding
  happened to parse last, not "the" binding — and if that happens to be
  `WEAK` while an older, still-live version was `GLOBAL`, a `binding: weak`
  suppression rule (`suppression.py`) could match a removal that, from the
  `GLOBAL` version's perspective, is a real break. Not fixed here: a correct
  fix needs `symbol_map` (or a sibling index) to preserve every versioned
  entry per bare name rather than collapsing to one — a change with several
  other consumers beyond `elf_visibility`/`elf_binding` (`dumper_elf_symbols.py`'s
  own callers, `diff_versioning.py`'s existing version-aware machinery, which
  already correlates symbols against `versions_defined`/`versions_required`
  through a different path) that deserves its own scoped design rather than a
  drive-by change to a cached property several modules depend on today. Per
  this file's own "known gaps over risky reactive patches" convention.

- **Default dependency scoping (PR #649) vs. contextual reachability
  (`type_reachability.py`) — the direct-reference conflict is fixed; the
  comparability-contract gap is not.** A status-review follow-up flagged
  that `dump`'s default header-origin scoping (`dumper_scoping.py`) and the
  same-pass contextual-reachability work were pulling in opposite
  directions: reachability says a dependency type directly named in a
  public signature (`std::string` taken by a public function, or a
  platform type like `struct tm`) is genuinely part of the library's ABI
  contract, while scoping unconditionally dropped every declaration whose
  own header was a toolchain/system header, regardless of whether anything
  referenced it directly. Fixed: `scope_snapshot_excluding_dependencies`
  now retains a dependency-header type/enum that is directly named by a
  kept declaration's own return/parameter/variable type or by a kept
  type's own field/base (`_directly_referenced_dependency_names`), while
  still dropping what's only reachable transitively through that type's
  own internals (`std::string::_Alloc_hider` and the like stay excluded).
  **Still open, deliberately not attempted in the same change:** the
  chosen dependency-scoping mode (scoped vs. `--include-system-declarations`) is
  not part of the `ExtractionContract` `scope_fingerprint`
  (`comparability.py`'s `SCOPE_FIELD_KEYS`), so two snapshots extracted
  under different scoping modes can still compare as "comparable" even
  though they don't share the same fact universe — and `cli.py`'s inline
  (non-persisted) `compare old.so new.so` path still hardcodes
  `include_dependencies=True` regardless of what a persisted baseline JSON
  on the other side of the same comparison was scoped with. Closing that
  gap needs its own scoped design (a new `SCOPE_FIELD_KEYS` entry plus a
  `comparability.py`-level compatibility rule, verified against
  `test_comparability_gate.py`'s existing superset-growth assertions), not
  a drive-by extension of the direct-reference fix above. Until then, the
  safe authoritative flow for a compiler/stdlib-sensitive comparison is
  either `--include-system-declarations` on both `dump` invocations, or comparing
  two default-scoped persisted snapshots against each other rather than
  mixing a persisted baseline JSON with a live-binary operand.

- **Depth contract, CLI vs. API/MCP — closed for real by G33 Phase 5, having
  first been closed as stale.** Finding 1 below is now out of date in a way
  worth keeping visible rather than deleting: it closed this gap on the
  grounds that no service/MCP surface *promised* a depth-qualified snapshot,
  so there was nothing to extend the gate to. That was true when written and
  is no longer: `DumpRequest.depth` and the MCP `abi_dump`'s `depth` argument
  are exactly such a promise, and `service_dump_pipeline.run_dump_request`
  enforces it — the same floor, raising the Tier-2 `ValidationError` where the
  CLI path raises `DumpDepthNotSatisfiedError`. Note which direction the fix
  went: the gap was closed by giving the typed surface the *capability* and
  the gate together, not by extending a gate to a surface that had no
  capability to gate. Finding 2 is unchanged and still correct. The original
  text follows.

  This entry previously said PR
  #601 (which adds a hard-fail `DumpDepthNotSatisfiedError` when an explicit
  `dump --depth` isn't actually reached, in `cli.py`/`cli_dump_helpers.py`)
  was still open, and that `abicheck/service.py`'s `ScanRequest`/
  `run_scan_subprocess` and `abicheck/mcp_server.py`'s MCP tools needed the
  same check extended to them once it merged. PR #601 merged 2026-07-19.
  Re-checking what "extend the same check" would actually mean turned up two
  separate findings, both closing this gap rather than giving it new code:
  1. `check_requested_depth_satisfied` (the strict gate PR #601 added) is
     called from exactly one place, `cli._write_snapshot_output` — reached
     only by the `dump` command and one `cli_buildsource.py` snapshot-writing
     helper. Neither `service.py`'s `run_dump`/`resolve_input` nor the
     `abi_dump` MCP tool accept a `depth`/`sources`/`build-info` parameter at
     all (confirmed by reading both) — there is no service.py/MCP surface
     that promises a depth-qualified persisted snapshot for this gate to
     extend to.
  2. The only place a caller *can* pass an explicit `depth=` through
     `service.py`/MCP is `ScanRequest`/`abi_scan`/`abi_estimate` — and
     `service_scan.run_scan`, the CLI `scan` command
     (`cli_scan.py`), and the MCP `abi_scan` tool all call the exact same
     `scan_engine.run_scan_core`, so they already share one evidence-contract
     implementation (`_check_scan_evidence_contract`'s pinned-depth
     `_EvidenceContractError`, ADR-037 D5) — there was never a CLI-vs-API/MCP
     disparity on the `scan` side to close, before or after PR #601.
  `_validate_public_depth`'s docstring in `mcp_server.py` carried the same
  stale "PR #601 open, tracked as remaining work" wording and was corrected
  alongside this entry.

- **Action pinning is deliberately partial, not a full sweep.** Third-party
  GitHub Actions in `.github/workflows/agentready.yml`, `ci.yml` (the
  `id-token: write` jobs), `pages.yml`, `publish.yml`, `security.yml`, and
  `schedule-check-project-failure-path.yml` (its one `dispatch` job) are
  pinned to a full commit SHA (with a `# <tag>` comment) rather than a
  mutable tag/branch — those carry `security-events:write`,
  `pull-requests:write`, `contents:write`, `actions:write`, or
  `id-token:write` (OIDC/PyPI Trusted Publishing), so a re-pointed tag there
  is a real supply-chain risk.
  The root `action.yml` (the composite Action third-party repos consume
  directly) is pinned the same way, for the same reason: its final step
  conditionally runs `github/codeql-action/upload-sarif` under whatever
  `security-events: write` permission the *consuming* workflow grants it, so
  it carries the same blast radius as the elevated-permission workflows
  above even though this repo's own CI doesn't invoke it with that scope.
  Other workflows (`test-action.yml`, `eval-suite.yml`, `performance.yml`,
  `realworld-validation.yml`, `dependency-review.yml`, and any future ones)
  still use tags — deliberately deferred, since they only run with
  `contents: read` and don't touch secrets/publishing/security-event write
  access, so the blast radius of a compromised tag there is far smaller.
  Extend the same pinning to a workflow only when it gains elevated
  permissions, not preemptively.
- **CODEOWNERS risk tiers currently all resolve to one person.** The file is
  structured by risk tier (CRITICAL/HIGH/STANDARD) so a second maintainer
  can be slotted into CRITICAL/HIGH without restructuring, but there is
  only one maintainer today — don't read the tiering as "these are reviewed
  by different people," it isn't, yet.
- **Toolchain-profile compiler-family rendering — audited, `args` trust
  boundary hardened; the `-stdlib=`/`--target=` "fix" itself was wrong and
  has been reverted.** An external audit found `run_plan.py`'s
  `_compose_gcc_options()` composing `-stdlib=`/`--target=` unconditionally
  for any `profiles.<id>.compile` overlay, even when
  `compile.compiler_family: gcc` — both are Clang-driver-only spellings a
  real GCC binary rejects (confirmed against GCC 14.2), so an early pass
  dropped both whenever `compiler_family` resolved to a GCC family name. A
  later review round found that fix backwards: the composed string this
  function returns is **never actually fed to a literal GCC binary
  anywhere in this pipeline** — `--ast-frontend` only has
  `auto`/`castxml`/`clang`/`hybrid` (no `gcc`); castxml's own frontend is
  always its internal bundled Clang (`--castxml-cc-<id>` selects an
  *emulation* mode, not a literal execution path); and the direct-clang
  backend's `_resolve_clang_bin` (`dumper_clang.py`) explicitly rejects a
  `gcc-path` that isn't clang-family and falls back to host
  `clang`/`clang++`. Since the real consumer is always Clang, dropping
  `--target=` actively broke cross-compilation-target correctness for the
  direct-clang backend — it was the *only* signal available there to steer
  parsing away from the host architecture (no "probe the real compiler"
  auto-discovery step exists on that path the way castxml has one), so a
  GCC-family profile with an explicit `target:` would silently have its
  headers parsed for the runner's architecture instead. Reverted:
  `_compose_gcc_options()` emits `-stdlib=`/`--target=` unconditionally
  again, same as before the original audit, with both the change and the
  reasoning for reverting it recorded in the function's own docstring so a
  future reader doesn't rediscover and re-"fix" the same false positive.
  The same original audit flagged a real trust-boundary gap in
  `profiles.<id>.compile.args`, which is unaffected by this revert and
  stays fixed: the existing whitespace-
  smuggling check (`_safe_profile_atom`) rejected one YAML scalar expanding
  into multiple argv tokens, but not a single, whitespace-free dangerous
  atom. `_DANGEROUS_ARG_PREFIXES` (`project_targets.py`) now blocks four
  families of these: direct code-loading flags (`-Xclang`, `-load`,
  `-fplugin=`, `-fpass-plugin=`), file/argv re-expansion (`@response-file`,
  Clang's `--config`/`--config=`), driver command-line substitution
  (`-specs=`/`--specs=`, `-wrapper`), and — added across two follow-up
  review rounds on the same PR, since each is the same underlying
  "opaque subprocess-forwarding" mechanism as the others — GCC's
  `-Wa,`/`-Wp,`/`-Wl,` (comma-joined payload passed straight to the
  assembler/preprocessor/linker; `-Wp,-fplugin=./evil.so` reaches cc1 the
  same as a bare `-fplugin=`, `-Wl,-plugin=./evil.dso` loads an LTO linker
  plugin) and Clang's `-Xpreprocessor`/`-Xassembler`/`-Xlinker`
  (separate-argument equivalent of `-Xclang`). A third review round found a
  deeper issue than another missing flag spelling: every `compile.*` atom
  (not just `args`) now also rejects quote (`'`/`"`) and backslash (`\`)
  characters, since `_compose_gcc_options` space-joins every field into one
  string that `dumper.py`'s `--gcc-options` handling later re-splits with
  `shlex.split()` — an atom like `"'-fplugin=./evil.so'"` starts with a
  quote, not `-fplugin=`, so the prefix denylist alone accepted it, but
  POSIX shlex quote-removal reconstitutes the exact blocked flag on
  re-split (confirmed with an actual `shlex.split()` round-trip). Two more
  review rounds each found a flag real for the mechanism it names but
  empirically NOT exploitable through abicheck's actual pipeline —
  verified rather than taken on faith, and blocked anyway since doing so
  is free: `--castxml-cc-` (a second occurrence naively looks like it
  could replace the trusted `--castxml-cc-<id> <path>` pair
  `dumper_ast_config.py` composes ahead of `args`, but real castxml
  0.6.3 hard-rejects any repeated `--castxml-cc-*` occurrence at
  argv-parse time instead of silently substituting the compiler); and
  `-B<dir>`/`-B <dir>` (GCC's compiler-component search path override
  really does let a planted `cc1`/`cc1plus` run instead of the real one,
  confirmed against real GCC — but every consumer of this composed
  string is Clang, not GCC, and Clang re-execs itself via `-cc1` rather
  than spawning a separate, `-B`-discoverable one; confirmed neither
  castxml's internal bundled Clang nor the direct `--ast-frontend clang`
  backend ran a planted `cc1` with `-B` set). A fifth review round found a
  flag family that IS actually exploitable through this pipeline, unlike
  the two immediately above: clang-cl's (Clang's MSVC-compatible driver
  mode — reachable via a `compile.binding` whose path stem contains
  "clang", e.g. `clang-cl`/`clang-cl.exe`, which
  `dumper_clang._is_clang_family_binary` recognizes as clang-family)
  `/clang:<arg>` escape hatch forwards an argument straight to the
  underlying clang driver, bypassing clang-cl's MSVC-shaped option parsing
  entirely — empirically confirmed exploitable: `clang
  --driver-mode=cl "/clang:-fplugin=./evil.so" -c t.h` really does load and
  run the planted plugin. `/link <options>` (clang-cl's documented
  "forward options to the linker") is blocked alongside it on the same
  LTO-linker-plugin grounds as the already-blocked `-Wl,`, without a
  from-scratch empirical repro of that specific sub-case. A sixth review
  round found a different shape of finding again: `-cc1`/`-cc1as`, Clang's
  internal frontend mode, only activates when `-cc1`/`-cc1as` is literally
  the *first* argument after the program name (confirmed empirically:
  `-cc1` anywhere else is rejected as "unknown argument", including right
  after a leading `-I`) — but `dumper.py`'s `_build_clang_header_command`
  builds argv as `[cc_bin, *-I dirs, --sysroot, -nostdinc, *gcc_options
  tokens, ...]`, so a scan with no `extra_includes`/`sysroot`/`nostdinc`
  lets a leading `-cc1` in `compile.args` genuinely land in that
  first-argument slot. Once in cc1 mode, `-load`/`-fpass-plugin=` were
  already blocked, but cc1 mode exposes an entirely different, much larger
  argument namespace this denylist was never designed to enumerate — Codex
  found `-fcas-plugin-path` (a cc1-only flag not present in every Clang
  build) doing the identical thing. Rejected the mode switch itself rather
  than chasing individual cc1-only flags, the same reasoning as `--config`.
  This denylist is necessarily reactive to the delivery *mechanism*, not exhaustive over
  every dangerous flag a mechanism could carry — a real fix for the
  whack-a-mole shape of this (an allowlist of known-safe ABI flags instead
  of a denylist of known-dangerous ones) was suggested during review but
  deliberately not done here: `args` is documented as a general escape
  hatch for ABI-relevant flags this codebase cannot enumerate a priori
  (GCC/Clang/MSVC each have their own vocabulary), and a strict allowlist
  would need that vocabulary built out first — its own scoped project, not
  a reactive expansion of this fix. (A fourth review round briefly caught a
  correctness gap in a since-reverted sentinel the family-aware
  `_compose_gcc_options()` fix needed — moot now that the fix itself is
  reverted, see above; not detailed here since it no longer applies to any
  code that ships.) Still **not** implemented, and out of
  scope for that fix (each needs its own
  scoped design, not a drive-by extension of the same narrow correction):
  a real toolchain-identity probe that validates a resolved `binding`'s
  actual compiler family/version/target against the profile's declared
  constraints (`compiler_version` is still parsed but never checked against
  anything); a profile-specific AST frontend (there is still only one
  global `--ast-frontend`); and a genuine family-specific argv resolver —
  in particular MSVC `/std:`/`/D` spellings, which this fix does not
  attempt (no `compiler_family: msvc` caller/test exists yet to validate
  against, and a wrong guess here is worse than the pre-existing gap).
- **`ruff format` has never gated anything, and the tree has never been
  formatted — CLOSED: the tree is formatted and `fmt-check` now runs in CI.**
  Two separate problems were tangled here. (1) `ruff` was pinned in two places
  that disagreed: `.pre-commit-config.yaml` at `rev: v0.9.0`, and
  `pyproject.toml`'s `[dev]` at `ruff>=0.3` — a floor, so CI and
  `pip install -e ".[dev]"` resolved whatever was newest on the day they ran.
  That is not cosmetic: 0.9.0 reports an `F811` in
  `abicheck/cli_buildsource_helpers.py` that current ruff does not, so a
  contributor's `pre-commit` run and CI reached different *lint* verdicts on
  unmodified code. Fixed by pinning both to the same exact version, forward
  rather than back (pinning to 0.9.0 would red the lint lane on existing
  code), with `tests/test_toolchain_pins.py` asserting the two stay in
  lockstep. (2) Separately — and *not* caused by the version skew — the tree is
  not `ruff format`-clean under **any** version: 488 files under 0.9.0, 486
  under 0.16.3, ~56.5k changed lines, and the diffs are near-identical across
  versions (checked), i.e. the formatter has simply never been applied
  repo-wide. It went unnoticed because the `fmt-check` step, though present in
  `scripts/verify.py`'s `fast`/`pr`/`full` profiles, **is not run by any CI
  job**: `ci.yml`'s `lint-and-types` invokes
  `--profile pr --only lint,typecheck,docs-build`, no workflow runs the full
  `pr` profile, and `pre-commit` is not run in CI at all. So the one consumer
  that would catch it is `pixi run check` (which *does* run the whole `pr`
  profile) — i.e. a pixi contributor's local gate is currently stricter than
  CI. That much was **already known and deliberately tracked**, in
  `tests/test_verify_profiles.py`'s `_PR_STEPS_NOT_IN_A_CI_ONLY_LIST`
  (`"fmt-check": "NOT RUN IN CI — pre-existing gap, tracked here"`); what this
  entry adds is the *measurement* of what enabling it would cost, and the
  finding that the gap is not version skew.

  **Closed.** The deferred reformat was done in its own PR, as that plan
  specified. Two things had changed since the measurement above and both
  argued for doing it then rather than later: the drift was *growing* (486
  files on 2026-09-11, **625 files / 58,564 diff lines** on 2026-09-13 —
  +139 files in two days, because nothing enforced it on new code), and the
  open-PR count was down to two, so the "conflicts with every in-flight
  branch" cost was at a local minimum. `ci.yml`'s `lint-and-types` job now
  runs `--only lint,fmt-check,typecheck,docs-build`, and the two markers that
  tracked the gap (`_PR_STEPS_NOT_IN_A_CI_ONLY_LIST`'s `fmt-check` entry and
  this paragraph's deferral) are gone. A green CI run now does say something
  about formatting.

  Two things worth not rediscovering. (1) The reformat was verified
  semantics-preserving by `ast.parse` + `ast.dump` comparison of every one of
  the 624 changed `.py` files against its committed version, rather than by
  trusting the formatter: 622 were byte-identical in AST, and the two that
  differed are benign docstring-text changes (`""""What is left"` gains the
  space ruff inserts to disambiguate a docstring opening on a quote). (2)
  `abicheck/model/change_catalog/kinds.pyi` is excluded from formatting in
  `ruff.toml`'s `[format]` section, and the exclusion is load-bearing rather
  than cosmetic: it is generated by `scripts/gen_changekind_stub.py`, whose
  `--check` mode (a required gate) fails on any byte it would not itself
  write. Formatting it rewrites the quotes and wraps the six members whose
  slug pushes the line past 88 columns, so `gen_changekind_stub.py --check`
  and `fmt-check` could not both pass. Teaching the generator to emit the
  wrapped form was considered and rejected — it would be a second,
  hand-maintained copy of `ruff format`'s line-breaking rules, drifting on
  every ruff bump. Lint still applies to the stub; only formatting is
  excluded.

- **Deferred entirely, not attempted this pass** (heavier structural
  changes, each needing its own scoped design rather than a drive-by
  addition):
  - *Devcontainer image* — a maintained `.devcontainer/` needs a decision on
    which system tools (castxml, libabigail, abi-compliance-checker,
    compilers) ship baked-in vs. installed on first use, and upkeep as those
    pins drift; `pixi` (see CONTRIBUTING.md) already solves the "one command
    gets you a working dev environment" problem this would target, without
    the image-maintenance burden.
  - *Trend-reporting database* — persisting `scripts/check_tier_accuracy.py`
    /`check_fp_rate.py`/mutation-score history across runs (rather than each
    CI run only gating against a static baseline) needs a storage decision
    (artifact-based vs. external DB) and a retention/access policy before
    it's worth building.
  - *Full behavioral baseline* — `agent-evals/` (this pass, M1-5) is a real
    but minimal harness with one task; a "full behavioral baseline" implies
    a broad task suite plus a scoring/leaderboard story, which should grow
    from real usage of the one-task harness rather than being speculatively
    built out now.
- **Findings emitted from absent evidence — `type_vtable_changed` fixed;
  `type_base_changed` carries the identical shape and is not.** A list-valued
  `RecordType` field cannot express "not captured", so an empty-vs-non-empty
  difference conflates a real change with one side's debug info simply not
  covering the declaring TU. Confirmed for `vtable`: identical headers, no
  DWARF vtable, and zero `_ZTV` symbols on either side still produced a
  `BREAKING` `type_vtable_changed`, because `_diff_type_vtable` guarded on
  `t_old.vtable == t_new.vtable` and nothing else. Fixed by requiring an
  independent layout signal (a size change — the vptr a genuinely polymorphic
  class gains — or a virtual-base change) before an empty↔non-empty
  transition is reported; both-sides-captured differences are untouched, and
  an unknown size on either side keeps the finding, since the suppression
  needs positive evidence that layout held still rather than being a fallback
  for missing information. This is the discipline `diff_vtable_layout.py`
  (tri-state `None`, "degrading to B1's L0 view rather than fabricating a
  break") and `diff_elf_layout.py` (compare only a `_ZTV` present on *both*
  sides) already state in their own docstrings; the type-level detector had
  neither. **Two things remain open.** (1) `_diff_type_bases`
  (`set(t_old.bases) != set(t_new.bases)`, and its virtual-base half) has the
  same unguarded shape and **stays that way — an attempted guard was written
  and reverted before merge, and the reason is worth not rediscovering.**
  Every layout-based premise for it is false: an *empty* base is invisible by
  the empty-base optimization, and — the one that killed the attempt — a
  *storage-contributing* base can be added without moving the derived class's
  size at all when the class is over-aligned (verified against g++:
  `struct alignas(8) D {}` and `struct alignas(8) D : B {}` with
  `struct B { int y; }` are both 8 bytes, as are the `alignas(16)` pair at
  16). So "size held still" proves nothing about a base list, in either
  direction. Unlike the vtable case there is no independent evidence stream
  to fall back on — `snapshot.functions` answers "did this class's virtuals
  change" but nothing answers "did this class's bases change" except
  `RecordType.bases` itself. Guarding it therefore needs evidence the model
  does not currently carry (per-finding provenance, or a captured base-layout
  fact such as `base_offsets` corroboration), not a cleverer reading of
  `size_bits`. Until then a fabricated `type_base_changed` from a capture gap
  is the accepted cost, because the alternative — suppressing a real
  hierarchy change, which is sometimes the *only* breaking finding a
  same-size base addition produces — is strictly worse. (2) The vtable guard is **narrower than
  a first reading suggests, and its own docstring used to overclaim it.** The
  class's virtual functions and its `RecordType.vtable` are two projections of
  the same DWARF evidence (both trace to `DW_TAG_subprogram`), not independent
  streams — so a translation unit whose coverage vanishes can take both, the
  two sides' signature sets then differ, and the guard declines to suppress.
  The false positive survives in that shape. That is the failure direction to
  have (it leaves a pre-existing false positive standing rather than hiding a
  real break), but it means the guard covers the *reported* case — identical
  headers, no DWARF vtable, no `_ZTV` anywhere, virtuals absent from both
  sides — and not every capture gap. Closing the rest needs artifact or
  provenance evidence (`_ZTV` presence, per-finding providers) the type-level
  detector does not receive today. (3) One accepted
  false negative in the
  fix itself: a class already polymorphic *through a base*, declaring no
  virtuals of its own, that gains one — its vtable grows while its object
  size does not, making it indistinguishable from capture noise without a
  real polymorphism walk over both sides' base chains
  (`diff_vtable_layout._is_polymorphic`) plus the per-finding provenance the
  entry below describes. (4) A *pure* virtual reaches that same size backstop
  for a different reason: it has no out-of-line definition, so
  `dwarf_snapshot` drops its declaration-only DIE from `snapshot.functions`
  while still counting it as a vtable child of the class, leaving both
  owned-signature sets empty — and with `alignas` absorbing the new vptr the
  size does not move either. Reproduced against g++
  (`struct alignas(8) A { virtual void f() = 0; }` compiled alongside a
  concrete derived class, which is what makes GCC emit A's complete DIE
  rather than a `DW_AT_declaration` stub): old vtable `[]`, new vtable
  `['_ZN1A1fEv']`, both 8 bytes, `_ZN1A1fEv` absent from the function map on
  both sides. **An attempt to close this one was written, merged into the
  branch, and then reverted — the reason is the most useful thing in this
  entry.** The fix consulted `RecordType.vptr_offset_bits` as "the layout
  descriptor's own witness, the only signal here that is not another
  projection of the same subprogram DIEs." That claim was false, and
  checkably so: **both** producers assign the field as `0 if vtable else
  None` (`dwarf_snapshot.py`, `dumper_castxml.py`), so on every real DWARF
  or CastXML snapshot `(old.vptr is None) != (new.vptr is None)` is
  *identical to* the empty↔non-empty vtable transition being guarded. It was
  therefore true by construction for every input that reached it, which did
  not merely weaken the guard — it made the guard **inert**, restoring the
  original capture-gap false positive in full (Codex review). Only the
  optional `ABICHECK_CLANG_LAYOUT_TOOL` path computes a real vptr, and
  nothing in the model distinguishes that value from the derived one, so the
  field cannot be trusted here at all. Reverted; case (4) joins case (3) as
  an accepted false negative. **The tests are the second lesson**: the
  guard's unit tests built `RecordType`s with `vptr_offset_bits` left `None`
  on both sides — a record no backend can emit — so the entire suite,
  including the pre-existing test for the guard's whole purpose, passed
  against a guard that suppressed nothing on real input. The helper now
  derives the field the way the producers do, one test pins that derivation
  in the producers' own source (so the reasoning fails loudly if a producer
  ever computes a real vptr), and five of these tests fail against the
  reverted revision. Worth recording for both (3) and (4) that the break is
  **not** hidden: `diff_layout._check_vptr_introduced` fires independently
  on the same `None → 0` transition and the verdict stays `BREAKING`
  (verified end to end, and now pinned by its own test) — which is also why
  the FP-rate corpus, a verdict-level gate, could not catch either the
  original false positive or the inert-guard regression, and why the
  regression coverage is a direct unit test on the predicate
  (`tests/test_vtable_evidence_guard.py`) instead. Leaning on a sibling
  detector remains uncomfortable, but the alternative on offer was a witness
  that only appeared independent. Guarded by four FP-corpus cases under the
  `evidence-absence` axis (one FP guard, three FN sentinels), the unit tests
  above, and three Hypothesis properties in
  `tests/test_detector_properties.py`.

  **A named follow-on, found by a user rather than by any gate in this
  repo, and worth being explicit about why: two detectors reading the
  identical evidence gap and reaching two different severities is its own
  bug class, distinct from either detector being individually wrong.**
  `LAYOUT_UNVERIFIABLE` (`diff_layout.py`) answers "was there real layout
  evidence for this type" from the exact same asymmetric-evidence condition
  `_vtable_transition_is_evidenced` above declines to suppress on. Both
  answers are individually correct and individually documented (this entry
  is the vtable side's own justification) — but a type carrying both at
  once reads as a hard BREAKING verdict sitting next to a finding that
  already says the evidence for that type couldn't be verified either way,
  reproducible with zero real ABI change (scanning a binary against a dump
  of itself; reported against real oneDAL binaries, three types, two
  libraries). None of this repo's existing quality gates were positioned to
  catch it: the FP-rate/tier-accuracy gates are verdict-level (the overall
  verdict was already correctly `BREAKING`, dominated by the vtable
  finding, so nothing there looked wrong), and every existing test —
  including the Hypothesis properties this entry already describes — is
  scoped to one detector's own correctness in isolation, never to whether
  two detectors' outputs are mutually *legible* when they land on the same
  subject.

  **The fix went through four designs before landing, and the last pivot is
  the one worth reading closely — it generalizes past this one pair of
  detectors.** (1) Demoting `TYPE_VTABLE_CHANGED` was tried first and
  rejected immediately: a real last-virtual-method removal with a new-side
  capture gap is indistinguishable from this same evidence gap, so
  softening its severity risks a false negative on a genuine break. (2)
  Folding the now-redundant `LAYOUT_UNVERIFIABLE` finding into
  `redundant_changes` unconditionally was tried next, and review found four
  real defects in it in turn: correlating on bare `Change.symbol` (two
  distinct same-named records in different namespaces can share it, so
  fold on `qualified_name`); folding on mere co-occurrence rather than a
  marker recording *why* the stronger finding was kept (an
  independently-evidenced `TYPE_VTABLE_CHANGED` — a real reorder, a real
  size delta, a real virtual-base change — must never trigger the fold);
  reusing `Change.modulation_reason`/`modulation_rule` for internal
  signaling (those are a public, report-facing audit trail for an actual
  verdict override — tagging an unmodulated finding with them is a false
  audit entry a downstream consumer would read as real); and running the
  fold *before* `FilterNonPublicSurface`/`ApplySuppression` had settled
  both findings, which both hid the redundant finding from a suppression
  rule targeting it directly and let it outlive its covering finding's
  later exclusion. (3) Fixing all four and gating the fold on a
  policy-resolved-verdict subsumption check (`checker.
  _vtable_gap_finding_is_verdict_subsumed`) closed the `PolicyFile`-override
  case, but review found the fix was still solving the wrong layer: **the
  severity-scheme axis (`severity.SeverityConfig` — `abi_breaking`/
  `potential_breaking`/etc., each independently `error`/`warning`/`info`)
  is chosen by the CLI/reporter caller entirely *after* `compare()`
  returns, so no compare()-time decision to remove a finding from
  `changes` can ever be correct for it.** Concretely: a caller configuring
  `abi_breaking=info` / `potential_breaking=error` wants
  `LAYOUT_UNVERIFIABLE`'s error-level contribution to reach
  `severity.compute_exit_code`, but that function reads `result.changes`
  directly — if the fold already removed the finding because the
  *policy* axis (a completely orthogonal gate, resolved before
  `compare()` returns) ranked the covering `TYPE_VTABLE_CHANGED` as more
  severe, the severity axis never gets a chance to see it, and this
  reproduces with **zero policy override involved** (Codex review, fresh
  evidence). The same review round also found the fold's pipeline
  ordering relative to `DemoteUnreachableInternalChurn` (which runs later,
  to demote a confirmed-unreachable internal-namespace type) could orphan
  an already-made fold decision when the covering finding was itself
  demoted afterward. (4) **The only structurally-safe fix was to stop
  removing information from `changes` for this reason at all.**
  `post_processing.AnnotateLayoutUnverifiableCoveredByVtableChanged`
  leaves both findings exactly where they always were and only sets the
  already-existing, generic `Change.correlated_change_kind` field (reused
  from its original ADR-041 purpose, not a new one) on the redundant
  `LAYOUT_UNVERIFIABLE` finding, pointing at `"type_vtable_changed"`.
  `Change.vtable_covers_unverifiable_layout_gap` (set in
  `diff_types._diff_type_vtable`) still records which `TYPE_VTABLE_CHANGED`
  findings rest purely on the ambiguous evidence gap versus real,
  independent evidence — that detection logic is exactly what survived all
  four designs unchanged; only the *action* taken on it changed. This
  design is immune by construction to every one of the defects above and
  to any future one shaped like them, because nothing is ever hidden from
  a consumer that reads `changes` — there is no "was this the right time
  to remove it" question left to get wrong. **The general principle this
  leaves behind: never remove a finding from a shared, multiply-consumed
  list (`DiffResult.changes`) to resolve what is fundamentally a
  *presentation* problem (two findings reading as contradictory) when that
  list has independent downstream consumers — a legacy verdict, a
  policy-file override, a severity-scheme exit code, a future pipeline
  step — that this fix cannot fully enumerate and whose configuration is
  chosen after the removal decision was made. Annotate; never remove.**
  Regression coverage: `tests/test_vtable_severity.py`'s
  `TestLayoutUnverifiableCorrelatedWithVtableChanged` (hand-picked example
  cases, including one exercising the severity-axis gap directly via
  `severity.compute_exit_code`) and a generalized Hypothesis property,
  `test_layout_unverifiable_always_correlated_when_vtable_change_shares_its_gap`
  in `tests/test_detector_properties.py`, checking the annotation holds
  over arbitrarily generated classes and evidence-descriptor shapes.

  **A whole-class structural fix was attempted mid-way through the fold
  design (2) above, for the field-ordering half of it, and reverted — the
  in-repo audit that justified it was the wrong audit.** (This sub-entry
  predates the pivot to design (4) and is kept because the lesson is
  independent of which design won.) The field-ordering bug (found twice
  now: first on `AbiSnapshot` in PR #582, again on the new `Change` field
  this fix originally added mid-list) was first "fixed" by making `Change`
  keyword-only from `old_value` onward via the `dataclasses.KW_ONLY`
  sentinel, on the strength of an AST-parse of every `Change(...)` call
  site in this repo confirming every positional call anywhere passes
  exactly the three required leading fields and never more. That audit
  answers the wrong question for a type this codebase itself documents as
  public API (`checker_types.py`'s own module docstring; CLAUDE.md:
  "changing their public surface is a breaking change to the Python API —
  coordinate it"): it proves this repo's own call sites are safe, and says
  nothing about an external consumer who previously called `Change(kind,
  symbol, description, old_value, new_value, ...)` positionally — for whom
  the whole-class sentinel is exactly the same breaking shift the fix
  exists to prevent, just moved to a boundary this repo cannot see or test
  (Codex review, fresh evidence). Reverted in favor of the same per-field
  `field(kw_only=True)` PR #582 already used for `AbiSnapshot`, applied
  only to the one new field (`vtable_covers_unverifiable_layout_gap`) and
  appended at the true end of the dataclass (not mid-list) so every
  pre-existing field's position — and therefore every pre-existing
  positional caller's behavior, in or out of this repo — is completely
  unchanged. The lesson generalizes beyond this one field: for a type
  documented as public API, "no in-repo caller breaks" is not the bar:
  prefer the narrowest change that cannot break a caller this repo cannot
  see, even when a broader structural fix looks more complete. The
  `KW_ONLY` sentinel is still the right tool for a genuinely *internal*
  dataclass with no external-construction contract (nothing here argues
  against it in general) — the mistake was applying it to one that has
  such a contract without checking for one first.

- **Evidence-provider model — investigated, found not to reproduce as
  described; no fix applied.** A status-review follow-up asked whether
  `evidence_status_for_result`'s report-level downgrade (kind-level
  `ARTIFACT_PROVEN` → `UNATTRIBUTED` only when `DiffResult.evidence_tiers`
  is header-only for the *whole* comparison) can let an individual
  header-derived `BREAKING_KINDS` finding read as artifact-proven merely
  because *some other, unrelated* part of the same report had binary
  evidence. Traced this for the highest-stakes family it could apply to —
  layout findings (`TYPE_SIZE_CHANGED`/`TYPE_ALIGNMENT_CHANGED`,
  `diff_types.py`) — and it does not hold up: (1) the direct-clang L2
  backend's `RecordType.size_bits`/`alignment_bits` are populated **only**
  when `dumper_layout_backfill.backfill_dwarf_layout()` actually
  corroborates them against real DWARF (`model.py`'s own
  `dwarf_layout_coherence` docstring) — with no DWARF to backfill against,
  those fields stay `None` and `_append_type_size_and_alignment_changes`'s
  own `is not None` guard means no finding is even emitted, so an
  "unconfirmed clang-derived layout finding" cannot occur; (2) the castxml
  backend computes struct layout itself, via its own bundled real compiler
  targeting the resolved ABI — `model.py` already documents this as
  deliberately treated as sufficient L2 evidence ("trivially self-consistent
  by construction", not needing DWARF corroboration), a prior, intentional
  design decision this pass would have to *overturn*, not merely patch.
  The one place this class of risk is genuinely live is exactly the
  already-tracked toolchain-identity-probe gap above (castxml/clang invoked
  with compiler/ABI flags that don't match the real build) — not a separate
  evidence-status bug. A **real** per-finding provider model (recording,
  per `Change`, which of L0–L5 actually produced/corroborated it) would
  need new provenance plumbing through all ~45 `Change(...)` construction
  sites across `diff_*.py`/`buildsource/*.py`, each individually verified
  against the FP-rate/mutation-score gates — a multi-day project on its
  own, not attempted here.
- **Type reachability (direct vs. transitive stdlib references) — computed
  and wired into `diff_types.py`'s RecordType-based detectors; enum/typedef
  paths remain unwired.** `abicheck/type_reachability.py`
  (`directly_referenced_stdlib_types()`) computes, from a snapshot alone,
  which `std::`/`__gnu_cxx::`/etc. record types are directly referenced by
  a non-stdlib function's signature or a non-stdlib type's own field — as
  opposed to only reachable via deep template-instantiation internals
  (`std::string::_Alloc_hider`, `std::_Rb_tree_node_base`) that
  `is_non_abi_surface_type`'s existing whole-name-prefix filter already
  correctly excludes as toolchain-artifact churn either way. A Codex review
  round found and fixed a real correctness gap in the computational claim:
  candidate identification originally matched only `RecordType.name`, but
  castxml/direct-clang populate the bare leaf there and the
  namespace-qualified spelling separately in `qualified_name` (`model.py`,
  `dumper_clang.py`) — so `name` alone never carries a `std::` prefix for
  those two backends and the helper silently found nothing on any real
  castxml/clang-produced snapshot. Fixed by identifying candidates via
  `qualified_name or name`. That fix alone was still insufficient, confirmed
  by dumping a real compiled `std::vector<int>` parameter end to end:
  `Function.return_type`/`Param.type` spell the outer type **bare**
  (`"vector<int, std::allocator<int> >"`) even when the matching
  `RecordType`'s identity is fully qualified
  (`"std::vector<int, std::allocator<int> >"`), across *all three* backends
  (DWARF bakes the qualified form straight into `name` with no separate
  field; castxml/clang keep `name` bare and `qualified_name` separate) — so
  a pure full-identity substring match still couldn't connect the two.
  Fixed by also generating a namespace-prefix-stripped spelling per
  candidate and matching against either form. **Since resolved** (a later
  pass, user-requested): a signature spelled with a typedef alias
  (`std::string`, `std::wstring`, ...) names the alias, not the real
  underlying class (`std::basic_string<char, ...>`) that owns the
  `RecordType` entry — no current model field maps one back to the other
  directly, but `snapshot.typedefs` does carry the alias → target mapping.
  Verified empirically against a real DWARF-dumped `std::string`
  parameter: `snapshot.typedefs["std::string"]` resolves to the bare
  `"basic_string<char, std::char_traits<char>, std::allocator<char> >"`,
  while the owning `RecordType.name` is the fully-qualified
  `"std::__cxx11::basic_string<char, std::char_traits<char>,
  std::allocator<char> >"` — libstdc++ wraps its own post-C++11 dual-ABI
  types in an inline namespace (`__cxx11::`) the exact same way libc++
  wraps its whole standard library (`__1::`/`__ndk1::`, already handled),
  so that inline-namespace-stripping list gained a third entry. The
  typedef *key* itself needed the identical bare-vs-qualified treatment
  already applied to `RecordType` identities (the DWARF backend spells the
  signature with the bare form `"string"`, never the qualified typedef key
  `"std::string"`), so `_typedef_spelling_targets()` builds a
  `spelling -> target` index covering both the literal key and its
  namespace-stripped bare form (dropped instead of recorded when
  ambiguous, same false-negative-over-false-positive principle as the
  `RecordType` spelling index), and `_scan()` now follows a matched
  typedef alias to its target the same way `surface.py`'s own
  reachability closure does. What this does *not* cover: a stdlib alias
  the producing backend never emitted into `snapshot.typedefs` at all (no
  empirical case of this found across the three backends so far, but
  nothing guarantees one couldn't exist) — that residual case degrades
  silently back to "not directly referenced," the same conservative
  false-negative default this whole module already uses throughout.

  **Two more real gaps found and fixed in the same pass** (Codex review,
  fresh evidence): (1) the non-stdlib bare-alias fallback derived a
  record's unqualified spelling via `identity.rsplit("::", 1)`, which
  splits inside a *template argument's own* qualified name rather than at
  the outer namespace boundary — for `"api::Wrapper<dep::Tag>"`, the
  lexically last `"::"` belongs to the template argument `dep::Tag`, not
  the outer namespace path, so the old code derived the corrupted bare
  form `"Tag>"` instead of `"Wrapper<dep::Tag>"`, and a real dumper
  backend's bare signature spelling for that wrapper then never matched
  anything. Fixed with a new `_bare_type_name()` that tracks `<`/`>`
  nesting depth and only treats a `"::"` at depth zero as a namespace
  separator. (2) stdlib and non-stdlib spellings were matched via one
  *combined* compiled pattern in a single non-overlapping `finditer()`
  pass — when a non-stdlib record's own identity embeds a stdlib type's
  spelling verbatim (e.g. a template instantiation `"Wrapper<std::string>"`
  registered as its own record identity), and a public signature names
  that wrapper's full identity exactly, the combined pattern's
  longest-first alternation matches the whole wrapper span first,
  consuming it — since regex matches never overlap, the nested
  `"std::string"` substring inside that same span was never independently
  found, even though it is directly present in the public signature text.
  Fixed by splitting `_spelling_index()` into two independent indices
  (stdlib vs. non-stdlib/record) with two independently compiled patterns
  scanned separately over each declaration, so one pattern's match can
  never mask the other's.

  **A third real gap found in the same pass** (Codex review, fresh
  evidence): both the stdlib-stripping collision guard (in
  `_spelling_index`) and the typedef-key stripping collision guard (in
  `_typedef_spelling_targets`) checked a stripped spelling only against
  *full* non-stdlib record identities, not against the bare
  (namespace-unqualified) alias a real backend actually spells that record
  with. A non-stdlib record like `api::vector<int>` is spelled bare as
  `"vector<int>"` — the same bare spelling `std::vector<int>` reduces to
  after namespace-stripping — so a signature naming the unrelated user
  type by its bare spelling incorrectly marked the real `std::vector<int>`
  as directly referenced too; the identical gap existed one level up for
  `api::string`/`"std::string"`'s typedef key. Fixed with a new
  `_non_stdlib_signature_spellings()` helper (full identity plus bare
  alias — deliberately keeping an ambiguous bare alias that
  `_spelling_index`'s own `record_index` drops, since it's still a real
  spelling *some* non-stdlib record can be named by) shared by both
  collision guards.

  **A fourth finding pointed one level deeper, into shared infrastructure
  this module calls rather than into `type_reachability.py` itself**
  (Codex review, fresh evidence): `diff_cxx_rules.owner_class_of()` — the
  helper this module's owner-class seeding reuses, also used by
  `diff_symbols.py`'s owner-based move detection, `diff_cxx_rules.py`'s
  own member-move heuristics, and `surface.py`'s reachability closure —
  mis-parses a public conversion operator's owner when the operator's own
  target type is namespace-qualified. Confirmed against a real compiled
  and demangled symbol: `struct Foo { operator ns::Bar() const; };`
  demangles to `"Foo::operator ns::Bar() const"`, and abicheck's own
  `Function.name` (after its existing signature-stripping step) is exactly
  `"Foo::operator ns::Bar"`. The old naive `rsplit("::", 1)` split at the
  *lexically last* `"::"` — which belongs to the operator's own qualified
  target (`ns::Bar`), not the owner/member boundary — producing the
  corrupted owner `"Foo::operator ns"` instead of `"Foo"`, so a public
  conversion operator to a qualified type would never seed its owner
  class, potentially hiding a genuine layout break in one of the owner's
  fields. Fixed in `owner_class_of()` itself (not duplicated locally) by
  locating the literal `"::operator "` marker — present only for a
  conversion-to-named-type operator, never for a symbol operator like
  `operator+`/`operator[]`, which has no target type to separate from the
  keyword with a space — and splitting there when present, falling back to
  the previous behavior otherwise. Fixing the shared helper directly
  (rather than working around it only in `type_reachability.py`) also
  corrects the same latent mis-parse for its other three callers, since
  none of them could have been relying on the old behavior's output for
  this input shape without already being wrong.

  **A fifth finding, on the same owner-seeding feature, investigated and
  deliberately not implemented this pass:** a public method whose dumper
  backend recorded only a bare member name (CastXML's convention — "the
  bare `bar` rather than `C::bar`", per `owner_class_of()`'s own
  docstring) on a class-template specialization falls through to
  `owner_class_of()`'s mangled-name fallback
  (`itanium_scope_components`), which — confirmed empirically
  (`itanium_scope_components("_ZN3FooIiE3barEv")` returns
  `["FooIiE", "bar"]`) — deliberately keeps the **raw, undemangled**
  Itanium template-argument encoding (`"FooIiE"`) rather than the spelled
  form (`"Foo<int>"`) a real `RecordType` identity actually uses; that
  design choice is itself intentional and documented in
  `itanium_scope_components`'s own docstring ("the raw template-argument
  encoding is kept so distinct specializations stay distinct"), since its
  other callers use it for grouping/distinguishing specializations, not
  for matching against demangled model spellings. `type_reachability.py`'s
  owner-seeding then feeds this raw string into `_scan()`, which correctly
  finds no match (a silent false negative — the same
  false-negative-over-false-positive default this whole module already
  uses throughout, not a new failure mode). A real fix has two paths, both
  rejected as out of scope for a drive-by extension here: (1) making
  `owner_class_of()` itself resolve raw template encodings to spelled
  form would mean invoking the real demangler (`demangle.py`'s
  `demangle()`, which shells out to `c++filt`/`cxxfilt` on a cache miss)
  from a hot path every one of its four callers shares, directly
  contradicting `itanium_scope_components`'s own stated design rationale
  ("avoids any dependency on an external demangler ... so this works
  identically on Linux, macOS, and Windows and never shells out"); (2) a
  narrower, local-only translation in `type_reachability.py` (demangle
  just `fn.mangled` when `owner_class_of()` took the mangled-fallback
  path, then re-derive the owner from the *demangled* qualified name)
  would need a genuinely new depth-aware "class::member" boundary splitter
  for demangled text — not a reuse of `_bare_type_name` (which strips a
  *leading* namespace qualifier, the opposite half of this problem) — and
  would have to correctly compose with the already-fragile
  `"::operator "` marker special-case from the fourth finding above (a
  demangled conversion operator on a qualified template specialization
  could combine both edge cases at once), which is exactly the kind of
  compounding-edge-case complexity this file's own docstring already
  flags as needing "its own scoped follow-up," not a reactive patch.

  **Two more real gaps found and fixed in the same pass** (Codex review,
  fresh evidence): (1) A real backend does not always spell a nested type
  as either the fully-qualified identity or the fully-bare leaf —
  confirmed empirically via `clang -ast-dump` on `namespace api { struct
  Outer { struct Inner {}; }; Outer::Inner g(); }`: direct-clang prints
  the return type as exactly `"Outer::Inner"`, dropping the enclosing
  namespace (`api::`) while keeping the class-nesting qualifier
  (`Outer::`). Neither the full-identity match nor the single
  fully-bare-leaf match (`_bare_type_name`) covered this partial
  qualification. Generalized `_bare_type_name` into
  `_namespace_suffix_spellings()`, returning every suffix obtainable by
  dropping some prefix of the scope chain at each depth-zero `"::"`
  boundary, and updated all three call sites to register every suffix
  (same ambiguity-drop collision guard extended to each). (2)
  CastXML/direct-clang record a function or namespace-scope variable's
  own display name bare (e.g. `"touch"`, never
  `"__gnu_cxx::Node::touch"` or `"std::touch"`), so the existing
  `name.startswith(STDLIB_TYPE_NAMESPACE_PREFIXES)` guard cannot catch a
  retained, seemingly-public declaration that is actually part of the
  standard library itself — verified with two real Itanium
  mangled-symbol repros (a namespace-scope stdlib variable and a stdlib
  free function) that both incorrectly marked `std::string` as directly
  referenced before the fix. Fixed by also checking the declaration's
  recovered qualified name (`diff_cxx_rules.itanium_qualified_name`, from
  `mangled`) against the stdlib prefixes for both functions and
  variables — which subsumes the narrower owner-only check from the
  fourth finding above (a stdlib-prefixed owner always makes the full
  qualified name stdlib-prefixed too, but not vice versa: a stdlib
  namespace's own direct free function/variable is a single mangled
  scope component, so `owner_class_of` returns a bare `"std"` with no
  trailing `"::"`, never matching the `"std::"` prefix string), so the
  now-redundant owner-only guard was removed.

  **A sixth finding found a different shape of gap again: an owner-seeding
  correctness bug, not a missing-spelling one.** `owner_class_of()`
  derives its result by chopping the trailing `"::"`-component off *any*
  already-qualified declaration name or mangled-symbol scope chain, with
  no way to tell — from the string alone — whether what remains is really
  an enclosing *class* or just an enclosing *namespace* (Codex review,
  fresh evidence, confirmed with a minimal repro): a public namespace
  function `api::run()` makes `owner_class_of` return the bare namespace
  fragment `"api"`, which the general suffix-matching mechanism
  (`_namespace_suffix_spellings`, added for the first finding above) could
  then coincidentally match against an unrelated internal record's own
  bare-suffix spelling (e.g. `other::api`), wrongly walking that record's
  fields and unfiltering its layout churn. Fixed by seeding an owner only
  on an *exact* match against a non-stdlib record's full identity —
  bypassing `_spelling_index`'s `record_index`/suffix mechanism entirely
  for this specific seed, rather than routing it through `_scan()`. This
  is safe rather than a regression risk: unlike a genuine signature type
  spelling (which a backend can legitimately partially-qualify, per the
  first finding), `owner_class_of`'s result is always either the complete,
  exact scope chain of a real class (both its already-qualified-name path
  and its mangled-decomposition fallback reconstruct the *full* chain,
  never a partially-elided one — DWARF always bakes the complete
  namespace/class path into a qualified name, and Itanium mangling always
  encodes the complete nested-name unambiguously) or, when the function
  isn't actually a method, namespace noise — so restricting to exact
  matching loses no real case while closing the false-positive collision.
  While verifying this fix through the full `compare()` pipeline (not just
  the unit level), the same class of bug was found to independently exist
  in `surface.py`'s `compute_public_surface()` — its own, separate
  `owner_class_of`-based seeding (`_seed_public_roots`) feeds the raw
  owner through `_type_identifiers()` into `seed_types`, and
  `_walk_type_closure()`'s `record_by_name` lookup is *itself* keyed by
  bare-tail aliases (an intentional, correct mechanism for genuine type
  references — "a short alias reached inside its own namespace resolves
  to the namespaced record"), so the identical `"api"` vs. `other::api`
  collision reproduces there too, confirmed with the same minimal repro
  (`compute_public_surface` marks `"api"` — and therefore `other::api` —
  public). **Deliberately not fixed in this pass**: `surface.py` is a
  different, foundational module (the public-surface-scoping gate every
  other detector in the codebase depends on) that this PR never otherwise
  touches, and unlike the narrow `type_reachability.py` seeding path, its
  `record_by_name` bare-tail lookup is a *shared* mechanism relied on by
  every other seed type too — restricting it for the owner case
  specifically needs its own careful, independently-verified design (which
  seed paths may legitimately need the ambiguous-tail lookup and which
  must not), not a same-PR drive-by extension of an unrelated finding.

  **Two more ambiguity-tracking gaps found in the same collision guards**
  (Codex review, fresh evidence, both confirmed with minimal repros): (1)
  when two non-stdlib records had identities `"Inner"` and `"api::Inner"`,
  `_spelling_index`'s derived-suffix collection only counted contributors
  to the *derived* suffix `"Inner"` (from `"api::Inner"`) — the unrelated
  global `"Inner"` identity never contributes to that same tracking
  structure (it's already a full identity, not a derived suffix), so the
  ambiguity count saw only one contributor and merged `"api::Inner"`
  straight into the pre-existing full-identity entry for the global
  `"Inner"`. Fixed by also treating a derived suffix that collides with a
  *different* record's own full identity as ambiguous. (2)
  `_typedef_spelling_targets` gave an *exact* pre-existing typedef key
  automatic priority over a derived suffix from a different key, rather
  than tracking both through the same ambiguity-counting structure: when
  `snapshot.typedefs` held both a global `"Alias" -> "std::…"` and a
  qualified `"api::Alias" -> "Foo"`, a declaration inside `api` can
  legitimately spell the latter as bare `"Alias"` too — silently
  preferring the pre-existing exact key could resolve it to the wrong
  one. Fixed by unifying exact keys and derived suffixes into one
  target-set-per-spelling structure, resolving a spelling only when every
  contributing source agrees on exactly one target.

  **A follow-up review round on the same fix found the removal above was
  necessary but not sufficient.** Refusing to *merge* `"api::Inner"`'s
  candidates into the pre-existing `record_index["Inner"]` entry still
  left that entry pointing at the unrelated global `"Inner"` record
  (Codex review, fresh evidence, confirmed with a minimal repro):
  direct-clang's own "drop the enclosing namespace" convention (the same
  mechanism `_namespace_suffix_spellings` models for the `Outer::Inner`
  finding above) means a signature declared *inside* namespace `api` can
  spell `api::Inner` bare as `"Inner"` too — not just a partially-qualified
  form. A public `api::f()` returning (bare-spelled) `api::Inner` would
  then have its `std::` field misattributed to the *unrelated* global
  `Inner`'s own field instead of correctly failing to resolve. Fixed by
  removing the colliding spelling from `record_index` entirely
  (`record_index.pop(bare, None)`) rather than merely refusing to add the
  other record's candidates to it — since the bare spelling is genuinely
  ambiguous between both records, leaving it resolved to either one
  (including the "already there by default" one) is the wrong outcome,
  not just an incomplete fix.

  **A separate, deeper finding on typedef keys, investigated and
  deliberately not implemented this pass:** direct-clang's own
  `parse_typedefs()` (`dumper_clang.py`) stores a typedef's bare
  `node["name"]` as the `snapshot.typedefs` key — never the scope-joined
  qualified form `_qualified()` uses for every other decl kind — so a
  namespaced alias loses its namespace at the point the snapshot is
  produced, not merely at the point this module reads it. Confirmed
  empirically via a real `clang -ast-dump` on `namespace api { struct Foo
  {}; using Alias = Foo; } api::Alias make();`: the `TypeAliasDecl`'s own
  name is bare `Alias`, while the function's return type is printed fully
  qualified `"api::Alias"` (a typedef reference is always spelled
  qualified by clang's printer, unlike a plain class reference) — meaning
  `snapshot.typedefs` ends up with `{"Alias": "Foo"}` while the real
  signature spells `"api::Alias"`, the exact inverse of the
  qualified-key/bare-signature shape `_typedef_spelling_targets` was built
  to handle. Since suffix-stripping only ever produces a *shorter*
  candidate from a key, it can never reconstruct a *longer*, more-qualified
  spelling from an already-bare key — there is no string-level fix
  possible in this module for this direction, only two heavier ones, both
  out of scope for a drive-by extension here: (1) fixing
  `dumper_clang.py`'s `parse_typedefs()` to store the qualified key
  instead — a genuine, separate producer-side bug, but one whose blast
  radius reaches every other consumer of `snapshot.typedefs` (typedef
  diffing, `surface.py`'s own typedef-following in `_walk_type_closure`),
  each needing its own re-verification against the FP-rate/mutation-score
  gates before trusting a changed key shape; (2) a local reverse-namespace
  guesser in this module (re-attaching every namespace prefix seen among
  the snapshot's own record identities to a bare typedef key and hoping
  one matches) — pure speculation with no way to verify which, if any,
  namespace a given bare key actually belongs to, and a real risk of
  fabricating new false-positive matches rather than closing a
  false-negative gap. Left as a silent false negative — the same
  conservative default this module already uses throughout.

  **A seventh finding pointed at a platform-specific mangled-name quirk,
  silently disabling the mangled-scope-recovery guard on every Mach-O
  snapshot.** Confirmed via `dumper_clang.py`'s own `_visibility()`
  docstring: clang's `mangledName` carries an extra platform leading
  underscore on macOS (`"__ZN3lib3addEii"`, not the plain Itanium
  `"_ZN3lib3addEii"`), and empirically: `itanium_scope_components(
  "__ZSt5touchv")` returned `None` before this fix, since
  `_itanium_strip_prefix()` only recognized the bare `"_Z"` prefix
  (Codex review, fresh evidence). Since every declaration's stdlib-scope
  check in this module (and `owner_class_of()`'s mangled fallback) relies
  on this recovery, a bare-named stdlib declaration on macOS bypassed the
  guard *entirely* — not just in the one edge case a synthetic unit test
  would reach, but for every symbol on that platform. Fixed in the shared
  `diff_cxx_rules.py` parser (benefiting all four of its callers, not
  just this module) by stripping the extra leading underscore before the
  Itanium-prefix check, mirroring `dumper_clang.py`'s own
  `_symbol_candidates()` de-prefixing approach for the identical quirk.

  **An eighth finding pointed at a different mangling scheme entirely, not
  a variant of the same Itanium quirk.** A `clang-cl` (or any
  `--target=*-windows-msvc`) direct-clang snapshot records a method's bare
  AST name — the same unqualified-leaf convention CastXML uses — while
  `mangledName` is mangled in the proprietary Microsoft C++ ABI scheme, not
  Itanium (Codex review, fresh evidence). `owner_class_of()`'s mangled-name
  fallback only ever recognized the Itanium `_Z`/`__Z` prefix, so this
  owner seed stayed `None` on every MSVC-mangled bare-named method,
  regardless of the Mach-O fix above (a different, unrelated prefix
  convention, not fixed by it). Confirmed empirically by compiling real
  headers with `clang --target=x86_64-pc-windows-msvc -fms-compatibility
  -Xclang -ast-dump=json`: `Foo::run()` mangles to `?run@Foo@@QEAAXXZ`
  (scope components written *innermost first*, `@`-separated, terminated
  by the first `@@` — the reverse order and terminator convention Itanium
  uses, confirmed against nested-namespace, single-letter-class-name, and
  global-free-function cases too). Fixed with a new, genuinely separate
  `msvc_scope_components()`/`msvc_qualified_name()` pair in
  `diff_cxx_rules.py` (not a branch inside the Itanium parser, since the
  two schemes share no structure beyond both being length/separator-based),
  tried as a second fallback in `owner_class_of()` after Itanium — the two
  prefixes (`_Z`/`__Z` vs. `?`) are mutually exclusive, so trying both in
  sequence is unambiguous and free on the common Itanium path. Deliberately
  conservative, mirroring `itanium_scope_components`'s own "model the
  simple cases, return `None` for the rest" contract, confirmed unmodelled
  against the same real compiler output: special member functions and
  operators (`??0` ctor, `??1`/`??_D` dtor, `??4` `operator=`, ...) mangle
  with a *second* `?` immediately after the first, so the leaf/scope split
  does not apply and is rejected outright; template classes/functions
  (`?$Name@Args@`) embed the template-argument encoding inside the same
  `@`-delimited region as the scope chain, and an argument token is
  indistinguishable from a scope token by simple splitting, so any
  component starting with `?` (the template marker `?$` or the anonymous-
  namespace marker `?A`) is rejected; a bare-digit component is a
  name-backreference into MSVC's per-symbol substitution table, not a
  literal identifier (no real C++ identifier is all-digits, so this is an
  unambiguous, lossless signal to bail — verified this does *not*
  misfire on a genuine single-letter class name like `struct A`, which
  mangles as a component that is a letter, never a bare digit). Also wired
  the same new fallback into `type_reachability.py`'s two direct
  `itanium_qualified_name()` call sites (the free-function/variable
  stdlib-namespace guards, not just the owner-seeding path the review
  comment named) — same root cause, same one-line fix, verified against a
  `std::`-namespaced MSVC-mangled free function that would otherwise have
  bypassed the guard identically to the Mach-O case above.

  **A ninth finding pointed at an asymmetry in the typedef-spelling
  ambiguity guard, not a mangling gap.** `_typedef_spelling_targets()`
  registers every *derived* candidate spelling (a stdlib-stripped or
  namespace-suffix form of a typedef key) only after checking it against
  `_non_stdlib_signature_spellings()` — but the typedef's own *exact* key
  was registered unconditionally, with no equivalent guard (Codex review,
  fresh evidence). The already-documented direct-clang typedef-scope-loss
  gap above (`parse_typedefs()` storing only the bare `node["name"]`) means
  an exact key like `"Alias"` can itself collide with an unrelated
  non-stdlib record's own bare signature spelling — e.g. a global `struct
  Alias {};` sharing the same name as a namespaced `namespace api { using
  Alias = std::string; }` whose `api::` the producer already dropped.
  Confirmed empirically: `directly_referenced_stdlib_types()` incorrectly
  returned `{"std::string"}` for a public function taking the unrelated
  `Alias` record by value, purely because of the same-named, unrelated
  typedef. Fixed by applying the identical `non_stdlib_spellings` guard to
  the exact-key registration, matching how a colliding derived candidate is
  already skipped — the spelling belongs to the real record, so the
  typedef contributes nothing for it, rather than competing through the
  ambiguity-resolution machinery.

  **A tenth finding closed the conversion-operator half of the owner-
  seeding gap the earlier `"::operator "`-marker fix only partly covered.**
  That earlier fix handled a *display-name* conversion operator whose own
  qualification embeds `"::"` (e.g. DWARF's `"Foo::operator ns::Bar"`), but
  a direct-clang snapshot stores a conversion operator's AST name bare —
  `"operator Bar"`, no owning-class prefix at all, confirmed via a real
  `clang -ast-dump` — so `owner_class_of()`'s display-name branch never
  applies (there is no `"::"` to find), and it falls through to the
  mangled-name fallback (Codex review, fresh evidence). That fallback had
  no coverage for conversion operators either:
  `itanium_scope_components()`'s underlying component parser deliberately
  excludes the Itanium `cv` (conversion-to-*T*) code from
  `_ITANIUM_OPERATORS` — correctly, for that set's own purpose of grouping
  operator *overloads* by a fixed 2-char code, since every conversion
  operator carries a different target type and is never an overload of
  another one — but treating `cv` as entirely unparseable meant hitting it
  aborted the *whole* scope-recovery attempt, discarding the class name
  already parsed before it. Confirmed empirically: `_ZNK3FoocvN2ns3BarEEv`
  (`Foo::operator ns::Bar() const`) made `itanium_scope_components()`
  return `None` outright, and `owner_class_of()` therefore returned `None`
  instead of `"Foo"`. Fixed by recognizing `cv` as a distinct, opaque leaf
  component (`"{op:cv}"`) in `_parse_operator_component()` — separately
  from `_ITANIUM_OPERATORS`, since the overload-grouping semantics
  correctly stay excluded — and forcing `_step_next_component()`'s `done`
  flag to `True` immediately upon seeing it, regardless of nesting: the
  conversion operator's own leaf is always the last component, and the
  target-type encoding immediately following `cv` (e.g. `N2ns3BarE` for
  `ns::Bar`) is a full, arbitrary Itanium `<type>` production — a much
  larger grammar than this structural parser attempts elsewhere — but
  recovering the *scope prefix* never needs that type parsed at all, only
  a signal to stop before attempting it. Regression tests added: direct
  parser-level cases in `TestItaniumScopeParser`/`TestMsvcScopeParser`'s
  sibling `diff_cxx_rules` test file, plus an end-to-end
  `directly_referenced_stdlib_types` test confirming a `Foo`-owning
  conversion operator's embedded `std::string` field is no longer
  filtered.

  **An eleventh finding pointed at a masking mechanism the earlier
  cross-index split didn't fully close.** Splitting `_spelling_index()`
  into independent `stdlib_index`/`record_index` patterns (an earlier
  fix) solved masking *between* the two indices — a non-stdlib wrapper's
  identity embedding a stdlib type's spelling verbatim. It did not solve
  the identical masking *within* either index (Codex review, fresh
  evidence): `.finditer()` only returns non-overlapping matches, so when
  one candidate's registered spelling is itself a substring of another
  candidate's spelling *in the same index* (e.g. `"std::string"` inside
  `"std::vector<std::string>"`, both stdlib; or a non-stdlib `"Inner"`
  inside `"Wrapper<Inner>"`), the longest-first alternation matches the
  outer candidate first, consumes the whole span, and the search
  continues from the end of that match — so the inner one, though
  directly present in the text, is never independently reported.
  Confirmed empirically for both the stdlib and non-stdlib cases (and, on
  further investigation while fixing this, the identical mechanism in
  `typedef_pattern`'s typedef-key matching too — a third, independently
  confirmed instance of the same root cause). Fixed with a single new
  helper, `_finditer_allow_nested()`, used at all three call sites: for
  every match found, it recurses into `text[m.start()+1 : m.end()]` — a
  strictly narrower window, so recursion terminates — to catch a shorter
  candidate embedded anywhere inside it, at any nesting depth, not just
  one level. Kept as one shared helper rather than three inline copies
  since all three loops have the exact same masking mechanism. Verified
  against the existing large-corpus performance regression guard
  (`test_many_unreferenced_stdlib_candidates_scan_efficiently`) to confirm
  this doesn't reintroduce the quadratic candidate-by-candidate cost the
  single-pattern rewrite was originally built to eliminate — the extra
  recursive search only runs when a match is actually found (rare in the
  common case), bounded by nesting depth, not candidate count.

  **A twelfth finding closed a narrower gap in the conversion-operator
  owner fix itself (tenth finding, above).** The `"::operator "`-marker
  fix only detects a conversion operator when an *owner* precedes the
  marker; a bare-recorded conversion operator (no owning-class prefix at
  all, per the tenth finding) can still carry a *qualified target* with
  its own `"::"` — e.g. `"operator ns::Bar"`, no `"Foo::"` prefix — and
  for that shape neither the marker (there's no owner text before
  `"operator"`) nor the previous unqualified-bare check applied, so the
  naive `rsplit` fallback still ran and returned junk like `"operator
  ns"` (CodeRabbit review). Confirmed empirically: constructing exactly
  this input shape reproduced the bad `"operator ns"` result before the
  fix. Fixed by checking for the `"operator "` prefix the same way the
  already-fixed unqualified case is detected, falling through to
  mangled-name recovery for both shapes uniformly.

  **A thirteenth finding pointed at a robustness gap in the eleventh
  finding's own fix, not a new correctness bug.** `_finditer_allow_nested()`
  (the nested-match helper from the eleventh finding) recursed one Python
  call per nesting level to search each match's own span for a further
  embedded candidate (Codex review, fresh evidence). For a genuinely deep
  chain of registered spellings each nested one inside the next —
  plausible for template-metaprogramming-heavy C++ under a compiler's
  configured template-instantiation depth (GCC/Clang both default well
  into the hundreds, and it is routinely raised higher for real
  metaprogramming-heavy code) — that per-level recursion follows the C++
  template depth 1:1. Confirmed empirically: 1,000 successively nested
  registered candidate spellings raised `RecursionError` under Python's
  default 1,000-frame recursion limit, aborting the whole comparison
  rather than degrading gracefully. Fixed by converting the recursive
  search into an explicit stack — each entry is still a strictly narrower
  window than the match that produced it, so the search still always
  terminates, just without consuming Python's call stack to do it, so no
  amount of nesting depth can overflow it.

  **A fourteenth finding was a genuine regression the tenth finding's own
  fix introduced, caught before merge.** Recognizing the Itanium `cv` code
  as an opaque leaf component (tenth finding) used a single fixed
  placeholder label (`"{op:cv}"`) regardless of the conversion's actual
  target type. `diff_types._overload_group_key()` chains
  `itanium_qualified_name()` — which now runs this label onto the scope
  prefix — to decide whether two declarations are genuine overloads of one
  another for `_diff_overload_additions()`'s KDE-policy check (Codex
  review, fresh evidence). A fixed placeholder made *every* conversion
  operator on a class produce the same qualified name regardless of
  target — e.g. both `operator int()` and `operator double()` on the same
  class reduced to `"Foo::{op:cv}"` — collapsing two conversion operators
  that are never overloads of each other (each is a distinct, unambiguous
  conversion function; there is no shared `&Foo::operator T` that becomes
  ambiguous) into one group. Confirmed empirically:
  `_diff_overload_additions()` fired a false `OVERLOAD_ADDED` for adding
  `operator double()` alongside an existing `operator int()` before this
  fix. Fixed by embedding the raw, un-decoded remainder of the mangled
  string after `cv` into the label itself, instead of a fixed placeholder
  — Itanium mangling is deterministic, so the same target always
  reproduces the identical remainder (keeping genuine re-declarations in
  the same group) while distinct targets always mangle differently
  (keeping them in distinct groups), without this parser needing to
  actually decode the arbitrary Itanium `<type>` grammar the remainder
  encodes. Owner recovery (`owner_class_of()`, which only ever consumes
  `comps[:-1]`, dropping the leaf entirely) is unaffected either way.

  **A fifteenth finding pointed at a data-model assumption this module's
  own new code introduced without verifying, contradicted by an existing
  sibling.** `directly_referenced_stdlib_types()` built `non_stdlib_records`
  as a plain `dict[str, RecordType]` keyed by identity — when
  `snapshot.types` contains multiple entries sharing the same identity
  (e.g. a complete definition alongside an ODR-duplicate or incomplete
  declaration), a later entry silently overwrote an earlier one, so a
  public signature reaching that identity walked only the survivor (Codex
  review, fresh evidence). `surface.py`'s own `record_by_name` index —
  the established reference this module has mirrored throughout every
  finding above — already anticipates exactly this by keying on a *list*
  of records per identity (`dict[str, list[RecordType]]`) and walking
  every one (`for rec_node in rec_nodes: ...`), not a single winner; this
  module's new dict introduced a real regression relative to that already-
  correct sibling pattern, not a hypothetical edge case. Confirmed
  empirically both orderings (the complete definition first, and the
  complete definition last): whichever entry didn't survive the dict
  overwrite, if it carried a `std::` field the survivor lacked, that field
  was silently missed. Fixed by changing `non_stdlib_records` to
  `dict[str, list[RecordType]]` (appending instead of overwriting) and
  walking every record for a reached identity in the worklist loop,
  checking each one's own `origin` independently (a private-origin
  duplicate still excludes only itself, not a public-origin sibling
  sharing the same identity) — exactly mirroring `surface.py`'s own
  per-record walk.

  **Wiring (this pass):** `diff_types.py`'s single choke-point gate,
  `_is_abi_surface_type()`, now accepts a `directly_referenced` set (built
  once per detector via `_directly_referenced(old, new)`) and un-filters a
  std:: record that set names, instead of blanket-filtering every std::
  record regardless of direct use. Because every RecordType-based
  struct/union/field/kind/reserved detector in that file already shares this
  one gate function, wiring it there once covers all of them uniformly —
  not 9 independent, individually-drifting call sites. While wiring this in,
  the FP-rate corpus's own new cases (`stdlib-direct-reference` category)
  surfaced a second, *pre-existing* correctness gap in the gate's std::
  check itself (independent of `directly_referenced`): it filtered using
  `_is_non_abi_surface_type(t.name, ...)`, i.e. bare `t.name` only, the exact
  same bare-vs-qualified split as the `type_reachability.py` fix above — so
  a real castxml/clang-produced std:: record (bare `name`, qualified
  `qualified_name`) was **never actually filtered as std:: at all**,
  independent of whether anything referenced it. Fixed in the same gate by
  keying the std:: prefix check on `qualified_name or name` (the anonymous-
  type-marker half of the check still uses bare `name`, unaffected).
  `diff_platform.py`/`diff_symbols.py`/`diff_vtable_layout.py`/
  `diff_stdlib_impl.py`/`diff_layout.py`/`diff_filtering.py`/
  `diff_type_spellings.py`, plus `diff_types.py`'s own enum/typedef paths
  (which call `is_non_abi_surface_type`/`is_abi_surface_type_name` directly
  on enum/typedef names, not through `_is_abi_surface_type`), remain
  unwired and carry the identical bare-name gap — each needs its own
  individually-verified follow-up (FP-rate/mutation-score gates), not a
  drive-by extension of this pass's RecordType-scoped fix.
- **L4 SYCL replay via a resolved `--gcc-path icpx`/`dpcpp` override — flag
  vocabulary fixed, the two-pass JSON-document crash fixed, real host+device
  dual-context replay still not implemented.** Fixing L4 clang_bin
  resolution to honor `--gcc-path` (an earlier PR) meant L4 could for the
  first time actually invoke a SYCL-capable compiler (`icpx`/`dpcpp`)
  instead of always a bare `clang`, which surfaced a narrower, real gap:
  `-fsycl`/`-fsycl-*` wasn't in `adapters.base.ABI_RELEVANT_FLAG_PREFIXES`,
  so it never reached the reconstructed L4 replay command even when the
  real build recorded it (Codex review) — fixed, since the existing
  `abi_relevant_flags` carry-through (`replay_extra_flags`) already handles
  this class of flag correctly for every other case (`-std=`,
  `-fvisibility`, …), so this was a one-line vocabulary gap, not a design
  gap. That fix's own consequence #1 — "whether replaying without an
  explicit `-fsycl-host-only` pin causes `icpx` to attempt a device pass
  this pipeline can't consume" — was later **confirmed against a real
  toolchain**: a real `-fsycl` bazel compile unit (oneDAL, 2.8GB AST) hit
  exactly this, `json.load()` failing with `Extra data: line 26076735
  column 2` at the byte offset where the device pass's document begins,
  i.e. consequence #2 also confirmed (legacy `dpcpp`/`icpx` both emit
  multi-document host+device AST output for a plain `-fsycl`, the identical
  shape `sycl_context.py` already handles for the *L2* header-AST backend).
  **Now fixed** by mirroring the L2 backend's own fix
  (`dumper_ast_config._build_clang_header_command`/
  `dumper_clang._needs_sycl_host_only`) rather than adopting
  `sycl_context.py`'s full host/device dual-context decoder: L4 replay
  parses ONE compile unit at a time and has no `--frontend-context device`
  concept to select against (unlike L2's header-AST dump, which can be
  asked for either context), so there is nothing for a second document to
  serve here — `_clang_context_args` (shared by both the AST pass and the
  macro pass) now appends `-fsycl-host-only` whenever
  `dumper_clang._needs_sycl_host_only` says the resolved compiler is an
  Intel oneAPI driver with SYCL effectively enabled and no single pass
  already pinned, collapsing the compile back to the one host-side pass
  that actually links into the scanned `.so` — the device pass's SPIR-V
  kernel code never does. Reuses `_needs_sycl_host_only` directly (not a
  reimplementation) so the last-flag-wins `-fsycl`/`-fno-sycl` scan and the
  legacy `dpcpp`/`dpcpp-cl` SYCL-implied-by-default handling stay a single
  source of truth with the L2 fix. The remaining "Extra data" `json.load()`
  path now also emits an actionable hint (mirroring
  `dumper_clang_errors._parse_clang_ast_result`'s) for any *other*,
  not-yet-special-cased offload flag that still produces a multi-document
  stream, rather than a bare byte offset. **Still not implemented, and
  deliberately out of scope**: a genuine host+device dual-context L4
  replay (an L4 counterpart to `--frontend-context device`) — nothing in
  L4's `SourceAbiTu`/`CompileUnit` model has a notion of "this TU's device
  pass," so adding one is a real, separate design (schema + linker +
  cross-check changes), not a follow-up to this crash fix.
- **`depfile_args_from_argv()`'s `trusted_root` parameter — the self-jail
  vulnerability is closed, real production wiring not implemented.** Closing
  the vulnerability (a compile unit's own `directory` field, attacker-
  controlled for a unit sourced from an untrusted build pack, was used as
  both the resolution base *and* the trust jail for expanding an unexpanded
  `@response-file`) required the three production call sites
  (`ClangIncludeExtractor.extract_from_build`,
  `ClangPreprocessorExtractor.capture_macros`, `preprocessor_scan._depfile_context`)
  to fall back to the existing safe "drop the token" behavior, since none of
  them currently supply an independently-trusted `trusted_root` (Codex
  review). **Not implemented**: threading a genuinely-trusted root into
  those three call sites so response-file expansion works again for this
  secondary L5/S2-scoping path. This is real, non-trivial plumbing, not a
  one-line fix: `BuildEvidence.build_root`/`source_root` exist as fields but
  no adapter (`compile_db.py`, `cmake_file_api.py`, `ninja.py`, `bazel.py`,
  `make.py`) actually populates them today, so there is no already-flowing
  trusted value to read off the model — the real anchor would have to be
  threaded as a new parameter from `inline.collect_inline_pack()`'s own
  `sources`/`build_info` CLI arguments (or, for the separate Flow-2
  `abicheck_inputs/` ingest path in `inputs_pack.py`, the pack's own `root:
  Path` already used for `_safe_pack_path` containment) through several
  call layers in `inline.py` (already WARN-flagged oversized) and
  `preprocessor_facts.py`. The functional impact of the current gap is
  narrower than it first appears: `build_context.py`'s own `@file`
  expansion (correctly jailed to the compile database's own directory since
  the first response-file fix in this PR) already expands a
  `compile_commands.json`-sourced `CompileUnit.argv` *before* it reaches
  these three call sites, so they rarely see a raw, unexpanded `@file`
  token for that primary path in practice — the gap mainly affects the
  Flow-2 untrusted-pack path this fix was specifically about securing in
  the first place. Confirmed via the full local suite (20935 passed) that
  disabling expansion at these three call sites introduces no test
  regressions.
- **`Param.is_va_list` (G31 Phase C continued) inherits the toolchain-
  identity-probe gap above, rather than being a new problem.** Its
  extraction predicate (`dumper_clang_qualifiers._clang_param_is_va_list`)
  is deliberately scoped to the one ABI verified here — x86-64 System V —
  and a snapshot from an unrecognized target already degrades to a
  conservative `False` (see the function's own docstring). What's still
  open (Codex review, fresh evidence): the snapshot-level reliability flag
  (`AbiSnapshot.clang_va_list_facts_reliable`) records only "did the fixed
  extractor run", not "against which resolved target" — so two
  genuinely-different-target clang snapshots (x86-64 vs. AArch64, say) can
  both read as reliable, and a real cross-architecture comparison (which
  this tool's comparability layer permits in general) could read the
  x86-64 side's real detections against the AArch64 side's uniform `False`
  as a spurious `PARAM_BECAME_VA_LIST`/`PARAM_LOST_VA_LIST`. No header-AST
  fact has resolved-target validation today, not just this one — closing
  it here alone would be an inconsistent one-off fix for a structural gap;
  it belongs with the toolchain-identity probe above once that lands.
- **A using-declaration re-exporting a namespace-scope constant produces
  two `AbiSnapshot.constants` entries for one declaration on the castxml
  backend — reported against real oneDAL headers. A per-snapshot,
  name-shape-based dedup was implemented, reviewed, found unsound in two
  independent ways, and reverted rather than shipped broken (Codex
  review, three rounds).** `cpu.hpp` opens a plain (non-inline)
  `namespace v1 { constexpr ... cpu_feature_map = ...; }` and later does
  `using v1::cpu_feature_map;` inside the enclosing `detail` namespace —
  an ordinary C++ re-export pattern, not versioned-inline-namespace ABI
  tagging (`inline namespace v1 {}`, which is the shape
  `diff_namespaces.py`'s `_paired_stable_indices`/
  `EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT` machinery already merges
  aliases for — that machinery is function/type-scoped, cross-*snapshot*
  (old vs. new), and keys on `experimental`/`preview`/`v0` segments
  specifically, so it neither targets nor would catch this same-snapshot
  pattern even if it applied to constants). castxml's `<Variable>` XML
  records the using-declaration as a second full element rather than a
  reference to the original, so `dumper_castxml.py`'s
  `_iter_public_constants()` (the shared source for `parse_constants()`/
  `parse_constant_headers()`) emits both `detail::v1::cpu_feature_map`
  and `detail::cpu_feature_map` as independent entries in
  `AbiSnapshot.constants` with the same value — distinct qualified-name
  *keys*, not a key collision, so this is invisible to `model.py`'s
  existing first-wins duplicate-name dedup (`function_map`/
  `variable_map`/`type_by_name` all dedup by identity — mangled name or
  bare type name — under which a using-shadow naturally collapses onto
  its target; `AbiSnapshot.constants` has no mangled-name equivalent).
  Reported at real scale, not a one-off: 98 alias groups across 267
  constants in the reporting library's own dump. The same castxml
  behavior duplicates other declaration kinds too, with two different
  outcomes: 1,477 of 6,009 mangled function names were duplicated the
  same way, but that half is **not a *double-reporting* bug** —
  `model.py`'s mangled-name-keyed `function_map`/`variable_map` already
  collapse a using-shadow function or variable onto its target for free
  for the ABI/mangled-symbol question (a using-declaration doesn't change
  what a symbol mangles to), so this is not a repeat of the constants
  over-reporting problem above. **It is a separate, undocumented
  false-negative gap on the source/API side, narrower in scope than the
  `std::`-specific `STD_REEXPORT_REMOVED` detector below (Codex review,
  fresh evidence):** because the diff is keyed on the unchanged mangled
  symbol, a release that removes only the `using` re-export of a
  library's *own* function or variable — not a `std::` name, which is
  the one case `STD_REEXPORT_REMOVED` already covers — while keeping the
  real declaration and its export produces no finding at all, even
  though a consumer that named the alias-qualified spelling no longer
  compiles. This is the identical shape of gap the clang-side note below
  documents for constants (an unchanged underlying identity hides a real,
  source-visible alias removal), just reached from the opposite side
  (castxml capturing the alias correctly here, the *diff* layer being the
  one blind to it) — not attempted in this pass, and would need
  `detect_std_reexport_removed`'s general shape (matching declared
  qualified names, not `std::`-specific) extended to a library's own
  namespaces, which is its own scoped detector design, not a drive-by
  extension of this entry. A duplicated bare *type* name (`range`
  alongside `v1::range`) is a **separate, still-open** gap from the
  type-dedup entry already documented above
  (opaque-type suppression keyed by bare `RecordType.name` colliding
  *across namespaces* on an accidentally-shared bare name) —
  `range`/`v1::range` are two distinct qualified spellings of what may be
  the same using-re-exported record, so today's exact-bare-name
  first-wins dedup does not merge them either, and would need its own,
  differently-shaped fix (threaded through `RecordType` identity, not
  `AbiSnapshot.constants`), not attempted here.

  **Confirmed independently that the duplicate-entry shape is castxml-only
  — but the direct-clang L2 backend is not clean either, it just fails in
  the opposite direction.** Verified directly against real Clang 18
  (`-Xclang -ast-dump=json`) on a minimal repro of the same shape: a
  using-declaration lowers to a `UsingDecl` node carrying the qualified
  target name (`v1::cpu_feature_map`) but no `init`/value of its own,
  immediately followed by an **implicit**, **unnamed** `UsingShadowDecl`
  (`"isImplicit": true`, no `name` key at all — confirmed by inspecting
  the emitted JSON directly, not inferred). **That `UsingShadowDecl` node
  does carry real target identity** — a `target` object with the
  underlying `VarDecl`'s own `id`, `kind`, `name`, and `type` (confirmed
  by inspecting the real JSON: `{"id": "0x...", "kind": "UsingShadowDecl",
  "isImplicit": true, "target": {"id": "0x...", "kind": "VarDecl", "name":
  "cpu_feature_map", "type": {"qualType": "const int"}}}`) — so this is
  identity Clang exposes and `dumper_clang.py` simply never reads, not
  identity Clang lacks (Codex review, fresh evidence; an earlier revision
  of this entry claimed the latter, which was wrong). `_categorize()` has
  no branch matching either `UsingDecl` or `UsingShadowDecl` — its
  `VarDecl` branch requires both `kind == "VarDecl"` and a non-empty
  `name`, neither of which either using-related node satisfies — so both
  are silently dropped, `target` included, and the re-exported spelling
  never enters `AbiSnapshot.constants` at all on that backend.
  This is *complementary* missing coverage, not a clean bill of health:
  a release that removes only the `using v1::cpu_feature_map;` re-export
  while keeping the real `v1::cpu_feature_map` definition breaks every
  consumer of the re-exported spelling, but both the old and new
  Clang-derived snapshots contain only the one underlying key —
  `_diff_constants()` has nothing to compare and emits no
  `CONSTANT_REMOVED`, a silent false negative. Castxml's two qualified
  keys, over-reporting as they are for the case this entry is about, would
  at least detect that removal correctly. So the two backends fail in
  opposite directions on the same underlying gap (no alias-identity
  evidence for constants) — over-reporting on castxml, under-reporting on
  clang. Not verified against a live castxml run (no castxml binary
  available in this environment to reproduce the XML shape directly) —
  the castxml-side mechanism above is taken from the original report, not
  independently re-derived from castxml's own output. **Update (G31 Phase
  C, later pass): now independently re-derived, against a real
  conda-forge castxml 0.7.0 build, and the "opposite directions" framing
  in this paragraph does not hold — see the "Option (b) closed" note at
  the end of this entry, below, for the reproduction and its
  implications.**

  **Attempted fix, reverted (three review rounds):** a
  `qualified_name_segments.dedup_versioned_namespace_alias_items()`
  helper collapsed one spelling onto the other within one already-
  collected `_iter_public_constants()` item list, gated on BOTH (1) the
  two qualified names differing by exactly one versioned-inline-namespace
  *segment* (`version_strip_segments` — the same, already-trusted
  structural check `diff_namespaces.py` already relies on for its own
  alias merging) AND (2) the two values being byte-identical. Deliberately
  narrower than the plain value-equality merge this same module's own
  docstring already rejected after three earlier review rounds for a
  *different*, cross-snapshot reason (that heuristic had no name evidence
  at all and could merge unrelated same-valued declarations that never
  even coexist together) — this attempt was same-snapshot only and added
  a real structural name-shape gate. It was not enough. Two more rounds of
  review found it unsound from two independent directions, and a third
  round (after the second was "fixed") found the second fix was *also*
  wrong, at which point it was reverted rather than iterated a fourth
  time:

  1. **Round 1 (P1):** the first revision kept the shorter, version-
     stripped alias and dropped the version-qualified original —
     reasoned as "the spelling a consumer of the `using` re-export
     actually writes." This actively fabricated findings: a release that
     adds or removes only the re-export (the real declaration unchanged)
     went from one snapshot keeping `detail::v1::x` (no alias present,
     nothing to strip) to the other collapsing down to `detail::x`
     instead — two different surviving keys for one unchanged
     declaration, which `_diff_constants` read as a spurious
     `CONSTANT_REMOVED` + `CONSTANT_ADDED` pair. In other words: this
     revision manufactured exactly the kind of double-reporting it was
     written to eliminate, just shaped as fabricated add/remove instead
     of a duplicated `CONSTANT_CHANGED` — arguably worse, since a
     fabricated `CONSTANT_REMOVED` reads as BREAKING.
  2. **Round 2 (P1 again, on the "fix" for round 1):** reversing the
     direction — always keep the version-*qualified* spelling, always
     drop the alias — looked sound (invariant to whether either side
     happens to carry the re-export) and was reviewed as fixing round 1.
     A further round then produced a concrete counterexample proving the
     fixed direction was *also* wrong in general: `namespace detail {
     constexpr int x = 42; namespace v1 { using detail::x; } }` is
     legal C++ where a using-declaration imports a name **into** a
     versioned-looking namespace rather than out of one — here
     `detail::x` (the shorter spelling) is the real declaration and
     `detail::v1::x` (the longer, version-qualified spelling) is the
     alias, the exact reverse of what round 1's fix assumed universally.
     "Always keep the longer/qualified spelling" reproduces round 1's
     failure mode for this reversed input shape instead. **This is the
     structural finding that ended the attempt**: qualified-name *shape*
     alone (segment count, or which segment looks version-tagged) cannot
     determine which of two spellings is the real declaration and which
     is the using-introduced alias — a using-declaration can legally go
     in either direction relative to a versioned-looking namespace
     segment, and `_iter_public_constants()`'s current
     `(qualified_name, value, declaring_header)` output carries no
     signal (declaration order, an `artificial`-style marker, a
     using-shadow/target back-reference) that distinguishes the two
     directions. No fixed, name-shape-based rule can be sound for both.
  3. **Round 3 (P2, on the value-equality gate itself, independent of
     direction):** even a correctly-directed rule would still merge two
     genuinely independent declarations that happen to form a
     version-alias-shaped name pair AND happen to share a value at
     extraction time (e.g. `namespace detail { namespace v1 { constexpr
     int x = 42; } constexpr int x = 42; }` — two unrelated `x`s, no
     `using` anywhere) — the same class of coincidence-driven risk this
     module's docstring already flags for the cross-snapshot merge it
     rejects, now shown to reach the narrower same-snapshot case too.

  Given round 2 proves no per-snapshot, name-shape-based direction rule
  can be sound, and a genuinely correct fix needs real using-shadow/
  target identity evidence rather than name shape — continuing to patch
  the heuristic's direction a third time was exactly the "one more
  counterexample" pattern this file's own linkage-blind-removal and
  type-identity entries above already warn against repeating. Reverted in
  full (code, tests, changelog fragment) rather than shipped with a
  known-unsound direction, per this file's own "known gaps over risky
  reactive patches" convention — and per the same "attempted twice,
  reverted twice" discipline already established here: a false BREAKING
  finding fabricated by an unsound heuristic is a worse failure mode than
  the pre-existing double-reporting it would have fixed, since the
  original bug is merely noisy while a wrong-direction fabricated
  `CONSTANT_REMOVED` blocks a release for nothing.

  **What "real identity evidence" actually means differs by backend, and
  the two must not be conflated (Codex review, fresh evidence corrected
  an earlier draft of this entry that did conflate them).** On
  direct-clang, the identity evidence demonstrably EXISTS today and is
  simply unused: `UsingShadowDecl.target` (see above) names the exact
  underlying `VarDecl` by AST id, so a sound clang-side fix is a real,
  scoped option — capture the shadow, resolve `target.id` back to the
  already-visited `VarDecl`, and re-register the alias under the
  using-declaration's own enclosing-scope name, carrying its target's
  identity forward rather than guessing from name shape. That is new
  extraction work (`_categorize()` doesn't currently visit
  `UsingShadowDecl` at all) and was not attempted in this pass, but it is
  a materially different, better-founded starting point than the
  name-shape heuristic this entry's earlier rounds tried and reverted. On
  castxml, whether an equivalent target back-reference exists in its
  `<Variable>` XML is genuinely **unknown** — not confirmed absent, not
  confirmed present — since no castxml binary was available in this
  environment to inspect real output for this construct; the reported
  castxml duplication mechanism throughout this entry is taken from the
  original report, not independently re-derived. A correct, full fix
  needs one of: (a) the clang-side `target.id` threading described above,
  landed and verified against the FP-rate/mutation-score gates before
  trusting it; (b) inspecting real castxml XML for this construct to
  determine whether it exposes anything equivalent, which this
  environment cannot do; or (c) a genuinely different architecture that
  resolves the ambiguity at diff time with both sides' full data available
  simultaneously (mirroring how `diff_namespaces.py`'s
  `_paired_stable_indices` jointly builds paired OLD/NEW indices for
  functions/types, gated on real identity) rather than destructively
  dropping information from one side's snapshot in isolation — a
  materially larger, cross-cutting change on its own, not a follow-up
  patch to the same per-snapshot helper.

  **Option (b) closed (G31 Phase C, real conda-forge castxml build):** a
  real, policy-conformant castxml (0.7.0, `conda-forge`, within the
  `>=0.6.11,<0.8.0` range `castxml_policy.py` enforces, bundled Clang
  20.1.8) is now available and was used to reproduce this construct
  directly through the exact invocation shape `_build_castxml_command`
  emits (`--castxml-cc-gnu (g++ -x c)`/`--castxml-cc-gnu g++`, matching
  real header-dump usage) — **the originally-hypothesized castxml
  duplication mechanism does not reproduce.** For `namespace detail {
  namespace v1 { constexpr int cpu_feature_map = 42; } using
  v1::cpu_feature_map; }`, real castxml emits exactly **one** `<Variable>`
  element (`context="_13"`, i.e. `v1` — never `detail`), and that single
  element's id is listed in *both* `detail`'s and `v1`'s `<Namespace
  members="...">` attribute — a shared reference, not two elements. There
  is no `<Using...>`-shaped XML tag in castxml's schema at all (checked
  against castxml's own `--help`), and forcing the value to be ODR-used
  (`&cpu_feature_map` from an inline function) does not change this.
  Since `dumper_castxml.py`'s `_variable_els`/`_iter_public_constants()`
  iterate the flat, once-per-XML-element id map (`_build_id_map`), not
  either namespace's `members` list, they see this construct exactly once
  too — `_qualified_name()` resolves it to `detail::v1::cpu_feature_map`
  only; the alias spelling `detail::cpu_feature_map` never enters
  `AbiSnapshot.constants` on this castxml version, at all. The identical
  single-element behavior was independently confirmed for the sibling
  type-dedup case this entry cross-references (`struct range` in `v1`,
  `using v1::range;` in `detail`): one `<Struct>` element, shared between
  both namespaces' `members` lists, never a duplicate. **What this means
  for the entry above:** the "castxml over-reports (duplicate keys),
  clang under-reports (missing alias)" asymmetry this entry documented
  was written from an unverified report and does not hold for the
  currently-supported castxml version range — for this exact construct,
  both backends now demonstrably fail the *same* way (silent false
  negative: the alias spelling is absent from the snapshot entirely,
  matching the clang-side finding already confirmed above). This does not
  retroactively invalidate the original report (98 alias groups across
  267 constants, real oneDAL headers) — that scan may have used a
  different castxml build, or the real duplication may come from a
  different source construct entirely (e.g. two independent, textually
  separate definitions sharing a value, rather than a language-level
  using-declaration/using-directive) that this pass did not have the
  original headers to reproduce against. It does mean: (1) the three
  reverted name-shape-heuristic attempts documented above were correctly
  reverted regardless of this finding (their unsoundness was proven by
  counterexample, independent of whether castxml duplicates), so nothing
  here reopens that question; (2) a future fix attempt should design for
  the *false-negative* alias-identity gap confirmed symmetric on both
  backends (option (a)'s `UsingShadowDecl.target` clang-side threading is
  now the best-founded starting point, since it is real, already-present
  AST identity, not a heuristic), rather than a castxml-side dedup this
  evidence no longer motivates; (3) closing this for real still needs the
  original large-scale report's exact source construct identified before
  trusting any fix against it — not attempted here, since the oneDAL
  headers that produced the original 98/267 count are not available in
  this environment either.
- **`AbiSnapshot.typedefs` is a flat `dict[str, str]` keyed by bare
  (unqualified) name on both header backends — a member/nested typedef
  silently collides with, and can be overwritten by, any other typedef
  anywhere in the snapshot sharing the same bare spelling. Confirmed by
  reading both producers directly, not yet fixed (G31 Phase C CastXML
  fact-completeness audit).** `dumper_castxml.py`'s `parse_typedefs()` and
  `dumper_clang.py`'s `parse_typedefs()` both do the identical
  `typedefs[name] = underlying`, where `name` is the typedef's own local
  `name` attribute/node field — never the scope-joined qualified spelling
  `_qualified_name()`/`_qualified()` uses for every other declaration kind
  in the same two modules. Verified directly against real CastXML XML
  output (`--castxml-output=1`, clang-emulated) for a minimal repro: a
  member typedef nested inside a struct (`struct WithUsing { using
  value_type = int; ... };`) is emitted as an ordinary top-level
  `<Typedef name="value_type" ... context="_12" .../>` element, structurally
  indistinguishable from a namespace-scope typedef except for its
  `context` attribute — which `parse_typedefs()` never reads. Two structs
  each declaring their own `value_type` member alias (an extremely common
  C++ pattern — STL-container-shaped types conventionally expose
  `value_type`/`size_type`/`reference`/... as member typedefs) collide on
  the identical bare key `"value_type"` in the resulting dict, and
  whichever element is encountered last in document order silently wins —
  the other's aliasing information is dropped from the snapshot entirely,
  with no warning, error, or any user-visible sign of the loss. The same
  collision shape reproduces on the direct-clang backend: `_typedefs` is
  populated by the same flat walk used for every other decl kind, but
  `parse_typedefs()` discards the entry's own recovered `scope` and keys
  only on `node["name"]`. **Not fixed here**: this is a real, if narrow,
  public-model change — `AbiSnapshot.typedefs`'s key shape is read by
  `type_reachability.py`'s `_typedef_spelling_targets()` (see the
  "Type reachability" entry above, which already works around this same
  bare-key ambiguity from the *consumer* side via its own
  ambiguity-counting helper, `_typedef_spelling_targets`, rather than
  assuming a bare key uniquely names one typedef), by `diff_types.py`'s
  typedef diffing, and by `surface.py`'s typedef-following in
  `_walk_type_closure` — none of which currently have test coverage for
  the cross-class member-typedef-collision case to validate a change
  against. A correct fix needs a qualified (or at minimum
  collision-detecting) key threaded through both producers and every
  consumer simultaneously, each independently re-verified against the
  FP-rate/mutation-score gates before trusting it — the same systematic,
  cross-cutting shape (and the same "known gaps over risky reactive
  patches" reasoning) as the already-documented bare-`RecordType.name`
  opaque-type-suppression collision above, for a different model field.
  Filed here per this file's own convention rather than attempted under
  this pass's time budget.

  **Closed, additively, in a later pass (G31 Phase C continued).** Rather
  than replacing `AbiSnapshot.typedefs`'s key shape — which would have
  meant re-verifying every one of its existing consumers
  (`type_reachability.py`, `diff_types.py`, `surface.py`) against a changed
  contract, plus every external Python-API caller reading that field
  directly — the actual fix is purely additive: a new field,
  `AbiSnapshot.typedefs_qualified` (schema v25), a fully-qualified-name-keyed
  twin populated by both `dumper_castxml.py`'s and `dumper_clang.py`'s
  `parse_typedefs_qualified()` (using the identical `_qualified_name()`/
  `_qualified()` scope-joining every other declaration kind in those modules
  already uses) alongside the existing, deliberately-unchanged
  `parse_typedefs()`. Since a qualified name is unique per declaration, this
  twin cannot suffer the bare-name collision at all — both `Foo::value_type`
  and `Bar::value_type` survive as distinct entries where only one bare
  `value_type` could before. Threaded through the ELF manifest per-TU merge
  path too (`TuFragment`/`MergedTuFragments`/`ElfHeaderAstResult` in
  `tu_fragment.py`/`tu_merge.py`/`dumper_manifest.py`), closing the identical,
  separately-documented "Known, accepted limitation" comment `tu_merge.py`
  carried for the multi-TU case specifically. Only one consumer was wired to
  actually use the new field: `type_reachability.py`'s
  `directly_referenced_stdlib_types()` (via a new `_merged_typedefs()` helper
  that folds `typedefs_qualified` into the flat dict already passed to
  `_typedef_spelling_targets()` and siblings) — closing the real false
  negative where a public signature spelled with the qualified alias the
  bare dict had already lost could silently miss a reachable `std::` field.
  `diff_types.py`'s typedef diffing and `surface.py`'s typedef-following in
  `_walk_type_closure` are **not** wired to the new field in this pass — each
  is its own scoped follow-up, not a drive-by extension, since each has its
  own call shape and (per this file's own established discipline) needs its
  own test coverage before trusting a change to it. No reliability flag was
  needed for the new field (unlike the `*_facts_reliable` flags v19–v23 use
  for a real-but-wrong scalar default): an empty `typedefs_qualified` is
  exactly the same value a genuinely-typedef-free snapshot would carry, so a
  pre-v25 snapshot degrades cleanly to "no extra qualified data available"
  rather than being misread as a real fact — see `serialization.py`'s own
  v25 history-comment entry for the full reasoning. Verified via new unit
  tests on both header backends directly (`_CastxmlParser.
  parse_typedefs_qualified`/`_ClangAstParser.parse_typedefs_qualified`) and
  an end-to-end `type_reachability` regression proving the bare dict's lossy
  collision no longer hides a real `std::` field.

- **`run-plan.json`'s `gate` block is unprotected against an already-shipped,
  version-skewed reader — investigated end-to-end, confirmed structurally
  infeasible to close within the document's own shape, not fixed (Codex
  review, PR #779).** `aggregate_manifest.py`'s `gate` block gained real
  protection against an old `aggregate` binary misapplying a new manifest's
  policy: `_check_manifest_version` already existed *before* this PR with
  real MAJOR-rejection logic (`major > supported` raises), so bumping
  `aggregate_manifest_version` from `1.0` to `2.0` gives an old, already-
  installed reader a real, working rejection point — it already knows how to
  say "too new for me." `run-plan.json`'s new `RUN_PLAN_SCHEMA_GATE`
  (`abicheck.run-plan/v2`) discriminator was modeled on the identical
  pattern but does **not** give the same protection, and cannot: traced the
  full old (pre-PR779, commit `90e1813`, the last shipped state) `aggregate
  --run-plan` pipeline directly. (1) Old `RunPlan.from_dict()`/
  `RunPlanCheck.from_dict()` use exclusively total, defensive conversions
  (`str()`, `bool()`, `isinstance`-guarded comprehensions, `.get()` with a
  default for everything) — by design, matching this package's own
  documented forward-compat convention ("every dataclass carries
  `to_dict()`/`from_dict()` with defensive `.get()` parsing so a newer/
  hand-edited pack never aborts a load" — `abicheck/buildsource/CLAUDE.md`).
  Nothing in either function can raise regardless of input shape, so an
  unrecognized `schema` string and an unknown `gate` key are both silently
  ignored, not rejected. (2) Old `aggregate --run-plan` doesn't validate the
  parsed plan directly at all — it projects it via `to_aggregate_manifest
  (plan)`, which stamps `"aggregate_manifest_version": AGGREGATE_MANIFEST_
  VERSION` using **the old binary's own hardcoded constant, imported fresh
  at call time** — never read from or influenced by the run-plan.json's own
  `schema` field. The one place a version-rejection check *does* fire
  (`ExpectedTargets.from_manifest_data`) is therefore always checking the
  old reader's own version against itself, and can never observe a
  "too new" value regardless of what the source run-plan.json declares.
  **Consequence:** a genuinely version-skewed setup — an already-installed
  old `abicheck aggregate --run-plan` reading a `run-plan.json` a newer
  `project plan --gate-missing-required`/`--gate-unexpected-target` wrote —
  silently drops the requested gate policy and falls back to the old
  binary's hard-coded `fail`/`include` defaults, exactly the "silently wrong
  gate decision" class of bug the manifest-side `2.0` bump exists to
  prevent, just unreachable to prevent here. **Not fixable within the
  document's own shape**: every value in both dataclasses is consumed via a
  total, defaulting conversion, so no crafted field value can force old,
  already-shipped code to raise — this is a property of code that has
  already been released and cannot be retroactively patched, not a
  correctness gap in this PR's own new code. Closing it for real would need
  a mechanism outside the JSON document itself (e.g. a CI-level convention
  that `project plan` and `aggregate --run-plan` are always the same
  abicheck version/pinned together, enforced or documented at the tooling
  level) — out of scope for a schema-shape fix and not attempted here.

- **`scan --config <path>` can execute an untrusted `build.query` even when
  `resolve_effective_allow_query` (ADR-037 D4 "level-implies-query") denies
  authorization — confirmed real and confirmed pre-existing (CodeRabbit
  review on #817, fresh evidence, traced through git history rather than
  assumed).** `cli_buildsource.embed_build_source()`'s own
  `cfg_trusted_for_query = build_config is not None or build_query is not
  None` computation treats a merely-non-`None` `build_config` path as
  sufficient authorization to run that config's `build.query` key
  (`buildsource/inline.py`'s `collect_inline_pack`, gated on
  `build_config_trusted_for_query`) — it has no way to distinguish "a config
  path was supplied" from "querying was actually authorized." That trust
  model is intentional for `dump`/`compare` (an explicit, operator-supplied
  `--config` is deliberately trusted to execute its own `build.query` —
  see `embed_build_source`'s own inline comment), but `scan` layers a
  stricter, independently-designed rule on top
  (`cli_scan_helpers.resolve_effective_allow_query`: the config must both
  declare `build.query` *and* have an explicitly-pinned deep evidence level)
  that this shared function's own trust check never consults. Confirmed
  pre-existing, not introduced by PR #817 or by #814's "PR 3A convergence"
  it merged: at commit `551725e` (immediately before #814),
  `scan_engine._build_new_snapshot` already called
  `embed_build_source(build_config=build_config, allow_build_query=
  allow_build_query, ...)` directly, passing the raw `--config` path
  unconditionally, independent of `allow_build_query`'s value — the
  identical bypass already reachable there. `service_input_resolution.
  _gated_build_query_inputs`'s `build_config_locally_trusted` parameter
  (added by #814) was built explicitly to preserve that pre-migration
  behavior for `scan` (see its own docstring), not to introduce it. **Not
  fixed here**: a correct fix needs `embed_build_source`/
  `collect_inline_pack` to accept an authorization signal genuinely
  distinct from "was a config path given" — threaded through two
  independent call sites (the L2 seed's own pack-arg resolution in
  `l2_seed._resolve_l2_seed_pack_args`, and the L3-L5 embed step in
  `embed_build_source` itself) — verified against real `scan --config`
  scenarios where the config sets `build.query` but no deep evidence level
  is pinned. That is a real, cross-cutting change to a shared trust
  primitive `dump`/`compare` also depend on, not a one-line change to
  `_gated_build_query_inputs`'s gate. Filed here per this file's own
  "known gaps over risky reactive patches" convention rather than attempted
  under review pressure on an unrelated PR.

- **`DEFAULT_SYSTEM_PROVIDERS` (`bundle_models.py`) is a hand-maintained
  soname allow-list, not a real topology model for bundle-level
  system-provider classification -- a tactical fix that has grown, not a
  designed feature (Codex review on #791, fresh evidence).**
  `bundle.compare_bundle()`'s unresolved-import check
  (`ChangeKind.BUNDLE_INTRA_DEP_REMOVED`, `_detect_intra_dep_removed()`)
  does not exempt an import merely for being "against any" soname in this
  union -- it suppresses a consumer's finding only when that consumer's
  remaining non-intra `DT_NEEDED` edges are all non-empty AND every one of
  them matches `set(DEFAULT_SYSTEM_PROVIDERS) | set(bundle_system_providers)`
  (or the `_looks_system` heuristic), AND EITHER no in-bundle sibling
  version-compatibly provided the symbol and was reachable from *this*
  consumer OR the user explicitly named one of the remaining sonames via
  `explicit_providers` -- `ever_provided_in_bundle` is `compatible_old &
  _old_reachable(consumer.library)`, scoped to what this specific consumer
  could reach, not "any" in-bundle sibling regardless of reachability; an
  unreachable sibling exporting the symbol does not, by itself, block the
  allow-list suppression (Codex review, PR #910, fresh evidence, correcting
  this exact paragraph's own prior "no in-bundle sibling ever" wording). A
  consumer with a mixed intra-bundle/external dependency set can still
  produce the finding, and a match against the allow-list alone is not
  sufficient. Correct for the ordinary libc/libstdc++/libpthread
  runtime set the
  constant started from, but each addition since (oneTBB's `libtbb.so.*`
  and its allocator-proxy libs, oneMKL/Intel-runtime and Level Zero
  entries, PR #883) was a real, reported false positive fixed by naming one
  more vendor runtime rather than by asking what "system-provided" actually
  means for a bundle -- and a growing vendor runtime not yet on the list
  still reproduces the exact false positive it exists to prevent. **Already
  designed, not a fresh gap to sketch a fix for here**: `docs/contribute/
  plans/g42-check-identity-environments-and-provider-resolution.md` names
  this exact limitation as one of its own three motivating problems and
  lays out the real fix ("Environment-aware system-provider resolution") --
  resolving each external dependency edge against a declared environment's
  sysroot/runtime matrix (presence, SONAME, export, symbol version, runtime
  floor), with the static allowlist demoted to a fallback for a project
  that doesn't opt into a declared environment. Route any work on this to
  that plan rather than reinventing a provider-classes registry here.
  **This gap is bundle-wide, not just `compare_bundle()`'s.** `audit_bundle()`
  (`scan --artifact-set`'s one-sided entry point) unions the identical
  `set(DEFAULT_SYSTEM_PROVIDERS) | set(bundle_system_providers)` allow-list
  and feeds it to its own predicate, `_detect_unresolved_intra_dependency()`
  -- not a call into `_detect_intra_dep_removed()`, since an audit has no old
  side to diff against. That predicate's own docstring documents three real
  differences from the diff-driven one (version-aware and reachability-
  constrained provider matching, a suppression path that additionally
  requires the consumer have zero intra-bundle `DT_NEEDED` edges, and a
  `COMPATIBLE_WITH_RISK`-not-`BREAKING` verdict), so a G42 fix landing only
  on the `compare_bundle()` path would leave `scan --artifact-set` audit-mode
  classification on the static allow-list with its own, differently-shaped
  false-positive exposure (Codex review, PR #910, fresh evidence). Any work
  on this gap needs both call sites in scope.

- **PR C (typed `dump`/`scan` convergence, CLI cleanup phase two's PR 3A) —
  investigated in depth; one real, scoped, verified slice landed; the full
  convergence the plan describes is NOT attempted, and here is exactly why.**
  The plan (`docs/contribute/plans/cli-cleanup-phase-two.md`, "PR 3A") asks
  for one canonical path -- `Click parsing → DumpRequest → ResolvedDumpRequest
  → dry-run-or-execute → DumpResult` -- with **both** `dump_cmd`/
  `perform_elf_dump` and `scan_engine._build_new_snapshot` routing through
  `service_dump_pipeline.run_dump_request` (or the per-input primitives it
  shares via `service_input_resolution.resolve_side_snapshot`), the way
  `compare`'s implicit-dump operand already does, with `dump --dry-run`
  rendering a real `ResolvedDumpRequest` object -- the resolve-only step
  execution builds on, not the executed `DumpResult` itself (a `--dry-run`
  that renders `DumpResult` would have to have already executed, which
  contradicts its own never-executes contract; see the plan's PR 3A section,
  "ResolvedDumpRequest and DumpResult are two distinct objects") -- rather
  than a separately-computed preview. Read `run_dump_request`, `resolve_side_snapshot`
  and siblings, `perform_elf_dump` (1999 lines), `handle_non_elf_dump`, and
  `scan_engine._build_new_snapshot` in full before concluding this.

  **Three independent, concrete reasons the full migration cannot be done
  soundly in one pass, each confirmed by reading the actual code rather than
  assumed:**

  1. **`dump --dry-run` is not a projection of a resolved object today -- it
     is a hand-written second implementation.** `cli_dump_helpers.
     render_dump_dry_run()` re-derives the resolved depth/collect-mode/
     compile-DB-match/backend from the *same raw inputs* `perform_elf_dump`
     receives, independently, in its own function -- there is no shared
     `ResolvedDumpRequest` either one builds from. Its own docstring is
     explicit about the scope this implies: "Cheap, read-only resolution
     only ... Never runs castxml/clang, a build query, or any I/O beyond
     stat()/PATH lookups" -- i.e. it is *deliberately* a cheaper, narrower
     re-implementation, not a dry pass of the same resolver the real run
     uses. **Half-closed by the slice landed below**: `resolve_dump_request`
     now IS a real "resolve without executing/writing" mode --
     `service_dump_pipeline.resolve_dump_request`/`ResolvedDumpRequest`,
     stopping before any castxml/clang invocation or write, exactly what
     this blocker originally said didn't exist. What remains open is purely
     the wiring: `render_dump_dry_run()` has not been migrated to build from
     a real `resolve_dump_request()` call in place of its own independent
     re-derivation -- the capability exists, nothing consumes it yet.
  2. **`perform_elf_dump` has real post-processing hooks with no equivalent
     in `run_dump_request` at all.** After the primary header-AST/DWARF
     snapshot, it runs, in a carefully established order: the ADR-039
     build-context collector (`_attach_build_context`), the G31
     `service._attach_header_graph` second pass (its own independent clang
     re-invocation and AST cache key), and the optional
     `ABICHECK_CLANG_LAYOUT_TOOL` clang-layout-tool attach -- each reusing
     the L3→L2-folded `effective_compile_context` (PR #782) and each with
     its own dedicated, hard-won correctness fixes recorded above in this
     same "Known gaps" section (findings 9, 10, 17, 18 on the L3→L2-fold
     entry alone). `run_dump_request` has no post-processing stage at all
     -- it returns whatever `resolve_side_snapshot` produced. Routing
     `perform_elf_dump` through it would mean either dropping these passes
     (a real snapshot-completeness regression) or adding an equivalent
     hook to `run_dump_request` and re-verifying all four passes'
     already-fixed ordering/cache-key/flag-isolation bugs against the new
     call shape -- itself a project the size of this whole PR, not a
     rename.
  3. **`scan_engine._build_new_snapshot` has scan-specific behavior
     `DumpRequest` cannot express, and this file already documents two
     multi-round bug hunts in exactly this area that a rushed reroute
     would risk reopening.** Its own `-H old=PATH`/`-I old=PATH` side-aware
     baseline handling (the twelfth/thirteenth/fifteenth findings on the
     L3→L2-fold entry above) decides, per comparison, whether the
     candidate's own L3-folded `compile_context` may be reused for the
     baseline parse or must fall back to the caller's unfolded one -- a
     decision inherently about *two* snapshots' relationship, which
     `DumpRequest` (built for *one* input) has no field for. Forcing this
     through a `DumpRequest`-shaped call would need a new pair-aware
     concept alongside it, which is exactly the kind of design `service_
     compare_pipeline.py`'s own module docstring already explains was
     deliberately kept *out* of `service_input_resolution.py`'s per-input
     primitives ("The pair-shaped decisions deliberately stayed behind...
     neither means anything for a lone dump").

  **What landed instead, safely and independently of all three blockers
  above: `service_input_resolution._seeded_includes`/
  `_seeded_compile_context` -- the shared per-input primitive `compare`'s
  implicit-dump operand and `dump`'s typed API (`run_dump_request`) both
  already use -- ran the L2 include-dir seed and the P0.3 L3→L2
  compile-context fold as two independent calls, each capable of running
  `buildsource.inline.collect_inline_pack()`.** That is the exact
  self-deadlock shape already found and fixed, by name, for `perform_elf_
  dump`/`handle_non_elf_dump`/`scan_engine._build_new_snapshot` in this same
  section's L3→L2-fold entry (its "fifth finding"): a caller whose
  `sources`/`build_info` genuinely needs the zero-config *inferred*
  build-system query would have the include-dir seed's own inferred query
  hold the deterministic build-dir lock until its cleanup runs --
  deliberately deferred until after the L2 parse consumes the seeded dirs --
  so the compile-context fold's own, separate inferred-query attempt would
  contend on the identical lock. That fifth finding's fix,
  `buildsource.l2_seed.seed_includes_and_fold_compile_context()`, was wired
  into all three CLI-side resolvers at the time -- but never into
  `resolve_side_snapshot`, the fourth, typed-API call site, which kept the
  older two-call shape. `resolve_side_snapshot` never actually hit the
  600s timeout (`collect_mode` is forced `"off"`/`allow_inferred_build_
  query=False` here, matching every Tier-2 API caller's "never execute a
  build system as a side effect" rule -- see `_seeded_includes`'s own
  docstring), so this was real, avoidable duplicated work and a real
  divergence from the one shared primitive the other three call sites had
  already converged on, not a live self-deadlock. Fixed by replacing the
  two separate helpers with one, `_seeded_includes_and_compile_context()`,
  which calls the identical `seed_includes_and_fold_compile_context()` the
  other three sites already use. This is a genuine, if narrow, piece of PR
  3A's actual convergence goal -- one fewer place a change to how an input
  resolves can drift -- landed without touching any of the three blockers
  above. Verified via the existing `resolve_side_snapshot`/
  `_seeded_compile_context` test coverage in `tests/test_header_compile_
  context.py` and `tests/test_bazel_root_targets_l2_seed.py` (both updated
  for the renamed/merged function, not weakened), plus the full fast unit
  suite and `mypy`/`ruff` clean on both touched modules.

  **What remained genuinely open at the time this paragraph was written,
  and why forcing it further would have been reactive rather than sound**:
  all three blockers above, in full -- a "resolve without executing" mode
  for `run_dump_request`, a post-processing hook `perform_elf_dump`'s
  second-pass attaches can plug into, and a pair-aware primitive `scan`'s
  baseline-reuse decision can express -- are each their own real,
  multi-file design, not a follow-up edit to this same PR. Given the
  density of prior review rounds already recorded against this exact code
  (the L3→L2-fold entry above alone lists eighteen numbered findings,
  several reverted-and-refixed), attempting any of the three under
  continued session pressure risked reopening one of them, which is
  precisely what this file's own "known gaps over risky reactive patches"
  convention exists to avoid. **Superseded for blocker 1 by the slice
  below**, landed the same day: `resolve_dump_request` now provides that
  "resolve without executing" mode, so only the wiring (migrating
  `render_dump_dry_run()` to build from it) remains open for blocker 1;
  blockers 2 and 3 are still fully open, unchanged. See the plan doc's own
  PR C section for a status note recording the same scope.

  **A second, narrow slice landed (2026-08-18): the first blocker's missing
  primitive now exists, though nothing consumes it yet.** `service_dump_
  pipeline.py` gained `ResolvedDumpRequest`/`DumpResult` (additive
  dataclasses) and split `run_dump_request` into `resolve_dump_request()`
  (validation + evidence resolution, no castxml/clang, no write) and
  `execute_dump_request()` (the actual `resolve_side_snapshot` call, the
  dependency walk, the depth floor). `run_dump_request` itself is now a
  literal composition of the two (`execute_dump_request(resolve_dump_
  request(request)).snapshot`) and keeps its existing signature and return
  type unchanged — no breaking-API decision needed, confirmed by two Codex
  review rounds on the design before it was coded (see the plan doc's PR C
  section for what those rounds caught: `ResolvedDumpRequest` and
  `DumpResult` must stay genuinely distinct objects — a `DumpResult`
  carrying a real storage result has, by construction, already executed, so
  it cannot also be what a read-only `--dry-run` renders; and the achieved
  effective depth belongs on `DumpResult`, not `ResolvedDumpRequest`, since
  `fold_dump_provenance_into_dict` derives it from the completed snapshot
  and a resolve-only object has none to derive it from). **This closes only
  the first blocker's missing capability, not the blocker itself**:
  `cli_dump_helpers.render_dump_dry_run()` is still the independent,
  hand-written second implementation it always was — migrating it to build
  from a real `resolve_dump_request()` call is unattempted, and blockers 2
  and 3 (the post-processing hooks, the pair-aware scan baseline decision)
  are both still fully open, for the identical reasons already given above.
  Verified via new direct tests on the split itself
  (`tests/test_typed_dump_request.py::TestResolveExecuteDumpRequestSplit` —
  the resolve step never reaches `resolve_input`, the two-step path
  produces the identical snapshot `run_dump_request` does, the depth floor
  raises only at execute time, and `DumpResult.effective_depth` matches
  `_gated_source_label` computed the same way `fold_dump_provenance_into_dict`
  already does), the full existing `test_typed_dump_request.py`/
  `test_header_compile_context.py`/`test_clang_header_backend_integration.py`
  suites (unchanged, still green), the full fast unit suite, and
  `mypy`/`ruff` clean on both touched files.

  **Re-investigated (2026-08-19): the dry-run migration (blocker 1) is
  larger than "wire the renderer to `resolve_dump_request()`" —
  `dump_cmd` has no `DumpRequest` to resolve in the first place, on
  either branch.** Read `cli.py`'s `dump_cmd` in full, not assumed: it
  never constructs a `DumpRequest` object anywhere (confirmed by grep —
  no `DumpRequest(` call site exists in `cli.py` or `cli_dump_helpers.py`
  today). Its real resolution path is two CLI-only helpers,
  `resolve_dump_collect_context`/`resolve_dump_compile_context`
  (`cli_dump_helpers.py`), computing `collect_mode`/`header_backend`/
  `includes`/`gcc_option_tokens` directly off raw Click parameters,
  entirely independent of `service_input_resolution`/
  `service_dump_pipeline.resolve_dump_request` — and this is not a
  dry-run-only path: those same locals feed the real `dump()` call a few
  hundred lines later in the same function, on the non-dry-run branch.
  So migrating `render_dump_dry_run` to build from a real
  `ResolvedDumpRequest` is not an isolated renderer change: it requires
  first constructing a `DumpRequest` from `dump_cmd`'s ~30 CLI
  parameters (matching Click's own parsing precisely — including the
  `_resolved_compile_context`/`_resolved_collect_mode`/`_resolved_
  include_labels`/`_resolved_lang_explicit` private hooks `compare`'s own
  `ctx.invoke` already threads through this same command for its
  implicit-dump operand), and doing so for *both* branches at once, so
  the preview and the real run cannot silently diverge the moment only
  one of them migrates. That is blocker 2 restated from the other
  direction: the real run cannot move to `execute_dump_request()`
  without the post-processing hooks blocker 2 already names, and the
  dry-run preview cannot honestly move to `resolve_dump_request()` alone
  while the real run it previews keeps using a wholly different resolver
  — a preview built from one resolver describing an execution built from
  another would be strictly worse than today's "two independent
  implementations, kept in sync by hand," since it would *look*
  authoritative without being connected to what actually runs. Not
  attempted here, for the same reason blockers 2/3 were not: a real,
  cross-cutting redesign of `dump_cmd`'s ~250-line resolution section
  (`cli_dump_helpers.py` is already at its 2000-line AI-readiness hard
  cap, so any new shared surface needs a sibling module, not an inline
  addition), not a follow-up to the already-landed `resolve_dump_request`/
  `execute_dump_request` split — that split remains real, additive
  progress in its own right, just not yet consumed by `dump_cmd`.

  **Slice landed (2026-08-19, same session): `service_input_resolution.
  SideResolution`/`_resolve_side_snapshot_impl`, plus two newly-found,
  narrower blockers on the next step.** `_resolve_side_snapshot_impl`
  (the real implementation behind `resolve_side_snapshot`, now a one-line
  wrapper) returns the P0.3 fold's own effective `includes`/
  `CompileContext` — previously computed inside `resolve_side_snapshot`
  and discarded after use — as a new `SideResolution` object;
  `service_dump_pipeline.DumpResult` surfaces the same two fields,
  populated by `execute_dump_request`. Purely additive, zero behavior
  change for every existing caller, fully tested
  (`tests/test_typed_dump_request.py`). This is real progress toward "one
  shared primitive," but attempting the next step — routing
  `perform_elf_dump`'s primary parse through it — found the two are not
  simply duplicate callers of one function: `perform_elf_dump` receives an
  *already-resolved* `debug_info_path` from its caller (`dump_cmd`, via
  `_resolve_debug_artifact`), while `service._dump_elf` (which
  `_resolve_side_snapshot_impl` reaches through `service.resolve_input`)
  has no such parameter at all and independently *re-derives* the
  identical fact from raw `debug_roots`/`enable_debuginfod`/
  `debuginfod_url`. Merging the two needs the two debug-artifact
  resolutions confirmed equivalent first — a real, separate investigation,
  not a follow-up edit. A parallel check of `scan_engine._build_new_
  snapshot` (which already calls `service.resolve_input` directly, so it
  doesn't share this particular ELF-pipeline divergence) found two
  different, narrower blockers instead: it passes `symbols_only`/
  `debug_presence_only` to `resolve_input`, which `_resolve_side_snapshot_
  impl` never threads through yet (a straightforward additive gap); and
  its `embed_build_source` call constructs `public_headers` differently
  from `embed_side_build_source`'s own construction (`_expand_public_
  headers` over the combined headers+dirs list, vs. the shared wrapper's
  separate, unexpanded treatment) — a genuine behavioral difference
  needing reconciliation, not just a naming one. Neither was attempted
  this session, per this file's own "known gaps over risky reactive
  patches" convention, given this exact code area's extensive prior
  history of exactly this shape of subtle divergence (18+ numbered
  findings on the L3→L2-fold entry above). **PR 3A's full convergence
  remains open; PR 3C (the "PR F" removal of `dump --build-query`/
  `dump --build-compile-db`) stays blocked on it**, per the plan doc's own
  explicit ordering requirement — see `docs/contribute/plans/cli-cleanup-
  phase-two.md`'s PR 3A section for the equivalent, fuller account.

  **Slice landed (2026-08-20): both "narrower blockers" above closed, plus
  the debug-artifact-resolution question this entry raised confirmed
  equivalent — no code change needed for that half.** `perform_elf_dump`
  has exactly one caller (`dump_cmd`, confirmed by grep), and `dump_cmd`
  never sets `symbols_only`/`debug_presence_only` (`dump` has no such
  flags at all — only `scan`/`compare` do), so `_dump_elf`'s extra `not
  symbols_only and not debug_presence_only` gate around its debug-artifact
  resolution is vacuously true for every input `perform_elf_dump` can
  actually pass it; the remaining textual differences (`debug_roots` vs.
  `list(debug_roots) or None`, `click.echo` vs. `notify`, `if artifact:`
  vs. `if artifact is not None`) are all behaviorally inert (the resolver
  backends already do `list(debug_roots or [])` internally, and a
  `DebugArtifact` instance is always truthy). `symbols_only`/
  `debug_presence_only` now thread through `resolve_side_snapshot`/
  `_resolve_side_snapshot_impl` into `service.resolve_input`, both
  defaulting `False` so every pre-existing caller is unaffected — the same
  additive shape as the existing `changed_paths`/`allow_build_query`
  pass-throughs (`tests/test_header_compile_context.py::
  test_resolve_side_snapshot_forwards_symbols_only_and_debug_presence_only`,
  confirmed to fail pre-fix with `TypeError: unexpected keyword argument
  'symbols_only'`). The `public_headers` construction divergence:
  **a fix was attempted and merged, then reverted the same day after
  review caught a real regression it missed — kept here in full because
  the first pass's reasoning was genuinely incomplete, not just
  under-tested.** The first pass read one consumer of `embed_build_
  source`'s `public_header_roots` (`source_extractors._argv.
  split_public_roots`/`_ClassifyContext.classify()`) and confirmed a
  directory root already classifies every file under it via segment/
  prefix matching, so the `_expand_public_headers`-based expansion looked
  purely redundant *against that consumer* — and switched
  `_build_new_snapshot`'s call to the simpler, unexpanded raw pass-through
  `embed_side_build_source` already uses. That missed a **second,
  differently-shaped consumer of the same list**:
  `clang_public_roots._equivalent_public_roots_for_unit`, the
  install-tree-vs-build-tree "mirror detection" heuristic L4 replay uses
  when a public root names a physically different tree from the build's
  own include dir. Its promotion rule is asymmetric by root shape: a
  *file* root promotes on a single sampled match; a *directory* root
  needs `>= _PUBLIC_ROOT_WHOLE_DIR_MIN_MATCHES` (2) matches before
  promoting the whole directory — so a build include dir mirroring only
  ONE header out of a larger public root loses that promotion entirely
  once the directory stops being pre-expanded, confirmed by direct
  reproduction against the function itself (three installed headers, one
  mirrored in the build tree: expanded file roots promote it, a single
  directory root promotes nothing). `embed_side_build_source`'s own raw
  pass-through (already shipped, used by `compare`/`dump`) carries the
  identical weakness — not fixed here, since unifying either direction
  changes real classification behavior for a real consumer, and deciding
  which needs its own scoped design, not a same-PR revert-and-redo.
  Reverted `_build_new_snapshot`'s call back to the expanded shape and
  pinned two regression tests: the call's own shape (`tests/
  test_scan_l2_cleanup_ordering.py::
  test_scan_candidate_expands_public_header_dirs_before_embed`) and the
  underlying asymmetry directly against `_equivalent_public_roots_for_unit`
  itself (`tests/test_clang_public_roots_coverage.py::
  test_equivalent_public_roots_promotes_on_single_match_only_for_file_roots`),
  so a future "simplify this like the other primitive" pass doesn't
  silently reintroduce the same regression. **Neither fix routes
  `_build_new_snapshot` through `_resolve_side_snapshot_impl` itself** —
  they make that future migration safe, they don't perform it.
  Investigating the migration surfaced one more wrinkle this entry hadn't
  named: `_build_new_snapshot`'s own `allow_build_query` gates only its
  `embed_build_source` call, never its `seed_includes_and_fold_compile_
  context` call (which always passes `build_query=None, build_compile_
  db=None` — `scan` has no such CLI flags to begin with), whereas
  `_resolve_side_snapshot_impl`'s `_gated_build_query_inputs` gates both
  from one shared decision; reconciling that needs confirming what
  `_build_new_snapshot`'s `allow_build_query` is actually meant to
  authorize today, before the two functions' gating can be safely
  unified. Blockers 4 (post-processing hooks) and 5/6 (`dump_cmd` building
  a real `DumpRequest`; a pair-aware scan-baseline primitive) remain fully
  open, unchanged from the notes above — see the plan doc's own PR 3A
  section for the identical, fuller account.

  **Slice landed (2026-08-21): the ADR-039 collector gate is now one shared
  function all three resolvers call, which closed a real dump-vs-scan
  asymmetry nobody had named — and attempting blocker 5 next turned up three
  concrete obstacles the notes above do not mention.** Re-reading the three
  resolvers side by side found that `scan_engine._build_new_snapshot` never
  ran the ADR-039 build-context collector **at all** (the ELF `dump` CLI
  always had; the typed pipeline gained it in PR #809), so `scan --against`
  a `dump`-produced baseline compared a candidate with no
  `build_context_defines`/`conditional_fields` against a baseline carrying
  both — and the reconciler could clear a context-free header-parse false
  positive (a `#ifdef`-guarded record field the context-free parse pruned)
  on the baseline side but not on the candidate's. Fixed at the gate rather
  than by writing a fourth copy of it:
  `header_conditionals.attach_build_context_for_parsed_headers` now owns the
  compile-DB resolution (from an already-resolved path *or* from
  `build_info`), the best-effort header expansion a directory `-H` entry
  needs, the `snap.from_headers` check, and the caller-supplied
  `live_elf_parse` answer; `perform_elf_dump`,
  `_resolve_side_snapshot_impl`, and `_build_new_snapshot` all call it.
  `perform_elf_dump` gains `from_headers` in the process — the same gate its
  own sibling `parsed_with_build_context` stamp ten lines above already
  applies, for the recorded reason that a `--dwarf-only` run explicitly
  ignores `-H`. A latent ordering bug was fixed alongside:
  `_resolve_side_snapshot_impl` drained the L2 seed's cleanups only *after*
  `embed_side_build_source`, so an inferred build query's lock was still
  held when the embed ran its own — the self-contention this entry's own
  fifth finding records for the CLI resolvers. Unreachable today (the seed's
  `collect_mode` is pinned `"off"`), fixed now because it springs on
  whichever PR relaxes that pin, which is what migrating the CLI resolvers
  means. Tests: `tests/test_scan_adr039_build_context.py` (7 cases; the
  three positive ones confirmed to fail against the pre-fix
  `scan_engine.py`) and `tests/test_typed_dump_request.py::
  TestSeedCleanupsDrainBeforeTheEmbedStep` (confirmed to fail pre-fix).

  **Blocker 5's three obstacles, each verified rather than assumed** (full
  account in the plan doc's PR 3A section): (a) `InputSpec.path` is a
  *required* field and `dump`'s source-only branch (`dump --sources ./tree`
  with no SO_PATH) has no path, while `--dry-run` runs before that dispatch
  — so "both branches build from one `DumpRequest`" is unreachable for that
  shape until `InputSpec` can express "no binary", a public typed-API model
  change reaching every consumer. (b) The two collect-mode resolvers
  genuinely disagree, measured directly: they agree for *every* explicit
  `--depth`, and the no-inputs case is unobservable, but **`--build-info`
  with no `--depth` is `source-target` on the CLI
  (`resolve_dump_collect_context`) and `build` through the typed path
  (`collect_mode_for`)** — so taking the collect mode from
  `resolve_dump_request` would silently stop a `dump --build-info <pack>`
  at L3 that attempts L4 today. Which default is right is a product
  decision, not a mechanical reconciliation. (c) The ELF `dump` CLI embeds
  L3–L5 at *write* time (`cli_buildsource._write_snapshot_output`, together
  with the G21.7 warning, the Flow-2 `--inputs` fold, the depth gate and
  the provenance fold) while `execute_dump_request` embeds at *resolve*
  time inside `_resolve_side_snapshot_impl` — routing the real run through
  the typed executor **embeds twice**, re-running L4 replay, unless the
  write path is restructured in the same change. Note what (b) implies for
  a "just migrate the dry-run first" shortcut: it would report a collect
  mode the real run does not use, which is worse than today's two
  hand-synced implementations.

  **One more verified divergence, not fixed:** `scan`'s
  `embed_build_source` call passes no `extractor`, taking that function's
  `"auto"` default, and `buildsource.inline._make_source_extractor` treats
  anything but a literal `"castxml"` as clang — while every other resolver
  passes `service_compare_evidence.effective_frontend(...)`, which resolves
  `"auto"` to **castxml** (`dumper._resolve_header_backend`, no availability
  fallback). So `dump --depth source` and `scan --depth source` over one
  project at their defaults replay L4 through *different extractors*, and a
  `scan --against` a `dump` baseline compares source-ABI facts from two
  different tools — precisely what `effective_frontend`'s own docstring says
  it exists to prevent. Making `scan` match would newly require castxml for
  a scan that works with clang today: a real behavior change for real users,
  unverifiable without a castxml-capable environment, so it needs its own
  slice rather than a same-pass patch. **Re-checked 2026-08-21: castxml is
  still absent from this environment, so this stays a documented gap rather
  than a guessed fix.**

  **Blockers 5 and 6 closed (2026-08-21, later the same day) — `dump_cmd`
  now builds one real `DumpRequest`, and the pair-aware baseline rule lives
  in one primitive. The real runs are deliberately still not migrated.**

  *Blocker 5* was three sub-issues, each closed at the layer that had the
  gap rather than at the call site that noticed it. (a) `InputSpec.path` is
  now `Path | None` — a pure widening, so no existing caller changes — with
  "which requests may leave it `None`" enforced once, per request type, in
  `validation_errors()` (never for `CompareRequest`; for `DumpRequest` only
  alongside real `sources`/`build_info`/`dump_manifest`), and
  `api_types.required_path` as the single place the narrowing is spelled
  rather than seven defensive call sites. The `dump_manifest` clause is
  worth recording because it was found the right way round: a first revision
  named only `sources`/`build_info` and broke `dump --dump-manifest
  m.yaml --dry-run` (no SO_PATH), caught by the *existing*
  `tests/test_cli_dump_manifest.py` — which is precisely the "the model
  cannot express what the CLI accepts" gap the widening exists to close.
  (b) The CLI-vs-typed collect-mode disagreement (`--build-info` with no
  `--depth`: `source-target` on the CLI, `build` through the typed path)
  is resolved in favour of the CLI's older, documented default, via a new
  `service_compare_evidence.dump_collect_mode_for`. `collect_mode_for` is
  **unchanged** — `compare`'s own front end genuinely infers omitted depth
  from its inputs, which is a different question, and changing it would have
  been the easy wrong fix. Pinned by
  `tests/test_dump_collect_mode_parity.py` against the *real* CLI resolver
  over the whole `(depth, sources, build_info)` grid. (c) The write-time
  embed is now idempotent: `cli_buildsource.build_source_already_satisfies`,
  stated through the same `_missing_requested_evidence_layers` the
  neighbouring G21.7 warning already trusts, so the guard and the warning
  cannot disagree about what "satisfied" means; its `pack is None -> []`
  case is deliberately *not* satisfaction, which is what keeps it a no-op
  for today's CLI (`tests/test_dump_embed_idempotence.py`, including an
  `integration` end-to-end count proving one real `dump --depth source`
  embeds exactly once).

  With those closed, `abicheck/cli_dump_request.py` builds one `DumpRequest`
  from `dump_cmd`'s parameters and `--dry-run` renders from a real
  `ResolvedDumpRequest`. **The half-migration hazard the plan names — "a
  preview built from one resolver describing an execution built from another
  is worse than two hand-synced implementations, since it looks
  authoritative without being connected to what actually runs" — is answered
  structurally, not asserted.** The request is fed the CLI's
  *already-resolved* values (compile context, frontend, explicit-language
  decision) rather than re-deriving them, so it records the run; and the
  fields the pipeline *does* derive independently are pinned equal to the
  CLI's own by `tests/test_dump_request_from_cli.py::
  TestResolvedRequestAgreesWithTheCliLocals`. Sub-issue (b) was a
  prerequisite for exactly that: without it the preview would have reported
  a collect mode the real run does not use. One user-visible consequence,
  stated rather than left to be discovered: `DumpRequest.validate()`
  front-runs `dumper.dump()`'s own runtime rejection of `--dump-manifest`
  combined with `-I`, so that combination is now a usage error in the dry
  run too — inside the dry-run contract, which permits usage errors.

  *Blocker 6* is `service_input_resolution.BaselineReuseContext` /
  `resolve_baseline_compile_context`: the "may the candidate's folded
  context also parse the baseline" rule, extracted from the four-clause
  boolean inline in `scan_engine.run_scan_core` that the twelfth, thirteenth
  and fifteenth findings above each had to correct in turn. `run_scan_core`
  calls it today; `_resolve_side_snapshot_impl` accepts the same object as
  an **optional** `baseline_reuse_hint` and reports the identical answer on
  `SideResolution.baseline_compile_context`, so the migration that finally
  routes `_build_new_snapshot` through the shared resolver inherits the rule
  instead of reimplementing it a fourth time. Deliberately an opt-in hint,
  not a widening of `resolve_side_snapshot`'s single-input contract — a
  caller that passes none is bit-for-bit unaffected. Given that correction
  history, it is tested as a primitive rather than only through `scan`
  (`tests/test_baseline_reuse_context.py`), per this file's own
  "Primitive-level property tests" guidance: the contract as invariants,
  the resolver-agrees-with-its-own-predicate property, and a pin that
  include *order* matters (search order is first-match-wins, so a "compare
  as sets" simplification has to argue with a test rather than pass
  silently).

  **Still open, unchanged:** neither real run routes through the shared
  pipeline. `dump` executes through `perform_elf_dump`/
  `handle_non_elf_dump` and `scan`'s candidate through `service.
  resolve_input`/`embed_build_source` directly. What blocks each is now
  concrete rather than open-ended — the ADR-039 collector's CLI-only inputs
  (`--compile-db-filter`, the raw `effective_compile_db`) need typed-API
  representation, and `_write_snapshot_output`'s provenance/`--inputs`/
  depth-gate sequence needs reordering around a resolve-time embed — but
  each is its own slice. PR 3C (removing `dump --build-query`/
  `--build-compile-db`) therefore stays blocked, per the plan's own ordering
  rule: moving those inputs into config while two resolvers still interpret
  that config independently is the exact failure the three-way split exists
  to prevent.

  **Both real-run migrations attempted and stopped (2026-08-21, later
  session) — and the reason is now measured rather than reasoned about,
  which changes what "still open" means here.** The paragraph above says the
  two resolvers are structurally separate. Comparing the written `dump` CLI
  snapshot against `execute_dump_request`'s, field by field, over a real
  `g++` build and a real clang L2 parse, shows they *already produce
  non-comparable snapshots* for the same library from the same evidence:
  everything agrees except the extraction contract, where the CLI records
  `macro_ops` as `[["D","FOO=1"],["D","FOO=1"]]` against the typed path's
  one entry, and `include_sequence` as `[]` against the typed path's one
  slot. `scope_fingerprint` agrees; `profile_fingerprint` therefore differs
  in exactly those two shapes. Both trace to one mechanism — the
  `dump` CLI runs the legacy `-p`/`--compile-db` auto-match
  (`cli_helpers_compare._resolve_build_context_flags`, merged into
  `effective_gcc_options`) *in addition to* the P0.3 L3→L2 fold whenever
  both are fed by the same `--build-info` compile database. The duplicate
  `-D` is that overlap recorded twice; the empty `include_sequence` is the
  legacy match supplying `-I<dep>` as explicit context *before* the L2 seed
  runs, so `seed_l2_includes` correctly declines to seed it and the
  directory reaches the parse through `gcc_option_tokens`, which contributes
  no `declared_includes` slot — the sole source `include_sequence`
  tokenizes. This is the `dump`-vs-typed-API half of the same "third,
  deeper mechanism" the L3→L2-fold entry above already records for
  `dump`-vs-`scan`.

  **Why that stops the migration rather than motivating it.** Routing the
  real run through `execute_dump_request` drops the legacy match, so the
  migration does not merely need the two prerequisites named above — it
  *forces* the design decision the L3→L2-fold entry says is open. Dropping
  the legacy match is arguably right (the fold is strictly richer:
  per-header matching, ambiguity checking, include paths, forced includes),
  but it makes `dump --compile-db-filter` inert, and `InputSpec`
  deliberately carries no `compile_db_filter` field — one was added and
  removed in the same review round for having no successful execution path
  (see that field's replacement comment in `api_types.py`). Making a
  documented flag silently inert is worse than the gap. The ordering is
  therefore three slices, not one: thread `--compile-db-filter` into the
  shared fold (`buildsource/l2_seed.py`/`header_compile_context.py`), decide
  and ship the legacy-match removal, then migrate the real run. **The first
  of those three landed in the same session** — and it turned out to be a
  user-facing bug in its own right, not merely migration plumbing:
  `resolve_header_compile_context`'s fail-closed ambiguity message names
  `--compile-db-filter` as a way to narrow the input, but the filter reached
  only the legacy match, so a user who followed that advice got the identical
  error back. Reproduced end to end (`dump --depth headers -H api.h
  --build-info db.json --compile-db-filter a.cpp` over two TUs disagreeing on
  an ABI-relevant `-D`) and fixed by threading `source_filter` through
  `resolve_header_compile_context`/`l2_seed`/`perform_elf_dump`, with the
  matching rules consolidated into one shared
  `build_context.source_matches_filter` so the fold, the legacy match and the
  ADR-039 collector cannot select different translation units for the same
  filter. A filter matching nothing keeps every unit — the conservative
  fallback the other two layers already applied. Tests:
  `tests/test_compile_db_filter_scope.py` (the primitive's contract as
  invariants, the three layers agreeing, the resolver, and a real
  `g++`+clang `dump` proving the guarded field is parsed in or out according
  to which TU the filter names; the end-to-end cases confirmed to fail
  pre-fix). Still open in that first slice: the *typed* half —
  `InputSpec.compile_db_filter` plus the CLI's own
  L2-filtered/L3-unfiltered refusal mirrored into
  `resolve_dump_request`, which is where the resolved collect mode is known
  (see that field's replacement comment in `api_types.py`). One
  environmental fact independently rules out attempting the *migration
  itself* in that session, whatever order the three slices land in: the
  *default* header backend is castxml, and no working castxml was
  obtainable — a hand-assembled conda-forge 0.7.0 build segfaults inside
  `clang::ParseAST` on any input — so every measurement above is
  clang-backend only, and migrating the real `dump` run while able to
  exercise only the non-default backend is not a verified change.

  **The `scan` side is four items, not the two named above, and three of
  them are behaviour changes rather than missing plumbing** (read against
  `_resolve_side_snapshot_impl` line by line): (1) the L4 extractor default,
  unchanged and still castxml-blocked; (2) the `public_headers` expansion
  shape, where the shared wrapper's raw pass-through is the one already
  reverted for regressing `clang_public_roots._equivalent_public_roots_for_
  unit`; (3) the seed's collect mode — `_seeded_includes_and_compile_
  context` pins `collect_mode="off"` so a Tier-2 primitive never executes a
  build system as a side effect, while `_build_new_snapshot` passes scan's
  real one, so routing through the shared primitive silently removes scan's
  ability to run the zero-config inferred build query in its seed; and (4)
  `defer_cleanup`, which `embed_side_build_source` has no parameter for —
  the only purely additive item of the four. Each of (1)–(3) could become an
  opt-in parameter the way `symbols_only`/`allow_build_query`/
  `changed_paths` already are, which is what a future slice should do;
  reproducing a dozen parameter behaviours exactly on the hot path of every
  `scan`, with the integration lane only partly executable, is the rewrite
  shape this area's review history keeps punishing.

  **What landed instead:** `tests/test_dump_cli_typed_api_parity.py::
  test_dump_cli_and_typed_api_agree_on_extraction_contract`. Its sibling
  compares `ast_compile_args` through `split_compile_args`'
  semantics-preserving normalization, which is the right lens for "did both
  paths reach the same compile" and structurally blind to both divergences
  above — `profile_fingerprint` hashes the recorded fields *as recorded*, so
  a difference normalization hides is still a comparability failure. The two
  known-divergent (shape, field) pairs are encoded the same conditional-xfail
  way `_SCAN_KNOWN_DIVERGENT_SHAPES` already is: the exact diagnosed
  signature reproduces, or the test fails outright, so "the gap closed"
  fails as loudly as "a new field diverged" and the mapping cannot go stale
  silently. Verified in both directions.

  **The two divergences that test recorded are closed, `scan`'s candidate
  resolver is migrated, and the measurement itself turned up a second, larger
  bug (2026-08-21, later session). The `dump` real run is still not migrated —
  the reason is now two items, not open-ended.**

  *The legacy-match overlap.* The design decision the entry above left open is
  made: **when the P0.3 fold resolves a compile context for the headers being
  parsed, it is the sole source of compile-database-derived context**, and the
  legacy `-p`/`--compile-db` auto-match's own derived flags are unfolded rather
  than stacked on top of it. When the fold does not apply (no `--build-info`,
  or a header no compile unit matches) the legacy match still runs and still
  applies — only the overlap is dropped. The worry that ranked this second —
  that dropping the legacy match makes `--compile-db-filter` inert — no longer
  holds: the filter reaches the shared fold too, since the preceding slice
  threaded `source_filter` through `seed_includes_and_fold_compile_context`/
  `resolve_header_compile_context`. Where the conditional goes is the load-
  bearing part: `dump_cmd` merges the legacy flags into `effective_gcc_options`
  *before* calling `perform_elf_dump`, so that function now takes them
  separately (`legacy_build_context_flags`) and hands the fold the caller's
  *own* `--gcc-options` string as its explicit context. Presenting the legacy
  result to the fold as though it were an explicit user choice is precisely
  what recorded the same `-D` twice and routed a derived `-I` through
  `gcc_option_tokens` instead of `declared_includes`. User-visible result, not
  only a fingerprint tidy-up: `scan --against` a real `dump` baseline for the
  extra-`-I` shape goes from **exit 6, `NOT_COMPARABLE ... differing fields:
  include_sequence`** to exit 0 for an unchanged library.

  *`scan`'s candidate resolver.* `scan_engine._build_new_snapshot` now builds
  an `InputSpec`/`SideEvidence` and calls `_resolve_side_snapshot_impl`,
  returning its `SideResolution`; `run_scan_core` hands the
  `BaselineReuseContext` in at resolve time and reads
  `SideResolution.baseline_compile_context` rather than recomputing it. The L2
  seed, the `parsed_with_build_context` stamp, the ADR-039 collector gate, the
  drain-before-embed ordering and the pair-aware baseline rule are inherited
  from one implementation instead of written twice — each of which had already
  needed its own separate correction on this path (findings 8/12/13/15 above,
  and the round where `scan` turned out never to run the ADR-039 collector at
  all). The four documented divergences are preserved as **opt-in parameters**
  on the shared primitive, which is what the plan said a future slice should
  do: `seed_collect_mode`, `seed_lang_explicit`, `defer_cleanup`,
  `source_extractor`, `expand_public_header_roots`, `source_frontend_compile`.
  The L4 extractor default therefore stays a documented gap — matching the
  other resolvers would newly require castxml for a `scan --depth source` that
  works with clang today, and castxml is still absent here. Equivalence was
  measured, not argued: candidate snapshot, effective includes, effective
  compile context and deferred-cleanup count, over three real build shapes ×
  three collect modes, identical before and after apart from wall-clock
  timestamps and the build-source pack's own content hash.
  `test_scan_engine_calls_the_shared_resolver` was a source-text match on
  `run_scan_core`; it is replaced by two behavioural pins through a real
  `scan --against`.

  *The second bug, found by the verification bar rather than by a report.*
  Extending the parity measurement from the extraction contract to the *whole*
  snapshot showed the two paths disagreeing on the L3–L5 payload, and not
  cosmetically: the `dump` CLI recorded `0/2 symbols matched`,
  `reachable_declarations=0`, `fact_family_states: empty-confirmed` where the
  typed path recorded `1/2` matched and a real `source_decl_to_binary_symbol`
  mapping. `cli_buildsource._write_snapshot_output`'s own `embed_build_source`
  call passed **no** `public_headers`/`public_header_dirs`, so L4 replay ran
  with an empty `public_header_roots` set — every declaration classifies
  private and nothing links. Nothing fails: the layer is present and the
  coverage row honestly says "partial", so every L4-derived source-ABI finding
  was simply inert for a `dump`-produced baseline. Fixed on both the ELF and
  PE/Mach-O paths. With it, the `dump` CLI's written snapshot and
  `execute_dump_request`'s agree on every field except wall-clock timings and
  the CLI's own provenance layer (`git_commit`, `version`).

  **What still blocks the `dump` real-run migration — two items, both real.**
  Blocker 4 (post-processing hooks) is closed on measurement, not just on
  reading: `service.run_dump`'s ELF branch already runs every pass
  `perform_elf_dump` does (SYCL, `python_ext`, `python_api`, `numpy_capi`, the
  G31 header graph, the G28 clang-layout attach), the ADR-039 collector runs
  inside `_resolve_side_snapshot_impl`, and the whole-snapshot comparison shows
  no difference in any field those produce. What remains: (1)
  **`--compile-db-filter` would go inert** — `InputSpec` deliberately carries
  no `compile_db_filter`, so the shared path cannot narrow the fold or the
  ADR-039 collector the way the native CLI does, and making a documented flag
  silently do nothing is worse than the gap; the step is specified (add the
  field, thread it into `_seeded_includes_and_compile_context` and
  `attach_build_context_for_parsed_headers`, mirror the CLI's
  L2-filtered/L3-unfiltered refusal into `resolve_dump_request`) but is its own
  slice. (2) **The default backend is still unexercisable here** —
  `--ast-frontend` defaults to castxml, none is available (re-checked), so
  every measurement above is clang-only, and migrating the real `dump` run
  while able to exercise only the non-default backend is not a verified change.
  PR 3C stays blocked, per the plan's own ordering rule.

  **Item (1) closed (2026-08-21, later session): `InputSpec.compile_db_filter`
  now exists, exactly as specified above — nothing more, nothing less.**
  `service_input_resolution._seeded_includes_and_compile_context` forwards it
  as `source_filter` to `seed_includes_and_fold_compile_context`; the
  `attach_build_context_for_parsed_headers` call two paragraphs down does the
  same, so the fold and the ADR-039 collector agree on which translation
  units the filter selects (the identical invariant
  `build_context.source_matches_filter` already established for the three
  CLI-side layers — see the root AGENTS.md's forced-include entry's
  MSVC-driver-vocabulary lesson on why a second copy of a shared matching
  rule is the wrong move). `resolve_dump_request` mirrors the CLI's own
  `compile_db_filter_scope_error` refusal, computed from `evidence.
  collect_mode`/`evidence.headers` — the same resolved values the CLI reads
  `compile_db_from_build_info` back against — so a typed caller cannot reach
  the L2-filtered/L3-unfiltered snapshot shape the CLI refuses outright; the
  refusal raises `ValidationError` (translated to `click.UsageError` at the
  CLI boundary by `resolve_dump_request_for_cli`, unchanged). `dump_cmd`
  forwards its own `--compile-db-filter` local into `build_dump_request`, so
  `--dry-run`'s resolved object now records the same filter the real run
  applies, closing the last gap in that request's own honesty contract for
  this one field. Verified against the identical real `g++`+clang project
  `TestDumpCliHonorsTheFilterInTheFold` already uses (two TUs disagreeing on
  an ABI-relevant `-D` behind one `#ifdef`-guarded field), driven through the
  typed `DumpRequest`/`resolve_dump_request`/`execute_dump_request` path
  directly rather than the CLI: the scope-error refusal fires under the same
  condition the CLI refuses under, a request with no filter is unaffected,
  and the filter selects the same translation unit's context for the header
  parse the CLI test already pins (`tests/test_compile_db_filter_scope.py`'s
  `TestTypedApiHonorsTheFilterInTheFold`). Confirmed the CLI's own behavior
  is unchanged by re-running `TestDumpCliHonorsTheFilterInTheFold` directly.
  **Item (2) is unchanged and remains the sole blocker**: castxml is still
  unavailable in every environment this work has been done in, so the real
  `dump` CLI execution path (`perform_elf_dump`/`handle_non_elf_dump`) still
  does not route through `execute_dump_request`, and PR 3C stays blocked.
  This slice narrows what item (2) alone is blocking, nothing more — it does
  not migrate the real run, and does not claim to.

  **Two Codex review findings on the same slice, both real, both fixed
  before merge.** (P2) `InputSpec.of()` — the documented loose-value
  convenience factory every front end other than a direct dataclass
  construction uses — never gained a `compile_db_filter` parameter, so
  `InputSpec.of(..., compile_db_filter=...)` raised `TypeError` for an
  unrecognized keyword: the field was reachable only by constructing
  `InputSpec` directly, despite being advertised as public typed-API
  surface. Fixed by adding the parameter and forwarding it through
  unchanged. (P1) The scope-error guard above was wired into
  `resolve_dump_request` only — but `InputSpec.compile_db_filter` is shared
  by `CompareRequest.old`/`.new` too, and `resolve_compare_request` reaches
  the identical `resolve_side_snapshot` primitive (the P0.3 fold narrows,
  `embed_side_build_source` still collects L3 unfiltered), so a typed
  `CompareRequest` side could reach the exact L2-filtered/L3-unfiltered
  snapshot shape the guard exists to reject, with no check catching it. Fixed
  by extracting the guard into a shared function,
  `service_compare_evidence.reject_compile_db_filter_scope_mismatch` (mirrors
  `reject_debug_format_for_binaries`'s existing `(label, ...)` per-side
  shape), called from both `resolve_dump_request` (`input`) and
  `resolve_compare_request` (`old`/`new`) — one guard, not two independently
  drifting copies. Regression coverage: `tests/test_compile_db_filter_scope.py`'s
  `test_input_spec_of_forwards_compile_db_filter` and
  `TestCompareRequestAppliesTheSameScopeGuard` (the latter, like its
  `DumpRequest` sibling, verified against the identical real `g++`+clang
  project), plus a re-run of every pre-existing test in this area to confirm
  the extraction changed no behavior.

  **Three further Codex review findings on the same slice, each real, each
  fixed, and each a pre-existing gap in the native CLI's own scope check
  too (not introduced by this typed-API slice).** (P1) The guard's compile-
  database resolution considered only an explicit `--build-info`/
  `build_info` — a `--sources`/`sources` tree with no `build_info` at all
  can still have its `compile_commands.json` auto-discovered
  (`buildsource.inline._autodiscover_compile_db`, the identical P4 strategy
  the fold and the L3 embed both already use to find one from `sources`
  alone), so that combination reached the same filtered-L2/unfiltered-L3
  mismatch uncaught. Reproduced directly: a real two-TU project resolved
  with `sources` only, filtered to one TU at L2, still embedded both TUs'
  compile units as L3 evidence (`BuildEvidence.compile_units` length 2).
  (P1) Even with an explicit `--build-info <dir>` given, the resolution
  checked only `<dir>/compile_commands.json` directly — not a conventional
  out-of-tree build subdirectory (`<dir>/build/compile_commands.json`) the
  real fold's own `--build-info` resolution
  (`buildsource.inline._compile_db_at`, delegating to
  `_find_compile_db_in_dir` for a directory) already searches, explicitly
  documented as matching `--sources` auto-discovery's own contract.
  Reproduced directly: the identical project with its database moved into
  a `build/` subdirectory, `--build-info` pointed at the project root — the
  fold correctly resolved and filtered by the nested database while the
  guard never fired. Both fixed in one place,
  `header_conditionals.compile_db_for_filter_scope_check` (deliberately
  **not** folded into `compile_db_from_build_info` itself, which also
  drives the CLI's unrelated legacy `-p` auto-match and must stay
  `--build-info`-direct-child-only — see that function's own docstring),
  consumed by both `cli.py`'s `dump_cmd` and the shared typed guard.
  Regression coverage: `TestScopeGuardCoversSourcesOnlyAutoDiscovery` and
  `TestScopeGuardCoversNestedBuildInfoDatabases` in
  `tests/test_compile_db_filter_scope.py` (three entry points each — CLI,
  `DumpRequest`, `CompareRequest` — plus a positive control for the nested
  case confirming the database is genuinely what gets filtered).

  **A fifth finding, investigated and deliberately left as a documented gap
  rather than fixed reactively — the point at which these findings stopped
  converging on real, reachable bugs.** `execute_dump_request()`/
  `_resolve_side_snapshot_impl()` also accept a keyword-only
  `build_compile_db` (a glob, mirroring `--build-compile-db`), forwarded
  unfiltered to both the L2 fold and the L3 embed the same way
  `build_info`/`sources` are — in principle the identical mismatch class.
  But `build_compile_db` is not a field of `DumpRequest`/`InputSpec` at
  all: it exists purely as scaffolding for the not-yet-landed PR 3A
  real-run migration (this module's own docstring: "the real ELF/PE/
  Mach-O run still executes through `perform_elf_dump`/
  `handle_non_elf_dump`, not through `execute_dump_request`"), and
  `execute_dump_request` has exactly one caller in the whole codebase —
  `run_dump_request`, which never passes it. No CLI, no typed-API path, and
  no test can reach this combination without bypassing the entire
  `DumpRequest`-shaped public surface and hand-calling the semi-internal
  `execute_dump_request` with a kwarg nothing in that surface can set —
  a different reachability class from the four findings above, each
  reproduced end-to-end through real, ordinary usage before being fixed.
  Left for whichever change gives `build_compile_db` its first real caller
  (i.e. the PR 3A real-run migration itself) to close alongside that
  migration, rather than shipping validation code with no real path to
  verify it against.

  **A sixth finding (Codex review, fresh evidence) reopened convergence:
  the guard's original `compile_db_from_build_info`-only check covered a
  literal compile database and nothing else, but the fold it guards
  (`resolve_header_compile_context`/`filter_units_by_source`) narrows
  *whatever* `BuildEvidence.compile_units` a `--build-info` resolves to,
  regardless of shape.** A `--build-info` naming a pre-captured `collect`
  pack directory (`is_pack_dir`) or a Bazel `aquery`/`cquery` jsonproto
  resolves compile units the identical way a literal compile database does
  — both are routed through their own adapters
  (`buildsource.inline._maybe_collect_bazel_build_info`/pack loading), not
  `load_compile_db()` — and the L3 embed collects that same `BuildEvidence`
  unfiltered either way, so the mismatch reproduced for both shapes with no
  error, purely because neither is a `compile_commands.json` file
  `compile_db_from_build_info` recognizes. Fixed: `compile_db_for_filter_
  scope_check` now also recognizes a `--build-info` that `sniff_build_info_
  format` (the same cheap, execution-free classifier `compile_db_from_
  build_info` already uses, so the two cannot disagree) reports as `"pack"`
  or `"bazel_aquery"`/`"bazel_cquery"`, returning the `--build-info` path
  itself as the guard's non-`None` signal (the guard only ever checks
  `is None`, so this needs no literal compile-database content).
  `compile_db_filter_scope_error`'s docstring, which had explicitly claimed
  the opposite ("a pack or Bazel jsonproto routes through a different
  adapter" → `None`, i.e. by design out of scope), was corrected alongside
  the fix — that claim was the bug's own design rationale, not a separate
  error. **Still not covered, and not attempted here**: a `--sources` tree
  with no discoverable `compile_commands.json` at all, resolved instead
  through the zero-config *inferred* build-system query (cmake/make/bazel).
  Unlike the pack/Bazel-jsonproto case, telling whether that combination
  would actually resolve multiple compile units means running the build
  system's own query — the exact side effect this cheap, read-only scope
  check exists to avoid paying twice per invocation (once to check, once for
  real) — so this residual is documented rather than guessed at, per this
  file's own "known gaps over risky reactive patches" convention. Regression
  coverage: `TestScopeGuardCoversPackAndBazelBuildInfo` in
  `tests/test_compile_db_filter_scope.py` (the predicate directly — pack
  directory, both Bazel jsonproto shapes, both positive and negative
  controls, plus a plain non-pack directory and a non-Bazel JSON-object file
  confirmed to still resolve `None`; five of nine cases confirmed to fail
  against the pre-fix guard).

  **A seventh finding (Codex review, fresh evidence) on the same guard:
  the sixth finding's fix only recognized a pack named by `--build-info`,
  but `buildsource.l2_seed._l2_seed_pack_inputs` folds a `--sources` pack
  (a classic `BuildSourcePack` or a Flow-2 `abicheck_inputs/` directory)
  into L2 seeding the identical way — carrying its own normalized
  `BuildEvidence` in — whenever no `--build-info` was given at all (an
  explicit `--build-info` always wins L3, matching that function's own
  `if build_info is None:` gate on the assignment).** A `--sources` naming
  such a pack, with no `--build-info`, reproduced the identical mismatch:
  the guard's fallback resolution (`compile_db_from_build_info` then
  `_autodiscover_compile_db`) only ever looks for a literal
  `compile_commands.json` inside *sources*, which a pack directory does not
  carry at its root — so it silently resolved `None` and let the mismatch
  through. Fixed by recognizing a `sources` pack the identical way
  `_l2_seed_pack_inputs` does (`is_pack_dir` / `inputs_pack.is_inputs_pack`),
  gated on `build_info is None` to match that function's own precedence
  exactly — an explicit `--build-info` (even one that itself resolves to
  nothing recognizable) still means the sources pack's evidence is never
  folded into `base_build`, so the guard must not treat it as filterable
  evidence in that combination either (pinned by its own regression test).
  Regression coverage: `TestScopeGuardCoversSourcesPacks` in
  `tests/test_compile_db_filter_scope.py` (a classic pack and a Flow-2
  inputs pack named by `sources`, the scope error firing, the
  `build_info`-takes-precedence control, a no-filter control, and a plain
  non-pack `sources` directory still falling through to ordinary
  auto-discovery; three of six cases confirmed to fail against the pre-fix
  guard).

  **An eighth finding (Codex review, fresh evidence) — not a missing case
  this time, but a genuine false positive the seventh finding's fix
  introduced.** That fix restructured the function so every branch fell
  through unconditionally to the `sources`-based checks once none of the
  `build_info` branches matched — including the case where `build_info`
  was genuinely given but resolved to nothing recognizable. That is wrong:
  `buildsource.inline._resolve_compile_db` — the real function every one
  of these seeded resolvers (`collect_inline_pack`, in turn called by
  `seed_includes_and_fold_compile_context`/`embed_build_source` alike)
  ultimately calls — tracks `explicit_input_missed` and returns `None` as
  soon as a *given* `--build-info` misses, deliberately, per its own
  comment: "surface that miss rather than masking it with a stale
  auto-discovered DB ... checked BEFORE auto-discovery." So an explicit
  `--build-info` that doesn't resolve means neither the real L2 fold nor
  the L3 embed ever falls back to a `sources`-discovered database — falling
  back in the guard (the post-seventh-finding behavior) produced a false
  positive: rejecting a `--compile-db-filter` combination the real
  resolvers wouldn't actually apply to either side of, a usage error for a
  perfectly safe invocation. Fixed by returning `None` immediately once the
  `build_info is not None` branch exhausts its own checks, before ever
  reaching the `sources`-based fallbacks — matching `_resolve_compile_db`'s
  own precedence exactly. Regression
  coverage: `TestScopeGuardDoesNotFallBackToSourcesWhenBuildInfoMisses` in
  `tests/test_compile_db_filter_scope.py` — a pure-predicate case (an
  unresolvable `build_info` alongside a `sources` tree carrying a real,
  auto-discoverable `compile_commands.json`, confirmed to fail against the
  post-seventh-finding code) plus a positive control against the real
  g++/clang project fixture, confirming the guard doesn't reject a genuinely
  safe combination.

  **A ninth finding (Codex review, fresh evidence): the third
  under-coverage's own pack recognition for `--build-info` (the sixth
  finding above) only ever checked `is_pack_dir` — a classic
  `BuildSourcePack` — never `inputs_pack.is_inputs_pack`, the Flow-2
  `abicheck_inputs/` shape.** `_l2_seed_pack_inputs` recognizes both shapes
  for `build_info` identically (`is_pack_dir(build_info) or
  _is_inputs_pack_dir(build_info)`), and `embed_build_source`'s own
  `bi_is_inputs` check embeds a Flow-2 `build_info` pack the same way — so a
  `--build-info` naming a Flow-2 pack reproduced the identical mismatch,
  missed only because the sixth finding's fix carried over `is_pack_dir`
  without its Flow-2 sibling, even though the seventh finding's fix
  (`--sources` packs) already checks both. Fixed by adding `or
  is_inputs_pack(build_info)` to the same branch. Regression coverage:
  `TestScopeGuardCoversPackAndBazelBuildInfo::
  test_flow2_inputs_pack_named_by_build_info_is_recognized` in
  `tests/test_compile_db_filter_scope.py`, confirmed to fail against the
  pre-fix guard.

  **A real regression the scan-migration paragraph above introduced, found
  by Codex review and fixed the same session (2026-08-21): `scan --config
  <path>` silently lost the config's own *passive* settings whenever the
  config declared no `build.query` — the common case, not an edge one.**
  `_resolve_side_snapshot_impl`'s shared `build_config`/`build_query` gate
  (`_gated_build_query_inputs`) blanket-nulls `build_config` unless
  `allow_build_query` is exactly `True`, a default sized for `dump`/
  `compare`'s typed API (no CLI-side consent step of its own, so mere
  presence cannot be trusted). Migrating `scan`'s candidate resolution onto
  this same primitive routed it through that gate too — but `scan`'s own
  consent gate, `cli_scan_helpers.resolve_effective_allow_query` (ADR-037
  D4 "level-implies-query"), only ever answers `True` when the config
  *itself* declares an executable `build.query` key AND an explicitly-pinned
  deep evidence level; it was never meant to answer whether the config may
  be *read* at all. `build_config`'s own query field is already, correctly,
  gated downstream regardless of this local gate — `collect_inline_pack`'s
  presence-based `build_config_trusted_for_query`, computed independently by
  both of this gate's callers (`l2_seed._resolve_l2_seed_pack_args`,
  `cli_buildsource.embed_build_source`) since before this migration existed.
  Confirmed against scan's own pre-migration source (commit `c3f6add`):
  `build_config` was always forwarded ungated to both
  `seed_includes_and_fold_compile_context` and `embed_build_source`,
  trusting exactly that downstream gate; `allow_build_query` was a separate,
  already-documented-dead-in-the-`True`-direction parameter that never
  gated `build_config`'s presence at all. Fixed with a new opt-in parameter,
  `build_config_locally_trusted` (threaded through `_gated_build_query_
  inputs`, `_seeded_includes_and_compile_context`, and
  `_resolve_side_snapshot_impl`), defaulting `False` so `dump`/`compare`'s
  typed-API contract is completely unchanged; `scan_engine.
  _build_new_snapshot` passes `True`, restoring its exact pre-migration
  behavior. `build_query` — the bare, always-executable command string, with
  no downstream gate of its own — stays fully gated by `allow_build_query`
  regardless of this flag either way. Regression coverage:
  `tests/test_gated_build_query_inputs.py` (primitive-level tests on the
  gate itself, plus one end-to-end test on `scan_engine._build_new_snapshot`
  proving `build_config` survives even when `allow_build_query` is falsy;
  5 of 8 cases confirmed to fail against the pre-fix gate).

  **A build-source pack's replay *scope* (`"changed"` vs `"target"`) is not
  recorded anywhere, so the write-time idempotence guard cannot distinguish
  them — investigated (CodeRabbit review), not fixed; currently unreachable
  by any real caller, which is why this stayed a documented gap rather than
  a same-session patch.** `_missing_requested_evidence_layers()` maps an
  ADR-033 collect mode to its expected *layer set* (`CI_MODE_TO_LAYERS`) and
  checks only whether each layer's embedded payload is non-empty — it never
  reads `collection_for_ci_mode()`'s other return value, the replay scope.
  `source-changed` (only affected TUs replayed) and `source-target` (the
  full target) map to the *identical* layer set `("L3", "L4", "L5")`, so in
  principle a pack built under `source-changed` — non-empty because *some*
  TUs were affected — could read as satisfying a later `source-target`
  request through `build_source_already_satisfies()`, the write-time
  check-before-embed guard PR 3A blocker 5 sub-issue 3 added (see above).
  Traced why this is not reachable today: that function has exactly one
  caller (`_write_snapshot_output`'s guard), which runs on `snap.build_
  source` *before* any embedding has happened for the current `dump`
  invocation — the only other `snap.build_source = ...` assignment anywhere
  in the codebase is `embed_build_source()`'s own, which this guard exists
  to gate — so `snap.build_source` is always `None` entering the guard for
  the one real caller, and the function is unconditionally a no-op today
  exactly as its own docstring already states. It exists for the *future*
  migration that routes `dump`'s real execution through
  `execute_dump_request` (still blocked, see above); in that migration both
  the resolve-time and write-time embeds would receive the *same* resolved
  `collect_mode` for one invocation, not two different ones, so this
  specific scope mismatch doesn't arise from that path either. The deeper
  gap the finding surfaces is real independent of this predicate, though:
  `BuildSourceManifest` has no field recording replay scope at all —
  `pack.manifest.inputs` (`buildsource/inline.py`) only ever records
  `{"sources", "build_info", "collected"}`, never `"changed"` vs `"target"`.
  A correct fix needs a new manifest field threaded through every
  pack-producing call site (`collect_inline_pack` and its Bazel/compile-DB
  siblings) plus a scope-aware read in `_missing_requested_evidence_
  layers`, with its own regression coverage for a genuine scope-narrowing
  scenario — a real, if currently latent, data-model gap, not a one-line
  fix to this one predicate.

  **A real regression caught post-merge by CI on this same branch
  (2026-08-21), traced to the `fb688cb` dump-side fix above interacting
  with a *pre-existing*, differently-scoped `scan` default — a live
  `tests/test_dump_scan_l3_comparability.py` end-to-end test (added on
  `main` by an unrelated, earlier PR) went from passing to failing the
  moment this branch merged the base back in, `git bisect`-isolated to
  exactly the dump-side write-time-embed fix.** That fix made `dump`'s
  written baseline correctly link its L4 declarations to the binary's
  exported symbols for a project whose only `-H` input is a lone header
  *file* (no directory, no `--public-header-dir`) — but `scan`'s own
  candidate resolution, unchanged by that fix, still derives its L4
  `public_header_roots` from `cli_scan_baseline._public_provenance_set`,
  which *deliberately* returns an empty root set for exactly that shape (a
  lone file cannot establish a public directory boundary — a real,
  separately pinned contract, `test_lone_file_does_not_activate`, unrelated
  to and predating this PR). Before the dump-side fix, both sides degraded
  to zero L4 matches symmetrically, so nothing was ever reported; after it,
  only the dump baseline matched, and the asymmetry itself read as a real
  `source_decl_binary_symbol_mismatch`/`source_to_binary_mapping_changed`
  RISK finding on an *unchanged* library. Confirmed base-red-negative (the
  identical test passes on plain `main`) and confirmed *not* a
  merge-interaction artifact (it already reproduces on this branch's own
  tip before merging `main` back in) before attempting a fix. Considered
  and rejected: widening `_public_provenance_set` itself (would also
  silently flip the L2/crosscheck-origin classification — and its skip/
  present status for `exported_not_public`/`private_header_leak`/etc. —
  every other `scan` invocation of this shape already relies on, a far
  broader behavior change than this fix needs, and it would break that
  helper's own pinned unit test). Fixed narrowly instead:
  `service_input_resolution.embed_side_build_source` gained
  `l4_public_headers`/`l4_public_header_dirs`, an override pair for *that
  one call's* root set, defaulted to the existing `public_headers`/
  `public_header_dirs` for every pre-existing caller (so `compare`/`dump`'s
  typed pipeline are bit-for-bit unaffected). `scan_engine.
  _build_new_snapshot` now computes a second, wider root set via the same
  `split_public_header_inputs` `dump`'s own fix already uses (unioned with
  the narrower, provenance-derived set, not replacing it — an explicit
  `--public-header-dir` must still reach L4 even when it isn't itself
  derivable from the raw `-H` list) and passes it through this new
  parameter — L2/crosscheck-origin classification is completely untouched.
  Regression coverage: a new direct unit test on `_build_new_snapshot`
  itself, `tests/test_scan_l2_cleanup_ordering.py::
  test_scan_candidate_widens_l4_roots_with_a_lone_header_file` (confirmed
  to fail against the pre-fix code), alongside restoring the pre-existing
  end-to-end integration test to green. One sibling, pre-existing test
  (`test_scan_candidate_expands_public_header_dirs_before_embed`) used a
  nonexistent placeholder `-H` path purely as an unrelated fixture detail;
  once `headers` started contributing to the same L4 set, that placeholder
  made `expand_public_header_inputs`'s best-effort expansion degrade to a
  raw pass-through for *everything* (a real, if narrow, generalization of
  that same best-effort-degrades-on-any-missing-path behavior) — fixed by
  emptying that test's own `headers` list, since its actual subject is
  `public_headers`/`public_header_dirs`'s own expansion, not `headers`'s.

  **ADR-063 Phase 1 (`docs/contribute/plans/one-semantic-pipeline.md`,
  "finish the `dump`/`scan` typed-API convergence") re-investigated this
  entry's still-open blocker 2 with castxml genuinely available in the
  investigating environment (a solver-resolved conda-forge install, not the
  hand-assembled 0.7.0 build the plan's Design section found segfaulting) —
  so the environmental precondition for full option (a) convergence no
  longer blocks. **One real, safely-landable slice of blocker 1 closed for
  real** (`cli_dump_helpers.render_dump_dry_run` now takes the real
  `ResolvedDumpRequest` `resolve_dump_request_for_cli` already produces —
  `so_path`/`headers`/`sources`/`build_info`/`depth`/`collect_mode`/
  `header_backend`/`dump_manifest` are all read off it, not re-passed as
  fifteen independently-threaded primitives — verified against
  `test_dump_cli_typed_api_parity.py -m integration`, 16/16 green. **That
  acceptance-gate file itself is clang-only, not evidence of castxml
  coverage**: every one of its subprocess invocations hard-codes
  `--ast-frontend clang`, not parametrized by backend at all (confirmed —
  `pytest tests/test_dump_cli_typed_api_parity.py -m integration -k
  castxml` selects zero tests). What castxml's newfound availability
  separately confirmed is broader but different: the wider integration
  suite's own castxml-specific cases (`pytest tests/ -m "integration and
  not slow" -k castxml`) are 38/38 green with only the two pre-existing,
  unrelated `xfail`s — real evidence `abicheck dump --ast-frontend castxml`
  itself works end to end in this environment, not evidence this
  particular parity file exercises it. Field-level parity between the two
  paths was already closed
  before this phase started -- `_CONTRACT_KNOWN_DIVERGENT_FIELDS` and
  `_SCAN_KNOWN_DIVERGENT_SHAPES` in that test module were both already
  empty -- so there was no xfail-gated shape left for this phase to flip;
  confirmed empty both before and after this phase's change.

  **Blocker 2 (the post-processing hooks) does NOT close, and the reason is
  independent of which AST backend is available, so obtaining castxml did
  not remove it.** Re-read `perform_elf_dump` end to end (not skimmed)
  looking specifically for whether its first try block (the primary
  `seed_includes_and_fold_compile_context` + `dump()` call) could be
  replaced by a call to `execute_dump_request()`, keeping the second try
  block's post-processing hooks (the ADR-039 collector's own explicit
  second call, the header-graph attach, the clang-layout-tool attach)
  unchanged as hooks applied to the returned snapshot. Two sub-findings,
  each confirmed against the real code, not assumed:

  1. *(Not actually a blocker — investigated and ruled out.)* The ADR-039
     collector (`attach_build_context_for_parsed_headers`) already runs a
     second time, unconditionally, inside `_resolve_side_snapshot_impl`
     itself (PR C's own shared-gate work wired it in there too). Calling it
     a *third* time from `perform_elf_dump`'s own existing second block —
     which is what "keep the hook, route the primary parse" would produce
     — is safe: `attach_build_context` *assigns*
     `snap.build_context_defines`/`conditional_fields`, it never
     accumulates, so a second identically-scoped call is idempotent, and
     `parsed_with_build_context` is only ever set `True`, never reset to
     `False`, so a redundant second stamp cannot regress it. Similarly,
     `scope_header_dirs` (a parameter `perform_elf_dump`'s own `dump()`
     call passes that `_dump_elf`, reached via `execute_dump_request`,
     does not) turns out to be provably redundant with `resolve_dump_
     request`'s own `public_header_dirs` (both are derived from the
     identical `split_public_header_inputs(headers)` call, and `dump()`
     unions them for the extraction contract) — so this is not a real
     divergence either, just a vestigial second computation of the same
     set of directories.
  2. *(A real, structural blocker, confirmed by reading the code, distinct
     from anything the Design section named.)* `dump_cmd`'s legacy
     `-p`/`--compile-db` auto-match (`cli_helpers_compare.
     _resolve_build_context_flags`, using `build_context_for_header`/
     `build_context_union_fallback` — a completely different code path from
     the P0.3 L3->L2 fold's `seed_includes_and_fold_compile_context`) is
     computed in `dump_cmd` *after* `resolve_dump_request_for_cli` already
     built the `ResolvedDumpRequest` (`_resolved`), on the real-execution
     branch only, never on the typed-request-building path at all. Its
     result (`effective_gcc_options`/`effective_compile_db`/
     `compile_db_context_matched`) is what `perform_elf_dump`'s own
     `effective_gcc_options` parameter already carries into its primary
     `dump()` call -- and per this same entry's earlier "legacy-match
     overlap" fix, that legacy match's derived flags are the *sole* source
     of compile-database-derived context whenever the P0.3 fold does *not*
     independently match the same header (the fold's result wins and
     supersedes it whenever the fold *does* match — already the case
     `effective_gcc_options`/`l3_context_applied`'s reassignment in
     `perform_elf_dump` handles). `resolve_dump_request`/
     `_resolve_side_snapshot_impl` has no equivalent call to
     `_resolve_build_context_flags` anywhere -- `DumpRequest.input.compile`
     only ever carries the CLI's own explicit `--gcc-options`, never the
     legacy match's derived flags. So routing `perform_elf_dump`'s primary
     parse through `execute_dump_request()` as-is would silently drop real,
     still-live, still-documented (`dump --build-query`/
     `--build-compile-db`/`-p`/`--compile-db` are explicitly not yet
     removed — PR 3C is gated on this same convergence closing first)
     compile-database-derived flags for exactly the headers the P0.3 fold
     itself does not match — a real regression, not a refactor, for any
     project relying on that fallback. Closing this for real needs the
     legacy match's computation moved earlier (before `resolve_dump_
     request_for_cli` runs) and threaded into the `DumpRequest`/
     `CompileContext` the resolved object carries, so the typed pipeline
     sees it too — a genuine, separate design change to the request-
     building sequence (which field absorbs the legacy match's *derived*,
     not user-typed, flags, and whether that blurs `DumpRequest`'s
     documented "records the run, not a second opinion about it"
     contract), not a same-session drive-by fix. **Not attempted here** —
     recorded so a future attempt starts from this precise mechanism
     instead of re-deriving it, per this file's own "known gaps over risky
     reactive patches" convention.

  **Net effect on this phase's own scoping**: full "route `perform_elf_dump`
  through `execute_dump_request`" (Design section item, this entry's
  original blocker 2) remains unattempted for the reason above -- this was
  never actually gated on castxml availability the way the Design section's
  own item 2 implied; that item's "(b) scope to clang, castxml tracked as
  residual" framing turned out to describe the wrong axis. What castxml's
  availability *did* let this phase newly verify -- run against the wider
  integration suite, not the clang-only acceptance corpus itself (see
  above) -- is that `abicheck dump --ast-frontend castxml` genuinely works
  end to end in this environment today, closing the environmental
  uncertainty the Design section's segfault finding had left open. The
  acceptance corpus's own field-level parity (`_CONTRACT_KNOWN_DIVERGENT_
  FIELDS`/`_SCAN_KNOWN_DIVERGENT_SHAPES` empty) remains verified for clang
  only, exactly as it was before this phase -- extending that specific
  corpus to also parametrize over castxml is real, still-open follow-on
  work this phase did not attempt.

  **Update (2026-08-29): the legacy-match threading half of blocker 2 is now
  closed; routing `perform_elf_dump` itself through `execute_dump_request`
  is still open.** This session re-read the exact mechanism the entry above
  names (`cli_helpers_compare._resolve_build_context_flags`'s legacy
  ``-p``/``--compile-db`` auto-match having no equivalent inside
  `resolve_dump_request`/`execute_dump_request`) and closed the piece that
  was safely landable without also restructuring `perform_elf_dump`'s own
  try/except/cleanup structure in the same change:

  1. `execute_dump_request` gained a new, purely additive
     `legacy_compile_db_tokens: tuple[str, ...] = ()` parameter, threaded
     down through `workflows.artifact.execute._resolve_side_snapshot_impl`
     into `workflows.artifact.resolve._seeded_includes_and_compile_context`
     — the exact same "optional pass-through, defaulted to a no-op, that
     exists only for `dump`'s still-live CLI legacy flags" pattern
     `build_config`/`build_query`/`build_compile_db` already established on
     these same three functions (PR 3A). A caller that already computed the
     legacy match's own derived flags (exactly what `dump_cmd` already does
     via `_resolve_build_context_flags`, unchanged) can now thread them
     through the typed pipeline and have them actually reach the real L2
     header-AST parse (`service.resolve_input`'s `compile=` argument).
  2. **Precedence preserved exactly**, mirroring `perform_elf_dump`'s own
     "legacy-match overlap" fix this entry already documents: the tokens are
     merged into the resolved `CompileContext.gcc_options` only when the
     P0.3 fold's own `applied` came back `False` for a given header — when
     the fold *does* apply, its own result is used verbatim and the legacy
     tokens are discarded rather than stacked on top, verified by a
     dedicated precedence test (see below) that pins the merged
     `gcc_options`/`gcc_option_tokens` string as byte-identical between "no
     legacy tokens" and "legacy tokens given" when the fold applies.
  3. The merge helper (`_fold_legacy_compile_db_tokens`) is a small,
     independent 3-line reimplementation of
     `cli_helpers_compare._merge_gcc_options`'s ordering (legacy flags
     prepended ahead of any existing `gcc_options`), not an import of that
     function — `workflows/artifact/resolve.py` is an engine-layer module
     under `scripts/check_ai_readiness.py`'s `engine-cli-boundary` check,
     which forbids importing a `cli_*` sibling (that module itself imports
     `click`). Confirmed via `check_ai_readiness.py`: zero new
     `engine-cli-boundary` findings.
  4. Verified end to end against a real `g++` build + real `compile_commands.json`
     + real clang L2 parse, not only the merge helper in isolation
     (`tests/test_legacy_compile_db_typed_threading.py`, 4/4 green): a
     compile unit whose source text does not `#include` the public header
     at all is exactly the shape where the two mechanisms provably disagree
     — `header_compile_context.resolve_header_compile_context` (the P0.3
     fold) returns `context=None` with **no union fallback** (confirmed by
     reading its own docstring: "no header the given `CompileUnit`s
     reference" degrades to nothing, full stop), while `build_context.
     build_context_for_header` (the legacy match) falls back to
     `build_context_union_fallback`, which still merges the compile
     database's `-D`s and still sets `compile_db_path` (so
     `_resolve_build_context_flags`'s own `matched` comes back `True`). One
     test proves the real CLI already sees the union-fallback define (the
     fixed point to reproduce); one proves the typed path does **not** see
     it with the new parameter absent (proving the gap this closes was
     real, and that the new parameter is genuinely opt-in rather than
     silently changing existing callers' behavior); one proves the typed
     path **does** see it, byte-identically to the CLI, once the CLI's own
     already-computed `_resolve_build_context_flags` output is threaded
     through `legacy_compile_db_tokens`; one proves the fold-wins precedence
     holds when the fold does apply.

  **What is explicitly still open, and why this session did not attempt
  it**: `perform_elf_dump`'s primary parse (the first try block --
  `seed_includes_and_fold_compile_context` + `dump()`) does **not** yet call
  `execute_dump_request()` — the real `dump` CLI's ELF/PE/Mach-O run still
  executes through `cli_dump_helpers.perform_elf_dump`/`handle_non_elf_dump`
  exactly as before, so `dump_cmd` does not pass `legacy_compile_db_tokens`
  anywhere yet (there is nowhere in it that calls `execute_dump_request` to
  pass it to). Closing that remaining piece needs restructuring
  `perform_elf_dump`'s own try/except/`ResolvedArtifactPlan` cleanup
  handling to delegate to `execute_dump_request()` while preserving its
  second try block's post-processing hooks (the ADR-039 collector's own
  second call, the header-graph attach, the clang-layout-tool attach) as
  hooks applied to the returned `DumpResult`'s snapshot — this entry's own
  earlier sub-finding 1 already confirmed that keeping those hooks as a
  second pass is safe/idempotent, so the remaining work is purely the
  control-flow restructuring itself, not a new correctness question. Given
  this exact code area's own history in this entry (18+ numbered findings
  on the adjacent L3->L2-fold alone, several reverted-and-refixed), that
  restructuring was deliberately left as its own, separately-reviewable
  slice rather than folded into this one — consistent with how every prior
  slice in this entry was landed one at a time. `cli_dump_request.py`'s own
  module docstring and `service_dump_pipeline.execute_dump_request`'s
  docstring both point back here for exactly what remains.

  **Correction (2026-08-29, same day, Codex review on PR #935): the
  threading above landed with a real bookkeeping gap of its own, now
  fixed.** Folding the legacy tokens into the resolved `CompileContext`
  (sub-finding 2 above) updated `gcc_options` but left the function's
  returned `applied` boolean — the exact signal `_resolve_side_snapshot_
  impl` gates `AbiSnapshot.parsed_with_build_context` on — untouched at
  `False` whenever the P0.3 fold itself did not match. Confirmed by reading
  the actual gate (`workflows/artifact/execute.py`'s `if context_applied
  and snap.from_headers: snap.parsed_with_build_context = True`): a typed
  dump relying purely on the legacy-match fallback would have parsed real
  compile-database context and then still reported it as absent — wrongly
  triggering the `header_parse_context_drift`/`header_build_context_
  mismatch` advisory findings and wrongly failing a `--depth build` gate
  that the real CLI's own `perform_elf_dump` path (whose `compile_db_
  context_matched` OR `l3_context_applied` condition already handles this
  correctly) would have satisfied for the identical evidence. A second,
  distinct problem in the same spot: an empty `legacy_compile_db_tokens`
  tuple is indistinguishable from "the legacy match never ran" — so a
  compile unit the legacy match genuinely matched, but which legitimately
  derives zero castxml flags, had no way to signal that it *was* matched.

  Fixed by adding a second, independent parameter, `legacy_compile_db_
  matched: bool = False` — mirroring `perform_elf_dump`'s own `compile_db_
  context_matched` parameter exactly, the second element of
  `_resolve_build_context_flags`'s own return — threaded through the
  identical three-function chain (`execute_dump_request` →
  `_resolve_side_snapshot_impl` → `_seeded_includes_and_compile_context`).
  `_seeded_includes_and_compile_context` now returns `applied=legacy_
  compile_db_matched` (not the fold's own, already-`False` `applied`) in
  the branch where the P0.3 fold did not match, in both its early-return
  path (no `sources`/`build_info`, or no headers) and its main path — so
  a real match sets `parsed_with_build_context` regardless of whether any
  tokens were actually derived, while an unmatched call (the default, and
  every pre-existing caller) stays exactly as it was. Both new parameters
  default falsy, so this remains purely additive.

  Verified with four fast, monkeypatch-based unit tests (no compiler
  needed — `tests/test_legacy_compile_db_matched_signal.py`): matched with
  zero tokens still sets `applied=True`; unmatched with zero tokens stays
  `applied=False` (the pre-existing default behavior, pinned unchanged);
  matched with real tokens sets both the folded `gcc_options` and
  `applied=True`; the fold-applies-wins precedence (sub-finding 2 above)
  holds regardless of what the legacy-match parameters claim. Confirmed
  each of the four fails with `TypeError: unexpected keyword argument
  'legacy_compile_db_matched'` against the pre-fix code (the parameter
  did not exist), not merely that they pass now.

  **Second correction (2026-08-29, same day, second Codex review round on
  PR #935): the fix above still under-counted a real call shape.** A caller
  may thread non-empty `legacy_compile_db_tokens` while leaving the new
  `legacy_compile_db_matched` parameter at its default `False` — exactly the
  shape `tests/test_legacy_compile_db_typed_threading.py`'s own end-to-end
  caller uses. `_seeded_includes_and_compile_context` still returned
  `applied=legacy_compile_db_matched` alone in that case (both the
  early-return and main-path branches), so `applied` stayed `False` even
  though non-empty tokens are themselves proof a legacy match derived real
  flags — reproducing the identical `parsed_with_build_context` under-report
  the first correction above closed, just reachable from a different call
  shape.

  Fixed via a shared `_legacy_compile_db_achieved(matched, tokens) -> bool`
  helper: `matched or bool(tokens)`. Both branches now call it instead of
  reading `legacy_compile_db_matched` directly. `legacy_compile_db_matched`
  remains necessary on its own for a genuinely matched compile unit that
  legitimately derives zero flags (an empty token tuple can't represent that
  case); non-empty tokens are sufficient evidence on their own, independent
  of whether `matched` was also passed.

  Verified with two new fast unit tests in the same file
  (`test_tokens_alone_without_explicit_matched_flag_still_marks_applied`,
  covering the main path; `test_early_return_path_also_honors_tokens_alone`,
  covering the early-return branch) — both confirmed to fail against the
  pre-fix code (`assert False is True`) via `git stash`, and to pass after.
  Full fast unit suite re-run clean (33836 passed, 129 skipped, 4 xfailed,
  0 failed) after this second correction.

  **Third correction (2026-08-29, same day, third Codex review round on PR
  #935): a distinct, real correctness bug in the same function, found by
  reading the actual token shapes involved rather than assumed.**
  `_fold_legacy_compile_db_tokens` used to merge *tokens* into
  `CompileContext.gcc_options` via `" ".join(tokens)` — but *tokens* are
  already-split argv entries (`build_context.to_castxml_flags()`'s own
  return, e.g. `("-I", "/opt/SDK Files/include")`, one element per argv
  position, never pre-joined), and every consumer of `gcc_options`
  re-splits it via `_compiler_options.split_gcc_options` before handing it
  to the real castxml/clang subprocess. A token containing embedded
  whitespace — a Windows SDK include path with a space, or a compile-db
  `-DNAME=a b` define — silently split back into the wrong number of
  tokens on that second pass, corrupting the derived include path or macro
  value the moment a typed dump relying on the legacy match actually
  reached the real parse. Confirmed real, not theoretical: `to_castxml_
  flags()` genuinely emits `-I`/`<path>` as two separate list elements
  (`flags.extend(["-I", str(inc)])`), so any compile-database include path
  with a space reaches this function in exactly the corrupting shape.

  **Also confirmed to be pre-existing, shared debt, not novel to this
  PR**: `cli_helpers_compare._merge_gcc_options` — the real CLI's own
  legacy-match merge path, which `_fold_legacy_compile_db_tokens`'s own
  docstring already documented as byte-for-byte mirroring — has the
  identical `" ".join(build_context_flags)` pattern feeding the identical
  `CompileContext(gcc_options=...)` field, so the real, unconditional
  `dump -p compile_commands.json` CLI path carries this same corruption
  today for a compile-database entry whose derived flags include
  whitespace. **Not fixed here** — this correction's scope is the typed
  pipeline this session's own work introduced; `_merge_gcc_options` is
  pre-existing, live, widely-exercised code with its own blast radius, and
  changing it needs its own dedicated review pass rather than riding along
  inside an unrelated correction. Recorded here as a known, real,
  reproducible gap: an include path or define value containing a space in
  a compile database used with `dump -p`/`--compile-db` (no `--dry-run`
  involved — this is the real-execution path) can silently corrupt the
  derived castxml flags.

  Fixed in the typed pipeline by routing *tokens* through
  `CompileContext.gcc_option_tokens` (verbatim argv entries, a field that
  is never re-parsed by `split_gcc_options`) instead of the `gcc_options`
  string. Precedence preserved exactly: since the combined-token order
  always places `gcc_options` ahead of `gcc_option_tokens` (later wins),
  and the legacy match must still lose to an explicit, caller-supplied
  value, *ctx*'s own `gcc_options` string is split once here — with the
  identical `split_gcc_options` splitter every consumer already applies to
  it downstream, so this changes no token list, only where the split
  happens — and the combined tuple built as `(*tokens, *split(ctx.
  gcc_options), *ctx.gcc_option_tokens)`: legacy first (lowest
  precedence), then whatever *ctx* already carried, in its original
  relative order.

  Verified with five new fast unit tests
  (`TestWhitespaceBearingTokensSurviveTheFold` in
  `tests/test_legacy_compile_db_matched_signal.py`): a whitespace-bearing
  include path and a whitespace-bearing define value both survive intact;
  an explicit `ctx.gcc_options` still outranks a conflicting legacy token;
  an explicit `ctx.gcc_option_tokens` still outranks a conflicting legacy
  token; an empty token tuple remains a true no-op (`ctx` returned
  unchanged by identity, not merely by value). Three of the five confirmed
  to fail against the pre-fix code via `git stash` (the two whitespace
  tests, and the `gcc_option_tokens`-precedence test — the `gcc_options`-
  precedence test and the no-op test already held under both versions).
  The three pre-existing tests this correction's field change touched
  (`test_matched_with_tokens_folds_flags_and_marks_applied`,
  `test_tokens_alone_without_explicit_matched_flag_still_marks_applied`,
  `test_early_return_path_also_honors_tokens_alone`) were updated to
  assert `gcc_option_tokens` instead of the now-unused `gcc_options`
  string; `tests/test_legacy_compile_db_typed_threading.py`'s own
  precedence test (`test_fold_wins_over_legacy_tokens_when_it_applies`)
  needed no change, since it already read the *combined* effective token
  sequence across both fields rather than pinning `gcc_options` alone.

  **Fourth correction (2026-08-29, same day): this entry's own claim that
  "the remaining work is purely the control-flow restructuring itself, not a
  new correctness question" is WRONG, and is retracted here.** A dedicated
  session set out to do exactly the routing that claim scoped —
  `perform_elf_dump`'s primary parse calling `execute_dump_request()` instead
  of `seed_includes_and_fold_compile_context()` + `dumper.dump()` as two
  independent steps — read every function on both sides end to end
  (`perform_elf_dump`, `handle_non_elf_dump`, `service_dump_pipeline`'s
  `resolve_dump_request`/`execute_dump_request`/`ResolvedDumpRequest`/
  `DumpResult`, `workflows/artifact/execute.py`'s
  `_resolve_side_snapshot_impl`/`enforce_requested_depth`,
  `workflows/artifact/resolve.py`'s `_seeded_includes_and_compile_context`,
  `service.resolve_input`, `service_dump_native._dump_elf`,
  `cli_buildsource._write_snapshot_output`, `cli_dump_request.
  build_dump_request`, and `frontends/cli/commands/dump.py`'s real call
  sites) and built the parameter-by-parameter parity map the routing needs.
  **Neither function was converted.** Two *structural* blockers were found
  that the claim above did not anticipate, both distinct from the legacy
  `-p`/`--compile-db` mechanism the earlier sub-finding 2 named and closed.
  Several previously-suspected blockers were, by contrast, ruled out for
  real; both lists are below so a future attempt starts from measured facts.

  **Blocker A (ELF only, and it is exactly `DumpResult`'s own documented
  "Lifetime caveat" made live rather than latent).** `perform_elf_dump`
  passes the CLI's *real* `collect_mode` to
  `seed_includes_and_fold_compile_context`, which sets
  `allow_inferred_build_query=collect_mode != "off"` (`buildsource/l2_seed.py`)
  and therefore genuinely returns non-empty `pending_cleanups` — the
  temporary build directory a zero-config *inferred* build-system query
  seeded, whose generated headers the seeded include dirs point at.
  `perform_elf_dump` drains that plan in a `finally` placed deliberately
  **after** its two post-processing second passes (`service._attach_header_
  graph` and `workflows.extraction.attach_clang_layout`), and its own inline
  comment states why in as many words: "the header-graph pass above (when
  requested) reuses the same seeded include dirs the main `dump()` parse
  used, so cleanup must wait until it ... has run". `_resolve_side_snapshot_
  impl` drains in a *nested* `finally` immediately after
  `service.resolve_input`, and its own comment states, equally explicitly,
  why it must: `embed_side_build_source` runs its own inferred query inside
  the same call, and an undrained seed still holds the deterministic
  per-source-tree build dir under an exclusive `flock`, so a later drain
  makes the second query self-contend for up to `INFERRED_QUERY_TIMEOUT_S`
  (600s) — the identical self-contention shape recorded as the fifth finding
  on the L3→L2-fold entry. The two requirements are in **direct conflict**
  the moment `perform_elf_dump`'s parse routes through that primitive: today
  they don't conflict only because `perform_elf_dump` runs no embed inside
  its own plan. Deferring the seed cleanups back out to the CLI caller (an
  additive `defer_seed_cleanup` pass-through, the obvious-looking fix) is
  precisely what re-creates the 600s contention; draining them where the
  shared primitive does is precisely what deletes the directories the two
  second passes still need to re-parse headers under. `DumpResult`'s own
  docstring already names this ("safe for *identity or comparison* ... a
  caller intending to re-read a file under one of these paths ... cannot yet
  do so safely"), and already scopes the fix as PR 3A's pair-aware/lifetime
  redesign — a separate piece of work, not a control-flow rewrite. Weakening
  or disabling either second pass to dodge it was considered and rejected:
  each exists because of its own recorded Codex-review regression (a second
  clang pass silently degrading to a declaration-only graph, and a
  `dump --ast-frontend clang` baseline silently carrying no layout-tool
  facts).

  **Blocker B (both ELF and PE/Mach-O).** `execute_dump_request` is a
  resolve **+ embed + enforce** pipeline: `_resolve_side_snapshot_impl` runs
  `embed_side_build_source` (L3-L5) inline, `service.resolve_input` →
  `run_dump` applies `dumper_scoping.resolve_dependency_scope` from
  `InputSpec.include_dependencies`, and `execute_dump_request` then calls
  `enforce_requested_depth`. The `dump` CLI does all three of those things
  **after** the parse and after provenance stamping, in
  `cli_buildsource._write_snapshot_output`: `embed_build_source` (guarded by
  `build_source_already_satisfies`), then `check_requested_depth_satisfied`,
  then `resolve_dependency_scope(snap, include_dependencies, header_roots)`.
  Routing the primary parse through `execute_dump_request` therefore reorders
  all three relative to the CLI's own post-parse pipeline, with three
  concrete consequences, none of them cosmetic: (1) the ADR-039 build-context
  reconciliation, the header-graph attach and the clang-layout attach would
  run over an *already dependency-scoped* snapshot (`--include-system-
  declarations` defaults off, so `InputSpec.include_dependencies=False` is
  the common case, and the inner scope call has no access to the write path's
  `header_roots` set at all); (2) the depth floor would be enforced against
  only the *inner* embed's result, before `_write_snapshot_output`'s own
  embed — the one that actually fills L3-L5 for a `dump` today — has run; and
  (3) that floor raises `ValidationError` where the CLI's own
  `check_requested_depth_satisfied` raises a Click error, a different
  user-facing message and exit code for the identical input. Making this
  safe means either suppressing three behaviors inside a shared Tier-2
  primitive for one caller (inventing a code path, which this entry's own
  convention forbids) or moving the CLI's write-time embed/enforce/scope
  stanza to resolution time — a real, separately-reviewable redesign of
  `_write_snapshot_output`'s contract, not part of the routing.

  **Ruled out, with evidence, so they are not re-litigated.** (i) The
  P0.3 fold's fourth return value (`l3_include_dirs`, which
  `perform_elf_dump` folds into `extra_hash_dirs`) is *not* lost: the folded
  context's own tokens carry those dirs, and `service_dump_native._dump_elf`
  independently recomputes the same set via
  `cache_relevant_operand_paths(cc.gcc_option_tokens)`. (ii) The P3
  inferred-header-root derivation (`resolve_inferred_header_roots` →
  `inc_extra`/`deferred`/`deferred_dirs`) is *not* lost either — `_dump_elf`
  performs the identical derivation itself. (iii) `debug_info_path` is not
  lost: `_dump_elf` resolves it from `debug_roots`/`enable_debuginfod`, both
  of which `build_dump_request` already puts on the `InputSpec` (it would be
  resolved *twice*, once by `dump_cmd` for its echo and once here, which is
  wasteful and double-logs but is not a correctness gap). (iv) The
  whole-snapshot cache (`resolve_input` → `cached_run_dump`, which
  `perform_elf_dump`'s bare `dump()` bypasses) does **not** newly activate:
  `build_dump_request` always sets `InputSpec.compile` to the CLI's resolved
  `CompileContext`, and `service_dump_cache._dump_is_cacheable` refuses to
  cache any call with a non-`None` `compile`. (v) `follow_deps` on PE/Mach-O
  is not a divergence: `populate_side_dependency_info` is documented and
  implemented as an ELF-only no-op. (vi) `ast_memoize_scope()`/
  `suppress_streaming_prune()` are trivially preservable — the caller can
  wrap the `execute_dump_request` call itself. (vii) `handle_non_elf_dump`
  has **no** Blocker A: it runs no post-processing second pass and already
  drains its plan in a `finally` immediately after the parse, exactly where
  the shared primitive does. It is blocked by B alone, which is why
  converting "the small one first as a warm-up" does not in fact isolate a
  safely-landable slice.

  **Net**: the remaining piece of this entry is *not* control-flow-only.
  Closing it needs (a) PR 3A's already-scoped pair-aware/lifetime redesign of
  the L2 seed's cleanup ownership, so a caller with post-parse hooks can keep
  the seeded dirs alive without the embed step self-contending on their lock,
  and (b) a decision about where `dump`'s L3-L5 embed, depth enforcement and
  dependency scoping belong — resolution time (matching the typed pipeline)
  or write time (matching today's CLI) — since the two cannot both be true of
  one code path. Recorded at this precision, per this file's own convention,
  so the next attempt starts from the mechanism rather than re-deriving it.

  **The real ELF `dump` run is migrated (CLI cleanup phase two, PR C).**
  Decision (b) above is resolved as **split, not uniform**: only the L3-L5
  embed moves to resolution time (`execute_dump_request`); depth enforcement
  and dependency scoping stay at write time, unchanged, in
  `_write_snapshot_output`. This became possible only because two
  prerequisites this entry's own "Blocker A"/"Blocker B" analysis called
  for were separately closed first, in the plan doc's own PR 3A subsection
  (2026-08-27/28, "Investigated further"/"Update"): a real, end-to-end test
  (`tests/test_dump_write_after_resolve_time_embed.py`) confirmed the
  depth-gate/provenance/dependency-scope half of `_write_snapshot_output`'s
  sequence already handles a resolve-time-embedded snapshot correctly with
  *no* code change (the two depth checks share the identical
  `evidence_depth.gated_source_label`/`depth_rank` primitives, so calling
  both is redundant, not wrong), and the Flow-2 `--inputs` pack fold was
  separately verified safe layered on top of a resolve-time embed too. That
  closes Blocker B's "resolve + embed + enforce" concern for the
  *embed* alone — depth enforcement is not moved, so its own reordering
  hazard never applies; **Blocker A (the seed-cleanup self-contention) is
  independently a non-issue for `dump`'s real invocation shape**, because a
  `dump` CLI request's own `header_backend`/`allow_build_query` inputs never
  produce a resolve-time seed with non-empty `pending_cleanups` for a
  request that also carries a compile database or a pack `--sources`/
  `--build-info` value under the shapes this migration's own tests exercise
  (`build_source_already_satisfies` already accounts for the common,
  compile-DB-backed case) — the two ELF-specific post-processing second
  passes (`_attach_header_graph`, `attach_clang_layout`) that Blocker A
  worried about run *inside* `service.resolve_input`'s own ELF dispatch
  (`service_dump_native.py`), not as a separate stage `execute_dump_request`
  adds on top, so they already see the seeded dirs before that dispatch's
  own cleanup drains them — the same ordering `perform_elf_dump` itself
  used to hand-maintain, now owned by one implementation instead of two.
  One structural nuance from Blocker B's own concern (1) is real and
  *not* separately reasoned away here, only measured: `service.run_dump`'s
  own choke point (`dumper_scoping.apply_dependency_scope_to_run_dump_
  result`) already dependency-scopes the snapshot *before*
  `_resolve_side_snapshot_impl`'s own ADR-039 collector/header-graph/
  clang-layout attaches run on it (this is the shared pipeline's own
  existing, pre-migration behavior for `compare`/`scan`, not something this
  migration introduces) — so `_write_snapshot_output`'s own, unchanged
  `resolve_dependency_scope` call at the end is a second, write-time pass
  over an already-once-scoped snapshot rather than the sole pass it used to
  be. Confirmed idempotent for every shape this migration's own parity
  suite exercises (both calls scope against the same effective header-root
  set), not proven idempotent in general.
  `frontends/cli/commands/dump.py`'s real (non-`--dry-run`) ELF branch calls
  a sibling module, `frontends/cli/dump_execute.py` (split out purely to
  keep `dump.py` under the architecture gate's 800-line production-file cap
  — ADR-061 freezes the flat `cli_*.py` root family's member list, so a
  genuinely new module goes into its responsibility-package tree, alongside
  `runtime.py`/`artifact_set_dry_run.py`, instead), which builds a second,
  execution-scoped `ResolvedDumpRequest` from the same
  `DumpRequest` `--dry-run` already resolves — re-pointed at the
  post-linker-script-following `so_path` (`resolve_dump_request`'s own
  `detect_binary_format` call runs before any such following, so feeding it
  the pre-follow path risked a wrong `fmt` for a symlink-to-linker-script
  input) and with `requested_depth` nulled out (so `execute_dump_request`'s
  own `enforce_requested_depth` — a *different*, more generically-worded
  `ValidationError` than `check_requested_depth_satisfied`'s
  `DumpDepthNotSatisfiedError` — never fires; `_write_snapshot_output`'s own
  call stays the sole, unchanged enforcement point for this case, preserving
  `tests/test_depth_vocabulary.py`'s pinned message) — and calls
  `execute_dump_request(exec_resolved, legacy_compile_db_tokens=...,
  legacy_compile_db_matched=...)`, threading the legacy `-p`/`--compile-db`
  auto-match through as the explicit pass-through ADR-063 Phase 1 already
  built for exactly this purpose (`execute_dump_request`'s own docstring)
  rather than porting it into `InputSpec` as a first-class field — the
  pass-through already implements the P0.3-fold-wins precedence rule
  `perform_elf_dump`'s own `_fold_explicit_gcc_options` hand-rolled, so
  adding a second, dataclass-shaped representation of the identical fact
  would be a second place for it to drift, not a cleaner one.
  `perform_elf_dump` itself is retired from this call site (still defined,
  in case any other caller depends on it, but `dump_cmd` no longer imports
  it); `handle_non_elf_dump` (PE/Mach-O) is untouched — no PE/Mach-O
  toolchain was available in this environment to verify a migration
  against, so it stays exactly where this entry's "Blocker B (both ELF and
  PE/Mach-O)" heading already scoped it: open.

  **Update (2026-09-01, PR #980): PE/Mach-O is now migrated too, closing
  this entry's remaining half.** The design this entry already worked out
  for ELF (null out `requested_depth` before calling
  `execute_dump_request`, keep `_write_snapshot_output`'s embed/enforce/
  scope stanza as the sole enforcement point, thread the legacy `-p`/
  `--compile-db` auto-match through as an explicit pass-through) carried
  over to PE/Mach-O mechanically, with no second structural investigation
  needed — `execute_dump_request`/`_resolve_side_snapshot_impl` were
  already format-generic (`is_elf=True if fmt == "elf" else None`,
  `attach_build_context_for_parsed_headers`/`embed_side_build_source`
  called unconditionally regardless of format), the same pipeline
  `compare`'s implicit-dump operand and `scan`'s candidate resolution
  already used for PE/Mach-O input. `handle_non_elf_dump` is retired from
  the CLI's real dispatch the same way `perform_elf_dump` was (still
  defined, for its own direct unit tests). **Verified only via mock-based
  CLI/unit tests, not a real end-to-end parity run** — no PE/Mach-O
  toolchain was available in this environment either, so unlike ELF's own
  `test_dump_cli_typed_api_parity.py` corpus, there is no byte-for-bit
  confirmation against a real compiled DLL/dylib. `AGENTS.md`'s own
  `service_dump_pipeline.py` entry is updated to match.

  One real, user-visible behavior change falls out of the migration rather
  than being a side effect nobody decided: `dump`'s L4 source-extractor
  default flips from an accidental **clang** (`perform_elf_dump` forwarded
  the bare, unresolved `header_backend` straight to the write-time embed,
  which treats anything but the literal string `"castxml"` as clang) to
  **castxml** (the shared pipeline's `effective_frontend` resolution,
  matching `compare`'s implicit-dump operand, the typed `DumpRequest` API,
  and `dump`'s own L2 header-AST default) — this is the plan's own
  "item 3" castxml L4 phantom-implicit-member bug's actual payoff: that fix
  (`Function.is_compiler_generated`, elsewhere in this file) is exactly what
  makes this default safe to inherit now, where an earlier investigation in
  this same entry explicitly deferred it for being unsafe before that fix
  existed. `--ast-frontend clang` recovers the previous default for a caller
  that needs it. The sibling `scan`-vs-`dump`/`compare` L4 extractor default
  divergence (the plan's own "item 2") is **unchanged by this migration** —
  `scan_engine._build_new_snapshot` still hardcodes `source_extractor="auto"`
  (resolving clang) via its own opt-in override on the shared primitive,
  deliberately preserved when `scan`'s candidate resolution was migrated
  onto the same primitive; that remains its own separate, deferred decision.

  > **Update (2026-09-02): item 2's *explicit-request* half is now closed;
  > only its unflagged-default half remains.** The paragraph above stays
  > accurate as a record of *that* migration, but the hardcoded
  > `source_extractor="auto"` it describes is gone — `scan_engine`'s call
  > site now passes `service_compare_evidence.explicit_source_extractor(
  > compile_context) or "auto"`. Full account, including why this was safe
  > where the earlier "flip it to `effective_frontend`" attempt (recorded
  > above) was not, and exactly what is still open: the plan's own **item 2**
  > in
  > `cli-cleanup-phase-two.md`, which was the
  > narrative owner for this item's status. **Update (2026-09-06): that plan
  > is closed and this item dissolves rather than closing** — ADR-068 retires
  > `scan`, so the unflagged-default half disappears with `scan_engine`
  > itself; the full investigation narrative is in that file's git history
  > (last full-length revision `a92a4a89`). The one thing worth keeping here,
  > since it is what this file exists for: the reverted attempt was reverted
  > for surfacing the castxml L4 phantom-implicit-member bug, and that bug
  > (`Function.is_compiler_generated`, elsewhere in this file) being fixed is
  > what made the request half tractable at all.

  Verified: the full fast unit suite; the real-toolchain (`g++`/clang/
  castxml) integration suite for this area
  (`test_dump_cli_typed_api_parity.py`'s 16 cases — `_CONTRACT_KNOWN_
  DIVERGENT_FIELDS` stays empty, i.e. zero remaining divergence between the
  migrated CLI path and the typed pipeline — plus `test_dump_scan_l3_
  comparability.py`, `test_dump_write_after_resolve_time_embed.py`,
  `test_dump_embed_idempotence.py` — updated to count the resolve-time embed
  call site too, not just the write-time fallback — `test_compile_db_
  filter_scope.py`, `test_dry_run_contract.py`, `test_dry_run_build_query_
  contract.py`, `test_l2_seed_flow2_packs.py`,
  `test_scan_adr039_build_context.py`, `test_castxml_l4_phantom_members.py`,
  `test_dump_depth_provenance.py`, `test_depth_vocabulary.py` — 2 xfails,
  the same pre-existing, already-documented `_SCAN_KNOWN_DIVERGENT_FRONTENDS`
  signature, unchanged); `mypy`/`ruff` clean on the touched modules.

  > **Update (Block 7, PR C's own tail): the explicit-`--config`
  > dry-run/execution parity residual named throughout this entry is now
  > closed.** `api_types.InputSpec` gained a `build_config` field mirroring
  > `ScanRequest.build_config` — `cli_dump_request.build_dump_request` sets
  > it from `dump`'s own `--config`, and `cli_compare_helpers`/
  > `frontends.cli.commands.compare`'s inline `--old/new-sources` embed path
  > (its own nested `ctx.invoke(dump_cmd, ...)`) sets it from `compare`'s own
  > raw `--config` value too. `workflows.plan.SidePlan.build_config` carries
  > it into `_check_bazel_target_scoping`, which now calls
  > `bazel_target_scoping_failure(..., build_config=side.build_config, ...)`
  > instead of always passing `None` for `dump`/`compare` — so the shared
  > `AnalysisPlanner.resolve` chokepoint both `--dry-run` and the real run
  > go through sees the same explicit config `embed_build_source` already
  > honored at real-execution time via its own `cfg_path = build_config or
  > discover_build_config(raw_sources)` precedence, instead of only ever
  > seeing whatever `.abicheck.yml` auto-discovery found at `sources`. Does
  > not change what actually executes: `embed_build_source` still receives
  > the CLI's own raw `--config` value out-of-band, exactly as before; this
  > only widens what the pre-flight resolution (and therefore `--dry-run`)
  > can see. The `--dry-run` `build.query` trust receipt
  > (`add_build_query_dry_run_section`) was already correct before this
  > change and is untouched.
  >
  > **A security review on the PR landing this (#1089) caught a real
  > regression in the first version of the fix, worth recording precisely
  > since it is exactly the class of bug ADR-032 D5 exists to prevent.** The
  > first attempt forwarded `compare`'s already-*resolved* `cfg_path`
  > (`config if config is not None else discover_project_config()` —
  > conflating an explicit `--config` with `_resolve_compare_config`'s own
  > auto-discovery fallback) into the nested `dump_cmd` invocation's
  > `build_config` parameter, instead of the raw, explicit-only `config`
  > value. `embed_build_source`'s `cfg_trusted_for_query = build_config is
  > not None or build_query is not None` treats any non-`None` `build_config`
  > as operator authorization to execute `build.query` — so that version
  > would have let a `compare --sources old=<tree>` invocation with *no*
  > `--config` at all execute an arbitrary `build.query` command from a
  > `.abicheck.yml` merely auto-discovered from the working directory or the
  > sources tree being compared, i.e. from content a reviewed PR's own
  > checkout can control. Fixed by forwarding the raw `config` parameter
  > (`run_compare`'s own un-resolved `--config` kwarg — `None` unless the
  > operator actually typed it) instead of `cfg_path`. Verified via
  > `tests/test_compare_inline_embed_config_trust.py`'s
  > `test_auto_discovered_config_is_not_forwarded` (fails against the
  > vulnerable version — asserts `build_config` stays `None` when only an
  > auto-discovered config exists) and `test_explicit_config_is_forwarded`
  > (positive control — an operator-supplied `--config` still reaches the
  > nested invocation, so the fix doesn't overshoot into never forwarding
  > one at all). `dump`'s own `--config` was never subject to this: its
  > `build_config` local is always the literal, explicit-only Click flag
  > value, with no auto-discovery fallback at that layer.
  >
  > Verified via `tests/test_analysis_plan.py`'s
  > `test_dump_request_honors_explicit_build_config_over_auto_discovery`/
  > `test_compare_request_honors_explicit_build_config_over_auto_discovery`
  > (request-level, through `AnalysisPlanner.resolve` directly) and
  > `tests/test_bazel_root_targets.py`'s
  > `test_dump_cli_explicit_config_scoping_matches_dry_run_and_real_run`
  > (end to end through the CLI, asserting `--dry-run` and the real run
  > agree); the full fast unit suite; `mypy`/`ruff` clean on every touched
  > module. Directory/package `compare`'s release fan-out was left
  > untouched — out of this residual's stated scope (the plan names
  > `dump`/`compare`, the single-pair commands).

- **Lambda-closure churn survives at the *function* level after the type-level
  fix — investigated, deliberately not patched (oneTBB flow-graph report,
  fresh evidence).** `name_classification._ANONYMOUS_TYPE_MARKERS` did not
  recognize clang's own closure spelling (`(lambda at <path>:<line>:<col>)`,
  or the `(lambda:<file>:<line>:<col>)` form
  `strip_anonymous_type_location` normalizes it to), so a template
  instantiated over a closure —
  `raii_guard<(lambda:task_group.h:522:26)>` — was treated as ordinary ABI
  surface. That half is fixed: the marker list now covers it, and an
  unrelated edit that merely shifts the lambda's line no longer produces a
  `type_removed`/`type_added` pair. **Two residuals were reproduced and are
  not closed.**

  (1) *Function-level findings on closure-parameterized symbols.* A
  destructor of that instantiation is still reported as a BREAKING
  `func_removed` (plus `func_added` for the shifted spelling), and a public
  function taking the closure-parameterized type by value still produces
  `func_params_changed`/`template_param_type_changed`, because the
  *mangled symbol* and the *parameter type spelling* both embed the
  closure's source coordinates. Reproduced directly through `compare()`
  with the two spellings differing only in the line number.
  `is_non_abi_surface_type` is a *type*-name predicate and is not consulted
  on either path, so the marker fix cannot reach them. **Demoting the
  removal is not the fix, and the reason matters**: this codebase already
  states the correct reading in `change_registry`'s own
  `unnamed_type_in_public_abi` entry ("the Itanium mangling of unnamed
  types is per-translation-unit and compiler-ordering dependent ... so
  exporting one is an ABI time bomb: a rebuilt consumer can fail to resolve
  the symbol"). A consumer *already linked* against the old numbering
  really does fail at load when the numbering shifts — so the removal is a
  genuine break, and softening it would hide a real one. That is the same
  direction of error the linkage-blind-removal entry above was reverted
  twice for. Nor is "strip `:<line>:<col>` before comparing two spellings"
  a free win: `strip_anonymous_type_location`'s own docstring records why
  the coordinates are kept (two unrelated lambdas in one header collapse to
  one key, silently overwriting an entry in every name-keyed map that
  consumes the spelling), and this pass has no real oneTBB snapshot to
  check a narrower "normalize only for pairwise spelling comparison, never
  for keys" variant against. What a correct fix needs is the annotation
  route ADR-style precedent already establishes elsewhere in this file
  ("Annotate; never remove"): correlate such a finding with the existing
  `unnamed_type_in_public_abi` RISK signal so a reader can see *why* the
  symbol churned, rather than removing it from `changes` or lowering its
  verdict. That is a new correlation pass with its own identity question
  (which finding covers which), not a marker-list edit.

  **Update: the ctor/dtor half of (1) is now closed via binary evidence,
  not annotation.** A later report against real oneTBB 2021.13.0 →
  2022.3.0 binaries reproduced exactly this shape at scale:
  `demote_lambda_closure_unexported_findings` (`diff_templates.py`) had
  already been added to demote a `FUNC_PARAMS_CHANGED`/
  `TEMPLATE_PARAM_TYPE_CHANGED`/`TEMPLATE_RETURN_TYPE_CHANGED` finding
  whose reported symbol is confirmed absent from BOTH sides' real ELF
  exported symbol table (never escalates; fails closed when no ELF
  evidence exists on either side) — but it deliberately excluded every
  castxml-synthesized ctor/dtor key
  (`dumper_castxml.is_synthetic_ctor_key`/`is_synthetic_dtor_key`), on the
  correct-as-far-as-it-went grounds that such a key is *never* itself a
  real exported symbol (castxml synthesizes it specifically because it
  could not produce one), so a membership check against the key text
  would always read "confirmed absent" — vacuously, not because anything
  was actually verified. That exclusion left every destructor/constructor
  of a closure-parameterized instantiation permanently un-demotable,
  which is exactly the residual (1) describes and exactly what the oneTBB
  report reproduced: 5 breaking `func_removed` findings, all on synthetic
  ctor/dtor keys naming
  `tbb::detail::raii_guard<(lambda:task_group.h:522:26)>`/
  `try_call_proxy<...>`/`task_arena_function<...>`/
  `delegated_function<...>`, paired 1:1 with 5 compatible `func_added`
  findings differing only in the lambda's line number.

  Fixed by asking the binary a question the synthetic key's *text* can
  answer honestly, even though the key itself is not a real symbol: is
  the owning class/class-template exported under ANY instantiation at
  all, on either side?
  `finding_identity_ctor_dtor.synthetic_ctor_dtor_template_base_name`
  recovers the owning scope from the key (that same module's own
  `synthetic_ctor_scope` for a ctor key's
  depth-aware `scope(params)` split; a plain prefix strip for a dtor key),
  reduces it to the bare, template-argument-stripped template name via
  `type_reachability._bare_type_name` plus a top-level `<` scan
  (`raii_guard<(lambda:...)>` → `raii_guard`), and
  `finding_identity_ctor_dtor.itanium_source_name_token` renders that name
  as its Itanium `<source-name>` encoding (`"raii_guard"` → `"10raii_guard"`,
  keyed on the identifier's encoded UTF-8 **byte** length rather than its
  Python character count, so a non-ASCII class name still produces the
  correct token) — a literal substring every real mangled symbol naming
  that class as a scope component must contain (Itanium C++ ABI §5.1.1),
  checked directly against the raw exported names with no external
  demangler invoked, the same "structural, not textual" preference
  `diff_cxx_rules.itanium_scope_components` already established for this
  codebase. Both helpers live in `finding_identity_ctor_dtor.py`, not
  `diff_templates.py` itself (which only keeps the classification/
  modulation entry point, `demote_lambda_closure_unexported_findings`):
  `diff_templates.py` is one of ADR-061's `debt.yaml`-tracked
  no-growth-baselined legacy files, `finding_identity_ctor_dtor.py` already
  owns every other castxml synthetic-ctor/dtor-key helper and had headroom
  under the flat 800-line production limit, and a brand-new flat `diff_*`
  sibling module is explicitly rejected by `scripts/check_architecture.py`'s
  `frozen_root_families` list (confirmed by trying it — `[frozen-root-family]`
  and `[root-module]` findings) — while a real `policy/` package migration
  for just this one function was investigated and found to cascade into
  `unclassified-import` findings for every one of its ~8 currently-flat,
  not-yet-layer-classified dependencies (`checker_policy`, `diff_symbols`,
  `dumper_castxml`, `elf_symbol_filter`, `type_reachability`,
  `name_classification`, ...), which is its own separate, much larger
  ADR-061 migration slice, not a follow-up to this fix. A template with
  zero exported members under any instantiation on either side is demoted
  (`Verdict.COMPATIBLE_WITH_RISK`,
  `modulation_rule="lambda_closure_never_exported"`, same ADR-025 hook,
  never removed); a template that *does* export some other instantiation
  is left exactly as severe as the detector made it, since this check
  cannot rule out that the specific closure-parameterized instantiation
  was the one a consumer actually linked against.

  Deliberately narrower than reconstructing the *exact* per-instantiation
  mangling: the real Itanium mangling of a closure-type template argument
  uses the compiler's own unnamed-type encoding (`Ul<parameter-types>E_`),
  never castxml's `(lambda:file:line:col)` spelling, so there is no way to
  derive the precise mangled substring for one specific instantiation from
  the snapshot text alone — checking the template's own name is the safe,
  strictly more conservative substitute this function's whole design
  (fail closed, never escalate) already calls for. See
  `tests/test_lambda_closure_function_demotion.py`'s
  `TestSyntheticCtorDtorKeysDemotedWhenTemplateNeverExported`/
  `TestSyntheticCtorDtorKeysNotDemotedWhenTemplateIsExported` for both
  directions, verified against the exact class names from the oneTBB
  report.

  **A review round on the same fix found a real gap in the substring
  search itself, not in the demotion logic around it.** Six `std::` names
  (`allocator`, `basic_string`, `basic_istream`, `basic_ostream`,
  `basic_iostream`) carry a *fixed, mandatory* Itanium ABI substitution
  (`Sa`/`Sb`/`Si`/`So`/`Sd`, C++ Itanium ABI §5.1.2) — the mangler always
  emits the abbreviation instead of the literal source-name, even on the
  first occurrence in a symbol. A real `std::allocator<int>::allocator()`
  mangles to `_ZNSaIiEC1Ev`, never to anything containing the literal
  substring `"9allocator"` — so a synthetic `std::allocator<(lambda:...)>`
  finding's literal-token search would read that class as "never
  exported" regardless of the truth, silently demoting a genuine removal.
  Fixed with `finding_identity_ctor_dtor.itanium_standard_substitution_
  token`, checked alongside the literal token whenever the owning class's
  qualified name is exactly one of the six; see
  `tests/test_lambda_closure_function_demotion.py`'s
  `TestStdAllocatorSyntheticKeyNotFalselyDemoted` for the exact
  counterexample from review, reproduced and fixed.

  Two related residuals from the same report. The compatible `func_added`
  churn is ordinary add/remove pairing (not itself a bug), left as-is.

  ~~The risk `declaration_renamed` findings elsewhere in the same report (7
  further ones) is pure noise from the lambda's identity embedding its
  source `line:col` rather than an ordinal position — closing that needs
  changing how a closure is *identified*, a materially larger change to
  `name_classification`/the castxml and clang backends' own closure naming
  than this fix's binary-evidence check, and is not attempted here.~~
  **Update: fixed, narrower than the originally-scoped "change how a
  closure is identified" (fresh report, real oneTBB 2021.12.0 -> 2023.0.0,
  139 `declaration_renamed` L5 findings against oneDNN's 3 for a comparable
  corpus size).** Tracing a representative sample confirmed the same
  mechanism this entry already names: `buildsource.graph_reconcile`'s
  structural-context tier correctly PAIRS an unrelated-edit-shifted
  closure with its own unchanged predecessor (that matching evidence never
  reads the coordinate text at all), but `_classify_outcome` then compared
  the two nodes' *literal* qualified-name spelling — which embeds the
  closure's own `:line:col` — to decide "renamed", so every closure an
  unrelated header edit merely shifted read as a rename. Rather than the
  originally-scoped identity-naming rewrite (which risks the exact
  same-header-collision trade-off `strip_anonymous_type_location`'s own
  docstring documents for *matching*), the actual fix is narrower and
  provably safe: `model.graph_identity.closure_location_free_identity`
  drops a closure/anonymous-tag marker's basename+coordinates entirely (not
  merely the checkout-root prefix `_normalize_graph_identity` already
  strips) for the *outcome-classification* comparison only, never for
  matching/merging — the pair was already established by tier evidence that
  never consults this text, so no new merge rides on it, and a genuine
  cross-file move is still caught by the separate, coordinate-text-blind
  `old_file`/`new_file` check right below it (reclassifying it
  `declaration_moved` instead of the misleading `declaration_renamed`,
  never hiding the file change). See
  `tests/test_graph_reconcile_closure_rename.py` — both the oneTBB-shaped
  regression cases and a standalone property-test class for the new
  primitive (idempotence, location/basename invariance, parenthesized/bare
  spelling agreement, marker-kind non-collapse, never-raises on arbitrary
  text) per this file's own "Primitive-level property tests" guidance,
  since this is a reusable identity-comparison helper, not a one-off
  patch. The separate, still-open gap about the L5 graph's own node *ids*
  not sharing the flat snapshot's ordinal renumbering (this file's own
  "The L5 source graph's own node identities are never renumbered..."
  entry, elsewhere in this file) is unaffected by this fix — it is about
  node-id/flat-field spelling agreement, not the rename-vs-not-renamed
  classification this fix corrects.

  And a public-surface filter gap (ELF-only, mangled-only symbols
  such as `std::once_flag::_Prepare_execution<lambda>`'s internal guard
  thunks, which `surface.py` cannot scope-classify because it never
  demangles) is a separate detector, not this one, and is likewise not
  addressed here.

  (2) *A constructor key rendering a literal `?` parameter.* Traced to two
  legitimate "type not recoverable" sentinels rather than to a formatting
  bug: `dwarf_snapshot._process_param` returns `Param(type="?")` for a
  `DW_TAG_formal_parameter` carrying no `DW_AT_type`, and
  `dumper_castxml._type_name` returns `"?"` when a referenced type id does
  not resolve in the XML (a closure class declared inside a function body
  is exactly the shape that goes unemitted). Both are honest unknowns, and
  which one produced the reported key cannot be determined without the
  original snapshot, which this pass does not have. Guessing a
  substitution here would replace a visible unknown with a fabricated
  spelling, which is strictly worse; closing it needs the real artifact (or
  a live castxml/DWARF repro of a closure-parameterized ctor) first.

- **The L5 source graph's own node identities are never renumbered
  alongside the flat snapshot's closure markers (Codex review on PR #868,
  fresh evidence).** `renumber_anonymous_closure_identities` rewrites a
  closure's `:<line>:<col>` discriminator to a stable `#N` ordinal across
  `AbiSnapshot.functions`/`variables`/`types`/`enums`/`typedefs`/
  `constants`/`fact_provenance` (`_LAMBDA_IDENTITY_FIELDS`), but
  `service_header_graph_attach._attach_header_graph`'s embedded
  `build_source.source_graph` is built by a genuinely separate clang
  parse (`buildsource.header_graph`), whose node ids
  (`graph_facts._decl_node_id`/`_type_node_id`) are derived directly from
  the raw, un-renumbered identity string -- confirmed by reading both
  functions, which apply no renumbering at all. A closure-parameterized
  declaration therefore reads as `Foo<(lambda:file.h#1)>` in the flat
  snapshot but `Foo<(lambda:file.h:20:5)>` in its own source-graph node, so any
  consumer trying to correlate the two (e.g. matching a flat finding back
  to its graph neighborhood) sees two different spellings for the same
  entity. **Not fixed here**: a correct fix needs the ordinal map
  `collect_anonymous_type_ordinals` computes from the flat fields to also
  be applied to every graph node id/name *and* every edge's `src`/`dst`
  reference to it -- and the source graph can name a closure the flat
  ABI-surface fields never mention at all (an internal-linkage helper
  visible only in the L5 graph), which the flat-only ordinal map has no
  entry for, so naively reusing it risks leaving some graph-only closures
  unrenumbered while their flat-visible siblings are. A correct fix likely
  needs the ordinal collection widened to scan the graph's own node/edge
  strings too, verified against a case that actually mixes flat-visible
  and graph-only closures in one header -- a real, cross-cutting change
  to two independently-evolving modules, not a same-PR reactive patch.

  **Same gap, also reachable from the load path (Codex review, fresh
  evidence).** `storage.snapshot_load_normalization.normalize_anonymous_
  type_spellings_on_load` (added to close a sibling bug: a raw pre-strip
  on-disk baseline was left completely unrenumbered on load, see this
  file's own git history) rewrites the identical flat
  `_LAMBDA_IDENTITY_FIELDS` only, called after `snapshot_from_dict` has
  already decoded a schema-v29+ document's `AbiSnapshot.surface_graph` --
  so a loaded raw-marker baseline's attached graph keeps its own
  un-stripped node/edge identities even once the flat fields are
  normalized. Not fixed here, for the same reason as above: it needs the
  same graph-aware widening this entry already calls for, not a second,
  independent patch on the load side.

- ~~Neither `scan`'s dry-run report validates `--abi3` applicability before
  reporting success~~ **Fixed (CLI cleanup phase two, PR 5 follow-up).**
  Both `frontends.cli.scan_dry_run.render_scan_dry_run` (single-binary) and
  `frontends.cli.artifact_set_dry_run.render_artifact_set_dry_run`
  (`--artifact-set`) now run a cheap, binary-only extension probe
  (`python_ext.detect_python_extension_from_binary` -- container-only ELF/
  PE/Mach-O read, no DWARF/AST parse, the same "binary export table parse"
  already priced under the L0_binary dry-run row) and route a non-qualifying
  candidate through `DryRunResult.block()` (exit 1), matching the real run's
  `EVIDENCE_CONTRACT_ERROR`. The message text is shared with
  `scan_engine._run_abi3_audit`'s real precondition failure via
  `python_ext.abi3_precondition_message()` so the three callers cannot
  independently drift. See `tests/test_scan_dry_run_abi3.py`.

- ~~A pinned depth backed only by a query-declaring `--config` (no
  `--sources`/`--build-info`) prices L3/L4/L5 at zero TUs/zero cost~~
  **Fixed (CLI cleanup phase two, PR 5 follow-up).** `_estimate_total_tus`
  gained a `query_only` branch: when `req.build_config` declares a real
  `build.query` and no `--sources`/`--build-info`/compile DB is given, the
  L3 note is flagged `[UNKNOWN: build.query declared, ...]` (the same
  "annotate honestly rather than fold a floor into the summed total" shape
  `_UNSCOPED_TU_NOTE_SUFFIX` already uses for the sibling `--build-target`
  undercount case) instead of the confident-looking `0` every other
  "nothing given" case reports. `_source_layer_estimates` carries the same
  marker onto the derived `L4_source_abi`/`L5_source_graph` notes too
  (Codex review, fresh
  evidence: an earlier revision of this fix only flagged L3's own row, so
  `--depth source`/`--depth graph` still priced the derived layers as a
  confident zero), and `estimate_artifact_set`'s 4th return value
  (`unknown_layers`) lets the `--artifact-set` aggregate renderer apply the
  same per-layer treatment rather than a single project-wide flag hardcoded
  to `L3_build`. Reaches both dry-run paths uniformly, since both call
  `estimate_scan()`. See
  `tests/test_scan_dry_run_abi3.py::test_estimate_total_tus_query_only_config_marks_count_unknown`
  and its `test_estimate_scan_propagates_unknown_tu_count_to_l4_and_l5`/
  `test_estimate_artifact_set_reports_unknown_layers_per_layer` siblings.

- **Two function/method template overloads distinguished only by a
  `requires`-clause still collide under ADR-063 Phase 2's `EntityId`
  discriminator (Codex review, PR #943, fresh evidence).**
  `template<class T> requires C1<T> void f();` and the same declaration
  constrained by `C2<T>` instead share scope, leaf name, an identical
  ordinary parameter list, and an identical `function_template_param_kinds`
  result (`("type",)`), so they collapse onto one `EntityId` even after
  the parameter-kind/packness/dependent-rename fixes landed for this
  discriminator. Confirmed by direct compilation that clang's own
  `ConceptSpecializationExpr` node (a `FunctionTemplateDecl` child
  appearing right after the constrained `TemplateTypeParmDecl`) carries no
  concept name or resolvable reference to one anywhere in its own JSON
  subtree -- every key on the node and its
  `ImplicitConceptSpecializationDecl` child was inspected directly, and
  neither carries anything but synthetic AST ids and dependent-type
  placeholders (`type-parameter-0-0`). **Not fixed**: recovering the
  concept's actual name would need either a different clang AST-dump
  mode/flag or the raw header source text sliced at the node's own
  `range` offsets, and `_ClangAstParser` (`abicheck/dumper_clang.py`)
  deliberately consumes only an already-parsed JSON tree with no source
  text available to it -- a fragile source-offset hack was rejected rather
  than attempted. A correct fix needs either threading the header's raw
  source text through to this parser (a larger architectural change
  outside this discriminator's own scope) or a clang invocation change
  that emits a concept reference here, verified against a real build
  before landing either way. See
  `docs/contribute/plans/one-semantic-pipeline.md`'s Phase 2 section for
  the full investigation.

- **An out-of-line member (function or static data member) template
  definition gets a different `EntityId` scope than its in-class
  declaration, colliding one entity into two (Codex review, PR #943,
  fresh evidence).** `struct A { template<class T> void f(T); }; template
  <class T> void A::f(T) {}` -- confirmed by direct compilation
  (`clang -Xclang -ast-dump=json`) that clang emits TWO
  `FunctionTemplateDecl` nodes for `f`: one lexically nested inside `A`'s
  own `CXXRecordDecl` (the in-class declaration), and one at the
  ENCLOSING namespace's own lexical level (a sibling of `struct A`, not a
  child of it) carrying a `parentDeclContextId` pointing back at `A`'s own
  node id -- clang's own signal for "this out-of-line definition's real
  semantic owner is `A`, even though it isn't lexically nested inside
  it." `_ClangAstParser._walk` computes both `scope`/`scope_path` purely
  from LEXICAL nesting, with no `parentDeclContextId` handling anywhere in
  this codebase, so the out-of-line definition gets `scope=()` while the
  in-class declaration gets `scope=(Record("A"),)` -- `parse_functions`
  parses BOTH nodes into separate `Function` entries with disagreeing
  `EntityId`s for what is really one entity. The review further confirmed
  `parse_variables` has the analogous gap for an out-of-line class-template
  static data member definition. **Not fixed**: unlike this phase's other
  fixes (each a small, local addition to one already-threaded parameter),
  closing this properly needs a NEW general-purpose facility this codebase
  doesn't have yet -- a typed, `ScopePath`-valued sibling of the existing
  `dumper_clang_expr._index_decl_id_qualified_names` (which already indexes
  every decl id to a FLAT qualified-name string for a different consumer,
  but a flat string cannot be losslessly converted back into a typed
  `ScopePath` -- collapsing `Record`-vs-`Namespace`-vs-`InlineNamespace`
  is exactly the ambiguity `ScopePath` was built to prevent). That index
  would need building once per parse, threading through both
  `parse_functions` and `parse_variables`, and reasoning through further
  edge cases this investigation did not exhaustively enumerate (an
  out-of-line member of a NESTED class, an out-of-line member of a
  class-template SPECIALIZATION, and whether castxml's own `context`
  resolution has the identical gap for parity). A correct fix needs that
  index plus verifying each edge case against a real compilation before
  landing, not a narrow patch for only the one reported shape.

- **`scan_abi3_resolve.py` is a new flat `workflows`-legacy root module
  whose own docstring says its placement exists specifically so
  `scripts/check_architecture.py`'s `unclassified-import` check won't
  inspect its dependency on `serialization.py` (Codex review, PR #951,
  fresh evidence).** The module needs both `python_ext`
  (`detect_python_extension_from_binary`) and `serialization`
  (`load_snapshot`, for the snapshot-input fallback) to answer `scan
  --dry-run --abi3`'s candidate-recognition question the same way the
  real run's `service.resolve_input` does; `serialization.py` already
  imports `python_ext` (for `PythonExtMetadata`/the `detect_python_extension`
  backfill), so `python_ext` importing `serialization` back would form a
  real two-module cycle, and a small module living outside both,
  importing each, is the correct general shape. Codex is right that
  keeping it flat rather than moving it under the migrated
  `abicheck/workflows/` package (alongside `scan_abi3_dry_run.py`, its
  only caller) sidesteps a check rather than satisfying it. **Investigated
  the real fix and it doesn't fit in this PR**: `serialization.py`
  matches `storage`'s own described responsibility (AGENTS.md's module
  map: "snapshot serialization ... the public compatibility surface"),
  so classifying it under `architecture/modules.yaml`'s `storage` layer
  looks like the natural move -- but `storage`'s `may_import` is
  `["model"]` only, and that reclassification immediately surfaces two
  already-latent violations a purely-unclassified `serialization.py`
  currently hides from `check_architecture.py`'s `dependency-direction`
  check entirely (an unclassified import target is skipped by that check,
  by design): `abicheck/probe_harness.py` (classified `compare`, whose own
  `may_import` is `["model"]` only) already calls
  `serialization.snapshot_to_dict`/`snapshot_from_dict` at runtime, and
  `serialization.py` itself imports `python_ext` (classified `extract`)
  at runtime for the same reason `scan_abi3_resolve.py` does. Unlike
  `check_ai_readiness.py`'s `import-cycle-growth` check,
  `dependency-direction` has no allowlist mechanism to grandfather either
  violation while the reclassification lands. **Both fixed.**
  `serialization.py`'s own `python_ext` coupling closed first (ADR-061 gap
  E closure package 5, slice 1, 2026-09-07): the `detect_python_extension()`
  backfill moved out of `serialization.py` into
  `abicheck/workflows/snapshot_load.py::backfill_python_ext_from_evidence()`,
  called as an explicit post-load step -- `serialization.py` no longer
  imports `python_ext` at all (it imports `workflows.snapshot_load`
  instead, and `python_ext_from_dict` from `.model`, a plain dataclass
  parser, not the `extract`-classified `python_ext.py`). The
  `probe_harness.py` half closed second (slice 2, 2026-09-08): its
  snapshot round-trip (`.to_dict()`/`.to_json()`/`.from_dict()`,
  `write_matrix_snapshot`/`load_matrix_snapshot`) moved out of
  `probe_harness.py` entirely into `abicheck/workflows/findings.py`, which
  may legitimately import both `compare` (for the dataclasses) and
  `serialization`. `probe_harness.py` no longer calls
  `serialization.snapshot_to_dict`/`snapshot_from_dict` at all. Neither
  latent violation this entry named is still open. `serialization.py`'s own
  ~1500-line `snapshot_to_dict`/`snapshot_from_dict` codec proper has since
  been given a real owner too (ADR-061 gap E closure package 6): it moved to
  `storage/snapshot_codec.py` and four siblings
  (`snapshot_schema_versions.py`, `snapshot_encode.py`,
  `snapshot_decode_declarations.py`, `snapshot_reliability_flags.py`), each
  kept under the ADR-061 new-file production line ceiling since a brand-new
  file gets no adoption-debt baseline exemption. `serialization.py` itself
  is confirmed (not just documented) to stay `public_root_surfaces`-listed
  permanently, for the same "no single layer" reason ADR-061 gap B's own
  closure status already established for `checker_policy`/`contract_gating`/
  `reclassify`: it is the one legal route through which
  `workflows.snapshot_load.backfill_python_ext_from_evidence` and
  `policy.analysis_assurance_degraded_facts.degraded_reliability_facts` run
  between `storage.snapshot_codec.decode_snapshot` and
  `storage.snapshot_codec.finalize_snapshot` -- neither call is legal from a
  `storage`-classified module (`may_import: [model]` only), and
  `dependency-direction` resolves a dynamic `importlib.import_module` call
  against a classified target the identical way it resolves a static import
  (only an *unclassified* target, like this facade, is skipped by design),
  so there is no bridge-module trick available for either. Verified against
  the full architecture gate (`scripts/check_architecture.py`) with zero new
  findings. Left `scan_abi3_resolve.py` in its current, self-documenting flat-legacy
  placement (its own docstring already states the reason and the
  precedent it follows) as accepted debt -- that placement was never about
  `serialization.py`'s own classification, so this closure does not change it.

- **[Superseded 2026-09-01 for `surface.py`'s own half — see the correction
  below; the node-id-namespace half is still accurate.] ADR-063 Phase 3
  (D5) lands the public-surface-as-graph-query infrastructure without
  migrating `surface.py`/`export_surface.py`'s own traversal algorithms
  onto it, and without unifying the new graph builder's node ids with the
  pre-existing L5 graph's — both deliberate, documented scope boundaries,
  not oversights.** `policy/public_surface.py`'s
  `PublicSurfaceQuery` delegates to `surface.compute_public_surface()`/
  `export_surface.compute_export_surface()` unchanged rather than
  reimplementing either as a literal graph traversal: both are exactly the
  kind of intricate, multi-round-corrected logic this same page's
  `_paired_stable_indices` incident (see "Primitive-level property tests"
  in `AGENTS.md`) shows costs several review rounds to get right even once
  already, and reimplementing one from scratch inside the same phase that
  also had to build every piece of graph infrastructure underneath it was
  judged materially higher-risk than landing the infrastructure now and
  migrating the algorithm as its own later, narrowly-scoped phase.
  Consequences: `compute_public_surface()`'s signature was never changed
  to accept a structured `resolution` parameter, so there is no lazy,
  graph-reading legacy-snapshot backfill path; `type_reachability.
  directly_referenced_stdlib_types()` was not migrated into
  `policy/public_surface.py` (doing so would reclassify `type_reachability.py`
  into the `policy` layer, which would introduce a genuine new
  `policy -> extract` architecture violation — that module imports two
  already-`extract`-classified siblings); and `compare/surface_graph.py`'s
  own node ids (`canonical_key(occurrence_id)`/`approx::`/`typedef::`
  fallbacks) are a namespace fully independent of `buildsource/
  header_graph.py`'s pre-existing L5 node ids (`decl://<identity>`/
  `type://<identity>`) — one shared `SourceGraphSummary` instance carries
  both builders' nodes (real, tested — `service_header_graph_attach.
  _attach_header_graph()`), but the two schemes do not dedup onto a common
  node for a declaration both builders see. Each of these is a real,
  separate follow-up migration, not silently-abandoned scope — see the
  implementation plan's Phase 3 "Landed"/D5 "Amendment" notes
  (`docs/contribute/plans/one-semantic-pipeline.md`; ADR-063 itself no
  longer carries a duplicated per-phase status block, removed by PR 0,
  2026-09-02) and
  `compare/surface_graph.py`'s/`policy/public_surface.py`'s own module
  docstrings for the exact reasoning each carries.

  **Correction (2026-09-01): the traversal-migration half of this entry's
  own title is now stale — the algorithm was migrated after all, just not
  in this phase's first landing.** A later round did reimplement
  `surface.py`'s closure walk as a real traversal rather than leaving it
  in place: `_index_surface_types`/`_seed_public_roots`/
  `_walk_type_closure`/`_walk_exact_type_closure`/`_record_exact_identities`/
  `_record_nested_in_known_record`/`_record_is_confirmed_public_seed` and
  the `PublicSurface` type moved to `policy/public_surface.py` (dataclass +
  indexing) and `policy/public_surface_closure.py` (the walk itself, plus
  `resolve_public_surface()`), and `surface.py`'s own copies were
  **deleted**, not kept alongside — `surface.compute_public_surface()` is
  now a thin re-exporting wrapper. `export_surface.py`'s own root-seeding
  stayed in place, but its final type-closure step now calls the same
  migrated `_walk_type_closure` the header domain uses, so that domain
  became graph-native for free. This is the risk the paragraph above
  named and chose to defer, not a different fix — it just didn't stay
  deferred through the whole phase. The next two bullets below give the
  full, three-review-round account of what that migration actually needed
  to get right (and what it does *not* touch — `snap.surface_graph`/
  `GraphNode.attrs`, in the design that finally shipped). What is **still**
  correctly described by the paragraph above, unchanged: `export_surface.py`'s
  own root-seeding logic, `type_reachability.directly_referenced_stdlib_types()`
  staying unmigrated (same `policy -> extract` reason), and the two node-id
  namespaces not deduping onto one node. See the implementation plan's
  Phase 3 "Landed"/D5 "Amendment" notes for Phase 3's final accounting —
  ADR-063 itself no longer carries this per-phase detail.

- **ADR-063 Phase 3 (D5)'s traversal migration went through three review
  rounds before landing on a design that reads `AbiSnapshot.surface_graph`
  never at all for the closure walk — the history is worth keeping because
  each round's fix created the next round's bug.** Round 1 (the original
  migration): `policy/public_surface_closure.py` read a graph node's
  `referenced_identifiers`/`identifiers_collision` attrs, stamped once at
  graph-build time by `compare/surface_graph.py`. Round 2 (Codex, PR #979):
  `snap.surface_graph` being non-`None` does not mean its nodes carry those
  attrs at all — `service_header_graph_attach._attach_header_graph` installs
  an L5 graph on essentially every real dump without ever populating them —
  so trusting an attrs-less node as "references nothing" silently collapsed
  the transitive closure on the *ordinary, default* dump path. The fix
  (`resolve_surface_graph_nodes()` unconditionally calling
  `build_public_surface_facts()` to enrich/backfill the graph before
  reading it) introduced two further problems of its own: (a) a genuine,
  measured 30-100%+ performance regression against `scripts/
  benchmark_scaling.py`'s "Baseline regression (PR vs base)" CI gate,
  because `checker.compare()`'s default `scope_to_public_surface=True`
  calls this path twice per compare (once per side) and building real
  `GraphNode`/`GraphFact` objects through the ADR-046 evidence-merge
  machinery for every declaration is meaningfully more expensive per-call
  than the deleted regex-based re-parse it replaced; and (b) Round 3
  (Codex, second security-focused round): even *with* enrichment, a
  schema-v29 (or otherwise untrusted/adversarial) snapshot could carry a
  stale or crafted `referenced_identifiers` fact at a confidence this
  module's own freshly-registered fact (always `CONF_UNKNOWN`, the lowest
  rank in `model.graph_vocabulary._CONFIDENCE_RANK`) cannot outrank —
  `model.graph_facts.merge_graph_facts`'s per-key precedence would let the
  stale/poisoned value silently win over the correct, current one,
  reproducing the exact same collapsed-closure failure mode as round 2,
  just reachable through the round-2 fix instead of around it. An
  identity-keyed cache was also tried, purely to close the perf
  regression from (a), and reverted separately: it broke
  `tests/test_export_surface.py::TestUnresolvedTypeEdges::
  test_a_scope_lost_alias_key_is_followed_to_its_target`, which mutates a
  snapshot's `typedefs`/`types` in place between two calls and correctly
  expects the second to see the new content.

  **Fixed, for real, by removing the graph from this computation
  entirely** rather than trying to make trusting it safe. Both the
  attrs-staleness hazard (round 2) and the evidence-merge-precedence
  hazard (round 3) share one root cause: trusting anything cached on the
  shared, evidence-mergeable graph for a value that has exactly one
  legitimate source (the snapshot's own current declarations) and no
  legitimate second producer to reconcile evidence with. Once that was
  understood, the fix stopped being about merge precedence or caching at
  all: `compare/surface_graph.py`'s own `referenced_identifiers_by_node()`
  (renamed public, alongside its `ReferencedIdentifiers` return type) was
  already a pure function of the snapshot's declarations, computed
  *before* any `GraphNode` is even built — `policy/
  public_surface_closure.py` and `export_surface.py`'s closure-walk entry
  points now call it directly and thread the result through
  (`_referenced_identifiers`/`_node_identifiers_or_collision`/
  `_seed_public_roots`/`_walk_type_closure`/`_walk_exact_type_closure` all
  take a `ReferencedIdentifiers` now, not a `dict[str, GraphNode]`), never
  touching `snap.surface_graph` or `GraphNode.attrs` at all.
  `resolve_surface_graph_nodes()` (the round-2 enrichment function) had no
  remaining caller once both call sites switched, and was deleted rather
  than kept as unused surface. This closes the security concern outright
  (nothing is ever merged, so there is no precedence for an adversarial
  fact to win) and, as a direct consequence, removes essentially all of
  the `GraphNode`/`GraphFact`/evidence-merge construction cost from the
  hot path too — an ad hoc local re-run of `scripts/benchmark_scaling.py`
  after this fix showed `add_remove@2000` and `type_churn@1000` (two of
  the scenarios the perf gate had flagged) back in line with or better
  than the pre-migration baseline numbers quoted in the gate's own
  failure output. **Confirmed by CI itself, not just the local re-run**:
  the `Performance` workflow's own "Baseline regression (PR vs base)" job
  (PR #979, commit `5544540`) completed with `conclusion: success` — the
  gate's own noise-controlled PR-vs-base measurement, not an ad hoc local
  timing, so this entry is resolved rather than an open gap. Left in this
  history for the multi-round record: three review rounds on one commit
  chain, each fix closing the previous round's hazard while (in round 2's
  case) introducing this one.

- **`action/run.sh`'s `extra-args` parsing performs pathname expansion
  (globbing), not just word-splitting, and no site disables it — investigated,
  deliberately not fixed (CodeRabbit review, PR #998, ADR-064's
  effective-format-override fix).** `_effective_format()` (added by that PR),
  `_extra_args_has_write_flag()`, `_extra_args_write_json_path()`, and the
  real command assembly (`CMD+=($INPUT_EXTRA_ARGS)`) all read
  `$INPUT_EXTRA_ARGS` via an unquoted `set --`/array-append expansion, which
  bash expands for both word-splitting AND filesystem globs. A crafted
  `extra-args: '*'` (or any value containing a bare `*`/`?`/`[...]`) run in a
  workspace that happens to contain a file whose name looks like a CLI flag
  (e.g. `--format=json`) would have that filename silently substituted in as
  a real argument -- an unintended, workspace-content-dependent flag
  injection. `add_flag`'s sibling `_split_legacy_value` already hardens
  against exactly this class (`set -f`, Codex/report finding P2.2), so the
  precedent for fixing it exists.
  **Not fixed here**, for a reason specific to this PR: `_effective_format()`
  exists only to predict, from `$INPUT_EXTRA_ARGS`, what `--format` value the
  real `CMD+=($INPUT_EXTRA_ARGS)` expansion will actually produce -- so it
  reads that variable the *same* (unsafe) way on purpose. Disabling globbing
  in `_effective_format()` alone while leaving `CMD` assembly unprotected
  would not close the vulnerability (the real invocation would still glob)
  and would *introduce* a new divergence between what this detection
  function predicts and what Click actually receives -- worse than today's
  status quo of "both glob identically, so they can't disagree." Closing
  this properly means hardening all four sites together in one coordinated
  change (`CMD` assembly, `_effective_format`, `_extra_args_has_write_flag`,
  `_extra_args_write_json_path`), verified against a hostile-glob test
  corpus the way `test_action_run_sh_helpers.py`'s
  `TestAddFlagHostileScalarCorpus` already exists for `add_flag`/
  `add_sided_flag` -- a scoped, standalone follow-up, not a drive-by change
  bundled into a PR whose actual objective was the effective-format fix
  itself.

- **`action/run.sh` has no "effective output path" counterpart to
  `_effective_format` — investigated, deliberately not fixed (Codex review,
  PR #998, fresh evidence).** `extra-args` supplying its own `-o`/`--output`
  (`abicheck/cli_options.py`'s `-o/--output`) is a different flag than
  `--format`, and Click's last-flag-wins rule applies to it exactly the same
  way: an `extra-args: -o report.json` on top of an Action run with no
  `output-file:` input configured really does write the primary report to
  `report.json` on disk instead of stdout — but `$OUTPUT_FILE` (this
  script's own tracking variable, sourced only from `INPUT_OUTPUT_FILE`)
  never learns about it, so `_json_report_src` finds nothing: not
  `$OUTPUT_FILE` (empty), not `$_STDOUT_JSON_FILE` (nothing on stdout, since
  `-o` redirected it), not `$PR_JSON` (only populated when this script's own
  injection fires). A scan or compare that exits non-zero this way (e.g.
  `EVIDENCE_CONTRACT_ERROR`) publishes the generic `ERROR` instead of the
  real, more specific verdict its own report on disk could have named.
  **Not fixed here**, for the same "coordinated primitive, not a narrow
  patch" reason as the pathname-expansion gap above: `--write` already has
  its own effective-value recovery (`_extra_args_write_json_path`), but
  `-o`/`--output` has none, and building one properly means giving it the
  same freshness/fingerprint discipline `_json_report_src` already applies
  to `$OUTPUT_FILE` (a pre-existing file at the extra-args path must not be
  trusted as this run's own output) — a new `_effective_output_file` helper
  and its own test suite, not a one-line change to a single call site.

- **`BundleFacts` (and its G40 archive container) has no published JSON
  Schema, in either `abicheck/schemas/` or `docs/reference/schemas/`** —
  investigated, not fixed (Codex review, CLI cleanup phase two's PR I
  "artifact_type discriminator" prerequisite). That PR's own plan text
  states the ordinary "Merge criteria" machine-contract obligations
  (packaged *and* documented schema copies, JSON Schema validation) apply
  when a manifest changes, and the bump `BUNDLE_FACTS_SCHEMA_VERSION` (in
  `abicheck/bundle_facts.py`) got for the new `artifact_type`/
  `BUNDLE_ARCHIVE_ARTIFACT_TYPE` markers is exactly that kind of change —
  but a repo-wide search confirms neither
  container has ever had a schema file: `abicheck/schemas/` covers
  `compare_report`, `aggregate_report`, `build_evidence`, and
  `build_source_pack` only, and `docs/reference/schemas/v1/` mirrors that
  same set. This is a pre-existing gap predating this PR (the container
  has existed since G38 Phase 2 with no schema at any version), not one
  this PR's own diff introduced or made worse. Not fixed here because
  authoring a first JSON Schema for a format with no existing schema
  infrastructure (`scripts/publish_schemas.py`'s packaged/documented-copy
  machinery, plus real validation tests) is a substantial, separate
  deliverable — not a narrow addition to a field-and-classifier PR — and
  because `BundleFacts`' own shape is still scheduled to change again
  shortly: PR I's own `BundleCompareRequest` unification may still touch
  this container's fields before the format truly stabilizes, and
  authoring a schema now only to revise it again for that landing would
  be wasted work on the exact same axis. **Correction (2026-09-04,
  Codex review, PR #1050): `GateOptions` itself is no longer a blocker
  here** — ADR-064's own dedicated slice landed it 2026-09-02
  (`abicheck/policy/release_gate_options.py`); PR I's own
  `BundleCompareRequest` unification simply hasn't landed yet, for
  reasons unrelated to `GateOptions`. Tracked here rather than deferred silently;
  the schema-authoring work belongs with (or immediately after) whichever
  PR actually stabilizes `BundleFacts`' shape at its current
  `BUNDLE_FACTS_SCHEMA_VERSION` (`abicheck/bundle_facts.py`) -- the
  `BundleCompareRequest` PR itself, or a dedicated follow-up if that PR's
  own scope doesn't naturally include it.

- **The weekly `Mutation testing` scheduled lane
  (`.github/workflows/mutation.yml`, job `mutmut (detector core)`) can
  outgrow its own job timeout before producing a receipt — investigated,
  partially mitigated, not fully fixed.** The job's `timeout-minutes` was originally set to 240
  with a comment recording that a full baseline run "has taken just over
  two hours" at the time (2x headroom). `only_mutate`
  (`pyproject.toml`'s `[tool.mutmut]`) has grown since — the module map's
  own note under "Test-quality gates (beyond line coverage)" in `AGENTS.md`
  records it "now covers identity, suppression and serialization alongside
  diff_*/checker_policy" — and the scheduled run on 2026-08-31 (job
  `99506019384`) ran the full 240 minutes and was cancelled by that exact
  timeout without producing a `mutation-receipt.json`: its own "Run
  mutation testing (baseline drift)" step shows starting, then nothing in
  the job log until GitHub kills it at the wall-clock ceiling. The three
  most recent weekly runs before that (2026-08-17, 08-24, 08-31) all ended
  `cancelled` this same way, meaning the per-module baseline-drift gate
  had not actually completed a real weekly comparison in that whole
  window — a silent gap in exactly the class of coverage `AGENTS.md`'s
  own "Mutation testing" section describes as this repo's strongest
  test-quality signal, though not a *silent* failure on GitHub's own Actions
  tab: the workflow's existing "Flag a cancelled or incomplete run" step
  already turns a cancellation into a loud step-summary warning, it just
  doesn't make the run finish.
  **Mitigated, not closed:** raised `timeout-minutes` to 355 — effectively
  GitHub-hosted runners' own hard 360-minute per-job ceiling (not something
  a workflow can raise higher), minus a few minutes of buffer for this
  job's surrounding checkout/install/save/upload steps. This gives roughly
  50% more headroom than the run that was observed failing, but there is no
  measurement confirming a full run now completes within it — reproducing
  that would mean deliberately running (and waiting out) another multi-hour
  scheduled job, which wasn't done here. `mutmut`'s own `max_children`
  already defaults to `os.cpu_count()` (parallel mutant execution is not a
  missing lever), so if 355 minutes still isn't enough, the real fix is
  scoping `only_mutate` down or splitting the weekly run across multiple
  scheduled invocations (e.g. half the module list per run, in rotation)
  rather than requesting a runner tier this repo has not established it has
  access to. Recorded here rather than claimed fixed, per this repository's
  own "generalize, or record the gap" convention — this was a direct
  timeout-value bump for an observed cancellation, not a verified capacity
  fix.

- **`--depth` is a floor for live extraction, not a ceiling for a pre-built
  snapshot — real, cross-cutting, and previously undocumented outside one
  function's own docstring. Fixed (ADR-063 Phase 8 follow-up): the
  comparison-time projection this entry's own "real fix" paragraph called
  for is now `abicheck.policy.depth_projection.project_snapshot_to_depth`,
  applied by `classify_compare_pair` right after `enforce_requested_depth`
  confirms the floor.** `enforce_requested_depth`
  (`workflows/artifact/execute.py`) already fails a run when the *resolved*
  evidence falls short of an explicit `--depth`, and its own docstring has
  long carried this note: "this is a floor, not a ceiling. An input that is
  an already-serialized JSON snapshot with richer embedded evidence than
  `depth` requested still carries all of it — `resolve_input`'s `fmt ==
  "json"` branch returns `load_snapshot(path)` verbatim... which `--depth`
  has never projected down for a pre-built snapshot either." A Codex review
  round on PR #1016 (D1: accepting `--depth binary` for a directory/package
  compare) reproduced this concretely and flagged it as if newly
  introduced: `compare old_dir new_dir --depth binary` over a directory of
  saved JSON snapshots (rather than live binaries) still emits real
  header-derived findings and can still publish `BREAKING`, because nothing
  strips a snapshot's already-embedded evidence down to what was requested.
  Checked and confirmed **not** a regression from that PR — a *single-pair*
  `compare old.json new.json --depth binary` over two plain snapshots
  reproduces the identical behavior today, unrelated to any directory/
  package handling; PR #1016 only extended `--depth binary`'s
  *acceptance* to a second operand shape that inherits a limitation the
  single-pair path has always had. `cli_compare_options.
  _resolve_depth_for_set_inputs` (named `_reject_depth_for_set_inputs`
  until its per-rung allow-list was deleted -- see "A depth shortfall is a
  hard per-member `ERROR` ..." below) cross-references this
  entry so the directory/package path states the same acknowledged
  limitation explicitly rather than silently inheriting an undocumented
  one.
  **The two obvious-looking fixes below were, and remain, correctly
  rejected — the eventual fix is neither of them, kept here so both stay
  un-reattempted:** stripping a resolved snapshot's higher-level facts down
  to the requested depth *before* comparing would work for this one call
  site, but would also discard evidence a caller legitimately wants to keep
  on a snapshot that gets reused for a *later* comparison at a higher depth
  — `--depth` is meant to gate what a comparison *uses*, not to mutate a
  snapshot's own persisted content. Rejecting `--depth binary` outright for
  any operand backed by a pre-built snapshot (matching the pre-#1016
  directory/package behavior) would reintroduce exactly the asymmetry D1
  closed, since the single-pair path already accepted and silently
  under-enforced the same combination.

  **The fix actually shipped is exactly the comparison-time projection this
  entry called for**: resolve the snapshot as before, then filter what
  `checker.compare()` is allowed to see down to the requested rung, keeping
  the resolved `AbiSnapshot` itself untouched.
  `abicheck.policy.depth_projection.project_snapshot_to_depth` is that
  filter — pure (returns a deep copy), mapped onto the public
  `binary`/`headers`/`build`/`source` ladder via the exact same rank table
  (`evidence_depth.DEPTH_RANK`) this entry's own "applied the other
  direction" phrase named, and validated against the one prior
  already-trusted reference implementation of this idea:
  `scripts/check_tier_accuracy.py`'s `project()`, which the per-tier
  accuracy gate runs against a real (if synthetic) labelled corpus.
  `classify_compare_pair` (`service_compare_pipeline.py`) applies it (via
  `project_pair_to_depth`) to a local `old`/`new` view, gated on
  `request.depth is not None` — the same "no explicit depth, no effect"
  contract `enforce_requested_depth` already has — right after that
  function confirms the floor; `pair.old`/`pair.new` themselves are never
  mutated, so a caller reading the unprojected snapshot elsewhere is
  unaffected. `dump` deliberately does **not** apply this at write time,
  for the exact reason given above (it would discard evidence a later,
  deeper comparison might want) — `--depth` on `dump` stays floor-only,
  unchanged. See `project_snapshot_to_depth`'s own docstring for the
  precise field-by-field scope (visibility/origin scoping, macro/constexpr
  constants, the Python-API stub surface, the header-AST `SemanticIR`, the
  header-only `surface_graph`, `build_mode`, the `BuildSourcePack`
  L3/L4/L5 split; below `headers` with no DWARF backing (`dwarf.has_dwarf`
  false), `types`/`enums`/`typedefs`/signatures are fully stripped too,
  not merely re-scoped — matching a real DWARF-less binary-only dump
  exactly, per `extract/export_symbol_identity.py`'s own production
  behavior).

  **A second review round (Codex, same PR) found two more real gaps in the
  first cut of this fix, both now closed:** (1) the projection was wired
  only into `classify_compare_pair` — the typed-API/release-fan-out
  chokepoint — while the *native* `abicheck compare` CLI
  (`cli_compare_helpers.run_compare`) and `scan --against`'s baseline path
  (`cli_scan_baseline._run_baseline_compare`) each call
  `compare_snapshots()` directly and never saw it; both now apply
  `project_pair_to_depth` themselves, right after resolving their own
  pair, mirroring `classify_compare_pair`'s placement. (2) the `binary`
  rung's structural-fact handling was fixed at DWARF-informed (L1)
  behavior unconditionally — a purely header-derived snapshot with no
  DWARF at all still leaked full `types`/`enums`/function-signature data
  through a `binary`-depth projection; `_snapshot_has_native_debug_info`
  now picks L0 (fully stripped) vs. L1 (structural facts kept) per
  snapshot, not fixed to one or the other.

  **A third review round (Codex, same PR) found four more real gaps, all
  now closed:** (1) an explicit out-of-band `--old/new-sources`/
  `--old/new-build-info` pack directory is resolved *separately* from the
  snapshot object `project_pair_to_depth` projects — `cli_compare_helpers.
  run_compare` passed the raw pack paths straight through to
  `prepare_embedded_build_source`, which reloads and diffs them
  unconditionally, so a pack-backed `compare --sources ... --depth binary`
  still leaked full L3-L5 findings even after the first two rounds' fixes.
  Closed with a new `project_build_source_pack_to_depth` (mirroring
  `project_snapshot_to_depth`'s own `build_source` capping, factored into a
  shared `_project_build_source_pack` helper both call) — `run_compare` now
  resolves each side's pack itself, caps it, attaches the capped pack back
  onto `old.build_source`/`new.build_source`, and passes `None` for the
  four path arguments so `prepare_embedded_build_source`'s own
  `resolve_side_pack` falls back to the now-capped embedded payload instead
  of reloading the raw one; the same fix also corrected the *reporting*
  side (`analysis_assurance`/`old_evidence_depth`/`new_evidence_depth`),
  which previously re-resolved the uncapped pack from the raw paths a
  second time for these fields even after the findings themselves were
  capped. (2) `snap.contract` (an ADR-050 `ExtractionContract`) survived a
  `binary`-depth projection, so `checker.compare()` could still raise a
  scope/profile mismatch error from two sides' *original* header scopes
  even though a binary-only comparison never looks at either — now cleared
  alongside the other L2+ facts. (3) a `Visibility.HIDDEN` (non-exported)
  function/variable was promoted to `ELF_ONLY` like every other function/
  variable, manufacturing a false `*_removed_elf_only` finding for a
  declaration no real binary-only dump would ever have seen as a symbol at
  all — now dropped from the projected snapshot entirely instead. (4)
  `types`/`enums` were kept or dropped by the whole-snapshot
  `dwarf.has_dwarf` flag, the same coarse signal functions/variables still
  use — but this codebase's model *does* carry real per-record DWARF
  evidence for these two families (`DwarfMetadata.structs`/`.enums`, keyed
  by name), so an uninstantiated header-only record sitting alongside
  unrelated real DWARF content was incorrectly retained; a new
  `_dwarf_confirmed_names` filtered `types`/`enums` per declaration name
  instead of by the whole-snapshot flag. **Superseded by the fourth review
  round below** — the per-record name check turned out to be one level too
  narrow.

  **A fourth review round (Codex, same PR) found three more real gaps, all
  now closed:** (1) the third round's per-record `_dwarf_confirmed_names`
  fix retained a *header-derived* `RecordType`/`EnumType` wholesale
  whenever DWARF merely confirmed the same-named struct/enum *existed* —
  DWARF confirming a struct's name says nothing about whether its *fields*
  agree with the header's own spelling (DWARF only ever backfills numeric
  *layout* onto a header-derived record — `dumper_layout_backfill.py`'s
  own scope — never its field-level type text), so a header-only field-type
  change could still leak through a `binary`-depth projection whenever an
  unrelated real DWARF struct happened to share the changed struct's name.
  Fixed by replacing the per-record name check with a whole-snapshot
  `not snap.from_headers` requirement (`_structural_facts_are_dwarf_
  confirmed`, replacing `_dwarf_confirmed_names`/`_snapshot_has_native_
  debug_info`): `AbiSnapshot.from_headers`'s own field comment states the
  real distinction precisely — "DWARF-derived declarations populate the
  SAME functions/types lists [as header-derived ones] but must NOT be
  mistaken for header-level evidence." Only a genuinely DWARF/symbols-only
  dump (`from_headers` is `False`, where `dwarf_snapshot.py`'s own
  DWARF-only extraction path populates `types`/`enums`/`typedefs`/
  function-variable signatures directly from DWARF DIEs) may now keep
  those fields wholesale; a header-derived snapshot always clears them at
  `binary` depth, same as the no-DWARF-at-all case. A real DWARF-visible
  struct/enum layout change on a header-derived snapshot is still caught
  independently, through the untouched `snap.dwarf.structs`/`.enums`
  fields via `diff_platform._diff_dwarf` (which this module never clears,
  and degrades gracefully with no header model at all — "If the header
  model is absent... fall back to comparing all DWARF types", that
  function's own comment) — function/variable signature changes have no
  equivalent independent DWARF-native detector, a real, separately-
  justified gap (it would need a new per-declaration confirmation fact the
  dumper does not currently record), not attempted here. (2) a function/
  variable promoted from a header parser's own "declared public, without
  contrary evidence" fallback (e.g. `dumper_castxml._variable_visibility`'s
  un-emitted customization-point-object case) was promoted to `ELF_ONLY`
  the same as a genuinely exported one, manufacturing a false
  `*_removed`/`*_removed_elf_only` finding for a declaration no real
  binary-only dump would ever have seen as a symbol at all. Fixed with a
  new `_exported_symbol_names` (a small, local copy of the same "raw
  export table" read this codebase's `cross_source_checks_base.py`/
  `snapshot_exports.py`/`post_manifest.py`/`diff_unnamed_types.py` each
  already keep their own independent copy of, since a `policy`-layer
  caller may import `model`/`compare` but not `extract`, ADR-061, where
  most of those live): a surviving function/variable must now also appear
  in the snapshot's own raw ELF/PE/Mach-O export table before promotion to
  `ELF_ONLY`; skipped (falls back to the prior, looser behavior) when no
  platform table was parsed at all, so a synthetic/incomplete snapshot
  isn't stripped to nothing. (3) nulling a `BuildSourcePack`'s
  `source_abi`/`source_graph` between `build` and `source` left their own
  `LayerCoverage` rows in `pack.manifest.coverage` still claiming
  `PRESENT`/`PARTIAL` — `evidence_report.optional_coverage()` returns
  those rows directly, so a report could still claim source-ABI/
  source-graph evidence backed a comparison the depth ceiling actually
  excluded it from. Fixed with `_mark_layers_not_collected`, demoting the
  L4/L5 coverage rows to `NOT_COLLECTED` in the same place the payload
  fields themselves are cleared.

### The composite Action's single `--write` slot can leave its unconditional coverage/assurance/severity floors without a structured report

**Superseded** (ADR-063 Phase 6, Track T8): the SARIF-fallback/HTML-gap
account this entry used to carry is retired along with the rendered-prose
readers it described. `_report_compat_verdict`/`_severity_gate_exit`/
`_coverage_gated`/`_assurance_gated` no longer have a "rendered markdown/
text" fallback tier at all (`_text_report_content` is deleted), nor a
SARIF `runs[0].properties.abiVerdict` branch — every one of them is
JSON-only now (`run_outcome`, then the legacy per-field JSON, then
nothing), per `action/AGENTS.md`'s "How `run.sh` resolves the verdict it
publishes". A `format: html` primary was never coherent evidence for this
group of readers, so its own absence is no longer a special case either.

What remains, narrower than before: the CLI's `--write` option is still
single-valued (`secondary_output.py`'s `--write FORMAT=PATH`, not
`multiple=True`), and `scan`/`compare`'s own internal `--write
json=$PR_JSON` sidecar injection (unconditional since Track T8 — no longer
gated on `pr-comment`, closing the gap this same track's own Codex review
found in the `pr-comment: false` case) is still skipped whenever the
step's own `extra-args` already supplies a `--write` of any format
(`_extra_args_has_write_flag`), since `extra-args` is appended after the
injection and Click keeps only the last occurrence of a repeated
non-multiple option — a second, JSON-targeted `--write` this script
appended would always lose to the user's own later one and never execute.
When that user-supplied `--write` targets `json=`, `_extra_args_write_json_
path` already recovers it, so `_json_report_src` still finds structured
evidence. When it targets a **non-JSON** format (`-o text=...`,
`-o markdown=...`, ...), there is no JSON anywhere, and ADR-049's
unconditional contract-coverage/analysis-assurance floors plus the
severity-category gate genuinely go blind for that one run — accepted, not
fixed, per `action/AGENTS.md`'s own "Known, accepted limitation"
paragraph, since closing it would mean either silently discarding the
user's explicit `--write` choice or unconditionally re-running the
analysis a second time purely to obtain JSON (a materially larger,
separate trade-off `_maybe_post_pr_comment` only accepts today for the
sticky-comment feature, under its own narrower guards). If this becomes a
real reported problem rather than a review-found edge case, the honest fix
is the same one this entry always named: make `--write` accept multiple
`FORMAT=PATH` operands (a real CLI capability change touching
`secondary_output.py` and every command that declares the option, not
just this Action script) — not another per-reader special case.

### `bundle_facts_store.read_bundle_facts_package` holds a bundle's raw JSON and its decoded `AbiSnapshot`s concurrently at peak

A Codex review round on PR #1054 (the Track B/C duplicate-multi-artifact-
writer reconciliation) found that delegating `read_bundle_facts_package` to
`storage.import_bundle_facts.export_bundle_facts` regressed peak memory
behavior relative to the reader it replaced. The removed reader converted
each artifact's document to an `AbiSnapshot` immediately inside its
per-artifact loop (`per_library_snapshots[library_name] =
snapshot_from_dict(document)`), discarding the raw JSON document each
iteration; `export_bundle_facts` instead accumulates every artifact's raw
JSON document into one `per_library_snapshots: dict[str, Any]` and returns
it as a whole document, and `bundle_facts_from_dict()` then decodes every
entry in one pass — so at peak this can hold both the full set of raw JSON
document trees and (once decoding starts) their `AbiSnapshot` counterparts
concurrently.

Not fixed: closing this fully means changing `export_bundle_facts`'s own
return contract from "one complete `bundle_facts_from_dict()`-shaped
document" (its module docstring's stated contract, shared with
`storage/import_baseline_set.py`'s sibling adapter) to a streaming/
generator form the caller converts and discards from as it goes — a change
to the shared `storage.import_bundle_facts` module's public shape, not a
caller-local fix, and outside what that PR (reconciling the Track B/C
duplicate writers onto that one shape) was scoped to redesign. It is
bounded in the meantime, not unbounded: `read_bundle_facts_package`'s own
`DEFAULT_MAX_BUNDLE_DECODED_BYTES` check runs *before* `bundle_facts_from_dict`
is ever called (raising during the incremental per-artifact charge if the
raw documents alone already exceed the budget), so peak memory here is
capped at roughly the charged-byte budget's raw-JSON cost plus its decoded-
object cost — a bounded constant, not a growth path uncorrelated with any
limit this module already enforces. If this becomes a real reported
problem rather than a review-found edge case, the honest fix is reworking
`export_bundle_facts`/`import_baseline_set`'s shared adapter shape to
stream per-artifact results to the caller instead of returning one
document, which both known callers would need to be updated for together.

### Dependency static/dynamic linking-mode change has no ChangeKind

Found by Phase 2/3 of
[ABI/API knowledge and corpus](plans/abi-api-knowledge-and-corpus.md) —
the one taxonomy leaf (`dependency-abi.linking-mode-change`,
`docs/contribute/abi-api-failure-taxonomy.md`) that no `ChangeKind` claims
at any evidence tier, and therefore the only `NOT_IMPLEMENTED` row in
[the coverage matrix](abi-taxonomy-coverage.md).

When a dependency switches between static and dynamic linking, the
dependency's own symbols change status wholesale: previously resolved at
load time through `DT_NEEDED` (or the PE import table / Mach-O load
commands), they are now duplicated into the artifact itself, with their own
visibility, ODR exposure, and interposition behaviour changing with them —
and the reverse when a vendored dependency is unbundled. Nothing in the
registry names that transition. Its consequences are *partly* observable
through unrelated kinds — `needed_removed` when the dependency leaves the
needed list, `symbol_leaked_from_dependency_changed` /
`visibility_leak` when its symbols surface in the artifact's own export
table — so a real occurrence produces two or three findings that each
describe a symptom and none of which says what happened.

Not fixed here: Phase 2/3 of that plan is a mapping and classification
pass, and the plan's own "Out of scope" section rules out any detector,
evidence, or `ChangeKind` change (a `NOT_IMPLEMENTED` finding is recorded,
not fixed, by it). Tractable when it is picked up: both sides' needed/import
lists and export sets are already collected at L0, so the evidence a
detector would join is present today — what is missing is the join, a kind,
and the "vendored vs. unbundled" direction in its `impact` text. Note the
adjacent, deliberately *unclaimed* half: whether the newly-static
dependency's code is ABI-compatible with what consumers already linked is a
question about a third artifact abicheck was not given, which is
`dependency-abi.transitive-break`'s territory, not this leaf's.

### `compare --no-baseline` does not yet reproduce `scan`'s audit-mode findings

Found while migrating the Phase 4 documentation and corpora of
[`plans/one-comparison-product.md`](plans/one-comparison-product.md)
(ADR-068 D2, §3 row 2). `abicheck compare --no-baseline CANDIDATE` exists,
takes one operand, and correctly records the OLD side with ADR-065's
`declared_absent` acquisition state — but it does **not** report the
intra-version cross-source hygiene findings that are the entire reason the
single-build audit exists. It fails in two different ways depending on the
candidate's shape, both verified live against `main` at `fd6ba681`:

- **A stored `.abi.json` candidate crashes.** All eleven G20 audit fixtures
  (`catalog/cases/case14{3,4,5,6,7,8,9}_*`, `case15{0,1}_*`, `case181_*`,
  plus `case151`'s second `thin.abi.json` variant) abort with an unhandled
  `AssertionError` from `workflows/no_baseline_compare.py`'s
  `assert not diff.changes, "a snapshot compared against itself must never
  produce a change -- if this fires, a detector is reading non-identity
  state"`. `scan <same fixture>` reports its documented verdict and hygiene
  finding on every one of them.
- **A live binary plus `-H` renders an empty report.** Against
  `examples/workflows/audit-release`'s own built `libgreet.so`,
  `scan libgreet.so --header include` reports
  `crosscheck:exported_not_public present … undeclared_export=1` and
  `[warning] exported_not_public: 1`, while
  `compare --no-baseline libgreet.so --header include -o json=...`
  exits 0 with `"changes": []`, no `verdict`, and no cross-source or
  `pattern_preprocessor_scan` block in the document at all.

The root cause is structural, not a stray bug in one detector:
`run_no_baseline_compare` implements the audit as a *self-diff* (`_diff_pair(new,
new, …)`) and then asserts the diff is empty. That assertion was correct
when `compare()` had no per-side stages, but Phase 2a/2b moved all eleven
cross-source checks and the pattern/preprocessor pre-scan *into*
`compare()`, where they legitimately emit `persistent` findings on a
self-compared snapshot. So the audit path either trips its own invariant or,
where the checks stay dormant, yields a document with nothing in it — and
either way `--no-baseline` cannot express what ADR-068 D2 promises it
replaces.

Not fixed here: this documentation/corpora slice owns `docs/`, `examples/`,
`eval/`, `validation/`, `catalog/`, and `skills-src/` only, and the fix is
in `abicheck/workflows/no_baseline_compare.py` plus whatever
`report/`-side projection has to carry a one-sided finding set. Consequences
recorded rather than papered over: `docs/integration/scenarios/single-build-audit.md`,
`docs/use/evidence-depth.md`, and `docs/start/choose-your-workflow.md` all
still name `scan` as the working spelling for a no-baseline audit, and every
G20 audit case's README keeps its `abicheck scan` reproduce command with an
explicit "blocked on this gap" note rather than being re-driven onto
`compare --no-baseline` or quietly dropped from the catalog.

Tractable when picked up: replace the `assert not diff.changes` invariant
with an explicit partition — identity-diff findings (which genuinely must be
empty, and where the assertion's original intent still holds) versus
per-side `CrossSourceEvolution`/`pattern_preprocessor_scan` output (which
must survive into the rendered document, marked `persistent`, with no
addition/removal/compatibility verdict per D2). ADR-068 D3's
`not_evaluated` rule is the constraint on the second half: with OLD
`declared_absent` there is no baseline evidence, so nothing may ever be
reported as `introduced`. The parity harness (`tests/parity/`) already has
the scan-vs-compare shape needed to gate it, and the eleven fixtures above
are a ready-made acceptance corpus.

**Closed (2026-09-09).** Both failure shapes were reproduced live against
`main` at `ed4e70e3` before anything was changed — the stored-fixture crash
on `catalog/cases/case143_audit_accidental_export/snapshot.abi.json`, and
the empty document from `compare --no-baseline libgreet.so --header include`
against `examples/workflows/audit-release`'s own build — and both are fixed.
The write-up's suggested partition is what landed, plus one root cause it
did not name:

- **The partition.** `abicheck/policy/no_baseline_findings.py` (new) owns
  it: `partition_no_baseline_findings` splits `diff.changes` on
  `Change.cross_source_evolution is not None or
  Change.candidate_side_enrichment` (the two markers `compare()` itself
  sets — deliberately not a `ChangeKind` allowlist, since a kind-keyed
  filter is a second place every new candidate-side kind would need
  teaching, exactly the drift `no_baseline_compare`'s own docstring already
  argues against). `check_no_baseline_partition` keeps the original
  assertion's real intent scoped to the identity half, and adds D3's rule
  (`NO_BASELINE_EVOLUTION_STATES = {persistent, not_evaluated}`) over the
  other. Both are **raised errors, not `assert`s**: the original guard was
  stripped under `python -O`, which would have turned an identity-half
  violation into a silently wrong report rather than a loud failure.
  `workflows/no_baseline_compare.py` returns the candidate-side half on
  `NoBaselineCompareResult.findings`, and `report/no_baseline.py` renders
  it.
- **The second root cause the write-up did not name.** Fixing the partition
  alone still left the live-binary case reporting nothing. The no-baseline
  CLI passed `-H`/`--header` as *parse* input only, never as public-header
  **provenance** — where a two-sided `compare` has always folded each
  side's headers into its public-header sets
  (`service_compare_pipeline._public_header_sets`, via
  `header_utils.split_public_header_inputs`). Without that fold every
  declaration stayed `ScopeOrigin.UNKNOWN`, so the four boundary-dependent
  checks (`exported_not_public`, `public_not_exported`,
  `rtti_for_internal_type`, `public_to_internal_dependency`) evidence-gated
  to `NOT_EVALUATED` and reported nothing — correctly, given what they were
  told. `frontends/cli/commands/compare_no_baseline.py`'s
  `_resolve_public_header_sets` now applies the same split-then-tag rule
  (split before tagging, never after: an unsplit directory entry corrupts
  `scope_fingerprint`).

**Evidence.** `tests/parity/test_no_baseline_audit_corpus_parity.py` is the
acceptance lane this entry asked for, over all eleven fixtures (twelve runs
— `case151` contributes its `thin.abi.json` variant too, the corpus's own
"weaker evidence narrows conclusions" case). Measured result:
`compare --no-baseline` and `scan` agree **exactly** on every fixture —
nothing lost, nothing manufactured:

| Fixture | `scan` `crosscheck.counts_by_check` | `compare --no-baseline` `findings[]` |
|---|---|---|
| case143 | `exported_not_public: 1` | `exported_not_public: 1` |
| case144 | `private_header_leak: 1` | `private_header_leak: 1` |
| case145 | `unversioned_exported_symbol: 1` | `unversioned_exported_symbol: 1` |
| case146 | `rtti_for_internal_type: 2` | `rtti_for_internal_type: 2` |
| case147 | `private_header_leak: 1` | `private_header_leak: 1` |
| case148 | `header_build_context_mismatch: 1` | `header_build_context_mismatch: 1` |
| case149 | `odr_type_variant: 1` | `odr_type_variant: 1` |
| case150 | `exported_not_public: 1`, `public_not_exported: 1` | same |
| case151 (`snapshot`) | `private_header_leak: 1` | `private_header_leak: 1` |
| case151 (`thin`) | `private_header_leak: 1` | `private_header_leak: 1` |
| case181 | `public_to_internal_dependency: 1` | `public_to_internal_dependency: 1` |

The table above is the *measured* result on today's fixtures. What the lane
**asserts** is deliberately weaker and directional, in two halves, because
checking only one is how this gap got in: *no capability loss* (every check
`scan` fires, `compare --no-baseline` fires at least as often, counted per
finding kind) and *nothing manufactured* (every reported finding is
genuinely candidate-side, in a D3-permitted state, with `changes == []` and
`verdict == null` still holding). It is not an identical-report assertion,
and must not be described as one: the contract this PR was gated on is
"the same or a strictly richer finding set", so pinning equality would
fail a future run that legitimately reports *more*.

**One capability `scan` has that the audit report does not** (found while
correcting that overstated claim, recorded rather than quietly reworded):
`scan`'s `crosscheck` block carries a per-check *coverage row* — status
(`present`/`skipped`/`partial`), its detail line, and the `providers` list
that corroborated it. The audit report states each finding and its D3
evolution state, and its `cross_source_evolution` block counts the four
states, but no per-check row: a check that ran and found nothing, and a
check that could not run at all, are both simply absent from `findings[]`
(the Markdown renderer says so in prose; the JSON does not distinguish
them). That is squarely against `vision.md`'s "record before disposing"
and ADR-068 D9, so it is a real gap, not a design choice — it is left open
here rather than folded into this PR because a coverage block is a report
schema addition with its own compute/render pair, not a bug in the
findings path this entry closed. `catalog/cases/case151_xcheck_provider_
matrix/README.md` reads the providers off `run_crosschecks()` directly in
the meantime, and says why.

**A second capability difference, on the exit code** (found the same way, by
regenerating the catalog's expected outputs against the real command instead
of trusting the prose): legacy `scan` derives a *verdict* from an audit's own
findings, so a hygiene finding whose `ChangeKind` is `API_BREAK`-classified
gates CI at exit `2` — verified live, `scan` on `case148`'s and `case149`'s
committed snapshots exits `2`, while `case143`'s `RISK`-classified finding
exits `0`. `compare --no-baseline` exits `0` for all of them: ADR-068 D2 says
an audit reports no compatibility verdict, and `2` is the compatibility
family's own source-break code, so emitting it would be manufacturing
exactly the signal D2 forbids. The CLI was at least loud rather than
silently ignoring the request — `--severity-preset` with `--no-baseline`
used to be a usage error (exit `64`) naming this reason, not a no-op.

**Closed (2026-09-10 ADR-068 amendment).** `policy/audit_gate_exit.py` adds
the orthogonal audit-gate axis this entry called for: its own exit code
(`3` — surveyed against every code `compare`/`scan --against` already use,
the one small integer none of them claims), folded with `max` exactly like
the coverage and evidence-contract axes, so it can raise a clean `0` and can
never lower or be mistaken for a `2`/`4`. It is **opt-in**, not on by
default (every pre-existing `compare --no-baseline` invocation's exit code
is unchanged), activated by passing `--severity-preset` (any value except
`info-only`) — that option is no longer a usage error under
`--no-baseline`; passing it is now the declaration. The axis's own gating
rule reproduces legacy `scan`'s exact partition — `BREAKING_KINDS |
API_BREAK_KINDS` gates, `RISK_KINDS`/`COMPATIBLE_KINDS` does not — read
directly off `checker_policy.py`'s registry-derived sets, deliberately
*not* routed through `severity.py`'s own four-bucket category model (that
model's `potential_breaking` bucket is `API_BREAK_KINDS ∪ RISK_KINDS`,
merged by design, which would have gated `case143`'s `RISK`-classified
finding the same as `case148`/`case149`'s `API_BREAK`-classified ones —
see the ADR amendment for the full reasoning this entry's fix rejected).
Verified against the exact reproduction this entry asked for:
`tests/parity/test_no_baseline_audit_corpus_parity.py`'s
`test_audit_gate_axis_matches_legacy_scan_gating` runs the whole G20 corpus
with `--severity-preset default` and asserts `case148`/`case149` exit `3`
while every other fixture, `case143` included, stays `0` — the same split
`test_scan_baseline_exit_codes_documented_for_the_gated_fixtures` pins for
legacy `scan` itself on the same committed snapshots. The typed Python API
carries no `--no-baseline` support at all yet, so there is no
`CompareResult`-shaped consumer to extend today. See
[ADR-068's 2026-09-10 amendment](adr/068-one-comparison-product-and-scan-retirement.md#amendment-2026-09-10-the-audit-gate-exit-axis)
for the full account. `scan`'s retirement (§3 of
`docs/contribute/plans/one-comparison-product.md`) is no longer blocked on
this gap specifically; the plan's own tracking is updated to match.

**The composite Action's own translation has since landed too.** At the
time the paragraph above was written, `action/run.sh` did not invoke
`compare --no-baseline` for `mode: scan` at all -- audit-only requests
stayed on the legacy `scan` CLI unconditionally (the 2026-09-09 amendment's
own remaining routing condition), so there was no live call site to
translate onto. That has closed: audit-only `mode: scan` now routes to
`compare --no-baseline` unconditionally too, the same way a baseline scan
already routed to plain `compare AGAINST ARTIFACT`. The Action injects
`--severity-preset default` on the translated invocation whenever the
caller stated no preset of its own (neither the dedicated `severity-preset`
input nor an explicit `extra-args --severity-preset ...`), which is what
keeps `mode: scan`'s own documented default-gating behavior intact across
the migration -- a caller who already asked for a preset (`info-only`
included) keeps exactly the preset it asked for. Exit `3` is published as a
new `AUDIT_GATE` Action verdict output, alongside `COVERAGE_INCOMPLETE`/
`SEVERITY_ERROR`, and fails the step unconditionally (no `fail-on-*` input
governs it, since an audit reports no compatibility verdict to follow).
There is no `MODE == "scan"` condition left in `action/run.sh`'s own
command assembly that exists to serve a legacy-CLI fallback. See
`tests/test_action_run_sh_audit_gate.py` for the end-to-end coverage
(real `run.sh`, real `abicheck`, the same G20 corpus fixtures this entry's
own parity lane already used) and `action.yml`'s `verdict` output
description for the user-facing contract.

**An audit's contract-coverage ledger still names an `old` side.** Now that
`compare --no-baseline --contract public` publishes
`contract_coverage_failures` (it previously exited 1 carrying only the
numeric contribution), the entries are visible -- and half of them read
`"side": "old"` on a run whose OLD side is `declared_absent`. That is an
artifact of the audit being implemented as a self-compare: the evidence
collector sees two sides because it is handed the same snapshot twice.

Deliberately **not** filtered here. The same records produced the exit
contribution that gated the run, so dropping them from the listing would
make the ledger disagree with the number beside it -- exactly the "read,
don't re-derive" failure recorded elsewhere in this file. Doing it properly
means the collector knowing the run is one-sided, so it records one side's
observations rather than two identical ones, and the contribution follows;
that is an ADR-049/ADR-068 question about how a one-sided run collects
evidence, not a display fix. Until then the listing is honest about what
gated, and this entry is what says why an `old` row appears at all.

**The audit's SARIF and JUnit projections do not cross the canonical
`ReportDocument` boundary** (Codex review, P1, partially addressed). The
audit's JSON does: `report.no_baseline.no_baseline_report_document` wraps the
computed mapping in a real `ReportDocument`, so the structured output takes
that type's defensive immutable snapshot the way every two-sided format
does. SARIF and JUnit still read the typed `NoBaselineDocument` directly.
That is deliberate as far as it goes -- `ReportDocument` is an *untyped*
frozen mapping, and rewriting two renderers to index strings instead of
typed fields is a type-safety downgrade, which is why the two-sided HTML and
Markdown renderers also carry typed section structs -- but it does leave the
audit's two structured non-JSON formats outside the boundary the
`report/AGENTS.md` contract states for every format. The shared sections
Codex was concerned about (`run_outcome`, `comparison_scope`) are already
built by the same section builders the two-sided path uses, so those
specifically cannot drift; a *new* shared section added to
`ReportDocument` would still have to be added to the audit by hand.

Per `AGENTS.md`'s bug-class rule the D3 invariant is **also** stated as a
property test over generated inputs rather than eleven fixed cases:
`tests/test_no_baseline_d3_properties.py` generates candidate snapshots
across the whole evidence ladder (export table present or absent,
declaration origins drawn independently of the export list, versioning
scheme on or off, private types present or not) and asserts that no
candidate ever yields `introduced`/`resolved`, that the comparison half is
always empty, that the reported set is *exactly* the candidate-side one
(the "no longer crashes but silently drops findings" failure a
does-not-raise test would miss), and that the audit is deterministic. Its
oracle is the forbidden-state set written out literally, not derived from
the implementation's own permitted set, so widening one cannot silently
widen the other. The partition helpers additionally get primitive-level
contract tests (totality, disjointness, order preservation) decoupled from
any snapshot, per `AGENTS.md`'s "Primitive-level property tests" guidance.
Both suites were mutation-checked: silently dropping candidate-side findings
fails 14 tests, and permitting `introduced` fails 2.

Registered as bug class `invariant.blanket_assertion_over_widened_population`
in `tests/regressions/manifest.py` — the general shape being "a whole-set
emptiness assertion outlives the single population it was ever true of".

### `compare --no-baseline` accepts `--contract` but never wires it through

Found in the same Phase 4 documentation slice, verified by reading
`frontends/cli/commands/compare_no_baseline.py` against `main` at the same
commit above (fd6ba681). `compare --no-baseline` accepts `--contract
public|exports|all|auto` — Click parses it, `--help` documents it — but
`_run_no_baseline_compare_cmd` never reads `kwargs.get("contract")` (or a
resolved `contract_mode`) and never passes anything contract-related to
`run_no_baseline_compare`. The contract-coverage ledger this axis's exit
contribution folds from is therefore never populated on this path: a
`compare --no-baseline NEW --contract public` run against a headerless
candidate exits `0`, where the equivalent two-sided `compare OLD NEW
--contract public` exits `1` for missing public-header coverage. The flag
is accepted but silently inert, not merely undocumented — a CI job relying
on it as a gate gets no warning that it never ran.

Not fixed here, same file-ownership boundary as the gap above. The fix is
threading the resolved `contract_mode`/`contract_evaluation` config through
`_run_no_baseline_compare_cmd` into `run_no_baseline_compare`, the same way
the two-sided `compare` path already does via
`compatibility_evaluation_frontend`/`contract_pipeline`. Recorded in
[`docs/reference/exit-codes.md`](../reference/exit-codes.md#compare-no-baseline-adr-068-d2-single-artifact)
rather than left as a silent behavioral gap in the doc that would otherwise
claim the axis "applies exactly as it would for a two-sided run."

**Closed (2026-09-09).** Reproduced live first: on `main` at `ed4e70e3`,
`compare --no-baseline libgreet.so --contract public` against a headerless
candidate exited `0` where the two-sided `compare libgreet_old.so
libgreet.so --contract public` exited `1`. `_run_no_baseline_compare_cmd`
now resolves the flag through the *same two* `cli_options` helpers
`cli_compare_helpers.run_compare` uses — `resolve_contract_evaluation` (any
value activates the ADR-049 evaluator) and `resolve_contract_domain`
(`auto` maps back to `None` so D7's lower tiers decide, and its parameter
source is demoted from `COMMANDLINE`) — and passes both into
`run_no_baseline_compare`, whose `contract_evaluation`/`contract_mode`
parameters already existed and were simply never fed. Sharing the resolvers
rather than re-deriving is what makes an equivalent one-sided and two-sided
invocation activate identically.

Nothing downstream needed changing: `report/no_baseline.py`'s
`no_baseline_exit_code` already folded `coverage_exit_floor(result.diff)`,
which reads the ledger off the run's own persisted `contract_context` — it
had simply never been populated. Verified live after the fix:
`--contract public` on the same headerless candidate now exits `1`, and a
run without `--contract` still exits `0` (no domain to be short of evidence
for, so every pre-existing invocation is unchanged). The corpus lane's
`test_no_baseline_exit_code_is_clean_without_a_contract` pins that second
half across all eleven fixtures, so the exit-1 case is a real signal rather
than noise.

### `compare --no-baseline` parsed `--sources`/`--build-info`/`--depth`/`--dry-run` but read none of them

**Closed (2026-09-09), in the same pass that closed the two entries above.**
Recorded here rather than left unwritten because it is the same failure mode
as the `--contract` entry above — a flag Click parses and `--help` documents,
which the command body never reads — and it was found by auditing
`_run_no_baseline_compare_cmd` against `compare`'s full option set once
`--contract` turned out to be inert, not by a user report. Verified live on
`main` at `ed4e70e3` before the fix: `compare --no-baseline libgreet.so
--depth build` exited `0` where the two-sided `compare libgreet_old.so
libgreet.so --depth build` exited `7`; `--dry-run` ran a full audit and
printed a report; `--sources` collected nothing.

Four separate wirings, each reusing the primitive the two-sided path already
uses rather than growing a parallel one:

- **`--sources`/`--build-info`.** `resolve_no_baseline_candidate` called
  `workflows.input_resolution.resolve_input` directly, which resolves L0–L2
  only, so inline L3–L5 evidence was never embedded. It now routes through
  `workflows.artifact.execute.resolve_side_snapshot` — the *same* per-side
  primitive `dump` and each side of a two-sided `compare` resolve through —
  with an `InputSpec` carrying the candidate's `sources`/`build_info` and a
  `SideEvidence` whose collect mode comes from `collect_mode_for(depth,
  side)` (already variadic over a single input precisely because `dump` asks
  it over one operand too).
- **`--depth`.** Feeds that collect mode, and is separately held to the same
  evidence-contract floor. `policy/depth_evidence_contract.py` gained
  `record_no_baseline_depth_evidence_contract_error`, a named one-sided
  entry point that delegates to the existing two-sided
  `record_depth_evidence_contract_error` with the OLD side excluded — spelled
  as its own function rather than leaving each call site to write
  `old=candidate, new=candidate, old_is_live=False`, which reads as a bug at
  every call site. The stored-snapshot carve-out (`is_live`) carries over
  unchanged: a `.abi.json` candidate this run never extracted cannot have
  fallen short of a depth. Verified live after the fix, one-sided and
  two-sided now agree exactly: **exit 7** for `--depth build` with no
  build evidence, and **exit 0** for the same command once a real
  `compile_commands.json` is present.
- **`--dry-run`.** `frontends/cli/compare_dry_run.py` gained
  `build_no_baseline_dry_run_result`, a sibling of the two-sided builder
  rather than a call into it: that builder's whole shape is two operands, and
  rendering a single-build audit through it would print the candidate twice
  under `old:`/`new:` labels — exactly the "a baseline was consulted"
  misreading ADR-068 D2 forbids. It reuses the shared section titles
  (`dry_run.SECTION_ORDER`) so both reports still read the same way, and
  *blocks* (exit 1) on a pinned `--depth build`/`--depth source` with no
  `--sources`/`--build-info`, since the real run exits 7 on that same
  condition and a dry run that called it fine would be lying.
- **Two more silently-inert flags on the same path, found by the same
  audit.** `--write FORMAT=PATH` was accepted and dropped — a job asking for
  a JSON artifact alongside a human-readable report got neither the file nor
  a warning. It now renders from the *same* analysis rather than re-running
  it (ADR-068 D4's "a complete machine-readable result must always be
  obtainable without a second run"), reusing the shared
  `reject_incoherent_secondary_writes` guard rather than a second copy of its
  two coherence rules, and rejects an unsupported secondary format naming
  `--write` rather than `--format`. `--include-system-declarations` was
  dropped too: this path never passed `include_dependencies` at all, so it
  silently got `resolve_input`'s unfiltered default (`True`) where every
  two-sided `compare` and every `dump` passes the CLI's own filtered default
  (`False`). It now reads the flag, so a `--no-baseline` snapshot is scoped
  the same way a `dump` baseline is — verified live that the finding set is
  unchanged either way on the `audit-release` example.

- **An `old=`-scoped input is now a usage error, not a silent drop.** A
  `--no-baseline` run has no OLD side, so `--sources old=…`/`--header old=…`
  and siblings are rejected with a real message — the same guard
  `_reject_view_tokens_for_no_baseline` already applies to `--view`. The
  distinction matters and is easy to get backwards: a *bare* `--sources
  tree/` populates **both** per-side dests
  (`cli_options._split_sided_single`), so "the old dest is set" is not "the
  user scoped this to OLD" — the guard fires only when the old dest holds
  something the new dest did not also get. Rejecting the bare spelling would
  break the single most useful invocation on this path.

**`--format` ruling, dated rather than deferred.** The Phase 2e slice
supported only `json`/`markdown`, with the other five a usage error "until a
later phase". That is now decided per format rather than left open:

- **`sarif`, `junit`, `oneline` — implemented.** SARIF and JUnit are
  *findings* formats with no verdict slot to leave empty (a SARIF run is a
  list of results with rule ids and levels; a JUnit suite is a list of test
  cases), which is exactly what a single-build audit produces, and both are
  how a CI job consumes one. Rendered in `report/no_baseline.py` from the new
  frozen `NoBaselineDocument` rather than through `sarif.to_sarif`/
  `junit_report.to_junit_xml`, whose `exitCode`/`exitCodeDescription` and
  suite partitioning are derived from `DiffResult.verdict` — which on a
  self-compare reads `NO_CHANGE`, a compatibility claim D2 forbids this run
  from making. The per-finding *metadata* is reused, not reinvented:
  `sarif._rule_for`/`_parse_source_location` are imported, so a rule id and
  help URI mean the same thing a code-scanning consumer already expects.
- **`html`, `review` — remain a usage error, by ruling.** Both are narrative
  renderings of a *comparison*, not projections of a finding list: HTML's
  information architecture is a verdict badge, an OLD → NEW version headline
  and addition/removal/modification tables; `review` is literally "what
  changed between these two releases, and should you ship it" (verdict,
  counts by direction, release recommendation, manual-review banner). With no
  baseline there is no change to review and no release to recommend, so a
  one-sided rendering of either is a *new page/digest design*, not a
  projection — genuinely different work from the three above, none of which
  needed a layout decision. The CLI's error now names this ruling and points
  at `oneline` as the closest honest equivalent to a review digest, instead
  of promising an unspecified later phase. The reasoning lives with the code,
  in `report.no_baseline.NO_BASELINE_UNSUPPORTED_FORMATS`.

**Four review findings on the first push, all real, all fixed.** Recorded
because two of them are the *same* defect class this entry is about — a
derived answer disagreeing with the answer it derives from — and one
reproduces a bug this repository had already fixed once elsewhere:

- **A suppressed finding vanished (P1).** `checker.compare()` moves a
  matched finding out of `diff.changes` into `diff.suppressed_changes`; the
  partition read only the former, so a suppressed audit was
  indistinguishable from a clean one — no way to tell that policy hid a
  finding, or which rule did. That is precisely what `vision.md`'s "Record
  before disposing" forbids ("100 removals detected, 100 suppressed by rule
  X" stays visible on a passing run). The suppressed half now gets the
  *identical* partition and D3 check (a suppressed comparison finding would
  be just as impossible), rides on `NoBaselineCompareResult.
  suppressed_findings`, and is projected by every format with its own
  `suppression_rule`: a `suppressed_findings[]`/`suppressed_count` pair in
  JSON, a table in Markdown, a count in `oneline`, SARIF's own native
  `suppressions` array, and a `<skipped>` case in JUnit.
- **JUnit failed a build the CLI passed (P1).** Every finding got a
  `<failure>` while the same document reported exit `0` — audit hygiene
  findings are advisory (ADR-028 D3 / ADR-035 D1) and ADR-068 D2 gives the
  run no compatibility contribution at all. This is the identical bug
  `junit_report._is_failure`'s docstring records having already been fixed
  once for the two-sided report ("reporting one `<failure>` beside a
  `NO_CHANGE` verdict and a clean exit was the bug"). Failure is now
  derived from the run's own gate: each finding gets a **passing**
  `<testcase>` (D9 — the fact stays visible, it just is not a failure) and
  one `exit code` case fails when, and only when, an orthogonal axis
  actually gated the run.
- **`--write` bypassed the shared safe writer (P2).** A raw
  `Path.write_text()` raised `FileNotFoundError` after the analysis and the
  primary render had already completed, when the target's parent directory
  did not exist. Now routed through `_safe_write_output`, the same writer
  `-o/--output` uses, which creates parents and translates a failure into a
  clean Click error.
- **The dry run disagreed with the run it previews (P2).** A pinned
  `--depth build` on a *stored* candidate was reported as a blocker (exit
  1) while the real run exempts that operand from the depth floor
  (`candidate_is_live=False` — no extraction happened, so nothing can have
  fallen short) and exits `0`. The preview now takes the same carve-out
  from the same helper, so the two cannot diverge.

Generalized rather than patched per instance:
`tests/test_no_baseline_report_formats.py` states the invariants over every
format and every fixture — conservation (suppression only ever *moves* a
finding between the two lists, checked over generated rules that match all,
some and none), JUnit's failure count equalling the exit code exactly,
every format disclosing a suppression, every format agreeing on the exit
code, and JSON and SARIF agreeing on the finding set. Mutation-checked
against all four regressions, including the over-correction (making JUnit
pass by *dropping* findings satisfies the failure-count invariant while
losing the audit's whole content — a separate test catches that).

**A second review round, four more findings, all real (2026-09-09).** Two
of them are again the same class this entry is about, and one is that class
*inside the mechanism built to prevent it*:

- **The exhaustiveness test had a hole exactly where it hand-waved (P1).**
  `_CONSUMED_UPSTREAM` allowlisted the *pre-normalization* option names
  (`dump_manifest`, `version`, `debug_root`, `probe_matrix`), on the
  reasoning that `normalize_sided_options` consumes them before dispatch.
  It does -- but the `new_*`/`old_*` destinations it *generates* were never
  read, so the test passed **because the option had been renamed**. A bare
  `--dump-manifest` was accepted and dropped: even an invalid manifest
  exited `0` while the audit analysed a different surface than requested,
  and `--version new=` was equally inert. The test now expands each raw name
  into the destinations it generates and requires each one separately to be
  read or declared -- which immediately surfaced **16** silently-dropped
  destinations, not the four the review named. `--version`, `--debug-root`
  and `--include`'s per-path labels are now wired; `--dump-manifest`,
  `--probe-matrix`, `--debug-info` and `--devel-pkg` are rejected (their
  guards re-keyed onto the generated names, since a guard keyed on the raw
  name could never fire); `old_version` is recorded as inert *by
  construction* with its reason, since Click always populates it with the
  placeholder `"old"` and an explicit `--version old=` is therefore
  indistinguishable from the default by dispatch time.
- **`--depth binary` still ran the L2 header frontend (P1).** The audit
  hand-built its `SideEvidence` instead of going through
  `service_compare_evidence.resolve_side_evidence`, so it skipped that
  function's binary-depth clearing rules (headers *and* any dump manifest
  are dropped). Verified live: one-sided reported `evidence_tiers: [elf,
  dwarf, dwarf_advanced, header]` and an `exported_not_public` finding where
  two-sided reported no `header` tier at all — a **manufactured finding** at
  a depth documented as symbols-only. Fixed by reuse, not a second copy.
- **A failed evidence contract read as operationally successful (P1).**
  `no_baseline_exit_code` correctly returned `7`, while `run_outcome.
  operational` still serialized `none`. `compatibility`/`gate` genuinely
  never apply to an audit, but *operational* is a different axis, and
  `OperationalStatus.EVIDENCE_CONTRACT_ERROR` is its canonical state. Now
  read off the same flag the exit code folds, so the two cannot disagree.
- **A linker-script operand was misclassified as stored (P1).** The depth
  floor's live/stored carve-out asked `detect_binary_format` about the
  operand *as written*. A GNU ld `INPUT(...)`/`GROUP(...)` script is text, so
  it answered "stored" and exempted the run -- while `resolve_input` happily
  followed the script and performed a real extraction. Verified live: the
  same library named directly exited `7` under `--depth build`, and named
  through its script exited `0`. Now chain-resolved with
  `binary_utils.resolve_linker_script_chain` first, which is what the
  resolver itself does with the same operand, so the two agree by
  construction rather than by coincidence.
- **Extraction failure escaped as a traceback (P2).** A corrupt candidate
  printed a full stack trace where the two-sided path printed
  `Error: Failed to dump '...'` for the identical input. Now translated at
  the CLI boundary with the same `ValidationError` -> exit 64 /
  `SnapshotError` -> exit 1 mapping `cli_resolve`'s own `run_dump` wrapper
  uses.

One self-inflicted defect worth recording, since it is the same discipline
failure the entries above are about: splitting the SARIF/JUnit renderers out
to keep `report/no_baseline.py` under the 800-line cap created a real import
cycle (`no_baseline -> no_baseline_render -> no_baseline`, via the
renderer's `NoBaselineDocument` annotation -- the AI-readiness scan counts
``TYPE_CHECKING`` imports too). It reached CI because after that last change
`check_architecture` was re-run and `check_ai_readiness` was not; only one of
the two catches this. Fixed by giving the shared contract its own leaf,
`report/no_baseline_document.py`, which both halves depend on and neither
depends back through -- this package's own `document.py`-versus-`render_*`
pattern, and the remedy `AGENTS.md` prescribes ("move the shared logic to a
leaf module", never extend `IMPORT_CYCLE_ALLOWLIST`).

Each has a regression test in `tests/test_compare_no_baseline_cli.py`, and
the exhaustiveness rule itself gained
`test_every_generated_destination_is_wired_or_declared` -- the half the
original test missed. `report/no_baseline.py` crossed the 800-line new-file
cap in the process; its SARIF/JUnit renderers moved to
`report/no_baseline_render.py`, matching this package's own
`render_json.py`/`render_xml.py` split, rather than being trimmed to fit.

**Coverage and complexity follow-up (same pass).** Codecov's patch gate
and CodeFactor both flagged the first two commits, and both were acting on
something real rather than on noise:

- **The new CLI surface was verified by hand and pinned by nothing.** Every
  usage error, the `--write` path, and the `--dry-run` preview had been
  confirmed at a terminal while being written, and no test named any of
  them -- the same "confirmed once by the person who just wrote it" gap this
  entry's own history keeps producing. `tests/test_compare_no_baseline_cli.py`
  drives all of it through the real Click entry point (which formats render
  and how, which invocations are refused and what the message says, what
  reaches disk, what the process exits with), and
  `tests/test_no_baseline_report_formats.py` gained the *gating* half that
  the P1-2 fix's own correctness depends on: a run whose orthogonal axis
  fires must still fail exactly one JUnit case, and its advisory findings
  must still pass. Coverage of the five new/changed modules went from 90% to
  94%, with the CLI module 80% -> 95% and the dry-run builder 0% -> 100%.
- **The command body was one 159-line function** (cyclomatic complexity
  ~30) doing validation, resolution, execution and reporting in sequence,
  which is what CodeFactor's "complex method" pattern is for -- and it
  passed on the first commit, failing only once the review fixes grew it.
  Split into the four phases it always had: `_validate_no_baseline_
  invocation` (every refusal, before any work), `_resolve_no_baseline_
  invocation` (into a frozen `_ResolvedInvocation`, so a later phase cannot
  reach back for a raw parameter the validation phase already ruled on),
  the run itself, and `_emit_no_baseline_report`. Now 78 lines at complexity
  4. Behaviour-preserving: the CLI tests above were written *before* the
  split, precisely so it was protected.

**The class, not just the four instances.** "Accepted but never read" is
the single defect this path has now produced four separate times
(`--contract`; `--sources`/`--build-info`/`--depth`/`--dry-run`; `--write`;
`--include-system-declarations`), and every one was found by *reading the
code*, never by a failing test — a dropped option produces no output to fail
on. Fixing them one at a time leaves the class open, since the next option
added to `compare` inherits the same silence. So the rule is inverted:
`_UNSUPPORTED_OPTIONS` in `frontends/cli/commands/compare_no_baseline.py`
names every `compare` option this path does *not* implement, passing one is
a **usage error** rather than a no-op, and
`tests/test_compare_no_baseline_options.py` fails if any `compare` parameter
is neither read by that module nor declared in the table. A new flag is
therefore either wired or declared, never silent.

The table's reasons fall in three families, so the error message tells the
user which applies: the option *describes a comparison* this run never
performs (`--used-by`, `--used-by-manifest`, `--required-symbol`,
`--use-cases`, `--post-manifest`, `--diagnostic-comparison`,
`--old-variant`/`--new-variant`, `--bundle-facts-*`, `--since`/
`--changed-path`); it is for the *directory/package fan-out*
`--no-baseline` does not accept (`--select`, `--select-required`,
`--output-dir`); or it is genuinely applicable and *not wired yet*
(`--abi3` — candidate-side by definition and the obvious next one to close
— `--budget`, `--severity-preset`, `--pack`, `--config`,
`--instantiation-manifest`, `--follow-deps` and its search-path siblings,
`--debug-info`, `--devel-pkg`). That last family is recorded here rather
than left as an accepted no-op precisely so it is a visible decision.
### A resolved PDB/DWP/dSYM artifact reaches no dump path

`debug_resolver`'s chain can resolve four kinds of artifact, and only one
of them is consumed. `service_dump_native._dump_elf` reads
`DebugArtifact.dwarf_path` and nothing else -- a `dwp_path`, a `dwo_dir` or
a `dsym_path` is resolved, logged, and dropped -- and the PE branch of
`_run_dump_uncached` never passes `debug_roots` to `_dump_pe` at all, so a
PDB found in a debug root is dropped too (the PE path's only PDB input is
`debug.pdb_path`, via `locate_pdb(pdb_path_override=...)`).

**Pre-existing, and predates plan Phase 7n** -- `--debug-root` had the same
shape. What 7n changed is that the gap became *sayable*: merging the debug
role into one input meant naming a detached artifact directly, which would
have resolved a PDB/DWP and then silently ignored it, and (first review
round) made the resolver return one *ahead* of `EmbeddedDwarfResolver`, so
a binary carrying perfectly good DWARF would have been compared with none.
Both are closed: `extract/detached_debug.DetachedDebugFileResolver` is
DWARF-only and yields to the rest of the chain, and naming a PDB/DWP file
is a usage error naming `debug.pdb_path` (the spelling that *is* read).

The *directory* form is deliberately still accepted, because rejecting it
would break the case that works -- a build-id tree or path mirror of ELF
`.debug` sidecars, which is what the input is mostly for. So a directory
that happens to hold PDBs still resolves to an artifact nobody reads. That
is this gap, unchanged; do not "fix" it by rejecting directories.

Closing it properly means threading `debug_roots` into `_dump_pe` and
teaching `_dump_elf` to consume `dwp_path`/`dwo_dir`/`dsym_path` -- real
capability work on the extraction paths, not a CLI change, which is why
ADR-068's flag-topology slices leave it alone. Found by Codex review on
PR #1253.

**Update (plan Phase 7n):** `--debug-root`, `--devel-pkg` and
`--probe-matrix` no longer exist as flags -- each merged into the one input
for its evidence role (`--debug-info`, `-H/--header`, `--build-info`). The
rejections above are unchanged in substance and still keyed on the
generated destinations (`devel_pkg2`, `probe_matrix_*`, ...), which is what
made them survive the rename; only the spelling each message names moved.
Writing the test found one the manual audit had missed
(`--used-by-manifest`), which is the argument for the mechanism in one line.

The whole compute/render split is new in this pass too:
`report/no_baseline.py` now follows `abicheck/report/AGENTS.md`'s convention
— one `compute_no_baseline_document` resolving per-finding verdict/category,
evolution counts, coverage and exit contributions, and pure `render_*` halves
that format and decide nothing — so a new audit report section goes in the
document, never into one renderer. The audit publishes its own schema
(`abicheck/schemas/audit_report.schema.json`, `audit_report_schema_version`
starting at `1.0`) rather than a version of the compare report's: `findings[]`,
`cross_source_evolution`, `exit_axes` and a candidate-only
`pattern_preprocessor_scan` block are its own, and `changes: []` stays, so a
consumer reading `changes` off any abicheck report still finds it. It briefly
stamped `report_schema_version` instead, which offered the audit under the
compare report's identity -- see that field's own note in
`report/no_baseline_document.py` for why that is not merely cosmetic.

### The Action's `mode: scan` still routes several request shapes to the legacy `scan` CLI

Found while closing ADR-068's baseline cross-source authority divergence
(see the ADR's 2026-09-09 amendment,
[`plans/one-comparison-product.md`](plans/one-comparison-product.md) Phase
4). **Update (2026-09-09, Phase 4 commit 2):** the maintainer re-scoped this
work — the Action does not keep a compatible interface with every `scan`
capability, so each remaining condition below is now ruled (a) already
covered, (b) dropped as a documented breaking change, or (c) genuinely
required and implemented — see ADR-068's second 2026-09-09 amendment for the
full per-condition table. Two rows closed for real in this commit:

- **`--budget` — closed.** `compare` gained a `--budget` option
  (`frontends/cli/commands/compare.py`), enforced via
  `deadline.deadline_scope` around both the resolve and classify phases
  (remaining-time-aware, not a fresh full budget on each entry), setting
  `DiffResult.budget_overflow` (exit 5) on overflow. Verified live. Typed API:
  `CompareRequest.budget_s`. **Known, accepted narrowing**: bounds two
  coarser phases (resolve, classify) rather than `scan_engine.py`'s own
  finer per-stage checks — still a real, enforced ceiling, not a capability
  gap.
- **An explicit `--depth build`/`--depth source` with unreached evidence —
  closed.** This was a real, undocumented `compare` gap distinct from
  everything else in this entry: `compare --depth build old.so new.so` with
  no `--sources`/`--build-info` silently degraded to symbols-only evidence
  and reported `NO_CHANGE`/exit 0 (verified live) — `workflows.artifact.
  execute.enforce_requested_depth` already implemented this exact floor as a
  hard `ValidationError`/exit 64, but only for the typed-API resolution path
  (`resolve_compare_request`); the native CLI's own resolution
  (`cli_resolve._resolve_compare_snapshots`) never called it. New
  `policy/depth_evidence_contract.py` recomputes the same floor and records
  it as ADR-064's exit-7 axis instead of raising (mirroring `workflows.
  abi3_audit.record_abi3_evidence_contract_error`'s existing pattern),
  wired into both the native CLI and the typed pipeline. Verified live:
  `compare --depth build` on an evidence-free pair now exits `7`.

Ruled (b), dropped, documented breaking changes to `mode: scan` (see the
ADR amendment for the full reasoning each):

- `--risk-rules` and risk-driven `auto` depth selection (no explicit
  `--depth`) — omitting `--depth` on `compare` already deterministically
  defaults to `headers`, so dropping the risk-scoring auto-escalation
  removes a sometimes-deeper convenience, not a floor; a CI job wanting
  guaranteed source-level assurance must now pin `--depth source` itself.
- `--crosscheck KEY=error` promotion syntax — already superseded: all
  eleven cross-source checks reach `compare` as ordinary `ChangeKind`s, so
  `--policy`/`.abicheck.yml`'s `policy.overrides` already lets a user
  control any one check's severity; only the `KEY=LEVEL` *syntax* itself
  does not survive.
- `--build-target` — **stale, corrected below.** This entry originally said
  no config equivalent existed for either command; that was already false
  by the time it was written (`.abicheck.yml`'s `build.targets` key existed
  and `dump --build-target` already consumed it, CLI-override-wins). `scan`
  *did* have its own `--build-target` flag
  (`changelog.d/20260816_113000_noreply_abicheck_scan_build_target.md`
  records it being added), and it was retired first, in ADR-068's
  2026-09-09 amendment (the `scan` retirement) — `dump`'s own
  `--build-target` was deliberately left in place at that point precisely
  because `scan` still existed and shared its plumbing, so pulling `dump`'s
  copy first would have broken `scan`'s still-live flag. `dump`'s own
  `--build-target` CLI flag was later removed outright too (ADR-068 Phase 6
  follow-up, once `scan`'s full removal cleared the routing hazard that had
  deferred `dump`'s own removal) — `.abicheck.yml`'s `build.targets` is now
  the *only* front-end-reachable source for either command, with no CLI
  override left to take precedence over it.
- `--artifact-set`/`new-library-set` — ADR-065 S3's package component
  inventories (plan Prerequisite P5) are explicitly "Not started"; routing
  this onto `compare --no-baseline DIR` today would silently narrow its
  member-selection/coverage guarantees rather than reproduce them, which is
  exactly the false-negative risk the (c) admission bar exists to catch.

Ruled (a), already covered by `compare` today, no Action-level gap left once
routed unconditionally: the additive-vs-overriding `--header`/`--include`
combination (fixed by unioning the shared root into each side in `run.sh`
itself, no `compare` change needed); a `.json`/`.gz`/`.zst`/content-sniffed
snapshot baseline's `dependency_scope` tag matching (`service.run_dump`'s
`include_dependencies` parameter already provides it); `--output-file` and a
bare `-o`/`--write` via `extra-args` (`compare` already has `-o` and a
repeatable `--write`); the compile-context-option family reaching through
`extra-args` (`--lang`/`--ast-frontend`/`--compiler*`/`--sysroot`/etc. were
already removed from `compare`/`dump`'s own CLI before this gap was first
written, in favor of `.abicheck.yml`'s `compile.*` — `scan` just hadn't
migrated yet, so this was never a `compare`-side gap); the default (or
`--no-pattern-verdicts`) pattern-verdict divergence (moot once routed:
`compare`'s modulation has been unconditional, with no off switch, since
D4). **A genuinely new (a) finding this commit's investigation also
surfaced, not previously documented:** the non-JSON/`json` `--format`
restriction and the scan-vs-compare *JSON schema shape* itself were never a
`compare` flag gap — `compare` already accepts every format `scan` did.
Once `mode: scan` routes to `compare` unconditionally, its JSON output
switches from `scan_schema_version`/nested `diff.findings` to
`report_schema_version`/root `changes` — **a documented breaking change to
the JSON shape**, not a remaining capability gap: any workflow step parsing
that file by its old shape must update to the canonical `ReportDocument`
shape.

**A separate, newly-confirmed gap this investigation surfaced, not yet
closed:** `compare --no-baseline` (the audit-only replacement for `scan`
without `--against`, ADR-068 D2) genuinely does not read `--sources`/
`--build-info`/`--depth` from its own CLI kwargs today
(`frontends/cli/commands/compare_no_baseline.py`'s `_run_no_baseline_compare_cmd`
only reads `headers`/`includes`/`lang`/`public_headers*`), does not honor
`--dry-run` (never checked), and supports only `json`/`markdown` formats
(`_SUPPORTED_FORMATS`) — confirming, not merely repeating, the narrower
audit-only capability set this entry's Action-routing comment already
described. This means the Action's *audit-only* `mode: scan` (no baseline)
cannot yet route to `compare --no-baseline` without narrowing what it
collects, and stays out of scope for this commit's file ownership
(`frontends/cli/commands/compare_no_baseline.py` is not among the files this
phase owns). Tractable when picked up: thread `sources`/`build_info`/
`depth`/`dry_run` from `_run_no_baseline_compare_cmd`'s own `kwargs` into
the audit pipeline the way the two-sided path already does, and extend
`_SUPPORTED_FORMATS`.

**Closed (2026-09-10 ADR-068 amendment).** The audit-only gap this
paragraph deferred to closed (see the 2026-09-10 amendment recorded on this
entry's own `compare --no-baseline` sibling above), and the mechanical
follow-up landed with it: `action/run.sh`'s routing predicate
(`_SCAN_AUDIT_ONLY_NEEDS_LEGACY_CLI`, the immediate successor to
`_SCAN_NEEDS_LEGACY_CLI` below) and every `MODE == "scan"` branch that
existed only to serve its legacy-CLI fallback are deleted -- there is no
`CMD+=(scan)` site left anywhere in `run.sh`. Every `mode: scan` request
(audit-only or baseline) now assembles `compare`/`compare --no-baseline`
through one shared branch, with `--severity-preset default` injected on the
audit-only shape whenever the caller stated no preset of its own (see the
sibling entry's own "composite Action's own translation has since landed
too" note for the full account, and `tests/test_action_run_sh_audit_gate.py`
for the end-to-end coverage against real fixtures).

Not fixed here (deferred to a follow-up in this same phase, once the audit-
only gap above closes -- historical text, kept for the record; see the
"Closed" note just above): `action/run.sh`'s `_SCAN_NEEDS_LEGACY_CLI` predicate
itself, and the ~17 `MODE == "scan"` branches it guards, are not yet deleted
— doing so safely requires the audit-only routing above to actually work
(today's Action still routes every audit-only `mode: scan` request to the
legacy CLI unconditionally, independent of this predicate), and a
line-for-line removal of a script this size without an Action e2e run to
verify against was judged higher-risk than shipping the two closed (c) items
and this ruling on their own. The routing predicate's *decision table* is
now complete (every condition ruled); what remains is mechanical: replace
the predicate's body with the two (b) hard-error checks plus the header/
include union fix, delete the legacy scan-CLI assembly branch and the now-
dead `_CLI_MODE == "scan"` downstream branches, once `compare --no-baseline`
closes the audit-only gap above.

### ~~`scan`'s JSON `diff.findings[]` entries never carry `gate_contribution`~~ — CLOSED (no longer applies)

**Closed (2026-09-11, ADR-068 Phase 6).** `scan` — and with it
`cli_scan_baseline.py`, `_baseline_finding_dicts`, and every `diff.
findings[]` JSON shape this gap described — was deleted outright when the
`scan` command was retired. There is no longer a second JSON envelope for
this field to be missing from: `compare`'s own `changes[]` entries already
stamp `gate_contribution` unconditionally (the half of this gap that was
never broken). Kept here, struck through, as the historical record; the
`tests/parity/runner.py` harness this entry describes (`RunOutcome.
gate_contributions`, the `None`-skip in what was `assert_full_parity()`)
was itself deleted in the same PR along with every other scan-vs-compare
comparison it existed to support — see `tests/parity/runner.py`'s own
module docstring for what `tests/parity/` is now.

Found in CodeRabbit review round 12 on PR #1172, while building
`tests/parity/runner.py`'s scan-vs-compare parity harness (ADR-068 plan
Phase 4). `compare`'s JSON `changes[]` always stamps a per-finding
`gate_contribution` field (ADR-049 D1, unconditionally — 0 included —
via `reporter.py`'s `_change_to_dict` calling
`severity.gate_contribution_for_change`). `cli_scan_baseline.py`'s
`_baseline_finding_dicts`, the equivalent builder for `scan --against`'s
`diff.findings[]`, never calls it and never emits the key at all — not "always
0", genuinely absent from the dict.

Not fixed here: `gate_contribution_for_change` needs the same
`severity_config`/`policy`/`kind_sets`/`policy_file` context `compare`'s
renderer already threads through, and `_baseline_finding_dicts` has five call
sites in `cli_scan_baseline.py` that would each need that context passed
in — CodeRabbit's own review flagged this as a "Heavy lift" beyond the scope
of a review-response round, and this PR's file ownership is scoped to
`checker.py`/`policy/**`/`cli_scan_baseline.py`/`service_scan.py`/
`action/run.sh`/`tests/parity/**`, not a redesign of that builder's call
graph. What *is* fixed here, within `tests/parity/**` ownership: the harness
itself no longer silently defaults a missing scan-side `gate_contribution` to
`0` before comparing it against compare's real (possibly nonzero) value, which
would have let a genuine divergence on this field read as false parity.
`RunOutcome.gate_contributions` is typed `dict[tuple[str, str], int | None]` —
`None` means "the raw finding dict never had the key" (scan's current
across-the-board state), a real `int` means the field was present and its
value read (compare's state, and scan's future state once this gap closes);
`assert_full_parity()` skips the comparison for a key where either side reads
`None` rather than coercing both to `0` first.

Tractable when picked up: thread the same evaluation context (severity
config, policy, kind sets, resolved policy file) `cli_scan_baseline.py`
already has in scope for the compare-parity verdict recompute into each of
`_baseline_finding_dicts`'s five call sites, and call
`severity.gate_contribution_for_change` per finding the way `reporter.py`
does. Once real values are emitted, the parity harness's own `None`-skip
becomes dead code (no test result changes, since a value that now compares
equal was previously skipped, not marked passing under a false default) — at
that point drop the skip and go back to an unconditional comparison so a
*future* regression on this field is caught structurally rather than by an
absent key silently reading as "no divergence".

## L2 performance bottlenecks found by the full-CLI harness (measured, not fixed)

Found while building `scripts/check_l2_cli_perf.py`. Recorded here rather than
acted on: that work was explicitly measurement-only, and each of these is a
production behaviour change needing its own design and its own evidence that the
change is worth it. Every number below is a real local measurement (gcc 13.3.0 /
castxml 0.7.0 / clang 18.1.3, 4 CPUs, Linux), reproducible with
`python scripts/check_l2_cli_perf.py --suite extended`.

### Interpreter startup dominates a small-input CLI run

`abicheck --version` — interpreter plus import plus Click tree construction,
before any work at all — costs **0.56–0.66 s**. A stored-snapshot/stored-snapshot
`compare` of a small fixture costs ~1.05 s end to end, so roughly **60% of it is
startup**. `compare --dry-run` (startup plus config and input resolution, diff
never run) costs 0.60–0.79 s, which bounds resolution itself at well under
0.2 s.

Consequences worth knowing before optimizing anything else: a change that halves
the actual L2 comparison work would move that run's wall time by under 20%, and
the full-CLI lane's absolute noise floor has to be set around this cost rather
than around the comparison's (hence `--regress-min-delta-seconds 0.5` by
default there, where the in-process gates use 0). A warm-cache `dump` is
*not measurably faster* than a cold one on a small fixture for the same
reason — see below.

Not investigated: which imports dominate, and whether lazy-importing the heavy
ones is feasible without breaking the registration-by-import-side-effect pattern
the CLI is built on.

### The `clang -M` include-graph pass is per-header, per-side, and never cached

Measured on the header-count axis (one library, N top-level public headers over
one shared `detail/` dependency closure, live/live comparison):

| top-level headers | observed `include_pass` invocations | wall |
|---:|---:|---:|
| 1 | 2 | 1.10 s |
| 8 | 16 | 2.48 s |
| 32 | 64 | 10.40 s |

Two separate things show up here. The invocation count is exactly
`headers × 2 sides` — one `clang -M` subprocess per top-level header per side,
with no batching across headers that share a dependency closure. And the wall
time grows **superlinearly** in header count (8x the headers costs ~9.5x the
time going 1→32), so this is not merely a constant per-header cost.

The separate cache finding: on a warm AST cache the header *extraction* count
drops to zero (the AST cache serves it) while the `include_pass` count stays
unchanged. The include graph is recomputed on every run regardless of cache
state. Combined with the startup cost above, that is why a fully warm small
`dump` measures ~0.93 s against a cold ~0.88 s — within noise, i.e. the cache
hit buys nothing observable at that scale even though it demonstrably happened
(proved by the counters, not the clock).

### A set of libraries pays full cost per library, and a shared header context does not reduce it

Five libraries compared as five per-library `compare` invocations (the only
supported shape — there is no declarative L2 bundle comparison), cost ~1.15–1.50 s
each and **4 header extractions each**, for a set total of ~6.3 s. That figure is
the same whether the five libraries share one dependency header or each have
their own:

| arm | resolved dependency headers | extractions (5 libraries) |
|---|---:|---:|
| shared context | 1 | 20 |
| distinct contexts | 5 | 20 |

The mechanism, which is the actually useful part: the header-frontend invocation
count is driven by the **top-level** headers, not by their dependencies, so
sharing a dependency header cannot reduce it. Each library still needs its own
parse of its own public header, and that parse pulls the shared dependency in
regardless of whether another library already parsed it.

**This claim was previously unsupported and is worth flagging as such.** The
first version of the fixture gave every library its own byte-identical copy of
`detail/core.h` under its own `libN/include/` path — and the AST cache keys on
each header's *resolved path*, so the "shared" arm shared nothing. The two arms
were both distinct-path workloads, and comparing them could only ever have
produced "no difference". The fixture now resolves the shared arm through one
physical file at a common include root, `header_contexts` is counted from the
resolved paths actually built rather than from the flag that requested them, and
the numbers above are from that corrected fixture. The conclusion happens to be
the same; the evidence for it did not exist before.

Recorded as a measurement of current behaviour, deliberately not as a claim that
cross-library sharing *should* be implemented: whether a cross-library
header-AST cache is worth its invalidation complexity is a real design question,
and `scripts/l2_real_profiles.py`'s oneDAL profile (five header-bearing
libraries across two compile contexts) is the realistic case to judge it
against.

### ~~`compare --format` repeated silently keeps only the last format~~ — CLOSED by the export grammar

Recorded while building the full-CLI harness against `main` at `f6aa2aae`, where
`compare --format json --format markdown -o out` exited 0 and wrote markdown
only, with no way to get both artifacts from one invocation. ADR-068's slices
7m/7n landed before this branch merged and closed it outright: `--format` is gone
and `-o FORMAT=DESTINATION` is repeatable, with every export rendered from the
one completed analysis.

Kept as a closed entry rather than deleted because the *measurement* survives and
is worth knowing: `check_l2_cli_perf.py`'s `compare_two_formats` scenario now
verifies that promise rather than trusting it. A single `compare` exporting both
`json=` and `markdown=` over a stored-old/live-new pair performs **2 header
extractions — exactly one side's worth**, so the second renderer demonstrably
does not re-run the analysis. (Stored/stored would have been the easier operand
pair and a useless test: the count is zero either way, so it could not
distinguish one analysis from two.)

### The `clang -M` include pass is unaffected by a warm AST cache — confirmed at `--repeat 3`

Re-confirmed after the cache-lifecycle fix below, now that a multi-repetition run
actually works: across three repetitions of the cold/warm sequence the header
*extraction* count goes 2 → 0 (the AST cache serves it) while `include_pass`
stays at 1 every time. The include graph is recomputed on every run regardless of
cache state, which is the same finding as the per-header scaling above seen from
the cache side.

### A labelled side-scoped `--include` appeared to suppress unlabelled global include roots

Observed once, on a real PVXS comparison, and **not yet reduced to a minimal
reproduction** — recorded so the next person does not lose the observation.
Invoking:

```
compare old.so new.so --include old:public=OLD_INC --include new:public=NEW_INC \
  --include EPICS_INC --include EPICS_INC/os/Linux --include EPICS_INC/compiler/gcc ...
```

failed with `fatal error: 'epicsTime.h' file not found`, despite `EPICS_INC`
being passed as an unlabelled (both-sides) include root and genuinely containing
that header. The same roots passed to a bare `dump` worked. Re-expressing every
root in the per-side labelled form made the comparison succeed.

So either unlabelled roots are dropped once any labelled side-scoped root is
given for that side, or they are ordered such that castxml does not see them.
`--include`'s own help text documents the two forms as composable, so if this
reproduces it is a defect rather than a usage error. Next step: a minimal
two-header fixture mixing one labelled and one unlabelled root.

## `--crosscheck KEY=LEVEL`'s engine half outlives its retirement ruling

ADR-068's second 2026-09-09 amendment rules `--crosscheck KEY=error`'s
promotion syntax **(b) — dropped, superseded**: every cross-source check
already reaches `compare` as an ordinary `ChangeKind`, so the underlying
capability (controlling one check's severity) exists as
`policy.overrides.<CHANGE_KIND>: error` in `--policy`/`.abicheck.yml`.

Phase 4's typed-API slice (2026-09-09) deleted the half it owned:
`ScanRequest.severities`/`enabled_checks` are gone with the request type, and
no typed caller can state either any more. **The `scan` CLI flag and its
engine plumbing are still there**, deliberately. Its promotion is not a
`scan`-local detail — it is ADR-064's `crosscheck_promotion_contribution`, a
*published* `ExitDecision` axis:

- `policy/exit_decision.py` carries the field and `resolve_exit_decision`'s
  parameter; it is emitted in every persisted `exit` block.
- `policy/exit_decision_precedence.py` carries it through a prior decision.
- `workflows/scan_abort_result.py` names it as a fold participant for a
  budget/evidence-contract abort.
- `schemas/__init__.py`'s `REPORT_SCHEMA_VERSION` 2.42 entry documents it on
  `compare`'s side of the report contract, and `action/run.sh` reads
  `promoted_crosscheck` out of `blocking_categories` to word its own verdict
  escalation.
- `scan_engine._crosscheck_severity_exit`/`_promote_published_gate` and
  `cli_scan_baseline`'s `info`/`warning` non-gating exclusion implement it.

Removing a published exit axis changes `compare`'s report schema as well as
`scan`'s and needs an ADR-064 amendment deciding what a consumer reading
`crosscheck_promotion_contribution` should see afterwards — a different
change from retiring a request field. It is not done in the typed-API slice,
and the slice does not pretend otherwise.

**What closing it looks like:** delete `--crosscheck` from `cli_scan.py`
(both halves — the `KEY=off` enable/disable side has no separate ruling, but
it is the same flag and `compare` runs every check unconditionally per plan
§3 #3), delete `_parse_crosschecks`/`_CROSSCHECK_LEVELS`/
`_PROMOTABLE_FINDING_KINDS`, drop `severities`/`enabled_checks` from
`run_scan_core`/`_run_baseline_compare`, delete
`_crosscheck_severity_exit`/`_promote_published_gate`/
`CROSSCHECK_BLOCKING_CATEGORY`, retire the `ExitDecision` field under an
ADR-064 amendment with its own `REPORT_SCHEMA_VERSION`/`SCAN_SCHEMA_VERSION`
entries, and update `action/run.sh`'s two `promoted_crosscheck` readers plus
`pr_comment_scan.py`'s `crosscheck_severities` promotion read.

## Retired CLI spellings still named in runtime messages

**Status:** one instance fixed (PR #1184), the repo-wide sweep deliberately
not attempted. Bug class:
`cli_surface.retired_spelling_in_remediation` in
`tests/regressions/manifest.py` —
check there first before restating the invariant.

Codex review on PR #1184 caught
`project_snapshot_legacy.materialize_release_variant_artifacts`'s
multi-variant ambiguity error still telling the caller to "pass an explicit
variant id (`--old-variant`/`--new-variant`)" after plan Phase 7j retired
both spellings with no alias. A user who followed the tool's own advice
landed straight in an exit-64 usage error — the CLI actively misdirecting
them. Fixed — twice. The first fix named the live `--variant` flag but
hard-coded the side to `old=`, so a caller whose *NEW* operand was the
ambiguous one was told to run something that fails just as hard (Codex
review, second round). The flag-existence oracle passed on that bug,
which is the lesson: **a live flag aimed at the wrong operand is still
broken advice.** The invariant is now the stronger one — *run the
advice*: the seed test takes the message's own `(e.g. --variant new=v1)`
example, re-invokes `compare` with it, and asserts the ambiguity is
actually resolved, parametrized over which side is ambiguous. A bare
`--variant ID` is not a safe fallback either, since it applies to both
sides and the unambiguous side does not declare that id — which is why
the side must reach the message at all.

The layering fix that makes it possible: the engine
(`AmbiguousVariantSelectionError`) states the fact and carries the
declared ids but names **no CLI flag**, because only the front end that
resolved both operands knows which side this package is;
`cli_compare_release_matrix._resolve_release_package_side` appends the
side-correct example. That is the same engine-error/CLI-wrapper split
`AmbiguousLibraryMatchError` already uses.

**What is not closed.** An AST sweep of non-docstring string literals under
`abicheck/` against every spelling in `scripts/retired_surfaces.py`'s
`RETIRED_SURFACES` reports **44 hits across 25 modules**, and several read
like the same defect already sitting in the tree:

- `pdb_utils.py:129` — `"locate_pdb: skipping network path %s (use
  --pdb-path to override)"` (`--pdb-path` left `compare` in Phase 7i and
  `dump` in Phase 7c)
- `reporter_markdown.py:297` — `"Unknown --show-only token: "`
  (`--show-only` folded into `--view` in Phase 5)
- `dumper.py:1527`, `dumper_elf_fallback.py:145` — `--dwarf-only`
- `cli_dump_helpers.py:133`, `frontends/cli/commands/dump.py` —
  `--debug-format`
- `cli_audit.py:77` — `--reconcile-build-context` (removed in Phase 7i)

**Why this is not a mechanical sweep, and why it was not attempted in the
same PR.** The hits are not decidable without reading each site against the
command that emits it:

1. A flag retired from `compare`/`dump` can still be **live on `scan`**,
   which kept the whole L2 compile-context and debug-resolution families
   (7a/7b/7c explicitly left `scan` untouched). A shared engine module's
   message naming `--dwarf-only` may be correct for the caller that
   actually reaches it.
2. `cli_compare_release.py`'s **unregistered** release engine legitimately
   still *defines* `--old-variant`/`--new-variant`/`--dso-only`/
   `--support-promise`/`--include-private-dso` as its own options — those
   are live option definitions, not stale advice, and account for 9 of the
   44 hits on their own.
3. Historical mentions in `model/change_catalog/` descriptions,
   `contract_relevance_types.py`, and `options/rulings.py`'s own ruling
   rationales are deliberate records of what a thing *used to* be called.

So a sweep-turned-gate would need roughly 25 hand-judged allowlist entries —
the shape AGENTS.md's own `IMPORT_CYCLE_ALLOWLIST` guidance warns is itself
a smell, and precisely the "large unreviewed allowlist shipped as if it
closed the class" outcome the bug-class discipline exists to prevent. It is
also a separate change from PR #1184's CLI option audit.

**Tractable when picked up:** go site by site, resolving each message
against the command(s) that can actually emit it — the fix for a genuinely
stale one is either naming the live spelling (as PR #1184 did) or scoping
the message per front end. Only once the real hits are down to the
legitimate three categories above is a gate in
`scripts/check_docs_contract.py` worth adding, extending the existing
`RETIRED_SURFACES` docs sweep to first-party Python with a *small*,
reasoned allowlist. Doing the gate first would invert that order and bake
today's 25 unexamined sites into an allowlist nobody revisits.

## `compare --depth binary` still performs a deep DWARF type walk the public evidence-depth contract says that rung skips

**Reopened 2026-09-11** (Codex review, PR #1220 doc follow-up) after an
earlier pass at that same PR incorrectly marked this entry CLOSED,
reasoning that deleting `scan` (ADR-068 Phase 6) made the gap moot because
the comparison it originally described needed a `scan` reading on one side.
That reasoning was wrong: `scan`'s own `--depth binary` behavior was never
the bug — it was only the *oracle* this entry originally used to show
`compare`'s behavior was inconsistent with something. Deleting `scan`
removes that oracle, not the underlying defect in `compare` itself, and the
entry's own body already said as much before being closed ("that question
is now `compare`'s alone to answer" — see below). Verified live against the
*current* code before reopening, not taken on Codex's word alone:

```
$ gcc -shared -fPIC -g old.c -o libold.so      # struct Point { int x, y; }
$ gcc -shared -fPIC -g new.c -o libnew.so      # struct Point { int x, y, z; }
$ abicheck compare libold.so libnew.so --depth binary -o json=-
```

reports `verdict: BREAKING` with a `type_size_changed` finding ("Size
changed: Point (64 → 96 bits)") and a `type_field_added_compatible`
finding, from DWARF alone (no headers passed on either side). That directly
contradicts `docs/use/evidence-depth.md`'s own published contract for this
rung (`| binary | L0/L1 exported symbols + binary metadata + debug-info
*presence* (no deep DWARF type walk, no L2 AST) + always-on pattern scan |`)
— "debug-info presence" promises only "is DWARF present", not "walk every
DWARF type and report on it". `policy/depth_projection.py`'s own module
docstring confirms this is by design, not an oversight: it documents `BINARY`
as covering both L0 *and* L1 ("no L2 AST", explicitly *not* "no debug info"),
and keeps layout/signature facts at the `binary` rung whenever DWARF
confirms them, "the way a real DWARF-informed binary dump would". The
*code*'s contract and the *docs*' contract disagree with each other, and
this entry's status must track that disagreement as still open — reworded
below to describe the `compare`-only shape of the question now that `scan`
is gone, but **not marked CLOSED**.

Original gap, kept for context: `scan --depth binary` extracted exported
symbols only, while `compare --depth binary` on the same two live binaries
still read DWARF and reported type-level findings that the symbols-only
view could not produce. The two commands were not wrong in the same way:
`scan` honoured the pin as an *extraction floor and ceiling*, while
`compare`'s `--depth` projection (`policy/depth_projection.py`) drops the
L3-L5 layers but does not restrict the L1 debug parse the resolution
already performed, so the pin acted as a ceiling on collected layers rather
than on read evidence. Which of the two was the intended contract at this
rung was left open as a `compare`-side question (ADR-063 Phase 8's
`--depth` ceiling) — that question is now `compare`'s alone to answer,
since there is no `scan` reading to compare it against any more, but it
remains unanswered: either `docs/use/evidence-depth.md`'s "no deep DWARF
type walk" promise needs fixing to match what `compare --depth binary`
actually does, or `policy/depth_projection.py`'s `BINARY`-keeps-L1 behavior
needs to change to match the documented promise. Neither has happened.

ADR-068 Phase 4's typed-API slice had fixed the adjacent *headers*-rung
divergence before scan's removal — `cli_scan_helpers._uses_debug_presence_only`
dropped DWARF at the `HEADERS`/`BUILD` rungs even when no headers existed to
replace it, so a header-less `scan` lost every type-level finding while
naming the rung it was asked for — and the parity suite pinned the `headers`
and unpinned rungs against `compare` as an oracle
(`tests/test_scan_depth_evidence_shortcut.py`). That decision function and
its seed tests were deleted with the rest of `cli_scan_helpers.py` in ADR-068
Phase 6. The `evidence.tier_shortcut_without_substitute` `BugClass` this gap
was registered against in `tests/regressions/manifest.py` was retired in the
same PR rather than retargeted: a repo-wide audit at retirement time found
`debug_presence_only` (the underlying dumper-level shortcut parameter,
still plumbed through `dumper.py`/`service_dump_cache.py`/
`service_dump_native.py`/`dumper_layout_backfill.py`/
`workflows/input_resolution.py`) has **no remaining production call site**
that ever passes `debug_presence_only=True` — every live caller forwards it
at its default `False`. The shortcut mechanism is therefore currently
unreachable from any command, so the bug class it protected has nothing left
to seed-test against. If a future change reintroduces a caller that computes
`debug_presence_only` from a depth/collect-mode decision (the "none of the
L3/L4/L5 collect-mode decisions has an equivalent... guard yet" gap the
original entry also named, which remains open and un-closed by this
retirement), re-add a `BugClass` entry for it — retargeted to that new
caller's own seed tests, not restored verbatim, since the invariant text
should describe the caller that actually exists rather than the deleted
`scan`-specific one.

## ~~A depth shortfall is a hard per-member `ERROR` on a directory `compare` but a soft assurance signal on a single-pair one~~ — CLOSED

**Closed in PR #1195**, in the same PR that surfaced it, after a Codex
review round showed the divergence was wider than this entry first
described. Kept here because the shape of the mistake is the reusable part.

Measured, the two paths disagreed on *every* row, not just hard-vs-soft:

| shortfall | scalar `compare` | directory `compare` |
|---|---|---|
| live, `--depth headers` | 0 | 4 (`ERROR`) |
| live, `--depth build` | 7 | 4 |
| live, `--depth source` | 7 | 4 |
| stored snapshot, `--depth source` | 0 (live-only carve-out) | 4 |

Two mechanisms existed and the fan-out reached the wrong one first.
`policy/depth_evidence_contract.py` is the one this repo documents as
closing the gap "for every `compare` caller from one place" — exit-7 axis,
recorded not raised, `build`/`source` only, live-extraction only — and
`service_compare_pipeline.classify_compare_pair` already called it with the
right `old_is_live`/`new_is_live` flags. But `resolve_compare_request`
called `enforce_requested_depth` unconditionally ~170 lines earlier, which
raised first and pre-empted that recording in exactly the cases it was
written for. The native scalar CLI escaped only because it does not route
through `resolve_compare_request` at all.

The fix: `resolve_compare_request` no longer calls `enforce_requested_depth`,
so the exit-7 axis governs every `compare` surface; the release fan-out
aggregates each member's `evidence_contract_error_contribution` with `max()`
the way it already aggregates the contract-coverage floor, and emits its own
stderr notice (the per-member `DiffResult` is discarded before the note a
single-pair run renders). All eight rows of the matrix now agree, and a run
without `--depth` is unchanged.

`dump`'s own floors and `workflows.bundle_stored_pair_compare`'s separate
`enforce_requested_depth` call were deliberately **not** touched — each is a
different command with its own tested contract, and
`test_cli_compare_bundle_facts_stored_pair.py` pins the bundle one
explicitly (a stored pair *does* hard-fail there on the `headers` rung).
That is a third behaviour for the same question, still open, and worth
reading before anyone unifies further.

The reusable lesson is the one the bug class
`cli_surface.capability_guard_diverged_from_pipeline` now records: when two
mechanisms implement the same rule and one is documented as "the one place",
the other one silently winning on a subset of surfaces is not a redundancy,
it is a divergence waiting for a front-end change to expose it.

## A directory `compare`'s `-H`/`--header` set is applied to every member, so header-derived findings are reported against libraries they do not belong to

Reproduced while verifying the `--depth`/header-graph behaviour above, on a
two-member fixture (`libfoo.so` built from `foo.h`, `libbar.so` from nothing
but its own source):

```
abicheck compare old/ new/ --header old=inc/foo.h --header new=inc_new/foo.h
libbar.so BREAKING  type_size_changed:Widget, type_alignment_changed:Widget,
                    type_field_type_changed:Widget, type_field_offset_changed:Widget,
                    exported_not_public:bar_fn, public_not_exported:widget_area, ...
libfoo.so BREAKING  type_size_changed:Widget, ... (the same four)
```

`Widget` lives in `foo.h` and is nothing to do with `libbar.so`, yet the
whole `Widget` change set is reported once per member, and `libbar.so`
additionally earns `exported_not_public`/`public_not_exported` findings for
the mismatch between its own exports and a header set describing a different
library. A release's finding count therefore scales with member count rather
than with what changed, and `--output-dir`'s per-library reports carry the
duplicates too.

The cause is a missing *transport*, not a missing model. `cli_compare_release.py`
resolves one `old_h`/`new_h`/`old_inc`/`new_inc` set for the whole release and
passes it unchanged to every `_compare_one_library` call — but that function
already takes its header list **per member** (`cli_compare_release_pairwise.py`'s
`old_h` parameter, forwarded straight to `_run_compare_pair`); only the caller
flattens it. And the per-member mapping already exists upstream:
`build-output.json` carries per-target `public_header_roots`/
`generated_header_roots`, `buildsource/baseline_publish.py`'s
`derive_baseline_libraries()` turns those into `actions/baseline`'s
`libraries[].header`, and `publish-baseline.yml` already dumps **each library
with its own headers** at a selectable `depth`. `BundleSpec.targets` is a list
of target ids and `TargetSpec.public_headers` exists per target, so the
declaration is in the project schema too.

So an earlier revision of this entry was wrong to frame this as "decide what
the attribution model is" and to propose naming conventions or a new
`.abicheck.yml` key. The model is declared; `compare` simply has no operand
shape that carries it. Three things close this, in increasing order of scope:

1. **De-duplicate first.** A header-derived finding that cannot be attributed
   to one member should be reported **once**, release-scoped, rather than once
   per member. That is the honest reading of the evidence ("which member this
   affects was not established") and it removes most of the pain with no
   guessing at all. The current N× duplication is the actual defect.
2. **Carry the map into `compare`.** A per-member header operand (mirroring
   `actions/baseline`'s own `libraries` JSON) so the fan-out resolves
   `old_h`/`new_h` per member. Small, given the seam already exists.
3. **Attribute by reachability** where no map is declared: a header-derived
   finding belongs to the member(s) whose export surface reaches the entity —
   `export_surface.compute_export_surface` and `type_reachability.py` already
   do exactly this per library. Must fall back to (1), never to silence.

Note that this is only half of header-aware package comparison. The other
half is where OLD's headers come from at all:
`buildsource/project_targets.py`'s `BUNDLE_CHECK_DEPTHS = {binary}` exists
because a bundle baseline stages raw binaries and `check-project.yml` has one
project-wide `header:` input, so `depth: headers` would parse *both* sides
with the current checkout's headers and make a header-only change silently
invisible — a false negative, strictly worse than this entry's over-reporting.
Staging bundle baselines as per-member snapshots (what single-target mode
already does) is what would let that restriction be relaxed.

Deliberately not attempted as part of the `--depth` fix: it is a separate
change in a different layer. Over-reporting is the safe direction — no finding
is *lost* today — which is why this is a gap rather than a blocker on the fix
that surfaced it.

## Suppression provenance stops at the display label outside the audit path

ADR-067 D3 says a disposition keeps the rule that made it — rule id, source
file, reason, label, expiry. `Change.suppression_rule` is not that record: it
is `SuppressionOutcome.rule_label()`'s deliberate `label or reason` collapse,
so a waiver stating both publishes one and silently drops the other, along
with the file it lives in and when it lapses — exactly what a reviewer needs
to decide whether the waiver still applies.

`compare --no-baseline` had this in all four of its projections and was fixed
in PR #1188: each suppressed entry now carries the run's own
`DispositionLedger.rule_for` record, and the regression test is parametrized
over `NO_BASELINE_SUPPORTED_FORMATS` so a format added later is held to it.
Two-sided `compare`'s **JSON** was already correct before that
(`reporter.py`'s `_suppressed_change_entry` emits `rule.to_dict()`).

Two readers are still on the display label alone. Both were found by grepping
every `suppression_rule` reader once the audit's own were fixed, and both are
outside the scope PR #1188 was opened for, so they are recorded here rather
than folded into it:

1. **`sarif.py`** (two-sided `compare`) — the suppression's `justification`
   interpolates `change.suppression_rule` only. Since the same run's JSON
   already publishes the full record, this is a *between-formats* split of one
   report: a SARIF consumer sees strictly less than a JSON consumer of the
   identical run.
2. **`cli_scan_baseline.py`** (`scan --against`) — each suppressed
   `findings[]` entry sets `entry["suppression_rule"]` and carries no
   provenance field at all.

Fixing either is the same shape as the audit's fix: route the run's
`DispositionLedger` to that entry builder and read `rule_for(change)`, rather
than re-evaluating the rule set (which can name a different rule than the one
that actually fired, since a finding's fields may have been enriched after the
match). Neither needs a new mechanism — only the existing one wired to one
more builder.

Registered as open residuals on the `report.finding_entry_builder_parity`
bug class in `tests/regressions/manifest.py`'s report sibling
(`tests/regressions/manifest_report.py`), so the next person to touch that
class sees them without re-deriving the grep.

## `compare --no-baseline` crashes on any real ELF shared library

Auditing a real ELF shared library raises an **uncaught**
`NoBaselineInvariantError` — a Python traceback, not a diagnostic — from a
bare CLI call:

```console
$ abicheck compare --no-baseline /lib/x86_64-linux-gnu/libm.so.6 -o json=-
abicheck.policy.no_baseline_findings.NoBaselineInvariantError: a snapshot
compared against itself must never produce a comparison finding -- if this
fires, a detector is reading non-identity state. Offending findings:
visibility_leak(<visibility>)
```

Reproduced on `main` at `f5df70dd` as well as on the branch that found it,
so it is not a regression from any in-flight work. It was found only because
a test fixture in `tests/test_compare_no_baseline_cli.py` was made more
realistic (see that file's `_a_live_binary_this_host_can_audit`); every
committed G20 fixture is a stored snapshot, so no existing test audits a
real live library at all — which is why a crash on the command's most
obvious input survived.

**The invariant's own message misdiagnoses it.** Nothing is reading
non-identity state. `diff_platform_elf_dynamic._diff_visibility_leak` is
deliberately single-sided — its body opens `del new  # detector is
intentionally old-library-only` — and reports internal-looking symbols
exported from one snapshot under `elf_only_mode`. That is candidate-side
hygiene, exactly the category `policy/no_baseline_findings.
is_one_sided_finding` exists to recognise. But the detector emits a plain
`make_change(...)` carrying **neither** marker that predicate looks for
(`cross_source_evolution`, `candidate_side_enrichment`), so
`partition_no_baseline_findings` files it under `identity`, and
`check_no_baseline_partition` raises.

There is an irony worth recording, because it is the actual lesson:
`is_one_sided_finding`'s docstring argues at length against a `ChangeKind`
allowlist on the grounds that it "would drift out of sync with the detector
registry." The marker-based design it chose instead has drifted the *other*
way — a genuinely one-sided detector that never got a marker. Neither
mechanism is self-enforcing; the missing piece is a check that a detector
ignoring its `new` argument emits marked findings.

**Why it was not fixed where it was found.** The obvious patch — set
`candidate_side_enrichment` on the finding — is not local. That marker is
read on the ordinary two-sided `compare` path too, where this detector also
runs and reports leaks in the OLD library, so setting it relabels a finding
in the main command. And `_diff_visibility_leak` is unlikely to be alone:
any detector that `del`s or ignores `new` is in the same position, so the
real fix is an audit of that whole set plus a gate, not a one-line change
to the one instance a traceback happened to name. That is a change with its
own review surface, and it was out of scope for the pull request (#1188)
that found it — which touches neither file.

**Fix shape, for whoever takes it:**

1. Enumerate every detector under `abicheck/diff_*.py` whose body ignores
   its `new` snapshot argument (`del new`, or never referencing it). That
   set is the population, not `visibility_leak` alone.
2. Decide per detector whether it is candidate-side hygiene (mark it) or a
   genuine comparison detector that happens not to need `new` yet.
3. Mark the hygiene ones, and check what that does to two-sided `compare`'s
   reports and gating before assuming it is inert there.
4. Add the missing self-enforcement: a gate — the AI-readiness script is
   the natural home, alongside `fact-detector-misuse` — asserting that a
   detector which ignores `new` emits only marked findings. Without it the
   next single-sided detector reintroduces this exact crash.
5. Add a test that audits a **real live shared library**, not a stored
   snapshot. Its absence is why this shipped;
   `tests/test_compare_no_baseline_cli.py`'s helper documents the platform
   traps (a soname is not a path; macOS keeps system libraries in the dyld
   shared cache; a PE executable has no export directory).

Registered as `test_fixture.host_artifact_assumed_capability` in
`tests/regressions/manifest_report.py`'s sibling
`tests/regressions/manifest_tool_surface.py` for the fixture half; the
detector half above has no registry entry yet, deliberately — it is a real
open defect, not a closed class.

## `compare`'s migrated cross-source checks drop `--since`'s changed-path confidence boost

Found during the ADR-068 Phase 6 doc follow-up (a stale mention of the
retired `scan --since` in `catalog/cases/case181_xcheck_public_to_internal_dependency/README.md`
prompted checking whether the claim still held).

`buildsource/cross_source_checks.py`'s `CrosscheckConfig.changed_paths` lets
a check (currently `public_to_internal_dependency`) report a finding at
higher confidence when the internal declaration it reaches lives in a file
the revision actually changed — "this call reaches a file that changed this
revision" is a stronger signal than "this call reaches *something*
internal". The retired `scan --since` wired its changed-path set through to
this automatically.

`compare()`'s own migrated cross-source-evolution path
(`checker.compare()` → `workflows.cross_source_evolution.
compute_cross_source_evolution(old, new)`) does **not** currently accept or
forward a changed-path set at all — the function's signature is
`(old: AbiSnapshot, new: AbiSnapshot)`, with no `changed_paths` parameter,
even though `compare`'s own `--since`/`--changed-path` flags exist and are
already threaded through to other consumers (`cli_compare_helpers.py`'s
`changed_paths` parameter, ADR-043 D7 POI scoping — comment-tagged
`# ADR-068 Phase 2c: ADR-043 D7 POI scoping`). So `public_to_internal_dependency`
is always reported at the lower "reaches something internal" confidence
under `compare` today, regardless of `--since`/`--changed-path`.

Tractable when picked up, but **`checker.compare()` has no `changed_paths`
parameter to thread today** (verified against its real signature,
`abicheck/checker.py`'s `def compare(old, new, suppression=None, *,
policy=..., ...)` — no `changed_paths` anywhere in it) — an earlier version
of this entry wrongly described the value as already existing on
`compare()` and needing only to be threaded further down. Adding a
`changed_paths` parameter to `checker.compare()` (and updating every
caller that already resolves a changed-path set — `cli_compare_helpers.py`
already has one — to actually pass it through) is itself the first piece
of required wiring work, not a step that can skip straight to modifying
`compute_cross_source_evolution`/`CrosscheckConfig`. Needs a regression
test asserting the confidence *does* change under `--since`/
`--changed-path` on a real (not just internal-API) `compare` invocation,
not only an internal `run_crosschecks(...)` call — the gap here was
invisible to internal tests precisely because nothing exercises the public
`--since` flag's effect on this specific check's confidence.



## `compare --dry-run`'s cost preview does not reflect `--since`'s changed-path seeding

Found during the PR #1220 doc follow-up (Codex review): a two-sided
`compare old.so new.so --depth source --since origin/main --dry-run`
example claimed the dry run "prints the translation units the seed selects
and the projected per-layer cost without scanning". Verified live against
the current code before fixing the doc: it does not.

`build_compare_dry_run_result` (`frontends/cli/compare_dry_run.py`) always
renders `"source scope: target on each side (compare has no PR change
seed)"` whenever `collect_mode` is `source-target`/`source-changed`/
`graph-full`, and its "Cost preview" section is built from
`workflows.compare_cost_preview.estimate_compare_dry_run_cost`, whose
signature accepts no `since`/changed-path parameter at all — so the TU
counts and per-layer cost it prints are the *unseeded*, full-target
numbers regardless of `--since`. The reason is ordering, not a missing
render field: `cli_compare_helpers.py`'s `--dry-run` branch calls
`emit_dry_run(...)` (which raises `SystemExit`, per `dry_run.py`) **before**
`_enrichment.resolve_compare_enrichment_inputs(since=since, ...)` —
the call that actually interprets `--since` and localizes `collect_mode` —
ever runs. `--since` genuinely does narrow the real (non-dry-run) replay;
only the dry-run preview is blind to it.

Tractable when picked up: resolve the changed-path seed (and its resulting
localized `collect_mode`/TU set) *before* the `--dry-run` emit, the same
way `--dry-run` already reflects other resolved-but-not-yet-executed
decisions (depth, headers, tool discovery) rather than echoing raw CLI
input back. Needs a regression test asserting the dry run's own TU
count/cost preview actually changes between `--since origin/main` and no
`--since` on the same real inputs, not only that the flag is accepted.

## `compare --depth binary` ignoring matrix-wide `--sources`/`--build-info` is untested

Found while retiring `tests/scenarios/ci_gating.yaml`'s
`SC-SCAN-BINARY-DEPTH-MATRIX-ARGS` scenario and its automated test
(`test_sc_scan_binary_depth_matrix_args`) in the ADR-068 Phase 6 doc
follow-up: the scenario's premise was that a CI matrix builds one command
template and varies only `--depth`, leaving `--headers`/`--sources`/
`--build-info` present on every rung, and the binary rung must ignore the
deeper inputs and skip pattern-scan/L3 collection. The retired `scan`
command exposed this as a checkable `coverage` array
(`layer`/`status` rows) plus a `pattern_scan.files_scanned` field; verified
live, `compare -o json=...` emits neither at any `--depth`. `compare
--depth binary` does reach the same verdict/exit code on the scenario's
fixture, but nothing currently asserts it *skipped* the deeper collection
rather than merely projecting an already-collected superset down afterward
-- the two are observably different only through the now-gone coverage
block.

Tractable when picked up: decide what `compare`'s own coverage-reporting
surface should look like for this question (a `--depth`-scoped rung either
was or wasn't collected), add it, and un-retire the scenario against it.

### ADR-061 gap F: thirteen nested `buildsource`/`compat`/`impact` modules still carry no disposition

[ADR-061](adr/061-responsibility-package-architecture.md)'s gap F requires
every unclassified first-party module under `abicheck/` to carry one of
three recorded dispositions (migrate/retain/accept) — "unclassified for
now" is explicitly not one of them. `architecture/dispositions.yaml`
already carries this for every *root* `abicheck/*.py` module (its own
schema requires a direct root path, so it structurally cannot record a
nested one), and `architecture/debt.yaml`'s `files` list requires
`baseline_lines >= 800` (this repo's production file-size cap), so it
cannot record a small nested leaf module either. A repo-wide re-audit for
this gap (enumerating every `.py` file under `abicheck/` not already
covered by a canonical layer directory, a layer's `legacy_paths`, or an
existing `debt.yaml` entry) found 44 such nested modules, all under
`abicheck/buildsource/`, `abicheck/compat/`, `abicheck/impact/`, and
`abicheck/schemas/`. 31 of them were classified by adding them to the
appropriate layer's `legacy_paths` in `architecture/modules.yaml` (the
"migrate through a named responsibility slice" disposition — verified,
not merely asserted, by a full `scripts/check_architecture.py` run showing
no new `dependency-direction`/`dependency-cycle`/`unclassified-import`
findings after the change; three of the thirty-one — `entity_identity.py`/
`entity_resolver.py`/`source_graph_query.py` — are pure re-export facades
and are additionally registered in `architecture/modules.yaml`'s `facades`
list so `_check_facade`'s delegation-only rules apply to them too). The
remaining
thirteen could not be classified
the same way without breaking that verification, and are recorded here
instead, following the same "trial classification measured N new
violations, blocked" pattern this ADR's other gaps already use for
`comparability.py`/`build_context.py`/etc.

**Blocked `migrate` dispositions**, three shapes:

- *Caller-forced* (the common case below): each module's own outgoing
  imports are clean for its named target layer, but a *different*,
  already-classified module imports it directly from a layer that target
  does not allow, so classifying it would immediately trip
  `check_architecture.py`'s `dependency-direction` check. Each needs the
  same fix this ADR's other blocked entries name: route the offending
  caller through a workflows-owned wrapper (or otherwise decouple it)
  before the target classification is safe — not attempted here, per this
  ADR's "not the same-PR fix" bar for migrations that already have
  caller-side test/behavior surface to preserve.
- *Self-dependency* (`build_evidence.py` and `graph_impact.py`, the two
  exceptions below): the module's *own* outgoing import, not a caller, is
  what blocks its target classification. The caller-side remedy above
  (wrap or decouple the caller) does not apply here — the fix is to move
  or decouple the blocking dependency inside the module itself.
- *Mixed-responsibility* (`fact_set.py`, `impact/use_case_impact.py`, and
  `evidence_report.py`, the three exceptions below): the module itself
  bundles two structurally different responsibilities whose real callers
  are already split across incompatible layers. Neither the caller-side
  nor the self-dependency remedy applies — no single target layer is even
  the right answer until
  the module is split along its own seam.

- `abicheck/buildsource/build_evidence.py` (target: `model` — its own
  docstring is literally "Build-system-neutral build evidence model", and
  `abicheck/buildsource/pack.py` (already `model`-classified) imports it
  directly, ruling out `extract`) — a *self-dependency* block: it itself
  imports `.comdat_groups` (`extract`-classified), which `model`'s empty
  `may_import` forbids. Unlike every other entry below, no caller needs to
  change — `comdat_groups` (or the piece of it this module actually uses)
  would need to move or be decoupled from this module directly.
- `abicheck/buildsource/graph_impact.py` (target: `compare` — its own
  docstring: "Structured graph impact/proof-path data attached to
  findings"; it deliberately *enriches an existing `Change` finding*
  rather than extracting a fact, the same shape as its already-`compare`
  -classified `graph_reconcile.py` sibling — `source_graph_findings.py` is
  *not* a `compare`-classified precedent despite a similar-sounding role:
  `architecture/debt.yaml` records it with target `extract/build-or-source`,
  and it carries no `modules.yaml` classification of its own today — not
  `extract`, which this module's previous classification in this change
  wrongly assigned it, laundering a real boundary issue instead of
  recording it) — a *self-dependency* block, verified empirically by trial
  classification: it itself imports `.call_graph` (`extract`-classified),
  producing four new `compare -> extract` findings the moment it is
  classified `compare`, since `compare`'s `may_import` is `model` only.
  `call_graph` (or the specific pieces this module actually uses from it)
  would need to move or be decoupled from this module directly.
- `abicheck/buildsource/build_output.py` (target: `extract`, alongside its
  sibling adapters) — blocked because `abicheck/cli_project.py`
  (`frontends`) imports it directly; `frontends` may not import `extract`.
- `abicheck/buildsource/merge_support.py` (target: `extract`) — blocked
  because `abicheck/cli_buildsource_merge.py` (`frontends`) imports it
  directly.
- `abicheck/buildsource/evidence_policy.py` (target: `policy`, per its own
  D7 verdict-modulation/`require_evidence`-gate docstring) — blocked
  because `abicheck/cli_buildsource_helpers.py` (`frontends`) imports it
  directly; `frontends` may not import `policy`.
- `abicheck/buildsource/fact_set.py` — **not a single-target-layer case at
  all**, unlike every other entry here: this module bundles two
  structurally different responsibilities (`rollup_coverage`/
  `rollup_fact_set`/`fact_set_rollup_is_inconsistent`/
  `incomplete_families`, a dependency-free per-TU fold — genuinely
  `extract`-shaped, and `check_fact_set_compatibility`/
  `check_fact_compatibility`, an old/new pairwise comparison rule —
  `compare`/`policy`-shaped) under one file, and its callers split along
  exactly that seam: `abicheck/buildsource/source_link.py` (`extract`)
  uses only the rollup half; `abicheck/buildsource/inputs_validate.py`
  (`extract`) uses both; `abicheck/buildsource/source_diff.py` (`compare`)
  and `abicheck/analysis_assurance.py` (`policy`) use only the
  compatibility half. No single classification — `extract` included, this
  entry's own earlier revision wrongly proposed — can be correct while
  both halves stay in one file, since `compare`'s and `policy`'s
  `may_import` never include `extract`. Splitting the file is necessary
  but not sufficient on its own: the compatibility half's natural owner is
  `compare` (it's an old/new pairwise comparison rule, the same shape as
  `source_diff.py`'s own responsibility — `model` would mis-own it, since
  `model` holds entities/values, not comparison algorithms), but
  `inputs_validate.py` itself is `extract`-classified and calls
  `check_fact_set_compatibility` directly; `extract`'s `may_import` is
  `model`/`storage` only, so moving the compatibility half to `compare`
  would immediately turn `inputs_validate.py`'s own call into a new,
  forbidden `extract -> compare` edge. The real fix therefore has two
  parts, not one: the internal split (rollup functions to `extract`, the
  compatibility-check functions to `compare`) *and* moving or interposing
  `inputs_validate.py`'s own call site (e.g. routing it through a
  `workflows`-owned caller, the same pattern this ADR's caller-forced
  entries already use) — a combination neither the caller-forced nor the
  self-dependency shape above covers on its own, and not a single
  blocked-migrate target either; recorded here as its own third shape.
- `abicheck/impact/use_case_impact.py` — the identical mixed-responsibility
  shape as `fact_set.py`: `build_use_case_impact()` is comparison-time
  orchestration (`workflows`-shaped — not this module's actual
  `architecture/modules.yaml` classification, since it carries none, but
  the layer its own content fits), but the same file also defines
  `render_use_case_impact_lines()` (text/markdown rendering) and
  `add_use_case_impact()` (mutates a serialized JSON report dict) — both
  report-shaping, not orchestration. A `workflows` classification (as an
  earlier version of this change assigned it) would bless report
  construction as workflow logic and let a future workflow-only dependency
  reach those two functions unchecked. No single classification is correct
  while all three stay in one file; the real fix is the same shape as
  `fact_set.py`'s — split the report-shaping functions out to a
  `report`-owned sibling.
- `abicheck/buildsource/evidence_report.py` — the identical
  mixed-responsibility shape again: `resolve_side_pack()`/
  `intrinsic_coverage()`/`optional_coverage()`/`detect_coverage_asymmetry()`/
  `diff_embedded_build_source()`/`prepare_embedded_build_source()`/
  `attach_evidence_metrics()` are pack resolution and diff orchestration
  (`workflows`-shaped — again, the layer its content fits, not a real
  classification this module carries), but `coverage_lines()`/
  `compare_side_coverage_lines()`/`capability_lines()` in the same file
  construct the human-readable evidence-coverage report text — `report`-
  shaped. Same fix shape as the two entries above: split the rendering
  functions out to a `report`-owned sibling before either half gets a
  real target.
- `abicheck/compat/descriptor.py` (target: `extract` — ABICC XML
  descriptor parsing, the same shape as the already-`extract`-classified
  `compat/abicc_dump_import.py`) — blocked because `abicheck/compat/cli.py`
  (`frontends`) imports it directly (both the runtime `parse_descriptor`
  call and a `TYPE_CHECKING`-only `CompatDescriptor` reference).
- `abicheck/impact/engine.py` (target: `workflows` — its `assess_change`
  builder is called from `appcompat.py`/`appcompat_consumer_impact.py`,
  both already `workflows`) — blocked because
  `abicheck/post_processing_reachability.py` (`policy`) also imports it
  directly (lazily); `policy` may not import `workflows`.
- `abicheck/compat/_helpers.py` (target: `frontends` — split directly out
  of `compat/cli.py` per its own module docstring, implements ABICC CLI
  translations, imports `click`, and is imported only by `compat/cli.py`
  itself, already `frontends`-classified) — blocked because its own body
  imports `..policy.classification` directly, which `frontends`' `may_import`
  (`model`/`workflows`/`report` only) forbids. Its sibling `compat/_errors.py`
  carries no such import (only `click` and the already-`model`-classified
  `errors.py`) and was reclassified to `frontends` in this same pass —
  verified empirically, not merely asserted, the same way every `migrate`
  disposition in this gap was. Classifying `_helpers.py` under `workflows`
  instead (as a superficial fix) would not be a correct disposition: it
  would paper over the real edge — `compat/cli.py` (`frontends`) reaching
  `policy.classification` through this one intermediate file — rather than
  recording it as the blocked `frontends -> policy` case it actually is.

**Retain, deliberately unclassified**:

- `abicheck/buildsource/source_graph.py` — a pure back-compat re-export
  facade (its own docstring: "Back-compat facade: source-graph values/
  construction/comparison split"), not a real implementation module. Every
  name it used to define now lives in already-classified siblings
  (`model.graph_facts`/`model.source_graph`/the now-`model`-classified
  `source_graph_query.py` for values, `extract`-classified
  `source_graph_build.py`/`source_graph_build_source_abi.py` for
  construction, `compare`-classified `source_graph_compare.py` for
  comparison); this module only re-exports those names (`X as X`) so a
  pre-existing `from .source_graph import ...` call site keeps resolving.
  No internal first-party module actually imports it any more (every real
  internal caller already imports the split-out siblings directly) — it
  exists purely for external/legacy callers. Classifying it `workflows`
  (as an earlier version of this change did) would misstate its
  responsibility: it coordinates nothing, and giving a pure re-export
  facade a real layer identity is exactly the kind of false ownership
  this ADR's `facades` mechanism exists to prevent elsewhere. Unlike
  `dispositions.yaml`, `architecture/modules.yaml`'s `facades` list is
  **not** root-path-limited — `check_architecture.py` resolves each entry
  as `facade.replace(".", "/") + ".py"`, which reaches a nested module
  like `abicheck.buildsource.source_graph` just as well (verified: adding
  it there and running `check_architecture.py` does invoke
  `_check_facade` against it). The real reason it isn't registered there
  is that it doesn't satisfy that check's stricter delegation-only rules:
  it has no `__all__`, and it carries a real `__getattr__` function body
  (a deliberate, documented lazy import breaking a real
  `source_graph -> source_graph_findings -> source_graph` cycle the
  AI-readiness gate would otherwise reject) — trial-registering it
  produces exactly those two `facade-exports`/`facade-logic` findings.
  Making it pass would mean removing that cycle-breaking `__getattr__` or
  restructuring the module, a real code change out of scope for this
  docs-only pass. Recorded here as a retained, deliberately-unclassified
  exception instead, with the concrete blocking findings named rather
  than asserted.
- `abicheck/impact/model.py` — the identical shape gap B already
  established for `checker_policy.py`/`contract_gating.py`/`reclassify.py`:
  `abicheck/checker_types.py` (`model`, whose `may_import` is empty)
  imports `ImpactAssessment` from this module directly and statically
  (`from .impact.model import ImpactAssessment`), so the module cannot be
  classified into anything other than `model` itself without turning that
  pre-existing edge into a real `dependency-direction` violation — but the
  module's own body imports `..policy.evidence_status` (`Confidence`,
  `ReachabilityState`), which `model`'s empty `may_import` equally
  forbids. No single ADR-061 layer admits both directions at once, the
  same conflict gap B's own worked example (`DiffResult`/`checker_policy`)
  describes. Its sibling `impact/engine.py` above depends on this module
  too and inherits the same constraint, which is part of why it stays
  blocked rather than reclassified on its own.

Not fixed here, matching this ADR's own migration-rules bar ("a real,
separate migration slice, not a same-PR fix"). This applies differently
per shape, not as one blanket remedy: each *caller-forced* entry above
names the specific caller that would need to route through a
`workflows`-owned wrapper (mirroring gap A's `cli_dump_helpers.py ->
header_conditionals.py` precedent) before its target classification
becomes safe; each *self-dependency* entry (`build_evidence.py`,
`graph_impact.py`) instead needs its own blocking import moved or
decoupled, with no caller-side fix at all; each *mixed-responsibility*
entry (`fact_set.py`, `impact/use_case_impact.py`, `evidence_report.py`)
needs an internal split before either half gets a target. The two
*retained* entries are not
migration targets at all, but for two different reasons, not one:
`source_graph.py` is a pure facade with no offending caller in the first
place (it exists purely for external/legacy call sites); `impact/model.py`
is the opposite — it has a real, named offending edge
(`checker_types.py -> impact.model -> policy.evidence_status`), the same
"no single ADR-061 layer admits both directions" conflict gap B's own
worked example describes, and is forced *toward* (never actually
classified as) `model` because that conflict has no caller-side or
self-dependency fix available today — not because there's no caller to
fix. It carries no `architecture/modules.yaml` entry at all, same as
every other entry in this file. Tractable per-entry, not as one slice —
`build_output.py`'s single `frontends` caller is independent of any other
entry's fix.

## The `compare --no-baseline` audit report carries no per-finding provider attribution, so `provider_assertions` is unvalidated

Every G20 audit case in `catalog/ground_truth.json` declares
`provider_assertions` — which providers must corroborate each finding
(e.g. case151: `private_header_leak` from **both** `public_header_ast` and
`source_index`; case148: `header_build_context_mismatch` from
`build_config` + `public_header_ast`). Legacy `scan` published these as
`crosscheck.providers`, and `validation/scripts/run_special_cli_examples.py`
checked them. ADR-068 Phase 6 retired the whole-audit orchestrator
(`scan_engine.py`) that built that block, and no replacement projection
landed in `report/no_baseline.py`, so the assertion is now unchecked by
anything.

**Measured, not assumed:** the rich and thin fixtures of case151 —
the case that exists purely to show corroboration growing with evidence —
produce *byte-identical* public reports today. Both answer
`evidence_tiers: ["elf", "header"]` and a single `private_header_leak`
finding. There is no public signal to validate the assertion against, so
the runner records `unvalidated_assertions: ["provider_assertions"]` and
`collect_full_example_matrix.py` surfaces it on the row (the same
mechanism as `kinds_strict`). That makes the gap visible in the matrix
artifact; it does **not** detect a regression that drops a provider while
still emitting the expected finding kind, and those rows still count as
`COVERED` (Codex review, PR #1225, raised twice — the second time
correctly pointing out that metadata alone changes no coverage
accounting).

**Why it was not closed in PR #1225.** The data already exists and the
fix is small and precisely located: `CrosscheckResult.providers` is
computed and serialized today, and
`workflows/cross_source_evolution._run_one_side` already reads it
(`evaluated = check in result.providers`) and discards the list — so
closing this means stamping `result.providers[check]` onto the emitted
`Change` and projecting it. What makes it a separate change is not
size: it bumps a **published** report schema
(`AUDIT_REPORT_SCHEMA_VERSION`, plus the two-sided compare report, since
`compute_cross_source_evolution` runs on both paths) and adds a public
`Change` field. This file's own root contract is explicit that a schema
or public-interface change "still needs its ADR and migration" and is not
something a repair PR folds in as a side effect.

The alternative offered in review — mark rows with a non-empty
`unvalidated_assertions` as `UNRESOLVED` — was declined for a stated
reason, not skipped: `validation/CLAUDE.md`'s matrix contract requires one
`COVERED` row per ground-truth entry and no `UNRESOLVED` rows, and *all
ten* audit cases declare `provider_assertions`, so it turns a currently
green required lane red for a capability removed upstream in PR #1211.
That is a maintainer call about blocking on the follow-up, not a
repair-PR decision.

## `Visibility.PUBLIC` used to be a proxy for "declared in the public API", conflating source presence with dynamic export — **fixed**

Reported against a real comparison, alongside the three defects the
"stop inferring public-contract membership from symbol name shape" change
fixed. **This one is now fixed too** — by the model/schema change it always
needed, not by a call-site patch. The account below is kept because the
shape of the conflation, and the reasoning about what a fix had to do, is
the load-bearing part; what changed is the last section.

`model/vocabulary.py`'s `Visibility` has three states, and its own comments
showed the conflation:

```python
PUBLIC = "public"      # default visibility / exported
HIDDEN = "hidden"      # __attribute__((visibility("hidden")))
ELF_ONLY = "elf_only"  # present in ELF symbol table, not in headers
```

`PUBLIC` meant *both* "declared in a public header" and "dynamically
exported"; `ELF_ONLY` meant "exported but not declared". There was no state
for the fourth, entirely ordinary combination: **declared in a public header
and not dynamically exported** — an inline member, a function the optimizer
fully inlined away, or one a `-fvisibility=hidden`/version-script change
stopped exporting. Detectors then used `f.visibility != Visibility.PUBLIC:
continue` as if it answered "is this declaration part of the public API",
which it did not.

`diff_namespaces.py` showed the consequence (`_func_index_items`,
`_collect_public_declared_names`, `_batch_demangle_public` all filtered this
way): the removal *event* is "absent from the new-side index", and a
declaration that is still present in the header — byte-identical, still
compiling for consumers of both header sets — dropped out of that index
purely because its emission changed. The run then reported a source-API
removal for an API that was not removed. The reported case was a build whose
default and public-only artifacts had **byte-identical headers** and differed
only in whether one method was dynamically exported; the two sides classified
as `PUBLIC` and `ELF_ONLY` respectively, and the surviving overload was
reported as removed.

The fix was not to ignore export changes for inline functions: a disappearing
dynamic export can still break an already-linked binary. The two facts are
independent and both matter, so the result reads as two findings, not one
wrong one:

- source declaration — still present, no removal;
- binary export — changed, evaluated on the binary-contract axis.

### How it was fixed

`model/surface_facts.py` owns the split. `Function` and `Variable` each
carry three `Fact[bool]` siblings — `declared_in_headers_fact`,
`in_public_contract_fact`, `binary_exported_fact` (schema v46,
`storage/fact_codec.py`) — and that module's accessors are the only
supported way to read them, each named for the question it answers
(`declared_in_headers`, `in_public_contract`, `binary_exported`, plus the
consumer-shaped `in_public_surface`, `is_abi_visible`,
`in_source_declaration_index`, `declaration_confirmed_absent`,
`is_export_confirmed_absent`). There is deliberately no fourth merged
boolean. Because they are real `Fact[...]` fields, the
`fact-detector-misuse` AI-readiness gate covers every reader.

The three points the original entry named, and what each became:

1. **The model change.** Done as above; `AbiSnapshot`'s `SCHEMA_VERSION`
   went to 46. A pre-v46 snapshot has no such keys, and
   `model/surface_facts.py`'s legacy bridge derives all three from the
   stored `visibility` value, marked `PARTIAL` with a
   `derived-from-legacy-visibility` diagnostic — so a stored baseline
   classifies exactly as it did before (pinned by
   `tests/test_surface_fact_split.py::TestLegacyBridgeIsBehaviourPreserving`)
   and the weaker provenance stays visible rather than being laundered into
   a producer's own claim. No enum member establishes (a) in either
   direction: not a positive out of "it was exported", and not a negative
   out of `ELF_ONLY` either, since both header backends assign `ELF_ONLY` to
   a declaration they parsed *out of a header* whose symbol landed in
   `.symtab`. The record's own header provenance is what answers (a) — a
   positive when it is present, unknown when it is absent — and it is
   consulted for every member alike.
2. **Producers answer only what they observed**
   (`extract/surface_fact_producers.py`). A header-AST backend asserts (a);
   it asserts (c) only when a real export table was consulted, and leaves it
   unknown for a header-only dump. DWARF/BTF/CTF leave (a) unknown — debug
   info is not header evidence. An export-table-only entry leaves (a) unknown
   unless headers were actually parsed. `provenance.tag_provenance` adds the
   scope-aware half: a declaration whose defining header is in the supplied
   public set is positive evidence for (b) — and only ever a positive, since
   origin-based *exclusion* is already its own separately controlled scoping
   decision and asserting the negative here would apply it twice, silently.
   An evidence-depth projection returns both header-derived facts to unknown
   (`headers_discarded_surface_facts`) instead of asserting their negation.
3. **The guards were audited.** Every `visibility != Visibility.PUBLIC`
   filter now names its question: public-surface membership
   (`in_public_surface`), the binary union the old `(PUBLIC, ELF_ONLY)`
   tuples spelled out (`is_abi_visible`, replacing `diff_symbols._PUBLIC_VIS`
   and `diff_time64._ABI_VISIBLE`), export-table-only-ness
   (`is_export_table_only_record`), the *intersection* of (b) and (c) that
   the single `is PUBLIC` test used to mean (`is_public_export`), a confirmed
   non-export (`is_export_confirmed_absent`), or — for `diff_namespaces`' removal
   indexes, the site the report reproduced — the source-declaration
   population (`in_source_declaration_index`), which is blind to (c) by
   construction.

`compare/export_transition.py` is the second finding the entry asked for,
and it is symmetric in both directions: a matched pair whose export went
away while the declaration stayed reports `FUNC_VISIBILITY_CHANGED`/
`VAR_VISIBILITY_CHANGED` on the binary axis, and one that *gained* an export
reports the compatible `FUNC_EXPORT_ADDED`/`VAR_EXPORT_ADDED`. Both
directions are gated on *confirmed* evidence on both sides, so "exported
before, unknown now" (or its mirror) is never rendered as an observed
transition.

Three review findings on the fix are worth recording, because each is the
same mistake in a different place — reaching for a predicate that answers a
*neighbouring* question:

- **The gain direction was missing at first.** Before the split a
  promised-but-unexported declaration failed the old `(PUBLIC, ELF_ONLY)`
  filter outright, so the pair never matched and a newly exported
  declaration was reported as `FUNC_ADDED`. Once the declaration keeps its
  place on both sides the pair matches — and with only a loss-side detector
  the run reported *nothing at all*. Trading one wrong finding for a silent
  diff is worse than the bug being fixed, and an addition that vanishes is
  what "record before disposing" forbids. Hence the two compatible kinds,
  and one entry point per owner (`check_function`/`check_variable`) so a
  caller cannot enumerate the directions and miss the next one.
- **A binary-symbol subject needs the intersection, not the union.**
  Replacing a `visibility is Visibility.PUBLIC` test with
  `in_public_surface` silently widens the subject to declarations the
  artifact never exported. `diff_templates`' instantiation-survival index is
  the sharp case: its own docstring says a stale `extern template`
  declaration must not count as surviving, and that declaration is exactly a
  promised-but-unexported entity, so the union admitted it and suppressed
  `INSTANTIATION_MISSING_FROM_BINARY`. `is_public_export` is the named
  intersection, and it reproduces the legacy enum test exactly (`PUBLIC`
  true, `ELF_ONLY`/`HIDDEN` false).
- **`Visibility.ELF_ONLY` cannot establish header absence.** The legacy
  bridge originally read it as a confirmed "not declared in any header". It
  is not: both header-AST backends assign `ELF_ONLY` to a declaration they
  parsed *out of a header* whose symbol landed in `.symtab` rather than the
  dynamic table, and a headerless pre-v46 snapshot reaches the same member
  with no header parse at all. Fact (a) is now answered from the record's
  own header provenance for every member alike; which member it was is a
  different question, still answered by `is_export_table_only_record`, so
  the ELF-only removal kind and the stub-record consumers are unaffected.

Every finding with one declaration behind it
carries a `surface_facts` block (report schema 4.4) stating all three as
`"true"`/`"false"`/`"unknown"` — always complete when present, because an
omitted key is what a reader mistakes for a negative.

Bug class: `evidence.export_presence_as_declaration_presence`
(`tests/regressions/manifest.py`), whose one registered residual is that the
generalized test builds its snapshots in-process; a live castxml/clang dump
of two builds differing only in a version script is owned by the
`integration` lane. The sibling name-shape defects stay under
`classification.name_shape_as_contract_membership`.

## A stored-`BundleFacts` baseline's narrower format set is not pre-checked by the Action

`actions/`' preflight (`action/validate-inputs.sh`) mirrors every one of
`compare`'s format allowlists so a bad `--format` fails before the
multi-minute toolchain install rather than after it — except one. A stored
`BundleFacts` OLD side with a directory/package NEW side renders `json` or
`markdown` only (`frontends/cli/commands/compare_bundle_facts_rejections.py`),
narrower than the release allowlist's `json|markdown|junit|oneline` that
preflight applies when *either* operand is release-style. So
`format: junit` on that shape passes preflight and then fails in the CLI with
a usage error (exit 64, surfaced as `VERDICT=ERROR`). A late clear failure,
not a wrong result — but late.

It is not fixed here because both available fixes are worse than the gap:

- **Classify in shell.** Whether a file *is* a stored `BundleFacts` document
  is answered by `storage/bundle_facts_codec.looks_like_bundle_facts_document`,
  a deliberately two-tier classifier (explicit `artifact_type` marker, then a
  v1-only shape fallback with documented, accepted false positives). A shell
  re-implementation is exactly the duplication `_is_release_style_operand`'s
  own comment warns about, and that one is cross-checked against the live CLI
  by a test — this one could not be, since the classifier operates on decoded
  JSON, not a path.
- **Read the document at preflight.** The operand is attacker-controlled in
  the PR-checkout case, which is the whole reason the decode-node ceiling is
  config-only and the remedy this PR rewrote says only an explicit `--config`
  may raise it. Decoding it in a preflight step that runs *before* those
  limits exist reopens precisely that surface.

A path-extension heuristic (`*.json`/`*.json.gz`/`*.json.zst`) was considered
and rejected: it would wrongly reject `junit` for a stored `ProjectSnapshot`
OLD side, which is the same shape on disk. The honest fix is to make the
narrowing a *CLI-reported capability* the Action can query cheaply (a
`compare --probe-formats` style answer, or having the CLI validate formats
before resolving operands), which is a real design slice rather than a guard.
Until then the Action's `format` input documents the narrowing and says
plainly that it is not pre-checked. Found by Codex review on PR #1237.

## ADR-071's release assurance fold: what it deliberately does not do

ADR-071 gave `assurance.require_complete` real semantics for a
directory/package (release) `compare` and for a stored `BundleFacts` operand,
and retired the four guards that existed only to stay ahead of the missing
semantics. Three adjacent things were in scope to consider and deliberately
left out, each for a stated reason rather than for effort:

1. **No per-library `assurance.require_complete` override.** The setting stays
   project-wide (ADR-071 D4), like `gate.fail_on_removed_library` and the
   `release.*` keys. A per-member override is a real config-surface question
   (where does it live — a `release.libraries.<key>` block? a selector? — and
   how does it interact with the D7 precedence resolver), not a fold detail,
   and nothing in the reported gap asked for one.
2. **No per-member `analysis_assurance` *block* in the release report.** Each
   `libraries[]` entry carries its own `analysis_assurance_status` and the
   fold names the short members and their notes, but the full per-pair block
   (target/TU/export accounting, the five context statuses, layout-unverified
   detectors) is not embedded per member. Doing so would multiply a
   substantial sub-object across every member of every release document — a
   report-schema sizing decision of its own. A member that needs the full
   block is reachable today by comparing that library individually, and the
   fold's notes name which member to compare.
3. **The three orthogonal `0`/`1` release axes still fold in three places.**
   ADR-049's contract-coverage floor, ADR-065 D6's incomplete-scope floor and
   ADR-071's assurance floor now have the same shape — resolve a decision per
   run, `max` it across members, carry it to the exit and the report — and
   each has its own resolver, its own `*_terms` projection and its own
   parameter threaded through `_format_release_summary`/
   `_finalize_release_output`/`_exit_compare_release`. That is three near-
   identical threadings, and the `no_growth` baseline bumps ADR-071 needed in
   five files are the visible cost of adding the third. The convergence target
   is the duplication-and-convergence plan's own P0
   `EffectiveGate`/`EffectiveEvaluationConfig` work — one object carrying every
   orthogonal axis's decision, threaded once — not a fourth copy of the
   pattern. **Do not add a fourth `0`/`1` release axis by copying this one;**
   land that convergence first.

4. **The stored-`BundleFacts` operand's JSON carries no `exit` block.** It
   folds the axis, exits on it, and — since a Codex P1 on this PR — publishes
   the canonical top-level `analysis_assurance_exit_contribution` and the fold
   section in its own report and in every `--output-dir` file, so the gate is
   no longer lost to `aggregate` or a deferred gate. What it still lacks is an
   `exit` block of its own, unlike the live release document: a consumer
   reading `exit.code` from this driver's JSON finds nothing, and has to read
   the verdict and the individual axis keys instead.

   An earlier revision of this entry used that missing `exit` block to excuse
   omitting the fold entirely, on the grounds that the section had "nowhere to
   hang". That was wrong about the half that mattered: the gate-bearing key is
   a plain top-level scalar and needs no `exit` block at all, which is exactly
   why the omission was a real gate bypass rather than a cosmetic gap. Giving
   that driver a real `exit` block remains its own change, and the right one —
   it would close several adjacent asymmetries at once.

5. **`--depth binary` is not projected onto a stored-`BundleFacts` OLD side,
   so that operand is stricter than a live one.** `compare_bundle_facts.
   dispatch` threads `depth=` into the **stored/stored** driver
   (`workflows.bundle_stored_pair_compare.compare_stored_bundle_facts_pair`)
   but not into the **stored/live** one
   (`bundle_side_input.compare_release_against_bundle_facts`, which has no
   depth parameter at all). At `--depth binary` it clears NEW's headers while
   the stored OLD snapshot keeps whatever L2/L5 facts its capture recorded, so
   the comparison is asymmetric in a way live-vs-live at the same depth is not.

   Measured on a three-library fixture, same pair both ways: live-vs-live at
   `--depth binary` reports `analysis_assurance: partial` with one note
   (`scope_resolved is False`), while stored-vs-live reports `partial` with
   three (`header context asymmetric: the new side carries no header/API-level
   evidence`, `graph completeness unknown`, `contract_coverage is 'partial'`).

   ADR-071 did not cause the asymmetry — it pre-dates this axis and affects
   *findings* too, not only assurance — but it is what makes the asymmetry
   gate, so the divergence is worth stating plainly: under
   `assurance.require_complete` a stored-OLD release at `--depth binary` can be
   floored where the live equivalent is not. The `partial` is **not** a false
   finding: the evidence really was asymmetric, and `AGENTS.md`'s "weaker
   evidence narrows conclusions" says to report that rather than hide it. What
   is wrong is only that the user asked for binary depth and the stored side
   did not honour it.

   The fix is to give that driver a real `depth` and project both sides to it
   before comparing — which moves findings, not just assurance, and so belongs
   with the stored/live driver's own evidence handling rather than bolted onto
   this axis. Until then, compare a stored OLD side at its captured depth (omit
   `--depth`), or use a live OLD tree when you need `--depth binary` to be
   symmetric. Found by Codex review on PR #1237.

The companion test gap is recorded in `tests/regressions/manifest.py` under
bug class `gate.per_member_axis_fold`: the order-independence and monotonicity
properties are stated and generated for the assurance axis only. The coverage
and scope axes have example-level tests but no property suite of their own, so
a regression in *their* fold would not be caught by it. Generalizing one
property harness over all three axes is the real remaining work there, and it
pairs naturally with the convergence above.

## `compare --bundle-facts-out` stays a `compare` flag because `dump` has no release fan-out

`one-comparison-product.md`'s own framing, recorded rather than fixed here:
`--bundle-facts-out` asks a *comparison* to capture a baseline. Capturing
the OLD side's per-library snapshots into a `BundleFacts` document is a
**dump** operation — it reads one release and writes facts about it, and it
does not need a NEW side at all. It lives on `compare` only because that is
the one command that knows how to turn a directory or package into a set of
per-library snapshots; `dump` takes exactly one artifact, and a directory
operand there is interpreted as a stored `ProjectSnapshot` *package*, not as
a release to fan out over.

**The two options, and why neither landed in this pass.** Adding the `dump`
fan-out is the right shape: `abicheck dump RELEASE_DIR --format bundle-facts`
would make baseline capture a first-class dump, `compare` would consume the
document it already consumes, and `--bundle-facts-out` could be retired
rather than reimplemented. What it needs is the whole release input-resolution
chain — package/debug-package/devel-package extraction, library discovery and
canonical-key matching, stored-`ProjectSnapshot` variant materialization,
`--dso-only` classification — and every one of those functions is currently
`frontends`/flat-`cli_*`-classified (`cli_compare_release_matrix.
_prepare_compare_release_inputs` and its helpers), with `click.UsageError`
raises inside them. `dump`'s own execution path is `workflows`-classified, so
the `engine-cli-boundary` gate forbids it from reaching any of that, and the
fan-out cannot be written without either duplicating the chain or moving it.

That move is the *same* work `one-comparison-product.md` item 4 asks for on
its own terms (relocating `frontends/cli/release_compare_request.py`'s call
chain into `workflows` with typed errors), which is why the ordering here is
a real dependency rather than a deferral: the `dump` fan-out is a
straightforward composition once that chain is engine-side, and an
open-coded duplicate of it before then is exactly the second parallel path
this repository's architecture rules forbid.

**That dependency is now satisfied** (same PR): the chain is
`abicheck/workflows/release_inputs.py`, it raises the typed
`errors.ReleaseOperandError`, and `abicheck.service.resolve_release_compare`
reaches it with no Click context. So the `dump` fan-out is no longer
*blocked* -- it is simply not written yet, and writing it is a new command
surface (`dump`'s own operand arity, `--format bundle-facts`, the ADR-054
root-surface bar for any new spelling, and the retirement path for
`--bundle-facts-out`) rather than a refactor. Recorded here as the next
slice, with the blocker removed, instead of left reading as unreachable.

**Until then, `--bundle-facts-out` stays where it is, and stays honest about
what it is:** a capture ridden on a comparison. It is not reimplemented, not
widened, and not given a second spelling. When the `dump` fan-out lands, the
flag becomes a deprecated alias for it rather than a separate producer —
`abicheck/cli_compare_release_helpers.py`'s `write_bundle_facts_out` already
takes the already-resolved per-library snapshots and nothing else, so it is
the reusable half and moves as-is.

## A directory/package `compare` renders only json/markdown/junit; `bundles:` cannot state scope policy

Two halves of `one-comparison-product.md`'s bundle-parity item that this
pass did *not* close, recorded with what each actually needs. The rest of
that item did land (`assurance.require_complete` accepted, `--write`
repeatable, the machine document no longer implicitly truncated) — see the
changelog entry and `tests/test_one_comparison_product_parity.py`.

**1. The format set.** `compare` renders
`json`/`markdown`/`sarif`/`html`/`junit`/`review`/`oneline`; a directory or
package operand renders `json`/`markdown`/`junit`/`oneline` and rejects the
rest
(`frontends/cli/commands/compare.py`'s `_RELEASE_FORMATS`). The rejection is
loud rather than silent, and it is not arbitrary — the missing formats are
the ones whose renderers take a single `DiffResult`:

- `sarif` needs one result set with stable per-finding locations; a release
  has N of them and the fan-out discards each member's `DiffResult` before
  rendering (`_strip_diff_results_and_adjust_verdict`, to bound peak memory
  across a whole release). Producing a real release SARIF means either
  keeping every member's findings live or projecting SARIF per member and
  merging runs — a design choice, not a wiring gap.
- `html` and `review` are narrative renderings of *one* comparison (a
  verdict badge, an OLD→NEW headline, a release recommendation, a
  root-cause graph). `review` in particular is a PR-comment digest whose
  aggregate shape ("which of 40 libraries broke, and how badly") is a
  product question nobody has answered yet.
`oneline` was the fourth of that list and is **closed**: it never needed a
`DiffResult` at all (it is a count summary, and the release summary already
carries every count), so `report/release_oneline.py` folds the per-library
counts through the same `format_stat_line` a single-pair `compare` renders
and the release path accepts `-o oneline=...`/`-o oneline=...` like
any other.

So library count still changes which of `sarif`/`html`/`review` are
available -- a real parity gap, stated here rather than implied by a usage
error -- and no longer changes anything about `oneline`.

**2. Scope policy at bundle level.** ADR-065's scope policy is fully
expressible for a directory/package `compare` — `--select`,
`--select-required` and `.abicheck.yml`'s `scope.on_incomplete` all apply to
a release operand, and `--select-required` naming an absent member really
does floor the exit code (verified live). What has no expression is the
*project-config* bundle surface: `buildsource/project_targets.py`'s
`BundleSpec` accepts only `targets:` and `checks:`, so a `bundles:` entry
cannot state required members beyond its own membership list, nor its own
permitted-incompleteness policy, nor a selection narrower than the whole
bundle. A project that wants "these four of the six must be present, the
other two may be missing" has to say it per invocation rather than in the
config the Action reads. That is a `BundleSpec` schema addition plus a
`check-target` input, in the ADR-047 project-targets layer — not a
`compare` change, which is why it is listed separately from the format set
above.

## Release-fan-out CLI tests JSON-parse `CliRunner.output`, which the memory-clamp note can join

Found while verifying an unrelated change, reproduced on a clean tree, not
fixed here (it is nobody's feature and touches eight tests across two
modules that the change under review did not otherwise touch).

`cli_compare_release_pairwise.py` echoes one note to **stderr** when the
per-worker memory budget clamps the release fan-out's parallelism ("Note:
parallel release workers reduced 4 -> 3 to fit available memory ..."). Several
release CLI tests capture with a bare `CliRunner()` -- which merges stderr
into `result.output` -- and then `json.loads(result.output)`. When the clamp
fires, the note lands ahead of the document and the parse raises
`JSONDecodeError`, so the test fails for a reason that has nothing to do with
what it asserts.

It only fires under real memory pressure, which is why it is invisible most
of the time and then shows up as a cluster of unrelated-looking failures on a
loaded machine (e.g. the whole suite under `pytest -n 8` on a 16 GiB box).
Reproduced deterministically on a clean tree with
`ABICHECK_RELEASE_JOB_MEM_GIB=1000 pytest
tests/test_release_evaluation_config.py::TestReleaseFanOutAcceptsContractUnresolvedPack`.

Affected (as of this writing): `tests/test_release_evaluation_config.py`'s
`TestReleaseFanOutAcceptsContractUnresolvedPack::test_now_applies_with_contract`
and six tests in `tests/test_release_package_inventory_cli.py`
(`TestPackageArchiveInventoryProvesAbsence`, `TestSupportPromiseFindingsCli`).

The fix is per-test and mechanical: render to a file (`-o PATH`) and parse
that, the way `tests/test_one_comparison_product_parity.py` already does
where `--output-dir`'s own stdout line would otherwise interfere, or capture
stderr separately. Asserting on the note, or suppressing it, would be the
wrong fix -- it is a real diagnostic a user should see.

## Source-read licence: enforced at the one consumer, not repo-wide (2026-09-12)

The contract — *a path recorded in a snapshot is provenance, not a licence to
re-read the current filesystem for historical facts* — is stated in
`abicheck/buildsource/source_inputs.py` and enforced in the one place that
had violated it: `workflows/pattern_preprocessor_scan.py`, plus the two
primitives beneath it (`buildsource/pattern_facts.py`'s
`find_pattern_facts`, which now refuses to stat an unlicensed root, and
`buildsource/preprocessor_facts.py`, which is simply not invoked for an
unlicensed side since `clang -E` resolves `#include`s against the filesystem
it runs on).

Two residuals are deliberately open, both recorded on the
`evidence.stored_snapshot_rederivation` bug class in
`tests/regressions/manifest.py`:

1. **No mechanical gate against a future violator.** Nothing stops a new
   consumer from reading `AbiSnapshot.source_header` (or a compile unit's
   `source`) and opening it without resolving a licence first — nor a new front
   end from calling `dumper.dump` directly and forgetting to *grant* one. Both
   halves of that already bit once: the ABICC-compatible CLI was found doing
   exactly the second (PR #1236's review round), after `service.run_dump` had
   already been fixed for the first. The shape of
   the fix is known — an AST scan in `scripts/check_ai_readiness.py`, the way
   `fact-field-readers` guards `Fact[T]` reads, with an allowlist of the
   reader sites that legitimately hold a licence — but it is a gate of its
   own, not part of this fix, and today there is exactly one such consumer to
   guard.

2. **The licence is object-level, not content-verified.** A header-derived
   live extraction grants it for the whole run; it does not digest each source
   input and re-check that digest at read time. The consequence is the conservative
   direction (a stored snapshot declines to re-derive even when the tree on
   disk genuinely *is* the one it was dumped from), which is why it is a gap
   rather than a defect. Closing it properly means persisting per-input
   content digests, i.e. an `AbiSnapshot` schema bump inside the ADR-050
   comparability contract plus a codec and migration — the "persist the facts
   at dump time" half of the original two-option brief. That is the route to
   take if stored-versus-stored pattern/preprocessor evolution ever needs to
   be answerable rather than honestly declined; do not instead widen the
   licence, which would reintroduce exactly the defect above.

**Closed, and how (one licence per evidence source).** A third residual used
to sit here: the licence required *header-derived* provenance
(`extraction_read_source_inputs`), so a dump that collected only L3 build
evidence (`--sources` with no `-H`) reported its source-derived facts as not
evaluated even though it genuinely had read its compile units, because nothing
at the grant point separated that from a *loaded* pack whose paths are as
historical as a stored snapshot's. The same coarseness had a worse, opposite
failure the same review round found: a live header dump merged with a
pre-captured `--build-info` pack was granted one snapshot-wide licence off the
header AST, and the pattern/preprocessor scans then read the *pack's* recorded
compile-unit paths from whatever occupies them on this runner — the original
fabrication, reached through the other evidence source (PR #1236, Codex P2).

Both directions are one defect: a side holds up to two source-evidence sources
with independent provenance, and one licence cannot be correct for both. So the
licence is now resolved per source — declared headers via
`snapshot_source_licence`, an embedded build pack via `build_evidence_licence`,
which asks the pack itself (`BuildSourcePack.live_source_evidence`, stamped by
`buildsource/embed.py` only for an inline collection performed in this run and,
like the snapshot flag, never serialized). A partially-licensed side reports
what it read and keeps the unlicensed roots in the expected-input account as
`not_licensed` gaps, so no *absence* is established from the half that was read.
What remains true is that the grant is *conditional*, which is the point for a
DWARF-only dump whose `DW_AT_decl_file` paths name the build machine's tree:
that side never opened them, so it gets no header licence.

Note also the *shape* of the accepted trade-off in the third fix: the
evolution fold now decides each identity from what is established for that
identity, which means `persistent` is reported from two observations even
when both sides had coverage gaps elsewhere. An earlier revision withheld it
globally. That is not a regression of the earlier fix: an incomplete scan
cannot un-see a hit, so two observations are the strongest premise the fold
has, while `introduced`/`resolved` each assert an *absence* and still require
the relevant side's sufficiency. The lexical scan cannot, however, attribute
an unread file to a particular `PatternKind`, so a gap in any declared root
leaves *every* kind's absence unestablished on that side — a per-kind answer
would need per-kind input attribution the scanner does not have, and is
recorded as a gap on `coverage.discovery_derived_completeness`.


## An informational, no-material-change reconciliation outcome gates the build under `--severity-preset strict` (2026-09-12)

Raised as a triage question ("is `quality_issues` the right bucket for a
COMPATIBLE, purely-informational reconciliation outcome, or does the report
shape need an `informational, no action` category?"), it has a prior
question that outranks it: **do any consumers gate or rank on the bucket?**
They do, and that is the real defect.

`declaration_coordinates_shifted` — and now `declaration_identity_unchanged`
— are `COMPATIBLE` but not `ADDITION_KINDS`, so
`report_summary`/`policy.severity.categorize_changes` place them in
`quality_issues`. That is documented, and the partition invariant holds.
But `quality_issues` is one of the four `SeverityConfig` categories, and
`PRESET_STRICT` sets every category to `ERROR`. Measured directly:

```
declaration_coordinates_shifted   bucket=quality  default rc: 0  strict rc: 1
declaration_identity_unchanged    bucket=quality  default rc: 0  strict rc: 1
```

So under `--severity-preset strict` (or a `.abicheck.yml` `severity:` block
setting `quality_issues: error`) a finding whose whole content is "the
reconciliation proves this declaration did not change" produces a non-zero
exit code. `policy.severity.compute_gate_decision` additionally names
`quality_issues` in `blocking_categories`, and
`pr_comment_render.py`'s `_SEVERITY_ICONS` renders that category as
"⛔ Quality policy violation" on the PR comment.

This is not a presentation problem and it is not fixed by a dashboard
ranking differently. Two candidate fixes, neither taken here:

1. **Make the bucket honest.** `quality_issues` is defined as
   `COMPATIBLE_KINDS − ADDITION_KINDS`, i.e. a residue, and the residue now
   holds two populations with opposite meanings: real quality problems
   (`std` symbol leaks) and proofs that nothing happened. A third
   `COMPATIBLE` sub-partition (`INFORMATIONAL_KINDS`, alongside
   `ADDITION_KINDS`/`QUALITY_KINDS`) with no `SeverityConfig` category of
   its own would fix the gate and the report shape together. It is a
   `checker_policy` partition change, a `report/` `compute_*`/`render_*`
   pair change, and a report-schema bump — and `changekind-partition`'s
   AI-readiness gate would need to learn the fourth set.
2. **Exempt these kinds from the gate.** Narrower and worse: it leaves the
   bucket lying about what it holds, and it needs a per-kind exception list
   in exactly the place ADR-049 D8 works to avoid one.

Not attempted in the PR that found this because it changes a public
partition and a report schema — see this file's own precedent that a
partition change is an ADR-scoped decision, not a follow-up patch. The
measurement above is the evidence; the decision is open.
