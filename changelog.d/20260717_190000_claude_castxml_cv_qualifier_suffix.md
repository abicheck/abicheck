<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **CastXML pointer-value vs. pointee CV qualifier spelling** (G28, deferred
  limitation from PR #582): `_type_name`'s `CvQualifiedType` rendering always
  emitted the qualifier as a prefix, so a volatile pointer *value*
  (`int * volatile`) and a pointer to a volatile *pointee* (`volatile int *`)
  both rendered as the identical string `"volatile int*"` — a real
  declarator-binding change (which side of the `*` the qualifier attaches
  to) was invisible to any string-spelling comparison. Now a qualifier
  directly wrapping a `PointerType`/`ReferenceType`/`RValueReferenceType`
  renders as a suffix (`int* const`) instead, matching the `"T * const"`
  convention `cv_qualifiers_only_differ`/`canonicalize_type_name` already
  treat as canonical for this case. A pointee-position qualifier
  (`const int *`) is unaffected and still renders as a prefix. Deliberately
  does **not** follow `Typedef`/`ElaboratedType` aliasing to a pointer one
  level down (`typedef int *IntPtr; volatile IntPtr x;` still renders as the
  prefix `"volatile IntPtr"`) — an initial version did follow the alias, but
  Codex review caught that this would make castxml diverge from the clang
  backend specifically on this case: `dumper_clang.py` takes clang's own
  `qualType` spelling verbatim, and clang's printer does not relocate a
  qualifier through a typedef to an implicit `*` either (verified against
  real `clang -ast-dump=json` output: it also spells this
  `"volatile IntPtr"`).
