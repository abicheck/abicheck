---
doc_type: contributor
level: expert
lifecycle: active
generated: false
---

# G43 — Wire the already-implemented TU-to-target attribution into `check-project.yml`/dump/compare

## Problem

PR #860 correctly rejects `evidence.projection: inferred`/shared evidence
packs today — `check-project.yml` explicitly errors when a target's
declared evidence is anything other than `projection: declared`, because
(per its own inline comment) "no dump/compare entry point yet filters an
inferred pack's TUs by `attribution_path`/target id before analysis." This
fail-closed behavior is correct and must not be relaxed without the real
fix reaching every consumer it needs to reach.

**Correction to an earlier draft of this plan, worth recording so it isn't
re-proposed**: this plan originally set out to *design and build* a new
TU→link-unit→DSO attribution chain from scratch. That work already
exists, confirmed by reading the code rather than assumed (Codex review on
the PR that first introduced this plan, fresh evidence):

- `abicheck/buildsource/link_attribution.py`'s `attribute_sources_to_targets()`
  computes exactly the attribution chain this plan describes — two
  independent, non-exclusive channels (a real target graph's own
  `source_files`, walked transitively through absorbed
  `OBJECT_LIBRARY`/`STATIC_LIBRARY` dependencies; a link-unit-graph
  fallback for build systems with no semantic target concept, e.g. Make) —
  and returns `{normalized_source_path: frozenset[target_identity]}`.
- `abicheck/buildsource/build_output.py`'s `_inferred_evidence_projection_issues()`
  already validates a declared `evidence.attribution_path` file: it must
  exist, be readable JSON, and actually tie at least one TU to the
  declaring target, or the target fails `project validate-build`.
- `abicheck/buildsource/inputs_pack.py`'s `ingest_inputs_pack()` already
  accepts `attribution`/`expected_target_id` parameters (ADR-053 D3) and
  filters a pack's translation units to exactly the ones the attribution
  mapping ties to the requested target — `_filter_tus_by_attribution()`
  keeps a TU whenever `expected_target_id in identities`, which already
  handles the multi-target case correctly (see "Multi-target attribution
  is valid evidence, not ambiguity" below).

**What ADR-053 itself identifies as still deferred — and what this plan is
now actually scoped to — is narrower: the CLI/workflow plumbing that calls
this already-built, already-validated mechanism from a real check.**
`check-project.yml`'s rejection comment says exactly this: the attribution
model and its validation exist; nothing in `check-project.yml`, nor any
real `dump`/`compare` CLI invocation, reads a target's `evidence.
attribution_path`, loads the attribution mapping, and threads
`(attribution, expected_target_id)` into `ingest_inputs_pack()` (or the
equivalent entry point a live `dump`/`compare` uses) before running the
actual analysis. That is a wiring gap, not a missing-model gap.

## Multi-target attribution is valid evidence, not ambiguity

An earlier draft of this plan treated a source attributed to more than one
target as an "ambiguous ownership" case that should not count toward any
target's completeness. **That is wrong, and the existing implementation
already gets it right**: `attribute_sources_to_targets()`'s own docstring
and the target-graph channel's own transitive-absorption logic mean a
source legitimately linked into several DSOs (a header-only utility object
compiled once and linked into multiple targets, or an `OBJECT_LIBRARY`
whose sources are absorbed into every dependent) is *correctly* attributed
to every one of those targets — and `_filter_tus_by_attribution()` already
keeps such a TU for each target's own filtered view, independently. This
is the whole point of returning a `frozenset` of identities per source
rather than a single owner: shared-but-real ownership is a normal,
expected shape, not a defect.

The only case that should ever be treated as incomplete/unresolved is a
source **absent from the attribution mapping entirely** — unknown to both
channels — which `_filter_tus_by_attribution()` already drops (fail-safe:
"a source absent from `attribution` entirely... is dropped, exactly like
one attributed to a different target only"). This plan's job is to make
that existing distinction visible in the declarative project's assurance
contract (see G41 Phase 3), not to invent a new, incorrect distinction
between "single-target" and "multi-target" attribution.

## Goal & acceptance criteria

Wire the existing attribution mechanism end to end so `check-project.yml`
can safely accept `evidence.projection: inferred` for a target whose
attribution is present and validated, instead of unconditionally rejecting
it:

1. `check-project.yml`'s evidence-routing step, for a target declaring
   `projection: inferred`, reads the validated `evidence.attribution_path`
   (already checked by `_inferred_evidence_projection_issues()` during
   `project validate-build`), loads the attribution mapping, and forwards
   it — plus the target's own **canonical attribution identity set, not a
   bare id or a single resolved identity** (see the correction below) —
   to whichever `dump`/`compare` entry point ultimately calls
   `ingest_inputs_pack()` for this check, instead of erroring out
   unconditionally.

   **"The target's own id" is wrong here, confirmed by a fresh review
   round reading `attribute_sources_to_targets()`/`_filter_tus_by_
   attribution()` directly, not assumed.** `attribute_sources_to_targets()`
   never records a bare target id as a TU's identity — it records
   `f"target://{target.id_suffix}"` when the target-graph channel supplies
   attribution, or `f"output://{basename}"` (the link-unit-graph channel's
   fallback for e.g. a Make build with no semantic target concept to
   attach an id to) when it doesn't. `_filter_tus_by_attribution()` then
   does exact set membership, `expected_target_id in identities` — so
   forwarding the plain project target id (e.g. `"core"` instead of
   `"target://core"`) would make every TU's identity set fail that
   membership check, silently dropping the *entire* pack's contribution to
   this target even though `project validate-build`'s own
   `_inferred_evidence_projection_issues()` accepted the identical mapping
   using exactly these canonical, prefixed identities.

   **A single resolved identity is still not enough, and a second review
   round found the real fix one layer deeper than the first correction
   reached.** `_inferred_evidence_projection_issues()` validates against
   `expected_identities = {f"target://{t.id}"}` **plus** the `output://`
   fallback **as a set of two acceptable spellings**, because the
   target-graph channel and the link-unit-graph fallback channel can each
   cover a *different subset* of one target's own TUs (e.g. some TUs
   discovered via a real target-graph adapter, others only reachable
   through the link-unit fallback for a mixed-build-system project) — the
   validator's own check is "does *any* TU's identity intersect this set,"
   never "pick the one identity that matches." Forwarding a single resolved
   string as `expected_target_id` (as the first correction above proposed)
   reproduces the original bug in a narrower form: whichever TUs are
   tagged with the *other* accepted spelling still fail
   `_filter_tus_by_attribution()`'s exact-membership test and are silently
   dropped — and, worse, the drop-reason accounting from this plan's own
   `_filter_tus_by_attribution()` return-tuple widening (above) could
   misclassify those dropped-but-legitimately-owned TUs as
   `dropped_other_target` rather than `dropped_unresolved`, since they
   *are* attributed, just under the identity spelling this call didn't
   pass. The fix: `_filter_tus_by_attribution()`/`ingest_inputs_pack()`
   must accept the **full accepted identity set** for this target —
   `expected_target_ids: frozenset[str]` (or an equivalent plural
   parameter), mirroring `_inferred_evidence_projection_issues()`'s own
   `expected_identities` set exactly — and test `identities &
   expected_target_ids` per TU, not `expected_target_id in identities`
   against one resolved string. `check-project.yml`'s evidence-routing
   step (and the CLI/typed-API surface below) therefore resolve and
   forward the *same set* `_inferred_evidence_projection_issues()` already
   computes (`{f"target://{t.id}"}`, plus the `output://` alternative when
   `t.binary` is set) rather than picking one member of it. This is a
   genuine, if narrow, signature widening on `ingest_inputs_pack()`'s
   existing Python-API parameter (`expected_target_id: str | None` becomes
   plural), not merely a resolution-logic change confined to the CLI/
   workflow wiring.
2. The real `dump`/`compare` CLI paths that consume a Flow-2
   `abicheck_inputs/` pack gain a way to receive an external
   `(attribution, expected_target_id)` pair — today `ingest_inputs_pack()`
   accepts these as a Python-API parameter pair, but no CLI flag or
   `check-project.yml`-driven invocation actually supplies them.
3. `check-project.yml`'s own rejection message and inline comment are
   updated once the wiring lands, so the workflow no longer says "no
   dump/compare entry point yet filters..." while one now does.

### Acceptance test

One shared, build-wide pack covers `core`, `math`, and `strings`, with a
real `attribution_path` manifest computed from
`attribute_sources_to_targets()` over the shared build's own evidence. A
source-only change in `strings` must not enter either the `core` or
`math` source snapshot (the existing `_filter_tus_by_attribution()`
already guarantees this once given the right `expected_target_id` — this
test is end-to-end through `check-project.yml`, not a new unit test on
already-tested filtering logic). A source genuinely shared by `core` and
`math` (an absorbed `OBJECT_LIBRARY`) must appear in *both* filtered
views, not be excluded from either. A TU present in the shared build but
genuinely absent from the attribution mapping (unresolvable by either
channel) must surface as a structured incomplete-coverage signal for the
requesting target, not as a silent `status: ok` drop — this is the new
`inputs_pack.py` behavior above, and it's what lets G41 Phase 3's
assurance gate actually catch the case. Removing/corrupting the
`attribution_path` file (or a target declaring `projection: inferred`
with no such path at all) must still produce `_inferred_evidence_
projection_issues()`'s existing validation failure — this plan must not
weaken that guard, only add the consumption path that makes a *valid*
attribution actually usable.

## Design

Most of the design is "thread an existing value through," not new logic —
with one confirmed exception, the unresolved-vs-other-target drop-reason
distinction in `inputs_pack.py` (see "Files & surfaces" below), which is
real new logic, narrow in scope:

A real `check-project.yml` run does not shell out to `dump`/`compare`
directly — it is a three-layer composite chain, confirmed by reading the
actual `uses:` steps: `check-project.yml` invokes
`./.check-project-src/actions/check-target` (the `actions/check-target/
action.yml`/`run.sh` composite), which in turn invokes the repository-root
`action.yml`/`run.sh` (the one that actually constructs the `dump`/
`compare` CLI command). Threading `attribution_path`/target id through
means adding a new input at **every** layer of that chain, not just the
outermost workflow — an earlier draft of this plan only described the
workflow-level output and a bare CLI flag, which would leave inferred
projection unusable end to end (the two intermediate composite Actions
would have no way to pass the value through). The design, corrected:

1. **`check-project.yml`**: replace the unconditional
   `projection == 'inferred'` rejection (see its own comment, quoted
   above) with: if `projection == 'inferred'`, require `attribution_path`
   to be set (already enforced by `project validate-build`, but re-check
   defensively at consumption time — a validated `build-output.json` at
   publish time doesn't guarantee the exact file used at check time is the
   same one), load it, and forward `(attribution_path resolved,
   canonical_attribution_identity_set)` — the **set** of `target://<id>`/
   `output://<basename>`-shaped identities described under "Goal &
   acceptance criteria" above, **not** a bare target id and **not** a
   single resolved identity string — as new outputs into its
   `actions/check-target` step (mirroring how `evidence-pack`/
   `evidence-producer` are already forwarded as step outputs today).
2. **`actions/check-target/action.yml`/`run.sh`**: gain new inputs
   (e.g. `attribution-path`/`attribution-target-ids`, the latter a
   comma-joined or JSON-encoded list, not a single id) accepting the
   values `check-project.yml` forwards, and pass them onward to the
   repository-root
   Action it invokes internally — the same forwarding shape its existing
   `candidate-build-output`/`evidence-pack-path` inputs already use (see
   `check-project.yml`'s own comment on those, quoted in this plan's
   Problem section).
3. **Repository-root `action.yml`/`run.sh`**: gain the matching inputs and
   have `run.sh`'s dispatch translate them into the new `dump`/`compare` CLI
   flag from step 4 below.
4. **CLI/typed-API surface — must be side-specific, not one global flag
   pair, confirmed by a fresh review round covering a case steps 1-3 don't
   reach.** Steps 1-3 above are single-sided by construction: a
   `check-project.yml` check invocation only ever dumps the *candidate*
   side directly (the baseline side is handled by the separate publication
   path this plan's own baseline-attribution wiring, above, already
   covers), so a single `attribution_path`/`target_id` pair reaching
   `check-target` is correct for that flow. But the underlying `dump`/
   `compare` CLI flag this step adds is a shared primitive, also reachable
   directly for a real two-sided `compare old new`/`scan --against`
   invocation where **both** `old=` and `new=` name pack-shaped build
   information — and TU-to-target attribution is release-specific (the
   set of TUs and which target owns them can genuinely differ between the
   old and new revisions), so one global attribution path/id cannot
   correctly scope both operands: applying it to only one side leaves the
   other side's evidence unfiltered by attribution at all, and applying
   one side's mapping to both risks incorrectly dropping or retaining TUs
   on whichever side it doesn't actually describe. The flag must therefore
   be **side-aware**, reusing this codebase's own established `old=`/`new=`
   scoping convention already used for `--sources`/`--build-info`/
   `--header`/`--ast-frontend` (`cli_scan.py`/`cli_compare_helpers.py`) —
   e.g. `--attribution-path old=PATH new=PATH` (and a matching
   `--attribution-target-ids old=ID[,ID...] new=ID[,ID...]`, plural per
   side — see the accepted-identity-set correction above: each side needs
   the *set* of accepted identities, not one id) — rather than a bare,
   unscoped flag pair. `ingest_inputs_pack(attribution=,
   expected_target_ids=)` (plural parameter) is then called once per side
   with that side's own resolved identity set.
   Steps 1-3's workflow/Action forwarding is unaffected in shape — a
   single-sided `check-project.yml` invocation supplies only the
   candidate-side (`new=`) half of this sided flag, never both — but the
   CLI/typed-API surface itself must accept both. This side-aware flag
   shape is the one genuinely new piece of *logic* this plan adds;
   everything above it is forwarding.
5. **Reject, don't silently widen, everything else**: a target declaring
   `projection: inferred` with no `attribution_path`, or one whose
   attribution file fails `_inferred_evidence_projection_issues()`'s
   existing checks, keeps failing exactly as it does today — this plan
   only adds a path for the *validated* case, it does not loosen the
   validation.

## Files & surfaces

- `.github/workflows/check-project.yml` — replace the unconditional
  `projection: inferred` rejection with the attribution-aware path above;
  update the inline comment and error message once wired.
- `actions/check-target/action.yml`/`run.sh` — new inputs forwarding
  `attribution_path`/target id to the repository-root Action it invokes.
- Repository-root `action.yml`/`run.sh` — new inputs, dispatched into the
  new CLI flag below (the same three-layer chain G45 documents needing to
  relax `new-library` through for its own, unrelated header-only-target
  gap — these are two separate wiring tasks through the identical Action
  chain, not the same fix).
- **Baseline publication — a wholly separate, previously-missing wiring
  gap on the *other* side of the comparison, confirmed by reading the real
  workflows/Action rather than assumed.** Everything above wires
  attribution into the *candidate* side (`check-project.yml` →
  `actions/check-target` → root Action). But when the *old* release was
  also built from the same shared/inferred build-wide pack, the *baseline*
  snapshot needs the identical attribution filtering, or the comparison is
  asymmetric: the candidate side is scoped to this target's own TUs while
  the stored baseline still carries every target's TUs from the shared
  pack, producing false source-level differences or a spurious
  comparability failure that has nothing to do with a real change.
  `publish-baseline.yml`/`update-main-baseline.yml` both pass one
  workflow-global `build-info` input straight through to `actions/
  baseline`, which forwards it as a single, unconditional `--build-info
  "$BUILD_INFO"` (`actions/baseline/run.sh`) to *every* library's `abicheck
  dump` call — there is no per-library attribution path or target-id input
  anywhere in this chain today. This plan must therefore also cover:
  - `actions/baseline/action.yml`/`run.sh` — a new per-library
    `attribution_path`/target-id input (or a per-entry field on the
    existing `libraries` JSON array `run.sh` already parses), forwarded
    into the same new CLI flag from step 4 above, mirroring the candidate
    side's wiring rather than inventing a second shape.
  - `abicheck/buildsource/baseline_publish.py`'s `derive_baseline_libraries()`
    — must surface (or accept) the per-library attribution path/target id
    so the two baseline-publishing workflows below can populate the new
    Action input per library entry, not just per profile.
  - `.github/workflows/publish-baseline.yml`/`update-main-baseline.yml` —
    thread the per-library attribution path through to the new
    `actions/baseline` input, the same way they already thread the
    profile-level `build-info` input.
  Without this half, wiring only the candidate chain leaves the baseline
  unscoped and can turn a real fix into a new false positive/negative on
  the very first inferred-projection target that publishes a baseline.
- Whichever CLI entry point(s) this chain ultimately shells out to for a
  build/source-depth check (see G41 Phase 2's per-target header/
  compile-context projection work, which touches the same call sites) —
  new, **side-aware** flag/config field for an attribution-manifest path
  + target id (`old=`/`new=` scoped, mirroring `--sources`/`--build-info`'s
  existing convention), not a single global pair — see the "Design"
  section above for why a two-sided live `compare`/`scan --against`
  invocation needs distinct old/new attribution.
- **`abicheck/buildsource/inputs_pack.py` — one real, confirmed gap
  remains here, correcting an earlier draft of this plan's "no new logic
  expected" claim.** `_filter_tus_by_attribution()`'s two drop reasons —
  "attributed to a *different* target" (expected, benign) and "absent
  from the attribution mapping entirely" (a genuine coverage gap: the
  attribution model couldn't determine ownership at all) — collapse into
  one undifferentiated `dropped` count, and `ingest_inputs_pack()`
  deliberately keeps that count out of `diagnostics` (a documented,
  correct choice for the *first* reason — see the function's own
  "Attribution-scoping... is the *intended* effect... not a lossy one"
  comment) so `ExtractorRecord.status` stays `"ok"` either way. Confirmed
  by reading both functions directly: there is currently no way to tell
  "every dropped TU was legitimately someone else's" from "some dropped
  TU was unresolvable, and a real source change could be silently absent
  from this target's analysis" — which is exactly the distinction G41
  Phase 3's assurance contract needs to gate on.

  **The propagation path, named end to end rather than left as "a
  structured incomplete-coverage signal" with no home** (the gap a Codex
  review of this plan's first draft correctly flagged — a local count
  split with no consumer wiring is not itself a closed gap): (1)
  `_filter_tus_by_attribution()` returns the two drop reasons separately
  — `(kept, dropped_other_target, dropped_unresolved)`, not one collapsed
  `dropped` count. (2) `IngestedInputs` (`inputs_pack.py`) gains a new
  field, e.g. `unresolved_attribution_tu_count: int = 0`, populated from
  the `dropped_unresolved` half only — the benign `dropped_other_target`
  count stays uncounted here, preserving the existing comment's reasoning
  that multi-target attribution is not lossy. This is a genuinely new
  field, not a repurposing of `diagnostics`/`status` (both stay exactly as
  they are today, so `ExtractorRecord.status` does not flip to `"partial"`
  merely from ordinary multi-target attribution). (3) `ingest_inputs_pack()`
  surfaces the new count on its own return value (mirroring how it already
  surfaces `tu_count`), and the caller that embeds an `IngestedInputs`
  pack into a `BuildSourcePack`/`AbiSnapshot.build_source` carries it
  forward on the pack itself.

  **The count must also survive a stored baseline's own serialize/reload
  round trip — confirmed to be a real, separate gap by reading
  `abicheck/buildsource/pack.py` directly, not assumed.**
  `BuildSourcePack.to_embedded_dict()`/`from_embedded_dict()` (the path a
  baseline snapshot's `.abi.json` actually goes through) enumerate exactly
  four persisted keys — `manifest`, `build_evidence`, `source_abi`,
  `source_graph` — and reconstruct the in-memory pack from only those; a
  plain in-memory `BuildSourcePack`/`IngestedInputs` attribute with no
  corresponding key in this list is silently dropped the moment a pack is
  embedded into a snapshot and later reloaded. Concretely: if only the
  *old* (previously-published baseline) side ever had an unmapped TU, a
  fresh `scan --against`/`compare` against that stored baseline would
  reload a pack whose unresolved-attribution count reads `0` regardless of
  what was true when the baseline was originally published —
  `compute_analysis_assurance()` then sees zero on that side and can
  report complete assurance for exactly the case this signal exists to
  catch. The field's real, persisted home is therefore
  `BuildSourceManifest` (`abicheck/buildsource/model.py`) — already
  serialized unconditionally via `manifest.to_dict()`/`from_dict()` inside
  both `to_embedded_dict()`/`from_embedded_dict()`, and already carrying
  its own `build_source_pack_version` to bump for this new field — not a
  bare dataclass attribute that happens to ride on the in-memory
  `BuildSourcePack`/`IngestedInputs` object only for the lifetime of one
  process. This is routed through `abicheck/model/`(shared value)/
  `abicheck/storage/`(the manifest schema/round-trip itself, per ADR-061's
  routing already established for G41/G39's own manifest work above), not
  grown as an ad hoc field on `pack.py` directly.

  **The consumer is `analysis_assurance.compute_analysis_assurance()`, not
  `contract_coverage_ledger.py` — corrected after a fresh review round
  confirmed the ledger is the wrong mechanism for G41 Phase 3's actual
  scope.** `coverage_failures_for_context()` (`contract_coverage_ledger.py`)
  is derived exclusively from a `PersistedContractContext` and returns `()`
  outright when that context is `None` — i.e. for every ordinary check that
  doesn't pass `--contract`. G41 Phase 3's `assurance:`/
  `require_complete_analysis` declaration, by contrast, is a general
  per-check floor with no `--contract` precondition at all (its own
  `require_target_resolution`/`require_all_selected_translation_units`
  fields apply to a plain `checks:` entry) — routing this signal through
  the contract-only ledger would mean a normal project check using
  `require_complete_analysis` (G41 Phase 3's own stated acceptance
  scenario) sees a clean, empty ledger and reports complete assurance even
  with a genuinely unmapped TU, exactly the false negative this whole
  signal exists to prevent. `analysis_assurance.compute_analysis_assurance()`
  is the actual general mechanism — computed unconditionally for every
  comparison (`checker.compare()` calls it regardless of `--contract`),
  already accepts `old_pack`/`new_pack: BuildSourcePack | None` explicitly,
  and already rolls up "existing pipeline signals... into an
  `AnalysisAssurance`" per its own docstring. (4)
  `compute_analysis_assurance()` reads the new
  `unresolved_attribution_tu_count` field off the `BuildSourcePack`(s) it's
  given and folds it into `AnalysisAssurance` as a new, named
  incompleteness reason (not silently absorbed into an existing status
  value), so a target with one genuinely unresolved TU reports incomplete
  assurance even though `ExtractorRecord.status` stayed `"ok"`. G41
  Phase 3's `assurance:` contract then reads `AnalysisAssurance` the same
  way it already reads every other assurance signal — no separate
  consumption path needed, since this *is* the mechanism Phase 3 already
  names as the engine to reuse. The `--contract`-scoped
  `contract_coverage_ledger.py` stays untouched by this plan; it remains
  the right, additional, unsuppressible signal for the narrower
  public/export contract-evaluation case, but it is not — and was
  incorrectly described here as — the general-purpose one. This four-step
  path — new return tuple, new `IngestedInputs`/`BuildSourcePack` field,
  `compute_analysis_assurance()` wiring, Phase 3 consumption — is the
  "real, if narrow, piece of new logic" the effort estimate below already
  budgets for; it was previously described only as "fold... into a
  structured incomplete-coverage signal" with no field name or consumer
  named, and then (incorrectly) routed to the wrong, contract-only
  consumer — both of which this correction fixes.
- `abicheck/buildsource/build_output.py` — no new logic expected;
  `_inferred_evidence_projection_issues()` already validates the
  `attribution_path` this plan's CLI wiring reads.

## Tests

- An `integration` end-to-end fixture matching the acceptance test above,
  run through the real `check-project.yml` workflow (or its equivalent
  local invocation), not only through `ingest_inputs_pack()`'s existing
  unit tests — the gap this plan closes is specifically that no real
  workflow invocation reaches that already-tested function with real
  attribution data.
- A regression test confirming a target declaring `projection: inferred`
  with no `attribution_path`, or a corrupted one, still fails exactly as
  it does today — this plan must not be the PR that accidentally weakens
  `_inferred_evidence_projection_issues()`'s existing guard.
- A stored-baseline round-trip regression test: publish a baseline from a
  pack carrying a genuinely unresolved-attribution TU, write it to an
  embedded `.abi.json` via `to_embedded_dict()`, reload it via
  `from_embedded_dict()`, and assert the unresolved-attribution count
  survives — confirming the persistence gap above is actually closed, not
  merely that the in-memory field exists for the lifetime of one process.

## Effort & risk

M (revised down from the original L estimate, since the attribution model
and its validation are both already implemented; confirmed to still hold
once the full three-layer Action chain — `check-project.yml` →
`actions/check-target` → repository-root Action — was accounted for, since
every added layer there is forwarding, not new logic; confirmed once more
against the one real exception, `inputs_pack.py`'s unresolved-vs-
other-target drop-reason distinction, which is new but narrow; and
confirmed once more still after adding the baseline-publication wiring
above, which doubles the Action-chain plumbing — candidate side and
baseline side — but stays the same *kind* of work, forwarding a path/id
through an already-established chain shape, not new logic) — the
remaining work is mostly CLI/workflow/Action plumbing connecting two
already-tested pieces (`attribute_sources_to_targets()`'s output, already
validated by `_inferred_evidence_projection_issues()`) to a third
(`ingest_inputs_pack()`'s existing `attribution`/`expected_target_id`
parameters — **which this plan also widens to accept a set of accepted
identities rather than one string, per the correction above; a small,
narrow signature change to an existing function, not new extraction
logic**), plus updating `check-project.yml`'s own rejection logic and
messaging, plus mirroring that same wiring through `actions/baseline` and
its two calling workflows, plus the one confirmed piece of new logic
above. Low design risk; the main risk is scope creep back into
re-deriving attribution logic that already exists — resist that, and keep
this plan to the wiring task ADR-053 itself identifies as deferred.

## Out of scope

- Redesigning `attribute_sources_to_targets()`'s two channels or their
  confidence/fallback rules — both already exist and are tested; this
  plan consumes them as-is.
- Any change to `_filter_tus_by_attribution()`'s multi-target semantics —
  already correct, as established above.
- Extending attribution to build systems neither existing channel covers
  (a target-graph-free, link-unit-free build) — out of scope for both the
  original ADR-053 work and this wiring plan.
