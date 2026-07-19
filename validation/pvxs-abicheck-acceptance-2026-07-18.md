# PVXS acceptance spike (2026-07-18): shadow-check false positives

Follow-up to `pvxs-abi-validation-2026-07.md` (which covers F1–F7 from an
earlier validation pass). This spike evaluated whether `abicheck` could run
as an independent shadow check alongside pvxs's existing
`abi-dumper` + `abi-compliance-checker` (ACC) gate, without replacing it, and
found three new false positives on a real historical diff
(`1.5.2` → `cc7bc72`) plus one confirmed positive (a private-layout break ACC
misses). Two of the three false positives are fixed in this change; the
third is a known, documented gap.

This environment has no network access to clone pvxs/EPICS Base and no
`castxml` install, so the original end-to-end repro script (building both
pvxs revisions and running `abicheck compare` against the real `.so`s) could
not be re-executed here. The three findings below were instead root-caused
directly against the reported symptoms and reproduced with minimal synthetic
fixtures / targeted unit tests exercising the same code paths. Re-running the
full pvxs repro is the acceptance step still owed before this can move off
"local/experimental shadow" status for pvxs specifically (see "Status" below).

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

**Fix.** `diff_types.py` now keys every old/new type-matching map through a
new `_type_map_key(t)` helper: `t.qualified_name or t.name`. Header-mode
snapshots get the real qualified identity; DWARF-only snapshots are
unaffected (their `RecordType.name` is already the qualified spelling built
by walking the DIE namespace chain — see `dwarf_snapshot.py`). Global-scope
types (`qualified_name is None`) fall back to the bare name exactly as
before, so the change is a no-op for the common case and only removes the
same-leaf-name collision.

Tests: `tests/test_diff_types_deep.py::TestQualifiedNameMatching`.

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

## Status

- **Fixed & tested in this branch:** FP-1 (type-matching key), FP-2
  (anonymous-type path leak in `canonicalize_type_name`).
- **Documented, not fixed:** FP-3 (system-header leakage when public-header
  scoping is off or unresolvable) — needs a scoped follow-up touching the L2
  parse path in `dumper_castxml.py`.
- **Not independently re-verified in this environment:** the original
  end-to-end pvxs `1.5.2` → `cc7bc72` repro (no network/`castxml` access
  here). Re-running `abicheck compare` over the real `libpvxs.so`/
  `libpvxsIoc.so` pair with these fixes applied — confirming FP-1/FP-2 no
  longer appear and quantifying whether FP-3 remains under
  `--scope-public-headers` — is the acceptance step still owed before pvxs
  can be recommended as more than a local/experimental shadow check.
