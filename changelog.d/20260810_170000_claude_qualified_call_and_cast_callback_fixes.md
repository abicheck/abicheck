### Fixed

- **`call_graph.py` classified an explicitly-qualified member call
  (`obj.Base::f()`, or the common `Base::f()` base-chaining call from inside
  an override) as `virtual`/`overapprox`**: C++ qualification suppresses
  dynamic dispatch regardless of the target's own virtuality, but clang's
  `-ast-dump=json` carries no qualifier field the way its text dump does.
  Fixed by deriving qualification from source-range arithmetic already
  present in the JSON (`_member_expr_is_qualified`), with a separate branch
  for a genuinely implicit `this` receiver (clang anchors that receiver's
  synthesized position at the member name itself, not before a written
  qualifier).
- **`callback_graph.py`'s `_address_taken_function` didn't unwrap an
  explicit cast**: a callback argument converted to its target
  function-pointer type (`(handler_t)handler`, `static_cast<handler_t>(...)`,
  `reinterpret_cast<handler_t>(...)`) wraps the same function-to-pointer
  decay this function already recognized, but only `ParenExpr` was unwrapped
  — silently omitting the registration for any API that requires or
  commonly receives a cast callback argument. Now also unwraps
  `CStyleCastExpr`/`CXXStaticCastExpr`/`CXXReinterpretCastExpr`/
  `CXXConstCastExpr`/`CXXFunctionalCastExpr`/`CXXDynamicCastExpr`.
- Fixed two pre-existing test bugs surfaced by Windows CI:
  `test_callback_graph.py`'s missing-clang test passed an empty
  `BuildEvidence()`, which hits `extract_from_build`'s own early return
  before its availability check ever runs (now uses a real compile unit,
  matching the established sibling-test convention); `test_macro_graph.py`'s
  real-clang end-to-end test filtered by an Itanium-mangled prefix, which
  never matches a Windows runner's MSVC-targeted mangling (now matches by
  substring instead, which both schemes carry).
