---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# `unversioned_exported_symbol` false-positives on base-version (VER_FLG_BASE) symbols of versioned libraries: the model cannot tell base-bound from unversioned

Index: [Known gaps — closed and moot](../archive/known-gaps-closed.md)

**Status (2026-10-12): CLOSED.** Fixed by recording the binding as its own fact, `ElfSymbol.version_binding` (snapshot schema v58), the "next step" this entry named; the check flags only `unversioned`. See the [closed entry](../archive/known-gaps-closed.md) for what landed and the residual it leaves. The text below is the original write-up.

Found once ADR-068 §3
row 3's first slice made `checker.compare()` run this check
automatically: self-comparing a real, canonically GNU-symbol-versioned
C library (`zlib1g`, exercised by `.github/workflows/realworld-
validation.yml`'s "Ubuntu package and bundle smoke" job) reports
`COMPATIBLE_WITH_RISK` with one `unversioned_exported_symbol` finding
per base-API function — `readelf -V`/`objdump -T` confirm every one of
them is legitimately bound to the anonymous/default GNU version node
(`Base`, i.e. the library's own SONAME), the standard, permanent
convention for a versioned library's original API: symbols present
before the library adopted symbol versioning stay on the base version
forever, and retroactively assigning one a named version tag would
itself be an ABI break. `_check_unversioned_exported_symbol`
(`buildsource/cross_source_checks.py`) cannot currently distinguish "bound to the
base version" from "genuinely absent from `.gnu.version`" — both leave
`ElfSymbol.version == ""` (`elf_metadata.py`'s `_apply_version_to_symbol`
intentionally never populates `version`/`is_default` for `ver_idx < 2`,
and `_build_verdef_index` intentionally excludes the base/`VER_FLG_BASE`
entry from `ver_index_map`), so nothing in the persisted model lets the
check tell them apart.

**Why not fixed here.** A sound fix needs a real distinguishing fact
(e.g. a new `ElfSymbol` field recording "explicitly bound to the base
version"), which is a `model/`+`storage/` schema change (`AbiSnapshot.
SCHEMA_VERSION` bump, `elf_from_dict`/`elf_to_dict` wiring in
`snapshot_platform_blocks.py`) — real, scoped work, but a materially
larger and riskier change than "make an already-migrated check
reachable," and touching the shared `ElfSymbol.version` field's *value*
for the base case (the cheaper-looking alternative) risks silently
changing every other detector that already reads `version == ""` as
"unversioned" (symbol-version add/remove detection, OLD/NEW symbol
matching by `(name, version)`, and others) — an unbounded blast radius
for a targeted fix.

**Why it stands regardless.** `buildsource/cross_source_checks.py`'s own module
docstring states the design tolerance this falls squarely inside: these
findings "are never BREAKING on their own... default to RISK or
API_BREAK and are advisory/suppressible until a check earns its
FP-rate-gate corpus and is promoted" (ADR-035 D4). The smoke-test
assertions were widened to accept `COMPATIBLE_WITH_RISK` alongside
`NO_CHANGE`/`COMPATIBLE` rather than silently suppressing or re-gating
the check — per this repository's standing rule against trading a
flag for a real capability (ADR-068 D5). **Next step, if picked up**:
the model change above, or narrowing the check to require some other
corroborating evidence of genuine omission (unresearched; a naive
ratio/threshold heuristic is explicitly not the fix — see this file's
own "attempted twice, reverted twice" discipline for why an unvalidated
heuristic is worse than a documented gap).
