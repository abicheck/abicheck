<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **Pack manifests: nested list values now type-tag their elements for
  conflict detection** (`compatibility_evaluation_resolver.py`):
  `_value_identity_key` already distinguished a bare `True` from `1` (both
  `==`/hash equal in Python), but only tagged the *outer* value -- a list
  assignment canonicalizes to a tuple, and `(True,)`/`(1,)` still compared
  and hashed equal as a whole, since tuple equality recurses into elements
  with plain `==`/`hash()`. Two packs assigning `x: [true]` and `x: [1]` to
  the same field were silently treated as agreeing. Fixed by recursing
  `_value_identity_key` into nested tuples so each element carries its own
  type tag.

- **Pack manifests: a directly constructed `str`-mixin `Enum` value/key is
  now flattened to its plain payload, not its `__str__` representation**
  (`compatibility_evaluation_packs.py`): `ChangeKind`/`ContractMode` are
  `(str, Enum)` mixins, so a real member (e.g. `ChangeKind.FUNC_REMOVED` as
  a policy slug, or `ContractMode.PUBLIC` as a field value) passes the
  existing `isinstance(value, str)`/slug-membership checks via
  `str`-equality, but `str(value)` invokes `Enum.__str__` and returns
  `"ChangeKind.FUNC_REMOVED"` rather than the member's actual payload
  `"func_removed"`. Such a pack silently stopped matching or conflicting
  with an equivalent manifest-loaded (plain-`str`) assignment. Fixed with a
  new `_plain_str` helper (checks `.value` for an `Enum` member first) used
  for policy-pack slugs, contract/gate-pack field values, and field-name
  keys alike.

- **ADR-049 Phase 3 shadow evaluator: L4/L5 source-derived public-by-
  construction findings are no longer downgraded to `UNKNOWN_UNRESOLVED`**
  (no behavior change outside this still-unwired shadow module):
  `post_processing.MarkReachability`'s own `_PUBLIC_SOURCE_ABI_KINDS` (e.g.
  `PUBLIC_MACRO_REMOVED`, `INLINE_FUNCTION_REMOVED`, `PUBLIC_TYPEDEF_REMOVED`)
  are proven public by construction, but their `symbol` (a macro/inline-
  function/typedef name) is never a real C/C++ header-surface function/
  variable/type `classify_change_surface` can place -- these findings fell
  through to that function's "cannot place it, keep it" conservative
  fallback, which `_in_surface_result_is_confirmed` correctly refuses to
  treat as genuine confirmation, wrongly downgrading a definitive public
  break to `UNKNOWN_UNRESOLVED` even with fully resolvable surfaces. Fixed
  by trusting this kind set unconditionally, at the same early point as
  `python_*`/`_NEVER_FILTER_KIND_NAMES` -- before the resolvable-surface
  gate and the identity-ambiguity gate, so an unrelated evidence gap can't
  downgrade a definitive event.
