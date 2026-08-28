# ADR-056: Multi-Artifact / Library-Set `scan`

**Date:** 2026-07-29
**Status:** Proposed — partially implemented (see
[G35](../plans/g35-multi-artifact-scan.md)'s "Implementation status" note
for what shipped ahead of formal sign-off vs. what remains deferred). The
shipped slice lives in `abicheck/service_scan.py`, `abicheck/bundle.py`, and
`abicheck/cli_scan.py`.
**Verified:** main@2e43d53 on 2026-08-04
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
- `scan`: `abicheck/cli_scan.py`'s `ARTIFACT` argument is a single positional,
  deliberately narrowed by **ADR-043 D5** ("`scan` always targets exactly
  one artifact") from an earlier repeated `--binary` flag. The service layer
  enforces this explicitly: `ScanRequest.binaries: list[Path]`
  (`service_scan.py:226`) is already plural-typed, and the cost estimator
  (`_intrinsic_layer_estimates`) already sums its **`L0_binary` row** over
  `len(req.binaries)` — but `run_scan` (`service_scan.py:881-883`)
  hard-rejects `len(req.binaries) != 1`. This is unfinished scaffolding,
  not a deliberate plural-then-narrowed design: no comment, test, or ADR
  explains why the field is plural while the guard is singular.
  **Correction (checked against the live estimator, not assumed):** only
  that one `L0_binary` row is `len(req.binaries)`-aware —
  `L1_debug`/`L2_header` (`_intrinsic_layer_estimates`) and every
  `L3_build`/`L4_graph`/`L5_source` row
  (`_source_layer_estimates`) are computed **once**, independent of
  `len(req.binaries)`. The estimator is not "already plural-aware" as a
  whole; only its cheapest row is. See G35 Phase 1/3 for what this means
  for `run_scan_set`'s own cost accounting.
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
abicheck scan --artifact-set a.so --artifact-set b.so --artifact-set c.so   # new: explicit path form
```

**Superseded (2026-08-28, CLI cleanup phase two, PR 5):** the third line
above originally showed a comma-separated `--artifact-set a.so,b.so,c.so`
value; the flag is now a repeatable option instead (shown above), with no
comma-separated alias — see
`docs/contribute/plans/cli-cleanup-phase-two.md`'s PR 5 section. This ADR's
own decision (an alternate operand shape for `scan`, D2 through D5 below)
is unaffected; only the value syntax for multiple explicit paths changed.

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

**`--against` must also be rejected with `--artifact-set`.** `scan --against
OLD` stores a single baseline path in `ScanRequest.baseline`
(`abicheck/cli_scan.py`); nothing about that shape extends to a *set* of
artifacts each needing their own, distinct old-side baseline — running
every member of `--artifact-set` against the same single `--against` value
would silently compare unrelated libraries against one shared file. D2
below scopes `--artifact-set` to **audit-only** (no old side, no
`--against`) specifically to avoid designing that set-to-set baseline
question here; `cli_scan.py` must therefore reject `--artifact-set
--against` together as a `click.UsageError`, the same way it rejects
`ARTIFACT --artifact-set` together. A future ADR can revisit a genuine
set-vs-set comparison form if there's real demand — not silently allowed
through by omission in this one.

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
builds the *same* `BundleSnapshot`/`ResolutionGraph` machinery `compare`'s
directory path already uses (generalized to take a `list[AbiSnapshot]` +
paths instead of being reachable only from `compare-release`'s
directory-matching code) for the current set alone.

**This is deliberately a narrower finding set than `compare`'s bundle layer,
not the same one.** Re-reading ADR-023's own detection steps: 7 of its 9
`bundle_*` kinds are constructed by reading a *per-library diff's* changes
(`func_removed`, `func_params_changed`, `type_*_changed`, ...) against the
new-side resolution graph — `bundle_intra_dep_removed`,
`bundle_intra_dep_signature_changed`, `bundle_intra_type_changed`,
`bundle_provider_changed`, both manifest-instantiation kinds, and both
library-added/-removed kinds all require an old side to diff against. A
library-set `scan` audit has no old side by construction, so none of those
apply.

**Correction (checked against the shipped code, not ADR-023's original
design table):** `ResolutionGraph` (`abicheck/bundle_models.py`) has no
`unresolved: list[UnresolvedImport]` field — that field only ever existed
in ADR-023's proposed design; what shipped is `provides`/`consumers`/
`intra_needed`/`extra_needed`, and the "symbol nothing in the bundle
provides" computation lives inline in `_detect_intra_dep_removed`
(`abicheck/bundle.py`), not as a precomputed graph field. Generalizing that
detector to a no-old-side audit is **not** a safe drop-in reuse:
`_detect_intra_dep_removed` relies on `_import_is_external` to rule out a
legitimately external dependency before flagging a finding, and
`_import_is_external` returns `False` immediately for any *unversioned*
import (`consumer.version == ""`, `abicheck/bundle.py`) — by design, since
in `compare`'s diff-driven case an unversioned sibling import that used to
resolve and now doesn't is exactly the regression the detector exists to
catch. An audit-only `--artifact-set` has no "used to resolve" history to
lean on: a library in the set that legitimately imports an unversioned
symbol from a real dependency *outside* the declared set (any DSO not on
the `--bundle-system-providers` allow-list) would be indistinguishable from
a genuinely broken intra-set reference, and reusing the detector unmodified
would report it as `BREAKING` — a false positive, not a corner case.

The audit-scoped finding this ADR authorizes must therefore be more
conservative than a direct reuse of `_detect_intra_dep_removed`:

- `--artifact-set` audits under a **declared closed-world assumption**: the
  user is asserting the given set is the complete intra-set surface they
  care about, with any known external dependency named via a
  `--bundle-system-providers` equivalent. `--bundle-system-providers` today
  is declared only on `compare`'s `release_options` (`abicheck/cli_options.py`,
  consumed by `cli_compare_release.py`) — this ADR requires the **same
  flag** be added to `scan`'s `--artifact-set` path too (`cli_scan.py`),
  plus the matching `abi_scan` MCP parameter and Action input, and threaded
  through to `run_scan_set`'s audit-mode detector (D2's new check). Without
  it, a `scan --artifact-set` user has no way to make the closed-world
  declaration this design assumes, and every legitimate external dependency
  produces an avoidable risk finding — that gap would defeat the point of
  downgrading the kind to `COMPATIBLE_WITH_RISK` in the first place. This
  must be stated plainly in the command's `--help` text and docs, not left
  implicit.
- Even under that assumption, an unversioned import with no intra-set
  provider is **evidence of an unresolved reference, not proof of one** —
  the audit has no diff to confirm it ever worked. The new kind's
  `default_verdict` is therefore `COMPATIBLE_WITH_RISK`, not `BREAKING`
  (mirroring `bundle_provider_changed`'s own precedent in ADR-023's table
  for an indeterminate-until-confirmed case), and its description/evidence
  must say explicitly "no provider found in this artifact set" rather than
  implying removal.
- Reported as a new, audit-scoped kind (e.g.
  `bundle_unresolved_intra_dependency`, exact name TBD at implementation
  time) rather than reusing `bundle_intra_dep_removed`'s name — that kind's
  own registry description is specifically "no longer provides" (implies a
  diff confirming a prior working state), which this finding cannot claim.
- `bundle_intra_dep_resolved_to_different_version` is also diff-shaped (old
  `gnu.version` vs. new) and does not apply.

See G35 Phase 2 for the audit-mode entry point this implies, including the
requirement that it build its own conservative check rather than calling
`_detect_intra_dep_removed` directly.

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
  plan's Phase 3+ in G35), not attempted here, for the same reason
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

- Closes part of the audit-mode gap ADR-023 left open: a user with no "old"
  snapshot (the common case for a first-time scan of a newly vendored
  multi-.so dependency, e.g. oneDAL) gets at least the unresolved-import
  subset of cross-DSO findings `compare` already gives a user who has two
  release directories — not the full diff-driven 9-kind set, which
  structurally needs an old side (see D2).
- Finishes `ScanRequest.binaries`'s already-plural typing instead of leaving
  it as dead-end scaffolding — the estimator's existing `L0_binary`
  `len(req.binaries)`-aware row becomes reachable (the other rows still
  need the fix G35 Phase 1/3 describes to scale correctly too).
- Reuses `bundle.py`'s existing types (`BundleSnapshot`, `ResolutionGraph`,
  `BundleFinding`) rather than inventing parallel ones for `scan`; only one
  new, narrower audit-scoped `ChangeKind` is added (D2), not a duplicate of
  the 9 `compare`-side ones.
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

## Implementation plan

**Partially implemented** (see the Status header above).
[G35](../plans/g35-multi-artifact-scan.md) is the single,
tracked source of truth for the phased implementation breakdown — module
list, task-by-task detail, and status per phase. Earlier drafts of this
ADR duplicated that breakdown inline here as a second numbered list; it
drifted out of sync with G35 more than once as review rounds corrected
details in one copy but not the other (most notably: whether `run_scan`
itself keeps rejecting a multi-item `binaries` list — it does, per D1/D2
above; only the new `run_scan_set` entry point accepts multiple binaries).
Per this file's own "one fact defined in exactly one place" rule
(`docs/AGENTS.md`), the phased plan now lives only in G35; this ADR records
the *decision* (D1/D2 above) and defers to G35 for *how* it gets built.

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
