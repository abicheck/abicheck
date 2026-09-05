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
- **The thirteenth round's own identity-first matching no longer trusts an
  `Anonymous`-scoped identity's ordinal across two snapshots.** An
  `Anonymous`/`LocalToFunction` scope segment's own ordinal is not stable
  across two snapshots by design (`model.identity_stability`'s own
  docstring: inserting an earlier anonymous sibling shifts every later
  one's ordinal, even though nothing about those later declarations
  changed), so raw `EntityId` equality could pair two genuinely unrelated
  declarations that happen to collide on a shifted ordinal plus the same
  bare name — fabricating a `CONSTANT_CHANGED`/`TYPEDEF_BASE_CHANGED` for
  what is really just an unrelated addition (Codex review, PR #1078,
  fourteenth round). Both `diff_constants` and `diff_typedefs` now gate
  their identity-matched set through
  `model.identity_stability.entity_id_is_cross_snapshot_stable` — that
  predicate's own docstring named this collision path as exactly the kind
  of real consumer it was written for but had no call site yet; this is
  the first one.

- **A genuine ODR-duplicate/multi-TU typedef or constant occurrence no
  longer loses its own value evidence to a "most facts present" reduction.**
  `compare.typedefs._aliases`/`_underlying` and `compare.constants.
  _values`/`_value` used to read through `SemanticIRIndex`'s *reduced*,
  one-entry-per-`EntityId` view (`entities_of_kind()`/`.fact()` ->
  `SemanticIR.canonical_entities()`) rather than `SemanticIR.occurrences`
  itself (Codex review, PR #1078, fifteenth round). That reduction's own
  docstring already warns it must never back a legacy-shape projection,
  since it silently collapses two occurrences sharing one `EntityId` —
  exactly the case `OccurrenceId`'s own disambiguator exists to keep
  distinct — down to a single winner picked by "most facts present, then
  `canonical_key` order". A real value change on whichever occurrence lost
  that reduction was therefore invisible to `diff_typedefs`/
  `diff_constants` even though `SemanticIR` itself never merged the two.
  All four functions, plus `compare.typedefs._bare_typedef_side_index`'s
  own bare-projection loop (which had the same bug, compounded by a type
  mismatch that made every bare-projected typedef read as permanently
  unresolved once `_underlying` started expecting an `OccurrenceId`), now
  iterate `SemanticIRIndex.ir.occurrences` directly and key by
  `OccurrenceId`, so two same-identity occurrences are just another
  instance of the alias-collision multiset comparison these detectors
  already handle correctly.

- **A colliding group's own occurrence-level removals/additions no longer
  collapse back down to one after post-processing.** `compare.typedefs.
  diff_typedefs`/`compare.constants.diff_constants` correctly emit one
  finding per contributing entity when a whole colliding group vanishes or
  newly appears (twelfth round), but `diff_filtering._dedup_exact` -- the
  first pass of the public `checker.compare()` pipeline's own
  post-processing -- keyed only on `(kind, description)` (Codex review, PR
  #1078, sixteenth round). Two entities in the same colliding group render
  identical description text by construction, so this pass silently
  collapsed two genuinely distinct, independently-provable findings back
  down to one before a caller ever saw them -- `tests/test_diff_layout.py`'s
  own `test_second_type_still_compared_when_first_shares_its_bare_name` had
  already named the identical risk for a bare-name-keyed `RecordType`
  detector as a known, then-unaddressed concern. `_dedup_exact`'s key now
  also includes `symbol`, `old_value`/`new_value` (via the existing
  `compare.dedup_key.hashable_value`, since those slots are not guaranteed
  to be hashable scalars), and `entity_id` (the compare-time `EntityId`'s
  own `.key`, when a producer set one) -- a producer that never sets
  `entity_id` degrades to exactly the previous key plus the two now-included
  value fields, so this is additive rather than a behavior change for any
  detector that predates entity identity.

- **A dedup key built from `entity_id` alone still collapsed two genuine
  ODR/multi-TU occurrences.** The sixteenth round's own `_dedup_exact` fix
  used `entity_id.key` to distinguish colliding findings, but two occurrences
  legitimately share one `EntityId` -- `OccurrenceId.disambiguator` is what
  tells them apart -- so removing both with the same value still collapsed
  them to one (Codex review, PR #1078, seventeenth round). `Change` gains a
  new `disambiguator` field, populated by `compare.typedefs`/
  `compare.constants` via the new `model.semantic_ir_legacy_adapter.
  producer_occurrence_disambiguator` (mirroring `producer_entity_id`'s own
  synthetic-vs-real gate), and `_dedup_exact`'s key now includes it too --
  `None` for a producer that predates occurrence-level identity, which
  degrades this key to exactly the sixteenth round's own.
- **A stored snapshot written between two normalizer slices for the same
  entity kind is no longer rejected as corrupt.** `_assert_sidecar_identity_
  consistent`'s reverse-direction check (a sidecar entry with no matching
  `SemanticIR` occurrence) used to fire unconditionally, but a v38-v41
  snapshot written after the identity-resolution slice for a kind (which
  always populates its sidecar) and before that kind's own *normalization*
  slice (which populates matching `SemanticIR` occurrences) legitimately has
  a populated sidecar with zero matching occurrences -- indistinguishable,
  from stored data alone, from a snapshot that genuinely has none of that
  kind (Codex review, PR #1078, seventeenth round). The check now only runs
  this direction when `SemanticIR` resolves *some* occurrence of the kind in
  question -- every occurrence of a kind is written from the same parse
  pass, so a producer that resolves even one has no such ambiguity left, and
  a specific missing name is then a real disagreement, not this gap.

- **A dedup key including `disambiguator` no longer collides two occurrence
  findings on their public `report_finding_id` (schema 2.3).** The
  seventeenth round's `Change.disambiguator` field fixed `_dedup_exact`'s
  own collision, but `report_finding_id` -- the stable per-finding
  fingerprint report consumers key waivers and cross-run correlation on --
  never looked at it either, so two now-surviving ODR-duplicate findings
  with otherwise-identical kind/symbol/values/description still collided on
  this id (Codex review, PR #1078, eighteenth round). `report_finding_id`
  now appends `disambiguator` **only when set**, so it hashes identically
  for every pre-existing finding (always `None`) -- unconditionally joining
  it would have rehashed every finding id this function has ever produced,
  the same universal-rehash cost a prior round explicitly declined to pay
  for a narrower risk (`docs/contribute/plans/public-contract-default.md`'s
  "delimiter" finding).
- **The bare-alias typedef projection no longer drops a real occurrence's
  own disambiguator.** `compare.typedefs._bare_typedef_side_index` wraps
  each real IR entity in a fresh, synthetic per-alias-collision `EntityId`
  to keep bare-alias collisions distinct, but discarded the *source*
  occurrence's own `OccurrenceId.disambiguator` in the process -- so two
  ODR-duplicate occurrences projected through this bare-key path (a side
  forced into bare-key mode by the *other* side predating schema v25)
  collapsed right back together in `_dedup_exact`, silently dropping one of
  two real removals (Codex review, PR #1078, eighteenth round). The
  projection now carries the source disambiguator forward onto the new
  `OccurrenceId`. Closing this also meant loosening
  `producer_occurrence_disambiguator`'s own gate: it used to return `None`
  whenever the occurrence's `entity_id` was synthetic (mirroring
  `producer_entity_id`'s "don't stamp a fabricated identity onto a durable
  reference" rule), but `disambiguator` is a purely internal dedup
  discriminator, never an external identity reference -- gating it the same
  way only threw away real evidence for no safety benefit.

- **The bare-alias typedef projection no longer collides two distinct
  entities with blank disambiguators.** The eighteenth round's own fix
  carried a bare-projected occurrence's *source* disambiguator forward, but
  two genuinely distinct qualified entities (`a::Alias`, `b::Alias`) with no
  real disambiguator at all -- the overwhelming common case -- still
  produced two `Change`s with `entity_id=None` (synthetic) and
  `disambiguator=None` alike, colliding right back together in
  `_dedup_exact` (Codex review, PR #1078, nineteenth round). Each
  bare-projected occurrence's own per-alias ordinal (already used to keep
  its synthetic `EntityId.scope` distinct) is now folded into its
  `disambiguator` unconditionally -- collision-safe on its own, and still
  carrying real ODR-duplicate evidence through (as `"<ordinal>:<source
  disambiguator>"`) when the source has one.
- **A pre-normalization historical snapshot's real constant removal is no
  longer silently missed.** The seventeenth round's own relaxation of the
  sidecar-consistency check lets a v38-v41 snapshot load when its
  `constant_entity_ids` sidecar is populated but `SemanticIR` carries zero
  constant occurrences at all (a real, legitimate window between the
  identity-resolution and constant-normalization slices) -- but
  `_constant_side_index` still trusted any non-`None` `semantic_ir`
  wholesale, so such a snapshot's real flat `constants` evidence was
  silently discarded and a genuine removal went undetected (Codex review,
  PR #1078, nineteenth round). Both `_constant_side_index` and
  `_typedef_side_index` (symmetric gap, fixed for consistency) now trust
  `semantic_ir` for a cohort only when it resolves at least one occurrence
  of that kind (the new `model.semantic_ir_legacy_adapter.
  semantic_ir_covers_kind`), falling back to the legacy adapter's
  projection of that side's own flat collection otherwise.
- **Two mixed-occurrence constant collisions and several stale docstrings
  fixed on CodeRabbit review.** `diff_constants`'s mixed-group pairing now
  prefers a removed/added pair whose values are both resolved when one
  exists, instead of always taking position 0 -- an unresolved-marker
  occurrence in the first slot used to demote a genuinely comparable pair to
  no recoverable value text and out of the fingerprint-reliability gate's
  own reach. Two regression tests (`test_a_stable_identitys_own_base_
  change_is_not_masked_by_a_new_arrival`/its constant sibling) used a named
  `Namespace` scope for their "stable" identity, which rendered under a
  different alias than the colliding "added" one entirely -- so neither
  test actually exercised the colliding-group path its own docstring
  claimed to cover; both now use an empty scope so the two collide as
  intended. `diff_symbols.py`, `model/semantic_ir_legacy_adapter.py`, and
  `scripts/semantic_ir_cutover.py` each had a stale "both sides" framing of
  the old (pre-per-side-independence) selector behavior, corrected.
- **Two entity-distinct occurrences with blank disambiguators no longer
  collide on the public, documented-stable `finding_id`.** Two anonymous-
  scope typedef/constant occurrences that both survive
  `diff_filtering._dedup_exact` via distinct `entity_id`s (each carrying an
  empty `disambiguator`, the common case) still hashed to the same
  `report_finding_id` -- that function never consulted `entity_id` at all
  (Codex review). First fixed by appending `entity_id.key` to
  `report_finding_id` conditionally, then **reverted** on a second Codex
  pass: unlike `disambiguator`, `entity_id` predates this PR entirely
  (function/variable/layout detectors already populate it), so that fix
  would have silently rehashed this documented-stable id for that whole
  pre-existing, already-shipped population -- not just the newly-collision-
  prone typedef/constant occurrences this round targeted. Fixed at the
  source instead: `compare.typedefs`/`compare.constants`'s new
  `_collision_safe_disambiguator` gives a genuinely distinguishable
  occurrence a real `disambiguator` whenever its producer supplied none,
  so `report_finding_id`'s existing (eighteenth-round) conditional
  `disambiguator` append closes the gap without touching any other kind's
  id.
- **A partial removal/addition within an equal-valued, entity-unstable
  colliding group no longer attributes an arbitrary occurrence's identity
  to the residual finding.** When only *some* (not all) of a value
  bucket's occurrences are excess, `diff_constants`/`diff_typedefs` picked
  an arbitrary list-prefix occurrence and stamped its real `entity_id` on
  the resulting `CONSTANT_REMOVED`/`CONSTANT_ADDED` -- presenting
  unrecoverable evidence as if it were observed attribution, and
  potentially crediting a still-*present* declaration's identity to a
  finding claiming it vanished. The new `_attribute_residuals` now
  attributes a real identity only when the *entire* value bucket vanishes
  from one side (every occurrence in it genuinely is gone); a partial
  residual now carries no `entity_id`/`disambiguator` at all (Codex
  review).
- **Two colliding, cross-snapshot-stable shared occurrences in one alias
  group no longer emit their findings in a `PYTHONHASHSEED`-dependent
  order.** `diff_constants`/`diff_typedefs` iterated their `shared_ids` set
  directly to emit each shared identity's own `CONSTANT_CHANGED`/
  `TYPEDEF_BASE_CHANGED`; a `set` has no defined iteration order, so two
  such findings from one comparison could print in either order across two
  runs of byte-identical input. Both now iterate in `old_ids`'s own
  (deterministic) encounter order instead, keeping the set only for O(1)
  membership checks (Codex review).
- **A mixed removed/added colliding group now pairs every substitution it
  can, not just the first.** `diff_constants`/`diff_typedefs` paired off
  exactly one removed occurrence with one added occurrence as a single
  `CONSTANT_CHANGED`/`TYPEDEF_BASE_CHANGED`, leaving every further pair to
  fall through as an independent `CONSTANT_REMOVED`/`CONSTANT_ADDED` --
  fabricating a removal-and-addition story for what equal cardinality on
  both sides makes an equally valid multi-pair substitution (e.g. anonymous
  `X=[1,2]` becoming `X=[3,4]`, previously one change plus a spurious
  removal and addition). Both now loop the pairing while occurrences remain
  on each side, the direct generalization of the tenth round's own
  one-pair fix (Codex review).
- **Fixed an unrelated, pre-existing broken doc link inherited via the
  merge from `main`.** `docs/contribute/plans/cli-cleanup-phase-two.md`
  linked `vision.md` with one directory level too many
  (`../../../vision.md`, pointing outside `docs/` entirely); every sibling
  page at the same depth correctly uses `../vision.md`. This broke the
  `mkdocs build --strict` CI gate on every commit of this PR (and on
  `main`) since the link was introduced.
- **Multiple ambiguous residuals within one colliding value bucket no
  longer collapse to a single finding.** When more than one occurrence
  becomes an ambiguous residual (e.g. four equal-valued, entity-unstable
  occurrences shrinking to one -- three independent removals, not one
  repeated three times), each carried `entity_id=None`/`disambiguator=None`
  alike, making them byte-identical and collapsing to one via
  `diff_filtering._dedup_exact`. Each ambiguous residual now gets its own
  synthetic, non-identity-claiming disambiguator (`"ambiguous:<i>"`) so
  multiplicity survives dedup, without attributing any real identity to it
  (Codex review).
- **`_dedup_exact`'s `entity_id`/`disambiguator` discriminators are now
  scoped to the typedef/constant kinds that actually need them.** Unlike
  `disambiguator` (a field this PR introduces), `entity_id` predates it and
  is already asymmetrically populated between evidence tiers for other
  kinds -- `diff_types.py`'s AST-based `FIELD_RENAMED` sets a real
  `entity_id`, but `diff_platform.py`'s DWARF-layout-based `FIELD_RENAMED`
  for the identical rename does not. Applying the discriminator
  unconditionally stopped that pair from collapsing across evidence tiers
  the way it always had, reintroducing a duplicate the pre-sixteenth-round
  key never produced. Scoped via a new `_OCCURRENCE_AWARE_KINDS` constant
  (Codex review).
- **An ordinary, non-colliding entity-backed constant/typedef finding no
  longer changes its public `finding_id`.** The twentieth round's
  `_collision_safe_disambiguator` fallback fired even for a single
  distinct entity whose whole bare-alias group vanished/appeared, or the
  lone shared identity in a group of one -- fabricating a nonempty
  `disambiguator` from its `entity_id` with no actual ambiguity to
  resolve, silently rehashing `report_finding_id` for the common case
  instead of just the genuinely collision-prone one. The new
  `_group_safe_disambiguator` only falls back when the group genuinely has
  more than one entity; an ordinary finding keeps hashing exactly as
  before (Codex review).
- **A genuine typedef/type/enum removal on a `SemanticIR`-only side no
  longer gets misclassified as an unconfirmed, stripped-binary removal.**
  `diff_types._has_type_evidence` only checked the legacy flat
  `types`/`enums`/`typedefs` collections, but `compare.typedefs`'
  per-side-independence design can legitimately trust a real `SemanticIR`
  for typedef comparison even when a snapshot's own flat `typedefs` map is
  empty. Such a snapshot has real type evidence this function must
  recognize -- otherwise `_removals_are_unconfirmed`'s stripped-binary
  heuristic silently suppressed a real removal purely because the legacy
  sidecar wasn't populated. `_has_type_evidence` now also recognizes
  `SemanticIR` type/enum/typedef occurrences (Codex review).
- **A lone bare-projected typedef no longer changes its public
  `finding_id`.** `_bare_typedef_side_index` (the bare-key-space fallback
  used when one comparison side predates schema v25) unconditionally
  stamped a synthetic `str(ordinal)` disambiguator -- always `"0"` for a
  bare alias with exactly one real entity -- even with no collision to
  disambiguate. Only stamps the ordinal-based discriminator now when the
  bare alias actually has more than one real entity, mirroring
  `_group_safe_disambiguator`'s group-size gate for this separate inline
  path (Codex review).
- **`finding_id`'s public schema description now documents the
  `disambiguator` hash input.** The seventeenth round's conditional
  append of `Change.disambiguator` (never itself a reported field) into
  `finding_id`'s hash was never reflected in the published JSON Schema's
  `finding_id` description, which still documented only the original
  six-field algorithm -- a consumer following the documented algorithm
  could not reproduce the id for a disambiguated typedef/constant
  occurrence finding. Updated the schema description and bumped
  `REPORT_SCHEMA_VERSION` to 2.50 (Codex review).
- **`scan --against`'s JSON also gains a version signal for the changed
  `finding_id` algorithm.** `cli_scan_baseline._finding_summary()`
  serializes the same `report_finding_id()` `compare`'s 2.50 bump covers,
  but `SCAN_SCHEMA_VERSION` wasn't bumped in lockstep -- a scan consumer
  had no way to feature-detect the widened hash. Bumped to 1.28 (Codex
  review).
- **The user guide's `finding_id` description now documents the seventh
  hash input too.** `docs/use/output-formats.md` still described
  `finding_id` as the hash of exactly the original six fields after the
  schema's own description was updated -- fixed to match (Codex review).
- **A DWARF-vs-real-IR typedef comparison no longer silently loses a
  genuine global-alias value change.** A DWARF-sourced snapshot's flat
  `typedefs` map is already qualified-keyed (`dwarf_snapshot.py` keys it
  by the full qualified name), unlike a header-AST backend's bare keying.
  `_bare_typedef_side_index` unconditionally rendered the *other* side's
  real IR down to bare leaf names, so a global alias and a same-leaf
  namespaced one collapsed onto one key there while the DWARF side kept
  them as two separate opaque keys -- a key-space mismatch that could
  silently drop a real base-type change entirely. `typedef_index_pair`
  now detects a DWARF-qualified-native side (`diff_helpers.
  typedef_flat_map_is_dwarf_qualified`) and renders the real-IR side fully
  qualified too, matching DWARF's own convention (Codex review).
- **`typedef_flat_map_is_dwarf_qualified` no longer misclassifies a
  header-parsed snapshot that also happens to carry DWARF metadata.** A
  header-parsed ELF binary can set both `dwarf` and a bare, header-derived
  `typedefs` together (`dumper.py`'s combined header+DWARF path) --
  `dwarf is not None` alone can't tell that apart from a genuinely
  DWARF-only side, and misclassifying it fabricated a removal for an
  entirely unchanged typedef. Now also checks `not snapshot.from_headers`
  (Codex review).
