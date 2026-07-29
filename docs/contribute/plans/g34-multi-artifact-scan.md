---
doc_type: contributor
level: advanced
lifecycle: active
---

# G34 — Multi-Artifact / Library-Set `scan`

**Origin:** User request to properly scan cases where one logical
"product" ships as several binary files (reference case: Intel oneDAL —
`libonedal_core.so` + `libonedal_thread.so`/`libonedal_dpc.so` behind one
shared header tree). Architecture investigation found `compare` already
solves this (ADR-023's bundle layer) but `scan`/`dump` have no equivalent —
a user auditing a freshly-vendored multi-.so dependency with no "old"
snapshot to compare against cannot express "these N files are one artifact"
at all.
**ADR:** [ADR-056](../adr/056-multi-artifact-library-set-scan.md) — Proposed,
not yet accepted for implementation. This plan is the phased breakdown *if*
ADR-056 is accepted; no phase here should be started before that.
**Type:** Initiative plan (cross-cutting; not tied to a single
`usecase-registry.yaml` gap — spans `abicheck/service_scan.py`,
`abicheck/bundle.py`, `abicheck/cli.py`, `abicheck/mcp_server.py`,
`reporter.py`).
**Effort:** M · **Risk:** low-medium — additive-only (existing single-artifact
`scan ARTIFACT` invocation is unchanged byte-for-byte), but touches a shared
module (`bundle.py`) that gains a second caller, and the CLI/MCP parity rule
(ADR-037 D10) means the flag must land on both surfaces together.

---

## Problem

See ADR-056's Context section for the full investigation. Summary:

- `scan`/`dump` are hard single-artifact by explicit, recent design
  (ADR-043 D5, 2026-07-16) — but `ScanRequest.binaries: list[Path]` is
  already plural-typed and the cost estimator already sums over it; only
  `run_scan`'s guard forces length 1. Unfinished scaffolding, not a
  considered-and-rejected design.
- `compare`'s bundle layer (ADR-023) already answers "is this multi-library
  release internally ABI-consistent" — but only when there's an old release
  to diff against, and only reachable via directory/package `compare`
  input, never `scan`'s one-build audit mode.
- ADR-023's bundle resolution graph is ELF-symbol-only; it does not see
  header-AST/DWARF evidence, so cross-DSO *type* drift is caught only
  indirectly (via per-library `type_*_changed` findings cross-referenced
  against the ELF resolution graph), never directly from a merged type
  closure. ADR-056 deliberately defers that harder problem — see its D2.

## Goal & acceptance criteria

- **G34.1** — `scan --artifact-set DIR|path,path,...` audits a set of
  libraries with no old side, producing one `AbiSnapshot`-based report per
  artifact plus a `bundle_findings`/`bundle_verdict` section from the same
  `ResolutionGraph`/`BundleFinding` machinery `compare`'s directory path
  already uses (ADR-023), generalized to run without an old-side diff.
- **G34.2** — `ScanRequest.binaries` accepts more than one path end to end via
  a new `run_scan_set`/`ScanSetResult` entry point; `run_scan`'s existing
  single-binary `ScanResult` return type is untouched, so existing service,
  `run_scan_subprocess`, and MCP callers that consume `.verdict`/
  `.exit_code`/`.to_dict()` see no behavior change (see ADR-056 D1/D2 and
  Phase 1 below). `ScanSetResult` itself carries a **set-level**
  `verdict`/`exit_code` (an explicit precedence table covering scan's own
  `BUDGET_OVERFLOW`/`EVIDENCE_CONTRACT_ERROR` failure verdicts alongside the
  ordinary compatibility ladder and the bundle layer's verdict — see
  Phase 1's precedence rules; deliberately **not** a reuse of
  `compare-release`'s `_RELEASE_VERDICT_ORDER`, which has no entries for
  either scan-specific failure state), not just the per-artifact list.
- **G34.3** — `abi_scan` MCP tool gains the equivalent `artifact_set`
  parameter in the same change (ADR-043 D10 parity rule), not a follow-up.
- **G34.4** — `tests/test_cli_root_surface.py`, `README.md`,
  `docs/reference/cli-reference.md` updated in the same PR as the CLI flag
  (AGENTS.md's root-surface-change discipline, applied here to a flag
  addition on an existing command rather than a new root verb).
- **Acceptance gate:** **triggered.** ADR-056 D2 requires one new,
  audit-scoped `ChangeKind` (e.g. `bundle_unresolved_intra_dependency`,
  `default_verdict = COMPATIBLE_WITH_RISK`) distinct from ADR-023's existing
  9 `bundle_*` kinds, since none of those can fire without an old side to
  diff against (see D2's correction). Phase 2 below owns the enum entry,
  `change_registry.py` metadata, detector implementation, and the
  `changekind-partition`/`changekind-detector`/`changekind-docs`
  AI-readiness checks this triggers — follow the shared new-`ChangeKind`
  checklist from
  [G24](g24-linux-abi-gap-closure.md#shared-checklist-every-new-changekind-in-this-plan),
  **plus two obligations that checklist does not itself enumerate**:
  `scripts/evidence_tiers.py`'s minimum-evidence-tier mapping (checked by
  the no-unspecified-evidence-tier test) and `python
  scripts/gen_detector_spec.py`'s regenerated
  `docs/reference/detector-spec.{md,json}` (checked by the
  generated-files-in-sync test) — both called out explicitly in Phase 2
  below since a new enum member without them fails the PR gate regardless
  of what the shared checklist lists.

## Design (phases)

### Phase 0 — Prerequisite: reconcile `bundle.py`'s resolution-graph drift

Before generalizing `bundle.py` to a second caller, resolve the doc/code
divergence ADR-023's 2026-07-29 amendment flags: either make
`_compute_resolution_graph` actually reuse `resolver.py`/`binder.py`, or
formally re-scope ADR-023's "Pro" claim to match the self-contained
implementation that shipped. This is a small, independently-reviewable
change — do it first so the third caller this plan adds (`scan
--artifact-set`) doesn't compound an already-diverged module. **Not
started.**

### Phase 1 — `service_scan.py`: finish the plural `binaries` path

- Add `run_scan_set(req) -> ScanSetResult` (new aggregate dataclass:
  `per_artifact: list[ScanArtifactResult]` + bundle findings/verdict +
  **set-level `verdict: str`/`exit_code: int`**), looping over
  `req.binaries` and reusing the existing single-binary dump/scan pipeline
  per artifact. **`run_scan`'s own signature and `ScanResult` return type
  stay exactly as they are today** — existing single-binary callers
  (service, `run_scan_subprocess`, MCP `abi_scan`) are unaffected; only
  `--artifact-set`/`artifact_set` callers route through `run_scan_set`.
  Without an explicit set-level `verdict`/`exit_code`, none of the CLI, MCP,
  or Action surfaces have a defined single result to gate on when one
  member is `BREAKING`/`API_BREAK`/budget-overflowed and another passes.
- **Set-level precedence is its own explicit table, not
  `compare-release`'s `_RELEASE_VERDICT_ORDER`.** That table
  (`abicheck/cli_compare_release_helpers.py`) only ranks
  `NO_CHANGE`/`COMPATIBLE`/`COMPATIBLE_WITH_RISK`/`API_BREAK`/`BREAKING`/
  `ERROR`/`not_comparable` — it has no entries for `BUDGET_OVERFLOW` or
  `EVIDENCE_CONTRACT_ERROR`, the two scan-specific failure verdicts
  `run_scan` itself already produces (`abicheck/service_scan.py`, exit
  codes 5 and 1 respectively) and that a `--artifact-set` member can
  legitimately return. Reusing that table's `.get(v, 0)` lookup would
  silently rank both as low as `NO_CHANGE`, defeating the point of
  aggregating them at all. `ScanSetResult`'s aggregation instead follows
  the single-artifact scan engine's own exit-code semantics explicitly:
  1. **Any member `BUDGET_OVERFLOW`** → the whole set is `BUDGET_OVERFLOW`,
     `exit_code = 5`. This dominates every other outcome, including a
     confirmed `BREAKING` member — the same reasoning ADR-050 D2 already
     established for `not_comparable` in `compare-release`: a member whose
     analysis didn't finish is worse than one that finished and found a
     break, because its true result is unknown, not merely bad.
  2. **Else, worst compatibility verdict** across the members that did
     complete (`NO_CHANGE`/`COMPATIBLE` < `COMPATIBLE_WITH_RISK` <
     `API_BREAK` < `BREAKING`, plus the bundle layer's own verdict in the
     same ladder), with its corresponding exit code (0/0/0/2/4 — the
     existing single-scan severity-aware mapping).
  3. **Any member `EVIDENCE_CONTRACT_ERROR`** raises the set's `exit_code`
     to at least `1` without lowering a worse code from step 2 (i.e.
     `exit_code = max(step_2_exit_code, 1)`) — an evidence-contract error
     is a real problem but a lesser one than a confirmed break, matching
     that verdict's existing standalone exit code (1) in the single-artifact
     contract. **`verdict` follows the same rule as `exit_code`, not left
     implicit:** the set's `verdict` string is set to
     `"EVIDENCE_CONTRACT_ERROR"` whenever step 2's worst compatibility
     verdict is `NO_CHANGE`/`COMPATIBLE`/`COMPATIBLE_WITH_RISK` (i.e. the
     error is the dominant problem in the set); when step 2 already
     produced `API_BREAK`/`BREAKING`, `verdict` stays that stronger value
     and `EVIDENCE_CONTRACT_ERROR`'s presence is only reflected in the
     exit-code floor and in a per-member flag inside `per_artifact`, not by
     overwriting a worse verdict string. Without this rule an API/MCP
     consumer gating on `.verdict` (e.g. treating anything other than
     `"COMPATIBLE"`-family as a problem) and a CLI/Action consumer gating
     on `.exit_code` could reach different pass/fail conclusions for the
     identical set.
  This precedence must be spelled out in `run_scan_set`'s own docstring and
  covered by a dedicated unit test (mixed BREAKING + budget-overflow member
  set resolves to `BUDGET_OVERFLOW`/5; mixed COMPATIBLE +
  evidence-contract-error resolves to exit 1) so a different implementation
  detail doesn't silently gate the same artifact set differently later.
- **Artifact identity, not an anonymous list (P1).** Neither `ScanResult`
  nor its nested `ScanOutcome`/report carries a binary path or library
  identity anywhere (checked against the live dataclasses,
  `abicheck/service_scan.py`) — a bare `list[ScanResult]` gives a CLI/MCP/
  API consumer no way to attribute a given member's findings back to the
  artifact that produced them, which matters most for the directory form
  of `--artifact-set` where the caller may not even know which files were
  discovered or in what order. `per_artifact` is therefore
  `list[ScanArtifactResult]`, a new thin wrapper
  (`path: Path`/`library: str` + the existing `ScanResult` fields, or an
  `artifact: Path` + `result: ScanResult` pair — exact shape decided at
  implementation time) rather than a bare `ScanResult`. Also applies to the
  bundle layer's own consumer/provider references (already
  library-name-keyed per ADR-023, so no change needed there) and to
  `ScanSetResult.to_dict()`'s JSON shape, which must key or label each
  member by artifact.
- Public re-export: `abicheck/service.py` re-exports the scan engine's
  public API from `service_scan.py` today (`run_scan`, `ScanResult`,
  `run_scan_subprocess`, in its `__all__`) as the Tier-2 service facade —
  `abi_scan`/other MCP tools import from `.service`, not `.service_scan`
  directly. `ScanSetResult`, `run_scan_set`, and (Phase 1's later bullet)
  `run_scan_set_subprocess` need the same re-export + `__all__` entries in
  `service.py`, or the MCP route to them doesn't exist without bypassing
  the Tier-2 facade. This also changes the public service surface, so
  `python scripts/gen_python_api_reference.py` →
  `docs/reference/python-api-reference.md` needs regenerating (Phase 3).
- **One budget for the whole set, not one per artifact (P1 — real
  regression risk).** `run_scan` starts each `run_scan_core` call with a
  fresh `_time.monotonic()` (`abicheck/service_scan.py`) and applies
  `req.budget.total_timeout` against that fresh start. A naive loop over
  `run_scan`/`run_scan_core` for N artifacts would therefore let each
  member consume up to the *full* configured budget independently — a
  `--budget 15m` on a 5-artifact set could run ~75 minutes instead of the
  documented whole-command guard. `run_scan_set` must compute one
  set-level deadline up front (`start = _time.monotonic()` once, before the
  loop) and pass each artifact's *remaining* budget
  (`req.budget.total_timeout - elapsed`) into that artifact's
  `run_scan_core` call, failing the set with the existing budget-overflow
  contract (exit code 5) the moment the remaining budget is exhausted
  rather than starting a member that can't finish in time.
- **`run_scan_set` cannot simply call `run_scan` in a loop.** `ScanResult`
  (`abicheck/service_scan.py`) has no snapshot field
  (`verdict`/`exit_code`/`findings`/`layers`/`confidence`/`estimate`/
  `report` only) — `run_scan_core` (`abicheck/scan_engine.py`) computes a
  candidate `AbiSnapshot` internally but `run_scan` discards it before
  returning. Phase 2's `build_bundle_snapshot(list[AbiSnapshot], paths)`
  entry point needs the actual snapshots, not just each artifact's
  `ScanResult`. `run_scan_set` needs its own internal path through
  `run_scan_core` (or a thin wrapper around it) that retains each
  artifact's `AbiSnapshot` alongside its `ScanResult`, rather than
  re-deriving snapshots by re-parsing the same binaries a second time.
- `run_scan_set` takes a `bundle_system_providers: list[str]` parameter
  (same shape as `compare-release`'s `--bundle-system-providers`,
  `abicheck/cli_options.py`/`cli_compare_release.py`) and threads it into
  Phase 2's audit-mode detector — this is the closed-world escape hatch
  ADR-056 D2 requires; without it there is no way for a `scan
  --artifact-set` caller to declare a legitimate external dependency.
- **MCP timeout parity:** `abi_scan` calls `run_scan_subprocess`
  (`abicheck/service_scan.py`), a killable-child-process wrapper around
  `run_scan` that the MCP server relies on to terminate a hung scan (and
  its compiler descendants) at the tool timeout rather than orphaning it
  (`abicheck/mcp_server.py`). That wrapper is singular-`run_scan`-only.
  `run_scan_set` needs the equivalent — a `run_scan_set_subprocess`
  wrapping `run_scan_set` the same way — so an `artifact_set`-mode
  `abi_scan` call (potentially N expensive scans) gets the same
  timeout/process-tree cleanup instead of either being rejected by the
  singular wrapper or running unbounded in the MCP server's own process.
- `tests/test_scan_estimate.py::test_run_scan_rejects_multiple_binaries` —
  **keep unchanged, unmodified.** `run_scan` itself still rejects a
  multi-item `binaries` list (Phase 1's bullet above); this test is the
  regression guard for that preserved singular contract, not something to
  relax. Add a **new**, separate acceptance test exercising
  `run_scan_set` with a multi-item `binaries` list instead.
- **Not started.**

### Phase 2 — `bundle.py`: audit-mode entry point (no old side)

- Generalize `build_bundle_snapshot()`'s entry point so a caller can supply
  `list[AbiSnapshot]` + paths directly (today only reachable through
  `compare-release`'s directory-matching code, which always assumes an old
  and a new side). **Reject, don't silently degrade, an unsupported input
  set.** The live `build_bundle_snapshot` silently skips any non-ELF path
  (`_path_looks_like_elf` check, `continue`) and returns a graph built from
  whatever survived — correct for `compare-release`'s existing directory
  scan (a mixed-format release directory legitimately has non-library
  files to skip), but wrong for an explicit `scan --artifact-set` audit.
  Two distinct cases, both requiring an explicit reject/mark-incomplete
  outcome rather than a silently clean `bundle_verdict`:
  1. **Directory form** (`--artifact-set DIR`) — zero ELF members survive
     filtering: reject the invocation (`click.UsageError`/equivalent).
  2. **Explicit-list form** (`--artifact-set a.so,plugin.dll,...`) — *any*
     caller-named member is unsupported, even if others are fine. Unlike
     the directory form (where "some files aren't libraries" is expected
     and skipping is correct), every entry in an explicit, comma-separated
     list was named by the user as part of the set they want audited —
     silently dropping `plugin.dll` and reporting a clean bundle for just
     `liba.so` would misrepresent the audit as covering the full
     caller-declared set when it didn't. The explicit-list path must
     reject or mark the result incomplete for *any* unsupported named
     member, not only when the whole set collapses to zero.
  Never a bare "no findings" for either case.
- Add `ChangeKind.BUNDLE_UNRESOLVED_INTRA_DEPENDENCY` (exact name TBD),
  `default_verdict = COMPATIBLE_WITH_RISK`, registered in
  `change_registry.py` alongside ADR-023's existing 9 `bundle_*` entries.
  This is a **new** kind, not a reuse of `bundle_intra_dep_removed` — see
  ADR-056 D2's correction for why reuse is unsafe. Update
  `scripts/evidence_tiers.py` with this kind's minimum-evidence tier (an
  ELF-symbol-level finding, same tier as the existing `bundle_*` entries)
  — `tests/test_...::test_no_new_unspecified_evidence_tier_kinds`-shaped
  coverage fails otherwise. Re-run `python scripts/gen_detector_spec.py` →
  `docs/reference/detector-spec.{md,json}` after adding the enum entry —
  a changed `ChangeKind` set with a stale generated spec fails
  `scripts/verify.py`'s generated-files-in-sync check.
- **Detector-registry note (not a gap):** ADR-023's existing 9 `bundle_*`
  kinds are not registered via `@registry.detector(...)`
  (`detector_registry.py`) — that decorator's contract is a per-library
  `(old_snapshot, new_snapshot) -> changes` detector wired into `compare()`
  (see `diff_symbols.py`'s detectors for the shape), which does not fit a
  post-hoc layer that reads N already-computed per-library diffs plus a
  cross-artifact resolution graph. This new audit-scoped detector follows
  the same established, shipped precedent (unregistered, called directly
  from the bundle-analysis entry point) rather than the general
  `@registry.detector` convention — noted explicitly here so a future
  reviewer doesn't read the omission as an oversight. Confirmed against the
  actual enforcement mechanism, not just precedent: the AI-readiness
  `changekind-detector` check (`scripts/check_ai_readiness.py`,
  `check_changekind_detector_crossref`) only WARNs if `ChangeKind.<NAME>`
  never appears as a literal token anywhere in `abicheck/` outside
  `checker_policy.py` — it does not require `@registry.detector`
  specifically, so this kind is not silently exempt from any check; it
  simply isn't gated on that particular decorator, the same as its 9
  `bundle_*` siblings.
- New audit-mode detector — **do not call `_detect_intra_dep_removed`
  directly, and do not assume extending its `system_providers` set alone is
  enough.** In the live detector, an allow-listed `extra_needed` edge only
  suppresses a finding when the *symbol itself* also matches
  `DEFAULT_SYSTEM_SYMBOLS`/`_looks_system_symbol` (`abicheck/bundle.py`) —
  it never unconditionally trusts a caller-declared external provider. That
  is correct for `compare`'s built-in system allow-list (glibc/libstdc++
  exports are inherently well-known-shaped), but wrong for
  `--bundle-system-providers`' actual purpose here: a user declaring
  `libvendor.so` as external is asserting "trust every symbol this DSO
  provides," including an arbitrary custom export like `vendor_init` that
  will never match a system-symbol heuristic. The audit-mode detector
  therefore needs **two independent suppression paths**, not one extended
  set:
  1. reuse `_import_is_external`'s version/provider-soname evidence as-is
     (unaffected by this issue — it already trusts a resolved verneed
     provider outside the bundle unconditionally); and
  2. for a consumer's *unversioned* import with no intra-set provider,
     check the import against the caller-supplied
     `bundle_system_providers` **DSO allow-list directly** (i.e. "is this
     consumer's only non-intra DT_NEEDED edge for this symbol a declared
     external provider?") and suppress unconditionally when so — no
     system-symbol-shape check gating it, since the whole point of the
     explicit allow-list is to cover exactly the non-system-shaped case.
  Document the **ambiguous case** explicitly: a symbol with no verneed
  provider info, satisfiable in principle by more than one declared
  external provider (or by both a declared provider and something
  unresolvable) — resolve it conservatively per this module's existing
  false-negative-over-false-positive default (AGENTS.md "Known gaps"):
  suppress rather than flag, and note in the finding's `evidence` that
  attribution to a specific external provider was not confirmed. Emit
  `BUNDLE_UNRESOLVED_INTRA_DEPENDENCY` with `COMPATIBLE_WITH_RISK` severity
  and a description that says "no provider found in this artifact set"
  (not "removed") only when neither suppression path applies.
- Unit tests: (a) a case with a genuinely external, unversioned dependency
  from a DSO **not** on the allow-list correctly still produces the
  risk-level finding (the earlier-round P1 false-positive this design
  closes); (b) the same case with the DSO **added** via
  `--bundle-system-providers`/`bundle_system_providers` correctly
  suppresses it, including for a non-system-shaped custom symbol name —
  the regression test for this round's escape-hatch correctness.
- **The set-level deadline (Phase 1) must reach this phase too, not stop
  after the last member scan.** Phase 1's remaining-budget threading only
  covers each artifact's `run_scan_core` call; building the resolution
  graph and running the new audit detector over a large-symbol bundle
  happens *after* the last member finishes, with no deadline check of its
  own — a set could exhaust its whole advertised `--budget` on N member
  scans and then keep running an unbounded amount of extra time in bundle
  construction/detection before `run_scan_set` returns. `build_bundle_snapshot`/
  the new audit detector must accept the same set-level deadline
  (remaining budget after the last member scan) and raise the existing
  budget-overflow condition if exceeded, so `ScanSetResult` can end up
  `BUDGET_OVERFLOW` from bundle-phase work too, not only from a member
  scan.
- **Not started.**

### Phase 3 — CLI + MCP surface

- `abicheck/cli_scan.py` (the module `scan` is actually registered from —
  not `cli.py`): make the existing required `ARTIFACT` `@click.argument`
  optional, add `--artifact-set` (directory or comma-separated explicit
  paths) **and** `--bundle-system-providers` (same option shape as
  `compare`'s existing flag in `abicheck/cli_options.py` — reuse that
  decorator/option definition rather than redeclaring it), and enforce, as
  `click.UsageError` (exit 64):
  - exactly one of `ARTIFACT`/`--artifact-set` is given;
  - `--against` is rejected together with `--artifact-set` (ADR-056 D2
    scopes `--artifact-set` to audit-only — no old side; `--against`
    stores one baseline path in `ScanRequest.baseline`, which does not
    extend to a set of artifacts each needing its own baseline);
  - `--bundle-system-providers` without `--artifact-set` is rejected too
    (the flag is meaningless outside audit-mode, same "don't accept a flag
    with no effect" discipline `compare`'s own scoping flags follow).
  Wire the accepted form to `ScanRequest.binaries`/`run_scan_set`
  (`bundle_system_providers` param, Phase 1).
- `abicheck/mcp_server.py`: add `artifact_set` **and**
  `bundle_system_providers` params to `abi_scan`, same validation shape as
  CLI (ADR-043 D10 parity — land together, not as a follow-up PR). Route an
  `artifact_set`-mode call through the new `run_scan_set_subprocess`
  (Phase 1) instead of the existing singular `run_scan_subprocess`, so the
  MCP tool timeout still terminates the process tree for a hung
  multi-artifact scan.
  **`binary_path` must become optional in the same change.** The live
  `abi_scan(binary_path: str, ...)` (`abicheck/mcp_server.py`) declares
  `binary_path` with no default, so it is required in the generated MCP
  tool schema — an `artifact_set`-only call would be rejected by MCP's own
  schema validation before the tool body's XOR check ever runs. Make
  `binary_path: str | None = None` and enforce, inside the tool body, that
  exactly one of `binary_path`/`artifact_set` is given and that `against`
  is rejected together with `artifact_set` — the same three-way validation
  `cli_scan.py`'s CLI flags enforce (Phase 3's CLI bullet above), mirrored
  on the MCP side rather than assumed to follow from "same validation
  shape as CLI" alone.
- `action.yml` (repo-root composite Action manifest — not under `action/`;
  `action/` holds the shell implementation only) + `action/run.sh` +
  `action/validate-inputs.sh`: the
  Action has no path to this feature at all today, not just a rejection to
  carve out. `run.sh`'s `scan` branch hard-requires a single
  `INPUT_NEW_LIBRARY` (`SCAN_ARTIFACT="${INPUT_NEW_LIBRARY:?...}"`) and
  explicitly errors on a directory/package value
  (`"scan does not accept a directory or package... scan analyses exactly
  one artifact"`, `run.sh` around the `scan` mode branch). Landing
  `--artifact-set` end-to-end for Action users needs: two new Action inputs
  (`new-library-set` and `bundle-system-providers`), `run.sh` forwarding
  them to `--artifact-set`/`--bundle-system-providers` instead of the
  positional artifact when `new-library-set` is set, and *then* narrowing
  `validate-inputs.sh`'s rejection to still block a bare directory/package
  passed as `new-library` while allowing the new dedicated input. Carving
  the rejection out of `validate-inputs.sh` alone, without the input +
  `run.sh` wiring, would let a `mode: scan` Action run pass preflight and
  then fail deeper in the pipeline — worse than today's clear, early
  rejection.
  **Correction:** `bundle-system-providers` is **not** an existing Action
  input to mirror — checked against the live `action.yml`/`run.sh`, neither
  declares nor forwards one today, even though `compare`'s CLI has carried
  `--bundle-system-providers` since ADR-023. This is a pre-existing gap
  independent of this ADR (Action users of `compare`'s bundle layer have no
  way to extend the system-provider allow-list at all). Since this phase is
  adding the input from scratch anyway, wire it to **both** modes —
  `run.sh` forwards `INPUT_BUNDLE_SYSTEM_PROVIDERS` to
  `--bundle-system-providers` in the `compare` branch too, not only the new
  `scan --artifact-set` branch — rather than shipping a second, scan-only
  input whose name collides in spelling but not in reach with the CLI flag
  `compare` users would reasonably expect the same input name to control.
- Regenerate the generated reference pages this phase's surface changes
  drift out of sync (`docs/AGENTS.md`'s "Regenerating generated docs" —
  `scripts/verify.py --profile pr` fails on a stale generated file, this
  is not optional cleanup):
  - `python scripts/gen_mcp_reference.py` → `docs/reference/mcp-tools-reference.md`
    (needs the `mcp` extra installed) for the new `artifact_set` param.
  - `python scripts/gen_action_reference.py` → `docs/reference/github-action-inputs.md`
    for the new `new-library-set` input.
  - `python scripts/gen_cli_reference.py` → `docs/reference/cli-reference.md`
    for `--artifact-set` (already listed under G34.4 above; grouped here
    since all three generators run together in practice).
- **Not started.**

### Phase 4 — Reporting

**Correction (checked against the live code, not assumed):** `scan` does
not render through `reporter.py`/`report_summary.py` at all —
`abicheck/cli_scan.py::_emit_scan_report` serializes a `ScanOutcome`
directly (text/JSON via its own rendering functions, e.g. `_render_text`).
The `bundle_findings`/`bundle_verdict` JSON/Markdown assembly ADR-023
shipped lives in `abicheck/cli_compare_release_helpers.py`
(`_release_md_bundle_findings` and the `summary["bundle_findings"]`
JSON-assembly block), reachable only from `compare-release`'s own
result-rendering path — not from anything `scan` calls.

- Extract the bundle-findings JSON/Markdown assembly out of
  `cli_compare_release_helpers.py` into a shared, `BundleDiffResult`-only
  helper (no dependency on `compare-release`'s own result types) that both
  `cli_compare_release_helpers.py` and `cli_scan.py` can call — do not
  duplicate the rendering logic.
- `ScanSetResult` (Phase 1) carries `bundle_findings`/`bundle_verdict`;
  `cli_scan.py` needs a new `--artifact-set`-aware branch (`_emit_scan_report`
  doesn't take a `ScanSetResult` today — either overload it or add a
  sibling `_emit_scan_set_report`) that calls the shared helper above for
  the bundle section and reuses `_render_text`/JSON assembly for each
  `per_artifact` entry.
- `bundle.json`/`bundle.md` file outputs (ADR-023's `--output-dir`
  contract) — decide whether `--artifact-set` follows the same
  `--output-dir` convention `compare-release` uses or folds the bundle
  section into `scan`'s existing single-file output; `scan` has no
  `--output-dir` concept today, only `-o/--output` for one file, so this
  needs an explicit choice, not an assumption that `compare-release`'s
  shape transfers unchanged.
- **Not started.**

### Phase 5 — Deferred (explicitly out of scope, see ADR-056 D2)

Cross-artifact **type** resolution above the ELF-symbol level (merging N
snapshots' type tables so a header-shared type used by value across
libraries resolves through one closure, the way `type_reachability.py`
resolves within one snapshot today) — the harder, more valuable half of the
original oneDAL ask. ADR-056 explicitly defers this; it needs its own
follow-on ADR once Phases 1-4 are proven out, per the same
scoped-follow-up discipline `AGENTS.md`'s "Known gaps" section already
applies to `type_reachability.py`'s own incremental fixes.

## Files & surfaces

Modified:
```text
abicheck/service_scan.py     # Phase 1 — ScanRequest.binaries plural path
abicheck/bundle.py           # Phase 0 (drift fix) + Phase 2 (audit-mode entry point)
abicheck/cli_scan.py         # Phase 3 — scan --artifact-set/--bundle-system-providers, ARTIFACT made optional; Phase 4 — bundle section rendering
abicheck/cli_options.py      # Phase 3 — reuse --bundle-system-providers option definition for scan
abicheck/mcp_server.py       # Phase 3 — abi_scan artifact_set/bundle_system_providers params
abicheck/cli_compare_release_helpers.py  # Phase 4 — extract shared bundle-findings render helper
action.yml                   # Phase 3 — new new-library-set/bundle-system-providers inputs (repo-root manifest, not under action/)
action/run.sh                # Phase 3 — forward new-library-set/bundle-system-providers to the CLI flags
action/validate-inputs.sh    # Phase 3 — narrow rejection once run.sh/action.yml support the new inputs
docs/reference/mcp-tools-reference.md      # Phase 3 — generated, gen_mcp_reference.py
docs/reference/github-action-inputs.md     # Phase 3 — generated, gen_action_reference.py
docs/reference/cli-reference.md            # Phase 3 — generated, gen_cli_reference.py
```

New:
```text
tests/test_scan_artifact_set.py   # Phase 1-2, mirrors tests/test_bundle.py's shape
examples/caseNNN_scan_artifact_set_audit/   # Phase 4 — two-library audit-mode example, incl. its own README.md
```

Adding the example fixture is not just the `caseNNN_*` directory. Per
`AGENTS.md`'s example-catalog obligations (the `examples-ground-truth`/
`examples-readme-sync` AI-readiness checks, `tests/test_examples_docs.py`)
a new case requires, in the same PR:
- the case directory with its own `README.md`;
- an entry in `examples/ground_truth.json`;
- `examples/README.md`'s catalog (verdict distribution, case-index row)
  kept in sync with `ground_truth.json`;
- `python scripts/gen_examples_docs.py` re-run, committing the resulting
  `docs/reference/examples/*.md` page for the new case.
Omitting any of these fails the PR gate, not just leaves documentation
stale — this is a hard requirement, not optional polish.

## Tests

- `tests/test_scan_estimate.py::test_run_scan_rejects_multiple_binaries` —
  **stays unchanged** (Phase 1: `run_scan` itself keeps rejecting a
  multi-item `binaries` list; only the new `run_scan_set` accepts them).
  New, separate acceptance test for `run_scan_set` with a multi-item list
  (Phase 1).
- `tests/test_bundle.py` — extended for the new audit-mode entry point
  (Phase 2), alongside its existing `compare`-side coverage.
- `tests/test_scan_artifact_set.py` (new) — CLI/service end-to-end for
  `scan --artifact-set` (Phase 1-4 combined).
- `tests/test_cli_root_surface.py` — flag-level surface assertion updated
  (Phase 3).
- `tests/test_mcp_*` — `abi_scan`'s new `artifact_set`/
  `bundle_system_providers` params (and `binary_path` becoming optional)
  covered alongside existing `abi_scan` tests (Phase 3).
- **Action tests (Phase 3) — currently missing from this plan, added
  here.** `validate-inputs.sh` and `run.sh` deliberately duplicate input
  validation (`action/AGENTS.md`) and must stay in sync; a bare code
  change to either without test coverage is exactly the drift that
  duplication risks. Add: `tests/test_action_validate_inputs.py` coverage
  for the new `new-library-set`/`bundle-system-providers` inputs'
  mutual-exclusion/XOR validation (mirroring the CLI-side checks, Phase 3
  above); and a `run.sh`/composite-Action test confirming
  `new-library-set` and `bundle-system-providers` actually reach
  `--artifact-set`/`--bundle-system-providers` on the resulting CLI
  invocation, for **both** `mode: scan` and `mode: compare` (per the
  earlier-round correction that `bundle-system-providers` must forward in
  both modes, not just the new scan branch).

## Example fixtures

At least one two-library audit-mode case (no old side, one intra-bundle
finding — e.g. a sibling importing a symbol the set no longer provides
anywhere), following ADR-023's own example-case obligations for its
`compare`-side bundle findings.

## Changelog

This plan touches `abicheck/**/*.py` (`service_scan.py`, `bundle.py`,
`cli_scan.py`, `mcp_server.py`, `service.py`) across every phase, so per
`AGENTS.md`'s Conventions section a `changelog.d/` fragment (`scriv
create`, `### Added` for the new `--artifact-set`/`scan` capability) is
required in the same PR — CI's `changelog-check.yml` rejects a PR touching
those paths without one. Land it with whichever phase first touches
`abicheck/**/*.py` (Phase 1), not deferred to the last phase.

## Effort & risk

M, phased; each phase is additive and independently shippable (existing
`scan ARTIFACT` behavior never changes). Main risk is Phase 2 — a second
caller of `bundle.py`'s resolution graph means any future correction there
(including the Phase 0 drift fix) now needs verification against two call
sites, not one.

## Out of scope

- Cross-artifact type-level resolution above ELF symbols (Phase 5, deferred
  to a future ADR — see ADR-056 D2).
- Any change to `dump`'s single-artifact contract (ADR-056 D1 explicitly
  excludes `dump`).
- Non-ELF (PE/Mach-O) library sets — `bundle.py`'s resolution graph stays
  ELF-only, unchanged from ADR-023's own scope.
- A `--no-bundle-analysis`-equivalent opt-out for `scan --artifact-set` —
  not introduced unless usage feedback shows a real need, mirroring
  ADR-023's own precedent (see ADR-056 Consequences).
