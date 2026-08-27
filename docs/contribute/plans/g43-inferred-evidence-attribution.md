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
   it — plus the target's own id — to whichever `dump`/`compare` entry
   point ultimately calls `ingest_inputs_pack()` for this check, instead of
   erroring out unconditionally.
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
   same one), load it, and forward `(attribution_path resolved, target_id)`
   as new outputs into its `actions/check-target` step (mirroring how
   `evidence-pack`/`evidence-producer` are already forwarded as step
   outputs today).
2. **`actions/check-target/action.yml`/`run.sh`**: gain new inputs
   (e.g. `attribution-path`/`attribution-target-id`) accepting the values
   `check-project.yml` forwards, and pass them onward to the repository-root
   Action it invokes internally — the same forwarding shape its existing
   `candidate-build-output`/`evidence-pack-path` inputs already use (see
   `check-project.yml`'s own comment on those, quoted in this plan's
   Problem section).
3. **Repository-root `action.yml`/`run.sh`**: gain the matching inputs and
   have `run.sh`'s dispatch translate them into the new `dump`/`compare` CLI
   flag from step 4 below.
4. **CLI/typed-API surface**: the actual `dump`/`compare` CLI gains a new
   flag (or config field) accepting an attribution-manifest path and a
   target id, which it loads and passes to `ingest_inputs_pack(attribution=,
   expected_target_id=)` — this is the one genuinely new piece of *logic*
   this plan adds; everything above it is forwarding.
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
- Whichever CLI entry point(s) this chain ultimately shells out to for a
  build/source-depth check (see G41 Phase 2's per-target header/
  compile-context projection work, which touches the same call sites) —
  new flag/config field for an attribution-manifest path + target id.
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
  Phase 3's assurance contract needs to gate on. Closing this needs
  `_filter_tus_by_attribution()` to return the two drop reasons
  separately (e.g. `(kept, dropped_other_target, dropped_unresolved)`),
  and `ingest_inputs_pack()` to fold only the *unresolved* count into a
  structured incomplete-coverage signal a consumer can act on (not
  `diagnostics`/`status`, which the existing comment's reasoning still
  correctly keeps clean for the benign case) — a real, if narrow, piece
  of new logic, not a pure wiring task.
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

## Effort & risk

M (revised down from the original L estimate, since the attribution model
and its validation are both already implemented; confirmed to still hold
once the full three-layer Action chain — `check-project.yml` →
`actions/check-target` → repository-root Action — was accounted for, since
every added layer there is forwarding, not new logic; and confirmed once
more against the one real exception, `inputs_pack.py`'s unresolved-vs-
other-target drop-reason distinction, which is new but narrow) — the
remaining work is mostly CLI/workflow/Action plumbing connecting two
already-tested pieces (`attribute_sources_to_targets()`'s output, already
validated by `_inferred_evidence_projection_issues()`) to a third
(`ingest_inputs_pack()`'s existing `attribution`/`expected_target_id`
parameters), plus updating `check-project.yml`'s own rejection logic and
messaging, plus the one confirmed piece of new logic above. Low design
risk; the main risk is scope creep back into
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
