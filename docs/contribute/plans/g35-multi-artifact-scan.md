---
doc_type: contributor
level: advanced
lifecycle: active
---

> **Historical note (2026-08-09).** The MCP server this plan's "Phase 3 —
> CLI + MCP surface" describes (including the `abi_scan_set` tool marked
> "done" below) has been removed entirely — see
> `docs/contribute/adr/021-mcp-security-model.md` (retired the same date).
> Every MCP-specific bullet in this plan (the `abi_scan`/`abi_estimate`
> `artifact_set` params, `abi_scan_set`, `mcp_server.py`/
> `mcp_server_scan.py`, `docs/reference/mcp-tools-reference.md`) describes a
> removed interface and is not something to re-implement. The CLI
> (`scan --artifact-set`) and GitHub Action surfaces this plan describes
> are unaffected and remain current.

> **Superseded (2026-08-28, CLI cleanup phase two, PR 5).** Every
> comma-separated `--artifact-set a.so,b.so,c.so`-style example below is
> historical: the flag is now a repeatable option
> (`--artifact-set a.so --artifact-set b.so --artifact-set c.so`), with no
> comma-separated alias — see
> `docs/contribute/plans/cli-cleanup-phase-two.md`'s PR 5 section and
> `docs/contribute/adr/056-multi-artifact-library-set-scan.md`'s own
> matching note. This plan's design decisions (the explicit-list-vs-directory
> distinction, per-member rejection, colliding-identity handling) are
> unaffected — only the value syntax multiple explicit paths use changed.

# G35 — Multi-Artifact / Library-Set `scan`

**Origin:** User request to properly scan cases where one logical
"product" ships as several binary files (reference case: Intel oneDAL —
`libonedal_core.so` + `libonedal_thread.so`/`libonedal_dpc.so` behind one
shared header tree). Architecture investigation found `compare` already
solves this (ADR-023's bundle layer) but `scan`/`dump` have no equivalent —
a user auditing a freshly-vendored multi-.so dependency with no "old"
snapshot to compare against cannot express "these N files are one artifact"
at all.
**ADR:** [ADR-056](../adr/056-multi-artifact-library-set-scan.md) — Proposed;
formal decision-maker sign-off is still pending, but Phases 1-4's core
scope (engine/detector/CLI slice, plus the GitHub Action wiring) has
already shipped ahead of that sign-off, in the same PR that proposed the
ADR — see the "Implementation status" note below for exactly what shipped
vs. what remains deferred (MCP wiring, the full example-catalog
obligation, and the dry-run/estimator's per-member scaling).
**Type:** Initiative plan (cross-cutting; not tied to a single
`usecase-registry.yaml` gap — spans `abicheck/service_scan.py`,
`abicheck/bundle.py`, `abicheck/cli_scan.py`, `abicheck/mcp_server.py`,
`reporter.py`).
**Effort:** M · **Risk:** low-medium — additive-only (existing single-artifact
`scan ARTIFACT` invocation is unchanged byte-for-byte), but touches a shared
module (`bundle.py`) that gains a second caller, and the CLI/MCP parity rule
(ADR-037 D10) means the flag must land on both surfaces together.

**Implementation status (2026-07-29):** Phase 1 (`service_scan.py`'s
`ScanArtifactResult`/`ScanSetResult`/`run_scan_set`/`run_scan_set_subprocess`),
Phase 2 (`bundle.py`'s `discover_artifact_set`/`audit_bundle`/
`_detect_unresolved_intra_dependency`, plus the
`BUNDLE_UNRESOLVED_INTRA_DEPENDENCY` `ChangeKind`), the CLI + GitHub Action
halves of Phase 3 (`scan --artifact-set`/`--bundle-system-providers` in
`cli_scan.py`/`cli_options.py`; `new-library-set`/`bundle-system-providers`
Action inputs in `action.yml`/`action/run.sh`/`action/validate-inputs.sh`,
with `bundle-system-providers` also wired to `compare`'s pre-existing
release-path flag, a gap independent of this ADR), and Phase 4's shared
bundle-findings render helper (`bundle.render_bundle_findings_markdown`,
reused by both `cli_scan.py` and `cli_compare_release_helpers.py`) have
shipped, with unit, CLI-level, and Action-level tests (`tests/test_bundle.py`,
`tests/test_scan_artifact_set.py`, `tests/test_action_validate_inputs.py`,
`tests/test_action_run_sh_artifact_set.py`) and a real gcc-built end-to-end
case. **Still open, not silently dropped:**

- ~~Phase 3's MCP half~~ — **done (2026-08-09).** A new `abi_scan_set` MCP
  tool (`mcp_server_scan.py`), not an `artifact_set` parameter grafted onto
  `abi_scan` itself: `abi_scan`'s own `against`/`policy`/`suppression_file`/
  `contract_evaluation` family are all baseline-comparison arguments
  `run_scan_set` (audit-only, ADR-056 D2, rejects a baseline outright)
  cannot accept, so a shared single-tool signature would have to make every
  one of them silently ignored under `artifact_set` — the same reasoning
  `cli_scan.py` already applies by giving `--artifact-set` its own code path
  rather than overloading `scan ARTIFACT`. Takes `artifact_paths` (2+),
  routes through the already-existing `run_scan_set_subprocess` (its own
  docstring anticipated exactly this caller — "MCP `abi_scan` must route an
  `artifact_set` call through this"), and forwards the same argument family
  `abi_scan` does minus the comparison-only ones, plus
  `bundle_system_providers`. `docs/reference/mcp-tools-reference.md`
  regenerated.
- The full example-catalog obligation (`examples/caseNNN_.../`,
  `ground_truth.json`, `examples/README.md`, `gen_examples_docs.py`) — a
  unit-level fixture covers the detector for now, not a binary example case.
- ~~The dry-run/estimator gap flagged below (Phase 3's estimator bullet)~~ —
  **done (2026-08-29, CLI cleanup phase two, PR 5).** `scan --artifact-set
  --dry-run` is a real preview now (`render_artifact_set_dry_run`,
  `abicheck/frontends/cli/artifact_set_dry_run.py`), not a hard rejection.
  Its cost projection sums a genuinely per-member-scaled estimate: one
  single-binary `ScanRequest` per discovered member, each run through
  `service.estimate_scan()` independently and the per-layer results
  summed across members — closing the L1-L5 under-count Phase 3's own
  estimator bullet flagged (only `L0_binary` scaled by `len(binaries)`
  there). Scoped to this one preview call site, not `estimate_scan()`
  itself: any *other* caller passing a multi-binary `ScanRequest` still
  hits the original single-request-shaped estimator, so that general gap
  stays open for those callers.
- **Cross-member header-obligation attribution — fixed (2026-08-09), via the
  minimum-viable half of the two options this entry originally named.**
  `_run_artifact_set` passes the *same* declared header set to every
  member's scan (`_run_scan_one_member` → `run_scan_core`'s per-member
  crosscheck pass); when a shared umbrella header partitions its declared
  API across sibling DSOs (the motivating oneDAL-style layout — `core_fn`
  declared in a common header but only implemented/exported by
  `libcore.so`, not `libalgo.so`), the `public_not_exported` crosscheck ran
  independently per member and had no notion of "this symbol is satisfied
  by a sibling, not this binary" — `abicheck/buildsource/crosscheck.py`'s
  `_check_public_not_exported` takes a single `AbiSnapshot` with no
  cross-snapshot context at all. With the check at its default advisory
  severity this was silent (RISK-only, doesn't change the verdict/exit
  code), but `--crosscheck public_not_exported=error` — the documented way
  to gate CI on it — turned a legitimate, correctly-partitioned set into a
  false `API_BREAK` for every member. Fixed the "union of every member's
  exports" way (not by attributing each header declaration to a specific
  owning member — that stays open, see below): `bundle.py`'s new
  `artifact_set_member_exports()` runs a cheap, ELF-header/dynsym-only pass
  over every set member up front; `service_scan.run_scan_set()` hands each
  member's own scan the union of what its *siblings* export via a new
  `CrosscheckConfig.sibling_exported_symbols` field, consulted by
  `_check_public_not_exported` alongside this member's own export table and
  the existing L4 reconciliation set. **Still open (two gaps, both flagged
  in review rather than shipped silently broken):** (1) per-declaration
  ownership attribution (so a symbol that moved to the *wrong* sibling, with
  no genuinely missing export anywhere in the set, could still be flagged
  as a mis-attribution rather than silently accepted) — this fix accepts
  "some sibling exports it" as sufficient, matching the minimum bar this
  entry originally set, not the more precise future model; (2) a sibling's
  own L4 reconciliation (ctor clone / Mach-O / demangle-drift variant
  spellings, the same class `_l4_reconciled_symbols` already exempts for
  the *current* member) is not applied to `sibling_exported_symbols` —
  `artifact_set_member_exports()` is a deliberately cheap, ELF-only pass
  with no L4/build-source data at all, run before any member's own
  snapshot (which is what carries that reconciliation mapping) has even
  been built, so a declaration a sibling exports only under such a variant
  spelling still false-positives. Fixing that would mean building every
  member's full snapshot (for its own L4 mapping) before scanning any
  member, the same class of heavier plumbing change as (1) — not attempted
  in the same pass as the raw-export union fix.

---

## Problem

See ADR-056's Context section for the full investigation. Summary:

- `scan`/`dump` are hard single-artifact by explicit, recent design
  (ADR-043 D5, 2026-07-16) — but `ScanRequest.binaries: list[Path]` is
  already plural-typed and the cost estimator's `L0_binary` row already
  sums over it (its `L1`-`L5` rows don't yet — see ADR-056's correction and
  Phase 1 below); only `run_scan`'s guard forces length 1. Unfinished
  scaffolding, not a considered-and-rejected design.
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

- **G35.1** — `scan --artifact-set DIR|path,path,...` audits a set of
  libraries with no old side, producing one `AbiSnapshot`-based report per
  artifact plus a `bundle_findings`/`bundle_verdict` section from the same
  `ResolutionGraph`/`BundleFinding` machinery `compare`'s directory path
  already uses (ADR-023), generalized to run without an old-side diff.
- **G35.2** — `ScanRequest.binaries` accepts more than one path end to end via
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
- **G35.3** — `abi_scan` MCP tool gains the equivalent `artifact_set`
  parameter in the same change (ADR-043 D10 parity rule), not a follow-up.
- **G35.4** — `tests/test_cli_root_surface.py`, `README.md`,
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
implementation that shipped.

- **Documentation re-scoping: done.** ADR-023's 2026-07-29 amendment
  (this PR) already corrects the stale "reuses `resolver.py`/`binder.py`"
  claim in place, appending the verified-against-code correction per this
  repo's append-don't-retcon ADR convention. No further doc work needed
  for this option.
- **Code reconciliation (making `_compute_resolution_graph` actually reuse
  `resolver.py`/`binder.py`): not started**, and not required before
  Phase 1-4 — this plan proceeds against `bundle.py`'s shipped,
  self-contained graph as-is (now accurately documented), since a real
  reuse of `resolver.py`/`binder.py` is a separate, independently-scoped
  change (asymmetric single-root-binary engine vs. `bundle.py`'s symmetric
  peer-library-set engine — not a drop-in swap) that this plan does not
  depend on and should not bundle in.

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
- **Version the new JSON contract — don't let it ride under the unchanged
  scalar version.** `abicheck/schemas/__init__.py`'s `SCAN_SCHEMA_VERSION`
  (currently `"1.3"`) is the explicit, additively-bumped version marker
  every `ScanResult.to_dict()` embeds (`"scan_schema_version":
  SCAN_SCHEMA_VERSION`, `abicheck/service_scan.py`) — the mechanism this
  codebase already uses so a consumer can tell what shape to expect.
  `ScanSetResult.to_dict()` introduces a genuinely new top-level shape
  (`per_artifact`, set-level `verdict`/`exit_code`, bundle
  findings/verdict) that a consumer parsing the unchanged `"1.3"` marker
  would have no way to distinguish from a single-binary `ScanResult`.
  Bump `SCAN_SCHEMA_VERSION` (additive minor, matching this constant's
  existing convention) and add the corresponding schema-version test
  coverage (mirroring whatever asserts today's `"1.3"` value) so the new
  aggregate shape is a versioned, detectable contract from its first
  release rather than an undocumented addition under an unchanged marker.
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
  documented whole-command guard.
  **Fix — do not recompute remaining budget externally; `run_scan_core`
  already does.** `run_scan_core` derives its own remaining budget from
  `budget_s - (time.monotonic() - start)` (`_remaining_budget_s`,
  `abicheck/scan_engine.py`) — it already subtracts elapsed-since-`start`
  internally. Combining a *shared, set-level* `start` (computed once,
  before the loop) with an already-*reduced* `budget_s` per member (as an
  earlier round of this plan specified: "pass each artifact's remaining
  budget") double-subtracts: after member 1 finishes at the 7-minute mark
  of a 15-minute budget, member 2 would receive `budget_s = 8min`
  (correctly reduced) but the *original* `start` from 7 minutes ago, so
  `run_scan_core` computes its own remaining as `8min - 7min_elapsed =
  1min` — overflowing the whole set around the 8-minute mark instead of
  the intended 15. The correct combination is one of:
  1. pass the **same, shared `start`** *and* the **original, unreduced,
     total `budget_s`** to every member's `run_scan_core` call — since
     `run_scan_core` already computes `budget_s - (now - shared_start)`
     internally, this alone produces the correct shrinking remaining
     budget across members with no external recomputation needed; or
  2. give each member its own fresh `start = _time.monotonic()` at the
     moment it begins, paired with that member's actual remaining budget
     computed externally (`total_timeout - elapsed_so_far`) — an
     equivalent absolute-deadline formulation, but only if the shared
     `start` from option 1 is *not* also passed alongside it.
  Option 1 is simpler and requires no new per-member arithmetic —
  prefer it. Cover with a test asserting a 2-member set where member 1
  consumes most of the budget correctly overflows on member 2 at the
  *original* total deadline, not early.
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
  **A member with no snapshot is a real, expected case, not an edge
  case to paper over:** `run_scan_core` raises `_BudgetOverflow`/
  `_EvidenceContractError` for a member that hits either condition, and
  `run_scan`'s own `except` clauses (`abicheck/service_scan.py`) convert
  that directly to a `ScanResult(verdict="BUDGET_OVERFLOW", ...)`/
  `"EVIDENCE_CONTRACT_ERROR"` with **no snapshot at all** — there is
  nothing to retain for that member. Phase 2's bundle construction must
  treat a missing member snapshot as an incomplete-bundle condition, not
  silently build the resolution graph from only the members that
  succeeded: a failed member could have been the actual provider of a
  symbol another (successful) member imports, and excluding it would let
  the audit detector invent a false "unresolved intra-dependency" finding
  for a symbol that was never actually missing from the true set — it was
  only missing from the *analyzed* subset. `ScanSetResult`'s bundle
  section must report itself as skipped/incomplete (not a clean
  `bundle_verdict`) whenever any declared member lacks a snapshot, mirroring
  the same "never a bare 'no findings' for an unsupported/incomplete input"
  principle Phase 2 already applies to non-ELF members.
- `run_scan_set` takes a `bundle_system_providers: list[str]` parameter
  (same shape as `compare-release`'s `--bundle-system-providers`,
  `abicheck/cli_options.py`/`cli_compare_release.py`) and threads it into
  Phase 2's audit-mode detector — this is the closed-world escape hatch
  ADR-056 D2 requires; without it there is no way for a `scan
  --artifact-set` caller to declare a legitimate external dependency.
- **`run_scan_set` must reject `req.baseline is not None` itself, not
  rely on `cli_scan.py`/MCP front-end validation alone.** Phase 1's
  service.py re-export (above) makes `run_scan_set` a public, directly
  callable Tier-2 entry point — a Python API caller building
  `ScanRequest(binaries=[a, b], baseline=old)` by hand and calling
  `run_scan_set` directly bypasses `cli_scan.py`'s `--against`/
  `--artifact-set` mutual-exclusion check entirely (that check lives in
  the CLI layer, not the service layer). Without an internal guard,
  `run_scan_set` would silently compare every member against the same
  single `old` baseline — exactly the "unrelated libraries compared
  against one shared file" failure mode ADR-056 D2 scopes
  `--artifact-set` to audit-only specifically to avoid. Raise (or set an
  equivalent usage-error result) from `run_scan_set` itself when
  `req.baseline is not None`, so the audit-only contract holds for every
  caller, not just the CLI/MCP front doors.
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
  3. **Colliding canonical identities — reject, don't silently keep one.**
     `build_bundle_snapshot(libraries: dict[str, Path])`
     (`abicheck/bundle.py`) is keyed by canonical library name; `compare`'s
     own directory-matching path (`_build_match_map`,
     `abicheck/cli_helpers_compare.py`) already has to resolve this for
     two-sided old-vs-new matching (picks the newest version on a tie), but
     that resolution strategy doesn't apply to a one-sided audit set — for
     `--artifact-set dir1/libfoo.so,dir2/libfoo.so` (two explicit paths
     canonicalizing to the same name) or a directory containing two
     same-named libraries, silently building the dict would keep only one
     path's `AbiSnapshot` in the bundle graph while `run_scan_set` still
     scanned both individually — the bundle graph would then attribute
     provider/consumer findings using only one of the two, producing
     incorrect results for the dropped one without any indication anything
     was dropped. Discovery must check for a canonical-name collision
     across the resolved member set *before* handing it to
     `build_bundle_snapshot` and reject with a usage error
     (`click.UsageError`/exit 64) naming the colliding paths — there is no
     sound way to silently pick one, unlike `compare`'s old-vs-new case
     where "newest version wins" is a meaningful tiebreak.
     **Correction — deduplicate symlink aliases before collision-checking,
     don't reject them.** `discover_shared_libraries()`
     (`abicheck/package.py`, `os.walk(..., followlinks=False)`) lists a
     symlink file (`libfoo.so -> libfoo.so.1`) and its real target
     (`libfoo.so.1`) as two separate discovered paths — a completely
     ordinary Unix install/package layout, not an edge case — and both
     canonicalize to the same `_canonical_library_key`. Rejecting that as
     a collision would fail `--artifact-set DIR` on common, correct
     directory layouts. Resolve each discovered path (`Path.resolve()`
     or an inode/`os.path.samefile` check) and deduplicate identical
     targets *before* the collision check; only reject when two distinct
     underlying files (different resolved paths/inodes) canonicalize to
     the same name.
  Never a bare "no findings" for any of the three cases.
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
- **The "does anyone in the bundle provide it" check itself must be
  version-aware, not name-only (P1 — inherited from the live detector,
  worth fixing here since this is a new function, not a call into the
  old one).** `ResolutionGraph.providers_for(symbol)`
  (`abicheck/bundle_models.py`) is keyed purely by symbol name — it
  returns every provider of that name regardless of which `gnu.version_d`
  each one exports, and `_detect_intra_dep_removed`'s own `if providers:
  continue` (short-circuiting "someone provides it, not a finding") never
  cross-checks that against the consumer's required `gnu.version_r`
  (`ConsumerEntry.version`). Both fields already exist in the graph
  (`ProviderEntry.version`, `ConsumerEntry.version`) — they're just not
  consulted at this step. Consequence for a set-audit with no old side to
  compare against: a consumer requiring `foo@V2` where the only intra-set
  provider exports `foo@V1` (a real, load-time-unresolvable mismatch) is
  currently indistinguishable from a consumer whose `foo` import is
  correctly satisfied, since `providers_for("foo")` returns non-empty
  either way. The new audit detector's provider check must confirm
  **version compatibility, not just label match**: GNU version *labels*
  are not globally unique across providers (`abicheck/bundle.py`'s own
  `_import_is_external` docstring — two siblings can both legitimately
  export a symbol under the same label, e.g. `V1`), so matching by
  `ConsumerEntry.version == ProviderEntry.version` alone is still
  attributable to the wrong provider: if a consumer's verneed specifically
  targets `liba.so` for `foo@V1`, `liba.so` drops the export, and an
  unrelated sibling `libb.so` happens to also export `foo@V1`, label-only
  matching would wrongly accept `libb.so` as satisfying the consumer. When
  `ConsumerEntry.version_soname` is populated (the precise, per-symbol
  verneed provider — the same field `_import_is_external` already prefers
  for exactly this reason), the provider check must resolve that soname to
  its actual bundle library (reusing/extending
  `BundleSnapshot.is_intra_bundle_provider`'s own soname-to-library
  matching, `abicheck/bundle_models.py`) and require the matching
  `ProviderEntry.library` to be that specific library — not just any
  provider sharing the label. Only when `version_soname` is empty
  (unavailable, e.g. a JSON snapshot predating the field) fall back to
  label-only matching among `providers_for(symbol)`, mirroring
  `_import_is_external`'s own two-tier fallback structure. **For an
  unversioned consumer import, a name-matching provider must also be
  DT_NEEDED-reachable from the consumer, not merely present anywhere in
  the declared set (P1).** `providers_for(symbol)` is set-wide, not
  scoped to the consumer's own dependency closure — if `libconsumer.so`
  imports `foo` but has no `DT_NEEDED` path (direct or transitive) to
  unrelated sibling `libplugin.so`, merely including `libplugin.so`
  somewhere in the artifact set must not count as resolving `foo`, since
  the real loader would never load `libplugin.so` while loading
  `libconsumer.so`. Add a small BFS helper over `ResolutionGraph.intra_needed`
  (soname → library-name resolved the same way
  `BundleSnapshot.is_intra_bundle_provider` already does) computing the
  set of libraries transitively reachable from a given consumer, and
  require an unversioned match's provider library to be in that set. The
  *versioned* path above doesn't need this — a specific
  `version_soname`-resolved provider is target-precise by construction —
  but the unversioned name-only fallback does. When no compatible
  provider exists under this scheme, treat the
  symbol as unresolved-in-set the same as a name-level miss, subject to
  the same weak-import/suppression-path handling below. Cover with two
  tests: (a) consumer requires `foo@V2`, set only provides `foo@V1` —
  produces a finding instead of a clean `bundle_verdict`; (b) consumer's
  verneed targets `liba.so` for `foo@V1` (via `version_soname`), `liba.so`
  no longer exports it, but unrelated sibling `libb.so` also exports
  `foo@V1` — must still produce a finding (the same-label-different-
  provider regression this correction closes), not be masked by the wrong
  provider matching on label alone.
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
  will never match a system-symbol heuristic. **Before either suppression
  path, skip a `weak` import outright** — `_detect_intra_dep_removed`
  itself does this first (`if consumer.weak: continue`), since an
  unresolved *weak* symbol is valid ELF and resolves to null at load time,
  not a broken reference; the audit detector must carry the same
  exclusion, not just inherit it by accident, since it is being
  specified here as a standalone function rather than a call into the
  existing one. The audit-mode detector therefore needs, after the weak
  check, **two independent suppression paths**, not one extended set:
  1. reuse `_import_is_external`'s version/provider-soname evidence as-is
     (unaffected by this issue — it already trusts a resolved verneed
     provider outside the bundle unconditionally); and
  2. for a consumer's *unversioned* import with no intra-set provider, a
     **DSO-level (not per-symbol) allow-list check.**
  **Correction — the per-symbol framing in an earlier round of this plan
  was not implementable against the real data model, fixed here.**
  `ResolutionGraph.extra_needed` (`abicheck/bundle_models.py`) is
  `library -> list[soname]` — a consumer-to-DSO-set fact, not a
  per-symbol one — and `ConsumerEntry.version_soname` (the one field that
  *does* disambiguate a specific symbol to a specific provider) is only
  populated for a *versioned* import ("" for unversioned/unknown). So for
  the unversioned case this path exists to handle, there is no fact in the
  graph saying "DSO X specifically provides symbol Y" — only "this
  consumer needs some symbols from some set of external DSOs." Attributing
  a missing unversioned symbol to one specific declared provider among
  several would require loading and indexing the allow-listed DSOs' own
  exported-symbol tables (a real, larger scope expansion — parsing
  binaries this ADR never asked the caller to provide paths for — out of
  scope here). The implementable check is therefore coarser, at DSO-set
  granularity: suppress an unversioned, unresolved-in-set import for a
  consumer **iff all four hold**:
  1. the consumer has **zero intra-set `DT_NEEDED` edges**
     (`ResolutionGraph.intra_needed.get(consumer.library)` is empty) —
     **this is the load-bearing condition a prior round of this plan
     omitted, and omitting it is unsound, not just imprecise.** If the
     consumer still depends on an intra-set library (e.g. `libcore`) that
     simply stopped exporting the symbol, that intra-set library — not
     anything external — is the true, broken provider; the live
     `_detect_intra_dep_removed` never suppresses purely on "the
     consumer's *external* deps happen to look fine" for exactly this
     reason, since a consumer needing both `libcore` (intra) and
     `libc.so.6` (extra, system) would otherwise suppress a genuinely
     broken `libcore` reference with zero symbol-level evidence that
     `libcore` was ever ruled out. Restricting to "no intra-set deps at
     all" means this coarse path only ever fires for a consumer whose
     *entire* dependency set is external — the one case where there is no
     intra-set candidate to have silently broken;
  2. the consumer has at least one non-intra `DT_NEEDED` edge (paired with
     condition 1: a consumer with literally zero `DT_NEEDED` edges at all
     is a degenerate case, not "purely external" — treat as unresolved);
  3. every one of those non-intra edges is either a well-known system
     soname or on the caller-supplied `bundle_system_providers` allow-list
     — mirroring `_detect_intra_dep_removed`'s existing
     `e in system_providers or _looks_system(e)` union of the built-in
     `DEFAULT_SYSTEM_PROVIDERS`/`_looks_system` heuristic with the
     caller-supplied set (`abicheck/bundle.py:220`), not a narrower,
     caller-list-only version of it (checking only the caller-supplied
     list, as an earlier round of this plan specified, would regress an
     ordinary consumer needing both a declared `libvendor.so` and plain
     `libc.so.6` — requiring the user to redundantly name every built-in
     system library just to keep today's already-working suppression);
  4. the non-intra-edge list from condition 2/3 is non-empty before the
     `all(...)` check runs — mirroring the live code's `if extra_needed
     and all(...)` guard exactly, not just `all(...)` alone: `all()` over
     an empty list is vacuously `True` in Python, so without this guard a
     consumer with *zero* non-intra edges (already excluded by condition
     1's "purely external" framing, but worth stating as its own explicit
     guard rather than relying on condition 1 alone to prevent it) would
     otherwise suppress unconditionally — exactly the genuinely-broken
     intra-set reference this detector exists to
  catch. Requiring at least one external edge before the all-covered check
  applies closes that vacuous-truth gap; a unit test for the empty-edge
  case (no non-intra DT_NEEDED at all, symbol genuinely unresolved) must
  still produce the finding. This is deliberately
  all-or-nothing per consumer: if a consumer needs both an allow-listed
  `libvendor.so` and a second, undeclared external DSO, suppression does
  **not** apply and the finding still fires for every one of that
  consumer's unresolved unversioned symbols — a known, documented
  imprecision (can't distinguish "provided by the allow-listed DSO" from
  "provided by the undeclared one" at this granularity), not a silent gap.
  Document this limitation directly in the detector's docstring and in
  ADR-056/user-facing `--bundle-system-providers` help text: the allow-list
  is a per-consumer, all-non-intra-deps-covered escape hatch that only
  applies to a consumer with **no remaining intra-set dependencies at
  all** — not a per-symbol attribution mechanism, and not something that
  rescues a mixed intra+external consumer.
  Emit `BUNDLE_UNRESOLVED_INTRA_DEPENDENCY` with `COMPATIBLE_WITH_RISK`
  severity and a description that says "no provider found in this
  artifact set" (not "removed") only when neither suppression path
  applies.
- Unit tests: (a) a case with a genuinely external, unversioned dependency
  from a DSO **not** on the allow-list correctly still produces the
  risk-level finding (the earlier-round P1 false-positive this design
  closes); (b) the same case with the DSO **added** via
  `--bundle-system-providers`/`bundle_system_providers` correctly
  suppresses it, including for a non-system-shaped custom symbol name —
  the regression test for this round's escape-hatch correctness; (c) a
  consumer needing symbols from **both** an allow-listed DSO and a second,
  undeclared external DSO still produces the finding (the documented
  DSO-set-granularity limitation above, not a silent false negative);
  (d) a consumer with **zero** non-intra `DT_NEEDED` edges (its would-be
  provider was itself dropped from the set) still produces the finding
  regardless of `--bundle-system-providers` — the vacuous-`all([])` guard
  regression test; (e) a consumer needing symbols from both a declared
  `--bundle-system-providers` entry and an ordinary built-in system DSO
  (e.g. `libc.so.6`) suppresses without the user having to redundantly
  name the built-in one — the `DEFAULT_SYSTEM_PROVIDERS`-union regression
  test; (f) an unresolved *weak* import with a non-system-shaped name
  produces no finding regardless of allow-list state — the weak-import
  exclusion regression test; (g) a consumer with **one intra-set edge**
  (e.g. `libcore`, which stopped exporting the symbol) **and** an
  otherwise-fully-allow-listed external edge (`libc.so.6`) still produces
  the finding — the mixed-intra-plus-external regression test for the P1
  soundness fix above; the case this whole coarse suppression path exists
  to avoid ever silently swallowing.
- **The set-level deadline (Phase 1) must reach this phase too, not stop
  after the last member scan.** Phase 1's shared-`start`/total-`budget_s`
  threading (corrected above) only covers each artifact's `run_scan_core`
  call; building the resolution graph and running the new audit detector
  over a large-symbol bundle happens *after* the last member finishes,
  with no deadline check of its own — a set could exhaust its whole
  advertised `--budget` on N member scans and then keep running an
  unbounded amount of extra time in bundle construction/detection before
  `run_scan_set` returns. `build_bundle_snapshot`/the new audit detector
  must accept the same shared `start` + total `budget_s` (computing its
  own remaining time from them the same way `run_scan_core` does, not a
  pre-computed "remaining after member N" value passed in) and raise the
  existing budget-overflow condition if exceeded, so `ScanSetResult` can
  end up `BUDGET_OVERFLOW` from bundle-phase work too, not only from a
  member scan.
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
- **`ScanRequest` doesn't carry every option the single-binary CLI path
  uses today — this must be closed, not silently dropped.** The live
  single-binary branch (`cli_scan.py`) passes `abi3_floor`,
  `enabled_checks`/`severities` (from `--crosscheck`), `build_config`,
  `allow_build_query`, **and `--risk-rules`** straight to `run_scan_core`
  (the last of these loaded via `_load_risk_rules(risk_rules_path)` and
  fed into `score_changed_paths`), bypassing `ScanRequest` entirely —
  none of those six are fields on `ScanRequest`
  (`abicheck/service_scan.py`); `run_scan` itself hardcodes
  `RiskRules.default()` rather than accepting a custom profile. If
  `--artifact-set` routes only through `ScanRequest`/`run_scan_set` as
  planned above, an invocation combining `--artifact-set` with
  `--abi3`/`--crosscheck KEY=off`/`--build-config`/build-query
  control/`--risk-rules custom.yml` would silently produce different
  findings (or select a different evidence depth, in the risk-rules case)
  per artifact than the equivalent single-binary `scan` invocation with
  the same flags — a correctness gap, not a missing nice-to-have. Phase
  1's `run_scan_set` must accept and forward all six
  (either by extending `ScanRequest` with the missing fields — the more
  durable fix, also closing the same gap for any future `ScanRequest`
  caller — or by giving `run_scan_set` its own equivalent parameters
  threaded straight to each member's `run_scan_core` call the way the
  single-binary CLI path already does). Cover with a test asserting an
  artifact-set member's findings match what a single-binary `scan` of the
  same file with the same `--abi3`/`--crosscheck` flags would produce.
- **`--dry-run` needs its own `--artifact-set`-aware branch, not the
  existing one — done (2026-08-29), see the "Still open" bullet above for
  the shipped shape.** Making `ARTIFACT` optional means an
  `--artifact-set ... --dry-run` invocation reaches the live dry-run
  branch (`cli_scan.py`) with `artifact=None` — it unconditionally passes
  `artifact=artifact` into `render_scan_dry_run` and constructs
  `ScanRequest(binaries=[artifact])`, so today's code would describe and
  cost-estimate one nonexistent pseudo-binary (`None`) instead of
  discovering and estimating the requested set. Add a set-aware dry-run
  path (discover the set the same way the real run would, estimate/render
  per member plus the bundle layer) before `ARTIFACT` is made optional —
  landing the optional-positional change without this branch would leave
  `--artifact-set --dry-run` broken from day one.
  **The per-member estimate itself needs a real fix, not a reuse of
  today's estimator as-is (ADR-056's correction).**
  `_intrinsic_layer_estimates`/`_source_layer_estimates`
  (`abicheck/service_scan.py`) only scale their `L0_binary` row by
  `len(req.binaries)` — `L1_debug`/`L2_header` and every
  `L3_build`/`L4_graph`/`L5_source` row are computed **once**, regardless
  of set size, because they were written for a single-binary `ScanRequest`
  and nothing scales them per member. Phase 1's `run_scan_set` runs
  `run_scan_core` once per artifact (each with its own header parse and,
  at `--depth build`/`source`, its own build replay), so reusing the
  existing single-`ScanRequest` estimate for `--artifact-set --dry-run`/
  `abi_estimate` would understate the dominant cost by roughly N× at any
  depth beyond `binary` — the opposite of what a budget-planning estimate
  is for. The set-aware dry-run path must sum a real per-member estimate
  across every layer (not just `L0_binary`) plus the bundle-analysis cost
  from Phase 2, not call the existing single-artifact estimator once and
  multiply only what it already happens to scale.
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
  **Every discovered member needs the same per-file MCP guards as the
  scalar path, not just the top-level `artifact_set` argument.** The live
  `abi_scan`/`abi_estimate` call `_safe_read_path(...)` and
  `_check_file_size(...)` (`abicheck/mcp_server.py`) on `binary_path`
  specifically to enforce `MCP_MAX_FILE_SIZE` before any parsing happens —
  a resource-exhaustion guard, not incidental validation. When
  `artifact_set` names a directory or a comma-separated list, discovering
  its members and handing them straight to `run_scan_set_subprocess`
  without running the same two checks on *each* discovered path would let
  a single oversized member (or a path escaping the expected root) bypass
  the guard entirely and consume unbounded parser memory/CPU inside the
  subprocess. Run `_safe_read_path`/`_check_file_size` over every
  discovered member before starting `run_scan_set_subprocess`, and add a
  regression test with one oversized member in an otherwise-valid set.
- **`abi_estimate` needs the same `artifact_set` treatment as `abi_scan`,
  not a follow-up.** `abi_estimate(binary_path: str, ...)`
  (`abicheck/mcp_server.py`) is `abi_scan`'s dry-run/cost-estimate
  counterpart on the MCP surface (the equivalent of `scan --dry-run` —
  Phase 3's CLI dry-run bullet above), and is currently `binary_path`-only
  the same way `abi_scan` was before this phase. Without the equivalent
  `artifact_set` param (+ the same `binary_path`-optional /
  mutual-exclusion treatment), an MCP caller has no way to get an
  aggregate cost estimate for a library set before running the real
  N-member `artifact_set` scan — forcing them to either skip estimation or
  fall back to N separate single-binary estimates that don't reflect the
  bundle-analysis cost. Land `abi_estimate`'s `artifact_set` param in the
  same change as `abi_scan`'s, regenerate
  `docs/reference/mcp-tools-reference.md` for both tools together (not
  just `abi_scan`), and add the equivalent test coverage.
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
    (needs the `mcp` extra installed) for the new `abi_scan_set` tool
    (regenerated 2026-08-09, see the Implementation status note above).
  - `python scripts/gen_action_reference.py` → `docs/reference/github-action-inputs.md`
    for the new `new-library-set` input.
  - `python scripts/gen_cli_reference.py` → `docs/reference/cli-reference.md`
    for `--artifact-set` (already listed under G35.4 above; grouped here
    since all three generators run together in practice).
- **CLI + GitHub Action halves shipped** (this pass's Implementation status
  note, above). **MCP half done (2026-08-09)** — see the Implementation
  status note's own updated bullet for what shipped (a dedicated
  `abi_scan_set` tool, not an `artifact_set` param on `abi_scan`, and
  `abi_estimate` — dry-run cost estimation — is unaffected, since G35's
  per-member estimator scaling gap is still separately open below).

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
- **`-o`/`--output` contract for `--artifact-set`: one aggregate document,
  not `--output-dir`, decided here rather than left to the implementer.**
  `scan` has no `--output-dir` concept today, only `-o/--output` writing
  one file; `--artifact-set` keeps that same shape rather than introducing
  `compare-release`'s separate `--output-dir`/`bundle.json`/`bundle.md`
  convention (a new CLI concept this ADR doesn't otherwise need). `-o
  result.json` writes one `ScanSetResult.to_dict()` document — `per_artifact`
  as an array of each member's serialized result plus the bundle section —
  not one file per member (which risks the same file being overwritten
  across members) and not a directory of files. Text output (`-o
  result.txt`/stdout) renders each member's summary in sequence followed by
  the bundle-findings section. Add a test asserting a single `-o` write
  produces one well-formed aggregate document for a multi-member set.
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
- an entry in `catalog/ground_truth.json`;
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
  `scan --artifact-set` (Phase 1-4 combined), including a colliding-
  canonical-name rejection test (two explicit paths, or a directory with
  two same-named libraries) — the discovery-collision regression test.
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
