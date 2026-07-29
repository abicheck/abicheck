---
doc_type: contributor
level: advanced
lifecycle: active
---

# ADR-056: Multi-Artifact / Library-Set `scan`

**Date:** 2026-07-29
**Status:** Proposed — not implemented. This ADR records the decision and
scope; no code changes accompany it (see
[G34](../plans/g34-multi-artifact-scan.md) for the phased implementation
plan once/if this ADR is accepted for execution).
**Decision maker:** (pending)

---

## Context

ADR-023 already solved "a library ships as a bundle of several `.so`s with
cross-DSO relationships" — but only for `compare`. Its reference case is
literally Intel oneDAL: `libonedal_core.so`, `libonedal_thread.so`,
`libonedal_dpc.so`, ... behind one `include/oneapi/dal/` header tree, with
DT_NEEDED edges between the algorithm libraries and core.

The gap this ADR addresses is narrower but real: **`scan`/`dump` have no
equivalent.** A user who wants to *audit* such a bundle in isolation (no old
snapshot to compare against — `scan`'s own "one-build audit" mode, ADR-043
D5) or *dump* a full L2-L5 evidence snapshot for it cannot express "these N
`.so` files are one logical artifact" at all. `scan`/`dump` are hard
single-artifact:

- `dump`: `abicheck/cli.py`'s `so_path` argument has no plural form; there is
  no `DumpRequest` type — `service.run_dump(path: Path, ...)` takes exactly
  one path in its signature. There is no dedicated rejection message; a
  directory falls through `resolve_input`'s format-sniffing chain to a
  generic "cannot detect format" error.
- `scan`: `abicheck/cli.py`'s `ARTIFACT` argument is a single positional,
  deliberately narrowed by **ADR-043 D5** ("`scan` always targets exactly
  one artifact") from an earlier repeated `--binary` flag. The service layer
  enforces this explicitly: `ScanRequest.binaries: list[Path]`
  (`service_scan.py:226`) is already plural-typed, and the cost estimator
  (`_intrinsic_layer_estimates`) already sums over `len(req.binaries)`
  correctly — but `run_scan` (`service_scan.py:881-883`) hard-rejects
  `len(req.binaries) != 1`. This is unfinished scaffolding, not a
  deliberate plural-then-narrowed design: no comment, test, or ADR explains
  why the field is plural while the guard is singular.
- The GitHub Action's bash pre-flight validator (`action/validate-inputs.sh`)
  independently enforces the same single-artifact contract for `scan mode`
  and `deps tree`/`deps compare`, added specifically after a real user
  pointed a multi-library release directory at `mode: scan`.

Where `compare`'s bundle layer (`abicheck/bundle.py`, ADR-023) does exist,
it is **ELF-symbol-only**: it parses `ElfMetadata` for each library and
builds a `ResolutionGraph` from DT_NEEDED edges and the dynamic symbol
table. It does not see header-AST (L2) or DWARF/build (L3-L5) evidence, so
it cannot resolve a cross-DSO **type** reference the way a single-library L2
scan already resolves types within one binary. `AbiSnapshot` itself
(`model.py`) has no multi-library representation — one snapshot is
irreducibly one `library: str`.

There is also a real doc/code drift discovered while investigating this:
ADR-023 states the bundle resolution graph "reuses `resolver.py`/
`binder.py`" (the `stack-check` engine, ADR-008). It does not — `bundle.py`
has its own independent, lighter ELF-only implementation. See ADR-023's
2026-07-29 amendment for the correction. This matters here because it means
there are already **two** independent dependency-graph engines in the
codebase before this ADR adds anything; a third, evidence-richer one should
not be added without deciding whether to consolidate first.

---

## Decision

**Two separable questions.** They do not have to be answered the same way,
and answering "no" to either one is a legitimate outcome of this ADR — the
point of writing it is to make the choice explicit and recorded, not to
presuppose an expansion.

### D1. Does `scan`'s operand shape change?

Per ADR-054's root-command admission bar (referenced from ADR-043's
amendment trail), a change to what an *existing* command's operand accepts
is a smaller bar than a new root verb, but D5's "exactly one artifact" is
itself a recent (2026-07-16), deliberate, explicit decision — reopening it
needs the same rigor as adding a command, not a routine flag addition.

**Recommendation: yes, narrowly** — `scan` gains an alternate operand shape
for a library set, not a change to its existing single-artifact form:

```text
abicheck scan ARTIFACT               # unchanged: exactly one artifact
abicheck scan --artifact-set DIR     # new: every discoverable shared library in DIR
abicheck scan --artifact-set a.so,b.so,c.so   # new: explicit repeated-path form
```

`scan`'s positional `ARTIFACT` is a required `@click.argument` in
`abicheck/cli_scan.py` (the actual module the `scan` command is registered
from — not `cli.py`, which was misidentified in earlier drafts of this ADR
after Context's own citations). Adding `--artifact-set` cannot simply sit
alongside that required positional: `cli_scan.py` must make `ARTIFACT`
optional and enforce, as a `click.UsageError` (exit 64, matching the
existing usage-error convention for mutually-exclusive `compare` scoping
flags per ADR-043 D2), that a `scan` invocation supplies **exactly one** of
`ARTIFACT` or `--artifact-set` — never both, never neither. This is a
required part of D1's implementation, called out explicitly here since it's
easy for an implementer to add the flag without touching the positional's
`required=` behavior and ship a command that can never actually reach the
new code path.

Rationale against silently overloading the positional `ARTIFACT` the way
`compare` overloads its positional operands (auto-detecting file vs.
directory vs. package): `compare`'s auto-detection is exactly the ambiguity
ADR-043's own Context section criticizes elsewhere (D5's rationale is
explicit-over-implicit: "`--mode`/`--source-method` are removed... there is
no longer a separate flag encoding what presence of `--against` already
tells you" — the same explicit-flag preference applies here). A one-artifact
`scan somefile.so` and an N-artifact `scan --artifact-set somedir/` should
not be the same code path silently branching on `Path.is_dir()`; ADR-023's
own bundle layer is opt-*out* by default for `compare` because directory
input already implies "this is a release," but `scan`'s positional
`ARTIFACT` has no equivalent precedent — it has only ever meant "one
binary." A new, explicit flag keeps that meaning intact and makes the N-ary
case opt-in and visible in the invocation.

`--artifact-set` reuses `ScanRequest.binaries`'s existing plural typing —
closing the scaffolding gap described in Context — rather than introducing
a second field.

`dump` is explicitly **not** extended by this decision. `dump`'s job is "one
persisted snapshot, one library" and every consumer of a dumped snapshot
(cache, `--against`, MCP `abi_dump`) assumes that 1:1 relationship;
overloading it to emit N snapshots (or one merged one — see D2, merging is
rejected) is a bigger, separate change this ADR does not authorize. A
library-set `scan` composes N ordinary single-library `dump`s internally
(see D2), so `dump` itself does not need to change.

### D2. What evidence depth does cross-artifact resolution use?

**Recommendation: reuse and generalize `bundle.py`'s existing
`ResolutionGraph`, do not attempt L2-L5 cross-DSO type merging in this ADR.**

A library-set `scan` produces `list[AbiSnapshot]` — one full snapshot per
artifact, at whatever `--depth` was requested, completely unchanged from
today's single-artifact `scan` pipeline run N times. On top of that list, it
runs the *same* `BundleSnapshot`/`ResolutionGraph`/`compare_bundle`-shaped
machinery `compare`'s directory path already uses (generalized to take a
`list[AbiSnapshot]` + paths instead of being reachable only from
`compare-release`'s directory-matching code), producing the same class of
cross-DSO findings (`bundle_intra_dep_removed`, `bundle_provider_changed`,
etc.) against an **audit baseline** (no old side) the way `scan`'s existing
one-build audit mode already reports single-library findings with no
`--against`.

Explicitly **not** in this ADR's scope:

- Merging N `AbiSnapshot`s' type tables into one cross-artifact type graph
  so a type defined via header in library A and used by value in library B
  resolves through the *same* mechanism `TypeMap`/`surface.py` use within
  one snapshot. This is the "harder, more valuable" case named in the
  original investigation (oneDAL's `oneapi::dal::detail::data_collection`
  used by value across algorithm libraries) — ADR-023 case 3 already
  partially covers a version of this for `compare` (a `type_*_changed` on a
  type reachable from another library's public symbol type closure), by
  reading the *per-library diff's* changes, not by building a merged type
  graph. A library-set `scan` (no old side to diff) has no equivalent
  "changes to read" — it would need direct, snapshot-only cross-library type
  reachability, which is new machinery, not a generalization of what
  `bundle.py` does today. Left as explicit future work (see the ADR-056
  plan's Phase 3+ in G34), not attempted here, for the same reason
  `type_reachability.py`'s own known-gaps list (AGENTS.md) treats each
  additional layer of cross-reference resolution as its own scoped,
  independently-verified follow-up rather than a drive-by extension.
- Fixing the `resolver.py`/`binder.py` vs. `bundle.py` doc/code drift (ADR-023
  amendment). Recorded as a prerequisite worth doing *before* generalizing
  `bundle.py`'s graph to a third caller (this ADR's `scan --artifact-set`),
  since a third caller of an already-diverged-from-its-own-docs module makes
  the drift worse, not better — but the fix itself (either make `bundle.py`
  actually call `resolver.py`/`binder.py`, or formally re-scope ADR-023's
  claim) is its own small, separately-reviewable change, not bundled into
  this ADR's implementation plan.

### Non-goals (mirrors ADR-023's own Non-goals, extended)

- **Merged `AbiSnapshot`s.** A library-set `scan` never produces one
  `AbiSnapshot` for N binaries — `AbiSnapshot.library: str` stays singular;
  each artifact keeps its own identity in every report. Cross-artifact
  findings are always additive/attributed (`provider_library`/
  `consumer_libraries`), exactly like ADR-023's `BundleFinding` shape.
- **Reverse impact analysis against an external application.** Unchanged
  from ADR-023 — stays in `appcompat`/`stack-check`.
- **Dynamic `dlopen`/`dlsym` plugin contracts.** Unchanged from ADR-023.
- **`dump` gaining a multi-artifact form.** See D1 above.
- **Non-ELF (PE/Mach-O) bundles.** `bundle.py`'s resolution graph is
  ELF-only today (ADR-023's own scope); this ADR does not extend that.

---

## Consequences

**Positive**

- Closes the audit-mode gap ADR-023 left open: a user with no "old" snapshot
  (the common case for a first-time scan of a newly vendored multi-.so
  dependency, e.g. oneDAL) gets the same class of cross-DSO findings
  `compare` already gives a user who has two release directories.
- Finishes `ScanRequest.binaries`'s already-plural typing instead of leaving
  it as dead-end scaffolding — the cost estimator's existing
  `len(req.binaries)`-aware code becomes correct rather than unreachable.
- Reuses `bundle.py`'s existing types (`BundleSnapshot`, `ResolutionGraph`,
  `BundleFinding`, the 9 `bundle_*` `ChangeKind`s) rather than inventing a
  parallel set for `scan`.
- Keeps the harder cross-DSO type-merging problem explicitly deferred rather
  than attempted half-way, consistent with how the rest of this codebase's
  AGENTS.md "Known gaps" section treats similar cross-reference resolution
  work as its own scoped, individually-verified passes.

**Negative / cost**

- A new `--artifact-set` flag and a new library-set code path in `scan` is
  real surface growth on a command ADR-043 D5 explicitly tried to keep
  minimal — justified here only because it's additive (existing
  single-artifact `scan ARTIFACT` is completely unchanged) and gated behind
  an explicit flag, not a behavior change to existing invocations.
- Generalizing `bundle.py`'s resolution graph to a second caller
  (`scan --artifact-set`, not just `compare`'s directory path) means any
  future fix to that graph (e.g. resolving the `resolver.py`/`binder.py`
  drift, per ADR-023's amendment) now affects two commands' output, not one
  — needs test coverage on both call sites, not just `compare`'s.
- `--artifact-set` audit findings are opt-in-only-by-flag but, once
  triggered, follow ADR-023's "bundle analysis is default-on for the flag's
  scope" precedent — i.e. once a user passes `--artifact-set`, cross-DSO
  findings are always computed and reported, no separate
  `--no-bundle-analysis`-equivalent opt-out is introduced by this ADR
  (mirroring ADR-023's own default; add one only if usage feedback shows a
  real need, per that ADR's own escape-hatch precedent).

**Migration**

- None for existing users — `scan ARTIFACT` (single positional) is
  byte-for-byte unchanged. `--artifact-set` is new, additive surface.
- CLI/service/MCP parity (ADR-037 D1's tier discipline, ADR-043 D10):
  `service_scan.ScanRequest` already carries the needed field; the MCP
  `abi_scan` tool needs the equivalent `artifact_set` parameter added in the
  same change that adds the CLI flag, not as a follow-up — per ADR-043 D10's
  rule that MCP tool surface changes track CLI changes together.

---

## Implementation plan (not started — see G34 for the tracked, phased version)

1. `abicheck/service_scan.py` — replace `run_scan`'s `len() != 1` guard with
   real multi-binary handling, **without changing `run_scan`'s existing
   return type for the single-binary path**. `run_scan` returns
   `ScanResult` today (`verdict`/`exit_code`/`findings`/`layers`/
   `confidence`/`estimate`/`report`), and existing service callers,
   `run_scan_subprocess`, and the MCP tool all consume it directly —
   `.verdict`/`.exit_code` and `.to_dict()` are load-bearing. A single-item
   `req.binaries` must still return exactly one `ScanResult`, unchanged. A
   new multi-binary entry point (e.g. `run_scan_set(req) ->
   ScanSetResult`, a new aggregate dataclass wrapping `per_artifact:
   list[ScanResult]` + the bundle layer's findings/verdict) is added
   alongside `run_scan`, not as a change to what `run_scan` itself returns.
2. `abicheck/bundle.py` — generalize the entry point that currently only
   `compare-release`'s directory matching calls (`build_bundle_snapshot`/
   `compare_bundle`) to accept a `list[AbiSnapshot]` + path list directly,
   so a library-set `scan` can call it without going through
   `compare-release`'s file-matching layer at all (there is no "old vs new"
   matching to do for a single-side audit).
3. `abicheck/cli_scan.py` (the module the `scan` command is actually
   registered from — see D1 above) — make the existing `ARTIFACT`
   `@click.argument` optional, add `--artifact-set` (directory or
   comma-separated explicit list), and enforce exactly one of the two is
   given (`click.UsageError`, exit 64) before wiring to
   `ScanRequest.binaries` / the new `run_scan_set` entry point.
4. `abicheck/mcp_server.py` — add the equivalent `artifact_set` parameter to
   `abi_scan`, same validation shape as the CLI flag.
5. Reporter — `scan`'s report gains a `bundle_findings`/`bundle_verdict`
   section when `--artifact-set` was used, reusing ADR-023's existing
   `bundle.json`/`bundle.md` output shape rather than inventing a new one.
6. Tests — `tests/test_scan_estimate.py::test_run_scan_rejects_multiple_binaries`
   needs updating to reflect the new plural-accepting behavior (a
   single-binaries-list still works exactly as before); new
   `tests/test_scan_artifact_set.py` mirroring `tests/test_bundle.py`'s
   shape for the audit-mode (no old side) case.
7. `tests/test_cli_root_surface.py` / `README.md` /
   `docs/reference/cli-reference.md` — updated together per AGENTS.md's
   root-command-surface-change rule (this is a flag addition to an existing
   command, not a new root verb, but the same "don't update code without
   docs/tests in the same PR" discipline applies).
8. Examples — at least one `--artifact-set` audit-mode example case
   (two-library bundle, no old side, one intra-bundle finding), following
   ADR-023's own example obligations.

---

## References

- ADR-002: Multi-binary / release compare UX
- ADR-006: Package-level comparison
- ADR-008: Full-stack dependency validation (`resolver.py`, `binder.py`)
- ADR-023: Bundle-Aware Multi-Binary ABI Analysis (the `compare`-side
  precedent this ADR extends to `scan`; see its 2026-07-29 amendment for the
  `resolver.py`/`binder.py` drift this ADR's D2 flags as a prerequisite)
- ADR-037: CLI Interface Contract (D1 tier discipline, D10 MCP parity)
- ADR-043: Pre-1.0 CLI Surface Reset (D5, the decision this ADR narrowly
  amends; D1/D10 admission-bar precedent reused for D1 above)
- ADR-054: CLI Project-Integration Surface Consolidation (root-command
  admission bar referenced in D1)
