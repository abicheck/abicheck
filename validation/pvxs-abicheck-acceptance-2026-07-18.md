# PVXS acceptance spike (2026-07-18): shadow-check false positives

Follow-up to `pvxs-abi-validation-2026-07.md` (which covers F1–F7 from an
earlier validation pass). This spike evaluated whether `abicheck` could run
as an independent shadow check alongside pvxs's existing
`abi-dumper` + `abi-compliance-checker` (ACC) gate, without replacing it, and
found three new false positives on a real historical diff
(`1.5.2` → `cc7bc72`) plus one confirmed positive (a private-layout break ACC
misses). Two of the three false positives are fixed in this change; the
third is a known, documented gap.

The three findings below were root-caused against the reported symptoms and
fixed. FP-2 was additionally re-verified end-to-end against a real, tool-
compiled reproduction (`castxml`/`clang` installed, real pvxs source cloned
from `github.com/epics-base/pvxs` — network access and `castxml` turned out
to be available in this environment after all) using the *actual* `testCase`
class body copied verbatim from pvxs's `src/pvxs/unittest.h` at
`cc7bc72dd7676c72871889c8586014947567ed1d` (the exact revision named in the
original spike) — see "Real-tool re-verification" below. FP-1 is verified at
the unit level with a fixture that reproduces the exact reported mechanism
(two distinct namespace-qualified types sharing the bare leaf name `_Impl`);
a full real-libstdc++-header end-to-end repro hit unrelated host-toolchain
friction (this host's clang can't parse glibc/libstdc++ 13 headers without
additional GNU-dialect flags `abicheck`'s clang frontend doesn't currently
pass — a distinct, pre-existing environment-compatibility gap, not a defect
in this fix; see "What wasn't re-verified end-to-end" below). The full
historical `1.5.2` → `cc7bc72` diff (both DSOs, built against EPICS Base 7.0)
was not reproduced: pvxs's `.ci/cue.py prepare` is written for the GitHub
Actions build matrix and exits immediately (`SETUP_PATH is empty`) without
that matrix's environment variables — building EPICS Base 7.0 by hand was out
of scope for this pass. That remains the acceptance step still owed before
this can move off "local/experimental shadow" status for pvxs specifically
(see "Status" below).

## FP-1 (fixed): unrelated types sharing a short/leaf name get cross-matched

**Symptom.** Comparing `libpvxs.so.1.5` 1.5.2 → cc7bc72, `abicheck` reported
`type_field_removed`/`type_field_added`/`type_base_changed` for a type named
`_Impl`, all pointing at `/usr/include/c++/13/bits/shared_ptr_base.h:587`.
The removed fields (`_M_facets`, `_M_caches`, `_M_names`) and the added field
(`_M_storage`) belong to *different*, unrelated `_Impl` template internals —
not the same declaration before and after a real change.

**Root cause.** `RecordType.name` is deliberately kept as the bare/leaf
spelling (`model.py`) so DWARF-only and header-mode snapshots keep matching
by the same key; the real namespace-qualified spelling lives in the separate
`RecordType.qualified_name` field, populated by both header dumpers
(`dumper_castxml.py`, `dumper_clang.py`) but, until this change, never used
as the actual old/new matching key. `diff_types.py` built its old/new
type-matching dicts keyed by the bare `t.name` in every type-level detector,
so two distinct `std::*::_Impl` instantiations that happen to share the leaf
name `_Impl` collided in the same dict slot.

**Fix.** Every old/new type-matching map in `diff_types.py` is now built
through `diff_helpers.build_type_map()`, keyed by `diff_helpers.type_map_key(t)`
(`t.qualified_name or t.name`). Header-mode snapshots get the real qualified
identity; DWARF-only snapshots are unaffected (their `RecordType.name` is
already the qualified spelling built by walking the DIE namespace chain —
see `dwarf_snapshot.py`). Global-scope types (`qualified_name is None`) fall
back to the bare name exactly as before, so the change is a no-op for the
common case and only removes the same-leaf-name collision.

Three follow-up defects were caught by automated PR review (`chatgpt-codex-
connector`) across successive versions of this fix and closed in the same PR
before merge:

1. **Schema-evolution regression.** A snapshot pair where only one side
   populates `qualified_name` (an older serialized snapshot predating the
   field, or a producer that never sets it) would key the two sides
   differently (`Foo` vs. `ns::Foo`) and manufacture a phantom
   `TYPE_REMOVED` + `TYPE_ADDED` for an unchanged type. Fixed by
   `diff_helpers.TypeMap`, a `Mapping` wrapper that keeps a collision-safe
   bare-name alias for `get`/`in` lookups (only added when the bare name is
   unambiguous within that snapshot, so it can't reopen the FP-1 collision).
   The alias is deliberately excluded from `items`/`values`/iteration, so a
   detector's `for name, t in old_map.items()` loop still visits each type
   exactly once — an initial version that leaked the alias into iteration
   double-processed (and double-reported) every namespaced type, caught by
   `tests/test_header_graph_examples.py`'s real castxml/clang-backed
   integration lane before merge.
2. **Downstream identity leak.** The qualified key is for old/new *matching*
   only — `Change.symbol`, `dumper_hybrid`'s per-fact provenance dict
   (`type_fact_key`/`field_fact_key`, keyed by the bare name), and
   `diff_filtering._dedup_cross_kind`'s DWARF↔AST redundancy correlation
   (which matches a DWARF field symbol's *bare* parent type name against the
   AST-level type symbol, "FIX-F") all expect the bare declaration name. An
   initial version let the qualified key leak into these paths, which
   silently defeated the DWARF↔AST dedup for any namespaced type — caught by
   the same real integration lane (case187/191 in
   `tests/test_header_graph_examples.py`, which assert a specific DWARF-
   sourced `ChangeKind` survives on Linux) failing in CI on the pushed PR.
   Fixed by re-deriving the bare name (`t_old.name`) at every emission/
   provenance-lookup site instead of reusing the qualified matching key.
3. **One-directional schema-evolution fix.** F#1's `TypeMap` alias only maps
   bare -> qualified (built from the *target* side's own contents), so it
   only resolved the "legacy old / fresh new" direction: iterating a legacy
   old snapshot's bare keys against a fresh new snapshot's alias worked, but
   the reverse (fresh old, keyed `ns::Handle`, vs. legacy new, keyed only
   `Handle`) looked the new side up by the qualified key alone, found
   nothing, and manufactured the same phantom `TYPE_REMOVED` + `TYPE_ADDED`
   pair. Fixed by `diff_helpers.lookup_matched_type()`, used at all 9 old/new
   lookup call sites: try the qualified key first, then retry with the bare
   declaration name. That retry is itself only safe when the *probing* type's
   own bare name is unambiguous within its own snapshot — reviewed a second
   time by the same bot, which found that an unconditional retry reopens the
   original short-leaf-name collision through the back door: two distinct
   namespaced types sharing a bare name on the probing side (one genuinely
   removed, one genuinely kept) would retry the removed one's failed lookup
   with the bare name and land on the unrelated survivor, diffing two
   different types against each other. Fixed by tracking bare-name ambiguity
   per `TypeMap` (`bare_name_is_unambiguous()`) and gating the fallback on
   the *probing* side's own ambiguity, not just the target side's.

All three were genuine defects, not false alarms — each was verified with a
standalone repro before and after the fix (shown in the PR review thread)
and closed with dedicated regression tests (`tests/test_diff_helpers.py`
`TestLookupMatchedType`/`TestTypeMap`, `tests/test_diff_types_deep.py`
`TestQualifiedNameMatching`/`TestHybridProvenanceKeys`/
`TestEmittedSymbolStaysBare`) rather than just a manual assertion that the
fix looked right. The pattern across all three: each fix closed one
direction/axis of the compatibility-alias problem without testing its
interaction with the *other* axis already in play (ambiguity, direction,
identity-leak) — a reminder that this kind of "old/new matching with a
compatibility fallback" logic needs pairwise (not just per-mechanism) test
coverage before it's trustworthy.

Tests: `tests/test_diff_helpers.py::TestTypeMapKey`/`TestTypeMap` (direct
unit coverage of the moved matching primitives), `tests/test_diff_types_deep.py`
`TestQualifiedNameMatching` (collision fix + both Codex regressions),
`TestHybridProvenanceKeys`, `TestEmittedSymbolStaysBare`.

## FP-2 (fixed): anonymous-enum field spelling embeds an absolute path

**Symptom.** A `testCase::result` field — an *identical* unnamed enum
declared in both the old and new pvxs checkouts (only the source *tree root*
differs, since old/new are extracted to separate directories) — was reported
as `type_field_type_changed`:

```text
old: enum (unnamed enum at .../old/include/pvxs/unittest.h:56:5)
new: enum (unnamed enum at .../new/include/pvxs/unittest.h:56:5)
```

**Root cause.** The clang `-ast-dump=json` frontend (`dumper_clang.py`)
copies the field's `qualType` string verbatim, and clang synthesizes a name
for an anonymous enum/struct field that embeds the absolute source path.
`_field_type_genuinely_changed` (`diff_types.py`) compares field-type
spellings via `canonicalize_type_name`, which normalized whitespace/const
placement/pointer spacing but had no handling for an embedded path — so two
structurally identical declarations differing only in checkout root compared
unequal.

**Fix.** `canonicalize_type_name` (`name_classification.py`) now strips the
`at <path>:<line>:<col>` suffix before all other normalization, collapsing
both spellings to a path/line/column-independent `(unnamed enum)` marker.
This also makes such comparisons robust to incidental line renumbering
within the same file.

Tests: `tests/test_review_fixes.py::TestCanonicalizeTypeName` (new
`test_anonymous_*` cases).

## FP-3 (documented, not fixed): transitively-included system-header
declarations leak into the diff when public-header scoping doesn't apply

**Symptom.** Five `API_BREAK` findings on the same diff belong to libstdc++,
not pvxs: `std::__exception_ptr::operator!=`, and four relational operators
on `_Bit_iterator_base` (`std::operator<`/`>`/`<=`/`>=`), sourced from
`<bits/exception_ptr.h>` and `<bits/stl_bvector.h>` — headers pvxs never
installs, pulled in transitively via `<functional>`/`<vector>`.

**Root cause (investigated, not changed).** The L2 header-AST parse
(`dumper_castxml.py`'s `parse_functions`/`parse_types`) populates
`snap.functions`/`snap.types` from every declaration reachable in the
translation unit, with no header-provenance filtering at parse time — only
`parse_public_constants`/`parse_public_typedef_headers` apply the
`_decl_is_public` origin check, and they feed a different (L4 source-surface)
consumer, not the main function/type population `diff_symbols.py`/
`diff_types.py` operate on. The only filter that *can* remove this class of
finding is the opt-in `FilterNonPublicSurface` post-processing step
(`post_processing.py`), gated on `--scope-public-headers` *and* on the
snapshot actually carrying resolvable header provenance
(`surface.py:resolvable`); when either condition doesn't hold, filtering is a
no-op and every transitively-included system declaration stays in the diff.

**Why this wasn't fixed here.** Closing it fully means extending
provenance-based filtering into the main L2 parse path
(`parse_functions`/`parse_types` in `dumper_castxml.py`) so system-header
declarations are excluded from `snap.functions`/`snap.types` whenever a
public-header set is known, independent of whether `--scope-public-headers`
was passed — a change to the core snapshot-population path touched by every
detector, not a contained fix like FP-1/FP-2. That needs its own scoped
design + regression pass rather than a drive-by addition here, consistent
with how `pvxs-abi-validation-2026-07.md` treats similarly structural gaps
(F5b, F7) as documented rather than fixed inline.

**Current mitigation:** pass `--scope-public-headers` (the CLI default) with
a header set that resolves to `surface.resolvable = True` (i.e. `-H`/
`--public-header-dir` pointed at the library's actual installed headers,
matching pvxs's own `-public-headers` convention for `abi-dumper`) — this
already demotes `std::` operators via the existing `REASON_SYSTEM_HEADER`
path in `surface.py`. The gap is specifically the *default-off* / scoping-
unresolvable case, which the reported repro command hits because it never
passes `--scope-public-headers` explicitly and relies on the CLI default
without confirming header-provenance resolution.

## Real-tool re-verification (FP-2)

Re-ran the exact reported scenario with real tools rather than only synthetic
unit fixtures:

- Cloned `github.com/epics-base/pvxs` at `cc7bc72dd7676c72871889c8586014947567ed1d`
  (the exact "new" revision the original spike names) and copied the real
  `testCase` class body (including its private anonymous `enum { Nothing,
  Diag, Pass, Fail } result;`) verbatim from `src/pvxs/unittest.h` into two
  directories at different absolute paths (`.../old/pvxs/unittest.h` and
  `.../new/pvxs/unittest.h`), mirroring the real old-checkout-vs-new-checkout
  layout that triggers the bug.
- Compiled a trivial `.so` for each side (`g++ -std=c++17 -g -Og -fPIC
  -shared`) and ran `python -m abicheck compare` with `--ast-frontend clang`
  (the fallback path the original spike documents hitting, since this host's
  bundled castxml 0.6.3/clang-17 can't parse current glibc/libstdc++ 13
  headers — the exact "CastXML fallback" friction §10 of the original spike
  describes).
- **Pre-fix baseline** (a `git worktree` checked out at
  `6ad4016`, the commit this branch forked from, run via `PYTHONPATH`):
  `rc=4`, `verdict: BREAKING`, `type_field_type_changed | testCase | Field
  type changed: testCase::result` — the exact false positive reported.
- **This branch (with the FP-2 fix applied):** `rc=0`, `verdict: NO_CHANGE`,
  zero findings — identical headers at different absolute paths no longer
  trip a false field-type-change.

## What wasn't re-verified end-to-end

- **FP-1 against real libstdc++ headers.** A direct repro (`std::locale`,
  `std::shared_ptr`, `std::vector<bool>` all included from one header,
  compiled with g++, dumped with `--ast-frontend clang`) hit a pre-existing,
  unrelated environment gap: this host's system `clang`/`clang++` needs
  additional flags (`-std=gnu++17` plus GNU-dialect predefines that `g++`'s
  own driver sets automatically) to parse this glibc/libstdc++ 13 install
  that `abicheck`'s clang-frontend invocation doesn't currently pass —
  `clang++ -std=gnu++17 -fsyntax-only` parses the same header cleanly by
  hand, confirming this is an invocation-flag gap rather than a genuine
  parse failure. This is a distinct, pre-existing gap (not touched by this
  PR) and out of scope to fix here; FP-1 itself is covered by direct,
  deterministic unit tests that reproduce the exact reported matching
  mechanism (two distinct namespace-qualified `_Impl` types sharing the bare
  leaf name) rather than depending on this host's specific toolchain/header
  compatibility.
- **The full historical `1.5.2` → `cc7bc72` diff** (both `libpvxs.so`/
  `libpvxsIoc.so`, built against a real EPICS Base 7.0). pvxs's own
  `.ci/cue.py prepare` is written for the GitHub Actions build matrix
  (`SETUP_PATH` comes from matrix env vars) and exits immediately when run
  standalone; hand-building EPICS Base 7.0 to unblock it was out of scope
  for this pass.
- **FP-3** (system-header leakage) — unchanged from the original spike;
  still documented, not fixed (see above).

## Positive results confirmed by this spike (no code change needed)

- **Private C++ layout change detection** (§13 of the original spike,
  `std::function<void()>` → `std::function<void(const Connected&)>` on a
  private member): `abicheck` correctly reports `TYPE_FIELD_TYPE_CHANGED` /
  a struct-field break — this is exactly what
  `TestBaseClassChanges`/`TestQualifiedNameMatching`-style field-diff tests
  already cover, and ACC misses this class of change entirely (it never
  inspects private members without special flags).
- **Exit-code contract** (`0`/`2`/`4`/`64` for compatible / API-break /
  ABI-break / usage-error) matches the documented matrix in
  `docs/reference/exit-codes.md` and needed no change.
- **Isolation**: `abicheck` never executes target-DSO code (parses ELF/DWARF
  and, for header mode, spawns only the AST frontend + compiler — no
  `dlopen` of the compared library), matching the existing security posture
  this repo already documents.

## Broader-scale generalization: closing the same bug class everywhere it hides

FP-1's fix (and the three Codex-caught regressions it took to make it fully
ambiguity-safe) was applied to `diff_types.py` only. A follow-up review asked
the more useful question directly: is this specific `RecordType` file the
only place this bug class exists, or was it just the first place someone
happened to look? The answer was no — the same bare-leaf-name collision was
found live in two more places, and fixing it there the same way it was fixed
in `diff_types.py` (rather than leaving each as a standing gap) is the work
recorded here. ADR-045 (`docs/development/adr/045-identity-based-old-new-entity-matching.md`)
records the underlying principle this generalizes.

- **`diff_symbols.py` had never adopted the ambiguity-safe matching at
  all.** Its own four call sites — `_diff_functions`'s virtual-method-owner
  resolution (feeding `diff_cxx_rules._resolve_owner_type`),
  `_diff_ctor_overload_ambiguity`'s class grouping, `_diff_access_levels`,
  and `_diff_anon_fields` — still built plain `{t.name: t for t in ...}`
  dicts, independently reintroducing FP-1's exact false-positive/false-
  negative risk in four different detectors. All four now route through
  `diff_helpers.build_type_map`/`lookup_matched_type`. Regression tests in
  `tests/test_diff_symbols_type_matching.py` (7 tests, 5 confirmed via
  `git stash` to fail against the pre-fix code) pin the fix per call site;
  `_converting_ctors_by_class` additionally needed a fallback to the bare
  name when `owner_class_of` can't resolve a scope from a non-Itanium-
  mangled test symbol, to avoid breaking `tests/test_explicit_ctor.py`'s
  existing synthetic-mangled-name fixtures (real castxml/DWARF-derived
  constructors always have real mangled symbols and are unaffected).
- **`EnumType` had no `qualified_name` equivalent at all.** Added, populated
  the same way as `RecordType.qualified_name` on both the castxml
  (`_qualified_type_name`) and clang (`entry.scope`) header dumpers.
  `diff_types.py`'s `_diff_enums`, `_diff_enum_renames`, and
  `_diff_enum_deprecated` now match old/new enums through the same
  `build_type_map`/`lookup_matched_type` machinery. Regression tests in
  `tests/test_diff_enum_type_matching.py` (5 tests, all confirmed via
  `git stash` to fail against the pre-fix `diff_types.py`) plus dumper-level
  unit tests (`test_dumper_clang.py::test_parse_enums_sets_qualified_name_for_namespaced_enum`,
  `test_castxml_schema_completeness.py::TestCastxmlParserPopulatesNewAttributes::test_namespaced_enum_qualified_name`)
  confirm both backends populate the new field correctly.
- **`diff_helpers.TypeMap`/`type_map_key`/`lookup_matched_type` are now
  generic** (a `Protocol`-bound `TypeVar` over any `name`/`qualified_name`
  shape) instead of hard-typed to `RecordType`, so `RecordType` and
  `EnumType` share one implementation rather than duplicating the matching/
  ambiguity logic per entity kind.
- **A generalized Hypothesis property**
  (`test_same_leaf_name_matching_is_order_independent` /
  `test_same_leaf_name_enum_matching_is_order_independent` in
  `tests/test_detector_properties.py`) generates two distinct qualified
  entities sharing a bare leaf name, randomizes snapshot list insertion
  order on both old and new sides, and asserts the emitted diff is
  order-independent regardless of which detector fires. This is the actual
  point of doing this at "broader scale" rather than per-file: it does not
  need to know which detector is vulnerable, so it also catches any future
  detector that reintroduces this pattern, closing the discovery gap (each
  of the two live gaps above was found only because someone went looking by
  hand) rather than just the two instances found this time.

## Status

- **Fixed & tested in this branch:** FP-1 (type-matching key, plus the three
  Codex-caught regressions across successive versions of that fix, plus the
  broader-scale generalization to `diff_symbols.py` and `EnumType` above),
  FP-2 (anonymous-type path leak in `canonicalize_type_name`). Full fast unit
  suite green, `mypy`/`ruff`/AI-readiness clean.
- **FP-2 additionally re-verified against real tools** (real pvxs source,
  real `clang`-frontend dump, before/after on a real `.so` pair) — see
  "Real-tool re-verification" above.
- **Documented, not fixed:** FP-3 (system-header leakage when public-header
  scoping is off or unresolvable) — needs a scoped follow-up touching the L2
  parse path in `dumper_castxml.py`.
- **Not re-verified end-to-end:** FP-1 against real libstdc++ headers (blocked
  by an unrelated clang-invocation-flag gap on this host, not this fix — see
  above) and the full historical `1.5.2` → `cc7bc72` diff over the real
  `libpvxs.so`/`libpvxsIoc.so` pair (needs a from-scratch EPICS Base 7.0
  build; pvxs's `.ci/cue.py prepare` is CI-matrix-only and doesn't run
  standalone). Completing both — and quantifying whether FP-3 remains under
  `--scope-public-headers` on the real historical diff — is the acceptance
  step still owed before pvxs can be recommended as more than a
  local/experimental shadow check.
