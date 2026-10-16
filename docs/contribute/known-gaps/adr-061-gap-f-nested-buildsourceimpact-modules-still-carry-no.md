---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# ADR-061 gap F: nested `buildsource`/`impact` modules still carry no disposition

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The compat/* modules named in the entry are gone (compat front end removed). Several named nested modules still exist with no architecture/*.yaml mention, e.g. buildsource/build_evidence.py, build_output.py, evidence_policy.py, fact_set.py, merge_support.py, source_graph.py and impact/use_case_impact.py (0 hits).


[ADR-061](../adr/061-responsibility-package-architecture.md)'s gap F requires
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
- **Closed (2026-10, Lane C stage 3):** `abicheck/buildsource/graph_impact.py`
  (target: `compare`) was a self-dependency block: it imported three call-edge
  label constants from the `extract`-classified `call_graph.py`. Those labels
  are shared vocabulary, so they moved to `model/graph_vocabulary.py`
  (`call_graph.py` and every other reader now import them from there), and
  `graph_impact.py` — which then imports only `model` — is classified
  `compare`.
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
- **Closed (2026-10) by deletion, with the rest of `abicheck/compat/`:**
  `abicheck/compat/descriptor.py` (target: `extract` — ABICC XML
  descriptor parsing, the same shape as the already-`extract`-classified
  `compat/abicc_dump_import.py`) — blocked because `abicheck/compat/cli.py`
  (`frontends`) imports it directly (both the runtime `parse_descriptor`
  call and a `TYPE_CHECKING`-only `CompatDescriptor` reference).
- **Closed (2026-10) by classifying it `policy`:** `abicheck/impact/engine.py`
  (originally targeted at `workflows` — its `assess_change` builder is
  called from the consumer-scoping workflow) was blocked because
  `abicheck/post_processing_reachability.py` (`policy`) also imports it, and
  `policy` may not import `workflows`. It imports only `model`, and building
  a finding's impact assessment is a `policy` decision, so `policy` serves
  both callers (the Lane C `appcompat.py` split).
- **Closed (2026-10) by deletion:** `abicheck/compat/_helpers.py` (target: `frontends` — split directly out
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
- `abicheck/impact/assessment.py` — the identical shape gap B already
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
place (it exists purely for external/legacy call sites); `impact/assessment.py`
is the opposite — it has a real, named offending edge
(`checker_types.py -> impact.assessment -> policy.evidence_status`), the same
"no single ADR-061 layer admits both directions" conflict gap B's own
worked example describes, and is forced *toward* (never actually
classified as) `model` because that conflict has no caller-side or
self-dependency fix available today — not because there's no caller to
fix. It carries no `architecture/modules.yaml` entry at all, same as
every other entry in this file. Tractable per-entry, not as one slice —
`build_output.py`'s single `frontends` caller is independent of any other
entry's fix.
