---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A directory `compare` still feeds the union `-H` set to every member (no per-member header operand); unreached type findings still count in member verdicts

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** Step 1 (dedupe), the public_not_exported release-level half and the step-3 attribution report (workflows/release_member_attribution.py, policy/member_type_attribution.py) have landed. Still open: step 2 (a per-member header operand, plus the quadratic union-header walk) and keeping proven_unreachable findings out of member verdicts.


**Update (2026-09-17), read this first.** The release product model landed:
a directory/package comparison now judges **one** public contract backed by
**several** binary providers (`docs/learn/products-not-libraries.md` § "One
public surface, many providers"). Concretely, against the three steps this
entry proposes below:

- **Step 1 ("de-duplicate first") is done.** A finding several members report
  identically is rendered once, release-scoped, naming every affected library
  (`report.release_public_surface.dedupe_shared_member_findings`), and each
  member entry records how many of its findings were folded there
  (`product_level_findings`). Counts, verdicts and exit codes are untouched by
  the fold, so nothing is hidden or downgraded — only the duplicated evidence
  stops being emitted N times.
- **The `public_not_exported` half is not merely de-duplicated, it is
  answered correctly.** It was never a duplication problem at heart: the
  obligation is satisfied by *any* member, so the per-member answer was a
  Cartesian product. It now runs once, at release level, against the union of
  the bundle's usable exports (`policy.release_contract_reconciliation`), with
  the member pass no longer running it at all
  (`workflows.crosscheck_ownership`). Measured on a 12-library / 480-declaration
  fixture: 5,280 findings -> 0, report 8.7 MiB -> 77 KiB.
- **`exported_not_public` stayed per member, deliberately.** The example below
  reads it as the same defect, and it is not: the exporting member *is* its
  correct attribution. It is judged against the one shared declaration index
  and folded only when several members really do export the same symbol.
- **Steps 2 and 3 remain open exactly as written below** (a per-member header
  operand; reachability-based attribution where no map is declared). The type
  findings in the example above are still not *attributed* to the member whose
  surface reaches `Widget` — they are now reported once for the release,
  naming both members, which is the honest "which member this affects was not
  established" reading step 1 asks for, not the attribution step 3 would give.
  Over-reporting attribution, not duplication, is what is left.

**Performance face of the same gap (2026-09).** Every member walks the
union header set, so the release cost grows super-linearly with its member
count. See "Multi-library L2 compare scales quadratically with library count"
above for the measurements, what has been memoized, and why steps 2/3 in this entry
are the complete fix.

**Update (2026-09-18).** The model is no longer scoped to the live
directory/package fan-out. The other three drivers that compare several
libraries at once — a stored `BundleFacts` baseline against a live release
(`workflows.release_public_surface.stored_old_live_new_reconciliation`), two
stored documents (`workflows.bundle_stored_pair_compare`), and a
multi-library ABICC descriptor (`compat.multi_library_run`, removed with the
`compat` front end in 2026-10) — each reconcile
one product contract through the same
`workflows.release_public_surface.reconcile_member_sets` and enter the same
`member_pass_scope` ownership for their member pass. What remains scoped to
the live path: only that path acquires a surface (the other three read a
recorded or projected one), so the acquisition ledger's instrumentation is
empty on a stored comparison, and only that path performs the
`shared_findings` fold — the stored documents already emit one product-level
finding rather than repeating it per member. The driver inventory is not
mechanically enforced: nothing fails when a fifth multi-member driver is
added, so `tests/test_release_public_surface_drivers.py` is a hand-maintained
list (recorded in `tests/regressions/manifest_classification.py`'s
`release.cartesian_product_contract` known gaps).

**Known dormant branch (2026-09-18).** `storage/bundle_facts_archive.py`'s
public-surface blob carries the same second-materialization byte charge
`manifest_blob` does (a blob served from cache still builds a second object
graph, so its bytes are charged again -- the object-count amplification the
aggregate budget bounds). Through the public loader that branch is
currently **unreachable**: every other slot that could share the surface's
hash decodes it under a different shape first (the instantiation manifest
demands a top-level `provides:` list, a library slot demands a snapshot),
so a shared hash is refused before the surface slot runs. The guard is kept
because it is cheap and becomes load-bearing the moment slot ordering or a
shape check changes;
`tests/test_release_public_surface_persistence.py::TestTheSurfaceBlobIsReadExactlyOnce`
pins the property that makes it dormant, so that change cannot happen
silently. It is also why that file's own patch coverage will not reach
100% -- the uncovered lines are this guard, and covering them would mean
reaching past the public entry point, which this repo's own
third-party-boundary rule refuses.

**Update (2026-10-01): step 3 landed as attribution, not yet as a
verdict change.** `workflows/release_member_attribution.py` computes each
member's export surfaces and `policy/member_type_attribution.py` answers,
per type finding, `reaches`/`proven_unreachable`/`unestablished`; the
release fold attaches that partition to each product-level finding
(`shared_findings[].attribution`, release schema 1.11). On the example
below, `Widget` is now `reaches: libfoo.so`, `unestablished: libbar.so` —
libbar's only export has no declaration, so its reach cannot be decided,
and saying so is the honest answer. What remains open:

- **Member verdicts and counts still include unreached findings.**
  `libbar.so` above is still `BREAKING` on `Widget`. Excluding a
  `proven_unreachable` finding from a member's own verdict is the policy
  half; it was deliberately not bundled with the report half, because it
  changes exit codes and `--contract exports` already offers that scoping
  explicitly per member.
- **Step 2 (a per-member header operand) is still open**, and with it the
  quadratic cost: every member still parses the union header set.
- Making this work exposed an `export_surface` defect that was its own
  bug: the C tag idiom `typedef struct X {...} X;` was flagged as an
  ambiguous name, so every such type was undecidable under
  `--contract exports` too. Fixed in the same change.

The original entry follows, unchanged.


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
