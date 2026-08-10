<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`Param.is_va_list` now has a producer (G31 Phase C continued).** No
  backend had ever populated this fact — `param_became_va_list`/
  `param_lost_va_list` were unreachable on real input. The direct-clang
  header-AST backend now extracts it (`dumper_clang_qualifiers.
  _clang_param_is_va_list`, x86-64 System V spelling only — the one ABI
  verified here; an unrecognized target's real `va_list` still reads
  `False`, a conservative false negative rather than a guessed spelling).

  Two gates come with it, mirroring `Param.is_restrict`'s own (G31 Phase
  C), plus one that's deliberately *stricter*: the detector is now
  **header-tier, `"clang"`-producer-ONLY — not `"hybrid"` either**, unlike
  `is_restrict`'s gate. castxml has never populated this fact, so a hybrid
  merge's castxml-verbatim parameters for a MATCHED function carry a
  permanent, version-independent `False` (not a legacy-baseline artifact —
  `is_restrict`'s hybrid inclusion is safe only because castxml IS a real
  producer there). A function whose parser coverage differs between the
  old and new snapshot (clang-only-appended in one, matched-and-blind in
  the other) would otherwise read a real, unchanged `va_list` parameter as
  added/removed purely from that coverage shift. Snapshot schema **v23**
  adds `clang_va_list_facts_reliable` for the separate pre-v23-baseline
  case. The whole-snapshot disk cache version is bumped to `11` so a warm
  cache re-extracts rather than replaying a snapshot that predates the fix.

- **`Variable.access`/`Variable.value` now have a producer (G31 Phase C
  continued).** No backend had ever populated either fact — every variable
  read the model defaults (`AccessLevel.PUBLIC`, `None`), so
  `var_access_changed`/`var_access_widened` were unreachable on real
  input. The castxml backend now extracts both: `access` reuses the same
  structured `access` attribute already read for `Function`/`TypeField`
  (a static class member's `<Variable>` element carries it too), and
  `value` reuses the same verbatim, unevaluated `init` attribute already
  read for `TypeField.default`/`Param.default`, restricted to
  const/constexpr variables (a non-const initializer can be an arbitrary
  runtime expression, e.g. `init="f()"`, which is not the "compile-time
  constant" `Variable.value`'s own docstring promises).

  `value` needed no reliability flag — `var_value_changed`'s detector
  already declines per-pair unless BOTH sides are non-`None`, so a legacy
  blanket-`None` side is silently skipped rather than misread. `access`
  has no such "unknown" state (a plain enum, `PUBLIC` by construction), so
  it gets the same treatment as `is_va_list` above: header-tier,
  `"castxml"`-producer-ONLY (not `"hybrid"`, for the identical
  parser-coverage-shift reason), and snapshot schema **v24** adds
  `castxml_var_access_facts_reliable` for the pre-v24-baseline case. The
  whole-snapshot disk cache version is bumped to `12`.

### Documentation

- **[Header-Backend Capabilities](https://abicheck.readthedocs.io/en/latest/reference/header-backend-capabilities/)
  updated (G31 Phase C continued)** for `Param.is_va_list`'s and
  `Variable.access`/`Variable.value`'s new producers — regenerated from
  `scripts/backend_capabilities.py`. All three model fields the page used
  to flag as having no producer on any layer now have one.
