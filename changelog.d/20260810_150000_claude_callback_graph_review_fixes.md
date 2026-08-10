### Fixed

- **`callback_graph.py`'s registration join silently dropped callback-only
  declarations**: `augment_graph_with_callback_registrations` required both
  endpoints of a `DECL_REGISTERS_CALLBACK`/`DECL_TAKES_ADDRESS_OF` edge to
  already have a `source_decl` node — the common case for a private handler
  used only as a callback (never itself called) or a registration API's own
  callback parameter. Now mints the missing endpoint instead, the same
  precedent `override_graph.py` already establishes in this family.

### Known issues

- **`callback_graph.py`'s `CALLBACK_MAY_INVOKE` can produce a false-positive
  edge, not just a missing one**: two unrelated functions each declaring
  their own same-named function-pointer parameter (e.g. both named `h`)
  collapse onto the same slot identity, since neither `callback_graph.py`
  nor the upstream `call_graph.py` scope-qualify a parameter/field identity.
  A function registered on one function's `h` can be reported as a possible
  target of a call through a different, unrelated function's own `h`. A real
  fix needs a scope-qualified identity in `call_graph.py` itself (shared
  infrastructure); documented and pinned by a dedicated regression test
  rather than silently accepted.
