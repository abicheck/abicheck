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
  `.exit_code`/`.to_dict()` see no behavior change (see ADR-056's
  implementation-plan step 1).
- **G34.3** — `abi_scan` MCP tool gains the equivalent `artifact_set`
  parameter in the same change (ADR-043 D10 parity rule), not a follow-up.
- **G34.4** — `tests/test_cli_root_surface.py`, `README.md`,
  `docs/reference/cli-reference.md` updated in the same PR as the CLI flag
  (AGENTS.md's root-surface-change discipline, applied here to a flag
  addition on an existing command rather than a new root verb).
- **Acceptance gate:** any new `ChangeKind` this plan introduces (none
  expected — Phase 1 reuses ADR-023's existing 9 `bundle_*` kinds) would
  need the shared new-`ChangeKind` checklist from
  [G24](g24-linux-abi-gap-closure.md#shared-checklist-every-new-changekind-in-this-plan);
  not currently triggered since no new kind is planned.

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
  `per_artifact: list[ScanResult]` + bundle findings/verdict), looping over
  `req.binaries` and reusing the existing single-binary dump/scan pipeline
  per artifact. **`run_scan`'s own signature and `ScanResult` return type
  stay exactly as they are today** — existing single-binary callers
  (service, `run_scan_subprocess`, MCP `abi_scan`) are unaffected; only
  `--artifact-set`/`artifact_set` callers route through `run_scan_set`.
- `tests/test_scan_estimate.py::test_run_scan_rejects_multiple_binaries` —
  update to reflect the new accepting behavior; add a companion test
  confirming a single-item `binaries` list still behaves identically to
  today's singular path (regression guard).
- **Not started.**

### Phase 2 — `bundle.py`: audit-mode entry point (no old side)

- Generalize `build_bundle_snapshot()`/`compare_bundle()`'s entry point so
  a caller can supply `list[AbiSnapshot]` + paths directly (today only
  reachable through `compare-release`'s directory-matching code, which
  always assumes an old and a new side).
- New audit-mode variant: given only a "new" side's `list[AbiSnapshot]` +
  `ResolutionGraph`, emit the subset of `BundleFinding`s that make sense
  with no diff to read from (case 1's shape — "sibling still imports a
  symbol nothing in the set provides" — applies directly to a single-side
  resolution graph; cases 2/3/5 which key off a per-library *diff*'s
  changes do not apply in audit mode and are out of scope for Phase 2).
- **Not started.**

### Phase 3 — CLI + MCP surface

- `abicheck/cli_scan.py` (the module `scan` is actually registered from —
  not `cli.py`): make the existing required `ARTIFACT` `@click.argument`
  optional, add `--artifact-set` (directory or comma-separated explicit
  paths), and enforce exactly one of `ARTIFACT`/`--artifact-set` via
  `click.UsageError` (exit 64) before wiring to `ScanRequest.binaries`/
  `run_scan_set`.
- `abicheck/mcp_server.py`: add `artifact_set` param to `abi_scan`, same
  validation shape as CLI (ADR-043 D10 parity — land together, not as a
  follow-up PR).
- `action/validate-inputs.sh`: the existing single-artifact rejection for
  `mode: scan` needs a carve-out once `--artifact-set` exists as a
  supported multi-file form — otherwise the Action's own pre-flight
  validator would block the newly-supported case.
- **Not started.**

### Phase 4 — Reporting

- `scan`'s report gains a `bundle_findings`/`bundle_verdict` section when
  `--artifact-set` was used — reuse ADR-023's existing `bundle.json`/
  `bundle.md` output shape, don't invent a parallel one.
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
abicheck/cli_scan.py         # Phase 3 — scan --artifact-set, ARTIFACT made optional
abicheck/mcp_server.py       # Phase 3 — abi_scan artifact_set param
action/validate-inputs.sh    # Phase 3 — carve-out for the new supported form
reporter.py / report_summary.py  # Phase 4 — bundle section on scan reports
```

New:
```text
tests/test_scan_artifact_set.py   # Phase 1-2, mirrors tests/test_bundle.py's shape
examples/caseNNN_scan_artifact_set_audit/   # Phase 4 — two-library audit-mode example
```

## Tests

- `tests/test_scan_estimate.py` — updated rejection test + new
  plural-acceptance regression test (Phase 1).
- `tests/test_bundle.py` — extended for the new audit-mode entry point
  (Phase 2), alongside its existing `compare`-side coverage.
- `tests/test_scan_artifact_set.py` (new) — CLI/service end-to-end for
  `scan --artifact-set` (Phase 1-4 combined).
- `tests/test_cli_root_surface.py` — flag-level surface assertion updated
  (Phase 3).
- `tests/test_mcp_*` — `abi_scan`'s new `artifact_set` param covered
  alongside existing `abi_scan` tests (Phase 3).

## Example fixtures

At least one two-library audit-mode case (no old side, one intra-bundle
finding — e.g. a sibling importing a symbol the set no longer provides
anywhere), following ADR-023's own example-case obligations for its
`compare`-side bundle findings.

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
