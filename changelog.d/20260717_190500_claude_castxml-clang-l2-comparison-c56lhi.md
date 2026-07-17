### Fixed

- **Cv-neutral callback parameters, and legacy-snapshot cv false positives on
  variables.** Follow-up review findings on the CastXML/Clang L2 parity work
  in #582:
  - `_strip_cv_qualifiers`'s template-arg depth tracking (added to stop
    `Box<const int>` vs. `Box<int>` from misclassifying as cv-only)
    over-corrected by also blocking on `(`/`)`, so a cv-neutral callback/
    function-pointer parameter (e.g. `const` added to a callback's own
    by-value `int` parameter) misfired the breaking `FUNC_PARAMS_CHANGED`
    path. Confirmed against real clang/gcc mangling: `void(*)(int)` and
    `void(*)(const int)` are the identical function pointer type — top-level
    cv on a by-value (or trailing pointer-own `* const`/`* volatile`)
    parameter is dropped for mangling at every nesting level of a function
    type, not just the outermost. Now only blocks on `<`/`[` (template args,
    array subscripts), resolved independently per comma-separated parameter
    within one callback's own list (a sibling parameter's unrelated pointer
    sigil must not affect another's verdict).
  - A callback parameter's POINTEE cv (`void(*)(int*)` vs.
    `void(*)(const int*)`) IS a genuinely different, non-interchangeable
    function pointer type (confirmed against real mangling — unlike an
    ordinary top-level parameter, where `T*` implicitly converts to `const
    T*`, a caller's existing callback written for one pointee-cv signature
    can't be passed where the other is expected), so the fix above must NOT
    neutralize it. An array-typed callback parameter decays to a pointer too
    (`void(*)(int[3])` vs. `void(*)(const int[3])`), and gets the same
    treatment.
  - `_check_variable` didn't consult `header_cv_facts_reliable` the way
    `diff_types._field_type_genuinely_changed` does for struct fields — a
    pre-v9 CastXML snapshot's `_type_name()` silently dropped `volatile`
    from a variable's type spelling, and `Variable` has no dedicated
    `is_volatile` fact to fall back on (unlike `TypeField`), so an unchanged
    `volatile` variable compared against a legacy baseline misreported a
    breaking `VAR_TYPE_CHANGED` purely from the tool upgrade. Gated the same
    way, reusing `func_signature_cv_only_differ`. The suppression didn't
    fully suppress on its own, though: it only skipped `VAR_TYPE_CHANGED`,
    then fell through to an unconditional `is_const`-based const-transition
    check, so the same false positive resurfaced as the more specific
    `VAR_BECAME_CONST` instead. Now returns immediately for the legacy-noise
    case, leaving only the dedicated `is_pure_const_flip` path (a real,
    structural signal independent of the legacy type-spelling bug) free to
    report a genuine const transition.
  - The clang backend's typedef-desugaring fix for a field's hidden
    `const`/`volatile` (`typedef const int T;` renders as the bare alias
    `"T"` in `qualType`, only visible via `desugaredQualType`) scanned the
    WHOLE desugared spelling for a pointer typedef too — but `typedef const
    int *P;` desugars to `"const int *"`, where the `const` qualifies the
    POINTEE, not `P` itself (a plain, non-const pointer). Now scans only the
    substring after the last top-level `*`.
  - `_without_top_level_const` (a variable's own top-level const, as opposed
    to its pointee's) only recognized a bare pointer's trailing own-const at
    the absolute end of the string, so a variable whose type is itself a
    function or array pointer (`void (* const)()`, `int (* const)[5]` —
    canonicalized with the qualifier directly after the `*`, before the
    declarator's closing `)`/`]`, not at the string's end) misreported
    becoming const as `VAR_TYPE_CHANGED` instead of `VAR_BECAME_CONST`. Two
    further fixes on top of that: the sigil search picked the LAST
    top-level `*`/`&` in the whole string, which for a function-pointer
    variable whose own PARAMETER is itself a pointer (`void (* const)(int
    *)`) picks up the parameter's `*` instead of the outer declarator's —
    now scoped to the first top-level `(...)` group specifically (always
    the outer declarator); and removing `const` from a combined
    `"const volatile"`/`"volatile const"` span (when volatile is unchanged
    and only const is newly added — still a pure const-only flip) left a
    stray separator space that made the two sides compare spuriously
    unequal — now trimmed to match `canonicalize_type_name`'s own
    no-whitespace-adjacent-to-sigil convention.
  - Legacy CastXML C-linkage variable mangled-key identity across the fix
    that normalizes bogus `_Z`-prefixed keys to bare export names is a
    known, deliberately deferred limitation (needs dict-key reconciliation,
    not just a comparison gate) — documented in
    `docs/development/plans/g28-castxml-clang-l2-parity-hardening.md`.
  - A callback parameter that is itself a function pointer with a
    cv-qualified RETURN type (`void(*)(int (*)())` vs. `void(*)(const int
    (*)())` — confirmed distinct, non-interchangeable types by real g++
    mangling) had that return-type cv wrongly treated as the callback
    parameter's own neutral by-value qualifier: the recursive paren
    handling hid the inner declarator's `*` inside an isolated recursive
    call whose own pointer-position tracking never reached the enclosing
    scope.
  - A member-function-POINTER's own trailing cv (`void (C::*)(int) const`
    — the pointer points to a const member function; `void (* const)()`'s
    own trailing const is dropped for mangling, but a member function's is
    NOT, matching the existing `FUNC_CV_CHANGED` precedent — confirmed
    distinct, non-interchangeable types by real g++ mangling: two
    same-named overloads differing only in this trailing const compile as
    distinct symbols) was stripped like an ordinary disposable trailing
    qualifier, both as a callback parameter and as a variable's own type
    (`_without_top_level_const`'s end-of-string fallback didn't know a real
    parameter list preceded the trailing cv). Fixed in both
    `_strip_cv_qualifiers` and `_without_top_level_const` by recognizing a
    pointer/pointer-to-member declarator-grouping paren structurally — it's
    always immediately followed by another top-level paren/bracket (the
    real parameter list or array dimensions) — regardless of what's inside
    it (a bare sigil, or a class-qualified `Class::*`).
  - A pointer-to-array callback parameter's own cv (`int (*)[3]` vs.
    `int (* const)[3]` — real g++ rejects the two as a redefinition, not
    distinct overloads, confirming they're the identical type) was wrongly
    treated as non-neutral: the array-decay handling above also fired for
    the pointee's array bound after an already-seen pointer sigil, moving
    the boundary past the declarator's own trailing qualifier. Now only
    treats a `[...]` as decay when no pointer sigil has been seen yet in
    the current parameter.
  - `abicheck/diff_symbols.py` was already at the 2000-line AI-readiness
    hard cap, so the pre-existing variable-alignment/const-normalization
    helpers moved into a new leaf module (`diff_symbols_variables.py`),
    mirroring the existing `diff_symbols_scalar.py`/`diff_symbols_renames.py`
    split pattern.
