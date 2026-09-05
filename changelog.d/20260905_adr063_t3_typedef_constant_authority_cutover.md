<!--
A new scriv changelog fragment.

Uncomment the section that is right (remove the HTML comment wrapper).
-->

### Changed

- **An anonymous-namespace typedef or constant no longer disappears from
  comparison.** `_aliases`/`_values` now key an entity by
  `model.semantic_ir_legacy_adapter.render_display_name_or_leaf` when its
  identity's scope contains an unrenderable segment (`Anonymous`/
  `LocalToFunction`), instead of skipping it outright (Codex review: the
  legacy adapter's own synthetic identity for the identical declaration
  always renders — an empty scope built purely from the flat map's own
  string key — so using the real `SemanticIR` directly was silently
  dropping an anonymous-namespace declaration the legacy path always
  surfaced). The fallback keeps every *named* ancestor and only omits the
  unrenderable segment itself (`N::<anonymous>::X` renders `"N::X"`,
  matching the header-AST parsers' own qualified-name convention for the
  flat legacy collections exactly) rather than collapsing straight to the
  bare leaf name — an earlier version of this fix did exactly that and a
  follow-up Codex round found it fabricated a removal/addition pair for an
  unchanged declaration whenever the anonymous segment had a named
  ancestor.
- **The typedef and constant detector cohorts' `SemanticIR` is now a real
  authority, not a fidelity-gated echo of the legacy projection** (ADR-063
  Track T3, "typedef/constant authority cutover"). `compare.typedefs.
  typedef_index_pair`/`compare.constants.constant_index_pair` previously
  built *both* an IR-backed and a legacy-projected index on every
  comparison and used the IR only when it exactly reproduced the legacy
  alias/value/identity map — so a real `SemanticIR` that disagreed with the
  legacy projection was never actually trusted, only silently routed
  around. Each side of a comparison is now decided independently: a side
  with a real `SemanticIR` reads it directly, with no second index built
  for that side and nothing left to adjudicate against; a side with none
  (DWARF-only, a producer without typedef/constant identity, a pre-v38
  reload) still reads through the legacy adapter's projection of its own
  flat collection, unaffected. Deciding per side rather than
  both-or-neither (Codex review) matters: the two shapes are matched by
  rendered alias name, not by `EntityId`, so mixing them is safe, and a
  both-or-neither rule would have discarded a side's real `SemanticIR` in
  favor of a legacy reconstruction of it whenever the *other* side lacked
  one — fabricating a removal if that reconstruction happened to disagree
  with (or lack) content the real IR still has. One consequence: a real
  `SemanticIR`'s own emission order, and its own resolved value for a
  typedef/constant, are now authoritative even where they would have
  disagreed with a stale legacy projection — that disagreement is the
  whole point of authority transfer, not a bug to route around.
- **A constant's addition/removal is no longer masked by an unsupported
  value.** `diff_constants` now emits `CONSTANT_ADDED`/`CONSTANT_REMOVED`
  for a membership change regardless of whether the constant's own value
  can be rendered — previously, a constant whose `canonical_spelling` is
  `Fact.unsupported()` (a clang compound-initializer fingerprint or
  bool-literal spelling) on the side where it actually exists caused the
  whole comparison to skip before the membership check ever ran, silently
  dropping a real addition or removal (Codex review; only reachable in
  practice once `SemanticIR` became the sole comparison-time source above).
  Only the value *comparison* (`CONSTANT_CHANGED`) still requires both
  sides' values to be comparable text.
- **The Track T3 consistency check now also runs on a loaded snapshot.**
  `serialization.snapshot_from_dict` constructs `AbiSnapshot` before
  decoding `semantic_ir` from the document and assigning it onto the
  already-constructed snapshot directly — bypassing `AbiSnapshot.
  __post_init__` entirely, so a stored v38+ snapshot whose sidecar
  disagreed with its own `SemanticIR` loaded without the new check ever
  running (Codex review). `snapshot_from_dict` now re-runs it explicitly
  right after decoding.
- **A per-side-independent typedef comparison no longer mixes bare and
  qualified key spaces.** When one side does not trust qualified typedef
  naming (a genuinely pre-v25 baseline, which also predates `SemanticIR`
  entirely), `typedef_index_pair` now renders *both* sides through the
  legacy adapter over the comparison's own bare-keyed maps, even when the
  other side carries a real `SemanticIR` — which always renders under its
  own fully qualified name (Codex review: using it directly there would key
  that side as e.g. `"ns::Alias"` against the bare-mode side's `"Alias"`
  for the identical declaration, fabricating a removal out of a naming
  granularity mismatch). Per-side independence still applies whenever both
  sides trust qualified naming, which is the common case.
- **A constant value hidden behind an unsupported fact is no longer
  compared as unchanged.** `diff_constants` now falls back to each
  snapshot's own flat `AbiSnapshot.constants` raw text — the same text the
  legacy declaration parser always populated, independently of
  `SemanticIR`'s cross-backend-safety decision — when the canonical
  `SemanticIR` value is `Fact.unsupported()`, gated through the existing
  `is_fingerprint_comparison_unreliable` predicate like any other
  fingerprint comparison (Codex review: without this, a real edit to a
  clang compound initializer or a `constexpr bool` aliased to a
  `True`/`False`-named identifier between two same-backend snapshots
  produced no finding at all).
- **A `SemanticIR` disagreeing, by identity, with its own legacy sidecar is
  now a hard, loud failure instead of a silently-absorbed fallback.** The
  one piece of the old fidelity gate still worth checking once the IR is
  the sole comparison-time source — whether a real `SemanticIR`'s resolved
  `EntityId` for a rendered typedef/constant name agrees with the same
  snapshot's own `typedef_entity_ids`/`constant_entity_ids` sidecar, both
  written by the same producer pass — moved to `AbiSnapshot.__post_init__`
  (`model.semantic_ir_legacy_adapter.assert_typedef_ir_consistent`/
  `assert_constant_ir_consistent`), which now raises the new
  `errors.SemanticIrAuthorityError` on a genuine disagreement. This runs
  once per snapshot construction rather than once per comparison, and a
  snapshot carrying a `SemanticIR` with no populated legacy sidecar at all
  (the common, forward-looking shape) is unaffected — the check only fires
  when both representations are actually present and disagree.
- **A colliding alias/name no longer silently drops all but one occurrence's
  value.** `compare.typedefs._aliases`/`compare.constants._values` used to
  key each rendered alias by a single `setdefault`-won `EntityId`, discarding
  every other entity that happened to render to the identical alias (two
  anonymous-scoped typedefs or constants sharing a leaf name, per
  `render_display_name_or_leaf`'s own accepted collision risk) — so a real
  value change on whichever occurrence lost the race was silently compared
  as "unchanged" whenever the *other* occurrence's value happened to match
  across sides (Codex review, PR #1078, sixth round). Both now group every
  colliding entity per alias and compare the sorted value multiset, only
  falling back to a symmetric-difference representative pair when reporting
  a real difference — the same failure mode a flat legacy map's own key
  collision already accepted (one occurrence wins), just without the
  additional risk of silently declaring "unchanged".
- **A typedef comparison forced into bare-key mode no longer discards a
  side's real `SemanticIR` just because its bare `typedefs` map happens to
  be empty.** `typedef_index_pair`'s bare-key-space branch (one side
  genuinely pre-v25, forcing both sides through the legacy adapter over
  their own bare-keyed maps) used to trust the caller-supplied bare map
  alone — every current real header-AST producer populates
  `typedefs`/`typedefs_qualified`/`semantic_ir` together from one shared
  element pass, so this specific split does not occur from a real dump
  today, but a hand-built or future-producer snapshot carrying real typedef
  `SemanticIR` occurrences with an incomplete bare map is exactly the case
  this module's own per-side-independence design already declines to treat
  as impossible (Codex review, PR #1078, seventh round). The new
  `_bare_typedef_side_index` projects a side's own real `SemanticIR` down
  onto bare aliases when it has one, instead of trusting the bare map
  parameter on its own; constants have no bare/qualified split (no
  schema-versioned bare-only baseline predates them), so this fix is
  typedef-only.
- **A constant membership change inside a colliding group is no longer
  masked by filtering unsupported values before comparing.**
  `compare.constants.diff_constants`'s multiset comparison filtered out
  unsupported (`None`) values *before* comparing, so an unsupported-valued
  occurrence appearing or disappearing under a colliding name (alongside a
  comparable one whose own value didn't change) produced an identical
  filtered value list on both sides and was silently read as "unchanged"
  (Codex review, PR #1078, eighth round). `diff_constants` now also
  compares each side's raw occurrence count independently of value
  comparability, reporting `CONSTANT_CHANGED` (with no recoverable value
  text) whenever the counts disagree even though the filtered values agree.
- **The Track T3 sidecar-identity consistency check now also covers an
  anonymous-scoped typedef/constant.** `_assert_sidecar_identity_consistent`
  used to key its lookup by the strict `render_display_name`, which
  refuses to render at all for an `Anonymous`/`LocalToFunction` scope
  segment — skipping such an entity entirely, even though the same
  producer's own legacy sidecar keys it by the *flattened* name (the same
  convention `render_display_name_or_leaf` matches), so a genuine identity
  disagreement for an anonymous-scoped declaration passed construction
  silently (Codex review, PR #1078, eighth round). The check now keys by
  `render_display_name_or_leaf` instead, grouping entities that collide on
  one flattened name and flagging a disagreement only when *none* of the
  colliding entities match the sidecar's recorded id — since the sidecar
  itself, built by a plain `dict` comprehension over the same colliding
  key, can only ever reflect one of them.
- **A colliding constant group's own addition/removal is no longer
  misclassified as a value change, and a shared per-name legacy fallback
  can no longer be misattributed across occurrences within one.**
  `compare.constants.diff_constants`'s collision path is now built around
  `collections.Counter` multiset subtraction instead of sorted-list
  equality (Codex review, PR #1078, ninth round). A colliding group that
  grew or shrank by a value already present elsewhere in the group (e.g. a
  second anonymous-namespace `X=1` alongside an existing `X=1`) used to
  read as a value *change* under sorted-list comparison, reporting
  `CONSTANT_CHANGED` (an API break) for what is a purely compatible
  addition — `Counter` subtraction now classifies a pure net-addition or
  net-removal as `CONSTANT_ADDED`/`CONSTANT_REMOVED`, reserving
  `CONSTANT_CHANGED` for a group with both a net addition and a net
  removal. Separately, the per-name legacy fallback text
  (`AbiSnapshot.constants.get(name)`) reflects only *one* raw value per bare
  name — whichever occurrence's own parse happened to win that same
  collision upstream — so applying it to every unresolved occurrence in a
  colliding group risked masking a real difference (both sides coincide on
  borrowed text) or fabricating one (the borrowed text differs between
  sides for reasons unrelated to either occurrence's own value); the
  collision path no longer consults it at all, representing an unresolved
  occurrence with an internal sentinel instead.
- **A typedef collision's own addition/removal is no longer misclassified
  as a base-type change, and a mixed constant collision no longer drops an
  independently provable addition or removal.** `compare.typedefs.
  diff_typedefs` now uses the identical `collections.Counter`
  multiset-subtraction approach `diff_constants` adopted in the previous
  fragment entry (Codex review, PR #1078, tenth round): a colliding group
  that grows or shrinks by a value already present elsewhere in the group
  (e.g. a second anonymous-namespace `Alias=int` alongside an existing
  `Alias=int`) is now correctly read as a pure, untracked-and-compatible
  addition (typedef additions carry no `ChangeKind` at all) or a
  `TYPEDEF_REMOVED`, never a `TYPEDEF_BASE_CHANGED` with an identical
  `old_value`/`new_value`. Separately, `diff_constants`'s own mixed-group
  handling (both a net removal and a net addition present at once, e.g. a
  stable `X=1` becoming `X=2` while a different, newly-added
  anonymous-scope `X=3` also appears) used to pick one representative pair
  from each side and emit a single `CONSTANT_CHANGED`, silently dropping
  the independently provable `CONSTANT_ADDED`/`CONSTANT_REMOVED` for
  whatever didn't get picked — it now pairs off exactly one removed value
  with one added value as that one `CONSTANT_CHANGED` story, then reports
  every remaining distinct value in either set as its own
  `CONSTANT_ADDED`/`CONSTANT_REMOVED`.
- **The Track T3 sidecar-identity consistency check is now bidirectional.**
  `_assert_sidecar_identity_consistent` only ever checked that a
  `SemanticIR` occurrence's rendered name, when present in the sidecar,
  agreed with the sidecar's recorded id — a sidecar entry naming a
  declaration `SemanticIR` has *no* occurrence for at all passed
  construction silently (Codex review, PR #1078, tenth round). Since
  `SemanticIR` is the sole comparison-time source for this cohort, such a
  declaration would never actually reach a comparison, potentially masking
  a real removal against another snapshot that also lacks it. The check
  now also iterates the sidecar's own keys and raises
  `SemanticIrAuthorityError` for any name with no corresponding
  `SemanticIR` occurrence, closing the reverse direction the original
  one-way check left open.
- **A colliding group's `CONSTANT_CHANGED`/`TYPEDEF_BASE_CHANGED` pairing is
  now deterministic, preserves exact multiplicity, and attributes each
  finding to the entity that actually produced it.** The previous rounds'
  own fix converted a `Counter` multiset difference to a `set` for
  iteration, which introduced three further defects (Codex review, PR
  #1078, eleventh round): which colliding value paired into the `CHANGED`
  finding — and, through it, the outcome of the
  `is_fingerprint_comparison_unreliable` gate applied to that pairing —
  depended on `PYTHONHASHSEED`, so the identical comparison could
  alternate between passing and failing across runs; converting to a `set`
  collapsed repeated identical values to one entry, silently dropping
  every additional identical removal/addition beyond the first (three
  colliding `X=1` occurrences shrinking to one reported only one removal,
  not two); and every emitted finding for a name shared a single
  `entity_id` computed once from `old_ids[0]`/`new_ids[0]`, misattributing
  a residual finding to whichever entity happened to occupy that position
  rather than the occurrence that actually changed. `compare.constants.
  diff_constants` and `compare.typedefs.diff_typedefs` both now group each
  side's own entities by value in a plain, insertion-ordered `dict`
  (`old_by_value`/`new_by_value`) instead of a `Counter`/`set`, so pairing
  order is deterministic and reproducible, multiplicity is exact, and
  every finding carries the specific occurrence's own entity_id.
- **A whole colliding group disappearing (or newly appearing) now reports
  every contributing entity, not just the first.** Both `diff_constants`'s
  whole-name `CONSTANT_REMOVED`/`CONSTANT_ADDED` paths and `diff_typedefs`'s
  whole-alias `TYPEDEF_REMOVED`/`TYPEDEF_VERSION_SENTINEL` path used to
  stamp only `old_ids[0]`/`new_ids[0]` even when the vanishing (or
  brand-new) group carried more than one distinct colliding entity (Codex
  review, PR #1078, twelfth round) — removing two anonymous-scoped `X`
  declarations at once reported only one removal. Each now emits one
  finding per contributing entity, with that entity's own entity_id.
- **A typedef's bare-mode projection no longer collapses two distinct
  qualified entities sharing a bare leaf name onto one last-wins string.**
  `_bare_typedef_side_index` used to project a side's real `SemanticIR`
  straight into a `dict[str, str]` and hand it to `legacy_typedef_ir`,
  which can only construct one occurrence per key — so two qualified
  entities that flatten to the same bare alias (e.g. an unchanged
  `a::Alias` and a newly-added `b::Alias`) silently overwrote each other,
  fabricating a `TYPEDEF_BASE_CHANGED` for a declaration that never
  actually changed whenever the discarded entity's value differed from the
  survivor's (Codex review, PR #1078, twelfth round). Each real IR entity
  now gets its own synthetic, collision-safe bare identity (a fresh
  `Anonymous` scope wrapper that renders to nothing, keeping the identity
  distinct while still rendering under the shared bare alias) instead of
  collapsing into a shared dict key, letting the existing occurrence-level
  collision handling in `diff_typedefs` compare them correctly.
- **A stable entity's own value change inside a colliding group is no
  longer masked by value-only matching, and a whole vanishing/appearing
  group's per-name legacy fallback is no longer shared across its
  members.** Both `diff_constants` and `diff_typedefs` now match colliding
  occurrences by shared `EntityId` *before* falling back to value-based
  pairing (Codex review, PR #1078, thirteenth round): an entity present
  under the identical `EntityId` on both sides is the same declaration, so
  its own old/new value comparison is exact, never a heuristic pairing.
  Previously, a stable entity changing value (e.g. `X=1` -> `X=2`) while a
  *different*, newly-added colliding entity happened to carry the old
  value (`X=1`) let value-only multiset subtraction cancel the stable
  entity's old value against the new entity's, reporting only a
  compatible-looking addition and silently losing both the real breaking
  change and the genuine addition. Separately, `diff_constants`'s
  whole-group vanish/appear loops (from the twelfth round's own per-entity
  fix) still called the per-name legacy fallback for every member of a
  multi-entity group, crediting the same borrowed text to genuinely
  different declarations — the fallback is now used only for a
  single-entity group, matching the ninth round's identical rule for the
  general collision path.
