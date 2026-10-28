# ADR-090: An Unresolved Surface Without Type-Level Evidence Is Not a Clean Verdict

**Date:** 2026-10-28
**Status:** Proposed — not implemented. This ADR proposes a change to the default verdict label and to the default exit code of a `compare` run with no type-level evidence. No code has changed.

Governed by: [ADR-009](009-verdict-system-and-exit-codes.md) (verdict and exit codes), [ADR-042](042-compatibility-and-gate-decision-separation.md) (compatibility decision separated from gate decision), [ADR-049](049-contract-relevance-and-compatibility-configuration.md) (contract domain default), [ADR-064](064-canonical-gate-algorithm-and-exit-decision.md) (exit-decision precedence), [ADR-065](065-comparison-scope-selection-and-completeness.md) (absent is not removed), [ADR-066](066-longitudinal-history-and-versioning-policy.md) (policy decides acceptance, not facts), [ADR-071](071-release-analysis-assurance-fold.md) (`assurance.require_complete` for a release), [ADR-084](084-binary-depth-source-contract-gate.md) (a header-less exit-code change, maintainer-approved precedent), [ADR-088](088-one-compare-exit-gate.md) (one compare exit gate).

## Context

### The reported defect, reproduced

Built from `catalog/cases/case19_enum_member_removed` (`old/lib.c` and `new/lib.c`) with `gcc -shared -fPIC -g`, then `strip --strip-debug` applied to copies. Run on this checkout (`python -m abicheck` from the worktree). The scratch directory is shown as `<scratch>/`; nothing else was edited.

Unstripped, no headers (`python -m abicheck compare one_unstripped.so two_unstripped.so`), exit `4`:

```
Warning: '<scratch>/one_unstripped.so' — no headers provided. Will use DWARF debug info if available, else symbols-only mode.
Warning: '<scratch>/two_unstripped.so' — no headers provided. Will use DWARF debug info if available, else symbols-only mode.
abicheck: DWARF debug info ...
abicheck: DWARF debug info ...
abicheck: DWARF debug info done (0.0s)
abicheck: DWARF debug info done (0.0s)
one_unstripped.so   old -> new
Compatibility: BREAKING | Policy: strict_abi | Gate: REJECTED (exit 4)
Evidence: recorded snapshot facts; no debug-derived layout verification, no source replay.
WARNING: No header/AST data; type-level changes may be missed
Findings: 2 detected; 1 retained (1 gating, 0 non-gating); 1 review groups.
Public surface: 0 additions, 1 removals, 0 modifications.
Runtime/dependency: 0 findings.
Header/binary consistency: 0 introduced, 0 resolved, 0 persistent, 0 not evaluated.

1. Status::FOO: removed
   Consequence: Enum member removed: Status::FOO
   Action: Review the detailed finding and its evidence.
   Findings: enum_member_removed

Audit: 2 detected; 1 gating; 0 acknowledged; 0 suppressed; 0 rule(s) applied; 0 scope-excluded.
Details: add -o markdown=abi-report.md -o json=abi-report.json to this invocation.

Exit-code scheme: legacy verdict (0=compatible, 2=API break, 4=ABI break; 1=the contract domain could not be closed on the evidence; with .abicheck.yml's assurance.require_complete: true, 1=incomplete analysis assurance -- both orthogonal axes that never lower a 2/4). Set a severity setting (--severity-preset, a --pack, or .abicheck.yml's severity: block) for the severity-aware scheme -- there is no manual scheme selector any more.
```

Stripped, no headers (`python -m abicheck compare one.so two.so`), exit `0`:

```
Warning: '<scratch>/one.so' — no headers provided. Will use DWARF debug info if available, else symbols-only mode.
Warning: '<scratch>/two.so' — no headers provided. Will use DWARF debug info if available, else symbols-only mode.
abicheck: DWARF debug info ...
abicheck: DWARF debug info done (0.0s)
abicheck: DWARF debug info ...
abicheck: DWARF debug info done (0.0s)
Warning: public-header scoping could not resolve the public surface (no header-derived public symbols); fell back to the full export table. Compatibility is UNCONFIRMED — treat this result as manual-review-required, not a clean public surface.
one.so   old -> new
Compatibility: NO_CHANGE | Policy: strict_abi | Gate: ACCEPTED (exit 0)
Evidence: recorded snapshot facts; no debug-derived layout verification, no source replay.
WARNING: Manual review required -- the public surface could not be resolved, so analysis fell back to the full export table. Treat this result as unconfirmed, not a clean public surface.
WARNING: Binary-only analysis without debug info; many ABI changes cannot be detected (struct layout, enum values, type changes)
Findings: 0 detected; 0 retained (0 gating, 0 non-gating); 0 review groups.
Public surface: 0 additions, 0 removals, 0 modifications.
Runtime/dependency: 0 findings.
Header/binary consistency: 0 introduced, 0 resolved, 0 persistent, 0 not evaluated.

Audit: 0 detected; 0 gating; 0 acknowledged; 0 suppressed; 0 rule(s) applied; 0 scope-excluded.
Details: add -o markdown=abi-report.md -o json=abi-report.json to this invocation.

Exit-code scheme: legacy verdict (0=compatible, 2=API break, 4=ABI break; 1=the contract domain could not be closed on the evidence; with .abicheck.yml's assurance.require_complete: true, 1=incomplete analysis assurance -- both orthogonal axes that never lower a 2/4). Set a severity setting (--severity-preset, a --pack, or .abicheck.yml's severity: block) for the severity-aware scheme -- there is no manual scheme selector any more.
```

With `.abicheck.yml` containing `assurance.require_complete: true` in the stripped directory: exit `1`. Without it: exit `0`, as above.

### The matrix observed so far

All runs below are gcc, C, on this checkout. "stored" means `dump` to JSON, then `compare` on the two JSON files.

| Operands | Headers | Live / stored | `verdict` | exit | `analysis_assurance.status` | `confidence` / `evidence_tier` | `scope.resolved` |
|---|---|---|---|---|---|---|---|
| unstripped (`-g`) both | none | live | BREAKING | 4 | complete | medium / dwarf_aware | true |
| unstripped (`-g`) both | none | stored | BREAKING | 4 | — | — | — |
| stripped both | none | live | **NO_CHANGE** | **0** | partial | low / elf_only | false |
| stripped both | none | stored | **NO_CHANGE** | **0** | not captured (text output only) | not captured | not captured |
| stripped both | `-H` per side | live | BREAKING | 4 | complete | high / header_aware | true |
| unstripped both | `-H` per side | live | BREAKING | 4 | complete | high / header_aware | true |
| old unstripped, new stripped | none | live | **COMPATIBLE** | **0** | partial | medium / dwarf_aware | true |

The last row is a second instance of the same defect, and it is not the shape the brief described. The old side has DWARF and the new side does not. `enum_member_removed` is **not reported at all**. The report carries two findings, `dwarf_info_missing` and `public_surface_shrank` (2 → 1 declarations), both `compatible`. The verdict is `COMPATIBLE`, which is not `NO_CHANGE`, and the exit is 0. The scope resolved, because the one-sided DWARF side still produced a public surface. So a rule keyed only on "public surface unresolved" does not fire on this run.

The stripped-both headline also contradicts its own `release_recommendation`: `version_bump: none`, rationale "No ABI or API changes detected; no version bump required." The `run_outcome.compatibility`, the top-level `verdict`, and the terminal headline all say `NO_CHANGE`. The `exit.reasons` is `["clean"]`.

### What the rule says

`AGENTS.md` (Product decisions, "Weaker evidence narrows conclusions"): missing evidence "never upgrades to a clean compatibility claim". "Absent is not removed" requires that an unknown is reported as unknown. `vision.md` (Evidence and trust): "uncertainty is reported as uncertainty and never upgraded to a clean compatibility claim", and "a strict project may require certain evidence and fail its assurance gate when it is missing, without that gate manufacturing an ABI finding."

The detection gap itself is legitimate. Without DWARF or headers, an enumerator removal cannot be seen. The defect is the claim. The label, the JSON verdict, the `release_recommendation`, and the gate all assert that the comparison is clean, while the same run's warnings and `analysis_assurance.status: partial` say it is not.

### The machinery already exists, but its default is off

- `analysis_assurance` is already computed on every `compare`. `policy/analysis_assurance/_compute.py` derives `status: partial` from roughly a dozen conditions. One of them is `not result.scope_resolved`, so the stripped run above is `partial` through that branch. The others include asymmetric L0/DWARF/L3 context, graph degradation, unaccounted exports, and contract-coverage partiality.
- The exit contribution exists but is opt-in. `policy/analysis_assurance/_outputs.py` returns 0 "unconditionally when `require_complete` is False (the default)". `docs/reference/exit-codes.md` ("Analysis-assurance contribution") documents `assurance.require_complete: true` as the only switch.
- The headline does not consult `analysis_assurance`. `Compatibility: NO_CHANGE` is computed from the change set alone, and the warnings are appended beside it.

### The same defect is already known and open

`docs/contribute/known-gaps.md`, entry dated 2026-10-15 ("A default release-profile Rust run without headers reports `NO_CHANGE ... ACCEPTED` while its own warnings say the result is unconfirmed"), status OPEN. Its full write-up is `docs/contribute/known-gaps/header-less-rust-runs-report-no-change-and-alignment-noise.md`. It records a real return-type change read as `NO_CHANGE`, `Gate: ACCEPTED`, exit 0. The 2026-10-15 entry says the fix "is a verdict-labelling decision (ADR-066/063), not a fix for one reader." This ADR is that decision.

### Two observations from the reproduction that shape the proposal

1. The `dwarf_info_missing` finding is emitted only when exactly one side has DWARF. The both-stripped run emits warnings but no finding. So the tool records one-sided absence as a fact and both-sided absence only as prose.
2. `--header` (`-H`) applied as shorthand gives both sides the same header. The stripped run with one shared header read `NO_CHANGE`, which was my own setup error (the new side still declares `FOO`). With `--header old=… --header new=…` it reads `BREAKING`/4. Per-side headers are needed to test this path.

## Decision

Proposed. Nothing below is implemented.

### D1. The trigger

A comparison is **unconfirmed** when all of the following hold:

- **(a)** The change-derived verdict is `NO_CHANGE`, `COMPATIBLE` or `COMPATIBLE_WITH_RISK`. An observed `API_BREAK` or `BREAKING` is never downgraded or overridden by this rule.
- **(b)** Type-level evidence is **absent on at least one compared side**. Type-level evidence means either a DWARF type/layout section or a header AST for that side.
- **(c)** The two operands are **not byte-identical** (equal SHA-256). Identical operands are a fact (`NO_CHANGE` by construction), not an evidence gap, so they are exempt.

The public surface being unresolved (`scope.resolved: false`) is recorded as a reason in the notes but is **not** a required conjunct. The brief proposed "public surface unresolved AND type-level evidence absent on either side". The mixed run in the matrix above (one side DWARF, one side stripped, no headers) is the reason for dropping the conjunct: it has a resolved scope, has type-level evidence missing on one side, and reads `COMPATIBLE`/0 with the enum removal unreported. Keeping the conjunct would leave that case as a clean-looking pass.

Under D1, the table of cells is:

- Both sides have DWARF, or both have headers: not triggered.
- One side has headers and the other is stripped with no headers: triggered (the stripped side has no type-level evidence).
- Both sides stripped, no headers: triggered.

### D2. The outcome

When triggered, the compatibility outcome is **`UNCONFIRMED`**, not `NO_CHANGE` or `COMPATIBLE`. It is a new value on the compatibility axis:

- Headline: `Compatibility: UNCONFIRMED | Policy: strict_abi | Gate: REJECTED (exit 1)`. The gate word is provisional; the point is that the headline and the gate agree.
- JSON: `verdict: "UNCONFIRMED"`, `run_outcome.compatibility: "UNCONFIRMED"`, `exit.reasons` gains `"unconfirmed_compatibility"`, and a new `exit.unconfirmed_contribution` (0 or 1) sits beside the other axis contributions.
- `release_recommendation.version_bump` becomes `"undetermined"` with a rationale naming the missing evidence and the two remedies (headers on both sides, or unstripped debug builds). The current `"none"` is the clean claim this ADR removes.

Where the value lives: `Verdict` (`abicheck/model/change_catalog/registry.py`) is the change-derived enum. The proposal adds `UNCONFIRMED` to the *report's* compatibility vocabulary, not to the change-kind verdict set that `*_KINDS` derive from. The implementation has to keep those two apart. A separate `compatibility_outcome` field was considered and rejected: the 2026-10-15 defect is exactly a headline and a field disagreeing, and a field that consumers ignore keeps the clean headline.

### D3. The exit code

`UNCONFIRMED` contributes exit **1** by default, folded with `max` like the existing contract-coverage and analysis-assurance axes. It never lowers a 2 or 4. No new exit code is introduced, because `1` already means "the analysis could not be closed on the evidence" in `docs/reference/exit-codes.md`.

### D4. Opt-out and migration

- New `.abicheck.yml` key: `assurance.unconfirmed: fail | warn`. `fail` is the default from stage 2. `warn` keeps the `UNCONFIRMED` label and the same JSON, and contributes exit 0. `warn` is an honest, permanent, non-gating mode, not a deprecated one. The label itself has **no** opt-out, because the label is the thing being corrected.
- No setting restores `NO_CHANGE` for a triggered run.
- Stages (pre-1.0; the surface reset precedent is [ADR-043](043-cli-pre-1.0-surface-reset.md)):
  - **Stage 0** (this ADR accepted): no behaviour change; the design is recorded and the test matrix below is built, with the oracle described there.
  - **Stage 1** (next minor release): the `UNCONFIRMED` label ships in the headline, JSON, SARIF and Markdown, with exit still 0. stderr notice names the stage-2 exit change and the `assurance.unconfirmed` key. Changelog fragment (`scriv create`) marks it a behaviour change, because any consumer matching `verdict == "NO_CHANGE"` on a stripped run will see it change.
  - **Stage 2** (the following minor release): default exit becomes 1 (`assurance.unconfirmed: fail`). Changelog fragment, `docs/reference/exit-codes.md` migration note, and the known-gap entry from 2026-10-15 closed.
- Release (directory/package) fan-out: each member's own triggered contribution folds with `max`, the same pattern as ADR-071.

### D5. Remedy wording

The `UNCONFIRMED` message names the two remedies, in this order: supply `--header old=… --header new=…` for both sides, or compare unstripped debug builds (`-g` without `strip --strip-debug`). It does not say the build is wrong.

## Surfaces affected

Exact list for the implementation PR. Each item needs a check, not just a text update.

**CLI** (`abicheck compare`)
- The scalar `compare`, and the directory/package fan-out (`--select`, `--dry-run` plan view if it reports a verdict).
- `compare --no-baseline` (ADR-068's audit mode): its headline and exit.
- The stderr "Exit-code scheme" epilog text, which names the current exit-1 meanings.
- The `frontends/cli/runtime.py` stderr notice path.

**Other CLI commands that consume a compare exit**
- `abicheck aggregate` reads `analysis_assurance_exit_contribution` per target. It needs an `unconfirmed_exit_contribution` term alongside it.
- `dump` produces no verdict; it is affected only where a stored snapshot is later compared. The trigger must be decidable from stored facts (see Open questions).

**Config**
- `abicheck/buildsource/build_config.py`: new `KeySpec` for `assurance.unconfirmed`, beside `assurance.require_complete`.
- `docs/reference/config-keys-reference.md` (generated by `scripts/gen_config_reference.py`).

**GitHub Action**
- Root `action.yml` outputs and gate.
- `actions/check-target`, `actions/aggregate`, `actions/report`. `actions/report` is a projection (ADR-072, ADR-073) and must render `UNCONFIRMED` without recomputing it.
- `docs/reference/github-action-inputs.md` (generated) if any input text changes.

**Typed Python API**
- `CompareRequest` and `effective_gate_for_compare` (`abicheck/policy/effective_gate.py`, ADR-088): the new key has to flow through the same single derivation, so the CLI and typed API cannot disagree.
- `DiffResult.verdict` and `AnalysisAssurance` consumers.

**JSON and schemas**
- `abicheck/schemas/compare_report.schema.json`: `verdict` enum, `run_outcome.compatibility`, `exit.reasons`, the new `unconfirmed_contribution`, `release_recommendation.version_bump` enum.
- `abicheck/schemas/aggregate_report.schema.json`.
- `abicheck/schemas/release_schema.py` (release JSON).
- `abicheck/report/change_json.py` (the producer of those fields).

**Other formats**
- SARIF (`abicheck/sarif.py`): the run-level level for `UNCONFIRMED` (see Open questions).
- JUnit (`abicheck/junit_report.py`): ADR-042 notes it computes its exit inline in places, so it needs the same change.
- HTML CI Gate card (`abicheck/html_report.py`).
- Markdown (`abicheck/reporter_markdown.py`, which already has a manual-review branch around line 845).
- Terminal review (`abicheck/report/render_review.py`).
- Confidence text (`abicheck/confidence.py`, the "Binary-only analysis without debug info" warning).

**Policy**
- `abicheck/policy/exit_decision.py`, `exit_decision_precedence.py` (the new axis in ADR-064's precedence).
- `abicheck/policy/analysis_assurance/_outputs.py` (today's `require_complete` contribution).
- `abicheck/policy/release_assurance.py` and `release_exit_decision.py` (release fold).

**Docs**
- `docs/reference/exit-codes.md`: the exit-1 row, the analysis-assurance section, and the "Exit `0` covers `NO_CHANGE`, `COMPATIBLE`, and `COMPATIBLE_WITH_RISK`" warning, which needs `UNCONFIRMED` added as a non-zero case.
- `docs/learn/verdicts.md`: the verdict-to-exit chain.
- `docs/contribute/known-gaps.md`: the 2026-10-15 Rust entry and its write-up, closed at stage 2.

## Consequences

- **Stripped and debug-less libraries stop passing by default.** A `compare` with no type-level evidence on a side exits 1 from stage 2. That is the point of the change, and it is the largest population affected. Default `cargo build --release` artifacts are the known case; the blast radius across other ecosystems is not measured.
- **Byte-identical stripped inputs are the exception that matters for CI.** Under D1(c) they remain `NO_CHANGE`, because they are provably the same. Without that exemption, `compare same.so same.so` on stripped artifacts would fail, which is absurd.
- **Consumers that match on `verdict` see a new value.** Stage 1 exists so this breaks with a warning first. Any consumer that enumerates verdicts exhaustively needs `UNCONFIRMED` added.
- **The standalone-binary promise is preserved as admission, not acceptance.** `vision.md` says "standalone binaries are first-class inputs, even stripped ones" and "debug information is consumed when present and is never a prerequisite". This ADR does not make stripped binaries inputs that are refused. It changes what a clean result on them may claim. The vision's own wording ("the coverage stated") supports that reading, but a maintainer should confirm it, because it is the strongest tension with the vision.
- **Header-less exit changes have a precedent in both directions.** [ADR-084](084-binary-depth-source-contract-gate.md) lowered a header-less exit from 2 to 0, with maintainer approval. This ADR raises a header-less exit from 0 to 1, and it needs the same kind of approval.
- **The one-sided DWARF case must not depend on scope.** Because D1 does not require `scope.resolved: false`, the mixed run stops being a clean pass. It also means `scope.resolved` no longer carries the whole burden of the trigger.
- **Known-gap closure.** The 2026-10-15 Rust entry is closed at stage 2 and its write-up is updated.
- **Traceability.** `docs/contribute/adr/adr-surface-registry.yaml` gets a `gap` entry for this ADR, following ADR-079 and ADR-083, because nothing is shipped yet.

## Alternatives considered

1. **Keep as-is and record it as a known gap.** This is the status quo: the 2026-10-15 Rust entry already exists and is OPEN, and the defect is still reproducible. A known-gap entry records the defect; it does not stop the label or the exit from asserting a clean result. It also leaves the rule in `AGENTS.md` violated on the default path, which the brief and this repository's own rule both forbid. **Rejected.**

2. **Change only the text label.** For example `NO_CHANGE (unconfirmed)`, or keep the enum and only reword the warning. The JSON `verdict`, `run_outcome.compatibility`, `release_recommendation.version_bump: "none"`, SARIF level, Action outputs, and the gate all still read clean. The stripped run already prints two warnings that say "unconfirmed" beside `NO_CHANGE ... ACCEPTED`, and that did not prevent the reading the maintainer reported. **Rejected.**

3. **Make `assurance.require_complete` default true.** This is the strongest alternative on exit, and it does catch the mixed run, because that run is `partial`. Its problems are these. It flips the exit on every `partial` run, and `partial` has about a dozen causes unrelated to type-level evidence (asymmetric L0 context, graph degradation, unaccounted exports, contract-coverage partiality, and others). It keeps the headline `NO_CHANGE` on a run where the verdict is still asserted clean, so the label remains the defect. It also leaves the opt-in shape in place and only changes its default. **Rejected as the primary fix. Its breadth should be measured before it is ever proposed again**, because the number of runs it would newly fail is unknown here.

4. **Trigger only on "public surface unresolved" (the brief's conjunct, minus type-level evidence).** This is simpler, and it is what `scope.resolved` already encodes. It misses the mixed run, which reads `COMPATIBLE`/0 with a resolved scope and an unreported enum removal. **Rejected** in favour of D1's disjunction on type-level evidence.

5. **A new exit code for `UNCONFIRMED`.** The exit-code table is a public contract (`docs/reference/exit-codes.md`), and `1` already carries "could not be closed on the evidence". A new code would break every consumer that switches on the existing set. **Rejected.**

6. **A separate `compatibility_outcome` field, leaving `verdict` as is.** Considered above in D2. A field that consumers ignore is what let the headline and the gate disagree. **Rejected.**

## Test matrix

The test matrix is the proof this ADR asks for. Its oracle is not the implementation's own helper. The oracle is: (i) `catalog/cases/case19_enum_member_removed` as ground truth (`enum_member_removed` is BREAKING, exit 4, when type-level evidence is present on both sides); (ii) the rule "a triggered comparison must never read `NO_CHANGE`, `COMPATIBLE` or `COMPATIBLE_WITH_RISK` with exit 0 in any surface"; and (iii) byte-identity, which is decided by the operands' hashes, not by the implementation under test.

Dimensions: stripped (`strip --strip-debug`) versus unstripped (`-g`), per side (both, and the mixed cases in both orientations); headers none versus per-side `-H`; C versus C++; live versus stored (`dump` then `compare`); gcc and clang. Plus release fan-out with one stripped member, and identical-input cases.

| # | Operands | Headers | Language | Live / stored | Expected after implementation | Observed today |
|---|---|---|---|---|---|---|
| 1 | unstripped both | none | C | live | BREAKING / 4 | BREAKING / 4 (verified) |
| 2 | unstripped both | none | C | stored | BREAKING / 4 | BREAKING / 4 (verified) |
| 3 | stripped both | none | C | live | UNCONFIRMED / 1 | NO_CHANGE / 0 (verified) |
| 4 | stripped both | none | C | stored | UNCONFIRMED / 1 | NO_CHANGE / 0 (verified) |
| 5 | stripped both | per-side | C | live | BREAKING / 4 (not UNCONFIRMED: headers present both sides) | BREAKING / 4, `aa` complete (verified) |
| 6 | unstripped both | per-side | C | live | BREAKING / 4 | BREAKING / 4 (verified) |
| 7 | old unstripped, new stripped | none | C | live | UNCONFIRMED / 1 (verdict is `COMPATIBLE` today) | COMPATIBLE / 0, enum removal not reported (verified) |
| 8 | old stripped, new unstripped | none | C | live | UNCONFIRMED / 1 | not run |
| 9 | old unstripped, new stripped | `-H` on the new side only | C | live | UNCONFIRMED / 1 if no change is observed (the old side still has no type-level evidence); BREAKING if the change is observed | not run |
| 10 | stripped both, byte-identical | none | C | live | NO_CHANGE / 0 (exempt by D1(c)) | not run |
| 11 | stripped both | none | C++ | live and stored | UNCONFIRMED / 1 | not run |
| 12 | stripped both | per-side | C++ | live | BREAKING where the change is header-visible | not run |
| 13 | rows 1–9 | — | clang | live and stored | same as gcc | not run |
| 14 | release directory, one stripped member | none | C | release fan-out | release exit = max of member contributions; the member reads UNCONFIRMED | not run |
| 15 | default `cargo build --release` cdylib with a return-type change | none | Rust | live | BREAKING if observed, otherwise UNCONFIRMED / 1 (closes the 2026-10-15 gap) | NO_CHANGE / 0 (documented in the known-gap write-up, not re-run here) |

Assertions required across every surface in the Surfaces list, not only the CLI: the JSON `verdict`, `run_outcome.compatibility`, `exit.code`, `release_recommendation.version_bump`; the SARIF and JUnit output; the Markdown and HTML headline; the Action outputs; the typed `CompareRequest` result; the `aggregate` roll-up. Each must agree with the others for a given cell. A `NO_CHANGE` or `COMPATIBLE` with exit 0 on any row where D1's trigger holds fails the matrix.

Negative controls: rows 1, 2, 5 and 6 must keep their current verdict and exit. Any change on those rows is over-triggering.

Per the repository's bug-class rule, the regression test exercises the invariant with more than the reported input: at least the rows above plus a generated set of stripped-versus-unstripped pairs, against the oracle, not against the trigger helper.

## Open questions for maintainers

1. **Trigger breadth.** D1 includes `COMPATIBLE_WITH_RISK`. Should risk-only results on a debug-less side be `UNCONFIRMED`, or should only `NO_CHANGE` and `COMPATIBLE` be triggered? Including it is the rule as written; excluding it is narrower and would still leave the case-19 defect fixed.
2. **Verdict enum versus compatibility field.** D2 adds `UNCONFIRMED` to the report's verdict vocabulary. The change-kind `Verdict` enum stays unchanged. Confirm this split is acceptable to the `*_KINDS` derivation and to ADR-042.
3. **Stored-snapshot decidability.** D1(b) must be decidable from a stored snapshot. The stored stripped run is `partial` and `elf_only`, so the fact is at least partly recorded. It has not been confirmed which snapshot field records per-side DWARF or header presence, and the implementation must check that before stage 1.
4. **Stage 1 label change.** Should the label ship with exit 0 for one release (as proposed), or should label and exit move together? The first is gentler; the second is one fewer release of a known-wrong label.
5. **SARIF level.** What SARIF `level` and notification shape does `UNCONFIRMED` take? Using `note` would repeat the clean-looking mistake; `warning` is proposed.
6. **Blast radius of `require_complete`.** Measure how many current `partial` runs (across the catalog and the test corpus) would newly exit 1 under the alternative in Alternatives 3, so that the trade-off is recorded with numbers rather than by argument.
7. **Vision wording.** Confirm the reading in Consequences that "first-class, even stripped" concerns admission, not the clean claim.

## Verification of this ADR

What was run for this proposal: the reproduction above, on this checkout, with the commands and outputs quoted. The matrix rows marked "verified" were run; the rest are proposals for the implementation to measure. Not run: C++, clang, the GitHub Action, macOS and Windows, release fan-out, and the Rust default-build case. These are the implementation's test matrix, not claims made here.

No `**Verified:**` receipt is attached: that receipt is anchored to a commit on `main`, and this ADR is a proposal on a branch.
