# Root cause: how an unsound export-loss ownership join shipped

Scope: the constructor/destructor exemption added to
`compare/undeclared_exports.py`'s removal half in
[PR #1308](https://github.com/abicheck/abicheck/pull/1308) (`123dd1a98`), and
the Itanium ownership parsing it rests on. Base of this analysis:
`dc8c2dd43cf7e9ed4f4b4cda439cd03f87a63205`.

This note separates what was **measured** from what is **inferred**. Nothing
below rests on an oneDAL artifact: none was present in this workspace (see
"Not run").

## What PR #1308 got right, and must keep

Before it, a lost `_ZTV`/`_ZTI`/`_ZTS`/`_ZTT` export was invisible once
headers were supplied — the class declaration and its whole method list
survive, so no header-derived detector can see it, and supplying *more*
evidence turned a BREAKING verdict into NO_CHANGE. That fix is real and is
re-asserted here against the strongest form of the exemption
(`tests/test_export_owner_resolution.py`, section 4).

## The three defects, and the measurements that establish them

**1. The owner join could not match, and then matched the wrong thing.**
`_is_declared_class_ctor_or_dtor` tried `snapshot.type_by_name(owner)` with
`owner = "::".join(components[:-1])` — a qualified path built from
`itanium_scope_components`, which deliberately keeps template arguments in
Itanium encoding (`BoxIiE`). Measured on a real `g++`/castxml dump,
`type_by_name` is keyed by `RecordType.name`, which every backend fills with
the **unqualified** spelling (`'Widget'`, `'Box<int>'`, `'Base'`). So the
qualified candidate could never match any snapshot, for any input, and the
bare `components[-2]` fallback always decided. That fallback is first-wins
over a name-collision index: two classes named `Base` in different namespaces
share one entry, and the loser is only a dropped-duplicate log line. A lost
`other::Base::Base()` was therefore suppressed by the existence of an
unrelated `api::Base`.

**2. "The owning class is still declared" is not evidence about an export
obligation.** Two native controls, each changing only a version script with
headers, source and SONAME fixed, and each run by compiling a client **once**
against OLD and executing that same binary (eager binding, never recompiled)
against NEW:

| control | symbol | OLD binding | old client against NEW |
|---|---|---|---|
| out-of-line ctor | `_ZN3api6WidgetC1Ev` | `T` (strong) | `undefined symbol` |
| `extern template` ctor | `_ZN3api3BoxIiEC1Ev` | `W` (COMDAT) | `undefined symbol` |

The owning class is declared in both. So neither "it is a constructor", nor
"it is a template", nor "the symbol was weak" licenses suppression, and the
distinction a rebuilt client would show is not the question — an
already-linked client cannot emit a definition it was never given.

**3. Inheriting constructors did not parse at all.** C++11 `using Base::Base;`
mangles as `CI<n><base-type>` (Itanium 5.1.4.3); confirmed against a real g++
13 build emitting `_ZN3api7DerivedCI1NS_4BaseEEi`.
`_parse_ctor_dtor_component` accepted only `C1`–`C5`, so the whole name failed
to parse and `itanium_scope_components` returned `None`. That is not local to
this detector: `symbol_ownership.owning_scope_components` — and through it
`symbol_origin`, internal-namespace classification and public-surface
scoping — fell back to the conservative textual nested-name scan for every
inheriting constructor in every library. Note also that a demangler prints
the *base* first here; the owner is the derived class.

## Why the verification missed it

- **The exemption's own tests used a hand-built snapshot, and the shape they
  reproduced was the one the author already had in mind.** The fixture
  declared a ctor placeholder and a type, so the bare-name tier matched and
  the test passed. It could not surface defect 1, because a hand-built
  snapshot never carries two same-named classes, and it never reached
  `type_by_name`'s real key convention.
- **The one live-toolchain test involved was a mislabelled corpus entry.**
  `TestOptimizationLevelFP::test_o0_vs_o2_cpp_no_break` was the evidence the
  exemption was built to satisfy. Its header declared the special members
  **out-of-line** while its source defined them **in-class**. Under that
  header the `-O2` build really does remove a symbol old clients bound:
  measured, a client built against the `-O0` build dies against the `-O2`
  build with `undefined symbol: _ZN6WidgetC1Ev`. So the detector was made to
  stay silent about a true positive in order to satisfy a test whose premise
  was wrong. With the header carrying the same in-class definitions its source
  does, the same client runs against both builds (exit 0) and the "no break"
  claim becomes true. Both halves are now pinned, and the out-of-line variant
  is an explicit true-positive control.
- **"More evidence never weakens a verdict" was applied too broadly.** The
  scoped form is the defensible one, and is what is tested here in both
  directions: richer evidence must preserve an established public binary loss
  **under the same contract**, while it may legitimately refine relevance or
  confidence. Changing the header changes the contract; changing the
  optimization level does not.

## What changed

- `model/mangled_name.py` parses `CI1`/`CI2` (owner = derived class).
  `model/owner_recovery.py` provides `itanium_special_member_owner`, which
  reports the owner path, whether it carries template arguments, and whether
  the ctor is inheriting.
  `diff_cxx_rules.itanium_ctor_dtor_marker_span` deliberately still declines
  the form: its callers rewrite a 2-character marker in place, and `CI1` is
  three characters with a base-type encoding attached.
- `compare/export_owner_resolution.py` owns the join, with explicit
  `unique`/`ambiguous`/`unresolved`/`unsupported` states and a reason code per
  record, resolved against the **fully-qualified** ctor/dtor placeholder keys
  rather than the unqualified type index.
- The exemption now requires all four of: a parsed ctor/dtor leaf; a
  non-template owner; exactly one declaration key, matched on both sides; and
  every matching declaration `is_inline`. Anything else keeps the finding and
  lets public-surface scoping, contract evaluation, suppression and policy
  decide relevance — the machinery that owns that judgement for every other
  change.

**Intentional semantic change.** Losing an out-of-line or template-
specialization ctor/dtor export now reports where it previously could be
suppressed, and a bare-name collision no longer suppresses anything. Both are
verdict-affecting. No default was changed to preserve a prior verdict.

## Preventing recurrence

- The new bug class `classification.declaration_existence_as_export_obligation`
  (`tests/regressions/manifest_classification.py`) states the invariant and its
  axes, so the next fix in this area starts from the class rather than from a
  reproducer.
- Six mutation controls were run against the new suite and each is caught:
  always-covered, dropping the template guard, dropping the inline guard, a
  bare-name-first join, dropping `CI` parsing, and taking the base class as
  the inheriting constructor's owner.
- **Proposed, not implemented:** a pinned, prebuilt two-snapshot canary for
  semantic detector changes — a handful of stored `AbiSnapshot` pairs from a
  real C++ project with their expected finding sets, run on any PR touching
  `compare/`, `diff_*` or `policy/`. It needs no toolchain and no project
  build in CI, and it is the cheap thing that would have caught defect 1: a
  hand-built snapshot cannot express `type_by_name`'s real key convention,
  and a full live build is too expensive to gate on.

## Not run

- **The oneDAL corpus replay: NOT_RUN.** No oneDAL report, snapshot pair,
  policy or build was present in this workspace, and none was fetched. The
  1,076-record per-member classification table the task asks for cannot be
  produced without those artifacts, and no claim is made here about how many
  of them this change affects. The reported cohorts map onto defects 1–3
  above, but that mapping is *inference from the mechanisms*, not a
  reconciliation against the records.
- **The handoff `native_controls.py` and its recorded results were not
  present either.** Equivalent controls were rebuilt from the task's own
  description with local `g++ 13.3` and run against a real loader; their
  sources are reproduced in `tests/test_export_owner_resolution.py`.
- **clang as a second header backend: not exercised.** The integration lane
  here runs castxml. The manglings asserted are g++-produced.
- **PR #1308's review history was not read** — GitHub was not consulted for
  this note. Everything above about that PR comes from its code and its tests
  as they stand in the tree.

## Reading the classification per record

The per-record table the task asks for is produced by the resolver itself
rather than by a one-off script, so it stays available for the next corpus:

```python
from abicheck.compare.export_owner_resolution import special_member_export_coverage

for change in result.changes:
    if change.kind.value.endswith("_removed_elf_only") and change.symbol:
        c = special_member_export_coverage(change.symbol, old_snapshot, new_snapshot)
        print(change.symbol, c.join.value, c.reason, c.owner, c.templated, c.inherited)
```

Every removal reported carries a row: `join` gives owner resolution
(`unique`/`ambiguous`/`unresolved`/`unsupported`), `reason` gives the
disposition (`template_specialization`, `out_of_line_definition`,
`owner_declared_on_one_side_only`, `bare_name_collision_only`,
`owner_not_declared`, `not_a_parsed_special_member`,
`inline_declaration_unchanged_on_both_sides`), and `owner` names the scope
the join resolved to. Rows are per member library and per raw symbol; nothing
here deduplicates a spelling across members, so a symbol lost from two
libraries is two rows.

The reported oneDAL cohorts map onto these rows as follows — **inference from
the mechanisms, not a reconciliation against the records**, which cannot be
done without the artifacts:

| cohort | expected row |
|---|---|
| template-owner matching (~400) | `unique` / `template_specialization` |
| inherited constructor matching (~299) | `unique` or `unresolved`, owner = the derived class (previously `not_a_parsed_special_member`, since `CI<n>` did not parse) |
| ordinary members of template classes | `unsupported` / `not_a_parsed_special_member` — the exemption never covered non-special members, so these were, and remain, reported |
| internal/runtime instantiations | whatever the row says; relevance for them is public-surface scoping's and `--contract`'s question, not this join's |
